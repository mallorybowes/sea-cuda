#!/usr/bin/env python3
"""Render a demo: four drives, staggered spin-up, varying load, spin-down."""
import numpy as np, wave, sys
sys.path.insert(0, '/home/mal/tmp/barracuda')
from barracuda import (FS, SPINDLE_HZ, click, drone, spin_curve, spindown_noise)

rng = np.random.default_rng(7)
DUR   = 50.0
N     = int(FS*DUR)
DRIVES = 4
STAGGER = 3.0            # seconds between each drive starting
BANK  = [click(rng, 1.0) for _ in range(40)]   # precomputed variants

def rate_at(t):
    """Click rate over the demo, using the measured per-activity figures."""
    if t < 14:  return 8      # init/recalibration during and after spin-up
    if t < 20:  return 1.5    # idle
    if t < 27:  return 22     # desktop + file browsing (measured 23.4)
    if t < 34:  return 60     # heavy - thrashing
    if t < 36:  return 3      # settling
    return 0.5

mix = np.zeros(N + FS)
for d in range(DRIVES):
    start = 0.5 + d*STAGGER
    stop  = 36.0 + d*0.8
    f = spin_curve(DUR, spin_up_at=start, spin_down_at=stop)
    detune = 1.0 + (d - 1.5)*0.0022        # a fraction of a percent apart
    v = drone(f, rng, detune) * 0.16
    # Drone fades with spindle speed so a stopped drive is silent.
    v *= np.clip(f/SPINDLE_HZ, 0, 1)**1.5

    # Clicks, gated to when this drive is actually up to speed.
    t = 0.0
    while t < DUR:
        r = rate_at(t)/DRIVES
        t += rng.exponential(1.0/max(r, 0.05))
        if t >= DUR: break
        spun = f[min(int(t*FS), N-1)]/SPINDLE_HZ
        if spun < 0.55: continue
        c = BANK[rng.integers(len(BANK))] * (0.35 + 0.65*rng.beta(1.6, 3.0)) * 0.5
        i = int(t*FS); mix[i:i+len(c)] += c
    v += spindown_noise(f, rng)
    mix[:N] += v

x = mix[:N]
x /= np.abs(x).max()/0.89
w = wave.open('/home/mal/tmp/barracuda/demo.wav', 'w')
w.setnchannels(1); w.setsampwidth(2); w.setframerate(FS)
w.writeframes((x*32767).astype('<i2').tobytes()); w.close()
print(f"rendered {DUR:.0f}s, peak {np.abs(x).max():.2f}")
