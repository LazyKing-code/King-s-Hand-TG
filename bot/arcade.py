from __future__ import annotations

import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, User

from bot.gameui import render_four, render_penalty, render_result, render_vault, send_or_edit_card
from bot.moderation import format_user, mention

_COLS = 7
_ROWS = 6
_EMPTY = "0" * (_COLS * _ROWS)
_SIDES = {"L": "Left", "C": "Centre", "R": "Right"}


def four_markup(cid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(str(n), callback_data=f"gm:f:{cid}:{n}")
                for n in range(1, 8)
            ]
        ]
    )


def penalty_markup(cid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("⬅  Left", callback_data=f"gm:p:{cid}:L"),
                InlineKeyboardButton("⬆  Centre", callback_data=f"gm:p:{cid}:C"),
                InlineKeyboardButton("Right  ➡", callback_data=f"gm:p:{cid}:R"),
            ]
        ]
    )


def vault_markup(cid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🤝  Share", callback_data=f"gm:v:{cid}:share"),
                InlineKeyboardButton("👑  Take", callback_data=f"gm:v:{cid}:take"),
            ]
        ]
    )


def _drop(board: str, col: int, piece: str) -> str | None:
    cells = list((board + _EMPTY)[:42])
    for row in range(_ROWS - 1, -1, -1):
        i = row * _COLS + col
        if cells[i] == "0":
            cells[i] = piece
            return "".join(cells)
    return None


def _won(board: str, piece: str) -> bool:
    cells = (board + _EMPTY)[:42]

    def at(r: int, c: int) -> str:
        if 0 <= r < _ROWS and 0 <= c < _COLS:
            return cells[r * _COLS + c]
        return ""

    for r in range(_ROWS):
        for c in range(_COLS):
            if at(r, c) != piece:
                continue
            for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
                if all(at(r + dr * k, c + dc * k) == piece for k in range(4)):
                    return True
    return False


def _full(board: str) -> bool:
    return "0" not in (board + _EMPTY)[:42]


async def start_four(query, context, cid: str, ch: dict) -> None:
    from bot.games import _short_name

    ch["board"] = _EMPTY
    ch["turn"] = ch["a"]
    ch["event"] = "Challenger drops first. Four in a line wins."
    await query.answer("Four in a row. You drop second.")
    await _show_four(query, context, cid, ch)


async def _show_four(query, context, cid: str, ch: dict) -> None:
    from bot.games import _short_name

    name_a = await _short_name(context, ch["chat_id"], ch["a"])
    name_b = await _short_name(context, ch["chat_id"], ch["b"])
    turn_a = ch["turn"] == ch["a"]
    who = name_a if turn_a else name_b
    png = render_four(
        name_a=name_a,
        name_b=name_b,
        board=ch.get("board") or _EMPTY,
        turn_is_a=turn_a,
        event=ch.get("event") or f"{who} to drop.",
    )
    a_m = await format_user(context, ch["chat_id"], ch["a"])
    b_m = await format_user(context, ch["chat_id"], ch["b"])
    bat = a_m if turn_a else b_m
    await send_or_edit_card(
        query,
        png,
        f"<b>To drop:</b> {bat}\n{a_m} gold  ·  {b_m} teal",
        four_markup(cid),
    )


async def on_four_drop(query, context, cid: str, ch: dict, user: User, col: int) -> None:
    from bot.games import _award, _challenges, _short_name

    if user.id not in (ch["a"], ch["b"]) or col not in range(7):
        await query.answer("This is not your match.", show_alert=True)
        return
    if ch["status"] != "live":
        await query.answer("Accept first.", show_alert=True)
        return
    if user.id != ch["turn"]:
        await query.answer("Wait for your turn.", show_alert=True)
        return
    piece = "1" if user.id == ch["a"] else "2"
    nxt = _drop(ch.get("board") or _EMPTY, col, piece)
    if nxt is None:
        await query.answer("That column is full.", show_alert=True)
        return
    ch["board"] = nxt
    ch["expires"] = time.time() + 300
    you = await _short_name(context, ch["chat_id"], user.id)
    if _won(nxt, piece):
        await _finish_duel(
            query,
            context,
            cid,
            ch,
            winner=user.id,
            title="Four in a row",
            event=f"{you} connected four.",
            win_pts=45,
            lose_pts=12,
            kind="four",
        )
        return
    if _full(nxt):
        await _finish_duel(
            query,
            context,
            cid,
            ch,
            winner=None,
            title="Four in a row",
            event="Board full. Draw.",
            win_pts=20,
            lose_pts=20,
            kind="four",
        )
        return
    ch["turn"] = ch["b"] if user.id == ch["a"] else ch["a"]
    other = await _short_name(context, ch["chat_id"], ch["turn"])
    ch["event"] = f"{you} dropped in column {col + 1}. {other} to play."
    await query.answer(f"Column {col + 1}.")
    await _show_four(query, context, cid, ch)


