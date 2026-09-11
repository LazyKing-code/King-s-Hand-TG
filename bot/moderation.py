from __future__ import annotations

import logging
import time
from html import escape

from telegram import ChatMember, Message, Update, User
from telegram.constants import ChatType
from telegram.error import RetryAfter, TelegramError
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


_NOTICE_WINDOW = 5.0
_notice_batch: dict[tuple[int, int], dict] = {}


async def notify(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    html: str,
    event: str = "Notice",
    *,
    batch_user: User | None = None,
) -> None:
    body = html
    if batch_user is not None:
        key = (chat_id, batch_user.id)
        now = time.time()
        batch = _notice_batch.get(key)
        if batch and now < batch["expires"]:
            batch["count"] += 1
            batch["html"] = html
            batch["expires"] = now + _NOTICE_WINDOW
            extra = (
                f"\n({batch['count']} stickers removed just now)"
                if batch["count"] > 1
                else ""
            )
            body = html + extra
            try:
                await context.bot.edit_message_text(
                    body,
                    chat_id=chat_id,
                    message_id=batch["msg_id"],
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
            except TelegramError:
                pass
            await send_to_log(context, chat_id, body, event)
            return
        try:
            sent = await context.bot.send_message(
                chat_id,
                body,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception:
            log.exception("Group notify failed")
            await send_to_log(context, chat_id, body, event)
            return
        _notice_batch[key] = {
            "expires": now + _NOTICE_WINDOW,
            "count": 1,
            "msg_id": sent.message_id,
        }
        await send_to_log(context, chat_id, body, event)
        return
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


# Roles this bot will offer when promoting. Each is a set of the admin
# rights it will *try* to grant — it can still only grant a right the bot
# itself has, and it never grants can_promote_members (so an admin it made
# can never add/remove other admins, or edit itself out of demotion).
_ROLE_RIGHTS: dict[str, tuple[str, ...]] = {
    "helper": ("can_delete_messages", "can_pin_messages"),
    "mod": (
        "can_delete_messages",
        "can_restrict_members",
        "can_invite_users",
        "can_pin_messages",
        "can_manage_video_chats",
    ),
    "admin": (
        "can_manage_chat",
        "can_delete_messages",
        "can_restrict_members",
        "can_invite_users",
        "can_pin_messages",
        "can_manage_video_chats",
        "can_change_info",
    ),
}
_ROLE_ALIASES = {
    "moderator": "mod",
    "mods": "mod",
    "full": "admin",
    "senior": "admin",
    "minimal": "helper",
    "light": "helper",
    "junior": "helper",
}
DEFAULT_ADMIN_ROLE = "mod"
ROLE_NAMES = tuple(_ROLE_RIGHTS.keys())

_ROLE_DESCRIPTIONS = {
    "helper": "Can delete messages and pin. Cannot mute, ban, kick, or invite.",
    "mod": "Can delete messages, mute/ban/kick, invite, pin, and manage video chats.",
    "admin": "Everything mod has, plus manage chat settings and change group info.",
}


def parse_admin_role(args: list[str]) -> str:
    """Pick a known role out of free-form command args. Defaults to mod."""
    for raw in args:
        key = _ROLE_ALIASES.get(raw.strip().lower(), raw.strip().lower())
        if key in _ROLE_RIGHTS:
            return key
    return DEFAULT_ADMIN_ROLE


def role_description(role: str) -> str:
    return _ROLE_DESCRIPTIONS.get(role, _ROLE_DESCRIPTIONS[DEFAULT_ADMIN_ROLE])


def infer_admin_role(member) -> str | None:
    """Guess helper/mod/admin from Telegram rights when we have no stored role."""
    if getattr(member, "status", None) not in (ChatMember.ADMINISTRATOR, "administrator"):
        return None
    has = {
        name: bool(getattr(member, name, False))
        for name in (
            "can_manage_chat",
            "can_delete_messages",
            "can_restrict_members",
            "can_invite_users",
            "can_pin_messages",
            "can_manage_video_chats",
            "can_change_info",
        )
    }
    # Match from most to least privileged.
    for role in ("admin", "mod", "helper"):
        wanted = _ROLE_RIGHTS[role]
        if all(has.get(name, False) for name in wanted):
            return role
    if any(has.values()):
        return "admin"  # custom rights — still an admin
    return "admin"


def _admin_rights_kwargs(bot_member, chat, *, grant: bool, role: str = DEFAULT_ADMIN_ROLE) -> dict:
    """Only set rights Telegram allows this bot to grant, filtered to what the
    chosen role wants. Skip forum-only flags on normal groups — passing
    can_manage_topics there makes promote fail. Never grants can_promote_members:
    an admin this bot makes can never make/edit other admins."""
    wanted = set(_ROLE_RIGHTS.get(role, _ROLE_RIGHTS[DEFAULT_ADMIN_ROLE])) if grant else set()

    def bit(name: str) -> bool:
        return bool(grant and name in wanted and getattr(bot_member, name, False))

    flags = {
        "is_anonymous": False,
        "can_manage_chat": bit("can_manage_chat"),
        "can_delete_messages": bit("can_delete_messages"),
        "can_manage_video_chats": bit("can_manage_video_chats"),
        "can_restrict_members": bit("can_restrict_members"),
        "can_promote_members": False,
        "can_change_info": bit("can_change_info"),
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
            "can_restrict_members",
            "can_change_info",
            "can_invite_users",
            "can_pin_messages",
        )
    )


async def promote_limited(
    context: ContextTypes.DEFAULT_TYPE,
    chat,
    user_id: int,
    *,
    role: str = DEFAULT_ADMIN_ROLE,
) -> dict:
    bot_id = context.bot.id
    me = await context.bot.get_chat_member(chat.id, bot_id)
    if me.status not in (ChatMember.ADMINISTRATOR, ChatMember.OWNER):
        raise ValueError("I am not an admin in this group.")
    if me.status == ChatMember.ADMINISTRATOR and not me.can_promote_members:
        raise ValueError("I need the Add new admins permission.")
    flags = _admin_rights_kwargs(me, chat, grant=True, role=role)
    if not _has_grantable_right(flags):
        raise ValueError(
            "I need Delete messages (and ideally Restrict members, Pin messages, "
            "and Invite users) so I have something to grant."
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
    return flags


_DEMOTE_FLAGS = {
    "is_anonymous": False,
    "can_manage_chat": False,
    "can_delete_messages": False,
    "can_manage_video_chats": False,
    "can_restrict_members": False,
    "can_promote_members": False,
    "can_change_info": False,
    "can_invite_users": False,
    "can_post_messages": False,
    "can_edit_messages": False,
    "can_pin_messages": False,
    "can_post_stories": False,
    "can_edit_stories": False,
    "can_delete_stories": False,
    "can_manage_topics": False,
    "can_manage_direct_messages": False,
}


async def demote(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int) -> bool:
    try:
        me = await context.bot.get_chat_member(chat_id, context.bot.id)
        if me.status == ChatMember.ADMINISTRATOR and not getattr(
            me, "can_promote_members", False
        ):
            log.warning("Cannot demote %s: bot lacks Add admins", user_id)
            return False
        target = await context.bot.get_chat_member(chat_id, user_id)
        if target.status == ChatMember.OWNER:
            return False
        if target.status != ChatMember.ADMINISTRATOR:
            db.clear_admin_role(chat_id, user_id)
            return True
        if not getattr(target, "can_be_edited", False):
            log.warning(
                "Cannot demote %s: not promoted by this bot (can_be_edited=false)",
                user_id,
            )
            return False
        try:
            await context.bot.promote_chat_member(
                chat_id=chat_id, user_id=user_id, **_DEMOTE_FLAGS
            )
        except Exception:
            chat = await context.bot.get_chat(chat_id)
            await context.bot.promote_chat_member(
                chat_id=chat_id,
                user_id=user_id,
                **_admin_rights_kwargs(me, chat, grant=False),
            )
        after = await context.bot.get_chat_member(chat_id, user_id)
        ok = after.status != ChatMember.ADMINISTRATOR
        if ok:
            db.clear_admin_role(chat_id, user_id)
        return ok
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


_last_placeholder: dict[int, float] = {}


async def send_placeholder(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    now = time.time()
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
    try:
        status = await member_status(update, user.id)
    except Exception:
        return
    if status in (ChatMember.LEFT, ChatMember.BANNED):
        return
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
            batch_user=user,
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
                    batch_user=user,
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
                batch_user=user,
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
                batch_user=user,
            )
        else:
            await notify(
                context,
                chat_id,
                f"{who} reached 3/3, but the bot could not demote them.\n"
                f"The bot can only demote admins <b>it</b> made with {cmd('makeadmin')}. "
                f"Drop them yourself, then reply with {cmd('makeadmin')}.",
                event="Demote failed",
                batch_user=user,
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
            batch_user=user,
        )
        return

    ok = await kick(context, chat_id, user.id)
    db.reset_strikes(chat_id, user.id)
    await announce_kick(context, chat, user, reason, ok)


_SKIP_USER_IDS = {777000, 1087968824}  # Telegram / Group Anonymous Bot


def _user_from_seen_row(row: dict) -> User:
    return User(
        id=int(row["user_id"]),
        is_bot=bool(row["is_bot"]),
        first_name=row["first_name"] or str(row["user_id"]),
        last_name=row["last_name"] or None,
        username=row["username"] or None,
    )


def _users_in_update(update: Update) -> list[User]:
    found: dict[int, User] = {}

    def add(user: User | None) -> None:
        if user and user.id:
            found[user.id] = user

    add(update.effective_user)
    msg = update.effective_message
    if msg:
        add(msg.from_user)
        add(getattr(msg, "forward_from", None))
        origin = getattr(msg, "forward_origin", None)
        add(getattr(origin, "sender_user", None))
        add(msg.left_chat_member)
        if msg.reply_to_message:
            add(msg.reply_to_message.from_user)
            add(getattr(msg.reply_to_message, "forward_from", None))
        for user in msg.new_chat_members or []:
            add(user)
        for entity in tuple(msg.entities or ()) + tuple(msg.caption_entities or ()):
            if entity.user:
                add(entity.user)
    if update.chat_member:
        add(update.chat_member.new_chat_member.user)
        add(update.chat_member.old_chat_member.user)
    if update.callback_query:
        add(update.callback_query.from_user)
    return list(found.values())


async def remember_from_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Index every user Telegram sends us. Bots cannot look up @username otherwise."""
    for user in _users_in_update(update):
        db.remember_user(user)
    chat = update.effective_chat
    user = update.effective_user
    if (
        chat
        and user
        and not user.is_bot
        and chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)
    ):
        db.touch_member(chat.id, user.id)


async def _user_from_username(
    context: ContextTypes.DEFAULT_TYPE,
    username: str,
    chat_id: int | None = None,
) -> User | None:
    needle = username.strip().lstrip("@").lower()
    if not needle:
        return None
    if context.bot.username and needle == context.bot.username.lower():
        return None
    cached = db.lookup_seen_username(needle)
    if cached:
        if chat_id:
            try:
                member = await context.bot.get_chat_member(chat_id, int(cached["user_id"]))
                db.remember_user(member.user)
                return member.user
            except Exception:
                pass
        return _user_from_seen_row(cached)
    if chat_id:
        try:
            for admin in await context.bot.get_chat_administrators(chat_id):
                db.remember_user(admin.user)
                uname = admin.user.username
                if uname and uname.lower() == needle:
                    return admin.user
        except Exception:
            pass
    try:
        info = await context.bot.get_chat(f"@{needle}")
    except Exception:
        return None
    if info.type != ChatType.PRIVATE or not info.id:
        return None
    found = User(
        id=info.id,
        is_bot=bool(getattr(info, "is_bot", False)),
        first_name=info.first_name or needle,
        last_name=info.last_name,
        username=info.username,
    )
    db.remember_user(found)
    return found


async def resolve_target(update: Update, context: ContextTypes.DEFAULT_TYPE) -> User | None:
    msg: Message = update.effective_message
    chat = update.effective_chat
    chat_id = chat.id if chat else None
    reply = msg.reply_to_message
    if reply and reply.from_user:
        uid = reply.from_user.id
        if uid not in _SKIP_USER_IDS:
            if not (reply.sender_chat and chat and reply.sender_chat.id == chat.id):
                db.remember_user(reply.from_user)
                return reply.from_user
    text = msg.text or msg.caption or ""
    entities = tuple(msg.entities or ()) + tuple(msg.caption_entities or ())
    for entity in entities:
        if entity.type == "bot_command":
            continue
        if entity.type == "text_mention" and entity.user:
            db.remember_user(entity.user)
            return entity.user
        if entity.type == "mention":
            raw = text[entity.offset : entity.offset + entity.length]
            found = await _user_from_username(context, raw, chat_id)
            if found:
                return found
    args = context.args or []
    if not args:
        return None
    raw = args[0].strip()
    if raw.lstrip("-").isdigit():
        uid = int(raw)
        if chat:
            try:
                member = await context.bot.get_chat_member(chat.id, uid)
                db.remember_user(member.user)
                return member.user
            except Exception:
                pass
        cached = db.lookup_seen_id(uid)
        if cached:
            return _user_from_seen_row(cached)
        return User(id=uid, first_name=str(uid), is_bot=False)
    return await _user_from_username(context, raw, chat_id)
