"""Pure game rules for the arena (cricket, rock-paper-scissors) and the
leaderboard math. No telegram, no db — every function here takes plain
data in and returns plain data out, so it can be unit tested without a
bot running. Keep it that way; put Telegram/db wiring in bot/arena.py.
"""
from __future__ import annotations

MAX_PLAYERS = 2

# ---------------------------------------------------------------------------
# Hand cricket
# ---------------------------------------------------------------------------


def cricket_new(a: int, b: int, batter: int, overs: int = 1) -> dict:
    """A fresh cricket match. `batter` bats innings 1. `overs` = 1, 2, or 3."""
    overs = max(1, min(3, overs))  # Clamp between 1 and 3
    return {
        "a": a,
        "b": b,
        "batter": batter,
        "innings": 1,
        "balls": 0,
        "overs": overs,
        "max_balls": overs * 6,
        "score_a": 0,
        "score_b": 0,
        "first_innings": None,
        "picks": {},
        "last": "",
    }


def _picks(ch: dict) -> dict[str, int]:
    raw = ch.get("picks") or {}
    out: dict[str, int] = {}
    if isinstance(raw, dict):
        for key, val in raw.items():
            try:
                out[str(int(key))] = int(val)
            except (TypeError, ValueError):
                continue
    ch["picks"] = out
    return out


def cricket_both_picked(ch: dict) -> bool:
    picks = _picks(ch)
    return str(ch["a"]) in picks and str(ch["b"]) in picks


def cricket_record_pick(ch: dict, user_id: int, n: int) -> str:
    """Lock a 1-6 pick for one player. Returns bad, dup, wait, or ready."""
    if n not in range(1, 7):
        return "bad"
    picks = _picks(ch)
    uid = str(int(user_id))
    if uid not in (str(ch["a"]), str(ch["b"])):
        return "bad"
    if uid in picks:
        return "ready" if cricket_both_picked(ch) else "dup"
    picks[uid] = n
    ch["picks"] = picks
    return "ready" if cricket_both_picked(ch) else "wait"


def cricket_apply_ball(ch: dict) -> str:
    """Score the two locked picks. Returns play, chase, or finished.

    Target for the chase is always (first innings score + 1) — this already
    covers "out for 0" correctly (target becomes 1) with no special case.
    The chase ends the instant the target is passed, mid-over if needed.
    """
    max_balls = int(ch.get("max_balls") or 6)
    picks = _picks(ch)
    pa, pb = int(picks[str(ch["a"])]), int(picks[str(ch["b"])])
    ch["picks"] = {}
    batter = ch["batter"]
    bat_n = pa if batter == ch["a"] else pb
    bowl_n = pb if batter == ch["a"] else pa
    out = bat_n == bowl_n
    played = int(ch.get("balls") or 0) + 1
    if out:
        ch["balls"] = max_balls
        ch["last"] = f"OUT — both picked {bat_n}"
    else:
        key = "score_a" if batter == ch["a"] else "score_b"
        ch[key] = int(ch.get(key) or 0) + bat_n
        ch["balls"] = played
        ch["last"] = f"+{bat_n} runs ({bat_n} vs {bowl_n})"

    chase_score = int((ch["score_a"] if batter == ch["a"] else ch["score_b"]) or 0)
    innings = int(ch.get("innings") or 1)
    first = ch.get("first_innings")
    if innings == 2 and first is not None and chase_score > int(first):
        ch["waiting"] = "Chase complete."
        return "finished"

    over = out or int(ch.get("balls") or 0) >= max_balls
    if over:
        if innings == 1:
            total = int((ch["score_a"] if batter == ch["a"] else ch["score_b"]) or 0)
            ch["first_innings"] = total
            ch["innings"] = 2
            ch["balls"] = 0
            ch["batter"] = ch["b"] if batter == ch["a"] else ch["a"]
            ch["waiting"] = f"Need {total + 1} to win. Both pick 1-6."
            ch["last"] = f"First innings over at {total}."
            return "chase"
        ch["waiting"] = "Match over."
        return "finished"
    ch["waiting"] = "Next ball - both pick 1-6."
    return "play"


