"""The one door to Hermes internals.

Hermes has no public Kanban API for plugins yet (no pin-a-card-to-a-model, no unblock from a hook, no board
or event reader, no egress redaction). Every private import lives in THIS file, so a Hermes update breaks one
place, `hermes calling-mom doctor` names it, and the plugin degrades instead of failing at import time.

``ERROR`` is None when everything the plugin needs is importable. When it is set, ``register`` installs no
hooks. The helpers below are best effort and return safe values.
"""
try:
    from agent.redact import redact_for_egress
    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_boards as kbb
    from hermes_cli import kanban_db_connect as kbc
    ERROR = None
except Exception as exc:  # a removed or renamed Hermes module: report it, do not crash the host
    kb = kbb = kbc = redact_for_egress = None
    ERROR = f"{type(exc).__name__}: {exc}"


def load_config() -> dict:
    try:
        from hermes_cli.config import load_config as load
        return load() or {}
    except Exception:
        return {}


def board_activity(conn) -> list:
    """``[(task, comments, events)]`` of every card of the connected board (archived ones too), read only.
    The sweep counts who else touched the board from this (``touches.py``)."""
    return [(t, kb.list_comments(conn, t.id), kb.list_events(conn, t.id)) for t in kb.list_tasks(conn, include_archived=True)]


def running_cards(conn) -> list:
    """The cards that run now (``last_heartbeat_at`` tells the sweep which ones went quiet), read only."""
    return kb.list_tasks(conn, status="running")


def board_cards(conn) -> tuple:
    """``(tasks, links)`` of the connected board: its live cards and the ``(parent, child)`` pairs between them, read only."""
    tasks = kb.list_tasks(conn)
    return tasks, {(parent, t.id) for t in tasks for parent in kb.parent_ids(conn, t.id)}


def link(conn, parent_id, child_id) -> None:
    """``pace`` chains cards with a parent link. Raises ValueError when Hermes refuses (a cycle, a running child)."""
    kb.link_tasks(conn, parent_id, child_id)


def unlink(conn, parent_id, child_id) -> bool:
    return kb.unlink_tasks(conn, parent_id, child_id)


def profile_name() -> str:
    """The Hermes profile this process works for; quota health is kept per profile."""
    try:
        from hermes_cli.profiles import current_profile_name
        return current_profile_name("default") or "default"
    except Exception:
        return "default"


def last_event_id(conn, task_id, kind) -> "int | None":
    """Store id of the newest event of ``kind`` on a card: how a write calling-mom just made is told apart from outside help."""
    ids = [e.id for e in kb.list_events(conn, task_id) if e.kind == kind]
    return ids[-1] if ids else None


def check_board_read() -> "str | None":
    """None when the board reads the sweep needs work on the first live board, else the error (``doctor`` shows it)."""
    try:
        for slug in list_board_slugs()[:1]:
            with kb.scoped_current_board(slug), kbc.connect_closing() as conn:
                board_activity(conn)
                running_cards(conn)
                board_cards(conn)
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


def hook_budget() -> float:
    """Seconds Hermes gives a bounded hook (``plugins.hook_callback_timeout``, default 30), clamped to 1..600.

    Every wait inside ``pre_tool_call``, ``transform_tool_result`` and ``post_api_request`` is a fraction of it.
    A missing, non-numeric or negative value means Hermes' own default, as in Hermes itself."""
    plugins = load_config().get("plugins")
    try:
        value = float(plugins.get("hook_callback_timeout")) if isinstance(plugins, dict) else 30.0
    except (TypeError, ValueError):
        value = 30.0
    return min(600.0, max(1.0, value)) if value >= 0 else 30.0


def save_config(cfg) -> None:
    from hermes_cli.config import save_config as save
    save(cfg)


def profile_exists(name) -> bool:
    try:
        from hermes_cli.profiles import profile_exists as exists
        return bool(exists(name))
    except Exception:
        return False  # unknown profile: the caller skips the step that needs it


def list_providers(cfg) -> list:
    """Authenticated providers with their models, for the setup list. Empty: setup asks you to type the pair."""
    try:
        from hermes_cli.model_switch_providers import list_authenticated_providers
        model = cfg.get("model") if isinstance(cfg.get("model"), dict) else {}
        rows = list_authenticated_providers(current_provider=model.get("provider", ""),
                                            user_providers=cfg.get("providers"), max_models=40)
        return [{"slug": r["slug"], "name": r.get("name") or r["slug"], "models": list(r.get("models") or [])} for r in rows]
    except Exception:
        return []


def list_board_slugs() -> list:
    try:
        from hermes_cli.kanban_db_boards import list_boards
        return [b.get("slug") or b.get("name") for b in list_boards(include_archived=False)]
    except Exception:
        return []


def has_menu() -> bool:
    try:
        from hermes_cli.curses_ui import curses_radiolist  # noqa: F401
        return True
    except Exception:
        return False


def menu(title, labels, default, search=None):
    """Hermes' own selection list: arrow keys, ``/`` filters (over ``search`` when given). Returns the row index,
    or None when the user pressed ESC or Ctrl+C."""
    from hermes_cli.curses_ui import curses_radiolist
    index = curses_radiolist(title, labels, default, searchable=True, search_labels=search, cancel_returns=-1)
    return None if index < 0 else index  # ESC and Ctrl+C: Hermes would otherwise hand back the default row


def probe_model(provider, model) -> None:
    """One real call through Hermes' plugin LLM gate; raises when the pair does not answer."""
    from agent.plugin_llm import PluginLlm
    PluginLlm(plugin_id="calling-mom").complete(
        [{"role": "user", "content": "Reply with the single word OK."}], provider=provider, model=model,
        max_tokens=16, timeout=60, purpose="calling-mom-doctor")


def hermes_version() -> str:
    try:
        from hermes_cli import __version__
        return str(__version__)
    except Exception:
        return "?"
