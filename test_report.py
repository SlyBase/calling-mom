"""Tests for the attention report (WP6): report.py, ``hermes calling-mom report`` and the plugin wiring.

    cd <plugin dir> && python -m unittest -v test_report

The fixture log below is hand-counted; EXPECTED is what a person gets with pencil and paper. A changed number
here means a changed contract (docs/TECHNICAL.md, "Attention report").
"""
import importlib.util
import json
import os
import types
import unittest
from pathlib import Path

import test_calling_mom as base
import test_cli

PLUGIN_DIR = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("calling_mom_report_under_test", PLUGIN_DIR / "report.py")
report = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(report)

plugin, cli = base.plugin, test_cli.cli
NOW = 1_000_000
DAY = 86400
T = NOW - 3600        # inside the 7-day window
OLD = NOW - 8 * DAY   # outside it


def row(kind=None, ts=T, **fields):
    return {"ts": ts, **({"kind": kind} if kind else {}), **fields}


ROWS = [
    {"ts": T, "kind": "tick", "board": "default"},                                             # not an event of any rung
    # Worker: 4 tool calls in 3 runs (a/1, a/2, b/1), 3 signals fired; the old row is outside the window
    row(task="a", run="1", tool="terminal", ok=True, signals=[]),
    row(task="a", run="1", tool="terminal", ok=False, signals=["same_error"]),
    row(task="a", run="2", tool="terminal", ok=True, signals=["loop", "steps"]),
    row(task="b", run="1", tool="read_file", ok=True, signals=[]),
    row(ts=OLD, task="a", run="1", tool="terminal", ok=True, signals=["loop"]),
    # Worker tokens seen by post_api_request: 3 requests, 3500 in / 350 out
    row("worker_usage", task="a", run="1", tokens_in=1000, tokens_out=100),
    row("worker_usage", task="a", run="1", tokens_in=2000, tokens_out=200),
    row("worker_usage", task="b", run="1", tokens_in=500, tokens_out=50),
    row("worker_usage", ts=OLD, task="a", run="1", tokens_in=9999, tokens_out=9999),
    # Advisor: two Mom calls (one said nothing, still a call and still paid) and one Olymp call (Mom asked first)
    row("advisor", task="a", olymp=False, tokens_in=100, tokens_out=20, usd=0.01),
    row("advisor", task="a", olymp=False, noop=True, tokens_in=50, tokens_out=5, usd=0.005),
    row("advisor", task="b", olymp=True, tokens_in=400, tokens_out=80, usd=0.2),
    row("advisor_error", task="a", error="Timeout"),                                             # no call happened
    row("advisor", ts=OLD, task="a", olymp=False, tokens_in=1, tokens_out=1, usd=1.0),
    # Advice feedback: 1 followed with evidence, 1 old-style "followed" (the old meaning is acted), 1 old-style "rejected"
    # (no evidence either way: it stays in unknown); the old row is outside the window
    row("advice_feedback", task="a", result="followed", anchors=2, match="abc123"),
    row("advice_feedback", task="a", verdict="followed"),
    row("advice_feedback", task="b", verdict="rejected"),
    row("advice_feedback", ts=OLD, task="b", result="followed", anchors=1, match="x"),
    # Outside help (touch rows: ids and counts). Comments: default 2 (one row twice: dedupe by store id), desktop 1, 1 old
    row("touch", src="comment", what="comment", author="default", actor="other", store_id=1, task="a", board="x"),
    row("touch", src="comment", what="comment", author="default", actor="other", store_id=1, task="a", board="x"),
    row("touch", src="comment", what="comment", author="default", actor="other", store_id=2, task="b", board="x"),
    row("touch", src="comment", what="comment", author="desktop", actor="other", store_id=3, task="b", board="x"),
    row("touch", ts=OLD, src="comment", what="comment", author="default", actor="other", store_id=4, task="a", board="x"),
    # Board changes: link 2, unlink 1, reclaim 1 (the same store id as a comment is another thing: the key has the source)
    row("touch", src="event", what="link", actor="unattributed", store_id=1, task="a", board="x"),
    row("touch", src="event", what="link", actor="unattributed", store_id=11, task="b", board="x"),
    row("touch", src="event", what="unlink", actor="unattributed", store_id=12, task="b", board="x"),
    row("touch", src="event", what="reclaim", actor="unattributed", store_id=13, task="a", board="x"),
    # Reached a person: triage 20 waited 60 s (written twice, the last row wins), triage 21 and to_person 22 still wait
    row("touch", src="event", what="triage", store_id=20, task="a", board="x"),
    row("touch", src="event", what="triage", store_id=20, task="a", board="x", wait_s=60),
    row("touch", src="event", what="triage", store_id=21, task="b", board="x"),
    row("touch", src="event", what="to_person", store_id=22, task="b", board="x"),
    row("own_write", task="a", board="x", what="pin", store_id=30),
    row("own_write", task="a", board="x", what="unblock", store_id=31),
    # Chat waits: clarify 30 s, approval 90 s; one old
    row("touch", src="chat", what="chat_wait", wait="clarify", outcome="answered", store_id="r1", wait_s=30),
    row("touch", src="chat", what="chat_wait", wait="approval", outcome="denied", store_id="r2", wait_s=90),
    row("touch", ts=OLD, src="chat", what="chat_wait", wait="sudo", outcome="timeout", store_id="r3", wait_s=999),
    # Pre-flight: 2 model calls (1 go, 1 hold); the other three rows cost no call
    row("preflight", task="a", verdict="go", tokens_in=30, tokens_out=3, usd=0.003),
    row("preflight", task="a", verdict="hold", tokens_in=40, tokens_out=4, usd=0.004),
    row("preflight", task="a", verdict="held_again"),
    row("preflight", task="a", verdict="allowed", reason="cap"),
    row("preflight", task="a", verdict="skipped", reason="local_only"),
    # Questions: Mom answered 1, Mom briefed 1, Mom escalated and Olymp answered 1 (500/50 tokens are Mom and Olymp mixed)
    row("question", task="a", outcome="answered", by="mom", tokens_in=60, tokens_out=6, usd=0.006),
    row("question", task="a", outcome="human", by="mom", tokens_in=70, tokens_out=7, usd=0.007),
    row("question", task="b", outcome="answered", by="olymp", tokens_in=500, tokens_out=50, usd=0.002),
    row("question", task="b", outcome="skipped", reason="cap"),
    row("question", task="b", outcome="error", reason="timeout"),
    # Verify: 1 pass, 2 not (fail, timeout); started and stale are not results
    row("verify", task="a", state="started"),
    row("verify", task="a", state="pass"),
    row("verify", task="a", state="fail"),
    row("verify", task="b", state="timeout"),
    row("verify", task="b", state="stale"),
    # You: 3 answers (latency 60, 120, 300 s), 1 of them released a hold
    row("human_answer", task="a", via="desktop", accepted=True, allow_once=False, latency_s=60),
    row("human_answer", task="a", via="cli", accepted=False, allow_once=True, latency_s=120),
    row("human_answer", task="b", via="card", accepted=False, allow_once=False, latency_s=300),
]

