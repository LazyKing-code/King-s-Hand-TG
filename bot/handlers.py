from __future__ import annotations

import logging
import time
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from bot import db
from bot.commands import cmd, cmd_name
from bot.config import OWNER_IDS
from bot.invite import ADD_TEXT, bot_username, pick_keyboard, url_buttons
from bot.release import deliver_if_needed
from bot.moderation import (
    DEFAULT_KICK_MSG,
    announce_kick,
    apply_punishment,
    demote,
    demote_all_admins,
    format_user,
    forwarded_chat,
    is_group_admin,
    is_immune,
    is_owner,
    kick,
    mention,
    promote_limited,
    require_group_admin,
    require_group_owner,
    resolve_target,
)

from bot.nsfw import is_nsfw_sticker

log = logging.getLogger(__name__)

TARGET_HINT = "Reply to them, or mention them."
_PENDING_LOG: dict[int, int] = {}
_REPORT_WINDOW = 25.0
_report_batch: dict[int, dict] = {}


def command_payload(update: Update) -> str:
    msg = update.effective_message
    text = (msg.text or msg.caption or "").strip()
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        return ""
    return parts[1].strip()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    extra = ""
    if user and not OWNER_IDS:
        extra = (
            f"\n\nYour id is <code>{user.id}</code>. "
            "The person who set this bot up may ask you for it."
        )
    if not chat or chat.type != ChatType.PRIVATE:
        await update.effective_message.reply_html(
            "<b>King's Hand</b>\n"
            f"Send <code>{cmd('help')}</code> to see what you can use here."
            + extra
        )
        return
    if not user:
        return
    db.touch_bot_user(user.id)
    username = await bot_username(context)
    payload = (context.args[0].lower() if context.args else "")
    if payload in {"notes", "whatsnew", "update"}:
        await deliver_if_needed(user.id, context.bot, username)
        return
    await update.effective_message.reply_html(
        "<b>King's Hand</b>\n"
        "I keep the group in order — stickers, spam, whispers, and the occasional scolding.\n\n"
        f"{ADD_TEXT}\n\n"
        f"Then send <code>{cmd('help')}</code> in the group to see commands for your role."
        + extra,
        reply_markup=url_buttons(username),
        disable_web_page_preview=True,
    )
    await update.effective_message.reply_text(
        "Or pick a chat you already admin:",
        reply_markup=pick_keyboard(),
    )
    await deliver_if_needed(user.id, context.bot, username)


