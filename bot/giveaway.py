"""/giveaway command system - create, manage, and draw giveaways."""
from __future__ import annotations

import asyncio
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
from bot.moderation import is_group_admin, require_group_admin

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


def _mention_user(uid: int) -> str:
    user_info = db.lookup_seen_id(uid)
    if user_info and user_info.get("username"):
        return f"@{escape(str(user_info['username']))}"
    if user_info and user_info.get("first_name"):
        return f'<a href="tg://user?id={uid}">{escape(str(user_info["first_name"]))}</a>'
    return f'<a href="tg://user?id={uid}">User {uid}</a>'


def _winner_mentions(winner_ids: list[int]) -> str:
    return ", ".join(_mention_user(uid) for uid in winner_ids)


def _ended_text(prize: str, winner_ids: list[int], *, giveaway_id: str | None = None) -> str:
    if not winner_ids:
        text = (
            f"<b>🎁 GIVEAWAY ENDED</b>\n\n"
            f"Prize: {escape(prize)}\n\n"
            "No participants entered. Giveaway cancelled."
        )
    else:
        text = (
            f"<b>🎉 GIVEAWAY ENDED</b>\n\n"
            f"Prize: <b>{escape(prize)}</b>\n\n"
            f"🏆 Winner(s): {_winner_mentions(winner_ids)}\n\n"
            "Winners: tap <b>Claim Prize</b> on the announcement below.\n"
            "Admins: confirm after you hand it over, or reroll if they stay inactive."
        )
    if giveaway_id:
        text += f"\n\n<code>id: {escape(giveaway_id)}</code>"
    return text


def _winner_status_line(uid: int, claimed: set[int], confirmed: set[int]) -> str:
    name = _mention_user(uid)
    if uid in confirmed:
        return f"• {name} — 🎉 confirmed (prize given)"
    if uid in claimed:
        return f"• {name} — ✅ claimed (waiting admin confirm)"
    return f"• {name} — ⏳ waiting to claim"


def _claim_board_text(giveaway: dict, *, reroll_count: int = 0) -> str:
    winners = giveaway.get("winner_ids") or []
    claimed = set(giveaway.get("claimed_ids") or [])
    confirmed = set(giveaway.get("confirmed_ids") or [])
    lines = [
        "<b>🎉 Giveaway Winner(s)</b>",
        f"Prize: <b>{escape(giveaway['prize'])}</b>",
        "",
        "<b>Status:</b>",
    ]
    for uid in winners:
        lines.append(_winner_status_line(uid, claimed, confirmed))
    lines.append("")
    if winners and all(uid in confirmed for uid in winners):
        lines.append("All prizes confirmed. Nice.")
    elif winners and all(uid in claimed or uid in confirmed for uid in winners):
        lines.append("All winners claimed. Admins: tap <b>Confirm</b> after giving the prize.")
        lines.append("Reroll is locked while everyone has claimed.")
    else:
        lines.append("Winners: tap <b>Claim Prize</b> so admins know you're active.")
        lines.append("Admins: <b>Confirm</b> after giving the prize, or <b>Reroll unclaimed</b> only for people who never claim.")
    if reroll_count > 0:
        lines.append(f"\n<i>Rerolled {reroll_count} time(s)</i>")
    lines.append(f"\n<code>id: {escape(giveaway['id'])}</code>")
    return "\n".join(lines)


