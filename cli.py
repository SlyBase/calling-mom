"""User-facing commands of the plugin: ``hermes calling-mom setup | status | doctor``.

Pure logic first (testable with fake I/O), Hermes wiring last (``make_env``).
"""
import argparse
import copy
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

MIN_HERMES = (0, 22, 0)
LLM_KEY = "plugins.entries.calling-mom.llm."
CRON_NAME = "calling-mom: sweep - escalate parked cards, push metrics"
# Starting points, not promises: model ids change. `setup` lists what is really configured.
PRESETS = {
    "subscription": {"mom": ("claude-subscription-directsdk-experimental", "claude-sonnet-5-5"),
                     "olymp": ("claude-subscription-directsdk-experimental", "claude-opus-5-5")},
    "openrouter": {"mom": ("openrouter", "deepseek/deepseek-v4.1-flash-1017"), "olymp": None},
}


# ---------------------------------------------------------------- interactive list

class SetupCancelled(Exception):
    """ESC in a menu, Ctrl+C or end of input: stop everything, write nothing."""


class SetupError(ValueError):
    """A setup flag or answer that cannot be used; the message says what to pass instead."""


def routes_from(cfg):
    """Delegation model routes (advanced-delegation: ``delegation.backends.model_map.options``) as
    ``(usable, unusable_names)``. Usable = has a name, a provider AND a model: we always need the pair."""
    options = _get(cfg, "delegation.backends.model_map.options", [])
    usable, unusable = [], []
    for o in options if isinstance(options, list) else []:
        if isinstance(o, dict) and o.get("name"):
            if o.get("provider") and o.get("model"):
                usable.append({k: str(o.get(k) or "") for k in ("name", "description", "provider", "model")})
            else:
                unusable.append(str(o["name"]))
    return usable, unusable


def delegation_hint(cfg) -> str:
    """Why no delegation routes are offered, when the Advanced Delegation plugin is on. Empty = nothing to say."""
    if "advanced-delegation" not in (_get(cfg, "plugins.enabled", []) or []):
        return ""
    usable, unusable = routes_from(cfg)
    if usable:
        return ""
    if unusable:
        return (f"Advanced Delegation is on, but its route(s) {', '.join(unusable)} have no provider, so they cannot be "
                "offered here (we need provider and model). Add a provider with: hermes delegation config")
    return ("Advanced Delegation is installed but has no model routes yet, so there is nothing to offer here. "
            "Create them with: hermes delegation config   (then run setup again)")


def pick(prompt, options, ask, say, default=0, none_label=None, other=True, choose=None, search=None):
    """Choose one of ``options`` = [(label, value)].

    With ``choose`` (a menu: arrow keys, ``/`` filters) the menu owns the screen and returns a row index
    (None = cancelled: ESC or Ctrl+C). Without it: a numbered list. Enter = default, ``o`` = type a value
    yourself, last number = ``none_label`` (returns None). Three invalid answers fall back to the default.
    ``search`` = short text per option that the menu's filter matches (a long label would match nearly anything)."""
    if choose:
        extra = ([none_label] if none_label else []) + (["other (type it)"] if other else [])
        labels = [label for label, _ in options] + extra
        start = len(options) if default == "none" else default
        index = choose(prompt, labels, start, **({"search": list(search) + extra} if search else {}))
        if index is None:
            raise SetupCancelled()
        if index < len(options):
            return options[index][1]
        return None if none_label and index == len(options) else ask("value > ").strip()
    lines = [f"  {i}) {label}" for i, (label, _) in enumerate(options, 1)]
    if none_label:
        lines.append(f"  {len(options) + 1}) {none_label}")
    if other:
        lines.append("  o) other (type it)")
    fallback = None if default == "none" else options[default][1]
    shown = len(options) + 1 if default == "none" else default + 1
    for _ in range(3):
        say(prompt)
        for line in lines:
            say(line)
        answer = ask(f"[{shown}] > ").strip().lower()
        if not answer:
            break
        if answer == "o" and other:
            return ask("value > ").strip()
        if answer.isdigit():
            n = int(answer)
            if 1 <= n <= len(options):
                return options[n - 1][1]
            if none_label and n == len(options) + 1:
                return None
        say("Please enter one of the numbers" + (" or o" if other else "") + ".")
    return fallback


