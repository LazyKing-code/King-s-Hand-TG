from __future__ import annotations

import logging
import time
from html import escape

from telegram import ChatMember, Message, Update, User
from telegram.constants import ChatType
from telegram.error import RetryAfter
from telegram.ext import ContextTypes

from bot import db
from bot.commands import cmd
from bot.config import IMMUNE_IDS, OWNER_IDS
from bot.placeholder import ensure_placeholder_file

log = logging.getLogger(__name__)


def mention(user: User) -> str:
    name = escape(user.full_name or user.username or str(user.id))
    return f'<a href="tg://user?id={user.id}">{name}</a>'


def is_immune(user_id: int, chat_id: int | None = None) -> bool:
    if user_id in OWNER_IDS or user_id in IMMUNE_IDS:
        return True
    if chat_id is None:
        return False
    return db.is_extra_owner(chat_id, user_id) or db.is_trusted(chat_id, user_id)


async def is_owner(update: Update, user_id: int) -> bool:
    if user_id in OWNER_IDS:
        return True
    chat = update.effective_chat
    if not chat:
        return False
    if db.is_extra_owner(chat.id, user_id):
        return True
    try:
        member = await chat.get_member(user_id)
        return member.status == ChatMember.OWNER
    except Exception:
        return False


async def require_group_owner(update: Update) -> bool:
    user = update.effective_user
    if not user:
        return False
    if update.effective_chat and update.effective_chat.type not in (
        ChatType.GROUP,
        ChatType.SUPERGROUP,
    ):
        await update.effective_message.reply_text("Use that in the group, not here.")
        return False
    if await is_owner(update, user.id):
        return True
    await update.effective_message.reply_text("Only the owner can use that.")
    return False


async def is_group_admin(update: Update, user_id: int) -> bool:
    if await is_owner(update, user_id):
        return True
    chat = update.effective_chat
    if not chat:
        return False
    try:
        member = await chat.get_member(user_id)
        return member.status in (ChatMember.ADMINISTRATOR, ChatMember.OWNER)
    except Exception:
        return False


async def require_group_admin(update: Update) -> bool:
    user = update.effective_user
    if not user:
        return False
    if update.effective_chat and update.effective_chat.type not in (
        ChatType.GROUP,
        ChatType.SUPERGROUP,
    ):
        await update.effective_message.reply_text("Use that in the group, not here.")
        return False
    if await is_group_admin(update, user.id):
        return True
    await update.effective_message.reply_text("Only admins can use that.")
    return False


async def require_group(update: Update) -> bool:
    if update.effective_chat and update.effective_chat.type in (
        ChatType.GROUP,
        ChatType.SUPERGROUP,
    ):
        return True
    if update.effective_message:
        await update.effective_message.reply_text("Use that in the group, not here.")
    return False


async def member_status(update: Update, user_id: int) -> str:
    chat = update.effective_chat
    member = await chat.get_member(user_id)
    return member.status


DEFAULT_KICK_MSG = "{user} was kicked."


def format_kick_html(template: str | None, user: User, reason: str, chat_title: str) -> str:
    raw = (template or "").strip() or DEFAULT_KICK_MSG
    name = user.full_name or user.username or str(user.id)
    username = f"@{user.username}" if user.username else "—"
    html = escape(raw)
    html = html.replace("{user}", mention(user))
    html = html.replace("{name}", escape(name))
    html = html.replace("{username}", escape(username))
    html = html.replace("{id}", str(user.id))
    html = html.replace("{reason}", escape(reason))
    html = html.replace("{chat}", escape(chat_title or "group"))
    return html


def forwarded_chat(message: Message | None):
    if not message:
        return None
    origin = getattr(message, "forward_origin", None)
    return getattr(origin, "chat", None)


