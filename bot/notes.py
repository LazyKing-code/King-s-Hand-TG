from __future__ import annotations

import logging
import re

from telegram import Update
from telegram.constants import ChatType
from telegram.ext import ContextTypes

from bot import db
from bot.commands import cmd
from bot.moderation import is_group_admin, is_immune, require_group, require_group_admin

log = logging.getLogger(__name__)

_NAME = re.compile(r"^[a-zA-Z0-9_]{1,32}$")
_HASH = re.compile(r"^#([a-zA-Z0-9_]{1,32})\b")


def _norm_name(raw: str) -> str | None:
    name = (raw or "").strip().lstrip("#").lower()
    if not _NAME.fullmatch(name):
        return None
    return name


def _content(update: Update, context: ContextTypes.DEFAULT_TYPE, skip: int = 1) -> str:
    args = context.args or []
    if len(args) > skip:
        return " ".join(args[skip:]).strip()
    reply = update.effective_message.reply_to_message if update.effective_message else None
    if reply:
        return (reply.text or reply.caption or "").strip()
    return ""


async def cmd_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_admin(update):
        return
    if not context.args:
        await update.effective_message.reply_text(
            f"Usage: {cmd('save')} name then the text, or reply to a message.\n"
            f"Members can later send #name or {cmd('get')} name."
        )
        return
    name = _norm_name(context.args[0])
    if not name:
        await update.effective_message.reply_text("Note names: letters, numbers, underscore. Max 32.")
        return
    text = _content(update, context, skip=1)
    if not text:
        await update.effective_message.reply_text("Give some text, or reply to a message.")
        return
    db.save_note(update.effective_chat.id, name, text)
    await update.effective_message.reply_text(f"Saved note #{name}.")


async def cmd_get(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group(update):
        return
    if not context.args:
        await update.effective_message.reply_text(f"Usage: {cmd('get')} name — or send #name")
        return
    name = _norm_name(context.args[0])
    if not name:
        await update.effective_message.reply_text("That is not a valid note name.")
        return
    text = db.get_note(update.effective_chat.id, name)
    if not text:
        await update.effective_message.reply_text("No note with that name.")
        return
    await update.effective_message.reply_text(text)


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_admin(update):
        return
    if not context.args:
        await update.effective_message.reply_text(f"Usage: {cmd('clear')} notename")
        return
    name = _norm_name(context.args[0])
    if not name:
        await update.effective_message.reply_text("That is not a valid note name.")
        return
    if db.delete_note(update.effective_chat.id, name):
        await update.effective_message.reply_text(f"Deleted #{name}.")
    else:
        await update.effective_message.reply_text("No note with that name.")


async def cmd_notes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group(update):
        return
    names = db.list_notes(update.effective_chat.id)
    if not names:
        await update.effective_message.reply_text(
            f"No notes yet. Admins: {cmd('save')} name"
        )
        return
    listed = ", ".join(f"#{n}" for n in names)
    await update.effective_message.reply_text(f"Notes: {listed}")


async def cmd_filter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_admin(update):
        return
    if not context.args:
        await update.effective_message.reply_text(
            f"Usage: {cmd('filter')} keyword then the reply, or reply to a message.\n"
            f"Remove with {cmd('stop')} keyword."
        )
        return
    keyword = " ".join(context.args[:1]).strip().lower()
    if len(keyword) < 2 or len(keyword) > 64:
        await update.effective_message.reply_text("Keyword should be 2–64 characters.")
        return
    text = _content(update, context, skip=1)
    if not text:
        await update.effective_message.reply_text("Give a reply text, or reply to a message.")
        return
    db.save_filter(update.effective_chat.id, keyword, text)
    await update.effective_message.reply_text(f"Filter saved for “{keyword}”.")


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_admin(update):
        return
    if not context.args:
        await update.effective_message.reply_text(f"Usage: {cmd('stop')} keyword")
        return
    keyword = " ".join(context.args).strip().lower()
    if db.delete_filter(update.effective_chat.id, keyword):
        await update.effective_message.reply_text(f"Stopped filter “{keyword}”.")
    else:
        await update.effective_message.reply_text("No filter with that keyword.")


async def cmd_filters(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_admin(update):
        return
    items = db.list_filters(update.effective_chat.id)
    if not items:
        await update.effective_message.reply_text("No filters.")
        return
    listed = ", ".join(k for k, _ in items)
    await update.effective_message.reply_text(f"Filters: {listed}")


async def cmd_blacklist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_admin(update):
        return
    if not context.args:
        words = db.list_blacklist(update.effective_chat.id)
        if not words:
            await update.effective_message.reply_text(
                f"No blacklisted words. Add one: {cmd('blacklist')} word"
            )
            return
        await update.effective_message.reply_text("Blacklist: " + ", ".join(words))
        return
    word = " ".join(context.args).strip().lower()
    if len(word) < 2 or len(word) > 64:
        await update.effective_message.reply_text("Word should be 2–64 characters.")
        return
    db.add_blacklist(update.effective_chat.id, word)
    await update.effective_message.reply_text(
        f"Blacklisted “{word}”. Matching messages from members will be deleted."
    )


async def cmd_unblacklist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_admin(update):
        return
    if not context.args:
        await update.effective_message.reply_text(f"Usage: {cmd('unblacklist')} word")
        return
    word = " ".join(context.args).strip().lower()
    if db.remove_blacklist(update.effective_chat.id, word):
        await update.effective_message.reply_text(f"Removed “{word}” from the blacklist.")
    else:
        await update.effective_message.reply_text("That word was not on the list.")


def _message_text(update: Update) -> str:
    msg = update.effective_message
    if not msg:
        return ""
    return (msg.text or msg.caption or "").strip()


async def on_triggers(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not message or not chat or not user or user.is_bot:
        return
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return
    text = _message_text(update)
    if not text or text.startswith("/"):
        return

    lowered = text.lower()
    if not is_immune(user.id, chat.id) and not await is_group_admin(update, user.id):
        for word in db.list_blacklist(chat.id):
            if re.search(rf"(?<!\w){re.escape(word)}(?!\w)", lowered, re.I):
                try:
                    await message.delete()
                except Exception:
                    pass
                return

    hash_match = _HASH.match(text)
    if hash_match:
        note = db.get_note(chat.id, hash_match.group(1).lower())
        if note:
            try:
                await message.reply_text(note)
            except Exception:
                log.exception("note reply failed")
            return

    for keyword, reply in db.list_filters(chat.id):
        if re.search(rf"(?<!\w){re.escape(keyword)}(?!\w)", lowered, re.I):
            try:
                await message.reply_text(reply)
            except Exception:
                log.exception("filter reply failed")
            return
