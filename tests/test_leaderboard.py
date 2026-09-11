import os
import tempfile
import time
import unittest

_TMP_DIR = tempfile.mkdtemp(prefix="lb_test_")
os.environ["DATA_DIR"] = _TMP_DIR

from telegram.constants import ChatType  # noqa: E402

from bot import db, leaderboard  # noqa: E402

CHAT_ID = -100987


class FakeMessage:
    def __init__(self):
        self.sent: list[tuple] = []

    async def reply_html(self, text, **kwargs):
        self.sent.append((text, kwargs))
        return self

    async def reply_text(self, text, **kwargs):
        self.sent.append((text, kwargs))
        return self


class FakeChat:
    id = CHAT_ID
    title = "Test Chat"
    type = ChatType.SUPERGROUP


class FakeUser:
    id = 1


class FakeUpdate:
    def __init__(self, args):
        self.effective_message = FakeMessage()
        self.effective_chat = FakeChat()
        self.effective_user = FakeUser()
        self._args = args


class FakeContext:
    def __init__(self, args):
        self.args = args


class FakeQuery:
    def __init__(self, data):
        self.data = data
        self.answered = False
        self.edited: tuple | None = None

    async def answer(self, *a, **k):
        self.answered = True

    async def edit_message_text(self, text, **kwargs):
        self.edited = (text, kwargs)


class FakeCallbackUpdate:
    def __init__(self, data):
        self.callback_query = FakeQuery(data)
        self.effective_chat = FakeChat()


def _seed_results(n_cricket: int, n_rps: int) -> None:
    now = time.time()
    for i in range(n_cricket):
        db.save_arena_result(
            CHAT_ID, "cricket", 1, "Alice", 2, "Bob",
            1 if i % 2 == 0 else 2, f"{i+1} - {i}",
            finished_at=now - i,
        )
    for i in range(n_rps):
        db.save_arena_result(
            CHAT_ID, "rps", 1, "Alice", 3, "Cara",
            None, "Rock vs Rock",
            finished_at=now - i,
        )


class LeaderboardTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        db.init()
        with db.cursor() as conn:
            conn.execute("DELETE FROM arena_results")

    async def test_empty_board_message(self):
        update = FakeUpdate([])
        await leaderboard.cmd_lb(update, FakeContext([]))
        text, _ = update.effective_message.sent[0]
        self.assertIn("No games finished yet", text)

    async def test_board_ranks_by_wins(self):
        _seed_results(n_cricket=6, n_rps=0)
        update = FakeUpdate([])
        await leaderboard.cmd_lb(update, FakeContext([]))
        text, _ = update.effective_message.sent[0]
        self.assertIn("Leaderboard", text)
        self.assertIn("Alice", text)
        self.assertIn("Bob", text)

    async def test_unknown_kind_rejected(self):
        update = FakeUpdate(["chess"])
        await leaderboard.cmd_lb(update, FakeContext(["chess"]))
        text, _ = update.effective_message.sent[0]
        self.assertIn("Unknown game", text)

    async def test_history_pagination_first_page(self):
        _seed_results(n_cricket=12, n_rps=0)
        update = FakeUpdate(["cricket"])
        await leaderboard.cmd_lb(update, FakeContext(["cricket"]))
        text, kwargs = update.effective_message.sent[0]
        self.assertIn("page 1/3", text)
        self.assertIn("only the last 3 days".lower(), text.lower())
        markup = kwargs["reply_markup"]
        self.assertIsNotNone(markup)

    async def test_history_pagination_callback_advances_page(self):
        _seed_results(n_cricket=12, n_rps=0)
        update = FakeCallbackUpdate("lb:cr:1")
        await leaderboard.on_lb_callback(update, FakeContext([]))
        text, _ = update.callback_query.edited
        self.assertIn("page 2/3", text)

    async def test_history_page_out_of_range_clamps(self):
        _seed_results(n_cricket=6, n_rps=0)
        update = FakeCallbackUpdate("lb:cr:99")
        await leaderboard.on_lb_callback(update, FakeContext([]))
        text, _ = update.callback_query.edited
        self.assertIn("page 2/2", text)  # only 2 pages of 5, clamped to last

    async def test_rps_history_isolated_from_cricket(self):
        _seed_results(n_cricket=3, n_rps=2)
        update = FakeUpdate(["rps"])
        await leaderboard.cmd_lb(update, FakeContext(["rps"]))
        text, _ = update.effective_message.sent[0]
        self.assertIn("Cara", text)
        self.assertNotIn("Bob", text)


if __name__ == "__main__":
    unittest.main()
