"""Bot admin-rights checks and clear, non-silent failure messages."""
from __future__ import annotations

import logging
import time
from typing import Iterable

from telegram import ChatMember
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import ContextTypes

from bot import tg

log = logging.getLogger(__name__)

# Right key → (ChatMember attribute, human label for admins)
RIGHTS: dict[str, tuple[str, str]] = {
    "delete": ("can_delete_messages", "Delete messages"),
    "restrict": ("can_restrict_members", "Ban users"),
    "promote": ("can_promote_members", "Add new admins"),
    "pin": ("can_pin_messages", "Pin messages"),
    "info": ("can_change_info", "Change group info"),
    "invite": ("can_invite_users", "Invite users via link"),
    "video": ("can_manage_video_chats", "Manage video chats"),
    "manage": ("can_manage_chat", "Manage chat"),
}

_ALERT_COOLDOWN = 300.0  # one clear notice per right per chat every 5 min
_last_alert: dict[tuple[int, str], float] = {}


def friendly_error(exc: BaseException) -> str:
    """Plain-English message for members/admins — never leave them guessing."""
    raw = (getattr(exc, "message", None) or str(exc) or "").strip()
    low = raw.lower()

    if isinstance(exc, Forbidden) or "bot was blocked" in low or "bot is not a member" in low:
        return (
            "I can't do that right now — I may have been removed, muted, "
            "or blocked from posting here."
        )
    if "not enough rights" in low or "chat_admin_required" in low or "need administrator" in low:
        return (
            "I'm missing an admin permission for that. "
            "Promote me with Delete messages / Ban users / Add new admins as needed, then try again."
        )
    if "can't delete" in low or "message can't be deleted" in low:
        return "I couldn't delete that message. I need the Delete messages permission."
    if "user is an administrator" in low or "can't remove chat owner" in low:
        return "I can't moderate that person while they're an admin/owner I didn't promote."
    if "can't restrict self" in low or "can't demote chat creator" in low:
        return "Telegram won't let me change that account."
    if "reply message not found" in low or "message to delete not found" in low:
        return "That message is already gone."
    if "flood" in low or "retry after" in low or "too many requests" in low:
        return "Telegram asked me to slow down — try again in a few seconds."
    if isinstance(exc, BadRequest) and raw:
        # Keep short; avoid dumping huge internal traces.
        return f"That didn't work: {raw[:180]}"
    if isinstance(exc, TelegramError) and raw:
        return f"Telegram rejected that: {raw[:180]}"
    return "That failed. Check my admin rights in this group and try again."


async def get_bot_member(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    bot = context.bot
    return await tg.call(
        lambda: bot.get_chat_member(chat_id, bot.id),
        chat_id=chat_id,
    )


async def bot_is_admin(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> bool:
    try:
        me = await get_bot_member(context, chat_id)
    except TelegramError:
        return False
    return me.status in (ChatMember.ADMINISTRATOR, ChatMember.OWNER)


async def missing_right_labels(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    need: Iterable[str],
) -> list[str]:
    """Human labels for rights the bot lacks. Empty list = ok."""
    try:
        me = await get_bot_member(context, chat_id)
    except TelegramError as exc:
        return [friendly_error(exc)]

    if me.status == ChatMember.OWNER:
        return []
    if me.status != ChatMember.ADMINISTRATOR:
        return ["Admin status in this group"]

    missing: list[str] = []
    for key in need:
        spec = RIGHTS.get(key)
        if not spec:
            continue
        attr, label = spec
        if not bool(getattr(me, attr, False)):
            missing.append(label)
    return missing


def format_missing(missing: list[str]) -> str:
    if not missing:
        return ""
    if missing == ["Admin status in this group"]:
        return (
            "I'm not an admin here. Promote me with Delete messages, Ban users, "
            "and Add new admins so I can moderate."
        )
    joined = ", ".join(missing)
    return (
        f"I'm missing admin permission(s): <b>{joined}</b>. "
        "Open group info → Administrators → me, turn those on, then try again."
    )


async def ensure_rights(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    *need: str,
    alert: bool = True,
    alert_key: str | None = None,
) -> str | None:
    """Return None if ok, else an HTML explanation. Optionally posts a throttled alert."""
    missing = await missing_right_labels(context, chat_id, need)
    if not missing:
        return None
    text = format_missing(missing)
    if alert:
        await alert_problem(context, chat_id, alert_key or ",".join(need), text)
    return text


async def alert_problem(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    key: str,
    html: str,
    *,
    force: bool = False,
) -> bool:
    """Tell the group clearly — at most once per key every few minutes (unless force)."""
    now = time.time()
    stamp_key = (chat_id, key)
    if not force and now - _last_alert.get(stamp_key, 0.0) < _ALERT_COOLDOWN:
        return False
    _last_alert[stamp_key] = now
    try:
        await tg.send_message(
            context.bot,
            chat_id,
            html,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return True
    except Exception:
        log.warning("Could not post rights alert in chat %s", chat_id, exc_info=True)
        return False


async def alert_failure(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    key: str,
    exc: BaseException,
    *,
    prefix: str = "That action failed",
) -> None:
    await alert_problem(
        context,
        chat_id,
        key,
        f"{prefix}: {friendly_error(exc)}",
    )


def format_bot_rights_report(me) -> str:
    """HTML report for /rights."""
    if me.status == ChatMember.OWNER:
        return "<b>My rights</b>\nI'm listed as the group owner (unusual for a bot)."
    if me.status != ChatMember.ADMINISTRATOR:
        return (
            "<b>My rights</b>\n"
            "I'm <b>not an admin</b> here — moderation, deletes, and many games "
            "features that clean messages won't work until you promote me."
        )

    lines = ["<b>My rights in this group</b>", ""]
    for _key, (attr, label) in RIGHTS.items():
        ok = bool(getattr(me, attr, False))
        lines.append(f"{'✅' if ok else '❌'} {label}")

    needed = ("delete", "restrict", "promote")
    missing = [
        RIGHTS[k][1]
        for k in needed
        if not bool(getattr(me, RIGHTS[k][0], False))
    ]
    lines.append("")
    if missing:
        lines.append(
            "For full moderation I still need: <b>"
            + ", ".join(missing)
            + "</b>."
        )
    else:
        lines.append("Core moderation rights look good.")
    return "\n".join(lines)
