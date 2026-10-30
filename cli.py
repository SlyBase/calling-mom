"""User-facing commands of the plugin: ``hermes calling-mom setup | status | doctor``.

Pure logic first (testable with fake I/O), Hermes wiring last (``make_env``).
"""
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
CRON_NAME = "calling-mom-sweep"
# Starting points, not promises: model ids change. `setup` lists what is really configured.
PRESETS = {
    "subscription": {"sibling": ("claude-subscription-directsdk-experimental", "claude-sonnet-5-5"),
                     "mom": ("claude-subscription-directsdk-experimental", "claude-opus-5-5")},
    "openrouter": {"sibling": ("openrouter", "deepseek/deepseek-v4.1-flash-1017"), "mom": None},
}


# ---------------------------------------------------------------- interactive list

def pick(prompt, options, ask, say, default=0, none_label=None, other=True):
    """Numbered list. ``options`` = [(label, value)]. Enter = default, ``o`` = type a value yourself,
    last number = ``none_label`` (returns None). Three invalid answers fall back to the default."""
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


def _pick_pair(title, listing, ask, say, none_label=None, current=None):
    """Provider first, then one of its models. ``None`` when ``none_label`` was chosen.
    ``current`` = (provider, model) already configured: preselected, Enter keeps it."""
    if not listing:  # nothing detectable: the user types the pair
        provider = ask(f"{title} provider{' (empty = none)' if none_label else ''} > ").strip()
        if not provider and none_label:
            return None
        return provider, ask(f"{title} model > ").strip()
    slugs = [p["slug"] for p in listing]
    cur_provider, cur_model = current or (None, None)
    provider = pick(f"{title}: which provider?", [(f"{p['name']} ({p['slug']})", p["slug"]) for p in listing],
                    ask, say, none_label=none_label,
                    default=slugs.index(cur_provider) if cur_provider in slugs else ("none" if none_label else 0))
    if provider is None:
        return None
    models = next((p["models"] for p in listing if p["slug"] == provider), [])
    if not models:
        return provider, ask(f"{title} model > ").strip()
    shown = models[:25]
    return provider, pick(f"{title}: which model?", [(m, m) for m in shown], ask, say,
                          default=shown.index(cur_model) if cur_provider == provider and cur_model in shown else 0)


# ---------------------------------------------------------------- config changes

def _merge_list(old, new):
    out = list(old)
    for x in new:
        if x not in out:
            out.append(x)
    return out


def _get(cfg, dotted, default=None):
    for part in dotted.split("."):
        if not isinstance(cfg, dict) or part not in cfg:
            return default
        cfg = cfg[part]
    return cfg


