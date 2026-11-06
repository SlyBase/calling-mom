"""Evidence that advice was followed. Pure functions, stdlib only.

Mom's advice names things in backticks: a command (`git status`), a path (`src/app.py`), a tool (`read_file`). Those are
its anchors. A later tool call of the same run that uses an anchor is evidence the advice was followed; any other call is
only evidence that the worker did something. The anchors live in memory for the run; the log keeps their count and a hash.
"""
import hashlib
import re
from typing import NamedTuple

MAX_ANCHORS = 8
_TICKS = re.compile(r"`([^`\n]{2,200})`")
_TOOL = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+")
_FILE = re.compile(r"[\w.-]+\.\w{1,8}")
_ARG = 300  # characters of one argument that can match: a file's content is not a call to that file


class Anchor(NamedTuple):
    kind: str  # command | path | tool
    text: str
    hash: str


def _kind(text) -> str:
    if re.search(r"\s", text):
        return "command"
    if _TOOL.fullmatch(text):
        return "tool"
    return "path" if "/" in text or text.startswith(("~", ".")) or _FILE.fullmatch(text) else "command"


def anchors(advice) -> list:
    """The anchors of an advice text: backticked spans, each once, at most ``MAX_ANCHORS``."""
    found = []
    for span in _TICKS.findall(advice or ""):
        span = " ".join(span.split())
        if len(span) > 1 and span not in [a.text for a in found]:
            found.append(Anchor(_kind(span), span, hashlib.sha256(span.encode()).hexdigest()[:12]))
    return found[:MAX_ANCHORS]


def _call_text(tool, args) -> str:
    if tool in ("terminal", "execute_code"):
        return " ".join(str((args or {}).get("command") or (args or {}).get("code") or "").split())
    return " ".join(" ".join(str(v)[:_ARG].split()) for v in (args or {}).values())


def matches(anchor, tool, args) -> bool:
    """Does this tool call use the anchor? A command anchor must start the normalised command, a path anchor must be in
    the call, a tool anchor must be the tool."""
    text = _call_text(tool, args)
    if anchor.kind == "tool":
        return tool == anchor.text
    return text.startswith(anchor.text) if anchor.kind == "command" else anchor.text in text
