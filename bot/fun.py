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

    question = " ".join(context.args).strip()
    try:
        status_msg = await msg.reply_text("Searching...")
    except Exception:
        log.exception("ask: could not send status")
        return

    try:
        answer = await _answer_from_wikipedia(question)
        if not answer:
            await status_msg.edit_text("Searching the web...")
            answer = await _answer_from_ddg_instant(question)
        if not answer:
            answer = await _answer_from_web(question)
        if answer:
            await status_msg.edit_text(answer)
        else:
            await status_msg.edit_text(
                "I couldn't find a good answer. Try rephrasing your question."
            )
    except Exception:
        log.exception("ask failed")
        try:
            await status_msg.edit_text(
                "Something went wrong while searching. Try again?"
            )
        except Exception:
            pass


def _shorten(text: str, max_sentences: int = 2) -> str:
    text = " ".join((text or "").split())
    if not text:
        return ""
    parts = [
        p.strip()
        for p in text.replace("? ", "?|").replace("! ", "!|").replace(". ", ".|").split("|")
        if p.strip()
    ]
    if not parts:
        return text[:400]
    out = " ".join(parts[:max_sentences])
    if out[-1] not in ".!?":
        out += "."
    return out


async def _answer_from_wikipedia(question: str) -> str | None:
    try:
        import wikipedia
        from wikipedia.exceptions import DisambiguationError, PageError
    except ImportError:
        log.warning("wikipedia package not installed")
        return None

    try:
        wikipedia.set_lang("en")
        results = wikipedia.search(question, results=3)
        if not results:
            return None

        title = results[0]
        try:
            summary = wikipedia.summary(title, sentences=2, auto_suggest=False)
        except DisambiguationError as exc:
            options = [o for o in (exc.options or []) if o]
            if not options:
                return None
            summary = wikipedia.summary(options[0], sentences=2, auto_suggest=False)
        except PageError:
            if len(results) < 2:
                return None
            summary = wikipedia.summary(results[1], sentences=2, auto_suggest=False)

        summary = _shorten(summary, 2)
        return summary or None
    except Exception as exc:
        log.warning("Wikipedia search failed: %s", exc)
        return None


async def _answer_from_ddg_instant(question: str) -> str | None:
    """DuckDuckGo Instant Answer JSON API (no scraping; good for facts)."""
    try:
        import json
        import urllib.parse
        import urllib.request

        params = urllib.parse.urlencode(
            {
                "q": question,
                "format": "json",
                "no_html": 1,
                "skip_disambig": 1,
            }
        )
        url = f"https://api.duckduckgo.com/?{params}"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "KingsHandBot/1.0 (Telegram; /ask)"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))

        for key in ("AbstractText", "Answer"):
            text = (data.get(key) or "").strip()
            if text:
                return _shorten(text, 2)

        related = data.get("RelatedTopics") or []
        for item in related:
            if not isinstance(item, dict):
                continue
            text = (item.get("Text") or "").strip()
            if text:
                return _shorten(text, 2)
            for nested in item.get("Topics") or []:
                if isinstance(nested, dict):
                    text = (nested.get("Text") or "").strip()
                    if text:
                        return _shorten(text, 2)
        return None
    except Exception as exc:
        log.warning("DDG instant answer failed: %s", exc)
        return None


async def _answer_from_web(question: str) -> str | None:
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        log.warning("duckduckgo-search package not installed")
        return None

    try:
        # Newer duckduckgo-search builds dropped DDGS.answers(); text() is enough.
        with DDGS() as ddgs:
            results = list(ddgs.text(question, max_results=3))
        for item in results:
            body = (item or {}).get("body") or (item or {}).get("title") or ""
            short = _shorten(body, 2)
            if short:
                return short
        return None
    except Exception as exc:
        log.warning("Web search failed: %s", exc)
        return None
