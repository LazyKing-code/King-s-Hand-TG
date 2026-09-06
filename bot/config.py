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
NSFW_THRESHOLD = float(os.getenv("NSFW_THRESHOLD", "0.55"))
IMMUNE_IDS = parse_id_list(os.getenv("IMMUNE_IDS", "")) | set(OWNER_IDS)

# Optional. Leave empty for /kick /help. Set kh_ only if another bot already took those names.
_raw_prefix = os.getenv("COMMAND_PREFIX", "").strip().lstrip("/")
_raw_prefix = _raw_prefix.replace("-", "_")
_raw_prefix = "".join(ch for ch in _raw_prefix if ch.isalnum() or ch == "_").lower()
if _raw_prefix in {"none", "off", "false", "0"}:
    _raw_prefix = ""
if _raw_prefix and not _raw_prefix.endswith("_"):
    _raw_prefix += "_"
COMMAND_PREFIX = _raw_prefix[:8]

# Pack names containing these tokens are treated as NSFW without scanning.
PACK_KEYWORDS = (
    "nsfw",
    "porn",
    "hentai",
    "henta",
    "rule34",
    "r34",
    "lewd",
    "ahegao",
    "xxx",
    "18plus",
    "nudes",
    "nude",
    "sex",
    "boobs",
    "pussy",
    "penis",
    "dick",
    "cock",
    "cum",
    "onlyfans",
)
