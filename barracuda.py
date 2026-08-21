#!/usr/bin/env python3
"""
Barracuda drive-sound synthesiser.

Every constant here was measured from recordings, not invented; see
~/tmp/hdmotion-analysis.md for provenance. Nothing is sampled - the whole
sound is generated, so drive count, spin stagger and seek density are free
parameters rather than properties of a recording.
"""
import numpy as np
from scipy import signal

FS = 48000

# ── Spindle ───────────────────────────────────────────────────────────────
# 120 Hz = 7200 rpm, measured from the ST15150N recording: sharp peak at 120
# with harmonics at 600/720 and troughs at the 60/180 mains lines.
SPINDLE_HZ   = 120.0
SPIN_RAMP    = 1200.0 / 60.0      # 9LP: 0 to 7200 in ~6 s
SPINDOWN_COAST = 8.5              # 9LP free coast, ~8.5 s to rest
# Relative harmonic weights. h1 dominates; 5th/6th are the ones that survived
# in the idle spectrum, which is why they are not negligible here.
HARMONICS = {1: 1.00, 2: 0.16, 3: 0.11, 4: 0.09, 5: 0.22, 6: 0.18}

# Bearing and motor whine - measured from the real 7200 rpm drive's idle
# spectrum above 700 Hz. These are deliberately NOT harmonics of 120: they come
# out at 8.89x, 14.79x, 17.72x, 20.63x and so on, because they are bearing and
# motor tones rather than spindle partials.
#
# Without them the drone is a pure harmonic stack ending at 720 Hz, which
# Mallory described as sounding "like it's running under some type of
# insulation". That is exactly what a missing 1-2.5 kHz band sounds like.
# (Hz at 7200 rpm, amplitude relative to the fundamental)
WHINE = [(583.0, 0.736), (1746.1, 1.098), (1763.7, 0.517), (2329.1, 0.840),
         (2446.3, 0.573), (2592.8, 0.463), (2838.9, 0.645), (2910.6, 0.876),
         (2950.2, 0.580), (2983.9, 0.475)]
WHINE_LEVEL = 0.30        # measured levels came off a distant, noisy capture

# A short bright onset before the body responds - the actual impact, as
# distinct from the ringing it excites. Mallory: "lacks a bit of crispness".
TICK_MS, TICK_HP_HZ, TICK_LEVEL = 0.25, 3800, 0.42

# ── Actuator click ────────────────────────────────────────────────────────
# Resonances from the unclipped full-band recording. The two low modes are the
# chassis body - absent from the poor recording, and the reason its clicks
# read as static rather than impacts.
CLICK_MODES = [   # (Hz, gain, decay tau seconds)
    # Measured from the ST39173W Barracuda 9LP itself - the drive this project
    # is actually about - in its clean, unclipped 0-30s cycle. These replace a
    # set borrowed from a 2010 ST1500DL003, which was the wrong drive entirely
    # and was only ever used because it was the one unclipped recording available.
    # The real drive is much less bright (90th percentile 5859 Hz against the
    # Green's 15469) and rings notably longer - which is what an eleven-platter
    # SCSI server drive should do.
    (586,   0.62, 0.0070),
    (1758,  1.00, 0.0040),
    (2320,  0.78, 0.0034),
    (2438,  0.58, 0.0032),
    (2602,  0.46, 0.0030),
    (2906,  0.62, 0.0028),
    (3023,  0.50, 0.0027),
    (5039,  0.58, 0.0020),
    (5203,  0.50, 0.0020),
]
BURST_MS = 0.15

# ── Listening position ────────────────────────────────────────────────────
# Mallory's note on the first render: too much "hollowness" - the enclosure
# ring that you hear with an ear against the case, but not from across the
# room, where the direct transient dominates and the box resonance is
# proportionally much smaller.
#
# So distance is a parameter, not a fudge. 0.0 = ear on the chassis (full
# enclosure ring), 1.0 = across the room. It scales the decay and level of
# the low chassis modes only; the actuator modes above ~3 kHz are the direct
# sound and are left alone.
DISTANCE = 1.00
CHASSIS_MAX_HZ = 1500       # modes below this are enclosure, not actuator

