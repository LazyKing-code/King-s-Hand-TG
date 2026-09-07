from __future__ import annotations

import asyncio
import logging
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType
from telegram.error import Forbidden, RetryAfter, TelegramError
from telegram.ext import ContextTypes

from bot import db
from bot.commands import cmd
from bot.config import OWNER_IDS
from bot.invite import bot_username, url_buttons

log = logging.getLogger(__name__)

# Add a new entry at the bottom when you ship. The last id is broadcast once.
_NOTES: list[dict] = [
    {
        "id": "2026-09-06.4",
        "date": "6 September 2026",
        "headline": "Commands, joins, and sticker reports",
        "intro": (
            "This release tightens everyday group behaviour. "
            "Only what changed is listed below."
        ),
        "sections": [
            (
                "Command menu",
                "Tapping a suggestion such as /help in a group now runs correctly "
                "when Telegram inserts the bot username (/help@BotName).",
            ),
            (
                "Telegram bots",
                "Bots you add from BotFather are no longer kicked on join. "
                "Join verification still applies to people. "
                "Raidmode still stops flood joins, not official bots.",
            ),
            (
                "Member status",
                f"{cmd('stats')} (reply or @username) now shows immune, trusted, "
                "approved, and sticker warnings as separate fields.",
            ),
            (
                "Sticker reports",
                f"A {cmd('report')} now bans that sticker and its pack, and is "
                "enforced for members and admins. "
                f"Only the owner can {cmd('unreport')} or {cmd('unreportall')} confirm. "
                "Removal notices tag the sender; rapid spam is grouped into one message.",
            ),
            (
                "Admin demote",
                "Three banned-sticker warnings still demote an admin this bot promoted "
                f"with {cmd('makeadmin')}. The next banned sticker after that is a kick.",
            ),
        ],
    },
    {
        "id": "2026-09-07.1",
        "date": "7 September 2026",
        "headline": "Hand cricket and game cards",
        "intro": (
            "This update is about match rules and how games look in the group. "
            "Only what changed is listed below."
        ),
        "sections": [
            (
                "Hand cricket",
                "If you are out for 0, the other side only needs 1 to win. "
                "The chase now ends as soon as that target is passed — they do not keep batting to 18. "
                "Your number locks for that ball and cannot be changed.",
            ),
            (
                "Scoreboard cards",
                "Cricket, toss, lucky 7, and duels post a graphic card with both player names, "
                "score, and who is batting.",
            ),
        ],
    },
    {
        "id": "2026-09-07.2",
        "date": "7 September 2026",
        "headline": "New games and per-game boards",
        "intro": (
            "Three new two-player games, and a wins/losses board for each game. "
            "Only what changed is listed below."
        ),
        "sections": [
            (
                "Four in a row",
                f"{cmd('four')} @username — a visible board. Drop in columns 1–7. "
                "First to four in a line wins.",
            ),
            (
                "Penalty duel",
                f"{cmd('penalty')} — five kicks each. Shooter and keeper pick Left, Centre, or Right. "
                "Same side is a save.",
            ),
            (
                "The vault",
                f"{cmd('vault')} — both lock Share or Take. Split, steal, or both walk away empty.",
            ),
            (
                "Per-game leaderboard",
                f"{cmd('top')} cricket, {cmd('top')} four, {cmd('top')} penalty, and the other games "
                f"show wins, losses, and draws. {cmd('balance')} lists your line for each.",
            ),
        ],
    },
]


def current_note() -> dict:
    return _NOTES[-1]


RELEASE_ID = current_note()["id"]


def _section_html(title: str, body: str) -> str:
    return f"<b>{escape(title)}</b>\n{escape(body)}"


def release_html(note: dict | None = None) -> str:
    note = note or current_note()
    parts = [
        "<b>King's Hand</b> · Release notes",
        f"<i>{escape(note['date'])} · {escape(note['headline'])}</i>",
        "",
        escape(note["intro"]),
        "",
    ]
    for title, body in note["sections"]:
        parts.append(_section_html(title, body))
        parts.append("")
    parts.append(
        f"In the group, send {cmd('help')} for commands that match your role. "
        f"Earlier notes: {cmd('whatsnew')} history."
    )
    return "\n".join(parts).strip()


def history_html() -> str:
    lines = ["<b>King's Hand</b> · Previous releases", ""]
    for note in reversed(_NOTES):
        lines.append(f"<b>{escape(note['date'])}</b> — {escape(note['headline'])}")
        for title, _body in note["sections"]:
            lines.append(f"• {escape(title)}")
        lines.append("")
    lines.append(f"Latest detail: {cmd('whatsnew')}")
    return "\n".join(lines).strip()


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
    html = release_html()
    try:
        await bot.send_message(
            user_id,
            html,
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
                html,
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
    args = [a.lower() for a in (context.args or [])]
    show_history = bool(args and args[0] in {"history", "all", "archive"})
    html = history_html() if show_history else release_html()
    await msg.reply_html(
        html,
        reply_markup=None if show_history else _notes_markup(username),
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
            f"Preview of {RELEASE_ID}. {n} people have not received this note yet "
            f"({total} known private chats).\n"
            f"{cmd('release')} send — deliver in private chat\n"
            f"{cmd('whatsnew')} · {cmd('whatsnew')} history"
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
