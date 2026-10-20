"""Team hand cricket. No Telegram and no database.

Team A bats first. Each ball the current batter and the current bowler
pick 1–6 in secret. The same number is out; otherwise the batter's number
is the runs. A wicket brings the next batter. The bowler changes every ball.
The innings ends on all out or when the overs are bowled. The chase can
finish mid-over.
"""

from __future__ import annotations

MAX_PER_TEAM = 11
MIN_PLAYERS = 2
IDLE_TTL = 90
LOBBY_TTL = 5 * 60
LOBBY_MAX = 15 * 60


def cricket_new(starter_id: int, starter_name: str) -> dict:
    return {
        "starter_id": int(starter_id),
        "starter_name": starter_name,
        "overs": 1,
        "team_a": [],
        "team_b": [],
        "innings": 1,
        "batting": "a",
        "batter_idx": 0,
        "bowler_idx": 0,
        "balls": 0,
        "score_a": 0,
        "score_b": 0,
        "out_a": [],
        "out_b": [],
        "first_innings": None,
        "picks": {},
        "last": "",
        "stalls": {},
    }


def set_overs(game: dict, overs: int) -> str:
    if overs not in (1, 2, 3):
        return "bad"
    game["overs"] = overs
    return "ok"


def _side_key(side: str) -> str:
    if side not in ("a", "b"):
        raise ValueError(side)
    return "team_a" if side == "a" else "team_b"


def _out_key(side: str) -> str:
    return "out_a" if side == "a" else "out_b"


def team(game: dict, side: str) -> list[dict]:
    raw = game.get(_side_key(side)) or []
    return [p for p in raw if isinstance(p, dict) and "id" in p]


def join(game: dict, user_id: int, name: str, side: str) -> str:
    """Join a side, switching off the other side. Returns ok, full, or bad.

    A full side does not remove the player from the side they are already on.
    """
    if side not in ("a", "b"):
        return "bad"
    uid = int(user_id)
    if side_of(game, uid) == side:
        for person in team(game, side):
            if int(person["id"]) == uid:
                person["name"] = name
        return "ok"
    already = [p for p in team(game, side) if int(p["id"]) != uid]
    if len(already) >= MAX_PER_TEAM:
        return "full"
    for key in ("team_a", "team_b"):
        game[key] = [p for p in (game.get(key) or []) if int(p.get("id", -1)) != uid]
    players = team(game, side)
    players.append({"id": uid, "name": name})
    game[_side_key(side)] = players
    return "ok"


def side_of(game: dict, user_id: int) -> str | None:
    uid = int(user_id)
    if any(int(p["id"]) == uid for p in team(game, "a")):
        return "a"
    if any(int(p["id"]) == uid for p in team(game, "b")):
        return "b"
    return None


def can_begin(game: dict) -> str:
    """Returns ok or need_sides. Two players on opposite sides is enough."""
    if not team(game, "a") or not team(game, "b"):
        return "need_sides"
    if len(team(game, "a")) + len(team(game, "b")) < MIN_PLAYERS:
        return "need_sides"
    return "ok"


def begin(game: dict) -> str:
    result = can_begin(game)
    if result != "ok":
        return result
    game["innings"] = 1
    game["batting"] = "a"
    game["batter_idx"] = 0
    game["bowler_idx"] = 0
    game["balls"] = 0
    game["score_a"] = 0
    game["score_b"] = 0
    game["out_a"] = []
    game["out_b"] = []
    game["first_innings"] = None
    game["picks"] = {}
    game["last"] = ""
    game["stalls"] = {}
    return "ok"


def max_balls(game: dict) -> int:
    return max(1, min(3, int(game.get("overs") or 1))) * 6


def over_label(balls: int) -> str:
    return f"{int(balls) // 6}.{int(balls) % 6}"


def fielding_side(game: dict) -> str:
    return "b" if game.get("batting") == "a" else "a"


def _outs(game: dict, side: str) -> set[int]:
    return {int(x) for x in (game.get(_out_key(side)) or [])}


def _from_index(players: list[dict], outs: set[int], start: int) -> dict | None:
    if not players:
        return None
    count = len(players)
    for step in range(count):
        person = players[(int(start) + step) % count]
        if int(person["id"]) not in outs:
            return person
    return None


def current_batter(game: dict) -> dict | None:
    side = "a" if game.get("batting") != "b" else "b"
    return _from_index(team(game, side), _outs(game, side), int(game.get("batter_idx") or 0))


def current_bowler(game: dict) -> dict | None:
    players = team(game, fielding_side(game))
    if not players:
        return None
    return players[int(game.get("bowler_idx") or 0) % len(players)]


def _index_of(players: list[dict], user_id: int) -> int:
    uid = int(user_id)
    for index, person in enumerate(players):
        if int(person["id"]) == uid:
            return index
    return 0


def both_picked(game: dict) -> bool:
    batter = current_batter(game)
    bowler = current_bowler(game)
    if not batter or not bowler:
        return False
    picks = game.get("picks") or {}
    return str(int(batter["id"])) in picks and str(int(bowler["id"])) in picks


def record_pick(game: dict, user_id: int, number: int) -> str:
    """Returns bad, spectator, dup, wait, or ready."""
    if number not in range(1, 7):
        return "bad"
    batter = current_batter(game)
    bowler = current_bowler(game)
    if not batter or not bowler:
        return "bad"
    uid = int(user_id)
    if uid not in (int(batter["id"]), int(bowler["id"])):
        return "spectator"
    picks = dict(game.get("picks") or {})
    key = str(uid)
    if key in picks:
        game["picks"] = picks
        return "ready" if both_picked(game) else "dup"
    picks[key] = int(number)
    game["picks"] = picks
    return "ready" if both_picked(game) else "wait"


