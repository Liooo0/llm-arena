"""SQLite 数据库层：建表 + 读写操作。

四张表：
- questions: 一次评测的问题
- answers:   每个模型对该问题的回答（含耗时/token/错误）
- ratings:   用户对单个回答的打分（answer_id 唯一，可覆盖更新）
- duels:     pairwise 对决记录（用户判断两个回答谁更好）→ 驱动 Elo 排名

约定：每个函数用独立的短连接（with 块自动 close），避免跨线程共享连接问题。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import elo as elo_engine

DB_PATH = Path(__file__).parent / "llm_arena.db"


def _conn() -> sqlite3.Connection:
    """打开一个带 Row factory 的新连接。"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _now() -> str:
    """当前 UTC 时间的 ISO 字符串。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init_db() -> None:
    """建表(幂等)+ 旧库迁移(缺列 ALTER 补齐)。"""
    with _conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS questions (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                text       TEXT NOT NULL,
                category   TEXT NOT NULL DEFAULT '通用',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS answers (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
                model       TEXT NOT NULL,
                content     TEXT,
                tokens      INTEGER,
                latency_ms  INTEGER,
                error       TEXT,
                created_at  TEXT NOT NULL,
                input_tokens   INTEGER,
                output_tokens  INTEGER,
                ttft_ms        INTEGER,
                judge_score    INTEGER CHECK(judge_score BETWEEN 1 AND 5),
                judge_reason   TEXT,
                cost_usd       REAL
            );

            CREATE TABLE IF NOT EXISTS ratings (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                answer_id  INTEGER NOT NULL UNIQUE REFERENCES answers(id) ON DELETE CASCADE,
                score      INTEGER NOT NULL CHECK(score BETWEEN 1 AND 5),
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS duels (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                question_id   INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
                winner_model  TEXT NOT NULL,
                loser_model   TEXT NOT NULL,
                outcome       REAL NOT NULL CHECK(outcome IN (1.0, 0.0, 0.5)),
                created_at    TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS elo_ratings (
                model    TEXT PRIMARY KEY,
                rating   REAL NOT NULL,
                games    INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_answers_question ON answers(question_id);
            CREATE INDEX IF NOT EXISTS idx_answers_model ON answers(model);
            CREATE INDEX IF NOT EXISTS idx_duels_question ON duels(question_id);
            """
        )
        _migrate(conn)


def _migrate(conn: sqlite3.Connection) -> None:
    """旧库迁移: answers 表缺的新列逐个 ALTER 补齐(幂等)。"""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(answers)")}
    additions = {
        "input_tokens": "INTEGER",
        "output_tokens": "INTEGER",
        "ttft_ms": "INTEGER",
        "judge_score": "INTEGER",
        "judge_reason": "TEXT",
        "cost_usd": "REAL",
    }
    for col, typ in additions.items():
        if col not in cols:
            conn.execute(f"ALTER TABLE answers ADD COLUMN {col} {typ}")


# ---------------------------------------------------------------------------
# 写入
# ---------------------------------------------------------------------------

def create_question(text: str, category: str) -> int:
    """插入一个问题，返回其 id。"""
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO questions(text, category, created_at) VALUES (?, ?, ?)",
            (text, category, _now()),
        )
        return int(cur.lastrowid)


