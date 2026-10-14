#!/bin/bash
# Webwright contender: see README.md. Usage: ./run.sh [--limit N] [--only INV] [--use-saved-script] [--check]
set -euo pipefail
cd "$(dirname "$0")"
exec .venv/bin/python run.py "$@"
