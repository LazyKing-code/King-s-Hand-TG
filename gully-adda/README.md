# Gully Adda

A Telegram group bot for Indian party games. This folder is its own bot: its own token, its own database, and no connection to sticker moderation.

The first version has two games.

**Tambola / Housie.** Anyone in the group can take a ticket. The card is sent in a private chat. The bot calls numbers from 1 to 90. The first valid line and the first valid full house win.

**Gully hand cricket.** People join Team A or Team B. The host picks 1, 2, or 3 overs. Each ball, the current batter and the current bowler pick 1–6 in secret. The same number is out, and the next batter comes in. The other side then chases.

`/board` shows wins for this group.

## Setup

1. Open [@BotFather](https://t.me/BotFather) and send `/newbot`. Copy the token.
2. Send `/setprivacy`, pick this bot, and choose **Disable**, so later word games can read the chat. Tambola and cricket already work from commands and buttons.
3. Copy `.env.example` to `.env`. Set `BOT_TOKEN` and your Telegram user id (`@userinfobot`).
4. Add the bot to a group.

```text
cd gully-adda
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m bot.main
```

On Windows, activate with `.venv\Scripts\activate` instead.

Keep that process running. One copy only — a second copy with the same token will not receive games.

## Commands

| Command | What it does |
|---|---|
| `/tambola` | Open a Tambola lobby. Tickets, then the host taps Begin. |
| `/tambola cancel` | Host or a group admin stops it. |
| `/cricket` | Open a gully cricket lobby. Join A or B, pick overs, Begin. |
| `/cricket cancel` | Host or a group admin stops it. |
| `/board` | Tambola claims and cricket wins in this group. |
| `/help` | Short rules. |
| `/start` | Required in a private chat before a Tambola ticket can be delivered. |

One Tambola game and one cricket match can be open in a group at a time.

## Try it

In a test group, two people send `/start` to the bot in private, then:

- `/tambola` — both tap **Take ticket**, the host taps **Begin**, then one of them claims **Line** after a full row is called.
- `/cricket` — three people join across the two sides, the host picks **1 over** and **Begin**, and a matching 1–6 pick brings in the next batter.

## Tests

From this folder:

```text
python -m unittest discover -s tests
```
