"""/top command - activity leaderboard (daily, weekly, monthly)."""
from __future__ import annotations

from html import escape

from telegram import Update
from telegram.ext import ContextTypes

from bot import db
from bot.commands import cmd
from bot.moderation import require_group


async def cmd_active(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show activity leaderboard for daily/weekly/monthly message counts."""
    if not await require_group(update):
        return
    
    msg = update.effective_message
    chat_id = update.effective_chat.id
    args = [a.lower() for a in (context.args or [])]
    
    # Default to daily if no period specified
    period = "daily"
    if args:
        if args[0] in ("day", "daily", "today"):
            period = "daily"
        elif args[0] in ("week", "weekly"):
            period = "weekly"
        elif args[0] in ("month", "monthly"):
            period = "monthly"
        else:
            await msg.reply_text(
                f"Usage: {cmd('active')} [daily|weekly|monthly]\n"
                "Shows who has sent the most messages in the chat."
            )
            return
    
    board = db.get_activity_leaderboard(chat_id, period, limit=10)
    
    if not board:
        await msg.reply_text(
            f"No activity data yet for {period} period.\n"
            "Message counts start tracking from when this feature was added."
        )
        return
    
    # Build leaderboard text
    period_name = {"daily": "Today", "weekly": "This Week", "monthly": "This Month"}[period]
    lines = [f"<b>🏆 Top Chatters — {period_name}</b>\n"]
    
    for i, entry in enumerate(board, start=1):
        user_id = entry["user_id"]
        count = entry["count"]
        
        # Try to get user info from seen_users
        user_info = db.lookup_seen_id(user_id)
        if user_info and user_info.get("username"):
            name = f"@{user_info['username']}"
        elif user_info and user_info.get("first_name"):
            name = escape(user_info["first_name"])
        else:
            name = f"User {user_id}"
        
        emoji = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
        lines.append(f"{emoji} {name} — {count} messages")
    
    lines.append("")
    lines.append(
        f"See other periods: {cmd('active')} daily · {cmd('active')} weekly · {cmd('active')} monthly"
    )
    
    await msg.reply_html("\n".join(lines), disable_web_page_preview=True)
