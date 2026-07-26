#!/usr/bin/env bash
# Deploy the verified GitHub main branch into a configured Linux checkout.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${AZIMUTH_REPO:-$(dirname "$SCRIPT_DIR")}"
VENV="${AZIMUTH_VENV:-$ROOT/.venv}"
SERVICE="${AZIMUTH_SERVICE:-azimuth-photo.service}"
HEALTH_URL="${AZIMUTH_HEALTH_URL:-http://127.0.0.1:8000/api/dev/status}"
BACKUP_URL="${AZIMUTH_BACKUP_URL:-http://127.0.0.1:8000/api/system/backup/now}"
TEST_LOG="${AZIMUTH_DEPLOY_TEST_LOG:-/tmp/azimuth-photo-deploy-tests.log}"

cd "$ROOT"

if [[ "$(git branch --show-current)" != "main" ]]; then
    echo "Deploy refused: $ROOT must be on main." >&2
    exit 1
fi
if [[ -n "$(git status --porcelain)" ]]; then
    echo "Deploy refused: $ROOT has uncommitted changes." >&2
    exit 1
fi
if [[ ! -x "$VENV/bin/python" ]]; then
    echo "Deploy refused: missing managed runtime at $VENV." >&2
    exit 1
fi

git fetch --quiet origin main
git merge --ff-only origin/main

echo "Running isolated verification..."
(
    cd "$ROOT/web"
    AZIMUTH_SMOKE_MODE=1 \
    PYTHONPYCACHEPREFIX="${XDG_CACHE_HOME:-${TMPDIR:-/tmp}}/azimuth-photo/test-pycache" \
    "$VENV/bin/python" -m pytest -q
) >"$TEST_LOG" 2>&1 || {
    tail -n 80 "$TEST_LOG"
    echo "Deploy refused: test suite failed." >&2
    exit 1
}
tail -n 2 "$TEST_LOG"

echo "Creating a verified catalog snapshot through the live service..."
curl --fail --silent --show-error --max-time 3600 \
    --request POST "$BACKUP_URL" >/tmp/azimuth-photo-deploy-backup.json

sudo -n systemctl restart "$SERVICE"
for attempt in $(seq 1 60); do
    if curl --fail --silent --max-time 2 "$HEALTH_URL" >/dev/null; then
        echo "DEPLOYED $(git log --oneline -1)"
        exit 0
    fi
    sleep 2
done

sudo -n systemctl status "$SERVICE" --no-pager || true
echo "Deploy failed: $SERVICE did not become healthy at $HEALTH_URL." >&2
exit 1