def updates(sibling, mom, boards, cfg) -> dict:
    """Dotted key -> value for one ``setup``. Allowlists only grow: entries you already granted stay."""
    pairs = [p for p in (sibling, mom) if p]
    upd = {
        "calling_mom.sibling.provider": sibling[0], "calling_mom.sibling.model": sibling[1],
        "calling_mom.mom.provider": mom[0] if mom else "", "calling_mom.mom.model": mom[1] if mom else "",
        "calling_mom.advisor.enabled": True,
        "calling_mom.advisor.boards": _merge_list(_get(cfg, "calling_mom.advisor.boards", []), boards),
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


def setup(cfg, listing, boards, ask, say, preset=None):
    """Ask (or take a preset), return ``(updates, summary_lines)``. Writes nothing."""
    if preset:
        if preset not in PRESETS:
            raise ValueError(f"unknown preset {preset!r}; choose from {sorted(PRESETS)}")
        sibling, mom = PRESETS[preset]["sibling"], PRESETS[preset]["mom"]
    else:
        say("Sibling = the cheap model that answers first. Mom = the expensive model, asked only for rare edge cases.")
        cur = {k: ((_get(cfg, f"calling_mom.{k}.provider") or None), _get(cfg, f"calling_mom.{k}.model"))
               for k in ("sibling", "mom")}
        sibling = _pick_pair("Sibling", listing, ask, say, current=cur["sibling"] if cur["sibling"][0] else None)
        mom = _pick_pair("Mom", listing, ask, say, none_label="no mom (sibling only)",
                         current=cur["mom"] if cur["mom"][0] else None)
    chosen = ["*"] if not boards else pick("Which board may use the advisor?", [(b, [b]) for b in boards], ask, say,
                                           none_label="all boards", other=False)
    upd = updates(sibling, mom, chosen if chosen is not None else ["*"], cfg)
    summary = [f"sibling : {sibling[1]} ({sibling[0]})", f"mom     : {f'{mom[1]} ({mom[0]})' if mom else 'off'}",
               f"boards  : {', '.join(upd['calling_mom.advisor.boards']) or '(none)'}"]
    return upd, summary


# ---------------------------------------------------------------- status

def sibling_pair(cm):
    """``(provider, model)`` of the sibling: the ``sibling:`` block, else the legacy top-level pair."""
    sb = cm.get("sibling") or {}
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
    sp, sm = sibling_pair(cm)
    mom = cm.get("mom") or {}
    adv = cm.get("advisor") or {}
    lines = [f"sibling : {sm or '?'} ({sp or '?'})"]
    if mom.get("model") and mom.get("provider"):
        lines.append(f"mom     : {mom['model']} ({mom['provider']}), calls today {spent.get('mom', 0)} / {mom.get('max_per_day', '?')}, "
                     f"max {mom.get('max_per_card', '?')} per card")
    else:
        lines.append("mom     : off")
    boards = adv.get("boards") or []
    lines.append(f"advisor : {'on' if adv.get('enabled') else 'OFF'}, boards: {', '.join(boards) or '(none)'}")
    if adv.get("enabled") and not boards:
        lines.append("warning : advisor is on but no board is opted in, nothing is sent (run: hermes calling-mom setup)")
    if errors and errors[0]:
        lines.append(f"errors  : {errors[0]} advisor call(s) failed today, last: {errors[1]}. Run: hermes calling-mom doctor")
    lines.append(f"spend   : ${spent.get('usd', 0):.4f} / ${adv.get('daily_usd_cap', '?')}, "
                 f"{spent.get('tokens', 0)} / {adv.get('daily_token_cap', '?')} tokens today (sibling only)")
    return lines


# ---------------------------------------------------------------- doctor

def version_ok(version) -> bool:
    m = re.match(r"\s*v?(\d+)\.(\d+)(?:\.(\d+))?", str(version or ""))
    return bool(m) and tuple(int(x or 0) for x in m.groups()) >= MIN_HERMES


def find_sweeps(homes) -> list:
    """``[(home, job id, state)]`` of every ``calling-mom-sweep`` cron job in the given Hermes homes.
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
        "" if internals else "kanban_db / kanban_db_connect not importable: this Hermes is not supported")
    sp, sm = sibling_pair(cm)
    sibling_ok = bool(sp and sm)
    add("sibling pair", "ok" if sibling_ok else "fail",
        f"{sm} ({sp})" if sibling_ok else "model AND provider are needed (run: hermes calling-mom setup)")
    mom = cm.get("mom") or {}
    mm, mp = mom.get("model") or "", mom.get("provider") or ""
    mom_on = bool(mm and mp)
    add("mom pair", "ok" if (mom_on or not (mm or mp)) else "fail",
        f"{mm} ({mp})" if mom_on else ("off" if not (mm or mp) else "model AND provider are needed"))
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
        add("advisor", "warn", "off or no board opted in: nothing is sent to the sibling or mom")
    if sweeps is not None:
        add("sweep cron", *_sweep_check(sweeps))
    if static_ok:
        tiers = [("sibling call", sp, sm)] + ([("mom call", mp, mm)] if mom_on else [])
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


def _blocks(env):
    cfg = (env.effective or env.load)()
    return (cfg.get("calling_mom") or {}), (_get(cfg, "plugins.entries.calling-mom.llm", {}) or {})


def cmd_setup(env, preset=None, yes=False) -> int:
    cfg = env.load()
    upd, summary = setup(cfg, env.providers(), env.boards(), env.ask, env.say, preset=preset)
    env.say("\nThis will be written to your Hermes config:")
    for line in summary:
        env.say("  " + line)
    env.say("  (and the matching plugins.entries.calling-mom.llm allowlist; existing entries stay)")
    if not yes and env.ask("Write it? [y/N] > ").strip().lower() not in ("y", "yes"):
        env.say("Nothing written.")
        return 1
    env.save(apply_updates(cfg, upd))
    env.say("Config written.")
    existing = env.sweeps()
    if existing:  # jobs are per Hermes home: look everywhere, never create a second one
        env.say("A sweep cron job already exists: " + ", ".join(f"{i} ({h}, {st})" for h, i, st in existing)
                + ". Not creating another.")
    elif yes or env.ask("Create the 1-minute sweep cron job (catches crash-parked cards, pushes metrics)? [Y/n] > ").strip().lower() in ("", "y", "yes"):
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

def make_env(spent, effective, log_path, ask=input, say=print):  # pragma: no cover - needs a live Hermes
    from hermes_cli import config as hc

    def load():
        return hc.load_config()

    def providers():
        try:
            from hermes_cli.model_switch_providers import list_authenticated_providers
            cfg = hc.load_config()
            model = cfg.get("model") if isinstance(cfg.get("model"), dict) else {}
            rows = list_authenticated_providers(current_provider=model.get("provider", ""),
                                                user_providers=cfg.get("providers"), max_models=40)
            return [{"slug": r["slug"], "name": r.get("name") or r["slug"], "models": list(r.get("models") or [])} for r in rows]
        except Exception:
            return []  # setup then asks you to type the pair

    def boards():
        try:
            from hermes_cli.kanban_db_boards import list_boards
            return [b.get("slug") or b.get("name") for b in list_boards(include_archived=False)]
        except Exception:
            return []

    def probe(provider, model):
        from agent.plugin_llm import PluginLlm
        PluginLlm(plugin_id="calling-mom").complete(
            [{"role": "user", "content": "Reply with the single word OK."}], provider=provider, model=model,
            max_tokens=16, timeout=60, purpose="calling-mom-doctor")

    try:
        from hermes_cli import __version__ as version
        import hermes_cli.kanban_db, hermes_cli.kanban_db_connect  # noqa: F401
        internals = True
    except Exception:
        version, internals = getattr(sys.modules.get("hermes_cli"), "__version__", "?"), False

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

    return Env(load=load, save=hc.save_config, providers=providers, boards=boards, spent=spent, probe=probe,
               version=str(version), internals=internals, ask=ask, say=say, cron=cron, sweeps=sweeps,
               effective=lambda: {**hc.load_config(), "calling_mom": effective()},
               errors=lambda: count_errors(log_path()))


def setup_parser(parser):  # pragma: no cover - argparse glue
    sub = parser.add_subparsers(dest="cm_cmd")
    s = sub.add_parser("setup", help="choose sibling/mom from your configured providers and grant them")
    s.add_argument("--preset", choices=sorted(PRESETS), help="skip the model questions")
    s.add_argument("--yes", action="store_true", help="do not ask before writing")
    sub.add_parser("status", help="show the effective setup and today's usage")
    sub.add_parser("doctor", help="check the setup and make one real call to each model")
    parser.set_defaults(cm_cmd=None)


def dispatch(args, spent, effective, log_path):  # pragma: no cover - argparse glue
    env = make_env(spent, effective, log_path)
    cmd = getattr(args, "cm_cmd", None) or "status"
    if cmd == "setup":
        return cmd_setup(env, preset=args.preset, yes=args.yes)
    return {"status": cmd_status, "doctor": cmd_doctor}[cmd](env)
