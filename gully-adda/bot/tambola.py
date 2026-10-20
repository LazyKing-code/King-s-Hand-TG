"""Telegram lobby for Tambola. Rules live in tambola_logic."""

from __future__ import annotations

import logging
import time
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import Forbidden, TelegramError
from telegram.ext import ContextTypes

from bot import db, tambola_logic as logic
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

log = logging.getLogger(__name__)

KIND = "tambola"


def _lobby_deadline(session: dict, now: float) -> float:
    return min(session["created_at"] + logic.LOBBY_MAX, now + logic.LOBBY_TTL)


def _winner_line(label: str, winner: dict | None, *, closed: bool = False) -> str:
    if not winner:
        return f"{label}: {'unclaimed' if closed else 'open'}"
    return f"{label}: {escape(str(winner.get('name') or 'player'))}"


def _called_block(called: list[int]) -> str:
    if not called:
        return "No numbers yet."
    cells = [f"{int(n):2d}" for n in called]
    rows = [" ".join(cells[i : i + 10]) for i in range(0, len(cells), 10)]
    latest = int(called[-1])
    body = "\n".join(rows)
    return f"Latest: <b>{latest}</b>  ·  {len(called)}/90\n<pre>{escape(body)}</pre>"


def render(
    session_id: str,
    game: dict,
    *,
    house_closed: bool = False,
) -> tuple[str, InlineKeyboardMarkup | None]:
    starter_id = int(game["starter_id"])
    players = game.get("players") or {}
    lines = [
        "<b>Tambola</b>",
        f"Host: {mention(starter_id, str(game.get('starter_name') or 'host'))}",
        f"Tickets: {len(players)}/{logic.MAX_TICKETS}",
    ]
    if not game.get("started"):
        if players:
            lines.append("")
            for person in players.values():
                lines.append(f"• {escape(str(person.get('name') or 'player'))}")
        lines.append("")
        lines.append("Tap <b>Take ticket</b>. The card is sent in a private chat.")
        lines.append(f"The host taps <b>Begin</b> once at least {logic.MIN_PLAYERS} people have a ticket.")
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("Take ticket", callback_data=f"tb:{session_id}:j")],
            [
                InlineKeyboardButton("Begin", callback_data=f"tb:{session_id}:go"),
                InlineKeyboardButton("Cancel", callback_data=f"tb:{session_id}:x"),
            ],
        ])
        return "\n".join(lines), markup

    lines.append("")
    lines.append(_called_block(list(game.get("called") or [])))
    lines.append(_winner_line("Line", game.get("line_winner"), closed=house_closed))
    lines.append(_winner_line("Full house", game.get("full_winner"), closed=house_closed))
    if not house_closed and game.get("claim_until"):
        lines.append("")
        lines.append("All 90 numbers are out. Claim now — the house closes shortly.")
    elif not house_closed and (not game.get("line_winner") or not game.get("full_winner")):
        lines.append("")
        lines.append("The host can tap <b>Call</b> to draw the next number sooner.")
    if house_closed:
        return "\n".join(lines), None
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("Call", callback_data=f"tb:{session_id}:c")],
        [
            InlineKeyboardButton("Line", callback_data=f"tb:{session_id}:ln"),
            InlineKeyboardButton("Full house", callback_data=f"tb:{session_id}:fh"),
        ],
        [InlineKeyboardButton("Cancel", callback_data=f"tb:{session_id}:x")],
    ])
    return "\n".join(lines), markup


def _finished_text(game: dict) -> str:
    text, _markup = render("done", {**game, "started": True}, house_closed=True)
    return text + "\n\n<b>House closed.</b>"


def _ticket_text(ticket: list) -> str:
    return (
        "<b>Your Tambola ticket</b>\n"
        "A dot is an empty square. Line and Full house are claimed on the group message.\n"
        f"<pre>{escape(logic.format_ticket(ticket))}</pre>"
    )


