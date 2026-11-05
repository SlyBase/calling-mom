"""Backend of the Calling mom Desktop half: routes under /api/plugins/calling-mom/.

Runs in the gateway process. Reads the plugin's own log and, read-only, the Kanban boards; the only write is
POST /answer, which goes through the plugin's ``answer_card`` (the same door as ``hermes calling-mom answer``).
"""
import importlib.util
import json
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter()
DAY = 86400
_plugin = None
_FINISHED = ("pass", "fail", "timeout")  # a verify row is a result only in these states
BADGES = ("held", "for you", "verify failed", "olymp", "mom", "verified")  # most urgent first; a card shows the first two
_SHOWN = {  # rows worth a line in the activity list
    "advisor": lambda r: not r.get("noop"),
    "question": lambda r: r.get("outcome") in ("answered", "human"),
    "preflight": lambda r: r.get("verdict") in ("go", "hold", "held_again"),
    "verify": lambda r: r.get("state") in _FINISHED,
    "human_answer": lambda r: True,
    "escalated": lambda r: True,
}


def mom():
    """The plugin module, loaded once by path: this file runs in the gateway, outside the plugin package."""
    global _plugin
    if _plugin is None:
        spec = importlib.util.spec_from_file_location("calling_mom_host", Path(__file__).resolve().parent.parent / "__init__.py")
        _plugin = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_plugin)
    return _plugin


def _rows(since=0):
    """Log rows from ``since`` (epoch seconds) on; a broken line or a missing file is skipped."""
    try:
        lines = mom()._log_path().read_text().splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("ts", 0) >= since:
            rows.append(row)
    return rows


def _counts(rows):
    """Counts per rung. Mom's calls: advice, pre-flight checks, and every question she answered or briefed."""
    of = lambda kind, **want: [r for r in rows if r.get("kind") == kind and all(r.get(k) in v for k, v in want.items())]
    advice = of("advisor")
    questions = of("question", outcome=("answered", "human"))
    checks = of("preflight", verdict=("go", "hold"))
    verify = of("verify", state=_FINISHED)
    return {
        "worker": {"tool_calls": sum(1 for r in rows if "kind" not in r),
                   "signals": sum(len(r.get("signals") or []) for r in rows if "kind" not in r)},
        "mom": {"calls": sum(1 for r in advice if not r.get("olymp")) + len(checks) + len(questions),
                "answered": sum(1 for r in questions if r["outcome"] == "answered" and r.get("by") == "mom"),
                "checked": len(checks), "holds": sum(1 for r in checks if r["verdict"] == "hold"),
                "verified": sum(1 for r in verify if r["state"] == "pass"),
                "verify_failed": sum(1 for r in verify if r["state"] != "pass")},
        "olymp": {"calls": sum(1 for r in advice if r.get("olymp")) + sum(1 for r in questions if r.get("by") == "olymp")},
        "you": {"briefs": sum(1 for r in questions if r["outcome"] == "human"), "answered": len(of("human_answer"))},
    }


def _recent(rows, limit=10):
    shown = [r for r in rows if _SHOWN.get(r.get("kind"), lambda _: False)(r)]
    return [{"ts": r["ts"], "kind": r["kind"], "task": r.get("task"), "board": r.get("board"),
             "detail": r.get("outcome") or r.get("verdict") or r.get("state") or r.get("via") or r.get("category") or r.get("reason"),
             "by": r.get("by") or ("olymp" if r.get("olymp") else None)}
            for r in reversed(shown[-limit:])]


def _board(board):
    """The cards of one board, read-only: ``[(task, comments)]``; comments only for blocked cards."""
    with mom()._connect(board) as conn:
        return [(t, mom().kb.list_comments(conn, t.id) if t.status == "blocked" else []) for t in mom().kb.list_tasks(conn)]


