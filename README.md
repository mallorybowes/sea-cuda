# sea-cuda

Makes a silent SSD sound like a 1990s SCSI drive array.

> *"If the real thing don't do the trick <br>
>   You better make up something quick"* <br>
> &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; — with apologies to Ann Wilson, who named
> a Seagate product line in 1977 without knowing it.

Not samples. Every sound is synthesised from parameters **measured off real
recordings** of a Seagate ST39173W Barracuda 9LP — 9.1 GB, 7200 rpm, Wide SCSI,
ball bearings. The daemon watches actual disk I/O through `/proc/diskstats`, so
when it chatters, something really is happening.

```
python3 barracudad.py --preset subtle
```

Listen first: [`demo/barracuda-9lp.wav`](demo/barracuda-9lp.wav) — four drives
spinning up staggered, idle, desktop activity, heavy seeking, then a staggered
spin-down. [`demo/coffee-shop.wav`](demo/coffee-shop.wav) is eight drives, at
volume, for when you want to be asked to leave.

## Why synthesise instead of sampling

Because then everything is a parameter. Drive count, spin stagger, listening
distance, seek density and spindle speed are all knobs rather than properties
baked into a recording. A sample loop can only ever replay one array in one
room; this generates any of them.

It also means the spin-up cascade is *correct* rather than approximate — each
spindle ramps independently and the voices are detuned a fraction of a percent
apart, so they beat against each other the way real ones did.

## Measured, not invented

| Parameter | Value | Source |
|---|---|---|
| Spindle | 120 Hz (7200 rpm) | sharp idle peak, with harmonics at 600/720 and **troughs** at the 60/180 Hz mains lines |
| Bearing whine | 583, 1746, 2329, 2911 Hz … | inharmonic — 4.85×, 14.54×, 19.39× the fundamental, so motor tones, not spindle partials |
| Click resonances | 586 – 5203 Hz | isolated actuator clicks, decay ~1.8 ms |
| Seek rate, real use | 17–26 clicks/s | Win98 boot, file browsing, loading a game |
| Seek rate, benchmark | ~123 clicks/s | a seek exerciser — 5× denser than real work, which is why tuning against benchmarks sounds wrong |
| Spin-up | ~6 s, linear | ~1200 rpm/s |
| Spin-down | ~8.5 s, curved coast | free coast, opening with the actuator park clunk |

Full derivation, including the failed attempts, is in
[`docs/measurements.md`](docs/measurements.md).

`analyse_drive.py` is the measurement battery, and it is generic — point it at a
recording of any drive and it will report spindle speed, harmonics, a
mains-hum cross-check, bearing partials, the click impulse response and the
ramp rates.

```
python3 analyse_drive.py recording.wav --spindle-hint 120
```

## Configuration

Everything lives in `config.toml`.

```toml
[drives]
count         = 4              # 1 for a lone drive, 8 for a NAS
spin_up_mode  = "staggered"    # or "together"
rpm           = 7200           # 5400 / 7200 / 10000; the drone follows

[character]
distance      = 1.00     # 0 = ear on the chassis, 1 = across the room
brightness    = 0.42     # click onset level
whine         = 0.30     # bearing tones; 0 = pure spindle drone
rattle        = 0.028    # ball-bearing knock on spin-down

[activity]
clicks_per_io = 0.55     # raise it if your NVMe is too quiet to be satisfying
```

`distance` exists because the first listener said it sounded like her ear was
against the case. That is a listening position, not a defect — so it became a
parameter. Enclosure ring dominates up close and the direct transient dominates
across the room, and the knob interpolates between them.

Presets: `subtle` (one drive, quiet — good on a laptop), `authentic` (the
measured defaults), `coffee_shop` (regrettable).

## Control

```
./barracuda-ctl.sh toggle | start [preset] | stop | spinup | spindown | status

kill -USR1 $(cat ~/.barracudad.pid)      # park the array, daemon keeps running
kill -USR2 $(cat ~/.barracudad.pid)      # spin back up
echo 'drives 8'  > ~/.barracudad.ctl     # also: volume N, spinup, spindown, status
```

`drives N` works live — new spindles start from rest and spin up while the
existing ones keep running.

Three `.desktop` launchers can be dropped in `~/.local/share/applications/` and
pinned to a dock or bound to keys.

### Independent volume

With `independent = true` the daemon holds an **absolute** output level
regardless of the system volume: turn the music up and the array stays where it
is, because it watches the default sink and inversely adjusts its own stream.
It reacts to PipeWire events rather than polling, so the compensation lands with
the fader rather than a second later. Compensation caps at 150 % stream volume,
so below about 23 % system volume it does start getting quieter with everything
else.

### Suspend

Nothing to install. The daemon parks the array as the machine goes down and
spins it up on resume, controlled by `[sleep]` in `config.toml`.

It does this by holding a **systemd delay inhibitor** and watching logind's
`PrepareForSleep`, rather than by dropping a script in `system-sleep/`. A sleep
hook cannot work here: systemd freezes `user.slice` *before* running those
hooks, so a user daemon is already stopped by the time one fires and never
makes a sound. `man systemd-sleep` says so, and points at inhibitor locks
instead. (Distros also disagree about the path — Debian and Ubuntu read
`/etc/systemd/system-sleep`, Arch only `/usr/lib/systemd/system-sleep`, so the
hook silently no-ops on half the world.)

The coast has to fit inside logind's `InhibitDelayMaxSec`, 5 seconds by
default; `coast_s` defaults to 3.0, and is clamped to 3.5 to leave the drain
room. A delay inhibitor can only ever *delay* a suspend, never block it, so a
wedged daemon costs you five seconds at worst.

