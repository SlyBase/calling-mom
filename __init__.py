"""calling-mom — hand a card to a stronger model when the local one can't.

Two deterministic rules, no self-assessment by the local model:

1. **Give-up.** A worker blocks its card with ``capability`` (or an untyped
   block) → the card is pinned to the strong model and returned to ``ready``,
   so the next dispatch solves it with more intelligence. Exactly once: after
   that, the strong model's own block parks the card for a human.
2. **Critical marker.** A card whose title or body contains ``[crit]`` is
   pinned to the strong model at claim time (a second, stronger opinion on
   work that must be right the first time).

Blocks that need a human (``needs_input``, ``dependency``) are never touched.

Config (config.yaml, all optional)::

    calling_mom:
      model: deepseek/deepseek-v4.1-flash-1017
      provider: openrouter
      marker: "[crit]"

Kill switch: ``CALLING_MOM=off`` in the environment.
"""

import contextlib
import importlib.util
import json
import logging
import os
import re
import shlex
import subprocess
import time
from collections import deque
from pathlib import Path

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_boards as kbb

logger = logging.getLogger(__name__)

try:
    from . import signals
except ImportError:  # loaded by path (tests, plugin doctor): no package context
    _spec = importlib.util.spec_from_file_location("calling_mom_signals", Path(__file__).with_name("signals.py"))
    signals = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(signals)

try:
    from . import rules
except ImportError:  # loaded by path (tests, plugin doctor)
    _rules_spec = importlib.util.spec_from_file_location("calling_mom_rules", Path(__file__).with_name("rules.py"))
    rules = importlib.util.module_from_spec(_rules_spec)
    _rules_spec.loader.exec_module(rules)

try:
    from . import cli
except ImportError:  # loaded by path (tests, plugin doctor)
    _cli_spec = importlib.util.spec_from_file_location("calling_mom_cli", Path(__file__).with_name("cli.py"))
    cli = importlib.util.module_from_spec(_cli_spec)
    _cli_spec.loader.exec_module(cli)

DEFAULTS = {
    "model": "deepseek/deepseek-v4.1-flash-1017",
    "provider": "openrouter",
    "marker": "[crit]",
    "observe": True,  # log stuck signals (hashes only) to logs/calling-mom.jsonl
    "limits": {"same_error": 3, "loop": 3, "malformed": 3, "steps": 40},
    "verify": {  # card line `verify: <cmd>` gates kanban_complete. Card text is untrusted input:
        "timeout_s": 120, "tail_chars": 1500, "max_fix": 3,
        "trusted_creators": ["user"],  # only cards created by these are honoured
        "allow": ["python3", "python", "pytest", "test", "true", "false", "make", "ruff", "uv", "node", "npm", "go", "cargo"],
    },
    "ladder": {"effort_first": False, "effort": "high"},  # free rung: same model, more thinking, before another model
    "review": {"marker": "[review]", "reviewer": "reviewer"},  # [review] cards finish via the reviewer profile ("" = off)
    "metrics": {"pushgateways": [], "instance": "hermes"},  # Pushgateway base URLs, tried in order ([] = off)
    "local_only_marker": "[local-only]",  # such cards never send anything to a remote model
    "advisor": {  # one short question to the strong model at the point where the worker is stuck
        "enabled": False,  # master switch; even when on, nothing is sent unless a board or card opts in:
        "boards": [],  # board slugs that opt in ("*" = all); a card with `marker` in title/body opts in itself
        "marker": "[advisor]",
        "max_consults_per_card": 3, "max_output_tokens": 700,
        "context_chars": 6000, "daily_usd_cap": 2.0, "daily_token_cap": 200000, "timeout_s": 40,
    },
    # "olymp" tier: a very strong (expensive) model, asked only for the listed categories. Empty model = off:
    # every category then uses the "mom" (top-level model/provider). A per-category entry below still wins.
    # The mom answers first and says ESCALATE only for a rare high-stakes case; then (and only then) olymp is asked.
    # Olymp runs on a subscription: no USD/token cap, but a hard call limit per card and per day.
    "olymp": {"model": "", "provider": "", "categories": ["architecture"], "max_per_card": 1, "max_per_day": 6},
    # per category {model, provider}; missing keys fall back to the top-level model/provider.
    # categories: error (same_error, malformed) | loop (loop) | validation (failed verify:) | architecture
    "categories": {
        "error": {}, "loop": {}, "validation": {},
        # completion: once per run, card vs. completion summary before kanban_complete
        "completion": {"max": 1},
        # watch: privileged access to another machine (ssh, pct, ...): ONE look per card, own budget, mom only
        "watch": {"max": 1},
        # architecture: review of critical changes (+ the second_opinion tool). The lists live in rules.py;
        # `commands_extra` adds your own regexes without replacing them.
        "architecture": {
            "max": 4, "patterns": rules.CRITICAL_FILES, "commands": rules.CRITICAL_COMMANDS,
            "commands_extra": [], "watch": rules.WATCH_COMMANDS,
        },
    },
}

# Block kinds worth a stronger model. ``None`` = untyped block (legacy, or the
# failure breaker parking the card after repeated crashes).
ESCALATABLE_KINDS = {None, "capability"}
AUTHOR = "escalation"
_trackers: dict = {}  # (task, run) -> signals.Tracker, one per worker run
_verify_fails: dict = {}  # (task, run) -> failed verifications so far
_history: dict = {}  # (task, run) -> last tool steps (advisor context)
_watch_used: dict = {}  # (task, run) -> `watch` reviews so far (a NO_ACTION answer writes no comment to count)
_arch_seen: set = set()  # (task, run, path-or-command) already reviewed
_redirected: set = set()  # (task, run) whose completion was already handed to the reviewer
_completion_checked: set = set()  # (task, run) whose completion summary was already checked
_awaiting_reply: set = set()  # (task, run) that got advice; the next worker reply says if it helped
_REFUTED = re.compile(
    r"disprov|refut|ruled out|widerleg|not the (cause|issue|problem)|already (resolved|fixed|verified|done)|"
    r"does(n't| not) apply|is (wrong|incorrect)", re.I)
