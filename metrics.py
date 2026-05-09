import os
import sqlite3
from datetime import datetime, timezone

DB_PATH = os.environ.get("METRICS_DB_PATH", "./chroma_db/metrics.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp     TEXT    NOT NULL,
    input_tokens  INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    provider      TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations (
    id         TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    page_url   TEXT
);
CREATE TABLE IF NOT EXISTS turns (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    timestamp       TEXT NOT NULL,
    user_message    TEXT NOT NULL,
    bot_reply       TEXT NOT NULL,
    input_tokens    INTEGER,
    output_tokens   INTEGER
);
"""

def _conn():
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.executescript(_SCHEMA)
    c.commit()
    return c


# ── Usage logging ─────────────────────────────────────────────────────────────

def log(input_tokens: int, output_tokens: int, provider: str) -> None:
    try:
        with _conn() as c:
            c.execute(
                "INSERT INTO messages (timestamp, input_tokens, output_tokens, provider) VALUES (?,?,?,?)",
                (datetime.now(timezone.utc).isoformat(), input_tokens, output_tokens, provider),
            )
    except Exception as e:
        print(f"[metrics] log error: {e}")

def daily_stats(days: int = 30) -> list[dict]:
    try:
        with _conn() as c:
            rows = c.execute("""
                SELECT
                    DATE(timestamp)    AS day,
                    COUNT(*)           AS messages,
                    SUM(input_tokens)  AS input_tokens,
                    SUM(output_tokens) AS output_tokens,
                    provider
                FROM messages
                WHERE timestamp >= DATE('now', :offset)
                GROUP BY day, provider
                ORDER BY day DESC
            """, {"offset": f"-{days} days"}).fetchall()
        return [{"day": r[0], "messages": r[1], "input_tokens": r[2],
                 "output_tokens": r[3], "provider": r[4]} for r in rows]
    except Exception as e:
        print(f"[metrics] query error: {e}")
        return []

def totals() -> dict:
    try:
        with _conn() as c:
            r = c.execute(
                "SELECT COUNT(*), SUM(input_tokens), SUM(output_tokens) FROM messages"
            ).fetchone()
        return {"messages": r[0] or 0, "input_tokens": r[1] or 0, "output_tokens": r[2] or 0}
    except Exception as e:
        print(f"[metrics] totals error: {e}")
        return {"messages": 0, "input_tokens": 0, "output_tokens": 0}


# ── Conversation logging ───────────────────────────────────────────────────────

def upsert_conversation(session_id: str, page_url: str | None) -> None:
    try:
        with _conn() as c:
            c.execute(
                "INSERT OR IGNORE INTO conversations (id, started_at, page_url) VALUES (?,?,?)",
                (session_id, datetime.now(timezone.utc).isoformat(), page_url),
            )
    except Exception as e:
        print(f"[metrics] upsert_conversation error: {e}")

def log_turn(session_id: str, user_message: str, bot_reply: str,
             input_tokens: int, output_tokens: int) -> None:
    try:
        with _conn() as c:
            c.execute(
                """INSERT INTO turns
                   (conversation_id, timestamp, user_message, bot_reply, input_tokens, output_tokens)
                   VALUES (?,?,?,?,?,?)""",
                (session_id, datetime.now(timezone.utc).isoformat(),
                 user_message, bot_reply, input_tokens, output_tokens),
            )
    except Exception as e:
        print(f"[metrics] log_turn error: {e}")

def get_conversations(limit: int = 50) -> list[dict]:
    try:
        with _conn() as c:
            rows = c.execute("""
                SELECT c.id, c.started_at, c.page_url,
                       COUNT(t.id)      AS turns,
                       MIN(t.user_message) AS first_message
                FROM conversations c
                LEFT JOIN turns t ON t.conversation_id = c.id
                GROUP BY c.id
                ORDER BY c.started_at DESC
                LIMIT ?
            """, (limit,)).fetchall()
        return [{"id": r[0], "started_at": r[1], "page_url": r[2],
                 "turns": r[3], "first_message": r[4]} for r in rows]
    except Exception as e:
        print(f"[metrics] get_conversations error: {e}")
        return []

def delete_oldest_conversations(count: int) -> int:
    try:
        with _conn() as c:
            ids = [r[0] for r in c.execute(
                "SELECT id FROM conversations ORDER BY started_at ASC LIMIT ?", (count,)
            ).fetchall()]
            if not ids:
                return 0
            placeholders = ",".join("?" * len(ids))
            c.execute(f"DELETE FROM turns WHERE conversation_id IN ({placeholders})", ids)
            c.execute(f"DELETE FROM conversations WHERE id IN ({placeholders})", ids)
        return len(ids)
    except Exception as e:
        print(f"[metrics] delete_oldest_conversations error: {e}")
        return 0

def get_turns(session_id: str) -> list[dict]:
    try:
        with _conn() as c:
            rows = c.execute(
                "SELECT timestamp, user_message, bot_reply FROM turns WHERE conversation_id=? ORDER BY id",
                (session_id,)
            ).fetchall()
        return [{"timestamp": r[0], "user_message": r[1], "bot_reply": r[2]} for r in rows]
    except Exception as e:
        print(f"[metrics] get_turns error: {e}")
        return []
