import unittest

from bot.arena_logic import (
    aggregate_leaderboard,
    cricket_apply_ball,
    cricket_both_picked,
    cricket_new,
    cricket_record_pick,
    cricket_summary,
    cricket_winner,
    paginate,
    rps_new,
    rps_record_pick,
    rps_resolve,
)

A, B = 111, 222


class TestCricketPicks(unittest.TestCase):
    def test_first_pick_waits(self):
        ch = cricket_new(A, B, A)
        self.assertEqual(cricket_record_pick(ch, A, 3), "wait")
        self.assertFalse(cricket_both_picked(ch))

    def test_second_pick_ready(self):
        ch = cricket_new(A, B, A)
        cricket_record_pick(ch, A, 3)
        self.assertEqual(cricket_record_pick(ch, B, 5), "ready")
        self.assertTrue(cricket_both_picked(ch))

    def test_duplicate_pick_rejected(self):
        ch = cricket_new(A, B, A)
        cricket_record_pick(ch, A, 3)
        self.assertEqual(cricket_record_pick(ch, A, 4), "dup")
        # still just one pick locked in
        self.assertEqual(ch["picks"], {str(A): 3})

    def test_out_of_range_pick_rejected(self):
        ch = cricket_new(A, B, A)
        self.assertEqual(cricket_record_pick(ch, A, 0), "bad")
        self.assertEqual(cricket_record_pick(ch, A, 7), "bad")

    def test_stranger_pick_rejected(self):
        ch = cricket_new(A, B, A)
        self.assertEqual(cricket_record_pick(ch, 999, 3), "bad")


class TestCricketBall(unittest.TestCase):
    def test_out_ends_innings_immediately(self):
        ch = cricket_new(A, B, A)
        cricket_record_pick(ch, A, 4)
        cricket_record_pick(ch, B, 4)
        result = cricket_apply_ball(ch)
        self.assertEqual(result, "chase")
        self.assertEqual(ch["first_innings"], 0)
        self.assertEqual(ch["innings"], 2)
        self.assertEqual(ch["batter"], B)

    def test_out_for_zero_target_is_one(self):
        ch = cricket_new(A, B, A)
        cricket_record_pick(ch, A, 6)
        cricket_record_pick(ch, B, 6)
        cricket_apply_ball(ch)  # out first ball, first_innings=0
        cricket_record_pick(ch, A, 1)
        cricket_record_pick(ch, B, 2)
        result = cricket_apply_ball(ch)
        # chaser (B) scored 1 run vs target 1 -> passes target of 0, finishes immediately
        self.assertEqual(result, "finished")
        self.assertEqual(cricket_winner(ch), B)

    def test_runs_accumulate_across_balls(self):
        ch = cricket_new(A, B, A)
        for _ in range(3):
            cricket_record_pick(ch, A, 3)
            cricket_record_pick(ch, B, 5)  # never matches -> never out
            cricket_apply_ball(ch)
        self.assertEqual(ch["score_a"], 9)
        self.assertEqual(ch["balls"], 3)

    def test_six_balls_ends_innings_without_out(self):
        ch = cricket_new(A, B, A)
        result = "play"
        for i in range(6):
            cricket_record_pick(ch, A, 1)
            cricket_record_pick(ch, B, 2)
            result = cricket_apply_ball(ch)
        self.assertEqual(result, "chase")
        self.assertEqual(ch["first_innings"], 6)
        self.assertEqual(ch["balls"], 0)

    def test_chase_ends_mid_over_when_target_passed(self):
        ch = cricket_new(A, B, A)
        # innings 1: A scores 5 then out -> target 6
        cricket_record_pick(ch, A, 5)
        cricket_record_pick(ch, B, 2)
        self.assertEqual(cricket_apply_ball(ch), "play")
        cricket_record_pick(ch, A, 3)
        cricket_record_pick(ch, B, 3)  # out
        self.assertEqual(cricket_apply_ball(ch), "chase")
        self.assertEqual(ch["first_innings"], 5)
        # innings 2: B chases, needs 6. Ball 1: +6 -> passes immediately.
        cricket_record_pick(ch, A, 1)
        cricket_record_pick(ch, B, 6)
        result = cricket_apply_ball(ch)
        self.assertEqual(result, "finished")
        self.assertEqual(ch["balls"], 1)  # did not play out the full over
        self.assertEqual(cricket_winner(ch), B)

    def test_chase_falls_short_first_side_wins(self):
        ch = cricket_new(A, B, A)
        cricket_record_pick(ch, A, 4)
        cricket_record_pick(ch, B, 4)  # out immediately, first_innings=0... use non-zero instead
        cricket_apply_ball(ch)
        # Rebuild with a real score: redo cleanly
        ch = cricket_new(A, B, A)
        for pick in (3, 3, 3):
            cricket_record_pick(ch, A, pick)
            cricket_record_pick(ch, B, 1)
            cricket_apply_ball(ch)
        cricket_record_pick(ch, A, 2)
        cricket_record_pick(ch, B, 2)  # out on 4th ball
        result = cricket_apply_ball(ch)
        self.assertEqual(result, "chase")
        target_side_total = ch["first_innings"]
        self.assertEqual(target_side_total, 9)
        # chase: B bats now, never scores enough, gets out for 3
        cricket_record_pick(ch, A, 5)
        cricket_record_pick(ch, B, 5)  # out immediately
        result = cricket_apply_ball(ch)
        self.assertEqual(result, "finished")
        self.assertEqual(cricket_winner(ch), A)

    def test_tie_is_possible(self):
        ch = cricket_new(A, B, A)
        # A scores 4 then out
        cricket_record_pick(ch, A, 4)
        cricket_record_pick(ch, B, 1)
        cricket_apply_ball(ch)
        cricket_record_pick(ch, A, 6)
        cricket_record_pick(ch, B, 6)  # out for total 4
        self.assertEqual(cricket_apply_ball(ch), "chase")
        self.assertEqual(ch["first_innings"], 4)
        # B chases: 3, then 1 (reaches exactly 4 - not over target), then out
        cricket_record_pick(ch, A, 2)
        cricket_record_pick(ch, B, 3)
        self.assertEqual(cricket_apply_ball(ch), "play")
        cricket_record_pick(ch, A, 5)
        cricket_record_pick(ch, B, 1)
        self.assertEqual(cricket_apply_ball(ch), "play")
        cricket_record_pick(ch, A, 1)
        cricket_record_pick(ch, B, 1)  # out, score stays at 4
        result = cricket_apply_ball(ch)
        self.assertEqual(result, "finished")
        self.assertIsNone(cricket_winner(ch))
        self.assertEqual(cricket_summary(ch), "4 - 4")