def _pick_pair(title, listing, ask, say, none_label=None, current=None, choose=None, routes=()):
    """Provider first, then one of its models. ``None`` when ``none_label`` was chosen.
    ``current`` = (provider, model) already configured: preselected, Enter keeps it."""
    if routes:  # delegation routes already name a provider + model: offer them first
        in_routes = bool(current) and any((r["provider"], r["model"]) == tuple(current) for r in routes)
        source = pick(f"{title}: where do the values come from?",
                      [("one of your delegation routes", "route"), ("provider and model, chosen by hand", "hand")],
                      ask, say, none_label=none_label, other=False, choose=choose,
                      default="none" if none_label and not current else (0 if in_routes or not current else 1))
        if source is None:
            return None
        if source == "route":
            names = [(f"{r['name']}: {r['model']} ({r['provider']})" + (f" - {r['description']}" if r["description"] else ""), r)
                     for r in routes]
            chosen = pick(f"{title}: which route?", names, ask, say, other=False, choose=choose, search=[r["name"] for r in routes],
                          default=next((i for i, (_, r) in enumerate(names) if current and (r["provider"], r["model"]) == tuple(current)), 0))
            return chosen["provider"], chosen["model"]
    if not listing:  # nothing detectable: the user types the pair
        provider = ask(f"{title} provider{' (empty = none)' if none_label else ''} > ").strip()
        if not provider and none_label:
            return None
        return provider, ask(f"{title} model > ").strip()
    slugs = [p["slug"] for p in listing]
    cur_provider, cur_model = current or (None, None)
    provider = pick(f"{title}: which provider?", [(f"{p['name']} ({p['slug']})", p["slug"]) for p in listing],
                    ask, say, none_label=none_label, choose=choose,
                    default=slugs.index(cur_provider) if cur_provider in slugs else ("none" if none_label else 0))
    if provider is None:
        return None
    models = next((p["models"] for p in listing if p["slug"] == provider), [])
    if not models:
        return provider, ask(f"{title} model > ").strip()
    shown = models if choose else models[:25]  # a menu filters, a numbered list has to stay short
    return provider, pick(f"{title}: which model?", [(m, m) for m in shown], ask, say, choose=choose,
                          default=shown.index(cur_model) if cur_provider == provider and cur_model in shown else 0)


# ---------------------------------------------------------------- config changes

def _merge_list(old, new):
    out = list(old)
    for x in new:
        if x not in out:
            out.append(x)
    return out


def _boards(old, new):
    """Opted-in boards only grow, but ``*`` already means all of them."""
    merged = _merge_list(old, new)
    return ["*"] if "*" in merged else merged


def _get(cfg, dotted, default=None):
    for part in dotted.split("."):
        if not isinstance(cfg, dict) or part not in cfg:
            return default
        cfg = cfg[part]
    return cfg


def updates(mom, olymp, boards, cfg) -> dict:
    """Dotted key -> value for one ``setup``. Allowlists only grow: entries you already granted stay."""
    pairs = [p for p in (mom, olymp) if p]
    upd = {
        "calling_mom.mom.provider": mom[0], "calling_mom.mom.model": mom[1],
        "calling_mom.olymp.provider": olymp[0] if olymp else "", "calling_mom.olymp.model": olymp[1] if olymp else "",
        "calling_mom.advisor.enabled": True,
        "calling_mom.advisor.boards": _boards(_get(cfg, "calling_mom.advisor.boards", []), boards),
        LLM_KEY + "allow_model_override": True, LLM_KEY + "allow_provider_override": True,
        LLM_KEY + "allowed_models": _merge_list(_get(cfg, LLM_KEY + "allowed_models", []), [m for _, m in pairs]),
        LLM_KEY + "allowed_providers": _merge_list(_get(cfg, LLM_KEY + "allowed_providers", []), [p for p, _ in pairs]),
    }
    return upd


def apply_updates(cfg, upd) -> dict:
    out = copy.deepcopy(cfg)
    for dotted, value in upd.items():
        node = out
        *path, last = dotted.split(".")
        for part in path:
            node = node.setdefault(part, {})
        node[last] = value
    return out


