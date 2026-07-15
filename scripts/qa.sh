#!/usr/bin/env bash
# Deterministic desktop end-to-end gate. Never targets a long-lived server or catalog.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
PYTHON="$ROOT/web/.venv/bin/python"
if [ -x "$ROOT/web/.venv/Scripts/python.exe" ]; then
  PYTHON="$ROOT/web/.venv/Scripts/python.exe"
fi

if [ ! -x "$PYTHON" ]; then
  echo "QA requires a virtualenv at web/.venv" >&2
  exit 2
fi

cd "$ROOT/web"
exec "$PYTHON" -m qa.run "$@"
