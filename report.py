"""The attention report: what reached whom, and what it cost. Stdlib only.

One function, ``build``, behind ``hermes calling-mom report`` and ``GET /summary``. It counts rows of the plugin's own
log (hashes and numbers, no prose), so the same log always gives the same report. ``render`` turns the result into text.

Limits, on purpose: Mom's cost is a lower bound (the first, escalating call of an Olymp advice is not logged), and the
price per token is one blended number (Mom's dollars over her tokens, input and output together).
"""
import json
import statistics

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


def build(rows, since=0, board=None, tasks=()) -> dict:
    """The numbers of one window: rows from ``since`` (epoch seconds) on, optionally of one board.

    A row belongs to ``board`` when it says so or when its card is in ``tasks`` (tool and advice rows carry no board)."""
    rows = [r for r in rows if r.get("ts", 0) >= since
            and (board is None or r.get("board") == board or r.get("task") in tasks)]
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
                "verify_failed": sum(1 for r in verify if r["state"] != "pass"),
                "tips_followed": len(of("advice_feedback", verdict=("followed",))),
                "tips_rejected": len(of("advice_feedback", verdict=("rejected",)))},
        "olymp": {"calls": olymp_calls},
        "you": {"briefs": sum(1 for r in questions if r["outcome"] == "human"), "answered": len(answers),
                "holds_released": sum(1 for r in answers if r.get("allow_once")),
                "median_answer_s": int(statistics.median(waits)) if waits else None},
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


def render(r) -> str:
    """The text of ``hermes calling-mom report``. ``r`` is ``build`` plus ``days`` (and ``board``, ``olymp.today/cap``)."""
    w, m, o, y, c = r["worker"], r["mom"], r["olymp"], r["you"], r["cost"]
    head = f"Calling mom, last {_n(r['days'], 'day')}" + (f", board {r['board']}" if r.get("board") else "")
    olymp = _n(o["calls"], "call") + (f", {o['today']} of {o['cap']} today" if "cap" in o else "")
    price = f"${c['mom_usd']:.4f}" if c["mom_usd"] else "no price reported"  # a provider without a price is not free
    wait = f" (median {duration(y['median_answer_s'])})" if y["median_answer_s"] is not None else ""
    lines = [
        head,
        f"Worker  {_n(w['tool_calls'], 'tool call')} in {_n(w['runs'], 'run')}, {_n(w['signals'], 'stuck signal')}; "
        f"{_n(w['api_calls'], 'API call')}, {w['tokens_in']:,} tokens in / {w['tokens_out']:,} out",
        f"Mom     {_n(m['calls'], 'call')}: {_n(m['answered'], 'question')} answered, {_n(m['checked'], 'pre-flight check')} "
        f"({_n(m['holds'], 'hold')}), {m['verified']} verify passed / {m['verify_failed']} failed, "
        f"tips followed {m['tips_followed']} / rejected {m['tips_rejected']}",
        f"Olymp   {olymp}",
        f"You     {_n(y['briefs'], 'brief')}, {_n(y['answered'], 'answer')}{wait}, {_n(y['holds_released'], 'hold')} released",
        f"Cost    Mom {price} ({c['mom_tokens_in']:,} in / {c['mom_tokens_out']:,} out tokens), "
        f"Olymp {_n(c['olymp_calls'], 'call')}",
    ]
    if c["all_on_mom_usd"] is not None:
        lines.append(f"        Every call on Mom would have cost ${c['all_on_mom_usd']:.4f} ({c['worker_tokens']:,} worker tokens)")
    return "\n".join(lines)
