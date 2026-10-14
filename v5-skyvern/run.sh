#!/bin/zsh
# Runs the bench records through local Skyvern one at a time (starts the server first if it is not up).
#   ./run.sh [--limit N] [--only INV-26-xxxx] [--block task|task_v2] [--run-with code|agent] [--fresh-workflow]
HERE="${0:A:h}"
cd "$HERE"
"$HERE/start_server.sh" || exit 1
exec "$HERE/.venv/bin/python" "$HERE/run.py" "$@"
