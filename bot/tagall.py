from __future__ import annotations

import asyncio
import logging
import re
import time
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType
from telegram.error import RetryAfter, TelegramError
from telegram.ext import ContextTypes

from bot import db
from bot.config import OWNER_IDS
from bot.moderation import require_group

log = logging.getLogger(__name__)

# Same glyph for every mention. Keep well under Telegram's 100-entity cap:
# the header uses 2 entities (bold + italic), so 40 mentions = 42 total.
DOT = "·"
BATCH = 40
SEND_GAP = 1.2
COOLDOWN = 90
_last: dict[int, float] = {}
_ID_RE = re.compile(r"-?\d+")


def _chunk(ids: list[int], size: int) -> list[list[int]]:
    return [ids[i : i + size] for i in range(0, len(ids), size)]


def _dots(ids: list[int]) -> str:
    return "".join(f'<a href="tg://user?id={user_id}">{DOT}</a>' for user_id in ids)


async def _require_you(update: Update) -> bool:
    if not await require_group(update):
        return False
    user = update.effective_user
    if user and user.id in OWNER_IDS:
        return True
    await update.effective_message.reply_text("Only the owner can ping everyone.")
    return False


async def _send_html(bot, chat_id: int, body: str) -> None:
    for _ in range(4):
        try:
            await bot.send_message(
                chat_id,
                body,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            return
        except RetryAfter as exc:
            await asyncio.sleep(float(exc.retry_after) + 0.5)
    raise TelegramError("tagall hit RetryAfter too many times")


async def on_seen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user or user.is_bot:
        return
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return
    db.touch_member(chat.id, user.id)


async def cmd_notag(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group(update):
        return
    db.set_tagall_optout(update.effective_chat.id, update.effective_user.id, True)
    await update.effective_message.reply_text("You will be skipped on /tagall.")


async def cmd_tagme(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group(update):
        return
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    db.touch_member(chat_id, user_id)
    db.set_tagall_optout(chat_id, user_id, False)
    await update.effective_message.reply_text("You will be included in /tagall.")


async def on_tagall_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data or not query.data.startswith("ta:"):
        return
    chat = query.message.chat if query.message else None
    user = query.from_user
    if not chat or not user or chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await query.answer()
        return
    if user.is_bot:
        await query.answer("Bots are skipped.", show_alert=True)
        return
    if query.data == "ta:in":
        db.touch_member(chat.id, user.id)
        db.set_tagall_optout(chat.id, user.id, False)
        await query.answer("You will be included in /tagall.")
        return
    if query.data == "ta:out":
        db.set_tagall_optout(chat.id, user.id, True)
        await query.answer("You will be skipped on /tagall.")
        return
    await query.answer()


async def _send_invite(update: Update) -> None:
    await update.effective_message.reply_html(
        "Tap once if you want to be included when everyone is tagged.\n"
        "You do not have to send a message.",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Include me", callback_data="ta:in"),
                    InlineKeyboardButton("Skip me", callback_data="ta:out"),
                ]
            ]
        ),
    )


def _parse_add_ids(update: Update, args: list[str]) -> list[int]:
    found: set[int] = set()
    for raw in args:
        if _ID_RE.fullmatch(raw):
            found.add(int(raw))
    msg = update.effective_message
    if msg and msg.entities:
        for entity in msg.entities:
            if entity.type == "text_mention" and entity.user and not entity.user.is_bot:
                found.add(entity.user.id)
    return sorted(found)


async def cmd_tagall(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_you(update):
        return
    chat = update.effective_chat
    chat_id = chat.id
    args = list(context.args or [])
    head = args[0].lower() if args else ""

    if head in {"invite", "signup", "pin"}:
        await _send_invite(update)
        return

    if head == "add":
        ids = _parse_add_ids(update, args[1:])
        if not ids:
            await update.effective_message.reply_text(
                "Send numeric ids after the command, or mention people in the same message."
            )
            return
        for user_id in ids:
            db.touch_member(chat_id, user_id)
        await update.effective_message.reply_text(
            f"Saved {len(ids)} people for /tagall. They will be pinged even if they never spoke."
        )
        return

    now = time.time()
    if now - _last.get(chat_id, 0) < COOLDOWN:
        wait = int(COOLDOWN - (now - _last[chat_id]))
        await update.effective_message.reply_text(f"Wait {wait}s before tagging everyone again.")
        return

    note = " ".join(args).strip()
    ids = set(db.list_member_ids(chat_id))
    try:
        for admin in await context.bot.get_chat_administrators(chat_id):
            if not admin.user.is_bot:
                ids.add(admin.user.id)
                db.touch_member(chat_id, admin.user.id)
    except Exception:
        log.exception("tagall admin list failed")

    skip = {context.bot.id, update.effective_user.id}
    targets = [uid for uid in sorted(ids) if uid not in skip and not db.is_tagall_optout(chat_id, uid)]
    if not targets:
        await update.effective_message.reply_text(
            "Nobody is on the list yet. Telegram does not give bots a full member list.\n"
            "Pin /tagall invite so silent members can tap in, or /tagall add with their ids."
        )
        return

    _last[chat_id] = now
    batches = _chunk(targets, BATCH)
    header = "<b>Everyone</b>"
    if note:
        header += f"\n{escape(note)}"
    header += f"\n<i>{len(targets)}</i>\n"

    try:
        for index, batch in enumerate(batches):
            body = header + _dots(batch) if index == 0 else _dots(batch)
            await _send_html(context.bot, chat_id, body)
            if index + 1 < len(batches):
                await asyncio.sleep(SEND_GAP)
    except Exception:
        log.exception("tagall send failed")
        await update.effective_message.reply_text("Could not send the tag. Try again in a moment.")