async def start_penalty(query, context, cid: str, ch: dict) -> None:
    ch["goals_a"] = 0
    ch["goals_b"] = 0
    ch["kicks_a"] = 0
    ch["kicks_b"] = 0
    ch["shooter"] = ch["a"]
    ch["picks"] = {}
    ch["event"] = "Challenger shoots first. Pick a side — it locks."
    ch["waiting"] = "Shooter and keeper both tap. Revealed together."
    await query.answer("You keep first. Then you shoot.")
    await _show_penalty(query, context, cid, ch)


async def _show_penalty(query, context, cid: str, ch: dict, markup=True) -> None:
    from bot.games import _short_name

    name_a = await _short_name(context, ch["chat_id"], ch["a"])
    name_b = await _short_name(context, ch["chat_id"], ch["b"])
    kick = ch["kicks_a"] + ch["kicks_b"] + 1
    png = render_penalty(
        name_a=name_a,
        name_b=name_b,
        goals_a=ch["goals_a"],
        goals_b=ch["goals_b"],
        shooter_is_a=ch["shooter"] == ch["a"],
        kick=kick,
        event=ch.get("event") or "Pick Left, Centre, or Right.",
        waiting=ch.get("waiting") or "Both lock a side.",
    )
    a_m = await format_user(context, ch["chat_id"], ch["a"])
    b_m = await format_user(context, ch["chat_id"], ch["b"])
    shoot = a_m if ch["shooter"] == ch["a"] else b_m
    cap = f"<b>Shooting:</b> {shoot}\n{a_m} {ch['goals_a']}  ·  {b_m} {ch['goals_b']}"
    await send_or_edit_card(query, png, cap, penalty_markup(cid) if markup else None)


async def on_penalty_pick(query, context, cid: str, ch: dict, user: User, side: str) -> None:
    from bot.games import _short_name

    if user.id not in (ch["a"], ch["b"]) or side not in _SIDES:
        await query.answer("This is not your match.", show_alert=True)
        return
    if ch["status"] != "live":
        await query.answer("Accept first.", show_alert=True)
        return
    if str(user.id) in ch["picks"]:
        await query.answer("Already locked for this kick.", show_alert=True)
        return
    ch["picks"][str(user.id)] = side
    ch["expires"] = time.time() + 300
    if len(ch["picks"]) < 2:
        you = await _short_name(context, ch["chat_id"], user.id)
        ch["waiting"] = f"{you} locked. Waiting for the other side."
        await query.answer("Locked. Waiting.")
        await _show_penalty(query, context, cid, ch)
        return

    pa, pb = ch["picks"][str(ch["a"])], ch["picks"][str(ch["b"])]
    ch["picks"] = {}
    shooter = ch["shooter"]
    shot = pa if shooter == ch["a"] else pb
    keep = pb if shooter == ch["a"] else pa
    goal = shot != keep
    if goal:
        if shooter == ch["a"]:
            ch["goals_a"] += 1
        else:
            ch["goals_b"] += 1
        ch["event"] = f"GOAL  ·  shot {_SIDES[shot]}  ·  keep {_SIDES[keep]}"
    else:
        ch["event"] = f"SAVE  ·  both went {_SIDES[shot]}"
    if shooter == ch["a"]:
        ch["kicks_a"] += 1
    else:
        ch["kicks_b"] += 1

    ga, gb = ch["goals_a"], ch["goals_b"]
    ka, kb = ch["kicks_a"], ch["kicks_b"]
    winner = _penalty_winner(ga, gb, ka, kb)
    if winner == "a":
        name = await _short_name(context, ch["chat_id"], ch["a"])
        await _finish_duel(
            query, context, cid, ch, winner=ch["a"], title="Penalty duel",
            event=f"{ch['event']}. {name} takes it {ga}–{gb}.",
            win_pts=42, lose_pts=12, kind="penalty",
        )
        return
    if winner == "b":
        name = await _short_name(context, ch["chat_id"], ch["b"])
        await _finish_duel(
            query, context, cid, ch, winner=ch["b"], title="Penalty duel",
            event=f"{ch['event']}. {name} takes it {gb}–{ga}.",
            win_pts=42, lose_pts=12, kind="penalty",
        )
        return
    if ka >= 10 and kb >= 10 and ga == gb:
        await _finish_duel(
            query, context, cid, ch, winner=None, title="Penalty duel",
            event=f"{ch['event']}. Still level after extra kicks.",
            win_pts=20, lose_pts=20, kind="penalty",
        )
        return

    ch["shooter"] = ch["b"] if shooter == ch["a"] else ch["a"]
    nxt = await _short_name(context, ch["chat_id"], ch["shooter"])
    ch["waiting"] = f"{nxt} shoots next. Both pick again."
    await query.answer(ch["event"])
    await _show_penalty(query, context, cid, ch)


