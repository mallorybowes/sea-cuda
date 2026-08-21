#!/usr/bin/env bash
# Control the array without a panel applet.
#   barracuda-ctl.sh toggle          spin down if running, up if parked
#   barracuda-ctl.sh start [preset]  launch the daemon
#   barracuda-ctl.sh stop            spin down and exit
#   barracuda-ctl.sh status
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
PID=~/.barracudad.pid
CTL=~/.barracudad.ctl
running() { [ -f "$PID" ] && kill -0 "$(cat "$PID")" 2>/dev/null; }

case "${1:-toggle}" in
  start)
    running && { echo "already running"; exit 0; }
    setsid python3 "$HERE/barracudad.py" ${2:+--preset "$2"} >/dev/null 2>&1 &
    sleep 0.5; running && echo "started" || echo "failed to start" ;;
  stop)
    running || { echo "not running"; exit 0; }
    kill -INT "$(cat "$PID")"; echo "spinning down and exiting" ;;
  toggle)
    if running; then
      # Park or restart in place - the daemon keeps running either way.
      if [ -e "$CTL" ]; then
        state=$(echo status > "$CTL" 2>/dev/null; echo ok)
        echo spindown > "$CTL" 2>/dev/null || kill -USR1 "$(cat "$PID")"
        echo "parked (run 'toggle' again... use spinup to restart)"
      else
        kill -USR1 "$(cat "$PID")"; echo "parked"
      fi
    else
      setsid python3 "$HERE/barracudad.py" --preset subtle >/dev/null 2>&1 &
      sleep 0.5; echo "started (subtle)"
    fi ;;
  spinup)   running && { echo spinup > "$CTL" 2>/dev/null || kill -USR2 "$(cat "$PID")"; echo "spinning up"; } ;;
  spindown) running && { echo spindown > "$CTL" 2>/dev/null || kill -USR1 "$(cat "$PID")"; echo "spinning down"; } ;;
  status)
    if running; then echo "running (pid $(cat "$PID"))"; echo status > "$CTL" 2>/dev/null
    else echo "not running"; fi ;;
  *) echo "usage: $0 {toggle|start [preset]|stop|spinup|spindown|status}"; exit 1 ;;
esac
