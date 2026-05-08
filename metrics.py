import os
import sqlite3
from datetime import datetime, timezone

DB_PATH = os.environ.get("METRICS_DB_PATH", "./chroma_db/metrics.db")

_CREATE = """
CREATE TABLE IF NOT EXISTS messages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp     TEXT    NOT NULL,
    input_tokens  INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    provider      TEXT    NOT NULL
)
"""

def _conn():
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.execute(_CREATE)
    c.commit()
    return c

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
                    DATE(timestamp)   AS day,
                    COUNT(*)          AS messages,
                    SUM(input_tokens) AS input_tokens,
                    SUM(output_tokens)AS output_tokens,
                    provider
                FROM messages
                WHERE timestamp >= DATE('now', :offset)
                GROUP BY day, provider
                ORDER BY day DESC
            """, {"offset": f"-{days} days"}).fetchall()
        return [
            {"day": r[0], "messages": r[1],
             "input_tokens": r[2], "output_tokens": r[3], "provider": r[4]}
            for r in rows
        ]
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
