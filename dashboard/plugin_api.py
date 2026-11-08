"""Backend of the Calling Mom Desktop half: routes under /api/plugins/calling-mom/.

Runs in the gateway process. Reads the plugin's own log and, read-only, the Kanban boards; the only write is
POST /answer, which goes through the plugin's ``answer_card`` (the same door as ``hermes calling-mom answer``).
"""
import importlib.util
import re
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter()
DAY = 86400
_plugin = None
_FINISHED = ("pass", "fail", "timeout")  # a verify row is a result only in these states
BADGES = ("held", "for you", "stalled", "verify failed", "olymp", "mom", "verified")  # most urgent first; a card shows the first two
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


def _version():
    """The plugin version from plugin.yaml (no YAML parser in the gateway needed for one line)."""
    return re.search(r"^version:\s*(\S+)", (Path(__file__).resolve().parent.parent / "plugin.yaml").read_text(), re.M).group(1)


def _rows(since=0):
    """Log rows from ``since`` (epoch seconds) on; a broken line or a missing file is skipped."""
    return [r for r in mom().report.read(mom()._log_path()) if r.get("ts", 0) >= since]


def _recent(rows, limit=10):
    shown = [r for r in rows if _SHOWN.get(r.get("kind"), lambda _: False)(r)]
    titles = _titles()
    return [{"ts": r["ts"], "kind": r["kind"], "task": r.get("task"), "board": r.get("board"),
             "detail": r.get("outcome") or r.get("verdict") or r.get("state") or r.get("via") or r.get("category") or r.get("reason"),
             "by": r.get("by") or ("olymp" if r.get("olymp") else None),
             "title": titles.get(r.get("task"))}
            for r in reversed(shown[-limit:])]


def _titles():
    """Task id -> card title over the opted-in boards: a log row names only the id, so the page joins the title here."""
    return {t.id: t.title for b in mom()._core.list_board_slugs() if mom()._opted_in(b) for t, _ in _board(b)}


def _active():
    """Running/ready/blocked cards on the opted-in boards, with the worker's last chat message (null when there is none), newest last-activity first."""
    items = []
    for board in mom()._core.list_board_slugs():
        if mom()._opted_in(board):
            items.extend(_active_tasks(board))
    return sorted(items, key=lambda i: -i.pop("_last_activity"))


def _last_chat(board, task):
    """The last assistant message of the card's worker session (what the LLM last said), null when there is none.
    The session id is the ``Session:`` header of the card's worker log; Hermes' own session reader finds the message."""
    try:
        header = (mom()._core.kb.worker_logs_dir(board) / f"{task.id}.log").read_text(encoding="utf-8").splitlines()[0]
        if not header.startswith("Session:"):
            return None
        text = mom()._core.last_assistant_message(task.assignee or "default", header.removeprefix("Session:").strip())
    except Exception:  # a display must never break the page: no log, no store, an unknown profile, a Hermes change
        return None
    return text[:300] if text else None


def _active_tasks(board):
    """The running/ready/blocked cards of one board, each with the worker's last chat message; the last-activity stamp comes from the card and its comments."""
    with mom()._connect(board) as conn:
        out = []
        for t in mom().kb.list_tasks(conn):
            if t.status in ("running", "ready", "blocked"):
                comments = mom().kb.list_comments(conn, t.id)
                out.append({
                    "board": board, "task_id": t.id, "title": t.title, "status": t.status,
                    "last_chat": _last_chat(board, t),
                    "_last_activity": max([t.created_at or 0] + [c.created_at for c in comments])})
        return out


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


def _stalled(board, cards, now):
    """The running cards of a board without a heartbeat for ``calling_mom.stall_minutes``, read only (the sweep comments, a person reclaims)."""
    quota, minutes = mom().quota, mom().settings()["stall_minutes"]
    return [{"board": board, "task_id": t.id, "title": t.title, "brief": None, "holds": [], "can_accept": False,
             "stalled": {"minutes": quota.minutes_silent(t, now),
                         "reclaim": f"hermes kanban --board {board} reclaim {t.id} --reason stalled"},
             "age_s": max(0, int(now - quota.last_beat(t)))}
            for t in quota.stalled([t for t, _ in cards], now, minutes)]


def _needs_you():
    state, now = mom()._load_holds(), time.time()
    items = []
    for b in mom()._core.list_board_slugs():
        if mom()._opted_in(b):
            cards_ = _board(b)
            items += _waiting(b, cards_, state, now) + _stalled(b, cards_, now)
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
        opted = mom()._opted_in(slug)
        waiting = {i["task_id"]: i for i in _waiting(slug, cards_, state, now)} if opted else {}
        stalled = {i["task_id"] for i in _stalled(slug, cards_, now)} if opted else set()
        for task, _ in cards_:
            names = set(activity.get(task.id, ())) | ({"stalled"} if task.id in stalled else set())
            if task.id in waiting:
                names |= ({"for you"} if waiting[task.id]["brief"] else set()) | ({"held"} if waiting[task.id]["holds"] else set())
            picked = [n for n in BADGES if n in names][:2]
            if picked:
                out[task.id] = picked
    return out


