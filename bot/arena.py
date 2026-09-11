"""Telegram wiring for the arena games (cricket, rock-paper-scissors).

Design rules learned from the last version of this feature (removed for
being buggy):
  - The database is the ONLY source of truth for match state. No in-memory
    challenge cache — that dual-state setup is what caused "this match
    expired" bugs before. Every read is a fresh `db.load_arena_match`.
  - Text + buttons only, one message edited in place. No images — that was
    the source of message-spam complaints before.
  - Names are snapshotted into the payload at match creation, not looked up
    live on every render. Fewer network calls, nothing breaks if someone
    leaves the group mid-match.
  - Every state change goes through a per-match asyncio.Lock so two fast
    taps on the same match can't race each other.
  - Idle matches are actively swept and closed (not just lazily ignored) so
    nothing can sit "running" for days.
"""
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

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, User
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from bot import arena_logic as logic
from bot import db
from bot.commands import cmd
from bot.fun import resolve_member
from bot.moderation import require_group

log = logging.getLogger(__name__)

ACCEPT_TTL = 60           # pending challenge must be accepted within 1 min
IDLE_TTL = 5 * 60         # a live match with no move for 5 min auto-closes
MAX_CONCURRENT = 3        # per chat, per kind
RESULT_RETENTION_DAYS = 3
SWEEP_INTERVAL = 30
PURGE_INTERVAL = 3 * 3600

KIND_CRICKET = "cricket"
KIND_RPS = "rps"
_KIND_CODE = {KIND_CRICKET: "cr", KIND_RPS: "rp"}
_CODE_KIND = {v: k for k, v in _KIND_CODE.items()}
KIND_LABEL = {KIND_CRICKET: "hand cricket", KIND_RPS: "rock-paper-scissors"}

_locks: dict[str, asyncio.Lock] = {}
_start_locks: dict[tuple[int, str], asyncio.Lock] = {}


