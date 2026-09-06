from __future__ import annotations

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
from bot.fun import resolve_member
from bot.moderation import format_user, mention, require_group

log = logging.getLogger(__name__)

_CHALLENGE_TTL = 120
_challenges: dict[str, dict] = {}

RPS = {"stone": "🪨 Stone", "paper": "📄 Paper", "scissors": "✂️ Scissors"}
RPS_WIN = {("stone", "scissors"), ("paper", "stone"), ("scissors", "paper")}


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


def _award(chat_id: int, user_id: int, points: int, *, win: bool | None = None) -> None:
    db.add_coins(chat_id, user_id, points)
    db.record_game(chat_id, user_id, points=points, win=win, day_key=_day_key(), week_key=_week_key())


def _new_id() -> str:
    return secrets.token_hex(4)


def _purge() -> None:
    now = time.time()
    dead = [k for k, v in _challenges.items() if v.get("expires", 0) < now]
    for k in dead:
        _challenges.pop(k, None)


def _pending_for(chat_id: int, user_id: int) -> str | None:
    _purge()
    for cid, ch in _challenges.items():
        if ch.get("chat_id") != chat_id:
            continue
        if user_id in (ch.get("a"), ch.get("b")) and ch.get("status") in {"open", "live"}:
            return cid
    return None


async def _need_rival(update: Update, context: ContextTypes.DEFAULT_TYPE) -> User | None:
    msg = update.effective_message
    actor = update.effective_user
    rival = await resolve_member(update, context)
    if not rival or not actor:
        await msg.reply_text(
            "Reply to a friend, or tag them.\n"
            f"Example: {cmd('cricket')} @username"
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
                InlineKeyboardButton("Haan, let's play", callback_data=f"gm:ok:{cid}"),
                InlineKeyboardButton("Nah", callback_data=f"gm:no:{cid}"),
            ]
        ]
    )


def _rps_markup(cid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🪨 Stone", callback_data=f"gm:r:{cid}:stone"),
                InlineKeyboardButton("📄 Paper", callback_data=f"gm:r:{cid}:paper"),
                InlineKeyboardButton("✂️ Scissors", callback_data=f"gm:r:{cid}:scissors"),
            ]
        ]
    )


def _hand_markup(cid: str) -> InlineKeyboardMarkup:
    row1 = [InlineKeyboardButton(str(n), callback_data=f"gm:c:{cid}:{n}") for n in range(1, 4)]
    row2 = [InlineKeyboardButton(str(n), callback_data=f"gm:c:{cid}:{n}") for n in range(4, 7)]
    return InlineKeyboardMarkup([row1, row2])


