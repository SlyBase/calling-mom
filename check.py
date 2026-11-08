"""One gate for the calling-mom plugin: tests, coverage threshold, copy drift.

    python check.py            # gate: exit 1 on any failure
    python check.py --sync     # make the deployed copy match
    python check.py --min 95   # relax the coverage threshold

Stdlib only. ``CALLING_MOM_DEPLOYED`` (optional) names an installed copy that must stay byte-identical
to this checkout; without it the drift check is skipped.
"""
import argparse
import contextlib
import filecmp
import io
import os
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CANONICAL = HERE
DEPLOYED = Path(os.environ["CALLING_MOM_DEPLOYED"]) if os.environ.get("CALLING_MOM_DEPLOYED") else None
MODULES = ["test_signals", "test_calling_mom", "test_metrics", "test_matrix", "test_cli", "test_rules", "test_core", "test_dashboard", "test_report", "test_precedents", "test_demo",
           "test_touches", "test_feedback", "test_count", "test_quota", "test_quota_flow", "test_notify"]
PAYLOAD = ["__init__.py", "_core.py", "tests/test_core.py", "cli.py", "rules.py", "tests/test_rules.py", "metrics.py", "signals.py", "plugin.yaml",
           "tests/test_signals.py", "tests/test_calling_mom.py", "tests/test_metrics.py", "tests/test_matrix.py", "tests/test_cli.py",
           "linecov.py", "check.py", "README.md", "CHANGELOG.md", "scripts/sweep.py", "scripts/demo.py", "tests/test_demo.py",
           "tests/test_dashboard.py", "tests/test_report.py", "report.py", "precedents.py", "tests/test_precedents.py", "dashboard/plugin_api.py", "dashboard/manifest.json", "desktop/plugin.js",
           "touches.py", "tests/test_touches.py", "feedback.py", "tests/test_feedback.py", "tests/test_count.py", "quota.py", "tests/test_quota.py", "tests/test_quota_flow.py", "tests/test_notify.py"]


def measure(minimum):
    """Run the suite ONCE, under the line tracer: it reports the failures and the coverage."""
    sys.path.insert(0, str(HERE))
    sys.path.insert(0, str(HERE / "tests"))
    import linecov  # noqa: E402  (local, stdlib only)

    out = io.StringIO()
    with contextlib.redirect_stdout(out):  # detail lives in linecov.py
        covered, total, missing, failed = linecov.measure(MODULES)
    ran = re.search(r"ran (\d+) tests", out.getvalue())
    print("tests      : %s (%s tests)" % ("FAILED" if failed else "OK", ran.group(1) if ran else "?"))
    if failed:
        print(out.getvalue()[-4000:])
    pct = 100.0 * covered / max(1, total)
    print("coverage   : %d/%d lines (%.1f%%), missing %d, threshold %.0f%%"
          % (covered, total, pct, missing, minimum))
    return not failed and pct >= minimum


def compare(sync):
    if DEPLOYED is None or not DEPLOYED.exists():
        print("drift      : skipped (set CALLING_MOM_DEPLOYED to compare an installed copy)")
        return True
    drift = [name for name in PAYLOAD
             if not (DEPLOYED / name).exists()
             or not filecmp.cmp(CANONICAL / name, DEPLOYED / name, shallow=False)]
    if not drift:
        print("drift      : none (both copies identical)")
        return True
    if sync:
        for name in drift:
            (DEPLOYED / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(CANONICAL / name, DEPLOYED / name)
        print("drift      : synced %d file(s) -> %s" % (len(drift), DEPLOYED))
        return True
    print("drift      : %d file(s) differ: %s" % (len(drift), ", ".join(drift)))
    print("             fix with: python check.py --sync")
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min", type=float, default=100.0, help="minimum line coverage (default 100)")
    ap.add_argument("--sync", action="store_true", help="copy the canonical files to the deployed copy")
    args = ap.parse_args()

    ok = measure(args.min)
    ok = compare(args.sync) and ok
    print("gate       : %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
