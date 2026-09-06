from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from bot.commands import cmd
from bot.invite import ADD_TEXT, bot_username, url_buttons


def _pages() -> dict[str, str]:
    return {
        "index": (
            "<b>King's Hand</b>\n"
            f"Send <code>{cmd('help')}</code> in the group for this guide. "
            f"Staff tools like <code>{cmd('kick')}</code> and <code>{cmd('lock')}</code> "
            "are in the buttons below.\n"
            f"Private chat: <code>/start</code> and <code>{cmd('help')}</code> work too.\n\n"
            "Anyone in the group can use these:\n\n"
            f"<b>{cmd('help')}</b> — this guide\n"
            f"<b>{cmd('rules')}</b> — group rules\n"
            f"<b>{cmd('id')}</b> — chat / user ids\n"
            f"<b>{cmd('notes')}</b> · <b>#name</b> — saved notes\n"
            f"<b>{cmd('staff')}</b> — reply to ping admins\n"
            f"<b>{cmd('whisper')}</b> — reply, then your private note. Only they can open it.\n"
            f"<blockquote>{cmd('whisper')} stay after the call</blockquote>\n"
            f"<b>{cmd('scold')}</b> — playful roast. Reply or add their username.\n"
            f"<blockquote>{cmd('scold')}\n{cmd('scold')} @username</blockquote>\n"
            f"<b>{cmd('gamehelp')}</b> — games: toss, dice, lucky 7, stone-paper, hand cricket\n"
            f"<blockquote>{cmd('daily')} · {cmd('top')} · {cmd('cricket')} @user</blockquote>\n"
            f"<b>{cmd('stats')}</b> — your role, approval, warnings, and what you can do\n"
            f"<b>{cmd('notag')}</b> — skip group pings · <b>{cmd('tagme')}</b> — get them again\n\n"
            "<b>Calculator</b> — no command. Send only the sum.\n"
            "<blockquote>2+2\n(5*3)/2\n2^8</blockquote>\n\n"
            "Staff commands are in the buttons below."
        ),
        "you": (
            "<b>Everyone</b>\n"
            "These work for every member.\n\n"
            f"<b>{cmd('help')}</b> — this guide\n"
            f"<b>{cmd('rules')}</b> — group rules\n"
            f"<b>{cmd('id')}</b> — chat / user ids\n"
            f"<b>{cmd('notes')}</b> · send <code>#name</code> for a saved note\n"
            f"<b>{cmd('staff')}</b> — reply to a message to ping admins\n"
            f"<b>{cmd('whisper')}</b> — reply, then your private note. Only they can open it.\n"
            f"<blockquote>{cmd('whisper')} stay after the call</blockquote>\n"
            f"<b>{cmd('scold')}</b> — playful roast. Reply or add their username.\n"
            f"<blockquote>{cmd('scold')}\n{cmd('scold')} @username</blockquote>\n"
            f"<b>{cmd('gamehelp')}</b> — how to play · {cmd('daily')} streak · {cmd('top')} board\n"
            f"<b>{cmd('stats')}</b> — your role, approval, warnings, and what you can do\n"
            f"<b>{cmd('notag')}</b> — skip group pings · <b>{cmd('tagme')}</b> — get them again\n\n"
            "<b>Calculator</b> — no command. Send only the sum.\n"
            "<blockquote>2+2\n(5*3)/2\n2^8</blockquote>"
        ),
        "approved": (
            "<b>Approved</b>\n"
            "Same commands as everyone. Approval is not extra commands — "
            "it is a sticker chance.\n\n"
            "If a member keeps sending banned stickers, they get warnings, then a kick. "
            "Approved people get <b>one extra chance</b>. The next banned sticker after that "
            "still kicks.\n\n"
            f"They can still use {cmd('whisper')}, {cmd('scold')}, {cmd('gamehelp')}, "
            f"{cmd('stats')}, {cmd('notag')}, and the calculator.\n"
            "They cannot lock the chat, kick, or ping everyone.\n\n"
            f"Only the owner can {cmd('approve')} or {cmd('unapprove')} someone."
        ),
        "admins": (
            "<b>Admins</b>\n"
            "Everything members have, plus keeping the room in order.\n\n"
            f"<b>{cmd('lock')}</b> · <b>{cmd('unlock')}</b> — freeze or open chat for members\n"
            f"<b>{cmd('flood')}</b> — mute people who burst messages\n"
            f"<blockquote>{cmd('flood')} on\n{cmd('flood')} 6 4\n"
            f"{cmd('floodmute')} 10m\n{cmd('flood')} off</blockquote>\n"
            f"<b>{cmd('welcome')}</b> · <b>{cmd('goodbye')}</b> — join/leave messages\n"
            f"<b>{cmd('verify')}</b> · <b>{cmd('unverified')}</b> — join captcha; bots banned\n"
            f"<b>{cmd('zombies')}</b> — deleted accounts; runs daily\n"
            f"<b>{cmd('setrules')}</b> · <b>{cmd('cleanservice')}</b>\n"
            f"<b>{cmd('ban')}</b> · <b>{cmd('unban')}</b> · <b>{cmd('mute')}</b> · "
            f"<b>{cmd('unmute')}</b> · <b>{cmd('kick')}</b>\n"
            f"<blockquote>{cmd('ban')} @user 1d reason\n{cmd('mute')} 10m</blockquote>\n"
            f"<b>{cmd('warn')}</b> · <b>{cmd('warns')}</b> · <b>{cmd('resetwarns')}</b>\n"
            f"<b>{cmd('pin')}</b> · <b>{cmd('unpin')}</b> · <b>{cmd('del')}</b> · "
            f"<b>{cmd('purge')}</b>\n"
            f"<b>{cmd('save')}</b> · <b>{cmd('filter')}</b> · <b>{cmd('blacklist')}</b>\n"
            f"<b>{cmd('report')}</b> — reply to a sticker to ban that pack\n"
            f"<b>{cmd('packs')}</b> — banned packs; tap Allow (no sticker reply)\n"
            f"<b>{cmd('strikes')}</b> · <b>{cmd('forgive')}</b> — sticker warnings\n"
            f"<b>{cmd('tag')}</b> — set a nickname. <code>{cmd('tag')} null</code> clears it\n"
            f"<b>{cmd('joins')}</b> — how many joins I have seen\n"
            f"<b>{cmd('stats')}</b> — reply to someone to see their status\n\n"
            f"Admins still cannot {cmd('tagall')}, {cmd('approve')}, {cmd('trust')}, "
            f"{cmd('makeadmin')}, or raid tools."
        ),
        "owner": (
            "<b>Owner</b>\n"
            "Everything admins have, plus the keys.\n\n"
            f"<b>{cmd('tagall')}</b> — ping everyone without listing names\n"
            f"<blockquote>{cmd('tagall')}\n{cmd('tagall')} meeting in 5\n"
            f"{cmd('tagall')} invite</blockquote>\n"
            f"<b>{cmd('approve')}</b> · <b>{cmd('unapprove')}</b> · "
            f"<b>{cmd('trust')}</b> · <b>{cmd('untrust')}</b>\n"
            f"<b>{cmd('makeadmin')}</b> · <b>{cmd('dropadmin')}</b> · "
            f"<b>{cmd('dropadmins')} confirm</b>\n"
            f"<b>{cmd('raidmode')}</b> · <b>{cmd('purgejoins')}</b>\n"
            f"<b>{cmd('setlog')}</b> — in the log chat: <code>{cmd('setlog')} here</code>. "
            f"In each main group: <code>{cmd('setlog')}</code>\n"
            f"<b>{cmd('setplaceholder')}</b> · <b>{cmd('setkickmsg')}</b>\n"
            f"<b>{cmd('allowpack')}</b> · <b>{cmd('allowsticker')}</b> · "
            f"<b>{cmd('packs')}</b> — undo a false sticker ban\n"
            f"<b>{cmd('addowner')}</b> · <b>{cmd('removeowner')}</b> · <b>{cmd('owners')}</b>\n\n"
            "You are never punished for stickers."
        ),
        "add": (
            f"{ADD_TEXT}\n\n"
            "Use the buttons under this message, or send <code>/start</code> here "
            "and pick a group or channel from the list."
        ),
    }