_HIGH_EFFORT = {"high", "xhigh", "max", "ultra"}
_ctx = None  # plugin context, gives access to ctx.llm
ADVISOR = "advisor"  # author of comments written by versions before 1.3.1; new ones carry the worker's profile
_UNAVAILABLE = "[advisor unavailable]"
# an advisor comment is recognised by its text, whoever signed it
_IS_ADVICE = "author = 'advisor' OR body LIKE '[advisor unavailable]%' OR body LIKE '[%/%] run %'"
COOLDOWN_S = 300  # a model that just failed is not asked again for this long (each dead call delays the worker)
_down: dict = {}  # (provider, model) -> time of its last failure


class _CoolingDown(Exception):
    """The model failed a moment ago: skip silently, the first failure already left its comment."""
# `steps` is a pace signal (logged, never a question): it cost one advisor call and returned NO_ACTION
_CATEGORY = {"same_error": "error", "malformed": "error", "loop": "loop"}


def _merge(default, override):
    """Overlay ``override`` on ``default``; a value of the wrong type keeps the default."""
    if isinstance(default, dict):
        if not isinstance(override, dict):
            return dict(default)
        return {k: _merge(v, override[k]) if k in override else v for k, v in default.items()} | {
            k: v for k, v in override.items() if k not in default
        }
    if isinstance(default, bool):
        return override if isinstance(override, bool) else default
    if isinstance(default, float):
        return float(override) if isinstance(override, (int, float)) and not isinstance(override, bool) else default
    if isinstance(default, int):
        return override if isinstance(override, int) and not isinstance(override, bool) else default
    if isinstance(default, str):
        return override if isinstance(override, str) and override.strip() else default
    if isinstance(default, list):
        return list(override) if isinstance(override, list) else list(default)
    return default if override is None else override


def settings() -> dict:
    """DEFAULTS overlaid with the profile's ``calling_mom`` block (type-guarded)."""
    try:
        from hermes_cli.config import load_config

        raw = (load_config() or {}).get("calling_mom") or {}
    except Exception as exc:  # pragma: no cover - config is best effort
        logger.debug("calling-mom: config read failed: %s", exc)
        raw = {}
    cfg = _merge(DEFAULTS, raw)
    mom = raw.get("mom") if isinstance(raw.get("mom"), dict) else {}
    if mom.get("model") and mom.get("provider"):  # the mom block names the cheap tier; a half pair is ignored
        cfg["model"], cfg["provider"] = str(mom["model"]), str(mom["provider"])
    return cfg


def _enabled() -> bool:
    return os.environ.get("CALLING_MOM", "on").strip().lower() not in {"off", "0", "false"}


class _connect:
    """Board-pinned DB connection: enter the board scope, then open the DB."""

    def __init__(self, board=None):
        from hermes_cli import kanban_db as kb

        self._stack = contextlib.ExitStack()
        if board:
            self._stack.enter_context(kb.scoped_current_board(board))

    def __enter__(self):
        from hermes_cli import kanban_db_connect as kbc

        return self._stack.enter_context(kbc.connect_closing())

    def __exit__(self, *exc):
        return self._stack.__exit__(*exc)


def _pin_strong(conn, task, why: str, reason: str = "other") -> None:
    """Pin the card to the strong model. ``reason`` feeds the escalation metric:
    ``crit`` (marker at claim), ``blocked`` (worker gave up), ``breaker`` (crash park)."""
    from hermes_cli import kanban_db as kb

    cfg = settings()
    kb.set_model_override(conn, task.id, cfg["model"], provider=cfg["provider"])
    kb.add_comment(conn, task.id, AUTHOR, f"Escalated to {cfg['model']} — {why}.")
    _trace_row("escalated", task=task.id, reason=reason)
    logger.info("calling-mom: %s -> %s (%s)", task.id, cfg["model"], why)


def on_claimed(task_id=None, board=None, **_):
    """Rule 2: pin ``[crit]`` cards to the strong model before the spawn."""
    if not _enabled() or not task_id:
        return
    from hermes_cli import kanban_db as kb

    try:
        with _connect(board) as conn:
            task = kb.get_task(conn, task_id)
            if task is None or task.model_override or _is_local_only(task):
                return
            marker = settings()["marker"].lower()
            text = f"{task.title or ''}\n{task.body or ''}".lower()
            if marker and marker in text:
                _pin_strong(conn, task, "critical card", reason="crit")
    except Exception as exc:  # pragma: no cover - never break dispatch
        logger.warning("calling-mom: claim hook failed for %s: %s", task_id, exc)


def _profile_runs_strong() -> bool:
    """True when this process's own profile already uses the strong model (e.g. the reviewer profile)."""
    try:
        from hermes_cli.config import load_config

        model = (load_config() or {}).get("model") or {}
        current = model.get("default") if isinstance(model, dict) else model
        return current == settings()["model"]
    except Exception:
        return False


