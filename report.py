"""The attention report: what reached whom, and what it cost. Stdlib only.

One function, ``build``, behind ``hermes calling-mom report`` and ``GET /summary``. It counts rows of the plugin's own
log (hashes and numbers, no prose), so the same log always gives the same report. ``render`` turns the result into text.

Every number comes from a recorded row, and each section names its source. What was not observed is said so, never
shown as zero. Limits, on purpose: Mom's cost is a lower bound (the first, escalating call of an Olymp advice is not
logged), and the price per token is one blended number (Mom's dollars over her tokens, input and output together).
"""
import datetime
import json
import statistics
from collections import Counter

_FINISHED = ("pass", "fail", "timeout")  # a verify row is a result only in these states


def _sum(rows, key):
    total = 0
    for r in rows:
        try:
            total += int(r.get(key) or 0)
        except (TypeError, ValueError):
            pass
    return total


def _usd(rows):
    total = 0.0
    for r in rows:
        try:
            total += float(r.get("usd") or 0)
        except (TypeError, ValueError):
            pass
    return total


def read(path) -> list:
    """The rows of the log; a missing file is an empty log and a broken line is skipped."""
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def parse_when(text) -> int:
    """Epoch seconds of an ISO date or date-time (``2026-11-05``, ``2026-11-06T03:00``). No offset means UTC. Raises ValueError."""
    when = datetime.datetime.fromisoformat(str(text).strip().replace("Z", "+00:00"))
    return int((when if when.tzinfo else when.replace(tzinfo=datetime.timezone.utc)).timestamp())


def iso(ts) -> str:
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


_OUTSIDE_SOURCE = "kanban store (task_comments, task_events), read by the sweep"
_ADVICE_RESULT = {"followed": "acted"}  # before 1.5 "followed" meant "the worker did something next": that is `acted` now
_REACHED = ("triage", "to_person")


def _touches(rows) -> list:
    """Touch rows once per thing: the same store id written twice (a card that waited, then left) counts once, the last row wins."""
    kept = {}
    for n, r in enumerate(r for r in rows if r.get("kind") == "touch"):
        kept[(r.get("board"), r.get("src"), r.get("store_id") if r.get("store_id") is not None else f"row{n}")] = r
    return list(kept.values())


def _outside(rows, own) -> dict:
    touch = _touches(rows)
    comments = Counter(r.get("author") or "unknown" for r in touch if r.get("src") == "comment")
    changes = Counter(r.get("what") for r in touch if r.get("src") == "event" and r.get("what") not in _REACHED)
    reached = [r for r in touch if r.get("what") in _REACHED]
    return {"comments": sum(comments.values()), "authors": dict(comments), "changes": sum(changes.values()),
            "changes_by": dict(changes), "actor": "unattributed", "to_person": len(reached),
            "waiting": sum(1 for r in reached if r.get("wait_s") is None), "own_writes": len(own), "source": _OUTSIDE_SOURCE}


def _chat_waits(rows) -> dict:
    waits = [r for r in _touches(rows) if r.get("src") == "chat"]
    seconds = [int(r["wait_s"]) for r in waits if isinstance(r.get("wait_s"), (int, float))]
    return {"count": len(waits), "total_s": sum(seconds), "longest_s": max(seconds, default=0),
            "by_kind": dict(Counter(r.get("wait") or "other" for r in waits)),
            "source": "on_human_input_request / on_human_input_resolved"}


def _advice(rows) -> dict:
    """Delivered advice (a silent NO_ACTION answer is none) and what became of it. Advice without a verdict is unknown."""
    calls = sum(1 for r in rows if r.get("kind") == "advisor" and not r.get("noop"))
    results = Counter(r.get("result") or _ADVICE_RESULT.get(r.get("verdict")) for r in rows if r.get("kind") == "advice_feedback")
    known = {k: results[k] for k in ("followed", "acted", "ignored")}
    return {"calls": calls, **known, "unknown": max(0, calls - sum(known.values())),
            "source": "advisor rows and advice_feedback rows"}


