import unittest

from bot.board import render_board


class TestBoard(unittest.TestCase):
    def test_empty_board_points_at_the_commands(self):
        text = render_board([])
        self.assertIn("/cricket", text)
        self.assertIn("/tambola", text)

    def test_counts_wins_lines_and_full_houses(self):
        rows = [
            {"user_id": 1, "user_name": "Ada", "kind": "cricket", "result": "win", "n": 2, "last_at": 10},
            {"user_id": 1, "user_name": "Ada", "kind": "tambola", "result": "full", "n": 1, "last_at": 12},
            {"user_id": 1, "user_name": "Ada", "kind": "tambola", "result": "line", "n": 3, "last_at": 11},
            {"user_id": 2, "user_name": "Ben", "kind": "cricket", "result": "win", "n": 5, "last_at": 9},
            {"user_id": 2, "user_name": "Ben <b>", "kind": "tambola", "result": "line", "n": 1, "last_at": 20},
        ]
        text = render_board(rows)
        self.assertIn("1. Ben &lt;b&gt; — 5 wins", text)
        self.assertIn("2. Ada — 2 wins", text)
        self.assertIn("1 full house, 3 lines", text)
        self.assertIn("Ben &lt;b&gt; — 1 line", text)


class TestDb(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path

        import bot.db as db

        self.db = db
        self.tmp = tempfile.TemporaryDirectory()
        self.old = db.DB_PATH
        db.DB_PATH = Path(self.tmp.name) / "t.db"
        db.init()

    def tearDown(self):
        self.db.DB_PATH = self.old
        self.tmp.cleanup()

    def test_one_live_session_round_trip_and_results(self):
        db = self.db
        db.create_session("abc", -100, "tambola", 7, {"players": {}}, deadline=10)
        loaded = db.load_session("abc")
        self.assertEqual(loaded["status"], "lobby")
        self.assertEqual(loaded["payload"], {"players": {}})
        self.assertEqual(db.active_session(-100, "tambola")["id"], "abc")
        self.assertIsNone(db.active_session(-100, "cricket"))

        db.save_session("abc", {"started": True}, status="live", deadline=1)
        due = db.due_sessions(5)
        self.assertEqual([row["id"] for row in due], ["abc"])

        db.add_results([
            {
                "chat_id": -100,
                "kind": "tambola",
                "user_id": 7,
                "user_name": "Ada",
                "result": "line",
            }
        ])
        counts = db.board_counts(-100)
        self.assertEqual(counts[0]["result"], "line")
        self.assertEqual(counts[0]["n"], 1)

        db.save_session("abc", {"started": True}, status="finished", deadline=1)
        self.assertIsNone(db.active_session(-100, "tambola"))
        self.assertEqual(db.due_sessions(5), [])


if __name__ == "__main__":
    unittest.main()
