#!/usr/bin/env python3
"""
Full measurement battery for a drive recording.

  python3 analyse_drive.py <file.wav> [--spindle-hint 120]

Prints everything needed to parameterise the synth, and writes a spectrogram
PNG next to the input. Every number it reports is one the engine consumes.
"""
import sys, os, wave
import numpy as np
from scipy import signal
import matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt

path = sys.argv[1]
hint = float(sys.argv[sys.argv.index('--spindle-hint')+1]) if '--spindle-hint' in sys.argv else None

w = wave.open(path); fs = w.getframerate()
x = np.frombuffer(w.readframes(w.getnframes()), dtype='<i2').astype(np.float64)/32768.0
dur = len(x)/fs
peak = np.abs(x).max()
print(f"== {os.path.basename(path)}")
print(f"   {dur:.1f}s  {fs} Hz  peak {peak:.3f}  rms {np.sqrt((x**2).mean()):.4f}"
      f"  {'CLIPPED - levels untrustworthy' if peak >= 0.999 else 'not clipped'}")

# ── quiet windows = idle, where the drone is unmasked ────────────────────
win = int(fs*2); nw = max(1, len(x)//win)
rms = np.array([np.sqrt((x[i*win:(i+1)*win]**2).mean()) for i in range(nw)])
quiet = np.argsort(rms)[:max(3, nw//8)]
acc = None
for i in quiet:
    f, P = signal.welch(x[i*win:(i+1)*win], fs, nperseg=16384)
    acc = P if acc is None else acc+P
Pq = acc/len(quiet)

b = (f >= 60) & (f <= 220)
f0 = f[b][np.argmax(Pq[b])]
print(f"\n-- spindle --\n   fundamental {f0:.1f} Hz = {f0*60:.0f} rpm")
if hint: print(f"   (hint {hint:.0f} Hz = {hint*60:.0f} rpm -> {'MATCH' if abs(f0-hint)<4 else 'DIFFERENT DRIVE'})")
for k in (2,3,4,5,6):
    m = (f >= f0*k-5) & (f <= f0*k+5)
    if m.any(): print(f"   harmonic {k}: {f[m][np.argmax(Pq[m])]:7.1f} Hz  {10*np.log10(Pq[m].max()+1e-20):6.1f} dB")
for lo,hi in [(60,80),(170,190)]:
    m=(f>=lo)&(f<=hi)
    print(f"   mains check {lo}-{hi} Hz: {10*np.log10(Pq[m].max()+1e-20):6.1f} dB")

print("\n-- bearing / motor whine (inharmonic partials above 700 Hz) --")
m = (f >= 700) & (f <= 3000); ff, PP = f[m], Pq[m]
pk,_ = signal.find_peaks(10*np.log10(PP+1e-20), prominence=4)
ref = Pq[(f>=f0-5)&(f<=f0+5)].max()
for i in sorted(pk[np.argsort(PP[pk])[::-1][:12]], key=lambda j: ff[j]):
    print(f"   {ff[i]:7.1f} Hz  rel {np.sqrt(PP[i]/ref):.3f}   = {ff[i]/f0:5.2f} x f0")

# ── isolated clicks = the impulse response ───────────────────────────────
sos = signal.butter(4, [300, min(20000, fs/2-1000)], btype='band', fs=fs, output='sos')
xb = signal.sosfilt(sos, x); env = np.abs(signal.hilbert(xb))
pk,_ = signal.find_peaks(env, height=np.percentile(env, 99.5), distance=int(fs*0.20))
print(f"\n-- click impulse response ({len(pk)} isolated events) --")
if len(pk) > 4:
    L = int(fs*0.06)
    A = np.array([xb[p-200:p-200+L] for p in pk if p > 200 and p-200+L < len(xb)])
    me = np.abs(signal.hilbert(A, axis=1)).mean(axis=0)
    pi = int(np.argmax(me)); pv = me[pi]; af = me[pi:]
    for db in (-10, -20, -30):
        i = np.argmax(af < pv*10**(db/20))
        print(f"   decay {db} dB: {i/fs*1000:.2f} ms" if i > 0 else f"   decay {db} dB: not reached in 60 ms")
    fc, Pc = signal.welch(A.ravel(), fs, nperseg=2048)
    mm = (fc >= 150) & (fc <= min(22000, fs/2)); ffc, PPc = fc[mm], Pc[mm]
    cum = np.cumsum(PPc)/PPc.sum()
    print(f"   centroid {(ffc*PPc).sum()/PPc.sum():.0f} Hz   10th {ffc[np.argmax(cum>.1)]:.0f}  90th {ffc[np.argmax(cum>.9)]:.0f} Hz")
    p2,_ = signal.find_peaks(10*np.log10(PPc+1e-20), prominence=3)
    o = np.argsort(PPc[p2])[::-1][:9]
    print("   resonances:", ', '.join(f"{ffc[p2[i]]:.0f}" for i in sorted(o, key=lambda j: ffc[p2[j]])), "Hz")

# ── speed over time: spin-up / spin-down ramps ───────────────────────────
f2, t2, S = signal.spectrogram(x, fs, nperseg=8192, noverlap=7680)
band = (f2 >= 20) & (f2 <= f0*1.8); fb = f2[band]
prev = None; tr = []
for i in range(S.shape[1]):
    col = S[band, i]
    if prev is None: j = int(np.argmax(col))
    else:
        near = np.abs(fb-prev) < 12
        j = int(np.argmax(np.where(near, col, 0)))
        if col[j] < col.max()*0.05: j = int(np.argmax(col))
    prev = fb[j]; tr.append((t2[i], fb[j], 10*np.log10(col[j]+1e-14)))
tr = np.array(tr)
strong = tr[tr[:,2] > tr[:,2].max()-30]
if len(strong) > 10:
    d = np.diff(strong[:,1])/np.diff(strong[:,0])
    up = d[d > 8]; dn = d[d < -8]
    print("\n-- speed ramps --")
    if len(up): print(f"   spin-up   median {np.median(up):.1f} Hz/s = {np.median(up)*60:.0f} rpm/s")
    if len(dn): print(f"   spin-down median {abs(np.median(dn)):.1f} Hz/s = {abs(np.median(dn))*60:.0f} rpm/s")

fig, ax = plt.subplots(2,1, figsize=(14,7), sharex=True)
ax[0].plot(np.arange(len(x))/fs, x, lw=.25); ax[0].set_ylabel('amp')
m = f2 <= 3000
ax[1].pcolormesh(t2, f2[m], 10*np.log10(S[m]+1e-14), shading='auto', cmap='magma')
ax[1].set_ylabel('Hz'); ax[1].set_xlabel('s')
out = os.path.splitext(path)[0] + '_analysis.png'
plt.tight_layout(); plt.savefig(out, dpi=100)
print(f"\nspectrogram -> {out}")
