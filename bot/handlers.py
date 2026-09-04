from __future__ import annotations

import logging

from html import escape

from telegram import Update
from telegram.constants import ChatType
from telegram.ext import ContextTypes

from bot import db
from bot.config import OWNER_IDS
from bot.invite import ADD_TEXT, bot_username, pick_keyboard, url_buttons
from bot.moderation import (
    DEFAULT_KICK_MSG,
    announce_kick,
    apply_punishment,
    demote,
    demote_all_admins,
    format_user,
    forwarded_chat,
    is_immune,
    is_owner,
    kick,
    mention,
    require_group_admin,
    require_group_owner,
    resolve_target,
    send_placeholder,
)

from bot.nsfw import is_nsfw_sticker

log = logging.getLogger(__name__)

TARGET_HINT = "Reply to them, or mention them."
_PENDING_LOG: dict[int, int] = {}


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
            "Send <code>/help</code> to see what you can use here."
            + extra
        )
        return
    username = await bot_username(context)
    await update.effective_message.reply_html(
        "<b>King's Hand</b>\n"
        "I keep the group in order — stickers, spam, whispers, and the occasional scolding.\n\n"
        f"{ADD_TEXT}\n\n"
        "Then send <code>/help</code> in the group to see commands for your role."
        + extra,
        reply_markup=url_buttons(username),
        disable_web_page_preview=True,
    )
    await update.effective_message.reply_text(
        "Or pick a chat you already admin:",
        reply_markup=pick_keyboard(),
    )


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
        await send_placeholder(context, chat.id)
    except Exception:
        log.exception("Placeholder failed")

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
            f"{mention(target)} is an owner, so they stay immune. Use /removeowner first."
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
            "No extra trusted users yet. Reply /trust to someone you do not want punished."
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
        "They stay on /trusted until you /untrust them."
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
            lines.append(f"• {await format_user(context, chat_id, uid)} — /addowner")
    lines.append("The Telegram group creator is always an owner.")
    await update.effective_message.reply_html("\n".join(lines))


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_admin(update):
        return
    reply = update.effective_message.reply_to_message
    if not reply or not reply.sticker:
        await update.effective_message.reply_text("Reply to a sticker with /report and I will ban that pack.")
        return
    set_name = reply.sticker.set_name
    if not set_name:
        db.cache_set(reply.sticker.file_unique_id, True, None)
        try:
            await reply.delete()
        except Exception:
            pass
        await update.effective_message.reply_text("That sticker has no pack; I banned this file only.")
        return
    db.ban_pack(update.effective_chat.id, set_name)
    db.cache_set(reply.sticker.file_unique_id, True, set_name)
    try:
        await reply.delete()
    except Exception:
        pass
    await update.effective_message.reply_text(f"Banned pack: {set_name}")


async def cmd_blockpack(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_report(update, context)


async def cmd_allowpack(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_owner(update):
        return
    reply = update.effective_message.reply_to_message
    if not reply or not reply.sticker or not reply.sticker.set_name:
        await update.effective_message.reply_text("Reply to a sticker with /allowpack.")
        return
    db.allow_pack(update.effective_chat.id, reply.sticker.set_name)
    await update.effective_message.reply_text(f"Allowed pack: {reply.sticker.set_name}")


async def cmd_allowsticker(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_owner(update):
        return
    reply = update.effective_message.reply_to_message
    if not reply or not reply.sticker:
        await update.effective_message.reply_text("Reply to a sticker with /allowsticker.")
        return
    db.allow_sticker(reply.sticker.file_unique_id)
    await update.effective_message.reply_text("That sticker is whitelisted.")


async def cmd_setplaceholder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_owner(update):
        return
    reply = update.effective_message.reply_to_message
    if not reply or not reply.sticker:
        await update.effective_message.reply_text(
            "Reply to the sticker I should post after deleting a banned sticker."
        )
        return
    db.set_placeholder(update.effective_chat.id, reply.sticker.file_id)
    await update.effective_message.reply_text(
        "Saved. I will send this sticker after I delete a banned one.\n"
        "Telegram still shows the original for a moment; this replaces it right after."
    )


async def cmd_clearplaceholder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group_owner(update):
        return
    db.set_placeholder(update.effective_chat.id, None)
    await update.effective_message.reply_text(
        "Custom placeholder cleared. I will use the default removed sticker again."
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
            "Who should I make admin? Reply to them first. "
            "Drop them manually if they are already admin "
            "(the bot can only later demote people it made admin)."
        )
        return
    try:
        await context.bot.promote_chat_member(
            chat_id=update.effective_chat.id,
            user_id=target.id,
            is_anonymous=False,
            can_manage_chat=True,
            can_delete_messages=True,
            can_manage_video_chats=True,
            can_restrict_members=False,
            can_promote_members=False,
            can_change_info=False,
            can_invite_users=True,
            can_pin_messages=True,
            can_manage_topics=False,
        )
        db.reset_strikes(update.effective_chat.id, target.id)
        await update.effective_message.reply_html(
            f"{mention(target)} is now admin via this bot.\n"
            "They cannot add/remove admins or kick the bot. "
            "If they hit 3 sticker warnings, I can drop them."
        )
    except Exception:
        log.exception("makeadmin failed")
        await update.effective_message.reply_text(
            "Failed. Drop them manually first, give me Add admins, then try /makeadmin again."
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
            "Then use /makeadmin on the people who should be admin again.\n\n"
            "Send /dropadmins confirm to run it."
        )
        return
    ok_count, failed = await demote_all_admins(context, update.effective_chat.id)
    if failed:
        await update.effective_message.reply_html(
            f"Dropped {ok_count} admin(s).\n"
            "Could not drop (make them admin with /makeadmin next time):\n"
            + "\n".join(f"• {name}" for name in failed)
        )
        return
    await update.effective_message.reply_text(
        f"Dropped {ok_count} admin(s). Reply /makeadmin to each person I should make admin."
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
            "Open each <b>main</b> group and send <code>/setlog</code> there.\n"
            "To use a different inbox later, send <code>/setlog here</code> in that other log chat first."
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
            "1. In the <b>log</b> chat send <code>/setlog here</code>\n"
            "2. In a <b>main</b> group send <code>/setlog</code>\n"
            "3. Repeat step 2 in every other main group that should use that same log.\n"
            "For a second log inbox, <code>/setlog here</code> there, then <code>/setlog</code> in the mains that should use it.\n"
            "<code>/unsetlog</code> only unlinks <b>this</b> group. <code>/setlog done</code> clears the pending inbox."
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
            "or as a member who can send messages (group), then /setlog again."
        )
        return
    db.set_log_chat(chat.id, target.id)
    dest = escape(target.title or str(target.id))
    await msg.reply_html(
        f"This group now logs to <b>{dest}</b>.\n"
        "Other groups are unchanged. Send <code>/setlog</code> in another main group "
        "to point that one at the same inbox, or <code>/setlog here</code> in a different log chat first."
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
            "Send /setkickmsg with your text, or reply to a message.\n\n"
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
