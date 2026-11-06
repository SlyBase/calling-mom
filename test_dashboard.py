"""Tests for the backend of the Desktop half (dashboard/plugin_api.py), over HTTP like the app calls it.

    cd <plugin dir> && python -m unittest -v test_dashboard

Reuses the fixtures of test_calling_mom: one temporary kanban DB (board ``t-board``), one log, one state file,
a fake Mom. The route module is pointed at the same plugin instance, so a test sets config on ONE object.
"""
import importlib.util
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import test_calling_mom as base

PLUGIN_DIR = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("calling_mom_dashboard_under_test", PLUGIN_DIR / "dashboard" / "plugin_api.py")
api = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(api)

plugin, kb, with_conn = base.plugin, base.kb, base.with_conn
CMD = "git push --force origin main"


class DashboardFixture(base.QuestionFixture):
    """A board where Mom answers first, the route module wired to the test plugin, an HTTP client."""

    def setUp(self):
        self._db = os.environ["HERMES_KANBAN_DB"]  # a board of its own per test: a list must not show another test's cards
        os.environ["HERMES_KANBAN_DB"] = str(Path(tempfile.mkdtemp()) / "kanban.db")
        base.kbc.init_db(Path(os.environ["HERMES_KANBAN_DB"]))
        super().setUp()
        self._plugin, api._plugin = api._plugin, plugin
        self._slugs, plugin._core.list_board_slugs = plugin._core.list_board_slugs, lambda: ["t-board"]
        app = FastAPI()
        app.include_router(api.router)
        self.client = TestClient(app)

    def tearDown(self):
        api._plugin = self._plugin
        plugin._core.list_board_slugs = self._slugs
        super().tearDown()
        os.environ["HERMES_KANBAN_DB"] = self._db

    def put(self, kind=None, ts=None, **fields):
        """Append one row to the observation log, like the hooks do (``ts`` defaults to now)."""
        path = Path(os.environ["CALLING_MOM_LOG"])
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {"ts": int(ts if ts is not None else time.time()), **({"kind": kind} if kind else {}), **fields}
        with path.open("a") as fh:
            fh.write(json.dumps(row) + "\n")

    def get(self, path, **params):
        res = self.client.get(path, params=params)
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def brief_card(self):
        """A card Mom asked a person about: blocked, with a `[for you]` brief."""
        self.llm._res.text = base.BRIEF
        return self.ask()

    def hold_card(self, reason="it rewrites shared history", alternative="push a branch"):
        tid = self.card()
        with_conn(lambda c: kb.block_task(c, tid, reason="may I force push?", kind="needs_input"))
        plugin._record_hold(tid, plugin.cmd_hash(CMD), reason, alternative)
        return tid


class LoaderTest(unittest.TestCase):
    def test_the_plugin_module_is_loaded_once_from_the_package_next_to_the_dashboard_folder(self):
        saved, api._plugin = api._plugin, None
        try:
            module = api.mom()
            self.assertIs(api.mom(), module)
            self.assertTrue(callable(module.answer_card))
            self.assertEqual(Path(module.__file__).resolve(), PLUGIN_DIR / "__init__.py")
        finally:
            api._plugin = saved