def _waiting(board, cards, state, now):
    """The cards of a board that wait for a person: a `[for you]` brief nobody answered yet, or a pre-flight hold nobody allowed."""
    answered, allowed = state.get("answered") or {}, state.get("allowances") or {}
    items = []
    for task, comments in cards:
        if task.status != "blocked":
            continue
        briefs = [c for c in comments if (c.body or "").startswith(mom()._FOR_YOU)]
        brief = briefs[-1] if briefs and answered.get(task.id) != briefs[-1].id else None
        holds = [h for h in mom().holds_for(task.id) if h["cmd_hash"] not in (allowed.get(task.id) or {})]
        if brief or holds:
            since = min(([brief.created_at] if brief else []) + [h.get("ts", now) for h in holds])
            items.append({"board": board, "task_id": task.id, "title": task.title,
                          "brief": brief.body.removeprefix(mom()._FOR_YOU).strip() if brief else None,
                          "holds": [{"reason": h.get("reason"), "alternative": h.get("alternative")} for h in holds],
                          "can_accept": bool(brief and mom()._recommendation(comments)),
                          "age_s": max(0, int(now - since))})
    return items


def _needs_you():
    state, now = mom()._load_holds(), time.time()
    items = [i for b in mom()._core.list_board_slugs() if mom()._opted_in(b) for i in _waiting(b, _board(b), state, now)]
    return sorted(items, key=lambda i: -i["age_s"])


def _activity(rows):
    """Task id -> badge names the log earns it. The last finished verify run decides ``verified`` or ``verify failed``."""
    seen = {}
    for r in rows:
        kind, found = r.get("kind"), seen.setdefault(r.get("task"), set())
        if kind == "advisor" and not r.get("noop"):
            found.add("olymp" if r.get("olymp") else "mom")
        elif kind == "question" and r.get("outcome") in ("answered", "human"):
            found.add("olymp" if r.get("by") == "olymp" else "mom")
        elif kind == "preflight" and r.get("verdict") in ("go", "hold"):
            found.add("mom")
        elif kind == "verify" and r.get("state") in _FINISHED:
            found -= {"verified", "verify failed"}
            found.add("verified" if r["state"] == "pass" else "verify failed")
    return seen


@router.get("/cards")
def cards(board: str | None = None):
    """``{task id: [badge name, ...]}`` for the cards the plugin touched, at most two each, most urgent first."""
    slugs = mom()._core.list_board_slugs()
    if board is not None:
        slugs = [board] if board in slugs else []
    activity, state, now, out = _activity(_rows()), mom()._load_holds(), time.time(), {}
    for slug in slugs:
        cards_ = _board(slug)
        waiting = {i["task_id"]: i for i in _waiting(slug, cards_, state, now)} if mom()._opted_in(slug) else {}
        for task, _ in cards_:
            names = set(activity.get(task.id, ()))
            if task.id in waiting:
                names |= ({"for you"} if waiting[task.id]["brief"] else set()) | ({"held"} if waiting[task.id]["holds"] else set())
            picked = [n for n in BADGES if n in names][:2]
            if picked:
                out[task.id] = picked
    return out


@router.get("/needs-you")
def needs_you():
    return _needs_you()


@router.get("/summary")
def summary(days: int = Query(7, ge=1, le=90)):
    now = time.time()
    rows = _rows(now - days * DAY)
    out = _counts(rows)
    out["olymp"] |= {"today": mom()._spent_today()["olymp"], "cap": mom().settings()["olymp"]["max_per_day"]}
    out["today"] = {"mom_calls": _counts([r for r in rows if r["ts"] >= now - now % DAY])["mom"]["calls"]}
    out["you"]["waiting"] = len(_needs_you())
    return {"days": days, **out, "recent": _recent(rows)}


class Answer(BaseModel):
    board: str
    task_id: str
    text: str | None = None
    accept: bool = False
    allow_once: bool = False


@router.post("/answer")
def answer(body: Answer):
    """A person's click: ``answer_card`` with ``by="desktop"``. The board must exist, so a name is never turned into a path."""
    if body.board not in mom()._core.list_board_slugs():
        raise HTTPException(404, f"no board {body.board!r}")
    out = mom().answer_card(body.board, body.task_id, body.text, by="desktop", accept=body.accept, allow_once=body.allow_once)
    if not out["ok"]:
        raise HTTPException(out["status"], out["error"])
    return out