def setup(cfg, listing, boards, ask, say, preset=None, choose=None, routes=()):
    """Ask (or take a preset), return ``(updates, summary_lines)``. Writes nothing."""
    if preset:
        if preset not in PRESETS:
            raise ValueError(f"unknown preset {preset!r}; choose from {sorted(PRESETS)}")
        mom, olymp = PRESETS[preset]["mom"], PRESETS[preset]["olymp"]
    else:
        say("Mom = your everyday helper (cheap, asked often). Olymp = the expensive model, asked only for rare edge cases.")
        cur = {k: ((_get(cfg, f"calling_mom.{k}.provider") or None), _get(cfg, f"calling_mom.{k}.model"))
               for k in ("mom", "olymp")}
        mom = _pick_pair("Mom", listing, ask, say, current=cur["mom"] if cur["mom"][0] else None, choose=choose, routes=routes)
        olymp = _pick_pair("Olymp", listing, ask, say, none_label="no olymp (mom only)",
                           current=cur["olymp"] if cur["olymp"][0] else None, choose=choose, routes=routes)
    chosen = ["*"] if not boards else pick("Which board may use the advisor?", [(b, [b]) for b in boards], ask, say,
                                           none_label="all boards", other=False, choose=choose)
    return _result(cfg, mom, olymp, chosen if chosen is not None else ["*"])


def _result(cfg, mom, olymp, boards):
    upd = updates(mom, olymp, boards, cfg)
    summary = [f"mom     : {mom[1]} ({mom[0]})", f"olymp   : {f'{olymp[1]} ({olymp[0]})' if olymp else 'off'}",
               f"boards  : {', '.join(upd['calling_mom.advisor.boards']) or '(none)'}"]
    return upd, summary


# ---------------------------------------------------------------- flags (no questions)

def parse_pair(value, routes):
    """``provider/model`` (split at the first slash: models may contain more) or ``route:NAME``."""
    value = (value or "").strip()
    if value.startswith("route:"):
        name = value[6:]
        for r in routes:
            if r["name"] == name:
                return r["provider"], r["model"]
        raise SetupError(f"unknown route {name!r}; routes with provider and model: {', '.join(r['name'] for r in routes) or '(none)'}")
    provider, _, model = value.partition("/")
    if not provider or not model:
        raise SetupError(f"{value!r} is not provider/model (for example openrouter/x-ai/grok-4) or route:NAME")
    return provider, model


def resolve(mom, olymp, cfg, routes):
    """Flags -> (mom pair, olymp pair or None). No ``--olymp``: keep the one that is configured."""
    if (mom or "").strip().lower() == "none":
        raise SetupError("mom cannot be none: it answers every stuck signal")
    if olymp is None:
        current = (_get(cfg, "calling_mom.olymp.provider"), _get(cfg, "calling_mom.olymp.model"))
        pair = current if all(current) else None
    else:
        pair = None if olymp.strip().lower() == "none" else parse_pair(olymp, routes)
    return parse_pair(mom, routes), pair


def parse_boards(text):
    text = (text or "").strip()
    return ["*"] if text == "*" else [b.strip() for b in text.split(",") if b.strip()]


# ---------------------------------------------------------------- status

def mom_pair(cm):
    """``(provider, model)`` of the mom: the ``mom:`` block, else the legacy top-level pair."""
    sb = cm.get("mom") or {}
    if sb.get("model") or sb.get("provider"):
        return sb.get("provider") or "", sb.get("model") or ""
    return cm.get("provider") or "", cm.get("model") or ""


def count_errors(path, now=None):
    """``(count, last error name)`` of today's (UTC) ``advisor_error`` rows in the plugin log."""
    import time
    now = int(now if now is not None else time.time())
    start = now - now % 86400
    count, last = 0, None
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return 0, None
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("kind") == "advisor_error" and row.get("ts", 0) >= start:
            count, last = count + 1, row.get("error")
    return count, last


