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

# Write a command to the control fifo, or fail so the caller can fall back to a
# signal. The [ -p ] test is the whole point: if the fifo is MISSING, `echo x >
# "$CTL"` creates a regular FILE and succeeds, so the `||` fallback never fires
# and the command vanishes silently. A fifo goes missing whenever a daemon exits
# uncleanly, or when a second instance removes it on its way out.
ctl() { [ -p "$CTL" ] && echo "$1" > "$CTL" 2>/dev/null; }

# The daemon loads a multi-megabyte sample and opens an audio device before it
# writes its pid file. That takes longer than the half second this script used
# to wait, so a perfectly healthy start printed "failed to start" - which sends
# you reading the launcher when the daemon is either fine or dying for its own
# reasons. Poll instead of guessing, and return the moment it is up.
wait_started() { for _ in $(seq 1 40); do running && return 0; sleep 0.25; done; return 1; }

case "${1:-toggle}" in
  start)
    running && { echo "already running"; exit 0; }
    setsid python3 "$HERE/barracudad.py" ${2:+--preset "$2"} >/dev/null 2>&1 &
    wait_started && echo "started" || echo "failed to start (run barracudad.py directly to see why)" ;;
  stop)
    running || { echo "not running"; exit 0; }
    # SIGTERM, not SIGINT. A daemon started as a background job from a shell
    # without job control inherits SIGINT = SIG_IGN (POSIX), and CPython keeps
    # an inherited ignore rather than installing its own handler - so `kill
    # -INT` was a silent no-op and stop did nothing at all. barracudad catches
    # SIGTERM explicitly and routes it through the same shutdown path.
    kill -TERM "$(cat "$PID")"; echo "spinning down and exiting" ;;
  toggle)
    if running; then
      # Park or restart in place - the daemon keeps running either way.
      if [ -e "$CTL" ]; then
        state=$(ctl status; echo ok)
        ctl spindown || kill -USR1 "$(cat "$PID")"
        echo "parked (run 'toggle' again... use spinup to restart)"
      else
        kill -USR1 "$(cat "$PID")"; echo "parked"
      fi
    else
      setsid python3 "$HERE/barracudad.py" --preset subtle >/dev/null 2>&1 &
      wait_started && echo "started (subtle)" || echo "failed to start (run barracudad.py directly to see why)"
    fi ;;
  spinup)   running && { ctl spinup   || kill -USR2 "$(cat "$PID")"; echo "spinning up"; } ;;
  spindown) running && { ctl spindown || kill -USR1 "$(cat "$PID")"; echo "spinning down"; } ;;
  status)
    if running; then echo "running (pid $(cat "$PID"))"; ctl status
    else echo "not running"; fi ;;
  *) echo "usage: $0 {toggle|start [preset]|stop|spinup|spindown|status}"; exit 1 ;;
esac
