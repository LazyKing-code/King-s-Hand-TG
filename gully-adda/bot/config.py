from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

_data = os.getenv("DATA_DIR", "").strip()
DATA_DIR = Path(_data).expanduser() if _data else (ROOT / "data")
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "bot.db"


def parse_id_list(*raws: str) -> set[int]:
    ids: set[int] = set()
    for raw in raws:
        for part in (raw or "").replace(";", ",").split(","):
            part = part.strip()
            if not part:
                continue
            try:
                ids.add(int(part))
            except ValueError:
                continue
    return ids


BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_IDS = parse_id_list(os.getenv("OWNER_IDS", ""), os.getenv("OWNER_ID", ""))
OWNER_ID = next(iter(sorted(OWNER_IDS)), 0)
