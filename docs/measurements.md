# Barracuda drive-sound model — parameters measured from hdmotion video

Source: `hdmotion.mp4.mkv` / `hdmotion.wav` (719 s, 48 kHz mono, decoded from the
native Opus stream — no MP3 generation added).
Analysed 2026-08-20.

## The drive, identified from the video's own captions

**Seagate Barracuda ST15150N** — 4 GB, 50-pin SCSI, late 1996 (the author dates
the model's launch to late 1994 at ~$2500). Eleven platters, 21 heads used for
user data, plus a **dedicated servo surface** rather than embedded servo. That
last detail matters acoustically: a dedicated servo surface means the actuator
tracks continuously against one reference platter, which is part of why these
drives chatter the way they do.

7200 rpm is now corroborated twice — the caption implies it and the measured
120 Hz fundamental confirms it independently.

## Spindle (the drone)

**120 Hz fundamental = 7200 rpm.** Confirmed, not assumed:

- In the quietest 2 s windows (idle, no seeking) there is a sharp peak at 120 Hz
  with harmonics visible at 600 and 720 Hz.
- The spectrum **dips** at 60 Hz and 180 Hz, so it is not mains hum — hum would
  peak there. 60 Hz sits 14.8 dB below 120 Hz.
- The 120 Hz component varies by 54 dB across the file. Mains hum is constant;
  this tracks what the drive is doing.

A second steady tone sits at **~82 Hz** (≈ 4900 rpm) — almost certainly a case
fan, not the drive. **Do not model it.** It is contamination in the source, and
reproducing it would be copying someone else's cooling.

## Seek clicks

Measured over 240 s, 10,807 detected transients:

| Parameter | Value |
|---|---|
| Click rate, real-world use | 17-26 / s |
| Click rate, synthetic seek test | ~123 / s |
| Inter-click interval | median 9.4 ms (p10 4.6, p90 18.9) |
| Attack to peak | 0.10 ms — an impulse |
| Decay to −10 dB | 1.40 ms |
| Spectral centroid | 3523 Hz |
| Resonances | 2133, 2508, 2977, 4312, 4898, 9703 Hz |

So a click is a **near-instantaneous excitation of a multi-resonant body,
decaying with a time constant around 1.2 ms** — i.e. synthesise as an impulse or
1 ms noise burst through a bank of resonant filters at those frequencies, not as
a sample.

**Why the source "sounds like static":** at 45 clicks/s with a median 9.4 ms gap
and a 1.4 ms decay to −10 dB, the tail of each click overlaps the next. The
averaged click never reaches −20 dB inside a 30 ms window. There is no silence
between clicks to hear them as discrete events.

## Caveats on these numbers

- **The recording clips** (peak = 1.000 full scale), so relative click amplitudes
  are not trustworthy. Frequencies and timings are; levels are not.
- The click energy is suspiciously narrow (10th–90th percentile only
  2883–3984 Hz). A real actuator click is broader than that. Treat the
  resonance frequencies as real and the bandwidth as an artifact of the camera
  mic and lossy codec.
- Everything here describes **one drive in one chassis**. The ensemble
  behaviour — stagger, detune, beating — should be generated, not sampled.

## Click rate by activity — the labelled dataset

Segment boundaries read off extracted frames (1 per 10 s); rates measured from
the audio over the same spans.

| Segment | clicks/s | rms | median gap |
|---|---|---|---|
| hdmotion.exe seek patterns | 122.7 | 0.215 | 6.1 ms |
| benchmark / read-speed test | 17.5 | 0.069 | 10.9 ms |
| Win98 boot | 16.7 | 0.065 | 13.8 ms |
| desktop + file browsing | 23.4 | 0.079 | 13.0 ms |
| SimCity 3000 load | 26.4 | 0.078 | 10.9 ms |

**The single most useful result here.** Ordinary use sits at **17-26 clicks/s**;
the artificial seek exerciser sits at **123/s**, five to seven times denser. So a
widget driven by real disk activity should target roughly 20/s for desktop work,
and reserve anything above ~50/s for genuine thrashing. Tuning against a
benchmark recording would make it sound wrong in exactly the way the video's
author complains about.

Note the sequential read test is the *quietest* segment (17.5/s). That is correct
physics — a sequential read barely moves the arm — and it is a good sanity check
that the click detector is measuring seeks rather than general noise.

## Still to extract

The video shows seek patterns on a CRT via `hdmotion.exe` while the sound plays:
the screen is the model's **input** (head position over time) and the audio is
its **output**. Extracting frames and reading the visualisation would give the
missing mapping — seek distance to click rate — which is what lets `blktrace`
LBA deltas drive the synth directly.

---

# Second source: hdmotion2 (startup / init / spin-down)

25 s, **not clipped** (peak 0.661 vs 1.000 in the first video), and the audio is
full-band. This is the "voice" source; the first video is the "performance"
source.

## Important: this is a different, slower drive

**Steady spindle 99.6 Hz = 5977 rpm (~6000)**, with a clean harmonic series at
197.8 / 297.4 / 395.5 / 498.0 Hz. That is not the 7200 rpm ST15150N from the
first video.

**So do not take the drone pitch from this recording.** Use its *click timbre*,
which is excellent, and set the drone to **120 Hz** for a Barracuda. Mixing the
two sources without noticing this would give a 6000 rpm whine under 7200 rpm
seek behaviour.

## Spin-up — the startup movement

Fundamental tracked with a continuity constraint:

| t | Hz | rpm |
|---|---|---|
| 3.50 s | 70.3 | 4219 |
| 3.75 s | 76.2 | 4570 |
| 4.00 s | 82.0 | 4922 |
| 4.25 s | 87.9 | 5273 |
| 4.50 s | 93.8 | 5625 |
| 4.75 s | 99.6 | 5977 (settled) |

**The ramp is linear at ~23.4 Hz/s (~1400 rpm/s)** — a straight line, not an
exponential ease. Extrapolating back to zero puts the start at ~0.5 s, so the
whole spin-up is roughly **4.2 seconds**. For a staggered eight-drive cabinet,
that is the per-voice ramp; the 30 s cascade Mallory remembers is 8 of these
started a few seconds apart.

**Spin-down** runs at a similar ~20 Hz/s (94 Hz at 17.5 s down to 64 Hz at
19.0 s), reaching silence around 21 s. Slightly slower than spin-up.

## The click impulse response — the prize

31 isolated clicks, well separated (0.15 s to 11.11 s, plus one at 17.30 s), so
each decays without overlapping the next.

| Parameter | hdmotion2 (good) | hdmotion1 (poor) |
|---|---|---|
| Decay to -10 dB | 1.17 ms | 1.40 ms |
| Spectral centroid | 7698 Hz | 3523 Hz |
| 10th-90th percentile | **375 - 15469 Hz** | 2883 - 3984 Hz |
| Resonances | 352, 750, 3727, 4781, 5273, 5977, 13477, 14062 Hz | 2133, 2508, 2977, 4312, 4898, 9703 Hz |

**This confirms the earlier diagnosis outright.** The good recording carries
click energy from 375 Hz up, including low resonances at **352 and 750 Hz** that
are entirely absent from the first video. That low-frequency body is the chassis
thump — the part that makes a click read as an impact rather than a hiss, and
exactly what Mallory identified by ear as missing.

The decay agreement between the two recordings (1.17 vs 1.40 ms) is a good
cross-check: the timing survived the bad recording even though the spectrum did
not.

## Synthesis recipe, from both sources

1. **Drone:** 120 Hz fundamental with harmonics (from hdmotion1), per-voice
   detune of a fraction of a percent so multiple drives beat against each other.
2. **Click:** excite a resonant filter bank at 352, 750, 3727, 4781, 5273 Hz
   (plus the two ~13-14 kHz resonances if the output is full-band) with a ~1 ms
   burst; decay constant ~1.2 ms.
3. **Rate:** ~20 clicks/s for ordinary desktop activity, up to ~50+/s for
   thrashing, ~120/s only for a deliberate seek exerciser.
4. **Startup:** linear rpm ramp at ~1400 rpm/s over ~4 s per drive, staggered
   across voices.

---

## Target confirmed: the 7200 rpm Barracuda, not the Green

The second video's drive was identified from a YouTube comment as a **Seagate
ST1500DL003 Barracuda Green, nominal 5900 rpm** — which matches the 5977 rpm
measured here to within ~1%, i.e. the model number and the audio agree. It is a
2010-era 1.5 TB SATA drive wearing a revived brand name, unrelated to the 1996
9 GB SCSI Barracudas this project is actually about.

**So: drone at 120 Hz. Click timbre from hdmotion2. Spin-down NOT from
hdmotion2.**

That last point is the subtle one. The Green uses an **unload ramp** — heads park
off the platter on a plastic ramp, which is what the big transient at 17.30 s
is. The 1990s Barracudas were **contact start-stop**: heads landed on a dedicated
landing zone on the platter surface, and the drive coasted to rest with the air
bearing collapsing under them. Different mechanism, different sound.

Copying hdmotion2's spin-down would give a 2010 ramp-unload click at the end of a
1996 drive. Model the CSS gesture instead: no sharp unload transient, a longer
coast, and the head-settling character as the platter slows below flying speed.