class SummaryTest(DashboardFixture, unittest.TestCase):
    """GET /summary: counts per rung of the ladder, from the plugin's own log."""

    def test_an_empty_log_counts_zero_everywhere(self):
        out = self.get("/summary")
        self.assertEqual((out["worker"]["tool_calls"], out["worker"]["signals"]), (0, 0))
        self.assertEqual(out["mom"], {"calls": 0, "answered": 0, "checked": 0, "holds": 0, "verified": 0, "verify_failed": 0,
                                      "tips_followed": 0, "tips_rejected": 0})
        self.assertEqual(out["olymp"]["calls"], 0)
        self.assertEqual((out["you"]["briefs"], out["you"]["answered"]), (0, 0))
        self.assertEqual((out["days"], out["recent"], out["today"]), (7, [], {"mom_calls": 0}))

    def test_every_rung_is_counted_from_the_rows_it_wrote(self):
        for sigs in ([], ["same_error"], ["loop", "steps"]):
            self.put(tool="terminal", ok=True, signals=sigs, task="t_a")
        self.put("advisor", task="t_a", category="error", olymp=False)
        self.put("advisor", task="t_a", category="error", olymp=False, noop=True)   # Mom looked and said nothing: still a call
        self.put("advisor", task="t_a", category="architecture", olymp=True)
        self.put("preflight", task="t_a", verdict="go")
        self.put("preflight", task="t_a", verdict="hold")
        self.put("preflight", task="t_a", verdict="held_again")                      # no model call
        self.put("preflight", task="t_a", verdict="allowed", reason="cap")           # no model call
        self.put("question", task="t_a", outcome="answered", by="mom")
        self.put("question", task="t_a", outcome="human", by="mom")
        self.put("question", task="t_a", outcome="answered", by="olymp")             # Mom escalated, Olymp answered
        self.put("question", task="t_a", outcome="skipped", reason="cap")
        self.put("question", task="t_a", outcome="error", reason="timeout")
        self.put("verify", task="t_a", state="started")
        self.put("verify", task="t_a", state="pass")
        self.put("verify", task="t_a", state="fail")
        self.put("verify", task="t_a", state="timeout")
        self.put("human_answer", task="t_a", via="desktop")
        self.put("human_answer", task="t_a", via="card")
        out = self.get("/summary")
        self.assertEqual((out["worker"]["tool_calls"], out["worker"]["signals"]), (3, 3))
        # Mom: 3 advice (the Olymp one was asked of her first) + 2 pre-flight + 3 questions
        self.assertEqual(out["mom"], {"calls": 8, "answered": 1, "checked": 2, "holds": 1, "verified": 1, "verify_failed": 2,
                                      "tips_followed": 0, "tips_rejected": 0})
        self.assertEqual(out["olymp"]["calls"], 2)
        self.assertEqual((out["you"]["briefs"], out["you"]["answered"]), (1, 2))

    def test_the_route_and_the_cli_report_show_the_same_numbers(self):
        self.put(tool="terminal", ok=True, signals=["loop"], task="t_a", run="1")
        self.put("worker_usage", task="t_a", run="1", tokens_in=100, tokens_out=10)
        self.put("advisor", task="t_a", olymp=False, tokens_in=10, tokens_out=1, usd=0.01)
        self.put("question", task="t_a", outcome="human", by="mom", tokens_in=20, tokens_out=2, usd=0.02)
        self.put("human_answer", task="t_a", via="cli", allow_once=True, latency_s=30)
        out, cli_report = self.get("/summary"), plugin.attention_report(7)
        for key in ("days", "board", "worker", "mom", "olymp", "cost"):
            self.assertEqual(out[key], cli_report[key], key)
        self.assertEqual(out["you"], {**cli_report["you"], "waiting": 0})
        self.assertEqual(out["cost"]["all_on_mom_usd"], round(110 * 0.03 / 33, 6))

    def test_the_window_is_the_last_n_days(self):
        self.put("advisor", ts=time.time() - 8 * 86400, task="t_a", category="error")
        self.put("advisor", task="t_a", category="error")
        self.assertEqual(self.get("/summary")["mom"]["calls"], 1)
        self.assertEqual(self.get("/summary", days=30)["mom"]["calls"], 2)
        self.assertEqual(self.get("/summary", days=30)["days"], 30)

    def test_today_starts_at_utc_midnight(self):
        midnight = int(time.time()) // 86400 * 86400
        self.put("advisor", ts=midnight - 5, task="t_a", category="error")
        self.put("advisor", ts=midnight + 1, task="t_a", category="error")
        self.put("advisor", ts=time.time(), task="t_a", category="error")
        out = self.get("/summary")
        self.assertEqual((out["mom"]["calls"], out["today"]["mom_calls"]), (3, 2))

    def test_days_must_be_between_1_and_90(self):
        for days in (0, 91, "x"):
            with self.subTest(days=days):
                self.assertEqual(self.client.get("/summary", params={"days": days}).status_code, 422)

    def test_olymp_shows_the_calls_of_today_against_its_daily_cap(self):
        plugin._add_spend(0.0, 0, olymp=2)
        self.assertEqual(self.get("/summary")["olymp"], {"calls": 0, "today": 2, "cap": plugin.settings()["olymp"]["max_per_day"]})

    def test_a_broken_log_line_and_a_missing_log_do_not_hurt(self):
        self.put("advisor", task="t_a", category="error")
        with Path(os.environ["CALLING_MOM_LOG"]).open("a") as fh:
            fh.write("not json\n[1, 2]\n")
        self.assertEqual(self.get("/summary")["mom"]["calls"], 1)
        Path(os.environ["CALLING_MOM_LOG"]).unlink()
        self.assertEqual(self.get("/summary")["mom"]["calls"], 0)

    def test_recent_activity_is_the_last_ten_rows_worth_a_line_newest_first_without_prose(self):
        t0 = int(time.time()) - 5000
        for i in range(12):
            self.put("advisor", ts=t0 + i, task=f"t_{i}", category="error", olymp=False, model="m", usd=0.1)
        self.put("advisor", ts=t0 + 1000, task="t_noop", noop=True)
        self.put("preflight", ts=t0 + 1001, task="t_p", verdict="allowed", reason="cap")
        self.put("question", ts=t0 + 1002, task="t_q", board="t-board", outcome="human", by="mom", tokens_in=5)
        recent = self.get("/summary", days=90)["recent"]
        self.assertEqual(len(recent), 10)
        self.assertEqual([r["task"] for r in recent][:3], ["t_q", "t_11", "t_10"])
        self.assertEqual(recent[0], {"ts": t0 + 1002, "kind": "question", "task": "t_q", "board": "t-board", "detail": "human", "by": "mom", "title": None})
        self.assertEqual(set(recent[1]), {"ts", "kind", "task", "board", "detail", "by", "title"})

    def test_each_kind_has_a_detail_and_a_name(self):
        t0 = int(time.time()) - 5000
        self.put("advisor", ts=t0, task="t_1", category="loop", olymp=True)
        self.put("preflight", ts=t0 + 1, task="t_2", verdict="hold")
        self.put("verify", ts=t0 + 2, task="t_3", state="fail")
        self.put("human_answer", ts=t0 + 3, task="t_4", via="desktop")
        self.put("escalated", ts=t0 + 4, task="t_5", reason="blocked")
        self.put("verify", ts=t0 + 5, task="t_6", state="stale")
        rows = {r["task"]: (r["kind"], r["detail"], r["by"]) for r in self.get("/summary", days=90)["recent"]}
        self.assertEqual(rows, {"t_1": ("advisor", "loop", "olymp"), "t_2": ("preflight", "hold", None), "t_3": ("verify", "fail", None),
                                "t_4": ("human_answer", "desktop", None), "t_5": ("escalated", "blocked", None)})


