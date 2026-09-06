from __future__ import annotations

import asyncio
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType
from telegram.error import Forbidden, RetryAfter, TelegramError
from telegram.ext import ContextTypes

from bot import db
from bot.commands import cmd
from bot.config import OWNER_IDS
from bot.invite import bot_username, url_buttons

log = logging.getLogger(__name__)

# Bump this string when you ship a new note. Each id is sent once per user.
RELEASE_ID = "2026-09-06"
RELEASE_TITLE = "September 2026"


def release_html() -> str:
    return (
        "<b>King's Hand</b>\n"
        f"<i>Official update · {RELEASE_TITLE}</i>\n"
        "━━━━━━━━━━━━━━\n\n"
        "Thank you for adding me. This note is for people who opened a private "
        "chat with the bot.\n\n"
        "<b>1. Commands are the normal names now</b>\n"
        "Use <code>/help</code>, <code>/kick</code>, <code>/ban</code>, "
        "<code>/lock</code> — there is no <code>kh_</code> prefix anymore.\n"
        "If Rose is still in the group, remove it so both bots do not answer "
        "the same command.\n\n"
        "<b>2. Everyday group tools</b>\n"
        "Welcome and goodbye, join verify, rules, notes, filters, warns, "
        "mute / ban, pin, purge, lock, and anti-flood — in this bot.\n\n"
        "<b>3. Games in the group</b>\n"
        "Toss, dice, lucky 7, stone-paper-scissors, and hand cricket "
        "(vs a friend). Daily streak and a leaderboard.\n"
        f"<blockquote>{cmd('daily')} · {cmd('gamehelp')} · {cmd('top')} · "
        f"{cmd('cricket')} @user</blockquote>\n"
        "Coins are for fun only. Nothing is real money.\n\n"
        "<b>4. Stickers</b>\n"
        "NSFW pack detection is unchanged. Reply to a sticker with "
        f"<code>{cmd('report')}</code> to ban a pack. "
        f"<code>{cmd('packs')}</code> lists them — tap Allow to undo.\n\n"
        f"In the group, send <code>{cmd('help')}</code> for commands that match "
        "your role. This message stays in our private chat."
    )


def _notes_markup(username: str | None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton("Open /help", callback_data="help:index"),
            InlineKeyboardButton("Games guide", callback_data="gm:h:index"),
        ]
    ]
    if username:
        rows.extend(url_buttons(username).inline_keyboard)
    return InlineKeyboardMarkup(rows)


async def send_release_card(bot, user_id: int, username: str | None) -> bool:
    try:
        await bot.send_message(
            user_id,
            release_html(),
            parse_mode="HTML",
            reply_markup=_notes_markup(username),
            disable_web_page_preview=True,
        )
        return True
    except Forbidden:
        db.drop_bot_user(user_id)
        log.info("release: %s blocked the bot", user_id)
        return False
    except RetryAfter as exc:
        await asyncio.sleep(float(exc.retry_after) + 0.5)
        try:
            await bot.send_message(
                user_id,
                release_html(),
                parse_mode="HTML",
                reply_markup=_notes_markup(username),
                disable_web_page_preview=True,
            )
            return True
        except TelegramError:
            log.exception("release retry failed for %s", user_id)
            return False
    except TelegramError:
        log.exception("release send failed for %s", user_id)
        return False


def _targets(*, only_unseen: bool) -> list[int]:
    ids = set(db.list_bot_users()) | set(OWNER_IDS)
    if not only_unseen:
        return sorted(ids)
    return sorted(uid for uid in ids if db.bot_user_release(uid) != RELEASE_ID)


async def broadcast_release(application, *, force: bool = False) -> tuple[int, int]:
    me = await application.bot.get_me()
    username = me.username
    sent = 0
    failed = 0
    for uid in _targets(only_unseen=not force):
        db.touch_bot_user(uid)
        ok = await send_release_card(application.bot, uid, username)
        if ok:
            db.set_bot_user_release(uid, RELEASE_ID)
            sent += 1
        else:
            failed += 1
        await asyncio.sleep(0.05)
    db.kv_set("last_release_broadcast", RELEASE_ID)
    log.info("release %s sent=%s failed=%s", RELEASE_ID, sent, failed)
    return sent, failed


async def maybe_broadcast_on_startup(application) -> None:
    await asyncio.sleep(8)
    if db.kv_get("last_release_broadcast") == RELEASE_ID:
        return
    try:
        await broadcast_release(application, force=False)
    except Exception:
        log.exception("startup release broadcast failed")


async def deliver_if_needed(user_id: int, bot, username: str | None) -> None:
    db.touch_bot_user(user_id)
    if db.bot_user_release(user_id) == RELEASE_ID:
        return
    if await send_release_card(bot, user_id, username):
        db.set_bot_user_release(user_id, RELEASE_ID)


async def cmd_whatsnew(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    msg = update.effective_message
    if not user or not chat or not msg:
        return
    if chat.type == ChatType.PRIVATE:
        db.touch_bot_user(user.id)
    username = await bot_username(context)
    await msg.reply_html(
        release_html(),
        reply_markup=_notes_markup(username),
        disable_web_page_preview=True,
    )
    if chat.type == ChatType.PRIVATE:
        db.set_bot_user_release(user.id, RELEASE_ID)


async def cmd_release(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    msg = update.effective_message
    if not user or not msg:
        return
    if user.id not in OWNER_IDS:
        await msg.reply_text("Only the bot owner can send the official update.")
        return
    args = [a.lower() for a in (context.args or [])]
    username = await bot_username(context)
    if not args or args[0] in {"preview", "show"}:
        n = len(_targets(only_unseen=True))
        total = len(set(db.list_bot_users()) | set(OWNER_IDS))
        await msg.reply_html(
            release_html(),
            reply_markup=_notes_markup(username),
            disable_web_page_preview=True,
        )
        await msg.reply_text(
            f"Preview above. {n} people have not received this note yet "
            f"({total} known starters).\n"
            f"{cmd('release')} send — deliver in private chat\n"
            f"Anyone can read it with {cmd('whatsnew')}"
        )
        return
    if args[0] != "send":
        await msg.reply_text(f"Use {cmd('release')} preview or {cmd('release')} send")
        return
    force = len(args) > 1 and args[1] in {"all", "force"}
    status = await msg.reply_text("Sending the official update…")
    sent, failed = await broadcast_release(context.application, force=force)
    await status.edit_text(
        f"Update {RELEASE_ID} delivered.\nSent: {sent}\nCould not reach: {failed}"
    )
