"""Standardised contract matrix for the calling-mom plugin.

One row per case: (entry point, prepared card, call kwargs, expected effect).
Every hook a card can reach is covered here, including every guard branch.
The row list IS the specification: add a case, add a row.

    cd <plugin dir> && python -m unittest test_matrix -v

Existing suites (test_calling_mom / test_signals / test_metrics) stay for the
internal mechanics (prompts, metrics rendering, spend ledger); this file pins the
observable behaviour of the hooks.
"""
import inspect
import json
import os
import tempfile
import unittest
from pathlib import Path

import test_calling_mom as base

plugin = base.plugin
kb = base.kb
with_conn = base.with_conn
task_of = base.self_task
STRONG = base.STRONG
PROVIDER = base.PROVIDER

# --- fixtures -----------------------------------------------------------


def make(title="contract card", body=None, state="ready", kind=None,
         override=None, error=None, created_by=None):
    """Create one card and put it in the requested state."""
    tid = with_conn(lambda c: kb.create_task(c, title=title, body=body, assignee="worker"))
    tid = tid.id if hasattr(tid, "id") else tid

    def apply(conn):
        conn.execute("UPDATE tasks SET status=? WHERE id=?", (state, tid))
        for column, value in (("block_kind", kind), ("last_failure_error", error),
                              ("created_by", created_by)):
            if value is not None:
                conn.execute("UPDATE tasks SET %s=? WHERE id=?" % column, (value, tid))
        if override:
            conn.execute("UPDATE tasks SET model_override=?, provider_override=? WHERE id=?",
                         (override, PROVIDER, tid))
        conn.commit()

    with_conn(apply)
    return tid


def observe(tid):
    t = task_of(tid)
    return {"status": t.status, "override": t.model_override, "kind": t.block_kind}


class patched:
    """Override plugin settings (and env) for the duration of one case."""

    def __init__(self, **env):
        self.env, self.old_env, self.old_settings = env, {}, None

    def __enter__(self):
        cfg = self.env.pop("settings", None)
        for k, v in self.env.items():
            self.old_env[k] = os.environ.get(k)
            os.environ[k] = str(v)
        if cfg is not None:
            self.old_settings = plugin.settings
            plugin.settings = lambda: dict(plugin.DEFAULTS, **cfg)
        return self

    def __exit__(self, *exc):
        for k, v in self.old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        if self.old_settings is not None:
            plugin.settings = self.old_settings
        return False


def blocked(title="capability card", kind="capability", reason="local model cannot", **kw):
    """Card in the state the weak model leaves behind: blocked + kind."""
    tid = make(title=title, **kw)
    with_conn(lambda c: kb.block_task(c, tid, reason=reason, kind=kind))
    return tid


def parked(title="parked card", error="worker exited with code 1", **kw):
    """Card the crash circuit breaker parked (``gave_up``)."""
    from hermes_cli import kanban_db_dispatch as kd
    tid = make(title=title)
    with_conn(lambda c: kd._record_task_failure(c, tid, error, outcome="crashed",
                                                failure_limit=1, **kw))
    assert task_of(tid).status == "blocked", "fixture did not park the card"
    return tid


# --- the matrix ---------------------------------------------------------
# entry: function on the plugin. call: kwargs. expect: keys asserted afterwards
#   status / override / kind  -> card state
#   action                    -> result.get("action")
#   none                      -> result is None
#   raises                    -> the call must raise (never happens; there for completeness)

