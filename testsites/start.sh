#!/usr/bin/env bash
# Starts (or stops) the four dependency-free test sites on ports 5180 to 5183.
cd "$(dirname "$0")" || exit 1
if [ "$1" = "stop" ]; then
  for site in ap-desk returns-desk expense-desk claims-desk; do
    [ -f "$site/.pid" ] && kill "$(cat "$site/.pid")" 2>/dev/null && rm -f "$site/.pid"
  done
  echo "stopped"; exit 0
fi
for site in ap-desk returns-desk expense-desk claims-desk; do
  cd "$site" || exit 1
  nohup node server.js > .server.log 2>&1 &
  echo $! > .pid
  cd ..
done
echo "AP Desk http://localhost:5180, Returns Desk :5181, Expense Desk :5182, Claims Desk :5183"
