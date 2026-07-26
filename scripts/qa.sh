#!/usr/bin/env bash
# Deterministic desktop end-to-end gate. Never targets a long-lived server or catalog.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
export TMPDIR="${TMPDIR:-${TEMP:-${TMP:-/tmp}}}"
export AZIMUTH_QA_SCRATCH="${AZIMUTH_QA_SCRATCH:-$TMPDIR/azimuth/qa-harness}"
mkdir -p "$TMPDIR" "$AZIMUTH_QA_SCRATCH"

cd "$ROOT/web"
if [[ -n "${AZIMUTH_VENV:-}" && -x "$AZIMUTH_VENV/bin/python" ]]; then
  PYTHON="$AZIMUTH_VENV/bin/python"
elif [[ -x .venv/bin/python ]]; then
  PYTHON=.venv/bin/python
elif [[ -x .venv/Scripts/python.exe ]]; then
  PYTHON=.venv/Scripts/python.exe
else
  echo "Azimuth Photo QA requires AZIMUTH_VENV or web/.venv (run scripts/setup first)." >&2
  exit 1
fi
exec "$PYTHON" -m qa.run "$@"
