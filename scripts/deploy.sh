#!/usr/bin/env bash
# Maintainer helper: copy this checkout to the directories that load the plugin, gate, smoke-test.
#
#   scripts/deploy.sh              # sync copies + gate + smoke
#   scripts/deploy.sh --restart    # also run $CALLING_MOM_RESTART (e.g. a gateway restart command)
#
# Targets come from the environment or from scripts/.deploy.env (git-ignored):
#   CALLING_MOM_TARGETS="/path/one/plugins/calling-mom /path/two/plugins/calling-mom"
#   CALLING_MOM_DEPLOYED=/path/two/plugins/calling-mom   # copy that check.py compares byte for byte
#   CALLING_MOM_PYTHON=/path/to/hermes/venv/bin/python
#   CALLING_MOM_RESTART="systemctl --user restart hermes-gateway"   # optional
#   CALLING_MOM_PROFILES="name=/hermes/home,other=/other/home"      # profiles the smoke test inspects
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
if [ -f "$SRC/scripts/.deploy.env" ]; then set -a; . "$SRC/scripts/.deploy.env"; set +a; fi
: "${CALLING_MOM_TARGETS:?set CALLING_MOM_TARGETS (see the header of this script)}"
PY="${CALLING_MOM_PYTHON:-python3}"
FILES="__init__.py _core.py test_core.py cli.py rules.py test_rules.py metrics.py signals.py plugin.yaml README.md CHANGELOG.md LICENSE test_signals.py test_calling_mom.py test_metrics.py test_matrix.py test_cli.py linecov.py check.py"

first=""
for dst in $CALLING_MOM_TARGETS; do
    [ -d "$dst" ] || { echo "skip (absent): $dst"; continue; }
    for f in $FILES; do cp "$SRC/$f" "$dst/$f"; done
    mkdir -p "$dst/scripts"
    cp "$SRC"/scripts/*.py "$dst/scripts/"
    echo "synced: $dst"
    first="${first:-$dst}"
done
[ -n "$first" ] || { echo "no target directory exists"; exit 1; }

"$PY" "$first/check.py"

if [ "${1:-}" = "--restart" ] && [ -n "${CALLING_MOM_RESTART:-}" ]; then
    sh -c "$CALLING_MOM_RESTART" || echo "WARN: restart command failed"
    sleep 20
fi

"$PY" "$first/scripts/smoke.py"
