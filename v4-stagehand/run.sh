#!/usr/bin/env bash
# Stagehand contender runner. All args go to run.ts (see its header or README.md).
#   ./run.sh --clear-cache            # the comparison run: 5 records, one at a time, fresh agent cache
#   ./run.sh --check                  # wiring check only (~3 model calls, decides nothing)
set -euo pipefail
cd "$(dirname "$0")"
[ -d node_modules/@browserbasehq/stagehand ] || npm install
if [ "${1:-}" = "--check" ]; then shift; exec npx tsx check.ts "$@"; fi
exec npx tsx run.ts "$@"