async def on_sticker(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    sticker = message.sticker if message else None
    if not message or not chat or not user or not sticker:
        return
    if user.is_bot:
        return
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return
    if is_immune(user.id, chat.id) or await is_owner(update, user.id):
        return
    if await is_group_admin(update, user.id):
        return

    nsfw, reason = await is_nsfw_sticker(sticker, chat.id, context.bot)
    if not nsfw:
        return

    try:
        await message.delete()
    except Exception:
        log.exception("Could not delete sticker")
        await message.reply_text(
            "I saw a banned sticker but could not delete it. I need permission to delete messages."
        )
        return

    try:
        await apply_punishment(update, context, user, reason)
    except Exception:
        log.exception("Punishment failed")


async def cmd_approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_owner(update):
        return
    target = await resolve_target(update, context)
    if not target:
        await update.effective_message.reply_text(f"Who? {TARGET_HINT}")
        return
    db.set_approved(update.effective_chat.id, target.id, True)
    db.reset_strikes(update.effective_chat.id, target.id)
    await update.effective_message.reply_html(
        f"{mention(target)} is approved. One extra chance on a banned sticker — "
        "after that, the next one still kicks."
    )


async def cmd_unapprove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_owner(update):
        return
    target = await resolve_target(update, context)
    if not target:
        await update.effective_message.reply_text(f"Who? {TARGET_HINT}")
        return
    db.set_approved(update.effective_chat.id, target.id, False)
    await update.effective_message.reply_html(f"{mention(target)} is no longer approved.")


async def cmd_trust(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_owner(update):
        return
    target = await resolve_target(update, context)
    if not target:
        await update.effective_message.reply_text(
            f"Who should never be punished? {TARGET_HINT}"
        )
        return
    chat_id = update.effective_chat.id
    if is_immune(target.id, chat_id) or await is_owner(update, target.id):
        db.set_trusted(chat_id, target.id, True)
        await update.effective_message.reply_html(
            f"{mention(target)} is already on the do-not-punish list."
        )
        return
    db.set_trusted(chat_id, target.id, True)
    await update.effective_message.reply_html(
        f"{mention(target)} is trusted and will not be punished for stickers."
    )


async def cmd_untrust(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_owner(update):
        return
    target = await resolve_target(update, context)
    if not target:
        await update.effective_message.reply_text(f"Who? {TARGET_HINT}")
        return
    chat_id = update.effective_chat.id
    if target.id in OWNER_IDS or db.is_extra_owner(chat_id, target.id):
        await update.effective_message.reply_html(
            f"{mention(target)} is an owner, so they stay immune. Use {cmd('removeowner')} first."
        )
        return
    db.set_trusted(chat_id, target.id, False)
    await update.effective_message.reply_html(
        f"{mention(target)} was removed from the do-not-punish list."
    )


async def cmd_trusted(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_owner(update):
        return
    chat_id = update.effective_chat.id
    ids = db.list_trusted(chat_id)
    if not ids:
        await update.effective_message.reply_text(
            f"No extra trusted users yet. Reply {cmd('trust')} to someone you do not want punished."
        )
        return
    lines = [await format_user(context, chat_id, uid) for uid in ids]
    await update.effective_message.reply_html(
        "Do-not-punish list:\n" + "\n".join(f"• {line}" for line in lines)
    )


async def cmd_addowner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_owner(update):
        return
    target = await resolve_target(update, context)
    if not target:
        await update.effective_message.reply_text(
            f"Who should be an extra owner? {TARGET_HINT}"
        )
        return
    chat_id = update.effective_chat.id
    if target.id in OWNER_IDS:
        await update.effective_message.reply_html(
            f"{mention(target)} is already a root owner in .env."
        )
        return
    db.set_extra_owner(chat_id, target.id, True)
    await update.effective_message.reply_html(
        f"{mention(target)} is now an owner in this group.\n"
        "They can run bot commands and will not be punished."
    )


async def cmd_removeowner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_owner(update):
        return
    target = await resolve_target(update, context)
    if not target:
        await update.effective_message.reply_text(f"Who? {TARGET_HINT}")
        return
    if target.id in OWNER_IDS:
        await update.effective_message.reply_text(
            "That id is in OWNER_IDS / OWNER_ID in .env. Remove it there and restart the bot."
        )
        return
    db.set_extra_owner(update.effective_chat.id, target.id, False)
    await update.effective_message.reply_html(
        f"{mention(target)} is no longer an extra owner. "
        f"They stay on {cmd('trusted')} until you {cmd('untrust')} them."
    )


async def cmd_owners(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_owner(update):
        return
    chat_id = update.effective_chat.id
    lines = ["Owners (commands + never punished):"]
    if OWNER_IDS:
        for uid in sorted(OWNER_IDS):
            lines.append(f"• {await format_user(context, chat_id, uid)} — .env")
    else:
        lines.append("• none in .env (set OWNER_IDS)")
    extra = db.list_extra_owners(chat_id)
    if extra:
        for uid in extra:
            lines.append(f"• {await format_user(context, chat_id, uid)} — {cmd('addowner')}")
    lines.append("The Telegram group creator is always an owner.")
    await update.effective_message.reply_html("\n".join(lines))


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_admin(update):
        return
    msg = update.effective_message
    chat = update.effective_chat
    reply = msg.reply_to_message
    if not reply or not reply.sticker:
        await msg.reply_text(
            f"Reply to a sticker with {cmd('report')} and I will ban that pack."
        )
        return
    chat_id = chat.id
    set_name = reply.sticker.set_name
    try:
        await reply.delete()
    except Exception:
        pass

    if not set_name:
        db.cache_set(reply.sticker.file_unique_id, True, None)
        db.ban_sticker(chat_id, reply.sticker.file_unique_id)
        label = "this sticker (no pack)"
        already = False
    else:
        already = db.pack_banned(chat_id, set_name)
        db.ban_pack(chat_id, set_name)
        db.cache_set(reply.sticker.file_unique_id, True, set_name)
        label = set_name

    try:
        await msg.delete()
    except Exception:
        pass

    if already:
        return
    await _ack_report(context, chat_id, label)


async def _ack_report(context: ContextTypes.DEFAULT_TYPE, chat_id: int, label: str) -> None:
    now = time.time()
    batch = _report_batch.get(chat_id)
    if batch and now < batch["expires"]:
        names: list[str] = batch["names"]
        if label not in names:
            names.append(label)
        batch["expires"] = now + _REPORT_WINDOW
        shown = names[-10:]
        extra = f"\n… +{len(names) - 10} more" if len(names) > 10 else ""
        body = (
            f"Banned {len(names)} pack(s):\n"
            + "\n".join(f"• {n}" for n in shown)
            + extra
        )
        try:
            await context.bot.edit_message_text(
                body,
                chat_id=chat_id,
                message_id=batch["msg_id"],
            )
        except TelegramError:
            pass
        return
    sent = await context.bot.send_message(chat_id, f"Banned pack: {label}")
    _report_batch[chat_id] = {
        "expires": now + _REPORT_WINDOW,
        "names": [label],
        "msg_id": sent.message_id,
    }


async def cmd_blockpack(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_report(update, context)


async def cmd_allowpack(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_admin(update):
        return
    chat_id = update.effective_chat.id
    msg = update.effective_message
    name = " ".join(context.args or []).strip()
    reply = msg.reply_to_message
    if reply and reply.sticker and reply.sticker.set_name:
        name = reply.sticker.set_name
    if not name:
        await _send_packs_list(msg, chat_id)
        return
    banned = db.list_banned_packs(chat_id)
    match = _find_named(banned, name) or name
    db.allow_pack(chat_id, match)
    await msg.reply_html(
        f"Allowed pack <code>{escape(match)}</code>. "
        "You do not need the original sticker — it is already deleted after a report."
    )


async def cmd_allowsticker(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_admin(update):
        return
    msg = update.effective_message
    reply = msg.reply_to_message
    if reply and reply.sticker:
        db.allow_sticker(reply.sticker.file_unique_id)
        db.unban_sticker(update.effective_chat.id, reply.sticker.file_unique_id)
        await msg.reply_text("That sticker is whitelisted.")
        return
    rest = " ".join(context.args or []).strip().lower()
    stickers = db.list_banned_stickers(update.effective_chat.id)
    if rest.startswith("s") and rest[1:].isdigit():
        target = _pick_index(stickers, rest[1:])
        if not target:
            await msg.reply_text(
                f"No banned sticker with that number. Send {cmd('packs')} first."
            )
            return
        db.allow_sticker(target)
        db.unban_sticker(update.effective_chat.id, target)
        await msg.reply_text("Removed that sticker from the ban list.")
        return
    await _send_packs_list(msg, update.effective_chat.id)


def _find_named(items: list[str], raw: str) -> str | None:
    needle = raw.strip()
    if not needle:
        return None
    for item in items:
        if item.lower() == needle.lower():
            return item
    return None


def _pick_index(items: list[str], raw: str) -> str | None:
    if raw.isdigit():
        idx = int(raw)
        if 1 <= idx <= len(items):
            return items[idx - 1]
        return None
    return _find_named(items, raw)


async def cmd_packs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_admin(update):
        return
    chat_id = update.effective_chat.id
    msg = update.effective_message
    args = list(context.args or [])
    invoked = (msg.text or "").split()[0].lstrip("/").split("@")[0].lower()
    if invoked == cmd_name("unbanpack") and args:
        args = ["unban", *args]
    banned = db.list_banned_packs(chat_id)
    allowed = db.list_allowed_packs(chat_id)
    stickers = db.list_banned_stickers(chat_id)

    if args:
        action = args[0].lower()
        rest = " ".join(args[1:]).strip()
        if action in {"unban", "remove", "del", "allow"}:
            if not rest:
                await msg.reply_text(
                    f"Which pack? Example: {cmd('packs')} unban 2\n"
                    f"or {cmd('packs')} unban PackName"
                )
                return
            if rest.lower().startswith("s") and rest[1:].isdigit():
                target = _pick_index(stickers, rest[1:])
                if not target:
                    await msg.reply_text("No banned sticker with that number. Send the list again.")
                    return
                db.allow_sticker(target)
                db.unban_sticker(chat_id, target)
                await msg.reply_text("Removed that sticker from the ban list (whitelisted).")
                return
            target = _pick_index(banned, rest)
            if not target:
                await msg.reply_text("That pack is not on the banned list. Check the number or name.")
                return
            db.allow_pack(chat_id, target)
            await msg.reply_html(
                f"Removed <code>{escape(target)}</code> from the banned list "
                "and whitelisted it so it is not auto-banned again."
            )
            return
        if action in {"unallow", "unwhitelist"}:
            if not rest:
                await msg.reply_text(f"Example: {cmd('packs')} unallow 1")
                return
            target = _pick_index(allowed, rest)
            if not target:
                await msg.reply_text("That pack is not on the whitelist.")
                return
            db.unallow_pack(chat_id, target)
            await msg.reply_html(f"Removed <code>{escape(target)}</code> from the whitelist.")
            return

    if not banned and not allowed and not stickers:
        await msg.reply_text(
            "No banned packs or stickers in this group yet.\n"
            f"Reply to a sticker with {cmd('report')} to ban its pack."
        )
        return
    await _send_packs_list(msg, chat_id)


def _packs_markup(
    banned: list[str],
    stickers: list[str],
    allowed: list[str],
) -> InlineKeyboardMarkup | None:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for i, name in enumerate(banned[:16], 1):
        label = name if len(name) <= 28 else name[:25] + "…"
        row.append(InlineKeyboardButton(f"Allow {i}: {label}", callback_data=f"pk:b:{i}"))
        if len(row) == 1:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    row = []
    for i, _uid in enumerate(stickers[:8], 1):
        row.append(InlineKeyboardButton(f"Allow s{i}", callback_data=f"pk:s:{i}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    row = []
    for i, name in enumerate(allowed[:8], 1):
        label = name if len(name) <= 22 else name[:19] + "…"
        row.append(InlineKeyboardButton(f"Drop {i}: {label}", callback_data=f"pk:a:{i}"))
        if len(row) == 1:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    if not rows:
        return None
    return InlineKeyboardMarkup(rows)


def _packs_text(banned: list[str], stickers: list[str], allowed: list[str]) -> str:
    lines = [
        "<b>Banned packs</b>",
        "The sticker is deleted after a report. Use the buttons, or the number — no reply needed.",
    ]
    if banned:
        for i, name in enumerate(banned, 1):
            link = f"https://t.me/addstickers/{name}"
            lines.append(f'{i}. <a href="{link}">{escape(name)}</a>')
        lines.append(
            f"Or: <code>{cmd('packs')} unban 2</code> · "
            f"<code>{cmd('allowpack')} PackName</code>"
        )
    else:
        lines.append("None")

    lines.append("")
    lines.append("<b>Banned stickers</b> (no pack)")
    if stickers:
        for i, uid in enumerate(stickers, 1):
            shown = escape(uid if len(uid) <= 24 else uid[:20] + "…")
            lines.append(f"s{i}. <code>{shown}</code>")
        lines.append(f"Tap <b>Allow s1</b> or <code>{cmd('packs')} unban s1</code>")
    else:
        lines.append("None")

    lines.append("")
    lines.append("<b>Whitelisted packs</b>")
    if allowed:
        for i, name in enumerate(allowed, 1):
            lines.append(f"{i}. <code>{escape(name)}</code>")
        lines.append(f"Drop whitelist: tap the button or <code>{cmd('packs')} unallow 1</code>")
    else:
        lines.append("None")
    return "\n".join(lines)


async def _send_packs_list(msg, chat_id: int) -> None:
    banned = db.list_banned_packs(chat_id)
    allowed = db.list_allowed_packs(chat_id)
    stickers = db.list_banned_stickers(chat_id)
    if not banned and not allowed and not stickers:
        await msg.reply_text(
            "No banned packs or stickers in this group yet.\n"
            f"Reply to a sticker with {cmd('report')} to ban its pack."
        )
        return
    text = _packs_text(banned, stickers, allowed)
    markup = _packs_markup(banned, stickers, allowed)
    if len(text) > 3500:
        await msg.reply_html(text[:3500], disable_web_page_preview=True, reply_markup=markup)
        return
    await msg.reply_html(text, disable_web_page_preview=True, reply_markup=markup)


async def on_packs_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data or not query.data.startswith("pk:"):
        return
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        await query.answer()
        return
    if not await is_group_admin(update, user.id):
        await query.answer("Only admins can change this list.", show_alert=True)
        return
    parts = query.data.split(":")
    if len(parts) != 3:
        await query.answer()
        return
    _, kind, raw = parts
    chat_id = chat.id
    banned = db.list_banned_packs(chat_id)
    allowed = db.list_allowed_packs(chat_id)
    stickers = db.list_banned_stickers(chat_id)
    if kind == "b":
        target = _pick_index(banned, raw)
        if not target:
            await query.answer("That pack is already gone. Refresh the list.", show_alert=True)
        else:
            db.allow_pack(chat_id, target)
            await query.answer(f"Allowed {target}")
    elif kind == "s":
        target = _pick_index(stickers, raw)
        if not target:
            await query.answer("That sticker is already gone.", show_alert=True)
        else:
            db.allow_sticker(target)
            db.unban_sticker(chat_id, target)
            await query.answer("Allowed that sticker")
    elif kind == "a":
        target = _pick_index(allowed, raw)
        if not target:
            await query.answer("Already removed.", show_alert=True)
        else:
            db.unallow_pack(chat_id, target)
            await query.answer(f"Removed whitelist for {target}")
    else:
        await query.answer()
        return
    banned = db.list_banned_packs(chat_id)
    allowed = db.list_allowed_packs(chat_id)
    stickers = db.list_banned_stickers(chat_id)
    if not banned and not allowed and not stickers:
        try:
            await query.edit_message_text("List is empty. Nothing banned or whitelisted.")
        except TelegramError:
            pass
        return
    text = _packs_text(banned, stickers, allowed)
    markup = _packs_markup(banned, stickers, allowed)
    try:
        await query.edit_message_text(
            text[:3500],
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=markup,
        )
    except TelegramError:
        pass


async def cmd_setplaceholder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_owner(update):
        return
    await update.effective_message.reply_text(
        "I no longer post a replacement sticker. After a delete I tag the sender in a text notice."
    )


async def cmd_clearplaceholder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_owner(update):
        return
    db.set_placeholder(update.effective_chat.id, None)
    await update.effective_message.reply_text(
        "Noted. Removal notices stay as a tagged message — no sticker is posted."
    )


async def cmd_forgive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_admin(update):
        return
    target = await resolve_target(update, context)
    if not target:
        await update.effective_message.reply_text(f"Who? {TARGET_HINT}")
        return
    db.reset_strikes(update.effective_chat.id, target.id)
    await update.effective_message.reply_html(f"Cleared warnings for {mention(target)}.")


async def cmd_strikes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_admin(update):
        return
    target = await resolve_target(update, context)
    if not target:
        await update.effective_message.reply_text(f"Who? {TARGET_HINT}")
        return
    count, kick_on_next = db.get_strikes(update.effective_chat.id, target.id)
    approved = db.is_approved(update.effective_chat.id, target.id)
    trusted = is_immune(target.id, update.effective_chat.id)
    await update.effective_message.reply_html(
        f"{mention(target)}\n"
        f"Warnings: {count}\n"
        f"Kick on next: {kick_on_next}\n"
        f"Approved: {approved}\n"
        f"Do not punish: {trusted}"
    )


async def cmd_makeadmin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_owner(update):
        return
    target = await resolve_target(update, context)
    if not target:
        await update.effective_message.reply_text(
            "Who should I make admin? Reply to one of their messages, "
            f"or send {cmd('makeadmin')} @username.\n"
            "If they are already admin, drop them in Telegram first "
            "(I can only change people I promoted)."
        )
        return
    if target.is_bot:
        await update.effective_message.reply_text("I cannot promote a bot this way.")
        return
    try:
        await promote_limited(context, update.effective_chat, target.id)
        db.reset_strikes(update.effective_chat.id, target.id)
        await update.effective_message.reply_html(
            f"{mention(target)} is now admin via this bot.\n"
            "They cannot add/remove admins or kick the bot. "
            "If they hit 3 sticker warnings, I can drop them."
        )
    except (TelegramError, ValueError) as exc:
        log.exception("makeadmin failed")
        await update.effective_message.reply_text(getattr(exc, "message", None) or str(exc))
    except Exception:
        log.exception("makeadmin failed")
        await update.effective_message.reply_text(
            "Failed. Drop them in Telegram first, give me Add admins, then try again."
        )


async def cmd_dropadmin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_owner(update):
        return
    target = await resolve_target(update, context)
    if not target:
        await update.effective_message.reply_text(f"Who? {TARGET_HINT}")
        return
    if await is_owner(update, target.id):
        await update.effective_message.reply_text("I will not drop an owner.")
        return
    ok = await demote(context, update.effective_chat.id, target.id)
    await update.effective_message.reply_text(
        "Admin rights removed." if ok else "Could not drop them."
    )


async def cmd_dropadmins(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_owner(update):
        return
    args = [a.lower() for a in (context.args or [])]
    if "confirm" not in args:
        await update.effective_message.reply_text(
            "This drops every admin I can, except the group creator and owners.\n"
            f"Then use {cmd('makeadmin')} on the people who should be admin again.\n\n"
            f"Send {cmd('dropadmins')} confirm to run it."
        )
        return
    ok_count, failed = await demote_all_admins(context, update.effective_chat.id)
    if failed:
        await update.effective_message.reply_html(
            f"Dropped {ok_count} admin(s).\n"
            f"Could not drop (make them admin with {cmd('makeadmin')} next time):\n"
            + "\n".join(f"• {name}" for name in failed)
        )
        return
    await update.effective_message.reply_text(
        f"Dropped {ok_count} admin(s). Reply {cmd('makeadmin')} to each person I should make admin."
        if ok_count
        else "No admins to drop (or I cannot drop the ones that are left)."
    )


async def cmd_kick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_admin(update):
        return
    target = await resolve_target(update, context)
    if not target:
        await update.effective_message.reply_text(f"Who? {TARGET_HINT}")
        return
    if await is_owner(update, target.id):
        await update.effective_message.reply_text("I will not kick an owner.")
        return
    reason = "manual kick"
    args = context.args or []
    if update.effective_message.reply_to_message:
        extra = " ".join(args).strip()
        if extra:
            reason = extra
    elif len(args) > 1:
        reason = " ".join(args[1:]).strip() or reason
    ok = await kick(context, update.effective_chat.id, target.id)
    await announce_kick(context, update.effective_chat, target, reason, ok)


async def _log_label(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> str:
    try:
        dest = await context.bot.get_chat(chat_id)
        name = escape(dest.title or dest.full_name or str(chat_id))
        return f"<b>{name}</b> (<code>{chat_id}</code>)"
    except Exception:
        return f"<code>{chat_id}</code>"


async def cmd_setlog(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_owner(update):
        return
    msg = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    args = [a.lower() for a in (context.args or [])]

    if args and args[0] in {"done", "clear", "cancel"}:
        _PENDING_LOG.pop(user.id, None)
        await msg.reply_text("Cleared the pending log chat.")
        return

    if args and args[0] in {"here", "this"}:
        _PENDING_LOG[user.id] = chat.id
        await msg.reply_html(
            "This chat is the <b>log inbox</b>. It can receive from more than one group.\n"
            f"Open each <b>main</b> group and send <code>{cmd('setlog')}</code> there.\n"
            f"To use a different inbox later, send <code>{cmd('setlog')} here</code> in that other log chat first."
        )
        return

    target = forwarded_chat(msg.reply_to_message) or forwarded_chat(msg)
    if target is None and context.args and args[0] not in {"status", "show"}:
        raw = context.args[0].strip()
        try:
            if raw.lstrip("-").isdigit():
                target = await context.bot.get_chat(int(raw))
            else:
                if not raw.startswith("@"):
                    raw = f"@{raw}"
                target = await context.bot.get_chat(raw)
        except Exception:
            await msg.reply_text(
                "Could not find that chat. Add me there first, then try again."
            )
            return
    if target is None and (not args or args[0] not in {"status", "show"}):
        pending_id = _PENDING_LOG.get(user.id)
        if pending_id and pending_id != chat.id:
            try:
                target = await context.bot.get_chat(pending_id)
            except Exception:
                target = None
    if target is None:
        current = db.get_log_chat(chat.id)
        if current:
            current_txt = f"This group logs to {await _log_label(context, current)}."
        else:
            current_txt = "This group has no log chat yet."
        pending_id = _PENDING_LOG.get(user.id)
        pending_txt = ""
        if pending_id:
            pending_txt = f"\nPending inbox: {await _log_label(context, pending_id)}"
        await msg.reply_html(
            f"{current_txt}{pending_txt}\n\n"
            "Each main group has its own link. One log chat can take many groups.\n"
            f"1. In the <b>log</b> chat send <code>{cmd('setlog')} here</code>\n"
            f"2. In a <b>main</b> group send <code>{cmd('setlog')}</code>\n"
            "3. Repeat step 2 in every other main group that should use that same log.\n"
            f"For a second log inbox, <code>{cmd('setlog')} here</code> there, then "
            f"<code>{cmd('setlog')}</code> in the mains that should use it.\n"
            f"<code>{cmd('unsetlog')}</code> only unlinks <b>this</b> group. "
            f"<code>{cmd('setlog')} done</code> clears the pending inbox."
        )
        return
    if target.id == chat.id:
        await msg.reply_text("Use a different group or channel than this one.")
        return
    title = escape(chat.title or "group")
    try:
        await context.bot.send_message(
            target.id,
            f"Logging punishments from <b>{title}</b> here.",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception:
        log.exception("setlog test send failed")
        await msg.reply_text(
            "I cannot post there. Add me as admin with Post messages (channel) "
            f"or as a member who can send messages (group), then {cmd('setlog')} again."
        )
        return
    db.set_log_chat(chat.id, target.id)
    dest = escape(target.title or str(target.id))
    await msg.reply_html(
        f"This group now logs to <b>{dest}</b>.\n"
        f"Other groups are unchanged. Send <code>{cmd('setlog')}</code> in another main group "
        f"to point that one at the same inbox, or <code>{cmd('setlog')} here</code> in a different log chat first."
    )


async def cmd_unsetlog(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_owner(update):
        return
    db.set_log_chat(update.effective_chat.id, None)
    await update.effective_message.reply_text(
        "Log chat cleared for this group only. Other groups keep their own logs."
    )


async def cmd_setkickmsg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_owner(update):
        return
    text = command_payload(update)
    reply = update.effective_message.reply_to_message
    if not text and reply:
        text = (reply.text or reply.caption or "").strip()
    if not text:
        current = db.get_kick_message(update.effective_chat.id) or DEFAULT_KICK_MSG
        await update.effective_message.reply_text(
            f"Send {cmd('setkickmsg')} with your text, or reply to a message.\n\n"
            "Placeholders:\n"
            "{user} {name} {username} {id} {reason} {chat}\n\n"
            f"Current:\n{current}"
        )
        return
    db.set_kick_message(update.effective_chat.id, text)
    await update.effective_message.reply_text("Kick message saved.")


async def cmd_kickmsg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_owner(update):
        return
    current = db.get_kick_message(update.effective_chat.id) or DEFAULT_KICK_MSG
    await update.effective_message.reply_text(f"Current kick message:\n{current}")


async def cmd_clearkickmsg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_owner(update):
        return
    db.set_kick_message(update.effective_chat.id, None)
    await update.effective_message.reply_text(
        f"Kick message reset to default:\n{DEFAULT_KICK_MSG}"
    )
