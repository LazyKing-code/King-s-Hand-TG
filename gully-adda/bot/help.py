from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from bot.common import require_group


HELP = """<b>Gully Adda</b>
Indian games for a group chat.

<b>/tambola</b>
Everyone takes a private ticket. The bot calls numbers from 1 to 90. First real line and first full house win. /tambola cancel stops it.

<b>/cricket</b>
Join Team A or Team B. The host picks 1–3 overs, then Begin. Each ball the batter and the bowler pick 1–6 in secret. The same number is out and the next batter comes in. /cricket cancel stops it.

<b>/board</b>
Wins in this group.

Open a private chat and tap Start before taking a Tambola ticket, or the card cannot be delivered.
"""


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return
    if require_group(update):
        await msg.reply_html(
            "Gully Adda is in this group. Send /help for Tambola and cricket.",
            disable_web_page_preview=True,
        )
        return
    username = context.bot.username
    extra = ""
    if username:
        extra = f'\n\nAdd me to a group: https://t.me/{username}?startgroup=true'
    await msg.reply_html(
        "You can take Tambola tickets now.\n\n" + HELP + extra,
        disable_web_page_preview=True,
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not msg:
        return
    await msg.reply_html(HELP, disable_web_page_preview=True)
