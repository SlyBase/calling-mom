"""Outside help: who else touched a board's cards. Pure functions over what ``_core.board_activity`` reads. Stdlib only.

A touch row keeps ids and counts, never text: the author id of a comment (a profile or surface such as ``default``,
``desktop``), the kind of a board event, the store id (for dedupe) and the time. The Kanban store records no actor
for its events, so those count as ``unattributed``; the plugin's own writes are taken out by the caller (``own_write``).
"""
import re

# comments the plugin writes itself (SPEC-1.5 section 3): recognised by prefix, whoever signed them
OWN_PREFIXES = ("[advisor unavailable]", "[pre-flight] HOLD:", "[mom answered]", "[olymp answered]", "[for you]",
                "[you via calling-mom]", "[quota]", "[stalled]")
OWN_AUTHORS = {"advisor", "escalation", "you"}
_ADVICE = re.compile(r"\[[a-z_]+/[a-z_]+\] run ")  # `[<category>/<signal>] run N · ...`
EVENT_WHAT = {"linked": "link", "unlinked": "unlink", "unblocked": "unblock", "edited": "edit", "archived": "archive",
              "assigned": "assign", "model_override_set": "model", "reasoning_effort_set": "effort",
              "changes_requested": "changes"}
_LEAVES = {"promoted", "unblocked", "claimed", "completed", "archived"}  # events that end a wait in triage or blocked


def comment_touch(task, comment) -> "dict | None":
    """The touch row of a comment by someone who is neither the card's assignee nor calling-mom, else None."""
    author, body = (comment.author or "").strip(), (comment.body or "").lstrip()
    if not author or author == task.assignee or author in OWN_AUTHORS or body.startswith(OWN_PREFIXES) or _ADVICE.match(body):
        return None
    return {"ts": comment.created_at, "task": task.id, "src": "comment", "what": "comment", "author": author,
            "actor": "other", "store_id": comment.id}


def event_touch(task, event) -> "dict | None":
    """The touch row of a structural board event (no actor is stored, so ``unattributed``), else None.
    A reclaim counts only when a person asked for it (``manual``), not the dispatcher's own ttl release."""
    what = "reclaim" if event.kind == "reclaimed" and (event.payload or {}).get("manual") else EVENT_WHAT.get(event.kind)
    if not what:
        return None
    return {"ts": event.created_at, "task": task.id, "src": "event", "what": what, "actor": "unattributed", "store_id": event.id}


def triage_entries(events) -> list:
    """``[(event id, time)]`` of every entry into triage: a status event into it, or the block-loop breaker parking the card."""
    return [(e.id, e.created_at) for e in events
            if e.kind == "block_loop_detected" or (e.kind == "status" and (e.payload or {}).get("status") == "triage")]


def wait_s(task, events, since_id, since_ts, waiting_status) -> "int | None":
    """Seconds from ``since_ts`` until the card left ``waiting_status`` (triage, blocked); None = still waiting.
    A leaving event after the entry ends the wait; a card that left without one ends it at its last event."""
    later = [e for e in events if e.id > since_id]
    leave = next((e for e in later if e.kind in _LEAVES or (e.kind == "status" and (e.payload or {}).get("status") != waiting_status)), None)
    if leave:
        return max(0, leave.created_at - since_ts)
    if task.status == waiting_status:
        return None
    return max(0, max([e.created_at for e in later] + [since_ts]) - since_ts)


def question_block(events, ts) -> "tuple | None":
    """``(event id, time)`` of the ``needs_input`` block a question row of time ``ts`` answers: the latest one before it."""
    blocks = [e for e in events if e.kind == "blocked" and (e.payload or {}).get("kind") == "needs_input" and e.created_at <= ts]
    return (blocks[-1].id, blocks[-1].created_at) if blocks else None


def reached_person(question_row) -> bool:
    """A ``question`` row Mom did not answer reached a person. Hermes turning a dependency into ``needs_input`` (``rekind``) asked nobody."""
    return question_row.get("outcome") != "answered" and question_row.get("reason") != "rekind"
