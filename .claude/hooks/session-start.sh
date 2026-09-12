#!/usr/bin/env bash
# A cloud session sets itself up before the first prompt. Elsewhere, nothing.
set -euo pipefail
[[ "${CLAUDE_CODE_REMOTE:-}" == "true" ]] || exit 0
exec "$CLAUDE_PROJECT_DIR/scripts/cloud-setup"