class NeedsYouTest(DashboardFixture, unittest.TestCase):
    """GET /needs-you: blocked cards on opted-in boards that carry a `[for you]` brief or a pre-flight hold."""

    def test_nothing_waits_when_nothing_was_asked(self):
        self.assertEqual(self.get("/needs-you"), [])
        self.assertEqual(self.get("/summary")["you"]["waiting"], 0)

    def test_a_brief_shows_its_text_without_the_marker_and_can_be_accepted(self):
        tid = self.brief_card()
        (item,) = self.get("/needs-you")
        self.assertEqual((item["board"], item["task_id"], item["title"]), ("t-board", tid, "fix the parser"))
        self.assertEqual(item["brief"], base.BRIEF_COMMENT.removeprefix("[for you] "))
        self.assertEqual((item["holds"], item["can_accept"]), ([], True))
        self.assertIsInstance(item["age_s"], int)
        self.assertLess(item["age_s"], 60)
        self.assertEqual(self.get("/summary")["you"]["waiting"], 1)

    def test_a_hold_shows_why_and_what_to_do_instead_and_cannot_be_accepted(self):
        tid = self.hold_card()
        (item,) = self.get("/needs-you")
        self.assertEqual(item["task_id"], tid)
        self.assertIsNone(item["brief"])
        self.assertEqual(item["holds"], [{"reason": "it rewrites shared history", "alternative": "push a branch"}])
        self.assertFalse(item["can_accept"])

    def test_a_card_with_a_brief_and_a_hold_is_one_item(self):
        tid = self.brief_card()
        plugin._record_hold(tid, plugin.cmd_hash(CMD), "it rewrites shared history", "push a branch")
        (item,) = self.get("/needs-you")
        self.assertEqual((bool(item["brief"]), len(item["holds"])), (True, 1))

    def test_a_card_waits_since_the_oldest_of_its_brief_and_its_holds(self):
        tid = self.brief_card()
        plugin._record_hold(tid, plugin.cmd_hash(CMD), "r", "a")
        state = plugin._load_holds()
        state["holds"][tid][plugin.cmd_hash(CMD)]["ts"] = int(time.time()) - 3600
        plugin._write_state(state)
        self.assertGreaterEqual(self.get("/needs-you")[0]["age_s"], 3600)

    def test_a_hold_on_a_card_that_is_not_blocked_waits_for_nobody(self):
        tid = self.card()
        plugin._record_hold(tid, plugin.cmd_hash(CMD), "r", "a")        # the worker was held but carries on
        self.assertEqual(self.get("/needs-you"), [])

    def test_a_brief_without_a_recommendation_cannot_be_accepted(self):
        tid = self.card()
        with_conn(lambda c: kb.block_task(c, tid, reason="x", kind="needs_input"))
        with_conn(lambda c: kb.add_comment(c, tid, "worker", "[for you] Question: what? (a brief by hand)"))
        self.assertFalse(self.get("/needs-you")[0]["can_accept"])

    def test_cards_that_are_not_waiting_for_a_person_are_left_out(self):
        plain = self.card()
        with_conn(lambda c: kb.block_task(c, plain, reason="x", kind="needs_input"))              # blocked, no brief, no hold
        running = self.card()
        with_conn(lambda c: kb.add_comment(c, running, "worker", "[for you] Question: old brief"))  # brief, but not blocked
        self.assertEqual(self.get("/needs-you"), [])

    def test_a_brief_that_was_answered_does_not_come_back_when_the_card_blocks_again(self):
        tid = self.brief_card()
        self.assertTrue(plugin.answer_card("t-board", tid, "Use B.", by="desktop")["ok"])
        with_conn(lambda c: c.execute("UPDATE tasks SET status = 'blocked', block_kind = 'needs_input' WHERE id = ?", (tid,)).connection.commit())
        self.assertEqual(self.status(tid), "blocked")        # Hermes would park a second block in triage; the fixture forces it
        self.assertEqual(self.get("/needs-you"), [])

    def test_a_hold_a_person_already_allowed_is_not_waiting(self):
        tid = self.hold_card()
        plugin.allow_once("t-board", tid, plugin.cmd_hash(CMD), by="desktop")
        self.assertEqual(self.get("/needs-you"), [])

    def test_the_oldest_wait_comes_first(self):
        young = self.hold_card()
        old = self.hold_card()
        state = plugin._load_holds()
        state["holds"][old][plugin.cmd_hash(CMD)]["ts"] = int(time.time()) - 3600
        plugin._write_state(state)
        items = self.get("/needs-you")
        self.assertEqual([i["task_id"] for i in items], [old, young])
        self.assertGreaterEqual(items[0]["age_s"], 3600)

    def test_only_boards_that_opted_in_are_read(self):
        self.brief_card()
        plugin.DEFAULTS["answer"].update(boards=["somewhere-else"])
        plugin.DEFAULTS["advisor"].update(boards=[])
        read = []
        board = api._board
        api._board = lambda b: read.append(b) or board(b)
        try:
            self.assertEqual(self.get("/needs-you"), [])
        finally:
            api._board = board
        self.assertEqual(read, [])


