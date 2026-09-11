from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from bot import db
from bot.commands import cmd
from bot.invite import ADD_TEXT, bot_username, url_buttons
from bot.moderation import is_group_admin, is_owner


def _pages() -> dict[str, str]:
    return {
        "index": (
            "<b>King's Hand</b>\n"
            "Group guide. Tap a button for the full list. "
            "Staff pages only open if you are an admin or owner.\n\n"
            f"<b>{cmd('help')}</b> — this menu\n"
            f"<b>{cmd('rules')}</b> · <b>{cmd('id')}</b> · <b>{cmd('stats')}</b>\n"
            f"<b>{cmd('notes')}</b> · send <code>#name</code> for a saved note\n"
            f"<b>{cmd('staff')}</b> — reply to ping admins\n"
            f"<b>{cmd('whatsnew')}</b> — latest official update\n\n"
            "<b>Fun</b> — whisper, scold, calculator (tap Fun)\n\n"
            "Mute / ban / lock / raid tools are not listed here. "
            "Admins and owners see extra buttons."
        ),
        "you": (
            "<b>Everyone</b>\n"
            "These work for every member.\n\n"
            f"<b>{cmd('help')}</b> — this guide\n"
            f"<b>{cmd('rules')}</b> — group rules\n"
            f"<b>{cmd('id')}</b> — chat / user ids\n"
            f"<b>{cmd('notes')}</b> · <code>#name</code> — saved notes\n"
            f"<b>{cmd('staff')}</b> — reply to ping admins\n"
            f"<b>{cmd('whisper')}</b> — reply, then a private note only they can open\n"
            f"<blockquote>{cmd('whisper')} stay after the call</blockquote>\n"
            f"<b>{cmd('scold')}</b> — playful roast. Reply or @username\n"
            f"<b>{cmd('stats')}</b> — role, immune, trust, approval, warnings "
            "(admins: reply or tag someone)\n"
            f"<b>{cmd('notag')}</b> · <b>{cmd('tagme')}</b> — skip or join group pings\n"
            f"<b>{cmd('whatsnew')}</b> — this release · "
            f"<code>{cmd('whatsnew')} history</code>\n\n"
            "<b>Calculator</b> — send only the sum, no command.\n"
            "<blockquote>2+2\n(5*3)/2\n2^8</blockquote>"
        ),
        "fun": (
            "<b>Fun</b>\n\n"
            f"<b>{cmd('whisper')}</b> — reply to someone, then your secret. "
            "Only they can open it.\n"
            f"<blockquote>{cmd('whisper')} stay after the call</blockquote>\n"
            f"<b>{cmd('scold')}</b> — funny roast. Reply or add @username. "
            "A new line each time.\n"
            f"<blockquote>{cmd('scold')}\n{cmd('scold')} @username</blockquote>\n"
            "<b>Calculator</b> — no slash. Send only the sum.\n"
            "<blockquote>2+2\n(5*3)/2\n2^8</blockquote>\n"
            f"<b>{cmd('whatsnew')}</b> — what changed in this bot"
        ),
        "notes": (
            "<b>Notes</b>\n"
            "Members can read notes staff saved.\n\n"
            f"<b>{cmd('notes')}</b> — list names\n"
            f"<b>{cmd('get')} name</b> — one note\n"
            "Or send <code>#name</code> in the group.\n\n"
            f"<b>{cmd('rules')}</b> — group rules\n"
            f"<b>{cmd('id')}</b> — ids\n\n"
            "Staff set notes with save / filter / blacklist (Staff button, admins only)."
        ),
        "stickers": (
            "<b>Stickers</b>\n"
            "I auto-detect NSFW / vulgar stickers, delete them, and warn. "
            f"Reply <code>{cmd('report')}</code> to ban a pack. "
            f"Owner: {cmd('unreport')} / {cmd('unreportall')} then report again by hand.\n\n"
            f"Members: reply <code>{cmd('staff')}</code> to ping admins.\n\n"
            "Pack bans, allow-list, and strikes are <b>admin tools</b> — "
            "they are not shown to everyone. Admins: open Staff."
        ),
        "approved": (
            "<b>Approved</b>\n"
            "Not extra commands — one extra sticker chance.\n\n"
            "Unapproved members: warnings, then a kick. "
            "Approved: one bypass, then the next banned sticker still kicks.\n\n"
            f"Only the owner can {cmd('approve')} / {cmd('unapprove')}."
        ),
        "admins": (
            "<b>Staff — admins</b>\n"
            "Sensitive. Members do not get this page.\n\n"
            f"<b>{cmd('lock')}</b> · <b>{cmd('unlock')}</b>\n"
            f"<b>{cmd('flood')}</b> · <b>{cmd('floodmute')}</b>\n"
            f"<blockquote>{cmd('flood')} on\n{cmd('flood')} 6 4\n"
            f"{cmd('floodmute')} 10m</blockquote>\n"
            f"<b>{cmd('welcome')}</b> · <b>{cmd('goodbye')}</b> · "
            f"<b>{cmd('verify')}</b> · <b>{cmd('unverified')}</b>\n"
            f"<b>{cmd('zombies')}</b> · <b>{cmd('setrules')}</b> · "
            f"<b>{cmd('cleanservice')}</b>\n"
            f"<b>{cmd('ban')}</b> · <b>{cmd('unban')}</b> · <b>{cmd('mute')}</b> · "
            f"<b>{cmd('unmute')}</b> · <b>{cmd('kick')}</b>\n"
            f"<blockquote>{cmd('ban')} @user 1d reason\n{cmd('mute')} 10m</blockquote>\n"
            f"<b>{cmd('warn')}</b> · <b>{cmd('warns')}</b> · "
            f"<b>{cmd('resetwarns')}</b> · <b>{cmd('warnlimit')}</b>\n"
            f"<b>{cmd('pin')}</b> · <b>{cmd('unpin')}</b> · <b>{cmd('del')}</b> · "
            f"<b>{cmd('purge')}</b>\n"
            f"<b>{cmd('save')}</b> · <b>{cmd('filter')}</b> · <b>{cmd('blacklist')}</b>\n"
            f"<b>{cmd('report')}</b> · <b>{cmd('packs')}</b> · "
            f"<b>{cmd('strikes')}</b> · <b>{cmd('forgive')}</b>\n"
            f"<b>{cmd('tag')}</b> · <b>{cmd('joins')}</b>\n\n"
            f"Not for admins: {cmd('unreport')}, {cmd('unreportall')}, {cmd('tagall')}, "
            f"{cmd('approve')}, {cmd('trust')}, {cmd('makeadmin')}, raid tools — owner only."
        ),
        "owner": (
            "<b>Owner only</b>\n"
            "These can remove people, grant admin, or wipe raids. "
            "Not shown to members or normal admins.\n\n"
            f"<b>{cmd('tagall')}</b> — ping everyone\n"
            f"<blockquote>{cmd('tagall')}\n{cmd('tagall')} meeting in 5</blockquote>\n"
            f"<b>{cmd('approve')}</b> · <b>{cmd('unapprove')}</b> · "
            f"<b>{cmd('trust')}</b> · <b>{cmd('untrust')}</b>\n"
            f"<b>{cmd('makeadmin')}</b> · <b>{cmd('dropadmin')}</b> · "
            f"<b>{cmd('dropadmins')} confirm</b>\n"
            f"<b>{cmd('raidmode')}</b> · <b>{cmd('purgejoins')}</b>\n"
            f"<blockquote>{cmd('purgejoins')} 2h\n{cmd('purgejoins')} 2h confirm</blockquote>\n"
            f"<b>{cmd('setlog')}</b> · <b>{cmd('setkickmsg')}</b>\n"
            f"<b>{cmd('allowpack')}</b> · <b>{cmd('allowsticker')}</b> · "
            f"<b>{cmd('unreport')}</b> · <b>{cmd('unreportall')}</b>\n"
            f"<b>{cmd('addowner')}</b> · <b>{cmd('removeowner')}</b> · "
            f"<b>{cmd('owners')}</b>\n"
            f"<b>{cmd('release')} send</b> — official update to people who /start the bot"
        ),
        "add": (
            f"{ADD_TEXT}\n\n"
            "Use the buttons under this message, or send <code>/start</code> here "
            "and pick a group or channel from the list."
        ),
    }


