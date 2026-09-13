#!/usr/bin/env bash
# Check what sea-cuda needs, and optionally install the systemd user service.
#
#   ./setup.sh                 report only, change nothing
#   ./setup.sh --install       install and enable the service
#   ./setup.sh --install subtle    ... with a preset
#
# Why this exists rather than just install steps in the README: two of the
# daemon's dependencies degrade QUIETLY. Without pw-play it falls back to aplay
# and the independent-volume feature stops working; without gdbus or
# systemd-inhibit the entire suspend behaviour disables itself. Both only warn
# on stderr - and once the daemon runs as a systemd service, stderr is the
# journal, which nobody reads until something is already wrong. This says it
# once, out loud, before you start depending on it.
set -u

HERE=$(cd -- "$(dirname -- "$0")" && pwd -P)
fatal=0 degraded=0

if [ -t 1 ]; then R=$'\e[31m'; Y=$'\e[33m'; G=$'\e[32m'; D=$'\e[2m'; N=$'\e[0m'
else R=''; Y=''; G=''; D=''; N=''; fi

# report LEVEL NAME DETAIL
#   need    - the daemon will not run at all
#   feature - the daemon runs, but something silently stops working
#   extra   - unrelated to the daemon
report() {
    case "$1" in
        ok)      printf '  %-20s %sok%s       %s%s%s\n'      "$2" "$G" "$N" "$D" "${3:-}" "$N" ;;
        need)    printf '  %-20s %sMISSING%s  %s\n'          "$2" "$R" "$N" "${3:-}"; fatal=$((fatal+1)) ;;
        feature) printf '  %-20s %sMISSING%s  %s\n'          "$2" "$Y" "$N" "${3:-}"; degraded=$((degraded+1)) ;;
        extra)   printf '  %-20s %smissing%s  %s%s%s\n'      "$2" "$D" "$N" "$D" "${3:-}" "$N" ;;
    esac
}

have() { command -v "$1" >/dev/null 2>&1; }
pymod() { python3 -c "import $1" >/dev/null 2>&1; }

echo "sea-cuda preflight"
echo

# ---- required ------------------------------------------------------------
if have python3; then
    pyv=$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null)
    # 3.11 is the floor: tomllib is stdlib from 3.11, and the config is TOML.
    if python3 -c 'import sys;sys.exit(0 if sys.version_info>=(3,11) else 1)' 2>/dev/null
    then report ok   "python3" "$pyv"
    else report need "python3" "found $pyv, need 3.11+ (tomllib reads config.toml)"; fi
else
    report need "python3" "not installed"
fi
pymod numpy && report ok "numpy" || report need "numpy" "the synthesis engine is numpy"

# ---- features that fail quietly -----------------------------------------
if have pw-play; then
    report ok "pw-play"
elif have aplay; then
    report feature "pw-play" "falls back to aplay; independent volume stops working"
else
    report need "pw-play" "no audio sink at all (aplay absent too)"
fi
have pactl        && report ok "pactl"          || report feature "pactl"          "independent volume stops working"
have gdbus        && report ok "gdbus"          "suspend handling" || report feature "gdbus"          "NO suspend handling: the array will not park before sleep"
have systemd-inhibit && report ok "systemd-inhibit" "suspend handling" || report feature "systemd-inhibit" "NO suspend handling: the array will not park before sleep"

# ---- environment ---------------------------------------------------------
if [ -d /proc ] && [ -r /proc/diskstats ]; then report ok "/proc/diskstats"
else report need "/proc/diskstats" "disk activity is read from here; Linux only"; fi
if systemctl --user show-environment >/dev/null 2>&1; then report ok "systemd --user"
else report feature "systemd --user" "no user session; the service cannot be installed"; fi

# ---- optional ------------------------------------------------------------
pymod scipy && report ok "scipy" "analysis only" || report extra "scipy" "analyse_drive.py only; the daemon does not use it"

echo
if [ "$fatal" -gt 0 ]; then
    printf '%s%d required item(s) missing - the daemon will not run.%s\n' "$R" "$fatal" "$N"
elif [ "$degraded" -gt 0 ]; then
    printf '%s%d feature(s) will silently do nothing.%s See the table in README.md.\n' "$Y" "$degraded" "$N"
else
    printf '%sEverything present.%s\n' "$G" "$N"
fi

# ---- install -------------------------------------------------------------
if [ "${1:-}" != "--install" ]; then
    [ "${1:-}" = "" ] || { echo; echo "unknown argument: ${1}"; echo "usage: $0 [--install [PRESET]]"; exit 2; }
    echo
    echo "Report only. Re-run with --install to install the systemd user service."
    [ "$fatal" -gt 0 ] && exit 1 || exit 0
fi

echo
[ "$fatal" -gt 0 ] && { printf '%sRefusing to install with required items missing.%s\n' "$R" "$N"; exit 1; }

preset="${2:-}"
unit="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/barracudad.service"
mkdir -p "$(dirname "$unit")"

# ExecStart is written from THIS checkout rather than left for a human to edit.
# It is the only path in the unit and the easiest thing to get wrong.
sed -e "s|^ExecStart=.*|ExecStart=$(command -v python3) $HERE/barracudad.py${preset:+ --preset $preset}|" \
    "$HERE/barracudad.service" > "$unit" || { echo "failed to write $unit"; exit 1; }

echo "installed $unit"
grep '^ExecStart=' "$unit" | sed 's/^/  /'
systemctl --user daemon-reload || exit 1
systemctl --user enable --now barracudad || exit 1
echo
systemctl --user is-active barracudad | sed 's/^/  active:  /'
systemctl --user is-enabled barracudad | sed 's/^/  enabled: /'
echo
echo "  logs:    journalctl --user -u barracudad -f"
echo "  suspend: ~/.barracudad-sleep.log"
echo "  restart: systemctl --user restart barracudad"