def on_blocked(task_id=None, board=None, reason=None, **_):
    """The weak model gave up. Ladder: (optional) more thinking -> strong model -> human."""
    if not _enabled() or not task_id or _profile_runs_strong():
        return
    from hermes_cli import kanban_db as kb

    try:
        with _connect(board) as conn:
            task = kb.get_task(conn, task_id)
            if task is None or task.status != "blocked" or _is_local_only(task):
                return
            if task.block_kind not in ESCALATABLE_KINDS:
                return
            if task.model_override == settings()["model"]:
                return  # the strong model already had its attempt
            why = f"blocked ({reason.strip()[:200]})" if reason and reason.strip() else "blocked"
            ladder = settings()["ladder"]
            if ladder["effort_first"] and (task.reasoning_effort or "") not in _HIGH_EFFORT:
                kb.set_reasoning_effort(conn, task_id, ladder["effort"])
                kb.add_comment(conn, task_id, AUTHOR, f"Retry with reasoning effort {ladder['effort']} on the same model — {why}.")
                _trace_row("ladder", task=task_id, rung="effort")
                # ponytail: the board would route the 2nd same-kind block to triage and skip the strong rung.
                # The ladder bounds the loop itself (effort high, then model pinned), so reset this card's counter.
                with kb.write_txn(conn):
                    conn.execute("UPDATE tasks SET block_recurrences = 0 WHERE id = ?", (task_id,))
            else:
                _pin_strong(conn, task, why, reason="blocked")
            kb.unblock_task(conn, task_id)
    except Exception as exc:  # pragma: no cover - never break the board
        logger.warning("calling-mom: block hook failed for %s: %s", task_id, exc)


_last_trace: dict = {}


_LOG_MAX_BYTES = 5_000_000  # ponytail: tail-trim at 5 MB; rotate properly if a month ever exceeds it


def _trace_row(kind, **fields):
    """Append one JSON row to the observe log (best effort, no content)."""
    try:
        path = _log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if path.stat().st_size > _LOG_MAX_BYTES:
                tail = path.read_text().splitlines()[-1000:]
                path.write_text("\n".join(tail) + "\n")
        except OSError:
            pass
        with path.open("a") as fh:
            fh.write(json.dumps({"ts": int(time.time()), "kind": kind, "pid": os.getpid(), **fields}) + "\n")
    except Exception:  # pragma: no cover
        pass


def _trace(kind, **fields):
    """Rate-limited (1/min per kind) liveness row, so a silent hook is distinguishable from a dead one."""
    now = time.time()
    if now - _last_trace.get(kind, 0) < 60 or not settings()["observe"]:
        return
    _last_trace[kind] = now
    _trace_row(kind, **fields)


def _state_path() -> Path:
    override = os.environ.get("CALLING_MOM_STATE")
    return Path(override) if override else _log_path().with_name("calling-mom-state.json")


def _load_marks() -> dict:
    try:
        return json.loads(_state_path().read_text())
    except (OSError, ValueError):
        return {}


def sweep(board=None) -> list:
    """Escalate cards the crash circuit-breaker parked (``gave_up``); the breaker fires no block hook.

    The watermark (last seen ``task_events.id`` per board) is persisted, so a restart
    neither re-escalates nor drops events. The first sweep of a board skips its history.
    Returns the escalated task ids.
    """
    if not _enabled():
        return []
    from hermes_cli import kanban_db as kb

    done = []
    try:
        with _connect(board) as conn:
            last = conn.execute("SELECT COALESCE(MAX(id), 0) FROM task_events").fetchone()[0]
            marks = _load_marks()
            key = board or ""
            seen = marks.get(key, last)
            rows = conn.execute(
                "SELECT task_id, payload FROM task_events WHERE kind = 'gave_up' AND id > ? AND id <= ?",
                (seen, last),
            ).fetchall()
            strong = settings()["model"]
            for row in rows:
                payload = json.loads(row["payload"] or "{}")
                task = kb.get_task(conn, row["task_id"])
                if (
                    payload.get("sticky") or payload.get("terminal_provider")
                    or task is None or task.status != "blocked" or _is_local_only(task)
                    or task.block_kind not in ESCALATABLE_KINDS
                    or task.model_override == strong
                    or signals.is_infra(payload.get("error") or task.last_failure_error)
                ):
                    continue
                _pin_strong(conn, task, f"circuit breaker ({str(payload.get('error') or '')[:150]})", reason="breaker")
                kb.unblock_task(conn, task.id)
                done.append(task.id)
            marks[key] = last
            _state_path().parent.mkdir(parents=True, exist_ok=True)
            _state_path().write_text(json.dumps(marks))
    except Exception as exc:  # pragma: no cover - never break the caller
        logger.warning("calling-mom: sweep failed on board %s: %s", board, exc)
    return done


def sweep_all() -> list:
    """Sweep every live board (entry point of the cron script)."""
    from hermes_cli import kanban_db_boards as kbb

    escalated = []
    for meta in kbb.list_boards(include_archived=False):
        escalated += sweep(meta["slug"])
    return escalated


def _resolve_task(task_id, board=None):
    """Resolve one task row on ``board`` (``None`` = the active board)."""
    with _connect(board) as conn:
        task = kb.get_task(conn, task_id)
    return task, board


def _escalate_parked_task(task, payload, board) -> bool:
    """Pin a breaker-parked card to the strong model once; returns True when escalated."""
    if (
        payload.get("sticky")
        or payload.get("terminal_provider")
        or task is None
        or task.status != "blocked"
        or _is_local_only(task)
        or task.block_kind not in ESCALATABLE_KINDS
        or task.model_override == settings()["model"]
        or signals.is_infra(payload.get("error") or task.last_failure_error)
    ):
        return False
    with _connect(board) as conn:
        fresh = kb.get_task(conn, task.id)
        if fresh is None or fresh.status != "blocked":
            return False
        _pin_strong(conn, fresh, "circuit breaker (%s)" % (str(payload.get("error") or "")[:150]), reason="breaker")
        kb.unblock_task(conn, fresh.id)
    return True


