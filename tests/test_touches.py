"""Tests for touches.py (WP1 of 1.5): who else touched a board's cards. Table tests, no board, no log.

    cd <plugin dir> && python -m unittest -v test_touches
"""
import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

_spec = importlib.util.spec_from_file_location("calling_mom_touches_under_test", Path(__file__).resolve().parent.parent / "touches.py")
touches = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(touches)

TASK = NS(id="t_1", assignee="worker", status="ready")


def comment(author, body="text", id=1, ts=100):
    return NS(id=id, author=author, body=body, created_at=ts)


def event(kind_, id=1, ts=100, **payload):
    return NS(id=id, kind=kind_, payload=payload or None, created_at=ts)


# every author and body class; True = outside help
COMMENTS = [
    ("default/note", "default", "please look at X", True),
    ("desktop/note", "desktop", "please look at X", True),
    ("user/note", "user", "ok", True),
    ("assignee is not outside help", "worker", "my own note", False),
    ("calling-mom advisor author (pre 1.3.1)", "advisor", "anything", False),
    ("calling-mom escalation author", "escalation", "Escalated to m", False),
    ("an answer through calling-mom is counted elsewhere", "you", "[you via calling-mom] use B", False),
    ("advice header under another author", "default", "[error/same_error] run 3 · m (p): 1 in / 2 out\nDo X", False),
    ("olymp advice header", "default", "[architecture/critical_change] run 1 · m (p, olymp): 1 in / 2 out", False),
    ("advisor unavailable", "default", "[advisor unavailable] 403", False),
    ("pre-flight hold", "default", "[pre-flight] HOLD: force push. Instead: commit", False),
    ("mom answered", "default", "[mom answered] B", False),
    ("olymp answered", "default", "[olymp answered] B", False),
    ("brief for you", "default", "[for you] Question: ...", False),
    ("quota note (WP2)", "default", "[quota] claude hit its usage limit", False),
    ("stalled note (WP2)", "default", "[stalled] no heartbeat since 11:00 UTC", False),
    ("a person who quotes a prefix mid-text", "default", "see the [for you] brief above", True),
    ("an empty author is no one", "", "text", False),
]

# event kind -> (what, counted); the store records no actor for any of them
EVENTS = [
    ("linked", "link"), ("unlinked", "unlink"), ("unblocked", "unblock"), ("edited", "edit"), ("archived", "archive"),
    ("assigned", "assign"), ("model_override_set", "model"), ("reasoning_effort_set", "effort"),
    ("changes_requested", "changes"),
]
NOT_TOUCHES = ["heartbeat", "claimed", "commented", "worker_result", "status", "promoted", "completed", "blocked"]


class CommentTest(unittest.TestCase):
    def test_every_author_and_body_class(self):
        for name, author, body, outside in COMMENTS:
            with self.subTest(name):
                row = touches.comment_touch(TASK, comment(author, body, id=7, ts=555))
                if not outside:
                    self.assertIsNone(row)
                    continue
                self.assertEqual(row, {"ts": 555, "task": "t_1", "src": "comment", "what": "comment", "author": author,
                                       "actor": "other", "store_id": 7})

    def test_the_text_never_reaches_the_row(self):
        row = touches.comment_touch(TASK, comment("default", "secret token abc"))
        self.assertNotIn("secret", str(row))

    def test_a_card_without_assignee_counts_every_named_author(self):
        row = touches.comment_touch(NS(id="t_2", assignee=None, status="ready"), comment("daidalos", "hi"))
        self.assertEqual(row["author"], "daidalos")


class EventTest(unittest.TestCase):
    def test_every_structural_event_is_unattributed(self):
        for kind, what in EVENTS:
            with self.subTest(kind):
                row = touches.event_touch(TASK, event(kind, id=9, ts=77))
                self.assertEqual(row, {"ts": 77, "task": "t_1", "src": "event", "what": what, "actor": "unattributed", "store_id": 9})

    def test_other_events_are_no_touch(self):
        for kind in NOT_TOUCHES:
            with self.subTest(kind):
                self.assertIsNone(touches.event_touch(TASK, event(kind)))

    def test_only_a_manual_reclaim_counts(self):
        self.assertEqual(touches.event_touch(TASK, event("reclaimed", manual=True, reason="x"))["what"], "reclaim")
        self.assertIsNone(touches.event_touch(TASK, event("reclaimed", reason="ttl")))
        self.assertIsNone(touches.event_touch(TASK, event("reclaimed")))


