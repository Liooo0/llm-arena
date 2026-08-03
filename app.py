"""LLM Arena 应用入口：FastAPI 路由 + 静态页面服务。

路由一览：
- POST /api/battle        发起对比评测（并发调用多个模型）
- POST /api/rate          给某个回答打分（1-5，可覆盖）
- GET  /api/leaderboard   排行榜（按平均分，可按分类过滤）
- GET  /api/history       评测历史列表
- GET  /api/history/{id}  单次评测详情
- GET  /api/models        可用模型与分类（静态配置，不暴露 key）
- GET  /、/leaderboard、/history  三个前端页面
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import database
import llm_client

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="LLM Arena 模型竞技场")
database.init_db()


# ---------------------------------------------------------------------------
# 请求体模型
# ---------------------------------------------------------------------------

class BattleRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    category: str = "通用"
    models: list[str] = Field(..., min_length=1)


class RateRequest(BaseModel):
    answer_id: int
    score: int = Field(..., ge=1, le=5)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@app.post("/api/battle")
async def api_battle(body: BattleRequest) -> dict:
    """发起一次对比评测：入库问题，并发调各模型，入库回答，返回结果。"""
    category = body.category if body.category in llm_client.CATEGORIES else "通用"
    # 只保留已知模型并去重
    model_ids = {m["id"] for m in llm_client.MODELS}
    models = list(dict.fromkeys(m for m in body.models if m in model_ids))
    if not models:
        raise HTTPException(status_code=400, detail="没有选择有效的模型")

    question_id = database.create_question(body.question, category)
    results = await llm_client.battle(body.question, category, models)

    answers_out = []
    for r in results:
        answer_id = database.create_answer(
            question_id=question_id,
            model=r["model"],
            content=r["content"],
            tokens=r["tokens"],
            latency_ms=r["latency_ms"],
            error=r["error"],
        )
        answers_out.append(
            {
                "answer_id": answer_id,
                "model": r["model"],
                "content": r["content"],
                "tokens": r["tokens"],
                "latency_ms": r["latency_ms"],
                "error": r["error"],
                "rating": None,
            }
        )

    return {"question_id": question_id, "question": body.question, "category": category, "answers": answers_out}


@app.post("/api/rate")
async def api_rate(body: RateRequest) -> dict:
    """给某个回答打分或覆盖原评分。"""
    if not database.answer_exists(body.answer_id):
        raise HTTPException(status_code=404, detail="回答不存在")
    rating_id = database.upsert_rating(body.answer_id, body.score)
    return {"rating_id": rating_id, "answer_id": body.answer_id, "score": body.score}


@app.get("/api/leaderboard")
async def api_leaderboard(category: Optional[str] = None) -> dict:
    """返回排行榜数据（可按分类过滤）。"""
    if category == "全部" or not category:
        category = None
    rows = database.get_leaderboard(category)
    return {"category": category or "全部", "items": rows}


@app.get("/api/history")
async def api_history() -> dict:
    """返回评测历史列表。"""
    return {"items": database.list_history()}


@app.get("/api/history/{question_id}")
async def api_history_detail(question_id: int) -> dict:
    """返回单次评测的完整详情。"""
    detail = database.get_history_detail(question_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="评测记录不存在")
    return detail


@app.get("/api/models")
async def api_models() -> dict:
    """返回可用模型与分类（不暴露任何 key）。"""
    return {"models": llm_client.MODELS, "categories": llm_client.CATEGORIES}


@app.get("/api/health")
async def api_health() -> dict:
    """并发 ping 所有模型，返回各自可用状态。"""
    return {"items": await _ping_all()}


async def _ping_all() -> list[dict]:
    """并发健康检查（复用 llm_client.battle 之外的独立实现）。"""
    import asyncio

    tasks = [llm_client.ping_model(m["id"]) for m in llm_client.MODELS]
    return list(await asyncio.gather(*tasks))


# ---------------------------------------------------------------------------
# 静态页面
# ---------------------------------------------------------------------------

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
async def page_index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/leaderboard", include_in_schema=False)
async def page_leaderboard() -> FileResponse:
    return FileResponse(STATIC_DIR / "leaderboard.html")


@app.get("/history", include_in_schema=False)
async def page_history() -> FileResponse:
    return FileResponse(STATIC_DIR / "history.html")
