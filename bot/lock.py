from __future__ import annotations

import json
import logging

from telegram import ChatPermissions, Update
from telegram.ext import ContextTypes

from bot import db
from bot.commands import cmd
from bot.moderation import require_group_admin, send_to_log

log = logging.getLogger(__name__)

_PERM_FIELDS = (
    "can_send_messages",
    "can_send_audios",
    "can_send_documents",
    "can_send_photos",
    "can_send_videos",
    "can_send_video_notes",
    "can_send_voice_notes",
    "can_send_polls",
    "can_send_other_messages",
    "can_add_web_page_previews",
    "can_change_info",
    "can_invite_users",
    "can_pin_messages",
    "can_manage_topics",
)

LOCKED = ChatPermissions(
    can_send_messages=False,
    can_send_audios=False,
    can_send_documents=False,
    can_send_photos=False,
    can_send_videos=False,
    can_send_video_notes=False,
    can_send_voice_notes=False,
    can_send_polls=False,
    can_send_other_messages=False,
    can_add_web_page_previews=False,
    can_change_info=False,
    can_invite_users=False,
    can_pin_messages=False,
    can_manage_topics=False,
)

OPEN = ChatPermissions(
    can_send_messages=True,
    can_send_audios=True,
    can_send_documents=True,
    can_send_photos=True,
    can_send_videos=True,
    can_send_video_notes=True,
    can_send_voice_notes=True,
    can_send_polls=True,
    can_send_other_messages=True,
    can_add_web_page_previews=True,
    can_change_info=False,
    can_invite_users=True,
    can_pin_messages=False,
    can_manage_topics=False,
)


def _dump_permissions(perms: ChatPermissions | None) -> str:
    data = {}
    for field in _PERM_FIELDS:
        data[field] = bool(getattr(perms, field, True)) if perms else True
    return json.dumps(data)


def _load_permissions(raw: str | None) -> ChatPermissions:
    if not raw:
        return OPEN
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return OPEN
    kwargs = {field: bool(data.get(field, True)) for field in _PERM_FIELDS}
    return ChatPermissions(**kwargs)


async def cmd_lock(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_admin(update):
        return
    chat = update.effective_chat
    args = [a.lower() for a in (context.args or [])]
    if args and args[0] in {"status", "info"}:
        state = "locked" if db.is_locked(chat.id) else "open"
        await update.effective_message.reply_text(f"Chat is {state}.")
        return
    if db.is_locked(chat.id):
        await update.effective_message.reply_text(f"Already locked. {cmd('unlock')} to open it.")
        return
    try:
        full = await context.bot.get_chat(chat.id)
        db.set_lock_backup(chat.id, _dump_permissions(full.permissions))
        try:
            await context.bot.set_chat_permissions(chat.id, LOCKED)
        except Exception:
            await context.bot.set_chat_permissions(
                chat.id,
                ChatPermissions(can_send_messages=False, can_send_other_messages=False),
            )
        db.set_locked(chat.id, True)
    except Exception:
        log.exception("lock failed")
        await update.effective_message.reply_text(
            "I could not lock the chat. I need permission to restrict members."
        )
        return
    await update.effective_message.reply_text(
        f"Chat locked. Members cannot send messages. Admins still can.\n{cmd('unlock')} to restore."
    )
    await send_to_log(context, chat.id, "Chat locked for members.", event="Lock")


async def cmd_unlock(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_admin(update):
        return
    chat = update.effective_chat
    if not db.is_locked(chat.id) and not db.get_lock_backup(chat.id):
        await update.effective_message.reply_text("Chat is not locked.")
        return
    perms = _load_permissions(db.get_lock_backup(chat.id))
    try:
        await context.bot.set_chat_permissions(chat.id, perms)
        db.set_locked(chat.id, False)
    except Exception:
        log.exception("unlock failed")
        await update.effective_message.reply_text(
            "Could not unlock. I need to be admin with Restrict members."
        )
        return
    await update.effective_message.reply_text("Chat unlocked. Member permissions restored.")
    await send_to_log(context, chat.id, "Chat unlocked.", event="Lock")