def _toss_markup(cid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Heads", callback_data=f"gm:t:{cid}:heads"),
                InlineKeyboardButton("Tails", callback_data=f"gm:t:{cid}:tails"),
            ]
        ]
    )


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
            f"Aaj ka bonus mil chuka hai. Kal wapas aana.\n"
            f"Streak: {streak} day(s). Coins: {coins}\n"
            f"Board: {cmd('top')}"
        )
        return
    streak = streak + 1 if last == _yesterday() else 1
    gain = 40 + 15 * min(streak - 1, 10)
    db.set_wallet(chat_id, user.id, coins + gain, streak, today)
    db.record_game(chat_id, user.id, points=gain, win=None, day_key=today, week_key=_week_key())
    await update.effective_message.reply_html(
        f"{mention(user)} claimed <b>+{gain}</b> coins. Streak <b>{streak}</b> 🔥\n"
        f"Balance: {coins + gain}. Play {cmd('toss')} {cmd('cricket')} {cmd('rps')} — "
        f"full guide {cmd('gamehelp')}"
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
    claimed = "haan" if last == _day_key() else f"nahi — {cmd('daily')}"
    await update.effective_message.reply_html(
        f"{mention(user)}\n"
        f"Coins: <b>{coins}</b>\n"
        f"Points: <b>{st['points']}</b> · today {st['day_points']}\n"
        f"Wins: {st['wins']} · Losses: {st['losses']}\n"
        f"Daily streak: {streak} · Today's bonus: {claimed}"
    )


async def cmd_top(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group(update):
        return
    args = [a.lower() for a in (context.args or [])]
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
            f"Board is empty. {cmd('daily')} then play {cmd('toss')} / {cmd('cricket')}."
        )
        return
    medals = ["🥇", "🥈", "🥉"]
    lines = [f"<b>Leaderboard — {escape(title)}</b>"]
    for i, (uid, score) in enumerate(rows, 1):
        who = await format_user(context, update.effective_chat.id, uid)
        prefix = medals[i - 1] if i <= 3 else f"{i}."
        lines.append(f"{prefix} {who} — <b>{score}</b>")
    lines.append(f"{cmd('top')} · {cmd('top')} today · {cmd('top')} week · {cmd('top')} wins")
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
                f"{mention(actor)} ne {mention(rival)} ko toss challenge diya.\n"
                "Dono Heads/Tails choose karo. Coin alag flip hoga."
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
    _award(update.effective_chat.id, actor.id, pts, win=win)
    face = "Heads (ashrafi side)" if coin == "heads" else "Tails"
    result = "jeet gaye ✨" if win else "thoda luck nahi tha"
    await msg.reply_html(
        f"🪙 {face}. You called <b>{choice}</b> — {result}. +{pts} coins"
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
                f"{mention(actor)} vs {mention(rival)} — dice. "
                "Accept karo, dono ke liye 🎲 roll hoga. Higher number jeetega."
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
        pts, note, win = 25, "Chhakka! 🔥", True
    elif value >= 5:
        pts, note, win = 15, "Solid roll.", True
    elif value == 1:
        pts, note, win = 5, "Ek. Phir try karo.", None
    else:
        pts, note, win = 8, "Okay roll.", None
    _award(update.effective_chat.id, actor.id, pts, win=win)
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
    _award(update.effective_chat.id, user.id, pts, win=win)
    label = "LOW (2–6)" if actual == "low" else ("HIGH (8–12)" if actual == "high" else "LUCKY 7")
    await update.effective_message.reply_html(
        f"🎲 {a} + {b} = <b>{total}</b> → {label}\n"
        f"You called <b>{side}</b>. {'Jeet! ' if win else 'Miss. '}+{pts} coins"
    )


async def cmd_rps(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group(update):
        return
    rival = await _need_rival(update, context)
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
            "Accept ke baad dono chhupke choose karo. Same = tie."
        ),
    )


async def cmd_cricket(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group(update):
        return
    rival = await _need_rival(update, context)
    if not rival:
        return
    actor = update.effective_user
    await _open_challenge(
        update,
        context,
        rival,
        kind="cricket",
        text=(
            f"{mention(actor)} vs {mention(rival)} — <b>Hand cricket</b> (school wala).\n"
            "Har ball 1–6 choose. Same number = OUT. Warna batsman ke runs lagte hain.\n"
            "Ek over (6 balls) each. Zyada score jeetega."
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
            "Ek match pehle se chal raha hai. Pehle usko khatam / cancel karo."
        )
        return
    cid = _new_id()
    _challenges[cid] = {
        "kind": kind,
        "chat_id": chat_id,
        "a": actor.id,
        "b": rival.id,
        "status": "open",
        "expires": time.time() + _CHALLENGE_TTL,
        "picks": {},
        "score_a": 0,
        "score_b": 0,
        "balls": 0,
        "innings": 1,
        "batter": actor.id,
    }
    await update.effective_message.reply_html(
        text + "\n<i>2 minute to accept.</i>",
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
    ch = _challenges.get(cid)
    user = update.effective_user
    if not ch or not user:
        await query.answer("This match expired. Start a new one.", show_alert=True)
        return
    if time.time() > ch["expires"]:
        _challenges.pop(cid, None)
        await query.answer("Time up. Start a new match.", show_alert=True)
        return
    action = parts[1]
    if action == "ok":
        await _on_accept(query, context, cid, ch, user)
        return
    if action == "no":
        if user.id not in (ch["a"], ch["b"]):
            await query.answer("Yeh tumhara match nahi hai.", show_alert=True)
            return
        _challenges.pop(cid, None)
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
    await query.answer()


async def _on_accept(query, context, cid: str, ch: dict, user: User) -> None:
    if user.id != ch["b"]:
        await query.answer("Sirf jis ko challenge mila hai woh accept kare.", show_alert=True)
        return
    ch["status"] = "live"
    ch["expires"] = time.time() + 300
    kind = ch["kind"]
    if kind == "dice":
        try:
            await _run_dice_duel(query, context, ch)
        finally:
            _challenges.pop(cid, None)
        return
    if kind == "toss":
        await query.answer("Toss live. Heads ya Tails choose karo.")
        await query.edit_message_text(
            "Dono: Heads ya Tails dabao. Coin alag flip hoga.",
            reply_markup=_toss_markup(cid),
        )
        return
    if kind == "rps":
        await query.answer("Choose stone / paper / scissors — secretly.")
        await query.edit_message_text(
            "Dono apna haath choose karo. Dusre ko nahi dikhega jab tak dono tap na karein.",
            reply_markup=_rps_markup(cid),
        )
        return
    if kind == "cricket":
        await query.answer("Hand cricket start. Batter first over.")
        board = await _cricket_scoreboard(context, ch)
        await _edit_html(query, board + "\nDono 1–6 choose karo.", _hand_markup(cid))
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


async def _cricket_scoreboard(context, ch: dict) -> str:
    a_m = await format_user(context, ch["chat_id"], ch["a"])
    b_m = await format_user(context, ch["chat_id"], ch["b"])
    bat_m = a_m if ch["batter"] == ch["a"] else b_m
    who = "first innings" if ch["innings"] == 1 else "second innings"
    return (
        f"<b>Hand cricket</b> — {who}, ball {ch['balls'] + 1}/6\n"
        f"Batting: {bat_m}\n"
        f"Score: {a_m} {ch['score_a']}  |  {b_m} {ch['score_b']}\n"
        "Same number = OUT (over khatam)."
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
        _award(chat_id, a, 30, win=True)
        _award(chat_id, b, 8, win=False)
        result = f"<code>{a}</code> jeeta ({v1} vs {v2}). +30 / +8"
    elif v2 > v1:
        _award(chat_id, b, 30, win=True)
        _award(chat_id, a, 8, win=False)
        result = f"<code>{b}</code> jeeta ({v2} vs {v1}). +30 / +8"
    else:
        _award(chat_id, a, 12, win=None)
        _award(chat_id, b, 12, win=None)
        result = f"Tie {v1}–{v2}. Dono +12"
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
        await query.answer("Yeh tumhara toss nahi.", show_alert=True)
        return
    if str(user.id) in ch["picks"]:
        await query.answer("Already chosen.")
        return
    ch["picks"][str(user.id)] = face
    ch["expires"] = time.time() + 300
    await query.answer(f"You called {face}.")
    if len(ch["picks"]) < 2:
        return
    coin = random.choice(["heads", "tails"])
    a, b = str(ch["a"]), str(ch["b"])
    aw, bw = ch["picks"].get(a) == coin, ch["picks"].get(b) == coin
    chat_id = ch["chat_id"]
    if aw and not bw:
        _award(chat_id, ch["a"], 22, win=True)
        _award(chat_id, ch["b"], 6, win=False)
        extra = "Challenger called it right."
    elif bw and not aw:
        _award(chat_id, ch["b"], 22, win=True)
        _award(chat_id, ch["a"], 6, win=False)
        extra = "Opponent called it right."
    elif aw and bw:
        _award(chat_id, ch["a"], 12, win=None)
        _award(chat_id, ch["b"], 12, win=None)
        extra = "Dono ne same call — split."
    else:
        _award(chat_id, ch["a"], 6, win=False)
        _award(chat_id, ch["b"], 6, win=False)
        extra = "Dono galat. Coin kuch aur tha."
    _challenges.pop(cid, None)
    await _edit_html(
        query,
        f"🪙 Coin: <b>{coin}</b>\n"
        f"Calls: {ch['picks'].get(a)} vs {ch['picks'].get(b)}\n{extra}",
    )


async def _on_rps_pick(query, context, cid, ch, user, hand: str) -> None:
    if user.id not in (ch["a"], ch["b"]) or hand not in RPS:
        await query.answer("Yeh match tumhara nahi.", show_alert=True)
        return
    if str(user.id) in ch["picks"]:
        await query.answer("Already locked.")
        return
    ch["picks"][str(user.id)] = hand
    ch["expires"] = time.time() + 300
    await query.answer("Locked.")
    if len(ch["picks"]) < 2:
        return
    pa, pb = ch["picks"][str(ch["a"])], ch["picks"][str(ch["b"])]
    chat_id = ch["chat_id"]
    a_m = await format_user(context, chat_id, ch["a"])
    b_m = await format_user(context, chat_id, ch["b"])
    if pa == pb:
        _award(chat_id, ch["a"], 10, win=None)
        _award(chat_id, ch["b"], 10, win=None)
        outcome = "Tie. Dono +10"
    elif (pa, pb) in RPS_WIN:
        _award(chat_id, ch["a"], 25, win=True)
        _award(chat_id, ch["b"], 8, win=False)
        outcome = f"{a_m} jeeta. +25 / +8"
    else:
        _award(chat_id, ch["b"], 25, win=True)
        _award(chat_id, ch["a"], 8, win=False)
        outcome = f"{b_m} jeeta. +25 / +8"
    _challenges.pop(cid, None)
    await _edit_html(query, f"{a_m}: {RPS[pa]}\n{b_m}: {RPS[pb]}\n\n{outcome}")


async def _on_cricket_pick(query, context, cid, ch, user, n: int) -> None:
    if user.id not in (ch["a"], ch["b"]) or n not in range(1, 7):
        await query.answer("Yeh match tumhara nahi.", show_alert=True)
        return
    if ch["status"] != "live":
        await query.answer("Pehle accept karo.", show_alert=True)
        return
    if str(user.id) in ch["picks"]:
        await query.answer("Is ball pe already choose kiya.")
        return
    ch["picks"][str(user.id)] = n
    ch["expires"] = time.time() + 300
    await query.answer(f"You played {n}.")
    if len(ch["picks"]) < 2:
        return
    pa, pb = ch["picks"][str(ch["a"])], ch["picks"][str(ch["b"])]
    ch["picks"] = {}
    batter = ch["batter"]
    bat_n = pa if batter == ch["a"] else pb
    bowl_n = pb if batter == ch["a"] else pa
    out = bat_n == bowl_n
    if out:
        ch["balls"] = 6
    else:
        key = "score_a" if batter == ch["a"] else "score_b"
        ch[key] += bat_n
        ch["balls"] += 1
    chat_id = ch["chat_id"]
    ball_txt = f"Ball: {pa} vs {pb}. "
    if out:
        ball_txt += f"OUT! Same {bat_n}."
    else:
        ball_txt += f"+{bat_n} runs."

    if ch["balls"] >= 6:
        if ch["innings"] == 1:
            ch["innings"] = 2
            ch["balls"] = 0
            ch["batter"] = ch["b"] if batter == ch["a"] else ch["a"]
            board = await _cricket_scoreboard(context, ch)
            await _edit_html(
                query,
                ball_txt + "\n\n" + board + "\nInnings change. Dono 1–6.",
                _hand_markup(cid),
            )
            return
        sa, sb = ch["score_a"], ch["score_b"]
        a_m = await format_user(context, chat_id, ch["a"])
        b_m = await format_user(context, chat_id, ch["b"])
        if sa > sb:
            _award(chat_id, ch["a"], 40, win=True)
            _award(chat_id, ch["b"], 12, win=False)
            winner = f"{a_m} jeeta"
        elif sb > sa:
            _award(chat_id, ch["b"], 40, win=True)
            _award(chat_id, ch["a"], 12, win=False)
            winner = f"{b_m} jeeta"
        else:
            _award(chat_id, ch["a"], 20, win=None)
            _award(chat_id, ch["b"], 20, win=None)
            winner = "Tie match — dono +20"
        _challenges.pop(cid, None)
        await _edit_html(
            query,
            f"{ball_txt}\n\n<b>Final:</b> {a_m} {sa}  —  {b_m} {sb}\n{winner}",
        )
        return
    board = await _cricket_scoreboard(context, ch)
    await _edit_html(query, ball_txt + "\n\n" + board, _hand_markup(cid))


def _help_pages() -> dict[str, str]:
    return {
        "index": (
            "<b>Games — kya khelein?</b>\n"
            "Saare games is group ke andar hain. Coins sirf mazaa ke liye hain, "
            "paise nahi. Har jeet leaderboard pe points deti hai (Indian time IST).\n\n"
            f"1. Roz {cmd('daily')} — streak bonus. Kal bhi aana.\n"
            f"2. {cmd('balance')} — tumhare coins / wins\n"
            f"3. {cmd('top')} — aaj / week / all-time board\n\n"
            "<b>Akele</b>\n"
            f"• {cmd('toss')} heads|tails — cricket wala coin toss\n"
            f"• {cmd('dice')} — Telegram dice, 6 pe extra\n"
            f"• {cmd('lucky7')} low|7|high — do dice, 7 up / 7 down (fun coins only)\n\n"
            "<b>Doston ke saath</b> (reply karo ya @tag)\n"
            f"• {cmd('cricket')} — hand cricket, school rules\n"
            f"• {cmd('rps')} — stone paper scissors\n"
            f"• {cmd('dice')} @user — dono ke dice, bada number jeete\n"
            f"• {cmd('toss')} @user — dono call, phir coin\n\n"
            "Buttons se pages kholo, ya " + cmd("gamehelp") + " cricket"
        ),
        "daily": (
            "<b>Daily bonus</b>\n"
            f"Har din ek baar {cmd('daily')} — India midnight (IST) ke baad reset.\n\n"
            "Pehla din 40 coins. Lagataar roz claim = streak. "
            "Har extra din +15, max streak bonus 10 din tak.\n\n"
            "Bhool gaye ek din? Streak 1 se start. "
            "Yahi reason hai roz GC pe aane ka.\n\n"
            f"{cmd('top')} today dikhata hai aaj ke points (daily + games)."
        ),
        "toss": (
            "<b>Toss</b>\n"
            "Cricket match se pehle wala coin. Heads ya tails.\n\n"
            f"<code>{cmd('toss')} heads</code>\n"
            f"<code>{cmd('toss')} tails</code>\n"
            "Sahi call = +20, galat = +5 (participation).\n\n"
            f"Dost se: unke message pe reply karke {cmd('toss')} "
            "ya {cmd('toss')} @username. Dono Heads/Tails dabate hain, "
            "phir bot coin flip karta hai. Same call ho to split."
        ),
        "dice": (
            "<b>Dice</b>\n"
            f"{cmd('dice')} bhejta hai asli Telegram 🎲.\n"
            "6 = chhakka (+25), 5 = +15, chhota roll thode coins.\n\n"
            f"Dost se: {cmd('dice')} @user → woh Accept kare → "
            "bot dono ke liye dice roll karega. Jiska number bada, woh jeeta (+30).\n\n"
            "Ludo / cricket dice wahi feel."
        ),
        "lucky7": (
            "<b>Lucky 7</b> (7 up / 7 down)\n"
            "Do dice (2 se 12). Tum pehle call karte ho:\n\n"
            f"• {cmd('lucky7')} low — 2,3,4,5,6\n"
            f"• {cmd('lucky7')} 7 — sirf saat (zyada payout +40)\n"
            f"• {cmd('lucky7')} high — 8 se 12\n\n"
            "Same idea as 7 up / 7 down at a mela stall. Here it is only coins — no money."
        ),
        "rps": (
            "<b>Stone paper scissors</b>\n"
            "India mein school / decision ke liye yahi.\n\n"
            f"Reply karke {cmd('rps')} ya {cmd('rps')} @username.\n"
            "Accept ke baad dono secretly tap: Stone, Paper, Scissors.\n"
            "Stone breaks scissors, paper covers stone, scissors cut paper. "
            "Same = tie.\n\n"
            "Winner +25, loser +8, tie +10."
        ),
        "cricket": (
            "<b>Hand cricket</b> — sabse mazedaar yahan\n"
            "Bachpan wala: dono haath pe 1 se 6 ungli. Same = OUT.\n\n"
            f"<b>Kaise start:</b> kisi ko reply {cmd('cricket')} "
            f"ya {cmd('cricket')} @username. Woh Accept dabaye.\n\n"
            "<b>Rules</b>\n"
            "• Har ball dono 1–6 choose (secret jab tak dono tap na karein)\n"
            "• Alag numbers → batsman ke utne runs\n"
            "• Same number → OUT, uski innings khatam\n"
            "• Ek over = 6 balls (ya pehle OUT)\n"
            "• Phir dusra player bat karega 6 balls\n"
            "• Jiska score zyada, match uska. Tie = dono ko points\n\n"
            "Winner +40. Short match, group mein spam kam."
        ),
        "top": (
            "<b>Leaderboard</b>\n"
            f"{cmd('top')} — all-time points\n"
            f"{cmd('top')} today — aaj IST\n"
            f"{cmd('top')} week — is hafte\n"
            f"{cmd('top')} wins — sabse zyada matches jeete\n\n"
            f"{cmd('balance')} apna score. Roz {cmd('daily')} + khel ke board chadho."
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
            await query.answer("Yahi page hai.")
            return
        await query.answer()
        return
    await query.answer()
