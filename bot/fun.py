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
    """Answer questions using Wikipedia / web search — return whatever we find."""
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
        answer = await _gather_answer(question)
        if answer:
            # Telegram message limit ~4096; keep answers readable
            if len(answer) > 3500:
                answer = answer[:3490].rstrip() + "…"
            await status_msg.edit_text(answer)
        else:
            await status_msg.edit_text(
                "Search came back empty for that. Try a shorter question, "
                "or add a year/name (e.g. iPhone 15 release date)."
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
    """Try several sources; return the first usable text, preferring fuller answers."""
    candidates: list[str] = []

    for getter in (
        _answer_from_wikipedia,
        _answer_from_ddg_instant,
        _answer_from_web,
        _answer_from_wiki_api,
    ):
        try:
            hit = await getter(question)
        except Exception as exc:
            log.warning("ask source %s failed: %s", getter.__name__, exc)
            hit = None
        if hit:
            candidates.append(hit)
            # Prefer a solid paragraph; otherwise keep collecting
            if len(hit) >= 80:
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


async def _answer_from_wikipedia(question: str) -> str | None:
    try:
        import wikipedia
        from wikipedia.exceptions import DisambiguationError, PageError
    except ImportError:
        log.warning("wikipedia package not installed")
        return None

    try:
        wikipedia.set_lang("en")
        try:
            wikipedia.set_user_agent("KingsHandBot/1.0 (Telegram /ask; contact via bot)")
        except Exception:
            pass

        results = wikipedia.search(question, results=5)
        if not results:
            return None

        last_err: Exception | None = None
        for title in results:
            try:
                summary = wikipedia.summary(title, sentences=3, auto_suggest=False)
            except DisambiguationError as exc:
                options = [o for o in (exc.options or []) if o][:3]
                for opt in options:
                    try:
                        summary = wikipedia.summary(opt, sentences=3, auto_suggest=False)
                        break
                    except Exception as inner:
                        last_err = inner
                        summary = None
                else:
                    continue
            except PageError as exc:
                last_err = exc
                continue
            except Exception as exc:
                last_err = exc
                continue

            summary = _shorten(summary or "", 3)
            if summary:
                # Include title when it helps (e.g. iPhone vs iPhone 15)
                if title.lower() not in summary.lower()[:80]:
                    return f"{title}: {summary}"
                return summary

        if last_err:
            log.warning("Wikipedia search failed: %s", last_err)
        return None
    except Exception as exc:
        log.warning("Wikipedia search failed: %s", exc)
        return None


async def _answer_from_wiki_api(question: str) -> str | None:
    """MediaWiki opensearch + extract — works even when the wikipedia package is flaky."""
    try:
        import json
        import urllib.parse
        import urllib.request

        headers = {"User-Agent": "KingsHandBot/1.0 (Telegram /ask)"}

        def _get(url: str) -> dict | list:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=12) as resp:
                return json.loads(resp.read().decode("utf-8", errors="replace"))

        search_url = (
            "https://en.wikipedia.org/w/api.php?"
            + urllib.parse.urlencode(
                {
                    "action": "opensearch",
                    "search": question,
                    "limit": 3,
                    "namespace": 0,
                    "format": "json",
                }
            )
        )
        data = _get(search_url)
        titles = data[1] if isinstance(data, list) and len(data) > 1 else []
        if not titles:
            return None

        title = titles[0]
        extract_url = (
            "https://en.wikipedia.org/w/api.php?"
            + urllib.parse.urlencode(
                {
                    "action": "query",
                    "prop": "extracts",
                    "exintro": 1,
                    "explaintext": 1,
                    "titles": title,
                    "format": "json",
                }
            )
        )
        payload = _get(extract_url)
        pages = (payload.get("query") or {}).get("pages") or {}
        for page in pages.values():
            extract = (page.get("extract") or "").strip()
            if extract:
                short = _shorten(extract, 3)
                if short:
                    return f"{title}: {short}" if title.lower() not in short.lower()[:80] else short
        return None
    except Exception as exc:
        log.warning("Wikipedia API failed: %s", exc)
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

        bits: list[str] = []
        heading = (data.get("Heading") or "").strip()
        for key in ("Answer", "AbstractText", "Definition"):
            text = (data.get(key) or "").strip()
            if text:
                bits.append(text)
        if not bits:
            related = data.get("RelatedTopics") or []
            for item in related:
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
    """DuckDuckGo text/news search — return whatever snippets we get."""
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        log.warning("duckduckgo-search package not installed")
        return None

    snippets: list[str] = []
    try:
        with DDGS() as ddgs:
            # Try a couple of backends; rate-limits are common on hosts.
            for backend in (None, "lite", "html"):
                try:
                    kwargs = {"max_results": 5}
                    if backend:
                        kwargs["backend"] = backend
                    results = list(ddgs.text(question, **kwargs))
                except TypeError:
                    # Older package without backend= kwarg
                    results = list(ddgs.text(question, max_results=5))
                except Exception as exc:
                    log.warning("DDG text backend %s failed: %s", backend, exc)
                    continue
                for item in results or []:
                    title = (item or {}).get("title") or ""
                    body = (item or {}).get("body") or ""
                    chunk = _clean_text(f"{title}. {body}" if title and body else (body or title))
                    if chunk and chunk not in snippets:
                        snippets.append(chunk)
                if snippets:
                    break

            if not snippets:
                try:
                    news = list(ddgs.news(question, max_results=3))
                except Exception as exc:
                    log.warning("DDG news failed: %s", exc)
                    news = []
                for item in news or []:
                    title = (item or {}).get("title") or ""
                    body = (item or {}).get("body") or ""
                    chunk = _clean_text(f"{title}. {body}" if title and body else (body or title))
                    if chunk:
                        snippets.append(chunk)
    except Exception as exc:
        log.warning("Web search failed: %s", exc)

    if not snippets:
        return None
    # Return top snippets so the user still gets something useful
    joined = "\n\n".join(_shorten(s, 2) for s in snippets[:3] if s)
    return joined or None