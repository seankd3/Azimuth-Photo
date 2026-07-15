#!/usr/bin/env bash
# Deterministic desktop end-to-end gate. Never targets a long-lived server or catalog.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
export TMPDIR="${TMPDIR:-${TEMP:-${TMP:-/tmp}}}"
export PHOTOARCHIVE_QA_SCRATCH="${PHOTOARCHIVE_QA_SCRATCH:-$TMPDIR/azimuth/qa-harness}"
mkdir -p "$TMPDIR" "$PHOTOARCHIVE_QA_SCRATCH"

cd "$ROOT/web"
if [[ -x .venv/bin/python ]]; then
  PYTHON=.venv/bin/python
elif [[ -x .venv/Scripts/python.exe ]]; then
  PYTHON=.venv/Scripts/python.exe
else
  echo "Azimuth Photo QA requires web/.venv (run scripts/setup first)." >&2
  exit 1
fi
exec "$PYTHON" -m qa.run "$@"
