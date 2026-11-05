"""Precedents: a person's answers to Mom's ``[for you]`` briefs, kept as text so Mom can reuse them. Stdlib only.

One JSON object per line in ``logs/calling-mom-precedents.jsonl`` next to the log: ``{id, board, question, answer, task,
ts}``. This is the one place the plugin keeps prose, and the file never leaves the machine except inside Mom's prompt
(which goes through Hermes' egress redaction). The text is redacted by the caller before it is stored.
"""
import hashlib
import json
import re

MAX_CHARS = 500  # per text, enforced by the caller that stores
MAX_IN_PROMPT = 3
_WORD = re.compile(r"[a-z0-9]{4,}")  # four letters and more: "the", "use", "for" say nothing about a topic


def path_for(log_path):
    return log_path.with_name("calling-mom-precedents.jsonl")


def make_id(board, task, brief_id) -> str:
    """One id per brief, so the same answer reached by two paths is stored once."""
    return hashlib.sha1(f"{board}\0{task}\0{brief_id}".encode()).hexdigest()[:8]


def read(path) -> list:
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("id"):
            out.append(row)
    return out


def _write(path, rows) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def add(path, entry) -> bool:
    """Append ``entry``; False when its id is already stored."""
    if any(e["id"] == entry["id"] for e in read(path)):
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps(entry) + "\n")
    return True


def forget(path, pid) -> bool:
    rows = read(path)
    keep = [e for e in rows if e["id"] != pid]
    if len(keep) == len(rows):
        return False
    _write(path, keep)
    return True


def clear(path) -> int:
    n = len(read(path))
    path.unlink(missing_ok=True)
    return n


def _words(text) -> set:
    return set(_WORD.findall(str(text).lower()))


def rank(entries, query, board, limit=MAX_IN_PROMPT) -> list:
    """The ``limit`` entries whose question shares most words with ``query``; at least one is required.
    On equal overlap the same board wins, then the newer entry."""
    want = _words(query)
    scored = [(len(want & _words(e.get("question", ""))), e) for e in entries]
    scored = [(n, e) for n, e in scored if n]
    scored.sort(key=lambda s: (-s[0], s[1].get("board") != board, -s[1].get("ts", 0)))
    return [e for _, e in scored[:limit]]


def prompt_text(entries) -> str:
    return "\n".join(f"- (card {e['task']}) Q: {e['question']} A: {e['answer']}" for e in entries)