class TriageTest(unittest.TestCase):
    def test_a_card_enters_triage_by_a_status_event_or_the_block_loop_breaker(self):
        events = [event("created", id=1), event("status", id=2, ts=10, status="triage"),
                  event("status", id=3, ts=20, status="todo"), event("block_loop_detected", id=4, ts=30, reason="x")]
        self.assertEqual(touches.triage_entries(events), [(2, 10), (4, 30)])

    def test_the_time_until_the_card_left_triage(self):
        events = [event("block_loop_detected", id=4, ts=30), event("commented", id=5, ts=35), event("promoted", id=6, ts=90)]
        self.assertEqual(touches.wait_s(NS(status="ready"), events, 4, 30, "triage"), 60)

    def test_leaving_events(self):
        for kind in ("promoted", "unblocked", "claimed", "completed", "archived"):
            with self.subTest(kind):
                self.assertEqual(touches.wait_s(NS(status="ready"), [event(kind, id=5, ts=130)], 1, 100, "triage"), 30)
        self.assertEqual(touches.wait_s(NS(status="ready"), [event("status", id=5, ts=140, status="todo")], 1, 100, "triage"), 40)

    def test_the_first_leaving_event_ends_the_wait_not_a_later_one(self):
        events = [event("promoted", id=5, ts=130), event("commented", id=6, ts=900)]
        self.assertEqual(touches.wait_s(NS(status="ready"), events, 1, 100, "triage"), 30)

    def test_a_status_event_into_triage_again_does_not_leave(self):
        events = [event("status", id=5, ts=140, status="triage")]
        self.assertIsNone(touches.wait_s(NS(status="triage"), events, 1, 100, "triage"))

    def test_still_waiting_is_none(self):
        self.assertIsNone(touches.wait_s(NS(status="triage"), [event("commented", id=5, ts=130)], 1, 100, "triage"))

    def test_a_card_that_left_without_a_known_event_uses_its_last_event(self):
        self.assertEqual(touches.wait_s(NS(status="ready"), [event("commented", id=5, ts=130)], 1, 100, "triage"), 30)

    def test_a_card_that_left_without_any_later_event_waited_zero(self):
        self.assertEqual(touches.wait_s(NS(status="ready"), [], 1, 100, "triage"), 0)

    def test_events_before_the_entry_do_not_count(self):
        events = [event("promoted", id=2, ts=50)]
        self.assertIsNone(touches.wait_s(NS(status="triage"), events, 3, 100, "triage"))

    def test_a_blocked_card_waits_in_blocked(self):
        self.assertIsNone(touches.wait_s(NS(status="blocked"), [], 1, 100, "blocked"))


class QuestionTest(unittest.TestCase):
    def test_the_block_that_asked_is_the_latest_needs_input_block_before_the_question(self):
        events = [event("blocked", id=1, ts=10, kind="capability"), event("blocked", id=2, ts=20, kind="needs_input"),
                  event("blocked", id=3, ts=30, kind="needs_input"), event("blocked", id=4, ts=90, kind="needs_input")]
        self.assertEqual(touches.question_block(events, 35), (3, 30))

    def test_a_question_without_a_block_is_not_found(self):
        self.assertIsNone(touches.question_block([event("blocked", id=1, ts=10, kind="capability")], 35))

    def test_a_person_is_reached_unless_mom_answered(self):
        for outcome, why, reached in (("human", None, True), ("error", "timeout", True), ("skipped", "cap", True),
                                      ("skipped", "goal", True), ("skipped", "rekind", False), ("answered", None, False)):
            with self.subTest((outcome, why)):
                row = {"kind": "question", "outcome": outcome, **({"reason": why} if why else {})}
                self.assertIs(touches.reached_person(row), reached)


if __name__ == "__main__":
    unittest.main()
