from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from telegram import Sticker

from bot.config import NSFW_THRESHOLD, PACK_KEYWORDS
from bot import db

log = logging.getLogger(__name__)

_detector = None
_detector_failed = False

NSFW_LABELS = {
    "BUTTOCKS_EXPOSED",
    "FEMALE_BREAST_EXPOSED",
    "FEMALE_GENITALIA_EXPOSED",
    "ANUS_EXPOSED",
    "MALE_GENITALIA_EXPOSED",
}


def _get_detector():
    global _detector, _detector_failed
    if _detector_failed:
        return None
    if _detector is None:
        try:
            from nudenet import NudeDetector

            _detector = NudeDetector()
        except Exception:
            log.exception("Could not load NudeNet. Pack keywords + /report still work.")
            _detector_failed = True
            return None
    return _detector


def pack_name_looks_nsfw(set_name: str | None) -> bool:
    if not set_name:
        return False
    lowered = set_name.lower().replace("-", "").replace("_", "")
    return any(token.replace("_", "") in lowered for token in PACK_KEYWORDS)


async def is_nsfw_sticker(sticker: Sticker, chat_id: int, bot) -> tuple[bool, str]:
    """Return (is_nsfw, reason). Owner allowlists always win."""
    unique = sticker.file_unique_id
    set_name = sticker.set_name

    if db.sticker_allowed(unique):
        return False, "whitelisted sticker"
    if db.pack_allowed(chat_id, set_name):
        return False, "whitelisted pack"
    if db.pack_banned(chat_id, set_name):
        return True, f"banned pack {set_name}"
    if pack_name_looks_nsfw(set_name):
        db.ban_pack(chat_id, set_name)
        db.cache_set(unique, True, set_name)
        return True, f"pack name {set_name}"

    cached = db.cache_get(unique)
    if cached is not None:
        if cached and set_name:
            db.ban_pack(chat_id, set_name)
        return cached, "cached scan"

    nsfw = await _scan_sticker_image(sticker, bot)
    db.cache_set(unique, nsfw, set_name)
    if nsfw and set_name:
        db.ban_pack(chat_id, set_name)
        return True, f"image scan; pack {set_name} auto-banned"
    if nsfw:
        return True, "image scan"
    return False, "clean"


async def _scan_sticker_image(sticker: Sticker, bot) -> bool:
    detector = _get_detector()
    if detector is None:
        return False

    file_id = sticker.thumbnail.file_id if sticker.thumbnail else sticker.file_id
    if sticker.is_animated and not sticker.thumbnail:
        log.info("Animated sticker has no thumbnail; skipping image scan")
        return False

    tmp_path: Path | None = None
    try:
        tg_file = await bot.get_file(file_id)
        with tempfile.NamedTemporaryFile(suffix=".webp", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        await tg_file.download_to_drive(custom_path=tmp_path)
        detections = detector.detect(str(tmp_path))
    except Exception:
        log.exception("NSFW scan failed")
        return False
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)

    for item in detections or []:
        label = (item.get("class") or item.get("label") or "").upper()
        score = float(item.get("score") or 0)
        if label in NSFW_LABELS and score >= NSFW_THRESHOLD:
            log.info("NSFW hit %s score=%.2f", label, score)
            return True
    return False
