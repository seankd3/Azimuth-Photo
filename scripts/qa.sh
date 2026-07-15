#!/usr/bin/env bash
# Deterministic desktop end-to-end gate. Never targets a long-lived server or catalog.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
export TMPDIR="${TMPDIR:-/mnt/expansion/tmp}"
export PHOTOARCHIVE_QA_SCRATCH="${PHOTOARCHIVE_QA_SCRATCH:-/mnt/expansion/tmp/az1/qa-harness}"
mkdir -p "$TMPDIR" "$PHOTOARCHIVE_QA_SCRATCH"

cd "$ROOT/web"
exec .venv/bin/python -m qa.run "$@"
