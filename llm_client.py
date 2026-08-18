"""LLM 并发调用层（双通道版）。

- opencode 海外通道：走 OpenAI 兼容 chat/completions，经 HTTPS_PROXY 代理出网
- ali 国内直连通道：阿里云 token-plan，trust_env=False 强制直连，不依赖代理
- 单个模型失败不影响其他：每个模型内部捕获异常，返回 error 字段
- 流式采集：stream=true，记录 TTFT / input-output tokens，并估算成本
- LLM Judge：自动审判（评分 1-5 + 理由），与人工评分互补
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, Optional

import httpx

TIMEOUT = 180.0  # 秒，单个请求上限（长篇生成任务需要更长时间）

# 通道定义：base_url + key 环境变量 + 是否走系统代理
PROVIDERS: dict[str, dict[str, Any]] = {
    "opencode": {
        "base_url": "https://opencode.ai/zen/go/v1",
        "api_key_env": "LLM_ARENA_API_KEY",
        "via_proxy": True,  # 走 HTTPS_PROXY（ClashX）
    },
    "ali": {
        "base_url": "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        "api_key_env": "ALI_API_KEY",
        "via_proxy": False,  # 国内直连，绕过代理
    },
}

# 模型静态配置（id 即 API 模型名；provider 决定走哪个通道）
# temperature: 各模型允许的采样温度。多数模型接受 0.7，kimi-k3 仅允许 1。
MODELS: list[dict[str, str]] = [
    # ---- 海外通道（opencode，需代理）----
    {"id": "deepseek-v4-flash", "name": "DeepSeek V4 Flash", "desc": "快速，便宜，适合日常", "provider": "opencode"},
    {"id": "deepseek-v4-pro", "name": "DeepSeek V4 Pro", "desc": "深度推理，代码强", "provider": "opencode"},
    {"id": "glm-5.2", "name": "GLM 5.2", "desc": "通用能力均衡", "provider": "opencode"},
    {"id": "kimi-k3", "name": "Kimi K3", "desc": "长文本，中文友好", "provider": "opencode", "temperature": 1.0},
    {"id": "qwen3.7-max", "name": "Qwen 3.7 Max", "desc": "千问旗舰，综合最强", "provider": "opencode"},
    # ---- 国内直连通道（阿里 token-plan，不需要代理）----
    {"id": "qwen3.8-max-preview", "name": "Qwen 3.8 Max · 国内直连", "desc": "阿里云直连，代理挂了也能用", "provider": "ali"},
    {"id": "deepseek-v4-flash-0731", "name": "DeepSeek V4 Flash · 国内直连", "desc": "阿里云直连，稳定不依赖代理", "provider": "ali"},
]

CATEGORIES: list[str] = ["代码", "中文写作", "翻译", "推理", "通用"]

DEFAULT_TEMPERATURE = 0.7

# ---------------------------------------------------------------------------
# 成本估算：每百万 tokens 美元（占位价，可自行校准；未列出的模型用 DEFAULT_PRICE）
# 仅作相对比较用，不保证与供应商实际计费一致。
# ---------------------------------------------------------------------------

MODEL_PRICES: dict[str, dict[str, float]] = {
    "deepseek-v4-flash": {"in": 0.27, "out": 1.10},
    "deepseek-v4-pro": {"in": 0.60, "out": 2.00},
    "glm-5.2": {"in": 0.45, "out": 1.50},
    "kimi-k3": {"in": 0.50, "out": 1.80},
    "qwen3.7-max": {"in": 0.70, "out": 2.40},
    "qwen3.8-max-preview": {"in": 0.70, "out": 2.40},
    "deepseek-v4-flash-0731": {"in": 0.27, "out": 1.10},
}
DEFAULT_PRICE: dict[str, float] = {"in": 0.60, "out": 1.80}


def estimate_cost(model: str, input_tokens: Optional[int], output_tokens: Optional[int]) -> Optional[float]:
    """按占位单价估算一次生成的成本（美元）。缺 token 数据时返回 None。"""
    if input_tokens is None and output_tokens is None:
        return None
    price = MODEL_PRICES.get(model, DEFAULT_PRICE)
    cost = 0.0
    if input_tokens:
        cost += input_tokens / 1_000_000 * price["in"]
    if output_tokens:
        cost += output_tokens / 1_000_000 * price["out"]
    return round(cost, 6)

# ---------------------------------------------------------------------------
# LLM Judge：自动审判。judge 模型自动避开被评模型。
# ---------------------------------------------------------------------------

# 裁判候选: 优先非推理快模型(推理模型会先消耗大量 token 思考, 不适合做即时裁判)
JUDGE_CANDIDATES = ["deepseek-v4-flash", "glm-5.2", "qwen3.7-max"]

JUDGE_SYSTEM = (
    "你是一个严格的评测裁判。你会收到【问题】和【候选回答】。"
    "请从准确性、完整性、结构清晰度、可用性四个维度评估该回答，"
    "输出 JSON(不要输出其他内容):{\"score\": 1到5的整数, \"reason\": \"一句话理由\"}"
)


def pick_judge(contestant_ids: list[str]) -> str:
    """选一个不在被评模型里的裁判模型。热点全部被评时,回退到第一个候选。"""
    for cand in JUDGE_CANDIDATES:
        if cand not in contestant_ids:
            return cand
    return JUDGE_CANDIDATES[0]


def parse_judge_output(text: str) -> tuple[Optional[int], str]:
    """解析裁判输出: 尽力抽取 JSON; 失败返回 (None, 原文截断)。

    单独成函数是为了可测试(不触网)。
    """
    text = (text or "").strip()
    for candidate in (text,
                      text[text.find("{"): text.rfind("}") + 1] if "{" in text and "}" in text else ""):
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
            score = data.get("score")
            reason = str(data.get("reason", "")).strip()
            if isinstance(score, (int, float)) and 1 <= int(score) <= 5:
                return int(score), reason or "无理由"
        except (json.JSONDecodeError, ValueError):
            continue
    return None, (text[:200] or "裁判输出为空")


async def judge_answer(judge_model: str, question: str, category: str,
                       answer_content: str) -> tuple[Optional[int], str]:
    """让裁判模型给单个回答打分。失败返回 (None, 错误原因)。"""
    if not answer_content:
        return None, "回答为空,不审判"
    payload = {
        "model": judge_model,
        "messages": [
            {"role": "system", "content": JUDGE_SYSTEM},
            {"role": "user", "content": (f"【问题·分类「{category}」】\n{question}\n\n"
                                         f"【候选回答】\n{answer_content[:8000]}")},
        ],
        "temperature": 0.0,
        "max_tokens": 2000,
    }
    try:
        async with _make_client(judge_model) as client:
            resp = await client.post("/chat/completions", json=payload, timeout=90.0)
        if resp.status_code != 200:
            return None, f"裁判调用失败 HTTP {resp.status_code}"
        msg = (resp.json().get("choices") or [{}])[0].get("message") or {}
        # 推理模型会把内容放 reasoning_content, content 为空时回退
        raw = _extract_content(msg.get("content")) or _extract_content(msg.get("reasoning_content")) or ""
        return parse_judge_output(raw)
    except Exception as exc:  # noqa: BLE001
        return None, f"裁判异常: {type(exc).__name__}"


def _temperature(model: str) -> float:
    """返回某模型的采样温度，未配置时用默认值。"""
    for m in MODELS:
        if m["id"] == model:
            return float(m.get("temperature", DEFAULT_TEMPERATURE))
    return DEFAULT_TEMPERATURE


def _provider(model: str) -> dict[str, Any]:
    """返回模型所属通道配置，未知模型回退到 opencode。"""
    pid = "opencode"
    for m in MODELS:
        if m["id"] == model:
            pid = m.get("provider", "opencode")
            break
    return PROVIDERS.get(pid, PROVIDERS["opencode"])


def _api_key(model: str) -> str:
    """读取模型所属通道的 API key，缺失时抛出明确错误。"""
    env_name = _provider(model)["api_key_env"]
    key = os.environ.get(env_name, "").strip()
    if not key:
        raise RuntimeError(f"缺少环境变量 {env_name}（模型 {model} 所属通道）")
    return key


def _make_client(model: str) -> httpx.AsyncClient:
    """按模型通道创建客户端。via_proxy=False 时 trust_env=False 强制直连。"""
    p = _provider(model)
    return httpx.AsyncClient(
        base_url=p["base_url"],
        headers={"Authorization": f"Bearer {_api_key(model)}"},
        trust_env=p["via_proxy"],
        timeout=TIMEOUT,
    )


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
    """并发调用单个模型（流式），内部兜底异常，绝不让错误冒泡。

    流式采集：TTFT（首 token 延迟）、input/output tokens、估算成本。
    """
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
        "stream": True,
    }
    started = time.monotonic()
    empty = {
        "model": model,
        "content": None,
        "tokens": None,
        "latency_ms": 0,
        "error": None,
        "input_tokens": None,
        "output_tokens": None,
        "ttft_ms": None,
        "cost_usd": None,
    }
    try:
        parts: list[str] = []
        reasoning_parts: list[str] = []
        ttft: Optional[float] = None
        usage: dict[str, Any] = {}
        async with client.stream(
            "POST", "/chat/completions", json=payload, timeout=TIMEOUT
        ) as resp:
            if resp.status_code != 200:
                body = (await resp.aread()).decode(errors="replace")[:300]
                empty["latency_ms"] = int((time.monotonic() - started) * 1000)
                empty["error"] = f"HTTP {resp.status_code}: {body}"
                return empty
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if chunk.get("usage"):
                    usage = chunk["usage"]
                dlt = ((chunk.get("choices") or [{}])[0].get("delta") or {})
                reasoning = dlt.get("reasoning_content") or dlt.get("reasoning")
                if reasoning:
                    reasoning_parts.append(_extract_content(reasoning))
                delta = dlt.get("content")
                if not delta:
                    continue
                if ttft is None:
                    ttft = time.monotonic()
                parts.append(_extract_content(delta))
        latency_ms = int((time.monotonic() - started) * 1000)
        content = "".join(p for p in parts if p)
        # 推理模型偶尔只在 reasoning 里给全文, content 为空时回退(截尾部)
        if not content and reasoning_parts:
            content = "".join(reasoning_parts)[-3000:]
        content = content or None
        in_tok = usage.get("input_tokens") or usage.get("prompt_tokens")
        out_tok = usage.get("output_tokens") or usage.get("completion_tokens")
        tokens = usage.get("total_tokens")
        if tokens is None and in_tok and out_tok:
            tokens = in_tok + out_tok
        return {
            "model": model,
            "content": content,
            "tokens": int(tokens) if tokens is not None else None,
            "latency_ms": latency_ms,
            "error": None,
            "input_tokens": int(in_tok) if in_tok is not None else None,
            "output_tokens": int(out_tok) if out_tok is not None else None,
            "ttft_ms": int((ttft - started) * 1000) if ttft else None,
            "cost_usd": estimate_cost(model, in_tok, out_tok),
        }
    except Exception as exc:  # noqa: BLE001 - 单模型失败要隔离，不能冒泡
        empty["latency_ms"] = int((time.monotonic() - started) * 1000)
        empty["error"] = f"{type(exc).__name__}: {exc!r}"
        return empty


async def _run_one(model: str, question: str, category: str) -> dict[str, Any]:
    """为单个模型创建独立客户端并调用（不同通道不同端点/密钥）。"""
    try:
        async with _make_client(model) as client:
            return await _call_one(client, model, question, category)
    except Exception as exc:  # noqa: BLE001
        return {
            "model": model,
            "content": None,
            "tokens": None,
            "latency_ms": 0,
            "error": f"{type(exc).__name__}: {exc!r}",
            "input_tokens": None,
            "output_tokens": None,
            "ttft_ms": None,
            "cost_usd": None,
        }


async def battle(question: str, category: str, models: list[str]) -> list[dict[str, Any]]:
    """对一组模型并发发起评测，返回结果列表（含失败项）。"""
    known = {m["id"] for m in MODELS}
    tasks = [
        _run_one(m, question, category) for m in models if m in known
    ]
    return list(await asyncio.gather(*tasks))


async def ping_model(model: str) -> dict[str, Any]:
    """健康检查：发一条极小的消息确认模型可用。"""
    try:
        async with _make_client(model) as client:
            started = time.monotonic()
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
            "latency_ms": 0,
            "detail": f"{type(exc).__name__}: {exc}",
        }
