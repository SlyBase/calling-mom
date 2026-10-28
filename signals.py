"""Measurable "the worker is stuck" signals. Pure logic: no Hermes imports.

Four escalation signals + one monitoring signal, all from tool results (the model never judges itself):

* same_error  - the same normalized failure N times in a row (red TDD tests with
                changing messages do not count; infrastructure errors never count)
* loop        - the identical tool call N times within the last 10 calls
* malformed   - N invalid tool calls in a row (typical for weak models)
* steps       - N tool calls without a kanban_heartbeat
* flux_stall  - Flux chain stuck on old SHA (monitoring only, no escalation)
"""

import hashlib
import json
import re
from collections import deque

_PATH = re.compile(r"(?<![\w.])/[\w./-]+")
_HEX = re.compile(r"\b(?:0x)?[0-9a-f]{6,}\b", re.I)
_DIGITS = re.compile(r"\d+")
_SPACE = re.compile(r"\s+")

# Errors a stronger model cannot fix: network, provider, rate limits.
_INFRA = re.compile(
    r"connection (?:refused|reset|aborted)|timed? ?out|temporary failure|name resolution|"
    r"econn\w*|network is unreachable|no route to host|service unavailable|overloaded|"
    r"rate.?limit|too many requests|\b429\b|\b(?:http|status|error)[ :]*5\d\d\b|"
    # board / worker plumbing (history: 6 of 7 breaker parks on the coding board)
    r"owner refused|execution unknown|retained by owner|skills?_unavailable|stale_kanban_claim|"
    r"memory pressure|credential or model|provider rejected",
    re.I,
)
_MALFORMED = re.compile(
    r"invalid_arg|invalid arguments?|unknown[ _]tool|missing required|malformed|schema validation|json(?:decode)? error",
    re.I,
)
# Flux stall: the chain is stuck on an old SHA (self-heals on the 10-minute interval)
_FLUX_STALL = re.compile(
    r"dependency .* is not ready|Applied revision: refs/heads/main@sha1:[0-9a-f]{40}",
    re.I,
)
_LOOP_WINDOW = 10
_EDIT_TOOLS = {"write_file", "patch"}  # state changed: an identical later call may now give a different answer


def signature(text: str) -> str:
    """Stable fingerprint of an error: paths, hex and numbers are normalized away."""
    s = str(text or "")[-400:]
    s = _PATH.sub("<p>", s)
    s = _HEX.sub("<h>", s)
    s = _DIGITS.sub("N", s)
    s = _SPACE.sub(" ", s).strip().lower()
    return hashlib.sha1(s.encode()).hexdigest()[:10]


def is_infra(text: str) -> bool:
    return bool(_INFRA.search(str(text or "")))


class Tracker:
    """State of one worker run. ``feed`` returns the event plus the signals that fired."""

    def __init__(self, same_error=3, loop=3, malformed=3, steps=40):
        self.limits = {"same_error": same_error, "loop": loop, "malformed": malformed, "steps": steps}
        self.last_sig, self.streak = None, 0
        self.window = deque(maxlen=_LOOP_WINDOW)
        self.bad_calls = 0
        self.steps = 0

    def feed(self, tool, args, ok, text="", error_type=None) -> dict:
        fired = []
        event = {"tool": tool, "ok": bool(ok), "sig": None, "infra": False, "error_type": error_type}
        if tool.startswith("kanban_"):
            if tool == "kanban_heartbeat":
                self.steps = 0
            event["signals"] = fired
            return event

        self.steps += 1
        if tool in _EDIT_TOOLS:
            self.window.clear()
        if self.steps >= self.limits["steps"]:
            fired.append("steps")
            self.steps = 0
        # Flux stall: monitoring signal (no escalation)
        if _FLUX_STALL.search(text):
            fired.append("flux_stall")

        if ok:
            self.last_sig, self.streak, self.bad_calls = None, 0, 0
        elif is_infra(text):
            event["infra"] = True
        else:
            sig = signature(text)
            event["sig"] = sig
            self.streak = self.streak + 1 if sig == self.last_sig else 1
            self.last_sig = sig
            if self.streak >= self.limits["same_error"]:
                fired.append("same_error")
                self.last_sig, self.streak = None, 0
            if _MALFORMED.search(f"{error_type or ''} {str(text)[:200]}"):
                self.bad_calls += 1
                if self.bad_calls >= self.limits["malformed"]:
                    fired.append("malformed")
                    self.bad_calls = 0

        key = hashlib.sha1(f"{tool}{json.dumps(args, sort_keys=True, default=str)}".encode()).hexdigest()
        self.window.append(key)
        if self.window.count(key) >= self.limits["loop"]:
            fired.append("loop")
            self.window.clear()

        event["signals"] = fired
        return event
