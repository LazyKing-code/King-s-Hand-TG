"""Copy the bot SQLite database to a timestamped backup file.

Usage (from repo root):
    python scripts/backup_db.py
    python scripts/backup_db.py --out backups/

On Railway, prefer downloading via the owner command /backupdb in a private
chat with the bot, or run this against a volume mount / copied bot.db.
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.config import DB_PATH  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("backups"),
        help="Directory for backup files (created if missing)",
    )
    args = parser.parse_args()

    if not DB_PATH.is_file():
        sys.exit(f"No database at {DB_PATH}")

    # Best-effort WAL checkpoint so the copied main file is complete.
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
    except sqlite3.Error as exc:
        print(f"Warning: wal_checkpoint failed ({exc}); copying anyway.")

    args.out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    dest = args.out / f"bot-{stamp}.db"
    shutil.copy2(DB_PATH, dest)
    # Also copy WAL/SHM if present (usually empty after checkpoint).
    for suffix in ("-wal", "-shm"):
        side = Path(str(DB_PATH) + suffix)
        if side.is_file() and side.stat().st_size:
            shutil.copy2(side, Path(str(dest) + suffix))
    print(f"Wrote {dest} ({dest.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