class CardsTest(DashboardFixture, unittest.TestCase):
    """GET /cards: per card at most two badge names for the Desktop board. Names only, no prose."""

    def test_a_card_without_plugin_activity_has_no_entry(self):
        self.card()
        self.put(tool="terminal", ok=True, task=self.tid, signals=[])
        self.assertEqual(self.get("/cards"), {})

    def test_the_log_decides_mom_olymp_and_verify(self):
        a, b, c, d, e = (self.card() for _ in range(5))
        self.put("advisor", task=a, category="error")
        self.put("advisor", task=b, category="architecture", olymp=True)
        self.put("question", task=c, outcome="answered", by="olymp")
        self.put("verify", task=d, state="pass")
        self.put("verify", task=e, state="fail")
        self.put("advisor", task=self.tid, noop=True)                    # Mom looked and had nothing to say
        self.put("preflight", task=self.tid, verdict="allowed", reason="cap")
        self.assertEqual(self.get("/cards"), {a: ["mom"], b: ["olymp"], c: ["olymp"], d: ["verified"], e: ["verify failed"]})

    def test_a_question_answered_by_mom_and_a_preflight_check_count_as_mom(self):
        a, b, c = (self.card() for _ in range(3))
        self.put("question", task=a, outcome="answered", by="mom")
        self.put("question", task=b, outcome="human", by="mom")
        self.put("preflight", task=c, verdict="go")
        self.assertEqual(self.get("/cards"), {a: ["mom"], b: ["mom"], c: ["mom"]})

    def test_the_last_verify_result_wins(self):
        a, b = self.card(), self.card()
        for state in ("pass", "started", "fail"):
            self.put("verify", task=a, state=state)
        for state in ("fail", "stale", "pass"):
            self.put("verify", task=b, state=state)
        self.assertEqual(self.get("/cards"), {a: ["verify failed"], b: ["verified"]})

    def test_a_brief_shows_for_you_and_a_hold_shows_held(self):
        brief, held = self.brief_card(), self.hold_card()
        self.assertEqual(self.get("/cards"), {brief: ["for you", "mom"], held: ["held"]})   # Mom wrote the brief

    def test_a_card_shows_at_most_two_badges_the_most_urgent_first(self):
        tid = self.brief_card()
        plugin._record_hold(tid, plugin.cmd_hash(CMD), "r", "a")
        self.put("advisor", task=tid, category="error", olymp=True)
        self.put("advisor", task=tid, category="error")
        self.put("verify", task=tid, state="fail")
        self.assertEqual(self.get("/cards")[tid], ["held", "for you"])
        plugin.DEFAULTS["answer"].update(boards=[])                      # not opted in: nothing waits, the log speaks
        plugin.DEFAULTS["advisor"].update(boards=[])
        self.assertEqual(self.get("/cards")[tid], ["verify failed", "olymp"])

    def test_one_board_only_when_asked(self):
        other = {"t-board": [self.card()], "other": [self.card()]}
        for tid in (t for ids in other.values() for t in ids):
            self.put("advisor", task=tid, category="error")
        board = api._board
        api._board = lambda b: [(t, c) for t, c in board("t-board") if t.id in other[b]]
        plugin._core.list_board_slugs = lambda: ["t-board", "other"]
        try:
            self.assertEqual(self.get("/cards", board="other"), {other["other"][0]: ["mom"]})
            self.assertEqual(self.get("/cards", board="t-board"), {other["t-board"][0]: ["mom"]})
            self.assertEqual(set(self.get("/cards")), {other["t-board"][0], other["other"][0]})
            self.assertEqual(self.get("/cards", board="nowhere"), {})
        finally:
            api._board = board

    def test_cards_of_a_board_that_did_not_opt_in_get_log_badges_only(self):
        tid = self.brief_card()
        plugin.DEFAULTS["answer"].update(boards=[])
        plugin.DEFAULTS["advisor"].update(boards=[])
        self.assertEqual(self.get("/cards"), {tid: ["mom"]})              # Mom wrote the brief; nobody is asked on this board

    def test_no_prose_leaves_this_route(self):
        self.brief_card()
        self.assertNotIn("Okta", self.client.get("/cards").text)


