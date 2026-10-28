#!/usr/bin/env bash
# Deploy this checkout to every directory that loads the plugin, then verify.
#
#   scripts/deploy.sh              # sync copies + gate + smoke
#   scripts/deploy.sh --restart    # also restart the gateway so it reloads the plugins
#
# Both target dirs are on the /opt/data volume: an image pull does not remove them.
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
CANON="${CALLING_MOM_CANONICAL:-/opt/data/profiles/daidalos/plugins/calling-mom}"
DEPLOYED="${CALLING_MOM_DEPLOYED:-/opt/data/plugins/calling-mom}"
PY="${CALLING_MOM_PYTHON:-/opt/hermes/.venv/bin/python}"
FILES="__init__.py metrics.py signals.py plugin.yaml README.md CHANGELOG.md LICENSE test_signals.py test_calling_mom.py test_metrics.py test_matrix.py linecov.py check.py"

for dst in "$CANON" "$DEPLOYED"; do
    [ -d "$dst" ] || { echo "skip (absent): $dst"; continue; }
    for f in $FILES; do cp "$SRC/$f" "$dst/$f"; done
    mkdir -p "$dst/scripts"
    cp "$SRC"/scripts/*.py "$dst/scripts/"
    echo "synced: $dst"
done

"$PY" "$CANON/check.py"

if [ "${1:-}" = "--restart" ]; then
    /command/s6-svc -r /run/service/gateway-default || echo "WARN: gateway restart failed (s6-svc)"
    sleep 20
fi

"$PY" "$CANON/scripts/smoke.py"
