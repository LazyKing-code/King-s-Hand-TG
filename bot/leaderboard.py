"""/lb — arena leaderboard and paginated match history."""
from __future__ import annotations

import logging
import time
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot import arena_logic as logic
from bot import db
from bot.arena import KIND_LABEL, RESULT_RETENTION_DAYS, ist_str
from bot.commands import cmd
from bot.moderation import require_group

log = logging.getLogger(__name__)

_PER_PAGE = 5
_KIND_ALIASES = {
    "cricket": "cricket",
    "hand": "cricket",
    "handcricket": "cricket",
    "rps": "rps",
    "rock": "rps",
    "paper": "rps",
    "scissors": "rps",
}
_KIND_CODE = {"cricket": "cr", "rps": "rp"}
_CODE_KIND = {v: k for k, v in _KIND_CODE.items()}


def _since() -> float:
    return time.time() - RESULT_RETENTION_DAYS * 86400


def _board_text(board: list[dict]) -> str:
    lines = [
        f"<b>Leaderboard</b> — last {RESULT_RETENTION_DAYS} days",
    ]
    if not board:
        lines.append("No games finished yet. Try " + cmd("cricket") + " or " + cmd("rps") + ".")
        return "\n".join(lines)
    for i, row in enumerate(board, start=1):
        lines.append(
            f"{i}. {escape(row['name'])} — {row['wins']}W {row['losses']}L {row['draws']}D"
        )
    lines.append("")
    lines.append(f"Match history: {cmd('lb')} cricket · {cmd('lb')} rps")
    return "\n".join(lines)


def _history_page_text(kind: str, results: list[dict], page: int, total_pages: int) -> str:
    lines = [f"<b>{KIND_LABEL[kind].title()} history</b> — page {page + 1}/{total_pages}"]
    if not results:
        lines.append(f"No {KIND_LABEL[kind]} matches finished in the last {RESULT_RETENTION_DAYS} days yet.")
    for row in results:
        when = ist_str(row["finished_at"])
        if row["winner_id"] is None:
            outcome = "Tied"
        else:
            outcome = f"{row['a_name'] if row['winner_id'] == row['a_id'] else row['b_name']} won"
        lines.append(
            f"• {when} — {escape(row['a_name'])} vs {escape(row['b_name'])} "
            f"({escape(row['summary'])}) — {escape(outcome)}"
        )
    lines.append("")
    lines.append(f"Only the last {RESULT_RETENTION_DAYS} days of match history is stored.")
    return "\n".join(lines)


def _history_markup(kind: str, page: int, total_pages: int) -> InlineKeyboardMarkup | None:
    if total_pages <= 1:
        return None
    code = _KIND_CODE[kind]
    row = []
    if page > 0:
        row.append(InlineKeyboardButton("« Prev", callback_data=f"lb:{code}:{page - 1}"))
    row.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="lb:noop:0"))
    if page < total_pages - 1:
        row.append(InlineKeyboardButton("Next »", callback_data=f"lb:{code}:{page + 1}"))
    return InlineKeyboardMarkup([row])


def _render_history(chat_id: int, kind: str, page: int) -> tuple[str, InlineKeyboardMarkup | None]:
    all_results = db.list_arena_results(chat_id, kind, _since())
    page, offset, total_pages = logic.paginate(len(all_results), page, _PER_PAGE)
    page_results = all_results[offset : offset + _PER_PAGE]
    text = _history_page_text(kind, page_results, page, total_pages)
    markup = _history_markup(kind, page, total_pages)
    return text, markup


async def cmd_lb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group(update):
        return
    msg = update.effective_message
    chat_id = update.effective_chat.id
    args = [a.lower() for a in (context.args or [])]

    if not args:
        results = db.list_arena_results(chat_id, None, _since())
        board = logic.aggregate_leaderboard(results, limit=10)
        await msg.reply_html(_board_text(board), disable_web_page_preview=True)
        return

    kind = _KIND_ALIASES.get(args[0])
    if not kind:
        await msg.reply_text(f"Unknown game. Try {cmd('lb')}, {cmd('lb')} cricket, or {cmd('lb')} rps.")
        return
    text, markup = _render_history(chat_id, kind, page=0)
    await msg.reply_html(text, reply_markup=markup, disable_web_page_preview=True)


async def on_lb_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    parts = query.data.split(":")
    if len(parts) != 3 or parts[0] != "lb":
        await query.answer()
        return
    _, code, raw_page = parts
    if code == "noop":
        await query.answer()
        return
    kind = _CODE_KIND.get(code)
    if not kind:
        await query.answer("Unknown game.", show_alert=True)
        return
    try:
        page = int(raw_page)
    except ValueError:
        page = 0
    chat_id = update.effective_chat.id
    text, markup = _render_history(chat_id, kind, page)
    await query.answer()
    try:
        await query.edit_message_text(
            text, parse_mode="HTML", reply_markup=markup, disable_web_page_preview=True
        )
    except Exception as exc:
        msg = str(exc).lower()
        if "not modified" not in msg:
            log.warning("lb pagination edit failed: %s", exc)
