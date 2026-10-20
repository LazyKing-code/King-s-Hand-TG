"""Telegram lobby for team hand cricket. Rules live in cricket_logic."""

from __future__ import annotations

import time
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot import cricket_logic as logic
from bot import db
from bot.common import (
    args_of,
    can_manage,
    clean_name,
    edit_html,
    forget_lock,
    lock_for,
    mention,
    new_id,
    require_group,
    start_lock,
)

KIND = "cricket"


def _lobby_deadline(session: dict, now: float) -> float:
    return min(session["created_at"] + logic.LOBBY_MAX, now + logic.LOBBY_TTL)


def _names(players: list[dict]) -> str:
    if not players:
        return "—"
    return "\n".join(f"• {escape(str(person.get('name') or 'player'))}" for person in players)


def _score_line(name: str, score: int, wickets: int) -> str:
    return f"{escape(name)}: <b>{score}/{wickets}</b>"


def _pick_line(game: dict) -> str:
    batter = logic.current_batter(game)
    bowler = logic.current_bowler(game)
    if not batter or not bowler:
        return "Waiting for the next ball."
    picks = game.get("picks") or {}
    bat_locked = str(int(batter["id"])) in picks
    bowl_locked = str(int(bowler["id"])) in picks
    if bat_locked and not bowl_locked:
        return f"{escape(str(batter.get('name') or 'Batter'))} locked in. Waiting for the bowler."
    if bowl_locked and not bat_locked:
        return f"{escape(str(bowler.get('name') or 'Bowler'))} locked in. Waiting for the batter."
    return "Batter and bowler: pick 1–6. The same number is out."


def render(session_id: str, game: dict, *, live: bool) -> tuple[str, InlineKeyboardMarkup | None]:
    starter_id = int(game["starter_id"])
    overs = int(game.get("overs") or 1)
    lines = [
        f"<b>Gully cricket</b> — {overs} over{'s' if overs != 1 else ''}",
        f"Host: {mention(starter_id, str(game.get('starter_name') or 'host'))}",
    ]
    if not live:
        lines.append("Team A bats first. A wicket brings the next batter.")
        lines.append("")
        lines.append("<b>Team A</b>")
        lines.append(_names(logic.team(game, "a")))
        lines.append("<b>Team B</b>")
        lines.append(_names(logic.team(game, "b")))
        lines.append("")
        lines.append("Join a side. Both sides need a player, then the host taps Begin.")
        markup = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Join A", callback_data=f"ck:{session_id}:a"),
                InlineKeyboardButton("Join B", callback_data=f"ck:{session_id}:b"),
            ],
            [
                InlineKeyboardButton("1 over", callback_data=f"ck:{session_id}:1"),
                InlineKeyboardButton("2 overs", callback_data=f"ck:{session_id}:2"),
                InlineKeyboardButton("3 overs", callback_data=f"ck:{session_id}:3"),
            ],
            [
                InlineKeyboardButton("Begin", callback_data=f"ck:{session_id}:go"),
                InlineKeyboardButton("Cancel", callback_data=f"ck:{session_id}:x"),
            ],
        ])
        return "\n".join(lines), markup

    side = "a" if game.get("batting") != "b" else "b"
    balls = int(game.get("balls") or 0)
    lines.append(f"Innings {int(game.get('innings') or 1)} · {logic.over_label(balls)}")
    lines.append("")
    lines.append(_score_line("Team A", int(game.get("score_a") or 0), len(game.get("out_a") or [])))
    lines.append(_score_line("Team B", int(game.get("score_b") or 0), len(game.get("out_b") or [])))
    if int(game.get("innings") or 1) == 2 and game.get("first_innings") is not None:
        target = int(game["first_innings"]) + 1
        chase = int(game.get("score_b") or 0) if side == "b" else int(game.get("score_a") or 0)
        lines.append(f"Target {target}. Need {max(0, target - chase)}.")
    batter = logic.current_batter(game)
    bowler = logic.current_bowler(game)
    lines.append("")
    if batter:
        lines.append(f"Batting: {mention(int(batter['id']), str(batter.get('name') or 'batter'))}")
    if bowler:
        lines.append(f"Bowling: {mention(int(bowler['id']), str(bowler.get('name') or 'bowler'))}")
    if game.get("last"):
        lines.append(f"Last: {escape(str(game['last']))}")
    lines.append("")
    lines.append(_pick_line(game))
    row1 = [
        InlineKeyboardButton(str(n), callback_data=f"ck:{session_id}:p{n}")
        for n in range(1, 4)
    ]
    row2 = [
        InlineKeyboardButton(str(n), callback_data=f"ck:{session_id}:p{n}")
        for n in range(4, 7)
    ]
    markup = InlineKeyboardMarkup([
        row1,
        row2,
        [InlineKeyboardButton("Cancel", callback_data=f"ck:{session_id}:x")],
    ])
    return "\n".join(lines), markup


