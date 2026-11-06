"""Quota, stalled cards and pacing (WP2 of 1.5): pure functions over plain data. Stdlib only.

Nothing here reads a board or a file. ``classify`` and ``parse_reset`` look at one error and keep only what they parse
(a class and a time), never the text. Provider health is a plain dict that the plugin keeps in its state file::

    {profile: {provider: {"state": "limited" | "down", "until": ts, "class": ..., "known": bool}}}

``known`` is False when the reset time is a guess (``LIMIT_UNKNOWN_S`` from now), so a guess never extends a real one.
"""
import datetime
import re

LIMIT_UNKNOWN_S = 1800  # a rate limit without a reset time: skip the provider for this long
DOWN_S = 1800           # an auth or key-limit error: skip the provider for this long
_READ = 4000            # the longest part of an error message that is looked at
_RESET = re.compile(r"\bresets\s+(\d{1,2})(?::(\d{2}))?\s*([ap]m)\s*\(UTC\)", re.I)
_HIT = re.compile(r"hit your [a-z0-9 -]{0,40}limit")
_KEY_LIMIT = ("key limit exceeded", "credit balance", "insufficient credit")


def classify(error_type=None, message=None, status=None, reason=None) -> str:
    """``rate_limit``, ``auth``, ``key_limit`` or ``other`` for one provider error (Hermes' ``reason`` helps, the text decides the rest)."""
    text = str(message or "")[:_READ].lower()
    if status == 402 or reason == "billing" or any(k in text for k in _KEY_LIMIT):
        return "key_limit"
    if "loggedout" in str(error_type or "").lower() or status == 401 or reason in ("auth", "auth_permanent") or "invalid api key" in text:
        return "auth"
    if reason == "upstream_rate_limit":  # an aggregator's upstream said 429: the key itself is healthy
        return "other"
    if status == 429 or reason == "rate_limit" or _HIT.search(text) or "usage limit" in text:
        return "rate_limit"
    return "other"


def parse_reset(message, now) -> "int | None":
    """Epoch seconds of ``resets 1:50pm (UTC)`` (the next time after ``now``). Any other zone or shape is None."""
    match = _RESET.search(str(message or "")[:_READ])
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2) or 0)
    if not (1 <= hour <= 12 and minute <= 59):
        return None
    at = now - now % 86400 + ((hour % 12) + (12 if match.group(3).lower() == "pm" else 0)) * 3600 + minute * 60
    return at if at > now else at + 86400


def clock(ts) -> str:
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%H:%M")


# ---------------------------------------------------------------- provider health

def mark(health, profile, provider, state, until, error_class, known=True) -> bool:
    """Record that ``provider`` is ``limited`` or ``down`` for ``profile`` until ``until``. True when that is news:
    a new state, a later known time (more than a minute off), or no entry yet. A guessed time never extends an entry."""
    entry = health.setdefault(profile, {}).get(provider)
    new = {"state": state, "until": int(until), "class": error_class, "known": bool(known)}
    if entry is None or entry["state"] != state:
        health[profile][provider] = new
        return True
    if not known or abs(entry["until"] - new["until"]) <= 60:
        return False
    health[profile][provider] = new
    return True


def active(health, profile, provider, now) -> "dict | None":
    entry = (health.get(profile) or {}).get(provider)
    return entry if entry and entry["until"] > now else None


def expire(health, now) -> list:
    """Drop the entries that have ended; ``[(profile, provider, state)]`` of them (each is a ``recovered`` row)."""
    gone = [(p, prov, e["state"]) for p, entries in health.items() for prov, e in entries.items() if e["until"] <= now]
    for profile, provider, _ in gone:
        del health[profile][provider]
        if not health[profile]:
            del health[profile]
    return gone


def entries(health, profile, now) -> list:
    """``[(provider, entry)]`` that are active for ``profile`` (None = every profile; the entry then names its profile), longest wait first."""
    rows = [(prov, dict(e, profile=p)) for p, by in health.items() if profile in (None, p) for prov, e in by.items() if e["until"] > now]
    return sorted(rows, key=lambda row: -row[1]["until"])


# ---------------------------------------------------------------- stalled cards

def last_beat(task) -> int:
    """The last sign of life of a running card: its heartbeat, else its start, else its creation."""
    return task.last_heartbeat_at or task.started_at or task.created_at or 0


def minutes_silent(task, now) -> int:
    return max(0, int(now - last_beat(task)) // 60)


def stalled(tasks, now, minutes) -> list:
    """The running cards without a heartbeat for ``minutes`` (0 = off)."""
    return [t for t in tasks if minutes > 0 and t.status == "running" and now - last_beat(t) >= minutes * 60]


# ---------------------------------------------------------------- pace

def _reaches(links, start, goal) -> bool:
    seen, stack = set(), [start]
    while stack:
        node = stack.pop()
        if node == goal:
            return True
        if node not in seen:
            seen.add(node)
            stack.extend(child for parent, child in links if parent == node)
    return False


def plan(tasks, links, n) -> dict:
    """At most ``n`` cards ready or running: chain the ``todo``/``ready`` cards behind ``n`` heads with parent links.

    Running cards head the first chains (more than ``n`` of them are left alone: ``skipped_running``). The waiting cards, by
    priority then age, fill the empty chains first and then go round-robin. A card whose link would close a cycle goes to the
    next chain, or into ``skipped``. Links that exist already are kept and not added again.
    """
    if n < 1:
        raise ValueError("max must be at least 1")
    known = set(links)
    running = sorted((t for t in tasks if t.status == "running"), key=lambda t: (t.created_at or 0, t.id))
    waiting = sorted((t for t in tasks if t.status in ("todo", "ready")), key=lambda t: (-(t.priority or 0), t.created_at or 0, t.id))
    heads = running[:n]
    chains = [[t.id] for t in heads] + [[] for _ in range(n - len(heads))]
    skipped = []
    for i, task in enumerate(waiting):
        for k in range(n):
            chain = chains[(len(heads) % n + i + k) % n]
            if not chain:
                chain.append(task.id)
                break
            if (chain[-1], task.id) in known or not _reaches(known, task.id, chain[-1]):
                known.add((chain[-1], task.id))
                chain.append(task.id)
                break
        else:
            skipped.append(task.id)
    add = [(a, b) for chain in chains for a, b in zip(chain, chain[1:]) if (a, b) not in links]
    return {"chains": chains, "add": add, "skipped": skipped, "skipped_running": len(running) - len(heads)}