EXPECTED = {
    "worker": {"tool_calls": 4, "runs": 3, "signals": 3, "api_calls": 3, "tokens_in": 3500, "tokens_out": 350},
    # calls: 3 advisor + 2 pre-flight + 3 questions = 8
    "mom": {"calls": 8, "answered": 1, "checked": 2, "holds": 1, "verified": 1, "verify_failed": 2},
    "olymp": {"calls": 2},
    "you": {"briefs": 1, "answered": 3, "holds_released": 1, "median_answer_s": 120,
            "outside": {"comments": 3, "authors": {"default": 2, "desktop": 1}, "changes": 4,
                        "changes_by": {"link": 2, "unlink": 1, "reclaim": 1}, "actor": "unattributed",
                        "to_person": 3, "waiting": 2, "own_writes": 2,
                        "source": "kanban store (task_comments, task_events), read by the sweep"},
            "chat_waits": {"count": 2, "total_s": 120, "longest_s": 90, "by_kind": {"approval": 1, "clarify": 1},
                           "source": "on_human_input_request / on_human_input_resolved"}},
    # advice: 2 delivered (the silent one is no advice); 1 followed, 1 acted, 0 ignored, the rest unknown
    "advice": {"calls": 2, "followed": 1, "acted": 1, "ignored": 0, "unknown": 0,
               "source": "advisor rows and advice_feedback rows"},
    # Mom's own rows: advisor 100+50 / 20+5 / 0.015, pre-flight 70 / 7 / 0.007, questions she alone answered 130 / 13 / 0.013
    # price per token = 0.035 / 395; every worker token (3850) at that price = 0.341139
    "cost": {"mom_usd": 0.035, "mom_tokens_in": 350, "mom_tokens_out": 45, "olymp_calls": 2,
             "worker_tokens": 3850, "all_on_mom_usd": 0.341139},
}

