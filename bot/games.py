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

    IST = ZoneInfo("Asia/Kolkata")
except Exception:
    IST = None  # type: ignore[assignment]

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, User
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes

from bot import db
from bot.commands import cmd
from bot.arcade import on_four_drop, on_penalty_pick, on_vault_pick, start_four, start_penalty, start_vault
from bot.fun import resolve_member
from bot.gameui import png_file, render_cricket, render_duel, render_result, send_or_edit_card
from bot.moderation import format_user, mention, require_group

log = logging.getLogger(__name__)

_challenges: dict[str, dict] = {}
_match_locks: dict[str, asyncio.Lock] = {}
_ACCEPT_TTL = 180
_LIVE_TTL = 45 * 60

RPS = {"stone": "🪨 Stone", "paper": "📄 Paper", "scissors": "✂️ Scissors"}
RPS_WIN = {("stone", "scissors"), ("paper", "stone"), ("scissors", "paper")}

KIND_LABELS = {
    "toss": "Toss",
    "dice": "Dice",
    "lucky7": "Lucky 7",
    "rps": "Stone-paper",
    "cricket": "Hand cricket",
    "four": "Four in a row",
    "penalty": "Penalty duel",
    "vault": "The vault",
}
KIND_ALIASES = {
    "toss": "toss",
    "coin": "toss",
    "dice": "dice",
    "lucky7": "lucky7",
    "lucky": "lucky7",
    "rps": "rps",
    "sps": "rps",
    "stone": "rps",
    "cricket": "cricket",
    "hand": "cricket",
    "four": "four",
    "connect4": "four",
    "c4": "four",
    "penalty": "penalty",
    "pk": "penalty",
    "spot": "penalty",
    "vault": "vault",
    "heist": "vault",
    "split": "vault",
}


def _now() -> datetime:
    if IST is not None:
        return datetime.now(IST)
    return datetime.utcnow() + timedelta(hours=5, minutes=30)


def _day_key() -> str:
    return _now().date().isoformat()


def _week_key() -> str:
    return _now().date().strftime("%G-W%V")


def _yesterday() -> str:
    return (_now().date() - timedelta(days=1)).isoformat()


def _award(chat_id: int, user_id: int, points: int, *, win: bool | None = None, kind: str | None = None) -> None:
    db.add_coins(chat_id, user_id, points)
    db.record_game(chat_id, user_id, points=points, win=win, day_key=_day_key(), week_key=_week_key())
    if kind:
        db.record_kind_game(chat_id, user_id, kind, win)


def _new_id() -> str:
    return secrets.token_hex(4)


def _match_ttl(ch: dict) -> int:
    return _LIVE_TTL if ch.get("status") == "live" else _ACCEPT_TTL


def _match_lock(cid: str) -> asyncio.Lock:
    lock = _match_locks.get(cid)
    if lock is None:
        lock = asyncio.Lock()
        _match_locks[cid] = lock
    return lock


def _save_match(cid: str, ch: dict) -> None:
    ch["expires"] = time.time() + _match_ttl(ch)
    _challenges[cid] = ch
    try:
        db.save_game_match(cid, ch)
    except Exception:
        log.exception("game match save failed cid=%s", cid)


def _end_match(cid: str) -> None:
    _challenges.pop(cid, None)
    db.delete_game_match(cid)


def _get_match(cid: str) -> dict | None:
    row = db.peek_game_match_row(cid)
    if row is not None:
        if float(row["expires"]) < time.time():
            db.delete_game_match(cid)
            _challenges.pop(cid, None)
            return None
        ch = db.decode_game_match(row["payload"])
        if not ch:
            return None
        _challenges[cid] = ch
        return ch
    ram = _challenges.get(cid)
    if not ram or time.time() > float(ram.get("expires") or 0):
        _challenges.pop(cid, None)
        return None
    try:
        db.save_game_match(cid, ram)
    except Exception:
        log.exception("game match heal failed cid=%s", cid)
    return ram


def _purge() -> None:
    now = time.time()
    db.purge_game_matches(now)
    dead = [k for k, v in _challenges.items() if v.get("expires", 0) < now]
    for k in dead:
        _challenges.pop(k, None)


def _pending_for(chat_id: int, user_id: int) -> str | None:
    _purge()
    for cid, ch in db.list_game_matches(chat_id):
        _challenges[cid] = ch
    for cid, ch in _challenges.items():
        if ch.get("chat_id") != chat_id:
            continue
        if user_id in (ch.get("a"), ch.get("b")) and ch.get("status") in {"open", "live"}:
            return cid
    return None


async def _need_rival(update: Update, context: ContextTypes.DEFAULT_TYPE, example: str = "cricket") -> User | None:
    msg = update.effective_message
    actor = update.effective_user
    rival = await resolve_member(update, context)
    if not rival or not actor:
        await msg.reply_text(
            "Reply to a friend, or tag them.\n"
            f"Example: {cmd(example)} @username"
        )
        return None
    if rival.id == actor.id:
        await msg.reply_text("Challenge someone else — not yourself.")
        return None
    if rival.is_bot:
        await msg.reply_text("Bots already work too hard. Challenge a human.")
        return None
    return rival


def _accept_markup(cid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅  Accept", callback_data=f"gm:ok:{cid}"),
                InlineKeyboardButton("✕  Decline", callback_data=f"gm:no:{cid}"),
            ]
        ]
    )


def _rps_markup(cid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🪨  Stone", callback_data=f"gm:r:{cid}:stone"),
                InlineKeyboardButton("📄  Paper", callback_data=f"gm:r:{cid}:paper"),
                InlineKeyboardButton("✂️  Scissors", callback_data=f"gm:r:{cid}:scissors"),
            ]
        ]
    )


