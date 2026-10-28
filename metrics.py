"""Prometheus text for the calling-mom plugin + Pushgateway push. Stdlib only.

Counters are derived from the plugin's own JSONL log (hashes and counts, no content), so a
restart of any process never resets them. The cron sweep pushes the text every minute.
"""

import contextlib
import json
import sqlite3
import time
import urllib.request
from collections import Counter

_CATEGORIES = ("error", "loop", "validation", "architecture")
_SIGNALS = ("same_error", "loop", "malformed", "steps")
_RUNGS = ("effort",)

_FAMILIES = {  # name -> (type, help)
    "calling_mom_events_total": ("counter", "Plugin log rows by kind."),
    "calling_mom_advisor_calls_total": ("counter", "Advisor questions to the strong model by category."),
    "calling_mom_advisor_tokens_total": ("counter", "Advisor tokens by direction."),
    "calling_mom_advisor_cost_usd_total": ("counter", "Advisor cost as reported by the provider (0 if it reports none)."),
    "calling_mom_stuck_signals_total": ("counter", "Stuck signals fired by worker tool results."),
    "calling_mom_verify_total": ("counter", "verify: gate runs by result."),
    "calling_mom_verify_ignored_total": ("counter", "verify: lines that were not run, by reason."),
    "calling_mom_review_redirects_total": ("counter", "Completions handed to the reviewer profile."),
    "calling_mom_ladder_total": ("counter", "Free-rung retries (more reasoning effort) by rung."),
    "calling_mom_escalations_total": ("counter", "Cards pinned to the strong model, by reason (crit|blocked|breaker)."),
    "calling_mom_gave_up_age_seconds": ("gauge", "Age of the newest gave_up event per board (breaker park), seconds."),
    "calling_mom_spend_today_usd": ("gauge", "Advisor spend today (UTC), USD."),
    "calling_mom_spend_today_tokens": ("gauge", "Advisor tokens today (UTC)."),
    "calling_mom_spend_cap_usd": ("gauge", "Configured daily advisor cap, USD."),
    "calling_mom_spend_cap_tokens": ("gauge", "Configured daily advisor cap, tokens."),
    "calling_mom_cards": ("gauge", "Cards by board and state: escalated (has an escalation comment) / parked (strong model pinned, waiting for a human)."),
    "calling_mom_last_push_timestamp_seconds": ("gauge", "Unix time of the last successful render (liveness)."),
}


def _num(value):
    return repr(round(value, 6)) if isinstance(value, float) else str(value)


def _label(value):
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _sample(name, value, **labels):
    inner = ",".join(f'{k}="{_label(v)}"' for k, v in labels.items())
    return f"{name}{{{inner}}} {_num(value)}" if inner else f"{name} {_num(value)}"


