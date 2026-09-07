from __future__ import annotations

import asyncio
import logging
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, User
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from bot import db
from bot.commands import cmd
from bot.lock import LOCKED, OPEN
from bot.moderation import (
    format_user,
    is_immune,
    kick,
    mention,
    require_group_admin,
    resolve_target,
    send_to_log,
)
from bot.raid import parse_duration
from bot.welcome import send_welcome

log = logging.getLogger(__name__)

WHO = (
    "Reply to them, mention @username, or use their numeric id. "
    "Usernames work after I have seen that person in the group."
)
_starting: set[tuple[int, int]] = set()
_skip_goodbye: set[tuple[int, int]] = set()


async def _unmute(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int) -> None:
    try:
        chat = await context.bot.get_chat(chat_id)
        perms = chat.permissions or OPEN
        await context.bot.restrict_chat_member(chat_id, user_id, permissions=perms)
    except Exception:
        log.exception("verify unmute failed")


async def _delete_prompt(context: ContextTypes.DEFAULT_TYPE, chat_id: int, msg_id: int | None) -> None:
    if not msg_id:
        return
    try:
        await context.bot.delete_message(chat_id, msg_id)
    except Exception:
        pass


async def _expire_pending(application, chat_id: int, user_id: int, expires_at: int) -> None:
    delay = expires_at - time.time()
    if delay > 0:
        await asyncio.sleep(delay)
    pending = db.get_pending_verify(chat_id, user_id)
    if not pending or pending["expires_at"] > int(time.time()) + 1:
        return
    bot = application.bot
    db.clear_pending_verify(chat_id, user_id)
    _skip_goodbye.add((chat_id, user_id))
    if pending.get("prompt_msg_id"):
        try:
            await bot.delete_message(chat_id, pending["prompt_msg_id"])
        except Exception:
            pass
    try:
        await bot.ban_chat_member(chat_id, user_id)
        await bot.unban_chat_member(chat_id, user_id, only_if_banned=True)
    except Exception:
        log.exception("verify timeout kick failed")
        return
    try:
        await bot.send_message(chat_id, "Removed someone who did not verify in time.")
    except Exception:
        pass


def take_skip_goodbye(chat_id: int, user_id: int) -> bool:
    key = (chat_id, user_id)
    if key in _skip_goodbye:
        _skip_goodbye.discard(key)
        return True
    return False
    try:
        application.create_task(_expire_pending(application, chat_id, user_id, expires_at))
    except Exception:
        log.exception("could not schedule verify timeout")


async def schedule_pending_jobs(application) -> None:
    now = int(time.time())
    for row in db.list_all_pending_verify():
        _schedule_expire(application, row["chat_id"], row["user_id"], max(now + 1, row["expires_at"]))


async def handle_join_gate(
    context: ContextTypes.DEFAULT_TYPE,
    chat,
    user: User,
    *,
    banned: bool,
) -> bool:
    """Return True if welcome should be skipped (waiting to verify)."""
    if banned or user.id == context.bot.id:
        return True
    if user.is_bot:
        policy = db.verify_bot_policy(chat.id)
        if policy == "allow":
            return False
        await _remove_bot(context, chat, user, ban=policy == "ban")
        return True
    if not db.verify_enabled(chat.id):
        return False
    if is_immune(user.id, chat.id):
        return False
    try:
        member = await context.bot.get_chat_member(chat.id, user.id)
        if member.status in ("administrator", "creator"):
            return False
    except Exception:
        pass
    key = (chat.id, user.id)
    if key in _starting or db.get_pending_verify(chat.id, user.id):
        return True
    _starting.add(key)
    try:
        await _start_human_verify(context, chat, user)
    finally:
        _starting.discard(key)
    return True


