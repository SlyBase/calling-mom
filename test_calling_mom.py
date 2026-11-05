"""Tests for the calling-mom plugin (stdlib unittest, no pytest needed).

    cd <plugin dir> && python -m unittest -v test_calling_mom
"""

import importlib.util
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent
_DB = Path(tempfile.mkdtemp(prefix="calling-mom-test-")) / "kanban.db"
os.environ["HERMES_KANBAN_DB"] = str(_DB)
# Never let a test touch the live observation log, spend ledger or sweep watermark.
_SANDBOX = Path(tempfile.mkdtemp(prefix="calling-mom-sandbox-"))
os.environ.setdefault("CALLING_MOM_LOG", str(_SANDBOX / "log.jsonl"))
os.environ.setdefault("CALLING_MOM_SPEND", str(_SANDBOX / "spend.json"))
os.environ.setdefault("CALLING_MOM_STATE", str(_SANDBOX / "state.json"))

from hermes_cli import kanban_db as kb
from hermes_cli import profiles          # noqa: E402
from hermes_cli import kanban_db_connect as kbc  # noqa: E402

kbc.init_db(_DB)

# Tests must not read the live profile config (it enables the advisor, sets overrides, ...).
import hermes_cli.config as _hermes_config  # noqa: E402

_hermes_config.load_config = lambda *a, **k: {}