_STAFF_KEYS = frozenset({"admins", "approved"})
_OWNER_KEYS = frozenset({"owner"})


def _is_private(update: Update) -> bool:
    chat = update.effective_chat
    return bool(chat and chat.type == ChatType.PRIVATE)


async def _access(update: Update) -> tuple[bool, bool, bool]:
    private = _is_private(update)
    user = update.effective_user
    if not user:
        return private, False, False
    owner = await is_owner(update, user.id)
    admin = owner or (not private and await is_group_admin(update, user.id))
    return private, admin, owner


def _allowed(key: str, *, private: bool, admin: bool, owner: bool) -> bool:
    if key == "add":
        return private
    if key in _OWNER_KEYS:
        return owner
    if key in _STAFF_KEYS:
        return admin
    return True


def _markup(
    key: str,
    username: str | None,
    *,
    private: bool,
    admin: bool,
    owner: bool,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton("Fun", callback_data="help:fun"),
            InlineKeyboardButton("Notes", callback_data="help:notes"),
        ],
        [
            InlineKeyboardButton("Everyone", callback_data="help:you"),
            InlineKeyboardButton("Stickers", callback_data="help:stickers"),
        ],
    ]
    staff_row: list[InlineKeyboardButton] = []
    if admin:
        staff_row.append(InlineKeyboardButton("Staff", callback_data="help:admins"))
        staff_row.append(InlineKeyboardButton("Approved", callback_data="help:approved"))
    if owner:
        staff_row.append(InlineKeyboardButton("Owner", callback_data="help:owner"))
    if staff_row:
        rows.append(staff_row)
    last: list[InlineKeyboardButton] = []
    if private:
        last.append(InlineKeyboardButton("Add me", callback_data="help:add"))
    if key != "index":
        last.append(InlineKeyboardButton("Overview", callback_data="help:index"))
    if last:
        rows.append(last)
    if key == "add" and private and username:
        rows = list(url_buttons(username).inline_keyboard) + rows
    return InlineKeyboardMarkup(rows)