def status_lines(cm, spent, errors=None) -> list:
    sp, sm = mom_pair(cm)
    olymp = cm.get("olymp") or {}
    adv = cm.get("advisor") or {}
    lines = [f"mom     : {sm or '?'} ({sp or '?'})"]
    if olymp.get("model") and olymp.get("provider"):
        lines.append(f"olymp   : {olymp['model']} ({olymp['provider']}), calls today {spent.get('olymp', 0)} / {olymp.get('max_per_day', '?')}, "
                     f"max {olymp.get('max_per_card', '?')} per card")
    else:
        lines.append("olymp   : off")
    boards = adv.get("boards") or []
    lines.append(f"advisor : {'on' if adv.get('enabled') else 'OFF'}, boards: {', '.join(boards) or '(none)'}")
    if adv.get("enabled") and not boards:
        lines.append("warning : advisor is on but no board is opted in, nothing is sent (run: hermes calling-mom setup)")
    if errors and errors[0]:
        lines.append(f"errors  : {errors[0]} advisor call(s) failed today, last: {errors[1]}. Run: hermes calling-mom doctor")
    lines.append(f"spend   : ${spent.get('usd', 0):.4f} / ${adv.get('daily_usd_cap', '?')}, "
                 f"{spent.get('tokens', 0)} / {adv.get('daily_token_cap', '?')} tokens today (mom only)")
    return lines


# ---------------------------------------------------------------- doctor

def version_ok(version) -> bool:
    m = re.match(r"\s*v?(\d+)\.(\d+)(?:\.(\d+))?", str(version or ""))
    return bool(m) and tuple(int(x or 0) for x in m.groups()) >= MIN_HERMES


def find_sweeps(homes) -> list:
    """``[(home, job id, state)]`` of every sweep cron job (``CRON_NAME``) in the given Hermes homes.
    Jobs live per home (root gateway, each profile): one home cannot see the others."""
    found = []
    for home in homes:
        try:
            jobs = json.loads((Path(home) / "cron" / "jobs.json").read_text()).get("jobs") or []
        except (OSError, ValueError, AttributeError):
            continue
        found += [(str(home), j.get("id"), j.get("state") or "?") for j in jobs if j.get("name") == CRON_NAME]
    return found


def _sweep_check(sweeps):
    active = [(h, i) for h, i, state in sweeps if state not in ("paused", "completed")]
    idle = [i for _, i, state in sweeps if state in ("paused", "completed")]
    if len(active) == 1:
        return "ok", f"{active[0][1]}" + (f" (paused duplicate: {', '.join(idle)})" if idle else "")
    if not active:
        return "warn", "no active sweep job: crash-parked cards are not escalated and no metrics are pushed (run: hermes calling-mom setup)"
    ids = ", ".join(f"{i} ({h})" for h, i in active)
    return "warn", f"{len(active)} active sweep jobs: {ids}. Keep one: hermes cron remove <id> in that home"


def run_checks(cm, llm, probe, version, internals, sweeps=None) -> list:
    """``[(name, "ok"|"warn"|"fail", detail)]``. Live calls only if the static checks passed.
    ``sweeps`` = ``find_sweeps()`` result; None skips the cron check."""
    out = []

    def add(name, status, detail=""):
        out.append((name, status, detail))

    minimum = ".".join(map(str, MIN_HERMES[:2]))
    add("hermes version", "ok" if version_ok(version) else "fail",
        f"{version}" if version_ok(version) else f"found {version!r}, need >= {minimum}")
    add("hermes internals", "ok" if internals else "fail",
        "" if internals else "a Hermes module the plugin needs is missing or renamed: this Hermes is not supported")
    sp, sm = mom_pair(cm)
    mom_ok = bool(sp and sm)
    add("mom pair", "ok" if mom_ok else "fail",
        f"{sm} ({sp})" if mom_ok else "model AND provider are needed (run: hermes calling-mom setup)")
    olymp = cm.get("olymp") or {}
    mm, mp = olymp.get("model") or "", olymp.get("provider") or ""
    olymp_on = bool(mm and mp)
    add("olymp pair", "ok" if (olymp_on or not (mm or mp)) else "fail",
        f"{mm} ({mp})" if olymp_on else ("off" if not (mm or mp) else "model AND provider are needed"))
    need_models = [m for m in (sm, mm) if m]
    need_providers = [p for p in (sp, mp) if p]
    miss_m = [m for m in need_models if m not in (llm.get("allowed_models") or [])]
    miss_p = [p for p in need_providers if p not in (llm.get("allowed_providers") or [])]
    flags = llm.get("allow_model_override") is True and llm.get("allow_provider_override") is True
    if flags and not (miss_m or miss_p):
        add("allowlist", "ok", "")
    else:
        what = ([] if flags else ["allow_*_override is off"]) + [f"model {m}" for m in miss_m] + [f"provider {p}" for p in miss_p]
        add("allowlist", "fail", "not granted: " + ", ".join(what) + " (fix: hermes calling-mom setup)")
    static_ok = all(status == "ok" for _, status, _ in out)
    adv = cm.get("advisor") or {}
    if adv.get("enabled") and adv.get("boards"):
        add("advisor", "ok", "boards: " + ", ".join(adv["boards"]))
    else:
        add("advisor", "warn", "off or no board opted in: nothing is sent to the mom or olymp")
    if sweeps is not None:
        add("sweep cron", *_sweep_check(sweeps))
    if static_ok:
        tiers = [("mom call", sp, sm)] + ([("olymp call", mp, mm)] if olymp_on else [])
        for name, prov, model in tiers:
            try:
                probe(prov, model)
                add(name, "ok", f"{model} answered")
            except Exception as exc:
                add(name, "fail", f"{model} ({prov}): {str(exc)[:200]}")
    return out