def _claim_markup(giveaway: dict) -> InlineKeyboardMarkup:
    winners = giveaway.get("winner_ids") or []
    claimed = set(giveaway.get("claimed_ids") or [])
    confirmed = set(giveaway.get("confirmed_ids") or [])
    gid = giveaway["id"]
    rows: list[list[InlineKeyboardButton]] = []

    if winners and not all(uid in confirmed for uid in winners):
        still_need_claim = [uid for uid in winners if uid not in claimed and uid not in confirmed]
        if still_need_claim:
            rows.append([InlineKeyboardButton("🎁 Claim Prize", callback_data=f"gv:claim:{gid}")])
        confirm_row: list[InlineKeyboardButton] = []
        for uid in winners:
            if uid in confirmed:
                continue
            label = f"✅ Confirm {uid}"
            info = db.lookup_seen_id(uid)
            if info and info.get("first_name"):
                short = str(info["first_name"])[:12]
                label = f"✅ {short}"
            confirm_row.append(
                InlineKeyboardButton(label, callback_data=f"gv:confirm:{gid}:{uid}")
            )
            if len(confirm_row) == 2:
                rows.append(confirm_row)
                confirm_row = []
        if confirm_row:
            rows.append(confirm_row)
        # Only allow reroll when someone still has not claimed
        if still_need_claim:
            rows.append(
                [InlineKeyboardButton("🎲 Reroll unclaimed", callback_data=f"gv:reroll:{gid}")]
            )
    return InlineKeyboardMarkup(rows)


_NO_BUTTONS = InlineKeyboardMarkup([])


async def _edit_giveaway_message(bot, chat_id: int, message_id: int | None, text: str, reply_markup=None) -> None:
    if not message_id:
        return
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode="HTML",
            reply_markup=_NO_BUTTONS if reply_markup is None else reply_markup,
            disable_web_page_preview=True,
        )
    except TelegramError as exc:
        log.warning("Could not edit giveaway message chat=%s msg=%s: %s", chat_id, message_id, exc)


async def _refresh_claim_message(bot, giveaway: dict, *, reroll_count: int = 0) -> None:
    text = _claim_board_text(giveaway, reroll_count=reroll_count)
    markup = _claim_markup(giveaway)
    mid = giveaway.get("claim_message_id")
    if mid:
        try:
            await bot.edit_message_text(
                chat_id=giveaway["chat_id"],
                message_id=mid,
                text=text,
                parse_mode="HTML",
                reply_markup=markup,
                disable_web_page_preview=True,
            )
            return
        except TelegramError as exc:
            log.warning("Could not refresh claim message: %s", exc)
    try:
        sent = await bot.send_message(
            giveaway["chat_id"],
            text,
            parse_mode="HTML",
            reply_markup=markup,
            disable_web_page_preview=True,
        )
        db.set_claim_message(giveaway["id"], sent.message_id)
    except TelegramError as exc:
        log.warning("Could not send claim board: %s", exc)


