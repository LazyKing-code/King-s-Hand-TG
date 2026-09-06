from __future__ import annotations

import asyncio
import logging
import sys

from telegram import (
    BotCommand,
    BotCommandScopeAllChatAdministrators,
    BotCommandScopeAllGroupChats,
    BotCommandScopeDefault,
)
from telegram.error import Conflict
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from bot import db
from bot.calc import on_calc
from bot.commands import add_cmd, cmd, cmd_name
from bot.config import BOT_TOKEN
from bot.flood import cmd_flood, cmd_floodmute, on_flood
from bot.fun import cmd_scold
from bot.games import (
    cmd_balance,
    cmd_cricket,
    cmd_daily,
    cmd_dice,
    cmd_gamehelp,
    cmd_lucky7,
    cmd_rps,
    cmd_top,
    cmd_toss,
    on_game_callback,
)
from bot.handlers import (
    cmd_addowner,
    cmd_allowpack,
    cmd_allowsticker,
    cmd_approve,
    cmd_clearkickmsg,
    cmd_clearplaceholder,
    cmd_dropadmin,
    cmd_dropadmins,
    cmd_forgive,
    cmd_kick,
    cmd_kickmsg,
    cmd_makeadmin,
    cmd_owners,
    cmd_packs,
    on_packs_callback,
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
    cmd_unreport,
    cmd_unreportall,
    on_sticker,
    start,
)
from bot.groupmod import (
    cmd_admins,
    cmd_ban,
    cmd_del,
    cmd_id,
    cmd_mute,
    cmd_pin,
    cmd_purge,
    cmd_resetwarns,
    cmd_staff,
    cmd_unban,
    cmd_unmute,
    cmd_unpin,
    cmd_unpinall,
    cmd_warn,
    cmd_warnlimit,
    cmd_warns,
    cmd_zombies,
    daily_zombies_loop,
)
from bot.help import cmd_help, on_help_callback
from bot.invite import on_chat_shared
from bot.lock import cmd_lock, cmd_unlock
from bot.notes import (
    cmd_blacklist,
    cmd_clear,
    cmd_filter,
    cmd_filters,
    cmd_get,
    cmd_notes,
    cmd_save,
    cmd_stop,
    cmd_unblacklist,
    on_triggers,
)
from bot.raid import cmd_joins, cmd_purgejoins, cmd_raidmode, on_chat_member, on_new_members
from bot.release import cmd_release, cmd_whatsnew, maybe_broadcast_on_startup
from bot.tag import cmd_tag
from bot.tagall import cmd_notag, cmd_tagall, cmd_tagme, on_seen, on_tagall_callback
from bot.welcome import (
    cmd_clearrules,
    cmd_cleanservice,
    cmd_goodbye,
    cmd_rules,
    cmd_setrules,
    cmd_welcome,
    on_left_service,
)
from bot.verify import (
    cmd_unverified,
    cmd_verify,
    on_verify_callback,
    schedule_pending_jobs,
)
from bot.whisper import cmd_whisper, on_whisper_callback
from bot.who import cmd_stats

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger(__name__)


_MEMBER_COMMANDS = [
    ("help", "What you can use"),
    ("rules", "Group rules"),
    ("id", "Chat and user ids"),
    ("notes", "Saved notes"),
    ("whisper", "Private note in the group"),
    ("scold", "Playful scolding"),
    ("gamehelp", "How to play games"),
    ("daily", "Daily game bonus"),
    ("top", "Game leaderboard"),
    ("whatsnew", "Latest official update"),
    ("stats", "Your status in this group"),
    ("notag", "Skip group pings"),
    ("tagme", "Include me in pings"),
]
_STAFF_COMMANDS = _MEMBER_COMMANDS + [
    ("welcome", "Set the join message"),
    ("unverified", "Joins waiting to verify"),
    ("ban", "Ban someone"),
    ("mute", "Mute someone"),
    ("kick", "Remove someone"),
    ("warn", "Warn someone"),
    ("lock", "Lock the chat"),
    ("unlock", "Open the chat"),
    ("flood", "Anti-flood settings"),
    ("pin", "Pin a message"),
    ("report", "Ban a sticker pack"),
    ("unreport", "Unreport a pack (owner)"),
    ("unreportall", "Clear all reports (owner)"),
    ("packs", "Banned sticker packs"),
    ("strikes", "Sticker warnings"),
    ("tag", "Set a member tag"),
    ("joins", "Recent joins"),
    ("tagall", "Ping everyone"),
    ("setlog", "Link a log chat"),
    ("makeadmin", "Grant admin through me"),
]