def exit_code(checks) -> int:
    return 1 if any(s == "fail" for _, s, _ in checks) else 0


# ---------------------------------------------------------------- commands

@dataclass
class Env:
    load: Callable
    save: Callable
    providers: Callable
    boards: Callable
    spent: Callable
    probe: Callable
    version: str
    internals: bool
    ask: Callable
    say: Callable
    cron: Callable
    sweeps: Callable
    effective: Optional[Callable] = field(default=None)
    errors: Optional[Callable] = field(default=None)
    routes: Callable = lambda: []      # usable delegation routes (see routes_from)
    choose: Optional[Callable] = None  # menu callback; None = numbered lists
    interactive: bool = True           # False: no terminal, never wait for an answer


def _blocks(env):
    cfg = (env.effective or env.load)()
    return (cfg.get("calling_mom") or {}), (_get(cfg, "plugins.entries.calling-mom.llm", {}) or {})


def _plan(env, cfg, preset, mom, olymp, boards):
    """Updates and summary: from flags when they say everything, otherwise by asking."""
    routes = env.routes()
    if (olymp or boards) and not (mom or preset):
        raise SetupError("--olymp and --boards need --mom (or --preset)")
    if mom or (preset and (boards or not env.interactive)):
        if mom:
            pair_mom, pair_olymp = resolve(mom, olymp, cfg, routes)
        else:
            if preset not in PRESETS:
                raise SetupError(f"unknown preset {preset!r}; choose from {sorted(PRESETS)}")
            pair_mom, pair_olymp = PRESETS[preset]["mom"], PRESETS[preset]["olymp"]
        chosen = parse_boards(boards) if boards else ([] if _get(cfg, "calling_mom.advisor.boards") else ["*"])
        return _result(cfg, pair_mom, pair_olymp, chosen)
    if not env.interactive:
        raise SetupError("This is not a terminal, so there is nobody to ask. Pass --mom provider/model (or route:NAME), "
                         "optionally --olymp, --boards and --cron/--no-cron.")
    hint = delegation_hint(cfg)
    if hint:
        env.say(hint)
    return setup(cfg, env.providers(), env.boards(), env.ask, env.say, preset=preset, choose=env.choose, routes=routes)


def cmd_setup(env, preset=None, yes=False, mom=None, olymp=None, boards=None, cron=None) -> int:
    cfg = env.load()
    try:
        upd, summary = _plan(env, cfg, preset, mom, olymp, boards)
    except SetupError as exc:
        env.say(str(exc))
        return 2
    except (SetupCancelled, KeyboardInterrupt, EOFError):
        env.say("\nCancelled. Nothing was written.")
        return 1
    env.say("\nWriting to your Hermes config:")
    for line in summary:
        env.say("  " + line)
    env.say("  (and the matching plugins.entries.calling-mom.llm allowlist; existing entries stay)")
    env.save(apply_updates(cfg, upd))
    env.say("Config written.")
    existing = env.sweeps()
    if existing:  # jobs are per Hermes home: look everywhere, never create a second one
        env.say("A sweep cron job already exists: " + ", ".join(f"{i} ({h}, {st})" for h, i, st in existing)
                + ". Not creating another.")
    elif cron is False:
        env.say("No sweep cron job created (--no-cron).")
    else:
        env.say(f"Creating the sweep cron job '{CRON_NAME}' (every minute: escalates cards the crash breaker parked, "
                "pushes metrics). Skip it with --no-cron.")
        env.cron()
    env.say("Next: hermes calling-mom doctor   (makes one real call to each model)")
    return 0