TEXT = """\
Calling mom, last 7 days
Worker  4 tool calls in 3 runs, 3 stuck signals; 3 API calls, 3,500 tokens in / 350 out
Mom     8 calls: 1 question answered, 2 pre-flight checks (1 hold), 1 verify passed / 2 failed
Olymp   2 calls
You     1 brief, 3 answers (median 2 min), 1 hold released
        outside help  3 comments (default 2, desktop 1) · 4 board changes (unattributed) · 2 cards waiting on a person
        chat waits    2 waits, 2 min in total, longest 1 min (approval 1, clarify 1)
Advice  2 calls: 1 followed · 1 acted · 0 ignored · 0 unknown
Cost    Mom $0.0350 (350 in / 45 out tokens), Olymp 2 calls
        Every call on Mom would have cost $0.3411 (3,850 worker tokens)"""


class BuildTest(unittest.TestCase):
    def build(self, rows=ROWS, **kw):
        return report.build(rows, since=NOW - 7 * DAY, **kw)

    def test_every_number_equals_the_hand_count(self):
        self.assertEqual(self.build(), EXPECTED)

    def test_an_empty_log_is_all_zero_and_shows_no_comparison(self):
        out = self.build([])
        self.assertEqual(out["worker"], {"tool_calls": 0, "runs": 0, "signals": 0, "api_calls": 0, "tokens_in": 0, "tokens_out": 0})
        self.assertEqual(out["mom"]["calls"], 0)
        self.assertEqual(out["you"]["briefs"], 0)
        self.assertEqual(out["you"]["outside"], {"comments": 0, "authors": {}, "changes": 0, "changes_by": {}, "actor": "unattributed",
                                                 "to_person": 0, "waiting": 0, "own_writes": 0,
                                                 "source": "kanban store (task_comments, task_events), read by the sweep"})
        self.assertEqual(out["you"]["chat_waits"]["count"], 0)
        self.assertEqual(out["advice"], {"calls": 0, "followed": 0, "acted": 0, "ignored": 0, "unknown": 0,
                                         "source": "advisor rows and advice_feedback rows"})
        self.assertEqual(out["cost"], {"mom_usd": 0.0, "mom_tokens_in": 0, "mom_tokens_out": 0, "olymp_calls": 0,
                                       "worker_tokens": 0, "all_on_mom_usd": None})

    def test_without_a_price_there_is_no_comparison(self):
        free = [dict(r, usd=0.0) if "usd" in r else r for r in ROWS]   # a provider that reports no cost
        out = self.build(free)
        self.assertEqual((out["cost"]["mom_usd"], out["cost"]["all_on_mom_usd"]), (0.0, None))
        self.assertEqual(out["cost"]["worker_tokens"], 3850)

    def test_a_price_without_worker_tokens_compares_nothing_to_nothing(self):
        out = self.build([r for r in ROWS if r.get("kind") != "worker_usage"])
        self.assertEqual((out["cost"]["worker_tokens"], out["cost"]["all_on_mom_usd"]), (0, 0.0))

    def test_the_window_starts_at_since(self):
        self.assertEqual(report.build(ROWS, since=0)["worker"]["tool_calls"], 5)
        self.assertEqual(report.build(ROWS, since=T + 1)["worker"]["tool_calls"], 0)

    def test_a_board_keeps_its_own_rows_and_the_rows_of_its_cards(self):
        rows = [row("question", task="t_1", board="x", outcome="human", by="mom"),
                row("advisor", task="t_2", olymp=False),            # no board in this row: found through the card
                row("advisor", task="t_3", olymp=False),            # another board's card
                row("question", task="t_3", board="y", outcome="human", by="mom")]
        out = report.build(rows, board="x", tasks={"t_1", "t_2"})
        self.assertEqual((out["mom"]["calls"], out["you"]["briefs"]), (2, 1))

    def test_broken_numbers_in_a_row_count_as_zero(self):
        out = self.build([row("advisor", olymp=False, tokens_in=None, tokens_out="x", usd=None),
                          row("human_answer", latency_s="soon"), row("human_answer", latency_s=40)])
        self.assertEqual((out["mom"]["calls"], out["cost"]["mom_tokens_in"], out["you"]["median_answer_s"]), (1, 0, 40))
        self.assertEqual(self.build([row("advisor", olymp=False, usd="free")])["cost"]["mom_usd"], 0.0)


