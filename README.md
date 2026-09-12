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

## Commands

Remove **Rose** (or any other group bot) **before** or right after this deploy, or both will answer `/kick` and `/lock`.

Send `/help` in the group. Common names: `/kick` `/ban` `/mute` `/lock` `/welcome`.

## Commands (reply to a user or sticker)

| Command | What it does |
|---|---|
| `/welcome` | Custom join message (`{user}` `{name}` `{id}` `{chat}` `{count}`). `on` / `off` / `reset`. Reply to a photo to use it. |
| `/verify` | Join captcha (on by default). `/unverified` lists who has not tapped yet. Telegram bots you add are kept; fake/spam accounts must tap the button. |
| `/goodbye` | Leave message (not after a ban) |
| `/cleanservice on` | Delete Telegram join/leave service messages |
| `/rules` · `/setrules` | Show / set group rules |
| `/id` | Chat and user ids |
| `/admins` | List admins |
| `/staff` | Reply to ping admins (Rose-style report) |
| `/ban` · `/unban` | Ban (`/ban @user 1d reason`) |
| `/mute` · `/unmute` | Mute (`/mute 10m`) |
| `/kick` | Manual kick (can rejoin) |
| `/warn` · `/warns` · `/resetwarns` | Staff warnings; at the limit they are kicked |
| `/pin` · `/unpin` · `/purge` · `/del` | Pin / mass-delete (reply to a message) |
| `/save` · `/get` · `/notes` | Notes; members can send `#name` |
| `/filter` · `/stop` | Auto-reply when a keyword is used |
| `/blacklist` | Delete messages that contain a word |
| `/zombies` | Preview deleted accounts. `/zombies confirm` kicks now. Daily auto-clean is on (`/zombies daily off` to stop). Frozen accounts are not visible to bots. |
| `/makeadmin` | Make them admin via this bot (needed for auto-demote). Optional role: `helper`, `mod` (default), `admin` |
| `/dropadmin` | Remove one admin |
| `/dropadmins` | Remove every admin the bot can (`/dropadmins confirm`) |
| `/approve` | Bypass until they send a banned sticker |
| `/unapprove` | Remove approval |
| `/trust` | Never punish this user |
| `/report` or `/blockpack` | Ban that sticker pack |
| `/packs` | List banned packs. Tap **Allow** on the list — no sticker reply needed |
| `/allowpack PackName` | Whitelist a pack by name (or tap Allow on `/packs`) |
| `/allowsticker` | Opens the same list, or `/allowsticker s1` |
| `/setlog` | Send kick/warning logs to another group or channel |
| `/setkickmsg` | Custom kick message (`{user}` `{name}` `{id}` `{reason}` `{chat}`) |
| `/setplaceholder` | Sticker to post after a delete |
| `/strikes` | Show warnings, role, and permissions for a user |
| `/forgive` | Clear sticker warnings |
| `/joins` | How many joins I recorded (1h / 6h / 1d / 7d) |
| `/purgejoins 2h` | Preview, then `/purgejoins 2h confirm` to ban those joins |
| `/lock` | Members cannot send messages |
| `/unlock` | Restore permissions |
| `/flood on` | Anti-flood (`/flood 6 4`, `/floodmute 10m`) |
| `/scold` | Funny scolding (reply or @username); new line each time |
| `/ask [question]` | Ask anything — free lookup (no API key needed) |
| `/tag` | Set a member tag; `/tag null` clears it |
| `/cricket @user` | Hand cricket. Accept first, then challenger picks 1–3 overs. Matching picks = out |
| `/rps @user` | One round of rock-paper-scissors |
| `/wordle` | Group Wordle (4 or 5 letters). Anyone starts; one game at a time. `/wordle cancel` for staff |
| `/gboard` | Games leaderboard, last 3 days. `/gboard cricket` / `/gboard rps` — paginated match history |
| `/active` | Activity leaderboard (daily/weekly/monthly) - who sends the most messages |
| `/giveaway` | Admin only: start a giveaway (`/giveaway 10m 1 Prize`) |
| `/gcancel` | Admin only: cancel a giveaway (reply to the message) |
| `/ghistory` | Admin only: view past giveaways and reroll history |
| `/greroll` | Admin only: reroll giveaway winners (reply to giveaway message) |
| `/rights` | Admin: show which permissions the bot has in this group |
| `/backupdb` | Bot owner only: download a SQLite backup (use in private chat) |
| `/release send` | Owner only: deliver that card to known starters |
| `/help` | Command guide |

Send `2+2` or `(5*3)/2` with no command — the bot replies with the answer.

Only the group owner and `OWNER_IDS` can use these.

## Raid cleanup