def _bot_commands(items: list[tuple[str, str]]) -> list[BotCommand]:
    return [BotCommand(cmd_name(name), desc) for name, desc in items]


async def on_startup(app: Application) -> None:
    await app.bot.delete_webhook(drop_pending_updates=True)
    await app.bot.set_my_commands(
        [BotCommand("start", "Start / add to a group"), BotCommand("help", "Help in private chat")],
        scope=BotCommandScopeDefault(),
    )
    await app.bot.set_my_commands(_bot_commands(_MEMBER_COMMANDS), scope=BotCommandScopeAllGroupChats())
    await app.bot.set_my_commands(
        _bot_commands(_STAFF_COMMANDS),
        scope=BotCommandScopeAllChatAdministrators(),
    )
    log.info("Commands ready (e.g. %s)", cmd("help"))
    await schedule_pending_jobs(app)
    app.bot_data["zombies_task"] = asyncio.create_task(daily_zombies_loop(app))
    app.bot_data["release_task"] = asyncio.create_task(maybe_broadcast_on_startup(app))


async def on_shutdown(app: Application) -> None:
    for key in ("zombies_task", "release_task"):
        task = app.bot_data.get(key)
        if task:
            task.cancel()


async def on_error(update: object, context: object) -> None:
    err = getattr(context, "error", None)
    if isinstance(err, Conflict):
        log.warning(
            "Telegram rejected this poller — another copy is using the same token. "
            "Stop extra Railway services and any local python -m bot.main. "
            "Group commands will not work until only one copy is running."
        )
        return
    log.exception("Unhandled error", exc_info=err)