MATRIX = [
    # on_claimed -- kanban_task_claimed
    dict(id="claimed/marker-title", entry="on_claimed",
         setup=lambda: make(title="[crit] deploy change"), call={},
         expect={"override": STRONG}),
    dict(id="claimed/marker-body", entry="on_claimed",
         setup=lambda: make(title="plain", body="please [crit] review"), call={},
         expect={"override": STRONG}),
    dict(id="claimed/no-marker", entry="on_claimed",
         setup=lambda: make(title="plain card"), call={},
         expect={"override": None}),
    dict(id="claimed/already-overridden", entry="on_claimed",
         setup=lambda: make(title="[crit] done", override="other/model"), call={},
         expect={"override": "other/model"}),
    dict(id="claimed/local-only-wins", entry="on_claimed",
         setup=lambda: make(title="[crit] [local-only] secret"), call={},
         expect={"override": None}),
    dict(id="claimed/kill-switch", entry="on_claimed",
         setup=lambda: make(title="[crit] deploy"), call={}, env={"CALLING_MOM": "off"},
         expect={"override": None}),
    dict(id="claimed/missing-task", entry="on_claimed",
         setup=lambda: "no-such-task", call={}, expect={"none": True}),

    # on_blocked -- kanban_task_blocked
    dict(id="blocked/capability-escalates", entry="on_blocked",
         setup=lambda: blocked(), call={"reason": "local model cannot"},
         expect={"status": "ready", "override": STRONG}),
    dict(id="blocked/needs-input-stays", entry="on_blocked",
         setup=lambda: blocked(kind="needs_input"), call={"reason": "which repo?"},
         expect={"status": "blocked", "override": None}),
    dict(id="blocked/local-only-stays", entry="on_blocked",
         setup=lambda: blocked(title="[local-only] secret job"), call={},
         expect={"status": "blocked", "override": None}),
    dict(id="blocked/already-strong-stays", entry="on_blocked",
         setup=lambda: blocked(override=STRONG), call={},
         expect={"status": "blocked", "override": STRONG}),
    # contract: the block hook decides on block_kind only; an infra error in the row
    # does NOT suppress it (unlike sweep/on_parked, which do consult it)
    dict(id="blocked/infra-error-does-not-suppress", entry="on_blocked",
         setup=lambda: blocked(error="Connection refused by 10.0.0.5:8000"), call={},
         expect={"status": "ready", "override": STRONG}),
    dict(id="blocked/not-blocked", entry="on_blocked",
         setup=lambda: make(state="ready"), call={},
         expect={"status": "ready", "override": None}),
    dict(id="blocked/profile-already-strong", entry="on_blocked",
         setup=lambda: blocked(), call={}, patch={"_profile_runs_strong": True},
         expect={"status": "blocked", "override": None}),
    dict(id="blocked/kill-switch", entry="on_blocked",
         setup=lambda: blocked(), call={}, env={"CALLING_MOM": "off"},
         expect={"status": "blocked", "override": None}),
    dict(id="blocked/missing-task", entry="on_blocked",
         setup=lambda: "no-such-task", call={}, expect={"none": True}),

    # on_worker_exited / on_parked -- the reactive path (existing core hook)
    dict(id="parked/crash-escalates", entry="on_worker_exited",
         setup=lambda: parked(), call={"outcome": "crashed"},
         expect={"status": "ready", "override": STRONG}),
    dict(id="parked/completed-is-ignored", entry="on_worker_exited",
         setup=lambda: parked(), call={"outcome": "completed"},
         expect={"status": "blocked", "override": None}),
    dict(id="parked/sticky-is-human", entry="on_parked",
         setup=lambda: parked(force_trip=True), call={"payload": {"sticky": True}},
         expect={"status": "blocked", "override": None}),
    dict(id="parked/infra-is-human", entry="on_parked",
         setup=lambda: parked(error="Connection refused by 10.0.0.5:8000"),
         call={"payload": {"error": "Connection refused by 10.0.0.5:8000"}},
         expect={"status": "blocked", "override": None}),
    dict(id="parked/already-strong", entry="on_parked",
         setup=lambda: blocked(override=STRONG), call={},
         expect={"status": "blocked", "override": STRONG}),
    dict(id="parked/kill-switch", entry="on_parked",
         setup=lambda: parked(), call={}, env={"CALLING_MOM": "off"},
         expect={"status": "blocked", "override": None}),
    dict(id="parked/missing-task", entry="on_parked",
         setup=lambda: "no-such-task", call={}, expect={"none": True}),
    dict(id="parked/missing-id", entry="on_parked",
         setup=lambda: None, call={"task_id": None}, expect={"none": True}),

    # sweep -- the cron fallback (idempotent)
    dict(id="sweep/escalates-gave-up", entry="sweep",
         setup=lambda: parked(), call={}, expect={"status": "ready", "override": STRONG}),
    dict(id="sweep/sticky-skipped", entry="sweep",
         setup=lambda: parked(force_trip=True), call={},
         expect={"status": "blocked", "override": None}),
    dict(id="sweep/infra-skipped", entry="sweep",
         setup=lambda: parked(error="Connection refused by 10.0.0.5:8000"), call={},
         expect={"status": "blocked", "override": None}),
    dict(id="sweep/kill-switch", entry="sweep",
         setup=lambda: parked(), call={}, env={"CALLING_MOM": "off"},
         expect={"status": "blocked", "override": None}),

    # on_pre_tool -- pre_tool_call
    dict(id="pretool/crit-create-modifies", entry="on_pre_tool",
         setup=lambda: None,
         call={"tool_name": "kanban_create", "args": {"title": "[crit] migrate db"}},
         expect={"action": "modify"}),
    dict(id="pretool/crit-create-local-only", entry="on_pre_tool",
         setup=lambda: None,
         call={"tool_name": "kanban_create", "args": {"title": "[crit] [local-only] x"}},
         expect={"none": True}),
    dict(id="pretool/create-with-model", entry="on_pre_tool",
         setup=lambda: None,
         call={"tool_name": "kanban_create", "args": {"title": "[crit] x", "model": "m"}},
         expect={"none": True}),
    dict(id="pretool/create-plain", entry="on_pre_tool",
         setup=lambda: None, call={"tool_name": "kanban_create", "args": {"title": "x"}},
         expect={"none": True}),
    dict(id="pretool/other-tool", entry="on_pre_tool",
         setup=lambda: None, call={"tool_name": "terminal", "args": {}},
         expect={"none": True}),
    dict(id="pretool/kill-switch", entry="on_pre_tool",
         setup=lambda: None, env={"CALLING_MOM": "off"},
         call={"tool_name": "kanban_create", "args": {"title": "[crit] x"}},
         expect={"none": True}),
    dict(id="pretool/verify-not-trusted", entry="on_pre_tool",
         setup=lambda: make(body="verify: python3 -c 0", created_by="worker",
                            state="running"),
         call={"tool_name": "kanban_complete", "args": {}},
         env={"HERMES_KANBAN_TASK": "@TASK@"}, expect={"none": True}),
    dict(id="pretool/verify-command-not-allowed", entry="on_pre_tool",
         setup=lambda: make(body="verify: ./evil.sh", created_by="user", state="running"),
         call={"tool_name": "kanban_complete", "args": {}},
         env={"HERMES_KANBAN_TASK": "@TASK@"}, expect={"none": True}),
    dict(id="pretool/verify-malformed-quoting", entry="on_pre_tool",
         setup=lambda: make(body="verify: python3 'unclosed", created_by="user", state="running"),
         call={"tool_name": "kanban_complete", "args": {}},
         env={"HERMES_KANBAN_TASK": "@TASK@"}, expect={"none": True}),
    dict(id="pretool/verify-fails-blocks", entry="on_pre_tool",
         setup=lambda: make(body="verify: python3 -c \"raise SystemExit(3)\"",
                            created_by="user", state="running"),
         call={"tool_name": "kanban_complete", "args": {}},
         env={"HERMES_KANBAN_TASK": "@TASK@"}, expect={"action": "block"}),
    dict(id="pretool/verify-passes", entry="on_pre_tool",
         setup=lambda: make(body="verify: python3 -c \"pass\"", created_by="user", state="running"),
         call={"tool_name": "kanban_complete", "args": {}},
         env={"HERMES_KANBAN_TASK": "@TASK@"}, expect={"none": True}),
    dict(id="pretool/no-worker-session", entry="on_pre_tool",
         setup=lambda: None, call={"tool_name": "kanban_complete", "args": {}},
         env={"HERMES_KANBAN_TASK": ""}, expect={"none": True}),

    # handle_second_opinion -- the tool
    dict(id="opinion/no-worker-session", entry="handle_second_opinion",
         setup=lambda: None, call={"args": {"question": "sqlite or postgres?"}},
         env={"HERMES_KANBAN_TASK": ""}, expect={"json_error": True}),
    dict(id="opinion/empty-question", entry="handle_second_opinion",
         setup=lambda: None, call={"args": {"question": "  "}},
         env={"HERMES_KANBAN_TASK": "@TASK@"}, expect={"json_error": True}),
    dict(id="opinion/advisor-disabled", entry="handle_second_opinion",
         setup=lambda: make(), call={"args": {"question": "A or B?"}},
         env={"HERMES_KANBAN_TASK": "@TASK@"},
         settings={"advisor": dict(plugin.DEFAULTS["advisor"], enabled=False)},
         expect={"json_error": True}),
]