def load_plugin():
    spec = importlib.util.spec_from_file_location(
        "calling_mom_under_test", PLUGIN_DIR / "__init__.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


plugin = load_plugin()
STRONG = plugin.settings()["model"]
PROVIDER = plugin.settings()["provider"]


ADVICE = re.compile(r"^\[(?:\w+/\w+\] run |advisor unavailable\])")


def advice_comments(tid):
    """Advisor comments of a card. Recognised by their text: the public kanban_comment tool writes them
    under the worker's own profile, older versions wrote author `advisor`."""
    return [c for c in with_conn(lambda conn: kb.list_comments(conn, tid)) if ADVICE.match(c.body or "")]


def with_conn(fn):
    with kbc.connect_closing() as conn:
        return fn(conn)


class EscalationTest(unittest.TestCase):
    def setUp(self):
        os.environ["CALLING_MOM"] = "on"

    def new_task(self, title, body=None):
        return with_conn(lambda c: kb.create_task(c, title=title, body=body, assignee="worker"))

    def task(self, task_id):
        return with_conn(lambda c: kb.get_task(c, task_id))

    def test_blocked_capability_card_is_escalated_and_ready(self):
        tid = self.new_task("hard refactor")
        with_conn(lambda c: kb.block_task(c, tid, reason="local model cannot do this", kind="capability"))
        plugin.on_blocked(task_id=tid, board=None, reason="local model cannot do this")
        after = self.task(tid)
        self.assertEqual(after.model_override, STRONG)
        self.assertEqual(after.provider_override, PROVIDER)
        self.assertEqual(after.status, "ready")

    def test_needs_input_block_is_left_alone(self):
        tid = self.new_task("needs a decision")
        with_conn(lambda c: kb.block_task(c, tid, reason="which repo?", kind="needs_input"))
        plugin.on_blocked(task_id=tid, board=None, reason="which repo?")
        after = self.task(tid)
        self.assertIsNone(after.model_override)
        self.assertEqual(after.status, "blocked")

    def test_second_block_after_escalation_parks_the_card(self):
        tid = self.new_task("hard refactor 2")
        with_conn(lambda c: kb.block_task(c, tid, reason="stuck", kind="capability"))
        plugin.on_blocked(task_id=tid, board=None, reason="stuck")
        with_conn(lambda c: kb.block_task(c, tid, reason="still stuck", kind="capability"))
        plugin.on_blocked(task_id=tid, board=None, reason="still stuck")
        after = self.task(tid)
        # Second give-up of the same reason: the board routes to triage (human),
        # never back to the weak model.
        self.assertIn(after.status, {"blocked", "triage"})
        self.assertEqual(after.model_override, STRONG)

    def test_critical_marker_pins_strong_model_on_claim(self):
        tid = self.new_task("[crit] migrate auth")
        plugin.on_claimed(task_id=tid, board=None)
        self.assertEqual(self.task(tid).model_override, STRONG)

    def test_plain_card_is_not_pinned(self):
        tid = self.new_task("rename a variable")
        plugin.on_claimed(task_id=tid, board=None)
        self.assertIsNone(self.task(tid).model_override)

    def test_kill_switch_disables_escalation(self):
        os.environ["CALLING_MOM"] = "off"
        tid = self.new_task("[crit] kill switch")
        with_conn(lambda c: kb.block_task(c, tid, reason="stuck", kind="capability"))
        plugin.on_blocked(task_id=tid, board=None, reason="stuck")
        plugin.on_claimed(task_id=tid, board=None)
        after = self.task(tid)
        self.assertIsNone(after.model_override)
        self.assertEqual(after.status, "blocked")


class ObserveTest(unittest.TestCase):
    """Step 0: observe-only logging of stuck signals."""

    def setUp(self):
        self.log = Path(tempfile.mkdtemp()) / "obs.jsonl"
        os.environ["CALLING_MOM_LOG"] = str(self.log)
        os.environ["HERMES_KANBAN_TASK"] = "t_obs"
        os.environ["HERMES_KANBAN_RUN_ID"] = "7"
        plugin._trackers.clear()

    def tearDown(self):
        for k in ("HERMES_KANBAN_TASK", "HERMES_KANBAN_RUN_ID"):
            os.environ.pop(k, None)

    def lines(self):
        import json
        return [json.loads(x) for x in self.log.read_text().splitlines()] if self.log.exists() else []

    def call(self, **kw):
        base = dict(tool_name="terminal", args={"command": "pytest"}, result="x", status="error",
                    error_type="tool_error", error_message="AssertionError: 1 != 2")
        base.update(kw)
        return plugin.on_tool_result(**base)

    def test_logs_each_call_and_never_changes_the_result(self):
        for _ in range(3):
            self.assertIsNone(self.call())
        rows = self.lines()
        self.assertEqual(len(rows), 3)
        self.assertIn("same_error", rows[2]["signals"])
        self.assertEqual(rows[0]["task"], "t_obs")
        self.assertNotIn("AssertionError", self.log.read_text())  # hashes only, no content

    def test_ignores_sessions_that_are_not_kanban_workers(self):
        os.environ.pop("HERMES_KANBAN_TASK")
        self.call()
        self.assertEqual(self.lines(), [])

    def test_kill_switch(self):
        os.environ["CALLING_MOM"] = "off"
        try:
            self.call()
        finally:
            os.environ["CALLING_MOM"] = "on"
        self.assertEqual(self.lines(), [])


class SettingsTest(unittest.TestCase):
    def test_nested_defaults_and_type_guard(self):
        merged = plugin._merge(
            {"a": "x", "n": 3, "flag": False, "d": {"k": 1, "j": 2}, "l": ["p"]},
            {"a": ["bad"], "n": True, "flag": True, "d": {"k": 9, "z": 5}, "l": ["q", "r"]},
        )
        self.assertEqual(merged, {"a": "x", "n": 3, "flag": True, "d": {"k": 9, "j": 2, "z": 5}, "l": ["q", "r"]})  # unknown keys pass (custom categories)


class BreakerTest(unittest.TestCase):
    """Step 1: cards parked by the crash circuit-breaker (gave_up) are escalated once."""

    def setUp(self):
        os.environ["CALLING_MOM"] = "on"
        os.environ["CALLING_MOM_STATE"] = str(Path(tempfile.mkdtemp()) / "state.json")
        plugin.sweep(board=None)  # first sweep only sets the watermark

    def parked(self, title, error="worker exited with code 1", **kw):
        from hermes_cli import kanban_db_dispatch as kd
        tid = with_conn(lambda c: kb.create_task(c, title=title, assignee="worker"))
        with_conn(lambda c: kd._record_task_failure(c, tid, error, outcome="crashed", failure_limit=1, **kw))
        self.assertEqual(self_task(tid).status, "blocked")
        return tid

    def test_crash_parked_card_is_escalated(self):
        tid = self.parked("crashy")
        plugin.sweep(board=None)
        after = self_task(tid)
        self.assertEqual((after.model_override, after.status), (STRONG, "ready"))

    def test_history_before_the_first_tick_is_ignored(self):
        os.environ["CALLING_MOM_STATE"] = str(Path(tempfile.mkdtemp()) / "fresh.json")
        tid = self.parked("old crash")
        plugin.sweep(board=None)
        self.assertIsNone(self_task(tid).model_override)

    def test_infrastructure_errors_are_not_escalated(self):
        tid = self.parked("net down", error="Connection refused by 10.0.0.5:8000")
        plugin.sweep(board=None)
        self.assertIsNone(self_task(tid).model_override)

    def test_sticky_breaker_trips_are_left_for_a_human(self):
        tid = self.parked("sticky", force_trip=True)
        plugin.sweep(board=None)
        self.assertIsNone(self_task(tid).model_override)

    def test_second_park_after_escalation_stays_parked(self):
        tid = self.parked("twice")
        plugin.sweep(board=None)
        from hermes_cli import kanban_db_dispatch as kd
        with_conn(lambda c: kd._record_task_failure(c, tid, "again", outcome="crashed", failure_limit=1))
        plugin.sweep(board=None)
        self.assertEqual(self_task(tid).status, "blocked")

    def test_watermark_survives_a_restart(self):
        tid = self.parked("persist")
        plugin.sweep(board=None)
        self.assertEqual(self_task(tid).model_override, STRONG)
        with_conn(lambda c: kb.set_model_override(c, tid, None))  # reset; a re-sweep must not redo it
        with_conn(lambda c: kb.block_task(c, tid, reason="x", kind="capability"))
        plugin.sweep(board=None)  # new "process": state comes from the file only
        self.assertIsNone(self_task(tid).model_override)

    def test_sweep_all_walks_every_board(self):
        self.assertIsInstance(plugin.sweep_all(), list)

    def test_kill_switch(self):
        tid = self.parked("off")
        os.environ["CALLING_MOM"] = "off"
        try:
            plugin.sweep(board=None)
        finally:
            os.environ["CALLING_MOM"] = "on"
        self.assertIsNone(self_task(tid).model_override)


def self_task(task_id):
    return with_conn(lambda c: kb.get_task(c, task_id))


class ParkedHookTest(unittest.TestCase):
    """Reactive path: a core hook that already exists (no core patch) escalates a parked card."""

    def setUp(self):
        os.environ["CALLING_MOM"] = "on"
        os.environ["CALLING_MOM_STATE"] = str(Path(tempfile.mkdtemp()) / "state.json")

    def parked(self, title, error="worker exited with code 1", **kw):
        from hermes_cli import kanban_db_dispatch as kd
        tid = with_conn(lambda c: kb.create_task(c, title=title, assignee="worker"))
        with_conn(lambda c: kd._record_task_failure(c, tid, error, outcome="crashed", failure_limit=1, **kw))
        self.assertEqual(self_task(tid).status, "blocked")
        return tid

    def test_parked_hook_escalates_the_card(self):
        tid = self.parked("crashy hook")
        plugin.on_parked(task_id=tid, board=None, payload={"error": "worker exited with code 1"})
        after = self_task(tid)
        self.assertEqual((after.model_override, after.status), (STRONG, "ready"))

    def test_escalation_hands_an_entered_connection_to_the_write(self):
        """Regression: the connection must be entered, never passed as context manager or closed."""
        task = type("T", (), {"id": "t1", "status": "blocked", "block_kind": None,
                              "model_override": None, "last_failure_error": "boom",
                              "title": "x", "body": ""})()
        seen = {}

        class Ctx:
            def __enter__(self):
                return "CONN"

            def __exit__(self, *exc):
                return False

        orig = (plugin._connect, plugin._pin_strong, plugin.kb)
        plugin._connect = lambda board=None: Ctx()
        plugin._pin_strong = lambda conn, t, why, **kw: seen.update(pin=conn, reason=kw.get("reason"))
        plugin.kb = type("KB", (), {
            "get_task": staticmethod(lambda c, i: task),
            "unblock_task": staticmethod(lambda c, i: seen.update(unblock=c)),
        })()
        try:
            self.assertTrue(plugin._escalate_parked_task(task, {"error": "boom"}, "default"))
        finally:
            plugin._connect, plugin._pin_strong, plugin.kb = orig
        self.assertEqual(seen["pin"], "CONN")
        self.assertEqual(seen["unblock"], "CONN")
        self.assertEqual(seen["reason"], "breaker")

    def test_worker_exited_hook_reuses_the_parked_path(self):
        tid = self.parked("crashy exit")
        plugin.on_worker_exited(
            task_id=tid, board=None, outcome="crashed", retry_status="ready", exit_code=1,
        )
        after = self_task(tid)
        self.assertEqual((after.model_override, after.status), (STRONG, "ready"))

    def test_second_park_after_escalation_is_not_escalated_again(self):
        tid = self.parked("twice")
        plugin.on_parked(task_id=tid, payload={"error": "boom"})
        from hermes_cli import kanban_db_dispatch as kd
        with_conn(lambda c: kd._record_task_failure(c, tid, "again", outcome="crashed", failure_limit=1))
        plugin.on_parked(task_id=tid, payload={"error": "again"})
        self.assertEqual(self_task(tid).status, "blocked")

    def test_infrastructure_error_is_left_alone(self):
        tid = self.parked("net down", error="Connection refused by 10.0.0.5:8000")
        plugin.on_parked(task_id=tid, payload={"error": "Connection refused by 10.0.0.5:8000"})
        self.assertIsNone(self_task(tid).model_override)

    def test_sticky_trip_is_left_for_a_human(self):
        tid = self.parked("sticky", force_trip=True)
        plugin.on_parked(task_id=tid, payload={"error": "boom", "sticky": True})
        self.assertIsNone(self_task(tid).model_override)

    def test_unknown_task_and_missing_id_are_no_ops(self):
        plugin.on_parked(task_id="no-such-task", payload={"error": "boom"})
        plugin.on_parked(task_id=None, payload={"error": "boom"})

    def test_kill_switch(self):
        tid = self.parked("off")
        os.environ["CALLING_MOM"] = "off"
        try:
            plugin.on_parked(task_id=tid, payload={"error": "boom"})
        finally:
            os.environ["CALLING_MOM"] = "on"
        self.assertIsNone(self_task(tid).model_override)

    def test_register_subscribes_both_parked_paths(self):
        from types import SimpleNamespace as NS
        seen = []
        plugin.register(NS(register_hook=lambda n, f: seen.append((n, f.__name__)),
                           register_tool=lambda **kw: None))
        names = dict(seen)
        self.assertIn("on_kanban_worker_exited", names)
        self.assertEqual(names["on_kanban_worker_exited"], "on_worker_exited")


class CreateHookTest(unittest.TestCase):
    """[crit] cards get the strong model when they are created (pre_tool_call -> modify)."""

    def setUp(self):
        os.environ["CALLING_MOM"] = "on"

    def pre(self, tool="kanban_create", **args):
        return plugin.on_pre_tool(tool_name=tool, args=args)

    def test_marker_in_title_sets_model_and_provider(self):
        out = self.pre(title="[crit] migrate auth")
        self.assertEqual(out, {"action": "modify", "args": {"model": STRONG, "provider": PROVIDER}})

    def test_marker_in_body(self):
        self.assertIsNotNone(self.pre(title="x", body="this is [CRIT] work"))

    def test_plain_card_untouched(self):
        self.assertIsNone(self.pre(title="rename a variable"))

    def test_explicit_model_wins(self):
        self.assertIsNone(self.pre(title="[crit] x", model="other/model"))

    def test_other_tools_ignored(self):
        self.assertIsNone(self.pre(tool="terminal", title="[crit] x"))

    def test_kill_switch(self):
        os.environ["CALLING_MOM"] = "off"
        try:
            self.assertIsNone(self.pre(title="[crit] x"))
        finally:
            os.environ["CALLING_MOM"] = "on"


class VerifyTest(unittest.TestCase):
    """Step 2: kanban_complete / kanban_request_review are gated by the card's `verify:` command."""

    def setUp(self):
        os.environ["CALLING_MOM"] = "on"
        os.environ["CALLING_MOM_LOG"] = str(Path(tempfile.mkdtemp()) / "v.jsonl")
        self.ws = tempfile.mkdtemp()
        os.environ["HERMES_KANBAN_WORKSPACE"] = self.ws
        os.environ["HERMES_KANBAN_RUN_ID"] = "1"
        plugin._verify_fails.clear()

    def tearDown(self):
        for k in ("HERMES_KANBAN_TASK", "HERMES_KANBAN_RUN_ID", "HERMES_KANBAN_WORKSPACE"):
            os.environ.pop(k, None)

    def card(self, body):
        tid = with_conn(lambda c: kb.create_task(c, title="verify card", body=body, assignee="worker", created_by="user"))
        os.environ["HERMES_KANBAN_TASK"] = tid
        return tid

    def gate(self, tool="kanban_complete"):
        return plugin.on_pre_tool(tool_name=tool, args={})

    def test_failing_command_blocks_the_completion_with_output(self):
        self.card("Do the thing.\nverify: python3 -c \"import sys; sys.stderr.write('boom'); sys.exit(3)\"")
        out = self.gate()
        self.assertEqual(out["action"], "block")
        self.assertIn("exit 3", out["message"])
        self.assertIn("boom", out["message"])

    def test_review_handoff_is_gated_too(self):
        self.card("verify: false")
        self.assertEqual(self.gate("kanban_request_review")["action"], "block")

    def test_passing_command_lets_the_card_complete(self):
        self.card("verify: true")
        self.assertIsNone(self.gate())

    def test_runs_inside_the_workspace(self):
        Path(self.ws, "marker.txt").write_text("x")
        self.card("verify: test -f marker.txt")
        self.assertIsNone(self.gate())

    def test_card_without_verify_line_is_ignored(self):
        self.card("Just do it.")
        self.assertIsNone(self.gate())

    def test_other_kanban_tools_are_not_gated(self):
        self.card("verify: false")
        self.assertIsNone(self.gate("kanban_comment"))

    def test_not_a_kanban_worker(self):
        self.card("verify: false")
        os.environ.pop("HERMES_KANBAN_TASK")
        self.assertIsNone(self.gate())

    def test_timeout_is_reported(self):
        self.card("verify: python3 -c \"import time; time.sleep(5)\"")
        old = plugin.DEFAULTS["verify"]["timeout_s"]
        plugin.DEFAULTS["verify"]["timeout_s"] = 1
        try:
            out = self.gate()
        finally:
            plugin.DEFAULTS["verify"]["timeout_s"] = old
        self.assertIn("timed out", out["message"])

    def test_repeated_failure_points_to_kanban_block_then_fails_open(self):
        self.card("verify: false")
        msgs = [self.gate() for _ in range(6)]
        self.assertTrue(all(m and m["action"] == "block" for m in msgs[:5]))
        self.assertNotIn("kanban_block", msgs[2]["message"])
        self.assertIn("kanban_block", msgs[3]["message"])   # 4th failure: stop fixing, give up properly
        self.assertIsNone(msgs[5])                           # 6th: never trap the worker

    def test_kill_switch(self):
        self.card("verify: false")
        os.environ["CALLING_MOM"] = "off"
        try:
            self.assertIsNone(self.gate())
        finally:
            os.environ["CALLING_MOM"] = "on"


class FakeLlm:
    """Stands in for ctx.llm: records calls, returns a canned completion."""

    def __init__(self, text="Root cause: X. Next: do Y.", cost=0.0021, boom=False):
        from types import SimpleNamespace as NS
        self.calls, self.boom = [], boom
        self._res = NS(text=text, model="deepseek/test", provider="openrouter",
                       usage=NS(input_tokens=1800, output_tokens=120, cost_usd=cost))

    def complete(self, messages, **kw):
        self.calls.append({"messages": messages, **kw})
        if self.boom:
            raise RuntimeError(getattr(self, "error", "provider down"))
        return self._res


class AdvisorFixture:
    """A kanban worker with the advisor on, a fake LLM and a recorded `kanban_comment` tool."""

    def setUp(self):
        from types import SimpleNamespace as NS
        os.environ["CALLING_MOM"] = "on"
        os.environ["CALLING_MOM_LOG"] = str(Path(tempfile.mkdtemp()) / "a.jsonl")
        os.environ["CALLING_MOM_SPEND"] = str(Path(tempfile.mkdtemp()) / "spend.json")
        os.environ["HERMES_KANBAN_RUN_ID"] = "1"
        plugin._trackers.clear(); plugin._history.clear()
        self.saved = {k: dict(v) if isinstance(v, dict) else v for k, v in plugin.DEFAULTS.items()}
        plugin.DEFAULTS["advisor"]["enabled"] = True
        plugin.DEFAULTS["advisor"]["boards"] = ["t-board"]
        os.environ["HERMES_KANBAN_BOARD"] = "t-board"
        self.llm = FakeLlm()
        plugin._down.clear()
        self.dispatched = []
        plugin._ctx = NS(llm=self.llm, dispatch_tool=self.dispatch_tool)
        self.tid = with_conn(lambda c: kb.create_task(c, title="fix the parser", body="Make tests pass.", assignee="worker"))
        os.environ["HERMES_KANBAN_TASK"] = self.tid

    def dispatch_tool(self, name, args, **kw):
        """Stands in for ctx.dispatch_tool: like the real kanban_comment tool it signs with the worker's profile."""
        self.dispatched.append((name, dict(args)))
        if name != "kanban_comment":
            return json.dumps({"error": "unknown tool"})
        with_conn(lambda c: kb.add_comment(c, args["task_id"], "worker", args["body"]))
        return json.dumps({"ok": True})

    def tearDown(self):
        plugin.DEFAULTS.clear(); plugin.DEFAULTS.update(self.saved)
        plugin._ctx = None
        for k in ("HERMES_KANBAN_TASK", "HERMES_KANBAN_RUN_ID", "HERMES_KANBAN_BOARD"):
            os.environ.pop(k, None)

    def stuck(self, n=3, text="AssertionError: boom", tool="terminal", args=None):
        out = None
        for i in range(n):
            out = plugin.on_tool_result(tool_name=tool, args=args or {"command": f"pytest {i}"}, result="r",
                                        status="error", error_type="tool_error", error_message=text)
        return out


class AdvisorTest(AdvisorFixture, unittest.TestCase):
    """Step 3: the strong model is asked ONE short question at the point where the worker is stuck."""

    def test_stuck_worker_gets_short_advice_appended_to_the_result(self):
        out = self.stuck(3)
        self.assertEqual(len(self.llm.calls), 1)
        self.assertIn("[ADVISOR deepseek/test]", out)
        self.assertIn("Root cause: X", out)
        self.assertTrue(out.startswith("r"))  # original result kept

    def test_no_call_before_the_signal(self):
        self.assertIsNone(self.stuck(2))
        self.assertEqual(self.llm.calls, [])

    def test_disabled_by_default(self):
        plugin.DEFAULTS["advisor"]["enabled"] = False
        self.assertIsNone(self.stuck(3))
        self.assertEqual(self.llm.calls, [])

    def test_budget_per_card_comes_from_durable_comments(self):
        for _ in range(5):
            plugin._trackers.clear()
            self.stuck(3)
        self.assertEqual(len(self.llm.calls), plugin.DEFAULTS["advisor"]["max_consults_per_card"])
        comments = with_conn(lambda c: kb.list_comments(c, self.tid))
        self.assertEqual(len(advice_comments(self.tid)), 3)
        self.assertIn("1800 in / 120 out", comments[-1].body)

    def test_category_picks_its_own_model(self):
        plugin.DEFAULTS["categories"] = {"error": {"model": "m/error", "provider": "p1"}, "loop": {"model": "m/loop", "provider": "p2"}}
        self.stuck(3)
        self.assertEqual((self.llm.calls[0]["model"], self.llm.calls[0]["provider"]), ("m/error", "p1"))
        plugin._trackers.clear()
        for _ in range(3):
            plugin.on_tool_result(tool_name="read_file", args={"path": "/a"}, result="r", status="ok")
        self.assertEqual((self.llm.calls[1]["model"], self.llm.calls[1]["provider"]), ("m/loop", "p2"))

    def test_provider_failure_is_fail_open(self):
        self.llm.boom = True
        self.assertIsNone(self.stuck(3))

    def test_daily_cost_cap(self):
        plugin.DEFAULTS["advisor"]["daily_usd_cap"] = 0.001
        self.stuck(3)                       # costs 0.0021 -> cap reached
        plugin._trackers.clear()
        self.assertIsNone(self.stuck(3))
        self.assertEqual(len(self.llm.calls), 1)

    def test_daily_token_cap_works_when_the_provider_reports_no_cost(self):
        self.llm = FakeLlm(cost=0.0)
        plugin._ctx.llm = self.llm
        plugin.DEFAULTS["advisor"]["daily_token_cap"] = 1000
        self.stuck(3)                       # 1800 + 120 tokens -> cap reached although cost is 0
        plugin._trackers.clear()
        self.assertIsNone(self.stuck(3))
        self.assertEqual(len(self.llm.calls), 1)

    def test_local_only_cards_never_leave_the_machine(self):
        with_conn(lambda c: kb.add_comment(c, self.tid, "x", "noise"))
        tid = with_conn(lambda c: kb.create_task(c, title="[local-only] secret infra", assignee="worker"))
        os.environ["HERMES_KANBAN_TASK"] = tid
        self.assertIsNone(self.stuck(3))
        self.assertEqual(self.llm.calls, [])

    def test_prompt_is_redacted_and_capped(self):
        token = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
        plugin.DEFAULTS["advisor"]["context_chars"] = 900
        self.stuck(3, text="auth failed with " + token + " " + "x" * 3000)
        sent = "\n".join(m["content"] for m in self.llm.calls[0]["messages"])
        self.assertNotIn(token, sent)
        self.assertLessEqual(len(self.llm.calls[0]["messages"][1]["content"]), 900)
        self.assertEqual(self.llm.calls[0]["max_tokens"], plugin.DEFAULTS["advisor"]["max_output_tokens"])

    def test_failed_verification_gets_advice_from_the_validation_category(self):
        plugin.DEFAULTS["categories"] = {"validation": {"model": "m/validate", "provider": "p3"}}
        with_conn(lambda c: kb.add_comment(c, self.tid, "x", "noise"))
        tid = with_conn(lambda c: kb.create_task(c, title="verify card", body="verify: python3 -c \"import sys; sys.stderr.write('nope'); sys.exit(1)\"", assignee="worker", created_by="user"))
        os.environ["HERMES_KANBAN_TASK"] = tid
        plugin._verify_fails.clear()
        first = plugin.on_pre_tool(tool_name="kanban_complete", args={})
        self.assertNotIn("ADVISOR", first["message"])             # 1st failure: no spend
        second = plugin.on_pre_tool(tool_name="kanban_complete", args={})
        self.assertIn("[ADVISOR", second["message"])               # 2nd failure: ask the validator
        self.assertEqual(self.llm.calls[-1]["model"], "m/validate")


class ArchitectureTest(AdvisorTest):
    """Step 4: soft review of critical changes + the voluntary second_opinion tool."""

    def setUp(self):
        super().setUp()
        plugin.DEFAULTS["categories"] = {
            "architecture": {"model": "m/arch", "provider": "p4", "max": 2,
                             "patterns": [r"\.tf$", r"migrations?/"], "commands": [r"terraform (apply|destroy)"]},
        }
        plugin._arch_seen.clear()

    def write(self, path, content="x = 1"):
        return plugin.on_tool_result(tool_name="write_file", args={"path": path, "content": content},
                                     result="written", status="ok")

    def test_critical_file_gets_a_review_appended(self):
        out = self.write("infra/main.tf", "resource aws_x y {}")
        self.assertIn("[ADVISOR", out)
        self.assertEqual(self.llm.calls[0]["model"], "m/arch")
        self.assertIn("infra/main.tf", self.llm.calls[0]["messages"][1]["content"])

    def test_ordinary_file_is_not_reviewed(self):
        self.assertIsNone(self.write("src/app.py"))
        self.assertEqual(self.llm.calls, [])

    def test_dangerous_command_is_reviewed(self):
        out = plugin.on_tool_result(tool_name="terminal", args={"command": "terraform apply -auto-approve"}, result="ok", status="ok")
        self.assertIn("[ADVISOR", out)
        self.assertIsNone(plugin.on_tool_result(tool_name="terminal", args={"command": "ls -la"}, result="ok", status="ok"))

    def test_each_path_is_reviewed_once_and_the_budget_is_two(self):
        self.write("a/main.tf"); self.write("a/main.tf")
        self.assertEqual(len(self.llm.calls), 1)
        self.write("b/main.tf"); self.write("c/main.tf")
        self.assertEqual(len(self.llm.calls), 2)

    def test_reviews_do_not_eat_the_stuck_budget(self):
        self.write("a/main.tf"); self.write("b/main.tf")
        out = self.stuck(3)
        self.assertIn("[ADVISOR", out)
        self.assertEqual(len(self.llm.calls), 3)

    def test_second_opinion_tool(self):
        out = json.loads(plugin.handle_second_opinion({"question": "Postgres or SQLite?", "context": "single user"}))
        self.assertIn("Root cause", out["opinion"])
        self.assertEqual(self.llm.calls[0]["model"], "m/arch")
        self.assertIn("Postgres or SQLite?", self.llm.calls[0]["messages"][1]["content"])

    def test_second_opinion_reports_when_unavailable(self):
        plugin.DEFAULTS["advisor"]["enabled"] = False
        out = json.loads(plugin.handle_second_opinion({"question": "q"}))
        self.assertIn("error", out)

    def test_register_adds_the_tool(self):
        from types import SimpleNamespace as NS
        seen = {}
        ctx = NS(register_hook=lambda *a, **k: None,
                 register_tool=lambda **kw: seen.setdefault(kw["name"], kw), llm=self.llm)
        plugin.register(ctx)
        self.assertEqual(seen["second_opinion"]["schema"]["name"], "second_opinion")
        self.assertIn("question", seen["second_opinion"]["schema"]["parameters"]["required"])


class CriticalDefaultsTest(AdvisorTest):
    """Audit: merges, releases, pushes to main, public comments and host shells went unreviewed."""

    def run_cmd(self, command, tool="terminal"):
        self.tid = with_conn(lambda c: kb.create_task(c, title="c", assignee="worker"))
        os.environ["HERMES_KANBAN_TASK"] = self.tid
        args = {"code": f"subprocess.run({command!r}, shell=True)"} if tool == "execute_code" else {"command": command}
        return plugin.on_tool_result(tool_name=tool, args=args, result="ok", status="ok")

    def test_irreversible_or_public_commands_are_reviewed(self):
        for cmd in ("gh pr merge 135 --squash", "gh release create v2.9.2", "git push origin main",
                    "git push --force origin feat/x", "git tag v1.2.0", "gh issue comment 133 --body x",
                    "gh api repos/o/r/pulls/1/merge -X PUT", "ssh root@pve1 chown hermes /x", "pct exec 101 -- id",
                    "kubectl -n prod delete pvc pgdata", "kubectl --context p apply -f x.yaml"):
            with self.subTest(cmd=cmd):
                self.assertIn("[ADVISOR", self.run_cmd(cmd) or "")

    def test_routine_commands_are_not_reviewed(self):
        for cmd in ("git push origin feat/x", "gh pr view 1", "git status", "pytest -q"):
            with self.subTest(cmd=cmd):
                self.assertIsNone(self.run_cmd(cmd))

    def test_execute_code_is_checked_like_terminal(self):
        self.assertIn("[ADVISOR", self.run_cmd("gh pr merge 1", tool="execute_code") or "")

    def test_only_the_first_4000_characters_are_classified(self):
        pad = "echo " + "x" * 4100
        self.assertIsNone(self.run_cmd(pad + "; gh pr merge 1"))   # ponytail: ceiling, a hit past 4000 characters is missed
        self.assertIn("[ADVISOR", self.run_cmd("gh pr merge 1; " + pad) or "")

    def test_a_huge_single_line_command_is_classified_fast(self):
        for command in ("git push " * 1400, ("git push origin feat/x\n" * 500)):
            started = time.perf_counter()
            self.assertIsNone(self.run_cmd(command))
            self.assertLess(time.perf_counter() - started, 0.5)   # whole hook, not just the regex


class CompletionCheckTest(AdvisorTest):
    """Audit: a card that asked for PR + merge + release was closed as 'no bug, no PR' unchecked."""

    def complete(self, summary="Triage done: no bug, no PR."):
        return plugin.on_pre_tool(tool_name="kanban_complete", args={"summary": summary})

    def test_gap_blocks_the_first_completion_once(self):
        self.llm._res.text = "GAP: the card asks for a PR, a merge and a release; none was made."
        first = self.complete()
        self.assertEqual(first["action"], "block")
        self.assertIn("none was made", first["message"])
        self.assertIn("needs_input", first["message"])
        self.assertIn("no bug, no PR", self.llm.calls[0]["messages"][1]["content"])
        self.assertIsNone(self.complete())  # never traps the worker: the 2nd attempt passes
        self.assertEqual(len(self.llm.calls), 1)

    def test_done_lets_the_completion_through(self):
        self.llm._res.text = "DONE"
        self.assertIsNone(self.complete())

    def test_completion_check_has_its_own_budget(self):
        for _ in range(3):
            plugin._trackers.clear()
            self.stuck(3)
        self.llm._res.text = "GAP: x"
        self.assertEqual(self.complete()["action"], "block")
        self.assertEqual(len(self.llm.calls), 4)


class NoActionTest(AdvisorTest):
    """Audit: the advisor invented a problem the worker had already solved, and echoed the card's guess."""

    def test_no_action_answer_is_dropped_silently(self):
        self.llm._res.text = "NO_ACTION"
        self.assertIsNone(self.stuck(3))
        self.assertEqual(advice_comments(self.tid), [])
        rows = [json.loads(x) for x in Path(os.environ["CALLING_MOM_LOG"]).read_text().splitlines()]
        self.assertTrue([r for r in rows if r.get("kind") == "advisor" and r.get("noop")])

    def test_stuck_prompt_allows_no_action_and_distrusts_the_card_guess(self):
        self.stuck(3)
        system = self.llm.calls[0]["messages"][0]["content"]
        self.assertIn("NO_ACTION", system)
        self.assertIn("guess", system)


class DeliveryTest(AdvisorTest):
    """Advice is delivered once, through public Hermes surfaces only (catalog rule 9)."""

    def test_advice_is_written_with_the_public_kanban_comment_tool(self):
        self.stuck(3)
        name, args = self.dispatched[-1]
        self.assertEqual((name, args["task_id"]), ("kanban_comment", self.tid))
        self.assertTrue(args["body"].startswith("[error/same_error] run 1"))
        self.assertEqual(len(advice_comments(self.tid)), 1)

    def test_the_plugin_never_touches_private_kanban_tool_state(self):
        src = (PLUGIN_DIR / "__init__.py").read_text()
        self.assertNotIn("_comment_watermark", src)
        self.assertNotIn("tools.kanban_tools", src)

    def test_a_comment_by_the_workers_own_profile_is_not_relayed_by_hermes(self):
        """The relay skips the worker's own comments by author; that is what makes a watermark hack unnecessary."""
        self.stuck(3)
        self.assertEqual(advice_comments(self.tid)[0].author, "worker")

    def test_failed_comment_write_still_delivers_the_advice(self):
        plugin._ctx.dispatch_tool = lambda name, args, **kw: json.dumps({"error": "board locked"})
        self.assertIn("[ADVISOR", self.stuck(3))

    def test_a_raising_comment_write_still_delivers_the_advice(self):
        def boom(name, args, **kw):
            raise RuntimeError("down")
        plugin._ctx.dispatch_tool = boom
        self.assertIn("[ADVISOR", self.stuck(3))

    def test_hermes_without_dispatch_tool_still_delivers_the_advice(self):
        plugin._ctx = type(plugin._ctx)(llm=self.llm)
        self.assertIn("[ADVISOR", self.stuck(3))

    def test_comments_of_older_versions_still_count_against_the_budget(self):
        for _ in range(plugin.DEFAULTS["advisor"]["max_consults_per_card"]):
            with_conn(lambda c: kb.add_comment(c, self.tid, "advisor", "[error/same_error] run 1 · m (p): 1 in / 1 out\nold"))
        self.assertIsNone(self.stuck(3))
        self.assertEqual(self.llm.calls, [])

    def test_the_latest_operator_note_ignores_advice_comments(self):
        with_conn(lambda c: kb.add_comment(c, self.tid, "user", "please also bump the version"))
        self.stuck(3)
        plugin._trackers.clear()
        self.stuck(3)                       # the first advice is now the newest comment
        self.assertIn("please also bump the version", self.llm.calls[-1]["messages"][1]["content"])

    def test_comment_names_the_run_and_hides_a_zero_cost(self):
        self.llm._res.usage.cost_usd = 0.0
        self.stuck(3)
        body = advice_comments(self.tid)[-1].body
        self.assertIn("run 1", body)
        self.assertNotIn("$0.0000", body)


class LogPathTest(unittest.TestCase):
    def test_default_log_lives_under_the_shared_root(self):
        from hermes_constants import get_default_hermes_root
        saved = os.environ.pop("CALLING_MOM_LOG")
        try:  # path only, never touch the live file
            self.assertEqual(plugin._log_path(), Path(get_default_hermes_root()) / "logs" / "calling-mom.jsonl")
        finally:
            os.environ["CALLING_MOM_LOG"] = saved


class FeedbackTest(AdvisorTest):
    """Audit: twice the worker refuted the advice in plain words, and nothing counted it."""

    def reply(self, content):
        from types import SimpleNamespace as NS
        plugin.on_api_response(assistant_message=NS(content=content))

    def feedback(self):
        rows = [json.loads(x) for x in Path(os.environ["CALLING_MOM_LOG"]).read_text().splitlines()]
        return [r["verdict"] for r in rows if r.get("kind") == "advice_feedback"]

    def test_refuted_advice_is_counted_as_rejected(self):
        self.stuck(3)
        self.reply("")  # a tool-call-only reply says nothing yet
        self.reply("The advisor's hypothesis is disproven: the secret matches.")
        self.reply("ruled out again")  # only the first reply after the advice counts
        self.assertEqual(self.feedback(), ["rejected"])

    def test_other_replies_count_as_followed(self):
        self.stuck(3)
        self.reply("Good point, running the tests in the foreground now.")
        self.assertEqual(self.feedback(), ["followed"])

    def test_no_advice_no_feedback(self):
        self.reply("disproven")
        self.assertFalse(Path(os.environ["CALLING_MOM_LOG"]).exists() and self.feedback())


class PromptTest(AdvisorTest):
    """Each kind of question gets its own system prompt; board chatter stays out of the context."""

    def system(self, i=-1):
        return self.llm.calls[i]["messages"][0]["content"]

    def test_prompts_differ_by_signal(self):
        self.stuck(3)
        plugin._arch_seen.clear()
        plugin.DEFAULTS["categories"]["architecture"] = {"max": 5, "patterns": [r"\.tf$"], "commands": []}
        plugin.on_tool_result(tool_name="write_file", args={"path": "x.tf", "content": "c"}, result="w", status="ok")
        plugin.handle_second_opinion({"question": "A or B?"})
        stuck, review, opinion = (self.system(i) for i in range(3))
        self.assertIn("stuck", stuck)
        self.assertIn("review", review)
        self.assertIn("design question", opinion)

    def test_board_tool_results_do_not_enter_the_advisor_context(self):
        plugin.on_tool_result(tool_name="kanban_show", args={}, result="huge board dump " * 50, status="ok")
        self.stuck(3)
        self.assertNotIn("huge board dump", self.llm.calls[0]["messages"][1]["content"])

    def test_tool_has_its_own_toolset_and_is_hidden_outside_workers(self):
        from types import SimpleNamespace as NS
        seen = {}
        plugin.register(NS(register_hook=lambda *a, **k: None, register_tool=lambda **kw: seen.update(kw), llm=self.llm))
        # "kanban" is a static list in toolsets.py: a tool registered there never reaches the model.
        self.assertEqual(seen["toolset"], "calling-mom")
        self.assertTrue(seen["check_fn"]())
        os.environ.pop("HERMES_KANBAN_TASK")
        self.assertFalse(seen["check_fn"]())


class CommentRouteTest(AdvisorTest):
    """A weak model cannot find a deferred tool; a kanban_comment with SECOND-OPINION: works anyway."""

    def comment(self, body):
        return plugin.on_tool_result(tool_name="kanban_comment", args={"body": body}, result="commented", status="ok")

    def test_question_marker_gets_an_answer_in_the_tool_result(self):
        out = self.comment("SECOND-OPINION: SQLite or Postgres?")
        self.assertIn("[ADVISOR", out)
        self.assertEqual(self.llm.calls[0]["model"], plugin.DEFAULTS["categories"].get("architecture", {}).get("model") or plugin.DEFAULTS["model"])

    def test_plain_comments_are_ignored(self):
        self.assertIsNone(self.comment("just a note"))
        self.assertEqual(self.llm.calls, [])

    def test_marker_is_case_insensitive(self):
        self.assertIn("[ADVISOR", self.comment("second-opinion: A or B?"))

    def test_local_only_card_gets_no_answer(self):
        tid = with_conn(lambda c: kb.create_task(c, title="[local-only] secrets", assignee="worker"))
        os.environ["HERMES_KANBAN_TASK"] = tid
        self.assertIsNone(self.comment("SECOND-OPINION: rotate the key?"))

    def test_system_prompt_section_advertises_the_route(self):
        from types import SimpleNamespace as NS
        seen = {}
        plugin.register(NS(register_hook=lambda *a, **k: None, register_tool=lambda **kw: None,
                           register_system_prompt_section=lambda **kw: seen.update({"text": kw["content"]({}), "kw": kw}),
                           llm=self.llm))
        self.assertIn("SECOND-OPINION", seen["text"])


class OptInTest(AdvisorTest):
    """Step A: nothing leaves the machine unless the board or the card opted in."""

    def test_default_config_is_closed(self):
        fresh = load_plugin()
        self.assertFalse(fresh.DEFAULTS["advisor"]["enabled"])
        self.assertEqual(fresh.DEFAULTS["advisor"]["boards"], [])

    def test_board_without_opt_in_sends_nothing(self):
        os.environ["HERMES_KANBAN_BOARD"] = "other-board"
        self.assertIsNone(self.stuck(3))
        self.assertEqual(self.llm.calls, [])

    def test_card_marker_opts_in_even_on_another_board(self):
        os.environ["HERMES_KANBAN_BOARD"] = "other-board"
        tid = with_conn(lambda c: kb.create_task(c, title="[advisor] hard parser", assignee="worker"))
        os.environ["HERMES_KANBAN_TASK"] = tid
        self.assertIn("[ADVISOR", self.stuck(3))

    def test_wildcard_board_list(self):
        plugin.DEFAULTS["advisor"]["boards"] = ["*"]
        os.environ["HERMES_KANBAN_BOARD"] = "anything"
        self.assertIn("[ADVISOR", self.stuck(3))

    def test_local_only_beats_every_opt_in(self):
        tid = with_conn(lambda c: kb.create_task(c, title="[advisor] [local-only] secrets", assignee="worker"))
        os.environ["HERMES_KANBAN_TASK"] = tid
        self.assertIsNone(self.stuck(3))


class SeqLlm(FakeLlm):
    """Answers from a list, one per call, and reports the model it was asked for."""

    def __init__(self, texts):
        super().__init__()
        self.texts = list(texts)

    def complete(self, messages, **kw):
        from types import SimpleNamespace as NS
        self.calls.append({"messages": messages, **kw})
        text = self.texts.pop(0) if self.texts else "ok"
        return NS(text=text, model=kw["model"], provider=kw["provider"],
                  usage=NS(input_tokens=500, output_tokens=50, cost_usd=0.01))


class TwoTierTest(AdvisorTest):
    """Mom answers first; the olymp model is asked only when the mom says ESCALATE."""

    def setUp(self):
        super().setUp()
        plugin.DEFAULTS["olymp"].update(model="m/olymp", provider="p/olymp")
        plugin.DEFAULTS["categories"] = {"architecture": {"max": 2, "patterns": [], "commands": [r"terraform apply"]}}
        plugin._arch_seen.clear()
        self.models = lambda: [c["model"] for c in self.llm.calls]

    def seq(self, *texts):
        self.llm = SeqLlm(texts)
        plugin._ctx.llm = self.llm

    def critical(self, cmd="terraform apply"):
        return plugin.on_tool_result(tool_name="terminal", args={"command": cmd}, result="r", status="ok")

    def comments(self):
        return [c.body for c in advice_comments(self.tid)]

    def test_routine_critical_change_is_answered_by_the_mom_alone(self):
        self.seq("Fine, nothing irreversible.")
        out = self.critical()
        self.assertEqual(self.models(), [STRONG])
        self.assertIn("Fine", out)

    def test_mom_escalates_a_real_edge_case_to_olymp(self):
        self.seq("ESCALATE: prod deploy without rollback", "Olymp says: keep the cron as fallback.")
        out = self.critical()
        self.assertEqual(self.models(), [STRONG, "m/olymp"])
        self.assertEqual(self.llm.calls[1]["provider"], "p/olymp")
        self.assertIn("Olymp says", out)
        self.assertNotIn("ESCALATE", out)

    def test_gate_instruction_goes_only_to_the_mom(self):
        self.seq("ESCALATE: x", "ok")
        self.critical()
        self.assertIn("ESCALATE", self.llm.calls[0]["messages"][0]["content"])
        self.assertNotIn("ESCALATE", self.llm.calls[1]["messages"][0]["content"])

    def test_routine_stuck_signal_never_reaches_olymp(self):
        self.seq("ESCALATE: x")
        self.stuck(3)
        self.assertEqual(self.models(), [STRONG])
        self.assertNotIn("ESCALATE", self.llm.calls[0]["messages"][0]["content"])

    def test_olymp_is_off_without_a_complete_pair(self):
        plugin.DEFAULTS["olymp"]["provider"] = ""
        self.seq("ESCALATE: x")
        self.critical()
        self.assertEqual(self.models(), [STRONG])
        self.assertNotIn("ESCALATE", self.llm.calls[0]["messages"][0]["content"])

    def test_olymp_is_asked_once_per_card(self):
        self.seq("ESCALATE: a", "olymp a", "ESCALATE: b", "must not be asked")
        self.critical("terraform apply -a")
        self.critical("terraform apply -b")
        self.assertEqual(self.models(), [STRONG, "m/olymp", STRONG])

    def test_olymp_has_a_daily_call_limit(self):
        plugin.DEFAULTS["olymp"].update(max_per_card=9, max_per_day=1)
        self.seq("ESCALATE: a", "olymp a", "ESCALATE: b")
        self.critical("terraform apply -a")
        self.critical("terraform apply -b")
        self.assertEqual(self.models(), [STRONG, "m/olymp", STRONG])

    def test_olymp_calls_do_not_count_against_the_dollar_cap(self):
        self.seq("ESCALATE: a", "olymp a")
        self.critical()
        self.assertEqual(plugin._spent_today()["usd"], 0.01)   # mom only
        self.assertEqual(plugin._spent_today()["olymp"], 1)

    def test_comment_names_model_and_provider_and_the_olymp_tier(self):
        self.seq("ESCALATE: a", "olymp a")
        self.critical()
        self.assertIn("m/olymp (p/olymp, olymp)", self.comments()[0])

    def test_mom_comment_names_the_provider_without_olymp_tag(self):
        self.seq("fine")
        self.critical()
        self.assertIn(f"{STRONG} ({PROVIDER})", self.comments()[0])
        self.assertNotIn("olymp)", self.comments()[0])

    def test_trace_row_carries_the_provider(self):
        self.seq("fine")
        self.critical()
        rows = [json.loads(l) for l in Path(os.environ["CALLING_MOM_LOG"]).read_text().splitlines()]
        self.assertEqual([r["provider"] for r in rows if r.get("kind") == "advisor"], [PROVIDER])

    def test_half_pair_in_a_category_is_ignored(self):
        plugin.DEFAULTS["categories"]["architecture"]["model"] = "m/only-model"
        self.seq("fine")
        self.critical()
        self.assertEqual((self.llm.calls[0]["model"], self.llm.calls[0]["provider"]), (STRONG, PROVIDER))

    def test_full_pair_in_a_category_beats_olymp(self):
        plugin.DEFAULTS["categories"]["architecture"].update(model="m/arch", provider="p/arch")
        self.seq("ESCALATE: x")
        self.critical()
        self.assertEqual(self.models(), ["m/arch"])
        self.assertNotIn("ESCALATE", self.llm.calls[0]["messages"][0]["content"])

    def test_olymp_failure_is_fail_open(self):
        class Boom(SeqLlm):
            def complete(self, messages, **kw):
                if kw["model"] == "m/olymp":
                    raise RuntimeError("down")
                return super().complete(messages, **kw)
        self.llm = Boom(["ESCALATE: x"])
        plugin._ctx.llm = self.llm
        self.assertIsNone(self.critical())


class LocalContractTest(AdvisorTest):
    """completion_contract=local-only is the board default (no PR/CI gate), not a privacy flag."""

    def test_default_contract_does_not_block_the_advisor(self):
        tid = with_conn(lambda c: kb.create_task(c, title="infra change", assignee="worker",
                                                 completion_contract="local-only"))
        os.environ["HERMES_KANBAN_TASK"] = tid
        self.assertIn("[ADVISOR", self.stuck(3))


class StepsSignalTest(AdvisorTest):
    def test_steps_alone_does_not_call_the_advisor(self):
        for i in range(plugin.DEFAULTS["limits"]["steps"]):
            out = plugin.on_tool_result(tool_name="read_file", args={"path": f"/f{i}"}, result="r", status="ok")
        self.assertIsNone(out)
        self.assertEqual(self.llm.calls, [])


class EgressRedactionTest(AdvisorTest):
    """Everything sent to a remote model passes the redactor. One row per secret shape (see SECURITY.md)."""

    SECRETS = {
        "github token": "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8",
        "openai key": "sk-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6",
        "anthropic key": "sk-ant-api03-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6-xyz",
        "openrouter key": "sk-or-v1-" + "a1B2c3D4" * 8,
        "aws key id": "AKIA" + "ABCDEFGHIJKLMNOP",
        "bearer header": "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abcdefghijklmnop.qrstuvwxyz0123",
        "password assignment": "DB_PASSWORD=hunter2hunter2",
        "url credentials": "https://deploy:s3cretpass@git.example.com/org/repo.git",
    }
    NEEDLES = ("a1B2c3D4e5F6", "A1b2C3d4E5f6", "ABCDEFGHIJKLMNOP", "abcdefghijklmnop", "hunter2hunter2", "s3cretpass")

    def test_no_secret_shape_reaches_the_remote_model(self):
        for name, secret in self.SECRETS.items():
            with self.subTest(secret=name):
                os.environ["HERMES_KANBAN_TASK"] = with_conn(lambda c: kb.create_task(c, title="deploy", assignee="worker"))
                self.llm.calls.clear()
                plugin._trackers.clear()
                self.stuck(3, text=f"deploy failed, config had {secret} in it")
                sent = "\n".join(m["content"] for m in self.llm.calls[0]["messages"])
                self.assertFalse(any(n in sent for n in self.NEEDLES), (name, sent[-300:]))


class ProviderErrorTest(AdvisorTest):
    """A dead provider must not be silent: ONE visible comment per card, the worker is not disturbed."""

    def comments(self):
        return [c.body for c in advice_comments(self.tid)]

    def test_failure_leaves_one_comment_and_does_not_touch_the_result(self):
        self.llm.boom = True
        self.assertIsNone(self.stuck(3))
        plugin._trackers.clear()
        self.assertIsNone(self.stuck(3))
        bodies = self.comments()
        self.assertEqual(len(bodies), 1)
        self.assertTrue(bodies[0].startswith("[advisor unavailable]"))
        self.assertIn("provider down", bodies[0])
        self.assertIn("doctor", bodies[0])

    def test_error_text_is_redacted_and_short(self):
        token = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
        self.llm.boom = True
        self.llm.error = f"403 key {token} " + "x" * 2000
        self.stuck(3)
        body = self.comments()[0]
        self.assertNotIn(token, body)
        self.assertLess(len(body), 600)

    def test_a_later_worker_run_does_not_comment_twice(self):
        """The cooldown is per process; the comment check must hold across runs (new process = empty cooldown)."""
        plugin._report_unavailable(self.tid, RuntimeError("first run"))
        plugin._report_unavailable(self.tid, RuntimeError("second run"))
        self.assertEqual(len(self.comments()), 1)
        self.assertIn("first run", self.comments()[0])

    def test_dead_model_is_not_called_again_during_the_cooldown(self):
        self.llm.boom = True
        self.stuck(3)
        plugin._trackers.clear()
        self.stuck(3)
        self.assertEqual(len(self.llm.calls), 1)          # the second question never left
        self.assertEqual(len(self.comments()), 1)         # and made no second comment

    def test_cooldown_ends_and_a_success_clears_it(self):
        self.llm.boom = True
        self.stuck(3)
        for pair in plugin._down:
            plugin._down[pair] -= plugin.COOLDOWN_S + 1   # cooldown over
        self.llm.boom = False
        plugin._trackers.clear()
        self.assertIn("[ADVISOR", self.stuck(3))
        self.assertEqual(plugin._down, {})

    def test_cooldown_is_per_model(self):
        plugin._down[("openrouter", "other/model")] = plugin.time.time()
        self.assertIn("[ADVISOR", self.stuck(3))

    def test_failure_does_not_eat_the_advice_budget(self):
        self.llm.boom = True
        self.stuck(3)
        plugin._down.clear()
        self.llm.boom = False
        plugin._trackers.clear()
        for _ in range(plugin.DEFAULTS["advisor"]["max_consults_per_card"]):
            plugin._trackers.clear()
            self.stuck(3)
        self.assertEqual(len(self.llm.calls), 1 + plugin.DEFAULTS["advisor"]["max_consults_per_card"])

    def test_card_that_did_not_opt_in_gets_no_error_comment(self):
        os.environ["HERMES_KANBAN_BOARD"] = "other-board"
        self.llm.boom = True
        self.stuck(3)
        self.assertEqual(self.comments(), [])

    def test_failure_is_traced(self):
        self.llm.boom = True
        self.stuck(3)
        rows = [json.loads(l) for l in Path(os.environ["CALLING_MOM_LOG"]).read_text().splitlines()]
        self.assertEqual([r["error"] for r in rows if r.get("kind") == "advisor_error"], ["RuntimeError"])


class MomBlockTest(unittest.TestCase):
    """`calling_mom.mom.{model,provider}` names the cheap tier; the legacy top-level pair still works."""

    def settings_with(self, block):
        orig = _hermes_config.load_config
        _hermes_config.load_config = lambda *a, **k: {"calling_mom": block}
        try:
            return plugin.settings()
        finally:
            _hermes_config.load_config = orig

    def test_mom_pair_replaces_the_top_level_pair(self):
        s = self.settings_with({"mom": {"model": "s/m", "provider": "s-p"}})
        self.assertEqual((s["model"], s["provider"]), ("s/m", "s-p"))

    def test_mom_beats_a_legacy_pair_in_the_same_config(self):
        s = self.settings_with({"model": "old/m", "provider": "old-p", "mom": {"model": "s/m", "provider": "s-p"}})
        self.assertEqual((s["model"], s["provider"]), ("s/m", "s-p"))

    def test_half_mom_pair_is_ignored(self):
        s = self.settings_with({"mom": {"model": "s/m"}})
        self.assertEqual((s["model"], s["provider"]), (plugin.DEFAULTS["model"], plugin.DEFAULTS["provider"]))

    def test_without_a_mom_block_nothing_changes(self):
        s = self.settings_with({"model": "old/m", "provider": "old-p"})
        self.assertEqual((s["model"], s["provider"]), ("old/m", "old-p"))


class CliRegistrationTest(unittest.TestCase):
    def test_register_adds_the_calling_mom_command(self):
        from types import SimpleNamespace as NS
        seen = {}
        ctx = NS(register_hook=lambda *a, **k: None, register_tool=lambda **kw: None, llm=None,
                 register_cli_command=lambda **kw: seen.update(kw))
        plugin.register(ctx)
        self.assertEqual(seen["name"], "calling-mom")
        self.assertTrue(callable(seen["setup_fn"]) and callable(seen["handler_fn"]))

    def test_old_hermes_without_cli_registration_still_loads(self):
        from types import SimpleNamespace as NS
        plugin.register(NS(register_hook=lambda *a, **k: None, register_tool=lambda **kw: None, llm=None))


class RiskTiersTest(AdvisorTest):
    """Critical commands and `watch` commands (ssh, pct, ...) have separate budgets; the list comes from rules.py."""

    def setUp(self):
        super().setUp()
        plugin._arch_seen.clear()
        plugin._watch_used.clear()

    def run_cmd(self, command):
        return plugin.on_tool_result(tool_name="terminal", args={"command": command}, result="ok", status="ok")

    def test_ssh_reads_cannot_use_up_the_budget_of_a_later_merge(self):
        for i in range(4):
            self.run_cmd(f"ssh pi 'ls /config/{i}'")
        self.assertIn("[ADVISOR", self.run_cmd("gh pr merge 142 --squash"))
        self.assertIn("[ADVISOR", self.run_cmd("git tag v2.9.4"))

    def test_watch_commands_get_one_review_per_card(self):
        self.run_cmd("ssh pi 'ls /a'")
        self.assertIsNone(self.run_cmd("ssh pi 'ls /b'"))
        self.assertEqual(len(self.llm.calls), 1)

    def test_a_noop_answer_still_uses_the_watch_budget(self):
        self.llm._res.text = "NO_ACTION"
        self.run_cmd("ssh pi 'ls /a'")
        self.run_cmd("ssh pi 'ls /b'")
        self.run_cmd("scp f pi:/tmp/")
        self.assertEqual(len(self.llm.calls), 1)          # no comment was written, the counter still counts

    def test_critical_budget_is_four_by_default(self):
        outs = [self.run_cmd(f"kubectl -n prod delete pod p{i}") for i in range(6)]
        self.assertEqual(sum(1 for o in outs if o), 4)

    def test_curl_write_to_the_github_api_is_critical(self):
        cmd = 'curl -sf -X PUT -H "Authorization: token T" https://api.github.com/repos/o/r/pulls/1/merge'
        self.assertIn("[ADVISOR", self.run_cmd(cmd))

    def test_a_critical_command_inside_ssh_is_critical_not_watch(self):
        self.run_cmd("ssh pi 'ls /a'")                      # uses the watch budget
        self.assertIn("[ADVISOR", self.run_cmd("ssh pi 'kubectl -n prod delete pvc x'"))

    def test_commands_extra_adds_your_own_patterns(self):
        self.assertIsNone(self.run_cmd("flyctl deploy"))
        cats = plugin.DEFAULTS["categories"]
        plugin.DEFAULTS["categories"] = {**cats, "architecture": {**cats["architecture"], "commands_extra": [r"\bflyctl deploy\b"]}}
        self.assertIn("[ADVISOR", self.run_cmd("flyctl deploy"))

    def test_watch_is_never_escalated_to_olymp(self):
        plugin.DEFAULTS["olymp"].update(model="m/olymp", provider="p/olymp")
        self.llm = SeqLlm(["ESCALATE: x", "olymp must not be asked"])
        plugin._ctx.llm = self.llm
        self.run_cmd("ssh pi 'ls /a'")
        self.assertEqual([c["model"] for c in self.llm.calls], [STRONG])


class CompletionPerRunTest(AdvisorTest):
    def complete(self):
        return plugin.on_pre_tool(tool_name="kanban_complete", args={"summary": "done"})

    def test_every_run_gets_its_own_completion_check(self):
        self.llm._res.text = "DONE"
        for run in ("1", "2", "3"):
            os.environ["HERMES_KANBAN_RUN_ID"] = run
            self.complete()
        self.assertEqual(len(self.llm.calls), 3)

    def test_still_once_per_run(self):
        self.llm._res.text = "DONE"
        self.complete()
        plugin._completion_checked.clear()                # same run, e.g. after a restart of the process
        self.complete()
        self.assertEqual(len(self.llm.calls), 1)


class DefensivePathsTest(AdvisorTest):
    """Paths the old basename-based coverage counted by accident (any package's __init__.py matched)."""

    def test_merge_keeps_the_default_for_a_wrong_type(self):
        self.assertEqual(plugin._merge({"a": 1}, "x"), {"a": 1})
        self.assertEqual(plugin._merge(1.5, "x"), 1.5)
        self.assertEqual(plugin._merge(None, 5), 5)
        self.assertIsNone(plugin._merge(None, None))

    def test_profile_runs_strong_is_false_when_the_config_breaks(self):
        orig = _hermes_config.load_config
        _hermes_config.load_config = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x"))
        try:
            self.assertFalse(plugin._profile_runs_strong())
        finally:
            _hermes_config.load_config = orig

    def test_a_card_that_vanished_is_not_escalated(self):
        task = type("T", (), {"id": "t1", "status": "blocked", "block_kind": None, "model_override": None,
                              "last_failure_error": "boom", "title": "x", "body": ""})()

        class Ctx:
            def __enter__(self):
                return "CONN"

            def __exit__(self, *exc):
                return False

        orig = (plugin._connect, plugin.kb)
        plugin._connect = lambda board=None: Ctx()
        plugin.kb = type("KB", (), {"get_task": staticmethod(lambda c, i: None)})()
        try:
            self.assertFalse(plugin._escalate_parked_task(task, {"error": "boom"}, "default"))
        finally:
            plugin._connect, plugin.kb = orig

    def test_dispatch_tick_sweeps_without_error(self):
        plugin.on_dispatch_tick(board=None)

    def test_yesterdays_spend_does_not_count(self):
        plugin._spend_path().write_text(json.dumps({"date": "2000-01-01", "usd": 9.0, "tokens": 9, "olymp": 9}))
        self.assertEqual(plugin._spent_today(), {"usd": 0.0, "tokens": 0, "olymp": 0})

    def test_earlier_run_summaries_reach_the_advisor_prompt(self):
        def add_run(c):
            c.execute("INSERT INTO task_runs (task_id, status, started_at, summary) VALUES (?, 'done', 1, 'earlier result')",
                      (self.tid,))
            c.commit()
        with_conn(add_run)
        self.stuck(3)
        self.assertIn("earlier result", self.llm.calls[0]["messages"][1]["content"])


class VerifySecurityTest(unittest.TestCase):
    """Step A: `verify:` is card text. It must not become a shell for whoever wrote the card."""

    def setUp(self):
        os.environ["CALLING_MOM"] = "on"
        os.environ["CALLING_MOM_LOG"] = str(Path(tempfile.mkdtemp()) / "v.jsonl")
        self.ws = tempfile.mkdtemp()
        os.environ["HERMES_KANBAN_WORKSPACE"] = self.ws
        os.environ["HERMES_KANBAN_RUN_ID"] = "1"
        plugin._verify_fails.clear()

    def tearDown(self):
        for k in ("HERMES_KANBAN_TASK", "HERMES_KANBAN_RUN_ID", "HERMES_KANBAN_WORKSPACE", "SECRET_TOKEN"):
            os.environ.pop(k, None)

    def card(self, body, creator="user"):
        tid = with_conn(lambda c: kb.create_task(c, title="sec card", body=body, assignee="worker", created_by=creator))
        os.environ["HERMES_KANBAN_TASK"] = tid

    def gate(self):
        return plugin.on_pre_tool(tool_name="kanban_complete", args={})

    def test_shell_metacharacters_are_not_interpreted(self):
        self.card("verify: true; touch PWNED")
        self.assertIsNone(self.gate())
        self.assertFalse(Path(self.ws, "PWNED").exists())

    def test_command_outside_the_allowlist_is_not_run(self):
        self.card("verify: touch PWNED")
        self.assertIsNone(self.gate())
        self.assertFalse(Path(self.ws, "PWNED").exists())

    def test_cards_from_untrusted_creators_are_not_run(self):
        self.card('verify: python3 -c "open(\'ran\', \'w\').close(); raise SystemExit(1)"', creator="email")
        self.assertIsNone(self.gate())
        self.assertFalse(Path(self.ws, "ran").exists())

    def test_ignored_verify_leaves_a_trace_row(self):
        self.card("verify: touch PWNED")
        self.gate()
        rows = [json.loads(x) for x in Path(os.environ["CALLING_MOM_LOG"]).read_text().splitlines()]
        self.assertTrue(any(r.get("kind") == "verify_ignored" for r in rows))

    def test_environment_is_scrubbed(self):
        os.environ["SECRET_TOKEN"] = "hunter2"
        self.card('verify: python3 -c "import os, sys; sys.exit(1 if \'SECRET_TOKEN\' in os.environ else 0)"')
        self.assertIsNone(self.gate())   # exit 0 -> the token was not visible


class BlockedCardTest(unittest.TestCase):
    """A card the weak model gave up on is pinned to the strong model once; the board's own breaker does the rest."""

    def setUp(self):
        os.environ["CALLING_MOM"] = "on"

    def give_up(self, tid, reason="stuck"):
        with_conn(lambda c: kb.block_task(c, tid, reason=reason, kind="capability"))
        plugin.on_blocked(task_id=tid, board=None, reason=reason)

    def test_first_give_up_pins_the_strong_model(self):
        tid = with_conn(lambda c: kb.create_task(c, title="hard", assignee="worker"))
        self.give_up(tid)
        t = self_task(tid)
        self.assertEqual((t.reasoning_effort, t.model_override, t.status), (None, STRONG, "ready"))

    def test_second_give_up_is_left_to_the_board_and_a_human(self):
        tid = with_conn(lambda c: kb.create_task(c, title="hard 2", assignee="worker"))
        self.give_up(tid); self.give_up(tid, "still stuck")
        t = self_task(tid)
        self.assertEqual(t.model_override, STRONG)
        self.assertIn(t.status, {"blocked", "triage"})   # the strong model had its attempt

    def test_an_untyped_block_on_a_card_without_assignee_is_a_human_to_do_and_stays(self):
        tid = with_conn(lambda c: kb.create_task(c, title="buy milk"))
        with_conn(lambda c: kb.block_task(c, tid, reason="waiting for the shop"))   # untyped
        plugin.on_blocked(task_id=tid, board=None, reason="waiting for the shop")
        t = self_task(tid)
        self.assertEqual((t.model_override, t.status), (None, "blocked"))

    def test_an_untyped_block_on_an_assigned_card_still_escalates(self):
        tid = with_conn(lambda c: kb.create_task(c, title="crashy", assignee="worker"))
        with_conn(lambda c: kb.block_task(c, tid, reason="crashed"))
        plugin.on_blocked(task_id=tid, board=None, reason="crashed")
        t = self_task(tid)
        self.assertEqual((t.model_override, t.status), (STRONG, "ready"))

    def test_a_typed_capability_block_without_assignee_still_escalates(self):
        tid = with_conn(lambda c: kb.create_task(c, title="orphan hard"))
        self.give_up(tid)
        self.assertEqual(self_task(tid).model_override, STRONG)

    def test_the_plugin_never_writes_the_board_tables_directly(self):
        self.assertNotIn("UPDATE tasks", Path(plugin.__file__).read_text())

    def test_a_profile_that_already_runs_the_strong_model_is_not_re_pinned(self):
        import hermes_cli.config as cfg
        old = cfg.load_config
        cfg.load_config = lambda *a, **k: {"model": {"default": STRONG}}
        try:
            tid = with_conn(lambda c: kb.create_task(c, title="reviewer block", assignee="worker"))
            with_conn(lambda c: kb.block_task(c, tid, reason="cannot", kind="capability"))
            plugin.on_blocked(task_id=tid, board=None, reason="cannot")
        finally:
            cfg.load_config = old
        t = self_task(tid)
        self.assertEqual((t.model_override, t.reasoning_effort, t.status), (None, None, "blocked"))


class ReviewRedirectTest(unittest.TestCase):
    """Step C2: a `[review]` card is not completed by the weak model; the board hands it to the reviewer profile."""

    def setUp(self):
        os.environ["CALLING_MOM"] = "on"
        os.environ["CALLING_MOM_LOG"] = str(Path(tempfile.mkdtemp()) / "r.jsonl")
        os.environ.pop("HERMES_PROFILE", None)
        self.saved = {k: dict(v) if isinstance(v, dict) else v for k, v in plugin.DEFAULTS.items()}
        plugin.DEFAULTS["review"]["reviewer"] = "rev"
        self._real_exists = profiles.profile_exists
        profiles.profile_exists = lambda name: name == "rev"   # no installed profile needed
        plugin._redirected.clear()

    def tearDown(self):
        profiles.profile_exists = self._real_exists
        plugin.DEFAULTS.clear(); plugin.DEFAULTS.update(self.saved)
        for k in ("HERMES_KANBAN_TASK", "HERMES_KANBAN_RUN_ID", "HERMES_PROFILE"):
            os.environ.pop(k, None)

    def card(self, title="[review] change the schema", body=None):
        tid = with_conn(lambda c: kb.create_task(c, title=title, body=body, assignee="implementer", created_by="user"))
        os.environ["HERMES_KANBAN_TASK"] = tid
        return tid

    def complete(self, **args):
        return plugin.on_pre_tool(tool_name="kanban_complete", args={"summary": "did the work", **args})

    def test_completion_is_redirected_to_review_by_the_reviewer_profile(self):
        tid = self.card()
        out = self.complete()
        self.assertEqual(out["action"], "block")
        self.assertIn("review", out["message"].lower())
        t = self_task(tid)
        self.assertEqual((t.status, t.assignee), ("review", "rev"))

    def test_review_summary_is_the_workers_summary(self):
        tid = self.card()
        self.complete(summary="implemented X, tests pass")
        events = with_conn(lambda c: c.execute("select payload from task_events where task_id=? and kind='review_requested'", (tid,)).fetchall())
        self.assertIn("implemented X", events[0][0])

    def test_card_without_marker_completes_normally(self):
        self.card(title="plain card")
        self.assertIsNone(self.complete())

    def test_the_reviewer_profile_itself_may_complete(self):
        tid = self.card()
        os.environ["HERMES_PROFILE"] = "rev"                      # == configured reviewer
        self.assertIsNone(self.complete())
        self.assertNotEqual(self_task(tid).status, "review")

    def test_local_only_cards_are_never_handed_to_a_remote_reviewer(self):
        self.card(title="[review] [local-only] secrets")
        self.assertIsNone(self.complete())

    def test_a_worker_calling_request_review_itself_is_not_touched(self):
        self.card()
        self.assertIsNone(plugin.on_pre_tool(tool_name="kanban_request_review", args={"summary": "s"}))

    def test_review_handoff_refused_returns_none(self):
        """When kb.request_review returns ok=False, the function logs a warning and returns None."""
        tid = self.card()
        # Mock kb.request_review to return ok=False
        original = kb.request_review
        kb.request_review = lambda *a, **k: (False, "reviewer profile not found")
        try:
            out = self.complete()
            self.assertIsNone(out)
        finally:
            kb.request_review = original

    def test_failing_verification_still_wins_over_the_redirect(self):
        tid = self.card(body='verify: python3 -c "raise SystemExit(1)"')
        os.environ["HERMES_KANBAN_WORKSPACE"] = tempfile.mkdtemp()
        out = self.complete()
        self.assertIn("Verification failed", out["message"])
        self.assertNotEqual(self_task(tid).status, "review")

    def test_unknown_reviewer_profile_fails_open(self):
        plugin.DEFAULTS["review"]["reviewer"] = "no-such-profile-xyz"
        tid = self.card()
        self.assertIsNone(self.complete())
        self.assertNotEqual(self_task(tid).status, "review")

    def test_disabled_by_empty_reviewer(self):
        plugin.DEFAULTS["review"]["reviewer"] = ""
        self.card()
        self.assertIsNone(self.complete())


class AdvisorContextTest(unittest.TestCase):
    """Test that the advisor context includes the current subtask and previous run results."""

    def test_build_messages_includes_latest_comment_and_prev_runs(self):
        import types
        task = types.SimpleNamespace(title="test task", body="test body")
        steps = [{"tool": "terminal", "args": {"command": "x"}, "ok": True, "text": "ok"}]
        # Without context
        msgs = plugin._build_messages(task, "steps", steps, "", 6000)
        user_content = msgs[1]["content"]
        self.assertNotIn("Current subtask", user_content)
        self.assertNotIn("Previous run results", user_content)
        # With context
        msgs = plugin._build_messages(
            task, "steps", steps, "", 6000,
            latest_comment="bereinige noch die alten php s3 sachen",
            prev_runs=["Run 52: Garage repair + HTML-TTFB fixed", "Run 51: Garage Web + Pangolin implemented"],
        )
        user_content = msgs[1]["content"]
        self.assertIn("Current subtask", user_content)
        self.assertIn("bereinige noch die alten php s3 sachen", user_content)
        self.assertIn("Previous run results", user_content)
        self.assertIn("Garage repair + HTML-TTFB fixed", user_content)
        self.assertIn("Garage Web + Pangolin implemented", user_content)


# --- WP1: no hook ever reaches Hermes's hook timeout ----------------------------------------------
# Hermes cuts a hook off at plugins.hook_callback_timeout. A small budget keeps these tests fast; every
# bound below is a FRACTION of it, so the tests hold on any machine speed (a stub that sleeps 45 s against
# a 30 s budget becomes "blocked until the test releases it" against a 6 s budget).
BUDGET = 6


def settle(timeout=10):
    """Wait until no background advice call is in flight (a test must not leak a thread into the next)."""
    end = time.monotonic() + timeout
    while plugin._inflight and time.monotonic() < end:
        time.sleep(0.01)
    assert not plugin._inflight, "advice thread still running"


class BudgetMixin:
    def set_budget(self, seconds=BUDGET):
        self._saved_load = _hermes_config.load_config
        _hermes_config.load_config = lambda *a, **k: {"plugins": {"hook_callback_timeout": seconds}}
        self.addCleanup(setattr, _hermes_config, "load_config", self._saved_load)


class GatedLlm(FakeLlm):
    """A Mom that answers only when the test lets her go: stands in for a model that takes 45 s."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.release, self.started, self.entered = threading.Event(), threading.Event(), 0

    def complete(self, messages, **kw):
        self.entered += 1
        self.started.set()
        self.release.wait(30)
        return super().complete(messages, **kw)


def log_rows(kind=None):
    path = Path(os.environ["CALLING_MOM_LOG"])
    rows = [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []
    return [r for r in rows if kind is None or r.get("kind") == kind]


class SlowMomTest(BudgetMixin, AdvisorFixture, unittest.TestCase):
    """Advice runs off the hot path: a Mom that is slow costs the worker nothing, the advice arrives late."""

    def setUp(self):
        super().setUp()
        self.set_budget()
        self.llm = GatedLlm()
        plugin._ctx.llm = self.llm
        plugin._late.clear(); plugin._inflight.clear(); plugin._completion_checked.clear()

    def tearDown(self):
        self.llm.release.set()
        settle()
        super().tearDown()

    def timed(self, fn, *a, **k):
        started = time.monotonic()
        out = fn(*a, **k)
        return out, time.monotonic() - started

    def later(self, tool="read_file", result="file text", **args):
        return plugin.on_tool_result(tool_name=tool, args=args or {"path": "/a"}, result=result, status="ok")

    def test_a_slow_mom_does_not_hold_the_tool_result(self):
        out, took = self.timed(self.stuck, 3)
        self.assertIsNone(out)
        self.assertLess(took, 0.4 * BUDGET)
        self.assertTrue(self.llm.started.wait(5))            # she was asked, in the background
        self.assertEqual(advice_comments(self.tid), [])

    def test_the_advice_comes_with_the_next_tool_result_and_lands_on_the_card(self):
        self.stuck(3)
        self.llm.release.set()
        settle()
        self.assertEqual(len(advice_comments(self.tid)), 1)  # the card comment is posted when the advice arrives
        out = self.later()
        self.assertTrue(out.startswith("file text"))
        self.assertIn("Root cause: X", out)
        self.assertIn("late", out.lower())
        self.assertEqual(self.later(path="/b"), "file text" if False else None)   # delivered exactly once
        rows = log_rows("advice_late")
        self.assertEqual([(r["signal"], r["task"]) for r in rows], [("same_error", self.tid)])
        self.assertIsInstance(rows[0]["ms"], int)

    def test_late_advice_waits_for_a_text_result(self):
        self.stuck(3)
        self.llm.release.set()
        settle()
        self.assertIsNone(plugin.on_tool_result(tool_name="read_file", args={"path": "/a"}, result=None, status="ok"))
        self.assertIn("Root cause: X", self.later())

    def test_one_advice_call_in_flight_per_run(self):
        self.stuck(3)
        self.assertTrue(self.llm.started.wait(5))
        plugin._trackers.clear()
        out, took = self.timed(self.stuck, 3)                # a second trigger while the first is pending
        self.assertIsNone(out)
        self.assertLess(took, 0.4 * BUDGET)
        self.llm.release.set()
        settle()
        self.assertEqual(self.llm.entered, 1)

    def test_a_late_followed_verdict_counts_from_the_delivery_not_the_generation(self):
        self.stuck(3)
        self.llm.release.set()
        settle()
        plugin.on_api_response(assistant_message=types.SimpleNamespace(content="Running the tests now."))
        self.assertEqual(log_rows("advice_feedback"), [])    # the worker has not seen the advice yet
        self.later()
        plugin.on_api_response(assistant_message=types.SimpleNamespace(content="Good point, thanks."))
        self.assertEqual([r["verdict"] for r in log_rows("advice_feedback")], ["followed"])

    def test_a_failing_background_call_leaves_no_trace_on_the_worker(self):
        self.llm.boom = True
        self.llm.release.set()
        self.assertIsNone(self.stuck(3))
        settle()
        self.assertIsNone(self.later())

    def test_a_slow_mom_does_not_hold_kanban_complete(self):
        out, took = self.timed(plugin.on_pre_tool, tool_name="kanban_complete", args={"summary": "all done"})
        self.assertIsNone(out)                               # fail-open: the card may finish
        self.assertLess(took, 0.4 * BUDGET)
        self.assertTrue(self.llm.started.wait(5))
        self.llm.release.set()
        settle()
        self.assertEqual(len(advice_comments(self.tid)), 1)  # the verdict still reaches the card

    def test_a_slow_mom_does_not_hold_the_verify_failure(self):
        ws = tempfile.mkdtemp()
        os.environ["HERMES_KANBAN_WORKSPACE"] = ws
        self.addCleanup(os.environ.pop, "HERMES_KANBAN_WORKSPACE", None)
        tid = with_conn(lambda c: kb.create_task(c, title="vf", body="verify: false", assignee="worker", created_by="user"))
        os.environ["HERMES_KANBAN_TASK"] = tid
        plugin._verify_fails.clear(); plugin._verify_runs.clear()
        plugin.on_pre_tool(tool_name="kanban_complete", args={})
        out, took = self.timed(plugin.on_pre_tool, tool_name="kanban_complete", args={})   # 2nd failure asks Mom
        self.assertEqual(out["action"], "block")
        self.assertNotIn("[ADVISOR", out["message"])
        self.assertLess(took, 0.4 * BUDGET)

    def test_olymp_escalation_runs_in_the_background_too(self):
        plugin.DEFAULTS["olymp"].update(model="m/olymp", provider="p/olymp")
        plugin.DEFAULTS["categories"] = {"architecture": {"max": 2, "patterns": [], "commands": [r"terraform apply"]}}
        plugin._arch_seen.clear()
        gate = self.llm

        class Mom(SeqLlm):
            def complete(self, messages, **kw):
                if kw["model"] == "m/olymp":
                    gate.entered += 1
                    gate.started.set()
                    gate.release.wait(30)
                return super().complete(messages, **kw)

        plugin._ctx.llm = Mom(["ESCALATE: prod deploy", "Olymp says: keep a fallback."])
        out, took = self.timed(plugin.on_tool_result, tool_name="terminal", args={"command": "terraform apply"},
                               result="r", status="ok")
        self.assertIsNone(out)
        self.assertLess(took, 0.4 * BUDGET)
        self.assertTrue(gate.started.wait(5))


class PromptSectionTest(unittest.TestCase):
    """The tip lands in the system prompt of Kanban workers only, never in a chat session."""

    def registered(self):
        seen = {}
        plugin.register(types.SimpleNamespace(
            register_hook=lambda *a, **k: None, register_tool=lambda **kw: None,
            register_system_prompt_section=lambda **kw: seen.update(kw)))
        return seen

    def test_content_is_a_callable_that_is_empty_outside_kanban_workers(self):
        os.environ.pop("HERMES_KANBAN_TASK", None)
        seen = self.registered()
        self.assertTrue(callable(seen["content"]))
        self.assertEqual(seen["content"]({}), "")

    def test_a_kanban_worker_gets_the_section(self):
        os.environ["HERMES_KANBAN_TASK"] = "t_x"
        self.addCleanup(os.environ.pop, "HERMES_KANBAN_TASK", None)
        text = self.registered()["content"]({})
        self.assertIn("SECOND-OPINION", text)
        self.assertIn("verify:", text)


class AsyncVerifyTest(BudgetMixin, unittest.TestCase):
    """`verify:` runs on a background thread: kanban_complete never waits longer than the hook budget allows."""

    def setUp(self):
        os.environ["CALLING_MOM"] = "on"
        os.environ["CALLING_MOM_LOG"] = str(Path(tempfile.mkdtemp()) / "v.jsonl")
        self.ws = tempfile.mkdtemp()
        os.environ["HERMES_KANBAN_WORKSPACE"] = self.ws
        os.environ["HERMES_KANBAN_RUN_ID"] = "1"
        plugin._verify_fails.clear(); plugin._verify_runs.clear(); plugin._edits.clear()
        self.set_budget()
        # the command ends when the test creates ./go: no sleep, no race with machine load
        Path(self.ws, "wait.py").write_text("import os, time\nwhile not os.path.exists('go'):\n    time.sleep(0.02)\n")

    def tearDown(self):
        Path(self.ws, "go").write_text("")
        for run in list(plugin._verify_runs.values()):
            run.done.wait(10)
        for k in ("HERMES_KANBAN_TASK", "HERMES_KANBAN_RUN_ID", "HERMES_KANBAN_WORKSPACE"):
            os.environ.pop(k, None)

    def card(self, body):
        tid = with_conn(lambda c: kb.create_task(c, title="verify card", body=body, assignee="worker", created_by="user"))
        os.environ["HERMES_KANBAN_TASK"] = tid
        return tid

    def gate(self, tool="kanban_complete"):
        started = time.monotonic()
        out = plugin.on_pre_tool(tool_name=tool, args={})
        self.took = time.monotonic() - started
        return out

    def states(self):
        return [r["state"] for r in log_rows("verify")]

    def release(self):
        Path(self.ws, "go").write_text("")

    def test_default_timeout_is_ten_minutes(self):
        self.assertEqual(load_plugin().DEFAULTS["verify"]["timeout_s"], 600)

    def test_a_long_command_blocks_with_running_and_a_later_call_passes(self):
        self.card("verify: python3 wait.py")
        out = self.gate()
        self.assertEqual(out["action"], "block")
        self.assertIn("verify running: `python3 wait.py`", out["message"])
        self.assertIn("Do not edit files", out["message"])
        self.assertIn("kanban_complete again", out["message"])
        self.assertLess(self.took, 0.4 * BUDGET)
        self.release()
        self.assertIsNone(self.gate())                       # each call waits up to 0.3 x budget for the result
        self.assertEqual(self.states(), ["started", "pass"])
        self.assertEqual(log_rows("verify")[1]["exit"], 0)
        self.assertIsInstance(log_rows("verify")[1]["ms"], int)

    def test_a_pass_holds_while_the_workspace_is_unchanged(self):
        self.card("verify: python3 wait.py")
        self.release()
        self.assertIsNone(self.gate())
        self.assertIsNone(self.gate())
        self.assertEqual(self.states(), ["started", "pass"])  # no second run

    def test_the_review_handoff_is_gated_the_same_way(self):
        self.card("verify: python3 wait.py")
        self.assertIn("verify running", self.gate("kanban_request_review")["message"])

    def test_an_edit_after_a_pass_forces_a_new_run(self):
        self.card("verify: python3 wait.py")
        self.release()
        self.assertIsNone(self.gate())
        self.assertIsNone(plugin.on_pre_tool(tool_name="write_file", args={"path": "x.py", "content": "1"}))
        Path(self.ws, "go").unlink()
        self.assertIn("verify running", self.gate()["message"])
        self.assertEqual(self.states(), ["started", "pass", "stale", "started"])
        self.release()
        self.assertIsNone(self.gate())

    def test_an_edit_while_the_command_runs_discards_its_result(self):
        self.card("verify: python3 wait.py")
        self.assertIn("verify running", self.gate()["message"])
        plugin.on_pre_tool(tool_name="patch", args={"path": "x.py"})
        self.assertIn("verify running", self.gate()["message"])   # the old run is dropped, a new one starts
        self.assertEqual(self.states(), ["started", "stale", "started"])
        self.release()
        self.assertIsNone(self.gate())
        self.assertEqual(self.states(), ["started", "stale", "started", "pass"])   # never a pass for the old run

    def test_a_failure_is_reported_once_then_the_next_call_runs_again(self):
        self.card("verify: python3 -c \"import sys; sys.stderr.write('boom'); sys.exit(3)\"")
        out = self.gate()
        self.assertIn("exit 3", out["message"])
        self.assertIn("boom", out["message"])
        self.assertEqual(self.states(), ["started", "fail"])
        self.gate()
        self.assertEqual(self.states(), ["started", "fail", "started", "fail"])
        self.assertEqual(log_rows("verify")[1]["exit"], 3)

    def test_a_command_that_runs_too_long_times_out_and_its_children_die(self):
        Path(self.ws, "tree.py").write_text(
            "import subprocess, sys, time\n"
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            "time.sleep(60)\n")
        self.card("verify: python3 tree.py")
        plugin.DEFAULTS["verify"]["timeout_s"] = 1
        self.addCleanup(plugin.DEFAULTS["verify"].__setitem__, "timeout_s", 600)
        out = self.gate()                                     # the pipe closes only when the whole tree is killed
        self.assertIn("timed out after 1s", out["message"])
        self.assertEqual(self.states(), ["started", "timeout"])

    def test_a_command_that_cannot_start_is_a_failure_with_a_reason(self):
        self.card("verify: python3 wait.py")
        real = plugin.subprocess.Popen
        plugin.subprocess.Popen = lambda *a, **k: (_ for _ in ()).throw(OSError("no exec"))
        try:
            out = self.gate()
        finally:
            plugin.subprocess.Popen = real
        self.assertIn("could not start: no exec", out["message"])

    def test_a_failing_check_passes_after_max_fix_plus_two_fails(self):
        self.card("verify: false")
        msgs = [self.gate() for _ in range(6)]
        self.assertTrue(all(m and m["action"] == "block" for m in msgs[:5]))
        self.assertIsNone(msgs[5])                            # never trap the worker

    def test_a_broken_gate_never_traps_the_worker(self):
        self.card("verify: python3 wait.py")
        real = plugin._workspace_state
        plugin._workspace_state = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("git exploded"))
        try:
            self.assertIsNone(self.gate())
        finally:
            plugin._workspace_state = real


class GitWorkspaceTest(AsyncVerifyTest):
    """In a git workspace the state is HEAD plus `git status`: an edit by any means forces a new run,
    but the artifacts the command writes itself do not invalidate its own result."""

    def setUp(self):
        super().setUp()
        self.git("init", "-q")
        Path(self.ws, "app.py").write_text("x = 1\n")
        Path(self.ws, "mk.py").write_text("open('artifact.txt', 'w').write('built')\n")
        Path(self.ws, ".gitignore").write_text("go\n")
        self.git("add", "-A")
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.ws, check=True, capture_output=True, text=True).stdout

    def test_an_edit_by_any_means_forces_a_new_run(self):
        self.card("verify: python3 wait.py")
        self.release()
        self.assertIsNone(self.gate())
        Path(self.ws, "app.py").write_text("x = 2\n")        # not through write_file or patch
        Path(self.ws, "go").unlink()
        self.assertIn("verify running", self.gate()["message"])
        self.assertEqual(self.states(), ["started", "pass", "stale", "started"])

    def test_a_new_commit_forces_a_new_run(self):
        self.card("verify: python3 wait.py")
        self.release()
        self.assertIsNone(self.gate())
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "more")
        self.assertIsNone(self.gate())
        self.assertEqual(self.states(), ["started", "pass", "stale", "started", "pass"])

    def test_the_artifacts_of_the_command_do_not_invalidate_its_result(self):
        self.card("verify: python3 mk.py")
        self.assertIsNone(self.gate())
        self.assertTrue(Path(self.ws, "artifact.txt").exists())
        self.assertIsNone(self.gate())
        self.assertEqual(self.states(), ["started", "pass"])

    def test_a_workspace_that_is_not_a_git_repo_falls_back_to_the_edit_counter(self):
        self.assertEqual(plugin._workspace_state(tempfile.mkdtemp(), 3)[0], None)
        self.assertEqual(plugin._workspace_state(None, 3)[0], None)


if __name__ == "__main__":
    unittest.main()


class PreflightTest(AdvisorFixture, unittest.TestCase):
    """WP2: Mom looks at a critical command before it runs. HOLD blocks and persists, everything else fails open."""

    CMD = "git push --force origin main"

    def setUp(self):
        super().setUp()
        self._state = os.environ.get("CALLING_MOM_STATE")
        os.environ["CALLING_MOM_STATE"] = str(Path(tempfile.mkdtemp()) / "state.json")
        self.llm = FakeLlm(text="GO", cost=0.001)
        plugin._ctx.llm = self.llm

    def tearDown(self):
        super().tearDown()
        if self._state is None:
            os.environ.pop("CALLING_MOM_STATE", None)
        else:
            os.environ["CALLING_MOM_STATE"] = self._state

    def run_cmd(self, command=None, tool="terminal"):
        args = {"command": command or self.CMD} if tool == "terminal" else {"code": command or self.CMD}
        return plugin.on_pre_tool(tool_name=tool, args=args)

    def hold_with(self, text="HOLD: it rewrites shared history. Instead: push a branch and open a PR."):
        self.llm.text = text
        self.llm._res.text = text

    def rows(self, kind="preflight"):
        path = Path(os.environ["CALLING_MOM_LOG"])
        rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        return [r for r in rows if r.get("kind") == kind]

    def comments(self):
        return [c.body for c in with_conn(lambda c: kb.list_comments(c, self.tid))]

    # --- GO ---------------------------------------------------------------------------------------------

    def test_go_allows_the_command_and_logs_a_prose_free_row(self):
        self.assertIsNone(self.run_cmd())
        self.assertEqual(len(self.llm.calls), 1)
        (row,) = self.rows()
        self.assertEqual(row["verdict"], "go")
        self.assertEqual(row["task"], self.tid)
        self.assertEqual(row["cmd_hash"], plugin.cmd_hash(self.CMD))
        self.assertEqual(row["model"], STRONG)
        self.assertNotIn("origin", json.dumps(row))

    def test_the_question_carries_the_card_and_the_command_and_asks_for_go_or_hold(self):
        self.run_cmd()
        system, user = (m["content"] for m in self.llm.calls[0]["messages"])
        self.assertIn("HOLD:", system)
        self.assertIn("fix the parser", user)
        self.assertIn(self.CMD, user)

    def test_the_question_carries_the_last_steps(self):
        plugin._history[plugin._worker_key()] = [
            {"tool": "terminal", "args": "{\"command\": \"ls\"}", "ok": True, "text": "ok"}
        ]
        self.run_cmd()
        user = self.llm.calls[0]["messages"][1]["content"]
        self.assertIn("Last steps:", user)
        self.assertIn("terminal", user)

    def test_execute_code_is_checked_like_a_terminal(self):
        self.run_cmd(tool="execute_code")
        self.assertEqual(len(self.llm.calls), 1)

    def test_the_prompt_is_redacted(self):
        token = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
        self.run_cmd(f"git push --force https://{token}@github.com/o/r main")
        self.assertNotIn(token, "\n".join(m["content"] for m in self.llm.calls[0]["messages"]))

    # --- HOLD -------------------------------------------------------------------------------------------

    def test_hold_blocks_with_reason_and_alternative_and_posts_the_card_comment(self):
        self.hold_with()
        out = self.run_cmd()
        self.assertEqual(out["action"], "block")
        self.assertIn("calling-mom pre-flight HOLD: it rewrites shared history", out["message"])
        self.assertIn("Instead: push a branch and open a PR", out["message"])
        self.assertIn("stays blocked on this card", out["message"])
        self.assertIn("kanban_block(kind='needs_input')", out["message"])
        self.assertTrue(any(c.startswith("[pre-flight] HOLD: it rewrites shared history") for c in self.comments()))
        self.assertEqual(self.rows()[0]["verdict"], "hold")

    def test_hold_is_persisted_under_task_and_command_hash(self):
        self.hold_with()
        self.run_cmd()
        hold = plugin.active_hold(self.tid, plugin.cmd_hash(self.CMD))
        self.assertEqual(hold["board"], "t-board")
        self.assertIn("rewrites shared history", hold["reason"])
        self.assertEqual([h["cmd_hash"] for h in plugin.holds_for(self.tid)], [plugin.cmd_hash(self.CMD)])

    def test_identical_retry_is_held_without_a_model_call(self):
        self.hold_with()
        self.run_cmd()
        again = self.run_cmd("git  push --force   origin main\n")  # same command, other whitespace
        self.assertEqual(again["action"], "block")
        self.assertIn("rewrites shared history", again["message"])
        self.assertEqual(len(self.llm.calls), 1)
        self.assertEqual([r["verdict"] for r in self.rows()], ["hold", "held_again"])

    def test_the_hold_survives_a_new_run_of_the_card(self):
        self.hold_with()
        self.run_cmd()
        os.environ["HERMES_KANBAN_RUN_ID"] = "2"
        plugin._history.clear()
        self.assertEqual(self.run_cmd()["action"], "block")
        self.assertEqual(len(self.llm.calls), 1)

    def test_a_different_critical_command_is_asked_again(self):
        self.hold_with()
        self.run_cmd()
        self.assertEqual(self.run_cmd("git push --force origin release")["action"], "block")
        self.assertEqual(len(self.llm.calls), 2)

    def test_a_hold_of_one_card_does_not_hold_another(self):
        self.hold_with()
        self.run_cmd()
        other = with_conn(lambda c: kb.create_task(c, title="other card", assignee="worker"))
        os.environ["HERMES_KANBAN_TASK"] = other
        self.llm.text = self.llm._res.text = "GO"
        self.assertIsNone(self.run_cmd())
        self.assertEqual(len(self.llm.calls), 2)

    def test_a_hold_without_an_alternative_still_holds(self):
        self.hold_with("HOLD: this deletes the remote branch")
        out = self.run_cmd()
        self.assertEqual(out["action"], "block")
        self.assertIn("this deletes the remote branch", out["message"])
        self.assertIn("Instead:", out["message"])

    # --- release ----------------------------------------------------------------------------------------

    def test_an_allowance_lets_the_held_command_run_once(self):
        self.hold_with()
        self.run_cmd()
        h = plugin.cmd_hash(self.CMD)
        self.assertTrue(plugin.allow_once("t-board", self.tid, h, by="cli"))
        self.assertIsNone(self.run_cmd())                     # allowed, no model call
        self.assertEqual(len(self.llm.calls), 1)
        self.assertEqual([r["verdict"] for r in self.rows()], ["hold", "allowed"])
        self.assertEqual(self.rows()[1]["reason"], "allowance")
        self.assertIsNone(plugin.active_hold(self.tid, h))    # consumed
        self.run_cmd()                                         # the next identical command is asked again
        self.assertEqual(len(self.llm.calls), 2)

    def test_an_allowance_needs_a_hold(self):
        self.assertFalse(plugin.allow_once("t-board", self.tid, plugin.cmd_hash(self.CMD), by="cli"))
        self.run_cmd()
        self.assertEqual(len(self.llm.calls), 1)               # nothing was pre-approved

    def test_consuming_no_allowance_is_a_noop(self):
        plugin._consume_allowance(self.tid, "nope")            # nothing to remove: stays empty
        self.assertIsNone(plugin.active_hold(self.tid, "nope"))

    def test_olymp_and_the_worker_have_no_way_to_release(self):
        self.hold_with()
        self.run_cmd()
        for _ in range(3):
            self.assertEqual(self.run_cmd()["action"], "block")
        self.assertEqual(len(self.llm.calls), 1)

    # --- fail open --------------------------------------------------------------------------------------

    def test_a_slow_mom_is_allowed_within_the_deadline(self):
        class Slow(FakeLlm):
            def complete(self, messages, **kw):
                time.sleep(2)
                return super().complete(messages, **kw)
        plugin._ctx.llm = Slow(text="HOLD: x. Instead: y.")
        plugin.DEFAULTS["preflight"]["timeout_s"] = 0.2
        t0 = time.monotonic()
        self.assertIsNone(self.run_cmd())
        self.assertLess(time.monotonic() - t0, 1.0)
        self.assertEqual((self.rows()[0]["verdict"], self.rows()[0]["reason"]), ("allowed", "timeout"))
        self.assertIsNone(plugin.active_hold(self.tid, plugin.cmd_hash(self.CMD)))

    def test_the_deadline_is_40_percent_of_the_hook_budget(self):
        class Slow(FakeLlm):
            def complete(self, messages, **kw):
                time.sleep(3)
                return super().complete(messages, **kw)
        plugin._ctx.llm = Slow()
        real = plugin._core.hook_budget
        plugin._core.hook_budget = lambda: 2.0
        try:
            t0 = time.monotonic()
            self.assertIsNone(self.run_cmd())
        finally:
            plugin._core.hook_budget = real
        self.assertLess(time.monotonic() - t0, 1.2)           # 0.4 x 2 s, plus slack; timeout_s is 12
        self.assertEqual(self.rows()[0]["reason"], "timeout")

    def test_a_provider_error_is_allowed(self):
        self.llm.boom = True
        self.assertIsNone(self.run_cmd())
        self.assertEqual((self.rows()[0]["verdict"], self.rows()[0]["reason"]), ("allowed", "down"))

    def test_a_model_that_just_failed_is_not_asked_again(self):
        self.llm.boom = True
        self.run_cmd()
        self.run_cmd("git push --force origin other")
        self.assertEqual(len(self.llm.calls), 1)
        self.assertEqual([r["reason"] for r in self.rows()], ["down", "down"])

    def test_an_unparseable_answer_is_allowed(self):
        self.hold_with("I am not sure, maybe?")
        self.assertIsNone(self.run_cmd())
        self.assertEqual((self.rows()[0]["verdict"], self.rows()[0]["reason"]), ("allowed", "parse"))
        self.assertIsNone(plugin.active_hold(self.tid, plugin.cmd_hash(self.CMD)))

    def test_an_empty_answer_is_allowed(self):
        self.hold_with("")
        self.assertIsNone(self.run_cmd())
        self.assertEqual(self.rows()[0]["reason"], "parse")

    def test_go_with_extra_words_is_not_a_go(self):
        self.hold_with("GO ahead but HOLD on")
        self.assertIsNone(self.run_cmd())
        self.assertEqual(self.rows()[0]["reason"], "parse")

    def test_the_per_card_cap_allows_and_stops_asking(self):
        plugin.DEFAULTS["preflight"]["max_per_card"] = 2
        for i in range(4):
            self.assertIsNone(self.run_cmd(f"git push --force origin b{i}"))
        self.assertEqual(len(self.llm.calls), 2)
        self.assertEqual([r.get("reason") for r in self.rows()], [None, None, "cap", "cap"])
        self.assertEqual([r["verdict"] for r in self.rows()], ["go", "go", "allowed", "allowed"])

    def test_the_cap_does_not_release_an_existing_hold(self):
        plugin.DEFAULTS["preflight"]["max_per_card"] = 1
        self.hold_with()
        self.run_cmd()
        self.assertEqual(self.run_cmd()["action"], "block")

    def test_the_daily_spend_cap_allows(self):
        plugin.DEFAULTS["advisor"]["daily_usd_cap"] = 0.0005
        self.run_cmd()                                         # costs 0.001: cap reached
        self.assertIsNone(self.run_cmd("git push --force origin b2"))
        self.assertEqual(len(self.llm.calls), 1)
        self.assertEqual(self.rows()[1]["reason"], "cap")

    def test_the_spend_is_counted(self):
        self.run_cmd()
        self.assertAlmostEqual(plugin._spent_today()["usd"], 0.001)

    def test_a_corrupt_state_file_means_no_holds(self):
        plugin._holds_path().parent.mkdir(parents=True, exist_ok=True)
        plugin._holds_path().write_text("{not json")
        self.assertIsNone(plugin.active_hold(self.tid, "abc"))
        self.assertIsNone(self.run_cmd())

    # --- never -----------------------------------------------------------------------------------------

    def test_local_only_cards_are_never_asked_and_never_held(self):
        tid = with_conn(lambda c: kb.create_task(c, title="[local-only] prod push", assignee="worker"))
        os.environ["HERMES_KANBAN_TASK"] = tid
        self.assertIsNone(self.run_cmd())
        self.assertEqual(self.llm.calls, [])
        self.assertEqual((self.rows()[0]["verdict"], self.rows()[0]["reason"]), ("skipped", "local_only"))

    def test_a_non_critical_command_is_not_asked(self):
        for cmd in ("ls -la", "git push origin feat/x", "pytest -q"):
            self.assertIsNone(self.run_cmd(cmd))
        self.assertEqual(self.llm.calls, [])
        self.assertEqual(self.rows(), [])

    def test_other_tools_are_not_asked(self):
        self.assertIsNone(plugin.on_pre_tool(tool_name="read_file", args={"path": "/a"}))
        self.assertIsNone(plugin.on_pre_tool(tool_name="terminal", args={}))
        self.assertEqual(self.llm.calls, [])

    def test_outside_a_kanban_worker_nothing_happens(self):
        os.environ.pop("HERMES_KANBAN_TASK")
        self.assertIsNone(self.run_cmd())
        self.assertEqual(self.llm.calls, [])

    def test_a_board_that_did_not_opt_in_is_not_asked(self):
        os.environ["HERMES_KANBAN_BOARD"] = "other-board"
        self.assertIsNone(self.run_cmd())
        self.assertEqual(self.llm.calls, [])

    def test_it_is_active_only_where_the_advisor_is(self):
        plugin.DEFAULTS["advisor"]["enabled"] = False
        self.assertIsNone(self.run_cmd())
        self.assertEqual(self.llm.calls, [])

    def test_it_has_its_own_switch(self):
        plugin.DEFAULTS["preflight"]["enabled"] = False
        self.assertIsNone(self.run_cmd())
        self.assertEqual(self.llm.calls, [])

    def test_the_kill_switch_wins(self):
        os.environ["CALLING_MOM"] = "off"
        self.assertIsNone(self.run_cmd())
        self.assertEqual(self.llm.calls, [])

    def test_a_card_that_does_not_exist_is_ignored(self):
        os.environ["HERMES_KANBAN_TASK"] = "t_missing"
        self.assertIsNone(self.run_cmd())
        self.assertEqual(self.llm.calls, [])

    def test_a_card_without_model_context_still_works_without_a_comment_tool(self):
        plugin._ctx.dispatch_tool = None                       # Hermes older than the plugin floor
        self.hold_with()
        self.assertEqual(self.run_cmd()["action"], "block")


BRIEF = ("ASK_HUMAN:\nQuestion: Which auth provider do we use?\nOptions: A) Okta B) Auth0\n"
         "Recommendation: B, it is cheaper.\nIf nobody answers: the card stays blocked.")
BRIEF_COMMENT = ("[for you] Question: Which auth provider do we use?\nOptions: A) Okta B) Auth0\n"
                 "Recommendation: B, it is cheaper.\nIf nobody answers: the card stays blocked.")


class QuestionFixture(AdvisorFixture):
    """WP3: a worker asked a person (`kanban_block(kind="needs_input")`) on a board where Mom answers first."""

    QUESTION = "Which config file should I change, A or B?"

    def setUp(self):
        super().setUp()
        self._state = os.environ.get("CALLING_MOM_STATE")
        os.environ["CALLING_MOM_STATE"] = str(Path(tempfile.mkdtemp()) / "state.json")
        plugin.DEFAULTS["answer"].update(enabled=True, boards=["t-board"])
        self.llm = FakeLlm(text="ANSWER: Change config B, A is generated by the build.", cost=0.001)
        plugin._ctx.llm = self.llm
        self.tid = self.card()

    def tearDown(self):
        super().tearDown()
        if self._state is None:
            os.environ.pop("CALLING_MOM_STATE", None)
        else:
            os.environ["CALLING_MOM_STATE"] = self._state

    def card(self, title="fix the parser", body="Config A is generated by the build. Edit config B."):
        tid = with_conn(lambda c: kb.create_task(c, title=title, body=body, assignee="worker"))
        os.environ["HERMES_KANBAN_TASK"] = tid
        return tid

    def ask(self, reason=None, kind="needs_input", tid=None, board="t-board"):
        """The worker blocks its card, then Hermes fires `kanban_task_blocked` (`on_question` is our listener)."""
        tid, reason = tid or self.tid, reason or self.QUESTION
        with_conn(lambda c: kb.block_task(c, tid, reason=reason, kind=kind))
        plugin.on_question(task_id=tid, board=board, reason=reason, run_id=1)
        return tid

    def status(self, tid=None):
        return with_conn(lambda c: kb.get_task(c, tid or self.tid)).status

    def comments(self, tid=None):
        return [c.body for c in with_conn(lambda c: kb.list_comments(c, tid or self.tid))]

    def rows(self, kind="question"):
        return log_rows(kind)

    def prompt(self, n=0):
        return "\n".join(m["content"] for m in self.llm.calls[n]["messages"])

    def seq(self, *texts):
        self.llm = SeqLlm(texts)
        plugin._ctx.llm = self.llm

    def answered_nothing(self, tid=None):
        self.assertEqual(self.status(tid), "blocked")
        self.assertEqual([c for c in self.comments(tid) if c.startswith(("[mom", "[olymp", "[for you"))], [])


class QuestionTest(QuestionFixture, unittest.TestCase):
    """Mom answers a worker question first: ANSWER unblocks, ASK_HUMAN writes a brief, anything else changes nothing."""

    def test_the_defaults_leave_it_off_until_setup_asks(self):
        self.assertEqual(self.saved["answer"], {"enabled": False, "boards": [], "max_per_card": 2, "precedents": True})
        self.assertEqual(self.saved["human_marker"], "[human]")

    # --- ANSWER -----------------------------------------------------------------------------------------

    def test_answer_is_posted_as_a_card_comment_and_the_card_continues(self):
        self.ask()
        self.assertEqual(self.comments()[-1], "[mom answered] Change config B, A is generated by the build.")
        self.assertEqual(self.status(), "ready")
        self.assertEqual(len(self.llm.calls), 1)
        self.assertEqual((self.llm.calls[0]["model"], self.llm.calls[0]["provider"]), (STRONG, PROVIDER))
        self.assertEqual(self.llm.calls[0]["timeout"], plugin.DEFAULTS["advisor"]["timeout_s"])

    def test_the_log_row_is_prose_free(self):
        self.ask()
        (row,) = self.rows()
        self.assertEqual((row["outcome"], row["by"], row["task"], row["board"], row["run"]),
                         ("answered", "mom", self.tid, "t-board", 1))
        self.assertEqual((row["tokens_in"], row["tokens_out"], row["usd"]), (1800, 120, 0.001))
        self.assertIsInstance(row["ms"], int)
        self.assertNotIn("config", json.dumps(row).lower())

    def test_the_answer_is_paid_from_the_shared_daily_budget(self):
        self.ask()
        spent = plugin._spent_today()
        self.assertEqual((spent["usd"], spent["tokens"]), (0.001, 1920))

    def test_the_question_carries_the_card_the_question_and_the_last_comments(self):
        with_conn(lambda c: kb.add_comment(c, self.tid, "human", "Prefer the minimal change."))
        self.ask()
        system, user = (m["content"] for m in self.llm.calls[0]["messages"])
        for word in ("ANSWER:", "ASK_HUMAN:", "Question:", "Options:", "Recommendation:", "If nobody answers:"):
            self.assertIn(word, system)
        self.assertNotIn("ESCALATE", system)                     # no olymp: nothing to escalate to
        self.assertIn("fix the parser", user)
        self.assertIn("Config A is generated", user)
        self.assertIn(self.QUESTION, user)
        self.assertIn("Prefer the minimal change.", user)

    def test_a_long_card_is_cut_in_the_middle_and_the_question_survives(self):
        plugin.DEFAULTS["advisor"]["context_chars"] = 400
        tid = self.card(body="filler " * 200)
        self.ask(tid=tid)
        user = self.llm.calls[0]["messages"][1]["content"]
        self.assertLessEqual(len(user), 400)
        self.assertIn("\n...\n", user)
        self.assertTrue(user.rstrip().endswith(self.QUESTION))

    def test_the_outbound_prompt_is_redacted(self):
        token = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
        tid = self.card(body=f"Deploy with https://deploy:s3cretpass@git.example.com/o/r.git and {token}")
        self.ask(reason=f"Which branch? I use {token} and DB_PASSWORD=hunter2hunter2", tid=tid)
        sent = self.prompt()
        for needle in ("a1B2c3D4e5F6", "s3cretpass", "hunter2hunter2"):
            self.assertNotIn(needle, sent)

    # --- ASK_HUMAN --------------------------------------------------------------------------------------

    def test_ask_human_writes_the_four_line_brief_and_the_card_stays_blocked(self):
        self.llm._res.text = BRIEF
        self.ask()
        self.assertEqual(self.comments()[-1], BRIEF_COMMENT)
        self.assertEqual(self.status(), "blocked")
        (row,) = self.rows()
        self.assertEqual((row["outcome"], row["by"]), ("human", "mom"))
        self.assertNotIn("Auth0", json.dumps(row))

    def test_the_brief_is_reshaped_to_exactly_four_lines(self):
        self.llm._res.text = ("ASK_HUMAN: Question: Which\nauth provider?\n\nOptions: A) Okta\n   B) Auth0\n"
                              "recommendation: B.\nIf nobody answers: wait.")
        self.ask()
        self.assertEqual(self.comments()[-1], "[for you] Question: Which auth provider?\nOptions: A) Okta B) Auth0\n"
                                              "Recommendation: B.\nIf nobody answers: wait.")

    def test_a_brief_missing_a_line_or_a_line_text_is_an_error_not_a_guess(self):
        for text in ("ASK_HUMAN:\nQuestion: Which?\nOptions: A) x B) y\nRecommendation: B",
                     "ASK_HUMAN:\nQuestion: Which?\nOptions: A) x B) y\nRecommendation: B\nIf nobody answers:",
                     "ASK_HUMAN:\nQuestion: Which?\nOptions:\nRecommendation: B\nIf nobody answers: wait.",
                     "ASK_HUMAN: just ask him"):
            with self.subTest(text=text):
                self.llm._res.text = text
                tid = self.card()
                self.ask(tid=tid)
                self.answered_nothing(tid)
                self.assertEqual((self.rows()[-1]["outcome"], self.rows()[-1]["reason"]), ("error", "parse"))

    # --- fast path: no model call -----------------------------------------------------------------------

    def test_every_fast_path_skip_asks_nobody_and_leaves_the_card_alone(self):
        def capped(tid):
            for _ in range(2):
                with_conn(lambda c: kb.add_comment(c, tid, "worker", "[mom answered] earlier"))
        rows = [  # (case, card title, card body, question, prepare(tid), expected skip reason)
            ("local-only", "fix", "[local-only] Edit config B.", None, None, "local_only"),
            ("human marker", "[human] choose a vendor", "body", None, None, "human_marker"),
            ("local-only beats a credential question", "fix", "[local-only] body", "What is the password?", None, "local_only"),
            ("per-card cap", "fix", "body", None, capped, "cap"),
            ("daily cap", "fix", "body", None, lambda tid: plugin._add_spend(0.0, 10 ** 9), "cap_daily"),
            ("goal budget", "fix", "body", "Goal-mode worker exhausted its turn budget (3/3) without completing the task.",
             None, "goal"),
        ]
        for name, title, body, question, prepare, reason in rows:
            with self.subTest(case=name):
                self.llm.calls.clear()
                tid = self.card(title=title, body=body)
                if prepare:
                    prepare(tid)
                before = self.comments(tid)
                self.ask(tid=tid, reason=question)
                self.assertEqual(self.llm.calls, [])
                self.assertEqual((self.status(tid), self.comments(tid)), ("blocked", before))
                self.assertEqual((self.rows()[-1]["outcome"], self.rows()[-1]["reason"]), ("skipped", reason))
                self.assertNotIn("config", json.dumps(self.rows()[-1]).lower())

    def test_a_block_that_hermes_re_kinded_is_not_a_worker_question(self):
        tid = self.card()
        with_conn(lambda c: kb.block_task(c, tid, reason="waiting for a parent", kind="dependency"))
        self.assertEqual(with_conn(lambda c: kb.get_task(c, tid)).block_kind, "needs_input")   # Hermes re-kinded it
        plugin.on_question(task_id=tid, board="t-board", reason="waiting for a parent", run_id=1)
        self.assertEqual(self.llm.calls, [])
        self.assertEqual((self.rows()[0]["outcome"], self.rows()[0]["reason"]), ("skipped", "rekind"))
        self.assertEqual(self.status(tid), "blocked")

    def test_silent_cases_leave_no_row_and_no_call(self):
        def off(): plugin.DEFAULTS["answer"]["enabled"] = False
        def other_board(): plugin.DEFAULTS["answer"]["boards"] = ["elsewhere"]
        def not_the_worker(): os.environ["HERMES_KANBAN_TASK"] = "t_someone_else"
        def kill(): os.environ["CALLING_MOM"] = "off"
        def human_block(): os.environ.pop("HERMES_KANBAN_TASK")        # `hermes kanban block`, the dashboard, ...
        for name, change, kind in (("answer off", off, "needs_input"), ("board not opted in", other_board, "needs_input"),
                                   ("blocked by somebody else", human_block, "needs_input"),
                                   ("another worker's card", not_the_worker, "needs_input"),
                                   ("kill switch", kill, "needs_input"), ("capability block", lambda: None, "capability"),
                                   ("transient block", lambda: None, "transient")):
            with self.subTest(case=name):
                self.tid = self.card()
                change()
                self.ask(kind=kind)
                os.environ["CALLING_MOM"] = "on"
                plugin.DEFAULTS["answer"].update(enabled=True, boards=["t-board"])
                self.assertEqual((self.llm.calls, self.rows()), ([], []))
                self.assertEqual(self.status(), "blocked")

    def test_the_wildcard_board_opts_everything_in(self):
        plugin.DEFAULTS["answer"]["boards"] = ["*"]
        self.ask(board="whatever")
        self.assertEqual(self.status(), "ready")

    def test_the_board_comes_from_the_environment_when_the_hook_omits_it(self):
        with_conn(lambda c: kb.block_task(c, self.tid, reason=self.QUESTION, kind="needs_input"))
        plugin.on_question(task_id=self.tid, reason=self.QUESTION)
        self.assertEqual(self.status(), "ready")

    def test_a_repeated_question_goes_to_triage_and_is_left_to_the_human(self):
        self.ask()
        with_conn(lambda c: kb.block_task(c, self.tid, reason="and which version?", kind="needs_input"))
        self.assertEqual(self.status(), "triage")                 # Hermes' own recurrence breaker
        plugin.on_question(task_id=self.tid, board="t-board", reason="and which version?", run_id=2)
        self.assertEqual(len(self.llm.calls), 1)

    def test_a_card_that_does_not_exist_is_ignored(self):
        plugin.on_question(task_id="t_missing", board="t-board", reason="x", run_id=1)
        self.assertEqual((self.llm.calls, self.rows()), ([], []))

    def test_without_a_model_context_nothing_is_asked(self):
        plugin._ctx = None
        self.ask()
        self.assertEqual(self.status(), "blocked")
        self.assertEqual(self.rows()[0]["reason"], "no_llm")

    # --- risk: Mom does not answer, she may write a brief -----------------------------------------------

    def test_a_critical_card_gets_a_brief_never_an_answer(self):
        tid = self.card(body="Release it: terraform apply in infra/ and tell me which workspace.")
        self.llm._res.text = BRIEF
        self.ask(tid=tid)
        self.assertEqual(self.comments(tid)[-1], BRIEF_COMMENT)
        self.assertEqual(self.status(tid), "blocked")
        self.assertEqual((self.rows()[0]["outcome"], self.rows()[0]["reason"]), ("human", "critical"))
        self.assertIn("Do NOT answer", self.llm.calls[0]["messages"][0]["content"])

    def test_a_human_topic_in_the_question_gets_a_brief(self):
        self.llm._res.text = BRIEF
        self.ask(reason="What is the password of the staging database?")
        self.assertEqual(self.comments()[-1], BRIEF_COMMENT)
        self.assertEqual((self.rows()[0]["outcome"], self.rows()[0]["reason"]), ("human", "human_topic"))

    def test_a_human_topic_in_the_card_gets_a_brief(self):
        tid = self.card(body="Rotate the API key of the mail service, then ask which region.")
        self.llm._res.text = BRIEF
        self.ask(tid=tid, reason="Which region?")
        self.assertEqual(self.rows()[0]["reason"], "human_topic")

    def test_an_active_preflight_hold_gets_a_brief_with_the_hold_in_it(self):
        plugin._record_hold(self.tid, plugin.cmd_hash("git push --force origin main"),
                            "it rewrites shared history", "push a branch")
        self.llm._res.text = BRIEF
        self.ask(reason="May I force push?")
        self.assertEqual(self.comments()[-1], BRIEF_COMMENT)
        self.assertEqual((self.rows()[0]["outcome"], self.rows()[0]["reason"]), ("human", "hold"))
        self.assertIn("it rewrites shared history", self.prompt())
        self.assertIn("push a branch", self.prompt())

    def test_when_mom_answers_anyway_on_a_risky_card_nothing_is_unblocked(self):
        self.ask(reason="What is the password of the staging database?")      # the fake still says ANSWER:
        self.answered_nothing()
        self.assertEqual((self.rows()[0]["outcome"], self.rows()[0]["reason"]), ("error", "parse"))

    # --- ESCALATE: olymp, within his caps ---------------------------------------------------------------

    def with_olymp(self, *texts):
        plugin.DEFAULTS["olymp"].update(model="m/olymp", provider="p/olymp")
        self.seq(*texts)

    def test_olymp_is_offered_to_mom_only_when_he_is_configured(self):
        self.with_olymp("ANSWER: B.")
        self.ask()
        self.assertIn("ESCALATE", self.llm.calls[0]["messages"][0]["content"])

    def test_escalate_asks_olymp_who_answers(self):
        self.with_olymp("ESCALATE: the choice is expensive to undo", "ANSWER: Use B.")
        self.ask()
        self.assertEqual([c["model"] for c in self.llm.calls], [STRONG, "m/olymp"])
        self.assertEqual(self.llm.calls[1]["provider"], "p/olymp")
        self.assertNotIn("ESCALATE", self.llm.calls[1]["messages"][0]["content"])
        self.assertEqual(self.comments()[-1], "[olymp answered] Use B.")
        self.assertEqual(self.status(), "ready")
        (row,) = self.rows()
        self.assertEqual((row["outcome"], row["by"]), ("answered", "olymp"))
        self.assertEqual(plugin._spent_today()["olymp"], 1)

    def test_olymp_may_hand_it_to_the_human(self):
        self.with_olymp("ESCALATE: x", BRIEF)
        self.ask()
        self.assertEqual(self.comments()[-1], BRIEF_COMMENT)
        self.assertEqual((self.rows()[0]["outcome"], self.rows()[0]["by"]), ("human", "olymp"))

    def test_olymp_per_card_cap_turns_the_escalation_into_a_brief_for_the_human(self):
        self.with_olymp("ESCALATE: hard one")
        with_conn(lambda c: kb.add_comment(c, self.tid, "worker", "[architecture/critical_change] run 1 · m (p, olymp): 1 in / 1 out\nx"))
        self.ask()
        self.assertEqual(len(self.llm.calls), 1)
        self.assertTrue(self.comments()[-1].startswith("[for you] Question: " + self.QUESTION))
        for label in ("\nOptions: ", "\nRecommendation: ", "\nIf nobody answers: "):
            self.assertIn(label, self.comments()[-1])
        self.assertEqual((self.rows()[0]["outcome"], self.rows()[0]["reason"]), ("human", "olymp_cap"))

    def test_olymp_daily_cap_turns_the_escalation_into_a_brief_for_the_human(self):
        self.with_olymp("ESCALATE: hard one")
        plugin._add_spend(0.0, 0, olymp=plugin.DEFAULTS["olymp"]["max_per_day"])
        self.ask()
        self.assertEqual(len(self.llm.calls), 1)
        self.assertEqual((self.rows()[0]["outcome"], self.rows()[0]["reason"]), ("human", "olymp_cap"))
        self.assertEqual(self.status(), "blocked")

    def test_escalate_without_an_olymp_is_a_brief_for_the_human(self):
        self.seq("ESCALATE: hard one")
        self.ask()
        self.assertEqual((self.rows()[0]["outcome"], self.rows()[0]["reason"]), ("human", "no_olymp"))
        self.assertEqual(self.status(), "blocked")

    def test_olymp_that_answers_nonsense_leaves_the_card_unchanged(self):
        self.with_olymp("ESCALATE: x", "ESCALATE: me too")
        self.ask()
        self.answered_nothing()
        self.assertEqual((self.rows()[0]["outcome"], self.rows()[0]["reason"]), ("error", "parse"))

    # --- errors: unchanged, logged, never retried -------------------------------------------------------

    def test_a_dead_provider_leaves_the_card_unchanged_and_is_not_retried(self):
        self.llm.boom = True
        self.ask()
        self.answered_nothing()
        self.assertEqual((self.rows()[0]["outcome"], self.rows()[0]["reason"]), ("error", "down"))
        self.assertEqual(len(self.llm.calls), 1)
        self.assertEqual([c for c in self.comments() if "unavailable" in c], [])

    def test_a_model_that_just_failed_is_not_asked_again(self):
        self.llm.boom = True
        self.ask()
        self.ask(tid=self.card())
        self.assertEqual(len(self.llm.calls), 1)
        self.assertEqual(self.rows()[1]["reason"], "cooldown")

    def test_a_slow_mom_is_given_up_within_the_advisor_timeout(self):
        class Slow(FakeLlm):
            def complete(self, messages, **kw):
                time.sleep(2)
                return super().complete(messages, **kw)
        plugin._ctx.llm = Slow(text="ANSWER: late")
        plugin.DEFAULTS["advisor"]["timeout_s"] = 0.2
        t0 = time.monotonic()
        self.ask()
        self.assertLess(time.monotonic() - t0, 1.0)
        self.answered_nothing()
        self.assertEqual((self.rows()[0]["outcome"], self.rows()[0]["reason"]), ("error", "timeout"))

    def test_unparseable_or_empty_answers_change_nothing(self):
        for text in ("Sure, go ahead!", "", "ANSWER:", "ANSWER:   "):
            with self.subTest(text=text):
                self.llm._res.text = text
                tid = self.card()
                self.ask(tid=tid)
                self.answered_nothing(tid)
                self.assertEqual((self.rows()[-1]["outcome"], self.rows()[-1]["reason"]), ("error", "parse"))

    def test_when_the_comment_cannot_be_written_the_card_is_not_unblocked(self):
        plugin._ctx.dispatch_tool = lambda *a, **k: json.dumps({"error": "nope"})
        self.ask()
        self.assertEqual(self.status(), "blocked")
        self.assertEqual((self.rows()[0]["outcome"], self.rows()[0]["reason"]), ("error", "comment"))

    def test_when_the_card_cannot_be_unblocked_it_is_an_error_row(self):
        from unittest import mock
        with mock.patch.object(kb, "unblock_task", return_value=False):
            self.ask()
        self.assertEqual((self.rows()[0]["outcome"], self.rows()[0]["reason"]), ("error", "unblock"))

    def test_an_answer_is_cut_at_1500_characters(self):
        self.llm._res.text = "ANSWER: " + "x" * 3000
        self.ask()
        self.assertEqual(len(self.comments()[-1]), len("[mom answered] ") + 1500)

    def test_the_hook_is_registered_next_to_the_capability_hook(self):
        from types import SimpleNamespace as NS
        seen = []
        plugin.register(NS(register_hook=lambda n, f: seen.append((n, f.__name__)), register_tool=lambda **kw: None))
        self.assertIn(("kanban_task_blocked", "on_question"), seen)
        self.assertIn(("kanban_task_blocked", "on_blocked"), seen)


class AnswerCardTest(QuestionFixture, unittest.TestCase):
    """A person answers a brief or releases a hold: one entry point for the CLI and the Desktop page."""

    CMD = "git push --force origin main"

    def setUp(self):
        super().setUp()
        self.llm._res.text = BRIEF
        self.ask()                                   # leaves a blocked card with Mom's `[for you]` brief

    def answer(self, text=None, tid=None, **kw):
        kw.setdefault("by", "cli")
        return plugin.answer_card("t-board", tid or self.tid, text, **kw)

    def hold(self, tid=None):
        plugin._record_hold(tid or self.tid, plugin.cmd_hash(self.CMD), "it rewrites shared history", "push a branch")

    def human_rows(self):
        return self.rows("human_answer")

    # --- the answer -------------------------------------------------------------------------------------

    def test_text_is_written_as_the_persons_comment_and_the_card_continues(self):
        out = self.answer("Use A, Okta is already licensed.")
        self.assertEqual((out["ok"], out["task_id"]), (True, self.tid))
        self.assertEqual(self.comments()[-1], "[you via calling-mom] Use A, Okta is already licensed.")
        self.assertEqual(self.status(), "ready")

    def test_accept_copies_moms_recommendation(self):
        self.assertTrue(self.answer(accept=True)["ok"])
        self.assertEqual(self.comments()[-1], "[you via calling-mom] B, it is cheaper.")
        self.assertEqual(self.status(), "ready")

    def test_the_log_row_says_who_answered_how(self):
        self.answer(accept=True, by="desktop")
        (row,) = self.human_rows()
        self.assertEqual((row["via"], row["accepted"], row["allow_once"], row["task"], row["board"]),
                         ("desktop", True, False, self.tid, "t-board"))
        self.assertIsInstance(row["latency_s"], int)
        self.assertGreaterEqual(row["latency_s"], 0)
        self.assertNotIn("cheaper", json.dumps(row))

    def test_a_typed_answer_is_not_logged_as_accepted(self):
        self.answer("Use A.")
        self.assertEqual((self.human_rows()[0]["via"], self.human_rows()[0]["accepted"]), ("cli", False))

    def test_the_text_is_trimmed(self):
        self.answer("  Use A.  \n")
        self.assertEqual(self.comments()[-1], "[you via calling-mom] Use A.")

    # --- holds ------------------------------------------------------------------------------------------

    def test_allow_once_records_an_allowance_through_the_preflight_function_and_the_command_runs(self):
        self.hold()
        out = self.answer("Go ahead, I checked the remote.", allow_once=True)
        self.assertTrue(out["ok"])
        allowance = plugin._load_holds()["allowances"][self.tid][plugin.cmd_hash(self.CMD)]
        self.assertEqual((allowance["by"], allowance["board"]), ("cli", "t-board"))
        self.assertTrue(self.human_rows()[0]["allow_once"])
        self.assertEqual(self.status(), "ready")
        self.llm.calls.clear()
        self.assertIsNone(plugin.on_pre_tool(tool_name="terminal", args={"command": self.CMD}))   # the worker's retry
        self.assertEqual(self.llm.calls, [])                                                       # no new model call

    def test_allow_once_alone_needs_no_text(self):
        self.hold()
        self.assertTrue(self.answer(allow_once=True)["ok"])
        self.assertTrue(self.comments()[-1].startswith("[you via calling-mom] Allowed once"))

    def test_a_card_with_only_a_hold_can_be_released(self):
        tid = self.card()
        with_conn(lambda c: kb.block_task(c, tid, reason="may I force push?", kind="needs_input"))
        self.hold(tid)
        self.assertTrue(self.answer(allow_once=True, tid=tid)["ok"])
        self.assertEqual(self.status(tid), "ready")
        self.assertEqual(self.human_rows()[0]["task"], tid)

    def test_without_allow_once_a_hold_stays(self):
        self.hold()
        self.answer("Use A.")
        self.assertIsNotNone(plugin.active_hold(self.tid, plugin.cmd_hash(self.CMD)))
        self.assertNotIn("allowances", plugin._load_holds())

    # --- validation: nothing changes on an error --------------------------------------------------------

    def test_bad_requests_change_nothing(self):
        other_card = self.card()                                       # blocked, but no brief and no hold
        with_conn(lambda c: kb.block_task(c, other_card, reason="x", kind="needs_input"))
        gone = self.card()                                             # not blocked
        rows = [  # (case, kwargs, expected status, fragment of the error)
            ("nothing to do", dict(), 400, "nothing to do"),
            ("text and accept", dict(text="x", accept=True), 400, "not both"),
            ("blank text", dict(text="   "), 400, "1 to 2000"),
            ("text too long", dict(text="x" * 2001), 400, "1 to 2000"),
            ("text is not a string", dict(text=5), 400, "1 to 2000"),
            ("unknown who", dict(text="x", by="mom"), 400, "'cli' or 'desktop'"),
            ("unknown board", dict(text="x", board="nowhere"), 404, "did not opt in"),
            ("unknown card", dict(text="x", tid="t_missing"), 404, "no card"),
            ("not blocked", dict(text="x", tid=gone), 409, "not blocked"),
            ("no brief and no hold", dict(text="x", tid=other_card), 409, "no brief and no hold"),
            ("allow_once without a hold", dict(allow_once=True), 409, "no held command"),
            ("accept without a recommendation", dict(accept=True, tid=other_card), 409, "no brief and no hold"),
        ]
        for name, kw, status, fragment in rows:
            with self.subTest(case=name):
                kw.setdefault("by", "cli")
                board = kw.pop("board", "t-board")
                tid = kw.pop("tid", self.tid)
                text = kw.pop("text", None)
                out = plugin.answer_card(board, tid, text, **kw)
                self.assertFalse(out["ok"])
                self.assertEqual(out["status"], status)
                self.assertIn(fragment, out["error"])
                self.assertEqual(self.human_rows(), [])
                self.assertFalse(any(c.startswith("[you") for c in self.comments(tid)))
        self.assertEqual(self.status(), "blocked")

    def test_accept_on_a_brief_without_a_recommendation_line_is_refused(self):
        tid = self.card()
        with_conn(lambda c: kb.block_task(c, tid, reason="x", kind="needs_input"))
        with_conn(lambda c: kb.add_comment(c, tid, "worker", "[for you] Question: what? (a brief by hand)"))
        out = self.answer(accept=True, tid=tid)
        self.assertEqual((out["ok"], out["status"]), (False, 400))
        self.assertEqual(self.status(tid), "blocked")

    def test_a_second_answer_finds_the_card_already_running(self):
        self.answer("Use A.")
        out = self.answer("Use B.")
        self.assertEqual((out["ok"], out["status"]), (False, 409))
        self.assertEqual(len(self.human_rows()), 1)

    def test_the_board_of_the_advisor_counts_as_opted_in_for_holds(self):
        plugin.DEFAULTS["answer"].update(enabled=False, boards=[])
        self.hold()
        self.assertTrue(self.answer(allow_once=True)["ok"])

    def test_when_the_card_cannot_be_unblocked_the_answer_reports_it(self):
        from unittest import mock
        with mock.patch.object(kb, "unblock_task", return_value=False):
            out = self.answer("Use A.")
        self.assertEqual((out["ok"], out["unblocked"]), (True, False))

    # --- somebody else unblocked the card ---------------------------------------------------------------

    def test_a_card_claimed_again_after_somebody_else_unblocked_it_is_logged_once(self):
        with_conn(lambda c: kb.unblock_task(c, self.tid))
        plugin.on_claimed(task_id=self.tid, board=None)
        plugin.on_claimed(task_id=self.tid, board=None)
        (row,) = self.human_rows()
        self.assertEqual((row["via"], row["accepted"], row["allow_once"], row["task"]), ("card", False, False, self.tid))
        self.assertIsInstance(row["latency_s"], int)

    def test_a_card_that_was_answered_here_is_not_logged_again_when_claimed(self):
        self.answer("Use A.")
        plugin.on_claimed(task_id=self.tid, board=None)
        self.assertEqual([r["via"] for r in self.human_rows()], ["cli"])

    def test_a_claim_of_a_card_without_a_brief_logs_nothing(self):
        plugin.on_claimed(task_id=self.card(), board=None)
        self.assertEqual(self.human_rows(), [])

    def test_a_claim_of_a_card_that_does_not_exist_is_ignored(self):
        plugin.on_claimed(task_id="t_missing", board=None)
        self.assertEqual(self.human_rows(), [])