async def send_to_log(
    context: ContextTypes.DEFAULT_TYPE,
    source_chat_id: int,
    html: str,
    event: str,
) -> None:
    log_chat_id = db.get_log_chat(source_chat_id)
    if not log_chat_id or log_chat_id == source_chat_id:
        return
    title = str(source_chat_id)
    try:
        source = await context.bot.get_chat(source_chat_id)
        title = source.title or source.full_name or title
    except Exception:
        pass
    body = f"<b>{escape(event)}</b>\nFrom: {escape(title)}\n{html}"
    try:
        await context.bot.send_message(
            log_chat_id,
            body,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception:
        log.exception("Log chat send failed for %s", source_chat_id)


async def notify(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    html: str,
    event: str = "Notice",
) -> None:
    try:
        await context.bot.send_message(
            chat_id,
            html,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception:
        log.exception("Group notify failed")
    await send_to_log(context, chat_id, html, event)


async def announce_kick(
    context: ContextTypes.DEFAULT_TYPE,
    chat,
    user: User,
    reason: str,
    ok: bool,
) -> None:
    who = mention(user)
    if not ok:
        await notify(
            context,
            chat.id,
            f"Could not kick {who}.",
            event="Kick failed",
        )
        return
    title = getattr(chat, "title", None) or str(chat.id)
    html = format_kick_html(db.get_kick_message(chat.id), user, reason, title)
    try:
        await context.bot.send_message(
            chat.id,
            html,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception:
        log.exception("Kick message failed")
    await send_to_log(
        context,
        chat.id,
        f"User: {who} (<code>{user.id}</code>)\nReason: {escape(reason)}",
        event="Kicked",
    )


async def format_user(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int) -> str:
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        return f"{mention(member.user)} (<code>{user_id}</code>)"
    except Exception:
        return f"<code>{user_id}</code>"


def _admin_rights_kwargs(bot_member, chat, *, grant: bool) -> dict:
    """Only set rights Telegram allows this bot to grant. Skip forum-only flags
    on normal groups — passing can_manage_topics there makes promote fail."""

    def bit(name: str) -> bool:
        return bool(grant and getattr(bot_member, name, False))

    flags = {
        "is_anonymous": False,
        "can_manage_chat": bit("can_manage_chat"),
        "can_delete_messages": bit("can_delete_messages"),
        "can_manage_video_chats": bit("can_manage_video_chats"),
        "can_restrict_members": False,
        "can_promote_members": False,
        "can_change_info": False,
        "can_invite_users": bit("can_invite_users"),
        "can_pin_messages": bit("can_pin_messages"),
    }
    if getattr(chat, "is_forum", False):
        flags["can_manage_topics"] = False
    return flags


def _has_grantable_right(flags: dict) -> bool:
    return any(
        flags.get(name)
        for name in (
            "can_manage_chat",
            "can_delete_messages",
            "can_manage_video_chats",
            "can_invite_users",
            "can_pin_messages",
        )
    )


async def promote_limited(
    context: ContextTypes.DEFAULT_TYPE,
    chat,
    user_id: int,
) -> None:
    bot_id = context.bot.id
    me = await context.bot.get_chat_member(chat.id, bot_id)
    if me.status not in (ChatMember.ADMINISTRATOR, ChatMember.OWNER):
        raise ValueError("I am not an admin in this group.")
    if me.status == ChatMember.ADMINISTRATOR and not me.can_promote_members:
        raise ValueError("I need the Add new admins permission.")
    flags = _admin_rights_kwargs(me, chat, grant=True)
    if not _has_grantable_right(flags):
        raise ValueError(
            "I need Delete messages (and ideally Pin messages and Invite users) "
            "so I have something to grant."
        )
    target = await context.bot.get_chat_member(chat.id, user_id)
    if target.status == ChatMember.OWNER:
        raise ValueError("They already own the group.")
    if target.status == ChatMember.ADMINISTRATOR and not getattr(
        target, "can_be_edited", False
    ):
        raise ValueError(
            "They are already admin, and I did not promote them, so I cannot "
            f"edit their rights. Drop them in Telegram, then {cmd('makeadmin')} again."
        )
    await context.bot.promote_chat_member(chat_id=chat.id, user_id=user_id, **flags)


async def demote(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int) -> bool:
    try:
        chat = await context.bot.get_chat(chat_id)
        me = await context.bot.get_chat_member(chat_id, context.bot.id)
        await context.bot.promote_chat_member(
            chat_id=chat_id,
            user_id=user_id,
            **_admin_rights_kwargs(me, chat, grant=False),
        )
        return True
    except Exception:
        log.exception("Failed to demote %s", user_id)
        return False


async def kick(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int) -> bool:
    try:
        await context.bot.ban_chat_member(chat_id, user_id)
        await context.bot.unban_chat_member(chat_id, user_id, only_if_banned=True)
        return True
    except Exception:
        log.exception("Failed to kick %s", user_id)
        return False


def _should_keep_admin(chat_id: int, user_id: int) -> bool:
    return user_id in OWNER_IDS or db.is_extra_owner(chat_id, user_id)


async def demote_all_admins(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
) -> tuple[int, list[str]]:
    admins = await context.bot.get_chat_administrators(chat_id)
    bot_id = context.bot.id
    ok_count = 0
    failed: list[str] = []
    for admin in admins:
        user = admin.user
        if user.id == bot_id or user.is_bot:
            continue
        if admin.status == ChatMember.OWNER:
            continue
        if _should_keep_admin(chat_id, user.id):
            continue
        if await demote(context, chat_id, user.id):
            ok_count += 1
        else:
            failed.append(mention(user))
    return ok_count, failed


_PLACEHOLDER_GAP = 18.0
_last_placeholder: dict[int, float] = {}


async def send_placeholder(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    now = time.time()
    last = _last_placeholder.get(chat_id, 0.0)
    if now - last < _PLACEHOLDER_GAP:
        return
    _last_placeholder[chat_id] = now

    async def _send() -> None:
        custom = db.get_placeholder(chat_id)
        if custom:
            try:
                await context.bot.send_sticker(chat_id, custom)
                return
            except RetryAfter as exc:
                _last_placeholder[chat_id] = time.time() + float(exc.retry_after)
                log.warning("Placeholder delayed %ss in %s", exc.retry_after, chat_id)
                return
            except Exception:
                log.exception("Custom placeholder failed for chat %s", chat_id)

        cached = db.kv_get("default_placeholder_file_id")
        if cached:
            try:
                await context.bot.send_sticker(chat_id, cached)
                return
            except RetryAfter as exc:
                _last_placeholder[chat_id] = time.time() + float(exc.retry_after)
                return
            except Exception:
                log.exception("Cached default placeholder failed")
                db.kv_set("default_placeholder_file_id", None)

        path = ensure_placeholder_file()
        try:
            msg = await context.bot.send_sticker(chat_id, path)
            if msg.sticker and msg.sticker.file_id:
                db.kv_set("default_placeholder_file_id", msg.sticker.file_id)
            return
        except RetryAfter as exc:
            _last_placeholder[chat_id] = time.time() + float(exc.retry_after)
            return
        except Exception:
            log.exception("Default placeholder sticker failed")
        try:
            await context.bot.send_photo(chat_id, path)
        except Exception:
            log.exception("Placeholder photo fallback failed")

    try:
        await _send()
    except Exception:
        log.exception("Placeholder failed")


async def apply_punishment(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user: User,
    reason: str,
) -> None:
    chat = update.effective_chat
    assert chat
    chat_id = chat.id
    status = await member_status(update, user.id)
    strikes, kick_on_next = db.get_strikes(chat_id, user.id)
    approved = db.is_approved(chat_id, user.id)
    who = mention(user)
    why = escape(reason)

    if approved:
        db.set_approved(chat_id, user.id, False)
        db.set_strikes(chat_id, user.id, strikes, kick_on_next=True)
        await notify(
            context,
            chat_id,
            f"{who} was on {cmd('approve')}. I removed that sticker ({why}).\n"
            "Approval is gone. Next banned sticker will get them kicked.",
            event="Unapproved",
        )
        return

    if kick_on_next:
        if status == ChatMember.ADMINISTRATOR:
            if not await demote(context, chat_id, user.id):
                await notify(
                    context,
                    chat_id,
                    f"Could not kick {who} while they are still admin. "
                    f"Drop them, then {cmd('makeadmin')} them through the bot.",
                    event="Kick failed",
                )
                return
        ok = await kick(context, chat_id, user.id)
        db.reset_strikes(chat_id, user.id)
        await announce_kick(context, chat, user, reason, ok)
        return

    if status == ChatMember.ADMINISTRATOR:
        strikes += 1
        if strikes < 3:
            db.set_strikes(chat_id, user.id, strikes, False)
            await notify(
                context,
                chat_id,
                f"{who}, I removed that sticker ({why}). Warning {strikes}/3.\n"
                "At 3 warnings you will be demoted.",
                event="Warning",
            )
            return
        ok = await demote(context, chat_id, user.id)
        db.set_strikes(chat_id, user.id, strikes, kick_on_next=True)
        if ok:
            await notify(
                context,
                chat_id,
                f"{who} reached 3/3 warnings and was demoted.\n"
                "Sending another banned sticker will get them kicked.",
                event="Demoted",
            )
        else:
            await notify(
                context,
                chat_id,
                f"{who} reached 3/3, but the bot could not demote them.\n"
                f"The bot can only demote admins <b>it</b> made with {cmd('makeadmin')}. "
                f"Drop them yourself, then reply with {cmd('makeadmin')}.",
                event="Demote failed",
            )
        return

    strikes += 1
    if strikes < 3:
        db.set_strikes(chat_id, user.id, strikes, False)
        await notify(
            context,
            chat_id,
            f"{who}, I removed that sticker ({why}). Warning {strikes}/3.",
            event="Warning",
        )
        return

    ok = await kick(context, chat_id, user.id)
    db.reset_strikes(chat_id, user.id)
    await announce_kick(context, chat, user, reason, ok)


_SKIP_USER_IDS = {777000, 1087968824}  # Telegram / Group Anonymous Bot


async def _user_from_username(context: ContextTypes.DEFAULT_TYPE, username: str) -> User | None:
    name = username.strip()
    if not name:
        return None
    if not name.startswith("@"):
        name = f"@{name}"
    try:
        info = await context.bot.get_chat(name)
    except Exception:
        return None
    if info.type != ChatType.PRIVATE or not info.id:
        return None
    return User(
        id=info.id,
        is_bot=False,
        first_name=info.first_name or name,
        last_name=info.last_name,
        username=info.username,
    )


async def resolve_target(update: Update, context: ContextTypes.DEFAULT_TYPE) -> User | None:
    msg: Message = update.effective_message
    reply = msg.reply_to_message
    if reply and reply.from_user:
        uid = reply.from_user.id
        if uid not in _SKIP_USER_IDS:
            if not (reply.sender_chat and update.effective_chat
                    and reply.sender_chat.id == update.effective_chat.id):
                return reply.from_user
    if msg.entities:
        for entity in msg.entities:
            if entity.type == "text_mention" and entity.user:
                return entity.user
            if entity.type == "mention":
                raw = (msg.text or msg.caption or "")[entity.offset : entity.offset + entity.length]
                found = await _user_from_username(context, raw)
                if found:
                    return found
    args = context.args or []
    if not args:
        return None
    raw = args[0].strip()
    if raw.lstrip("-").isdigit():
        uid = int(raw)
        chat = update.effective_chat
        if chat:
            try:
                member = await context.bot.get_chat_member(chat.id, uid)
                return member.user
            except Exception:
                pass
        return User(id=uid, first_name=str(uid), is_bot=False)
    return await _user_from_username(context, raw)
