"""Tests for quota.py (WP2 of 1.5): error classes, reset times, provider health, stalled cards, pace plan.
Table tests over plain data, no board, no log.

    cd <plugin dir> && python -m unittest -v test_quota
"""
import calendar
import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

_spec = importlib.util.spec_from_file_location("calling_mom_quota_under_test", Path(__file__).resolve().parent / "quota.py")
quota = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(quota)


def utc(h, m=0, day=6):
    return calendar.timegm((2026, 11, day, h, m, 0))


# the real shapes seen on 2026-11-05 (logs of the gateway) and the ones the spec names
SESSION = ("Incomplete upstream response (first upstream attempt: status 429, capture incomplete, native retries denied: 0, "
           "upstream said: This request would exceed your account's rate limit. Please try again later.): "
           "You've hit your session limit · resets 12:20pm (UTC)")
KEY_LIMIT = ("Error code: 403 - {'error': {'message': 'Key limit exceeded (total limit). Manage it using "
             "https://openrouter.ai/workspaces/default/keys/abc $42.48 of $10.00 used; does not reset.', 'code': 403}}")

# (name, error type, message, status, reason, class)
CLASSES = [
    ("claude subscription session limit", "ClaudeAPIError", SESSION, 429, "rate_limit", "rate_limit"),
    ("a 429 without text", "RateLimitError", "", 429, None, "rate_limit"),
    ("usage limit wording", "ClaudeAPIError", "You've hit your usage limit", None, None, "rate_limit"),
    ("openrouter key limit", "PermissionDeniedError", KEY_LIMIT, 403, "auth", "key_limit"),
    ("key limit wins over auth", "PermissionDeniedError", "Key limit exceeded", 403, "auth", "key_limit"),
    ("credits gone (402)", "APIStatusError", "payment required", 402, "billing", "key_limit"),
    ("claude logged out", "ClaudeCodeLoggedOut", "", None, None, "auth"),
    ("a 401", "AuthenticationError", "invalid x-api-key", 401, "auth", "auth"),
    ("a revoked login", "AuthError", "relogin", None, "auth_permanent", "auth"),
    ("an aggregator's upstream 429 leaves the key healthy", "RateLimitError", "model busy", 429, "upstream_rate_limit", "other"),
    ("overloaded", "APIStatusError", "overloaded", 529, "overloaded", "other"),
    ("a timeout", "ReadTimeout", "timed out", None, "timeout", "other"),
    ("nothing known", "", "", None, None, "other"),
    ("none at all", None, None, None, None, "other"),
]


class ClassifyTest(unittest.TestCase):
    def test_every_error_class(self):
        for name, etype, message, status, reason, want in CLASSES:
            with self.subTest(name):
                self.assertEqual(quota.classify(etype, message, status, reason), want)

    def test_a_huge_message_is_read_bounded(self):
        self.assertEqual(quota.classify("E", "x" * 10_000_000 + "Key limit exceeded"), "other")


RESETS = [
    ("the spec example", "You've hit your session limit · resets 1:50pm (UTC)", utc(11, 19), utc(13, 50)),
    ("the real one", SESSION, utc(10, 52), utc(12, 20)),
    ("without minutes", "resets 9am (UTC)", utc(6), utc(9)),
    ("noon", "resets 12:00pm (UTC)", utc(8), utc(12)),
    ("midnight is the next day", "resets 12am (UTC)", utc(20), utc(0, day=7)),
    ("a time of today that passed is tomorrow", "resets 9am (UTC)", utc(13), utc(9, day=7)),
    ("a zone that is not UTC is unknown", "resets 1:50pm (Europe/Berlin)", utc(8), None),
    ("no zone is unknown", "resets 1:50pm", utc(8), None),
    ("an hour past 12 is unknown", "resets 13:50pm (UTC)", utc(8), None),
    ("minutes past 59 are unknown", "resets 1:75pm (UTC)", utc(8), None),
    ("no reset text", KEY_LIMIT, utc(8), None),
    ("empty", "", utc(8), None),
    ("none", None, utc(8), None),
]


class ResetTest(unittest.TestCase):
    def test_every_shape(self):
        for name, text, now, want in RESETS:
            with self.subTest(name):
                self.assertEqual(quota.parse_reset(text, now), want)

    def test_clock_is_hh_mm_utc(self):
        self.assertEqual(quota.clock(utc(13, 5)), "13:05")