Telegram does not give bots join dates for people already in the group. This bot records joins while it is running (up to 7 days). Keep it online as admin.

During an active flood: `/raidmode 1h` then later `/purgejoins 2h` → `/purgejoins 2h confirm`.

## Games

`/cricket` and `/rps` are text + button matches, all state kept in the database (no images, no in-memory state to lose on restart).

**Wordle:** `/wordle` (or `/wordle 4` / `/wordle 5`) starts a shared group puzzle. Anyone can start; only one game runs at a time. Players guess by sending any 4- or 5-letter word; the board is refreshed by deleting the old message and posting a new one together (so it stays quick and tidy). First correct guess wins. After 5 minutes the word is revealed. Per-person guess cooldown avoids spam. Staff can stop early with `/wordle cancel` and start again.

**Cricket improvements:**
- **Accept first**: Rival Accepts/Declines the challenge before overs are chosen
- **Over selection**: After accept, challenger picks 1, 2, or 3 overs (1 over = 6 balls each)
- **Better UI**: Clean table format with clear scoreboard and batting/bowling info
- **Opponent names**: Shows "Waiting for [name]..." instead of "other pick"
- **Spam protection**: 500ms cooldown between button clicks to prevent lag
- **Smooth gameplay**: Instant feedback with emojis and clear status messages

At most 3 matches of each cricket/rps game run at once per group. The same person cannot be in two cricket (or rps) matches at the same time. An unaccepted challenge auto-closes after 1 minute; a live match with no move for 5 minutes auto-closes with no result recorded. Match history (`/gboard cricket` / `/gboard rps`) only keeps the last 3 days.

**Important:** When challenging someone with `/cricket @username`, you must either:
- Reply to their message, or
- Type `/cricket @` and select their name from Telegram's suggestion menu (don't just type the username)

If the person has never sent a message in the chat, the bot can't find them. Ask them to send any message first.

## Activity Tracking

Every group message is counted per user. The bot tracks:
- **Daily** (resets after 24 hours)
- **Weekly** (resets after 7 days)
- **Monthly** (resets after 30 days)

Use `/active` (or `/active daily` / `/active weekly` / `/active monthly`) to see the top 10 chatters. This helps identify the most active members and is used as a requirement condition for giveaways.

## Giveaways

Admins can run giveaways with `/giveaway <duration> <winners> <prize>`:

```
/giveaway 10m 1 iPad Pro
/giveaway 1h 2 Discord Nitro
/giveaway 30m 1 Prize req:10 age:7
```

**Features:**
- Duration: 10s, 5m, 1h, 2d (up to 30 days)
- Multiple winners: 1-10
- Requirements:
  - `req:10` — minimum 10 messages needed
  - `age:7` — account age 7+ days (optional)
- Members click "Enter Giveaway" button to participate
- Winners are auto-announced when time expires with a claim board
- Winners tap **Claim Prize** to prove they're active; admins **Confirm** after handing over the prize
- Admins can **Reroll unclaimed** from the board (confirmed winners are kept)
- `/gcancel` (reply to giveaway message) to cancel
- `/greroll` (reply to giveaway/winner message, or use the id from `/ghistory`)
- `/ghistory` shows last 10 giveaways with ids, winners, and claim status
- Multiple giveaways can run in parallel with different timings
- Real-time instant capture and announcements

**Daily Zombie Scan:**
The bot checks for deleted/deactivated accounts daily and asks if you want to kick them. If none found, it sends a "no zombies found" message. Reply `/zombies confirm` when prompted to kick them.

## Production (Railway / multi-group)

The bot is already multi-group: each chat has its own settings, trusted users, strikes, games, etc.

**Permissions (no silent failures):** If the bot can't delete, kick, mute, or announce, it tells the group in plain English (throttled so it doesn't spam). Admins can run `/rights` anytime.

**Telegram flood limits:** Outbound API calls go through a paced gateway that respects `RetryAfter` and spaces global/per-chat traffic. Heavy jobs (`/tagall`, release broadcast) still send in chunks.

**SQLite / data:**
1. Mount **one** persistent volume (e.g. Railway volume at `/data`).
2. Set `DATA_DIR=/data` (Docker already defaults to this).
3. Run **exactly one** replica — two instances fighting over one SQLite file (or the same bot token) will break.
4. Back up regularly:
   - Owner: `/backupdb` in a private chat with the bot
   - Or: `python scripts/backup_db.py` (writes under `backups/`)

**Immune vs owner:** Put only yourself in `OWNER_IDS`. Put friends who should never be punished (but shouldn't get owner commands) in `IMMUNE_IDS`. Group owners use `/trust` for per-group immunity only.