def cmd_status(env) -> int:
    cm, _ = _blocks(env)
    for line in status_lines(cm, env.spent(), env.errors() if env.errors else None):
        env.say(line)
    return 0


def cmd_doctor(env) -> int:
    cm, llm = _blocks(env)
    checks = run_checks(cm, llm, env.probe, env.version, env.internals, env.sweeps())
    for name, status, detail in checks:
        env.say(f"{ {'ok': 'OK  ', 'warn': 'WARN', 'fail': 'FAIL'}[status] }  {name:<17} {detail}".rstrip())
    return exit_code(checks)


# ---------------------------------------------------------------- Hermes wiring (verified live, see README)

def make_env(spent, effective, log_path, core, ask=input, say=print):  # pragma: no cover - needs a live Hermes
    def providers():
        return core.list_providers(core.load_config())

    def probe(provider, model):
        core.probe_model(provider, model)

    version, internals = core.hermes_version(), core.ERROR is None
    interactive = sys.stdin.isatty()

    def hermes_bin():
        return shutil.which("hermes") or str(Path(sys.executable).with_name("hermes"))

    def sweeps():
        from hermes_constants import get_default_hermes_root, get_hermes_home
        root = Path(get_default_hermes_root())
        homes = {str(Path(get_hermes_home())), str(root), *map(str, sorted((root / "profiles").glob("*")))}
        return find_sweeps(sorted(homes))

    def cron():
        from hermes_constants import get_hermes_home
        dst = Path(get_hermes_home()) / "scripts" / "calling_mom_sweep.py"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(Path(__file__).with_name("scripts") / "sweep.py", dst)
        r = subprocess.run([hermes_bin(), "cron", "create", "every 1m", "--name", CRON_NAME, "--no-agent",
                            "--script", dst.name, "--interpreter", sys.executable, "--deliver", "local"],
                           capture_output=True, text=True, timeout=120)
        say("cron job created" if r.returncode == 0 else f"cron job NOT created: {(r.stderr or r.stdout).strip()[:200]}")

    return Env(load=core.load_config, save=core.save_config, providers=providers, boards=core.list_board_slugs, spent=spent, probe=probe,
               version=str(version), internals=internals, ask=ask, say=say, cron=cron, sweeps=sweeps,
               routes=lambda: routes_from(core.load_config())[0],
               choose=core.menu if interactive and core.has_menu() else None, interactive=interactive,
               effective=lambda: {**core.load_config(), "calling_mom": effective()},
               errors=lambda: count_errors(log_path()))


def setup_parser(parser):  # pragma: no cover - argparse glue
    sub = parser.add_subparsers(dest="cm_cmd")
    s = sub.add_parser("setup", help="choose mom/olymp from your configured providers and grant them")
    s.add_argument("--preset", choices=sorted(PRESETS), help="skip the model questions")
    s.add_argument("--mom", metavar="PROVIDER/MODEL", help="the everyday helper, or route:NAME (a delegation route)")
    s.add_argument("--olymp", metavar="PROVIDER/MODEL", help="the expensive model, route:NAME, or none (default: keep the current one)")
    s.add_argument("--boards", metavar="A,B", help="boards that may use the advisor, or * (default: keep, else all)")
    s.add_argument("--cron", action=argparse.BooleanOptionalAction, default=None,
                   help="create the sweep cron job (default: yes, unless one exists)")
    s.add_argument("--yes", action="store_true", help="accepted for scripts; setup never asks before writing")
    sub.add_parser("status", help="show the effective setup and today's usage")
    sub.add_parser("doctor", help="check the setup and make one real call to each model")
    parser.set_defaults(cm_cmd=None)


def dispatch(args, spent, effective, log_path, core):  # pragma: no cover - argparse glue
    env = make_env(spent, effective, log_path, core)
    cmd = getattr(args, "cm_cmd", None) or "status"
    if cmd == "setup":
        return cmd_setup(env, preset=args.preset, yes=args.yes, mom=args.mom, olymp=args.olymp, boards=args.boards, cron=args.cron)
    return {"status": cmd_status, "doctor": cmd_doctor}[cmd](env)
