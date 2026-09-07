import unittest

from bot.cricket import apply_ball, draw_name, record_pick, result_why


def match(a=1, b=2) -> dict:
    return {
        "a": a,
        "b": b,
        "picks": {},
        "score_a": 0,
        "score_b": 0,
        "balls": 0,
        "innings": 1,
        "batter": a,
        "first_innings": None,
    }


class CricketEngineTest(unittest.TestCase):
    def test_draw_name_strips_symbols(self):
        self.assertEqual(draw_name("LUFFY ™"), "LUFFY")
        self.assertEqual(draw_name(""), "Player")

    def test_card_renders(self):
        try:
            from bot.gameui import render_cricket
        except ModuleNotFoundError:
            self.skipTest("Pillow is not installed in this Python")
        png = render_cricket(
            name_a="LUFFY ™",
            name_b="King",
            score_a=5,
            score_b=0,
            batter_is_a=False,
            innings=2,
            ball=1,
            target=5,
            last="First innings over at 5.",
            waiting="Need 6 to win. Both pick 1–6.",
        )
        self.assertGreater(len(png), 1000)
        self.assertTrue(png.startswith(b"\x89PNG"))

    def test_lock_then_bowl(self):
        ch = match()
        self.assertEqual(record_pick(ch, 1, 2), "wait")
        self.assertEqual(record_pick(ch, 1, 5), "dup")
        self.assertEqual(record_pick(ch, 2, 5), "ready")
        self.assertEqual(apply_ball(ch), "play")
        self.assertEqual(ch["score_a"], 2)
        self.assertEqual(ch["balls"], 1)
        self.assertEqual(ch["picks"], {})
        self.assertIn("+2", ch["last"])

    def test_same_number_is_out_for_zero(self):
        ch = match()
        record_pick(ch, 1, 4)
        record_pick(ch, 2, 4)
        self.assertEqual(apply_ball(ch), "chase")
        self.assertEqual(ch["score_a"], 0)
        self.assertEqual(ch["first_innings"], 0)
        self.assertEqual(ch["innings"], 2)
        self.assertEqual(ch["batter"], 2)
        self.assertEqual(ch["balls"], 0)
        self.assertIn("Need 1", ch["waiting"])

    def test_out_on_second_ball_sets_chase_target(self):
        ch = match()
        record_pick(ch, 1, 5)
        record_pick(ch, 2, 6)
        self.assertEqual(apply_ball(ch), "play")
        record_pick(ch, 1, 4)
        record_pick(ch, 2, 4)
        self.assertEqual(apply_ball(ch), "chase")
        self.assertEqual(ch["score_a"], 5)
        self.assertEqual(ch["first_innings"], 5)
        self.assertEqual(ch["innings"], 2)
        self.assertEqual(ch["batter"], 2)
        self.assertIn("Need 6", ch["waiting"])

    def test_chase_ends_when_target_passed(self):
        ch = match()
        ch["innings"] = 2
        ch["batter"] = 2
        ch["first_innings"] = 5
        record_pick(ch, 1, 1)
        record_pick(ch, 2, 6)
        self.assertEqual(apply_ball(ch), "finished")
        self.assertEqual(ch["score_b"], 6)
        self.assertEqual(result_why(ch), "won")

    def test_six_legal_balls_then_chase(self):
        ch = match()
        for _ in range(6):
            record_pick(ch, 1, 1)
            record_pick(ch, 2, 2)
            phase = apply_ball(ch)
        self.assertEqual(phase, "chase")
        self.assertEqual(ch["score_a"], 6)
        self.assertEqual(ch["first_innings"], 6)

    def test_chase_tie(self):
        ch = match()
        ch["score_a"] = 2
        ch["innings"] = 2
        ch["batter"] = 2
        ch["first_innings"] = 2
        ch["score_b"] = 1
        ch["balls"] = 5
        record_pick(ch, 1, 2)
        record_pick(ch, 2, 1)
        self.assertEqual(apply_ball(ch), "finished")
        self.assertEqual(ch["score_b"], 2)
        self.assertEqual(result_why(ch), "tie")


if __name__ == "__main__":
    unittest.main()
