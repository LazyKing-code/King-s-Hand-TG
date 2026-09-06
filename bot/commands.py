from __future__ import annotations

from telegram.ext import Application, CommandHandler, filters

from bot.config import COMMAND_PREFIX


def cmd_name(name: str) -> str:
    """Telegram command name without slash, e.g. kick."""
    return f"{COMMAND_PREFIX}{name.lower().lstrip('/')}"


def cmd(name: str) -> str:
    """User-facing command, e.g. /kick."""
    return f"/{cmd_name(name)}"


def add_cmd(
    app: Application,
    names: str | list[str],
    callback,
    *,
    private_slash: bool = False,
) -> None:
    """Register /name (and, if a prefix is set, also /name in private chat)."""
    if isinstance(names, str):
        names = [names]
    bare = [n.lower().lstrip("/") for n in names]
    registered = [cmd_name(n) for n in bare]
    app.add_handler(CommandHandler(registered, callback))
    if private_slash and COMMAND_PREFIX:
        extra = [n for n in bare if n not in registered]
        if extra:
            app.add_handler(CommandHandler(extra, callback, filters=filters.ChatType.PRIVATE))
