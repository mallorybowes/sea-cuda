# sea-cuda

Makes a silent SSD sound like a 1990s SCSI drive array.

> *"You'd have some kinda-a-a-a..."* — with apologies to Ann Wilson, who named
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

```
sudo install -m 755 barracuda-sleep-hook.sh /etc/systemd/system-sleep/barracuda
```

Parks the array as the machine goes down, spins it up on resume. Every path in
the hook is bounded — a sleep hook that blocks would hang suspend, which is a
much worse bug than a missing sound effect.

## Requirements

Python 3.11+, `numpy`, `scipy` (analysis only), PipeWire with `pw-play` and
`pactl`. Linux; reads `/proc/diskstats`.

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
