"""Cron entry (no LLM, every minute): escalate cards the crash circuit-breaker parked, then push metrics.

Why a cron job: the dispatcher tick hook does not fire in every gateway setup, and cards the circuit
breaker parks emit no block hook. Silent unless it acted. The Pushgateway push is fail-open.

Install (profile scripts dir, then schedule):
    cp scripts/sweep.py "$HERMES_HOME/scripts/calling_mom_sweep.py"
    hermes cron create "every 1m" --name "calling-mom: sweep - escalate parked cards, push metrics" --no-agent \
        --script calling_mom_sweep.py --interpreter <hermes venv python> --deliver local

Set CALLING_MOM_DIR when the plugin does not live in $HERMES_HOME/plugins/calling-mom.
"""
import importlib.util
import os
import sys
import time
from pathlib import Path

from hermes_constants import get_hermes_home

PLUGIN_DIR = Path(os.environ.get("CALLING_MOM_DIR") or Path(get_hermes_home()) / "plugins" / "calling-mom")


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, PLUGIN_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mod = load("calling_mom_cron", "__init__.py")
for task_id in mod.sweep_all():
    print(f"calling-mom: escalated {task_id}")

cfg = mod.settings()
if cfg["metrics"]["pushgateways"]:
    metrics = load("calling_mom_metrics", "metrics.py")
    boards = {m["slug"]: str(m["db_path"]) for m in mod.kbb.list_boards(include_archived=False)}
    adv = cfg["advisor"]
    text = metrics.render(
        rows=metrics.read_rows(mod._log_path()), spend=mod._spent_today(),
        caps={"usd": adv["daily_usd_cap"], "tokens": adv["daily_token_cap"]},
        boards=metrics.board_counts(boards), now=time.time(),
    )
    if not metrics.push(text, cfg["metrics"]["pushgateways"], instance=cfg["metrics"]["instance"]):
        print("calling-mom: Pushgateway not reachable (metrics not pushed)")
sys.exit(0)