async def _send_ticket(bot, user_id: int, ticket: list) -> None:
    await bot.send_message(
        chat_id=user_id,
        text=_ticket_text(ticket),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


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


def _result_rows(session: dict) -> list[dict]:
    game = session["payload"]
    rows = []
    for key, result in (("line_winner", "line"), ("full_winner", "full")):
        winner = game.get(key)
        if not winner:
            continue
        rows.append({
            "chat_id": session["chat_id"],
            "kind": KIND,
            "user_id": int(winner["id"]),
            "user_name": str(winner.get("name") or winner["id"]),
            "result": result,
        })
    return rows


async def _finish(bot, session: dict) -> None:
    db.add_results(_result_rows(session))
    text = _finished_text(session["payload"])
    await _close(bot, session, "finished", text)


async def cmd_tambola(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return
    if not require_group(update):
        await msg.reply_text("Add me to a group, then send /tambola there.")
        return
    if args_of(update, context)[:1] == ["cancel"]:
        await _cancel_command(update, context)
        return

    chat_id = update.effective_chat.id
    async with start_lock(chat_id, KIND):
        if db.active_session(chat_id, KIND):
            await msg.reply_text("A Tambola game is already open in this chat. Finish it or send /tambola cancel.")
            return
        now = time.time()
        payload = logic.new_game(user.id, clean_name(user))
        session_id = new_id()
        db.create_session(
            session_id,
            chat_id,
            KIND,
            user.id,
            payload,
            deadline=now + logic.LOBBY_TTL,
        )
    text, markup = render(session_id, payload)
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
        await msg.reply_text("No Tambola game is open in this chat.")
        return
    if not await can_manage(context.bot, chat.id, user.id, session["starter_id"]):
        await msg.reply_text("Only the host or a group admin can cancel Tambola.")
        return
    async with lock_for(session["id"]):
        fresh = db.load_session(session["id"])
        if not fresh or fresh["status"] not in ("lobby", "live"):
            await msg.reply_text("That Tambola game is already over.")
            return
        await _close(context.bot, fresh, "cancelled", "<b>Tambola</b>\nCancelled.")


async def on_tambola_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data or not query.from_user:
        return
    parts = query.data.split(":")
    if len(parts) != 3 or parts[0] != "tb":
        await query.answer()
        return
    session_id, action = parts[1], parts[2]
    async with lock_for(session_id):
        session = db.load_session(session_id)
        if not session or session["kind"] != KIND or session["status"] not in ("lobby", "live"):
            await query.answer("This Tambola game is over.", show_alert=True)
            return
        await _action(query, context, session, action)


async def _action(query, context, session: dict, action: str) -> None:
    user = query.from_user
    game = session["payload"]
    if action == "j":
        await _take_ticket(query, context, session, game, user)
        return
    if action == "go":
        await _begin(query, context, session, game, user)
        return
    if action == "c":
        await _manual_call(query, context, session, game, user)
        return
    if action == "ln":
        await _claim(query, context, session, game, user, "line")
        return
    if action == "fh":
        await _claim(query, context, session, game, user, "full")
        return
    if action == "x":
        if not await can_manage(context.bot, session["chat_id"], user.id, session["starter_id"]):
            await query.answer("Only the host or a group admin can cancel.", show_alert=True)
            return
        await query.answer("Cancelled.")
        await _close(context.bot, session, "cancelled", "<b>Tambola</b>\nCancelled.")
        return
    await query.answer()


async def _take_ticket(query, context, session, game, user) -> None:
    if game.get("started") or session["status"] != "lobby":
        await query.answer("Tickets are closed. This game has already started.", show_alert=True)
        return
    existing = logic.player(game, user.id)
    if existing:
        try:
            await _send_ticket(context.bot, user.id, existing["ticket"])
        except Forbidden:
            await query.answer(
                "Open a private chat with me and tap Start, then take the ticket again.",
                show_alert=True,
            )
            return
        except TelegramError:
            log.exception("resend ticket failed")
            await query.answer("Could not send the ticket. Try again.", show_alert=True)
            return
        await query.answer("Sent your ticket again in private.")
        return
    if len(game.get("players") or {}) >= logic.MAX_TICKETS:
        await query.answer("This game is full.", show_alert=True)
        return
    ticket = logic.make_ticket()
    try:
        await _send_ticket(context.bot, user.id, ticket)
    except Forbidden:
        await query.answer(
            "Open a private chat with me and tap Start, then take the ticket again.",
            show_alert=True,
        )
        return
    except TelegramError:
        log.exception("send ticket failed")
        await query.answer("Could not send the ticket. Try again.", show_alert=True)
        return
    added = logic.add_player(game, user.id, clean_name(user), ticket=ticket)
    if added != "ok":
        await query.answer("Could not take a ticket.", show_alert=True)
        return
    now = time.time()
    db.save_session(session["id"], game, status="lobby", deadline=_lobby_deadline(session, now))
    text, markup = render(session["id"], game)
    await _publish(context.bot, session, text, markup)
    await query.answer("Ticket sent in private.")


async def _begin(query, context, session, game, user) -> None:
    if int(user.id) != int(game["starter_id"]):
        await query.answer("Only the host can begin.", show_alert=True)
        return
    if session["status"] != "lobby":
        await query.answer("This game has already started.")
        return
    started = logic.begin(game)
    if started == "need_players":
        await query.answer(
            f"Need at least {logic.MIN_PLAYERS} tickets before beginning.",
            show_alert=True,
        )
        return
    now = time.time()
    logic.call_next(game, now)
    deadline = _next_deadline(game, now)
    db.save_session(session["id"], game, status="live", deadline=deadline)
    session["status"] = "live"
    text, markup = render(session["id"], game)
    await _publish(context.bot, session, text, markup)
    await query.answer("First number is out.")


async def _manual_call(query, context, session, game, user) -> None:
    if int(user.id) != int(game["starter_id"]):
        await query.answer("Only the host can call the next number.", show_alert=True)
        return
    if session["status"] != "live" or not game.get("started"):
        await query.answer("Begin the game first.", show_alert=True)
        return
    if game.get("claim_until"):
        await query.answer("Every number is already out.")
        return
    now = time.time()
    number = logic.call_next(game, now)
    if number is None:
        await query.answer("No numbers left.")
        await _finish(context.bot, session)
        return
    await query.answer(f"Called {number}.")
    await _after_call(context.bot, session, game, now)


async def _after_call(bot, session: dict, game: dict, now: float) -> None:
    if logic.is_over(game, now):
        await _finish(bot, session)
        return
    db.save_session(session["id"], game, status="live", deadline=_next_deadline(game, now))
    text, markup = render(session["id"], game)
    await _publish(bot, session, text, markup)


def _next_deadline(game: dict, now: float) -> float:
    if game.get("claim_until"):
        return float(game["claim_until"])
    last = game.get("last_call_at")
    if last is None:
        return now + logic.CALL_INTERVAL
    return float(last) + logic.CALL_INTERVAL


async def _claim(query, context, session, game, user, kind: str) -> None:
    if session["status"] != "live":
        await query.answer("The game has not started.", show_alert=True)
        return
    result = logic.claim(game, user.id, kind)
    if result == "absent":
        await query.answer("Take a ticket first.", show_alert=True)
        return
    if result == "invalid":
        await query.answer("Not yet — check your ticket against the called numbers.", show_alert=True)
        return
    if result == "taken":
        label = "line" if kind == "line" else "full house"
        await query.answer(f"Someone already won the {label}.", show_alert=True)
        return
    if result == "full" or logic.is_over(game, time.time()):
        await query.answer("Full house!")
        await _finish(context.bot, session)
        return
    now = time.time()
    db.save_session(session["id"], game, status="live", deadline=_next_deadline(game, now))
    text, markup = render(session["id"], game)
    await _publish(context.bot, session, text, markup)
    await query.answer("Line!")


async def on_due(bot, session: dict) -> None:
    """Called from the sweep while the session lock is held."""
    now = time.time()
    if session["status"] == "lobby":
        await _close(bot, session, "expired", "<b>Tambola</b>\nLobby closed — the host did not begin.")
        return
    game = session["payload"]
    if logic.is_over(game, now):
        await _finish(bot, session)
        return
    last = game.get("last_call_at")
    if (
        last is not None
        and not game.get("claim_until")
        and now - float(last) >= logic.LIVE_STALL_TTL
    ):
        await _close(bot, session, "expired", "<b>Tambola</b>\nClosed — numbers stopped being called.")
        return
    if game.get("claim_until"):
        return
    if last is not None and now - float(last) < logic.CALL_INTERVAL:
        return
    number = logic.call_next(game, now)
    if number is None:
        await _finish(bot, session)
        return
    await _after_call(bot, session, game, now)
