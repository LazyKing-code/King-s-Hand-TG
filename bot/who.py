from __future__ import annotations

from html import escape

from telegram import ChatMember, Update, User
from telegram.ext import ContextTypes

from bot import db
from bot.config import OWNER_IDS
from bot.fun import resolve_member
from bot.moderation import is_group_admin, is_owner, mention, require_group


async def _telegram_status(update: Update, user_id: int) -> str | None:
    chat = update.effective_chat
    if not chat:
        return None
    try:
        return (await chat.get_member(user_id)).status
    except Exception:
        return None


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group(update):
        return
    chat = update.effective_chat
    actor = update.effective_user
    msg = update.effective_message
    if not chat or not actor or not msg:
        return

    target = await resolve_member(update, context)
    looking_up_other = bool(target) and target.id != actor.id
    if looking_up_other and not await is_group_admin(update, actor.id):
        await msg.reply_text("You can only check your own status. Admins can check anyone.")
        return
    if not target:
        target = actor

    await msg.reply_html(await member_card(update, context, target), disable_web_page_preview=True)


async def member_card(update: Update, context: ContextTypes.DEFAULT_TYPE, user: User) -> str:
    chat = update.effective_chat
    assert chat is not None
    chat_id = chat.id
    uid = user.id
    tg = await _telegram_status(update, uid)
    owner = await is_owner(update, uid)
    admin = await is_group_admin(update, uid)
    approved = db.is_approved(chat_id, uid)
    trusted = db.is_trusted(chat_id, uid)
    strikes, kick_on_next = db.get_strikes(chat_id, uid)
    nick = db.get_nick(chat_id, uid)
    opted = db.is_tagall_optout(chat_id, uid)

    if uid in OWNER_IDS or db.is_extra_owner(chat_id, uid) or tg == ChatMember.OWNER:
        rank = "Owner"
    elif admin:
        rank = "Admin"
    elif approved:
        rank = "Approved member"
    else:
        rank = "Member"

    can: list[str] = [
        "Chat, stickers, /whisper, /scold, /help",
        "/stats for yourself",
        "/notag or /tagme for group pings",
        "Math in a message by itself, like 2+2",
    ]
    cannot: list[str] = []
    if owner:
        can.append("Every owner command: /tagall, /lock, /kick, /approve, logs, raid tools")
    elif admin:
        can.append("Admin tools: /lock, /flood, /report, /kick, /strikes, /tag, /joins")
        cannot.append("Owner-only tools like /tagall, /approve, /trust, /makeadmin, /raidmode")
    else:
        cannot.append("Lock, kick, raid, tag-all, and other staff commands")

    if trusted or owner:
        can.append("Banned stickers are ignored — no warnings")
    elif approved:
        can.append("One extra chance on a banned sticker; the next one kicks")
        cannot.append("Keep sending banned stickers after that extra chance")
    else:
        cannot.append("Banned stickers: warnings, then kick (admins can also be demoted)")

    if opted:
        can.append("Skipped when everyone is tagged")
    else:
        can.append("Included when everyone is tagged")

    lines = [
        f"<b>{mention(user)}</b>",
        f"Role: {rank}",
    ]
    if nick:
        lines.append(f"Tag: {escape(nick)}")
    if tg:
        pretty = {
            ChatMember.OWNER: "group creator",
            ChatMember.ADMINISTRATOR: "admin",
            ChatMember.MEMBER: "member",
            ChatMember.RESTRICTED: "restricted",
            ChatMember.LEFT: "left",
            ChatMember.BANNED: "banned",
        }.get(tg, tg)
        lines.append(f"Telegram: {pretty}")
    lines.append(f"Approved: {'yes' if approved else 'no'}")
    lines.append(f"Trusted (never punished): {'yes' if trusted or owner else 'no'}")
    if kick_on_next:
        lines.append("Warnings: next banned sticker kicks")
    else:
        lines.append(f"Warnings: {strikes}")
    lines.append("")
    lines.append("<b>Can</b>")
    lines.extend(f"• {item}" for item in can)
    if cannot:
        lines.append("<b>Cannot</b>")
        lines.extend(f"• {item}" for item in cannot)
    return "\n".join(lines)