class WindowTest(unittest.TestCase):
    def test_a_window_is_since_inclusive_and_until_exclusive(self):
        rows = [row("touch", ts=ts, src="comment", what="comment", author="d", store_id=ts, task="a") for ts in (10, 20, 30)]
        count = lambda **kw: report.build(rows, **kw)["you"]["outside"]["comments"]
        self.assertEqual((count(since=10), count(since=10, until=30), count(since=11, until=30), count(since=0, until=10)), (3, 2, 1, 0))

    def test_iso_dates_and_date_times_are_utc(self):
        self.assertEqual(report.parse_when("2026-11-05"), 1793836800)
        self.assertEqual(report.parse_when("2026-11-06T03:00"), 1793836800 + 86400 + 3 * 3600)
        self.assertEqual(report.parse_when("2026-11-06T03:00:30Z"), 1793836800 + 86400 + 3 * 3600 + 30)
        self.assertEqual(report.parse_when("2026-11-06T04:00+01:00"), report.parse_when("2026-11-06T03:00"))
        for bad in ("yesterday", "", "2026-13-01"):
            with self.subTest(bad), self.assertRaises(ValueError):
                report.parse_when(bad)

    def test_iso_renders_a_utc_minute(self):
        self.assertEqual(report.iso(1793836800 + 86400 + 3 * 3600), "2026-11-06T03:00Z")


class AdviceTest(unittest.TestCase):
    def advice(self, *rows, calls=4):
        base_rows = [row("advisor", task="a", olymp=False)] * calls
        return report.build([*base_rows, *rows], since=0)["advice"]

    def test_the_four_results_and_the_rest_is_unknown(self):
        out = self.advice(row("advice_feedback", result="followed"), row("advice_feedback", result="followed"),
                          row("advice_feedback", result="acted"), row("advice_feedback", result="ignored"))
        self.assertEqual({k: out[k] for k in ("calls", "followed", "acted", "ignored", "unknown")},
                         {"calls": 4, "followed": 2, "acted": 1, "ignored": 1, "unknown": 0})

    def test_advice_without_a_verdict_is_unknown_and_an_unknown_row_changes_nothing(self):
        out = self.advice(row("advice_feedback", result="unknown", anchors=0, match=None), row("advice_feedback", result="acted"))
        self.assertEqual((out["acted"], out["unknown"]), (1, 3))

    def test_more_verdicts_than_calls_never_make_a_negative_unknown(self):
        out = self.advice(row("advice_feedback", result="followed"), row("advice_feedback", result="followed"), calls=1)
        self.assertEqual((out["followed"], out["unknown"]), (2, 0))