def _penalty_winner(ga: int, gb: int, ka: int, kb: int) -> str | None:
    if ka < 5 or kb < 5:
        rest_a = 5 - ka
        rest_b = 5 - kb
        if ga > gb + rest_b:
            return "a"
        if gb > ga + rest_a:
            return "b"
        return None
    if ka == kb and ga != gb:
        return "a" if ga > gb else "b"
    return None


async def start_vault(query, context, cid: str, ch: dict) -> None:
    from bot.games import _short_name

    ch["picks"] = {}
    a_n = await _short_name(context, ch["chat_id"], ch["a"])
    b_n = await _short_name(context, ch["chat_id"], ch["b"])
    png = render_vault(
        name_a=a_n,
        name_b=b_n,
        line="One pot. Share splits it. Take tries to keep it all.",
        waiting="If both Take, the pot is empty. Picks lock.",
    )
    await query.answer("Share or Take. You cannot change it.")
    await send_or_edit_card(
        query,
        png,
        "The vault is open. Lock Share or Take.",
        vault_markup(cid),
    )


async def on_vault_pick(query, context, cid: str, ch: dict, user: User, move: str) -> None:
    if user.id not in (ch["a"], ch["b"]) or move not in {"share", "take"}:
        await query.answer("This is not your match.", show_alert=True)
        return
    if str(user.id) in ch["picks"]:
        await query.answer("Already locked.", show_alert=True)
        return
    ch["picks"][str(user.id)] = move
    ch["expires"] = time.time() + 300
    if len(ch["picks"]) < 2:
        await query.answer("Locked. Waiting for the other call.")
        return
    pa, pb = ch["picks"][str(ch["a"])], ch["picks"][str(ch["b"])]
    labels = {"share": "Share", "take": "Take"}
    detail = f"{labels[pa]}  vs  {labels[pb]}"
    if pa == "share" and pb == "share":
        await _finish_duel(
            query, context, cid, ch, winner=None, title="The vault",
            event=f"Both shared. {detail}",
            win_pts=24, lose_pts=24, kind="vault",
        )
        return
    if pa == "take" and pb == "take":
        await _finish_duel(
            query, context, cid, ch, winner=None, title="The vault",
            event=f"Both took. The pot is gone. {detail}",
            win_pts=4, lose_pts=4, kind="vault",
        )
        return
    winner = ch["a"] if pa == "take" else ch["b"]
    from bot.games import _short_name

    name = await _short_name(context, ch["chat_id"], winner)
    await _finish_duel(
        query, context, cid, ch, winner=winner, title="The vault",
        event=f"{name} took the pot. {detail}",
        win_pts=48, lose_pts=8, kind="vault",
    )


async def _finish_duel(
    query,
    context,
    cid: str,
    ch: dict,
    *,
    winner: int | None,
    title: str,
    event: str,
    win_pts: int,
    lose_pts: int,
    kind: str,
) -> None:
    from bot.games import _award, _challenges, _short_name

    try:
        await query.answer()
    except Exception:
        pass
    chat_id = ch["chat_id"]
    a, b = ch["a"], ch["b"]
    a_m = await format_user(context, chat_id, a)
    b_m = await format_user(context, chat_id, b)
    name_a = await _short_name(context, chat_id, a)
    name_b = await _short_name(context, chat_id, b)
    if winner == a:
        _award(chat_id, a, win_pts, win=True, kind=kind)
        _award(chat_id, b, lose_pts, win=False, kind=kind)
        headline = f"{name_a} wins"
        cap_w = f"{a_m} wins"
    elif winner == b:
        _award(chat_id, b, win_pts, win=True, kind=kind)
        _award(chat_id, a, lose_pts, win=False, kind=kind)
        headline = f"{name_b} wins"
        cap_w = f"{b_m} wins"
    else:
        _award(chat_id, a, win_pts, win=None, kind=kind)
        _award(chat_id, b, lose_pts, win=None, kind=kind)
        headline = "Draw"
        cap_w = "Draw"
    png = render_result(title=title, headline=headline, detail=event)
    _challenges.pop(cid, None)
    await send_or_edit_card(
        query,
        png,
        f"{event}\n\n{a_m} vs {b_m}\n{cap_w}",
        None,
    )
