"""SQLite 数据库层：建表 + 读写操作。

三张表：
- questions: 一次评测的问题
- answers:   每个模型对该问题的回答（含耗时/token/错误）
- ratings:   用户对单个回答的打分（answer_id 唯一，可覆盖更新）

约定：每个函数用独立的短连接（with 块自动 close），避免跨线程共享连接问题。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

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
    """建表（幂等）。"""
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
                created_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ratings (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                answer_id  INTEGER NOT NULL UNIQUE REFERENCES answers(id) ON DELETE CASCADE,
                score      INTEGER NOT NULL CHECK(score BETWEEN 1 AND 5),
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_answers_question ON answers(question_id);
            CREATE INDEX IF NOT EXISTS idx_answers_model ON answers(model);
            """
        )


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
) -> int:
    """插入一条模型回答记录，返回其 id。失败时 content 可为空、error 填原因。"""
    with _conn() as conn:
        cur = conn.execute(
            """INSERT INTO answers
               (question_id, model, content, tokens, latency_ms, error, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (question_id, model, content, tokens, latency_ms, error, _now()),
        )
        return int(cur.lastrowid)


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


def get_rating(answer_id: int) -> Optional[int]:
    """返回某回答的当前评分（无则 None）。"""
    with _conn() as conn:
        row = conn.execute(
            "SELECT score FROM ratings WHERE answer_id = ?", (answer_id,)
        ).fetchone()
        return int(row["score"]) if row else None


def get_leaderboard(category: Optional[str] = None) -> list[dict[str, Any]]:
    """按模型聚合：对比次数、平均分、平均耗时、平均 token。可按分类过滤。"""
    where, params = "", []
    if category:
        where = "WHERE q.category = ?"
        params = [category]

    sql = f"""
        SELECT a.model,
               COUNT(DISTINCT a.question_id)                 AS battle_count,
               ROUND(AVG(r.score), 2)                        AS avg_score,
               ROUND(AVG(a.latency_ms), 0)                   AS avg_latency_ms,
               ROUND(AVG(a.tokens), 0)                       AS avg_tokens,
               SUM(CASE WHEN a.error IS NOT NULL THEN 1 ELSE 0 END) AS error_count
        FROM answers a
        JOIN questions q ON q.id = a.question_id
        LEFT JOIN ratings r ON r.answer_id = a.id
        {where}
        GROUP BY a.model
        ORDER BY avg_score DESC NULLS LAST, battle_count DESC
    """
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


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
