from __future__ import annotations

import asyncio
import logging
import re
import time
from types import SimpleNamespace

from telegram import ChatMember, Update, User
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from bot import db
from bot.commands import cmd
from bot.config import IMMUNE_IDS, OWNER_IDS
from bot.lock import LOCKED, OPEN
from bot.moderation import (
    is_group_admin,
    is_owner,
    kick,
    mention,
    require_group,
    require_group_admin,
    resolve_target,
    send_to_log,
)

log = logging.getLogger(__name__)

WHO = (
    "Reply to them, mention @username, or use their numeric id. "
    "Usernames work after I have seen that person in the group."
)
_DUR = re.compile(
    r"^(\d+)\s*(s|sec|secs|second|seconds|m|min|mins|h|hr|hrs|hour|hours|"
    r"d|day|days|w|wk|week|weeks)$",
    re.I,
)
_MAX_RESTRICT = 366 * 86400


def parse_restrict(text: str) -> int | None:
    """Seconds to restrict, 0 = forever keyword, None = not a duration."""
    raw = (text or "").strip().lower()
    if raw in {"forever", "perm", "permanent", "indef", "indefinite"}:
        return 0
    match = _DUR.fullmatch(raw)
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2)[0].lower()
    seconds = amount * {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[unit]
    if seconds < 30 or seconds > _MAX_RESTRICT:
        return None
    return seconds


def format_restrict(seconds: int) -> str:
    if seconds >= 86400 and seconds % 86400 == 0:
        days = seconds // 86400
        return f"{days} day" if days == 1 else f"{days} days"
    if seconds >= 3600 and seconds % 3600 == 0:
        hours = seconds // 3600
        return f"{hours} hour" if hours == 1 else f"{hours} hours"
    if seconds >= 60:
        minutes = seconds // 60
        return f"{minutes} min" if minutes == 1 else f"{minutes} min"
    return f"{seconds}s"


def _duration_and_reason(args: list[str]) -> tuple[int | None, str]:
    """(seconds or None forever, reason). None seconds means forever."""
    if not args:
        return None, ""
    parsed = parse_restrict(args[0])
    if parsed is None:
        return None, " ".join(args).strip()
    reason = " ".join(args[1:]).strip()
    if parsed == 0:
        return None, reason
    return parsed, reason


async def _guard_target(update: Update, context: ContextTypes.DEFAULT_TYPE, target: User) -> bool:
    if target.id == context.bot.id:
        await update.effective_message.reply_text("I will not do that to myself.")
        return False
    if target.is_bot:
        await update.effective_message.reply_text("I will not do that to a bot.")
        return False
    if await is_owner(update, target.id):
        await update.effective_message.reply_text("I will not do that to an owner.")
        return False
    actor = update.effective_user
    if await is_group_admin(update, target.id) and not await is_owner(update, actor.id):
        await update.effective_message.reply_text("Only an owner can punish another admin.")
        return False
    return True


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    user = update.effective_user
    msg = update.effective_message
    if not chat or not user or not msg:
        return
    lines = [
        f"Chat: {escape(chat.title or chat.full_name or 'this chat')}",
        f"Chat id: <code>{chat.id}</code>",
        f"Your id: <code>{user.id}</code>",
    ]
    if chat.username:
        lines.append(f"Chat username: @{escape(chat.username)}")
    reply = msg.reply_to_message
    if reply and reply.from_user and reply.from_user.id not in {777000, 1087968824}:
        t = reply.from_user
        lines.append(f"Replied user: {mention(t)}")
        lines.append(f"Their id: <code>{t.id}</code>")
        if t.username:
            lines.append(f"Their username: @{escape(t.username)}")
    if reply and reply.sticker:
        st = reply.sticker
        lines.append(f"Sticker file id: <code>{escape(st.file_id)}</code>")
        if st.set_name:
            lines.append(f"Pack: <code>{escape(st.set_name)}</code>")
    await msg.reply_html("\n".join(lines), disable_web_page_preview=True)


async def cmd_admins(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group(update):
        return
    chat = update.effective_chat
    try:
        admins = await context.bot.get_chat_administrators(chat.id)
    except Exception:
        await update.effective_message.reply_text("I could not list admins.")
        return
    people = [a for a in admins if not a.user.is_bot]
    lines = ["<b>Admins</b>"]
    for admin in people:
        role = "creator" if admin.status == ChatMember.OWNER else "admin"
        lines.append(f"• {mention(admin.user)} — {role}")
    await update.effective_message.reply_html("\n".join(lines), disable_web_page_preview=True)


async def cmd_staff(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group(update):
        return
    msg = update.effective_message
    reply = msg.reply_to_message
    if not reply:
        await msg.reply_text(f"Reply to a message with {cmd('staff')} to ping the admins.")
        return
    try:
        admins = await context.bot.get_chat_administrators(update.effective_chat.id)
    except Exception:
        await msg.reply_text("I could not load admins.")
        return
    pings = []
    for admin in admins:
        user = admin.user
        if user.is_bot or user.id == context.bot.id:
            continue
        pings.append(mention(user))
    if not pings:
        await msg.reply_text("No human admins to ping.")
        return
    extra = " ".join(context.args or []).strip()
    why = escape(extra) if extra else "please take a look"
    reporter = mention(update.effective_user) if update.effective_user else "Someone"
    await msg.reply_html(
        f"{reporter} called staff ({why}).\n" + " ".join(pings),
        disable_web_page_preview=True,
    )


async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_admin(update):
        return
    target = await resolve_target(update, context)
    if not target:
        await update.effective_message.reply_text(f"Who? {WHO}")
        return
    if not await _guard_target(update, context, target):
        return
    args = list(context.args or [])
    if update.effective_message.reply_to_message:
        remaining = args
    elif args:
        remaining = args[1:]
    else:
        remaining = []
    seconds, reason = _duration_and_reason(remaining)
    reason = reason or "banned"
    until = int(time.time()) + seconds if seconds else None
    try:
        await context.bot.ban_chat_member(
            update.effective_chat.id,
            target.id,
            until_date=until,
        )
    except TelegramError as exc:
        await update.effective_message.reply_text(getattr(exc, "message", None) or str(exc))
        return
    except Exception:
        log.exception("ban failed")
        await update.effective_message.reply_text("Ban failed. I need Ban users permission.")
        return
    who = mention(target)
    when = f" for {format_restrict(seconds)}" if seconds else ""
    await update.effective_message.reply_html(f"{who} is banned{when}.")
    await send_to_log(
        context,
        update.effective_chat.id,
        f"{who} (<code>{target.id}</code>) banned{when}.\nReason: {escape(reason)}",
        event="Ban",
    )


async def cmd_unban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_admin(update):
        return
    target = await resolve_target(update, context)
    if not target:
        await update.effective_message.reply_text(f"Who? {WHO}")
        return
    try:
        await context.bot.unban_chat_member(
            update.effective_chat.id, target.id, only_if_banned=True
        )
    except TelegramError as exc:
        await update.effective_message.reply_text(getattr(exc, "message", None) or str(exc))
        return
    except Exception:
        log.exception("unban failed")
        await update.effective_message.reply_text("Unban failed.")
        return
    await update.effective_message.reply_html(f"{mention(target)} can join again.")


async def cmd_mute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_admin(update):
        return
    target = await resolve_target(update, context)
    if not target:
        await update.effective_message.reply_text(f"Who? {WHO}")
        return
    if not await _guard_target(update, context, target):
        return
    args = list(context.args or [])
    remaining = args if update.effective_message.reply_to_message else args[1:]
    seconds, reason = _duration_and_reason(remaining)
    reason = reason or "muted"
    until = int(time.time()) + seconds if seconds else None
    locked = LOCKED
    try:
        await context.bot.restrict_chat_member(
            update.effective_chat.id,
            target.id,
            permissions=locked,
            until_date=until,
        )
    except TelegramError as exc:
        await update.effective_message.reply_text(getattr(exc, "message", None) or str(exc))
        return
    except Exception:
        log.exception("mute failed")
        await update.effective_message.reply_text("Mute failed. I need Ban users permission.")
        return
    who = mention(target)
    when = f" for {format_restrict(seconds)}" if seconds else ""
    await update.effective_message.reply_html(f"{who} is muted{when}.")
    await send_to_log(
        context,
        update.effective_chat.id,
        f"{who} muted{when}.\nReason: {escape(reason)}",
        event="Mute",
    )


async def cmd_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_admin(update):
        return
    target = await resolve_target(update, context)
    if not target:
        await update.effective_message.reply_text(f"Who? {WHO}")
        return
    try:
        chat = await context.bot.get_chat(update.effective_chat.id)
        perms = chat.permissions or OPEN
        await context.bot.restrict_chat_member(
            update.effective_chat.id,
            target.id,
            permissions=perms,
        )
    except TelegramError as exc:
        await update.effective_message.reply_text(getattr(exc, "message", None) or str(exc))
        return
    except Exception:
        log.exception("unmute failed")
        await update.effective_message.reply_text("Unmute failed.")
        return
    await update.effective_message.reply_html(f"{mention(target)} can talk again.")


async def cmd_pin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_admin(update):
        return
    msg = update.effective_message
    target = msg.reply_to_message
    if not target:
        await msg.reply_text(f"Reply to a message with {cmd('pin')}. Add loud to notify everyone.")
        return
    args = [a.lower() for a in (context.args or [])]
    notify = any(a in {"loud", "notify", "alert"} for a in args)
    try:
        await context.bot.pin_chat_message(
            update.effective_chat.id,
            target.message_id,
            disable_notification=not notify,
        )
    except TelegramError as exc:
        await msg.reply_text(getattr(exc, "message", None) or str(exc))
        return
    await msg.reply_text("Pinned." if notify else "Pinned silently.")


async def cmd_unpin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_admin(update):
        return
    msg = update.effective_message
    reply = msg.reply_to_message
    try:
        if reply:
            await context.bot.unpin_chat_message(
                update.effective_chat.id, message_id=reply.message_id
            )
        else:
            await context.bot.unpin_chat_message(update.effective_chat.id)
    except TelegramError as exc:
        await msg.reply_text(getattr(exc, "message", None) or str(exc))
        return
    await msg.reply_text("Unpinned.")


async def cmd_unpinall(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_admin(update):
        return
    try:
        await context.bot.unpin_all_chat_messages(update.effective_chat.id)
    except TelegramError as exc:
        await update.effective_message.reply_text(getattr(exc, "message", None) or str(exc))
        return
    await update.effective_message.reply_text("All pins removed.")


async def cmd_del(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_admin(update):
        return
    msg = update.effective_message
    reply = msg.reply_to_message
    if not reply:
        await msg.reply_text(f"Reply to a message with {cmd('del')} to delete it.")
        return
    try:
        await reply.delete()
    except Exception:
        await msg.reply_text("Could not delete that message.")
        return
    try:
        await msg.delete()
    except Exception:
        pass


async def cmd_purge(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_admin(update):
        return
    msg = update.effective_message
    reply = msg.reply_to_message
    if not reply:
        await msg.reply_text(
            f"Reply to the first message to keep, with {cmd('purge')}. "
            "I delete from that message up to this command."
        )
        return
    start = reply.message_id
    end = msg.message_id
    if end <= start:
        await msg.reply_text("Nothing to purge.")
        return
    ids = list(range(start, end + 1))
    chat_id = update.effective_chat.id
    deleted = 0
    for i in range(0, len(ids), 100):
        chunk = ids[i : i + 100]
        try:
            await context.bot.delete_messages(chat_id, chunk)
            deleted += len(chunk)
        except Exception:
            for mid in chunk:
                try:
                    await context.bot.delete_message(chat_id, mid)
                    deleted += 1
                except Exception:
                    pass
    leftover = f"Removed {deleted} message(s)."
    try:
        await context.bot.send_message(chat_id, leftover)
    except Exception:
        pass


async def cmd_warn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_admin(update):
        return
    target = await resolve_target(update, context)
    if not target:
        await update.effective_message.reply_text(f"Who? {WHO}")
        return
    if not await _guard_target(update, context, target):
        return
    args = list(context.args or [])
    remaining = args if update.effective_message.reply_to_message else args[1:]
    reason = " ".join(remaining).strip() or "warned"
    chat_id = update.effective_chat.id
    count = db.get_warns(chat_id, target.id) + 1
    limit = db.warn_limit(chat_id)
    who = mention(target)
    if count >= limit:
        db.set_warns(chat_id, target.id, 0)
        ok = await kick(context, chat_id, target.id)
        if ok:
            await update.effective_message.reply_html(
                f"{who} reached {limit} warnings and was kicked.\nReason: {escape(reason)}"
            )
        else:
            db.set_warns(chat_id, target.id, count)
            await update.effective_message.reply_html(
                f"{who} is at {count}/{limit}, but I could not kick them."
            )
        await send_to_log(
            context,
            chat_id,
            f"{who} hit warn limit ({limit}). Kick {'ok' if ok else 'failed'}. {escape(reason)}",
            event="Warn",
        )
        return
    db.set_warns(chat_id, target.id, count)
    await update.effective_message.reply_html(
        f"{who} warning {count}/{limit}: {escape(reason)}"
    )
    await send_to_log(
        context, chat_id, f"{who} warn {count}/{limit}: {escape(reason)}", event="Warn"
    )


async def cmd_warns(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group(update):
        return
    actor = update.effective_user
    msg = update.effective_message
    looking = bool(context.args) or bool(msg and msg.reply_to_message)
    target = await resolve_target(update, context) if looking else actor
    if not target:
        target = actor
    if target.id != actor.id and not await is_group_admin(update, actor.id):
        await msg.reply_text("You can only check your own warnings.")
        return
    count = db.get_warns(update.effective_chat.id, target.id)
    limit = db.warn_limit(update.effective_chat.id)
    await msg.reply_html(
        f"{mention(target)} has {count}/{limit} warnings (separate from sticker strikes)."
    )


async def cmd_resetwarns(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_admin(update):
        return
    target = await resolve_target(update, context)
    if not target:
        await update.effective_message.reply_text(f"Who? {WHO}")
        return
    db.set_warns(update.effective_chat.id, target.id, 0)
    await update.effective_message.reply_html(f"Cleared warnings for {mention(target)}.")


async def cmd_warnlimit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_admin(update):
        return
    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text(
            f"Current limit: {db.warn_limit(update.effective_chat.id)} warnings then kick.\n"
            f"Example: {cmd('warnlimit')} 4"
        )
        return
    db.set_warn_limit(update.effective_chat.id, int(context.args[0]))
    await update.effective_message.reply_text(
        f"Warn limit is now {db.warn_limit(update.effective_chat.id)}."
    )


_DEAD_ERR = ("deactivated", "deleted", "frozen", "user_deactivated", "user is deactivated")


def _looks_dead(user: User) -> bool:
    if getattr(user, "is_deleted", False):
        return True
    name = (user.first_name or "").strip()
    if name.lower() in {"deleted account", "deleted"} and not user.username:
        return True
    return False


async def _protect_bot(bot, chat_id: int) -> set[int]:
    ids: set[int] = set(OWNER_IDS) | set(IMMUNE_IDS)
    ids.add(bot.id)
    ids.update(db.list_trusted(chat_id))
    ids.update(db.list_extra_owners(chat_id))
    try:
        for admin in await bot.get_chat_administrators(chat_id):
            ids.add(admin.user.id)
    except Exception:
        pass
    return ids


async def find_dead_accounts(bot, chat_id: int, *, limit: int = 1500) -> tuple[list[int], int]:
    protect = await _protect_bot(bot, chat_id)
    found: list[int] = []
    scanned = 0
    for uid in db.list_member_ids(chat_id):
        if uid in protect:
            continue
        scanned += 1
        if scanned > limit:
            break
        try:
            member = await bot.get_chat_member(chat_id, uid)
        except TelegramError as exc:
            err = (getattr(exc, "message", None) or str(exc)).lower()
            if any(token in err for token in _DEAD_ERR):
                found.append(uid)
            elif "user not found" in err or "chat not found" in err:
                db.forget_member(chat_id, uid)
            await asyncio.sleep(0.04)
            continue
        except Exception:
            await asyncio.sleep(0.04)
            continue
        if member.status in (ChatMember.LEFT, ChatMember.BANNED):
            db.forget_member(chat_id, uid)
        elif _looks_dead(member.user):
            found.append(uid)
        await asyncio.sleep(0.04)
    return found, scanned


async def kick_dead_accounts(bot, chat_id: int, ids: list[int]) -> int:
    kicked = 0
    for uid in ids:
        try:
            await bot.ban_chat_member(chat_id, uid)
            await bot.unban_chat_member(chat_id, uid, only_if_banned=True)
            kicked += 1
            db.forget_member(chat_id, uid)
        except Exception:
            log.exception("zombie kick failed for %s", uid)
        await asyncio.sleep(0.05)
    return kicked


async def run_daily_zombies(application) -> None:
    bot = application.bot
    for chat_id in db.list_known_chat_ids():
        if not db.zombies_daily(chat_id):
            continue
        found, scanned = await find_dead_accounts(bot, chat_id)
        if not found:
            log.info("daily zombies chat=%s scanned=%s none", chat_id, scanned)
            continue
        kicked = await kick_dead_accounts(bot, chat_id, found)
        log.info("daily zombies chat=%s removed=%s scanned=%s", chat_id, kicked, scanned)
        if kicked:
            await send_to_log(
                SimpleNamespace(bot=bot),
                chat_id,
                f"Daily cleanup removed {kicked} deleted/deactivated account(s).",
                event="Zombies",
            )


async def daily_zombies_loop(application) -> None:
    await asyncio.sleep(120)
    while True:
        try:
            await run_daily_zombies(application)
        except Exception:
            log.exception("daily zombies run failed")
        await asyncio.sleep(24 * 3600)


async def cmd_zombies(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_admin(update):
        return
    chat_id = update.effective_chat.id
    args = [a.lower() for a in (context.args or [])]
    if args and args[0] in {"daily", "auto"}:
        if len(args) > 1 and args[1] in {"on", "enable"}:
            db.set_zombies_daily(chat_id, True)
            await update.effective_message.reply_text(
                "Daily deleted-account cleanup is on. I scan people I have seen, once a day."
            )
            return
        if len(args) > 1 and args[1] in {"off", "disable"}:
            db.set_zombies_daily(chat_id, False)
            await update.effective_message.reply_text("Daily deleted-account cleanup is off.")
            return
        state = "on" if db.zombies_daily(chat_id) else "off"
        await update.effective_message.reply_text(
            f"Daily cleanup is {state}.\n"
            f"{cmd('zombies')} daily on · off\n"
            "Telegram does not tell bots who is frozen, so frozen accounts cannot be auto-removed."
        )
        return

    found, scanned = await find_dead_accounts(context.bot, chat_id, limit=400)
    confirm = "confirm" in args
    if not found:
        await update.effective_message.reply_text(
            f"No deleted/deactivated accounts in the members I have seen (scanned {scanned}).\n"
            "Frozen accounts are not flagged to bots, so they will not show here.\n"
            f"Daily auto-clean is {'on' if db.zombies_daily(chat_id) else 'off'}."
        )
        return
    if not confirm:
        await update.effective_message.reply_text(
            f"Found {len(found)} deleted/deactivated account(s) among people I have seen.\n"
            f"Send {cmd('zombies')} confirm to kick them now.\n"
            f"Daily auto-clean is {'on' if db.zombies_daily(chat_id) else 'off'} "
            f"({cmd('zombies')} daily off to stop it)."
        )
        return
    kicked = await kick_dead_accounts(context.bot, chat_id, found)
    await update.effective_message.reply_text(f"Removed {kicked} deleted/deactivated account(s).")
