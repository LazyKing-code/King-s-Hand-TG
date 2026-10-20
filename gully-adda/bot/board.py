"""Per-group wins for Tambola and gully cricket."""

from __future__ import annotations

from html import escape

from telegram import Update
from telegram.ext import ContextTypes

from bot import db
from bot.common import require_group


def _people(rows: list[dict]) -> dict[int, dict]:
    people: dict[int, dict] = {}
    for row in rows:
        person = people.setdefault(
            int(row["user_id"]),
            {"name": row["user_name"], "last": 0.0, "cricket": 0, "line": 0, "full": 0},
        )
        if float(row["last_at"]) >= person["last"]:
            person["name"] = row["user_name"]
            person["last"] = float(row["last_at"])
        kind = row["kind"]
        result = row["result"]
        count = int(row["n"])
        if kind == "cricket" and result == "win":
            person["cricket"] += count
        elif kind == "tambola" and result == "line":
            person["line"] += count
        elif kind == "tambola" and result == "full":
            person["full"] += count
    return people


def render_board(rows: list[dict]) -> str:
    people = _people(rows)
    lines = ["<b>Gully Adda</b>", "Wins in this group.", ""]

    cricket = [p for p in people.values() if p["cricket"] > 0]
    cricket.sort(key=lambda p: (-p["cricket"], p["name"].lower()))
    lines.append("<b>Hand cricket</b>")
    if not cricket:
        lines.append("No match wins yet. Send /cricket.")
    else:
        for index, person in enumerate(cricket[:10], start=1):
            wins = person["cricket"]
            word = "win" if wins == 1 else "wins"
            lines.append(f"{index}. {escape(person['name'])} — {wins} {word}")

    tambola = [p for p in people.values() if p["line"] or p["full"]]
    tambola.sort(key=lambda p: (-(p["full"] * 2 + p["line"]), p["name"].lower()))
    lines.append("")
    lines.append("<b>Tambola</b>")
    if not tambola:
        lines.append("No claims yet. Send /tambola.")
    else:
        for index, person in enumerate(tambola[:10], start=1):
            bits = []
            if person["full"]:
                houses = person["full"]
                bits.append("1 full house" if houses == 1 else f"{houses} full houses")
            if person["line"]:
                lines_won = person["line"]
                bits.append("1 line" if lines_won == 1 else f"{lines_won} lines")
            lines.append(f"{index}. {escape(person['name'])} — {', '.join(bits)}")
    return "\n".join(lines)


async def cmd_board(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    chat = update.effective_chat
    if not msg or not chat:
        return
    if not require_group(update):
        await msg.reply_text("Send /board in a group.")
        return
    text = render_board(db.board_counts(chat.id))
    await msg.reply_html(text, disable_web_page_preview=True)
