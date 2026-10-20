from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager

from bot.config import DB_PATH

ACTIVE = ("lobby", "live")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@contextmanager
def cursor():
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    with cursor() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                starter_id INTEGER NOT NULL,
                message_id INTEGER,
                payload TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                deadline REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_chat_kind
                ON sessions(chat_id, kind, status);
            CREATE INDEX IF NOT EXISTS idx_sessions_deadline
                ON sessions(deadline);
            CREATE TABLE IF NOT EXISTS results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                user_name TEXT NOT NULL,
                result TEXT NOT NULL,
                finished_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_results_chat
                ON results(chat_id, kind);
            """
        )


def _row(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    try:
        payload = json.loads(row["payload"])
    except json.JSONDecodeError:
        payload = {}
    return {
        "id": str(row["id"]),
        "chat_id": int(row["chat_id"]),
        "kind": str(row["kind"]),
        "status": str(row["status"]),
        "starter_id": int(row["starter_id"]),
        "message_id": int(row["message_id"]) if row["message_id"] is not None else None,
        "payload": payload if isinstance(payload, dict) else {},
        "created_at": float(row["created_at"]),
        "updated_at": float(row["updated_at"]),
        "deadline": float(row["deadline"]),
    }


def active_session(chat_id: int, kind: str) -> dict | None:
    with cursor() as conn:
        row = conn.execute(
            """
            SELECT * FROM sessions
            WHERE chat_id=? AND kind=? AND status IN ('lobby', 'live')
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (chat_id, kind),
        ).fetchone()
    return _row(row)


def create_session(
    session_id: str,
    chat_id: int,
    kind: str,
    starter_id: int,
    payload: dict,
    *,
    deadline: float,
) -> None:
    now = time.time()
    with cursor() as conn:
        conn.execute(
            """
            INSERT INTO sessions(
                id, chat_id, kind, status, starter_id, message_id,
                payload, created_at, updated_at, deadline
            ) VALUES (?, ?, ?, 'lobby', ?, NULL, ?, ?, ?, ?)
            """,
            (session_id, chat_id, kind, starter_id, json.dumps(payload), now, now, deadline),
        )


def load_session(session_id: str) -> dict | None:
    with cursor() as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE id=?", (session_id,)
        ).fetchone()
    return _row(row)


def save_session(
    session_id: str,
    payload: dict,
    *,
    status: str,
    deadline: float,
) -> None:
    with cursor() as conn:
        conn.execute(
            """
            UPDATE sessions
            SET payload=?, status=?, deadline=?, updated_at=?
            WHERE id=?
            """,
            (json.dumps(payload), status, deadline, time.time(), session_id),
        )


def set_message(session_id: str, message_id: int) -> None:
    with cursor() as conn:
        conn.execute(
            "UPDATE sessions SET message_id=? WHERE id=?",
            (message_id, session_id),
        )


def due_sessions(now: float) -> list[dict]:
    with cursor() as conn:
        rows = conn.execute(
            """
            SELECT * FROM sessions
            WHERE status IN ('lobby', 'live') AND deadline <= ?
            ORDER BY deadline ASC
            """,
            (now,),
        ).fetchall()
    return [item for row in rows if (item := _row(row))]


def add_results(rows: list[dict]) -> None:
    if not rows:
        return
    now = time.time()
    with cursor() as conn:
        conn.executemany(
            """
            INSERT INTO results(chat_id, kind, user_id, user_name, result, finished_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    int(row["chat_id"]),
                    str(row["kind"]),
                    int(row["user_id"]),
                    str(row["user_name"]),
                    str(row["result"]),
                    float(row.get("finished_at") or now),
                )
                for row in rows
            ],
        )


def board_counts(chat_id: int) -> list[dict]:
    with cursor() as conn:
        rows = conn.execute(
            """
            SELECT user_id, user_name, kind, result, COUNT(*) AS n, MAX(finished_at) AS last_at
            FROM results
            WHERE chat_id=?
            GROUP BY user_id, kind, result
            """,
            (chat_id,),
        ).fetchall()
    return [
        {
            "user_id": int(row["user_id"]),
            "user_name": str(row["user_name"]),
            "kind": str(row["kind"]),
            "result": str(row["result"]),
            "n": int(row["n"]),
            "last_at": float(row["last_at"] or 0),
        }
        for row in rows
    ]
