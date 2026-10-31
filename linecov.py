"""Line coverage for the calling-mom plugin using only the stdlib.

Runs the unittest suite under sys.settrace and reports, per plugin file, which
executable lines were never reached. No third-party dependency.

    /opt/hermes/.venv/bin/python linecov.py [test modules ...]
"""
import ast
import dis
import io
import os
import sys
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent  # measure the copy this file lives in

targets = {PLUGIN / "signals.py", PLUGIN / "metrics.py", PLUGIN / "cli.py", PLUGIN / "rules.py", PLUGIN / "__init__.py"}
hit = {p: set() for p in targets}


def executable_lines(path):
    src = path.read_text()
    code = compile(src, str(path), "exec")
    lines = set()
    stack = [code]
    while stack:
        c = stack.pop()
        for _, _, ln in c.co_lines() if hasattr(c, "co_lines") else []:
            if ln:
                lines.add(ln)
        for const in c.co_consts:
            if hasattr(const, "co_code"):
                stack.append(const)
    # `# pragma: no cover` on an `except` or a `def` line drops the whole handler / function
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.ExceptHandler, ast.FunctionDef)) and "pragma: no cover" in src.splitlines()[node.lineno - 1]:
            lines -= set(range(node.lineno, node.end_lineno + 1))
    return lines


NAMES = {p.name: p for p in targets}


_target_of = {}  # filename -> our target file, or None


def _target(fn):
    if fn not in _target_of:
        t = NAMES.get(os.path.basename(fn))
        # same basename in another package (hermes_cli/__init__.py) must not count as ours
        _target_of[fn] = t if t is not None and os.path.dirname(os.path.realpath(fn)) == str(PLUGIN) else None
    return _target_of[fn]


def tracer(frame, event, arg):
    t = _target(frame.f_code.co_filename)
    if t is None:
        return None  # not our file: no line events for this frame (this is what keeps the run fast)
    if event == "line":
        hit[t].add(frame.f_lineno)
    return tracer


def measure(mods):
    """Run the suite under the tracer; return (covered, total, missing) line counts."""
    sys.path.insert(0, str(PLUGIN))
    os.chdir(PLUGIN)
    sys.settrace(tracer)
    try:
        suite = unittest.defaultTestLoader.loadTestsFromNames(mods)
        buf = io.StringIO()
        res = unittest.TextTestRunner(stream=buf, verbosity=0).run(suite)
    finally:
        sys.settrace(None)
    failed = len(res.failures) + len(res.errors)
    print("ran", res.testsRun, "tests | failures", len(res.failures), "| errors", len(res.errors))
    if failed:
        print(buf.getvalue())
    total = covered = missing = 0
    for p in sorted(targets):
        ex = executable_lines(p)
        miss = sorted(ex - hit[p])
        total += len(ex)
        covered += len(ex & hit[p])
        missing += len(miss)
        print("\n%s: %d/%d lines (%.1f%%)" % (p.name, len(ex & hit[p]), len(ex), 100.0 * len(ex & hit[p]) / max(1, len(ex))))
        if miss:
            src = p.read_text().splitlines()
            for ln in miss:
                print("  %4d| %s" % (ln, src[ln - 1].strip()[:100]))
    print("\nTOTAL %d/%d (%.1f%%)" % (covered, total, 100.0 * covered / max(1, total)))
    return covered, total, missing, failed


def main(mods):
    covered, total, missing, failed = measure(mods)
    return 0 if missing == 0 and not failed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["test_signals", "test_calling_mom", "test_metrics", "test_matrix", "test_cli", "test_rules", "test_core"]))