def on_parked(task_id=None, board=None, payload=None, **_):
    """Escalate a crash-breaker parked card reactively; never break the caller."""
    if not _enabled() or not task_id:
        return
    try:
        task, board = _resolve_task(task_id, board)
        if task is None:
            return
        _escalate_parked_task(task, payload or {}, board)
    except Exception as exc:  # pragma: no cover - hook must stay fail-open
        logger.warning("calling-mom: parked hook failed for %s: %s", task_id, exc)


def on_worker_exited(task_id=None, board=None, outcome=None, **_):
    """``on_kanban_worker_exited``: the dispatcher fires this after breaker accounting has
    committed, so a crash-parked card can be escalated without polling."""
    if outcome == "completed":
        return
    on_parked(task_id=task_id, board=board, payload={"error": None})


def on_dispatch_tick(board=None, **_):
    """Dispatcher hook. Fires only where the plugin is enabled in the dispatcher's own home;
    the cron script (scripts/calling_mom_sweep.py) covers every other setup."""
    _trace("tick", board=board)
    sweep(board)


def on_pre_tool(tool_name="", args=None, **_):
    """pre_tool_call: ``[crit]`` cards get the strong model at creation; completion is gated by ``verify:``."""
    if not _enabled():
        return None
    if tool_name in _GATED:
        key = _worker_key()
        try:
            blocked = _verify_gate(key) if key else None
            if blocked or not key or tool_name != "kanban_complete":
                return blocked
            return _review_redirect(key, args or {}) or _completion_check(key, args or {})
        except Exception as exc:  # pragma: no cover - a broken check must not trap the worker
            logger.warning("calling-mom: verify/review gate failed: %s", exc)
            return None
    if tool_name != "kanban_create":
        return None
    args = args or {}
    if args.get("model"):
        return None
    cfg = settings()
    text = f"{args.get('title') or ''}\n{args.get('body') or ''}".lower()
    marker, local = cfg["marker"].lower(), cfg["local_only_marker"].lower()
    if marker and marker in text and not (local and local in text):
        return {"action": "modify", "args": {"model": cfg["model"], "provider": cfg["provider"]}}
    return None


def _advisor_opted_in(task) -> bool:
    """Data leaves the machine only for opted-in boards or cards; ``[local-only]`` always wins."""
    if _is_local_only(task):
        return False
    adv = settings()["advisor"]
    board = os.environ.get("HERMES_KANBAN_BOARD", "")
    if "*" in adv["boards"] or board in adv["boards"]:
        return True
    marker = adv["marker"].lower()
    return bool(marker) and marker in f"{task.title or ''}\n{task.body or ''}".lower()


def _is_local_only(task) -> bool:
    # not `completion_contract`: "local-only" there is the board DEFAULT (no PR/CI acceptance), not a privacy flag
    marker = settings()["local_only_marker"].lower()
    return bool(marker) and marker in f"{task.title or ''}\n{task.body or ''}".lower()


def _spend_path() -> Path:
    override = os.environ.get("CALLING_MOM_SPEND")
    return Path(override) if override else _log_path().with_name("calling-mom-spend.json")


def _spent_today() -> dict:
    """``{"usd", "tokens", "olymp"}`` spent on advice today (UTC). Tokens cap what USD cannot: some models
    report no cost. ``olymp`` counts calls to the olymp model (a subscription: calls, not dollars)."""
    zero = {"usd": 0.0, "tokens": 0, "olymp": 0}
    try:
        data = json.loads(_spend_path().read_text())
    except (OSError, ValueError):
        return zero
    if data.get("date") != time.strftime("%Y-%m-%d", time.gmtime()):
        return zero
    return {"usd": float(data.get("usd", 0.0)), "tokens": int(data.get("tokens", 0)), "olymp": int(data.get("olymp", 0))}


def _add_spend(usd: float, tokens: int, olymp: int = 0) -> None:
    path = _spend_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    now = _spent_today()
    path.write_text(json.dumps({
        "date": time.strftime("%Y-%m-%d", time.gmtime()),
        "usd": now["usd"] + usd, "tokens": now["tokens"] + tokens, "olymp": now["olymp"] + olymp,
    }))


def _remember(key, tool, args, ok, text) -> None:
    if tool.startswith("kanban_"):  # board dumps are noise for the advisor
        return
    _history.setdefault(key, deque(maxlen=8)).append(
        {"tool": tool, "args": json.dumps(args, default=str)[:200], "ok": ok, "text": str(text)[-300:]}
    )


_PROMPTS = {
    "stuck": "You advise a weaker coding agent that may be stuck. The card text can hold the author's guess "
             "about the cause: trust the last steps over that guess and never repeat a hypothesis the steps "
             "already refute. If the steps show progress and no clear problem, answer exactly NO_ACTION. "
             "Otherwise answer in at most 100 words: the most likely root cause, then the next 1-3 concrete "
             "steps. Do not restate the task.",
    "critical_change": "You review a critical change made by a weaker coding agent. Answer in at most 100 words: "
                       "the biggest risk or irreversible aspect and what to double-check before going on. "
                       "If it is fine, say so in one line. Do not restate the task.",
    "completion": "You check whether a weaker coding agent delivered what its card asks. Compare the card "
                  "with the agent's completion summary. A requested deliverable (PR, merge, release, fix, report) "
                  "that is missing is a gap unless the summary proves it is not needed; a claim like 'not our bug' "
                  "needs evidence. Answer exactly DONE, or 'GAP: ' plus what is missing in at most 60 words.",
    "watch_change": "You review a command a weaker coding agent ran on another machine (ssh, pct, qm, scp). "
                    "Answer in at most 80 words: only if it changes state or exposes data, name the risk and what to "
                    "double-check. A plain read-only command: answer exactly NO_ACTION. Do not restate the task.",
    "second_opinion": "You answer a design question from a coding agent. Answer in at most 120 words: "
                      "recommend one option, give the deciding reason and name one risk.",
}


