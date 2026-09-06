# nsfw-sticker-mod

Telegram group bot that deletes NSFW / vulgar stickers, warns people, demotes admins after 3 hits, and kicks them if they keep going.

You (the owner) are never punished and can still send any sticker.

## What it can and cannot do

Telegram has no official “this sticker is NSFW” flag. The bot **auto-detects** by:

1. Scanning the sticker thumbnail with NudeNet (photo-style nudity)
2. Banning the **whole pack** after one hit, so they cannot keep sending other stickers from that pack
3. Catching pack names that already contain words like `nsfw`, `hentai`, `lewd`
4. Remembering each sticker so it is not scanned twice

Cartoon / drawn stickers will sometimes slip through. Reply with `/kh_report` on those — that pack is banned from then on.

The sticker can flash for a second before it is deleted. That is a Telegram limit.

## Punishment rules

| Who | First hits | Then |
|---|---|---|
| You (owner / `OWNER_ID`) | Ignored | You can post everything |
| `/kh_approve` user | Unapproved | Next banned sticker → kicked |
| Admin | Warning 1, 2 | 3rd → demoted; 4th → kicked |
| Normal member | Warning 1, 2 | 3rd → kicked |

**Critical:** the bot can only demote admins **it made with** `/kh_makeadmin`. If your friends are already admins, drop them once, then reply `/kh_makeadmin`. They still look like admins (pin, invite, delete messages) but **cannot** add admins or kick the bot.

## Setup

1. Open [@BotFather](https://t.me/BotFather) → `/newbot` → copy the token.
2. `/setprivacy` → select the bot → **Disable** (or it will not see stickers).
3. Copy `.env.example` to `.env`. Put in your token and your Telegram user id (`@userinfobot` can tell you the id).
4. Add the bot to the **supergroup**. Promote it with:
   - Delete messages
   - Ban users
   - Add new admins
5. In admin rights for your friends: turn **off** “Add admins” and “Ban users” so they cannot remove the bot. Then drop them and `/kh_makeadmin` them through the bot.
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

## Command prefix

Telegram does not allow a hyphen in command names, so `/kh-help` cannot be registered. Commands use **`/kh_help`**.

Examples: `/kh_help` `/kh_kick` `/kh_makeadmin` `/kh_lock`

Rose can keep `/kick` and `/lock` while you migrate. This bot only answers the `/kh_…` names, so you can remove Rose when you are ready.

In a private chat with the bot, `/start` and `/help` still work.

Set `COMMAND_PREFIX=h_` in `.env` if you want `/h_help` instead.

**Replacing Rose:** welcome, goodbye, join verification, rules, notes, filters, blacklist, ban/mute, warns, pin/purge, and `/kh_staff` cover everyday Rose use. Not copied: federations, per-media locks (stickers/URLs as separate lock types), and connected chats. Sticker NSFW handling stays unique to this bot.

## Commands (reply to a user or sticker)

| Command | What it does |
|---|---|
| `/kh_welcome` | Custom join message (`{user}` `{name}` `{id}` `{chat}` `{count}`). `on` / `off` / `reset`. Reply to a photo to use it. |
| `/kh_verify` | Join captcha (on by default). `/kh_unverified` lists who has not tapped yet. Bots are banned on join. |
| `/kh_goodbye` | Leave message (not after a ban) |
| `/kh_cleanservice on` | Delete Telegram join/leave service messages |
| `/kh_rules` · `/kh_setrules` | Show / set group rules |
| `/kh_id` | Chat and user ids |
| `/kh_admins` | List admins |
| `/kh_staff` | Reply to ping admins (Rose-style report) |
| `/kh_ban` · `/kh_unban` | Ban (`/kh_ban @user 1d reason`) |
| `/kh_mute` · `/kh_unmute` | Mute (`/kh_mute 10m`) |
| `/kh_kick` | Manual kick (can rejoin) |
| `/kh_warn` · `/kh_warns` · `/kh_resetwarns` | Staff warnings; at the limit they are kicked |
| `/kh_pin` · `/kh_unpin` · `/kh_purge` · `/kh_del` | Pin / mass-delete (reply to a message) |
| `/kh_save` · `/kh_get` · `/kh_notes` | Notes; members can send `#name` |
| `/kh_filter` · `/kh_stop` | Auto-reply when a keyword is used |
| `/kh_blacklist` | Delete messages that contain a word |
| `/kh_zombies` | Preview deleted accounts. `/kh_zombies confirm` kicks now. Daily auto-clean is on (`/kh_zombies daily off` to stop). Frozen accounts are not visible to bots. |
| `/kh_makeadmin` | Make them admin via this bot (needed for auto-demote) |
| `/kh_dropadmin` | Remove one admin |
| `/kh_dropadmins` | Remove every admin the bot can (`/kh_dropadmins confirm`) |
| `/kh_approve` | Bypass until they send a banned sticker |
| `/kh_unapprove` | Remove approval |
| `/kh_trust` | Never punish this user |
| `/kh_report` or `/kh_blockpack` | Ban that sticker pack |
| `/kh_packs` | List banned packs. Tap **Allow** on the list — no sticker reply needed |
| `/kh_allowpack PackName` | Whitelist a pack by name (or tap Allow on `/kh_packs`) |
| `/kh_allowsticker` | Opens the same list, or `/kh_allowsticker s1` |
| `/kh_setlog` | Send kick/warning logs to another group or channel |
| `/kh_setkickmsg` | Custom kick message (`{user}` `{name}` `{id}` `{reason}` `{chat}`) |
| `/kh_setplaceholder` | Sticker to post after a delete |
| `/kh_strikes` | Show warnings |
| `/kh_forgive` | Clear sticker warnings |
| `/kh_joins` | How many joins I recorded (1h / 6h / 1d / 7d) |
| `/kh_purgejoins 2h` | Preview, then `/kh_purgejoins 2h confirm` to ban those joins |
| `/kh_lock` | Members cannot send messages |
| `/kh_unlock` | Restore permissions |
| `/kh_flood on` | Anti-flood (`/kh_flood 6 4`, `/kh_floodmute 10m`) |
| `/kh_scold` | Funny scolding (reply or @username); new line each time |
| `/kh_gamehelp` | Games guide (buttons). Also `/kh_gamehelp cricket` |
| `/kh_daily` | Daily coins + streak (resets at midnight IST) |
| `/kh_balance` | Your coins, wins, points |
| `/kh_top` | Leaderboard (`today` / `week` / `wins`) |
| `/kh_toss` | Coin toss. Solo: `heads`/`tails`. Reply to challenge a friend |
| `/kh_dice` | Telegram dice, or reply to duel |
| `/kh_lucky7` | Two dice: `low` / `7` / `high` (fun coins only) |
| `/kh_rps` | Stone-paper-scissors vs a member |
| `/kh_cricket` | Hand cricket vs a member (one over each) |
| `/kh_tag` | Set a member tag; `/kh_tag null` clears it |
| `/kh_help` | Command guide |

Send `2+2` or `(5*3)/2` with no command — the bot replies with the answer.

Only the group owner and `OWNER_IDS` can use these.

## Raid cleanup

Telegram does not give bots join dates for people already in the group. This bot records joins while it is running (up to 7 days). Keep it online as admin.

During an active flood: `/kh_raidmode 1h` then later `/kh_purgejoins 2h` → `/kh_purgejoins 2h confirm`.

