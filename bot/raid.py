from __future__ import annotations

import asyncio
import logging
import re
import time

from telegram import ChatMember, Update
from telegram.constants import ChatType
from telegram.error import RetryAfter, TelegramError
from telegram.ext import ContextTypes

from bot import db
from bot.commands import cmd
from bot.config import IMMUNE_IDS, OWNER_IDS
from bot.moderation import is_immune, require_group_admin, require_group_owner, send_to_log
from bot.welcome import on_user_joined, on_user_left

log = logging.getLogger(__name__)

MAX_WINDOW = 7 * 24 * 3600
_PURGE_RUNNING: set[int] = set()
_RAIDMODE_STREAK: dict[int, int] = {}
_ADMIN_CACHE: dict[int, tuple[float, set[int]]] = {}

_IN_CHAT = {
    ChatMember.MEMBER,
    ChatMember.ADMINISTRATOR,
    ChatMember.OWNER,
    ChatMember.RESTRICTED,
}

_DURATION_RE = re.compile(
    r"^(\d+)\s*(m|min|mins|h|hr|hrs|hour|hours|d|day|days|w|wk|week|weeks)$",
    re.I,
)


def parse_duration(text: str) -> int | None:
    raw = (text or "").strip().lower()
    if not raw:
        return None
    match = _DURATION_RE.fullmatch(raw)
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2)[0].lower()
    seconds = amount * {"m": 60, "h": 3600, "d": 86400, "w": 604800}[unit]
    if seconds < 60 or seconds > MAX_WINDOW:
        return None
    return seconds