def _lock_for(match_id: str) -> asyncio.Lock:
    lock = _locks.get(match_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[match_id] = lock
    return lock


def _start_lock_for(chat_id: int, kind: str) -> asyncio.Lock:
    key = (chat_id, kind)
    lock = _start_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _start_locks[key] = lock
    return lock


def _forget_lock(match_id: str) -> None:
    _locks.pop(match_id, None)


def _now() -> float:
    return time.time()


def ist_str(ts: float) -> str:
    dt = datetime.fromtimestamp(ts, tz=_IST) if _IST else (
        datetime.utcfromtimestamp(ts) + timedelta(hours=5, minutes=30)
    )
    return dt.strftime("%d %b, %I:%M %p")


def _clean_name(user: User) -> str:
    name = (user.first_name or user.full_name or str(user.id)).strip()
    name = " ".join(name.split())
    return name[:20] if name else str(user.id)


def _new_match_id() -> str:
    return secrets.token_hex(4)


# ---------------------------------------------------------------------------
# Challenge flow — shared by /cricket and /rps
# ---------------------------------------------------------------------------


async def _start_challenge(update: Update, context: ContextTypes.DEFAULT_TYPE, kind: str) -> None:
    if not await require_group(update):
        return
    msg = update.effective_message
    challenger = update.effective_user
    rival = await resolve_member(update, context)
    if not challenger:
        return
    if not rival:
        await msg.reply_text(
            "Can't find that user. Try this:\n"
            "• Reply to their message, or\n"
            f"• Type {cmd(kind)} @ and select their name from Telegram's suggestion list\n\n"
            "If they've never sent a message here, ask them to send any message first."
        )
        return
    if rival.id == challenger.id:
        await msg.reply_text("Challenge someone else — not yourself.")
        return
    if rival.is_bot:
        await msg.reply_text("Bots don't play. Challenge a human.")
        return

    chat_id = update.effective_chat.id
    match_id = _new_match_id()
    a_name, b_name = _clean_name(challenger), _clean_name(rival)
    if kind == KIND_CRICKET:
        payload = logic.cricket_new(challenger.id, rival.id, batter=challenger.id)
    else:
        payload = logic.rps_new(challenger.id, rival.id)
    payload["a_name"] = a_name
    payload["b_name"] = b_name

    # Hold a per-(chat, kind) lock across the check-then-create so two
    # /cricket or /rps calls fired at the same instant can't both slip past
    # the cap and push the count above MAX_CONCURRENT.
    async with _start_lock_for(chat_id, kind):
        active = db.count_active_arena_matches(chat_id, kind)
        if active >= MAX_CONCURRENT:
            await msg.reply_text(
                f"There are already {MAX_CONCURRENT} {KIND_LABEL[kind]} matches running in "
                f"this chat. Wait for one to finish before starting another."
            )
            return
        # One person = one match of this game at a time (pending/selecting/live)
        if db.user_in_active_arena_match(chat_id, kind, challenger.id):
            await msg.reply_text(
                f"You're already in a {KIND_LABEL[kind]} match. "
                "Finish or let it expire before starting another."
            )
            return
        if db.user_in_active_arena_match(chat_id, kind, rival.id):
            await msg.reply_text(
                f"{_clean_name(rival)} is already in a {KIND_LABEL[kind]} match. "
                "Wait until that one ends."
            )
            return
        db.create_arena_match(
            match_id, chat_id, kind, challenger.id, rival.id, payload,
            deadline=_now() + ACCEPT_TTL,
        )

    code = _KIND_CODE[kind]
    text = (
        f"<b>{escape(KIND_LABEL[kind].title())} challenge</b>\n"
        f"{_mention(challenger.id, a_name)} challenged {_mention(rival.id, b_name)}.\n"
        + (_cricket_rules_line() if kind == KIND_CRICKET else _rps_rules_line())
        + f"\n{_mention(rival.id, b_name)} — Accept or decline within 1 minute."
    )
    markup = InlineKeyboardMarkup([[
        InlineKeyboardButton("Accept", callback_data=f"g:{code}:ok:{match_id}"),
        InlineKeyboardButton("Decline", callback_data=f"g:{code}:no:{match_id}"),
    ]])
    sent = await msg.reply_html(text, reply_markup=markup, disable_web_page_preview=True)
    db.set_arena_match_message(match_id, sent.message_id)


def _mention(user_id: int, name: str) -> str:
    return f'<a href="tg://user?id={user_id}">{escape(name)}</a>'


def _cricket_rules_line() -> str:
    return (
        "After accept, the challenger picks 1–3 overs. Both pick 1-6 every ball — "
        "matching numbers means the batter is out. Highest total wins."
    )


def _rps_rules_line() -> str:
    return "One round. Rock beats scissors, scissors beats paper, paper beats rock."


async def cmd_cricket(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start a cricket challenge — accept first, then overs."""
    await _start_challenge(update, context, KIND_CRICKET)


async def cmd_rps(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _start_challenge(update, context, KIND_RPS)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _cricket_keyboard(match_id: str) -> InlineKeyboardMarkup:
    row1 = [InlineKeyboardButton(str(n), callback_data=f"g:cr:p:{match_id}:{n}") for n in range(1, 4)]
    row2 = [InlineKeyboardButton(str(n), callback_data=f"g:cr:p:{match_id}:{n}") for n in range(4, 7)]
    return InlineKeyboardMarkup([row1, row2])


def _rps_keyboard(match_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("Rock", callback_data=f"g:rp:p:{match_id}:r"),
        InlineKeyboardButton("Paper", callback_data=f"g:rp:p:{match_id}:p"),
        InlineKeyboardButton("Scissors", callback_data=f"g:rp:p:{match_id}:s"),
    ]])


def _cricket_text(payload: dict) -> str:
    a_name, b_name = payload["a_name"], payload["b_name"]
    a_id, b_id = payload["a"], payload["b"]
    batter_id = payload["batter"]
    bowler_id = b_id if batter_id == a_id else a_id
    batter_name = a_name if batter_id == a_id else b_name
    bowler_name = b_name if batter_id == a_id else a_name
    innings = payload.get("innings", 1)
    balls = int(payload.get("balls") or 0)
    max_balls = int(payload.get("max_balls") or 6)
    overs = int(payload.get("overs") or 1)
    sa, sb = int(payload.get("score_a") or 0), int(payload.get("score_b") or 0)
    
    # Header
    lines = [f"<b>🏏 Hand Cricket ({overs} over{'s' if overs > 1 else ''})</b>"]
    
    # Current batting info with table
    lines.append("")
    lines.append(f"<b>━━━ Innings {innings} ━━━</b>")
    lines.append(f"<b>🏏 Batting:</b> {_mention(batter_id, batter_name)}")
    lines.append(f"<b>⚾ Bowling:</b> {_mention(bowler_id, bowler_name)}")
    lines.append(f"<b>Ball:</b> {balls}/{max_balls}")
    lines.append("")
    
    # Scoreboard table
    lines.append("<b>━━━ SCOREBOARD ━━━</b>")
    lines.append(f"{escape(a_name)}: <code>{sa:>3}</code>")
    lines.append(f"{escape(b_name)}: <code>{sb:>3}</code>")
    
    # Target info for innings 2
    if innings == 2 and payload.get("first_innings") is not None:
        target = int(payload["first_innings"]) + 1
        chasing_score = sb if batter_id == b_id else sa
        need = max(0, target - chasing_score)
        lines.append(f"\n<b>🎯 Target:</b> {target}")
        lines.append(f"<b>Need:</b> {need} runs to win")
    
    # Last ball result
    last = payload.get("last")
    if last:
        lines.append(f"\n<b>Last ball:</b> {escape(str(last))}")
    
    # Pick status
    picks = payload.get("picks") or {}
    lines.append("")
    if picks and len(picks) == 1:
        locked_id = int(list(picks.keys())[0])
        locked_name = a_name if locked_id == a_id else b_name
        waiting_id = b_id if locked_id == a_id else a_id
        waiting_name = b_name if locked_id == a_id else a_name
        lines.append(f"✅ {escape(locked_name)} locked in")
        lines.append(f"⏳ Waiting for {_mention(waiting_id, waiting_name)}...")
    else:
        lines.append("⚡ <b>Both pick 1–6 now!</b>")
    
    return "\n".join(lines)


def _cricket_finished_text(payload: dict, winner_id: int | None) -> str:
    a_name, b_name = payload["a_name"], payload["b_name"]
    a_id, b_id = payload["a"], payload["b"]
    sa, sb = int(payload.get("score_a") or 0), int(payload.get("score_b") or 0)
    overs = int(payload.get("overs") or 1)
    
    lines = [f"<b>🏏 Hand Cricket - MATCH FINISHED!</b>"]
    lines.append("")
    lines.append("<b>━━━ FINAL SCORE ━━━</b>")
    lines.append(f"{escape(a_name)}: <code>{sa:>3}</code>")
    lines.append(f"{escape(b_name)}: <code>{sb:>3}</code>")
    lines.append("")
    
    if winner_id is None:
        lines.append("🤝 <b>Match Tied!</b>")
    else:
        winner_name = a_name if winner_id == a_id else b_name
        margin = abs(sa - sb)
        lines.append(f"🏆 <b>Winner: {_mention(winner_id, winner_name)}</b>")
        lines.append(f"Won by {margin} run{'s' if margin != 1 else ''}")
    
    return "\n".join(lines)


def _rps_text(payload: dict) -> str:
    a_name, b_name = payload["a_name"], payload["b_name"]
    a_id, b_id = payload["a"], payload["b"]
    lines = [
        f"<b>Rock-paper-scissors</b> — {_mention(a_id, a_name)} vs {_mention(b_id, b_name)}",
    ]
    picks = payload.get("picks") or {}
    if picks and len(picks) == 1:
        who = a_name if str(a_id) in picks else b_name
        lines.append(f"{escape(who)} locked in. Waiting for the other pick…")
    else:
        lines.append("Both pick a move.")
    return "\n".join(lines)


def _rps_finished_text(payload: dict, winner_id: int | None, move_a: str, move_b: str) -> str:
    a_name, b_name = payload["a_name"], payload["b_name"]
    a_id, b_id = payload["a"], payload["b"]
    lines = [
        f"<b>Rock-paper-scissors finished</b> — {_mention(a_id, a_name)} vs {_mention(b_id, b_name)}",
        f"{escape(a_name)}: {logic.RPS_LABELS[move_a]} · {escape(b_name)}: {logic.RPS_LABELS[move_b]}",
    ]
    if winner_id is None:
        lines.append("Draw!")
    else:
        name = a_name if winner_id == a_id else b_name
        lines.append(f"{escape(name)} wins!")
    return "\n".join(lines)


async def _edit(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int | None, text: str, markup=None) -> None:
    if message_id is None:
        return
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode="HTML",
            reply_markup=markup,
            disable_web_page_preview=True,
        )
    except TelegramError as exc:
        msg = (getattr(exc, "message", None) or str(exc)).lower()
        if "not modified" in msg:
            return
        log.warning("arena edit failed for message %s: %s", message_id, exc)
    except Exception:
        log.exception("arena edit failed unexpectedly for message %s", message_id)


# ---------------------------------------------------------------------------
# Callback dispatch
# ---------------------------------------------------------------------------


async def on_arena_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    parts = query.data.split(":")
    if len(parts) < 4 or parts[0] != "g":
        await query.answer()
        return
    _, code, action, match_id, *rest = parts
    kind = _CODE_KIND.get(code)
    if kind is None:
        await query.answer("Unknown game.", show_alert=True)
        return

    async with _lock_for(match_id):
        match = db.load_arena_match(match_id)
        if not match or match["kind"] != kind:
            await query.answer("This match is no longer active.", show_alert=True)
            _forget_lock(match_id)
            return
        
        user = update.effective_user
        
        # Handle over selection (before checking if user is in match)
        if action == "ov":
            await _handle_over_select(query, context, match, user, rest[0] if rest else "1")
            return
        
        if not user or user.id not in (match["a_id"], match["b_id"]):
            await query.answer("This is not your match.", show_alert=True)
            return

        if action == "ok":
            await _handle_accept(query, context, match, user)
        elif action == "no":
            await _handle_decline(query, context, match, user)
        elif action == "p":
            await _handle_pick(query, context, match, user, rest[0] if rest else "")
        else:
            await query.answer()


async def _handle_over_select(query, context, match: dict, user: User, overs_str: str) -> None:
    """Challenger picks overs after the rival has accepted."""
    if match["status"] != "selecting":
        await query.answer("Overs already chosen, or this challenge is not ready.", show_alert=True)
        return
    if user.id != match["a_id"]:
        await query.answer("Only the challenger picks the overs.", show_alert=True)
        return

    try:
        overs = int(overs_str)
    except ValueError:
        overs = 1
    overs = max(1, min(3, overs))

    batter = random.choice([match["a_id"], match["b_id"]])
    payload = logic.cricket_new(match["a_id"], match["b_id"], batter=batter, overs=overs)
    payload["a_name"] = match["payload"].get("a_name", "Player A")
    payload["b_name"] = match["payload"].get("b_name", "Player B")

    db.save_arena_match(match["id"], payload, status="live", deadline=_now() + IDLE_TTL)
    await query.answer(f"{overs} over{'s' if overs > 1 else ''} — game on!")
    await _edit(
        context,
        match["chat_id"],
        match["message_id"],
        _cricket_text(payload),
        _cricket_keyboard(match["id"]),
    )


async def _handle_accept(query, context, match: dict, user: User) -> None:
    if match["status"] != "pending":
        await query.answer("Already handled.", show_alert=True)
        return
    if user.id != match["b_id"]:
        await query.answer("Only the challenged player can accept.", show_alert=True)
        return
    kind = match["kind"]
    if kind == KIND_CRICKET:
        # Rival accepted — challenger picks overs next
        a_name = match["payload"].get("a_name", "Player A")
        b_name = match["payload"].get("b_name", "Player B")
        payload = {
            "a_name": a_name,
            "b_name": b_name,
            "accepted": True,
        }
        text = (
            f"<b>🏏 Hand Cricket</b>\n"
            f"{_mention(match['b_id'], b_name)} accepted "
            f"{_mention(match['a_id'], a_name)}'s challenge.\n\n"
            f"{_mention(match['a_id'], a_name)} — pick overs to start "
            f"(1 over = 6 balls each):"
        )
        markup = InlineKeyboardMarkup([[
            InlineKeyboardButton("1 Over", callback_data=f"g:cr:ov:{match['id']}:1"),
            InlineKeyboardButton("2 Overs", callback_data=f"g:cr:ov:{match['id']}:2"),
            InlineKeyboardButton("3 Overs", callback_data=f"g:cr:ov:{match['id']}:3"),
        ]])
        db.save_arena_match(
            match["id"], payload, status="selecting", deadline=_now() + ACCEPT_TTL
        )
        await query.answer("Accepted! Waiting for overs.")
        await _edit(context, match["chat_id"], match["message_id"], text, markup)
        return

    payload = logic.rps_new(match["a_id"], match["b_id"])
    payload["a_name"], payload["b_name"] = match["payload"]["a_name"], match["payload"]["b_name"]
    text = _rps_text(payload)
    markup = _rps_keyboard(match["id"])
    db.save_arena_match(match["id"], payload, status="live", deadline=_now() + IDLE_TTL)
    await query.answer("Accepted! Game on!")
    await _edit(context, match["chat_id"], match["message_id"], text, markup)


async def _handle_decline(query, context, match: dict, user: User) -> None:
    if match["status"] != "pending":
        await query.answer("Already handled.", show_alert=True)
        return
    if user.id != match["b_id"]:
        await query.answer("Only the challenged player can decline.", show_alert=True)
        return
    a_name, b_name = match["payload"]["a_name"], match["payload"]["b_name"]
    db.delete_arena_match(match["id"])
    _forget_lock(match["id"])
    await query.answer("Declined.")
    await _edit(
        context, match["chat_id"], match["message_id"],
        f"{escape(b_name)} declined the challenge from {escape(a_name)}.",
        None,
    )


# Anti-spam: track last click time per user
_last_click: dict[tuple[str, int], float] = {}
CLICK_COOLDOWN = 0.5  # 500ms between clicks


async def _handle_pick(query, context, match: dict, user: User, raw: str) -> None:
    if match["status"] != "live":
        await query.answer("This match is not live.", show_alert=True)
        return
    
    # Anti-spam check
    now = _now()
    key = (match["id"], user.id)
    last = _last_click.get(key, 0)
    if now - last < CLICK_COOLDOWN:
        await query.answer("⏳ Slow down! Wait a moment.", show_alert=True)
        return
    _last_click[key] = now
    
    payload = match["payload"]
    kind = match["kind"]

    if kind == KIND_CRICKET:
        try:
            n = int(raw)
        except ValueError:
            await query.answer("Invalid pick.", show_alert=True)
            return
        outcome = logic.cricket_record_pick(payload, user.id, n)
        if outcome == "bad":
            await query.answer("Pick a number 1-6.", show_alert=True)
            return
        if outcome == "dup":
            await query.answer("You already locked this ball.", show_alert=True)
            return
        if outcome == "wait":
            db.save_arena_match(match["id"], payload, status="live", deadline=_now() + IDLE_TTL)
            await query.answer(f"🔒 Locked: {n}")
            await _edit(context, match["chat_id"], match["message_id"], _cricket_text(payload), _cricket_keyboard(match["id"]))
            return
        # ready — score the ball
        result = logic.cricket_apply_ball(payload)
        if result == "finished":
            winner = logic.cricket_winner(payload)
            _finish_match(match, payload, winner, logic.cricket_summary(payload))
            # Clean up spam tracking
            _last_click.pop((match["id"], match["a_id"]), None)
            _last_click.pop((match["id"], match["b_id"]), None)
            await query.answer("✅ Match finished!")
            await _edit(context, match["chat_id"], match["message_id"], _cricket_finished_text(payload, winner), None)
            return
        db.save_arena_match(match["id"], payload, status="live", deadline=_now() + IDLE_TTL)
        await query.answer(f"⚡ {payload.get('last', 'Ball played')}")
        await _edit(context, match["chat_id"], match["message_id"], _cricket_text(payload), _cricket_keyboard(match["id"]))
        return

    # rock-paper-scissors
    move = {"r": "rock", "p": "paper", "s": "scissors"}.get(raw)
    if not move:
        await query.answer("Invalid pick.", show_alert=True)
        return
    outcome = logic.rps_record_pick(payload, user.id, move)
    if outcome == "bad":
        await query.answer("Invalid pick.", show_alert=True)
        return
    if outcome == "dup":
        await query.answer("You already locked your move.", show_alert=True)
        return
    if outcome == "wait":
        db.save_arena_match(match["id"], payload, status="live", deadline=_now() + IDLE_TTL)
        await query.answer("Locked in.")
        await _edit(context, match["chat_id"], match["message_id"], _rps_text(payload), _rps_keyboard(match["id"]))
        return
    winner, move_a, move_b = logic.rps_resolve(payload)
    summary = f"{logic.RPS_LABELS[move_a]} vs {logic.RPS_LABELS[move_b]}"
    _finish_match(match, payload, winner, summary)
    await query.answer("Match finished!")
    await _edit(context, match["chat_id"], match["message_id"], _rps_finished_text(payload, winner, move_a, move_b), None)


def _finish_match(match: dict, payload: dict, winner_id: int | None, summary: str) -> None:
    db.save_arena_result(
        match["chat_id"], match["kind"],
        match["a_id"], payload["a_name"],
        match["b_id"], payload["b_name"],
        winner_id, summary,
    )
    db.delete_arena_match(match["id"])
    _forget_lock(match["id"])


# ---------------------------------------------------------------------------
# Background maintenance
# ---------------------------------------------------------------------------


async def sweep_idle_matches_once(application) -> int:
    """Close matches nobody accepted or moved on in time. Returns how many
    were closed. Split out from the loop below so it can be unit tested and
    called deterministically — no open match should ever sit for days."""
    closed = 0
    for match in db.list_expired_arena_matches():
        async with _lock_for(match["id"]):
            fresh = db.load_arena_match(match["id"])
            if not fresh or fresh["deadline"] >= _now():
                continue  # already handled by a real move in the meantime
            if fresh["status"] == "pending":
                text = (
                    f"{KIND_LABEL[fresh['kind']].capitalize()} challenge expired — "
                    "nobody accepted in time."
                )
            elif fresh["status"] == "selecting":
                text = (
                    f"{KIND_LABEL[fresh['kind']].capitalize()} challenge expired — "
                    "overs were not chosen in time."
                )
            else:
                text = (
                    f"{KIND_LABEL[fresh['kind']].capitalize()} match closed — no moves "
                    "for 5 minutes. No result recorded."
                )
            db.delete_arena_match(fresh["id"])
            await _edit(application, fresh["chat_id"], fresh["message_id"], text, None)
        _forget_lock(match["id"])
        closed += 1
    return closed


async def sweep_idle_matches_loop(application) -> None:
    await asyncio.sleep(10)
    while True:
        try:
            await sweep_idle_matches_once(application)
        except Exception:
            log.exception("arena sweep failed")
        await asyncio.sleep(SWEEP_INTERVAL)


def purge_old_results_once() -> int:
    """Keep only the last RESULT_RETENTION_DAYS of match history."""
    cutoff = _now() - RESULT_RETENTION_DAYS * 86400
    removed = db.purge_arena_results(cutoff)
    if removed:
        log.info("arena: purged %s result(s) older than %s days", removed, RESULT_RETENTION_DAYS)
    return removed


async def purge_old_results_loop(application) -> None:
    while True:
        try:
            purge_old_results_once()
        except Exception:
            log.exception("arena purge failed")
        await asyncio.sleep(PURGE_INTERVAL)
