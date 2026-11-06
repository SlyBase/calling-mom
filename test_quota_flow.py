"""Tests for quota awareness, the Mom fallback, stalled cards and pace (WP2 of 1.5), through the plugin.

    cd <plugin dir> && python -m unittest -v test_quota_flow
"""
import calendar
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

import quota
import test_calling_mom as base

plugin, kb, with_conn = base.plugin, base.kb, base.with_conn
BOARD = "t-board"
MOM = "openrouter"      # DEFAULTS["provider"]
SUB = "claude-subscription-directsdk-experimental"
KEY_LIMIT = ("Error code: 403 - {'error': {'message': 'Key limit exceeded (total limit). Manage it using "
             "https://openrouter.ai/workspaces/default/keys/abc $42.48 of $10.00 used; does not reset.', 'code': 403}}")


def session_limit(ts):
    """The real Claude subscription message, with a reset time at ``ts``."""
    t = time.gmtime(ts)
    hour = t.tm_hour % 12 or 12
    return (f"Incomplete upstream response (first upstream attempt: status 429, upstream said: This request would exceed "
            f"your account's rate limit.): You've hit your session limit · resets {hour}:{t.tm_min:02d}{'pm' if t.tm_hour >= 12 else 'am'} (UTC)")


class RoutedLlm(base.FakeLlm):
    """A fake ``ctx.llm`` where a provider can be made to fail: ``fail = {provider: error text}``."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.fail = {}
        self.script = {}   # provider -> text

    def complete(self, messages, **kw):
        self.calls.append({"messages": messages, **kw})
        if kw["provider"] in self.fail:
            raise RuntimeError(self.fail[kw["provider"]])
        return NS(text=self.script.get(kw["provider"], self._res.text), model=kw["model"], provider=kw["provider"],
                  usage=self._res.usage)


class QuotaFixture(base.AdvisorFixture):
    """The advisor on for BOARD, its own state file, a fake LLM that can fail per provider, the process working for profile ``p1``."""

    PROFILE = "p1"

    def setUp(self):
        self._env = {k: os.environ.get(k) for k in ("CALLING_MOM_STATE", "HERMES_KANBAN_DB")}
        os.environ["HERMES_KANBAN_DB"] = str(Path(tempfile.mkdtemp()) / "kanban.db")   # a board of its own: the sweep lists every card
        base.kbc.init_db(Path(os.environ["HERMES_KANBAN_DB"]))
        os.environ["CALLING_MOM_STATE"] = str(Path(tempfile.mkdtemp()) / "state.json")
        super().setUp()
        self.llm = RoutedLlm()
        plugin._ctx.llm = self.llm
        self._profile = mock.patch.object(plugin._core, "profile_name", lambda: self.PROFILE)
        self._profile.start()
        self.addCleanup(self._profile.stop)

    def tearDown(self):
        super().tearDown()
        for k, v in self._env.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

    def rows(self, kind="quota", **want):
        return [r for r in base.log_rows(kind) if all(r.get(k) == v for k, v in want.items())]

    def state(self):
        path = Path(os.environ["CALLING_MOM_STATE"])
        return json.loads(path.read_text()) if path.exists() else {}

    def comments(self, tid=None):
        return [c.body for c in with_conn(lambda c: kb.list_comments(c, tid or self.tid))]

    def api_error(self, message, provider=MOM, status=429, reason="rate_limit", etype="ClaudeAPIError", model="m/x"):
        return plugin.on_api_error(provider=provider, model=model, error={"type": etype, "message": message},
                                   status_code=status, reason=reason)

    def limit(self, provider=MOM, hours=2):
        self.api_error(session_limit(time.time() + hours * 3600), provider=provider)

    def fallback(self, provider="fb", model="fb/model"):
        plugin.DEFAULTS["mom_fallback"].update(provider=provider, model=model)

    def providers(self):
        return [c["provider"] for c in self.llm.calls]


class ApiErrorTest(QuotaFixture, unittest.TestCase):
    def test_a_session_limit_marks_the_provider_until_its_reset_and_logs_a_row_without_text(self):
        message = session_limit(time.time() + 2 * 3600)
        self.api_error(message)
        entry = self.state()["health"]["p1"][MOM]
        self.assertEqual((entry["state"], entry["class"], entry["known"]), ("limited", "rate_limit", True))
        self.assertEqual(entry["until"], quota.parse_reset(message, time.time()))
        (row,) = self.rows()
        self.assertEqual((row["state"], row["src"], row["error_class"], row["profile"], row["provider"], row["model"]),
                         ("limited", "api_error", "rate_limit", "p1", MOM, "m/x"))
        self.assertEqual((row["until"], row["task"], row["board"]), (entry["until"], self.tid, BOARD))
        for text in (Path(os.environ["CALLING_MOM_LOG"]).read_text(), json.dumps(self.state())):
            self.assertNotIn("session limit", text)
            self.assertNotIn("upstream", text)

    def test_a_rate_limit_without_a_reset_time_is_a_guess_of_30_minutes(self):
        self.api_error("rate limited")
        entry = self.state()["health"]["p1"][MOM]
        self.assertFalse(entry["known"])
        self.assertAlmostEqual(entry["until"], time.time() + quota.LIMIT_UNKNOWN_S, delta=5)
        self.assertIsNone(self.rows()[0]["until"])        # the row says "unknown", the state keeps the guess

    def test_an_openrouter_key_limit_is_down_for_30_minutes(self):
        self.api_error(KEY_LIMIT, status=403, reason="auth", etype="PermissionDeniedError")
        entry = self.state()["health"]["p1"][MOM]
        self.assertEqual((entry["state"], entry["class"]), ("down", "key_limit"))
        self.assertAlmostEqual(entry["until"], time.time() + quota.DOWN_S, delta=5)
        self.assertEqual(self.rows()[0]["error_class"], "key_limit")
        self.assertNotIn("openrouter.ai", Path(os.environ["CALLING_MOM_LOG"]).read_text() + json.dumps(self.state()))

    def test_claude_logged_out_is_down_for_auth(self):
        self.api_error("", provider=SUB, status=None, reason=None, etype="ClaudeCodeLoggedOut")
        self.assertEqual(self.state()["health"]["p1"][SUB]["class"], "auth")

    def test_the_same_news_again_leaves_one_row(self):
        self.limit()
        self.limit()
        self.assertEqual(len(self.rows()), 1)

    def test_an_error_that_is_not_a_quota_error_changes_nothing(self):
        self.api_error("boom", status=500, reason="server_error")
        self.assertEqual(self.rows(), [])
        self.assertNotIn("health", self.state())

    def test_only_worker_sessions_of_opted_in_boards_count(self):
        os.environ["HERMES_KANBAN_BOARD"] = "elsewhere"
        self.limit()
        os.environ["HERMES_KANBAN_BOARD"] = BOARD
        del os.environ["HERMES_KANBAN_TASK"]
        self.limit()
        os.environ["HERMES_KANBAN_TASK"] = self.tid
        os.environ["CALLING_MOM"] = "off"
        self.limit()
        os.environ["CALLING_MOM"] = "on"
        plugin.on_api_error(provider=None, error={"type": "E", "message": "x"}, status_code=429)
        self.assertEqual(self.rows(), [])

    def test_a_payload_without_an_error_dict_is_other(self):
        plugin.on_api_error(provider=MOM, error=None, status_code=None, reason=None)
        self.assertEqual(self.rows(), [])


class SkipTest(QuotaFixture, unittest.TestCase):
    def test_mom_is_not_called_while_her_provider_is_limited_and_the_card_says_until_when(self):
        self.limit()
        until = self.state()["health"]["p1"][MOM]["until"]
        self.assertIsNone(self.stuck(3))
        self.assertEqual(self.llm.calls, [])
        (body,) = self.comments()
        self.assertTrue(body.startswith("[advisor unavailable]"))
        self.assertIn(f"{MOM} is limited until {quota.clock(until)} UTC", body)
        self.assertEqual(len(self.rows(state="skipped")), 1)
        self.assertEqual(self.rows(state="skipped")[0]["tier"], "mom")
        self.assertEqual(base.log_rows("advisor_error"), [])      # a skipped call is no failed call

    def test_the_comment_is_written_once_per_card(self):
        self.limit()
        self.stuck(3)
        plugin._trackers.clear()
        self.stuck(3)
        self.assertEqual(len(self.comments()), 1)
        self.assertEqual(len(self.rows(state="skipped")), 2)

    def test_another_profile_is_not_silenced(self):
        self.limit()
        with mock.patch.object(plugin._core, "profile_name", lambda: "p2"):
            self.assertIn("[ADVISOR", self.stuck(3))
        self.assertEqual(len(self.llm.calls), 1)

    def test_a_failing_call_with_a_key_limit_marks_the_provider_and_the_next_call_is_skipped(self):
        self.llm.fail[MOM] = KEY_LIMIT
        self.stuck(3)
        self.assertEqual(self.state()["health"]["p1"][MOM]["state"], "down")
        self.assertEqual(self.rows()[0]["src"], "call")
        plugin._down.clear()                     # not the 5 minute cooldown: the health decides
        plugin._trackers.clear()
        self.stuck(3)
        self.assertEqual(len(self.llm.calls), 1)
        self.assertNotIn("Key limit", Path(os.environ["CALLING_MOM_LOG"]).read_text() + json.dumps(self.state()))

    def test_an_ended_limit_is_logged_as_recovered_and_the_call_goes_out(self):
        self.limit()
        data = self.state()
        data["health"]["p1"][MOM]["until"] = int(time.time()) - 5
        Path(os.environ["CALLING_MOM_STATE"]).write_text(json.dumps(data))
        self.assertIn("[ADVISOR", self.stuck(3))
        (row,) = self.rows(state="recovered")
        self.assertEqual((row["provider"], row["profile"]), (MOM, "p1"))
        self.assertNotIn("health", self.state())

    def test_a_timeout_is_not_a_quota_event(self):
        with mock.patch.object(plugin, "_complete_within", side_effect=plugin._Timeout()):
            with self.assertRaises(plugin._Timeout):
                plugin._ask_model({"provider": MOM, "model": "m"}, [], 8, 1, "x")
        self.assertEqual(self.rows(), [])


class HealthLinesTest(QuotaFixture, unittest.TestCase):
    def test_status_and_doctor_see_what_is_limited_and_for_which_profile(self):
        self.assertEqual(plugin.provider_health_lines(), [])
        self.limit()
        until = self.state()["health"]["p1"][MOM]["until"]
        self.assertEqual(plugin.provider_health_lines(), [f"{MOM} limited until {quota.clock(until)} UTC (profile p1)"])


class FallbackTest(QuotaFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.fallback()

    def test_the_fallback_answers_when_mom_is_limited_and_the_header_says_so(self):
        self.limit()
        out = self.stuck(3)
        self.assertEqual(self.providers(), ["fb"])
        self.assertIn("[ADVISOR fb/model]", out)
        (body,) = self.comments()
        self.assertRegex(body, r"^\[error/same_error\] run 1 · fb/model \(fb, fallback\): ")
        self.assertEqual(len(self.rows(state="fallback")), 1)
        self.assertEqual(base.log_rows("advisor")[0]["provider"], "fb")
        self.assertGreater(plugin._spent_today()["tokens"], 0)         # Mom's caps count it

    def test_a_failing_mom_call_is_asked_again_on_the_fallback(self):
        self.llm.fail[MOM] = "provider down"
        out = self.stuck(3)
        self.assertEqual(self.providers(), [MOM, "fb"])
        self.assertIn("[ADVISOR fb/model]", out)
        self.assertIn("(fb, fallback)", self.comments()[0])

    def test_olymp_has_no_fallback(self):
        plugin.DEFAULTS["olymp"].update(model="opus", provider=SUB)
        self.limit(provider=SUB)
        self.llm.script[MOM] = "ESCALATE: irreversible"
        self.assertIsNone(plugin._advise(("%s" % self.tid, "1"), "architecture", "second_opinion", note="Question: x"))
        self.assertEqual(self.providers(), [MOM])           # Mom asked, Olymp skipped, the fallback never stands in
        (body,) = self.comments()
        self.assertIn(f"{SUB} is limited until", body)
        self.assertEqual(self.rows(state="skipped")[0]["tier"], "olymp")

    def test_without_a_working_fallback_the_card_hears_about_mom(self):
        self.limit()
        self.limit(provider="fb")
        self.stuck(3)
        self.assertEqual(self.llm.calls, [])
        self.assertIn(f"{MOM} is limited until", self.comments()[0])

    def test_a_fallback_that_fails_too_leaves_the_error_of_the_first_try(self):
        self.llm.fail[MOM] = "primary down"
        self.llm.fail["fb"] = "fallback down"
        self.stuck(3)
        self.assertEqual(self.providers(), [MOM, "fb"])
        self.assertIn("primary down", self.comments()[0])

    def test_the_cooldown_of_mom_hands_over_to_the_fallback(self):
        plugin._down[(MOM, plugin.DEFAULTS["model"])] = time.time()
        out = self.stuck(3)
        self.assertEqual(self.providers(), ["fb"])
        self.assertIn("fb/model", out)

    def test_no_fallback_configured_means_no_retry(self):
        plugin.DEFAULTS["mom_fallback"].update(provider="", model="")
        self.llm.fail[MOM] = "provider down"
        self.stuck(3)
        self.assertEqual(self.providers(), [MOM])

    def test_half_a_pair_is_no_fallback(self):
        plugin.DEFAULTS["mom_fallback"].update(provider="fb", model="")
        self.limit()
        self.stuck(3)
        self.assertEqual(self.llm.calls, [])


class FallbackPreflightTest(QuotaFixture, unittest.TestCase):
    CMD = "git push --force origin main"

    def test_preflight_uses_the_fallback_when_mom_is_down_and_never_retries_on_a_failure(self):
        self.fallback()
        self.llm._res.text = "GO"
        self.limit()
        self.assertIsNone(plugin.on_pre_tool(tool_name="terminal", args={"command": self.CMD}))
        self.assertEqual(self.providers(), ["fb"])
        (row,) = self.rows("preflight")
        self.assertEqual((row["verdict"], row["model"]), ("go", "fb/model"))

    def test_a_failing_preflight_call_is_allowed_without_a_second_call(self):
        self.fallback()
        self.llm.fail[MOM] = "provider down"
        self.assertIsNone(plugin.on_pre_tool(tool_name="terminal", args={"command": self.CMD}))
        self.assertEqual(self.providers(), [MOM])      # the hook is bounded: one call
        self.assertEqual(self.rows("preflight")[0]["reason"], "down")


class FallbackQuestionTest(QuotaFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        plugin.DEFAULTS["answer"].update(enabled=True, boards=[BOARD])
        self.llm.script = {MOM: "ANSWER: A", "fb": "ANSWER: B from the fallback"}

    def ask(self):
        with_conn(lambda c: kb.block_task(c, self.tid, reason="A or B?", kind="needs_input"))
        plugin.on_question(task_id=self.tid, board=BOARD, reason="A or B?", run_id=1)

    def test_the_fallback_answers_a_question_while_mom_is_limited(self):
        self.fallback()
        self.limit()
        self.ask()
        self.assertEqual(self.providers(), ["fb"])
        self.assertIn("[mom answered] B from the fallback", self.comments())
        self.assertEqual(self.rows("question")[0]["outcome"], "answered")

    def test_without_a_fallback_the_question_goes_to_a_person_and_says_limited(self):
        self.limit()
        self.ask()
        self.assertEqual(self.llm.calls, [])
        self.assertEqual(self.rows("question")[0]["reason"], "limited")
        self.assertEqual(self.rows("question")[0]["outcome"], "error")


class WorkerExitedTest(QuotaFixture, unittest.TestCase):
    PROFILE = "worker"

    def exited(self, outcome="rate_limited", board=BOARD, tid=None):
        plugin.on_worker_exited(task_id=tid or self.tid, board=board, outcome=outcome, assignee="worker", run_id=3)

    def quota_comments(self):
        return [c for c in self.comments() if c.startswith("[quota]")]

    def test_one_comment_per_card_and_limit_window_with_the_provider_and_reset(self):
        with_conn(lambda c: c.execute("UPDATE tasks SET workspace_path = '/work/space' WHERE id = ?", (self.tid,)) and c.commit())
        self.limit(provider=SUB)
        until = self.state()["health"]["worker"][SUB]["until"]
        self.exited()
        self.exited()
        (body,) = self.quota_comments()
        self.assertEqual(body, f"[quota] {SUB} hit its usage limit; resets about {quota.clock(until)} UTC. "
                               "The dispatcher retries by itself; the work in /work/space is kept.")
        (row,) = self.rows(src="exit")
        self.assertEqual((row["state"], row["task"], row["board"], row["provider"], row["error_class"]),
                         ("paused", self.tid, BOARD, SUB, "rate_limit"))
        self.assertEqual(row["until"], until)
        author = with_conn(lambda c: kb.list_comments(c, self.tid))[-1].author
        self.assertEqual(author, plugin.NOTICE_AUTHOR)

    def test_a_later_window_is_a_new_comment(self):
        self.limit(provider=SUB, hours=2)
        self.exited()
        self.limit(provider=SUB, hours=5)
        self.exited()
        self.assertEqual(len(self.quota_comments()), 2)

    def test_an_unknown_reset_says_so(self):
        self.api_error("rate limited", provider=SUB)
        self.exited()
        (body,) = self.quota_comments()
        self.assertIn(f"{SUB} hit its usage limit; reset time unknown.", body)
        self.assertIn("the work in its workspace is kept", body)
        self.assertIsNone(self.rows(src="exit")[0]["until"])

    def test_without_a_health_entry_the_card_pin_names_the_provider(self):
        with_conn(lambda c: kb.set_model_override(c, self.tid, "opus", provider=SUB))
        self.exited()
        self.assertIn(f"{SUB} hit its usage limit; reset time unknown", self.quota_comments()[0])
        self.assertEqual(self.state()["health"]["worker"][SUB]["state"], "limited")
        self.exited()
        self.assertEqual(len(self.quota_comments()), 1)

    def test_without_any_hint_it_says_the_provider(self):
        self.exited()
        self.exited()
        (body,) = self.quota_comments()
        self.assertTrue(body.startswith("[quota] The provider hit its usage limit; reset time unknown."))
        self.assertEqual(self.rows(src="exit", state="paused")[0]["provider"], None)

    def test_an_expired_or_down_entry_is_not_this_limit(self):
        self.api_error(KEY_LIMIT, provider=SUB, status=403, reason="auth", etype="PermissionDeniedError")
        self.exited()
        self.assertTrue(self.quota_comments()[0].startswith("[quota] The provider"))

    def test_a_board_that_did_not_opt_in_gets_nothing(self):
        self.exited(board="elsewhere")
        self.assertEqual(self.quota_comments(), [])

    def test_a_missing_card_and_a_missing_id_are_ignored(self):
        self.exited(tid="no-such-card")
        plugin.on_worker_exited(task_id=None, board=BOARD, outcome="rate_limited")
        self.assertEqual(self.rows(), [])

    def test_a_crash_stays_a_crash(self):
        self.exited(outcome="crashed")
        self.exited(outcome="completed")
        self.assertEqual(self.quota_comments(), [])
        self.assertEqual(self.rows(), [])

    def test_a_card_that_cannot_be_read_does_not_break_the_hook(self):
        with mock.patch.object(plugin, "_connect", side_effect=RuntimeError("db gone")):
            self.exited()


class StallFixture(QuotaFixture):
    def running(self, beat_ago, run=4, tid=None):
        tid = tid or self.tid
        now = int(time.time())
        with_conn(lambda c: c.execute(
            "UPDATE tasks SET status = 'running', last_heartbeat_at = ?, started_at = ?, current_run_id = ? WHERE id = ?",
            (now - beat_ago * 60, now - 3600, run, tid)) and c.commit())

    def sweep(self):
        plugin._last_scan.clear()
        plugin.DEFAULTS["stall_minutes"] = self._minutes
        return plugin.sweep(board=BOARD)

    _minutes = 15

    def stalled(self, tid=None):
        return [c for c in self.comments(tid) if c.startswith("[stalled]")]


class StalledTest(StallFixture, unittest.TestCase):
    def test_twenty_minutes_without_a_heartbeat_is_one_comment_and_one_row(self):
        self.running(20)
        self.sweep()
        (body,) = self.stalled()
        beat = with_conn(lambda c: kb.get_task(c, self.tid)).last_heartbeat_at
        self.assertEqual(body, f"[stalled] no heartbeat since {quota.clock(beat)} UTC (run 4). The worker is probably gone, "
                               f"for example after a gateway restart. If so: hermes kanban --board {BOARD} reclaim {self.tid} --reason stalled")
        (row,) = self.rows("stall")
        self.assertEqual((row["task"], row["board"], row["run"], row["last_heartbeat"], row["minutes"]), (self.tid, BOARD, 4, beat, 20))
        self.assertEqual(with_conn(lambda c: kb.get_task(c, self.tid)).status, "running")      # never reclaimed by itself
        self.assertEqual(with_conn(lambda c: kb.list_comments(c, self.tid))[-1].author, plugin.NOTICE_AUTHOR)

    def test_ten_minutes_is_not_stalled(self):
        self.running(10)
        self.sweep()
        self.assertEqual(self.stalled(), [])

    def test_a_second_sweep_in_the_same_run_adds_nothing_and_a_new_run_may_comment_again(self):
        self.running(20, run=4)
        self.sweep()
        self.sweep()
        self.assertEqual(len(self.stalled()), 1)
        self.running(20, run=5)
        self.sweep()
        self.assertEqual(len(self.stalled()), 2)
        self.assertIn("(run 5)", self.stalled()[1])

    def test_a_card_that_left_running_is_forgotten(self):
        self.running(20)
        self.sweep()
        with_conn(lambda c: c.execute("UPDATE tasks SET status = 'ready' WHERE id = ?", (self.tid,)) and c.commit())
        self.sweep()
        self.assertEqual(self.state().get("stalled"), {})

    def test_zero_switches_it_off(self):
        self.running(60)
        self._minutes = 0
        self.sweep()
        self.assertEqual(self.stalled(), [])

    def test_a_board_that_did_not_opt_in_is_not_watched(self):
        self.running(60)
        plugin._last_scan.clear()
        plugin.sweep(board="elsewhere")
        self.assertEqual(self.stalled(), [])

    def test_a_broken_board_read_does_not_break_the_sweep(self):
        with mock.patch.object(plugin._core, "running_cards", side_effect=RuntimeError("gone")):
            self.running(60)
            self.sweep()
        self.assertEqual(self.stalled(), [])


class PaceFixture(QuotaFixture):
    def setUp(self):
        super().setUp()
        with_conn(lambda c: c.execute("UPDATE tasks SET status = 'done' WHERE id = ?", (self.tid,)) and c.commit())   # the fixture's card
        self.cards = [with_conn(lambda c, i=i: kb.create_task(c, title=f"card {i}", assignee="worker", priority=i)) for i in range(4)]

    def status(self, tid):
        return with_conn(lambda c: kb.get_task(c, tid)).status

    def statuses(self):
        return [self.status(t) for t in self.cards]

    def parents(self, tid):
        return with_conn(lambda c: kb.parent_ids(c, tid))

    def pace(self, **kw):
        return plugin.pace_board(BOARD, **({"n": 1} | kw))


class PaceTest(PaceFixture, unittest.TestCase):
    def test_dry_run_prints_the_plan_and_writes_nothing(self):
        out = self.pace(dry_run=True)
        self.assertTrue(out["ok"])
        self.assertEqual(out["added"], 0)
        self.assertEqual(self.statuses(), ["ready"] * 4)
        self.assertEqual(self.state().get("pace"), None)
        text = "\n".join(out["lines"])
        for tid in self.cards:
            self.assertIn(tid, text)
        self.assertIn("3 link", text)

    def test_max_one_leaves_exactly_one_card_ready_the_one_of_highest_priority(self):
        out = self.pace()
        self.assertEqual((out["ok"], out["added"], out["removed"]), (True, 3, 0))
        self.assertEqual(self.statuses().count("ready"), 1)
        self.assertEqual(self.status(self.cards[3]), "ready")          # priority 3 heads the chain
        self.assertEqual(self.parents(self.cards[2]), [self.cards[3]])
        (row,) = self.rows("pace")
        self.assertEqual((row["board"], row["max"], row["added"], row["removed"], row["skipped_running"]), (BOARD, 1, 3, 0, 0))
        self.assertEqual(len(self.rows("own_write", what="link")), 3)
        self.assertEqual(len(self.state()["pace"][BOARD]), 3)

    def test_undo_removes_exactly_the_links_pace_added_and_restores_the_cards(self):
        with_conn(lambda c: kb.link_tasks(c, self.cards[3], self.cards[2]))      # a link of the user: pace keeps it, undo leaves it
        before = self.statuses()
        out = self.pace()
        self.assertEqual(out["added"], 2)
        out = self.pace(undo=True)
        self.assertEqual((out["ok"], out["removed"]), (True, 2))
        self.assertEqual(self.parents(self.cards[2]), [self.cards[3]])
        self.assertEqual(self.parents(self.cards[1]), [])
        self.assertEqual(self.parents(self.cards[0]), [])
        self.assertEqual(self.statuses(), before)
        self.assertEqual(len(self.rows("own_write", what="unlink")), 2)
        self.assertNotIn(BOARD, self.state().get("pace", {}))
        self.assertEqual(self.rows("pace")[-1]["removed"], 2)

    def test_undo_dry_run_lists_without_removing(self):
        self.pace()
        out = self.pace(undo=True, dry_run=True)
        self.assertEqual(out["removed"], 0)
        self.assertIn("3 link", "\n".join(out["lines"]))
        self.assertEqual(len(self.state()["pace"][BOARD]), 3)

    def test_undo_without_links_says_so(self):
        out = self.pace(undo=True)
        self.assertEqual((out["ok"], out["removed"]), (True, 0))
        self.assertIn("nothing to undo", "\n".join(out["lines"]))

    def test_a_second_pace_asks_for_undo_first(self):
        self.pace()
        out = self.pace()
        self.assertFalse(out["ok"])
        self.assertIn("--undo", out["error"])

    def test_a_running_card_heads_the_chain(self):
        tid = self.cards[0]
        with_conn(lambda c: c.execute("UPDATE tasks SET status = 'running', current_run_id = 9 WHERE id = ?", (tid,)) and c.commit())
        self.pace()
        self.assertEqual(self.status(tid), "running")
        self.assertEqual(self.statuses().count("ready"), 0)
        self.assertEqual(self.parents(self.cards[3]), [tid])

    def test_running_cards_above_the_maximum_are_named(self):
        with_conn(lambda c: c.execute("UPDATE tasks SET status = 'running', current_run_id = 9 WHERE id IN (?, ?)",
                                      (self.cards[1], self.cards[2])) and c.commit())
        out = self.pace(dry_run=True)
        self.assertIn("1 running card(s) above the maximum are left running", "\n".join(out["lines"]))
        self.assertEqual(out["skipped_running"], 1)

    def test_a_card_that_cannot_be_placed_is_named(self):
        real = plugin.quota.plan
        with mock.patch.object(plugin.quota, "plan", lambda *a, **k: {**real(*a, **k), "skipped": [self.cards[0]]}):
            text = "\n".join(self.pace(dry_run=True)["lines"])
        self.assertIn(f"a link would close a cycle: {self.cards[0]}", text)

    def test_a_link_of_the_user_keeps_its_order(self):
        with_conn(lambda c: kb.link_tasks(c, self.cards[0], self.cards[1]))     # card 0 stays before card 1
        out = self.pace()
        self.assertEqual((out["ok"], out["added"]), (True, 2))
        self.assertEqual(self.parents(self.cards[1]), [self.cards[0]])         # the user's link, no second parent
        self.assertEqual(self.parents(self.cards[0]), [self.cards[2]])
        self.assertEqual(self.statuses().count("ready"), 1)

    def test_pace_links_are_not_outside_help(self):
        self.pace()
        plugin.DEFAULTS["advisor"]["boards"] = [BOARD]
        plugin._last_scan.clear()
        plugin.sweep(board=BOARD)
        self.assertEqual(self.rows("touch", what="link"), [])

    def test_a_link_hermes_refuses_is_reported_and_the_rest_goes_on(self):
        real = plugin._core.link

        def refuse(conn, parent, child):
            if child == self.cards[1]:
                raise ValueError("would create a cycle")
            real(conn, parent, child)
        with mock.patch.object(plugin._core, "link", refuse):
            out = self.pace()
        self.assertTrue(out["ok"])
        self.assertEqual(out["added"], 2)
        self.assertIn("refused", "\n".join(out["lines"]))

    def test_max_must_be_at_least_one(self):
        out = self.pace(n=0)
        self.assertFalse(out["ok"])
        self.assertIn("at least 1", out["error"])
        self.assertFalse(self.pace(n=None)["ok"])

    def test_a_board_that_cannot_be_read_is_an_error_not_a_crash(self):
        with mock.patch.object(plugin, "_connect", side_effect=RuntimeError("no such board")):
            out = self.pace()
        self.assertEqual((out["ok"], out["error"]), (False, "RuntimeError: no such board"))


if __name__ == "__main__":
    unittest.main()