def _is_private(update: Update) -> bool:
    chat = update.effective_chat
    return bool(chat and chat.type == ChatType.PRIVATE)


def _markup(key: str, username: str | None = None, *, private: bool = False) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton("Approved", callback_data="help:approved"),
            InlineKeyboardButton("Admins", callback_data="help:admins"),
        ],
        [
            InlineKeyboardButton("Owner", callback_data="help:owner"),
        ],
    ]
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
    "owner": "owner",
    "owners": "owner",
    "whisper": "you",
    "scold": "you",
    "fun": "you",
    "games": "you",
    "gamehelp": "you",
    "calc": "you",
    "lock": "admins",
    "flood": "admins",
    "welcome": "admins",
    "ban": "admins",
    "mute": "admins",
    "warn": "admins",
    "notes": "you",
    "rules": "you",
    "sticker": "admins",
    "nsfw": "admins",
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
    "stickers": "admins",
    "overview": "index",
}


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    key = "index"
    private = _is_private(update)
    if context.args:
        raw = context.args[0].lower().strip()
        key = _ALIASES.get(raw, raw if raw in _pages() else "index")
    if key == "add" and not private:
        key = "index"
    username = await bot_username(context)
    await update.effective_message.reply_html(
        _page(key),
        reply_markup=_markup(key, username, private=private),
        disable_web_page_preview=True,
    )


async def on_help_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data or not query.data.startswith("help:"):
        return
    key = query.data.split(":", 1)[-1]
    private = _is_private(update)
    if key == "add" and not private:
        await query.answer("Open my private chat to add me to a group.", show_alert=True)
        return
    username = await bot_username(context)
    text = _page(key)
    markup = _markup(key, username, private=private)
    try:
        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=markup,
            disable_web_page_preview=True,
        )
    except BadRequest as exc:
        if "not modified" in str(exc).lower():
            await query.answer("That's the overview.")
            return
        await query.answer()
        return
    await query.answer()