def _chassis_scale(f):
    if f >= CHASSIS_MAX_HZ:
        return 1.0, 1.0
    ring = 1.0 - 0.72*DISTANCE      # decay shortens with distance
    lvl  = 1.0 - 0.42*DISTANCE      # and sits lower against the transient
    return lvl, ring

def _resonator(f, tau, n, rng):
    """One decaying mode excited by a short noise burst."""
    t = np.arange(n) / FS
    nb = int(FS * BURST_MS / 1000)
    exc = np.zeros(n); exc[:nb] = rng.standard_normal(nb)
    ring = np.sin(2*np.pi*f*t + rng.uniform(0, 2*np.pi)) * np.exp(-t/tau)
    return np.convolve(exc, ring)[:n]

def click(rng, gain=1.0):
    n = int(FS * 0.05)
    out = np.zeros(n)
    for f, g, tau in CLICK_MODES:
        jitter = rng.uniform(0.985, 1.015)      # no two seeks identical
        lvl, ring = _chassis_scale(f)
        out += g * lvl * _resonator(f*jitter, tau*ring, n, rng)
    # Onset transient: the impact itself, ahead of the resonant response.
    nt = int(FS*TICK_MS/1000)
    tick = rng.standard_normal(nt) * np.exp(-np.arange(nt)/(nt/2.5))
    sos = signal.butter(2, TICK_HP_HZ, btype='high', fs=FS, output='sos')
    out[:nt] += signal.sosfilt(sos, tick) * TICK_LEVEL
    out /= (np.abs(out).max() + 1e-9)
    return out * gain

def drone(freq_curve, rng, detune=1.0):
    """Additive spindle tone. freq_curve is per-sample Hz, so ramps are free."""
    phase = np.cumsum(2*np.pi*freq_curve*detune/FS)
    out = np.zeros(len(freq_curve))
    for h, amp in HARMONICS.items():
        wob = 1 + 0.0015*np.sin(2*np.pi*0.7*np.arange(len(freq_curve))/FS + h)
        out += amp * np.sin(phase*h) * wob
    # Bearing/motor whine. Frequencies scale with spindle speed so the whole
    # set sweeps together during spin-up, as a rotating source must.
    ratio = freq_curve/SPINDLE_HZ
    for hz, amp in WHINE:
        ph = np.cumsum(2*np.pi*hz*ratio*detune/FS)
        out += amp*WHINE_LEVEL*np.sin(ph)*np.clip(ratio,0,1)
    # Broadband air/turbulence, gated by how fast it is spinning.
    air = rng.standard_normal(len(freq_curve)) * 0.018 * (freq_curve/SPINDLE_HZ)
    return (out/len(HARMONICS) + air)

def spin_curve(dur, spin_up_at=None, spin_down_at=None):
    """rpm-as-Hz over time: linear ramp up, plateau, linear coast down."""
    n = int(FS*dur); t = np.arange(n)/FS
    f = np.full(n, SPINDLE_HZ)
    if spin_up_at is not None:
        f = np.minimum(SPINDLE_HZ, np.maximum(0, (t - spin_up_at) * SPIN_RAMP))
    if spin_down_at is not None:
        u = np.clip((t - spin_down_at)/SPINDOWN_COAST, 0, 1)
        # Friction coast: fast at first, long tail. A straight ramp is what
        # made this read as an oscillator being switched off.
        coast = SPINDLE_HZ*(1 - u)**1.35
        f = np.minimum(f, np.where(t >= spin_down_at, coast, SPINDLE_HZ))
    return f

