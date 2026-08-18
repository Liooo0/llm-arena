"""LLM Judge / 成本估算 / 流式指标 / 数据库迁移 的离线测试(全部不触网)。"""
import asyncio
import json
import sqlite3

import httpx

import database
import llm_client

# ---------------------------------------------------------------------------
# parse_judge_output(纯函数)
# ---------------------------------------------------------------------------

def test_parse_pure_json():
    assert llm_client.parse_judge_output('{"score": 4, "reason": "结构清晰"}') == (4, "结构清晰")


def test_parse_json_with_noise():
    out = '好的，以下是评分：{"score":5,"reason":"很好"}'
    assert llm_client.parse_judge_output(out) == (5, "很好")


def test_parse_out_of_range():
    score, _ = llm_client.parse_judge_output('{"score": 9, "reason": "x"}')
    assert score is None


def test_parse_garbage():
    score, reason = llm_client.parse_judge_output("no json at all")
    assert score is None and "no json" in reason


def test_parse_empty():
    score, reason = llm_client.parse_judge_output("")
    assert score is None and reason


# ---------------------------------------------------------------------------
# pick_judge(纯函数)
# ---------------------------------------------------------------------------

def test_pick_judge_avoids_contestants():
    assert llm_client.pick_judge(["deepseek-v4-pro", "kimi-k3"]) == "deepseek-v4-flash"


def test_pick_judge_skips_contesting_candidates():
    assert llm_client.pick_judge(["deepseek-v4-flash", "glm-5.2"]) == "qwen3.7-max"


def test_pick_judge_fallback_when_all_contesting():
    assert llm_client.pick_judge(list(llm_client.JUDGE_CANDIDATES)) == llm_client.JUDGE_CANDIDATES[0]


# ---------------------------------------------------------------------------
# estimate_cost(纯函数)
# ---------------------------------------------------------------------------

def test_estimate_cost_known_model():
    c = llm_client.estimate_cost("deepseek-v4-flash", 1000, 1000)
    assert c == round(1000 / 1_000_000 * 0.27 + 1000 / 1_000_000 * 1.10, 6)


def test_estimate_cost_missing_tokens():
    assert llm_client.estimate_cost("whatever", None, None) is None


def test_estimate_cost_unknown_model_uses_default():
    c = llm_client.estimate_cost("no-such-model", 1_000_000, 0)
    assert c == llm_client.DEFAULT_PRICE["in"]


# ---------------------------------------------------------------------------
# 流式指标(httpx MockTransport, 模拟 SSE 上游)
# ---------------------------------------------------------------------------

def _streaming_http_handler():
    async def handler(request):
        payload = json.loads(request.content)
        assert payload.get("stream") is True

        async def body():
            yield b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n'
            yield b'data: {"choices":[{"delta":{"content":" world"}}]}\n\n'
            yield b'data: {"choices":[{"delta":{}}],"usage":{"prompt_tokens":5,"completion_tokens":6,"total_tokens":11}}\n\n'
            yield b'data: [DONE]\n\n'

        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, content=body())

    return handler


def test_call_one_streaming_metrics():
    async def main():
        transport = httpx.MockTransport(_streaming_http_handler())
        async with httpx.AsyncClient(transport=transport, base_url="http://x") as client:
            return await llm_client._call_one(client, "deepseek-v4-pro", "hi", "通用")

    r = asyncio.run(main())
    assert r["content"] == "hello world"
    assert r["tokens"] == 11
    assert r["input_tokens"] == 5
    assert r["output_tokens"] == 6
    assert r["ttft_ms"] is not None and r["latency_ms"] >= r["ttft_ms"]
    assert r["cost_usd"] is not None and r["cost_usd"] > 0


def test_call_one_error_isolation():
    async def main():
        async def handler(request):
            return httpx.Response(500, content=b"boom")

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport, base_url="http://x") as client:
            return await llm_client._call_one(client, "glm-5.2", "hi", "通用")

    r = asyncio.run(main())
    assert r["content"] is None and "HTTP 500" in r["error"]
    assert r["latency_ms"] >= 0


# ---------------------------------------------------------------------------
# 数据库迁移与 judge 读写(mock DB_PATH 到临时文件)
# ---------------------------------------------------------------------------

def test_migrate_old_answers_schema(tmp_path, monkeypatch):
    db = tmp_path / "arena.db"
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE answers (
        id INTEGER PRIMARY KEY, question_id INTEGER, model TEXT, content TEXT,
        tokens INTEGER, latency_ms INTEGER, error TEXT, created_at TEXT)""")
    conn.commit()
    conn.close()
    monkeypatch.setattr(database, "DB_PATH", db)
    database.init_db()  # executescript 会补其余表, _migrate 会补 answers 列
    conn = sqlite3.connect(db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(answers)")}
    conn.close()
    assert {"input_tokens", "output_tokens", "ttft_ms",
            "judge_score", "judge_reason", "cost_usd"} <= cols


def test_judge_roundtrip_and_leaderboard(tmp_path, monkeypatch):
    db = tmp_path / "arena.db"
    monkeypatch.setattr(database, "DB_PATH", db)
    database.init_db()
    qid = database.create_question("测试问题", "通用")
    aid = database.create_answer(
        qid, "m1", "答", 10, 1000, None,
        input_tokens=5, output_tokens=7, ttft_ms=200, cost_usd=0.0001,
    )
    database.update_judge(aid, 4, "不错")
    detail = database.get_history_detail(qid)
    a = detail["answers"][0]
    assert a["judge_score"] == 4 and a["judge_reason"] == "不错"
    assert a["input_tokens"] == 5 and a["ttft_ms"] == 200

    lb = database.get_elo_leaderboard()
    row = [r for r in lb if r["model"] == "m1"][0]
    assert row["avg_judge"] == 4.0
    assert row["avg_ttft_ms"] == 200.0
    assert row["total_cost_usd"] == 0.0001
    # tps = output 7 / 生成期(1000-200=800ms) = 8.75 → round 半偶 = 8.8
    assert row["avg_tps"] == 8.8