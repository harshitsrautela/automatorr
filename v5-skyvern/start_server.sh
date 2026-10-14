#!/bin/zsh
# Starts the local Skyvern API server (port 8010, SQLite, headless Chromium) in the background, if it is not already up.
#   ./start_server.sh          # start (or confirm running)
#   ./start_server.sh stop     # stop it
# Log: data/server.log   PID: data/server.pid
set -e
HERE="${0:A:h}"
cd "$HERE"
PORT=8010
PIDFILE="$HERE/data/server.pid"
LOG="$HERE/data/server.log"

if [[ "$1" == "stop" ]]; then
  if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    kill "$(cat "$PIDFILE")" && echo "stopped skyvern (pid $(cat "$PIDFILE"))"
  else
    echo "not running (no live pid file)"
  fi
  rm -f "$PIDFILE"
  exit 0
fi

if curl -sf "http://127.0.0.1:$PORT/api/v1/heartbeat" >/dev/null 2>&1; then
  echo "skyvern already up on :$PORT"
  exit 0
fi

mkdir -p data
# Load .env into the process env too: litellm reads GEMINI_API_BASE from os.environ, not from Skyvern's settings.
set -a; source "$HERE/.env"; set +a
export SKYVERN_ENV_FILE="$HERE/.env"
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-$HOME/Library/Caches/ms-playwright}"

nohup "$HERE/.venv/bin/python" "$HERE/server.py" >>"$LOG" 2>&1 &
echo $! >"$PIDFILE"
echo -n "starting skyvern (pid $(cat "$PIDFILE")) "
for i in {1..90}; do
  if curl -sf "http://127.0.0.1:$PORT/api/v1/heartbeat" >/dev/null 2>&1; then
    echo " up on :$PORT"
    exit 0
  fi
  if ! kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo " died; see $LOG"; tail -30 "$LOG"; exit 1
  fi
  echo -n "."; sleep 1
done
echo " timed out; see $LOG"; exit 1