@router.get("/needs-you")
def needs_you():
    return _needs_you()


@router.get("/active")
def active():
    """The cards climbing right now: running/ready on the opted-in boards, with the worker's last chat message, newest last-activity first."""
    return _active()


@router.get("/summary")
def summary(days: int = Query(7, ge=1, le=90), hours: int | None = Query(None, ge=1, le=2160), since: str | None = None, until: str | None = None):
    """The attention report (``hermes calling-mom report``) plus what only the Desktop shows: today, waiting, recent.
    ``hours`` takes priority over ``days`` (sub-day windows, 1..2160); ``since``/``until`` (ISO date or date-time, UTC) replace both."""
    now = time.time()
    try:
        end = mom().report.parse_when(until) if until else None
        if since is not None:
            start = mom().report.parse_when(since)
            days_arg = days  # explicit since wins; days only feeds the label
        else:
            start = (end or now) - (hours * 3600 if hours is not None else days * DAY)
            days_arg = hours / 24 if hours is not None else days
    except ValueError:
        raise HTTPException(400, "since and until must be an ISO date or date-time in UTC, for example 2026-11-05 or 2026-11-06T03:00")
    rows = _rows()
    out = mom().attention_report(days_arg, rows=rows, now=now, since=since, until=until)
    rows = [r for r in rows if r.get("ts", 0) >= start and (end is None or r.get("ts", 0) < end)]
    out["today"] = {"mom_calls": mom().report.build(rows, since=now - now % DAY)["mom"]["calls"]}
    out["you"]["waiting"] = len(_needs_you())
    return {**out, "recent": _recent(rows), "version": _version()}


class Answer(BaseModel):
    board: str
    task_id: str
    text: str | None = None
    accept: bool = False
    allow_once: bool = False


class Move(BaseModel):
    board: str
    task_id: str
    to: str


# Where a card of each status may go, and the verb that gets it there: each one is a real
# ``kanban_db`` transition, so a move never bypasses the dependency gate (the ready re-check,
# the review gate, the live-claim fence). ``to`` outside this map is a 409, and the current
# status must be a source the transition fires from (the verb's own guard re-checks).
_MOVE_SOURCES = {
    "ready": {"todo", "blocked"},
    "blocked": {"running", "ready"},
    "review": {"running", "ready"},
    "done": {"running", "ready", "blocked", "review"},
    "unblocked": {"blocked"},
    "archived": {"triage", "todo", "ready", "running", "blocked", "review", "done"},
}


def _move_verbs(conn, task_id):
    kb = mom().kb
    return {
        "ready": lambda: kb.promote_task(conn, task_id, actor="calling-mom")[0],
        "blocked": lambda: kb.block_task(conn, task_id, reason="moved from the Calling Mom page", kind="needs_input"),
        "review": lambda: kb.request_review(conn, task_id, force=True),
        "done": lambda: kb.complete_task(conn, task_id, result="completed from the Calling Mom page", force=True),
        "unblocked": lambda: kb.unblock_task(conn, task_id),
        "archived": lambda: kb.archive_task(conn, task_id),
    }


@router.post("/move")
def move(body: Move):
    """A card click in the context menu: one bucket move, via the status's transition verb."""
    if body.board not in mom()._core.list_board_slugs():
        raise HTTPException(404, f"no board {body.board!r}")
    with mom()._connect(body.board) as conn:
        task = mom().kb.get_task(conn, body.task_id)
        if task is None:
            raise HTTPException(404, f"no card {body.task_id!r}")
        verb = _move_verbs(conn, body.task_id)
        if task.status not in _MOVE_SOURCES.get(body.to, ()):
            raise HTTPException(409, f"cannot move {task.status!r} to {body.to!r}")
        if not verb[body.to]():
            raise HTTPException(409, f"cannot move {task.status!r} to {body.to!r}")
        return {"ok": True, "status": mom().kb.get_task(conn, body.task_id).status}


@router.post("/answer")
def answer(body: Answer):
    """A person's click: ``answer_card`` with ``by="desktop"``. The board must exist, so a name is never turned into a path."""
    if body.board not in mom()._core.list_board_slugs():
        raise HTTPException(404, f"no board {body.board!r}")
    out = mom().answer_card(body.board, body.task_id, body.text, by="desktop", accept=body.accept, allow_once=body.allow_once)
    if not out["ok"]:
        raise HTTPException(out["status"], out["error"])
    return out
