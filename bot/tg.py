"""Central Telegram API calls: pacing + RetryAfter retries.

All hot paths should go through `call` / helpers here so one flood from
Telegram slows the bot gracefully instead of failing silently or spamming.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

from telegram.error import NetworkError, RetryAfter, TimedOut

log = logging.getLogger(__name__)

T = TypeVar("T")

# Stay under typical Bot API budgets. Per-chat gap matters more than global.
_GLOBAL_GAP = 0.04  # ~25 calls/sec peak
_CHAT_GAP = 0.05  # 20 calls/sec per chat
_MAX_ATTEMPTS = 5

_global_lock = asyncio.Lock()
_chat_locks: dict[int, asyncio.Lock] = {}
_last_global = 0.0
_last_chat: dict[int, float] = {}


def _chat_lock(chat_id: int) -> asyncio.Lock:
    lock = _chat_locks.get(chat_id)
    if lock is None:
        lock = asyncio.Lock()
        _chat_locks[chat_id] = lock
    return lock


async def _pace(chat_id: int | None) -> None:
    global _last_global
    async with _global_lock:
        now = time.monotonic()
        wait = _GLOBAL_GAP - (now - _last_global)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_global = time.monotonic()

    if chat_id is None:
        return
    async with _chat_lock(chat_id):
        now = time.monotonic()
        last = _last_chat.get(chat_id, 0.0)
        wait = _CHAT_GAP - (now - last)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_chat[chat_id] = time.monotonic()


async def call(
    factory: Callable[[], Awaitable[T]],
    *,
    chat_id: int | None = None,
    attempts: int = _MAX_ATTEMPTS,
) -> T:
    """Run an API coroutine factory with pacing and RetryAfter / timeout retries.

    `factory` must build a *fresh* awaitable each call (e.g. lambda: bot.send_message(...)).
    """
    last_exc: BaseException | None = None
    for attempt in range(max(1, attempts)):
        await _pace(chat_id)
        try:
            return await factory()
        except RetryAfter as exc:
            delay = float(exc.retry_after) + 0.05
            log.warning(
                "Telegram RetryAfter %.1fs (chat=%s attempt=%s)",
                delay,
                chat_id,
                attempt + 1,
            )
            await asyncio.sleep(delay)
            last_exc = exc
        except (TimedOut, NetworkError) as exc:
            delay = min(2.0 * (attempt + 1), 8.0)
            log.warning(
                "Telegram network issue (%s); retry in %.1fs (chat=%s)",
                type(exc).__name__,
                delay,
                chat_id,
            )
            await asyncio.sleep(delay)
            last_exc = exc
    assert last_exc is not None
    raise last_exc


async def send_message(bot, chat_id: int, *args, **kwargs):
    return await call(
        lambda: bot.send_message(chat_id, *args, **kwargs),
        chat_id=chat_id,
    )


async def delete_message(bot, chat_id: int, message_id: int, **kwargs):
    return await call(
        lambda: bot.delete_message(chat_id, message_id, **kwargs),
        chat_id=chat_id,
    )


async def edit_message_text(bot, *args, chat_id: int | None = None, **kwargs):
    cid = chat_id if chat_id is not None else kwargs.get("chat_id")
    return await call(
        lambda: bot.edit_message_text(*args, **kwargs),
        chat_id=int(cid) if cid is not None else None,
    )