class OutsideTest(unittest.TestCase):
    def build(self, *rows, **kw):
        return report.build(list(rows), since=0, **kw)["you"]

    def test_a_chat_wait_belongs_to_you_whatever_the_board(self):
        you = self.build(row("touch", src="chat", what="chat_wait", wait="sudo", store_id="r", wait_s=7),
                         row("touch", src="comment", what="comment", author="d", store_id=1, task="a", board="y"), board="x")
        self.assertEqual((you["chat_waits"]["count"], you["outside"]["comments"]), (1, 0))

    def test_a_chat_wait_without_a_duration_counts_as_a_wait_and_adds_no_time(self):
        you = self.build(row("touch", src="chat", what="chat_wait", wait="sudo", store_id="r"))
        self.assertEqual((you["chat_waits"]["count"], you["chat_waits"]["total_s"], you["chat_waits"]["longest_s"]), (1, 0, 0))

    def test_a_card_that_left_has_no_waiting_row_left(self):
        you = self.build(row("touch", src="event", what="to_person", store_id=5, task="a", board="x"),
                         row("touch", src="event", what="to_person", store_id=5, task="a", board="x", wait_s=12))
        self.assertEqual((you["outside"]["to_person"], you["outside"]["waiting"]), (1, 0))

    def test_a_chat_row_without_an_id_is_not_merged_with_another(self):
        you = self.build(row("touch", src="chat", what="chat_wait", wait="sudo", wait_s=1),
                         row("touch", src="chat", what="chat_wait", wait="sudo", wait_s=2))
        self.assertEqual(you["chat_waits"]["count"], 2)


class RenderTest(unittest.TestCase):
    def test_the_text_of_the_fixture(self):
        self.assertEqual(report.render({"days": 7, **EXPECTED}), TEXT)

    def test_a_board_and_the_daily_olymp_cap_are_named(self):
        text = report.render({"days": 30, "board": "coding", **EXPECTED, "olymp": {"calls": 2, "today": 1, "cap": 6}})
        lines = text.splitlines()
        self.assertEqual(lines[0], "Calling mom, last 30 days, board coding")
        self.assertEqual(lines[3], "Olymp   2 calls, 1 of 6 today")

    def test_no_price_no_comparison_line_and_no_median(self):
        r = {"days": 7, **EXPECTED, "you": {**EXPECTED["you"], "median_answer_s": None},
             "cost": {**EXPECTED["cost"], "all_on_mom_usd": None}}
        text = report.render(r)
        self.assertNotIn("Every call on Mom", text)
        self.assertIn("You     1 brief, 3 answers, 1 hold released", text)

    def test_a_comparison_of_zero_is_still_shown(self):
        r = {"days": 7, **EXPECTED, "cost": {**EXPECTED["cost"], "worker_tokens": 0, "all_on_mom_usd": 0.0}}
        self.assertIn("Every call on Mom would have cost $0.0000 (0 worker tokens)", report.render(r))

    def test_a_provider_without_a_price_is_not_shown_as_free(self):
        r = {"days": 7, **EXPECTED, "cost": {**EXPECTED["cost"], "mom_usd": 0.0, "all_on_mom_usd": None}}
        self.assertIn("Cost    Mom 350 in / 45 out tokens on a subscription (no per-token price), Olymp 2 calls", report.render(r))

    def test_not_observed_is_said_and_never_shown_as_zero(self):
        you = {**EXPECTED["you"], "outside": {**EXPECTED["you"]["outside"], "observed": False},
               "chat_waits": {**EXPECTED["you"]["chat_waits"], "observed": False}}
        text = report.render({"days": 7, **EXPECTED, "you": you})
        self.assertIn("        outside help  not observed (no sweep has read this board: opt it in, then run the sweep)", text)
        self.assertIn("        chat waits    not observed (calling_mom.count.chat_waits is off)", text)

    def test_nothing_counted_reads_as_zero_when_it_was_observed(self):
        you = {**EXPECTED["you"], "outside": {**report.build([], since=0)["you"]["outside"], "observed": True},
               "chat_waits": {**report.build([], since=0)["you"]["chat_waits"], "observed": True}}
        text = report.render({"days": 7, **EXPECTED, "you": you})
        self.assertIn("outside help  0 comments · 0 board changes (unattributed) · 0 cards waiting on a person", text)
        self.assertIn("chat waits    0 waits", text)

    def test_a_window_without_days_names_both_ends(self):
        text = report.render({"days": None, "since": "2026-11-05T00:00Z", "until": "2026-11-06T03:00Z", **EXPECTED})
        self.assertEqual(text.splitlines()[0], "Calling mom, 2026-11-05T00:00Z to 2026-11-06T03:00Z")
        text = report.render({"days": None, "since": "2026-11-05T00:00Z", "until": None, **EXPECTED, "board": "coding"})
        self.assertEqual(text.splitlines()[0], "Calling mom, since 2026-11-05T00:00Z, board coding")

    def test_plurals_and_durations(self):
        r = {"days": 1, **EXPECTED, "mom": {**EXPECTED["mom"], "calls": 1, "checked": 1, "holds": 0, "answered": 0},
             "olymp": {"calls": 1}, "you": {**EXPECTED["you"], "briefs": 0, "answered": 1, "holds_released": 0, "median_answer_s": 45,
                                                          "outside": {**EXPECTED["you"]["outside"], "comments": 1, "authors": {"default": 1},
                                                                      "waiting": 1}, "chat_waits": {**EXPECTED["you"]["chat_waits"], "count": 1,
                                                                                                   "total_s": 5, "longest_s": 5, "by_kind": {"sudo": 1}}},
             "worker": {**EXPECTED["worker"], "tool_calls": 1, "runs": 1, "signals": 1}}
        text = report.render(r)
        self.assertIn("last 1 day\n", text + "\n")
        self.assertIn("1 tool call in 1 run, 1 stuck signal", text)
        self.assertIn("Olymp   1 call\n", text + "\n")
        self.assertIn("0 briefs, 1 answer (median 45 s)", text)
        self.assertIn("1 comment (default 1)", text)
        self.assertIn("1 card waiting on a person", text)
        self.assertIn("1 wait, 5 s in total, longest 5 s (sudo 1)", text)
        self.assertIn("Advice  2 calls", text)
        self.assertEqual([report.duration(s) for s in (59, 60, 3599, 3600, 90000)], ["59 s", "1 min", "59 min", "1.0 h", "25.0 h"])


