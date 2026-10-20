from __future__ import annotations

import asyncio
import logging
import time

from bot import cricket, db, tambola
from bot.common import lock_for

log = logging.getLogger(__name__)

SWEEP_INTERVAL = 5


async def sweep_once(bot) -> None:
    for session in db.due_sessions(time.time()):
        async with lock_for(session["id"]):
            fresh = db.load_session(session["id"])
            if not fresh or fresh["status"] not in ("lobby", "live"):
                continue
            if fresh["deadline"] > time.time():
                continue
            try:
                if fresh["kind"] == tambola.KIND:
                    await tambola.on_due(bot, fresh)
                elif fresh["kind"] == cricket.KIND:
                    await cricket.on_due(bot, fresh)
            except Exception:
                log.exception("sweep failed for %s", fresh["id"])


async def sweep_loop(app) -> None:
    await asyncio.sleep(2)
    while True:
        try:
            await sweep_once(app.bot)
        except Exception:
            log.exception("sweep loop failed")
        await asyncio.sleep(SWEEP_INTERVAL)