def format_duration(seconds: int) -> str:
    if seconds % 86400 == 0:
        days = seconds // 86400
        return f"{days} day" if days == 1 else f"{days} days"
    if seconds % 3600 == 0:
        hours = seconds // 3600
        return f"{hours} hour" if hours == 1 else f"{hours} hours"
    minutes = max(1, seconds // 60)
    return f"{minutes} min" if minutes == 1 else f"{minutes} min"


def _is_group(update: Update) -> bool:
    chat = update.effective_chat
    return bool(chat and chat.type in (ChatType.GROUP, ChatType.SUPERGROUP))


async def cached_admin_ids(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> set[int]:
    now = time.time()
    hit = _ADMIN_CACHE.get(chat_id)
    if hit and now - hit[0] < 60:
        return hit[1]
    ids: set[int] = set()
    try:
        for admin in await context.bot.get_chat_administrators(chat_id):
            ids.add(admin.user.id)
    except Exception:
        log.exception("Could not load admins for protect list")
        if hit:
            return hit[1]
    _ADMIN_CACHE[chat_id] = (now, ids)
    return ids


async def protected_ids(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> set[int]:
    ids: set[int] = set(OWNER_IDS) | set(IMMUNE_IDS)
    ids.add(context.bot.id)
    ids.update(db.list_trusted(chat_id))
    ids.update(db.list_extra_owners(chat_id))
    ids.update(await cached_admin_ids(context, chat_id))
    return ids


async def _maybe_raidmode_ban(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    user_id: int,
) -> bool:
    until = db.get_raid_until(chat_id)
    if until <= int(time.time()):
        return False
    if is_immune(user_id, chat_id) or user_id == context.bot.id:
        return False
    if user_id in await cached_admin_ids(context, chat_id):
        return False
    try:
        await context.bot.ban_chat_member(chat_id, user_id)
    except Exception:
        log.exception("Raidmode ban failed for %s", user_id)
        return False
    db.delete_join(chat_id, user_id)
    _RAIDMODE_STREAK[chat_id] = _RAIDMODE_STREAK.get(chat_id, 0) + 1
    streak = _RAIDMODE_STREAK[chat_id]
    if streak == 1 or streak % 10 == 0:
        await send_to_log(
            context,
            chat_id,
            f"Auto-banned a new join. Total this raidmode: {streak}.",
            event="Raidmode",
        )
    return True


def _record_join(chat_id: int, user_id: int, *, is_bot: bool = False) -> None:
    db.record_join(chat_id, user_id, int(time.time()))
    if not is_bot:
        db.touch_member(chat_id, user_id)


async def on_new_members(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    if not message or not chat or not _is_group(update):
        return
    members = message.new_chat_members or []
    banned_any = False
    for user in members:
        if user.is_bot and user.id == context.bot.id:
            continue
        _record_join(chat.id, user.id, is_bot=user.is_bot)
        banned = await _maybe_raidmode_ban(context, chat.id, user.id)
        if banned:
            banned_any = True
        await on_user_joined(context, chat, user, banned=banned)
    if banned_any or db.clean_service(chat.id):
        try:
            await message.delete()
        except Exception:
            pass


async def on_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    result = update.chat_member
    chat = update.effective_chat
    if not result or not chat or not _is_group(update):
        return
    user = result.new_chat_member.user
    if not user or user.id == context.bot.id:
        return
    old_status = result.old_chat_member.status
    new_status = result.new_chat_member.status
    if new_status in _IN_CHAT and old_status not in _IN_CHAT:
        _record_join(chat.id, user.id, is_bot=user.is_bot)
        banned = await _maybe_raidmode_ban(context, chat.id, user.id)
        await on_user_joined(context, chat, user, banned=banned)
    elif old_status in _IN_CHAT and new_status not in _IN_CHAT:
        db.forget_member(chat.id, user.id)
        await on_user_left(context, chat, user, new_status)


async def cmd_joins(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_admin(update):
        return
    chat_id = update.effective_chat.id
    now = int(time.time())
    windows = [("1h", 3600), ("6h", 6 * 3600), ("1d", 86400), ("3d", 3 * 86400), ("7d", MAX_WINDOW)]
    if context.args:
        seconds = parse_duration(context.args[0])
        if not seconds:
            await update.effective_message.reply_text(
                "Use a window up to 7 days, like 30m, 1h, 6h, 12h, 1d, 3d, 7d."
            )
            return
        count = db.count_joins_since(chat_id, now - seconds)
        await update.effective_message.reply_text(
            f"Joins I recorded in the last {format_duration(seconds)}: {count}"
        )
        return
    lines = ["Joins I recorded while in this group:"]
    for label, seconds in windows:
        lines.append(f"• Last {label}: {db.count_joins_since(chat_id, now - seconds)}")
    until = db.get_raid_until(chat_id)
    if until > now:
        lines.append(f"Raidmode ON for another {format_duration(until - now)}.")
    else:
        lines.append("Raidmode: off")
    lines.append("People who joined before I started tracking are not in this list.")
    await update.effective_message.reply_text("\n".join(lines))


def _parse_purge_args(args: list[str]) -> tuple[int | None, str, bool, str | None]:
    confirm = False
    mode = "ban"
    duration_raw = ""
    for token in args:
        low = token.lower()
        if low == "confirm":
            confirm = True
        elif low in {"kick", "ban"}:
            mode = low
        elif not duration_raw:
            duration_raw = token
    seconds = parse_duration(duration_raw) if duration_raw else None
    if not seconds:
        return None, mode, confirm, f"Use {cmd('purgejoins')} 2h  (30m–7d). Add confirm to run."
    return seconds, mode, confirm, None


async def cmd_purgejoins(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_owner(update):
        return
    chat = update.effective_chat
    chat_id = chat.id
    seconds, mode, confirm, error = _parse_purge_args(list(context.args or []))
    if error:
        await update.effective_message.reply_text(error)
        return
    assert seconds is not None
    if chat_id in _PURGE_RUNNING:
        await update.effective_message.reply_text("A purge is already running in this group.")
        return
    now = int(time.time())
    since = now - seconds
    user_ids = db.list_joins_since(chat_id, since)
    protected = await protected_ids(context, chat_id)
    targets = [uid for uid in user_ids if uid not in protected]
    skipped = len(user_ids) - len(targets)
    label = format_duration(seconds)
    action = "kick" if mode == "kick" else "ban"
    if not confirm:
        dur_token = next((a for a in (context.args or []) if parse_duration(a)), "2h")
        kick_bit = "kick " if mode == "kick" else ""
        await update.effective_message.reply_text(
            f"Last {label}: {len(user_ids)} recorded joins, {skipped} protected (owners/admins/trusted).\n"
            f"I would {action} {len(targets)} account(s). This can take a while for large raids.\n"
            "I can only remove people I saw join while I was running.\n\n"
            f"Send {cmd('purgejoins')} {dur_token} {kick_bit}confirm to start."
        )
        return
    if not targets:
        await update.effective_message.reply_text(
            f"No recorded joins to {action} in the last {label}."
        )
        return

    status = await update.effective_message.reply_text(
        f"Starting {action} of {len(targets)} joins from the last {label}…"
    )
    _PURGE_RUNNING.add(chat_id)
    context.application.create_task(
        _run_purge(
            bot=context.bot,
            chat_id=chat_id,
            targets=targets,
            mode=mode,
            label=label,
            status_chat_id=status.chat_id,
            status_message_id=status.message_id,
            log_context=context,
        )
    )


async def _run_purge(
    *,
    bot,
    chat_id: int,
    targets: list[int],
    mode: str,
    label: str,
    status_chat_id: int,
    status_message_id: int,
    log_context: ContextTypes.DEFAULT_TYPE,
) -> None:
    done = 0
    failed = 0
    total = len(targets)
    try:
        await send_to_log(
            log_context,
            chat_id,
            f"Purge started: {action_word(mode)} {total} joins from the last {label}.",
            event="Raid purge",
        )
        for user_id in targets:
            ok = await _ban_or_kick(bot, chat_id, user_id, mode)
            if ok:
                done += 1
                db.delete_join(chat_id, user_id)
            else:
                failed += 1
            processed = done + failed
            if processed % 20 == 0 or processed == total:
                try:
                    await bot.edit_message_text(
                        f"Purge last {label}: {done + failed}/{total} processed, {done} removed, {failed} failed.",
                        chat_id=status_chat_id,
                        message_id=status_message_id,
                    )
                except Exception:
                    pass
            await asyncio.sleep(0.12)
        summary = (
            f"Purge finished ({label}): {done} removed, {failed} failed, {total} targeted."
        )
        try:
            await bot.edit_message_text(summary, chat_id=status_chat_id, message_id=status_message_id)
        except Exception:
            await bot.send_message(status_chat_id, summary)
        await send_to_log(log_context, chat_id, summary, event="Raid purge")
    finally:
        _PURGE_RUNNING.discard(chat_id)


def action_word(mode: str) -> str:
    return "kick" if mode == "kick" else "ban"


async def _ban_or_kick(bot, chat_id: int, user_id: int, mode: str) -> bool:
    for _ in range(4):
        try:
            await bot.ban_chat_member(chat_id, user_id)
            if mode == "kick":
                await bot.unban_chat_member(chat_id, user_id, only_if_banned=True)
            return True
        except RetryAfter as exc:
            await asyncio.sleep(float(exc.retry_after) + 0.4)
        except TelegramError:
            log.exception("Purge remove failed for %s", user_id)
            return False
        except Exception:
            log.exception("Purge remove failed for %s", user_id)
            return False
    return False


async def cmd_raidmode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_owner(update):
        return
    chat_id = update.effective_chat.id
    args = [a.lower() for a in (context.args or [])]
    if not args or args[0] in {"status", "info"}:
        until = db.get_raid_until(chat_id)
        now = int(time.time())
        if until > now:
            await update.effective_message.reply_text(
                f"Raidmode is ON for another {format_duration(until - now)}. "
                f"New joins are auto-banned. {cmd('raidmode')} off to stop."
            )
        else:
            await update.effective_message.reply_text(
                f"Raidmode is off. Example: {cmd('raidmode')} 1h  (30m–7d) to auto-ban new joins."
            )
        return
    if args[0] in {"off", "stop", "disable"}:
        db.set_raid_until(chat_id, 0)
        _RAIDMODE_STREAK.pop(chat_id, None)
        await update.effective_message.reply_text("Raidmode off. New joins are allowed again.")
        await send_to_log(context, chat_id, "Raidmode turned off.", event="Raidmode")
        return
    seconds = parse_duration(args[0])
    if not seconds:
        await update.effective_message.reply_text(
            f"Use {cmd('raidmode')} 1h, {cmd('raidmode')} 6h, {cmd('raidmode')} 1d, or {cmd('raidmode')} off. Max 7 days."
        )
        return
    until = int(time.time()) + seconds
    db.set_raid_until(chat_id, until)
    _RAIDMODE_STREAK[chat_id] = 0
    await update.effective_message.reply_text(
        f"Raidmode ON for {format_duration(seconds)}. New joins will be banned automatically.\n"
        f"Owners, admins, and {cmd('trust')} users are skipped. {cmd('raidmode')} off to stop."
    )
    await send_to_log(
        context,
        chat_id,
        f"Raidmode enabled for {format_duration(seconds)}.",
        event="Raidmode",
    )
