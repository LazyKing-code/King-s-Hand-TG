"""Tambola / Housie rules. No Telegram and no database."""

from __future__ import annotations

import random

MAX_TICKETS = 30
MIN_PLAYERS = 2
CALL_INTERVAL = 8
CLAIM_WINDOW = 20
LOBBY_TTL = 5 * 60
LOBBY_MAX = 15 * 60
# A live game with no successful call for this long is abandoned.
LIVE_STALL_TTL = 3 * 60

# Inclusive column ranges used on a standard 1–90 housie ticket.
COLUMN_BOUNDS = (
    (1, 9),
    (10, 19),
    (20, 29),
    (30, 39),
    (40, 49),
    (50, 59),
    (60, 69),
    (70, 79),
    (80, 90),
)


def make_ticket(rng: random.Random | None = None) -> list[list[int | None]]:
    """A 3×9 ticket: 15 numbers, 5 per row, each column used and sorted downward."""
    rng = rng or random.Random()
    for _ in range(200):
        ticket = _try_ticket(rng)
        if ticket is not None and ticket_ok(ticket):
            return ticket
    raise RuntimeError("could not build a tambola ticket")


def ticket_ok(ticket: list[list[int | None]]) -> bool:
    if len(ticket) != 3 or any(len(row) != 9 for row in ticket):
        return False
    numbers: list[int] = []
    for row in ticket:
        row_nums = [n for n in row if n is not None]
        if len(row_nums) != 5:
            return False
        numbers.extend(row_nums)
    if len(numbers) != 15 or len(set(numbers)) != 15:
        return False
    if any(n < 1 or n > 90 for n in numbers):
        return False
    for col, (lo, hi) in enumerate(COLUMN_BOUNDS):
        col_nums = [ticket[r][col] for r in range(3) if ticket[r][col] is not None]
        if not col_nums:
            return False
        if len(col_nums) > 3:
            return False
        if any(n < lo or n > hi for n in col_nums):
            return False
        if col_nums != sorted(col_nums):
            return False
    return True


def ticket_numbers(ticket: list[list[int | None]]) -> list[int]:
    return [n for row in ticket for n in row if n is not None]


def _try_ticket(rng: random.Random) -> list[list[int | None]] | None:
    pools = [list(range(lo, hi + 1)) for lo, hi in COLUMN_BOUNDS]
    for pool in pools:
        rng.shuffle(pool)

    counts = [0] * 9
    chosen: list[set[int]] = []
    for _row in range(3):
        row_cols: set[int] = set()
        guard = 0
        while len(row_cols) < 5:
            guard += 1
            if guard > 100:
                return None
            col = rng.randrange(9)
            if col in row_cols or counts[col] >= 3:
                continue
            row_cols.add(col)
        for col in row_cols:
            counts[col] += 1
        chosen.append(row_cols)

    if any(count == 0 for count in counts):
        return None

    grid: list[list[int | None]] = [[None] * 9 for _ in range(3)]
    for row, cols in enumerate(chosen):
        for col in cols:
            grid[row][col] = pools[col].pop()

    for col in range(9):
        values = sorted(grid[row][col] for row in range(3) if grid[row][col] is not None)
        index = 0
        for row in range(3):
            if grid[row][col] is not None:
                grid[row][col] = values[index]
                index += 1
    return grid


def new_game(starter_id: int, starter_name: str) -> dict:
    return {
        "starter_id": int(starter_id),
        "starter_name": starter_name,
        "players": {},
        "called": [],
        "pool": list(range(1, 91)),
        "started": False,
        "line_winner": None,
        "full_winner": None,
        "last_call_at": None,
        "claim_until": None,
    }


def player(game: dict, user_id: int) -> dict | None:
    raw = (game.get("players") or {}).get(str(int(user_id)))
    return raw if isinstance(raw, dict) else None


def add_player(
    game: dict,
    user_id: int,
    name: str,
    rng: random.Random | None = None,
    ticket: list[list[int | None]] | None = None,
) -> str:
    """Returns ok, dup, full, or closed.

    Pass `ticket` when the card was already dealt (so a failed private
    message does not leave a player without a card).
    """
    if game.get("started"):
        return "closed"
    uid = str(int(user_id))
    if uid in (game.get("players") or {}):
        return "dup"
    if len(game.get("players") or {}) >= MAX_TICKETS:
        return "full"
    game.setdefault("players", {})[uid] = {
        "name": name,
        "ticket": ticket if ticket is not None else make_ticket(rng),
    }
    return "ok"


def begin(game: dict, rng: random.Random | None = None) -> str:
    """Returns ok, started, or need_players."""
    if game.get("started"):
        return "started"
    if len(game.get("players") or {}) < MIN_PLAYERS:
        return "need_players"
    rng = rng or random.Random()
    pool = list(game.get("pool") or range(1, 91))
    rng.shuffle(pool)
    game["pool"] = pool
    game["started"] = True
    game["called"] = []
    game["claim_until"] = None
    return "ok"


def call_next(game: dict, now: float) -> int | None:
    """Draw one number. Returns None when the pool is empty or the house is won."""
    if game.get("full_winner") or game.get("claim_until"):
        return None
    pool = list(game.get("pool") or [])
    if not pool:
        return None
    number = int(pool.pop(0))
    game["pool"] = pool
    called = list(game.get("called") or [])
    called.append(number)
    game["called"] = called
    game["last_call_at"] = float(now)
    if not pool:
        game["claim_until"] = float(now) + CLAIM_WINDOW
    return number


def called_set(game: dict) -> set[int]:
    return {int(n) for n in (game.get("called") or [])}


def line_ready(ticket: list[list[int | None]], called: set[int]) -> bool:
    for row in ticket:
        nums = [n for n in row if n is not None]
        if nums and all(n in called for n in nums):
            return True
    return False


def full_house_ready(ticket: list[list[int | None]], called: set[int]) -> bool:
    nums = ticket_numbers(ticket)
    return bool(nums) and all(n in called for n in nums)


def claim(game: dict, user_id: int, kind: str) -> str:
    """Returns line, full, taken, invalid, absent, or bad.

    A wrong claim does not remove the player.
    """
    if kind not in ("line", "full"):
        return "bad"
    who = player(game, user_id)
    if not who:
        return "absent"
    ticket = who.get("ticket") or []
    called = called_set(game)
    if kind == "line":
        if game.get("line_winner"):
            return "taken"
        if not line_ready(ticket, called):
            return "invalid"
        game["line_winner"] = {"id": int(user_id), "name": who.get("name") or str(user_id)}
        return "line"
    if game.get("full_winner"):
        return "taken"
    if not full_house_ready(ticket, called):
        return "invalid"
    game["full_winner"] = {"id": int(user_id), "name": who.get("name") or str(user_id)}
    return "full"


def is_over(game: dict, now: float | None = None) -> bool:
    if game.get("full_winner"):
        return True
    until = game.get("claim_until")
    if until is not None and now is not None and float(now) >= float(until):
        return True
    return False


def format_ticket(ticket: list[list[int | None]]) -> str:
    lines = []
    for row in ticket:
        cells = [f"{n:2d}" if n is not None else " ." for n in row]
        lines.append(" ".join(cells))
    return "\n".join(lines)
