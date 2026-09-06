from __future__ import annotations

import logging
import re
import unicodedata

from html import escape

from telegram import ChatMember, Update
from telegram.ext import ContextTypes

from bot import db
from bot.commands import cmd
from bot.fun import resolve_member
from bot.moderation import mention, require_group_admin

log = logging.getLogger(__name__)

CLEAR = frozenset({"null", "none", "off", "clear", "remove", "reset", "-"})
TITLE_MAX = 16
NICK_MAX = 32


def _is_clear(text: str) -> bool:
    return text.strip().lower() in CLEAR


def _visible_title(nick: str) -> str:
    chars: list[str] = []
    for ch in nick:
        cat = unicodedata.category(ch)
        if cat.startswith("C") or cat in {"So", "Sk"}:
            continue
        chars.append(ch)
        if len("".join(chars)) >= TITLE_MAX:
            break
    return "".join(chars).strip()[:TITLE_MAX]


def _nick_from_command(update: Update) -> str:
    msg = update.effective_message
    text = (msg.text or msg.caption or "").strip()
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        return ""
    body = parts[1].strip()
    if msg.reply_to_message:
        return body
    if msg.entities:
        last_end = 0
        for entity in msg.entities:
            if entity.type in {"text_mention", "mention"}:
                last_end = max(last_end, entity.offset + entity.length)
        if last_end:
            return text[last_end:].strip()
    args = body.split(maxsplit=1)
    if args and (args[0].startswith("@") or args[0].lstrip("-").isdigit()):
        return args[1].strip() if len(args) > 1 else ""
    return body


async def _apply_telegram_title(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int, title: str) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        if member.status not in (ChatMember.ADMINISTRATOR, ChatMember.OWNER):
            return False
        await context.bot.set_chat_administrator_custom_title(chat_id, user_id, title)
        return True
    except Exception:
        log.exception("custom title failed for %s", user_id)
        return False


async def cmd_tag(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_admin(update):
        return
    msg = update.effective_message
    chat = update.effective_chat
    target = await resolve_member(update, context)
    nick = _nick_from_command(update)
    if not target:
        await msg.reply_text(
            "Reply to someone, or add their username.\n"
            f"Example: {cmd('tag')} @user captain\n"
            f"Clear: {cmd('tag')} @user null"
        )
        return
    if target.is_bot:
        await msg.reply_text("Bots cannot have a member tag.")
        return

    if not nick:
        current = db.get_nick(chat.id, target.id)
        if current:
            await msg.reply_html(f"{mention(target)} is tagged <b>{escape(current)}</b>.")
        else:
            await msg.reply_html(f"{mention(target)} has no tag.")
        return

    if _is_clear(nick):
        db.set_nick(chat.id, target.id, None)
        shown = await _apply_telegram_title(context, chat.id, target.id, "")
        extra = " Visible tag next to their name was cleared." if shown else ""
        await msg.reply_html(f"Tag removed for {mention(target)}.{extra}")
        return

    if len(nick) > NICK_MAX:
        await msg.reply_text(f"Keep it under {NICK_MAX} characters.")
        return

    db.set_nick(chat.id, target.id, nick)
    title = _visible_title(nick)
    shown = False
    if title:
        shown = await _apply_telegram_title(context, chat.id, target.id, title)
    if shown:
        await msg.reply_html(
            f"{mention(target)} is now <b>{escape(nick)}</b>.\n"
            "That tag shows next to their name."
        )
        return
    await msg.reply_html(
        f"Saved <b>{escape(nick)}</b> for {mention(target)}.\n"
        "The small tag beside a name only works for admins. "
        f"{cmd('makeadmin')} them first if you want it visible in the member list."
    )
