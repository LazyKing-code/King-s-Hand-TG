from __future__ import annotations

from dataclasses import dataclass

from telegram import MessageEntity, Update
from telegram.ext import Application, BaseHandler, ContextTypes, filters

from bot.config import COMMAND_PREFIX


def cmd_name(name: str) -> str:
    """Telegram command name without slash, e.g. kick."""
    return f"{COMMAND_PREFIX}{name.lower().lstrip('/')}"


def cmd(name: str) -> str:
    """User-facing command, e.g. /kick."""
    return f"/{cmd_name(name)}"


@dataclass(frozen=True)
class ParsedCommand:
    command: str
    args: list[str]


def parse_slash_command(update: Update, bot_username: str | None) -> ParsedCommand | None:
    """Parse /help and /help@BotName. Ignore commands aimed at another bot."""
    msg = update.effective_message
    if not msg:
        return None
    text = (msg.text or msg.caption or "").strip()
    if not text.startswith("/"):
        return None

    entities = tuple(msg.entities or msg.caption_entities or ())
    token = text.split(maxsplit=1)[0]
    if (
        entities
        and entities[0].type == MessageEntity.BOT_COMMAND
        and entities[0].offset == 0
    ):
        token = text[entities[0].offset : entities[0].offset + entities[0].length]

    body = token[1:] if token.startswith("/") else token
    command, sep, mention = body.partition("@")
    command = command.lower().replace("-", "_")
    if not command:
        return None
    if mention:
        mine = (bot_username or "").lstrip("@").lower()
        if mine and mention.lower() != mine:
            return None

    rest = text[len(token) :].strip()
    args = rest.split() if rest else []
    if args and bot_username:
        first = args[0].lstrip("@").lower()
        if first == bot_username.lstrip("@").lower():
            args = args[1:]
    return ParsedCommand(command=command, args=args)


class SlashCommandHandler(BaseHandler[Update, ContextTypes.DEFAULT_TYPE]):
    """Like CommandHandler, but /help@BotName always reaches this bot."""

    def __init__(self, commands: list[str], callback, *, extra_filter=None):
        super().__init__(callback, block=True)
        self.commands = frozenset(c.lower() for c in commands)
        self._extra = extra_filter

    def check_update(self, update: object) -> ParsedCommand | None:
        if not isinstance(update, Update) or not update.effective_message:
            return None
        if self._extra is not None and not self._extra.check_update(update):
            return None
        username = None
        try:
            username = update.get_bot().username
        except Exception:
            try:
                username = update.effective_message.get_bot().username
            except Exception:
                username = None
        parsed = parse_slash_command(update, username)
        if parsed is None or parsed.command not in self.commands:
            return None
        return parsed

    async def handle_update(self, update, application, check_result, context):
        if isinstance(check_result, ParsedCommand):
            context.args = check_result.args
        return await super().handle_update(update, application, check_result, context)


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
    app.add_handler(SlashCommandHandler(registered, callback))
    if private_slash and COMMAND_PREFIX:
        extra = [n for n in bare if n not in registered]
        if extra:
            app.add_handler(
                SlashCommandHandler(
                    extra,
                    callback,
                    extra_filter=filters.ChatType.PRIVATE,
                )
            )
