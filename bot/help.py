from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from bot.invite import ADD_TEXT, bot_username, url_buttons

PAGES: dict[str, str] = {
    "index": (
        "<b>King's Hand</b>\n"
        "Anyone in the group can use these:\n\n"
        "<b>/help</b> — this guide\n"
        "<b>/whisper</b> — reply, then your private note. Only they can open it.\n"
        "<blockquote>/whisper stay after the call</blockquote>\n"
        "<b>/scold</b> — playful roast. Reply or add their username.\n"
        "<blockquote>/scold\n/scold @username</blockquote>\n"
        "<b>/stats</b> — your role, approval, warnings, and what you can do\n"
        "<b>/notag</b> — skip group pings · <b>/tagme</b> — get them again\n\n"
        "<b>Calculator</b> — no command. Send only the sum.\n"
        "<blockquote>2+2\n(5*3)/2\n2^8</blockquote>\n\n"
        "Staff commands are in the buttons below."
    ),
    "you": (
        "<b>Everyone</b>\n"
        "These work for every member.\n\n"
        "<b>/help</b> — this guide\n"
        "<b>/whisper</b> — reply, then your private note. Only they can open it.\n"
        "<blockquote>/whisper stay after the call</blockquote>\n"
        "<b>/scold</b> — playful roast. Reply or add their username.\n"
        "<blockquote>/scold\n/scold @username</blockquote>\n"
        "<b>/stats</b> — your role, approval, warnings, and what you can do\n"
        "<b>/notag</b> — skip group pings · <b>/tagme</b> — get them again\n\n"
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
        "They can still use /whisper, /scold, /stats, /notag, and the calculator.\n"
        "They cannot lock the chat, kick, or ping everyone.\n\n"
        "Only the owner can /approve or /unapprove someone."
    ),
    "admins": (
        "<b>Admins</b>\n"
        "Everything members have, plus keeping the room in order.\n\n"
        "<b>/lock</b> · <b>/unlock</b> — freeze or open chat for members\n"
        "<b>/flood</b> — mute people who burst messages\n"
        "<blockquote>/flood on\n/flood 6 4\n/floodmute 10m\n/flood off</blockquote>\n"
        "<b>/report</b> — reply to a sticker to ban that pack\n"
        "<b>/kick</b> — remove someone (reply to them)\n"
        "<b>/strikes</b> · <b>/forgive</b> — see or clear warnings\n"
        "<b>/tag</b> — set a nickname. <code>/tag null</code> clears it\n"
        "<b>/joins</b> — how many joins I have seen\n"
        "<b>/stats</b> — reply to someone to see their status\n\n"
        "Admins still cannot /tagall, /approve, /trust, /makeadmin, or raid tools."
    ),
    "owner": (
        "<b>Owner</b>\n"
        "Everything admins have, plus the keys.\n\n"
        "<b>/tagall</b> — ping everyone without listing names\n"
        "<blockquote>/tagall\n/tagall meeting in 5\n/tagall invite</blockquote>\n"
        "<b>/approve</b> · <b>/unapprove</b> · <b>/trust</b> · <b>/untrust</b>\n"
        "<b>/makeadmin</b> · <b>/dropadmin</b> · <b>/dropadmins confirm</b>\n"
        "<b>/raidmode</b> · <b>/purgejoins</b>\n"
        "<b>/setlog</b> — in the log chat: <code>/setlog here</code>. "
        "In each main group: <code>/setlog</code>\n"
        "<b>/setplaceholder</b> · <b>/setkickmsg</b>\n"
        "<b>/allowpack</b> · <b>/allowsticker</b> — undo a false sticker ban\n"
        "<b>/addowner</b> · <b>/removeowner</b> · <b>/owners</b>\n\n"
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
    return PAGES.get(key, PAGES["index"])


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
    "calc": "you",
    "lock": "admins",
    "flood": "admins",
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
        key = _ALIASES.get(raw, raw if raw in PAGES else "index")
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
