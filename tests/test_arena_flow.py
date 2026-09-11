"""End-to-end tests for the arena Telegram wiring using minimal fake
Telegram objects — no real network, no real bot token. Exercises the parts
most likely to have concurrency/state bugs: accept/decline authorization,
duplicate picks, full match completion, idle sweep, and result purge.
"""
import asyncio
import os
import tempfile
import time
import unittest

# Must happen before any `bot.*` import — DB_PATH is computed at import time.
_TMP_DIR = tempfile.mkdtemp(prefix="arena_flow_test_")
os.environ["DATA_DIR"] = _TMP_DIR

from bot import arena, db  # noqa: E402

CHAT_ID = -100123
A_ID, B_ID = 111, 222


class FakeBot:
    def __init__(self):
        self.edits: list[dict] = []

    async def edit_message_text(self, **kwargs):
        self.edits.append(kwargs)


class FakeContext:
    def __init__(self):
        self.bot = FakeBot()


class FakeUser:
    def __init__(self, user_id: int, name: str = "P"):
        self.id = user_id
        self.first_name = name
        self.full_name = name
        self.is_bot = False


class FakeQuery:
    def __init__(self, data: str):
        self.data = data
        self.answers: list[tuple] = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))


class FakeUpdate:
    def __init__(self, query: FakeQuery, user: FakeUser):
        self.callback_query = query
        self.effective_user = user


def _make_match(kind: str, deadline: float | None = None) -> str:
    match_id = "m" + str(int(time.time() * 1000) % 10**8)
    if kind == arena.KIND_CRICKET:
        payload = arena.logic.cricket_new(A_ID, B_ID, batter=A_ID)
    else:
        payload = arena.logic.rps_new(A_ID, B_ID)
    payload["a_name"] = "Alice"
    payload["b_name"] = "Bob"
    db.create_arena_match(
        match_id, CHAT_ID, kind, A_ID, B_ID, payload,
        deadline=deadline if deadline is not None else time.time() + 300,
    )
    return match_id


def _tap(match_id: str, code: str, action: str, user_id: int, extra: str | None = None):
    data = f"g:{code}:{action}:{match_id}" + (f":{extra}" if extra is not None else "")
    query = FakeQuery(data)
    user = FakeUser(user_id, "Alice" if user_id == A_ID else "Bob")
    update = FakeUpdate(query, user)
    context = FakeContext()
    return update, query, context


class ArenaFlowTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        db.init()
        with db.cursor() as conn:
            conn.execute("DELETE FROM arena_matches")
            conn.execute("DELETE FROM arena_results")
        arena._locks.clear()

    async def test_only_challenged_player_can_accept(self):
        mid = _make_match(arena.KIND_CRICKET)
        update, query, context = _tap(mid, "cr", "ok", A_ID)  # challenger tries to accept own challenge
        await arena.on_arena_callback(update, context)
        self.assertTrue(query.answers[0][1])  # show_alert True
        match = db.load_arena_match(mid)
        self.assertEqual(match["status"], "pending")

    async def test_stranger_cannot_tap(self):
        mid = _make_match(arena.KIND_CRICKET)
        update, query, context = _tap(mid, "cr", "ok", 999999)
        await arena.on_arena_callback(update, context)
        self.assertIn("not your match", query.answers[0][0])

    async def test_decline_removes_match(self):
        mid = _make_match(arena.KIND_CRICKET)
        update, query, context = _tap(mid, "cr", "no", B_ID)
        await arena.on_arena_callback(update, context)
        self.assertIsNone(db.load_arena_match(mid))

    async def test_duplicate_pick_rejected(self):
        mid = _make_match(arena.KIND_CRICKET)
        update, query, context = _tap(mid, "cr", "ok", B_ID)
        await arena.on_arena_callback(update, context)  # accept -> live

        update, query, context = _tap(mid, "cr", "p", A_ID, "3")
        await arena.on_arena_callback(update, context)  # A picks 3
        update, query, context = _tap(mid, "cr", "p", A_ID, "4")
        await arena.on_arena_callback(update, context)  # A tries again
        self.assertIn("already locked", query.answers[0][0])

    async def test_full_cricket_match_completes_and_records_result(self):
        mid = _make_match(arena.KIND_CRICKET)
        update, query, context = _tap(mid, "cr", "ok", B_ID)
        await arena.on_arena_callback(update, context)
        match = db.load_arena_match(mid)
        batter = match["payload"]["batter"]
        bowler = B_ID if batter == A_ID else A_ID

        # Force an immediate out: both pick the same number for 6 straight
        # balls at most — but "out" ends an innings on the first match, so
        # play deterministically: batter picks 4, bowler picks 4 => out.
        for _ in range(2):  # innings 1 (out on ball 1), innings 2 (out on ball 1)
            update, query, context = _tap(mid, "cr", "p", batter, "4")
            await arena.on_arena_callback(update, context)
            update, query, context = _tap(mid, "cr", "p", bowler, "4")
            await arena.on_arena_callback(update, context)
            match = db.load_arena_match(mid)
            if match is None:
                break
            batter, bowler = match["payload"]["batter"], (
                B_ID if match["payload"]["batter"] == A_ID else A_ID
            )

        self.assertIsNone(db.load_arena_match(mid))
        results = db.list_arena_results(CHAT_ID, arena.KIND_CRICKET, since=0)
        self.assertEqual(len(results), 1)
        # Both sides were out for 0 -> tie
        self.assertIsNone(results[0]["winner_id"])
        self.assertEqual(results[0]["summary"], "0 - 0")
        self.assertNotIn(mid, arena._locks)  # lock cleaned up after finish

    async def test_full_rps_match_completes(self):
        mid = _make_match(arena.KIND_RPS)
        update, query, context = _tap(mid, "rp", "ok", B_ID)
        await arena.on_arena_callback(update, context)

        update, query, context = _tap(mid, "rp", "p", A_ID, "r")  # rock
        await arena.on_arena_callback(update, context)
        update, query, context = _tap(mid, "rp", "p", B_ID, "s")  # scissors -> A wins
        await arena.on_arena_callback(update, context)

        self.assertIsNone(db.load_arena_match(mid))
        results = db.list_arena_results(CHAT_ID, arena.KIND_RPS, since=0)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["winner_id"], A_ID)

    async def test_idle_sweep_closes_stale_match_and_edits_message(self):
        mid = _make_match(arena.KIND_CRICKET, deadline=time.time() - 10)
        db.set_arena_match_message(mid, 777)
        context = FakeContext()
        closed = await arena.sweep_idle_matches_once(context)
        self.assertEqual(closed, 1)
        self.assertIsNone(db.load_arena_match(mid))
        self.assertEqual(len(context.bot.edits), 1)
        self.assertEqual(context.bot.edits[0]["chat_id"], CHAT_ID)
        self.assertEqual(context.bot.edits[0]["message_id"], 777)
        self.assertIn("expired", context.bot.edits[0]["text"])

    async def test_active_sweep_does_not_touch_live_recent_match(self):
        mid = _make_match(arena.KIND_CRICKET, deadline=time.time() + 300)
        closed = await arena.sweep_idle_matches_once(FakeContext())
        self.assertEqual(closed, 0)
        self.assertIsNotNone(db.load_arena_match(mid))

    def test_concurrency_cap_counts_active_matches(self):
        for _ in range(3):
            _make_match(arena.KIND_CRICKET)
        self.assertEqual(db.count_active_arena_matches(CHAT_ID, arena.KIND_CRICKET), 3)

    async def test_names_with_html_special_chars_do_not_break_rendering(self):
        # A display name containing raw HTML-significant characters must not
        # break parse_mode="HTML" edits — everything shown outside a
        # <a href=...> mention must be escaped.
        match_id = "mhtml1"
        payload = arena.logic.cricket_new(A_ID, B_ID, batter=A_ID)
        payload["a_name"] = "A & <b>Ruby</b>"
        payload["b_name"] = "Bob"
        db.create_arena_match(match_id, CHAT_ID, arena.KIND_CRICKET, A_ID, B_ID, payload, deadline=time.time() + 300)
        db.set_arena_match_message(match_id, 999)

        update, query, context = _tap(match_id, "cr", "ok", B_ID)
        await arena.on_arena_callback(update, context)
        text = context.bot.edits[0]["text"]
        self.assertNotIn("A & <b>Ruby</b>", text)  # raw markup must not survive
        self.assertIn("A &amp; &lt;b&gt;Ruby&lt;/b&gt;", text)

    async def test_concurrent_challenge_creation_respects_cap(self):
        # Simulates several /cricket calls firing at the same instant. Each
        # must serialize on the per-(chat, kind) lock so the cap is never
        # exceeded even under concurrent access.
        async def make_one():
            async with arena._start_lock_for(CHAT_ID, arena.KIND_CRICKET):
                if db.count_active_arena_matches(CHAT_ID, arena.KIND_CRICKET) >= arena.MAX_CONCURRENT:
                    return False
                _make_match(arena.KIND_CRICKET)
                return True

        results = await asyncio.gather(*(make_one() for _ in range(10)))
        self.assertEqual(sum(1 for ok in results if ok), arena.MAX_CONCURRENT)
        self.assertEqual(db.count_active_arena_matches(CHAT_ID, arena.KIND_CRICKET), arena.MAX_CONCURRENT)

    def test_purge_removes_only_old_results(self):
        now = time.time()
        db.save_arena_result(
            CHAT_ID, "cricket", A_ID, "Alice", B_ID, "Bob", A_ID, "5 - 2",
            finished_at=now - 4 * 86400,
        )
        db.save_arena_result(
            CHAT_ID, "cricket", A_ID, "Alice", B_ID, "Bob", B_ID, "1 - 9",
            finished_at=now - 1 * 86400,
        )
        removed = arena.purge_old_results_once()
        self.assertEqual(removed, 1)
        remaining = db.list_arena_results(CHAT_ID, "cricket", since=0)
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["summary"], "1 - 9")


if __name__ == "__main__":
    unittest.main()
