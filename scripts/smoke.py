"""Post-deploy / post-upgrade smoke test.

Answers three questions after a deploy or a Hermes image update:
  1. does the plugin still validate?
  2. is the hook registered in each profile that loads it?
  3. is the release gate green (tests, coverage, copy drift)?

    /opt/hermes/.venv/bin/python scripts/smoke.py     # exit 1 when anything fails
"""
import json
import os
import subprocess
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
PY = os.environ.get("CALLING_MOM_PYTHON", "/opt/hermes/.venv/bin/python")
HOME_DIR = Path("/opt/hermes")
HERMES = str(HOME_DIR / ".venv" / "bin" / "hermes")
HOOK = "on_kanban_worker_exited"
PROFILES = {"default": "/opt/data", "daidalos": "/opt/data/profiles/daidalos"}


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def check(ok, label, detail=""):
    print("%-4s %s%s" % ("PASS" if ok else "FAIL", label, ("  — " + detail) if detail and not ok else ""))
    return ok


def probe(home):
    code = ("import json;from hermes_cli import plugins;"
            "print(json.dumps({h: plugins.has_hook(h) for h in ['%s','kanban_task_claimed']}))" % HOOK)
    proc = run([PY, "-c", code], cwd=home, env=dict(os.environ, HERMES_HOME=home))
    if proc.returncode:
        return None, (proc.stderr or proc.stdout).strip().splitlines()[-1:]
    return json.loads(proc.stdout.strip().splitlines()[-1]), ""


def main():
    ok = True
    proc = run([PY, str(PLUGIN / "check.py")], cwd=str(PLUGIN))
    ok &= check(proc.returncode == 0, "release gate (tests + coverage + drift)", (proc.stdout or "")[-400:])

    for name, home in PROFILES.items():
        plugin_dir = Path(home) / "plugins" / "calling-mom"
        if not plugin_dir.exists():
            print("SKIP %-26s not installed in profile %s" % ("validate", name))
            continue
        proc = run([HERMES, "plugins", "validate", str(plugin_dir)], cwd=home,
                   env=dict(os.environ, HERMES_HOME=home))
        ok &= check("Validation passed" in (proc.stdout or ""), "validate in profile %s" % name,
                    (proc.stdout or proc.stderr or "")[-300:])

        hooks, err = probe(home)
        if hooks is None:
            ok &= check(False, "hook registration in profile %s" % name, str(err))
        else:
            ok &= check(hooks.get(HOOK) is True, "hook %s registered in profile %s" % (HOOK, name))

    print("smoke: %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