async def _perform_reroll(bot, giveaway: dict) -> tuple[bool, str]:
    """Reroll winners who never claimed. Claimed/confirmed winners are kept."""
    participants = [int(x) for x in (giveaway.get("participants") or [])]
    winners = [int(x) for x in (giveaway.get("winner_ids") or [])]
    claimed = set(giveaway.get("claimed_ids") or [])
    confirmed = set(giveaway.get("confirmed_ids") or [])
    # Anyone who claimed OR was confirmed stays — "Reroll unclaimed" means only silent winners
    keep = [uid for uid in winners if uid in claimed or uid in confirmed]
    dropped = [uid for uid in winners if uid not in keep]
    need = max(0, giveaway["winner_count"] - len(keep))
    if need == 0:
        return False, (
            "Every winner has already claimed (or been confirmed). "
            "Nothing to reroll — use Confirm after you give the prize."
        )
    # Prefer people who were not just dropped for inactivity
    pool = [uid for uid in participants if uid not in keep and uid not in dropped]
    if len(pool) < need:
        pool = [uid for uid in participants if uid not in keep]
    if len(pool) < need:
        return False, "Not enough other participants left to reroll."
    new_extra = random.sample(pool, need)
    new_winners = keep + new_extra
    # Avoid identical full set when possible
    if set(new_winners) == set(winners):
        alt = [uid for uid in pool if uid not in new_extra]
        if alt:
            new_extra = new_extra[:-1] + [random.choice(alt)]
            new_winners = keep + new_extra

    reroll_count = db.reroll_giveaway_winners(giveaway["id"], new_winners)
    if not reroll_count:
        return False, "Could not reroll that giveaway."
    fresh = db.load_giveaway(giveaway["id"])
    if not fresh:
        return False, "Giveaway disappeared after reroll."
    await _edit_giveaway_message(
        bot,
        fresh["chat_id"],
        fresh.get("message_id"),
        _ended_text(fresh["prize"], fresh["winner_ids"], giveaway_id=fresh["id"]),
    )
    await _refresh_claim_message(bot, fresh, reroll_count=reroll_count)

    kept_text = _winner_mentions(keep) if keep else "none"
    new_text = _winner_mentions(new_extra) if new_extra else "none"
    claim_note = (
        "New winners must claim. Claimed winners were kept."
        if keep
        else "New winners must claim."
    )
    return True, (
        f"🎲 <b>Giveaway Rerolled!</b>\n\n"
        f"Prize: <b>{escape(fresh['prize'])}</b>\n"
        f"Kept (already claimed): {kept_text}\n"
        f"New winner(s): {new_text}\n\n"
        f"Reroll #{reroll_count}. {claim_note}"
    )


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
    """Handle Enter / Claim / Confirm / Reroll button clicks."""
    query = update.callback_query
    if not query or not query.data:
        return

    parts = query.data.split(":")
    if len(parts) < 3 or parts[0] != "gv":
        await query.answer()
        return

    action = parts[1]
    giveaway_id = parts[2]
    user = update.effective_user
    if not user:
        await query.answer("Something went wrong.", show_alert=True)
        return

    if action == "enter":
        await _handle_enter(query, context, giveaway_id, user)
        return
    if action == "claim":
        await _handle_claim(query, context, giveaway_id, user)
        return
    if action == "confirm" and len(parts) >= 4:
        await _handle_confirm(update, query, context, giveaway_id, user, parts[3])
        return
    if action == "reroll":
        await _handle_reroll_button(update, query, context, giveaway_id, user)
        return
    await query.answer()


async def _handle_enter(query, context, giveaway_id: str, user) -> None:
    async with _lock_for(giveaway_id):
        giveaway = db.load_giveaway(giveaway_id)

        if not giveaway:
            await query.answer("This giveaway no longer exists.", show_alert=True)
            return

        if giveaway["status"] != "active":
            await query.answer("This giveaway has ended.", show_alert=True)
            winners = giveaway.get("winner_ids") or []
            await _edit_giveaway_message(
                context.bot,
                giveaway["chat_id"],
                giveaway.get("message_id") or (query.message.message_id if query.message else None),
                _ended_text(giveaway["prize"], winners, giveaway_id=giveaway_id),
            )
            return

        chat_id = giveaway["chat_id"]
        if giveaway["min_messages"] > 0:
            activity = db.get_user_activity(chat_id, user.id)
            total = activity.get("monthly", 0)
            if total < giveaway["min_messages"]:
                await query.answer(
                    f"You need at least {giveaway['min_messages']} messages to enter. You have {total}.",
                    show_alert=True,
                )
                return

        added = db.add_giveaway_participant(giveaway_id, user.id)
        if not added:
            await query.answer("You're already entered!", show_alert=True)
            return

        giveaway = db.load_giveaway(giveaway_id)
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


async def _handle_claim(query, context, giveaway_id: str, user) -> None:
    async with _lock_for(giveaway_id):
        result = db.claim_giveaway(giveaway_id, user.id)
        if result == "missing":
            await query.answer("Giveaway not found.", show_alert=True)
            return
        if result == "bad_status":
            await query.answer("This giveaway is not awaiting claims.", show_alert=True)
            return
        if result == "not_winner":
            await query.answer("Only winners can claim.", show_alert=True)
            return
        if result == "already":
            await query.answer("You already claimed (or were confirmed).", show_alert=True)
            return
        fresh = db.load_giveaway(giveaway_id)
        if fresh:
            await _refresh_claim_message(context.bot, fresh)
        await query.answer("Claimed! An admin can confirm once they give you the prize.", show_alert=True)