def main() -> None:
    if not BOT_TOKEN or BOT_TOKEN.startswith("123456"):
        sys.exit("Set BOT_TOKEN in .env (see .env.example)")

    db.init()
    app = Application.builder().token(BOT_TOKEN).post_init(on_startup).post_shutdown(on_shutdown).build()
    app.add_handler(CommandHandler("start", start))
    add_cmd(app, "help", cmd_help, private_slash=True)
    add_cmd(app, ["whatsnew", "changelog"], cmd_whatsnew, private_slash=True)
    add_cmd(app, "release", cmd_release)
    app.add_handler(CallbackQueryHandler(on_help_callback, pattern=r"^help:"))
    app.add_handler(CallbackQueryHandler(on_whisper_callback, pattern=r"^w:"))
    app.add_handler(CallbackQueryHandler(on_tagall_callback, pattern=r"^ta:"))
    app.add_handler(CallbackQueryHandler(on_packs_callback, pattern=r"^pk:"))
    app.add_handler(CallbackQueryHandler(on_verify_callback, pattern=r"^vf:"))
    app.add_handler(CallbackQueryHandler(on_game_callback, pattern=r"^gm:"))
    add_cmd(app, "whisper", cmd_whisper)
    add_cmd(app, "lock", cmd_lock)
    add_cmd(app, "unlock", cmd_unlock)
    add_cmd(app, "flood", cmd_flood)
    add_cmd(app, "floodmute", cmd_floodmute)
    add_cmd(app, "scold", cmd_scold)
    add_cmd(app, ["gamehelp", "games", "game_help"], cmd_gamehelp)
    add_cmd(app, "daily", cmd_daily)
    add_cmd(app, ["top", "leaderboard"], cmd_top)
    add_cmd(app, ["balance", "coins"], cmd_balance)
    add_cmd(app, "toss", cmd_toss)
    add_cmd(app, "dice", cmd_dice)
    add_cmd(app, ["lucky7", "7up"], cmd_lucky7)
    add_cmd(app, ["rps", "sps"], cmd_rps)
    add_cmd(app, "cricket", cmd_cricket)
    add_cmd(app, ["stats", "who", "user"], cmd_stats)
    add_cmd(app, ["tag", "nick"], cmd_tag)
    add_cmd(app, "tagall", cmd_tagall)
    add_cmd(app, "notag", cmd_notag)
    add_cmd(app, "tagme", cmd_tagme)
    add_cmd(app, "joins", cmd_joins)
    add_cmd(app, "purgejoins", cmd_purgejoins)
    add_cmd(app, "raidmode", cmd_raidmode)
    add_cmd(app, "approve", cmd_approve)
    add_cmd(app, "unapprove", cmd_unapprove)
    add_cmd(app, "trust", cmd_trust)
    add_cmd(app, "untrust", cmd_untrust)
    add_cmd(app, "trusted", cmd_trusted)
    add_cmd(app, "addowner", cmd_addowner)
    add_cmd(app, "removeowner", cmd_removeowner)
    add_cmd(app, "owners", cmd_owners)
    add_cmd(app, ["report", "blockpack"], cmd_report)
    add_cmd(app, "unreport", cmd_unreport)
    add_cmd(app, ["unreportall", "clearreports"], cmd_unreportall)
    add_cmd(app, ["packs", "bannedpacks", "unbanpack"], cmd_packs)
    add_cmd(app, "allowpack", cmd_allowpack)
    add_cmd(app, "allowsticker", cmd_allowsticker)
    add_cmd(app, "setplaceholder", cmd_setplaceholder)
    add_cmd(app, "clearplaceholder", cmd_clearplaceholder)
    add_cmd(app, "setlog", cmd_setlog)
    add_cmd(app, "unsetlog", cmd_unsetlog)
    add_cmd(app, "setkickmsg", cmd_setkickmsg)
    add_cmd(app, "kickmsg", cmd_kickmsg)
    add_cmd(app, "clearkickmsg", cmd_clearkickmsg)
    add_cmd(app, "forgive", cmd_forgive)
    add_cmd(app, "strikes", cmd_strikes)
    add_cmd(app, "makeadmin", cmd_makeadmin)
    add_cmd(app, "dropadmin", cmd_dropadmin)
    add_cmd(app, "dropadmins", cmd_dropadmins)
    add_cmd(app, "kick", cmd_kick)
    add_cmd(app, ["welcome", "setwelcome"], cmd_welcome)
    add_cmd(app, ["goodbye", "setgoodbye"], cmd_goodbye)
    add_cmd(app, ["verify", "captcha"], cmd_verify)
    add_cmd(app, ["unverified", "verifyqueue"], cmd_unverified)
    add_cmd(app, "cleanservice", cmd_cleanservice)
    add_cmd(app, "rules", cmd_rules)
    add_cmd(app, "setrules", cmd_setrules)
    add_cmd(app, "clearrules", cmd_clearrules)
    add_cmd(app, "id", cmd_id)
    add_cmd(app, "admins", cmd_admins)
    add_cmd(app, ["staff", "reportuser"], cmd_staff)
    add_cmd(app, "ban", cmd_ban)
    add_cmd(app, "unban", cmd_unban)
    add_cmd(app, ["mute", "tmute"], cmd_mute)
    add_cmd(app, "unmute", cmd_unmute)
    add_cmd(app, "pin", cmd_pin)
    add_cmd(app, "unpin", cmd_unpin)
    add_cmd(app, "unpinall", cmd_unpinall)
    add_cmd(app, ["del", "delete"], cmd_del)
    add_cmd(app, "purge", cmd_purge)
    add_cmd(app, "warn", cmd_warn)
    add_cmd(app, "warns", cmd_warns)
    add_cmd(app, ["resetwarns", "rmwarns"], cmd_resetwarns)
    add_cmd(app, "warnlimit", cmd_warnlimit)
    add_cmd(app, "zombies", cmd_zombies)
    add_cmd(app, ["save", "savenote"], cmd_save)
    add_cmd(app, "get", cmd_get)
    add_cmd(app, ["clear", "clearnote"], cmd_clear)
    add_cmd(app, "notes", cmd_notes)
    add_cmd(app, "filter", cmd_filter)
    add_cmd(app, "stop", cmd_stop)
    add_cmd(app, "filters", cmd_filters)
    add_cmd(app, ["blacklist", "bl"], cmd_blacklist)
    add_cmd(app, ["unblacklist", "unbl"], cmd_unblacklist)
    app.add_handler(ChatMemberHandler(on_chat_member, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, on_new_members))
    app.add_handler(MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER, on_left_service))
    app.add_handler(MessageHandler(filters.StatusUpdate.CHAT_SHARED, on_chat_shared))
    app.add_handler(MessageHandler(filters.ALL & ~filters.StatusUpdate.ALL, on_seen), group=-1)
    app.add_handler(MessageHandler(filters.Sticker.ALL, on_sticker))
    app.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & (filters.TEXT | filters.CAPTION) & ~filters.COMMAND,
            on_triggers,
        ),
        group=0,
    )
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, on_calc),
        group=1,
    )
    app.add_handler(
        MessageHandler(filters.ALL & ~filters.StatusUpdate.ALL, on_flood),
        group=2,
    )
    app.add_error_handler(on_error)
    log.info("Bot starting")
    app.run_polling(
        allowed_updates=["message", "chat_member", "callback_query"],
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