class HealthTest(unittest.TestCase):
    def test_a_limit_is_active_until_its_time_and_per_profile(self):
        health = {}
        self.assertTrue(quota.mark(health, "default", "claude", "limited", utc(13), "rate_limit", True))
        self.assertEqual(quota.active(health, "default", "claude", utc(12))["state"], "limited")
        self.assertIsNone(quota.active(health, "default", "claude", utc(13)))      # over at the reset time
        self.assertIsNone(quota.active(health, "other", "claude", utc(12)))        # another profile is not affected
        self.assertIsNone(quota.active(health, "default", "openrouter", utc(12)))  # nor another provider

    def test_the_same_news_changes_nothing(self):
        health = {}
        quota.mark(health, "p", "c", "limited", utc(13), "rate_limit", True)
        self.assertFalse(quota.mark(health, "p", "c", "limited", utc(13) + 30, "rate_limit", True))   # within a minute
        self.assertTrue(quota.mark(health, "p", "c", "limited", utc(14), "rate_limit", True))         # a later reset
        self.assertEqual(health["p"]["c"]["until"], utc(14))

    def test_an_unknown_reset_never_extends_a_known_one(self):
        health = {}
        quota.mark(health, "p", "c", "limited", utc(13), "rate_limit", True)
        self.assertFalse(quota.mark(health, "p", "c", "limited", utc(12, 30), "rate_limit", False))
        self.assertEqual(health["p"]["c"]["until"], utc(13))

    def test_a_new_state_is_a_change(self):
        health = {}
        quota.mark(health, "p", "c", "down", utc(12), "auth", True)
        self.assertTrue(quota.mark(health, "p", "c", "limited", utc(13), "rate_limit", True))
        self.assertEqual(health["p"]["c"]["state"], "limited")

    def test_expire_returns_what_ended_and_drops_it(self):
        health = {}
        quota.mark(health, "p", "c", "limited", utc(12), "rate_limit", True)
        quota.mark(health, "p", "d", "down", utc(18), "auth", True)
        self.assertEqual(quota.expire(health, utc(13)), [("p", "c", "limited")])
        self.assertEqual(list(health["p"]), ["d"])
        quota.expire(health, utc(19))
        self.assertEqual(health, {})   # an empty profile leaves no trace

    def test_the_entries_of_one_profile(self):
        health = {}
        quota.mark(health, "p", "c", "limited", utc(13), "rate_limit", True)
        quota.mark(health, "p", "d", "limited", utc(15), "rate_limit", True)
        quota.mark(health, "q", "e", "limited", utc(15), "rate_limit", True)
        self.assertEqual([prov for prov, _ in quota.entries(health, "p", utc(12))], ["d", "c"])   # the longest wait first
        self.assertEqual(quota.entries(health, "nobody", utc(12)), [])
        self.assertEqual(quota.entries(health, None, utc(12))[0][0], "d")                         # no profile: all of them
        self.assertEqual(len(quota.entries(health, None, utc(12))), 3)


def card(id_="t_1", status="running", beat=None, started=None, created=0, run=7, priority=0):
    return NS(id=id_, status=status, last_heartbeat_at=beat, started_at=started, created_at=created, current_run_id=run,
              priority=priority)


class StalledTest(unittest.TestCase):
    NOW = utc(12)

    def stalled(self, task, minutes=15):
        return [t.id for t in quota.stalled([task], self.NOW, minutes)]

    def test_twenty_minutes_without_a_heartbeat_is_stalled(self):
        self.assertEqual(self.stalled(card(beat=self.NOW - 20 * 60)), ["t_1"])

    def test_ten_minutes_is_not(self):
        self.assertEqual(self.stalled(card(beat=self.NOW - 10 * 60)), [])

    def test_exactly_the_limit_is_stalled(self):
        self.assertEqual(self.stalled(card(beat=self.NOW - 15 * 60)), ["t_1"])

    def test_zero_switches_it_off(self):
        self.assertEqual(self.stalled(card(beat=0), minutes=0), [])

    def test_a_card_that_never_beat_counts_from_its_start_then_its_creation(self):
        self.assertEqual(self.stalled(card(beat=None, started=self.NOW - 20 * 60)), ["t_1"])
        self.assertEqual(self.stalled(card(beat=None, started=None, created=self.NOW - 20 * 60)), ["t_1"])
        self.assertEqual(self.stalled(card(beat=None, started=self.NOW - 60)), [])

    def test_only_running_cards(self):
        self.assertEqual(self.stalled(card(status="ready", beat=0)), [])

    def test_last_beat_and_minutes(self):
        task = card(beat=self.NOW - 20 * 60)
        self.assertEqual(quota.last_beat(task), self.NOW - 20 * 60)
        self.assertEqual(quota.minutes_silent(task, self.NOW), 20)