_URL_CREDS = re.compile(r"(\b[a-z][a-z0-9+.-]*://[^/\s:@]+:)[^@\s/]+@", re.I)
_GATE = ("\n\nYou are the FIRST reviewer; a far more expensive reviewer exists. Give your normal answer. "
         "Only for a rare high-stakes case that you cannot judge as safe from this context (irreversible "
         "production or public action without a fallback) answer exactly 'ESCALATE: ' plus a one-line reason. "
         "Routine or reversible changes: never escalate.")


def _build_messages(task, signal, steps, note, limit, latest_comment=None, prev_runs=None) -> list:
    from agent.redact import redact_for_egress

    lines = [f"- {s['tool']}({s['args']}) -> {'ok' if s['ok'] else 'ERROR'}: {s['text']}" for s in steps or ()]
    user = f"Task: {task.title}\n{(task.body or '')[:1500]}\n"
    # Add current subtask (latest operator comment)
    if latest_comment:
        user += f"\nCurrent subtask (latest operator note):\n{latest_comment[:800]}\n"
    # Add previous run results (context for what was already done)
    if prev_runs:
        user += "\nPrevious run results:\n"
        for run in prev_runs:
            user += f"- {run[:300]}\n"
    user += f"\nStuck signal: {signal}\nLast steps:\n" + "\n".join(lines)
    if note:
        user += f"\n\nDetails:\n{note}"
    user = redact_for_egress(_URL_CREDS.sub(r"\1***@", user))  # Hermes' redactor misses scheme://user:pass@host
    if len(user) > limit:  # keep the task head and the most recent steps
        head = limit // 3
        user = user[:head] + "\n...\n" + user[-(limit - head - 5):]
    system = _PROMPTS.get(signal, _PROMPTS["stuck"])
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _advise(key, category, signal, note="") -> "str | None":
    """Ask the strong model ONE short question; returns the text to append, or None (fail-open)."""
    cfg = settings()
    adv = cfg["advisor"]
    if not adv["enabled"] or _ctx is None:
        return None
    from hermes_cli import kanban_db as kb

    # model and provider only travel as a PAIR: a half pair would send one provider's model id to another
    ov = {k: v for k, v in (cfg["categories"].get(category) or {}).items() if k in ("model", "provider") and v}
    if len(ov) == 1:
        logger.warning("calling-mom: categories.%s needs model AND provider; ignored", category)
        ov = {}
    olymp = cfg["olymp"]
    olymp_pair = bool(olymp["model"] and olymp["provider"])
    if olymp["model"] and not olymp_pair:
        logger.warning("calling-mom: olymp needs model AND provider; olymp is off")
    route = {"model": cfg["model"], "provider": cfg["provider"], **ov}
    gate = olymp_pair and not ov and category in olymp["categories"]
    # categories with an own budget; every other category shares max_consults_per_card
    own = {"architecture": 4, "completion": 1, "watch": 1} | {
        c: v["max"] for c, v in cfg["categories"].items() if isinstance(v, dict) and "max" in v}
    if category == "completion":  # one check per RUN: a later, bigger run must not inherit an earlier run's check
        limit, where, params = own[category], "body LIKE ?", [f"[completion/%] run {key[1] or '?'} %"]
    elif category in own:
        limit, where, params = own[category], "body LIKE ?", [f"[{category}/%"]
    else:
        limit, params = adv["max_consults_per_card"], [f"[{c}/%" for c in own] + [_UNAVAILABLE + "%"]
        where = " AND ".join(["body NOT LIKE ?"] * len(params))
    task, opted_in = None, False
    try:
        with _connect() as conn:
            task = kb.get_task(conn, key[0])
            if task is None or not _advisor_opted_in(task):
                return None
            opted_in = True
            # Get the latest operator comment (current subtask)
            latest_comment = conn.execute(
                f"SELECT body FROM task_comments WHERE task_id = ? AND NOT ({_IS_ADVICE}) ORDER BY created_at DESC LIMIT 1",
                (task.id,),
            ).fetchone()
            latest_comment = latest_comment[0] if latest_comment else None
            # Get the previous run results (context for what was already done)
            prev_runs = [
                row[0]
                for row in conn.execute(
                    "SELECT summary FROM task_runs WHERE task_id = ? AND summary IS NOT NULL ORDER BY started_at DESC LIMIT 3",
                    (task.id,),
                ).fetchall()
            ]
            used = conn.execute(  # budgets live in the board: durable, visible, restart-proof
                f"SELECT COUNT(*) FROM task_comments WHERE task_id = ? AND {where}",
                (task.id, *params),
            ).fetchone()[0]
            if used >= limit:
                return None
            olymp_used = conn.execute(
                "SELECT COUNT(*) FROM task_comments WHERE task_id = ? AND body LIKE '[%/%] run %, olymp)%'",
                (task.id,),
            ).fetchone()[0]
        spent = _spent_today()
        if spent["usd"] >= adv["daily_usd_cap"] or spent["tokens"] >= adv["daily_token_cap"]:
            return None
        gate = gate and olymp_used < olymp["max_per_card"] and spent["olymp"] < olymp["max_per_day"]

        def ask(r, msgs):
            pair = (r["provider"], r["model"])
            if time.time() - _down.get(pair, 0) < COOLDOWN_S:
                raise _CoolingDown()
            try:
                out = _ctx.llm.complete(msgs, provider=r["provider"], model=r["model"],
                                        max_tokens=adv["max_output_tokens"], timeout=adv["timeout_s"],
                                        purpose="kanban-advisor")
            except Exception:
                _down[pair] = time.time()
                raise
            _down.pop(pair, None)
            return out

        msgs = _build_messages(task, signal, _history.get(key), note, adv["context_chars"], latest_comment, prev_runs)
        is_olymp = False
        if gate:  # mom first, with the escalation instruction
            first = ask(route, [{**msgs[0], "content": msgs[0]["content"] + _GATE}, *msgs[1:]])
            if (first.text or "").strip().upper().startswith("ESCALATE"):
                _add_spend(first.usage.cost_usd or 0.0, (first.usage.input_tokens or 0) + (first.usage.output_tokens or 0))
                route, is_olymp = {"model": olymp["model"], "provider": olymp["provider"]}, True
                res = ask(route, msgs)
            else:
                res = first
        else:
            res = ask(route, msgs)
        text = (res.text or "").strip()
        if not text:
            return None
        cost = res.usage.cost_usd or 0.0
        if is_olymp:  # subscription: counted in calls, never in dollars or tokens
            _add_spend(0.0, 0, olymp=1)
        else:
            _add_spend(cost, (res.usage.input_tokens or 0) + (res.usage.output_tokens or 0))
        if text.upper().startswith("NO_ACTION"):  # nothing to say: no comment, no interruption
            _trace_row("advisor", task=task.id, category=category, signal=signal, model=res.model,
                       provider=route["provider"], olymp=is_olymp,
                       tokens_in=res.usage.input_tokens, tokens_out=res.usage.output_tokens, usd=cost, noop=True)
            return None
        price = f", ${cost:.4f}" if cost else ""  # some providers report no cost: tokens are the truth
        _post_comment(task.id, f"[{category}/{signal}] run {key[1] or '?'} · {res.model} "
                      f"({route['provider']}{', olymp' if is_olymp else ''}): "
                      f"{res.usage.input_tokens} in / {res.usage.output_tokens} out{price}\n{text}")
        _trace_row("advisor", task=task.id, category=category, signal=signal, model=res.model,
                   provider=route["provider"], olymp=is_olymp,
                   tokens_in=res.usage.input_tokens, tokens_out=res.usage.output_tokens, usd=cost)
        _awaiting_reply.add(key)
        return f"[ADVISOR {res.model}] {text}"
    except _CoolingDown:
        return None
    except Exception as exc:
        logger.warning("calling-mom: advisor failed (%s): %s", signal, exc)
        if opted_in:
            _report_unavailable(task.id, exc)
        return None


