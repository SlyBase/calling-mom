"""Tests for metrics.py: Prometheus text rendering and Pushgateway push (stdlib only).

    cd <plugin dir> && python -m unittest -v test_metrics
"""

import http.server
import importlib.util
import json
import tempfile
import threading
import unittest
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent


def load():
    spec = importlib.util.spec_from_file_location("ke_metrics_under_test", PLUGIN_DIR / "metrics.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


metrics = load()

ROWS = [
    {"kind": "tick"},
    {"tool": "terminal", "ok": False, "signals": ["same_error"]},
    {"tool": "terminal", "ok": False, "signals": ["same_error", "loop"]},
    {"tool": "read_file", "ok": True, "signals": []},
    {"kind": "advisor", "category": "error", "tokens_in": 300, "tokens_out": 120, "usd": 0.002},
    {"kind": "advisor", "category": "architecture", "tokens_in": 200, "tokens_out": 80, "usd": 0.001},
    {"kind": "verify", "ok": True, "fails": 0},
    {"kind": "verify", "ok": False, "fails": 1},
    {"kind": "verify_ignored", "why": "untrusted creator"},
    {"kind": "review_redirect", "reviewer": "reviewer"},
    {"kind": "ladder", "rung": "effort"},
]


def lines(text):
    return [x for x in text.splitlines() if x and not x.startswith("#")]


class RenderTest(unittest.TestCase):
    def render(self, **kw):
        base = dict(rows=ROWS, spend={"usd": 0.5, "tokens": 1000}, caps={"usd": 2.0, "tokens": 200000}, boards=[], now=1000)
        base.update(kw)
        return metrics.render(**base)

    def test_every_metric_has_help_and_type_and_the_text_ends_with_newline(self):
        text = self.render()
        self.assertTrue(text.endswith("\n"))
        names = {x.split("{")[0].split(" ")[0] for x in lines(text)}
        for n in names:
            self.assertIn(f"# TYPE {n} ", text, n)
            self.assertIn(f"# HELP {n} ", text, n)

    def test_advice_feedback_counts_followed_and_rejected_from_the_first_push(self):
        out = lines(self.render())
        self.assertIn('calling_mom_advice_feedback_total{verdict="followed"} 0', out)
        rows = ROWS + [{"kind": "advice_feedback", "verdict": "rejected"}]
        self.assertIn('calling_mom_advice_feedback_total{verdict="rejected"} 1', lines(self.render(rows=rows)))

    def test_provider_failures_and_olymp_calls_are_counted_from_the_first_push(self):
        out = lines(self.render())
        self.assertIn("calling_mom_advisor_errors_total 0", out)
        self.assertIn("calling_mom_olymp_calls_total 0", out)
        rows = ROWS + [{"kind": "advisor_error", "error": "RuntimeError"}] * 2 + [{"kind": "advisor", "olymp": True}]
        out = lines(self.render(rows=rows))
        self.assertIn("calling_mom_advisor_errors_total 2", out)
        self.assertIn("calling_mom_olymp_calls_total 1", out)

    def test_advisor_counters(self):
        out = lines(self.render())
        self.assertIn('calling_mom_advisor_calls_total{category="error"} 1', out)
        self.assertIn('calling_mom_advisor_calls_total{category="architecture"} 1', out)
        self.assertIn('calling_mom_advisor_tokens_total{direction="in"} 500', out)
        self.assertIn('calling_mom_advisor_tokens_total{direction="out"} 200', out)
        self.assertIn("calling_mom_advisor_cost_usd_total 0.003", out)

    def test_stuck_signals_are_counted_per_signal(self):
        out = lines(self.render())
        self.assertIn('calling_mom_stuck_signals_total{signal="same_error"} 2', out)
        self.assertIn('calling_mom_stuck_signals_total{signal="loop"} 1', out)

    def test_verify_results_and_ignored(self):
        out = lines(self.render())
        self.assertIn('calling_mom_verify_total{result="pass"} 1', out)
        self.assertIn('calling_mom_verify_total{result="fail"} 1', out)
        self.assertIn('calling_mom_verify_ignored_total{why="untrusted creator"} 1', out)

    def test_review_and_ladder(self):
        out = lines(self.render())
        self.assertIn("calling_mom_review_redirects_total 1", out)
        self.assertIn('calling_mom_ladder_total{rung="effort"} 1', out)

    def test_escalation_series_exists_even_without_escalations(self):
        """Without a zero-initialised series an `increase()` alert can never fire."""
        out = lines(self.render(rows=[]))
        for reason in ("crit", "blocked", "breaker"):
            self.assertIn('calling_mom_escalations_total{reason="%s"} 0' % reason, out)

    def test_escalations_are_counted_per_reason(self):
        rows = list(ROWS) + [
            {"kind": "escalated", "reason": "breaker"},
            {"kind": "escalated", "reason": "breaker"},
            {"kind": "escalated", "reason": "blocked"},
        ]
        out = lines(self.render(rows=rows))
        self.assertIn('calling_mom_escalations_total{reason="breaker"} 2', out)
        self.assertIn('calling_mom_escalations_total{reason="blocked"} 1', out)

    def test_gave_up_age_is_a_per_board_gauge(self):
        boards = [{"board": "coding", "escalated": 1, "parked": 0, "gave_up_age": 42},
                  {"board": "default", "escalated": 0, "parked": 0, "gave_up_age": None}]
        out = lines(self.render(boards=boards))
        self.assertIn('calling_mom_gave_up_age_seconds{board="coding"} 42', out)
        self.assertFalse([x for x in out if 'board="default"' in x and "gave_up_age" in x],
                         "a board that never parked must not export the gauge")

    def test_spend_gauges_and_caps(self):
        out = lines(self.render())
        self.assertIn("calling_mom_spend_today_usd 0.5", out)
        self.assertIn("calling_mom_spend_today_tokens 1000", out)
        self.assertIn("calling_mom_spend_cap_usd 2.0", out)
        self.assertIn("calling_mom_spend_cap_tokens 200000", out)

    def test_board_gauges(self):
        out = lines(self.render(boards=[{"board": "coding", "escalated": 3, "parked": 1}]))
        self.assertIn('calling_mom_cards{board="coding",state="escalated"} 3', out)
        self.assertIn('calling_mom_cards{board="coding",state="parked"} 1', out)

    def test_liveness_timestamp(self):
        self.assertIn("calling_mom_last_push_timestamp_seconds 1000", lines(self.render()))

    def test_empty_input_still_renders_zero_counters(self):
        out = lines(self.render(rows=[]))
        self.assertIn("calling_mom_review_redirects_total 0", out)
        self.assertIn('calling_mom_verify_total{result="pass"} 0', out)

    def test_label_values_are_escaped(self):
        out = lines(self.render(boards=[{"board": 'we"ird\\name', "escalated": 1, "parked": 0}]))
        self.assertIn('calling_mom_cards{board="we\\"ird\\\\name",state="escalated"} 1', out)


class ReadRowsTest(unittest.TestCase):
    def test_garbage_lines_are_skipped(self):
        p = Path(tempfile.mkdtemp()) / "x.jsonl"
        p.write_text('{"kind":"tick"}\nnot json\n\n{"kind":"advisor"}\n')
        self.assertEqual([r["kind"] for r in metrics.read_rows(p)], ["tick", "advisor"])

    def test_missing_file_is_empty(self):
        self.assertEqual(metrics.read_rows(Path("/nonexistent/none.jsonl")), [])


class BoardCountsTest(unittest.TestCase):
    def test_counts_escalated_and_parked_cards(self):
        import os
        path = Path(tempfile.mkdtemp()) / "kanban.db"
        os.environ.setdefault("HERMES_KANBAN_DB", str(path))
        from hermes_cli import kanban_db as kb
        from hermes_cli import kanban_db_connect as kbc

        kbc.init_db(path)
        with kbc.connect_closing(db_path=path) as c:
            a = kb.create_task(c, title="escalated", assignee="d")
            kb.add_comment(c, a, "escalation", "Escalated to x")
            p = kb.create_task(c, title="parked", assignee="d")
            kb.set_model_override(c, p, "m/strong", provider="openrouter")
            kb.block_task(c, p, reason="stuck", kind="capability")
            kb.create_task(c, title="plain", assignee="d")
        got = metrics.board_counts({"b1": str(path)})
        self.assertEqual(got[0]["escalated"], 1)
        self.assertEqual(got[0]["parked"], 1)

    def test_gave_up_age_comes_from_the_newest_breaker_park(self):
        import os
        path = Path(tempfile.mkdtemp()) / "kanban.db"
        os.environ.setdefault("HERMES_KANBAN_DB", str(path))
        from hermes_cli import kanban_db as kb
        from hermes_cli import kanban_db_connect as kbc
        from hermes_cli import kanban_db_dispatch as kd
        import time as _t

        kbc.init_db(path)
        with kbc.connect_closing(db_path=path) as c:
            tid = kb.create_task(c, title="parked twice", assignee="d")
            kd._record_task_failure(c, tid, "boom", outcome="crashed", failure_limit=1)
        got = metrics.board_counts({"b1": str(path)}, now=_t.time())
        self.assertIsNotNone(got[0]["gave_up_age"])
        self.assertLess(got[0]["gave_up_age"], 120)
        # a board without any park exports no age at all
        empty = Path(tempfile.mkdtemp()) / "empty.db"
        kbc.init_db(empty)
        self.assertIsNone(metrics.board_counts({"b0": str(empty)})[0]["gave_up_age"])

    def test_unreadable_board_is_skipped(self):
        self.assertEqual(metrics.board_counts({"gone": "/nonexistent/kanban.db"}), [])


class _Gateway(http.server.BaseHTTPRequestHandler):
    seen = []

    def do_PUT(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        _Gateway.seen.append((self.path, body))
        self.send_response(200); self.end_headers()

    def log_message(self, *a):
        pass


class PushTest(unittest.TestCase):
    def setUp(self):
        _Gateway.seen = []
        self.server = http.server.HTTPServer(("127.0.0.1", 0), _Gateway)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_put_replaces_the_group(self):
        self.assertTrue(metrics.push("x 1\n", [self.url], job="calling_mom", instance="hermes"))
        path, body = _Gateway.seen[0]
        self.assertEqual(path, "/metrics/job/calling_mom/instance/hermes")
        self.assertEqual(body, "x 1\n")

    def test_dead_gateway_is_skipped_for_the_next_one(self):
        self.assertTrue(metrics.push("x 1\n", ["http://127.0.0.1:9", self.url], timeout=1))
        self.assertEqual(len(_Gateway.seen), 1)

    def test_all_dead_returns_false_and_never_raises(self):
        self.assertFalse(metrics.push("x 1\n", ["http://127.0.0.1:9"], timeout=1))

    def test_no_gateway_configured_is_a_noop(self):
        self.assertFalse(metrics.push("x 1\n", []))


if __name__ == "__main__":
    unittest.main()
