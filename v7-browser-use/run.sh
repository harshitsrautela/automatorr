#!/bin/sh
# Runs the plain browser-use control on the bench records; output is also saved to results/<UTC time>.log.
# Pass-through flags: --limit N, --only INV-..., --max-steps N, --vision-retry, --check-llm
cd "$(dirname "$0")" || exit 1
mkdir -p results
./.venv/bin/python -u run.py "$@" 2>&1 | tee "results/$(date -u +%Y%m%dT%H%M%SZ).log"
