#!/bin/sh
# systemd sleep hook: park the array as the machine goes down, spin it back up
# on resume. Install to /etc/systemd/system-sleep/barracuda (root, +x).
#
# Runs as ROOT, while the daemon is a user process - hence the discovery loop
# over home directories rather than assuming a user.
#
# Every path here must fail fast. A hook that blocks will hang suspend, and a
# laptop that will not sleep is a much worse bug than a missing sound effect.

COAST=4          # seconds of audible coast before sleep. Full coast is 8.5s;
                 # 4 gets the park clunk and the start of the wind-down without
                 # noticeably delaying the lid close.

case "$1/$2" in
  pre/*)  CMD=spindown ;;
  post/*) CMD=spinup   ;;
  *)      exit 0       ;;
esac

for pidf in /home/*/.barracudad.pid; do
  [ -e "$pidf" ] || continue
  home=$(dirname "$pidf")
  pid=$(cat "$pidf" 2>/dev/null) || continue
  kill -0 "$pid" 2>/dev/null || continue       # stale pidfile, skip

  ctl="$home/.barracudad.ctl"
  if [ -p "$ctl" ]; then
    # Bounded: a FIFO with no reader would block forever.
    timeout 2 sh -c "echo $CMD > '$ctl'" 2>/dev/null || \
      { [ "$CMD" = spindown ] && kill -USR1 "$pid" || kill -USR2 "$pid"; }
  else
    [ "$CMD" = spindown ] && kill -USR1 "$pid" || kill -USR2 "$pid"
  fi

  [ "$1" = "pre" ] && sleep "$COAST"
done
exit 0
