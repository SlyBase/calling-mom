"""Tests for the honest count (WP1 of 1.5): touches from the board sweep, own writes, advice feedback with
evidence, chat waits.

    cd <plugin dir> && python -m unittest -v test_count
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

import test_calling_mom as base

plugin, kb, kbc = base.plugin, base.kb, base.kbc
BOARD = "t-board"


class TouchFixture(base.AdvisorFixture):
    """A board of its own per test (the sweep lists every card), opted in, with its own state file."""

    def setUp(self):
        self._env = {k: os.environ.get(k) for k in ("HERMES_KANBAN_DB", "CALLING_MOM_STATE")}
        os.environ["HERMES_KANBAN_DB"] = str(Path(tempfile.mkdtemp()) / "kanban.db")
        kbc.init_db(Path(os.environ["HERMES_KANBAN_DB"]))
        os.environ["CALLING_MOM_STATE"] = str(Path(tempfile.mkdtemp()) / "state.json")
        super().setUp()  # the advisor is on for BOARD; self.tid is a card assigned to `worker`

    def tearDown(self):
        super().tearDown()
        for k, v in self._env.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

    def card(self, title="other card", assignee="worker"):
        return base.with_conn(lambda c: kb.create_task(c, title=title, assignee=assignee))

    def sweep(self, board=BOARD):
        plugin._last_scan.clear()  # the touch scan is rate-limited per board
        return plugin.sweep(board=board)

    def touches(self, **want):
        return [r for r in base.log_rows("touch") if all(r.get(k) == v for k, v in want.items())]

    def state(self):
        return json.loads(Path(os.environ["CALLING_MOM_STATE"]).read_text())

    def sql(self, query, *args):
        def run(conn):
            with conn:
                conn.execute(query, args)
        base.with_conn(run)

    def event(self, tid, kind, ts, payload=None):
        self.sql("INSERT INTO task_events (task_id, kind, payload, created_at) VALUES (?, ?, ?, ?)",
                 tid, kind, json.dumps(payload) if payload is not None else None, ts)

    def comment(self, tid, author, body, ts=None):
        base.with_conn(lambda c: kb.add_comment(c, tid, author, body))
        if ts:
            self.sql("UPDATE task_comments SET created_at = ? WHERE task_id = ? AND author = ? AND body = ?", ts, tid, author, body)


class CommentTouchTest(TouchFixture, unittest.TestCase):
    def test_a_comment_by_someone_else_is_one_touch_with_the_author_and_no_text(self):
        self.comment(self.tid, "default", "do it this way, secret-text", ts=1000)
        self.comment(self.tid, "worker", "my note")
        self.comment(self.tid, "default", "[for you] Question: x Options: a Recommendation: b If nobody answers: c")
        self.sweep()
        rows = self.touches()
        self.assertEqual([(r["what"], r["author"], r["actor"], r["task"], r["board"], r["ts"]) for r in rows],
                         [("comment", "default", "other", self.tid, BOARD, 1000)])
        self.assertNotIn("secret-text", Path(os.environ["CALLING_MOM_LOG"]).read_text())

    def test_a_second_sweep_adds_nothing_and_a_new_comment_counts_once(self):
        self.comment(self.tid, "default", "one")
        self.sweep()
        self.sweep()
        self.assertEqual(len(self.touches()), 1)
        self.comment(self.tid, "desktop", "two")
        self.sweep()
        self.sweep()
        self.assertEqual([r["author"] for r in self.touches()], ["default", "desktop"])

    def test_the_first_sweep_counts_the_history_of_the_board(self):
        self.comment(self.tid, "default", "written before calling-mom 1.5 ever swept", ts=5)
        self.sweep()
        self.assertEqual([r["ts"] for r in self.touches()], [5])

    def test_the_watermark_survives_a_restart(self):
        self.comment(self.tid, "default", "one")
        self.sweep()
        plugin._last_trace.clear()  # a new process knows nothing but the state file
        self.sweep()
        self.assertEqual(len(self.touches()), 1)
        self.assertEqual(set(self.state()["touches"][BOARD]), {"comment", "event", "question", "waiting"})


class EventTouchTest(TouchFixture, unittest.TestCase):
    def test_structural_events_are_unattributed_touches(self):
        a, b = self.card("a"), self.card("b")
        base.with_conn(lambda c: kb.link_tasks(c, a, b))
        base.with_conn(lambda c: kb.unlink_tasks(c, a, b))
        base.with_conn(lambda c: kb.assign_task(c, a, "other"))
        self.sweep()
        self.assertEqual(sorted((r["what"], r["actor"]) for r in self.touches(src="event")),
                         [("assign", "unattributed"), ("link", "unattributed"), ("unlink", "unattributed")])

    def test_only_a_manual_reclaim_counts(self):
        self.event(self.tid, "reclaimed", 50, {"manual": True, "reason": "x"})
        self.event(self.tid, "reclaimed", 60, {"reason": "ttl"})
        self.sweep()
        self.assertEqual([r["what"] for r in self.touches()], ["reclaim"])

    def test_an_event_counts_once_however_often_the_sweep_runs(self):
        a, b = self.card("a"), self.card("b")
        base.with_conn(lambda c: kb.link_tasks(c, a, b))
        for _ in range(3):
            self.sweep()
        self.assertEqual(len(self.touches(what="link")), 1)

    def test_a_board_that_did_not_opt_in_and_no_board_are_not_read(self):
        self.comment(self.tid, "default", "hi")
        self.sweep(board="other-board")
        self.sweep(board=None)
        self.assertEqual(self.touches(), [])

    def test_a_sweep_without_touches_still_marks_the_board_as_read(self):
        self.sweep()
        self.assertIn(BOARD, self.state()["touches"])

    def test_the_kill_switch_reads_nothing(self):
        self.comment(self.tid, "default", "hi")
        os.environ["CALLING_MOM"] = "off"
        try:
            self.sweep()
        finally:
            os.environ["CALLING_MOM"] = "on"
        self.assertEqual(self.touches(), [])

    def test_a_board_that_cannot_be_read_never_breaks_the_sweep(self):
        real = plugin._core.board_activity
        plugin._core.board_activity = lambda conn: 1 / 0
        try:
            self.assertEqual(self.sweep(), [])
        finally:
            plugin._core.board_activity = real


class ScanRateTest(TouchFixture, unittest.TestCase):
    def test_a_board_is_scanned_at_most_every_30_seconds(self):
        self.sweep()
        self.comment(self.tid, "default", "late")
        plugin.sweep(board=BOARD)  # the dispatcher ticks again at once: no scan
        self.assertEqual(self.touches(), [])
        plugin._last_scan[BOARD] -= plugin.TOUCH_EVERY_S
        plugin.sweep(board=BOARD)
        self.assertEqual(len(self.touches()), 1)


class OwnWriteTest(TouchFixture, unittest.TestCase):
    def test_what_calling_mom_writes_itself_is_logged_and_not_counted(self):
        tid = self.card("hard one")
        base.with_conn(lambda c: kb.block_task(c, tid, reason="cannot", kind="capability"))
        plugin.on_blocked(task_id=tid, board=BOARD, reason="cannot")
        own = base.log_rows("own_write")
        self.assertEqual(sorted(r["what"] for r in own), ["pin", "unblock"])
        self.assertTrue(all(r["task"] == tid and r["board"] == BOARD and isinstance(r["store_id"], int) for r in own))
        self.sweep()
        self.assertEqual(self.touches(task=tid), [])

    def test_a_person_pinning_a_model_is_counted(self):
        tid = self.card("pinned by hand")
        base.with_conn(lambda c: kb.set_model_override(c, tid, "m", provider="p"))
        self.sweep()
        self.assertEqual([r["what"] for r in self.touches(task=tid)], ["model"])

    def test_the_claim_pin_and_the_crit_marker_are_own_writes(self):
        tid = self.card("[crit] migrate")
        plugin.on_claimed(task_id=tid, board=BOARD)
        self.assertEqual([r["what"] for r in base.log_rows("own_write")], ["pin"])
        self.sweep()
        self.assertEqual(self.touches(task=tid), [])

    def test_a_pin_without_a_board_scope_is_still_logged(self):
        tid = self.card("no scope")
        with base.kbc.connect_closing() as conn:
            plugin._pin_strong(conn, kb.get_task(conn, tid), "why")
        self.assertEqual([r["board"] for r in base.log_rows("own_write")], [None])


class WaitingTest(TouchFixture, unittest.TestCase):
    def test_a_card_in_triage_is_one_touch_that_closes_when_it_leaves(self):
        tid = self.card("parked")
        self.event(tid, "block_loop_detected", 1000, {"reason": "x"})
        self.sql("UPDATE tasks SET status = 'triage' WHERE id = ?", tid)
        self.sweep()
        rows = self.touches(task=tid, what="triage")
        self.assertEqual(len(rows), 1)
        self.assertNotIn("wait_s", rows[0])
        self.assertEqual(rows[0]["ts"], 1000)
        self.sweep()
        self.assertEqual(len(self.touches(task=tid, what="triage")), 1)  # still waiting: nothing new
        self.event(tid, "promoted", 1090)
        self.sql("UPDATE tasks SET status = 'ready' WHERE id = ?", tid)
        self.sweep()
        self.sweep()
        rows = self.touches(task=tid, what="triage")
        self.assertEqual([r.get("wait_s") for r in rows], [None, 90])
        self.assertEqual({r["store_id"] for r in rows}, {rows[0]["store_id"]})
        self.assertEqual(rows[1]["ts"], 1000)  # the entry time, so the window of a report finds both rows
        self.assertEqual(self.state()["touches"][BOARD]["waiting"], {})

    def test_a_card_that_left_triage_before_the_first_sweep_has_its_wait_at_once(self):
        tid = self.card("parked then freed")
        self.event(tid, "status", 100, {"status": "triage", "requested_status": "triage"})
        self.event(tid, "status", 400, {"status": "todo", "requested_status": "todo"})
        self.sweep()
        self.assertEqual([r.get("wait_s") for r in self.touches(task=tid, what="triage")], [300])

    def test_a_question_mom_did_not_answer_reached_a_person(self):
        tid = self.card("asks")
        self.event(tid, "blocked", 2000, {"kind": "needs_input", "reason": "which?"})
        self.sql("UPDATE tasks SET status = 'blocked', block_kind = 'needs_input' WHERE id = ?", tid)
        plugin._trace_row("question", task=tid, board=BOARD, run=1, outcome="human", ts=2005)
        plugin._trace_row("question", task=tid, board=BOARD, run=1, outcome="answered", by="mom", ts=2006)  # Mom answered: not a person
        plugin._trace_row("question", task=tid, board="other", run=1, outcome="human", ts=2007)  # another board
        self.sweep()
        rows = self.touches(task=tid, what="to_person")
        self.assertEqual([(r["ts"], r.get("wait_s"), r["src"]) for r in rows], [(2000, None, "event")])
        self.sweep()
        self.assertEqual(len(self.touches(task=tid, what="to_person")), 1)
        self.event(tid, "unblocked", 2600)
        self.sql("UPDATE tasks SET status = 'ready' WHERE id = ?", tid)
        self.sweep()
        self.assertEqual([r.get("wait_s") for r in self.touches(task=tid, what="to_person")], [None, 600])

    def test_a_question_row_without_a_matching_block_is_skipped(self):
        tid = self.card("no block event")
        plugin._trace_row("question", task=tid, board=BOARD, run=1, outcome="human", ts=2005)
        self.sweep()
        self.assertEqual(self.touches(what="to_person"), [])

    def test_a_waiting_card_that_is_gone_is_dropped(self):
        state = {"touches": {BOARD: {"comment": 0, "event": 0, "question": 0, "waiting": {"event:9": ["t_gone", "triage", 9, 5]}}}}
        Path(os.environ["CALLING_MOM_STATE"]).write_text(json.dumps(state))
        self.sweep()
        self.assertEqual(self.state()["touches"][BOARD]["waiting"], {})


class FeedbackFlowTest(base.AdvisorFixture, unittest.TestCase):
    """Advice names `git status`: a later call of the run that starts with it is evidence, any other call is only action."""

    def setUp(self):
        super().setUp()
        plugin._pending.clear()
        self.llm._res.text = "The cause is a dirty tree. Run `git status`, then fix `src/app.py`."

    def call(self, command="git status --short", tool="terminal", **args):
        return plugin.on_tool_result(tool_name=tool, args=args or {"command": command}, result="ok", status="ok")

    def results(self):
        return [(r["result"], r["anchors"], r["match"]) for r in base.log_rows("advice_feedback")]

    def test_a_matching_call_makes_the_advice_followed(self):
        self.stuck(3)
        self.assertEqual(self.results(), [])  # nothing is decided at delivery
        self.call("git status --short")
        self.assertEqual(self.results(), [("followed", 2, plugin.feedback.anchors("`git status`")[0].hash)])

    def test_the_hash_and_not_the_text_goes_to_the_log(self):
        self.stuck(3)
        self.call("git status")
        self.assertNotIn("git status", json.dumps(base.log_rows("advice_feedback")))

    def test_any_other_call_makes_it_acted_when_the_run_ends(self):
        self.stuck(3)
        self.call("ls")
        self.assertEqual(self.results(), [])
        plugin._flush_feedback()
        self.assertEqual(self.results(), [("acted", 2, None)])

    def test_a_later_match_still_makes_it_followed(self):
        self.stuck(3)
        self.call("ls")
        self.call("git status")
        plugin._flush_feedback()
        self.assertEqual([r[0] for r in self.results()], ["followed"])

    def test_a_run_that_ends_without_another_call_ignored_the_advice(self):
        self.stuck(3)
        plugin._flush_feedback()
        self.assertEqual(self.results(), [("ignored", 2, None)])

    def test_advice_without_anchors_stays_unknown_at_once(self):
        self.llm._res.text = "Root cause: X. Next: do Y."
        self.stuck(3)
        self.assertEqual(self.results(), [("unknown", 0, None)])
        self.call("ls")
        plugin._flush_feedback()
        self.assertEqual(len(self.results()), 1)

    def test_a_path_anchor_and_a_tool_anchor_are_evidence_too(self):
        self.stuck(3)
        self.call(tool="read_file", path="/repo/src/app.py")
        self.assertEqual([r[0] for r in self.results()], ["followed"])
        self.llm._res.text = "Use `read_file` on the log."
        plugin._trackers.clear()
        self.stuck(3)
        self.call(tool="read_file", path="/x")
        self.assertEqual([r[0] for r in self.results()], ["followed", "followed"])

    def test_every_pending_advice_is_judged_by_itself(self):
        self.stuck(3)
        self.llm._res.text = "Try `pytest -x`."
        plugin._trackers.clear()
        self.stuck(3)
        self.call("pytest -x tests")
        plugin._flush_feedback()
        self.assertEqual(sorted(r[0] for r in self.results()), ["acted", "followed"])

    def test_the_second_opinion_tool_does_not_count_as_evidence_for_its_own_advice(self):
        out = json.loads(plugin.handle_second_opinion({"question": "which design?"}))
        self.assertIn("opinion", out)
        self.call(tool="second_opinion", question="which design?")
        plugin._flush_feedback()
        self.assertEqual(self.results(), [("ignored", 2, None)])

    def test_another_run_is_not_judged(self):
        self.stuck(3)
        os.environ["HERMES_KANBAN_RUN_ID"] = "2"
        self.call("git status")
        os.environ["HERMES_KANBAN_RUN_ID"] = "1"
        self.assertEqual(self.results(), [])

    def test_the_flush_of_one_run_leaves_the_others(self):
        self.stuck(3)
        plugin._flush_feedback(("other", "9"))
        self.assertEqual(self.results(), [])
        plugin._flush_feedback((self.tid, "1"))
        self.assertEqual([r[0] for r in self.results()], ["ignored"])

    def test_the_reply_text_no_longer_decides_anything(self):
        self.stuck(3)
        plugin.on_api_response(assistant_message=NS(content="The advisor's hypothesis is disproven."))
        self.assertEqual(self.results(), [])


class ChatWaitTest(unittest.TestCase):
    """The time you wait on a prompt in chat: kind, seconds, outcome. Never the prompt, never the answer."""

    def setUp(self):
        os.environ["CALLING_MOM"] = "on"
        os.environ["CALLING_MOM_LOG"] = str(Path(tempfile.mkdtemp()) / "chat.jsonl")
        self.saved = json.dumps(plugin.DEFAULTS["count"])
        plugin._waits.clear()

    def tearDown(self):
        plugin.DEFAULTS["count"].update(json.loads(self.saved))

    def switch(self, on=True):
        plugin.DEFAULTS["count"]["chat_waits"] = on

    def wait(self, kind="clarify", outcome="answered", request_id="r1", prompt="rm -rf / ? my-password", seconds=0):
        plugin.on_human_input_request(kind=kind, request_id=request_id, prompt=prompt, session_id="s", platform="cli")
        if seconds:
            plugin._waits[request_id] -= seconds
        plugin.on_human_input_resolved(kind=kind, request_id=request_id, prompt=prompt, outcome=outcome, session_id="s")

    def rows(self):
        return base.log_rows("touch")

    def test_it_is_off_until_setup_asks(self):
        self.assertFalse(plugin.settings()["count"]["chat_waits"])
        self.wait()
        self.assertEqual(self.rows(), [])
        self.assertEqual(plugin._waits, {})

    def test_a_wait_is_one_row_with_kind_seconds_and_outcome(self):
        self.switch()
        self.wait(kind="approval", outcome="denied", seconds=42)
        (row,) = self.rows()
        self.assertEqual((row["src"], row["what"], row["wait"], row["outcome"], row["store_id"]), ("chat", "chat_wait", "approval", "denied", "r1"))
        self.assertIn(row["wait_s"], (42, 43))
        self.assertEqual(plugin._waits, {})

    def test_neither_the_prompt_nor_the_answer_reaches_the_log(self):
        self.switch()
        self.wait()
        self.assertNotIn("password", Path(os.environ["CALLING_MOM_LOG"]).read_text())

    def test_every_kind_and_an_odd_value_are_words_of_a_short_list(self):
        self.switch()
        for kind in ("clarify", "approval", "sudo", "something new", ""):
            self.wait(kind=kind, request_id=f"r-{kind}", outcome="Weird Outcome!")
        self.assertEqual([r["wait"] for r in self.rows()], ["clarify", "approval", "sudo", "other", "other"])
        self.assertEqual({r["outcome"] for r in self.rows()}, {"other"})

    def test_a_wait_that_started_before_this_process_has_no_duration(self):
        self.switch()
        plugin.on_human_input_resolved(kind="sudo", request_id="old", outcome="timeout")
        (row,) = self.rows()
        self.assertNotIn("wait_s", row)

    def test_the_kill_switch_counts_nothing(self):
        self.switch()
        os.environ["CALLING_MOM"] = "off"
        self.wait()
        self.assertEqual(self.rows(), [])

    def test_open_waits_are_bounded(self):
        self.switch()
        for i in range(150):
            plugin.on_human_input_request(request_id=f"r{i}", kind="clarify")
        self.assertLessEqual(len(plugin._waits), 100)
        self.assertIn("r149", plugin._waits)

    def test_a_request_without_an_id_is_ignored(self):
        self.switch()
        plugin.on_human_input_request(kind="clarify")
        self.assertEqual(plugin._waits, {})


if __name__ == "__main__":
    unittest.main()
