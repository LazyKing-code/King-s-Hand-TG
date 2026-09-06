from __future__ import annotations

from html import escape

from telegram import ChatMember, Update, User
from telegram.ext import ContextTypes

from bot import db
from bot.commands import cmd
from bot.config import IMMUNE_IDS, OWNER_IDS
from bot.moderation import is_group_admin, is_owner, mention, require_group, resolve_target


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

    asked = bool(msg.reply_to_message) or bool(context.args)
    if msg.entities:
        asked = asked or any(
            e.type in {"mention", "text_mention"} for e in msg.entities
        )
    target = await resolve_target(update, context)
    looking_up_other = bool(target) and target.id != actor.id
    if looking_up_other and not await is_group_admin(update, actor.id):
        await msg.reply_text("You can only check your own status. Admins can check anyone.")
        return
    if asked and not target:
        await msg.reply_text(
            "I could not find that person. Reply to one of their messages, or tag them."
        )
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
    extra_owner = db.is_extra_owner(chat_id, uid)
    root_owner = uid in OWNER_IDS
    listed_immune = uid in IMMUNE_IDS and not root_owner
    creator = tg == ChatMember.OWNER
    strikes, kick_on_next = db.get_strikes(chat_id, uid)
    nick = db.get_nick(chat_id, uid)
    opted = db.is_tagall_optout(chat_id, uid)

    immune_why: list[str] = []
    if root_owner:
        immune_why.append("bot owner")
    if extra_owner:
        immune_why.append("extra owner")
    if creator:
        immune_why.append("group creator")
    if trusted:
        immune_why.append("trust enabled")
    if listed_immune:
        immune_why.append("immune list")
    immune = bool(immune_why)

    if root_owner or extra_owner or creator:
        rank = "Owner"
    elif admin:
        rank = "Admin"
    elif trusted:
        rank = "Trusted member"
    elif approved:
        rank = "Approved member"
    else:
        rank = "Member"

    can: list[str] = [
        f"Chat, stickers, {cmd('whisper')}, {cmd('scold')}, {cmd('help')}, "
        f"{cmd('rules')}, {cmd('id')}",
        f"{cmd('stats')} for yourself",
        f"{cmd('notag')} or {cmd('tagme')} for group pings",
        "Math in a message by itself, like 2+2",
    ]
    cannot: list[str] = []
    if owner:
        can.append(
            f"Every owner command: {cmd('tagall')}, {cmd('lock')}, {cmd('kick')}, "
            f"{cmd('approve')}, logs, raid tools, {cmd('unreportall')}"
        )
    elif admin:
        can.append(
            f"Admin tools: {cmd('lock')}, {cmd('welcome')}, {cmd('ban')}, {cmd('mute')}, "
            f"{cmd('warn')}, {cmd('kick')}, {cmd('flood')}, {cmd('report')}, "
            f"{cmd('strikes')}, {cmd('tag')}, {cmd('joins')}"
        )
        cannot.append(
            f"Owner-only: {cmd('tagall')}, {cmd('approve')}, {cmd('trust')}, "
            f"{cmd('unreport')}, {cmd('unreportall')}, {cmd('makeadmin')}, {cmd('raidmode')}"
        )
    else:
        cannot.append("Lock, kick, raid, tag-all, and other staff commands")

    if immune:
        can.append("Banned stickers are ignored — no warnings")
    elif approved:
        can.append("One extra chance on a banned sticker; the next one kicks")
        cannot.append("Keep sending banned stickers after that extra chance")
    elif admin:
        cannot.append("Banned stickers: warnings 1–2, demote at 3, kick after that")
    else:
        cannot.append("Banned stickers: warnings, then kick")

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
    lines.append(f"Immune: {'yes — ' + ', '.join(immune_why) if immune else 'no'}")
    lines.append(f"Trusted: {'yes' if trusted else 'no'}")
    lines.append(f"Approved: {'yes' if approved else 'no'}")
    warns = db.get_warns(chat_id, uid)
    lines.append(f"Staff warnings: {warns}/{db.warn_limit(chat_id)}")
    if immune:
        lines.append("Sticker warnings: skipped (immune)")
    elif kick_on_next:
        lines.append("Sticker warnings: next banned sticker kicks")
    else:
        lines.append(f"Sticker warnings: {strikes}/3")
    lines.append("")
    lines.append("<b>Can</b>")
    lines.extend(f"• {item}" for item in can)
    if cannot:
        lines.append("<b>Cannot</b>")
        lines.extend(f"• {item}" for item in cannot)
    return "\n".join(lines)