async def _handle_confirm(update: Update, query, context, giveaway_id: str, user, target_raw: str) -> None:
    if not await is_group_admin(update, user.id):
        await query.answer("Only admins can confirm winners.", show_alert=True)
        return
    try:
        target_id = int(target_raw)
    except ValueError:
        await query.answer("Bad winner id.", show_alert=True)
        return
    async with _lock_for(giveaway_id):
        result = db.confirm_giveaway_winner(giveaway_id, target_id)
        if result == "missing":
            await query.answer("Giveaway not found.", show_alert=True)
            return
        if result == "bad_status":
            await query.answer("This giveaway is not awaiting confirms.", show_alert=True)
            return
        if result == "not_winner":
            await query.answer("That user is not a winner.", show_alert=True)
            return
        if result == "already":
            await query.answer("Already confirmed.", show_alert=True)
            return
        fresh = db.load_giveaway(giveaway_id)
        if fresh:
            await _refresh_claim_message(context.bot, fresh)
        await query.answer("Confirmed. Prize marked as given.", show_alert=True)


async def _handle_reroll_button(update: Update, query, context, giveaway_id: str, user) -> None:
    if not await is_group_admin(update, user.id):
        await query.answer("Only admins can reroll.", show_alert=True)
        return
    async with _lock_for(giveaway_id):
        giveaway = db.load_giveaway(giveaway_id)
        if not giveaway or giveaway["status"] != "finished":
            await query.answer("Nothing to reroll.", show_alert=True)
            return
        ok, text = await _perform_reroll(context.bot, giveaway)
        if not ok:
            await query.answer(text, show_alert=True)
            return
        await query.answer("Rerolled.", show_alert=False)
        try:
            await context.bot.send_message(
                giveaway["chat_id"],
                text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except TelegramError:
            pass


async def cmd_gcancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancel a giveaway. Usage: /gcancel (reply to giveaway message)"""
    if not await require_group_admin(update):
        return
    
    msg = update.effective_message
    reply = msg.reply_to_message
    
    if not reply:
        await msg.reply_text(f"Reply to a giveaway message with {cmd('gcancel')} to cancel it.")
        return
    
    target = db.find_giveaway_by_message(update.effective_chat.id, reply.message_id)
    if not target or target["status"] != "active":
        await msg.reply_text("That's not an active giveaway message.")
        return
    
    db.cancel_giveaway(target["id"])
    
    await _edit_giveaway_message(
        context.bot,
        update.effective_chat.id,
        reply.message_id,
        f"<b>🎁 GIVEAWAY CANCELLED</b>\n\nPrize: <s>{escape(target['prize'])}</s>\n\nThis giveaway has been cancelled by an admin.",
    )
    await msg.reply_text("Giveaway cancelled.")
    _forget_lock(target["id"])


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
        winners = entry.get("winner_ids") or []
        winner_count = len(winners)
        rerolls = entry["reroll_count"]
        gid = escape(entry["giveaway_id"])
        
        lines.append(f"• <b>{prize}</b> ({when})")
        lines.append(f"  <code>id: {gid}</code>")
        lines.append(f"  {winner_count} winner(s), {entry['participant_count']} participants")
        if winners:
            lines.append(f"  🏆 {_winner_mentions(winners)}")
        live = db.load_giveaway(entry["giveaway_id"])
        if live and live.get("winner_ids"):
            claimed = set(live.get("claimed_ids") or [])
            confirmed = set(live.get("confirmed_ids") or [])
            pending = [u for u in live["winner_ids"] if u not in confirmed]
            if not pending:
                lines.append("  Status: all confirmed")
            else:
                lines.append(
                    f"  Status: {len(claimed)} claimed, {len(confirmed)} confirmed, "
                    f"{len(pending)} still open"
                )
        if rerolls > 0:
            lines.append(f"  Rerolled {rerolls} time(s)")
        lines.append("")
    
    await msg.reply_html("\n".join(lines), disable_web_page_preview=True)


async def cmd_greroll(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reroll giveaway winners. Usage: /greroll (reply) or /greroll <giveaway_id>"""
    if not await require_group_admin(update):
        return
    
    msg = update.effective_message
    reply = msg.reply_to_message
    chat_id = update.effective_chat.id
    args = context.args or []
    
    giveaway = None
    
    if reply:
        giveaway = db.find_giveaway_by_message(chat_id, reply.message_id)
        if not giveaway:
            await msg.reply_text(
                "That message is not a giveaway (or its winner board).\n"
                f"Reply to the original giveaway / winner announcement, or use "
                f"{cmd('greroll')} <giveaway_id> from {cmd('ghistory')}."
            )
            return
    elif args:
        giveaway = db.load_giveaway(args[0].strip())
        if not giveaway:
            await msg.reply_text("Giveaway not found. Check the id in /ghistory.")
            return
    else:
        await msg.reply_text(
            f"Usage: reply to the giveaway/winner message with {cmd('greroll')}\n"
            f"Or: {cmd('greroll')} <giveaway_id>\n\n"
            f"Tip: after a draw, use the <b>Reroll unclaimed</b> button on the winner board."
        )
        return
    
    if giveaway["chat_id"] != chat_id:
        await msg.reply_text("That giveaway is from a different chat.")
        return
    
    if giveaway["status"] != "finished":
        await msg.reply_text("That giveaway hasn't finished yet or was cancelled.")
        return
    
    async with _lock_for(giveaway["id"]):
        fresh = db.load_giveaway(giveaway["id"])
        if not fresh or fresh["status"] != "finished":
            await msg.reply_text("That giveaway hasn't finished yet or was cancelled.")
            return
        ok, text = await _perform_reroll(context.bot, fresh)
    
    if not ok:
        await msg.reply_text(text)
        return
    await msg.reply_html(text, disable_web_page_preview=True)


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
            
            participants = list(fresh["participants"] or [])
            winners: list[int] = []
            
            try:
                if not participants:
                    db.cancel_giveaway(fresh["id"])
                    await _edit_giveaway_message(
                        application.bot,
                        fresh["chat_id"],
                        fresh.get("message_id"),
                        _ended_text(fresh["prize"], []),
                    )
                    drawn += 1
                    continue
                
                winner_count = min(fresh["winner_count"], len(participants))
                winners = random.sample(participants, winner_count)
                db.finish_giveaway(fresh["id"], winners)
                finished = db.load_giveaway(fresh["id"]) or fresh
                
                await _edit_giveaway_message(
                    application.bot,
                    finished["chat_id"],
                    finished.get("message_id"),
                    _ended_text(finished["prize"], winners, giveaway_id=finished["id"]),
                )
                await _refresh_claim_message(application.bot, finished)
                
                drawn += 1
            except Exception:
                log.exception("Failed drawing giveaway %s", fresh["id"])
                # If we already finished it in DB, still try to refresh the UI
                current = db.load_giveaway(fresh["id"])
                if current and current["status"] in {"finished", "cancelled"}:
                    await _edit_giveaway_message(
                        application.bot,
                        current["chat_id"],
                        current.get("message_id"),
                        _ended_text(
                            current["prize"],
                            current.get("winner_ids") or winners,
                            giveaway_id=current["id"],
                        ),
                    )
                    if current["status"] == "finished" and (current.get("winner_ids") or winners):
                        if not current.get("winner_ids") and winners:
                            # ensure winners persisted for claim UI
                            pass
                        await _refresh_claim_message(application.bot, current)
            finally:
                _forget_lock(fresh["id"])
    
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
