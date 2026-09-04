from __future__ import annotations

import logging
import random

from telegram import Update, User
from telegram.ext import ContextTypes

from bot.moderation import mention, require_group

log = logging.getLogger(__name__)

# {who} is replaced with a clickable mention.
SCOLDS = (
    "{who}, sit down. The group has seen this movie, and the acting is not getting better.",
    "{who}, that was a choice. A bold one. A wrong one. Please undo it in your soul.",
    "{who}, I am not angry. I am just disappointed in 4K.",
    "{who}, we need you to log off, drink water, and come back as a better person.",
    "{who}, the group chat is a public place, not your inner monologue with the volume stuck on 11.",
    "{who}, even the silent members looked up for that. Please never again.",
    "{who}, if nonsense was a sport, you would be carrying the national flag.",
    "{who}, take this as a friendly notice from management: you are on thin ice, and the ice is tired.",
    "{who}, I have seen children in traffic with more self-control.",
    "{who}, delete that energy. Not the message. The energy.",
    "{who}, the elders of this group have voted. The result is: behave.",
    "{who}, you are one more stunt away from becoming a cautionary tale.",
    "{who}, please find the nearest wall, apologize to it, and then apologize to us.",
    "{who}, that was not chaotic-good. That was chaotic-unemployed.",
    "{who}, we are begging you, in a civilized way, to touch grass and leave the keyboard alone.",
    "{who}, your brain sent the message before it finished loading. Wait for 100% next time.",
    "{who}, this is your official scolding. Frame it. Then do better.",
    "{who}, if you keep this up, even the bot will start pretending it is offline.",
    "{who}, the group is not a courtroom, but if it was, you would be losing.",
    "{who}, go stand in the corner of the internet until the vibe improves.",
    "{who}, that take was so leftover it should have been in the fridge.",
    "{who}, we like you. We just like you more when you are quiet for three minutes.",
    "{who}, consider this a verbal timeout. No stickers. No speeches. Just reflection.",
    "{who}, even auto-correct tried to stop you and you overrode it. Incredible. Stop.",
    "{who}, you have been weighed, you have been measured, and you have been found loud.",
    "{who}, please return your personality to factory settings. The custom firmware is buggy.",
    "{who}, this is the scolding of the day and you won it fair and square.",
    "{who}, if chaos needed a brand ambassador, HR would still reject this application.",
    "{who}, unplug, count to ten, and come back without the extra plot twist.",
    "{who}, the group has one brain cell on duty tonight and you just sent it on leave.",
)

_bags: dict[int, list[int]] = {}


def _next_scold(chat_id: int) -> str:
    bag = _bags.get(chat_id)
    if not bag:
        bag = list(range(len(SCOLDS)))
        random.shuffle(bag)
        _bags[chat_id] = bag
    return SCOLDS[bag.pop()]


async def resolve_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> User | None:
    msg = update.effective_message
    if not msg:
        return None
    reply = msg.reply_to_message
    if reply and reply.from_user and not reply.from_user.is_bot:
        return reply.from_user
    text = msg.text or msg.caption or ""
    if msg.entities:
        for entity in msg.entities:
            if entity.type == "text_mention" and entity.user:
                return entity.user
            if entity.type == "mention":
                uname = text[entity.offset : entity.offset + entity.length]
                try:
                    chat = await context.bot.get_chat(uname)
                    return User(
                        id=chat.id,
                        first_name=chat.first_name or uname,
                        is_bot=False,
                        username=chat.username,
                    )
                except Exception:
                    log.exception("scold username lookup failed")
    args = list(context.args or [])
    if args and args[0].lstrip("-").isdigit():
        uid = int(args[0])
        try:
            member = await context.bot.get_chat_member(update.effective_chat.id, uid)
            return member.user
        except Exception:
            return User(id=uid, first_name=str(uid), is_bot=False)
    return None


async def cmd_scold(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group(update):
        return
    msg = update.effective_message
    target = await resolve_member(update, context)
    if not target:
        await msg.reply_text(
            "Reply to someone, or add their username.\n"
            "Example: /scold @username"
        )
        return
    if target.is_bot:
        await msg.reply_text("The bot has already suffered enough.")
        return
    line = _next_scold(update.effective_chat.id).format(who=mention(target))
    await msg.reply_html(line, disable_web_page_preview=True)
