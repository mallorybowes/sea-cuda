#!/usr/bin/env python3
"""
Barracuda drive-sound daemon.

Watches real disk activity and synthesises a phantom drive array from it.
Nothing is sampled: every sound is generated from parameters measured off a
Seagate ST39173W Barracuda 9LP. See hdmotion-analysis.md for provenance.

  python3 barracudad.py                      # defaults, plays live
  python3 barracudad.py --preset coffee_shop # be asked to leave
  python3 barracudad.py --render out.wav --seconds 30
  python3 barracudad.py --list-devices
"""
import argparse, errno, fcntl, os, shutil, signal, subprocess, sys, time, tomllib, wave
import numpy as np
import barracuda as B

HERE = os.path.dirname(os.path.abspath(__file__))

def load_config(path, preset):
    with open(path, 'rb') as fh:
        cfg = tomllib.load(fh)
    if preset:
        p = cfg.get('preset', {}).get(preset)
        if p is None:
            sys.exit(f"no such preset: {preset}. available: "
                     + ', '.join(cfg.get('preset', {})) )
        # Presets are flat; map each key onto whichever section owns it.
        for k, v in p.items():
            for sect in ('audio', 'drives', 'character', 'activity', 'behaviour'):
                if k in cfg.get(sect, {}):
                    cfg[sect][k] = v; break
    return cfg

def apply_to_engine(cfg):
    """Push config into the synthesis module's parameters."""
    B.SPINDLE_HZ  = cfg['drives']['rpm']/60.0
    B.DISTANCE    = cfg['character']['distance']
    B.WHINE_LEVEL = cfg['character']['whine']
    B.TICK_LEVEL  = cfg['character']['brightness']

def root_device():
    """The disk backing / - e.g. nvme0n1 - so we watch the right thing."""
    try:
        st = os.stat('/')
        major, minor = os.major(st.st_dev), os.minor(st.st_dev)
        for line in open('/proc/diskstats'):
            f = line.split()
            if int(f[0]) == major and int(f[1]) == minor:
                name = f[2]
                # partition -> parent disk
                for cand in os.listdir('/sys/block'):
                    if name.startswith(cand) and name != cand:
                        return cand
                return name
    except Exception:
        pass
    return 'nvme0n1'

# ── Independent volume ────────────────────────────────────────────────────
# A stream in PipeWire has its own volume, independent of the sink's. So to
# hold a constant *absolute* level we read the sink volume and set this
# stream to target/sink - turn the system up, this stream turns itself down
# by the same factor, and the array stays where it was.
def _pactl(*args):
    try:
        return subprocess.run(['pactl', *args], capture_output=True,
                              text=True, timeout=2).stdout
    except Exception:
        return ''

def sink_volume():
    out = _pactl('get-sink-volume', '@DEFAULT_SINK@')
    pcts = [int(p.rstrip('%')) for p in out.split() if p.endswith('%')]
    return max(pcts)/100.0 if pcts else None

def find_stream(app_name):
    """Index of our own sink-input, matched on any identifying property."""
    out = _pactl('list', 'sink-inputs')
    idx, want = None, app_name.lower()
    for line in out.splitlines():
        line = line.strip()
        if line.startswith('Sink Input #'):
            idx = line.split('#')[1].strip()
        elif idx and any(k in line for k in
                         ('node.name', 'application.name', 'media.name')):
            if want in line.lower():
                return idx
    return None

def hold_level(stream, target, sink_vol):
    """Set this stream so target absolute level survives the master fader."""
    if stream is None or not sink_vol:
        return
    pct = int(round(min(1.5, target/max(0.05, sink_vol))*100))
    _pactl('set-sink-input-volume', stream, f'{pct}%')

def read_ios(dev):
    for line in open('/proc/diskstats'):
        f = line.split()
        if f[2] == dev:
            return int(f[3]) + int(f[7])       # reads + writes completed
    return None