def cricket_winner(ch: dict) -> int | None:
    """Winner's user id, or None for a tie. Only meaningful once finished."""
    sa, sb = int(ch.get("score_a") or 0), int(ch.get("score_b") or 0)
    if sa > sb:
        return int(ch["a"])
    if sb > sa:
        return int(ch["b"])
    return None


def cricket_summary(ch: dict) -> str:
    return f"{int(ch.get('score_a') or 0)} - {int(ch.get('score_b') or 0)}"


# ---------------------------------------------------------------------------
# Rock-paper-scissors
# ---------------------------------------------------------------------------

RPS_MOVES = ("rock", "paper", "scissors")
RPS_LABELS = {"rock": "Rock", "paper": "Paper", "scissors": "Scissors"}
_RPS_BEATS = {"rock": "scissors", "paper": "rock", "scissors": "paper"}


def rps_new(a: int, b: int) -> dict:
    return {"a": a, "b": b, "picks": {}}


def rps_both_picked(ch: dict) -> bool:
    picks = ch.get("picks") or {}
    return str(ch["a"]) in picks and str(ch["b"]) in picks


def rps_record_pick(ch: dict, user_id: int, move: str) -> str:
    """Lock a move for one player. Returns bad, dup, wait, or ready."""
    if move not in RPS_MOVES:
        return "bad"
    picks = ch.get("picks") if isinstance(ch.get("picks"), dict) else {}
    ch["picks"] = picks
    uid = str(int(user_id))
    if uid not in (str(ch["a"]), str(ch["b"])):
        return "bad"
    if uid in picks:
        return "ready" if rps_both_picked(ch) else "dup"
    picks[uid] = move
    return "ready" if rps_both_picked(ch) else "wait"


def rps_resolve(ch: dict) -> tuple[int | None, str, str]:
    """Resolve both locked moves. Returns (winner_id or None, move_a, move_b)."""
    picks = ch.get("picks") or {}
    move_a = str(picks[str(ch["a"])])
    move_b = str(picks[str(ch["b"])])
    if move_a == move_b:
        return None, move_a, move_b
    if _RPS_BEATS[move_a] == move_b:
        return int(ch["a"]), move_a, move_b
    return int(ch["b"]), move_a, move_b


# ---------------------------------------------------------------------------
# Leaderboard math (operates on plain result rows, not db rows)
# ---------------------------------------------------------------------------


def aggregate_leaderboard(
    results: list[dict],
    *,
    limit: int = 10,
) -> list[dict]:
    """results: dicts with a_id, a_name, b_id, b_name, winner_id, finished_at.
    Returns rows sorted by wins desc, losses asc: user_id, name, wins, losses, draws.
    """
    stats: dict[int, dict] = {}

    def touch(uid: int, name: str, finished_at: float) -> dict:
        row = stats.setdefault(
            uid,
            {"user_id": uid, "name": name, "wins": 0, "losses": 0, "draws": 0, "_last": -1.0},
        )
        if finished_at >= row["_last"]:
            row["name"] = name
            row["_last"] = finished_at
        return row

    for r in results:
        a_id, b_id = int(r["a_id"]), int(r["b_id"])
        finished_at = float(r.get("finished_at") or 0)
        row_a = touch(a_id, str(r["a_name"]), finished_at)
        row_b = touch(b_id, str(r["b_name"]), finished_at)
        winner = r.get("winner_id")
        if winner is None:
            row_a["draws"] += 1
            row_b["draws"] += 1
            continue
        winner = int(winner)
        if winner == a_id:
            row_a["wins"] += 1
            row_b["losses"] += 1
        else:
            row_b["wins"] += 1
            row_a["losses"] += 1

    ranked = sorted(stats.values(), key=lambda s: (-s["wins"], s["losses"], s["name"]))
    for row in ranked:
        row.pop("_last", None)
    return ranked[:limit]


def paginate(total: int, page: int, per_page: int) -> tuple[int, int, int]:
    """Clamp `page` (0-based) to a valid range for `total` items.
    Returns (page, offset, total_pages). total_pages is at least 1.
    """
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    return page, page * per_page, total_pages
