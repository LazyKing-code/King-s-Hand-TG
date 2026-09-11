from __future__ import annotations

import asyncio
import logging
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType
from telegram.error import Forbidden, RetryAfter, TelegramError
from telegram.ext import ContextTypes

from bot import db
from bot.commands import cmd
from bot.config import OWNER_IDS
from bot.invite import bot_username, url_buttons
from bot.release_data import add_note, load_notes  # noqa: F401  (add_note re-exported for callers)

log = logging.getLogger(__name__)

# Notes live in bot/release_notes.json, not here. Add a new one with:
#   python scripts/add_release_note.py "Headline" "Title::Body" "Title 2::Body 2"
# The newest entry in that file is the one broadcast to everyone who has
# /start'd the bot, the next time it restarts (or via /release send).
_NOTES: list[dict] = load_notes()


def current_note() -> dict:
    return _NOTES[-1]


RELEASE_ID = current_note()["id"]


def _section_html(title: str, body: str) -> str:
    return f"<b>{escape(title)}</b>\n{escape(body)}"


def release_html(note: dict | None = None) -> str:
    note = note or current_note()
    parts = [
        "<b>King's Hand</b> · Release notes",
        f"<i>{escape(note['date'])} · {escape(note['headline'])}</i>",
        "",
        escape(note["intro"]),
        "",
    ]
    for title, body in note["sections"]:
        parts.append(_section_html(title, body))
        parts.append("")
    parts.append(
        f"In the group, send {cmd('help')} for commands that match your role. "
        f"Earlier notes: {cmd('whatsnew')} history."
    )
    return "\n".join(parts).strip()


def history_html() -> str:
    lines = ["<b>King's Hand</b> · Previous releases", ""]
    for note in reversed(_NOTES):
        lines.append(f"<b>{escape(note['date'])}</b> — {escape(note['headline'])}")
        for title, _body in note["sections"]:
            lines.append(f"• {escape(title)}")
        lines.append("")
    lines.append(f"Latest detail: {cmd('whatsnew')}")
    return "\n".join(lines).strip()


def _notes_markup(username: str | None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton("Open /help", callback_data="help:index")]
    ]
    if username:
        rows.extend(url_buttons(username).inline_keyboard)
    return InlineKeyboardMarkup(rows)


async def send_release_card(bot, user_id: int, username: str | None) -> bool:
    html = release_html()
    try:
        await bot.send_message(
            user_id,
            html,
            parse_mode="HTML",
            reply_markup=_notes_markup(username),
            disable_web_page_preview=True,
        )
        return True
    except Forbidden:
        db.drop_bot_user(user_id)
        log.info("release: %s blocked the bot", user_id)
        return False
    except RetryAfter as exc:
        await asyncio.sleep(float(exc.retry_after) + 0.5)
        try:
            await bot.send_message(
                user_id,
                html,
                parse_mode="HTML",
                reply_markup=_notes_markup(username),
                disable_web_page_preview=True,
            )
            return True
        except TelegramError:
            log.exception("release retry failed for %s", user_id)
            return False
    except TelegramError:
        log.exception("release send failed for %s", user_id)
        return False


def _targets(*, only_unseen: bool) -> list[int]:
    ids = set(db.list_bot_users()) | set(OWNER_IDS)
    if not only_unseen:
        return sorted(ids)
    return sorted(uid for uid in ids if db.bot_user_release(uid) != RELEASE_ID)


async def broadcast_release(application, *, force: bool = False) -> tuple[int, int]:
    me = await application.bot.get_me()
    username = me.username
    sent = 0
    failed = 0
    for uid in _targets(only_unseen=not force):
        db.touch_bot_user(uid)
        ok = await send_release_card(application.bot, uid, username)
        if ok:
            db.set_bot_user_release(uid, RELEASE_ID)
            sent += 1
        else:
            failed += 1
        await asyncio.sleep(0.05)
    db.kv_set("last_release_broadcast", RELEASE_ID)
    log.info("release %s sent=%s failed=%s", RELEASE_ID, sent, failed)
    return sent, failed


async def maybe_broadcast_on_startup(application) -> None:
    await asyncio.sleep(8)
    if db.kv_get("last_release_broadcast") == RELEASE_ID:
        return
    try:
        await broadcast_release(application, force=False)
    except Exception:
        log.exception("startup release broadcast failed")


async def deliver_if_needed(user_id: int, bot, username: str | None) -> None:
    db.touch_bot_user(user_id)
    if db.bot_user_release(user_id) == RELEASE_ID:
        return
    if await send_release_card(bot, user_id, username):
        db.set_bot_user_release(user_id, RELEASE_ID)


async def cmd_whatsnew(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    msg = update.effective_message
    if not user or not chat or not msg:
        return
    if chat.type == ChatType.PRIVATE:
        db.touch_bot_user(user.id)
    username = await bot_username(context)
    args = [a.lower() for a in (context.args or [])]
    show_history = bool(args and args[0] in {"history", "all", "archive"})
    html = history_html() if show_history else release_html()
    await msg.reply_html(
        html,
        reply_markup=None if show_history else _notes_markup(username),
        disable_web_page_preview=True,
    )
    if chat.type == ChatType.PRIVATE:
        db.set_bot_user_release(user.id, RELEASE_ID)


async def cmd_release(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    msg = update.effective_message
    if not user or not msg:
        return
    if user.id not in OWNER_IDS:
        await msg.reply_text("Only the bot owner can send the official update.")
        return
    args = [a.lower() for a in (context.args or [])]
    username = await bot_username(context)
    if not args or args[0] in {"preview", "show"}:
        n = len(_targets(only_unseen=True))
        total = len(set(db.list_bot_users()) | set(OWNER_IDS))
        await msg.reply_html(
            release_html(),
            reply_markup=_notes_markup(username),
            disable_web_page_preview=True,
        )
        await msg.reply_text(
            f"Preview of {RELEASE_ID}. {n} people have not received this note yet "
            f"({total} known private chats).\n"
            f"{cmd('release')} send — deliver in private chat\n"
            f"{cmd('whatsnew')} · {cmd('whatsnew')} history"
        )
        return
    if args[0] != "send":
        await msg.reply_text(f"Use {cmd('release')} preview or {cmd('release')} send")
        return
    force = len(args) > 1 and args[1] in {"all", "force"}
    status = await msg.reply_text("Sending the official update…")
    sent, failed = await broadcast_release(context.application, force=force)
    await status.edit_text(
        f"Update {RELEASE_ID} delivered.\nSent: {sent}\nCould not reach: {failed}"
    )
