"""LLM 并发调用层。

- 走 OpenAI 兼容 chat/completions 格式
- base_url / key 来自环境变量（LLM_ARENA_API_KEY），代理经 HTTPS_PROXY 自动生效
- 单个模型失败不影响其他：每个模型内部捕获异常，返回 error 字段
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import httpx

BASE_URL = "https://opencode.ai/zen/go/v1"
TIMEOUT = 60.0  # 秒，单个请求上限

# 模型静态配置（id 即 API 模型名）
# temperature: 各模型允许的采样温度。多数模型接受 0.7，kimi-k3 仅允许 1。
MODELS: list[dict[str, str]] = [
    {"id": "deepseek-v4-flash", "name": "DeepSeek V4 Flash", "desc": "快速，便宜，适合日常"},
    {"id": "deepseek-v4-pro", "name": "DeepSeek V4 Pro", "desc": "深度推理，代码强"},
    {"id": "glm-5.2", "name": "GLM 5.2", "desc": "通用能力均衡"},
    {"id": "kimi-k3", "name": "Kimi K3", "desc": "长文本，中文友好", "temperature": 1.0},
    {"id": "qwen3.7-max", "name": "Qwen 3.7 Max", "desc": "千问旗舰，综合最强"},
]

CATEGORIES: list[str] = ["代码", "中文写作", "翻译", "推理", "通用"]

DEFAULT_TEMPERATURE = 0.7


def _temperature(model: str) -> float:
    """返回某模型的采样温度，未配置时用默认值。"""
    for m in MODELS:
        if m["id"] == model:
            return float(m.get("temperature", DEFAULT_TEMPERATURE))
    return DEFAULT_TEMPERATURE


def _api_key() -> str:
    """读取 API key，缺失时抛出明确错误。"""
    key = os.environ.get("LLM_ARENA_API_KEY", "").strip()
    if not key:
        raise RuntimeError("缺少环境变量 LLM_ARENA_API_KEY")
    return key


def _extract_content(raw: Any) -> str:
    """从 chat/completions 的 message.content 里稳健地提取纯文本。

    兼容两种格式：字符串 / 多模态分段列表（如 [{"type":"text","text":"..."}]）。
    """
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        parts: list[str] = []
        for item in raw:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if text:
                    parts.append(str(text))
        return "\n".join(parts)
    return str(raw)


async def _call_one(
    client: httpx.AsyncClient, model: str, question: str, category: str
) -> dict[str, Any]:
    """并发调用单个模型，内部兜底异常，绝不让错误冒泡。"""
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    f"你是一个专业的 AI 助手。用户给的问题分类是「{category}」。"
                    "请给出准确、结构清晰、可直接使用的回答，用 Markdown 排版。"
                ),
            },
            {"role": "user", "content": question},
        ],
        "temperature": _temperature(model),
        "max_tokens": 4096,
    }
    started = time.monotonic()
    try:
        resp = await client.post(
            "/chat/completions", json=payload, timeout=TIMEOUT
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        if resp.status_code != 200:
            return {
                "model": model,
                "content": None,
                "tokens": None,
                "latency_ms": latency_ms,
                "error": f"HTTP {resp.status_code}: {resp.text[:300]}",
            }
        data = resp.json()
        content = _extract_content(
            data.get("choices", [{}])[0].get("message", {}).get("content")
        )
        usage = data.get("usage") or {}
        tokens = (
            usage.get("total_tokens")
            or (usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0))
            or None
        )
        return {
            "model": model,
            "content": content,
            "tokens": int(tokens) if tokens is not None else None,
            "latency_ms": latency_ms,
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - 单模型失败要隔离，不能冒泡
        latency_ms = int((time.monotonic() - started) * 1000)
        return {
            "model": model,
            "content": None,
            "tokens": None,
            "latency_ms": latency_ms,
            "error": f"{type(exc).__name__}: {exc!r}",
        }


async def battle(question: str, category: str, models: list[str]) -> list[dict[str, Any]]:
    """对一组模型并发发起评测，返回结果列表（含失败项）。"""
    async with httpx.AsyncClient(
        base_url=BASE_URL, headers={"Authorization": f"Bearer {_api_key()}"}
    ) as client:
        tasks = [
            _call_one(client, m, question, category)
            for m in models
            if m in {x["id"] for x in MODELS}
        ]
        return list(await asyncio.gather(*tasks))


async def ping_model(model: str) -> dict[str, Any]:
    """健康检查：发一条极小的消息确认模型可用。"""
    async with httpx.AsyncClient(
        base_url=BASE_URL, headers={"Authorization": f"Bearer {_api_key()}"}
    ) as client:
        started = time.monotonic()
        try:
            resp = await client.post(
                "/chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 1,
                },
                timeout=30.0,
            )
            latency_ms = int((time.monotonic() - started) * 1000)
            ok = resp.status_code == 200
            return {
                "model": model,
                "ok": ok,
                "latency_ms": latency_ms,
                "detail": "" if ok else f"HTTP {resp.status_code}",
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "model": model,
                "ok": False,
                "latency_ms": int((time.monotonic() - started) * 1000),
                "detail": f"{type(exc).__name__}: {exc}",
            }