class ContractMatrixTest(unittest.TestCase):
    """Every row of MATRIX is one case. A failure names the case id."""

    @classmethod
    def setUpClass(cls):
        # A cron sweep has always run before a card can be parked: prime the watermark,
        # otherwise the first sweep legitimately skips the history.
        plugin.sweep(board=None)

    def setUp(self):
        os.environ["CALLING_MOM"] = "on"

    def run_case(self, case):
        tid = case["setup"]()
        env = dict(case.get("env") or {})
        if tid and tid != "no-such-task":
            for key, value in list(env.items()):
                if value == "@TASK@":
                    env[key] = tid
        call = dict(case["call"])
        entry = getattr(plugin, case["entry"])
        if ("task_id" in inspect.signature(entry).parameters and "task_id" not in call
                and tid and tid != "no-such-task"):
            call["task_id"] = tid
        patch = case.get("patch") or {}
        old = {}
        for name, value in patch.items():
            old[name] = getattr(plugin, name)
            setattr(plugin, name, (lambda *a, **k: value) if callable(value) is False else value)
        try:
            with patched(settings=case.get("settings"), **env):
                result = getattr(plugin, case["entry"])(**call)
        finally:
            for name, value in old.items():
                setattr(plugin, name, value)
        return tid, result

    def test_matrix(self):
        for case in MATRIX:
            with self.subTest(case=case["id"]):
                tid, result = self.run_case(case)
                exp = case["expect"]
                if "none" in exp:
                    self.assertIsNone(result, "%s: expected no directive" % case["id"])
                if "action" in exp:
                    self.assertEqual((result or {}).get("action"), exp["action"], case["id"])
                if "json_error" in exp:
                    self.assertIn("error", json.loads(result), case["id"])
                if tid and tid != "no-such-task":
                    state = observe(tid)
                    if "status" in exp:
                        self.assertEqual(state["status"], exp["status"], case["id"])
                    if "override" in exp:
                        self.assertEqual(state["override"], exp["override"], case["id"])


