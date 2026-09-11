"""Storage for user-facing release notes.

Deliberately has no telegram/PIL imports so `scripts/add_release_note.py`
stays fast and dependency-free. `bot/release.py` renders this data for
Telegram; this module only reads/writes `bot/release_notes.json`.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

try:
    from zoneinfo import ZoneInfo

    _IST = ZoneInfo("Asia/Kolkata")
except Exception:
    _IST = None  # type: ignore[assignment]

NOTES_PATH = Path(__file__).resolve().parent / "release_notes.json"


def _today() -> date:
    if _IST is not None:
        return datetime.now(_IST).date()
    return (datetime.utcnow() + timedelta(hours=5, minutes=30)).date()


def load_notes() -> list[dict]:
    if not NOTES_PATH.exists():
        return []
    with NOTES_PATH.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    out = []
    for note in data:
        note = dict(note)
        note["sections"] = [tuple(s) for s in note.get("sections", [])]
        out.append(note)
    return out


def save_notes(notes: list[dict]) -> None:
    payload = [
        {**note, "sections": [list(s) for s in note.get("sections", [])]}
        for note in notes
    ]
    with NOTES_PATH.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def _next_id(notes: list[dict], day: date) -> str:
    prefix = day.isoformat()
    seq = 1 + sum(1 for n in notes if str(n.get("id", "")).startswith(prefix + "."))
    return f"{prefix}.{seq}"


def add_note(
    headline: str,
    sections: list[tuple[str, str]],
    *,
    intro: str | None = None,
) -> dict:
    """Append a new release note and persist it. Returns the note added.

    `sections` is a list of (title, body) pairs, each one short paragraph
    describing one thing that changed, written for the people using the bot
    (not a commit message).
    """
    if not headline.strip():
        raise ValueError("headline is required")
    if not sections:
        raise ValueError("at least one section is required")
    notes = load_notes()
    today = _today()
    note = {
        "id": _next_id(notes, today),
        "date": f"{today.day} {today.strftime('%B %Y')}",
        "headline": headline.strip(),
        "intro": (intro or "Only what changed is listed below.").strip(),
        "sections": [(t.strip(), b.strip()) for t, b in sections],
    }
    notes.append(note)
    save_notes(notes)
    return note
