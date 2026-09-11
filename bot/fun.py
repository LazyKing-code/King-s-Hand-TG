from __future__ import annotations

import logging
import random
import time

from telegram import Update, User
from telegram.ext import ContextTypes

from bot.commands import cmd
from bot.config import BRAVE_API_KEY
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
# Skip DuckDuckGo for a while after a rate-limit hit (cloud hosts get blocked often).
_ddg_cooldown_until = 0.0


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
    """Answer via India web search. Not limited by Telegram — search engines rate-limit cloud IPs."""
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
        answer, status = await _gather_answer(question)
        if answer:
            if len(answer) > 3500:
                answer = answer[:3490].rstrip() + "…"
            await status_msg.edit_text(answer)
            return
        if status == "ratelimited":
            await status_msg.edit_text(
                "Search is temporarily rate-limited (the web provider blocks the "
                "server IP for a bit — this is not a Telegram rule).\n"
                "Try again in 1–2 minutes."
            )
            return
        await status_msg.edit_text(
            "Couldn't find anything useful for that. Try a clearer question."
        )
    except Exception:
        log.exception("ask failed")
        try:
            await status_msg.edit_text(
                "Something went wrong while searching. Try again?"
            )
        except Exception:
            pass


async def _gather_answer(question: str) -> tuple[str | None, str]:
    """Returns (answer, status) where status is ok|empty|ratelimited.

    No API key required. Order favors free sources that work from cloud hosts
    (DuckDuckGo HTML scrape is often rate-limited on Railway).
    """
    rate_limited = False

    # Optional paid/free-tier key — only if the owner set one.
    if BRAVE_API_KEY:
        hit = await _answer_from_brave(question)
        if hit:
            return hit, "ok"

    # Free + reliable on most hosts (including Railway).
    hit = await _answer_from_wikipedia(question)
    if hit:
        return hit, "ok"

    # Free Instant Answer JSON (often still works when DDG HTML is blocked).
    hit = await _answer_from_ddg_instant(question)
    if hit:
        return hit, "ok"

    # Full web scrape — best when it works, often rate-limited on cloud IPs.
    hit, ddg_limited = await _answer_from_web(question)
    if hit:
        return hit, "ok"
    rate_limited = rate_limited or ddg_limited

    return None, ("ratelimited" if rate_limited else "empty")


def _query_variants(question: str) -> list[str]:
    """Build a few search phrasings so free encyclopedias hit better titles."""
    raw = " ".join(question.strip().split())
    variants = [raw]
    cleaned = raw
    for ch in "?!.,;:\"'":
        cleaned = cleaned.replace(ch, " ")
    cleaned = " ".join(cleaned.split())
    if cleaned and cleaned.lower() != raw.lower():
        variants.append(cleaned)

    stop = {
        "a", "an", "the", "is", "are", "was", "were", "do", "does", "did",
        "who", "what", "when", "where", "why", "how", "which", "whom",
        "me", "my", "i", "we", "you", "please", "tell", "about",
        "kya", "hai", "hain", "ho", "hu", "hun", "mai", "main", "kaun", "kon",
    }
    tokens = [t for t in cleaned.split() if t.lower() not in stop]
    if tokens:
        short = " ".join(tokens)
        if short.lower() not in {v.lower() for v in variants}:
            variants.append(short)
    # Dedupe preserving order
    out: list[str] = []
    seen: set[str] = set()
    for v in variants:
        key = v.lower()
        if key and key not in seen:
            seen.add(key)
            out.append(v)
    return out[:4]


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


def _mark_ddg_limited() -> None:
    global _ddg_cooldown_until
    _ddg_cooldown_until = time.time() + 90


def _ddg_is_cooling() -> bool:
    return time.time() < _ddg_cooldown_until


async def _answer_from_brave(question: str) -> str | None:
    """Brave Search API with India country bias. Needs BRAVE_API_KEY."""
    try:
        import requests
    except ImportError:
        return None
    try:
        resp = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={
                "q": question,
                "country": "IN",
                "search_lang": "en",
                "count": 5,
                "text_decorations": 0,
                "spellcheck": 1,
            },
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "X-Subscription-Token": BRAVE_API_KEY,
            },
            timeout=12,
        )
        if resp.status_code == 429:
            log.warning("Brave search rate-limited")
            return None
        if not resp.ok:
            log.warning("Brave search HTTP %s: %s", resp.status_code, resp.text[:200])
            return None
        data = resp.json()
        snippets: list[str] = []
        for item in (data.get("web") or {}).get("results") or []:
            title = (item.get("title") or "").strip()
            body = (item.get("description") or "").strip()
            chunk = _clean_text(f"{title}. {body}" if title and body else (body or title))
            if chunk:
                snippets.append(chunk)
        if not snippets:
            return None
        return "\n\n".join(_shorten(s, 2) for s in snippets[:3])
    except Exception as exc:
        log.warning("Brave search failed: %s", exc)
        return None


async def _answer_from_ddg_instant(question: str) -> str | None:
    """DuckDuckGo Instant Answer with India locale."""
    if _ddg_is_cooling():
        return None
    try:
        import requests

        resp = requests.get(
            "https://api.duckduckgo.com/",
            params={
                "q": question,
                "format": "json",
                "no_html": 1,
                "skip_disambig": 1,
                "kl": _ASK_REGION,
            },
            headers={"User-Agent": "KingsHandBot/1.0 (Telegram; /ask)"},
            timeout=10,
        )
        if resp.status_code == 429:
            _mark_ddg_limited()
            return None
        data = resp.json()
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