def _report_unavailable(task_id, exc) -> None:
    """ONE visible comment per card when the advisor model cannot be reached (dead key, no login, ...).
    Without it the card just runs on without advice and nobody learns why."""
    try:
        from agent.redact import redact_for_egress

        _trace_row("advisor_error", task=task_id, error=type(exc).__name__)
        with _connect() as conn:
            if conn.execute("SELECT 1 FROM task_comments WHERE task_id = ? AND body LIKE ?",
                            (task_id, _UNAVAILABLE + "%")).fetchone():
                return
        reason = redact_for_egress(_URL_CREDS.sub(r"\1***@", f"{type(exc).__name__}: {exc}"))[:300]
        _post_comment(task_id, f"{_UNAVAILABLE} {reason}\nThe card continues without advice. "
                               "Check the setup: hermes calling-mom doctor")
    except Exception as err:  # pragma: no cover - reporting must never break the worker
        logger.warning("calling-mom: could not report advisor failure: %s", err)


def _post_comment(task_id, body) -> bool:
    """Write an advisor comment through the PUBLIC ``kanban_comment`` tool (``ctx.dispatch_tool``).

    The tool signs with the worker's own profile, and Hermes' comment relay skips a worker's own comments,
    so the advice (already in the tool result) is not delivered a second time as an "operator" note.
    Fail-open: a missing tool or a failed write never costs the worker its advice."""
    dispatch = getattr(_ctx, "dispatch_tool", None)
    if dispatch is None:  # Hermes older than the plugin floor
        return False
    try:
        out = json.loads(dispatch("kanban_comment", {"task_id": task_id, "body": body}) or "{}")
    except Exception as exc:
        logger.warning("calling-mom: advisor comment failed: %s", exc)
        return False
    if isinstance(out, dict) and out.get("error"):
        logger.warning("calling-mom: advisor comment refused: %s", str(out["error"])[:200])
        return False
    return True


_VERIFY_LINE = re.compile(r"(?im)^\s*verify:\s*(.+?)\s*$")
_GATED = {"kanban_complete", "kanban_request_review"}


def _verify_gate(key):
    """Run the card's ``verify:`` command before the worker may complete the card.

    Objective check, zero model tokens. Fail -> block with the output tail so the worker
    fixes it; after ``max_fix`` failures point to ``kanban_block`` (which escalates);
    two more failures later fail open so a broken check never traps a worker.
    """
    from hermes_cli import kanban_db as kb

    with _connect() as conn:
        task = kb.get_task(conn, key[0])
    match = _VERIFY_LINE.search(task.body or "") if task else None
    if not match:
        return None
    cfg = settings()["verify"]
    command = match.group(1)
    try:
        argv = shlex.split(command)
    except ValueError:
        argv = []
    why = (
        "untrusted creator" if task.created_by not in cfg["trusted_creators"]
        else "command not allowed" if not argv or os.path.basename(argv[0]) not in cfg["allow"]
        else None
    )
    if why:  # card text is not a shell: skip, leave a trace, never trap the worker
        _trace_row("verify_ignored", task=key[0], why=why, creator=task.created_by)
        return None
    workspace = os.environ.get("HERMES_KANBAN_WORKSPACE") or None
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8", "HOME": workspace or "/tmp"}
    try:
        proc = subprocess.run(
            argv, cwd=workspace, env=env, capture_output=True, text=True, timeout=cfg["timeout_s"],
        )
        ok, code, out = proc.returncode == 0, proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        ok, code, out = False, None, f"timed out after {cfg['timeout_s']}s"
    except OSError as exc:
        ok, code, out = False, None, f"could not start: {exc}"
    fails = _verify_fails.get(key, 0) + (0 if ok else 1)
    _verify_fails[key] = fails
    _trace_row("verify", task=key[0], ok=ok, fails=fails, exit=code)
    if ok or fails > cfg["max_fix"] + 2:
        return None
    reason = f"exit {code}" if code is not None else "timed out"
    tail = out[-cfg["tail_chars"]:].strip()
    msg = f"Verification failed ({reason}) for `{command}`. Fix this before completing:\n{tail}"
    advice = _advise(key, "validation", "verify_failed", note=f"verify command: {command}\n{tail}") if fails >= 2 else None
    if advice:
        msg += f"\n\n{advice}"
    if fails > cfg["max_fix"]:
        msg += "\n\nStill failing after several fixes: do NOT complete. Call kanban_block(kind='capability') with the failing output as reason."
    return {"action": "block", "message": msg}