def todo(id_, priority=0, created=0, status="todo"):
    return card(id_, status=status, priority=priority, created=created, run=None)


class PlanTest(unittest.TestCase):
    def test_four_ready_cards_and_max_one_make_one_chain_in_priority_then_age_order(self):
        tasks = [todo("a", 0, 4, "ready"), todo("b", 5, 3, "ready"), todo("c", 5, 2, "ready"), todo("d", 0, 1, "ready")]
        plan = quota.plan(tasks, set(), 1)
        self.assertEqual(plan["chains"], [["c", "b", "d", "a"]])
        self.assertEqual(plan["add"], [("c", "b"), ("b", "d"), ("d", "a")])
        self.assertEqual(plan["skipped_running"], 0)

    def test_a_running_card_heads_its_chain(self):
        tasks = [card("r"), todo("a", created=1, status="ready"), todo("b", created=2)]
        plan = quota.plan(tasks, set(), 1)
        self.assertEqual(plan["chains"], [["r", "a", "b"]])
        self.assertEqual(plan["add"], [("r", "a"), ("a", "b")])

    def test_two_chains_go_round_robin_and_a_running_card_takes_the_first(self):
        tasks = [card("r"), *(todo(x, created=i) for i, x in enumerate("abcd"))]
        plan = quota.plan(tasks, set(), 2)
        self.assertEqual(plan["chains"], [["r", "b", "d"], ["a", "c"]])     # a heads the empty chain, then round-robin
        self.assertEqual(plan["add"], [("r", "b"), ("b", "d"), ("a", "c")])

    def test_more_running_cards_than_max_are_not_touched(self):
        tasks = [card("r1", created=1), card("r2", created=2), card("r3", created=3), todo("a")]
        plan = quota.plan(tasks, set(), 2)
        self.assertEqual(plan["skipped_running"], 1)
        self.assertEqual(plan["chains"], [["r1", "a"], ["r2"]])

    def test_triage_blocked_done_are_skipped(self):
        tasks = [todo("a"), todo("t", status="triage"), todo("b", status="blocked"), todo("d", status="done"), todo("z", status="archived")]
        plan = quota.plan(tasks, set(), 1)
        self.assertEqual(plan["chains"], [["a"]])
        self.assertEqual(plan["add"], [])

    def test_existing_links_stay_and_are_not_added_again(self):
        tasks = [todo("a", created=1), todo("b", created=2)]
        plan = quota.plan(tasks, {("a", "b")}, 1)
        self.assertEqual(plan["add"], [])
        self.assertEqual(plan["chains"], [["a", "b"]])

    def test_a_link_that_would_close_a_cycle_goes_to_another_chain_or_is_skipped(self):
        tasks = [todo("a", created=1), todo("b", created=2)]
        plan = quota.plan(tasks, {("b", "a")}, 1)           # b must stay before a: the chain a -> b would be a cycle
        self.assertEqual(plan["add"], [])
        self.assertEqual(plan["skipped"], ["b"])
        self.assertEqual(plan["chains"], [["a"]])
        plan = quota.plan(tasks, {("b", "a")}, 2)           # with two chains b heads the second one
        self.assertEqual(plan["chains"], [["a"], ["b"]])
        self.assertEqual(plan["skipped"], [])

    def test_a_longer_cycle_is_found_too(self):
        tasks = [todo("a", created=1), todo("b", created=2)]
        plan = quota.plan(tasks, {("b", "x"), ("x", "a")}, 1)
        self.assertEqual(plan["skipped"], ["b"])

    def test_max_below_one_is_refused(self):
        with self.assertRaises(ValueError):
            quota.plan([], set(), 0)

    def test_nothing_to_do(self):
        plan = quota.plan([], set(), 3)
        self.assertEqual((plan["chains"], plan["add"], plan["skipped"]), ([[], [], []], [], []))


class DurationTest(unittest.TestCase):
    def test_hours_and_minutes(self):
        for seconds, want in [(30, "30 s"), (1800, "30 min"), (6660, "1 h 51 min"), (7200, "2 h"), (59, "59 s")]:
            with self.subTest(seconds):
                self.assertEqual(quota.hm(seconds), want)


if __name__ == "__main__":
    unittest.main()