# ── Suspend and resume ────────────────────────────────────────────────────
# Deliberately NOT a systemd sleep hook. Two reasons, both found the hard way:
#
#   1. systemd freezes user.slice *before* running anything in
#      /usr/lib/systemd/system-sleep, so a user daemon is already stopped by the
#      time the hook fires and can never make a sound from there. man
#      systemd-sleep says as much, and points at inhibitor locks instead.
#   2. Not every distro even looks in /etc/systemd/system-sleep - Debian and
#      Ubuntu patch systemd to, Arch does not.
#
# Hanging it off the compositor's idle daemon fails too: hypridle drops its own
# inhibitor when the session LOCKS rather than when its command finishes, and
# the session locks the instant suspend begins.
#
# So hold our own delay lock. logind will not begin suspending while one is
# held, which buys the coast; we drop it ourselves once the array has parked.
# logind stops waiting after InhibitDelayMaxSec (5s by default), so the coast
# has to fit inside that - a stuck daemon can delay a suspend, never block it.

def take_sleep_inhibitor():
    """Hold a delay lock on sleep. Returns a process to kill when done."""
    try:
        return subprocess.Popen(
            ['systemd-inhibit', '--what=sleep', '--mode=delay', '--who=Barracuda',
             '--why=Parking the array', 'sleep', 'infinity'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return None

def watch_prepare_for_sleep():
    """Non-blocking stream of logind PrepareForSleep signals."""
    try:
        p = subprocess.Popen(
            ['gdbus', 'monitor', '--system', '--dest', 'org.freedesktop.login1',
             '--object-path', '/org/freedesktop/login1'],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        os.set_blocking(p.stdout.fileno(), False)
        return p
    except OSError:
        return None

def start_player(fs):
    """Spawn the audio sink process.

    Restartable on purpose: the suspend drain closes the player outright to get
    an exact end-of-playback signal, so this has to be callable more than once.

    start_new_session puts the player in its own process group, and that is
    load-bearing. Ctrl-C sends SIGINT to the whole foreground process group, so
    a player spawned into ours died first and spin_down_on_exit then wrote to a
    dead pipe - which meant the park sound never played on exit and you got a
    BrokenPipeError traceback instead. Out of the group, the player outlives the
    interrupt exactly long enough to play the wind-down.

    It cannot be orphaned by this: the player reads our pipe, so whatever kills
    the daemon - even SIGKILL - closes the write end, and the player exits on
    EOF.
    """
    if shutil.which('pw-play'):
        return subprocess.Popen(
            ['pw-play', '--raw', '--format=s16', f'--rate={fs}', '--channels=1',
             '-P', '{ node.name = "Barracuda" media.name = "Barracuda array" '
                   'application.name = "Barracuda" media.role = "Music" }', '-'],
            stdin=subprocess.PIPE, start_new_session=True)
    return subprocess.Popen(['aplay','-q','-f','S16_LE','-r',str(fs),'-c','1','-'],
                            stdin=subprocess.PIPE, start_new_session=True)

SLEEPLOG = os.path.expanduser('~/.barracudad-sleep.log')

def slog(msg):
    """Log a suspend-cycle event to stderr and to a file.

    A file because the interesting half of a suspend happens after the terminal
    is gone: if a tail is still audible on resume, these timings say which side
    of the freeze it came from, which guessing never did.
    """
    print(f"  [sleep] {msg}", file=sys.stderr)
    try:
        with open(SLEEPLOG, 'a') as f:
            f.write(time.strftime('%Y-%m-%d %H:%M:%S') + f" {msg}\n")
    except OSError:
        pass

class Engine:
    def __init__(self, cfg, rng):
        self.cfg, self.rng = cfg, rng
        self.fs = cfg['audio']['sample_rate']
        self.n  = cfg['drives']['count']
        self.spindle = B.SPINDLE_HZ
        self.bank = [B.click(rng, 1.0) for _ in range(24)]
        self.tail = np.zeros(self.fs)          # click overlap between blocks
        self.t = 0.0
        self.phase = np.zeros((self.n, 1 + len(B.WHINE)))
        self.detune = 1.0 + (np.arange(self.n) - (self.n-1)/2)*cfg['drives']['detune']
        mode = cfg['drives'].get('spin_up_mode', 'staggered')
        if mode == 'together':
            # Every spindle on one contactor. Louder inrush, no cascade - what
            # a single power switch on a small box sounded like.
            self.start_at = np.full(self.n, 0.4)
        elif mode == 'staggered':
            self.start_at = np.arange(self.n)*cfg['drives']['stagger'] + 0.4
        else:
            sys.exit(f"spin_up_mode must be 'staggered' or 'together', got {mode!r}")
        # Spin state is a machine, not a one-shot flag: the array has to be
        # able to park and restart repeatedly while the daemon keeps running.
        spread = cfg['drives']['stagger']*0.28 if mode == 'staggered' else 0.0
        self.stop_offset = np.arange(self.n)*spread
        self.mode = 'up' if cfg['behaviour']['spin_up_on_start'] else 'running'
        self.mode_t = 0.0
        self.mode_s0 = np.zeros(self.n) if self.mode == 'up' else np.ones(self.n)

    def speed(self, drive, t):
        """Fraction of full speed for one drive at time t."""
        if self.mode == 'running':
            return 1.0
        s0 = self.mode_s0[drive]
        if self.mode == 'down':
            off = self.stop_offset[drive]
            if t < self.mode_t + off:
                return float(s0)
            u = min(1.0, (t - self.mode_t - off)/B.SPINDOWN_COAST)
            return float(max(0.0, s0*(1-u)**1.35))
        # spinning up, from wherever it currently is
        off = self.start_at[drive] if self.mode_t == 0.0 else 0.0
        el = max(0.0, t - self.mode_t - off)
        return float(np.clip(s0 + el*B.SPIN_RAMP/self.spindle, 0, 1))

    def spin(self, direction, t):
        """'up' or 'down', from the current speed of each drive."""
        self.mode_s0 = np.array([self.speed(d, t) for d in range(self.n)])
        self.mode, self.mode_t = direction, t

    def at_rest(self, t):
        return all(self.speed(d, t) <= 0.001 for d in range(self.n))

    def set_drives(self, n):
        """Change the array size live. New spindles start from rest."""
        n = max(0, min(16, n))
        if n == self.n: return
        keep = min(n, self.n)
        ph = np.zeros((n, 1 + len(B.WHINE))); ph[:keep] = self.phase[:keep]
        s0 = np.zeros(n); s0[:keep] = self.mode_s0[:keep]
        self.phase, self.mode_s0, self.n = ph, s0, n
        self.detune = 1.0 + (np.arange(n) - (n-1)/2)*self.cfg['drives']['detune']
        self.start_at = np.arange(n)*self.cfg['drives']['stagger'] + 0.4
        spread = self.cfg['drives']['stagger']*0.28 if self.cfg['drives'].get('spin_up_mode','staggered')=='staggered' else 0.0
        self.stop_offset = np.arange(n)*spread
        if self.mode != 'down': self.spin('up', self.t)

    def block(self, nsamp, click_rate):
        fs = self.fs
        out = np.zeros(nsamp)
        t0 = self.t
        for d in range(self.n):
            s0 = self.speed(d, t0); s1 = self.speed(d, t0 + nsamp/fs)
            if s1 <= 0.001 and s0 <= 0.001:
                continue
            ramp = np.linspace(s0, s1, nsamp)
            f = self.spindle*ramp*self.detune[d]
            # fundamental + harmonics, phase carried across blocks
            inc = 2*np.pi*f/fs
            ph = self.phase[d, 0] + np.cumsum(inc)
            self.phase[d, 0] = ph[-1] % (2*np.pi)
            v = np.zeros(nsamp)
            for h, amp in B.HARMONICS.items():
                v += amp*np.sin(ph*h)
            v /= len(B.HARMONICS)
            for i, (hz, amp) in enumerate(B.WHINE):
                inc2 = 2*np.pi*hz*ramp*self.detune[d]/fs
                p2 = self.phase[d, 1+i] + np.cumsum(inc2)
                self.phase[d, 1+i] = p2[-1] % (2*np.pi)
                v += amp*B.WHINE_LEVEL*np.sin(p2)*ramp
            v += self.rng.standard_normal(nsamp)*0.018*ramp
            out += v*ramp**1.5*0.16
        # seeks
        if click_rate > 0:
            k = self.rng.poisson(click_rate*nsamp/fs)
            for _ in range(int(k)):
                spun = max(self.speed(d, t0) for d in range(self.n))
                if spun < 0.55: break
                c = self.bank[self.rng.integers(len(self.bank))]
                a = (0.35 + 0.65*self.rng.beta(1.6, 3.0))*0.5
                i = int(self.rng.integers(0, nsamp))
                seg = c*a
                room = len(self.tail) - i
                self.tail[i:i+len(seg)] += seg[:room]
        out += self.tail[:nsamp]
        self.tail = np.roll(self.tail, -nsamp); self.tail[-nsamp:] = 0
        self.t += nsamp/fs
        return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', default=os.path.join(HERE, 'config.toml'))
    ap.add_argument('--preset')
    ap.add_argument('--device')
    ap.add_argument('--render'); ap.add_argument('--seconds', type=float, default=30)
    ap.add_argument('--list-devices', action='store_true')
    a = ap.parse_args()

    if a.list_devices:
        for line in open('/proc/diskstats'):
            f = line.split()
            if int(f[3]) + int(f[7]) > 0:
                print(f"  {f[2]:12s} {int(f[3])+int(f[7]):>12,} ops")
        return

    cfg = load_config(a.config, a.preset)
    apply_to_engine(cfg)
    rng = np.random.default_rng()
    eng = Engine(cfg, rng)
    fs = eng.fs
    dev = a.device or (root_device() if cfg['audio']['device'] == 'auto'
                       else cfg['audio']['device'])
    vol = cfg['audio']['volume']
    BLK = 1024

    if a.render:
        rate = cfg['activity']['idle_rate']
        buf = []
        n = int(a.seconds*fs/BLK)
        for i in range(n):
            if i > n*0.35: rate = 24
            if i > n*0.65: rate = 70
            buf.append(eng.block(BLK, rate))
        x = np.concatenate(buf); x = x/max(1e-9, np.abs(x).max())*0.9*vol
        w = wave.open(a.render, 'w'); w.setnchannels(1); w.setsampwidth(2); w.setframerate(fs)
        w.writeframes((x*32767).astype('<i2').tobytes()); w.close()
        print(f"rendered {a.seconds:.0f}s -> {a.render}")
        return

    # Control surface. Signals for the two things you want mid-set, and a FIFO
    # for everything else, so the array can be parked and restarted without
    # killing the daemon.
    #   kill -USR1 $(cat ~/.barracudad.pid)   spin down, keep running
    #   kill -USR2 $(cat ~/.barracudad.pid)   spin back up
    #   echo 'volume 0.8'  > ~/.barracudad.ctl
    #   echo 'drives 8'    > ~/.barracudad.ctl
    #   echo 'spindown'    > ~/.barracudad.ctl
    pidfile = os.path.expanduser('~/.barracudad.pid')
    ctlpath = os.path.expanduser('~/.barracudad.ctl')
    # Singleton guard. A second instance used to adopt the first's pid file and
    # control fifo and then delete both on its way out, which silently broke the
    # running daemon's control channel and left `stop` aimed at a dead pid.
    #
    # flock rather than "read the pid and check whether it is alive": the kernel
    # holds the lock for the life of the process and drops it on any death,
    # SIGKILL included. Nothing to clean up, and no liveness heuristic for a
    # recycled pid to fool.
    #
    # O_CREAT without O_TRUNC is the important part. The old code opened with
    # 'w', which truncates immediately - so a second instance destroyed the
    # owner's pid file merely by starting, before any check could run.
    pidfd = os.open(pidfile, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(pidfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        try:
            other = os.read(pidfd, 32).decode(errors='replace').strip() or '?'
        except OSError:
            other = '?'
        # Exit without touching the pid file or the fifo: the loser has to be
        # inert. SystemExit also skips the shutdown path, which unlinks both.
        sys.exit(f"barracudad is already running (pid {other}).\n"
                 f"  stop it with:  barracuda-ctl.sh stop\n"
                 f"  or run a second, isolated one:  "
                 f"HOME=/tmp/barracuda-test {sys.argv[0]}")
    os.ftruncate(pidfd, 0)
    os.write(pidfd, str(os.getpid()).encode())
    # pidfd is deliberately never closed - closing it would release the lock.
    pending = []
    signal.signal(signal.SIGUSR1, lambda *_: pending.append('spindown'))
    signal.signal(signal.SIGUSR2, lambda *_: pending.append('spinup'))
    # Route SIGTERM through the same shutdown as ctrl-c. Without this, a
    # `pkill barracudad` leaves the inhibitor process orphaned and every later
    # suspend pays the full InhibitDelayMaxSec before logind gives up on it.
    def _term(*_): raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _term)
    try:
        if not os.path.exists(ctlpath): os.mkfifo(ctlpath)
        ctl = os.open(ctlpath, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        ctl = None

    print(f"barracudad: watching {dev}, {eng.n} drives at {cfg['drives']['rpm']} rpm"
          + (f", preset '{a.preset}'" if a.preset else ""), file=sys.stderr)
    # pw-play, not aplay. The ALSA compatibility layer ignores PULSE_PROP, so
    # an aplay stream appears as "PipeWire ALSA [aplay]" with no way to tell it
    # apart from any other aplay - which meant the stream lookup found nothing
    # and the independent-volume compensation silently never ran.
    # pw-play takes explicit node properties, so the stream is named, findable,
    # and shows up as "Barracuda" in the system sound settings.
    if not shutil.which('pw-play'):
        print("  pw-play not found, falling back to aplay "
              "(independent volume will not work)", file=sys.stderr)
    player = start_player(fs)
    indep = cfg['audio'].get('independent', False)
    stream = None; last_vol_check = 0.0; last_sink = None
    # Event-driven rather than polled. A 1 s poll meant a quarter-second of the
    # array being briefly loud every time the fader moved, because nothing
    # noticed until the next tick. `pactl subscribe` pushes sink changes as they
    # happen, so the compensation lands with the fader instead of after it.
    # The slow poll stays as a fallback in case subscribe dies.
    sub = None
    if indep:
        print("  holding an absolute level against the system volume", file=sys.stderr)
        try:
            sub = subprocess.Popen(['pactl', 'subscribe'], stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, text=True)
            os.set_blocking(sub.stdout.fileno(), False)
        except Exception:
            sub = None
    slp = cfg.get('sleep', {})
    sleep_coast = float(slp.get('coast_s', 3.0))
    # Audio we have written is not audio that has been played. Whatever is still
    # in flight when we drop the inhibitor gets frozen with the rest of the
    # session and plays out on resume - which sounds like the array spinning
    # down just after you wake the machine.
    #
    # Four rounds went into estimating that backlog: pad for the 64 KiB pipe,
    # add pw-play's declared latency, measure bytes written against elapsed
    # time, add a constant for the part below the pipe. Every one of them was
    # arithmetic about a quantity nothing was actually reporting.
    #
    # There is a signal. pw-play drains what it has been given and only then
    # exits, so closing its stdin and waiting for the process to die IS end of
    # playback - pipe, player queue and sink included, with nothing to know
    # about any of them. Measured against known durations of audio, process
    # exit lands 0.047s after the last sample, repeatable to a millisecond, at
    # both 0.5s (inside the pipe) and 2.0s (over it).
    #
    # So: no drain_s, no downstream_s, no sample-rate arithmetic. Close it and
    # wait for it.
    #
    # logind stops honouring delay inhibitors at InhibitDelayMaxSec, 5s by
    # default, and simply proceeds. Staying inside it is our job, not its, so
    # the coast is clamped with a second in hand for the drain.
    if sleep_coast > 3.5:
        sleep_coast = 3.5
        print(f"  coast clamped to {sleep_coast:.1f}s, leaving the drain room "
              "inside logind's InhibitDelayMaxSec", file=sys.stderr)
    inhibitor = mon = None
    coast_until = release_ceiling = drain_t0 = None
    # draining: stopped writing, waiting for the player to finish and exit.
    # parked:   drained and released, waiting for the freeze. Writing here was
    #           the bug that survived the last fix - the inhibitor is already
    #           gone but the machine has not frozen yet, so anything written
    #           refills the pipe we just emptied and plays back on resume.
    draining = parked = False
    if slp.get('inhibit', True):
        inhibitor, mon = take_sleep_inhibitor(), watch_prepare_for_sleep()
        if inhibitor is not None and mon is not None:
            print(f"  parking on suspend, {sleep_coast:.1f}s coast then a "
                  "drain measured from the player", file=sys.stderr)
        else:
            print("  no suspend handling (systemd-inhibit or gdbus missing)",
                  file=sys.stderr)
            for pr in (inhibitor, mon):
                if pr is not None: pr.terminate()
            inhibitor = mon = None

    last = read_ios(dev); last_t = time.time(); rate = cfg['activity']['idle_rate']
    def handle(cmd):
        nonlocal vol
        parts = cmd.split()
        if not parts: return
        c = parts[0].lower()
        if c in ('spindown', 'park', 'stop'):
            eng.spin('down', eng.t); print("  -> spinning down", file=sys.stderr)
        elif c in ('spinup', 'start'):
            eng.spin('up', eng.t);   print("  -> spinning up", file=sys.stderr)
        elif c == 'volume' and len(parts) > 1:
            vol = max(0.0, min(1.0, float(parts[1]))); print(f"  -> volume {vol}", file=sys.stderr)
        elif c == 'drives' and len(parts) > 1:
            eng.set_drives(int(parts[1])); print(f"  -> {eng.n} drives", file=sys.stderr)
        elif c == 'status':
            print(f"  {eng.n} drives, mode={eng.mode}, vol={vol:.2f}, rate={rate:.1f}/s",
                  file=sys.stderr)
        else:
            print(f"  ? unknown command: {cmd!r}", file=sys.stderr)

    try:
        while True:
            now = time.time()
            while pending: handle(pending.pop(0))
            if ctl is not None:
                try:
                    data = os.read(ctl, 4096)
                    for line in data.decode(errors='replace').splitlines():
                        if line.strip(): handle(line.strip())
                except OSError as e:
                    if e.errno not in (errno.EAGAIN, errno.EWOULDBLOCK): raise
            if (now - last_t)*1000 >= cfg['activity']['poll_ms']:
                cur = read_ios(dev)
                if cur is not None and last is not None:
                    iops = (cur - last)/max(1e-6, now - last_t)
                    rate = min(cfg['activity']['max_rate'],
                               cfg['activity']['idle_rate']
                               + iops*cfg['activity']['clicks_per_io'])
                last, last_t = cur, now
            # Drain any sink events; each one means the fader may have moved.
            sink_event = False
            if sub is not None:
                try:
                    data = sub.stdout.read()
                    if data:
                        # "on sink #" only - NOT "on sink-input #", which this would also
                        # match as a substring. We set a sink-input volume ourselves,
                        # so a loose match makes the daemon retrigger on its own writes.
                        sink_event = any("on sink #" in ln for ln in data.splitlines())
                except (BlockingIOError, TypeError, ValueError):
                    pass
            if indep and (sink_event or
                          now - last_vol_check >= cfg['audio'].get('independent_poll_s', 2.0)):
                last_vol_check = now
                found = find_stream('Barracuda')
                if found != stream:
                    stream, last_sink = found, None      # force a re-apply
                sv = sink_volume()
                if sv is not None and sv != last_sink and stream is not None:
                    hold_level(stream, cfg['audio'].get('independent_level', 0.35), sv)
                    last_sink = sv
            # Suspend on the way down, resume on the way back up.
            if mon is not None:
                try:
                    data = mon.stdout.read()
                except (BlockingIOError, TypeError, ValueError):
                    data = None
                for ln in (data or '').splitlines():
                    if 'PrepareForSleep' not in ln:
                        continue
                    # Match on the argument after the signal name rather than an
                    # exact "(true,)" - gdbus spacing is not worth depending on.
                    arg = ln.rsplit('PrepareForSleep', 1)[1]
                    if 'true' in arg:
                        slog(f"suspend signalled, {sleep_coast:.1f}s coast")
                        handle('spindown')
                        coast_until = now + sleep_coast
                        # logind stops honouring delay inhibitors at
                        # InhibitDelayMaxSec (5s default) and simply proceeds.
                        # Never be the reason a machine is slow to sleep.
                        release_ceiling = now + 4.5
                    elif 'false' in arg:
                        slog("resumed")
                        handle('spinup')
                        coast_until = release_ceiling = drain_t0 = None
                        draining = parked = False
                        if inhibitor is None or inhibitor.poll() is not None:
                            inhibitor = take_sleep_inhibitor()
            # Coast is over. Stop writing and close the player: the whole coast
            # is already queued, and the only thing left is to let it be heard.
            if coast_until is not None and now >= coast_until:
                coast_until = None
                draining = True; drain_t0 = now
                try: player.stdin.close()
                except OSError: pass
            # Let go when the sound has actually finished - process exit is the
            # signal - or at the ceiling if the player somehow never dies. A
            # delay inhibitor must never be why a laptop is slow to sleep.
            if draining:
                done = player.poll() is not None
                if not (done or (release_ceiling and now >= release_ceiling)):
                    # Nothing to write, so nothing is pacing the loop - sleep,
                    # or this spins a core flat out waiting for the suspend.
                    time.sleep(0.01)
                    continue
                slog(f"drained in {now - drain_t0:.3f}s" if done else
                     f"CEILING HIT at {now - drain_t0:.3f}s, player still alive "
                     "- releasing anyway, expect a tail")
                draining = False; parked = True; release_ceiling = None
                if not done:
                    player.terminate()
                if inhibitor is not None:
                    inhibitor.terminate(); inhibitor = None
                # Back on its feet before the freeze rather than after it, so a
                # resume signal that never arrives cannot leave the daemon mute.
                player = start_player(fs)
                stream = last_sink = None          # new stream, re-apply volume
                slog("inhibitor released, parked")
            if parked:
                # Released but not yet frozen. Writing now would refill the pipe
                # we just emptied, and that is exactly what plays back on resume.
                time.sleep(0.05)
                continue
            b = eng.block(BLK, rate)*vol
            np.clip(b, -1, 1, out=b)
            player.stdin.write((b*32767).astype('<i2').tobytes())
            player.stdin.flush()
    except KeyboardInterrupt:
        if cfg['behaviour']['spin_down_on_exit'] and not eng.at_rest(eng.t):
            print("\nspinning down...", file=sys.stderr)
            eng.spin('down', eng.t)
            for _ in range(int(B.SPINDOWN_COAST*fs/BLK) + 20):
                b = eng.block(BLK, 0)*vol
                np.clip(b, -1, 1, out=b)
                player.stdin.write((b*32767).astype('<i2').tobytes())
        player.stdin.close(); player.wait()
        for pr in (sub, mon, inhibitor):
            if pr is not None:
                try: pr.terminate()
                except OSError: pass
        for f in (pidfile, ctlpath):
            try: os.unlink(f)
            except OSError: pass

if __name__ == '__main__':
    main()