def seek_train(dur, rate_fn, rng, gain=1.0):
    """Clicks scheduled as a Poisson process at a time-varying rate."""
    n = int(FS*dur); out = np.zeros(n + FS)
    t = 0.0
    while t < dur:
        r = max(0.1, rate_fn(t))
        t += rng.exponential(1.0/r)
        if t >= dur: break
        i = int(t*FS)
        # Amplitude stands in for seek distance: most seeks are short.
        a = gain * (0.35 + 0.65*rng.beta(1.6, 3.0))
        c = click(rng, a)
        out[i:i+len(c)] += c
    return out[:n]

# ── Spin-down rattle (ball-bearing era) ───────────────────────────────────
# Modelled, not measured, and deliberately so. The 1996 Barracudas used ball
# bearings; fluid-dynamic bearings only arrived around 2002. A ball-bearing
# spindle loses its lubricating film as it slows and rattles audibly on the
# way down. The reference recording is a 2010 FDB drive, which coasts to a
# stop silently - which is exactly why a pure frequency ramp sounds like an
# oscillator being switched off rather than a mechanism stopping.
#
# Two effects, both keyed to how far the platter has slowed:
#   1. bearing rattle - sparse impulses, a few per revolution, rising as the
#      film thins, through low-mid resonances (it is a metal-on-metal knock,
#      not a click)
#   2. head contact - these were contact start-stop drives, so below flying
#      speed the heads settle onto the landing zone with a brief scrape
RATTLE_MODES  = [(287, 1.00, 0.010), (496, 0.72, 0.008), (905, 0.40, 0.005)]
RATTLE_START  = 0.55      # fraction of full speed where the film starts to go
EVENTS_PER_REV = 2.5
CONTACT_BELOW = 0.10      # heads land somewhere under this fraction

def _rattle_hit(rng):
    n = int(FS*0.04); out = np.zeros(n)
    for f, g, tau in RATTLE_MODES:
        out += g*_resonator(f*rng.uniform(0.93, 1.07), tau, n, rng)
    return out/(np.abs(out).max()+1e-9)

def spindown_noise(freq_curve, rng, level=1.0):
    """Head park, bearing rattle and head contact, driven by the speed curve."""
    n = len(freq_curve); out = np.zeros(n + FS)
    ratio = freq_curve/SPINDLE_HZ
    # The park clunk. In the 9LP recording the spin-down does not fade in -
    # it opens with the loudest transient of the whole cycle as the actuator
    # slams home. Without it a stop sounds electronic rather than mechanical.
    dropping = np.where(np.diff(ratio) < -1e-6)[0]
    if len(dropping):
        i0 = int(dropping[0])
        park = click(rng, 1.0)
        out[i0:i0+len(park)] += park*1.15*level
    bank = [_rattle_hit(rng) for _ in range(24)]
    i = 0
    while i < n:
        r = ratio[i]
        if r <= 0.02 or r >= RATTLE_START:
            i += int(FS*0.01); continue
        rate = max(1.0, EVENTS_PER_REV*freq_curve[i])
        i += max(1, int(rng.exponential(FS/rate)))
        if i >= n: break
        # Loudest in the middle of the coast: the film is gone but there is
        # still enough energy to knock.
        grow = np.sin(np.pi*np.clip((RATTLE_START - ratio[i])/RATTLE_START, 0, 1))
        h = bank[rng.integers(len(bank))]*grow*rng.uniform(0.4, 1.0)*0.028*level
        out[i:i+len(h)] += h
    # Head contact: a brief scrape as the air bearing collapses.
    below = np.where(ratio < CONTACT_BELOW)[0]
    if len(below):
        c0 = below[0]; L = int(FS*0.45)
        if c0+L < n:
            env = np.linspace(1.0, 0.0, L)**2.2
            sc = rng.standard_normal(L)*env
            sos = signal.butter(2, [900, 5200], btype='band', fs=FS, output='sos')
            out[c0:c0+L] += signal.sosfilt(sos, sc)*0.055*level
    return out[:n]