class CommandTest(unittest.TestCase):
    def env(self):
        s, calls = test_cli.Script(), []

        def run(days, board, since=None, until=None):
            calls.append((days, board, since, until))
            return {"days": days, "board": board, **EXPECTED}

        e = cli.Env(load=lambda: {}, save=lambda c: None, providers=lambda: [], boards=lambda: [], spent=lambda: {},
                    probe=lambda p, m: None, version="0.22.5", internals=True, ask=s.ask, say=s.say, cron=lambda: None,
                    sweeps=lambda: [], report=run)
        return e, s, calls

    def test_the_text_report_is_the_default(self):
        e, s, calls = self.env()
        self.assertEqual(cli.cmd_report(e, days=7, board=None), 0)
        self.assertEqual(calls, [(7, None, None, None)])
        self.assertEqual(s.text, TEXT)

    def test_json_prints_the_numbers_for_a_script(self):
        e, s, calls = self.env()
        self.assertEqual(cli.cmd_report(e, days=3, board="coding", as_json=True), 0)
        self.assertEqual(calls, [(3, "coding", None, None)])
        self.assertEqual(json.loads(s.text), {"days": 3, "board": "coding", **EXPECTED})

    def test_a_window_with_since_and_until_is_passed_on_as_typed(self):
        e, s, calls = self.env()
        self.assertEqual(cli.cmd_report(e, days=7, board="coding", since="2026-11-05", until="2026-11-06T03:00"), 0)
        self.assertEqual(calls, [(7, "coding", "2026-11-05", "2026-11-06T03:00")])

    def test_a_date_that_is_none_is_refused_before_any_report_is_built(self):
        e, s, calls = self.env()
        self.assertEqual(cli.cmd_report(e, since="last tuesday"), 2)
        self.assertEqual(cli.cmd_report(e, until="2026-99-99"), 2)
        self.assertEqual(calls, [])
        self.assertIn("ISO date", s.text)

    def test_the_parser_knows_the_command(self):
        import argparse
        parser = argparse.ArgumentParser()
        cli.setup_parser(parser)
        args = parser.parse_args(["report"])
        self.assertEqual((args.cm_cmd, args.days, args.board, args.json), ("report", 7, None, False))
        args = parser.parse_args(["report", "--days", "30", "--board", "coding", "--json"])
        self.assertEqual((args.days, args.board, args.json), (30, "coding", True))
        args = parser.parse_args(["report", "--since", "2026-11-05", "--until", "2026-11-06T03:00"])
        self.assertEqual((args.since, args.until), ("2026-11-05", "2026-11-06T03:00"))