def _page(key: str) -> str:
    pages = _pages()
    return pages.get(key, pages["index"])


_ALIASES = {
    "you": "you",
    "user": "you",
    "users": "you",
    "member": "you",
    "members": "you",
    "everyone": "you",
    "approved": "approved",
    "admin": "admins",
    "admins": "admins",
    "staff": "admins",
    "owner": "owner",
    "owners": "owner",
    "whisper": "fun",
    "scold": "fun",
    "fun": "fun",
    "calc": "fun",
    "lock": "admins",
    "flood": "admins",
    "welcome": "admins",
    "ban": "admins",
    "mute": "admins",
    "warn": "admins",
    "notes": "notes",
    "rules": "notes",
    "sticker": "stickers",
    "stickers": "stickers",
    "nsfw": "stickers",
    "tag": "admins",
    "nick": "admins",
    "tagall": "owner",
    "add": "add",
    "invite": "add",
    "group": "add",
    "channel": "add",
    "access": "owner",
    "protect": "admins",
    "raid": "owner",
    "overview": "index",
    "whatsnew": "fun",
    "release": "owner",
}


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    key = "index"
    private, admin, owner = await _access(update)
    if context.args:
        raw = context.args[0].lower().strip()
        key = _ALIASES.get(raw, raw if raw in _pages() else "index")
    if not _allowed(key, private=private, admin=admin, owner=owner):
        key = "index"
    if private and update.effective_user:
        db.touch_bot_user(update.effective_user.id)
    username = await bot_username(context)
    await update.effective_message.reply_html(
        _page(key),
        reply_markup=_markup(key, username, private=private, admin=admin, owner=owner),
        disable_web_page_preview=True,
    )


async def on_help_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data or not query.data.startswith("help:"):
        return
    key = query.data.split(":", 1)[-1]
    private, admin, owner = await _access(update)
    if not _allowed(key, private=private, admin=admin, owner=owner):
        if key == "add":
            await query.answer("Open my private chat to add me to a group.", show_alert=True)
            return
        await query.answer("That page is only for admins or the owner.", show_alert=True)
        return
    username = await bot_username(context)
    text = _page(key)
    markup = _markup(key, username, private=private, admin=admin, owner=owner)
    try:
        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=markup,
            disable_web_page_preview=True,
        )
    except BadRequest as exc:
        if "not modified" in str(exc).lower():
            await query.answer("That's this page.")
            return
        await query.answer()
        return
    await query.answer()
