import random
import unittest

from bot.tambola_logic import (
    CLAIM_WINDOW,
    add_player,
    begin,
    call_next,
    claim,
    format_ticket,
    is_over,
    make_ticket,
    new_game,
    ticket_numbers,
    ticket_ok,
)


class TestTickets(unittest.TestCase):
    def test_many_tickets_follow_housie_shape(self):
        rng = random.Random(7)
        for _ in range(200):
            ticket = make_ticket(rng)
            self.assertTrue(ticket_ok(ticket))

    def test_format_uses_dot_for_blanks(self):
        ticket = make_ticket(random.Random(1))
        text = format_ticket(ticket)
        self.assertIn(" .", text)
        self.assertEqual(len(text.splitlines()), 3)


class TestTambolaFlow(unittest.TestCase):
    def test_need_two_players_to_begin(self):
        game = new_game(1, "Ada")
        self.assertEqual(add_player(game, 1, "Ada", random.Random(1)), "ok")
        self.assertEqual(begin(game, random.Random(1)), "need_players")
        self.assertFalse(game["started"])

    def test_duplicate_ticket_keeps_one_player(self):
        game = new_game(1, "Ada")
        add_player(game, 1, "Ada", random.Random(1))
        self.assertEqual(add_player(game, 1, "Ada", random.Random(2)), "dup")
        self.assertEqual(len(game["players"]), 1)

    def test_ticket_cap(self):
        game = new_game(1, "Ada")
        rng = random.Random(3)
        for uid in range(30):
            self.assertEqual(add_player(game, uid, f"P{uid}", rng), "ok")
        self.assertEqual(add_player(game, 99, "Late", rng), "full")

    def test_calls_do_not_repeat_and_open_a_claim_window(self):
        game = self._started()
        seen = []
        for _ in range(90):
            number = call_next(game, now=1000 + len(seen))
            self.assertIsNotNone(number)
            seen.append(number)
        self.assertEqual(len(set(seen)), 90)
        self.assertEqual(call_next(game, now=2000), None)
        self.assertEqual(game["claim_until"], 1000 + 89 + CLAIM_WINDOW)
        self.assertFalse(is_over(game, now=game["claim_until"] - 1))
        self.assertTrue(is_over(game, now=game["claim_until"]))

    def test_line_and_full_house(self):
        game = self._started()
        ticket = game["players"]["1"]["ticket"]
        row = [n for n in ticket[0] if n is not None]
        for number in row:
            self._force_call(game, number)
        self.assertEqual(claim(game, 1, "line"), "line")
        self.assertEqual(claim(game, 2, "line"), "taken")
        self.assertEqual(claim(game, 1, "full"), "invalid")

        for number in ticket_numbers(ticket):
            if number not in game["called"]:
                self._force_call(game, number)
        self.assertEqual(claim(game, 1, "full"), "full")
        self.assertTrue(is_over(game, now=0))
        self.assertEqual(claim(game, 2, "full"), "taken")

    def test_wrong_claim_does_not_remove_the_player(self):
        game = self._started()
        self.assertEqual(claim(game, 1, "line"), "invalid")
        self.assertIn("1", game["players"])
        self.assertIsNone(game["line_winner"])

    def test_stranger_cannot_claim(self):
        game = self._started()
        self.assertEqual(claim(game, 404, "line"), "absent")

    def test_closed_after_start(self):
        game = self._started()
        self.assertEqual(add_player(game, 9, "Late", random.Random(1)), "closed")

    def _started(self) -> dict:
        game = new_game(1, "Ada")
        add_player(game, 1, "Ada", random.Random(4))
        add_player(game, 2, "Ben", random.Random(5))
        self.assertEqual(begin(game, random.Random(6)), "ok")
        return game

    def _force_call(self, game: dict, number: int) -> None:
        pool = [n for n in game["pool"] if n != number]
        game["pool"] = [number] + pool
        game["claim_until"] = None
        drawn = call_next(game, now=50 + len(game["called"]))
        self.assertEqual(drawn, number)


if __name__ == "__main__":
    unittest.main()