async def _remove_bot(
    context: ContextTypes.DEFAULT_TYPE, chat, user: User, *, ban: bool
) -> None:
    chat_id = chat.id
    _skip_goodbye.add((chat_id, user.id))
    try:
        await context.bot.ban_chat_member(chat_id, user.id)
        if not ban:
            await context.bot.unban_chat_member(chat_id, user.id, only_if_banned=True)
    except Exception:
        log.exception("could not remove joining bot %s", user.id)
        return
    who = mention(user)
    action = "banned" if ban else "kicked"
    try:
        await context.bot.send_message(
            chat_id,
            f"Removed bot {who} ({action}).",
            parse_mode="HTML",
        )
    except Exception:
        pass
    await send_to_log(context, chat_id, f"Bot {who} {action} on join.", event="Verify")


async def _start_human_verify(context: ContextTypes.DEFAULT_TYPE, chat, user: User) -> None:
    chat_id = chat.id
    now = int(time.time())
    secs = db.verify_secs(chat_id)
    expires = now + secs
    try:
        await context.bot.restrict_chat_member(chat_id, user.id, permissions=LOCKED)
    except Exception:
        log.exception("verify mute failed")
        return
    minutes = max(1, secs // 60)
    markup = InlineKeyboardMarkup(
        [[InlineKeyboardButton("I'm human — verify", callback_data=f"vf:{user.id}")]]
    )
    try:
        sent = await context.bot.send_message(
            chat_id,
            f"{mention(user)} tap the button within {minutes} min to talk. "
            "Fake / spam accounts that skip this are removed. "
            "Telegram bots you add are kept.",
            parse_mode="HTML",
            reply_markup=markup,
        )
        prompt_id = sent.message_id
    except Exception:
        log.exception("verify prompt failed")
        prompt_id = None
    db.add_pending_verify(chat_id, user.id, now, expires, prompt_id)
    _schedule_expire(context.application, chat_id, user.id, expires)


async def on_verify_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data or not query.data.startswith("vf:"):
        return
    chat = update.effective_chat
    clicker = update.effective_user
    if not chat or not clicker:
        await query.answer()
        return
    try:
        target_id = int(query.data.split(":", 1)[1])
    except ValueError:
        await query.answer()
        return
    if clicker.id != target_id:
        await query.answer("This button is only for the person who just joined.", show_alert=True)
        return
    pending = db.get_pending_verify(chat.id, clicker.id)
    if not pending:
        await query.answer("You are already verified.", show_alert=True)
        return
    db.clear_pending_verify(chat.id, clicker.id)
    await _unmute(context, chat.id, clicker.id)
    await query.answer("You're in.")
    await _delete_prompt(context, chat.id, pending.get("prompt_msg_id") or query.message.message_id)
    await send_welcome(context, chat, clicker)


async def cmd_verify(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_admin(update):
        return
    chat_id = update.effective_chat.id
    msg = update.effective_message
    args = [a.lower() for a in (context.args or [])]
    if args and args[0] in {"on", "enable"}:
        db.set_verify_enabled(chat_id, True)
        await msg.reply_text(
            "Join verification is on. New people must tap the button. "
            "Telegram bots you add are kept. Spam joins that skip the button are kicked."
        )
        return
    if args and args[0] in {"off", "disable"}:
        db.set_verify_enabled(chat_id, False)
        await msg.reply_text("Join verification is off.")
        return
    if args and args[0] in {"list", "pending"}:
        await cmd_unverified(update, context)
        return
    if args and args[0] in {"pass", "approve"}:
        context.args = context.args[1:] if context.args else []
        await cmd_verifypass(update, context)
        return
    if args and args[0] == "bots":
        if len(args) > 1 and args[1] in {"allow", "keep", "off", "skip"}:
            db.set_verify_bot_policy(chat_id, "allow")
            await msg.reply_text(
                "Telegram bots (the ones you add from BotFather) are kept. "
                "Human-looking spam still has to verify, and raidmode still clears flood joins."
            )
            return
        if len(args) > 1 and args[1] in {"ban", "kick"}:
            db.set_verify_bot_policy(chat_id, args[1])
            await msg.reply_text(
                "Joining Telegram bots will be banned (cannot be added again)."
                if args[1] == "ban"
                else "Joining Telegram bots will be kicked (can be added again)."
            )
            return
        await msg.reply_text(
            f"{cmd('verify')} bots allow  — keep bots you add (default)\n"
            f"{cmd('verify')} bots kick · {cmd('verify')} bots ban  — old behaviour"
        )
        return
    if args and args[0] in {"time", "timeout"}:
        if len(args) < 2:
            await msg.reply_text(f"Example: {cmd('verify')} time 5m")
            return
        seconds = parse_duration(args[1])
        if not seconds or seconds > 3600:
            await msg.reply_text("Use 1m–60m, e.g. 5m.")
            return
        db.set_verify_secs(chat_id, seconds)
        await msg.reply_text(f"Unverified joins are removed after {seconds // 60} min.")
        return
    if args and args[0] in {"kickall", "purge"}:
        if "confirm" not in args:
            n = len(db.list_pending_verify(chat_id))
            await msg.reply_text(
                f"{n} people waiting. Send {cmd('verify')} kickall confirm to remove them."
            )
            return
        pending = db.list_pending_verify(chat_id)
        gone = 0
        for row in pending:
            db.clear_pending_verify(chat_id, row["user_id"])
            _skip_goodbye.add((chat_id, row["user_id"]))
            await _delete_prompt(context, chat_id, row.get("prompt_msg_id"))
            if await kick(context, chat_id, row["user_id"]):
                gone += 1
        await msg.reply_text(f"Removed {gone} unverified join(s).")
        return

    state = "on" if db.verify_enabled(chat_id) else "off"
    bots = {"allow": "kept", "kick": "kicked", "ban": "banned"}[db.verify_bot_policy(chat_id)]
    waiting = len(db.list_pending_verify(chat_id))
    await msg.reply_text(
        f"Join verify: {state}. Timeout: {db.verify_secs(chat_id) // 60} min. "
        f"Telegram bots: {bots}. Waiting: {waiting}.\n\n"
        f"{cmd('verify')} on · off\n"
        f"{cmd('unverified')} — who has not tapped yet\n"
        f"{cmd('verify')} time 5m\n"
        f"{cmd('verify')} bots allow  (default — keep bots you add)\n"
        f"{cmd('verify')} pass @user — skip the button\n"
        f"{cmd('verify')} kickall confirm"
    )


async def cmd_unverified(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_admin(update):
        return
    chat_id = update.effective_chat.id
    pending = db.list_pending_verify(chat_id)
    if not pending:
        await update.effective_message.reply_text("Nobody is waiting to verify.")
        return
    now = int(time.time())
    lines = [f"<b>Not verified yet</b> ({len(pending)})"]
    for row in pending[:40]:
        who = await format_user(context, chat_id, row["user_id"])
        left = max(0, row["expires_at"] - now)
        mins = max(1, (left + 59) // 60) if left else 0
        wait = "time's up" if left <= 0 else f"{mins} min left"
        lines.append(f"• {who} — {wait}")
    if len(pending) > 40:
        lines.append(f"…and {len(pending) - 40} more")
    lines.append(f"Skip one: {cmd('verify')} pass @user")
    await update.effective_message.reply_html("\n".join(lines), disable_web_page_preview=True)


async def cmd_verifypass(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_admin(update):
        return
    target = await resolve_target(update, context)
    if not target:
        await update.effective_message.reply_text(f"Who? {WHO}")
        return
    chat = update.effective_chat
    pending = db.clear_pending_verify(chat.id, target.id)
    await _unmute(context, chat.id, target.id)
    if pending:
        await _delete_prompt(context, chat.id, pending.get("prompt_msg_id"))
    await update.effective_message.reply_html(f"{mention(target)} is verified (skipped the button).")
    if pending:
        await send_welcome(context, chat, target)
