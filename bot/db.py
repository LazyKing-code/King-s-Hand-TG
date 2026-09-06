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
            CREATE TABLE IF NOT EXISTS banned_stickers (
                chat_id INTEGER NOT NULL,
                file_unique_id TEXT NOT NULL,
                PRIMARY KEY (chat_id, file_unique_id)
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
            CREATE TABLE IF NOT EXISTS notes (
                chat_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                text TEXT NOT NULL,
                PRIMARY KEY (chat_id, name)
            );
            CREATE TABLE IF NOT EXISTS filters (
                chat_id INTEGER NOT NULL,
                keyword TEXT NOT NULL,
                text TEXT NOT NULL,
                PRIMARY KEY (chat_id, keyword)
            );
            CREATE TABLE IF NOT EXISTS blacklist (
                chat_id INTEGER NOT NULL,
                word TEXT NOT NULL,
                PRIMARY KEY (chat_id, word)
            );
            CREATE TABLE IF NOT EXISTS warns (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (chat_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS pending_verify (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                joined_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                prompt_msg_id INTEGER,
                PRIMARY KEY (chat_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS bot_users (
                user_id INTEGER PRIMARY KEY,
                started_at INTEGER NOT NULL,
                last_seen INTEGER NOT NULL,
                last_release TEXT
            );
            CREATE TABLE IF NOT EXISTS game_wallet (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                coins INTEGER NOT NULL DEFAULT 0,
                streak INTEGER NOT NULL DEFAULT 0,
                last_daily TEXT,
                PRIMARY KEY (chat_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS game_stats (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                wins INTEGER NOT NULL DEFAULT 0,
                losses INTEGER NOT NULL DEFAULT 0,
                points INTEGER NOT NULL DEFAULT 0,
                day_points INTEGER NOT NULL DEFAULT 0,
                week_points INTEGER NOT NULL DEFAULT 0,
                day_key TEXT,
                week_key TEXT,
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
        _ensure_column(conn, "chat_settings", "welcome_text", "TEXT")
        _ensure_column(conn, "chat_settings", "welcome_on", "INTEGER")
        _ensure_column(conn, "chat_settings", "welcome_kind", "TEXT")
        _ensure_column(conn, "chat_settings", "welcome_file_id", "TEXT")
        _ensure_column(conn, "chat_settings", "goodbye_text", "TEXT")
        _ensure_column(conn, "chat_settings", "goodbye_on", "INTEGER")
        _ensure_column(conn, "chat_settings", "rules_text", "TEXT")
        _ensure_column(conn, "chat_settings", "clean_service", "INTEGER")
        _ensure_column(conn, "chat_settings", "warn_limit", "INTEGER")
        _ensure_column(conn, "chat_settings", "verify_on", "INTEGER")
        _ensure_column(conn, "chat_settings", "verify_secs", "INTEGER")
        _ensure_column(conn, "chat_settings", "verify_ban_bots", "INTEGER")
        _ensure_column(conn, "chat_settings", "zombies_daily", "INTEGER")


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
        "welcome_text",
        "welcome_on",
        "welcome_kind",
        "welcome_file_id",
        "goodbye_text",
        "goodbye_on",
        "rules_text",
        "clean_service",
        "warn_limit",
        "verify_on",
        "verify_secs",
        "verify_ban_bots",
        "zombies_daily",
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


def list_banned_packs(chat_id: int) -> list[str]:
    with cursor() as conn:
        rows = conn.execute(
            "SELECT set_name FROM banned_packs WHERE chat_id=? ORDER BY set_name COLLATE NOCASE",
            (chat_id,),
        ).fetchall()
        return [str(r["set_name"]) for r in rows]


def list_allowed_packs(chat_id: int) -> list[str]:
    with cursor() as conn:
        rows = conn.execute(
            "SELECT set_name FROM allowed_packs WHERE chat_id=? ORDER BY set_name COLLATE NOCASE",
            (chat_id,),
        ).fetchall()
        return [str(r["set_name"]) for r in rows]


def unallow_pack(chat_id: int, set_name: str) -> bool:
    with cursor() as conn:
        cur = conn.execute(
            "DELETE FROM allowed_packs WHERE chat_id=? AND set_name=?",
            (chat_id, set_name),
        )
        return cur.rowcount > 0


def ban_sticker(chat_id: int, file_unique_id: str) -> None:
    with cursor() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO banned_stickers(chat_id, file_unique_id) VALUES (?, ?)",
            (chat_id, file_unique_id),
        )


def sticker_banned(chat_id: int, file_unique_id: str) -> bool:
    with cursor() as conn:
        row = conn.execute(
            "SELECT 1 FROM banned_stickers WHERE chat_id=? AND file_unique_id=?",
            (chat_id, file_unique_id),
        ).fetchone()
        return row is not None


def list_banned_stickers(chat_id: int) -> list[str]:
    with cursor() as conn:
        rows = conn.execute(
            "SELECT file_unique_id FROM banned_stickers WHERE chat_id=? ORDER BY file_unique_id",
            (chat_id,),
        ).fetchall()
        return [str(r["file_unique_id"]) for r in rows]


def unban_sticker(chat_id: int, file_unique_id: str) -> bool:
    with cursor() as conn:
        cur = conn.execute(
            "DELETE FROM banned_stickers WHERE chat_id=? AND file_unique_id=?",
            (chat_id, file_unique_id),
        )
        conn.execute(
            "DELETE FROM sticker_cache WHERE file_unique_id=?",
            (file_unique_id,),
        )
        return cur.rowcount > 0


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


def list_known_chat_ids() -> list[int]:
    with cursor() as conn:
        rows = conn.execute(
            """
            SELECT chat_id FROM chat_settings
            UNION
            SELECT chat_id FROM members
            UNION
            SELECT chat_id FROM joins
            """
        ).fetchall()
        return [int(r["chat_id"]) for r in rows]


def zombies_daily(chat_id: int) -> bool:
    raw = _get_setting(chat_id, "zombies_daily")
    if raw is None:
        return True
    return bool(int(raw))


def set_zombies_daily(chat_id: int, enabled: bool) -> None:
    _set_setting(chat_id, "zombies_daily", int(enabled))


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


def get_welcome(chat_id: int) -> tuple[bool, str | None, str | None, str | None]:
    text = _get_setting(chat_id, "welcome_text")
    kind = _get_setting(chat_id, "welcome_kind")
    file_id = _get_setting(chat_id, "welcome_file_id")
    on_raw = _get_setting(chat_id, "welcome_on")
    enabled = bool(int(on_raw)) if on_raw is not None else bool(text or file_id)
    return enabled, (str(text) if text else None), (str(kind) if kind else None), (
        str(file_id) if file_id else None
    )


def set_welcome(
    chat_id: int,
    *,
    text: str | None = None,
    kind: str | None = None,
    file_id: str | None = None,
    enabled: bool | None = None,
) -> None:
    if text is not None:
        _set_setting(chat_id, "welcome_text", text or None)
    if kind is not None:
        _set_setting(chat_id, "welcome_kind", kind or None)
    if file_id is not None:
        _set_setting(chat_id, "welcome_file_id", file_id or None)
    if enabled is not None:
        _set_setting(chat_id, "welcome_on", int(enabled))


def clear_welcome(chat_id: int) -> None:
    _set_setting(chat_id, "welcome_text", None)
    _set_setting(chat_id, "welcome_kind", None)
    _set_setting(chat_id, "welcome_file_id", None)
    _set_setting(chat_id, "welcome_on", 0)


def get_goodbye(chat_id: int) -> tuple[bool, str | None]:
    text = _get_setting(chat_id, "goodbye_text")
    on_raw = _get_setting(chat_id, "goodbye_on")
    enabled = bool(int(on_raw)) if on_raw is not None else bool(text)
    return enabled, (str(text) if text else None)


def set_goodbye(chat_id: int, text: str | None = None, enabled: bool | None = None) -> None:
    if text is not None:
        _set_setting(chat_id, "goodbye_text", text or None)
    if enabled is not None:
        _set_setting(chat_id, "goodbye_on", int(enabled))


def get_rules(chat_id: int) -> str | None:
    value = _get_setting(chat_id, "rules_text")
    return str(value) if value else None


def set_rules(chat_id: int, text: str | None) -> None:
    _set_setting(chat_id, "rules_text", text)


def clean_service(chat_id: int) -> bool:
    return bool(int(_get_setting(chat_id, "clean_service") or 0))


def set_clean_service(chat_id: int, enabled: bool) -> None:
    _set_setting(chat_id, "clean_service", int(enabled))


def warn_limit(chat_id: int) -> int:
    return max(2, min(20, int(_get_setting(chat_id, "warn_limit") or 3)))


def set_warn_limit(chat_id: int, limit: int) -> None:
    _set_setting(chat_id, "warn_limit", max(2, min(20, limit)))


def verify_enabled(chat_id: int) -> bool:
    raw = _get_setting(chat_id, "verify_on")
    if raw is None:
        return True
    return bool(int(raw))


def set_verify_enabled(chat_id: int, enabled: bool) -> None:
    _set_setting(chat_id, "verify_on", int(enabled))


def verify_secs(chat_id: int) -> int:
    return max(60, min(3600, int(_get_setting(chat_id, "verify_secs") or 300)))


def set_verify_secs(chat_id: int, seconds: int) -> None:
    _set_setting(chat_id, "verify_secs", max(60, min(3600, seconds)))


def verify_ban_bots(chat_id: int) -> bool:
    raw = _get_setting(chat_id, "verify_ban_bots")
    if raw is None:
        return True
    return bool(int(raw))


def set_verify_ban_bots(chat_id: int, ban: bool) -> None:
    _set_setting(chat_id, "verify_ban_bots", int(ban))


def add_pending_verify(
    chat_id: int,
    user_id: int,
    joined_at: int,
    expires_at: int,
    prompt_msg_id: int | None,
) -> None:
    with cursor() as conn:
        conn.execute(
            """
            INSERT INTO pending_verify(chat_id, user_id, joined_at, expires_at, prompt_msg_id)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(chat_id, user_id) DO UPDATE SET
                joined_at=excluded.joined_at,
                expires_at=excluded.expires_at,
                prompt_msg_id=excluded.prompt_msg_id
            """,
            (chat_id, user_id, joined_at, expires_at, prompt_msg_id),
        )


def get_pending_verify(chat_id: int, user_id: int) -> dict | None:
    with cursor() as conn:
        row = conn.execute(
            "SELECT chat_id, user_id, joined_at, expires_at, prompt_msg_id "
            "FROM pending_verify WHERE chat_id=? AND user_id=?",
            (chat_id, user_id),
        ).fetchone()
        if not row:
            return None
        return {
            "chat_id": int(row["chat_id"]),
            "user_id": int(row["user_id"]),
            "joined_at": int(row["joined_at"]),
            "expires_at": int(row["expires_at"]),
            "prompt_msg_id": int(row["prompt_msg_id"] or 0) or None,
        }


def list_pending_verify(chat_id: int) -> list[dict]:
    with cursor() as conn:
        rows = conn.execute(
            "SELECT user_id, joined_at, expires_at, prompt_msg_id "
            "FROM pending_verify WHERE chat_id=? ORDER BY joined_at",
            (chat_id,),
        ).fetchall()
        return [
            {
                "user_id": int(r["user_id"]),
                "joined_at": int(r["joined_at"]),
                "expires_at": int(r["expires_at"]),
                "prompt_msg_id": int(r["prompt_msg_id"] or 0) or None,
            }
            for r in rows
        ]


def list_all_pending_verify() -> list[dict]:
    with cursor() as conn:
        rows = conn.execute(
            "SELECT chat_id, user_id, expires_at FROM pending_verify"
        ).fetchall()
        return [
            {
                "chat_id": int(r["chat_id"]),
                "user_id": int(r["user_id"]),
                "expires_at": int(r["expires_at"]),
            }
            for r in rows
        ]


def clear_pending_verify(chat_id: int, user_id: int) -> dict | None:
    row = get_pending_verify(chat_id, user_id)
    if not row:
        return None
    with cursor() as conn:
        conn.execute(
            "DELETE FROM pending_verify WHERE chat_id=? AND user_id=?",
            (chat_id, user_id),
        )
    return row


def get_warns(chat_id: int, user_id: int) -> int:
    with cursor() as conn:
        row = conn.execute(
            "SELECT count FROM warns WHERE chat_id=? AND user_id=?",
            (chat_id, user_id),
        ).fetchone()
        return int(row["count"]) if row else 0


def set_warns(chat_id: int, user_id: int, count: int) -> None:
    with cursor() as conn:
        if count <= 0:
            conn.execute(
                "DELETE FROM warns WHERE chat_id=? AND user_id=?",
                (chat_id, user_id),
            )
            return
        conn.execute(
            """
            INSERT INTO warns(chat_id, user_id, count)
            VALUES (?, ?, ?)
            ON CONFLICT(chat_id, user_id) DO UPDATE SET count=excluded.count
            """,
            (chat_id, user_id, count),
        )


def _upsert_named(table: str, chat_id: int, name: str, text: str) -> None:
    with cursor() as conn:
        conn.execute(
            f"""
            INSERT INTO {table}(chat_id, name, text)
            VALUES (?, ?, ?)
            ON CONFLICT(chat_id, name) DO UPDATE SET text=excluded.text
            """,
            (chat_id, name, text),
        )


def save_note(chat_id: int, name: str, text: str) -> None:
    _upsert_named("notes", chat_id, name, text)


def get_note(chat_id: int, name: str) -> str | None:
    with cursor() as conn:
        row = conn.execute(
            "SELECT text FROM notes WHERE chat_id=? AND name=?",
            (chat_id, name),
        ).fetchone()
        return str(row["text"]) if row else None


def delete_note(chat_id: int, name: str) -> bool:
    with cursor() as conn:
        cur = conn.execute(
            "DELETE FROM notes WHERE chat_id=? AND name=?",
            (chat_id, name),
        )
        return cur.rowcount > 0


def list_notes(chat_id: int) -> list[str]:
    with cursor() as conn:
        rows = conn.execute(
            "SELECT name FROM notes WHERE chat_id=? ORDER BY name",
            (chat_id,),
        ).fetchall()
        return [str(r["name"]) for r in rows]


def save_filter(chat_id: int, keyword: str, text: str) -> None:
    with cursor() as conn:
        conn.execute(
            """
            INSERT INTO filters(chat_id, keyword, text)
            VALUES (?, ?, ?)
            ON CONFLICT(chat_id, keyword) DO UPDATE SET text=excluded.text
            """,
            (chat_id, keyword, text),
        )


def delete_filter(chat_id: int, keyword: str) -> bool:
    with cursor() as conn:
        cur = conn.execute(
            "DELETE FROM filters WHERE chat_id=? AND keyword=?",
            (chat_id, keyword),
        )
        return cur.rowcount > 0


def list_filters(chat_id: int) -> list[tuple[str, str]]:
    with cursor() as conn:
        rows = conn.execute(
            "SELECT keyword, text FROM filters WHERE chat_id=? ORDER BY keyword",
            (chat_id,),
        ).fetchall()
        return [(str(r["keyword"]), str(r["text"])) for r in rows]


def add_blacklist(chat_id: int, word: str) -> None:
    with cursor() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO blacklist(chat_id, word) VALUES (?, ?)",
            (chat_id, word),
        )


def remove_blacklist(chat_id: int, word: str) -> bool:
    with cursor() as conn:
        cur = conn.execute(
            "DELETE FROM blacklist WHERE chat_id=? AND word=?",
            (chat_id, word),
        )
        return cur.rowcount > 0


def list_blacklist(chat_id: int) -> list[str]:
    with cursor() as conn:
        rows = conn.execute(
            "SELECT word FROM blacklist WHERE chat_id=? ORDER BY word",
            (chat_id,),
        ).fetchall()
        return [str(r["word"]) for r in rows]


def get_wallet(chat_id: int, user_id: int) -> tuple[int, int, str | None]:
    with cursor() as conn:
        row = conn.execute(
            "SELECT coins, streak, last_daily FROM game_wallet WHERE chat_id=? AND user_id=?",
            (chat_id, user_id),
        ).fetchone()
        if not row:
            return 0, 0, None
        return int(row["coins"]), int(row["streak"]), (str(row["last_daily"]) if row["last_daily"] else None)


def set_wallet(chat_id: int, user_id: int, coins: int, streak: int, last_daily: str | None) -> None:
    with cursor() as conn:
        conn.execute(
            """
            INSERT INTO game_wallet(chat_id, user_id, coins, streak, last_daily)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(chat_id, user_id) DO UPDATE SET
                coins=excluded.coins,
                streak=excluded.streak,
                last_daily=excluded.last_daily
            """,
            (chat_id, user_id, max(0, coins), streak, last_daily),
        )


def add_coins(chat_id: int, user_id: int, delta: int) -> int:
    coins, streak, last = get_wallet(chat_id, user_id)
    coins = max(0, coins + delta)
    set_wallet(chat_id, user_id, coins, streak, last)
    return coins


def get_game_stats(chat_id: int, user_id: int) -> dict:
    with cursor() as conn:
        row = conn.execute(
            "SELECT wins, losses, points, day_points, week_points, day_key, week_key "
            "FROM game_stats WHERE chat_id=? AND user_id=?",
            (chat_id, user_id),
        ).fetchone()
        if not row:
            return {
                "wins": 0,
                "losses": 0,
                "points": 0,
                "day_points": 0,
                "week_points": 0,
                "day_key": None,
                "week_key": None,
            }
        return {
            "wins": int(row["wins"]),
            "losses": int(row["losses"]),
            "points": int(row["points"]),
            "day_points": int(row["day_points"]),
            "week_points": int(row["week_points"]),
            "day_key": str(row["day_key"]) if row["day_key"] else None,
            "week_key": str(row["week_key"]) if row["week_key"] else None,
        }


def record_game(
    chat_id: int,
    user_id: int,
    *,
    points: int,
    win: bool | None,
    day_key: str,
    week_key: str,
) -> None:
    st = get_game_stats(chat_id, user_id)
    day_pts = st["day_points"] if st["day_key"] == day_key else 0
    week_pts = st["week_points"] if st["week_key"] == week_key else 0
    wins = st["wins"] + (1 if win is True else 0)
    losses = st["losses"] + (1 if win is False else 0)
    with cursor() as conn:
        conn.execute(
            """
            INSERT INTO game_stats(
                chat_id, user_id, wins, losses, points,
                day_points, week_points, day_key, week_key
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id, user_id) DO UPDATE SET
                wins=excluded.wins,
                losses=excluded.losses,
                points=excluded.points,
                day_points=excluded.day_points,
                week_points=excluded.week_points,
                day_key=excluded.day_key,
                week_key=excluded.week_key
            """,
            (
                chat_id,
                user_id,
                wins,
                losses,
                st["points"] + points,
                day_pts + points,
                week_pts + points,
                day_key,
                week_key,
            ),
        )


def list_game_top(
    chat_id: int,
    field: str,
    limit: int = 10,
    *,
    period_key: str | None = None,
) -> list[tuple[int, int]]:
    if field not in {"points", "day_points", "week_points", "wins"}:
        field = "points"
    extra = ""
    params: list = [chat_id]
    if field == "day_points" and period_key:
        extra = " AND day_key=?"
        params.append(period_key)
    elif field == "week_points" and period_key:
        extra = " AND week_key=?"
        params.append(period_key)
    params.append(limit)
    with cursor() as conn:
        rows = conn.execute(
            f"""
            SELECT user_id, {field} AS score FROM game_stats
            WHERE chat_id=? AND {field} > 0{extra}
            ORDER BY {field} DESC, user_id
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [(int(r["user_id"]), int(r["score"])) for r in rows]


def touch_bot_user(user_id: int) -> None:
    now = int(time.time())
    with cursor() as conn:
        conn.execute(
            """
            INSERT INTO bot_users(user_id, started_at, last_seen, last_release)
            VALUES (?, ?, ?, NULL)
            ON CONFLICT(user_id) DO UPDATE SET last_seen=excluded.last_seen
            """,
            (user_id, now, now),
        )


def list_bot_users() -> list[int]:
    with cursor() as conn:
        rows = conn.execute("SELECT user_id FROM bot_users ORDER BY user_id").fetchall()
        return [int(r["user_id"]) for r in rows]


def bot_user_release(user_id: int) -> str | None:
    with cursor() as conn:
        row = conn.execute(
            "SELECT last_release FROM bot_users WHERE user_id=?",
            (user_id,),
        ).fetchone()
        if not row or row["last_release"] is None:
            return None
        return str(row["last_release"])


def set_bot_user_release(user_id: int, release_id: str) -> None:
    now = int(time.time())
    with cursor() as conn:
        conn.execute(
            """
            INSERT INTO bot_users(user_id, started_at, last_seen, last_release)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                last_release=excluded.last_release,
                last_seen=excluded.last_seen
            """,
            (user_id, now, now, release_id),
        )


def drop_bot_user(user_id: int) -> None:
    with cursor() as conn:
        conn.execute("DELETE FROM bot_users WHERE user_id=?", (user_id,))
