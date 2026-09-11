from __future__ import annotations

import logging
import random

from telegram import Update, User
from telegram.ext import ContextTypes

from bot.commands import cmd
from bot.moderation import mention, require_group, resolve_target

log = logging.getLogger(__name__)

# WebSearch is imported dynamically when needed via CallDynamicTool

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
    return await resolve_target(update, context)


async def cmd_scold(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_group(update):
        return
    msg = update.effective_message
    target = await resolve_member(update, context)
    if not target:
        await msg.reply_text(
            "Reply to someone, or add their username.\n"
            f"Example: {cmd('scold')} @username"
        )
        return
    if target.is_bot:
        await msg.reply_text("The bot has already suffered enough.")
        return
    line = _next_scold(update.effective_chat.id).format(who=mention(target))
    await msg.reply_html(line, disable_web_page_preview=True)


async def cmd_ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Answer questions using Wikipedia first, then web search as fallback."""
    msg = update.effective_message
    if not context.args:
        await msg.reply_text(
            f"Ask me anything!\nExample: {cmd('ask')} what is the speed of light"
        )
        return
    
    question = " ".join(context.args)
    status_msg = await msg.reply_text("Searching...")
    
    # Try Wikipedia first (fast, clean, accurate for established facts)
    try:
        import wikipedia
        wikipedia.set_lang("en")
        results = wikipedia.search(question, results=1)
        
        if results:
            summary = wikipedia.summary(results[0], sentences=2, auto_suggest=False)
            await status_msg.edit_text(summary)
            return
            
    except wikipedia.exceptions.DisambiguationError as e:
        # Multiple possible topics - pick the first one
        try:
            summary = wikipedia.summary(e.options[0], sentences=2, auto_suggest=False)
            await status_msg.edit_text(summary)
            return
        except Exception:
            pass  # Fall through to web search
    except wikipedia.exceptions.PageError:
        pass  # Fall through to web search
    except Exception as exc:
        log.warning("Wikipedia search failed: %s", exc)
    
    # Wikipedia failed - fall back to web search (covers recent/obscure topics)
    try:
        await status_msg.edit_text("Searching the web...")
        
        from duckduckgo_search import DDGS
        
        # Get instant answer or text results
        with DDGS() as ddgs:
            # Try instant answer first (direct facts)
            try:
                answer_result = ddgs.answers(question)
                if answer_result:
                    answer = answer_result[0].get("text", "")
                    if answer:
                        await status_msg.edit_text(answer)
                        return
            except Exception:
                pass
            
            # Fall back to text search results
            results = list(ddgs.text(question, max_results=1))
            if results:
                body = results[0].get("body", "")
                if body:
                    # Limit to 2-3 sentences
                    sentences = body.split(". ")
                    short_answer = ". ".join(sentences[:2])
                    if not short_answer.endswith("."):
                        short_answer += "."
                    await status_msg.edit_text(short_answer)
                    return
        
        # If we got here, nothing useful was found
        await status_msg.edit_text(
            "I couldn't find a good answer. Try rephrasing your question."
        )
        
    except Exception as exc:
        log.exception("Web search failed")
        await status_msg.edit_text(
            "Something went wrong while searching. Try again?"
        )