class ObserveContractTest(unittest.TestCase):
    """``_trace`` liveness rows: one per kind per minute, silent when observe is off."""

    def setUp(self):
        os.environ["CALLING_MOM"] = "on"
        plugin._last_trace.clear()

    def trace_rows(self):
        path = plugin._log_path()
        rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()] if path.exists() else []
        return [r for r in rows if r["kind"].startswith("contract-")]

    def test_one_row_per_minute_per_kind(self):
        plugin._trace("contract-tick")
        plugin._trace("contract-tick")  # debounced
        self.assertEqual(len(self.trace_rows()), 1)

    def test_observe_off_writes_nothing(self):
        plugin.DEFAULTS["observe"] = False
        try:
            plugin._trace("contract-off")
        finally:
            plugin.DEFAULTS["observe"] = True
        self.assertEqual(self.trace_rows(), [])


class EscalationMetricTest(unittest.TestCase):
    """Every escalation writes one log row with its reason -- that is the metric's only source."""

    def reasons(self):
        path = plugin._log_path()
        rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()] if path.exists() else []
        return [r["reason"] for r in rows if r.get("kind") == "escalated"]

    def test_claim_marker_counts_as_crit(self):
        tid = make(title="[crit] contract", state="running")
        plugin.on_claimed(task_id=tid)
        self.assertIn("crit", self.reasons())

    def test_block_hook_counts_as_blocked(self):
        tid = blocked()
        plugin.on_blocked(task_id=tid, reason="local model cannot")
        self.assertIn("blocked", self.reasons())

    def test_breaker_park_counts_as_breaker(self):
        tid = parked()
        plugin.on_worker_exited(task_id=tid, outcome="crashed")
        self.assertIn("breaker", self.reasons())


