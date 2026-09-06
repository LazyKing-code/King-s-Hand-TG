from __future__ import annotations

import logging
import time
from html import escape

from telegram import ChatMember, Message, Update, User
from telegram.ext import ContextTypes

from bot import db
from bot.commands import cmd
from bot.moderation import mention, require_group, require_group_admin

log = logging.getLogger(__name__)

DEFAULT_WELCOME = "Welcome {user} to {chat}."
_PLACEHOLDERS = (
    "{user} {mention} {name} {fullname} {first} {last} {username} {id} {chat} {chatname} {count}"
)
_recent: dict[tuple[int, int], float] = {}


def format_member_html(
    template: str,
    user: User,
    chat_title: str,
    count: int,
) -> str:
    first = user.first_name or ""
    last = user.last_name or ""
    name = user.full_name or first or str(user.id)
    username = f"@{user.username}" if user.username else "—"
    html = escape(template.strip() or DEFAULT_WELCOME)
    html = html.replace("{fullname}", escape(name))
    html = html.replace("{username}", escape(username))
    html = html.replace("{mention}", mention(user))
    html = html.replace("{first}", escape(first))
    html = html.replace("{last}", escape(last))
    html = html.replace("{name}", escape(name))
    html = html.replace("{user}", mention(user))
    html = html.replace("{chatname}", escape(chat_title or "group"))
    html = html.replace("{chat}", escape(chat_title or "group"))
    html = html.replace("{count}", str(count))
    html = html.replace("{id}", str(user.id))
    return html


def _dedup(chat_id: int, user_id: int) -> bool:
    now = time.time()
    key = (chat_id, user_id)
    last = _recent.get(key, 0)
    if now - last < 25:
        return True
    _recent[key] = now
    if len(_recent) > 2000:
        cutoff = now - 60
        for old in [k for k, ts in _recent.items() if ts < cutoff]:
            _recent.pop(old, None)
    return False


def _media_from_message(message: Message | None) -> tuple[str | None, str | None, str]:
    if not message:
        return None, None, ""
    caption = (message.caption or message.text or "").strip()
    if message.photo:
        return "photo", message.photo[-1].file_id, caption
    if message.video:
        return "video", message.video.file_id, caption
    if message.animation:
        return "animation", message.animation.file_id, caption
    if message.sticker:
        return "sticker", message.sticker.file_id, caption
    return None, None, caption


