from __future__ import annotations

from telegram.ext import Application, CommandHandler, filters

from bot.config import COMMAND_PREFIX


def cmd_name(name: str) -> str:
    """Telegram command name without slash, e.g. kh_kick."""
    return f"{COMMAND_PREFIX}{name.lower().lstrip('/')}"


def cmd(name: str) -> str:
    """User-facing command, e.g. /kh_kick."""
    return f"/{cmd_name(name)}"


def add_cmd(
    app: Application,
    names: str | list[str],
    callback,
    *,
    private_slash: bool = False,
) -> None:
    """Register /kh_name (and optionally plain /name in private chat)."""
    if isinstance(names, str):
        names = [names]
    bare = [n.lower().lstrip("/") for n in names]
    app.add_handler(CommandHandler([cmd_name(n) for n in bare], callback))
    if private_slash:
        app.add_handler(CommandHandler(bare, callback, filters=filters.ChatType.PRIVATE))
