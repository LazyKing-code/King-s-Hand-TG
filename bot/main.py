from __future__ import annotations

import logging
import sys

from telegram import (
    BotCommand,
    BotCommandScopeAllChatAdministrators,
    BotCommandScopeAllGroupChats,
    BotCommandScopeDefault,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from bot import db
from bot.config import BOT_TOKEN
from bot.flood import cmd_flood, cmd_floodmute, on_flood
from bot.handlers import (
    cmd_addowner,
    cmd_allowpack,
    cmd_allowsticker,
    cmd_approve,
    cmd_blockpack,
    cmd_clearkickmsg,
    cmd_clearplaceholder,
    cmd_dropadmin,
    cmd_dropadmins,
    cmd_forgive,
    cmd_kick,
    cmd_kickmsg,
    cmd_makeadmin,
    cmd_owners,
    cmd_removeowner,
    cmd_report,
    cmd_setkickmsg,
    cmd_setlog,
    cmd_setplaceholder,
    cmd_strikes,
    cmd_trust,
    cmd_trusted,
    cmd_unapprove,
    cmd_unsetlog,
    cmd_untrust,
    on_sticker,
    start,
)
from bot.calc import on_calc
from bot.fun import cmd_scold
from bot.help import cmd_help, on_help_callback
from bot.invite import on_chat_shared
from bot.lock import cmd_lock, cmd_unlock
from bot.raid import cmd_joins, cmd_purgejoins, cmd_raidmode, on_chat_member, on_new_members
from bot.tag import cmd_tag
from bot.tagall import cmd_notag, cmd_tagall, cmd_tagme, on_seen, on_tagall_callback
from bot.whisper import cmd_whisper, on_whisper_callback
from bot.who import cmd_stats

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger(__name__)


_MEMBER_COMMANDS = [
    BotCommand("help", "What you can use"),
    BotCommand("whisper", "Private note in the group"),
    BotCommand("scold", "Playful scolding"),
    BotCommand("stats", "Your status in this group"),
    BotCommand("notag", "Skip group pings"),
    BotCommand("tagme", "Include me in pings"),
]
_STAFF_COMMANDS = _MEMBER_COMMANDS + [
    BotCommand("lock", "Lock the chat"),
    BotCommand("unlock", "Open the chat"),
    BotCommand("flood", "Anti-flood settings"),
    BotCommand("report", "Ban a sticker pack"),
    BotCommand("kick", "Remove someone"),
    BotCommand("strikes", "Show warnings"),
    BotCommand("tag", "Set a member tag"),
    BotCommand("joins", "Recent joins"),
    BotCommand("tagall", "Ping everyone"),
    BotCommand("setlog", "Link a log chat"),
    BotCommand("makeadmin", "Grant admin through me"),
]


async def on_startup(app: Application) -> None:
    await app.bot.set_my_commands(_MEMBER_COMMANDS, scope=BotCommandScopeDefault())
    await app.bot.set_my_commands(_MEMBER_COMMANDS, scope=BotCommandScopeAllGroupChats())
    await app.bot.set_my_commands(_STAFF_COMMANDS, scope=BotCommandScopeAllChatAdministrators())


def main() -> None:
    if not BOT_TOKEN or BOT_TOKEN.startswith("123456"):
        sys.exit("Set BOT_TOKEN in .env (see .env.example)")

    db.init()
    app = Application.builder().token(BOT_TOKEN).post_init(on_startup).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CallbackQueryHandler(on_help_callback, pattern=r"^help:"))
    app.add_handler(CallbackQueryHandler(on_whisper_callback, pattern=r"^w:"))
    app.add_handler(CallbackQueryHandler(on_tagall_callback, pattern=r"^ta:"))
    app.add_handler(CommandHandler("whisper", cmd_whisper))
    app.add_handler(CommandHandler("lock", cmd_lock))
    app.add_handler(CommandHandler("unlock", cmd_unlock))
    app.add_handler(CommandHandler("flood", cmd_flood))
    app.add_handler(CommandHandler("floodmute", cmd_floodmute))
    app.add_handler(CommandHandler("scold", cmd_scold))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("who", cmd_stats))
    app.add_handler(CommandHandler("user", cmd_stats))
    app.add_handler(CommandHandler("tag", cmd_tag))
    app.add_handler(CommandHandler("nick", cmd_tag))
    app.add_handler(CommandHandler("tagall", cmd_tagall))
    app.add_handler(CommandHandler("notag", cmd_notag))
    app.add_handler(CommandHandler("tagme", cmd_tagme))
    app.add_handler(CommandHandler("joins", cmd_joins))
    app.add_handler(CommandHandler("purgejoins", cmd_purgejoins))
    app.add_handler(CommandHandler("raidmode", cmd_raidmode))
    app.add_handler(CommandHandler("approve", cmd_approve))
    app.add_handler(CommandHandler("unapprove", cmd_unapprove))
    app.add_handler(CommandHandler("trust", cmd_trust))
    app.add_handler(CommandHandler("untrust", cmd_untrust))
    app.add_handler(CommandHandler("trusted", cmd_trusted))
    app.add_handler(CommandHandler("addowner", cmd_addowner))
    app.add_handler(CommandHandler("removeowner", cmd_removeowner))
    app.add_handler(CommandHandler("owners", cmd_owners))
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(CommandHandler("blockpack", cmd_blockpack))
    app.add_handler(CommandHandler("allowpack", cmd_allowpack))
    app.add_handler(CommandHandler("allowsticker", cmd_allowsticker))
    app.add_handler(CommandHandler("setplaceholder", cmd_setplaceholder))
    app.add_handler(CommandHandler("clearplaceholder", cmd_clearplaceholder))
    app.add_handler(CommandHandler("setlog", cmd_setlog))
    app.add_handler(CommandHandler("unsetlog", cmd_unsetlog))
    app.add_handler(CommandHandler("setkickmsg", cmd_setkickmsg))
    app.add_handler(CommandHandler("kickmsg", cmd_kickmsg))
    app.add_handler(CommandHandler("clearkickmsg", cmd_clearkickmsg))
    app.add_handler(CommandHandler("forgive", cmd_forgive))
    app.add_handler(CommandHandler("strikes", cmd_strikes))
    app.add_handler(CommandHandler("makeadmin", cmd_makeadmin))
    app.add_handler(CommandHandler("dropadmin", cmd_dropadmin))
    app.add_handler(CommandHandler("dropadmins", cmd_dropadmins))
    app.add_handler(CommandHandler("kick", cmd_kick))
    app.add_handler(ChatMemberHandler(on_chat_member, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, on_new_members))
    app.add_handler(MessageHandler(filters.StatusUpdate.CHAT_SHARED, on_chat_shared))
    app.add_handler(MessageHandler(filters.ALL & ~filters.StatusUpdate.ALL, on_seen), group=-1)
    app.add_handler(MessageHandler(filters.Sticker.ALL, on_sticker))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, on_calc),
        group=1,
    )
    app.add_handler(
        MessageHandler(filters.ALL & ~filters.StatusUpdate.ALL, on_flood),
        group=2,
    )
    log.info("Bot starting")
    app.run_polling(allowed_updates=["message", "chat_member", "callback_query"])


if __name__ == "__main__":
    main()
