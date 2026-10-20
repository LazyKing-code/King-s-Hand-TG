from __future__ import annotations

import asyncio
import logging
import secrets
from html import escape

from telegram import Update, User
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes

from bot.config import OWNER_IDS

log = logging.getLogger(__name__)

_locks: dict[str, asyncio.Lock] = {}
_start_locks: dict[tuple[int, str], asyncio.Lock] = {}


def new_id() -> str:
    return secrets.token_hex(4)


def lock_for(session_id: str) -> asyncio.Lock:
    lock = _locks.get(session_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[session_id] = lock
    return lock


def start_lock(chat_id: int, kind: str) -> asyncio.Lock:
    key = (chat_id, kind)
    lock = _start_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _start_locks[key] = lock
    return lock


def forget_lock(session_id: str) -> None:
    _locks.pop(session_id, None)


def clean_name(user: User) -> str:
    name = (user.first_name or user.full_name or str(user.id)).strip()
    name = " ".join(name.split())
    return (name or str(user.id))[:20]


def mention(user_id: int, name: str) -> str:
    return f'<a href="tg://user?id={int(user_id)}">{escape(name)}</a>'


def require_group(update: Update) -> bool:
    chat = update.effective_chat
    return bool(chat and chat.type in ("group", "supergroup"))


async def is_admin(bot, chat_id: int, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
    except TelegramError:
        return False
    return member.status in ("administrator", "creator")


async def can_manage(bot, chat_id: int, user_id: int, starter_id: int) -> bool:
    if int(user_id) == int(starter_id) or int(user_id) in OWNER_IDS:
        return True
    return await is_admin(bot, chat_id, user_id)


async def edit_html(bot, chat_id: int, message_id: int | None, text: str, markup=None) -> int | None:
    """Edit the game message. If it is gone, send a new one and return its id."""
    if message_id is None:
        return None
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode="HTML",
            reply_markup=markup,
            disable_web_page_preview=True,
        )
        return message_id
    except BadRequest as exc:
        lowered = str(exc).lower()
        if "not modified" in lowered:
            return message_id
        if "not found" in lowered or "message to edit" in lowered:
            return await _send_replacement(bot, chat_id, text, markup)
        log.warning("edit failed chat=%s message=%s: %s", chat_id, message_id, exc)
        return message_id
    except TelegramError:
        log.exception("edit failed chat=%s message=%s", chat_id, message_id)
        return message_id


async def _send_replacement(bot, chat_id: int, text: str, markup) -> int | None:
    try:
        sent = await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=markup,
            disable_web_page_preview=True,
        )
    except TelegramError:
        log.exception("replacement message failed chat=%s", chat_id)
        return None
    return sent.message_id


def args_of(update: Update, context: ContextTypes.DEFAULT_TYPE) -> list[str]:
    return [part.lower() for part in (context.args or [])]