class AnswerRouteTest(DashboardFixture, unittest.TestCase):
    """POST /answer: a person's click, as the page sends it. It is `answer_card(..., by="desktop")` and nothing else."""

    def setUp(self):
        super().setUp()
        self.tid = self.brief_card()

    def post(self, **body):
        body.setdefault("board", "t-board")
        body.setdefault("task_id", self.tid)
        return self.client.post("/answer", json=body)

    def test_accept_writes_the_recommendation_as_the_persons_answer_and_the_card_continues(self):
        res = self.post(accept=True)
        self.assertEqual((res.status_code, res.json()["ok"], res.json()["text"]), (200, True, "B, it is cheaper."))
        self.assertEqual(self.comments()[-1], "[you via calling-mom] B, it is cheaper.")
        self.assertEqual(self.status(), "ready")
        self.assertEqual(self.get("/needs-you"), [])

    def test_a_typed_answer_is_written_as_typed(self):
        self.assertEqual(self.post(text="Use A, Okta is licensed.").status_code, 200)
        self.assertEqual(self.comments()[-1], "[you via calling-mom] Use A, Okta is licensed.")

    def test_allow_once_releases_a_held_command(self):
        tid = self.hold_card()
        res = self.post(task_id=tid, allow_once=True)
        self.assertEqual(res.status_code, 200)
        self.assertIn(plugin.cmd_hash(CMD), plugin._load_holds()["allowances"][tid])
        self.assertEqual(self.status(tid), "ready")

    def test_the_log_says_it_came_from_the_desktop_whatever_the_client_claims(self):
        self.post(accept=True, by="cli")
        (row,) = base.log_rows("human_answer")
        self.assertEqual((row["via"], row["accepted"], row["task"]), ("desktop", True, self.tid))

    def test_the_errors_of_answer_card_keep_their_status_and_change_nothing(self):
        gone = self.card()
        rows = [  # (case, body, status)
            ("nothing to do", dict(), 400),
            ("text and accept", dict(text="x", accept=True), 400),
            ("text too long", dict(text="x" * 2001), 400),
            ("card is not blocked", dict(task_id=gone, text="x"), 409),
            ("no such card", dict(task_id="t_missing", text="x"), 404),
            ("board did not opt in", dict(text="x", board="nowhere"), 404),
            ("allow_once without a hold", dict(allow_once=True), 409),
        ]
        for name, body, status in rows:
            with self.subTest(case=name):
                res = self.post(**body)
                self.assertEqual(res.status_code, status, res.text)
                self.assertTrue(res.json()["detail"])
        self.assertEqual(self.status(), "blocked")
        self.assertEqual(base.log_rows("human_answer"), [])

    def test_a_board_that_does_not_exist_is_refused_before_anything_opens_it(self):
        plugin.DEFAULTS["answer"].update(boards=["*"])                   # "*" opts every board in, so only the name check stops it
        res = self.post(board="../../etc", text="x")
        self.assertEqual((res.status_code, res.json()["detail"]), (404, "no board '../../etc'"))


