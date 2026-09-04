import sqlite3
import time
from contextlib import contextmanager

from bot.config import DB_PATH


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
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
            CREATE TABLE IF NOT EXISTS approved (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                PRIMARY KEY (chat_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS strikes (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                kick_on_next INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (chat_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS banned_packs (
                chat_id INTEGER NOT NULL,
                set_name TEXT NOT NULL,
                PRIMARY KEY (chat_id, set_name)
            );
            CREATE TABLE IF NOT EXISTS allowed_packs (
                chat_id INTEGER NOT NULL,
                set_name TEXT NOT NULL,
                PRIMARY KEY (chat_id, set_name)
            );
            CREATE TABLE IF NOT EXISTS allowed_stickers (
                file_unique_id TEXT PRIMARY KEY
            );
            CREATE TABLE IF NOT EXISTS sticker_cache (
                file_unique_id TEXT PRIMARY KEY,
                is_nsfw INTEGER NOT NULL,
                set_name TEXT
            );
            CREATE TABLE IF NOT EXISTS trusted (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                PRIMARY KEY (chat_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS extra_owners (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                PRIMARY KEY (chat_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS chat_settings (
                chat_id INTEGER PRIMARY KEY,
                placeholder_file_id TEXT,
                log_chat_id INTEGER,
                kick_message TEXT,
                raid_until INTEGER
            );
            CREATE TABLE IF NOT EXISTS kv (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS joins (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                joined_at INTEGER NOT NULL,
                PRIMARY KEY (chat_id, user_id)
            );
            CREATE INDEX IF NOT EXISTS idx_joins_time ON joins(chat_id, joined_at);
            CREATE TABLE IF NOT EXISTS whispers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                from_id INTEGER NOT NULL,
                to_id INTEGER NOT NULL,
                text TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS nicknames (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                nick TEXT NOT NULL,
                PRIMARY KEY (chat_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS members (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                last_seen INTEGER NOT NULL,
                PRIMARY KEY (chat_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS tagall_optout (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                PRIMARY KEY (chat_id, user_id)
            );
            """
        )
        _ensure_column(conn, "chat_settings", "log_chat_id", "INTEGER")
        _ensure_column(conn, "chat_settings", "kick_message", "TEXT")
        _ensure_column(conn, "chat_settings", "raid_until", "INTEGER")
        _ensure_column(conn, "chat_settings", "lock_backup", "TEXT")
        _ensure_column(conn, "chat_settings", "locked", "INTEGER")
        _ensure_column(conn, "chat_settings", "flood_enabled", "INTEGER")
        _ensure_column(conn, "chat_settings", "flood_limit", "INTEGER")
        _ensure_column(conn, "chat_settings", "flood_window", "INTEGER")
        _ensure_column(conn, "chat_settings", "flood_mute", "INTEGER")


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, spec: str) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {spec}")


_SETTINGS_COLS = frozenset(
    {
        "placeholder_file_id",
        "log_chat_id",
        "kick_message",
        "raid_until",
        "lock_backup",
        "locked",
        "flood_enabled",
        "flood_limit",
        "flood_window",
        "flood_mute",
    }
)


def _set_setting(chat_id: int, column: str, value: object) -> None:
    if column not in _SETTINGS_COLS:
        raise ValueError(f"unknown setting: {column}")
    with cursor() as conn:
        conn.execute("INSERT OR IGNORE INTO chat_settings(chat_id) VALUES (?)", (chat_id,))
        conn.execute(
            f"UPDATE chat_settings SET {column}=? WHERE chat_id=?",
            (value, chat_id),
        )


def _get_setting(chat_id: int, column: str):
    if column not in _SETTINGS_COLS:
        raise ValueError(f"unknown setting: {column}")
    with cursor() as conn:
        row = conn.execute(
            f"SELECT {column} FROM chat_settings WHERE chat_id=?",
            (chat_id,),
        ).fetchone()
        if not row:
            return None
        return row[column]


def is_approved(chat_id: int, user_id: int) -> bool:
    with cursor() as conn:
        row = conn.execute(
            "SELECT 1 FROM approved WHERE chat_id=? AND user_id=?",
            (chat_id, user_id),
        ).fetchone()
        return row is not None


def set_approved(chat_id: int, user_id: int, approved: bool) -> None:
    with cursor() as conn:
        if approved:
            conn.execute(
                "INSERT OR IGNORE INTO approved(chat_id, user_id) VALUES (?, ?)",
                (chat_id, user_id),
            )
        else:
            conn.execute(
                "DELETE FROM approved WHERE chat_id=? AND user_id=?",
                (chat_id, user_id),
            )


def get_strikes(chat_id: int, user_id: int) -> tuple[int, bool]:
    with cursor() as conn:
        row = conn.execute(
            "SELECT count, kick_on_next FROM strikes WHERE chat_id=? AND user_id=?",
            (chat_id, user_id),
        ).fetchone()
        if not row:
            return 0, False
        return int(row["count"]), bool(row["kick_on_next"])


def set_strikes(chat_id: int, user_id: int, count: int, kick_on_next: bool) -> None:
    with cursor() as conn:
        conn.execute(
            """
            INSERT INTO strikes(chat_id, user_id, count, kick_on_next)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(chat_id, user_id) DO UPDATE SET
                count=excluded.count,
                kick_on_next=excluded.kick_on_next
            """,
            (chat_id, user_id, count, int(kick_on_next)),
        )


def reset_strikes(chat_id: int, user_id: int) -> None:
    with cursor() as conn:
        conn.execute(
            "DELETE FROM strikes WHERE chat_id=? AND user_id=?",
            (chat_id, user_id),
        )


def pack_banned(chat_id: int, set_name: str | None) -> bool:
    if not set_name:
        return False
    with cursor() as conn:
        row = conn.execute(
            "SELECT 1 FROM banned_packs WHERE chat_id=? AND set_name=?",
            (chat_id, set_name),
        ).fetchone()
        return row is not None


def pack_allowed(chat_id: int, set_name: str | None) -> bool:
    if not set_name:
        return False
    with cursor() as conn:
        row = conn.execute(
            "SELECT 1 FROM allowed_packs WHERE chat_id=? AND set_name=?",
            (chat_id, set_name),
        ).fetchone()
        return row is not None


def ban_pack(chat_id: int, set_name: str) -> None:
    with cursor() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO banned_packs(chat_id, set_name) VALUES (?, ?)",
            (chat_id, set_name),
        )
        conn.execute(
            "DELETE FROM allowed_packs WHERE chat_id=? AND set_name=?",
            (chat_id, set_name),
        )


def allow_pack(chat_id: int, set_name: str) -> None:
    with cursor() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO allowed_packs(chat_id, set_name) VALUES (?, ?)",
            (chat_id, set_name),
        )
        conn.execute(
            "DELETE FROM banned_packs WHERE chat_id=? AND set_name=?",
            (chat_id, set_name),
        )


def sticker_allowed(file_unique_id: str) -> bool:
    with cursor() as conn:
        row = conn.execute(
            "SELECT 1 FROM allowed_stickers WHERE file_unique_id=?",
            (file_unique_id,),
        ).fetchone()
        return row is not None


def allow_sticker(file_unique_id: str) -> None:
    with cursor() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO allowed_stickers(file_unique_id) VALUES (?)",
            (file_unique_id,),
        )
        conn.execute(
            "DELETE FROM sticker_cache WHERE file_unique_id=?",
            (file_unique_id,),
        )


def cache_get(file_unique_id: str) -> bool | None:
    with cursor() as conn:
        row = conn.execute(
            "SELECT is_nsfw FROM sticker_cache WHERE file_unique_id=?",
            (file_unique_id,),
        ).fetchone()
        if row is None:
            return None
        return bool(row["is_nsfw"])


_ID_TABLES = frozenset({"trusted", "extra_owners"})


def list_ids(table: str, chat_id: int) -> list[int]:
    if table not in _ID_TABLES:
        raise ValueError(f"unknown id table: {table}")
    with cursor() as conn:
        rows = conn.execute(
            f"SELECT user_id FROM {table} WHERE chat_id=? ORDER BY user_id",
            (chat_id,),
        ).fetchall()
        return [int(r["user_id"]) for r in rows]


def is_trusted(chat_id: int, user_id: int) -> bool:
    with cursor() as conn:
        row = conn.execute(
            "SELECT 1 FROM trusted WHERE chat_id=? AND user_id=?",
            (chat_id, user_id),
        ).fetchone()
        return row is not None


def set_trusted(chat_id: int, user_id: int, trusted: bool) -> None:
    with cursor() as conn:
        if trusted:
            conn.execute(
                "INSERT OR IGNORE INTO trusted(chat_id, user_id) VALUES (?, ?)",
                (chat_id, user_id),
            )
        else:
            conn.execute(
                "DELETE FROM trusted WHERE chat_id=? AND user_id=?",
                (chat_id, user_id),
            )


def list_trusted(chat_id: int) -> list[int]:
    return list_ids("trusted", chat_id)


def is_extra_owner(chat_id: int, user_id: int) -> bool:
    with cursor() as conn:
        row = conn.execute(
            "SELECT 1 FROM extra_owners WHERE chat_id=? AND user_id=?",
            (chat_id, user_id),
        ).fetchone()
        return row is not None


def set_extra_owner(chat_id: int, user_id: int, owner: bool) -> None:
    with cursor() as conn:
        if owner:
            conn.execute(
                "INSERT OR IGNORE INTO extra_owners(chat_id, user_id) VALUES (?, ?)",
                (chat_id, user_id),
            )
            conn.execute(
                "INSERT OR IGNORE INTO trusted(chat_id, user_id) VALUES (?, ?)",
                (chat_id, user_id),
            )
        else:
            conn.execute(
                "DELETE FROM extra_owners WHERE chat_id=? AND user_id=?",
                (chat_id, user_id),
            )


def list_extra_owners(chat_id: int) -> list[int]:
    return list_ids("extra_owners", chat_id)


def get_placeholder(chat_id: int) -> str | None:
    value = _get_setting(chat_id, "placeholder_file_id")
    return str(value) if value else None


def set_placeholder(chat_id: int, file_id: str | None) -> None:
    _set_setting(chat_id, "placeholder_file_id", file_id)


def get_log_chat(chat_id: int) -> int | None:
    value = _get_setting(chat_id, "log_chat_id")
    if value is None:
        return None
    return int(value)


def set_log_chat(chat_id: int, log_chat_id: int | None) -> None:
    _set_setting(chat_id, "log_chat_id", log_chat_id)


def get_kick_message(chat_id: int) -> str | None:
    value = _get_setting(chat_id, "kick_message")
    return str(value) if value else None


def set_kick_message(chat_id: int, text: str | None) -> None:
    _set_setting(chat_id, "kick_message", text)


def get_raid_until(chat_id: int) -> int:
    value = _get_setting(chat_id, "raid_until")
    if not value:
        return 0
    return int(value)


def set_raid_until(chat_id: int, until_ts: int | None) -> None:
    _set_setting(chat_id, "raid_until", int(until_ts or 0))


def get_lock_backup(chat_id: int) -> str | None:
    value = _get_setting(chat_id, "lock_backup")
    return str(value) if value else None


def set_lock_backup(chat_id: int, raw: str | None) -> None:
    _set_setting(chat_id, "lock_backup", raw)


def is_locked(chat_id: int) -> bool:
    return bool(int(_get_setting(chat_id, "locked") or 0))


def set_locked(chat_id: int, locked: bool) -> None:
    _set_setting(chat_id, "locked", int(locked))


def flood_settings(chat_id: int) -> tuple[bool, int, int, int]:
    enabled = bool(int(_get_setting(chat_id, "flood_enabled") or 0))
    limit = int(_get_setting(chat_id, "flood_limit") or 6)
    window = int(_get_setting(chat_id, "flood_window") or 4)
    mute = int(_get_setting(chat_id, "flood_mute") or 600)
    return enabled, max(3, limit), max(2, window), max(60, mute)


def set_flood_enabled(chat_id: int, enabled: bool) -> None:
    _set_setting(chat_id, "flood_enabled", int(enabled))


def set_flood_limit(chat_id: int, limit: int, window: int) -> None:
    _set_setting(chat_id, "flood_limit", limit)
    _set_setting(chat_id, "flood_window", window)


def set_flood_mute(chat_id: int, seconds: int) -> None:
    _set_setting(chat_id, "flood_mute", seconds)


def record_join(chat_id: int, user_id: int, joined_at: int) -> None:
    with cursor() as conn:
        conn.execute(
            """
            INSERT INTO joins(chat_id, user_id, joined_at)
            VALUES (?, ?, ?)
            ON CONFLICT(chat_id, user_id) DO UPDATE SET
                joined_at=excluded.joined_at
            """,
            (chat_id, user_id, joined_at),
        )


def count_joins_since(chat_id: int, since_ts: int) -> int:
    with cursor() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM joins WHERE chat_id=? AND joined_at>=?",
            (chat_id, since_ts),
        ).fetchone()
        return int(row["n"] if row else 0)


def list_joins_since(chat_id: int, since_ts: int) -> list[int]:
    with cursor() as conn:
        rows = conn.execute(
            "SELECT user_id FROM joins WHERE chat_id=? AND joined_at>=? ORDER BY joined_at",
            (chat_id, since_ts),
        ).fetchall()
        return [int(r["user_id"]) for r in rows]


def delete_join(chat_id: int, user_id: int) -> None:
    with cursor() as conn:
        conn.execute(
            "DELETE FROM joins WHERE chat_id=? AND user_id=?",
            (chat_id, user_id),
        )


def save_whisper(chat_id: int, from_id: int, to_id: int, text: str) -> int:
    now = int(time.time())
    with cursor() as conn:
        conn.execute("DELETE FROM whispers WHERE created_at < ?", (now - 7 * 24 * 3600,))
        cur = conn.execute(
            """
            INSERT INTO whispers(chat_id, from_id, to_id, text, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (chat_id, from_id, to_id, text, now),
        )
        return int(cur.lastrowid)


def get_whisper(whisper_id: int) -> sqlite3.Row | None:
    with cursor() as conn:
        return conn.execute(
            "SELECT id, chat_id, from_id, to_id, text, created_at FROM whispers WHERE id=?",
            (whisper_id,),
        ).fetchone()


def get_nick(chat_id: int, user_id: int) -> str | None:
    with cursor() as conn:
        row = conn.execute(
            "SELECT nick FROM nicknames WHERE chat_id=? AND user_id=?",
            (chat_id, user_id),
        ).fetchone()
        if not row:
            return None
        return str(row["nick"])


def set_nick(chat_id: int, user_id: int, nick: str | None) -> None:
    with cursor() as conn:
        if nick is None or not nick.strip():
            conn.execute(
                "DELETE FROM nicknames WHERE chat_id=? AND user_id=?",
                (chat_id, user_id),
            )
            return
        conn.execute(
            """
            INSERT INTO nicknames(chat_id, user_id, nick)
            VALUES (?, ?, ?)
            ON CONFLICT(chat_id, user_id) DO UPDATE SET nick=excluded.nick
            """,
            (chat_id, user_id, nick.strip()),
        )


def touch_member(chat_id: int, user_id: int) -> None:
    now = int(time.time())
    with cursor() as conn:
        conn.execute(
            """
            INSERT INTO members(chat_id, user_id, last_seen)
            VALUES (?, ?, ?)
            ON CONFLICT(chat_id, user_id) DO UPDATE SET last_seen=excluded.last_seen
            """,
            (chat_id, user_id, now),
        )


def forget_member(chat_id: int, user_id: int) -> None:
    with cursor() as conn:
        conn.execute(
            "DELETE FROM members WHERE chat_id=? AND user_id=?",
            (chat_id, user_id),
        )


def list_member_ids(chat_id: int) -> list[int]:
    with cursor() as conn:
        rows = conn.execute(
            """
            SELECT user_id FROM members WHERE chat_id=?
            UNION
            SELECT user_id FROM joins WHERE chat_id=?
            """,
            (chat_id, chat_id),
        ).fetchall()
        return [int(r["user_id"]) for r in rows]


def is_tagall_optout(chat_id: int, user_id: int) -> bool:
    with cursor() as conn:
        row = conn.execute(
            "SELECT 1 FROM tagall_optout WHERE chat_id=? AND user_id=?",
            (chat_id, user_id),
        ).fetchone()
        return row is not None


def set_tagall_optout(chat_id: int, user_id: int, opted_out: bool) -> None:
    with cursor() as conn:
        if opted_out:
            conn.execute(
                "INSERT OR IGNORE INTO tagall_optout(chat_id, user_id) VALUES (?, ?)",
                (chat_id, user_id),
            )
        else:
            conn.execute(
                "DELETE FROM tagall_optout WHERE chat_id=? AND user_id=?",
                (chat_id, user_id),
            )


def kv_get(key: str) -> str | None:
    with cursor() as conn:
        row = conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        if not row:
            return None
        return str(row["value"])


def kv_set(key: str, value: str | None) -> None:
    with cursor() as conn:
        if value is None:
            conn.execute("DELETE FROM kv WHERE key=?", (key,))
        else:
            conn.execute(
                """
                INSERT INTO kv(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (key, value),
            )


def cache_set(file_unique_id: str, is_nsfw: bool, set_name: str | None) -> None:
    with cursor() as conn:
        conn.execute(
            """
            INSERT INTO sticker_cache(file_unique_id, is_nsfw, set_name)
            VALUES (?, ?, ?)
            ON CONFLICT(file_unique_id) DO UPDATE SET
                is_nsfw=excluded.is_nsfw,
                set_name=excluded.set_name
            """,
            (file_unique_id, int(is_nsfw), set_name),
        )