def _review_redirect(key, args):
    """``[review]`` card + kanban_complete -> request_review with the reviewer profile (strong model).

    The weak model does the work; the stronger one only reads the result. Deterministic: the
    plugin makes the board call, the weak model is never asked to remember a rule.
    """
    cfg = settings()["review"]
    reviewer = cfg["reviewer"]
    if not reviewer or os.environ.get("HERMES_PROFILE") == reviewer or key in _redirected:
        return None
    from hermes_cli import kanban_db as kb
    from hermes_cli.profiles import profile_exists

    with _connect() as conn:
        task = kb.get_task(conn, key[0])
        if task is None or _is_local_only(task) or not cfg["marker"]:
            return None
        if cfg["marker"].lower() not in f"{task.title or ''}\n{task.body or ''}".lower():
            return None
        if not profile_exists(reviewer):
            logger.warning("calling-mom: reviewer profile %r is not installed", reviewer)
            return None
        metadata = args.get("metadata") if isinstance(args.get("metadata"), dict) else None
        run_id = os.environ.get("HERMES_KANBAN_RUN_ID")
        ok, reason = kb.request_review(
            conn, task.id, summary=str(args.get("summary") or "completed by the implementer"), metadata=metadata,
            reviewer=reviewer, expected_run_id=int(run_id) if run_id and run_id.isdigit() else None, with_reason=True,
        )
    if not ok:
        logger.warning("calling-mom: review hand-off refused for %s: %s", key[0], reason)
        return None
    _redirected.add(key)
    _trace_row("review_redirect", task=key[0], reviewer=reviewer)
    return {
        "action": "block",
        "message": f"This card is marked for review. Your result was handed to the reviewer profile '{reviewer}' "
                   f"(kanban_request_review). The card is now in review: do not call kanban_complete again, just end your turn.",
    }


def _completion_check(key, args):
    """Once per run: does the completion summary deliver what the card asks? A gap blocks the
    first ``kanban_complete`` only, so a wrong verdict costs one turn, never the card."""
    if key in _completion_checked:
        return None
    _completion_checked.add(key)
    summary = str(args.get("summary") or "")[:1500]
    advice = _advise(key, "completion", "completion", note=f"Completion summary of the agent:\n{summary}")
    verdict = (advice or "").split("] ", 1)[-1].strip()
    if not verdict.upper().startswith("GAP"):
        return None
    return {
        "action": "block",
        "message": f"A reviewer compared your summary with the card and found a gap:\n{verdict}\n\n"
                   "Do the missing part, then complete again. If you skip it on purpose, complete again and give "
                   "the evidence in the summary, or call kanban_block(kind='needs_input') when a human must decide.",
    }


def _architecture_hit(key, tool_name, args):
    """``(category, signal, note)`` when this call touches something risky (once per path/command), else None.
    Critical things and `watch` things (ssh, pct, ...) have separate budgets, see rules.py."""
    cfg = settings()["categories"].get("architecture") or {}
    args = args or {}
    if tool_name in ("write_file", "patch"):
        target = str(args.get("path") or "")
        body = str(args.get("content") or args.get("new_string") or "")[:800]
        tier, note = ("critical" if rules.is_critical_file(target, cfg.get("patterns") or []) else None), f"File: {target}\n{body}"
    elif tool_name in ("terminal", "execute_code"):  # execute_code runs shell commands too
        target = str(args.get("command") or args.get("code") or "")
        tier = rules.classify(target, extra=cfg.get("commands_extra") or [], critical=cfg.get("commands") or [],
                              watch=cfg.get("watch") or [])
        note = f"Command: {target[:400]}"
    else:
        return None
    if not target or tier is None or (*key, target) in _arch_seen:
        return None
    if tier == "watch":
        if _watch_used.get(key, 0) >= (settings()["categories"].get("watch") or {}).get("max", 1):
            return None
        _watch_used[key] = _watch_used.get(key, 0) + 1
    _arch_seen.add((*key, target))
    return ("watch", "watch_change", note) if tier == "watch" else ("architecture", "critical_change", note)


def handle_second_opinion(args, **_):
    """Tool: the worker asks the architecture reviewer one question (budgeted, redacted, fail-open)."""
    key = _worker_key()
    question = str((args or {}).get("question") or "").strip()
    if not key or not question:
        return json.dumps({"error": "needs a kanban worker session and a question"})
    note = f"Question: {question}\nContext: {str((args or {}).get('context') or '')[:1500]}"
    advice = _advise(key, "architecture", "second_opinion", note=note) if _enabled() else None
    if not advice:
        return json.dumps({"error": "second opinion unavailable (disabled, budget used, local-only card, or provider error)"})
    return json.dumps({"opinion": advice.split("] ", 1)[-1]})


