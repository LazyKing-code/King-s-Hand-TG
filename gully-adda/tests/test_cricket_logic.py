import unittest

from bot.cricket_logic import (
    apply_ball,
    begin,
    both_picked,
    can_begin,
    cricket_new,
    current_batter,
    current_bowler,
    idle,
    join,
    max_balls,
    over_label,
    record_pick,
    result_phrase,
    set_overs,
    side_of,
    summary,
    winner_side,
)

A1, A2, B1, B2 = 11, 12, 21, 22


def _ready(overs: int = 1) -> dict:
    game = cricket_new(A1, "A1")
    set_overs(game, overs)
    join(game, A1, "A1", "a")
    join(game, A2, "A2", "a")
    join(game, B1, "B1", "b")
    join(game, B2, "B2", "b")
    begin(game)
    return game


def _ball(game: dict, bat: int, bowl: int, bat_n: int, bowl_n: int) -> str:
    record_pick(game, bat, bat_n)
    record_pick(game, bowl, bowl_n)
    return apply_ball(game)


class TestLobby(unittest.TestCase):
    def test_both_sides_required(self):
        game = cricket_new(A1, "A1")
        join(game, A1, "A1", "a")
        join(game, A2, "A2", "a")
        self.assertEqual(can_begin(game), "need_sides")
        self.assertEqual(begin(game), "need_sides")

    def test_two_players_on_opposite_sides_can_start(self):
        game = cricket_new(A1, "A1")
        join(game, A1, "A1", "a")
        join(game, B1, "B1", "b")
        self.assertEqual(begin(game), "ok")
        self.assertEqual(current_batter(game)["id"], A1)
        self.assertEqual(current_bowler(game)["id"], B1)

    def test_switching_sides_leaves_the_old_one(self):
        game = cricket_new(A1, "A1")
        join(game, A1, "A1", "a")
        join(game, A1, "A1", "b")
        self.assertEqual(side_of(game, A1), "b")
        self.assertEqual(game["team_a"], [])

    def test_tapping_the_same_side_keeps_batting_order(self):
        game = cricket_new(1, "Host")
        join(game, 1, "First", "a")
        join(game, 2, "Second", "a")
        self.assertEqual(join(game, 1, "First", "a"), "ok")
        self.assertEqual([p["id"] for p in game["team_a"]], [1, 2])

    def test_overs_clamp(self):
        game = cricket_new(A1, "A1")
        self.assertEqual(set_overs(game, 4), "bad")
        self.assertEqual(set_overs(game, 3), "ok")
        self.assertEqual(max_balls(game), 18)
        self.assertEqual(over_label(4), "0.4")
        self.assertEqual(over_label(6), "1.0")

    def test_team_cap(self):
        game = cricket_new(1, "Host")
        for uid in range(11):
            self.assertEqual(join(game, uid, str(uid), "a"), "ok")
        join(game, 99, "Late", "b")
        self.assertEqual(join(game, 99, "Late", "a"), "full")
        self.assertEqual(side_of(game, 99), "b")