def _clear_stall(game: dict, user_id: int) -> None:
    stalls = dict(game.get("stalls") or {})
    stalls.pop(str(int(user_id)), None)
    game["stalls"] = stalls


def _advance_bowler(game: dict) -> None:
    players = team(game, fielding_side(game))
    if players:
        game["bowler_idx"] = (int(game.get("bowler_idx") or 0) + 1) % len(players)


def _score(game: dict, side: str) -> int:
    return int(game.get("score_a" if side == "a" else "score_b") or 0)


def _end_innings(game: dict, *, all_out: bool) -> str:
    side = "a" if game.get("batting") != "b" else "b"
    total = _score(game, side)
    ball = str(game.get("last") or "").strip()
    if int(game.get("innings") or 1) == 1:
        game["first_innings"] = total
        game["innings"] = 2
        game["batting"] = "b" if side == "a" else "a"
        game["balls"] = 0
        game["batter_idx"] = 0
        game["bowler_idx"] = 0
        game["picks"] = {}
        reason = "all out" if all_out else "overs done"
        note = f"Innings over ({reason}) at {total}."
        game["last"] = f"{ball} {note}".strip()
        return "chase"
    return "finished"


def apply_ball(game: dict) -> str:
    """Score the locked picks. Returns play, chase, or finished."""
    batter = current_batter(game)
    bowler = current_bowler(game)
    if not batter or not bowler or not both_picked(game):
        return "play"
    picks = game.get("picks") or {}
    bat_n = int(picks[str(int(batter["id"]))])
    bowl_n = int(picks[str(int(bowler["id"]))])
    game["picks"] = {}
    _clear_stall(game, int(batter["id"]))
    _clear_stall(game, int(bowler["id"]))

    side = "a" if game.get("batting") != "b" else "b"
    players = team(game, side)
    out = bat_n == bowl_n
    if out:
        outs = list(game.get(_out_key(side)) or [])
        outs.append(int(batter["id"]))
        game[_out_key(side)] = outs
        game["batter_idx"] = (_index_of(players, int(batter["id"])) + 1) % len(players)
        game["last"] = f"OUT — both picked {bat_n}"
    else:
        key = "score_a" if side == "a" else "score_b"
        game[key] = int(game.get(key) or 0) + bat_n
        game["batter_idx"] = _index_of(players, int(batter["id"]))
        game["last"] = f"+{bat_n} ({bat_n} vs {bowl_n})"

    game["balls"] = int(game.get("balls") or 0) + 1
    _advance_bowler(game)

    if int(game.get("innings") or 1) == 2 and game.get("first_innings") is not None:
        if _score(game, side) > int(game["first_innings"]):
            return "finished"

    all_out = current_batter(game) is None
    if all_out or int(game.get("balls") or 0) >= max_balls(game):
        return _end_innings(game, all_out=all_out)
    return "play"


def idle(game: dict) -> str:
    """90s with a missing pick. Returns skip, expire, or none.

    Each player may be skipped once. The next time that same player is the
    one holding up a ball, the match closes.
    """
    if both_picked(game):
        return "none"
    batter = current_batter(game)
    bowler = current_bowler(game)
    if not batter or not bowler:
        return "expire"
    picks = game.get("picks") or {}
    waiting: list[tuple[str, dict]] = []
    if str(int(batter["id"])) not in picks:
        waiting.append(("batter", batter))
    if str(int(bowler["id"])) not in picks:
        waiting.append(("bowler", bowler))
    if not waiting:
        return "none"
    stalls = {str(k): int(v) for k, v in (game.get("stalls") or {}).items()}
    if any(stalls.get(str(int(person["id"])), 0) >= 1 for _, person in waiting):
        return "expire"
    for role, person in waiting:
        stalls[str(int(person["id"]))] = stalls.get(str(int(person["id"])), 0) + 1
        if role == "batter":
            players = team(game, "a" if game.get("batting") != "b" else "b")
            game["batter_idx"] = (_index_of(players, int(person["id"])) + 1) % len(players)
        else:
            _advance_bowler(game)
    game["stalls"] = stalls
    game["picks"] = {}
    names = ", ".join(str(person.get("name") or person["id"]) for _, person in waiting)
    game["last"] = f"Skipped {names} — no pick."
    return "skip"


def winner_side(game: dict) -> str | None:
    """'a', 'b', or None for a tie."""
    score_a = int(game.get("score_a") or 0)
    score_b = int(game.get("score_b") or 0)
    if score_a > score_b:
        return "a"
    if score_b > score_a:
        return "b"
    return None


def result_phrase(game: dict) -> str:
    side = winner_side(game)
    if side is None:
        return "Tie"
    score_a = int(game.get("score_a") or 0)
    score_b = int(game.get("score_b") or 0)
    margin = abs(score_a - score_b)
    chase_score = score_b if side == "b" else score_a
    first = game.get("first_innings")
    if (
        side == "b"
        and first is not None
        and chase_score > int(first)
    ):
        left = len(team(game, "b")) - len(_outs(game, "b"))
        word = "wicket" if left == 1 else "wickets"
        return f"by {left} {word}"
    word = "run" if margin == 1 else "runs"
    return f"by {margin} {word}"


def summary(game: dict) -> str:
    return f"{int(game.get('score_a') or 0)} - {int(game.get('score_b') or 0)}"