_SECOND_OPINION = {
    "name": "second_opinion",
    "description": "Ask a stronger model one short question about a critical design or architecture decision. "
                   "Budgeted per card; use it before irreversible choices (schema, infra, public API), not for routine work.",
    "parameters": {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "The decision and the options you consider."},
            "context": {"type": "string", "description": "Constraints that matter (optional, short)."},
        },
        "required": ["question"],
    },
}


def _log_path() -> Path:
    """Shared by every profile on purpose: the gateway escalates, the cron sweeps, both must land
    in ONE log or the metrics only see half the story. ``get_default_hermes_root`` is the shared
    root (a profile's own home would fork the file per profile)."""
    override = os.environ.get("CALLING_MOM_LOG")
    if override:
        return Path(override)
    from hermes_constants import get_default_hermes_root

    return Path(get_default_hermes_root()) / "logs" / "calling-mom.jsonl"


def _worker_key():
    task = os.environ.get("HERMES_KANBAN_TASK")
    return (task, os.environ.get("HERMES_KANBAN_RUN_ID", "")) if task else None


def on_tool_result(tool_name="", args=None, result=None, status=None, error_type=None, error_message=None, **_):
    """Observe every tool result of a kanban worker; with the advisor on, append advice when stuck."""
    key = _worker_key()
    if not key or not _enabled():
        return None
    try:
        cfg = settings()
        if not cfg["observe"]:
            return None
        tracker = _trackers.get(key)
        if tracker is None:
            tracker = _trackers[key] = signals.Tracker(**cfg["limits"])
        ok = status != "error"
        text = error_message or (result if isinstance(result, str) else "")
        _remember(key, tool_name, args or {}, ok, text)
        event = tracker.feed(tool_name, args or {}, ok, text, error_type)
        row = {"ts": int(time.time()), "task": key[0], "run": key[1], "pid": os.getpid(), **event}
        path = _log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as fh:
            fh.write(json.dumps(row) + "\n")
        question = None
        if tool_name == "kanban_comment" and isinstance(result, str):
            m = re.match(r"(?is)\s*second[- ]opinion:\s*(.+)", str((args or {}).get("body") or ""))
            if m and cfg["advisor"]["enabled"]:
                question = m.group(1).strip()
                advice = _advise(key, "architecture", "second_opinion", note=f"Question: {question[:1500]}")
                if advice:
                    return f"{result}\n\n{advice}"
        if question is None and event["signals"] and cfg["advisor"]["enabled"] and isinstance(result, str):
            signal = event["signals"][0]
            if signal in _CATEGORY:  # flux_stall is monitoring-only, no escalation
                advice = _advise(key, _CATEGORY[signal], signal)
                if advice:
                    return f"{result}\n\n{advice}"
        if question is None and cfg["advisor"]["enabled"] and isinstance(result, str) and status != "error":
            hit = _architecture_hit(key, tool_name, args)
            advice = _advise(key, hit[0], hit[1], note=hit[2]) if hit else None
            if advice:
                return f"{result}\n\n{advice}"
    except Exception as exc:  # pragma: no cover - observing must never break a worker
        logger.warning("calling-mom: observe failed: %s", exc)
    return None


def on_api_response(assistant_message=None, **_):
    """post_api_request: the first worker text after advice tells whether it was followed or refuted."""
    key = _worker_key()
    content = str(getattr(assistant_message, "content", "") or "").strip()
    if key not in _awaiting_reply or not content:
        return
    _awaiting_reply.discard(key)
    # ponytail: keyword heuristic; a wrong label only skews the ratio, it never changes the run
    _trace_row("advice_feedback", task=key[0], verdict="rejected" if _REFUTED.search(content[:2000]) else "followed")


def register(ctx) -> None:
    global _ctx
    _ctx = ctx
    ctx.register_hook("kanban_task_claimed", on_claimed)
    ctx.register_hook("kanban_task_blocked", on_blocked)
    ctx.register_hook("transform_tool_result", on_tool_result)
    ctx.register_hook("pre_tool_call", on_pre_tool)
    ctx.register_hook("post_api_request", on_api_response)
    # Own toolset: "kanban" is a fixed list, so a tool registered there never reaches the model.
    _prompt = getattr(ctx, "register_system_prompt_section", None)
    if _prompt is not None:
        _prompt(
        id="calling-mom",
        content=(
            "Stuck or unsure about a critical decision? Write a kanban_comment whose text starts with "
            "`SECOND-OPINION: ` and your question; a stronger model answers in the tool result. "
            "A `verify: <command>` line in the card body is run before you may complete: fix failures first."
        ),
        )
    ctx.register_tool(name="second_opinion", toolset="calling-mom", schema=_SECOND_OPINION,
                      handler=handle_second_opinion, check_fn=lambda: bool(os.environ.get("HERMES_KANBAN_TASK")),
                      emoji="\U0001f9d0")
    register_cli = getattr(ctx, "register_cli_command", None)
    if register_cli is not None:  # older Hermes: no plugin CLI, the config keys still work
        register_cli(name="calling-mom", help="set up, inspect and test the Calling mom plugin",
                     setup_fn=cli.setup_parser,
                     handler_fn=lambda args: cli.dispatch(args, _spent_today, settings, _log_path),
                     description="setup: choose mom/olymp from your providers. status: show setup and usage. "
                                 "doctor: check the setup with one real call per model.")
    ctx.register_hook("on_kanban_dispatch_tick", on_dispatch_tick)
    ctx.register_hook("on_kanban_worker_exited", on_worker_exited)
    cfg = settings()
    logger.info(
        "calling-mom installed: block -> %s via %s; marker %s",
        cfg["model"], cfg["provider"], cfg["marker"],
    )