def _finished_text(game: dict) -> str:
    overs = int(game.get("overs") or 1)
    lines = [
        f"<b>Gully cricket finished</b> — {overs} over{'s' if overs != 1 else ''}",
        _score_line("Team A", int(game.get("score_a") or 0), len(game.get("out_a") or [])),
        _score_line("Team B", int(game.get("score_b") or 0), len(game.get("out_b") or [])),
        "",
    ]
    side = logic.winner_side(game)
    if side is None:
        lines.append("Tie.")
    else:
        lines.append(f"Team {side.upper()} wins {logic.result_phrase(game)}.")
    if game.get("last"):
        lines.append(escape(str(game["last"])))
    return "\n".join(lines)


async def _publish(bot, session: dict, text: str, markup) -> None:
    message_id = await edit_html(bot, session["chat_id"], session["message_id"], text, markup)
    if message_id and message_id != session["message_id"]:
        db.set_message(session["id"], message_id)
        session["message_id"] = message_id


async def _close(bot, session: dict, status: str, text: str) -> None:
    db.save_session(session["id"], session["payload"], status=status, deadline=time.time())
    session["status"] = status
    await _publish(bot, session, text, None)
    forget_lock(session["id"])


async def _show_live(bot, session: dict, game: dict, *, deadline: float) -> None:
    db.save_session(session["id"], game, status="live", deadline=deadline)
    session["status"] = "live"
    text, markup = render(session["id"], game, live=True)
    await _publish(bot, session, text, markup)


def _win_rows(session: dict) -> list[dict]:
    game = session["payload"]
    side = logic.winner_side(game)
    if side is None:
        return []
    rows = []
    for person in logic.team(game, side):
        rows.append({
            "chat_id": session["chat_id"],
            "kind": KIND,
            "user_id": int(person["id"]),
            "user_name": str(person.get("name") or person["id"]),
            "result": "win",
        })
    return rows


async def _finish(bot, session: dict) -> None:
    db.add_results(_win_rows(session))
    await _close(bot, session, "finished", _finished_text(session["payload"]))


async def _after_ball(bot, session: dict, game: dict, outcome: str) -> None:
    if outcome == "finished":
        await _finish(bot, session)
        return
    await _show_live(bot, session, game, deadline=time.time() + logic.IDLE_TTL)