def render(rows, spend, caps, boards, now) -> str:
    kinds = Counter(r["kind"] for r in rows if r.get("kind"))
    advisor = [r for r in rows if r.get("kind") == "advisor"]
    by_category = Counter({c: 0 for c in _CATEGORIES})
    by_category.update(r.get("category") or "unknown" for r in advisor)
    signals = Counter({s: 0 for s in _SIGNALS})
    for r in rows:
        signals.update(r.get("signals") or [])
    verify = [r for r in rows if r.get("kind") == "verify"]
    ignored = Counter(r.get("why") or "unknown" for r in rows if r.get("kind") == "verify_ignored")
    rungs = Counter({k: 0 for k in _RUNGS})
    rungs.update(r.get("rung") or "unknown" for r in rows if r.get("kind") == "ladder")
    escalations = Counter(r.get("reason") or "other" for r in rows if r.get("kind") == "escalated")

    samples = {name: [] for name in _FAMILIES}
    for kind, n in sorted(kinds.items()):
        samples["calling_mom_events_total"].append(_sample("calling_mom_events_total", n, kind=kind))
    for cat in sorted(by_category):
        samples["calling_mom_advisor_calls_total"].append(
            _sample("calling_mom_advisor_calls_total", by_category[cat], category=cat))
    for direction, key in (("in", "tokens_in"), ("out", "tokens_out")):
        samples["calling_mom_advisor_tokens_total"].append(
            _sample("calling_mom_advisor_tokens_total", sum(int(r.get(key) or 0) for r in advisor), direction=direction))
    samples["calling_mom_advisor_cost_usd_total"].append(
        _sample("calling_mom_advisor_cost_usd_total", sum(float(r.get("usd") or 0) for r in advisor)))
    for sig in sorted(signals):
        samples["calling_mom_stuck_signals_total"].append(
            _sample("calling_mom_stuck_signals_total", signals[sig], signal=sig))
    for result, want in (("pass", True), ("fail", False)):
        samples["calling_mom_verify_total"].append(
            _sample("calling_mom_verify_total", sum(1 for r in verify if bool(r.get("ok")) is want), result=result))
    for why, n in sorted(ignored.items()):
        samples["calling_mom_verify_ignored_total"].append(
            _sample("calling_mom_verify_ignored_total", n, why=why))
    samples["calling_mom_review_redirects_total"].append(
        _sample("calling_mom_review_redirects_total", kinds.get("review_redirect", 0)))
    for rung in sorted(rungs):
        samples["calling_mom_ladder_total"].append(_sample("calling_mom_ladder_total", rungs[rung], rung=rung))
    for reason, n in sorted(escalations.items()):
        samples["calling_mom_escalations_total"].append(
            _sample("calling_mom_escalations_total", n, reason=reason))
    samples["calling_mom_spend_today_usd"].append(_sample("calling_mom_spend_today_usd", spend.get("usd", 0.0)))
    samples["calling_mom_spend_today_tokens"].append(_sample("calling_mom_spend_today_tokens", spend.get("tokens", 0)))
    samples["calling_mom_spend_cap_usd"].append(_sample("calling_mom_spend_cap_usd", caps["usd"]))
    samples["calling_mom_spend_cap_tokens"].append(_sample("calling_mom_spend_cap_tokens", caps["tokens"]))
    for b in boards:
        for state in ("escalated", "parked"):
            samples["calling_mom_cards"].append(_sample("calling_mom_cards", b[state], board=b["board"], state=state))
        if b.get("gave_up_age") is not None:
            samples["calling_mom_gave_up_age_seconds"].append(
                _sample("calling_mom_gave_up_age_seconds", b["gave_up_age"], board=b["board"]))
    samples["calling_mom_last_push_timestamp_seconds"].append(
        _sample("calling_mom_last_push_timestamp_seconds", int(now)))

    out = []
    for name, (mtype, help_text) in _FAMILIES.items():
        if samples[name]:
            out += [f"# HELP {name} {help_text}", f"# TYPE {name} {mtype}", *samples[name]]
    return "\n".join(out) + "\n"


def read_rows(path) -> list:
    rows = []
    try:
        text = path.read_text()
    except OSError:
        return rows
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def board_counts(db_paths, now=None) -> list:
    """``[{"board", "escalated", "parked", "gave_up_age"}]`` from read-only board DBs.

    ``gave_up_age`` is the age in seconds of the newest breaker park on that board (None when the
    board never parked). It is what lets an alert say "a card parked, but nothing escalated it".
    Unreadable boards are skipped."""
    out = []
    now = time.time() if now is None else now
    for slug, path in db_paths.items():
        try:
            with contextlib.closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as db:
                escalated = db.execute(
                    "SELECT COUNT(DISTINCT task_id) FROM task_comments WHERE author = 'escalation'").fetchone()[0]
                parked = db.execute(
                    "SELECT COUNT(*) FROM tasks WHERE status IN ('blocked', 'triage') AND model_override IS NOT NULL").fetchone()[0]
                newest = db.execute(
                    "SELECT MAX(created_at) FROM task_events WHERE kind = 'gave_up'").fetchone()[0]
            out.append({"board": slug, "escalated": escalated, "parked": parked,
                        "gave_up_age": int(now - newest) if newest else None})
        except sqlite3.Error:
            continue
    return out


def push(text, urls, job="calling_mom", instance="hermes", timeout=5) -> bool:
    """PUT (replaces the whole group) to the first reachable Pushgateway; never raises."""
    for url in urls:
        try:
            req = urllib.request.Request(
                f"{url.rstrip('/')}/metrics/job/{job}/instance/{instance}", data=text.encode(), method="PUT",
                headers={"Content-Type": "text/plain; version=0.0.4"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if 200 <= resp.status < 300:
                    return True
        except Exception:
            continue
    return False