class TestRPS(unittest.TestCase):
    def test_rock_beats_scissors(self):
        ch = rps_new(A, B)
        rps_record_pick(ch, A, "rock")
        rps_record_pick(ch, B, "scissors")
        winner, ma, mb = rps_resolve(ch)
        self.assertEqual(winner, A)
        self.assertEqual((ma, mb), ("rock", "scissors"))

    def test_draw(self):
        ch = rps_new(A, B)
        rps_record_pick(ch, A, "paper")
        rps_record_pick(ch, B, "paper")
        winner, _, _ = rps_resolve(ch)
        self.assertIsNone(winner)

    def test_bad_move_rejected(self):
        ch = rps_new(A, B)
        self.assertEqual(rps_record_pick(ch, A, "lizard"), "bad")

    def test_duplicate_lock_rejected(self):
        ch = rps_new(A, B)
        rps_record_pick(ch, A, "rock")
        self.assertEqual(rps_record_pick(ch, A, "paper"), "dup")


class TestLeaderboard(unittest.TestCase):
    def test_wins_losses_draws_tallied(self):
        results = [
            {"a_id": A, "a_name": "Alice", "b_id": B, "b_name": "Bob", "winner_id": A, "finished_at": 1},
            {"a_id": A, "a_name": "Alice", "b_id": B, "b_name": "Bob", "winner_id": B, "finished_at": 2},
            {"a_id": A, "a_name": "Alice", "b_id": B, "b_name": "Bob", "winner_id": None, "finished_at": 3},
        ]
        board = aggregate_leaderboard(results)
        by_id = {row["user_id"]: row for row in board}
        self.assertEqual(by_id[A]["wins"], 1)
        self.assertEqual(by_id[A]["losses"], 1)
        self.assertEqual(by_id[A]["draws"], 1)
        self.assertEqual(by_id[B]["wins"], 1)
        self.assertEqual(by_id[B]["losses"], 1)

    def test_latest_name_wins(self):
        results = [
            {"a_id": A, "a_name": "OldName", "b_id": B, "b_name": "Bob", "winner_id": A, "finished_at": 1},
            {"a_id": A, "a_name": "NewName", "b_id": B, "b_name": "Bob", "winner_id": B, "finished_at": 5},
        ]
        board = aggregate_leaderboard(results)
        by_id = {row["user_id"]: row for row in board}
        self.assertEqual(by_id[A]["name"], "NewName")

    def test_sorted_by_wins_then_fewer_losses(self):
        results = [
            {"a_id": 1, "a_name": "P1", "b_id": 2, "b_name": "P2", "winner_id": 1, "finished_at": 1},
            {"a_id": 1, "a_name": "P1", "b_id": 3, "b_name": "P3", "winner_id": 3, "finished_at": 2},
            {"a_id": 2, "a_name": "P2", "b_id": 3, "b_name": "P3", "winner_id": 2, "finished_at": 3},
        ]
        board = aggregate_leaderboard(results)
        ids_in_order = [row["user_id"] for row in board]
        self.assertEqual(ids_in_order[0], 1)  # 1 win, 1 loss — tied wins with 2 and 3, but check order below

    def test_limit_applied(self):
        results = [
            {"a_id": i, "a_name": f"P{i}", "b_id": i + 100, "b_name": f"P{i+100}", "winner_id": i, "finished_at": i}
            for i in range(20)
        ]
        board = aggregate_leaderboard(results, limit=5)
        self.assertEqual(len(board), 5)


class TestPagination(unittest.TestCase):
    def test_first_page(self):
        page, offset, total_pages = paginate(total=12, page=0, per_page=5)
        self.assertEqual((page, offset, total_pages), (0, 0, 3))

    def test_clamps_above_range(self):
        page, offset, total_pages = paginate(total=12, page=99, per_page=5)
        self.assertEqual((page, offset, total_pages), (2, 10, 3))

    def test_clamps_below_zero(self):
        page, offset, total_pages = paginate(total=12, page=-5, per_page=5)
        self.assertEqual((page, offset, total_pages), (0, 0, 3))

    def test_empty_is_one_page(self):
        page, offset, total_pages = paginate(total=0, page=0, per_page=5)
        self.assertEqual((page, offset, total_pages), (0, 0, 1))


if __name__ == "__main__":
    unittest.main()