async def cmd_cricket(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return
    if not require_group(update):
        await msg.reply_text("Add me to a group, then send /cricket there.")
        return
    if args_of(update, context)[:1] == ["cancel"]:
        await _cancel_command(update, context)
        return

    chat_id = update.effective_chat.id
    async with start_lock(chat_id, KIND):
        if db.active_session(chat_id, KIND):
            await msg.reply_text(
                "A cricket match is already open in this chat. Finish it or send /cricket cancel."
            )
            return
        now = time.time()
        payload = logic.cricket_new(user.id, clean_name(user))
        session_id = new_id()
        db.create_session(
            session_id,
            chat_id,
            KIND,
            user.id,
            payload,
            deadline=now + logic.LOBBY_TTL,
        )
    text, markup = render(session_id, payload, live=False)
    sent = await msg.reply_html(text, reply_markup=markup, disable_web_page_preview=True)
    db.set_message(session_id, sent.message_id)


async def _cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not msg or not user or not chat:
        return
    session = db.active_session(chat.id, KIND)
    if not session:
        await msg.reply_text("No cricket match is open in this chat.")
        return
    if not await can_manage(context.bot, chat.id, user.id, session["starter_id"]):
        await msg.reply_text("Only the host or a group admin can cancel the match.")
        return
    async with lock_for(session["id"]):
        fresh = db.load_session(session["id"])
        if not fresh or fresh["status"] not in ("lobby", "live"):
            await msg.reply_text("That match is already over.")
            return
        await _close(context.bot, fresh, "cancelled", "<b>Gully cricket</b>\nCancelled.")


async def on_cricket_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data or not query.from_user:
        return
    parts = query.data.split(":")
    if len(parts) != 3 or parts[0] != "ck":
        await query.answer()
        return
    session_id, action = parts[1], parts[2]
    async with lock_for(session_id):
        session = db.load_session(session_id)
        if not session or session["kind"] != KIND or session["status"] not in ("lobby", "live"):
            await query.answer("This match is over.", show_alert=True)
            return
        await _action(query, context, session, action)


async def _action(query, context, session: dict, action: str) -> None:
    user = query.from_user
    game = session["payload"]
    if action in ("a", "b"):
        await _join(query, context, session, game, user, action)
        return
    if action in ("1", "2", "3"):
        await _overs(query, context, session, game, user, int(action))
        return
    if action == "go":
        await _begin(query, context, session, game, user)
        return
    if action.startswith("p") and action[1:].isdigit():
        await _pick(query, context, session, game, user, int(action[1:]))
        return
    if action == "x":
        if not await can_manage(context.bot, session["chat_id"], user.id, session["starter_id"]):
            await query.answer("Only the host or a group admin can cancel.", show_alert=True)
            return
        await query.answer("Cancelled.")
        await _close(context.bot, session, "cancelled", "<b>Gully cricket</b>\nCancelled.")
        return
    await query.answer()


async def _join(query, context, session, game, user, side: str) -> None:
    if session["status"] != "lobby":
        await query.answer("The match has already started.", show_alert=True)
        return
    result = logic.join(game, user.id, clean_name(user), side)
    if result == "full":
        await query.answer("That side is full.", show_alert=True)
        return
    if result != "ok":
        await query.answer("Could not join.", show_alert=True)
        return
    now = time.time()
    db.save_session(session["id"], game, status="lobby", deadline=_lobby_deadline(session, now))
    text, markup = render(session["id"], game, live=False)
    await _publish(context.bot, session, text, markup)
    label = "A" if side == "a" else "B"
    await query.answer(f"You're on Team {label}.")


async def _overs(query, context, session, game, user, overs: int) -> None:
    if session["status"] != "lobby":
        await query.answer("Overs are already set.", show_alert=True)
        return
    if int(user.id) != int(game["starter_id"]):
        await query.answer("Only the host picks the overs.", show_alert=True)
        return
    if logic.set_overs(game, overs) != "ok":
        await query.answer("Pick 1, 2, or 3 overs.", show_alert=True)
        return
    now = time.time()
    db.save_session(session["id"], game, status="lobby", deadline=_lobby_deadline(session, now))
    text, markup = render(session["id"], game, live=False)
    await _publish(context.bot, session, text, markup)
    await query.answer(f"{overs} over{'s' if overs != 1 else ''}.")


async def _begin(query, context, session, game, user) -> None:
    if int(user.id) != int(game["starter_id"]):
        await query.answer("Only the host can begin.", show_alert=True)
        return
    if session["status"] != "lobby":
        await query.answer("This match has already started.")
        return
    if logic.begin(game) != "ok":
        await query.answer("Both sides need at least one player.", show_alert=True)
        return
    await query.answer("Play.")
    await _show_live(context.bot, session, game, deadline=time.time() + logic.IDLE_TTL)


async def _pick(query, context, session, game, user, number: int) -> None:
    if session["status"] != "live":
        await query.answer("The match has not started.", show_alert=True)
        return
    result = logic.record_pick(game, user.id, number)
    if result == "spectator":
        await query.answer("Only the current batter and bowler pick this ball.", show_alert=True)
        return
    if result == "bad":
        await query.answer("That pick does not count.", show_alert=True)
        return
    if result == "dup":
        await query.answer("You already locked a number.")
        return
    if result == "wait":
        db.save_session(
            session["id"],
            game,
            status="live",
            deadline=time.time() + logic.IDLE_TTL,
        )
        text, markup = render(session["id"], game, live=True)
        await _publish(context.bot, session, text, markup)
        await query.answer("Locked in.")
        return
    outcome = logic.apply_ball(game)
    last = str(game.get("last") or "Ball bowled.")
    await query.answer(last[:180])
    await _after_ball(context.bot, session, game, outcome)


async def on_due(bot, session: dict) -> None:
    """Called from the sweep while the session lock is held."""
    if session["status"] == "lobby":
        await _close(bot, session, "expired", "<b>Gully cricket</b>\nLobby closed — the host did not begin.")
        return
    game = session["payload"]
    if logic.both_picked(game):
        outcome = logic.apply_ball(game)
        await _after_ball(bot, session, game, outcome)
        return
    outcome = logic.idle(game)
    if outcome == "skip":
        await _show_live(bot, session, game, deadline=time.time() + logic.IDLE_TTL)
        return
    if outcome == "expire":
        await _close(
            bot,
            session,
            "expired",
            "<b>Gully cricket</b>\nMatch closed — a player did not pick.",
        )
        return
    db.save_session(session["id"], game, status="live", deadline=time.time() + logic.IDLE_TTL)
