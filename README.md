# nsfw-sticker-mod

Telegram group bot that deletes NSFW / vulgar stickers, warns people, demotes admins after 3 hits, and kicks them if they keep going.

You (the owner) are never punished and can still send any sticker.

## What it can and cannot do

Telegram has no official “this sticker is NSFW” flag. The bot **auto-detects** by:

1. Scanning the sticker thumbnail with NudeNet (photo-style nudity)
2. Banning the **whole pack** after one hit, so they cannot keep sending other stickers from that pack
3. Catching pack names that already contain words like `nsfw`, `hentai`, `lewd`
4. Remembering each sticker so it is not scanned twice

Cartoon / drawn stickers will sometimes slip through. Reply with `/report` on those — that pack is banned from then on.

The sticker can flash for a second before it is deleted. That is a Telegram limit.

## Punishment rules

| Who | First hits | Then |
|---|---|---|
| You (owner / `OWNER_ID`) | Ignored | You can post everything |
| `/approve` user | Unapproved | Next banned sticker → kicked |
| Admin | Warning 1, 2 | 3rd → demoted; 4th → kicked |
| Normal member | Warning 1, 2 | 3rd → kicked |

**Critical:** the bot can only demote admins **it made with** `/makeadmin`. If your friends are already admins, drop them once, then reply `/makeadmin`. They still look like admins (pin, invite, delete messages) but **cannot** add admins or kick the bot.

## Setup

1. Open [@BotFather](https://t.me/BotFather) → `/newbot` → copy the token.
2. `/setprivacy` → select the bot → **Disable** (or it will not see stickers).
3. Copy `.env.example` to `.env`. Put in your token and your Telegram user id (`@userinfobot` can tell you the id).
4. Add the bot to the **supergroup**. Promote it with:
   - Delete messages
   - Ban users
   - Add new admins
5. In admin rights for your friends: turn **off** “Add admins” and “Ban users” so they cannot remove the bot. Then drop them and `/makeadmin` them through the bot.
6. Install and run (Windows):

```text
cd C:\Users\Subhan\nsfw-sticker-mod
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m bot.main
```

First start may download the NudeNet model (internet required).

Keep this window open, or later run it as a Windows service / Task Scheduler.

## Commands (reply to a user or sticker)

| Command | What it does |
|---|---|
| `/makeadmin` | Make them admin via this bot (needed for auto-demote) |
| `/dropadmin` | Remove one admin |
| `/dropadmins` | Remove every admin the bot can (`/dropadmins confirm`) |
| `/approve` | Bypass until they send a banned sticker |
| `/unapprove` | Remove approval |
| `/trust` | Never punish this user |
| `/report` or `/blockpack` | Ban that sticker pack |
| `/allowpack` | False positive: allow this pack |
| `/allowsticker` | Allow one sticker only |
| `/setlog` | Send kick/warning logs to another group or channel |
| `/setkickmsg` | Custom kick message (`{user}` `{name}` `{id}` `{reason}` `{chat}`) |
| `/setplaceholder` | Sticker to post after a delete |
| `/strikes` | Show warnings |
| `/forgive` | Clear warnings |
| `/kick` | Manual kick |
| `/joins` | How many joins I recorded (1h / 6h / 1d / 7d) |
| `/purgejoins 2h` | Preview, then `/purgejoins 2h confirm` to ban those joins |
| `/lock` | Members cannot send messages |
| `/unlock` | Restore permissions |
| `/flood on` | Anti-flood (`/flood 6 4`, `/floodmute 10m`) |
| `/scold` | Funny scolding (reply or @username); new line each time |
| `/tag` | Set a member tag; `/tag null` clears it |
| `/help` | Command guide |

Send `2+2` or `(5*3)/2` with no command — the bot replies with the answer.

Only the group owner and `OWNER_IDS` can use these.

## Raid cleanup

Telegram does not give bots join dates for people already in the group. This bot records joins while it is running (up to 7 days). Keep it online as admin.

During an active flood: `/raidmode 1h` then later `/purgejoins 2h` → `/purgejoins 2h confirm`.

