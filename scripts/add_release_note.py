"""Append a release note that will be sent to everyone who has /start'd the bot.

No telegram/PIL import — safe to run any time, doesn't need the bot's
dependencies installed.

Usage:
    python scripts/add_release_note.py "Headline" "Title::Body text" ["Title 2::Body 2" ...]
    python scripts/add_release_note.py "Headline" --intro "Custom intro line." "Title::Body"

Delivery is unchanged and still happens automatically: the newest entry in
bot/release_notes.json is broadcast once to every known private chat the next
time the bot restarts, or immediately via /release send in Telegram.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.release_data import add_note  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("headline", help="Short title for this update, e.g. 'Faster sticker checks'")
    parser.add_argument(
        "sections",
        nargs="+",
        help="One or more 'Title::Body text' pairs describing what changed, written for members.",
    )
    parser.add_argument("--intro", default=None, help="Optional one-line intro. Defaults to a generic line.")
    args = parser.parse_args()

    sections: list[tuple[str, str]] = []
    for raw in args.sections:
        if "::" not in raw:
            parser.error(f"section {raw!r} must be 'Title::Body text'")
        title, body = raw.split("::", 1)
        if not title.strip() or not body.strip():
            parser.error(f"section {raw!r} needs both a title and body")
        sections.append((title, body))

    note = add_note(args.headline, sections, intro=args.intro)
    print(f"Added release note {note['id']} — {note['headline']}")
    print(f"({len(note['sections'])} section(s). It goes out on the next bot restart, or /release send.)")


if __name__ == "__main__":
    main()
