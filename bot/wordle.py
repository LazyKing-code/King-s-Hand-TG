"""Group Wordle — shared board, first correct guess wins.

Board refresh deletes the old message and sends a new one in parallel so the
chat stays tidy without a noticeable pause. One active game per chat.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, User
from telegram.constants import ChatType, ParseMode
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from bot import db
from bot.commands import cmd
from bot.moderation import require_group, require_group_admin
from bot import rights, tg
from bot.wordle_words import words_for

log = logging.getLogger(__name__)

GAME_SECS = 5 * 60
GUESS_COOLDOWN = 3.0
SWEEP_INTERVAL = 5
HINT_DELETE_SECS = 4

_locks: dict[int, asyncio.Lock] = {}
_cooldowns: dict[tuple[int, int], float] = {}

_GUESS_RE = re.compile(r"^[A-Za-z]{4,5}$")


def _lock_for(chat_id: int) -> asyncio.Lock:
    lock = _locks.get(chat_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[chat_id] = lock
    return lock


def _now() -> float:
    return time.time()


def _clean_name(user: User) -> str:
    name = (user.first_name or user.full_name or str(user.id)).strip()
    name = " ".join(name.split())
    return name[:20] if name else str(user.id)


def score_guess(guess: str, answer: str) -> str:
    """Standard Wordle coloring: green exact, yellow elsewhere, red miss."""
    guess = guess.upper()
    answer = answer.upper()
    n = len(answer)
    marks = [""] * n
    leftover: dict[str, int] = {}
    for i, ch in enumerate(answer):
        if guess[i] == ch:
            marks[i] = "🟩"
        else:
            leftover[ch] = leftover.get(ch, 0) + 1
    for i, ch in enumerate(guess):
        if marks[i]:
            continue
        if leftover.get(ch, 0) > 0:
            marks[i] = "🟨"
            leftover[ch] -= 1
        else:
            marks[i] = "🟥"
    return "".join(marks)


def _time_left_label(ends_at: float) -> str:
    left = max(0, int(ends_at - _now()))
    mins, secs = divmod(left, 60)
    return f"{mins}:{secs:02d}"


def _format_board(game: dict, *, footer: str | None = None) -> str:
    length = int(game["length"])
    lines = [
        f"🔤 <b>Wordle</b> · {length} letters",
    ]
    status = str(game["status"])
    if status == "active":
        lines.append(f"⏱ Time left: <b>{_time_left_label(game['ends_at'])}</b>")
    elif status == "won":
        winner = escape(game.get("winner_name") or "Someone")
        lines.append(f"🎉 Nice one, <b>{winner}</b>!")
        lines.append(f"The word was <b>{escape(game['word'])}</b>.")
    elif status == "expired":
        lines.append("⏰ Time's up!")
        lines.append(f"The word was <b>{escape(game['word'])}</b>. Good effort, everyone!")
    elif status == "cancelled":
        lines.append("Wordle was cancelled by staff.")
        lines.append(f"The word was <b>{escape(game['word'])}</b>.")

    starter = escape(game.get("started_name") or "someone")
    lines.append(f"Started by {starter}")
    lines.append("")

    guesses = game.get("guesses") or []
    if guesses:
        for i, g in enumerate(guesses, start=1):
            name = escape(g.get("name") or "?")
            word = escape(g.get("word") or "")
            colors = g.get("colors") or ""
            lines.append(f"{i}. {name}  <code>{word}</code>  {colors}")
    else:
        lines.append("<i>No guesses yet — send a word to play.</i>")

    lines.append("")
    if footer:
        lines.append(footer)
    elif status == "active":
        lines.append(
            f"Send any <b>{length}-letter</b> word (A–Z). "
            "First correct guess wins!"
        )
        lines.append(
            f"Staff can stop it early with <code>{cmd('wordle')} cancel</code>."
        )
    return "\n".join(lines)


async def _soft_hint(msg, text: str) -> None:
    """Short private-feeling hint that cleans itself up."""
    try:
        sent = await msg.reply_text(text, disable_notification=True)
    except TelegramError:
        return

    async def _cleanup() -> None:
        await asyncio.sleep(HINT_DELETE_SECS)
        for target in (sent, msg):
            try:
                await target.delete()
            except TelegramError:
                pass

    asyncio.create_task(_cleanup())


async def _publish_board(
    bot,
    chat_id: int,
    text: str,
    *,
    old_message_id: int | None,
    context: ContextTypes.DEFAULT_TYPE | None = None,
) -> int | None:
    """Delete the previous board and send a fresh one at the same time."""

    async def _delete() -> None:
        if not old_message_id:
            return
        try:
            await tg.delete_message(bot, chat_id, old_message_id)
        except TelegramError:
            pass

    async def _send():
        return await tg.send_message(
            bot,
            chat_id,
            text,
            parse_mode=ParseMode.HTML,
            disable_notification=True,
        )

    results = await asyncio.gather(_delete(), _send(), return_exceptions=True)
    sent = results[1]
    if isinstance(sent, Exception):
        log.warning("wordle board send failed chat=%s: %s", chat_id, sent)
        if context is not None:
            await rights.alert_failure(
                context,
                chat_id,
                "wordle_board",
                sent,
                prefix="Wordle board couldn't update",
            )
        return old_message_id
    new_id = int(sent.message_id)
    db.set_wordle_message_id(chat_id, new_id)
    return new_id


def _pick_word(chat_id: int, length: int) -> str:
    bank = words_for(length)
    used = db.list_used_wordle_words(chat_id, length)
    available = bank - used
    if not available:
        db.clear_used_wordle_words(chat_id, length)
        available = set(bank)
    return random.choice(sorted(available))


async def _begin_game(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    length: int,
) -> None:
    msg = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not msg or not chat or not user:
        return
    if length not in (4, 5):
        await msg.reply_text("Pick 4 or 5 letters — try /wordle 4 or /wordle 5.")
        return

    async with _lock_for(chat.id):
        if db.get_active_wordle(chat.id):
            await msg.reply_text(
                "A Wordle is already running here. "
                f"Wait for it to finish, or ask staff to use {cmd('wordle')} cancel."
            )
            return
        word = _pick_word(chat.id, length)
        ends_at = _now() + GAME_SECS
        game = db.start_wordle_game(
            chat.id,
            word,
            length,
            user.id,
            _clean_name(user),
            ends_at,
        )
        if not game:
            await msg.reply_text(
                "A Wordle is already running here. "
                f"Wait for it to finish, or ask staff to use {cmd('wordle')} cancel."
            )
            return
        text = _format_board(game)
        try:
            sent = await tg.send_message(
                context.bot,
                chat.id,
                text,
                parse_mode=ParseMode.HTML,
            )
            db.set_wordle_message_id(chat.id, sent.message_id)
        except TelegramError as exc:
            log.warning("wordle start send failed: %s", exc)
            db.finish_wordle(chat.id, "cancelled")
            await msg.reply_text(rights.friendly_error(exc))


async def cmd_wordle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group(update):
        return
    msg = update.effective_message
    if not msg:
        return
    args = [a.lower() for a in (context.args or [])]

    if args and args[0] in {"cancel", "stop", "end"}:
        await _cancel_wordle(update, context)
        return

    if args and args[0] in {"4", "5"}:
        await _begin_game(update, context, int(args[0]))
        return

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("4 letters", callback_data="wd:start:4"),
                InlineKeyboardButton("5 letters", callback_data="wd:start:5"),
            ]
        ]
    )
    await msg.reply_text(
        "🔤 Group Wordle\n"
        "Everyone guesses the same word. First correct guess wins.\n\n"
        f"• Runs for {GAME_SECS // 60} minutes, then the word is revealed\n"
        "• One game at a time per group\n"
        f"• Or jump in with {cmd('wordle')} 4 / {cmd('wordle')} 5\n\n"
        "How many letters?",
        reply_markup=keyboard,
    )


async def _cancel_wordle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_admin(update):
        return
    msg = update.effective_message
    chat = update.effective_chat
    if not msg or not chat:
        return

    async with _lock_for(chat.id):
        game = db.get_active_wordle(chat.id)
        if not game:
            await msg.reply_text("No Wordle is running right now.")
            return
        finished = db.finish_wordle(chat.id, "cancelled")
        if not finished:
            await msg.reply_text("No Wordle is running right now.")
            return
        text = _format_board(finished)
        await _publish_board(
            context.bot,
            chat.id,
            text,
            old_message_id=game.get("message_id"),
            context=context,
        )
        await msg.reply_text("Wordle cancelled. Anyone can start a new one when ready.")


async def on_wordle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    parts = query.data.split(":")
    if len(parts) != 3 or parts[0] != "wd" or parts[1] != "start":
        await query.answer()
        return
    if parts[2] not in {"4", "5"}:
        await query.answer()
        return
    chat = update.effective_chat
    if not chat or chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await query.answer("Start Wordle in a group.", show_alert=True)
        return
    await query.answer()
    if update.effective_message:
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except TelegramError:
            pass
    await _begin_game(update, context, int(parts[2]))


async def on_wordle_guess(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Catch plain 4/5-letter words while a game is active."""
    msg = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not msg or not chat or not user:
        return
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return
    text = (msg.text or "").strip()
    if not _GUESS_RE.fullmatch(text):
        return

    game = db.get_active_wordle(chat.id)
    if not game:
        return
    length = int(game["length"])
    if len(text) != length:
        return

    guess = text.upper()
    # Any real-looking word of the right length is a valid guess. The secret
    # still comes from the clean bank — keeping the guess list tiny felt broken
    # (e.g. common words like TIER were rejected).
    if not guess.isalpha() or len(guess) != length:
        return

    key = (chat.id, user.id)
    now = _now()
    last = _cooldowns.get(key, 0.0)
    if now - last < GUESS_COOLDOWN:
        wait = GUESS_COOLDOWN - (now - last)
        await _soft_hint(
            msg,
            f"Easy does it — wait {wait:.0f}s before your next guess.",
        )
        return

    async with _lock_for(chat.id):
        game = db.get_active_wordle(chat.id)
        if not game or int(game["length"]) != length:
            return
        if _now() >= float(game["ends_at"]):
            # Let the sweep loop finalize; avoid racing here.
            return

        # Re-check cooldown under lock so two rapid messages don't both pass.
        last = _cooldowns.get(key, 0.0)
        now = _now()
        if now - last < GUESS_COOLDOWN:
            wait = GUESS_COOLDOWN - (now - last)
            await _soft_hint(
                msg,
                f"Easy does it — wait {wait:.0f}s before your next guess.",
            )
            return
        _cooldowns[key] = now

        colors = score_guess(guess, game["word"])
        guesses = list(game.get("guesses") or [])
        guesses.append(
            {
                "user_id": user.id,
                "name": _clean_name(user),
                "word": guess,
                "colors": colors,
            }
        )
        db.update_wordle_guesses(chat.id, guesses)
        old_id = game.get("message_id")

        won = guess == game["word"].upper()
        if won:
            finished = db.finish_wordle(
                chat.id,
                "won",
                winner_id=user.id,
                winner_name=_clean_name(user),
            )
            if not finished:
                return
            text = _format_board(finished)
        else:
            game["guesses"] = guesses
            text = _format_board(game)

        async def _del_guess() -> None:
            try:
                await msg.delete()
            except TelegramError:
                pass

        await asyncio.gather(
            _publish_board(
                context.bot, chat.id, text, old_message_id=old_id, context=context
            ),
            _del_guess(),
            return_exceptions=True,
        )


async def expire_wordles_loop(app) -> None:
    await asyncio.sleep(2)
    while True:
        try:
            due = db.list_active_wordles_due(_now())
            for game in due:
                chat_id = int(game["chat_id"])
                async with _lock_for(chat_id):
                    live = db.get_active_wordle(chat_id)
                    if not live:
                        continue
                    if _now() < float(live["ends_at"]):
                        continue
                    finished = db.finish_wordle(chat_id, "expired")
                    if not finished:
                        continue
                    text = _format_board(finished)
                    await _publish_board(
                        app.bot,
                        chat_id,
                        text,
                        old_message_id=live.get("message_id"),
                        context=app,
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("wordle expire sweep failed")
        await asyncio.sleep(SWEEP_INTERVAL)