class WiringTest(base.QuestionFixture, unittest.TestCase):
    """The plugin side: where the rows come from and how the report reads them."""

    def put(self, kind=None, ts=None, **fields):
        import time
        path = Path(os.environ["CALLING_MOM_LOG"])
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as fh:
            fh.write(json.dumps({"ts": int(ts or time.time()), **({"kind": kind} if kind else {}), **fields}) + "\n")

    def test_a_worker_api_request_logs_its_tokens_and_no_text(self):
        usage = {"input_tokens": 1000, "cache_read_tokens": 4000, "cache_write_tokens": 500, "output_tokens": 200, "prompt_tokens": 5500}
        plugin.on_api_response(assistant_message=types.SimpleNamespace(content="Running the tests now."), usage=usage)
        (r,) = base.log_rows("worker_usage")
        self.assertEqual((r["task"], r["tokens_in"], r["tokens_out"]), (self.tid, 5500, 200))
        self.assertNotIn("tests", json.dumps(r))

    def test_without_prompt_tokens_the_three_input_buckets_are_added(self):
        plugin.on_api_response(assistant_message=None, usage={"input_tokens": 10, "cache_read_tokens": 20, "output_tokens": 5})
        (r,) = base.log_rows("worker_usage")
        self.assertEqual((r["tokens_in"], r["tokens_out"]), (30, 5))

    def test_no_usage_no_row_and_outside_kanban_no_row(self):
        plugin.on_api_response(assistant_message=None, usage=None)
        plugin.on_api_response(assistant_message=None)
        self.assertEqual(base.log_rows("worker_usage"), [])
        del os.environ["HERMES_KANBAN_TASK"]
        plugin.on_api_response(assistant_message=None, usage={"input_tokens": 1, "output_tokens": 1})
        self.assertEqual(base.log_rows("worker_usage"), [])

    def test_observe_off_logs_nothing(self):
        plugin.DEFAULTS["observe"] = False
        try:
            plugin.on_api_response(assistant_message=None, usage={"input_tokens": 1, "output_tokens": 1})
        finally:
            plugin.DEFAULTS["observe"] = True
        self.assertEqual(base.log_rows("worker_usage"), [])

    def test_a_go_and_a_hold_log_what_the_question_cost(self):
        self.llm.text = "GO"
        self.llm._res.text = "GO"
        plugin.on_pre_tool(tool_name="terminal", args={"command": "git push --force origin main"})
        (r,) = base.log_rows("preflight")
        self.assertEqual((r["verdict"], r["tokens_in"], r["tokens_out"], r["usd"]), ("go", 1800, 120, 0.001))

    def test_the_report_reads_the_log_adds_the_olymp_cap_and_names_the_window(self):
        self.put("advisor", task="t_a", olymp=False, tokens_in=10, tokens_out=1, usd=0.5)
        self.put("advisor", ts=1, task="t_a", olymp=False)                      # 1970: outside any window
        plugin._add_spend(0.0, 0, olymp=2)
        out = plugin.attention_report(days=7)
        self.assertEqual((out["days"], out["board"], out["mom"]["calls"]), (7, None, 1))
        self.assertEqual(out["olymp"], {"calls": 0, "today": 2, "cap": plugin.settings()["olymp"]["max_per_day"]})

    def test_a_board_is_filtered_through_its_cards(self):
        mine = base.with_conn(lambda c: base.kb.create_task(c, title="mine", body="x", assignee="w"))
        self.put("advisor", task=mine, olymp=False)
        self.put("advisor", task="t_elsewhere", olymp=False)
        self.assertEqual(plugin.attention_report(days=7, board="t-board")["mom"]["calls"], 1)
        self.assertEqual(plugin.attention_report(days=7)["mom"]["calls"], 2)

    def test_the_cards_of_an_archived_task_still_belong_to_the_board(self):
        gone = base.with_conn(lambda c: base.kb.create_task(c, title="done long ago", body="x", assignee="w"))
        base.with_conn(lambda c: base.kb.archive_task(c, gone))
        self.put("advisor", task=gone, olymp=False)
        self.assertEqual(plugin.attention_report(days=7, board="t-board")["mom"]["calls"], 1)

    def test_a_hold_logs_what_the_question_cost_like_a_go(self):
        self.llm.text = "HOLD: it rewrites shared history. Instead: push a branch."
        self.llm._res.text = self.llm.text
        plugin.on_pre_tool(tool_name="terminal", args={"command": "git push --force origin main"})
        (r,) = base.log_rows("preflight")
        self.assertEqual((r["verdict"], r["tokens_in"], r["tokens_out"], r["usd"]), ("hold", 1800, 120, 0.001))

    def test_a_board_that_cannot_be_read_still_counts_its_own_rows(self):
        self.put("advisor", task="t_a", board="t-board", olymp=False)
        self.put("advisor", task="t_b", olymp=False)

        def broken(board):
            raise OSError("no such board")

        saved, plugin._connect = plugin._connect, broken
        try:
            self.assertEqual(plugin.attention_report(days=7, board="t-board")["mom"]["calls"], 1)
        finally:
            plugin._connect = saved

    def test_a_window_with_since_and_until_counts_only_that_window(self):
        self.put("touch", ts=100, src="comment", what="comment", author="default", store_id=1, task="t_a", board="t-board")
        self.put("touch", ts=200, src="comment", what="comment", author="default", store_id=2, task="t_a", board="t-board")
        out = plugin.attention_report(board="t-board", since="1970-01-01T00:01:30", until="1970-01-01T00:02:30", now=1000)
        self.assertEqual(out["you"]["outside"]["comments"], 1)
        self.assertEqual((out["days"], out["since"], out["until"]), (None, "1970-01-01T00:01Z", "1970-01-01T00:02Z"))
        out = plugin.attention_report(days=2, until="1970-01-04", rows=[], now=10 ** 9)  # days count back from until
        self.assertEqual((out["since"], out["until"]), ("1970-01-02T00:00Z", "1970-01-04T00:00Z"))

    def test_days_without_a_window_count_back_from_now(self):
        out = plugin.attention_report(days=1, now=2 * 86400)
        self.assertEqual((out["days"], out["since"], out["until"]), (1, "1970-01-02T00:00Z", None))

    def test_a_board_the_sweep_has_not_read_is_not_observed_and_a_scanned_one_is(self):
        self.assertFalse(plugin.attention_report(board="t-board")["you"]["outside"]["observed"])
        plugin._write_state({"touches": {"t-board": {}}})
        self.assertTrue(plugin.attention_report(board="t-board")["you"]["outside"]["observed"])
        self.assertFalse(plugin.attention_report(board="elsewhere")["you"]["outside"]["observed"])
        self.assertTrue(plugin.attention_report()["you"]["outside"]["observed"])  # all boards: at least one was read

    def test_chat_waits_are_observed_when_the_switch_is_on(self):
        self.assertFalse(plugin.attention_report()["you"]["chat_waits"]["observed"])
        plugin.DEFAULTS["count"]["chat_waits"] = True
        self.assertTrue(plugin.attention_report()["you"]["chat_waits"]["observed"])

    def test_a_missing_log_is_an_empty_report(self):
        Path(os.environ["CALLING_MOM_LOG"]).unlink(missing_ok=True)
        self.assertEqual(plugin.attention_report(days=7)["mom"]["calls"], 0)


if __name__ == "__main__":
    unittest.main()