def _hand_markup(cid: str) -> InlineKeyboardMarkup:
    faces = {1: "①", 2: "②", 3: "③", 4: "④", 5: "⑤", 6: "⑥"}
    row1 = [InlineKeyboardButton(f"{faces[n]}  {n}", callback_data=f"gm:c:{cid}:{n}") for n in range(1, 4)]
    row2 = [InlineKeyboardButton(f"{faces[n]}  {n}", callback_data=f"gm:c:{cid}:{n}") for n in range(4, 7)]
    return InlineKeyboardMarkup([row1, row2])


def _toss_markup(cid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🪙  Heads", callback_data=f"gm:t:{cid}:heads"),
                InlineKeyboardButton("🌙  Tails", callback_data=f"gm:t:{cid}:tails"),
            ]
        ]
    )


async def _short_name(context, chat_id: int, user_id: int) -> str:
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        name = member.user.first_name or member.user.full_name or str(user_id)
    except Exception:
        name = str(user_id)
    return " ".join(str(name).split())[:18]


async def cmd_daily(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group(update):
        return
    chat_id = update.effective_chat.id
    user = update.effective_user
    if not user:
        return
    today = _day_key()
    coins, streak, last = db.get_wallet(chat_id, user.id)
    if last == today:
        await update.effective_message.reply_text(
            f"Today's bonus is already claimed. Come back tomorrow.\n"
            f"Streak: {streak} day(s). Coins: {coins}\n"
            f"Board: {cmd('top')}"
        )
        return
    streak = streak + 1 if last == _yesterday() else 1
    gain = 40 + 15 * min(streak - 1, 10)
    db.set_wallet(chat_id, user.id, coins + gain, streak, today)
    db.record_game(chat_id, user.id, points=gain, win=None, day_key=today, week_key=_week_key())
    await update.effective_message.reply_html(
        f"{mention(user)} claimed <b>+{gain}</b> coins · streak <b>{streak}</b>\n"
        f"Balance: {coins + gain}. Open {cmd('gamehelp')} — new: {cmd('four')} {cmd('penalty')} {cmd('vault')}."
    )


async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group(update):
        return
    user = update.effective_user
    chat_id = update.effective_chat.id
    coins, streak, last = db.get_wallet(chat_id, user.id)
    st = db.get_game_stats(chat_id, user.id)
    if st["day_key"] != _day_key():
        st["day_points"] = 0
    claimed = "claimed" if last == _day_key() else f"not yet — {cmd('daily')}"
    lines = [
        f"{mention(user)}",
        f"Coins: <b>{coins}</b>",
        f"Points: <b>{st['points']}</b> · today {st['day_points']}",
        f"Wins: {st['wins']} · Losses: {st['losses']}",
        f"Daily streak: {streak} · Today's bonus: {claimed}",
    ]
    by_game = db.list_kind_stats(chat_id, user.id)
    if by_game:
        lines.append("")
        lines.append("<b>By game</b>")
        for row in by_game:
            label = KIND_LABELS.get(row["kind"], row["kind"])
            lines.append(
                f"• {escape(label)} — <b>{row['wins']}</b>W  {row['losses']}L  {row['draws']}D"
            )
    await update.effective_message.reply_html("\n".join(lines))


async def cmd_top(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group(update):
        return
    args = [a.lower() for a in (context.args or [])]
    kind = KIND_ALIASES.get(args[0]) if args else None
    if kind:
        await _top_kind(update, context, kind)
        return
    if args and args[0] in {"day", "today", "aaj"}:
        field, title = "day_points", "Today (IST)"
    elif args and args[0] in {"week", "hafta"}:
        field, title = "week_points", "This week"
    elif args and args[0] in {"wins", "jeet"}:
        field, title = "wins", "Most wins"
    else:
        field, title = "points", "All time"
    period = None
    if field == "day_points":
        period = _day_key()
    elif field == "week_points":
        period = _week_key()
    rows = db.list_game_top(update.effective_chat.id, field, period_key=period)
    if not rows:
        await update.effective_message.reply_text(
            f"Board is empty. {cmd('daily')} then play {cmd('four')} / {cmd('cricket')}."
        )
        return
    medals = ["🥇", "🥈", "🥉"]
    lines = [f"<b>Leaderboard — {escape(title)}</b>"]
    for i, (uid, score) in enumerate(rows, 1):
        who = await format_user(context, update.effective_chat.id, uid)
        prefix = medals[i - 1] if i <= 3 else f"{i}."
        lines.append(f"{prefix} {who} — <b>{score}</b>")
    games = " · ".join(cmd("top") + " " + k for k in ("cricket", "four", "penalty", "vault", "rps"))
    lines.append(f"{cmd('top')} · {cmd('top')} today · {cmd('top')} week · {cmd('top')} wins")
    lines.append(games)
    await update.effective_message.reply_html("\n".join(lines), disable_web_page_preview=True)


async def _top_kind(update: Update, context: ContextTypes.DEFAULT_TYPE, kind: str) -> None:
    label = KIND_LABELS.get(kind, kind)
    rows = db.list_kind_top(update.effective_chat.id, kind)
    if not rows:
        await update.effective_message.reply_text(
            f"No {label} matches yet. Challenge someone with {cmd(kind)}."
        )
        return
    medals = ["🥇", "🥈", "🥉"]
    lines = [f"<b>{escape(label)}</b> — wins / losses / draws"]
    for i, (uid, wins, losses, draws) in enumerate(rows, 1):
        who = await format_user(context, update.effective_chat.id, uid)
        prefix = medals[i - 1] if i <= 3 else f"{i}."
        played = wins + losses + draws
        lines.append(
            f"{prefix} {who} — <b>{wins}</b>W  {losses}L  {draws}D  ({played} played)"
        )
    lines.append(f"{cmd('top')} cricket · {cmd('top')} four · {cmd('top')} penalty · {cmd('top')} vault")
    await update.effective_message.reply_html("\n".join(lines), disable_web_page_preview=True)


async def cmd_toss(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group(update):
        return
    msg = update.effective_message
    actor = update.effective_user
    if not actor:
        return
    args = [a.lower() for a in (context.args or [])]
    choice = args[0] if args and args[0] in {"heads", "tails", "head", "tail"} else None
    if choice in {"head", "tail"}:
        choice = choice + "s"
    rival = await resolve_member(update, context)
    if not choice and rival and not rival.is_bot and rival.id != actor.id:
        await _open_challenge(
            update,
            context,
            rival,
            kind="toss",
            text=(
                f"{mention(actor)} challenged {mention(rival)} to a toss.\n"
                "Both pick Heads or Tails. The coin flips once both have locked."
            ),
        )
        return
    if not choice:
        await msg.reply_html(
            "Solo: <code>" + cmd("toss") + " heads</code> or <code>" + cmd("toss") + " tails</code>\n"
            "Vs friend: reply to them with " + cmd("toss")
        )
        return
    coin = random.choice(["heads", "tails"])
    win = choice == coin
    pts = 20 if win else 5
    _award(update.effective_chat.id, actor.id, pts, win=win, kind="toss")
    face = "Heads" if coin == "heads" else "Tails"
    result = "you win" if win else "not this time"
    png = render_result(
        title="Toss",
        headline=face,
        detail=f"You called {choice}. {result}  +{pts} coins",
    )
    await msg.reply_photo(
        png_file(png),
        caption=f"🪙 <b>{face}</b> — you called {choice}. +{pts} coins",
        parse_mode="HTML",
    )


async def cmd_dice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group(update):
        return
    actor = update.effective_user
    if not actor:
        return
    rival = await resolve_member(update, context)
    if rival and not rival.is_bot and rival.id != actor.id:
        await _open_challenge(
            update,
            context,
            rival,
            kind="dice",
            text=(
                f"{mention(actor)} vs {mention(rival)} — dice.\n"
                "Accept, then both get a 🎲. Higher number wins."
            ),
        )
        return
    try:
        sent = await update.effective_message.reply_dice(emoji="🎲")
    except TelegramError:
        log.exception("dice send failed")
        await update.effective_message.reply_text("Could not send a dice here. Check my permissions.")
        return
    value = sent.dice.value if sent.dice else 0
    if value == 6:
        pts, note, win = 25, "Six.", True
    elif value >= 5:
        pts, note, win = 15, "Solid roll.", True
    elif value == 1:
        pts, note, win = 5, "One. Try again.", None
    else:
        pts, note, win = 8, "Okay roll.", None
    _award(update.effective_chat.id, actor.id, pts, win=win, kind="dice")
    await update.effective_message.reply_text(f"{note} +{pts} coins. Challenge a friend: {cmd('dice')} @user")


async def cmd_lucky7(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group(update):
        return
    user = update.effective_user
    if not user:
        return
    args = [a.lower() for a in (context.args or [])]
    pick = args[0] if args else ""
    alias = {"low": "low", "under": "low", "7down": "low", "down": "low", "high": "high", "over": "high", "7up": "high", "up": "high", "7": "seven", "lucky": "seven"}
    side = alias.get(pick)
    if not side:
        await update.effective_message.reply_text(
            "Call the total of two dice (2–12).\n"
            f"{cmd('lucky7')} low   → 2 to 6\n"
            f"{cmd('lucky7')} 7     → exactly 7 (pays more)\n"
            f"{cmd('lucky7')} high  → 8 to 12"
        )
        return
    a, b = random.randint(1, 6), random.randint(1, 6)
    total = a + b
    if total == 7:
        actual = "seven"
    elif total < 7:
        actual = "low"
    else:
        actual = "high"
    win = side == actual
    pts = 40 if (win and side == "seven") else (18 if win else 4)
    _award(update.effective_chat.id, user.id, pts, win=win, kind="lucky7")
    label = "LOW (2–6)" if actual == "low" else ("HIGH (8–12)" if actual == "high" else "LUCKY 7")
    png = render_result(
        title="Lucky 7",
        headline=f"{a} + {b} = {total}",
        detail=f"{label}  ·  you called {side}  ·  +{pts}",
        accent=(16, 120, 80, 255),
    )
    await update.effective_message.reply_photo(
        png_file(png),
        caption=(
            f"🎲 {a} + {b} = <b>{total}</b> → {label}\n"
            f"You called <b>{side}</b>. {'Win. ' if win else 'Miss. '}+{pts} coins"
        ),
        parse_mode="HTML",
    )


async def cmd_rps(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group(update):
        return
    rival = await _need_rival(update, context, "rps")
    if not rival:
        return
    actor = update.effective_user
    await _open_challenge(
        update,
        context,
        rival,
        kind="rps",
        text=(
            f"{mention(actor)} vs {mention(rival)} — Stone / Paper / Scissors.\n"
            "Accept, then both pick in secret. Same hand is a tie."
        ),
    )


async def cmd_cricket(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group(update):
        return
    rival = await _need_rival(update, context, "cricket")
    if not rival:
        return
    actor = update.effective_user
    await _open_challenge(
        update,
        context,
        rival,
        kind="cricket",
        text=(
            f"{mention(actor)} vs {mention(rival)}\n"
            "<b>Hand cricket</b> — 6 balls each, or until OUT.\n"
            "Both pick 1–6 each ball. Same number = wicket.\n"
            "The challenger bats first. Chase ends as soon as the target is passed."
        ),
    )


async def cmd_four(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group(update):
        return
    rival = await _need_rival(update, context, "four")
    if not rival:
        return
    actor = update.effective_user
    await _open_challenge(
        update,
        context,
        rival,
        kind="four",
        text=(
            f"{mention(actor)} vs {mention(rival)} — <b>Four in a row</b>.\n"
            "Drop discs in columns 1–7. First to four in a line (any direction) wins.\n"
            "Challenger drops first. Turns lock — you cannot undo a drop."
        ),
    )


async def cmd_penalty(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group(update):
        return
    rival = await _need_rival(update, context, "penalty")
    if not rival:
        return
    actor = update.effective_user
    await _open_challenge(
        update,
        context,
        rival,
        kind="penalty",
        text=(
            f"{mention(actor)} vs {mention(rival)} — <b>Penalty duel</b>.\n"
            "Five kicks each. Shooter and keeper both pick Left / Centre / Right in secret.\n"
            "Same side = save. Different = goal. Then you swap. Sudden death if still level."
        ),
    )


async def cmd_vault(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group(update):
        return
    rival = await _need_rival(update, context, "vault")
    if not rival:
        return
    actor = update.effective_user
    await _open_challenge(
        update,
        context,
        rival,
        kind="vault",
        text=(
            f"{mention(actor)} vs {mention(rival)} — <b>The vault</b>.\n"
            "One pot. Both lock Share or Take.\n"
            "Both Share → split. One Take → they keep it. Both Take → empty."
        ),
    )


async def _open_challenge(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    rival: User,
    *,
    kind: str,
    text: str,
) -> None:
    actor = update.effective_user
    chat_id = update.effective_chat.id
    if _pending_for(chat_id, actor.id) or _pending_for(chat_id, rival.id):
        await update.effective_message.reply_text(
            "A match is already running. Finish or cancel it first."
        )
        return
    cid = _new_id()
    _challenges[cid] = {
        "kind": kind,
        "chat_id": chat_id,
        "a": actor.id,
        "b": rival.id,
        "status": "open",
        "expires": time.time() + _ACCEPT_TTL,
        "picks": {},
        "score_a": 0,
        "score_b": 0,
        "balls": 0,
        "innings": 1,
        "batter": actor.id,
        "event": "",
        "first_innings": None,
    }
    _save_match(cid, _challenges[cid])
    await update.effective_message.reply_html(
        text + "\n<i>2 minutes to accept.</i>",
        reply_markup=_accept_markup(cid),
    )


async def on_game_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data or not query.data.startswith("gm:"):
        return
    parts = query.data.split(":")
    if len(parts) < 3:
        await query.answer()
        return
    if parts[1] == "h":
        await _edit_help(query, parts[2] if len(parts) > 2 else "index")
        return
    cid = parts[2]
    async with _match_lock(cid):
        ch = _get_match(cid)
        user = update.effective_user or (query.from_user if query else None)
        if not user:
            await query.answer("Could not see who tapped. Try again.", show_alert=True)
            return
        if not ch:
            await query.answer("This match is no longer running. Start a new one.", show_alert=True)
            return
        if time.time() > float(ch.get("expires") or 0):
            _end_match(cid)
            await query.answer("This match timed out. Start a new one.", show_alert=True)
            return
        action = parts[1]
        if action == "ok":
            await _on_accept(query, context, cid, ch, user)
            return
        if action == "no":
            if user.id not in (ch["a"], ch["b"]):
                await query.answer("This is not your match.", show_alert=True)
                return
            _end_match(cid)
            await query.answer("Challenge cancelled.")
            try:
                await query.edit_message_text("Challenge cancelled.")
            except BadRequest:
                pass
            return
        if action == "r":
            await _on_rps_pick(query, context, cid, ch, user, parts[3] if len(parts) > 3 else "")
            return
        if action == "c":
            n = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
            await _on_cricket_pick(query, context, cid, ch, user, n)
            return
        if action == "t":
            await _on_toss_pick(query, context, cid, ch, user, parts[3] if len(parts) > 3 else "")
            return
        if action == "f":
            col = int(parts[3]) - 1 if len(parts) > 3 and parts[3].isdigit() else -1
            await on_four_drop(query, context, cid, ch, user, col)
            return
        if action == "p":
            await on_penalty_pick(query, context, cid, ch, user, parts[3] if len(parts) > 3 else "")
            return
        if action == "v":
            await on_vault_pick(query, context, cid, ch, user, parts[3] if len(parts) > 3 else "")
            return
        await query.answer()


async def _on_accept(query, context, cid: str, ch: dict, user: User) -> None:
    if user.id != ch["b"]:
        await query.answer("Only the person who was challenged can accept.", show_alert=True)
        return
    ch["status"] = "live"
    _save_match(cid, ch)
    kind = ch["kind"]
    if kind == "dice":
        try:
            await _run_dice_duel(query, context, ch)
        finally:
            _end_match(cid)
        return
    if kind == "toss":
        await query.answer("Call heads or tails.")
        a_n = await _short_name(context, ch["chat_id"], ch["a"])
        b_n = await _short_name(context, ch["chat_id"], ch["b"])
        png = render_duel(
            title="Toss",
            name_a=a_n,
            name_b=b_n,
            line="Both call the coin. It flips once both have locked.",
            waiting="Your call locks. You cannot change it.",
        )
        _save_match(cid, ch)
        await send_or_edit_card(query, png, f"{mention(query.from_user)} — toss is live.", _toss_markup(cid))
        return
    if kind == "rps":
        await query.answer("Pick stone, paper, or scissors.")
        a_n = await _short_name(context, ch["chat_id"], ch["a"])
        b_n = await _short_name(context, ch["chat_id"], ch["b"])
        png = render_duel(
            title="Stone · Paper · Scissors",
            name_a=a_n,
            name_b=b_n,
            line="Secret picks. Revealed when both have locked.",
            waiting="Tap once. Your hand is locked for this round.",
        )
        _save_match(cid, ch)
        await send_or_edit_card(query, png, "Match live. Choose below.", _rps_markup(cid))
        return
    if kind == "cricket":
        await query.answer("You bat second. Challenger bats first.")
        ch["event"] = "Challenger bats first. Both pick 1–6."
        _save_match(cid, ch)
        await _show_cricket(query, context, cid, ch, markup=_hand_markup(cid))
        return
    if kind == "four":
        await start_four(query, context, cid, ch)
        return
    if kind == "penalty":
        await start_penalty(query, context, cid, ch)
        return
    if kind == "vault":
        await start_vault(query, context, cid, ch)
        return
    await query.answer()


async def _edit_html(query, text: str, markup=None) -> None:
    try:
        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=markup,
            disable_web_page_preview=True,
        )
    except BadRequest:
        pass


async def _show_cricket(query, context, cid: str, ch: dict, *, markup=None) -> None:
    chat_id = ch["chat_id"]
    name_a = await _short_name(context, chat_id, ch["a"])
    name_b = await _short_name(context, chat_id, ch["b"])
    waiting = ch.get("waiting") or "Both pick a number. The first tap locks that ball."
    first = ch.get("first_innings")
    png = render_cricket(
        name_a=name_a,
        name_b=name_b,
        score_a=int(ch["score_a"] or 0),
        score_b=int(ch["score_b"] or 0),
        batter_is_a=ch["batter"] == ch["a"],
        innings=int(ch["innings"] or 1),
        ball=int(ch["balls"] or 0) + 1,
        target=None if first is None else int(first),
        event=ch.get("event") or "Both pick 1–6. Same number is OUT.",
        waiting=waiting,
    )
    a_m = await format_user(context, chat_id, ch["a"])
    b_m = await format_user(context, chat_id, ch["b"])
    bat = a_m if ch["batter"] == ch["a"] else b_m
    caption = (
        f"<b>Batting:</b> {bat}\n"
        f"{a_m} {ch['score_a']}  ·  {b_m} {ch['score_b']}"
    )
    try:
        await send_or_edit_card(query, png, caption, markup)
    except Exception:
        log.exception("cricket card send failed")


async def _finish_cricket(query, context, cid: str, ch: dict, event: str) -> None:
    try:
        await query.answer()
    except Exception:
        pass
    chat_id = ch["chat_id"]
    sa, sb = ch["score_a"], ch["score_b"]
    a_m = await format_user(context, chat_id, ch["a"])
    b_m = await format_user(context, chat_id, ch["b"])
    name_a = await _short_name(context, chat_id, ch["a"])
    name_b = await _short_name(context, chat_id, ch["b"])
    if sa > sb:
        _award(chat_id, ch["a"], 40, win=True, kind="cricket")
        _award(chat_id, ch["b"], 12, win=False, kind="cricket")
        headline = f"{name_a} wins"
        detail = f"{sa} – {sb}   ·   {event}"
        winner = f"{a_m} wins"
    elif sb > sa:
        _award(chat_id, ch["b"], 40, win=True, kind="cricket")
        _award(chat_id, ch["a"], 12, win=False, kind="cricket")
        headline = f"{name_b} wins"
        detail = f"{sb} – {sa}   ·   {event}"
        winner = f"{b_m} wins"
    else:
        _award(chat_id, ch["a"], 20, win=None, kind="cricket")
        _award(chat_id, ch["b"], 20, win=None, kind="cricket")
        headline = "Match tied"
        detail = f"{sa} – {sb}   ·   {event}"
        winner = "Tie — both +20"
    png = render_result(title="Hand cricket", headline=headline, detail=detail, accent=(34, 140, 90, 255))
    _end_match(cid)
    await send_or_edit_card(
        query,
        png,
        f"{event}\n\n<b>Final</b>  {a_m} {sa}  —  {b_m} {sb}\n{winner}",
        None,
    )


async def _cricket_scoreboard(context, ch: dict) -> str:
    a_m = await format_user(context, ch["chat_id"], ch["a"])
    b_m = await format_user(context, ch["chat_id"], ch["b"])
    bat_m = a_m if ch["batter"] == ch["a"] else b_m
    who = "first innings" if ch["innings"] == 1 else "chase"
    return (
        f"<b>Hand cricket</b> — {who}, ball {min(ch['balls'] + 1, 6)}/6\n"
        f"Batting: {bat_m}\n"
        f"{a_m} {ch['score_a']}  ·  {b_m} {ch['score_b']}"
    )


async def _run_dice_duel(query, context, ch: dict) -> None:
    chat_id = ch["chat_id"]
    await query.answer("Rolling…")
    try:
        await query.edit_message_text("Dice rolling…")
    except BadRequest:
        pass
    try:
        m1 = await context.bot.send_dice(chat_id, emoji="🎲")
        m2 = await context.bot.send_dice(chat_id, emoji="🎲")
    except TelegramError:
        log.exception("dice duel failed")
        await query.edit_message_text("Could not roll dice. Check that I can send messages here.")
        return
    v1 = m1.dice.value if m1.dice else 0
    v2 = m2.dice.value if m2.dice else 0
    a, b = ch["a"], ch["b"]
    if v1 > v2:
        _award(chat_id, a, 30, win=True, kind="dice")
        _award(chat_id, b, 8, win=False, kind="dice")
        result = f"Challenger wins ({v1} vs {v2}). +30 / +8"
    elif v2 > v1:
        _award(chat_id, b, 30, win=True, kind="dice")
        _award(chat_id, a, 8, win=False, kind="dice")
        result = f"Opponent wins ({v2} vs {v1}). +30 / +8"
    else:
        _award(chat_id, a, 12, win=None, kind="dice")
        _award(chat_id, b, 12, win=None, kind="dice")
        result = f"Tie {v1}–{v2}. Both +12"
    a_m = await format_user(context, chat_id, a)
    b_m = await format_user(context, chat_id, b)
    await context.bot.send_message(
        chat_id,
        f"{a_m} rolled {v1}, {b_m} rolled {v2}.\n{result}",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def _on_toss_pick(query, context, cid, ch, user, face: str) -> None:
    if user.id not in (ch["a"], ch["b"]) or face not in {"heads", "tails"}:
        await query.answer("This is not your toss.", show_alert=True)
        return
    if ch.get("status") != "live":
        await query.answer("Accept the challenge first.", show_alert=True)
        return
    if str(user.id) in ch["picks"]:
        await query.answer("Already locked.", show_alert=True)
        return
    ch["picks"][str(user.id)] = face
    _save_match(cid, ch)
    if len(ch["picks"]) < 2:
        await query.answer(f"Locked {face}. Waiting for the other call.")
        return
    coin = random.choice(["heads", "tails"])
    a, b = str(ch["a"]), str(ch["b"])
    aw, bw = ch["picks"].get(a) == coin, ch["picks"].get(b) == coin
    chat_id = ch["chat_id"]
    if aw and not bw:
        _award(chat_id, ch["a"], 22, win=True, kind="toss")
        _award(chat_id, ch["b"], 6, win=False, kind="toss")
        extra = "Challenger called it right."
    elif bw and not aw:
        _award(chat_id, ch["b"], 22, win=True, kind="toss")
        _award(chat_id, ch["a"], 6, win=False, kind="toss")
        extra = "Opponent called it right."
    elif aw and bw:
        _award(chat_id, ch["a"], 12, win=None, kind="toss")
        _award(chat_id, ch["b"], 12, win=None, kind="toss")
        extra = "Same call — points split."
    else:
        _award(chat_id, ch["a"], 6, win=False, kind="toss")
        _award(chat_id, ch["b"], 6, win=False, kind="toss")
        extra = "Both missed. The coin was the other side."
    _end_match(cid)
    png = render_result(title="Toss", headline=coin.upper(), detail=extra)
    await send_or_edit_card(
        query,
        png,
        f"🪙 Coin: <b>{coin}</b>\nCalls: {ch['picks'].get(a)} vs {ch['picks'].get(b)}\n{extra}",
        None,
    )


async def _on_rps_pick(query, context, cid, ch, user, hand: str) -> None:
    if user.id not in (ch["a"], ch["b"]) or hand not in RPS:
        await query.answer("This is not your match.", show_alert=True)
        return
    if ch.get("status") != "live":
        await query.answer("Accept the challenge first.", show_alert=True)
        return
    if str(user.id) in ch["picks"]:
        await query.answer("Already locked.", show_alert=True)
        return
    ch["picks"][str(user.id)] = hand
    _save_match(cid, ch)
    if len(ch["picks"]) < 2:
        await query.answer("Locked. Waiting for the other player.")
        return
    pa, pb = ch["picks"][str(ch["a"])], ch["picks"][str(ch["b"])]
    chat_id = ch["chat_id"]
    a_m = await format_user(context, chat_id, ch["a"])
    b_m = await format_user(context, chat_id, ch["b"])
    a_n = await _short_name(context, chat_id, ch["a"])
    b_n = await _short_name(context, chat_id, ch["b"])
    if pa == pb:
        _award(chat_id, ch["a"], 10, win=None, kind="rps")
        _award(chat_id, ch["b"], 10, win=None, kind="rps")
        outcome = "Tie. Both +10"
        headline = "It's a tie"
    elif (pa, pb) in RPS_WIN:
        _award(chat_id, ch["a"], 25, win=True, kind="rps")
        _award(chat_id, ch["b"], 8, win=False, kind="rps")
        outcome = f"{a_m} wins. +25 / +8"
        headline = f"{a_n} wins"
    else:
        _award(chat_id, ch["b"], 25, win=True, kind="rps")
        _award(chat_id, ch["a"], 8, win=False, kind="rps")
        outcome = f"{b_m} wins. +25 / +8"
        headline = f"{b_n} wins"
    _end_match(cid)
    png = render_result(
        title="Stone · Paper · Scissors",
        headline=headline,
        detail=f"{RPS[pa]}  vs  {RPS[pb]}",
    )
    await send_or_edit_card(
        query,
        png,
        f"{a_m}: {RPS[pa]}\n{b_m}: {RPS[pb]}\n\n{outcome}",
        None,
    )


async def _on_cricket_pick(query, context, cid, ch, user, n: int) -> None:
    if user.id not in (ch["a"], ch["b"]) or n not in range(1, 7):
        await query.answer("This is not your match.", show_alert=True)
        return
    if ch["status"] != "live":
        await query.answer("Accept the challenge first.", show_alert=True)
        return
    if str(user.id) in ch["picks"]:
        await query.answer("Locked for this ball. You cannot change it.", show_alert=True)
        return
    ch["picks"][str(user.id)] = n
    _save_match(cid, ch)
    other = ch["b"] if user.id == ch["a"] else ch["a"]
    if len(ch["picks"]) < 2:
        innings, balls = int(ch["innings"] or 1), int(ch["balls"] or 0)
        other_n = await _short_name(context, ch["chat_id"], other)
        you = await _short_name(context, ch["chat_id"], user.id)
        fresh = _get_match(cid)
        if (
            not fresh
            or int(fresh.get("innings") or 1) != innings
            or int(fresh.get("balls") or 0) != balls
            or len(fresh.get("picks") or {}) >= 2
        ):
            await query.answer("Locked.")
            return
        fresh["waiting"] = f"{you} locked. Waiting for {other_n}."
        fresh["event"] = "One shot locked. Number stays hidden until both play."
        _save_match(cid, fresh)
        await query.answer("Locked. Waiting for the other player.")
        await _show_cricket(query, context, cid, fresh, markup=_hand_markup(cid))
        return

    pa, pb = int(ch["picks"][str(ch["a"])]), int(ch["picks"][str(ch["b"])])
    ch["picks"] = {}
    batter = ch["batter"]
    bat_n = pa if batter == ch["a"] else pb
    bowl_n = pb if batter == ch["a"] else pa
    out = bat_n == bowl_n
    if not out:
        key = "score_a" if batter == ch["a"] else "score_b"
        ch[key] = int(ch.get(key) or 0) + bat_n
        ch["balls"] = int(ch.get("balls") or 0) + 1
        ch["event"] = f"Last ball  {bat_n} vs {bowl_n}  ·  +{bat_n} runs"
    else:
        ch["event"] = f"OUT  ·  both played {bat_n}"
        ch["balls"] = 6

    chase_score = int((ch["score_a"] if batter == ch["a"] else ch["score_b"]) or 0)
    innings = int(ch.get("innings") or 1)
    first = ch.get("first_innings")
    if innings == 2 and first is not None:
        if chase_score > int(first):
            _save_match(cid, ch)
            await _finish_cricket(query, context, cid, ch, f"{ch['event']}. Target passed.")
            return

    innings_over = out or int(ch.get("balls") or 0) >= 6
    if innings_over:
        if innings == 1:
            ch["first_innings"] = int((ch["score_a"] if batter == ch["a"] else ch["score_b"]) or 0)
            ch["innings"] = 2
            ch["balls"] = 0
            ch["batter"] = ch["b"] if batter == ch["a"] else ch["a"]
            target = int(ch["first_innings"] or 0)
            ch["waiting"] = f"Target {target + 1}. Chase starts now."
            ch["event"] = f"{ch['event']}. First innings {target}. Need {target + 1} to win."
            _save_match(cid, ch)
            await query.answer("Innings over. Chase begins.")
            await _show_cricket(query, context, cid, ch, markup=_hand_markup(cid))
            return
        first = int(ch.get("first_innings") or 0)
        if chase_score > first:
            why = "Target passed."
        elif chase_score == first:
            why = "Scores level."
        else:
            why = "Chase fell short."
        _save_match(cid, ch)
        await _finish_cricket(query, context, cid, ch, f"{ch['event']}. {why}")
        return

    ch["waiting"] = "Both pick again. Your next tap locks this ball."
    _save_match(cid, ch)
    await query.answer(ch["event"])
    await _show_cricket(query, context, cid, ch, markup=_hand_markup(cid))



def _help_pages() -> dict[str, str]:
    return {
        "index": (
            "<b>Games</b>\n"
            "All of these run in the group. Coins are for fun only — not real money. "
            "Wins add leaderboard points (India time, IST).\n\n"
            f"1. {cmd('daily')} once a day — streak bonus.\n"
            f"2. {cmd('balance')} — your coins and wins\n"
            f"3. {cmd('top')} — today / week / all-time board\n\n"
            "<b>Solo</b>\n"
            f"• {cmd('toss')} heads|tails — coin toss\n"
            f"• {cmd('dice')} — Telegram dice, extra on a 6\n"
            f"• {cmd('lucky7')} low|7|high — two dice (fun coins only)\n\n"
            "<b>With a friend</b> (reply or @tag)\n"
            f"• {cmd('cricket')} — hand cricket\n"
            f"• {cmd('four')} — four in a row (visual board)\n"
            f"• {cmd('penalty')} — five kicks each, shooter vs keeper\n"
            f"• {cmd('vault')} — share the pot or take it all\n"
            f"• {cmd('rps')} — stone paper scissors\n"
            f"• {cmd('dice')} @user — both roll, higher wins\n"
            f"• {cmd('toss')} @user — both call, then the coin flips\n\n"
            f"Boards: {cmd('top')} cricket · {cmd('top')} four · {cmd('top')} penalty\n"
            "Use the buttons, or " + cmd("gamehelp") + " four"
        ),
        "daily": (
            "<b>Daily bonus</b>\n"
            f"Once a day, {cmd('daily')} — resets after midnight in India (IST).\n\n"
            "Day one is 40 coins. Claiming every day builds a streak: "
            "+15 extra per day, up to 10 streak days.\n\n"
            "Miss a day and the streak returns to 1.\n\n"
            f"{cmd('top')} today shows today's points (daily + games)."
        ),
        "toss": (
            "<b>Toss</b>\n"
            "Call heads or tails.\n\n"
            f"<code>{cmd('toss')} heads</code>\n"
            f"<code>{cmd('toss')} tails</code>\n"
            "Correct call = +20, wrong = +5 (still playing).\n\n"
            f"Vs a friend: reply with {cmd('toss')} or {cmd('toss')} @username. "
            "Both tap Heads or Tails, then the bot flips. Same call splits the points."
        ),
        "dice": (
            "<b>Dice</b>\n"
            f"{cmd('dice')} sends a real Telegram 🎲.\n"
            "6 = +25, 5 = +15, lower rolls still give a few coins.\n\n"
            f"Vs a friend: {cmd('dice')} @user → they Accept → "
            "both dice roll. Higher number wins (+30).\n\n"
            "Same feel as a board-game dice."
        ),
        "lucky7": (
            "<b>Lucky 7</b>\n"
            "Two dice (total 2–12). Call first:\n\n"
            f"• {cmd('lucky7')} low — 2 through 6\n"
            f"• {cmd('lucky7')} 7 — exactly seven (pays +40)\n"
            f"• {cmd('lucky7')} high — 8 through 12\n\n"
            "Coins only — nothing is real money."
        ),
        "rps": (
            "<b>Stone paper scissors</b>\n"
            f"Reply with {cmd('rps')} or {cmd('rps')} @username.\n"
            "After Accept, both tap in secret: Stone, Paper, or Scissors.\n"
            "Stone beats scissors, paper beats stone, scissors beat paper. "
            "Same is a tie.\n\n"
            "Winner +25, other +8, tie +10."
        ),
        "cricket": (
            "<b>Hand cricket</b>\n"
            "School rules, short match.\n\n"
            f"Reply {cmd('cricket')} or {cmd('cricket')} @username. They tap Accept.\n\n"
            "• Challenger bats first\n"
            "• Each ball both pick 1–6 (hidden until both lock)\n"
            "• Your pick locks — you cannot change it\n"
            "• Different numbers → batsman scores that many\n"
            "• Same number → OUT, innings over (even on ball 1)\n"
            "• Then the other player chases. Need first innings + 1 to win\n"
            "• If first innings is 0, the chase wins on the first scoring shot\n"
            "• Chase ends as soon as the target is passed — they do not keep batting\n\n"
            "Winner +40, other +12, tie +20 each."
        ),
        "four": (
            "<b>Four in a row</b>\n"
            "A real board on a card. Unique in this group — not a coin flip.\n\n"
            f"Reply {cmd('four')} or {cmd('four')} @username.\n\n"
            "• 7 columns, 6 rows. Tap 1–7 to drop\n"
            "• Challenger is gold and drops first\n"
            "• First to four in a line (row, column, or diagonal) wins\n"
            "• A drop cannot be undone\n\n"
            f"Board: {cmd('top')} four\n"
            "Winner +45, other +12, full board +20 each."
        ),
        "penalty": (
            "<b>Penalty duel</b>\n"
            "You are not both guessing the same thing. One shoots, one keeps.\n\n"
            f"Reply {cmd('penalty')} or {cmd('penalty')} @username.\n\n"
            "• Five kicks each, then sudden death if needed\n"
            "• Both pick Left / Centre / Right in secret\n"
            "• Same side = save. Different = goal\n"
            "• Then you swap roles\n"
            "• Match can end early if the other cannot catch up\n\n"
            f"Board: {cmd('top')} penalty"
        ),
        "vault": (
            "<b>The vault</b>\n"
            "One round. One pot. Trust or greed.\n\n"
            f"Reply {cmd('vault')} or {cmd('vault')} @username.\n\n"
            "• Both lock Share or Take (cannot change)\n"
            "• Both Share → you split (+24 each)\n"
            "• One Take → they take the pot (+48 / +8)\n"
            "• Both Take → empty (+4 each)\n\n"
            f"Board: {cmd('top')} vault"
        ),
        "top": (
            "<b>Leaderboard</b>\n"
            f"{cmd('top')} — all-time points\n"
            f"{cmd('top')} today — today, IST\n"
            f"{cmd('top')} week — this week\n"
            f"{cmd('top')} wins — most match wins\n\n"
            "<b>Per game</b> (wins / losses / draws)\n"
            f"{cmd('top')} cricket · {cmd('top')} four · {cmd('top')} penalty\n"
            f"{cmd('top')} vault · {cmd('top')} rps · {cmd('top')} dice · {cmd('top')} toss\n\n"
            f"{cmd('balance')} shows your line for each game."
        ),
    }


def _help_markup(key: str) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("Daily", callback_data="gm:h:daily"),
            InlineKeyboardButton("Toss", callback_data="gm:h:toss"),
            InlineKeyboardButton("Dice", callback_data="gm:h:dice"),
        ],
        [
            InlineKeyboardButton("Lucky 7", callback_data="gm:h:lucky7"),
            InlineKeyboardButton("Stone-paper", callback_data="gm:h:rps"),
            InlineKeyboardButton("Cricket", callback_data="gm:h:cricket"),
        ],
        [
            InlineKeyboardButton("Four", callback_data="gm:h:four"),
            InlineKeyboardButton("Penalty", callback_data="gm:h:penalty"),
            InlineKeyboardButton("Vault", callback_data="gm:h:vault"),
        ],
        [
            InlineKeyboardButton("Leaderboard", callback_data="gm:h:top"),
            InlineKeyboardButton("Overview", callback_data="gm:h:index"),
        ],
    ]
    return InlineKeyboardMarkup(rows)


async def cmd_gamehelp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    key = "index"
    if context.args:
        raw = context.args[0].lower()
        aliases = {
            "help": "index",
            "daily": "daily",
            "toss": "toss",
            "coin": "toss",
            "dice": "dice",
            "lucky7": "lucky7",
            "lucky": "lucky7",
            "7": "lucky7",
            "rps": "rps",
            "stone": "rps",
            "sps": "rps",
            "cricket": "cricket",
            "hand": "cricket",
            "four": "four",
            "connect4": "four",
            "c4": "four",
            "penalty": "penalty",
            "pk": "penalty",
            "vault": "vault",
            "heist": "vault",
            "top": "top",
            "board": "top",
        }
        key = aliases.get(raw, "index")
    pages = _help_pages()
    await update.effective_message.reply_html(
        pages.get(key, pages["index"]),
        reply_markup=_help_markup(key),
        disable_web_page_preview=True,
    )


async def _edit_help(query, key: str) -> None:
    pages = _help_pages()
    text = pages.get(key, pages["index"])
    try:
        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=_help_markup(key),
            disable_web_page_preview=True,
        )
    except BadRequest as exc:
        if "not modified" in str(exc).lower():
            await query.answer("This page is already open.")
            return
        await query.answer()
        return
    await query.answer()
