from __future__ import annotations

import logging
import time
from collections import defaultdict, deque

from telegram import Update
from telegram.constants import ChatType
from telegram.ext import ContextTypes

from bot import db
from bot.lock import LOCKED
from bot.moderation import is_immune, is_owner, mention, require_group_admin, send_to_log
from bot.raid import cached_admin_ids, parse_duration

log = logging.getLogger(__name__)

_hits: dict[tuple[int, int], deque[float]] = defaultdict(deque)


async def cmd_flood(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_admin(update):
        return
    chat_id = update.effective_chat.id
    args = [a.lower() for a in (context.args or [])]
    enabled, limit, window, mute = db.flood_settings(chat_id)

    if not args or args[0] in {"status", "info"}:
        state = "on" if enabled else "off"
        await update.effective_message.reply_text(
            f"Anti-flood is {state}.\n"
            f"Trigger: {limit} messages in {window}s → mute {mute // 60} min.\n\n"
            "/flood on\n"
            "/flood off\n"
            "/flood 6 4     (6 messages in 4 seconds)\n"
            "/floodmute 10m"
        )
        return
    if args[0] in {"on", "enable"}:
        db.set_flood_enabled(chat_id, True)
        await update.effective_message.reply_text(
            f"Anti-flood on. {limit} messages in {window}s gets a {mute // 60} min mute."
        )
        return
    if args[0] in {"off", "disable"}:
        db.set_flood_enabled(chat_id, False)
        await update.effective_message.reply_text("Anti-flood off.")
        return
    if len(args) >= 2 and args[0].isdigit() and args[1].isdigit():
        new_limit = max(3, min(30, int(args[0])))
        new_window = max(2, min(30, int(args[1])))
        db.set_flood_limit(chat_id, new_limit, new_window)
        db.set_flood_enabled(chat_id, True)
        await update.effective_message.reply_text(
            f"Anti-flood on. {new_limit} messages in {new_window}s will mute."
        )
        return
    await update.effective_message.reply_text("Try /flood on, /flood off, or /flood 6 4")


async def cmd_floodmute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_admin(update):
        return
    if not context.args:
        await update.effective_message.reply_text("Example: /floodmute 10m")
        return
    seconds = parse_duration(context.args[0])
    if not seconds:
        await update.effective_message.reply_text("Use 1m–7d, e.g. /floodmute 10m")
        return
    db.set_flood_mute(update.effective_chat.id, seconds)
    await update.effective_message.reply_text(f"Flood mute set to {seconds // 60} min.")


async def on_flood(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not message or not chat or not user:
        return
    if user.is_bot:
        return
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return
    enabled, limit, window, mute_for = db.flood_settings(chat.id)
    if not enabled:
        return
    if is_immune(user.id, chat.id) or await is_owner(update, user.id):
        return
    if user.id in await cached_admin_ids(context, chat.id):
        return

    now = time.time()
    key = (chat.id, user.id)
    times = _hits[key]
    times.append(now)
    cutoff = now - window
    while times and times[0] < cutoff:
        times.popleft()
    if len(times) < limit:
        return

    times.clear()
    until = int(now) + mute_for
    try:
        await context.bot.restrict_chat_member(
            chat.id,
            user.id,
            permissions=LOCKED,
            until_date=until,
        )
    except Exception:
        log.exception("flood mute failed")
        return
    try:
        await message.delete()
    except Exception:
        pass
    minutes = mute_for // 60
    who = mention(user)
    try:
        await context.bot.send_message(
            chat.id,
            f"{who} hit the flood limit and is muted for {minutes} min.",
            parse_mode="HTML",
        )
    except Exception:
        pass
    await send_to_log(
        context,
        chat.id,
        f"{who} muted {minutes} min for flood ({limit}/{window}s).",
        event="Flood",
    )
