"""Tests for the calling-mom plugin (stdlib unittest, no pytest needed).

    cd <plugin dir> && /opt/hermes/.venv/bin/python -m unittest -v test_calling_mom
"""

import importlib.util
import json
import os
import tempfile
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
            raise RuntimeError("provider down")
        return self._res


class AdvisorTest(unittest.TestCase):
    """Step 3: the strong model is asked ONE short question at the point where the worker is stuck."""

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
        plugin._ctx = NS(llm=self.llm)
        self.tid = with_conn(lambda c: kb.create_task(c, title="fix the parser", body="Make tests pass.", assignee="worker"))
        os.environ["HERMES_KANBAN_TASK"] = self.tid

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
        self.assertEqual(sum(1 for x in comments if x.author == "advisor"), 3)
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
                           register_system_prompt_section=lambda **kw: seen.update({"text": kw["content"], "kw": kw}),
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


class LadderTest(unittest.TestCase):
    """Step C1: optional free rung - raise the reasoning effort of the same model before paying for another one."""

    def setUp(self):
        os.environ["CALLING_MOM"] = "on"
        self.saved = {k: dict(v) if isinstance(v, dict) else v for k, v in plugin.DEFAULTS.items()}
        plugin.DEFAULTS["ladder"]["effort_first"] = True

    def tearDown(self):
        plugin.DEFAULTS.clear(); plugin.DEFAULTS.update(self.saved)

    def give_up(self, tid, reason="stuck"):
        with_conn(lambda c: kb.block_task(c, tid, reason=reason, kind="capability"))
        plugin.on_blocked(task_id=tid, board=None, reason=reason)

    def test_first_give_up_raises_effort_not_the_model(self):
        tid = with_conn(lambda c: kb.create_task(c, title="hard", assignee="worker"))
        self.give_up(tid)
        t = self_task(tid)
        self.assertEqual((t.reasoning_effort, t.model_override, t.status), ("high", None, "ready"))

    def test_second_give_up_goes_to_the_strong_model(self):
        tid = with_conn(lambda c: kb.create_task(c, title="hard 2", assignee="worker"))
        self.give_up(tid); self.give_up(tid, "still stuck")
        t = self_task(tid)
        self.assertEqual((t.reasoning_effort, t.model_override, t.status), ("high", STRONG, "ready"))

    def test_third_give_up_is_left_to_the_board_and_a_human(self):
        tid = with_conn(lambda c: kb.create_task(c, title="hard 5", assignee="worker"))
        self.give_up(tid); self.give_up(tid, "again"); self.give_up(tid, "and again")
        t = self_task(tid)
        self.assertEqual(t.model_override, STRONG)
        self.assertIn(t.status, {"blocked", "triage"})   # no fourth rung

    def test_card_already_on_high_effort_skips_the_free_rung(self):
        tid = with_conn(lambda c: kb.create_task(c, title="hard 3", assignee="worker", reasoning_effort="high"))
        self.give_up(tid)
        self.assertEqual(self_task(tid).model_override, STRONG)

    def test_default_is_off_and_keeps_the_old_behaviour(self):
        plugin.DEFAULTS["ladder"]["effort_first"] = False
        tid = with_conn(lambda c: kb.create_task(c, title="hard 4", assignee="worker"))
        self.give_up(tid)
        t = self_task(tid)
        self.assertEqual((t.reasoning_effort, t.model_override), (None, STRONG))

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


if __name__ == "__main__":
    unittest.main()