class TestBalls(unittest.TestCase):
    def test_stranger_and_duplicate_picks(self):
        game = _ready()
        self.assertEqual(record_pick(game, 404, 3), "spectator")
        self.assertEqual(record_pick(game, A1, 0), "bad")
        self.assertEqual(record_pick(game, A1, 4), "wait")
        self.assertEqual(record_pick(game, A1, 5), "dup")
        self.assertFalse(both_picked(game))
        self.assertEqual(record_pick(game, B1, 2), "ready")

    def test_wicket_brings_the_next_batter(self):
        game = _ready()
        self.assertEqual(_ball(game, A1, B1, 4, 4), "play")
        self.assertEqual(current_batter(game)["id"], A2)
        self.assertEqual(game["score_a"], 0)
        self.assertEqual(game["balls"], 1)
        self.assertEqual(current_bowler(game)["id"], B2)

    def test_runs_stay_with_the_same_batter(self):
        game = _ready()
        self.assertEqual(_ball(game, A1, B1, 6, 1), "play")
        self.assertEqual(game["score_a"], 6)
        self.assertEqual(current_batter(game)["id"], A1)
        self.assertEqual(summary(game), "6 - 0")

    def test_all_out_ends_the_innings_early(self):
        game = _ready()
        _ball(game, A1, B1, 3, 3)
        result = _ball(game, A2, B2, 5, 5)
        self.assertEqual(result, "chase")
        self.assertEqual(game["first_innings"], 0)
        self.assertEqual(game["batting"], "b")
        self.assertEqual(game["balls"], 0)
        self.assertEqual(current_batter(game)["id"], B1)
        self.assertEqual(current_bowler(game)["id"], A1)

    def test_one_batter_out_ends_innings(self):
        game = cricket_new(A1, "A1")
        join(game, A1, "A1", "a")
        join(game, B1, "B1", "b")
        begin(game)
        self.assertEqual(_ball(game, A1, B1, 2, 2), "chase")
        self.assertEqual(game["first_innings"], 0)

    def test_overs_end_the_innings(self):
        game = cricket_new(A1, "A1")
        set_overs(game, 1)
        join(game, A1, "A1", "a")
        join(game, B1, "B1", "b")
        begin(game)
        result = "play"
        for _ in range(5):
            result = _ball(game, A1, B1, 1, 2)
            self.assertEqual(result, "play")
        result = _ball(game, A1, B1, 1, 2)
        self.assertEqual(result, "chase")
        self.assertEqual(game["first_innings"], 6)

    def test_chase_finishes_mid_over(self):
        game = cricket_new(A1, "A1")
        join(game, A1, "A1", "a")
        join(game, B1, "B1", "b")
        begin(game)
        for _ in range(6):
            _ball(game, A1, B1, 1, 2)
        self.assertEqual(game["first_innings"], 6)
        self.assertEqual(_ball(game, B1, A1, 6, 1), "play")
        self.assertEqual(_ball(game, B1, A1, 4, 1), "finished")
        self.assertEqual(winner_side(game), "b")
        self.assertEqual(result_phrase(game), "by 1 wicket")

    def test_tie_when_chase_is_bowled_out_level(self):
        game = cricket_new(A1, "A1")
        join(game, A1, "A1", "a")
        join(game, B1, "B1", "b")
        begin(game)
        _ball(game, A1, B1, 4, 4)  # A out for 0
        self.assertEqual(_ball(game, B1, A1, 1, 1), "finished")
        self.assertIsNone(winner_side(game))
        self.assertEqual(result_phrase(game), "Tie")

    def test_defend_by_runs(self):
        game = cricket_new(A1, "A1")
        join(game, A1, "A1", "a")
        join(game, B1, "B1", "b")
        begin(game)
        for _ in range(6):
            _ball(game, A1, B1, 2, 1)
        self.assertEqual(_ball(game, B1, A1, 3, 3), "finished")
        self.assertEqual(winner_side(game), "a")
        self.assertEqual(result_phrase(game), "by 12 runs")


class TestIdle(unittest.TestCase):
    def test_first_stall_skips_and_second_closes(self):
        game = cricket_new(A1, "A1")
        join(game, A1, "A1", "a")
        join(game, B1, "B1", "b")
        begin(game)
        self.assertEqual(idle(game), "skip")
        self.assertEqual(game["balls"], 0)
        self.assertIn("Skipped", game["last"])
        self.assertEqual(idle(game), "expire")

    def test_skip_moves_to_the_next_batter_once(self):
        game = _ready()
        self.assertEqual(idle(game), "skip")
        self.assertEqual(current_batter(game)["id"], A2)
        self.assertEqual(current_bowler(game)["id"], B2)
        # A2 and B2 have not stalled yet, so the match stays open.
        self.assertEqual(idle(game), "skip")
        self.assertEqual(current_batter(game)["id"], A1)

    def test_a_locked_pick_is_not_the_stall(self):
        game = _ready()
        record_pick(game, A1, 6)
        self.assertEqual(idle(game), "skip")
        self.assertEqual(current_batter(game)["id"], A1)
        self.assertEqual(current_bowler(game)["id"], B2)
        self.assertEqual(game["picks"], {})


if __name__ == "__main__":
    unittest.main()
