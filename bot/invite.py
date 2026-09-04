from __future__ import annotations

from html import escape

from telegram import (
    ChatAdministratorRights,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    KeyboardButtonRequestChat,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.constants import ChatType
from telegram.ext import ContextTypes

REQ_GROUP = 1
REQ_CHANNEL = 2


def _group_rights() -> ChatAdministratorRights:
    return ChatAdministratorRights(
        is_anonymous=False,
        can_manage_chat=True,
        can_delete_messages=True,
        can_manage_video_chats=False,
        can_restrict_members=True,
        can_promote_members=True,
        can_change_info=False,
        can_invite_users=True,
        can_post_stories=False,
        can_edit_stories=False,
        can_delete_stories=False,
        can_pin_messages=True,
    )


def _channel_rights() -> ChatAdministratorRights:
    return ChatAdministratorRights(
        is_anonymous=False,
        can_manage_chat=True,
        can_delete_messages=True,
        can_manage_video_chats=False,
        can_restrict_members=False,
        can_promote_members=False,
        can_change_info=False,
        can_invite_users=True,
        can_post_stories=False,
        can_edit_stories=False,
        can_delete_stories=False,
        can_post_messages=True,
    )


def _request(request_id: int, channel: bool) -> KeyboardButtonRequestChat:
    rights = _channel_rights() if channel else _group_rights()
    return KeyboardButtonRequestChat(
        request_id=request_id,
        chat_is_channel=channel,
        bot_is_member=False,
        user_administrator_rights=rights,
        bot_administrator_rights=rights,
        request_title=True,
    )


def pick_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("Add to a group", request_chat=_request(REQ_GROUP, False))],
            [KeyboardButton("Add to a channel", request_chat=_request(REQ_CHANNEL, True))],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Pick a group or channel",
    )


def url_buttons(username: str) -> InlineKeyboardMarkup:
    name = (username or "").lstrip("@")
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Add to a group",
                    url=f"https://t.me/{name}?startgroup=true",
                ),
                InlineKeyboardButton(
                    "Add to a channel",
                    url=f"https://t.me/{name}?startchannel=true",
                ),
            ]
        ]
    )


async def bot_username(context: ContextTypes.DEFAULT_TYPE) -> str:
    if context.bot.username:
        return context.bot.username
    me = await context.bot.get_me()
    return me.username or "LazyKingsHand_bot"


ADD_TEXT = (
    "<b>Add me to a group or channel</b>\n"
    "Tap a button, then pick the chat. I will ask for admin so I can delete, "
    "restrict, and remove people when needed."
)


async def on_chat_shared(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    shared = message.chat_shared if message else None
    if not shared:
        return
    title = escape(shared.title or "that chat")
    kind = "channel" if shared.request_id == REQ_CHANNEL else "group"
    await message.reply_html(
        f"I'm in the {kind} <b>{title}</b>.\n"
        "Send <code>/help</code> there. If I still cannot delete messages, make me admin again."
    )