def hm(seconds) -> str:
    """``45 s``, ``30 min``, ``1 h 51 min``."""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{seconds // 60} min"
    return f"{seconds // 3600} h" + (f" {seconds % 3600 // 60} min" if seconds % 3600 >= 60 else "")


def _quota(rows) -> dict:
    """Provider limits (a ``limited`` row is one limit, its seconds run to the reset time), providers down, cards that got a
    ``[quota]`` comment (``paused``), Mom calls skipped, fallback answers, and the cards flagged ``stalled``."""
    quota = [r for r in rows if r.get("kind") == "quota"]
    of = lambda state: [r for r in quota if r.get("state") == state]
    limits = [{"provider": r.get("provider"), "until": r.get("until"),
               "seconds": max(0, int(r["until"]) - int(r["ts"])) if isinstance(r.get("until"), (int, float)) else None}
              for r in of("limited")]
    stalls = [r for r in rows if r.get("kind") == "stall"]
    return {"limits": limits, "down": len(of("down")), "paused": len({r.get("task") for r in of("paused")}),
            "skipped": sum(1 for r in of("skipped") if r.get("tier") != "olymp"), "fallback": len(of("fallback")),
            "stalled": {"cards": len({r.get("task") for r in stalls}), "minutes": _sum(stalls, "minutes")},
            "source": "quota and stall rows: api_request_error, on_kanban_worker_exited, Mom and Olymp calls, the sweep"}


def build(rows, since=0, board=None, tasks=(), until=None) -> dict:
    """The numbers of one window: rows from ``since`` up to (not including) ``until`` (epoch seconds), optionally of one board.

    A row belongs to ``board`` when it says so or when its card is in ``tasks`` (tool and advice rows carry no board).
    Chat waits belong to you, not to a board."""
    rows = [r for r in rows if r.get("ts", 0) >= since and (until is None or r.get("ts", 0) < until)
            and (board is None or r.get("board") == board or r.get("task") in tasks or r.get("src") == "chat")]
    of = lambda kind, **want: [r for r in rows if r.get("kind") == kind and all(r.get(k) in v for k, v in want.items())]
    calls = [r for r in rows if "kind" not in r]
    usage, advice, olymp_advice = of("worker_usage"), of("advisor"), [r for r in of("advisor") if r.get("olymp")]
    questions = of("question", outcome=("answered", "human"))
    checks = of("preflight", verdict=("go", "hold"))
    verify = of("verify", state=_FINISHED)
    answers = of("human_answer")
    waits = [r["latency_s"] for r in answers if isinstance(r.get("latency_s"), (int, float))]
    # Mom's own rows: an Olymp advice and an Olymp question carry Olymp's tokens (a question row mixes both), so they stay out
    paid = [*(r for r in advice if not r.get("olymp")), *of("preflight"), *(r for r in of("question") if r.get("by") != "olymp")]
    mom_usd, mom_in, mom_out = _usd(paid), _sum(paid, "tokens_in"), _sum(paid, "tokens_out")
    worker_tokens = _sum(usage, "tokens_in") + _sum(usage, "tokens_out")
    olymp_calls = len(olymp_advice) + sum(1 for r in questions if r.get("by") == "olymp")
    priced = mom_usd > 0 and mom_in + mom_out > 0  # compare only when Mom reports a price
    return {
        "worker": {"tool_calls": len(calls), "runs": len({(r.get("task"), r.get("run")) for r in calls}),
                   "signals": sum(len(r.get("signals") or []) for r in calls), "api_calls": len(usage),
                   "tokens_in": _sum(usage, "tokens_in"), "tokens_out": _sum(usage, "tokens_out")},
        # every Olymp advice was asked of Mom first, so each advisor row is one Mom call
        "mom": {"calls": len(advice) + len(checks) + len(questions),
                "answered": sum(1 for r in questions if r["outcome"] == "answered" and r.get("by") == "mom"),
                "checked": len(checks), "holds": sum(1 for r in checks if r["verdict"] == "hold"),
                "verified": sum(1 for r in verify if r["state"] == "pass"),
                "verify_failed": sum(1 for r in verify if r["state"] != "pass")},
        "olymp": {"calls": olymp_calls},
        "you": {"briefs": sum(1 for r in questions if r["outcome"] == "human"), "answered": len(answers),
                "holds_released": sum(1 for r in answers if r.get("allow_once")),
                "median_answer_s": int(statistics.median(waits)) if waits else None,
                "outside": _outside(rows, of("own_write")), "chat_waits": _chat_waits(rows)},
        "advice": _advice(rows),
        "quota": _quota(rows),
        "cost": {"mom_usd": round(mom_usd, 6), "mom_tokens_in": mom_in, "mom_tokens_out": mom_out,
                 "olymp_calls": olymp_calls,
                 "worker_tokens": worker_tokens,
                 "all_on_mom_usd": round(worker_tokens * mom_usd / (mom_in + mom_out), 6) if priced else None},
    }


def duration(seconds) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} s"
    return f"{seconds // 60} min" if seconds < 3600 else f"{seconds / 3600:.1f} h"