**Why there is no drain setting.** Audio you have written is not audio that has
been played. Anything still queued when the inhibitor drops gets frozen with
the rest of the session and plays back on resume — which sounds like the array
spinning down just *after* you wake the machine. Four rounds went into
estimating that backlog (pad for the 64 KiB pipe, add the player's declared
latency, measure bytes written against elapsed time, add a constant for the
part below the pipe) and every one of them was arithmetic about a quantity
nothing was reporting.

There is a signal. `pw-play` drains what it has been given and only then exits,
so closing its stdin and waiting for the process to die *is* end of playback —
pipe, player queue and sink included, with nothing to know about any of them.
Measured against known durations, process exit lands 0.047 s after the last
sample, repeatable to a millisecond, both inside the pipe and over it. So the
daemon closes the player and waits, and there is no number here to get wrong.

Two things that are easy to get wrong if you reimplement this: keep writing
during the wait and the pipe is refilled as fast as it drains, so the wait
accomplishes nothing; and resume writing the moment the inhibitor is released
and you refill it again in the window before the machine actually freezes. The
daemon stops writing for both — `draining`, then `parked`.

Suspend events are logged with timings to `~/.barracudad-sleep.log`, which is
the file to look at if a tail ever comes back:

```
suspend signalled, 3.0s coast
drained in 0.733s
inhibitor released, parked
resumed
```

`barracuda-sleep-hook.sh` is kept for systems without logind.

## Start it on login

```
./setup.sh --install          # or: ./setup.sh --install subtle
```

That checks the dependencies first, refuses if anything required is missing,
and writes `ExecStart` from the checkout it is run out of — the one path in the
unit, and the easiest thing to get wrong by hand. By hand instead:

```
cp barracudad.service ~/.config/systemd/user/
$EDITOR ~/.config/systemd/user/barracudad.service   # point ExecStart at your checkout
systemctl --user daemon-reload
systemctl --user enable --now barracudad
```

A **user** service, not a system one: it needs your PipeWire session and holds
a logind sleep inhibitor on your behalf, neither of which exists before you log
in. It is tied to `graphical-session.target`, so it starts with the desktop and
stops with it.

`journalctl --user -u barracudad` is then where the diagnostics live, which
matters most for suspend: the interesting half of a suspend happens after the
terminal is gone.

Two settings in the unit are load-bearing and should not be trimmed:

| setting | why |
| --- | --- |
| `KillMode=mixed` | The default, `control-group`, SIGTERMs every process in the cgroup **including `pw-play`**. The player would die alongside the daemon and the wind-down would go into a dead pipe: no park sound on logout, and a `BrokenPipeError` in the journal. `mixed` signals only the daemon, which then parks the array and waits for the player to drain. |
| `TimeoutStopSec=30` | The park takes about 8.5s of real time to play. Measured stop-to-stopped is ~10s. |

Once it runs under systemd, prefer `systemctl --user restart barracudad` over
`barracuda-ctl.sh start`. The script still works - and the daemon's singleton
lock means a stray `start` refuses cleanly rather than fighting the service -
but systemd will consider the unit inactive while a script-started daemon runs.

## Requirements

Linux, and it reads `/proc/diskstats`. Run `./setup.sh` to check all of this at
once — it reports what is missing and, more usefully, **what will silently stop
working** if it is.

| Need | Without it | Arch | Debian/Ubuntu | Fedora |
| --- | --- | --- | --- | --- |
| Python 3.11+ | will not run — `tomllib` reads `config.toml` | `python` | `python3` | `python3` |
| `numpy` | will not run — the synthesis engine is numpy | `python-numpy` | `python3-numpy` | `python3-numpy` |
| `pw-play` | falls back to `aplay`; **independent volume stops working**, because the ALSA layer ignores the stream name the lookup needs | `pipewire-audio` | `pipewire-bin` | `pipewire-utils` |
| `pactl` | **independent volume stops working** | `libpulse` | `pulseaudio-utils` | `pulseaudio-utils` |
| `gdbus` | **no suspend handling at all** — the array will not park before sleep | `glib2` | `libglib2.0-bin` | `glib2` |
| `systemd-inhibit` | **no suspend handling at all** | `systemd` | `systemd` | `systemd` |
| `scipy` | nothing — `analyse_drive.py` only, the daemon never imports it | `python-scipy` | `python3-scipy` | `python3-scipy` |

Arch column verified against a working install; the others are the usual names
for those tools.

The bolded rows are the reason `setup.sh` exists. Those three failures are
quiet — the daemon starts, makes drive noises, and mentions the problem once on
stderr. Under the systemd service that stderr is the journal, so it is
entirely possible to run this for weeks without noticing the suspend feature
disabled itself on day one.


Desktop-agnostic by design: it is a daemon, not a panel applet, so it runs the
same on GNOME, KDE, COSMIC, i3 or a bare session.

## Status

Working and in daily use. No panel applet yet — a COSMIC one wants Rust and
libcosmic, whose applet API is still moving.

## Notes

The bearing rattle on spin-down is the one part modelled from physics rather
than measured: ball-bearing drives rattle as the lubricating film thins, and
fluid-dynamic bearings replaced them around 2002, so post-2002 recordings simply
do not contain it. It is deliberately subtle, because measurement of a real 9LP
coast shows a smooth taper rather than knocking.

## License

MIT. See [LICENSE](LICENSE).

## Trademarks

Not affiliated with, sponsored by, or endorsed by Seagate Technology LLC.
"Seagate" and "Barracuda" are trademarks of their respective owners and are used
here only to identify the specific hardware whose acoustic behaviour was
measured — a Seagate ST39173W Barracuda 9LP. No Seagate branding, artwork,
firmware or audio recordings are included in this project; every sound is
generated from scratch.