class ActiveTest(DashboardFixture, unittest.TestCase):
    """GET /active: running/ready cards on opted-in boards with their last comment, newest last-activity first."""

    def set_status(self, tid, status):
        with_conn(lambda c: c.execute("UPDATE tasks SET status = ? WHERE id = ?", (status, tid)).connection.commit())

    def active(self, tid):
        """The /active item for one card (the fixture's base card is also running, so filter by id)."""
        return [i for i in self.get("/active") if i["task_id"] == tid]

    def test_a_running_card_with_a_comment_shows_its_title_board_status_and_last_comment(self):
        tid = self.card(title="ship the parser")
        self.set_status(tid, "running")
        with_conn(lambda c: kb.add_comment(c, tid, "worker", "Parser done, running tests."))
        (item,) = self.active(tid)
        self.assertEqual((item["board"], item["task_id"], item["title"], item["status"]), ("t-board", tid, "ship the parser", "running"))
        self.assertEqual(item["last_comment"], {"author": "worker", "body": "Parser done, running tests.", "created_at": item["last_comment"]["created_at"]})
        self.assertIsInstance(item["last_comment"]["created_at"], int)

    def test_a_ready_card_with_no_comment_has_a_null_last_comment(self):
        tid = self.card(title="fresh card")
        self.set_status(tid, "ready")
        (item,) = self.active(tid)
        self.assertEqual((item["status"], item["last_comment"]), ("ready", None))

    def test_only_running_and_ready_cards_are_active(self):
        for status, active in (("running", True), ("ready", True), ("blocked", False), ("todo", False), ("done", False)):
            tid = self.card()
            self.set_status(tid, status)
            self.assertEqual(bool(self.active(tid)), active, status)

    def test_a_card_with_comments_shows_the_last_one_and_truncates_the_body(self):
        tid = self.card()
        with_conn(lambda c: kb.add_comment(c, tid, "worker", "first note"))
        with_conn(lambda c: kb.add_comment(c, tid, "worker", "x" * 300))
        (item,) = self.active(tid)
        self.assertEqual(item["last_comment"]["body"], "x" * 200)

    def test_a_known_prefix_on_the_last_comment_is_kept_as_is(self):
        tid = self.card()
        with_conn(lambda c: kb.add_comment(c, tid, "worker", "[pre-flight] HOLD: it rewrites shared history"))
        (item,) = self.active(tid)
        self.assertEqual(item["last_comment"]["body"], "[pre-flight] HOLD: it rewrites shared history")

    def test_the_newest_last_activity_comes_first(self):
        now = int(time.time())
        old = self.card()
        self.set_status(old, "ready")
        young = self.card()
        self.set_status(young, "running")
        with_conn(lambda c: kb.add_comment(c, old, "worker", "old note"))
        with_conn(lambda c: kb.add_comment(c, young, "worker", "young note"))
        with_conn(lambda c: c.execute("UPDATE task_comments SET created_at = ? WHERE task_id = ?", (now - 1000, old)).connection.commit())
        with_conn(lambda c: c.execute("UPDATE tasks SET created_at = ? WHERE id = ?", (now - 2000, old)).connection.commit())
        active = self.get("/active")
        self.assertLess(active.index([i for i in active if i["task_id"] == young][0]),
                        active.index([i for i in active if i["task_id"] == old][0]))

    def test_only_boards_that_opted_in_are_read(self):
        self.card()
        plugin.DEFAULTS["answer"].update(boards=["somewhere-else"])
        plugin.DEFAULTS["advisor"].update(boards=[])
        self.assertEqual(self.get("/active"), [])


class RecentIdentityTest(DashboardFixture, unittest.TestCase):
    """GET /summary recent rows carry a card title, joined from the boards (null when the card is gone)."""

    def test_a_recent_row_shows_the_card_title_and_falls_back_when_the_card_is_missing(self):
        tid = self.card(title="ship the parser")
        self.put("advisor", task=tid, category="error")
        self.put("advisor", task="t_gone", category="error")   # a log row for a card that is no longer on the board
        rows = {r["task"]: r["title"] for r in self.get("/summary", days=90)["recent"]}
        self.assertEqual(rows.get(tid), "ship the parser")
        self.assertIsNone(rows.get("t_gone"))

    def test_the_body_must_have_the_right_types(self):
        for body in ({"task_id": self.tid}, {"board": "t-board"}, {"board": "t-board", "task_id": self.tid, "text": 5},
                     {"board": "t-board", "task_id": self.tid, "accept": "maybe"}):
            with self.subTest(body=body):
                self.assertEqual(self.client.post("/answer", json=body).status_code, 422)
