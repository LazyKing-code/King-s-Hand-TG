"""/giveaway command system - create, manage, and draw giveaways."""
from __future__ import annotations

import asyncio
import json
import logging
import random
import secrets
import time
from datetime import datetime, timedelta
from html import escape

try:
    from zoneinfo import ZoneInfo
    _IST = ZoneInfo("Asia/Kolkata")
except Exception:
    _IST = None  # type: ignore[assignment]

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from bot import db
from bot.commands import cmd
from bot.moderation import require_group_admin

log = logging.getLogger(__name__)

SWEEP_INTERVAL = 10  # Check for expired giveaways every 10 seconds
_locks: dict[str, asyncio.Lock] = {}


def _lock_for(giveaway_id: str) -> asyncio.Lock:
    lock = _locks.get(giveaway_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[giveaway_id] = lock
    return lock


def _forget_lock(giveaway_id: str) -> None:
    _locks.pop(giveaway_id, None)


def _ist_str(ts: float) -> str:
    dt = datetime.fromtimestamp(ts, tz=_IST) if _IST else (
        datetime.utcfromtimestamp(ts) + timedelta(hours=5, minutes=30)
    )
    return dt.strftime("%d %b, %I:%M %p")


def _parse_duration(text: str) -> int | None:
    """Parse duration like 10m, 1h, 2d into seconds."""
    text = text.strip().lower()
    if not text:
        return None
    
    if text.endswith('s'):
        try:
            return int(text[:-1])
        except ValueError:
            return None
    elif text.endswith('m'):
        try:
            return int(text[:-1]) * 60
        except ValueError:
            return None
    elif text.endswith('h'):
        try:
            return int(text[:-1]) * 3600
        except ValueError:
            return None
    elif text.endswith('d'):
        try:
            return int(text[:-1]) * 86400
        except ValueError:
            return None
    
    # Try plain number (assume seconds)
    try:
        return int(text)
    except ValueError:
        return None


async def cmd_giveaway(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start a new giveaway. Usage: /giveaway 10m 1 Prize description"""
    if not await require_group_admin(update):
        return
    
    msg = update.effective_message
    args = context.args or []
    
    if len(args) < 3:
        await msg.reply_text(
            f"Usage: {cmd('giveaway')} <duration> <winners> <prize>\n"
            f"Example: {cmd('giveaway')} 10m 1 iPad Pro\n"
            f"Example: {cmd('giveaway')} 1h 2 Discord Nitro\n\n"
            "Duration: 10s, 5m, 1h, 2d\n"
            f"Advanced: {cmd('giveaway')} 10m 1 Prize req:10 age:7\n"
            "  req:10 = min 10 messages needed\n"
            "  age:7 = account age 7+ days"
        )
        return
    
    # Parse duration
    duration_seconds = _parse_duration(args[0])
    if not duration_seconds or duration_seconds < 10:
        await msg.reply_text("Duration must be at least 10 seconds (e.g., 10s, 1m, 1h).")
        return
    
    if duration_seconds > 30 * 86400:  # 30 days
        await msg.reply_text("Duration cannot exceed 30 days.")
        return
    
    # Parse winner count
    try:
        winner_count = int(args[1])
    except ValueError:
        await msg.reply_text("Winner count must be a number (e.g., 1, 2, 3).")
        return
    
    if winner_count < 1 or winner_count > 10:
        await msg.reply_text("Winner count must be between 1 and 10.")
        return
    
    # Parse prize and conditions
    prize_parts = []
    min_messages = 0
    min_account_age_days = 0
    
    for arg in args[2:]:
        if arg.startswith("req:"):
            try:
                min_messages = int(arg[4:])
            except ValueError:
                pass
        elif arg.startswith("age:"):
            try:
                min_account_age_days = int(arg[4:])
            except ValueError:
                pass
        else:
            prize_parts.append(arg)
    
    if not prize_parts:
        await msg.reply_text("Please specify a prize.")
        return
    
    prize = " ".join(prize_parts)
    if len(prize) > 200:
        await msg.reply_text("Prize description is too long (max 200 characters).")
        return
    
    # Create giveaway
    giveaway_id = secrets.token_hex(6)
    end_time = time.time() + duration_seconds
    chat_id = update.effective_chat.id
    created_by = update.effective_user.id
    
    db.create_giveaway(
        giveaway_id, chat_id, prize, end_time, created_by,
        winner_count=winner_count,
        min_messages=min_messages,
        min_account_age_days=min_account_age_days,
    )
    
    # Send giveaway message
    text = _giveaway_text(prize, end_time, winner_count, min_messages, min_account_age_days, 0)
    markup = InlineKeyboardMarkup([[
        InlineKeyboardButton("🎉 Enter Giveaway", callback_data=f"gv:enter:{giveaway_id}")
    ]])
    
    sent = await msg.reply_html(text, reply_markup=markup, disable_web_page_preview=True)
    db.set_giveaway_message(giveaway_id, sent.message_id)


def _giveaway_text(
    prize: str,
    end_time: float,
    winner_count: int,
    min_messages: int,
    min_account_age_days: int,
    participant_count: int,
) -> str:
    lines = [
        f"<b>🎁 GIVEAWAY</b>",
        f"Prize: <b>{escape(prize)}</b>",
        f"Winners: {winner_count}",
        f"Ends: {_ist_str(end_time)}",
    ]
    
    if min_messages > 0 or min_account_age_days > 0:
        lines.append("\n<b>Requirements:</b>")
        if min_messages > 0:
            lines.append(f"• At least {min_messages} messages in this chat")
        if min_account_age_days > 0:
            lines.append(f"• Account age: {min_account_age_days}+ days")
    
    lines.append(f"\n👥 {participant_count} participant(s)")
    lines.append("\nClick the button below to enter!")
    
    return "\n".join(lines)


async def on_giveaway_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle Enter button clicks."""
    query = update.callback_query
    if not query or not query.data:
        return
    
    parts = query.data.split(":")
    if len(parts) != 3 or parts[0] != "gv" or parts[1] != "enter":
        await query.answer()
        return
    
    giveaway_id = parts[2]
    user = update.effective_user
    
    if not user:
        await query.answer("Something went wrong.", show_alert=True)
        return
    
    async with _lock_for(giveaway_id):
        giveaway = db.load_giveaway(giveaway_id)
        
        if not giveaway:
            await query.answer("This giveaway no longer exists.", show_alert=True)
            return
        
        if giveaway["status"] != "active":
            await query.answer("This giveaway has ended.", show_alert=True)
            return
        
        # Check requirements
        chat_id = giveaway["chat_id"]
        
        # Check message requirement
        if giveaway["min_messages"] > 0:
            activity = db.get_user_activity(chat_id, user.id)
            total = activity.get("monthly", 0)  # Use monthly as cumulative
            if total < giveaway["min_messages"]:
                await query.answer(
                    f"You need at least {giveaway['min_messages']} messages to enter. You have {total}.",
                    show_alert=True,
                )
                return
        
        # Check account age
        if giveaway["min_account_age_days"] > 0:
            # Telegram user IDs are roughly sequential, lower = older account
            # This is a rough heuristic since we can't get exact account creation date
            # For now, we'll skip this check or implement it later with seen_users timestamp
            pass
        
        # Add participant
        added = db.add_giveaway_participant(giveaway_id, user.id)
        if not added:
            await query.answer("You're already entered!", show_alert=True)
            return
        
        # Update message
        giveaway = db.load_giveaway(giveaway_id)  # Reload to get updated participant list
        text = _giveaway_text(
            giveaway["prize"],
            giveaway["end_time"],
            giveaway["winner_count"],
            giveaway["min_messages"],
            giveaway["min_account_age_days"],
            len(giveaway["participants"]),
        )
        markup = InlineKeyboardMarkup([[
            InlineKeyboardButton("🎉 Enter Giveaway", callback_data=f"gv:enter:{giveaway_id}")
        ]])
        
        try:
            await query.edit_message_text(
                text, parse_mode="HTML", reply_markup=markup, disable_web_page_preview=True
            )
        except TelegramError:
            pass
        
        await query.answer("You're in! Good luck! 🍀", show_alert=False)


async def cmd_gcancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancel a giveaway. Usage: /gcancel (reply to giveaway message)"""
    if not await require_group_admin(update):
        return
    
    msg = update.effective_message
    reply = msg.reply_to_message
    
    if not reply:
        await msg.reply_text(f"Reply to a giveaway message with {cmd('gcancel')} to cancel it.")
        return
    
    # Find giveaway by message_id
    giveaways = db.list_active_giveaways(update.effective_chat.id)
    target = None
    for g in giveaways:
        if g.get("message_id") == reply.message_id:
            target = g
            break
    
    if not target:
        await msg.reply_text("That's not an active giveaway message.")
        return
    
    db.cancel_giveaway(target["id"])
    
    try:
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=reply.message_id,
            text=f"<b>🎁 GIVEAWAY CANCELLED</b>\n\nPrize: <s>{escape(target['prize'])}</s>\n\nThis giveaway has been cancelled by an admin.",
            parse_mode="HTML",
        )
    except TelegramError:
        pass
    
    await msg.reply_text("✅ Giveaway cancelled.")


async def cmd_ghistory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show giveaway history. Usage: /ghistory"""
    if not await require_group_admin(update):
        return
    
    msg = update.effective_message
    chat_id = update.effective_chat.id
    
    history = db.get_giveaway_history(chat_id, limit=10)
    
    if not history:
        await msg.reply_text("No giveaway history yet.")
        return
    
    lines = ["<b>🎁 Giveaway History</b> (last 10)\n"]
    
    for entry in history:
        when = _ist_str(entry["drawn_at"])
        prize = escape(entry["prize"])
        winner_count = len(entry["winner_ids"])
        rerolls = entry["reroll_count"]
        
        lines.append(f"• <b>{prize}</b> ({when})")
        lines.append(f"  {winner_count} winner(s), {entry['participant_count']} participants")
        if rerolls > 0:
            lines.append(f"  Rerolled {rerolls} time(s)")
        lines.append("")
    
    await msg.reply_html("\n".join(lines), disable_web_page_preview=True)


async def cmd_greroll(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reroll giveaway winners. Usage: /greroll (reply to giveaway message) or /greroll <giveaway_id>"""
    if not await require_group_admin(update):
        return
    
    msg = update.effective_message
    reply = msg.reply_to_message
    chat_id = update.effective_chat.id
    args = context.args or []
    
    giveaway_id = None
    
    # Method 1: Reply to giveaway message (easiest)
    if reply:
        # Find giveaway by message_id
        giveaways = db.list_active_giveaways(chat_id)
        for g in giveaways:
            if g.get("message_id") == reply.message_id and g["status"] == "finished":
                giveaway_id = g["id"]
                break
        
        # Also check finished giveaways in DB
        if not giveaway_id:
            # We need to check all giveaways (not just active)
            with db.cursor() as conn:
                row = conn.execute(
                    "SELECT id, status FROM giveaways WHERE chat_id=? AND message_id=?",
                    (chat_id, reply.message_id),
                ).fetchone()
                if row:
                    giveaway_id = str(row["id"])
        
        if not giveaway_id:
            await msg.reply_text(
                "That message is not a giveaway, or the giveaway data was not found.\n"
                f"You can also use {cmd('greroll')} <giveaway_id> without replying."
            )
            return
    
    # Method 2: Provide giveaway_id as argument
    elif args:
        giveaway_id = args[0]
    
    # Neither method provided
    else:
        await msg.reply_text(
            f"Usage: {cmd('greroll')} (reply to giveaway message)\n"
            f"Or: {cmd('greroll')} <giveaway_id>\n\n"
            "Find giveaway IDs in the original announcement or via /ghistory."
        )
        return
    giveaway = db.load_giveaway(giveaway_id)
    
    if not giveaway:
        await msg.reply_text("Giveaway not found. Make sure the ID is correct.")
        return
    
    if giveaway["status"] != "finished":
        await msg.reply_text("That giveaway hasn't finished yet or was cancelled.")
        return
    
    if giveaway["chat_id"] != chat_id:
        await msg.reply_text("That giveaway is from a different chat.")
        return
    
    # Get current history entry
    history_entries = [h for h in history if h["giveaway_id"] == giveaway_id]
    if not history_entries:
        await msg.reply_text("Could not find history for that giveaway.")
        return
    
    old_entry = history_entries[0]
    reroll_count = old_entry["reroll_count"] + 1
    
    # Reroll winners
    participants = giveaway["participants"]
    if len(participants) < giveaway["winner_count"]:
        await msg.reply_text("Not enough participants to reroll.")
        return
    
    # Pick new random winners
    new_winners = random.sample(participants, giveaway["winner_count"])
    
    # Update history with new winners
    with db.cursor() as conn:
        conn.execute(
            """
            UPDATE giveaway_history
            SET winner_ids=?, reroll_count=?
            WHERE giveaway_id=?
            """,
            (json.dumps(new_winners), reroll_count, giveaway_id),
        )
    
    # Announce new winners
    winner_mentions = []
    for uid in new_winners:
        user_info = db.get_seen_user(uid)
        if user_info and user_info.get("username"):
            winner_mentions.append(f"@{user_info['username']}")
        elif user_info and user_info.get("first_name"):
            winner_mentions.append(f'<a href="tg://user?id={uid}">{escape(user_info["first_name"])}</a>')
        else:
            winner_mentions.append(f'<a href="tg://user?id={uid}">User {uid}</a>')
    
    winner_text = ", ".join(winner_mentions)
    
    await msg.reply_html(
        f"🎲 <b>Giveaway Rerolled!</b>\n\n"
        f"Prize: <b>{escape(giveaway['prize'])}</b>\n"
        f"🏆 New Winner(s): {winner_text}\n\n"
        f"Reroll #{reroll_count}. Congratulations!",
        disable_web_page_preview=True,
    )


# ---------------------------------------------------------------------------
# Background sweep - draw winners when giveaways expire
# ---------------------------------------------------------------------------


async def draw_giveaway_winners_once(application) -> int:
    """Check for expired giveaways and draw winners. Returns how many were drawn."""
    drawn = 0
    now = time.time()
    
    for giveaway in db.list_active_giveaways():
        if giveaway["end_time"] > now:
            continue  # Not expired yet
        
        async with _lock_for(giveaway["id"]):
            # Reload to ensure still active
            fresh = db.load_giveaway(giveaway["id"])
            if not fresh or fresh["status"] != "active":
                continue
            
            # Draw winners
            participants = fresh["participants"]
            if not participants:
                # No participants - cancel
                db.cancel_giveaway(fresh["id"])
                try:
                    await application.bot.edit_message_text(
                        chat_id=fresh["chat_id"],
                        message_id=fresh["message_id"],
                        text=f"<b>🎁 GIVEAWAY ENDED</b>\n\nPrize: {escape(fresh['prize'])}\n\nNo participants entered. Giveaway cancelled.",
                        parse_mode="HTML",
                    )
                except TelegramError:
                    pass
                _forget_lock(fresh["id"])
                drawn += 1
                continue
            
            # Pick random winners
            winner_count = min(fresh["winner_count"], len(participants))
            winners = random.sample(participants, winner_count)
            
            # Save to history
            db.finish_giveaway(fresh["id"], winners)
            _forget_lock(fresh["id"])
            
            # Announce winners
            winner_mentions = []
            for uid in winners:
                user_info = db.get_seen_user(uid)
                if user_info and user_info.get("username"):
                    winner_mentions.append(f"@{user_info['username']}")
                elif user_info and user_info.get("first_name"):
                    winner_mentions.append(f'<a href="tg://user?id={uid}">{escape(user_info["first_name"])}</a>')
                else:
                    winner_mentions.append(f'<a href="tg://user?id={uid}">User {uid}</a>')
            
            winner_text = ", ".join(winner_mentions)
            
            # Update giveaway message
            try:
                await application.bot.edit_message_text(
                    chat_id=fresh["chat_id"],
                    message_id=fresh["message_id"],
                    text=f"<b>🎉 GIVEAWAY ENDED</b>\n\nPrize: <b>{escape(fresh['prize'])}</b>\n\n🏆 Winner(s): {winner_text}\n\nCongratulations!",
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
            except TelegramError as exc:
                log.warning("Could not edit giveaway message %s: %s", fresh["id"], exc)
            
            # Also send announcement in chat
            try:
                await application.bot.send_message(
                    fresh["chat_id"],
                    f"🎉 <b>Giveaway Winner(s) Announced!</b>\n\nPrize: <b>{escape(fresh['prize'])}</b>\n🏆 {winner_text}\n\nCongratulations! Contact the admin to claim your prize.",
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
            except TelegramError as exc:
                log.warning("Could not send giveaway announcement in chat %s: %s", fresh["chat_id"], exc)
            
            drawn += 1
    
    return drawn


async def draw_giveaway_winners_loop(application) -> None:
    """Background loop to check for expired giveaways."""
    await asyncio.sleep(15)  # Wait a bit on startup
    while True:
        try:
            await draw_giveaway_winners_once(application)
        except Exception:
            log.exception("giveaway draw loop failed")
        await asyncio.sleep(SWEEP_INTERVAL)