async def _answer_from_web(question: str) -> tuple[str | None, bool]:
    """India-region DuckDuckGo text/news. Returns (answer, was_rate_limited)."""
    if _ddg_is_cooling():
        return None, True
    try:
        from duckduckgo_search import DDGS
        from duckduckgo_search.exceptions import DuckDuckGoSearchException
    except ImportError:
        log.warning("duckduckgo-search package not installed")
        return None, False

    snippets: list[str] = []
    limited = False
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
                    try:
                        results = list(
                            ddgs.text(question, region=_ASK_REGION, max_results=5)
                        )
                    except Exception as exc:
                        if "ratelimit" in str(exc).lower() or "202" in str(exc):
                            limited = True
                            _mark_ddg_limited()
                        log.warning("DDG text failed: %s", exc)
                        continue
                except DuckDuckGoSearchException as exc:
                    if "ratelimit" in str(exc).lower() or "202" in str(exc):
                        limited = True
                        _mark_ddg_limited()
                    log.warning("DDG text backend %s failed: %s", backend, exc)
                    continue
                except Exception as exc:
                    if "ratelimit" in str(exc).lower() or "202" in str(exc):
                        limited = True
                        _mark_ddg_limited()
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

            if not snippets and not limited:
                try:
                    news = list(ddgs.news(question, region=_ASK_REGION, max_results=3))
                except TypeError:
                    try:
                        news = list(ddgs.news(question, max_results=3))
                    except Exception as exc:
                        log.warning("DDG news failed: %s", exc)
                        news = []
                except Exception as exc:
                    if "ratelimit" in str(exc).lower() or "202" in str(exc):
                        limited = True
                        _mark_ddg_limited()
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
        if "ratelimit" in str(exc).lower() or "202" in str(exc):
            limited = True
            _mark_ddg_limited()
        log.warning("Web search failed: %s", exc)

    if not snippets:
        return None, limited
    return "\n\n".join(_shorten(s, 2) for s in snippets[:3] if s) or None, limited


def _title_score(title: str, question: str) -> int:
    """Higher = better match between a result title and the user's question."""
    q_tokens = {t.lower() for t in _query_variants(question)[-1].split() if len(t) > 2}
    if not q_tokens:
        return 0
    t_tokens = {t.lower().strip("()[],") for t in title.split() if len(t) > 2}
    return len(q_tokens & t_tokens)


async def _answer_from_wikipedia(question: str) -> str | None:
    """Free encyclopedia lookup — works without any API key on cloud hosts."""
    hit = await _answer_from_wikipedia_package(question)
    if hit:
        return hit
    return await _answer_from_mediawiki(question)


async def _answer_from_wikipedia_package(question: str) -> str | None:
    try:
        import wikipedia
        from wikipedia.exceptions import DisambiguationError, PageError
    except ImportError:
        return None
    try:
        wikipedia.set_lang("en")
        try:
            wikipedia.set_user_agent("KingsHandBot/1.0 (Telegram /ask)")
        except Exception:
            pass
        for q in _query_variants(question):
            results = wikipedia.search(q, results=8) or []
            ranked = sorted(
                results, key=lambda title: _title_score(title, question), reverse=True
            )
            for title in ranked:
                if _title_score(title, question) == 0 and len(ranked) > 1:
                    continue
                try:
                    summary = wikipedia.summary(title, sentences=3, auto_suggest=False)
                except DisambiguationError as exc:
                    options = [o for o in (exc.options or []) if o][:3]
                    options = sorted(
                        options, key=lambda t: _title_score(t, question), reverse=True
                    )
                    summary = None
                    for opt in options:
                        try:
                            summary = wikipedia.summary(
                                opt, sentences=3, auto_suggest=False
                            )
                            title = opt
                            break
                        except Exception:
                            continue
                    if not summary:
                        continue
                except PageError:
                    continue
                except Exception:
                    continue
                summary = _shorten(summary or "", 3)
                if summary:
                    if title.lower() not in summary.lower()[:80]:
                        return f"{title}: {summary}"
                    return summary
        return None
    except Exception as exc:
        log.warning("Wikipedia package failed: %s", exc)
        return None


async def _answer_from_mediawiki(question: str) -> str | None:
    """Direct MediaWiki opensearch + extract — no API key."""
    try:
        import requests
    except ImportError:
        return None

    headers = {"User-Agent": "KingsHandBot/1.0 (Telegram /ask; free lookup)"}
    try:
        for q in _query_variants(question):
            search = requests.get(
                "https://en.wikipedia.org/w/api.php",
                params={
                    "action": "opensearch",
                    "search": q,
                    "limit": 8,
                    "namespace": 0,
                    "format": "json",
                },
                headers=headers,
                timeout=12,
            )
            if not search.ok:
                continue
            data = search.json()
            titles = data[1] if isinstance(data, list) and len(data) > 1 else []
            ranked = sorted(
                titles, key=lambda title: _title_score(title, question), reverse=True
            )
            for title in ranked:
                if _title_score(title, question) == 0 and len(ranked) > 1:
                    continue
                ext = requests.get(
                    "https://en.wikipedia.org/w/api.php",
                    params={
                        "action": "query",
                        "prop": "extracts",
                        "exintro": 1,
                        "explaintext": 1,
                        "titles": title,
                        "format": "json",
                    },
                    headers=headers,
                    timeout=12,
                )
                if not ext.ok:
                    continue
                pages = (ext.json().get("query") or {}).get("pages") or {}
                for page in pages.values():
                    extract = (page.get("extract") or "").strip()
                    if not extract:
                        continue
                    short = _shorten(extract, 3)
                    if short:
                        if title.lower() not in short.lower()[:80]:
                            return f"{title}: {short}"
                        return short
        return None
    except Exception as exc:
        log.warning("MediaWiki API failed: %s", exc)
        return None
