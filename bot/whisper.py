from __future__ import annotations

import logging
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, User
from telegram.error import Forbidden
from telegram.ext import ContextTypes

from bot import db
from bot.commands import cmd
from bot.moderation import mention, require_group, resolve_target

log = logging.getLogger(__name__)

ALERT_LIMIT = 180


def _after_command(update: Update) -> str:
    msg = update.effective_message
    text = (msg.text or msg.caption or "").strip()
    parts = text.split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


async def _target_and_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> tuple[User | None, str]:
    msg = update.effective_message
    text = msg.text or msg.caption or ""
    reply = msg.reply_to_message
    if reply and reply.from_user and not reply.from_user.is_bot:
        return reply.from_user, _after_command(update)

    target = await resolve_target(update, context)
    if not target:
        return None, ""

    last_end = 0
    for entity in msg.entities or []:
        if entity.type in {"text_mention", "mention"}:
            last_end = max(last_end, entity.offset + entity.length)
    if last_end:
        return target, text[last_end:].strip()

    args = list(context.args or [])
    if args and (args[0].startswith("@") or args[0].lstrip("-").isdigit()):
        return target, " ".join(args[1:]).strip()
    return target, _after_command(update)


async def cmd_whisper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group(update):
        return
    user = update.effective_user
    chat = update.effective_chat
    msg = update.effective_message
    if not user or not chat or not msg:
        return

    target, secret = await _target_and_text(update, context)
    if not target or not secret:
        await msg.reply_text(
            "Reply to someone, then type the whisper.\n"
            f"Example: reply to them with {cmd('whisper')} stay after the call"
        )
        return
    if target.id == user.id:
        await msg.reply_text("Pick someone else.")
        return
    if target.is_bot:
        await msg.reply_text("You cannot whisper a bot.")
        return
    if len(secret) > 3500:
        await msg.reply_text("Keep it under 3500 characters.")
        return

    whisper_id = db.save_whisper(chat.id, user.id, target.id, secret)
    card = (
        f"<b>Whisper</b>\n"
        f"{mention(user)} → {mention(target)}\n"
        "<i>Everyone can see this note. Only they can open it.</i>"
    )
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Open", callback_data=f"w:{whisper_id}")]]
    )
    await context.bot.send_message(
        chat.id,
        card,
        parse_mode="HTML",
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )
    try:
        await msg.delete()
    except Exception:
        pass


async def on_whisper_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data or not query.data.startswith("w:"):
        return
    clicker = query.from_user
    if not clicker:
        await query.answer()
        return
    try:
        whisper_id = int(query.data.split(":", 1)[1])
    except ValueError:
        await query.answer("That whisper is gone.", show_alert=True)
        return
    row = db.get_whisper(whisper_id)
    if not row:
        await query.answer("That whisper expired.", show_alert=True)
        return
    from_id = int(row["from_id"])
    to_id = int(row["to_id"])
    secret = str(row["text"])
    if clicker.id not in {from_id, to_id}:
        await query.answer("This one is not for you.", show_alert=True)
        return
    if len(secret) <= ALERT_LIMIT:
        await query.answer(secret, show_alert=True)
        return
    try:
        origin = query.message.chat.title if query.message and query.message.chat else "the group"
        await context.bot.send_message(
            clicker.id,
            f"Whisper from the group <b>{escape(origin)}</b>\n\n{escape(secret)}",
            parse_mode="HTML",
        )
        await query.answer("Long whisper — I sent it in our private chat.")
    except Forbidden:
        await query.answer(
            "Open a private chat with me first (tap Start), then Open again.",
            show_alert=True,
        )
    except Exception:
        log.exception("whisper dm failed")
        await query.answer("Could not deliver that whisper.", show_alert=True)