def _n(count, word):
    return f"{count} {word}" + ("" if count == 1 else "s")


def _window(r) -> str:
    if r.get("days") is not None:
        return f"last {_n(r['days'], 'day')}"
    return f"{r['since']} to {r['until']}" if r.get("until") else f"since {r['since']}"


def _outside_line(o) -> str:
    if o.get("observed") is False:
        return "not observed (no sweep has read this board: opt it in, then run the sweep)"
    who = f" ({', '.join(f'{a} {n}' for a, n in sorted(o['authors'].items(), key=lambda kv: (-kv[1], kv[0])))})" if o["authors"] else ""
    return (f"{_n(o['comments'], 'comment')}{who} · {_n(o['changes'], 'board change')} ({o['actor']}) · "
            f"{_n(o['waiting'], 'card')} waiting on a person")


def _chat_line(cw) -> str:
    if cw.get("observed") is False:
        return "not observed (calling_mom.count.chat_waits is off)"
    kinds = f" ({', '.join(f'{k} {n}' for k, n in sorted(cw['by_kind'].items()))})" if cw["by_kind"] else ""
    return (f"{_n(cw['count'], 'wait')}, {duration(cw['total_s'])} in total, longest {duration(cw['longest_s'])}{kinds}"
            if cw["count"] else "0 waits")


def _quota_line(q) -> str:
    each = lambda x: f"{x['provider'] or 'a provider'}, {hm(x['seconds']) if x['seconds'] is not None else 'reset unknown'}"
    limits = _n(len(q["limits"]), "limit") + (f" ({'; '.join(map(each, q['limits']))})" if q["limits"] else "")
    down = f" · {q['down']} provider{'' if q['down'] == 1 else 's'} down" if q["down"] else ""
    return f"{limits}{down} · {_n(q['paused'], 'card')} paused · {_n(q['skipped'], 'Mom call')} skipped · fallback used {q['fallback']}×"


def render(r) -> str:
    """The text of ``hermes calling-mom report``. ``r`` is ``build`` plus ``days`` (and ``board``, ``olymp.today/cap``)."""
    w, m, o, y, c, a, q = r["worker"], r["mom"], r["olymp"], r["you"], r["cost"], r["advice"], r["quota"]
    head = f"Calling Mom, {_window(r)}" + (f", board {r['board']}" if r.get("board") else "")
    olymp = _n(o["calls"], "call") + (f", {o['today']} of {o['cap']} today" if "cap" in o else "")
    tokens = f"{c['mom_tokens_in']:,} in / {c['mom_tokens_out']:,} out tokens"
    price = f"${c['mom_usd']:.4f} ({tokens})" if c["mom_usd"] else f"{tokens} on a subscription (no per-token price)"  # no price is not free
    wait = f" (median {duration(y['median_answer_s'])})" if y["median_answer_s"] is not None else ""
    lines = [
        head,
        f"Worker  {_n(w['tool_calls'], 'tool call')} in {_n(w['runs'], 'run')}, {_n(w['signals'], 'stuck signal')}; "
        f"{_n(w['api_calls'], 'API call')}, {w['tokens_in']:,} tokens in / {w['tokens_out']:,} out",
        f"Mom     {_n(m['calls'], 'call')}: {_n(m['answered'], 'question')} answered, {_n(m['checked'], 'pre-flight check')} "
        f"({_n(m['holds'], 'hold')}), {m['verified']} verify passed / {m['verify_failed']} failed",
        f"Olymp   {olymp}",
        f"You     {_n(y['briefs'], 'brief')}, {_n(y['answered'], 'answer')}{wait}, {_n(y['holds_released'], 'hold')} released",
        f"        outside help  {_outside_line(y['outside'])}",
        f"        chat waits    {_chat_line(y['chat_waits'])}",
        f"Advice  {_n(a['calls'], 'call')}: {a['followed']} followed · {a['acted']} acted · {a['ignored']} ignored · {a['unknown']} unknown",
        f"Quota   {_quota_line(q)}",
        f"Stalled {_n(q['stalled']['cards'], 'card')}" + (f", {hm(q['stalled']['minutes'] * 60)} without a heartbeat when flagged" if q["stalled"]["cards"] else ""),
        f"Cost    Mom {price}, Olymp {_n(c['olymp_calls'], 'call')}",
    ]
    if c["all_on_mom_usd"] is not None:
        lines.append(f"        Every call on Mom would have cost ${c['all_on_mom_usd']:.4f} ({c['worker_tokens']:,} worker tokens)")
    return "\n".join(lines)
