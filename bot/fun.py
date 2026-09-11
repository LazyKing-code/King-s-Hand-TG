from __future__ import annotations

import logging
import random

from telegram import Update, User
from telegram.ext import ContextTypes

from bot.commands import cmd
from bot.moderation import mention, require_group, resolve_target

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

# Prefer India English results for this group's audience.
_ASK_REGION = "in-en"


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
    """Answer questions via India-focused web search (no Wikipedia)."""
    msg = update.effective_message
    if not context.args:
        await msg.reply_text(
            f"Ask me anything!\nExample: {cmd('ask')} who won the world cup"
        )
        return

    question = " ".join(context.args).strip()
    try:
        status_msg = await msg.reply_text("Searching...")
    except Exception:
        log.exception("ask: could not send status")
        return

    try:
        answer = await _gather_answer(question)
        if answer:
            if len(answer) > 3500:
                answer = answer[:3490].rstrip() + "…"
            await status_msg.edit_text(answer)
        else:
            await status_msg.edit_text(
                "Search came back empty. Try again in a bit, or make the question a bit clearer."
            )
    except Exception:
        log.exception("ask failed")
        try:
            await status_msg.edit_text(
                "Something went wrong while searching. Try again?"
            )
        except Exception:
            pass


async def _gather_answer(question: str) -> str | None:
    """Web search only (India region). Return whatever useful text we get."""
    candidates: list[str] = []
    for getter in (_answer_from_web, _answer_from_ddg_instant):
        try:
            hit = await getter(question)
        except Exception as exc:
            log.warning("ask source %s failed: %s", getter.__name__, exc)
            hit = None
        if hit:
            candidates.append(hit)
            if len(hit) >= 60:
                return hit
    return max(candidates, key=len) if candidates else None


def _clean_text(text: str, max_chars: int = 900) -> str:
    text = " ".join((text or "").split())
    if not text:
        return ""
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return text


def _shorten(text: str, max_sentences: int = 3) -> str:
    text = _clean_text(text, max_chars=1200)
    if not text:
        return ""
    parts = [
        p.strip()
        for p in text.replace("? ", "?|").replace("! ", "!|").replace(". ", ".|").split("|")
        if p.strip()
    ]
    if not parts:
        return text
    out = " ".join(parts[:max_sentences])
    if out[-1] not in ".!?…":
        out += "."
    return out


async def _answer_from_ddg_instant(question: str) -> str | None:
    """DuckDuckGo Instant Answer with India locale."""
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
                "kl": _ASK_REGION,
            }
        )
        url = f"https://api.duckduckgo.com/?{params}"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "KingsHandBot/1.0 (Telegram; /ask)"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))

        bits: list[str] = []
        heading = (data.get("Heading") or "").strip()
        for key in ("Answer", "AbstractText", "Definition"):
            text = (data.get(key) or "").strip()
            if text:
                bits.append(text)
        if not bits:
            for item in data.get("RelatedTopics") or []:
                if not isinstance(item, dict):
                    continue
                text = (item.get("Text") or "").strip()
                if text:
                    bits.append(text)
                for nested in item.get("Topics") or []:
                    if isinstance(nested, dict):
                        text = (nested.get("Text") or "").strip()
                        if text:
                            bits.append(text)
                if len(bits) >= 3:
                    break

        if not bits:
            return None
        body = _shorten(" ".join(bits[:3]), 3)
        if heading and heading.lower() not in body.lower()[:60]:
            return f"{heading}: {body}"
        return body
    except Exception as exc:
        log.warning("DDG instant answer failed: %s", exc)
        return None


async def _answer_from_web(question: str) -> str | None:
    """India-region DuckDuckGo text/news search — return whatever snippets we get."""
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        log.warning("duckduckgo-search package not installed")
        return None

    snippets: list[str] = []
    try:
        with DDGS() as ddgs:
            for backend in ("auto", "lite", "html"):
                try:
                    results = list(
                        ddgs.text(
                            question,
                            region=_ASK_REGION,
                            safesearch="moderate",
                            backend=backend,
                            max_results=5,
                        )
                    )
                except TypeError:
                    results = list(
                        ddgs.text(question, region=_ASK_REGION, max_results=5)
                    )
                except Exception as exc:
                    log.warning("DDG text backend %s failed: %s", backend, exc)
                    continue
                for item in results or []:
                    title = (item or {}).get("title") or ""
                    body = (item or {}).get("body") or ""
                    chunk = _clean_text(
                        f"{title}. {body}" if title and body else (body or title)
                    )
                    if chunk and chunk not in snippets:
                        snippets.append(chunk)
                if snippets:
                    break

            if not snippets:
                try:
                    news = list(
                        ddgs.news(question, region=_ASK_REGION, max_results=3)
                    )
                except TypeError:
                    try:
                        news = list(ddgs.news(question, max_results=3))
                    except Exception as exc:
                        log.warning("DDG news failed: %s", exc)
                        news = []
                except Exception as exc:
                    log.warning("DDG news failed: %s", exc)
                    news = []
                for item in news or []:
                    title = (item or {}).get("title") or ""
                    body = (item or {}).get("body") or ""
                    chunk = _clean_text(
                        f"{title}. {body}" if title and body else (body or title)
                    )
                    if chunk:
                        snippets.append(chunk)
    except Exception as exc:
        log.warning("Web search failed: %s", exc)

    if not snippets:
        return None
    return "\n\n".join(_shorten(s, 2) for s in snippets[:3] if s) or None
