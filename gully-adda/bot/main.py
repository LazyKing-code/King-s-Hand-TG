from __future__ import annotations

import asyncio
import logging
import sys

from telegram import BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeDefault, Update
from telegram.error import Conflict
from telegram.ext import Application, CallbackQueryHandler, CommandHandler

from bot import db
from bot.board import cmd_board
from bot.config import BOT_TOKEN, DATA_DIR, DB_PATH
from bot.cricket import cmd_cricket, on_cricket_callback
from bot.help import cmd_help, cmd_start
from bot.sweep import sweep_loop
from bot.tambola import cmd_tambola, on_tambola_callback

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger(__name__)

_COMMANDS = [
    BotCommand("start", "Start the bot"),
    BotCommand("help", "How Tambola and cricket work"),
    BotCommand("tambola", "Start Tambola for the group"),
    BotCommand("cricket", "Start gully hand cricket"),
    BotCommand("board", "Wins in this group"),
]


async def on_startup(app: Application) -> None:
    await app.bot.delete_webhook(drop_pending_updates=True)
    await app.bot.set_my_commands(
        [BotCommand("start", "Start the bot"), BotCommand("help", "How the games work")],
        scope=BotCommandScopeDefault(),
    )
    await app.bot.set_my_commands(_COMMANDS, scope=BotCommandScopeAllGroupChats())
    log.info("SQLite path=%s (DATA_DIR=%s)", DB_PATH, DATA_DIR)
    app.bot_data["sweep_task"] = asyncio.create_task(sweep_loop(app))


async def on_shutdown(app: Application) -> None:
    task = app.bot_data.get("sweep_task")
    if task:
        task.cancel()


async def on_error(update: object, context: object) -> None:
    err = getattr(context, "error", None)
    if isinstance(err, Conflict):
        log.warning(
            "Telegram rejected this poller — another copy is using the same token. "
            "Stop the extra copy. Games will not run until only one is polling."
        )
        return
    log.exception("Unhandled error", exc_info=err)


def main() -> None:
    if not BOT_TOKEN or BOT_TOKEN.startswith("123456"):
        sys.exit("Set BOT_TOKEN in .env (see .env.example)")

    db.init()
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(on_startup)
        .post_shutdown(on_shutdown)
        .build()
    )
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("tambola", cmd_tambola))
    app.add_handler(CommandHandler("cricket", cmd_cricket))
    app.add_handler(CommandHandler("board", cmd_board))
    app.add_handler(CallbackQueryHandler(on_tambola_callback, pattern=r"^tb:"))
    app.add_handler(CallbackQueryHandler(on_cricket_callback, pattern=r"^ck:"))
    app.add_error_handler(on_error)
    log.info("Gully Adda polling")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
