from __future__ import annotations


def draw_name(name: str) -> str:
    chars = [c for c in (name or "") if c.isascii() and (c.isalnum() or c in " .'_-")]
    cleaned = " ".join("".join(chars).split())
    return (cleaned[:16] if cleaned else "Player")


def record_pick(ch: dict, user_id: int, n: int) -> str:
    if n not in range(1, 7):
        return "bad"
    picks = _picks(ch)
    uid = str(int(user_id))
    if uid in picks:
        return "ready" if _both(ch) else "dup"
    picks[uid] = n
    ch["picks"] = picks
    return "ready" if _both(ch) else "wait"


def apply_ball(ch: dict) -> str:
    """Score the current locked picks. Returns play, chase, or finished."""
    picks = _picks(ch)
    pa, pb = int(picks[str(ch["a"])]), int(picks[str(ch["b"])])
    ch["picks"] = {}
    batter = ch["batter"]
    bat_n = pa if batter == ch["a"] else pb
    bowl_n = pb if batter == ch["a"] else pa
    out = bat_n == bowl_n
    played = int(ch.get("balls") or 0) + 1
    if out:
        ch["balls"] = 6
        ch["last"] = f"OUT — both picked {bat_n}"
    else:
        key = "score_a" if batter == ch["a"] else "score_b"
        ch[key] = int(ch.get(key) or 0) + bat_n
        ch["balls"] = played
        ch["last"] = f"+{bat_n} runs  (you {bat_n} · they {bowl_n})"
    ch["event"] = ch["last"]

    chase_score = int((ch["score_a"] if batter == ch["a"] else ch["score_b"]) or 0)
    innings = int(ch.get("innings") or 1)
    first = ch.get("first_innings")
    if innings == 2 and first is not None and chase_score > int(first):
        ch["waiting"] = "Chase complete."
        return "finished"

    over = out or int(ch.get("balls") or 0) >= 6
    if over:
        if innings == 1:
            total = int((ch["score_a"] if batter == ch["a"] else ch["score_b"]) or 0)
            ch["first_innings"] = total
            ch["innings"] = 2
            ch["balls"] = 0
            ch["batter"] = ch["b"] if batter == ch["a"] else ch["a"]
            ch["waiting"] = f"Need {total + 1} to win. Both pick 1–6."
            ch["event"] = f"First innings over at {total}."
            ch["last"] = ch["event"]
            return "chase"
        ch["waiting"] = "Match over."
        return "finished"
    ch["waiting"] = "Next ball — both pick 1–6."
    return "play"


def result_why(ch: dict) -> str:
    sa, sb = int(ch.get("score_a") or 0), int(ch.get("score_b") or 0)
    if sa > sb:
        return "won"
    if sb > sa:
        return "won"
    return "tie"


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


def both_picked(ch: dict) -> bool:
    picks = _picks(ch)
    return str(ch["a"]) in picks and str(ch["b"]) in picks


def _both(ch: dict) -> bool:
    return both_picked(ch)