async def _member_count(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> int:
    try:
        return int(await context.bot.get_chat_member_count(chat_id))
    except Exception:
        return 0


async def send_welcome(
    context: ContextTypes.DEFAULT_TYPE,
    chat,
    user: User,
) -> None:
    if user.is_bot:
        return
    enabled, text, kind, file_id = db.get_welcome(chat.id)
    if not enabled:
        return
    if _dedup(chat.id, user.id):
        return
    title = getattr(chat, "title", None) or "group"
    count = await _member_count(context, chat.id)
    html = format_member_html(text or DEFAULT_WELCOME, user, title, count)
    caption = html[:1024]
    try:
        if kind == "photo" and file_id:
            await context.bot.send_photo(chat.id, file_id, caption=caption, parse_mode="HTML")
        elif kind == "video" and file_id:
            await context.bot.send_video(chat.id, file_id, caption=caption, parse_mode="HTML")
        elif kind == "animation" and file_id:
            await context.bot.send_animation(chat.id, file_id, caption=caption, parse_mode="HTML")
        elif kind == "sticker" and file_id:
            await context.bot.send_sticker(chat.id, file_id)
            await context.bot.send_message(chat.id, html, parse_mode="HTML", disable_web_page_preview=True)
        else:
            await context.bot.send_message(
                chat.id, html, parse_mode="HTML", disable_web_page_preview=True
            )
    except Exception:
        log.exception("welcome send failed")


async def send_goodbye(
    context: ContextTypes.DEFAULT_TYPE,
    chat,
    user: User,
) -> None:
    if user.is_bot:
        return
    enabled, text = db.get_goodbye(chat.id)
    if not enabled or not text:
        return
    title = getattr(chat, "title", None) or "group"
    count = await _member_count(context, chat.id)
    html = format_member_html(text, user, title, count)
    try:
        await context.bot.send_message(
            chat.id, html, parse_mode="HTML", disable_web_page_preview=True
        )
    except Exception:
        log.exception("goodbye send failed")


async def on_user_joined(
    context: ContextTypes.DEFAULT_TYPE,
    chat,
    user: User,
    *,
    banned: bool,
) -> None:
    if banned:
        return
    from bot.verify import handle_join_gate

    if await handle_join_gate(context, chat, user, banned=banned):
        return
    await send_welcome(context, chat, user)


async def on_user_left(
    context: ContextTypes.DEFAULT_TYPE,
    chat,
    user: User,
    new_status: str,
) -> None:
    if new_status != ChatMember.LEFT:
        return
    from bot.verify import take_skip_goodbye

    if db.clear_pending_verify(chat.id, user.id) or take_skip_goodbye(chat.id, user.id):
        return
    await send_goodbye(context, chat, user)


def _cmd_body(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    msg = update.effective_message
    text = (msg.text or msg.caption or "") if msg else ""
    parts = text.split(maxsplit=1)
    if context.args:
        joined = " ".join(context.args)
        if joined.lower() in {"on", "off", "reset", "clear", "status", "noformat"}:
            rest = joined.split(maxsplit=1)
            if len(rest) > 1 and rest[0].lower() == "noformat":
                return rest[1]
            return ""
        return joined
    if len(parts) > 1:
        return parts[1].strip()
    return ""


async def cmd_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_admin(update):
        return
    chat = update.effective_chat
    msg = update.effective_message
    args = [a.lower() for a in (context.args or [])]
    enabled, text, kind, file_id = db.get_welcome(chat.id)

    if args and args[0] in {"on", "enable"}:
        db.set_welcome(chat.id, enabled=True)
        if not text and not file_id:
            db.set_welcome(chat.id, text=DEFAULT_WELCOME)
        await msg.reply_text("Welcome messages are on.")
        return
    if args and args[0] in {"off", "disable"}:
        db.set_welcome(chat.id, enabled=False)
        await msg.reply_text("Welcome messages are off.")
        return
    if args and args[0] in {"reset", "clear"}:
        db.clear_welcome(chat.id)
        await msg.reply_text("Welcome message cleared and turned off.")
        return

    reply = msg.reply_to_message
    body = _cmd_body(update, context)
    if args and args[0] == "noformat":
        body = " ".join(context.args[1:]).strip() if context.args else ""
    media_kind, media_id, _cap = _media_from_message(reply)
    if media_kind:
        caption = body or (reply.caption or "").strip() or DEFAULT_WELCOME
        db.set_welcome(
            chat.id,
            text=caption,
            kind=media_kind,
            file_id=media_id,
            enabled=True,
        )
        await msg.reply_text("Saved that as the welcome (with caption placeholders).")
        return
    if not body and reply:
        body = (reply.text or reply.caption or "").strip()
    if body:
        db.set_welcome(chat.id, text=body, kind="", file_id="", enabled=True)
        await msg.reply_text("Welcome message saved and turned on.")
        return

    state = "on" if enabled else "off"
    current = text or "(default once you turn it on)"
    extra = f"\nMedia: {kind}" if kind and file_id else ""
    await msg.reply_text(
        f"Welcome is {state}.{extra}\n\n"
        f"Current:\n{current}\n\n"
        f"Placeholders: {_PLACEHOLDERS}\n\n"
        f"{cmd('welcome')} Welcome {{user}} to {{chat}}!\n"
        f"{cmd('welcome')} on · off · reset\n"
        f"Reply to a photo/video/gif with {cmd('welcome')} to use it."
    )


async def cmd_goodbye(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_admin(update):
        return
    chat = update.effective_chat
    msg = update.effective_message
    args = [a.lower() for a in (context.args or [])]
    enabled, text = db.get_goodbye(chat.id)

    if args and args[0] in {"on", "enable"}:
        db.set_goodbye(chat.id, enabled=True)
        await msg.reply_text("Goodbye messages are on." if text else f"Set text first: {cmd('goodbye')} Bye {{user}}")
        return
    if args and args[0] in {"off", "disable"}:
        db.set_goodbye(chat.id, enabled=False)
        await msg.reply_text("Goodbye messages are off.")
        return
    if args and args[0] in {"reset", "clear"}:
        db.set_goodbye(chat.id, text="", enabled=False)
        await msg.reply_text("Goodbye message cleared.")
        return

    body = _cmd_body(update, context)
    reply = msg.reply_to_message
    if not body and reply and (reply.text or reply.caption):
        body = (reply.text or reply.caption or "").strip()
    if body:
        db.set_goodbye(chat.id, text=body, enabled=True)
        await msg.reply_text("Goodbye message saved and turned on.")
        return

    state = "on" if enabled else "off"
    await msg.reply_text(
        f"Goodbye is {state}.\n\n"
        f"Current:\n{text or '(none)'}\n\n"
        f"Placeholders: {_PLACEHOLDERS}\n"
        f"Only sent when someone leaves on their own, not after a ban.\n\n"
        f"{cmd('goodbye')} Bye {{user}} · {cmd('goodbye')} off"
    )


async def cmd_cleanservice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_admin(update):
        return
    args = [a.lower() for a in (context.args or [])]
    if args and args[0] in {"on", "enable"}:
        db.set_clean_service(update.effective_chat.id, True)
        await update.effective_message.reply_text(
            "I will delete Telegram's join/leave service messages when I can."
        )
        return
    if args and args[0] in {"off", "disable"}:
        db.set_clean_service(update.effective_chat.id, False)
        await update.effective_message.reply_text("Service-message cleanup is off.")
        return
    state = "on" if db.clean_service(update.effective_chat.id) else "off"
    await update.effective_message.reply_text(
        f"Clean service messages: {state}.\n{cmd('cleanservice')} on · off"
    )


async def cmd_rules(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group(update):
        return
    rules = db.get_rules(update.effective_chat.id)
    if not rules:
        await update.effective_message.reply_text(
            f"No rules set. Admins: {cmd('setrules')} then the text, or reply to a message."
        )
        return
    await update.effective_message.reply_text(rules)


async def cmd_setrules(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_admin(update):
        return
    body = " ".join(context.args or []).strip()
    reply = update.effective_message.reply_to_message
    if not body and reply:
        body = (reply.text or reply.caption or "").strip()
    if not body:
        await update.effective_message.reply_text(
            f"Send {cmd('setrules')} plus the rules, or reply to the rules message."
        )
        return
    db.set_rules(update.effective_chat.id, body)
    await update.effective_message.reply_text(f"Rules saved. Anyone can read them with {cmd('rules')}.")


async def cmd_clearrules(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_admin(update):
        return
    db.set_rules(update.effective_chat.id, None)
    await update.effective_message.reply_text("Rules cleared.")


async def on_left_service(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    message = update.effective_message
    if not chat or not message:
        return
    if not db.clean_service(chat.id):
        return
    try:
        await message.delete()
    except Exception:
        pass