class EdgeContractTest(unittest.TestCase):
    """The remaining single-line branches: empty advice, an unstartable verify command,
    observe switched off, and the log tail-trim."""

    def setUp(self):
        from types import SimpleNamespace as NS
        os.environ["CALLING_MOM"] = "on"
        plugin._trackers.clear()
        plugin._history.clear()
        self.saved = {k: (dict(v) if isinstance(v, dict) else v) for k, v in plugin.DEFAULTS.items()}
        self.old_ctx = plugin._ctx
        plugin._ctx = NS(llm=base.FakeLlm(text="   "))  # empty answer
        self.old_log = os.environ.get("CALLING_MOM_LOG")
        os.environ["CALLING_MOM_LOG"] = str(Path(tempfile.mkdtemp()) / "cache-edge.jsonl")
        os.environ["HERMES_KANBAN_BOARD"] = "t-board"

    def tearDown(self):
        plugin.DEFAULTS.clear(); plugin.DEFAULTS.update(self.saved)
        plugin._ctx = self.old_ctx
        if self.old_log is None:
            os.environ.pop("CALLING_MOM_LOG", None)
        else:
            os.environ["CALLING_MOM_LOG"] = self.old_log
        path = plugin._log_path()
        if path.exists():
            path.unlink()

    def test_empty_advice_is_dropped(self):
        plugin.DEFAULTS["advisor"]["enabled"] = True
        plugin.DEFAULTS["advisor"]["boards"] = ["t-board"]
        tid = make(title="stuck", state="running")
        os.environ["HERMES_KANBAN_TASK"] = tid
        os.environ["HERMES_KANBAN_RUN_ID"] = "1"
        try:
            self.assertIsNone(plugin._advise((tid, "1"), "error", "same_error"))
        finally:
            os.environ.pop("HERMES_KANBAN_TASK", None)

    def test_verify_command_that_cannot_start_blocks_with_a_reason(self):
        tid = make(body="verify: python3 -c 0", created_by="user", state="running")
        os.environ["HERMES_KANBAN_TASK"] = tid
        orig = plugin.subprocess.run
        plugin.subprocess.run = lambda *a, **k: (_ for _ in ()).throw(OSError("no exec"))
        try:
            out = plugin.on_pre_tool(tool_name="kanban_complete", args={})
        finally:
            plugin.subprocess.run = orig
            os.environ.pop("HERMES_KANBAN_TASK", None)
        self.assertEqual(out["action"], "block")
        self.assertIn("could not start", out["message"])

    def test_observe_off_returns_none(self):
        plugin.DEFAULTS["observe"] = False
        tid = make(state="running")
        os.environ["HERMES_KANBAN_TASK"] = tid
        try:
            self.assertIsNone(plugin.on_tool_result(tool_name="terminal", args={}, result="x", status="ok"))
        finally:
            os.environ.pop("HERMES_KANBAN_TASK", None)

    def test_log_is_tail_trimmed_at_the_limit(self):
        path = plugin._log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join('{"kind":"old","n":%d}\n' % i for i in range(1500)))
        old = plugin._LOG_MAX_BYTES
        plugin._LOG_MAX_BYTES = 1000
        try:
            plugin._trace_row("new")
        finally:
            plugin._LOG_MAX_BYTES = old
        lines = path.read_text().splitlines()
        self.assertLessEqual(len(lines), 1001)
        self.assertIn('"kind": "new"', lines[-1])


class FailOpenTest(unittest.TestCase):
    """A broken plugin must never break a worker or a board mutation."""

    def setUp(self):
        os.environ["CALLING_MOM"] = "on"

    def test_observe_hook_is_fail_open(self):
        tid = make(state="running")
        os.environ["HERMES_KANBAN_TASK"] = tid
        os.environ["HERMES_KANBAN_RUN_ID"] = "1"
        orig = plugin.settings
        plugin.settings = lambda: (_ for _ in ()).throw(RuntimeError("config exploded"))
        try:
            self.assertIsNone(plugin.on_tool_result(
                tool_name="terminal", args={"command": "x"},
                result="boom", status="error", error_type="boom", error_message="boom"))
        finally:
            plugin.settings = orig
            os.environ.pop("HERMES_KANBAN_TASK", None)
            os.environ.pop("HERMES_KANBAN_RUN_ID", None)

    def test_claim_hook_is_fail_open(self):
        orig = plugin._connect
        plugin._connect = lambda board=None: (_ for _ in ()).throw(RuntimeError("db gone"))
        try:
            plugin.on_claimed(task_id="t_x")  # must not raise
        finally:
            plugin._connect = orig


if __name__ == "__main__":
    unittest.main()
