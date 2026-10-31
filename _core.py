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