def create_answer(
    question_id: int,
    model: str,
    content: Optional[str],
    tokens: Optional[int],
    latency_ms: Optional[int],
    error: Optional[str] = None,
    input_tokens: Optional[int] = None,
    output_tokens: Optional[int] = None,
    ttft_ms: Optional[int] = None,
    cost_usd: Optional[float] = None,
) -> int:
    """插入一条模型回答记录，返回其 id。失败时 content 可为空、error 填原因。"""
    with _conn() as conn:
        cur = conn.execute(
            """INSERT INTO answers
               (question_id, model, content, tokens, latency_ms, error, created_at,
                input_tokens, output_tokens, ttft_ms, cost_usd)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (question_id, model, content, tokens, latency_ms, error, _now(),
             input_tokens, output_tokens, ttft_ms, cost_usd),
        )
        return int(cur.lastrowid)


def update_judge(answer_id: int, score: Optional[int], reason: Optional[str]) -> None:
    """写入 LLM Judge 的结果(可覆盖)。score 为 None 表示本次审判失败。"""
    with _conn() as conn:
        conn.execute(
            "UPDATE answers SET judge_score = ?, judge_reason = ? WHERE id = ?",
            (score, reason, answer_id),
        )


def upsert_rating(answer_id: int, score: int) -> int:
    """写入/覆盖一条评分（answer_id 唯一）。返回 rating id。"""
    score = max(1, min(5, int(score)))
    with _conn() as conn:
        conn.execute(
            """INSERT INTO ratings(answer_id, score, created_at) VALUES (?, ?, ?)
               ON CONFLICT(answer_id)
               DO UPDATE SET score = excluded.score, created_at = excluded.created_at""",
            (answer_id, score, _now()),
        )
        cur = conn.execute("SELECT id FROM ratings WHERE answer_id = ?", (answer_id,))
        return int(cur.fetchone()["id"])


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------

def answer_exists(answer_id: int) -> bool:
    """判断某条回答记录是否存在。"""
    with _conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM answers WHERE id = ?", (answer_id,)
        ).fetchone()
        return row is not None


def answer_model(answer_id: int) -> Optional[str]:
    """返回某回答的模型 id。"""
    with _conn() as conn:
        row = conn.execute(
            "SELECT model FROM answers WHERE id = ?", (answer_id,)
        ).fetchone()
        return str(row["model"]) if row else None


def answer_question(answer_id: int) -> Optional[int]:
    """返回某回答所属 question id。"""
    with _conn() as conn:
        row = conn.execute(
            "SELECT question_id FROM answers WHERE id = ?", (answer_id,)
        ).fetchone()
        return int(row["question_id"]) if row else None


def get_rating(answer_id: int) -> Optional[int]:
    """返回某回答的当前评分（无则 None）。"""
    with _conn() as conn:
        row = conn.execute(
            "SELECT score FROM ratings WHERE answer_id = ?", (answer_id,)
        ).fetchone()
        return int(row["score"]) if row else None


def list_history(limit: int = 50) -> list[dict[str, Any]]:
    """按时间倒序返回评测列表，附模型数、是否已打分等概览。"""
    sql = """
        SELECT q.id, q.text, q.category, q.created_at,
               COUNT(a.id)                                AS answer_count,
               COUNT(r.id)                                AS rated_count
        FROM questions q
        LEFT JOIN answers a ON a.question_id = q.id
        LEFT JOIN ratings r ON r.answer_id = a.id
        GROUP BY q.id
        ORDER BY q.created_at DESC, q.id DESC
        LIMIT ?
    """
    with _conn() as conn:
        rows = conn.execute(sql, (limit,)).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Elo / 对决
# ---------------------------------------------------------------------------

def get_elo(model: str) -> tuple[float, int]:
    """返回某模型的 (elo, 对局数)，无记录时返回起始值。"""
    with _conn() as conn:
        row = conn.execute(
            "SELECT rating, games FROM elo_ratings WHERE model = ?", (model,)
        ).fetchone()
    if not row:
        return elo_engine.START_ELO, 0
    return float(row["rating"]), int(row["games"])


def _set_elo(model: str, rating: float, games: int) -> None:
    with _conn() as conn:
        conn.execute(
            """INSERT INTO elo_ratings(model, rating, games, updated_at) VALUES (?, ?, ?, ?)
               ON CONFLICT(model)
               DO UPDATE SET rating = excluded.rating, games = excluded.games,
                             updated_at = excluded.updated_at""",
            (model, rating, games, _now()),
        )


def record_duel(question_id: int, winner_model: str, loser_model: str,
                outcome: float = 1.0) -> dict[str, Any]:
    """记录一场对决并更新双方 Elo。

    outcome: 1.0 = winner_model 胜, 0.5 = 平局(此时两个参数语义变为 a/b), 0.0 = winner_model 负
    返回 {winner, loser, new_winner_elo, new_loser_elo, outcome}。
    """
    outcome = float(outcome)
    if outcome not in (1.0, 0.0, 0.5):
        raise ValueError("outcome 必须是 1.0 / 0.0 / 0.5")
    # 平局时按传入顺序当 a/b 更新
    ra, ga = get_elo(winner_model)
    rb, gb = get_elo(loser_model)
    new_a, new_b = elo_engine.update_elo(ra, rb, outcome)
    with _conn() as conn:
        conn.execute(
            "INSERT INTO duels(question_id, winner_model, loser_model, outcome, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (question_id, winner_model, loser_model, outcome, _now()),
        )
    _set_elo(winner_model, new_a, ga + 1)
    _set_elo(loser_model, new_b, gb + 1)
    return {
        "winner": winner_model, "loser": loser_model, "outcome": outcome,
        "new_winner_elo": new_a, "new_loser_elo": new_b,
    }


def get_elo_leaderboard(category: Optional[str] = None) -> list[dict[str, Any]]:
    """按 Elo 排序的模型排名（含对局数与评分统计，可按分类过滤）。"""
    where, params = "", []
    if category:
        where = "WHERE q.category = ?"
        params = [category]
    sql = f"""
        SELECT a.model,
               COUNT(DISTINCT a.question_id)                 AS battle_count,
               ROUND(AVG(r.score), 2)                        AS avg_score,
               ROUND(AVG(a.judge_score), 2)                  AS avg_judge,
               ROUND(AVG(a.latency_ms), 0)                   AS avg_latency_ms,
               ROUND(AVG(a.ttft_ms), 0)                      AS avg_ttft_ms,
               ROUND(AVG(a.output_tokens), 0)                AS avg_output_tokens,
               ROUND(SUM(a.cost_usd), 4)                     AS total_cost_usd,
               ROUND(AVG(CASE WHEN a.latency_ms IS NOT NULL AND a.ttft_ms IS NOT NULL
                              THEN a.latency_ms - a.ttft_ms END), 0) AS avg_gen_ms,
               SUM(CASE WHEN a.error IS NOT NULL THEN 1 ELSE 0 END) AS error_count
        FROM answers a
        JOIN questions q ON q.id = a.question_id
        LEFT JOIN ratings r ON r.answer_id = a.id
        {where}
        GROUP BY a.model
    """
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    elo_map = {m: get_elo(m) for m in {r["model"] for r in rows}}
    out = []
    for r in rows:
        d = dict(r)
        rating, games = elo_map.get(d["model"], (elo_engine.START_ELO, 0))
        d["elo"] = rating
        d["duel_count"] = games
        out_tok = d.get("avg_output_tokens")
        gen_ms = d.get("avg_gen_ms")
        d["avg_tps"] = round(out_tok / (gen_ms / 1000), 1) if out_tok and gen_ms else None
        out.append(d)
    # 有对决记录的按 Elo 排,没对决过的按平均分排
    out.sort(key=lambda x: (
        0 if x["duel_count"] > 0 else 1,
        -x["elo"],
        -(x["avg_score"] if x["avg_score"] is not None else -1),
    ))
    return out


def get_history_detail(question_id: int) -> Optional[dict[str, Any]]:
    """返回单次评测的完整详情（问题 + 各模型回答 + 评分）。"""
    with _conn() as conn:
        q = conn.execute(
            "SELECT * FROM questions WHERE id = ?", (question_id,)
        ).fetchone()
        if not q:
            return None
        answers = conn.execute(
            "SELECT * FROM answers WHERE question_id = ? ORDER BY id", (question_id,)
        ).fetchall()
        ratings = conn.execute(
            "SELECT answer_id, score FROM ratings WHERE answer_id IN "
            "(SELECT id FROM answers WHERE question_id = ?)",
            (question_id,),
        ).fetchall()
    rating_map = {r["answer_id"]: r["score"] for r in ratings}
    return {
        "question": dict(q),
        "answers": [
            {
                **dict(a),
                "rating": rating_map.get(a["id"]),
            }
            for a in answers
        ],
    }
