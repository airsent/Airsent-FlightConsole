"""ASC — Automated Self Checks.

A flight watchdog on the Jetson. From the vehicle handover (pre-arm) or arming
until the drone is disarmed on the ground it:

  * runs every check below many times a second (default 20 Hz),
  * raises warnings / critical findings to every console (through the fault list),
  * records closely ("focus") anything that is not OK,
  * records everything for the flight into its mission folder,
  * writes a summary on disarm for later (AI) analysis.

ASC checks and records; it does not fight the pilot or the Pixhawk. It acts on
its own in exactly one case nothing else can detect: the operator console link
is lost while the drone flies a console-driven mode (GUIDED / AUTO). Then it
holds position (LOITER) after 10 s and returns home (RTL) after 30 s. If the RC
pilot or a Pixhawk failsafe changes the mode, ASC stands down.
"""
import gzip
import json
import math
import os
import queue
import shutil
import threading
import time
from collections import deque
from datetime import datetime

OK, INFO, WARN, CRIT = 0, 1, 2, 3
LEVEL_NAME = {OK: 'ok', INFO: 'info', WARN: 'warning', CRIT: 'critical'}
CONSOLE_MODES = ('GUIDED', 'AUTO')
FAILSAFE_WORDS = {'radio': 'rc', 'rc ': 'rc', 'throttle': 'rc', 'battery': 'battery', 'gcs': 'gcs', 'ekf': 'ekf',
                  'fence': 'fence', 'crash': 'crash', 'vibration': 'vibe', 'terrain': 'terrain', 'adsb': 'adsb'}
ALERT_TEXTS = ('failsafe', 'crash', 'thrust loss', 'gps glitch', 'ekf variance', 'fence breach', 'vibration compensation',
               'yaw imbalance', 'mag anomaly', 'battery', 'lost', 'emergency')


def env_f(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def default_config():
    return {
        'rate_hz': env_f('AIRSENT_ASC_HZ', 20),
        'hold_after': env_f('AIRSENT_ASC_HOLD_AFTER', 10),       # console link lost → LOITER
        'rtl_after': env_f('AIRSENT_ASC_RTL_AFTER', 30),         # console link lost → RTL
        'max_tilt_warn': 35, 'max_tilt_crit': 50,
        'max_climb': 4.0, 'max_descent': 3.5,
        'max_speed': env_f('AIRSENT_ASC_MAX_SPEED', 15),
        'fence_alt': env_f('AIRSENT_ASC_FENCE_ALT', 120), 'fence_radius': env_f('AIRSENT_ASC_FENCE_RADIUS', 2000),
        'cells': int(env_f('AIRSENT_BATT_CELLS', 6)), 'cell_warn': 3.6, 'cell_crit': 3.5,
        'max_current': env_f('AIRSENT_ASC_MAX_CURRENT', 70), 'sag_volts': 1.5,
        'vib_warn': env_f('AIRSENT_MAX_VIBRATION', 30), 'vib_crit': 60,
        'min_sats': 10, 'max_hdop': 1.5,
        'motor_high_pwm': 1900, 'motor_spread_pct': 25, 'esc_spread': 0.25,
        'max_jetson_temp': 85, 'min_disk_gb': 20, 'keep_free_gb': 30,
        'flow_quality_min': 50, 'low_agl': 5.0,
        'max_rssi_low': 30, 'net_min_pct': 20,
    }


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _dist_m(a, b):
    if not a or not b:
        return None
    r = 6371000.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


class Check:
    """One check: fn(s, ctx) -> (level, message, value). `sustain` seconds before it is raised."""
    def __init__(self, cid, group, label, fn, when='session', sustain=1.0, fault=True):
        self.id, self.group, self.label, self.fn = cid, group, label, fn
        self.when, self.sustain, self.fault = when, sustain, fault


def build_checks(cfg):
    C = []
    add = lambda *a, **k: C.append(Check(*a, **k))

    # ── link / flight controller ───────────────────────────────────────────
    add('fc_link', 'Link', 'Pixhawk heartbeat', lambda s, c: (CRIT, 'No Pixhawk heartbeat', s.get('pixhawk_heartbeat_age'))
        if not s.get('pixhawk_connected') else (OK, '', s.get('pixhawk_heartbeat_age')), sustain=0.5, fault=False)
    add('ekf', 'Estimator', 'EKF health', lambda s, c: (CRIT if c['flying'] else WARN, 'EKF unhealthy', 0)
        if s.get('ekf_healthy') is False else (OK, '', 1), sustain=0.5, fault=False)
    add('vps_link', 'Link', 'Jetson ↔ VPS relay', lambda s, c: (WARN, 'Relay to the consoles is down', 0)
        if c['vps'] is False else (OK, '', 1), sustain=2)
    add('console', 'Link', 'Operator console', lambda s, c: (
        (CRIT if c['console_age'] >= cfg['hold_after'] else WARN), f"No operator console for {c['console_age']:.0f} s", c['console_age'])
        if c['console_seen'] and c['console_age'] is not None and c['console_age'] >= 4 else (OK, '', c['console_age']), sustain=0)
    add('network', 'Link', 'Network quality', lambda s, c: (
        (WARN, f"Network weak: {(s.get('network_link') or {}).get('percent')}%", (s.get('network_link') or {}).get('percent'))
        if _num((s.get('network_link') or {}).get('percent')) and (s.get('network_link') or {}).get('percent') < cfg['net_min_pct'] else (OK, '', (s.get('network_link') or {}).get('percent'))), sustain=3)
    add('rc', 'RC', 'RC receiver', lambda s, c: (WARN, 'RC receiver signal lost', 0)
        if c['real'] and not (s.get('rc_signal') or {}).get('input_present') else (OK, '', 1), sustain=1, fault=False)
    add('rc_rssi', 'RC', 'RC signal strength', lambda s, c: (WARN, f"RC signal weak: {s.get('rssi')}%", s.get('rssi'))
        if _num(s.get('rssi')) and 0 < s.get('rssi') < cfg['max_rssi_low'] else (OK, '', s.get('rssi')), sustain=2)

    # ── attitude / motion ──────────────────────────────────────────────────
    def tilt(s, c):
        r, p = s.get('roll'), s.get('pitch')
        if not (_num(r) and _num(p)):
            return OK, '', None
        t = max(abs(r), abs(p))
        if t >= cfg['max_tilt_crit']:
            return CRIT, f'Extreme tilt {t:.0f}°', t
        if t >= cfg['max_tilt_warn']:
            return WARN, f'High tilt {t:.0f}°', t
        return OK, '', t
    add('tilt', 'Attitude', 'Tilt angle', tilt, when='flying', sustain=0.3)

    def wobble(s, c):
        h = c['hist']
        rolls = [x.get('roll') for x in h if _num(x.get('roll'))][-40:]
        if len(rolls) < 20:
            return OK, '', None
        m = sum(rolls) / len(rolls)
        sd = math.sqrt(sum((x - m) ** 2 for x in rolls) / len(rolls))
        return (WARN, f'Attitude oscillating (roll σ {sd:.1f}°)', sd) if sd > 6 else (OK, '', sd)
    add('oscillation', 'Attitude', 'Attitude oscillation', wobble, when='flying', sustain=1)

    def vspeed(s, c):
        v = s.get('vspd')
        if not _num(v):
            return OK, '', None
        if v > cfg['max_climb']:
            return WARN, f'Climbing fast {v:.1f} m/s', v
        if -v > cfg['max_descent'] and s.get('flight_mode') != 'LAND':
            return WARN, f'Descending fast {-v:.1f} m/s', v
        return OK, '', v
    add('vspeed', 'Motion', 'Vertical speed', vspeed, when='flying', sustain=0.5)
    add('speed', 'Motion', 'Ground speed', lambda s, c: (WARN, f"Fast: {s.get('spd'):.1f} m/s", s.get('spd'))
        if _num(s.get('spd')) and s.get('spd') > cfg['max_speed'] else (OK, '', s.get('spd')), when='flying', sustain=1)

    # ── position / altitude ────────────────────────────────────────────────
    add('gps_fix', 'GPS', 'GPS fix', lambda s, c: (WARN if not c['flying'] else CRIT, f"GPS: {s.get('fix_type') or 'NO FIX'}", s.get('fix_type'))
        if (s.get('fix_type') or 'NO FIX') in ('NO FIX', '2D FIX') else (OK, '', s.get('fix_type')), sustain=1, fault=False)
    add('gps_sats', 'GPS', 'Satellites', lambda s, c: (WARN, f"Only {s.get('sats')} satellites", s.get('sats'))
        if _num(s.get('sats')) and s.get('sats') < cfg['min_sats'] else (OK, '', s.get('sats')), sustain=2)
    add('gps_hdop', 'GPS', 'HDOP', lambda s, c: (WARN, f"HDOP {s.get('hdop'):.2f}", s.get('hdop'))
        if _num(s.get('hdop')) and s.get('hdop') > cfg['max_hdop'] else (OK, '', s.get('hdop')), sustain=2)

    def gps_jump(s, c):
        h = c['hist']
        if len(h) < 2 or not (_num(s.get('lat')) and _num(h[-2].get('lat'))):
            return OK, '', None
        d = _dist_m((h[-2]['lat'], h[-2]['lon']), (s['lat'], s['lon']))
        dt = max(1e-3, c['dt'])
        allowed = (max(s.get('spd') or 0, h[-2].get('spd') or 0) + 3) * dt + 3
        return (WARN, f'Position jumped {d:.0f} m', d) if d and d > allowed else (OK, '', d)
    add('gps_jump', 'GPS', 'Position jumps', gps_jump, when='flying', sustain=0)
    add('home', 'GPS', 'Home position', lambda s, c: (WARN, 'Home not set — RTL has nowhere to go', 0)
        if c['armed'] and not (s.get('home') or {}).get('set') else (OK, '', 1), when='armed', sustain=2)

    def fence_alt(s, c):
        a = s.get('alt')
        if not _num(a):
            return OK, '', None
        lim = c['fence_alt'] or cfg['fence_alt']
        return (WARN, f'{a:.0f} m — close to the {lim:.0f} m ceiling', a) if a > 0.9 * lim else (OK, '', a)
    add('fence_alt', 'Geofence', 'Altitude vs ceiling', fence_alt, when='flying', sustain=0.5)

    def fence_dist(s, c):
        d = c['home_dist']
        if d is None:
            return OK, '', None
        lim = c['fence_radius'] or cfg['fence_radius']
        return (WARN, f'{d:.0f} m from home — close to the {lim:.0f} m fence', d) if d > 0.9 * lim else (OK, '', d)
    add('fence_dist', 'Geofence', 'Distance vs fence', fence_dist, when='flying', sustain=0.5)

    def agl(s, c):
        rf, a = s.get('rangefinder_agl'), s.get('alt')
        if not (_num(rf) and _num(a)) or rf <= 0 or a > 8:
            return OK, '', rf
        diff = abs(a - rf)
        return (WARN, f'LiDAR {rf:.1f} m vs altitude {a:.1f} m', diff) if diff > 2.5 else (OK, '', diff)
    add('lidar_agree', 'Altitude', 'LiDAR vs altitude', agl, when='flying', sustain=2)
    add('lidar_low', 'Altitude', 'LiDAR near the ground', lambda s, c: (WARN, 'No LiDAR reading close to the ground', s.get('rangefinder'))
        if c['real'] and _num(s.get('alt')) and 0.5 < s.get('alt') < 6 and not (_num(s.get('rangefinder')) and s.get('rangefinder') > 0)
        else (OK, '', s.get('rangefinder')), when='flying', sustain=2)
    add('flow', 'Altitude', 'Optical flow quality', lambda s, c: (WARN, f"Optical flow quality {s.get('of_quality')}", s.get('of_quality'))
        if c['real'] and _num(s.get('alt')) and s.get('alt') < cfg['low_agl'] and _num(s.get('of_quality')) and s.get('of_quality') < cfg['flow_quality_min']
        else (OK, '', s.get('of_quality')), when='flying', sustain=2)

    # ── vibration ──────────────────────────────────────────────────────────
    def vib(s, c):
        v = [s.get(k) for k in ('vib_x', 'vib_y', 'vib_z') if _num(s.get(k))]
        if not v:
            return OK, '', None
        m = max(v)
        if m >= cfg['vib_crit']:
            return CRIT, f'Severe vibration {m:.0f} m/s²', m
        return (WARN, f'High vibration {m:.0f} m/s²', m) if m >= cfg['vib_warn'] else (OK, '', m)
    add('vibration', 'Vibration', 'Vibration', vib, when='armed', sustain=1, fault=True)

    # ── power ──────────────────────────────────────────────────────────────
    def cell(s, c):
        v = s.get('voltage')
        if not _num(v) or v < 1:
            return OK, '', None
        per = v / cfg['cells']
        if per <= cfg['cell_crit']:
            return CRIT, f'Battery {v:.1f} V ({per:.2f} V/cell)', per
        return (WARN, f'Battery {v:.1f} V ({per:.2f} V/cell)', per) if per <= cfg['cell_warn'] else (OK, '', per)
    add('cell_voltage', 'Power', 'Cell voltage', cell, sustain=2, fault=False)

    def sag(s, c):
        h = [x.get('voltage') for x in list(c['hist'])[-int(cfg['rate_hz']):] if _num(x.get('voltage'))]
        if len(h) < 5:
            return OK, '', None
        d = max(h) - min(h)
        return (WARN, f'Voltage sagged {d:.1f} V in 1 s', d) if d > cfg['sag_volts'] else (OK, '', d)
    add('sag', 'Power', 'Voltage sag', sag, when='armed', sustain=0)
    add('current', 'Power', 'Current draw', lambda s, c: (WARN, f"High current {s.get('current'):.0f} A", s.get('current'))
        if _num(s.get('current')) and s.get('current') > cfg['max_current'] else (OK, '', s.get('current')), when='armed', sustain=1)

    def reserve(s, c):
        """Rough: can the remaining battery get the drone home? (alert only)"""
        pct, d = s.get('battery'), c['home_dist']
        rate = c['drain_pct_per_s']
        if not (_num(pct) and d is not None and rate and rate > 0):
            return OK, '', None
        need = (d / 5.0) * rate * 1.3 + 15          # 5 m/s home, 30 % margin, 15 % to land
        return (WARN, f'Battery {pct:.0f}% — about {need:.0f}% needed to get home', need) if pct < need else (OK, '', need)
    add('reserve', 'Power', 'Energy to return home', reserve, when='flying', sustain=3)

    # ── motors / ESC ───────────────────────────────────────────────────────
    def motor_sat(s, c):
        m = [s.get(k) for k in ('m1', 'm2', 'm3', 'm4') if _num(s.get(k))]
        if not m:
            return OK, '', None
        hi = max(m)
        return (WARN, f'A motor is near full power ({hi:.0f} µs)', hi) if hi >= cfg['motor_high_pwm'] else (OK, '', hi)
    add('motor_sat', 'Motors', 'Motor saturation', motor_sat, when='flying', sustain=1)

    def motor_bal(s, c):
        m = [s.get(k) for k in ('m1', 'm2', 'm3', 'm4') if _num(s.get(k))]
        if len(m) < 4 or max(m) < 1150:
            return OK, '', None
        spread = (max(m) - min(m)) / 10.0              # µs → % of a 1000 µs range
        return (WARN, f'Motor outputs uneven by {spread:.0f}% (balance / CG / motor)', spread) if spread > cfg['motor_spread_pct'] else (OK, '', spread)
    add('motor_balance', 'Motors', 'Motor balance', motor_bal, when='flying', sustain=2)

    def esc(s, c):
        e = s.get('esc_telemetry') or {}
        if not c['real'] or not e:
            return OK, '', None
        ch = [x for x in e.get('channels') or [] if x.get('connected')]
        if not e.get('connected'):
            return WARN, 'ESC telemetry lost', 0
        bad = [x['channel'] for x in ch if x.get('status')]
        if bad:
            return CRIT, f'ESC error on motor {bad}', len(bad)
        rpm = [x.get('rpm') or 0 for x in ch]
        if c['flying'] and rpm and min(rpm) <= 0:
            return CRIT, 'A motor reports 0 rpm in flight', 0
        if c['flying'] and len(rpm) >= 2 and max(rpm) > 0:
            mean = sum(rpm) / len(rpm)
            sp = (max(rpm) - min(rpm)) / mean if mean else 0
            if sp > cfg['esc_spread'] * 1.6:
                return WARN, f'Motor rpm uneven by {sp:.0%}', sp
        return OK, '', len(ch)
    add('esc', 'Motors', 'ESC telemetry', esc, when='armed', sustain=0.5)

    # ── Jetson / cameras ───────────────────────────────────────────────────
    add('jetson_temp', 'Jetson', 'Jetson temperature', lambda s, c: (WARN, f"Jetson {s.get('jetson_temp'):.0f} °C", s.get('jetson_temp'))
        if _num(s.get('jetson_temp')) and s.get('jetson_temp') >= cfg['max_jetson_temp'] else (OK, '', s.get('jetson_temp')), sustain=3, fault=False)
    add('jetson_cpu', 'Jetson', 'Jetson CPU', lambda s, c: (WARN, f"Jetson CPU {s.get('cpu'):.0f}%", s.get('cpu'))
        if _num(s.get('cpu')) and s.get('cpu') > 95 else (OK, '', s.get('cpu')), sustain=5)
    add('jetson_ram', 'Jetson', 'Jetson memory', lambda s, c: (WARN, f"Jetson memory {s.get('ram'):.0f}%", s.get('ram'))
        if _num(s.get('ram')) and s.get('ram') > 92 else (OK, '', s.get('ram')), sustain=5)
    add('disk', 'Jetson', 'Storage', lambda s, c: (WARN, f"Only {c['disk']:.0f} GB free", c['disk'])
        if _num(c['disk']) and c['disk'] < cfg['min_disk_gb'] else (OK, '', c['disk']), sustain=0, fault=False)
    add('front_cam', 'Cameras', 'Front camera', lambda s, c: (WARN, 'Front camera offline', 0)
        if c['real'] and not s.get('front_cam_active') else (OK, '', 1), sustain=2, fault=False)
    add('down_cam', 'Cameras', 'Down camera', lambda s, c: (WARN, 'Down camera offline', 0)
        if c['real'] and not s.get('down_cam_active') else (OK, '', 1), sustain=2, fault=False)

    def obstacle(s, c):
        p = s.get('proximity') or {}
        d = p.get('closest') if isinstance(p, dict) else None
        stop = s.get('stop_distance')
        if not (_num(d) and _num(stop)):
            return OK, '', d
        return (WARN, f'Obstacle {d:.1f} m ahead', d) if d < stop else (OK, '', d)
    add('obstacle', 'Cameras', 'Obstacle distance', obstacle, when='flying', sustain=0.3)
    add('loop', 'ASC', 'ASC loop timing', lambda s, c: (WARN, f"ASC running slow ({c['loop_ms']:.0f} ms/tick)", c['loop_ms'])
        if c['loop_ms'] > 2000 / cfg['rate_hz'] else (OK, '', c['loop_ms']), sustain=3)
    return C


# ── recording ──────────────────────────────────────────────────────────────
SAMPLE_KEYS = ('armed', 'flight_mode', 'roll', 'pitch', 'yaw', 'alt', 'vspd', 'spd', 'lat', 'lon', 'sats', 'hdop', 'fix_type',
               'ekf_healthy', 'voltage', 'current', 'battery', 'vib_x', 'vib_y', 'vib_z', 'rangefinder', 'rangefinder_agl',
               'of_quality', 'm1', 'm2', 'm3', 'm4', 'rssi', 'jetson_temp', 'cpu', 'gpu', 'ram')


def _compact(v):
    if isinstance(v, float):
        return round(v, 4)
    return v


class Recorder:
    """Writes a session's files from a background thread, so ASC never blocks on disk."""
    def __init__(self, folder):
        self.folder = folder
        os.makedirs(folder, exist_ok=True)
        self.q = queue.Queue(maxsize=20000)
        self.dropped = 0
        self._files = {}
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def write(self, name, record):
        try:
            self.q.put_nowait((name, record))
        except queue.Full:
            self.dropped += 1

    def _open(self, name):
        f = self._files.get(name)
        if f is None:
            path = os.path.join(self.folder, name)
            f = gzip.open(path, 'at', compresslevel=4) if name.endswith('.gz') else open(path, 'a')
            self._files[name] = f
        return f

    def _run(self):
        while True:
            item = self.q.get()
            if item is None:
                break
            name, record = item
            try:
                if name.endswith('.json'):
                    with open(os.path.join(self.folder, name), 'w') as f:
                        json.dump(record, f, indent=1, default=str)
                else:
                    self._open(name).write(json.dumps(record, separators=(',', ':'), default=str) + '\n')
            except OSError as e:
                print(f'[ASC] write failed {name}: {e}')
            finally:
                self.q.task_done()
        for f in self._files.values():
            try:
                f.close()
            except OSError:
                pass

    def flush(self):
        self.q.join()

    def close(self):
        self.q.put(None)
        self._t.join(timeout=10)


def make_room(root, keep_free_gb, protect=(), min_free_gb=None):
    """When free space drops below min_free_gb, delete the oldest mission folders until
    keep_free_gb is free. Never deletes `protect`."""
    min_free_gb = keep_free_gb if min_free_gb is None else min_free_gb
    removed = []
    try:
        if shutil.disk_usage(root).free / 1e9 >= min_free_gb:
            return removed
        folders = sorted((os.path.join(root, d) for d in os.listdir(root)
                          if os.path.isdir(os.path.join(root, d))), key=os.path.getmtime)
    except OSError:
        return removed
    for path in folders:
        try:
            if shutil.disk_usage(root).free / 1e9 >= keep_free_gb:
                break
        except OSError:
            break
        if os.path.abspath(path) in {os.path.abspath(p) for p in protect if p}:
            continue
        shutil.rmtree(path, ignore_errors=True)
        removed.append(os.path.basename(path))
    return removed


# ── the watchdog ───────────────────────────────────────────────────────────
class ASC:
    def __init__(self, root, config=None, clock=time.time, real=True):
        self.cfg = config or default_config()
        self.root, self.clock, self.real = root, clock, real
        self.checks = build_checks(self.cfg)
        self.act_enabled = True
        self.reset()

    def reset(self):
        self.session = None            # dict while watching
        self.rec = None
        self.state = {}                # check id -> {'level','since','raised','msg','value'}
        self.hist = deque(maxlen=200)
        self.action = None             # {'stage': 'hold'|'rtl'|'stood_down', ...}
        self.triggered = deque(maxlen=20)
        self.events = deque(maxlen=60)
        self.last_mode = None
        self.console_mode_at = 0.0
        self.checks_run = 0
        self._rate_win = deque(maxlen=60)
        self.loop_ms = 0.0
        self._last_t = None
        self._seen_text = set()
        self.console_seen_at = None
        self.console_seen_in_session = False
        self.last_summary = None
        self.post_thread = None
        self.seq = 0
        self.boot = int(self.clock())
        self.pending_param_dump = False
        self._ticks = 0

    # inputs ────────────────────────────────────────────────────────────────
    def console_heard(self, now=None):
        now = now or self.clock()
        self.console_seen_at = now
        if self.session:
            self.console_seen_in_session = True

    def console_mode_command(self, now=None):
        """A console asked for a mode change (so the next mode change is not 'external')."""
        self.console_mode_at = now or self.clock()

    QUIET = {'mission_ping', 'param_read', 'wp_download', 'get_log'}

    def note_command(self, action, who='Operator', data=None, now=None):
        """Operator commands go into the flight record (joystick input into its own file)."""
        if not self.session or action in self.QUIET:
            return
        now = now or self.clock()
        t = round(now - self.session['start'], 3)
        if action == 'velocity':
            d = data or {}
            self.rec.write('controls.jsonl.gz', {'t': t, 'who': who, **{k: d.get(k) for k in ('vx', 'vy', 'vz', 'yaw_rate')}})
            return
        detail = {k: v for k, v in (data or {}).items() if k not in ('action', 'request_id', 'console_id', 'operator', 'page', 'plan', 'params')}
        self._event('command', f'{who}: {action}' + (f' {detail}' if detail else ''), INFO)

    def write_params(self, values):
        if self.session and values:
            self.rec.write('params_start.json', {'count': len(values), 'params': values})

    def statustext(self, text, now=None):
        """Pixhawk messages: failsafes and other warnings become events / triggered flags."""
        now = now or self.clock()
        low = text.lower()
        key = (round(now), low)
        if key in self._seen_text or not any(w in low for w in ALERT_TEXTS):
            return
        self._seen_text.add(key)
        if 'failsafe' in low:
            kind = next((v for k, v in FAILSAFE_WORDS.items() if k in low), 'other')
            cleared = 'clear' in low or 'resolved' in low
            if cleared:
                for f in self.triggered:
                    if f['kind'] == kind:
                        f['cleared'] = True
            self.triggered.appendleft({'t': now, 'kind': kind, 'text': text, 'cleared': cleared})
            self._event('failsafe', f'PIXHAWK: {text}', CRIT if 'clear' not in low else INFO)
        else:
            self._event('pixhawk', f'PIXHAWK: {text}', WARN)

    # session lifecycle ─────────────────────────────────────────────────────
    def _open_session(self, now, folder):
        os.makedirs(self.root, exist_ok=True)
        if not folder:
            folder = os.path.join(self.root, datetime.fromtimestamp(now).strftime('%Y-%m-%d_%H%M%S') + '_flight')
        removed = make_room(self.root, self.cfg['keep_free_gb'], protect=[folder], min_free_gb=self.cfg['min_disk_gb']) if self.cfg['keep_free_gb'] else []
        asc_dir = os.path.join(folder, 'asc')
        self.rec = Recorder(asc_dir)
        self.session = {'folder': folder, 'dir': asc_dir, 'start': now, 'armed_at': None, 'stats': {},
                        'max_alt': 0.0, 'max_dist': 0.0, 'min_volt': None, 'max_current': 0.0, 'mah': 0.0,
                        'max_vib': 0.0, 'actions': [], 'failsafes': [], 'console_losses': 0, 'flying_s': 0.0,
                        'start_pct': None, 'end_pct': None}
        self.console_seen_in_session = bool(self.console_seen_at and now - self.console_seen_at < 5)
        self._event('session', f'ASC recording to {os.path.basename(folder)}/asc', INFO)
        self.pending_param_dump = True
        bad = [f'{c.label}: {self.state[c.id]["msg"]}' for c in self.checks
               if self.state.get(c.id, {}).get('raised') and self.state[c.id].get('active', True)]
        if bad:
            self._event('check', 'Already wrong at the start — ' + '; '.join(bad), WARN)
        if removed:
            self._event('storage', f'Removed old mission folders to keep space: {", ".join(removed)}', INFO)

    def _close_session(self, now):
        s = self.session
        if not s:
            return
        self._event('session', 'ASC session closed — summary written', INFO)
        stats = {cid: v for cid, v in s['stats'].items()}
        summary = {
            'format': 'airsent-asc-summary-1', 'folder': s['folder'],
            'started': datetime.fromtimestamp(s['start']).isoformat(), 'ended': datetime.fromtimestamp(now).isoformat(),
            'duration_s': round(now - s['start'], 1), 'flying_s': round(s['flying_s'], 1),
            'checks_run': self.checks_run, 'checks_per_s': self.checks_per_s(),
            'max_alt_m': round(s['max_alt'], 1), 'max_dist_m': round(s['max_dist'], 1),
            'min_voltage': s['min_volt'], 'max_current_a': round(s['max_current'], 1), 'used_mah': round(s['mah']),
            'battery_pct': [s['start_pct'], s['end_pct']], 'max_vibration': round(s['max_vib'], 1),
            'console_link_losses': s['console_losses'], 'asc_actions': s['actions'], 'pixhawk_failsafes': s['failsafes'],
            'checks': stats, 'samples_dropped': self.rec.dropped if self.rec else 0,
            'files': {'asc/samples.jsonl.gz': 'every tick (20 Hz): flight values + which checks were not OK',
                      'asc/samples.csv': 'the same as a spreadsheet',
                      'asc/telemetry_full.jsonl.gz': 'the complete console telemetry frame, 5 Hz',
                      'asc/events.jsonl': 'findings, actions, mode changes, Pixhawk messages, operator commands',
                      'asc/controls.jsonl.gz': 'every joystick / velocity command',
                      'asc/focus.jsonl.gz': 'detail while a check was not OK',
                      'asc/params_start.json': 'every Pixhawk parameter at the start',
                      'asc/mavlink.tlog': 'every MAVLink message from the Pixhawk (MAVExplorer)',
                      'asc/charts/*.png': 'flight charts'},
        }
        self.rec.write('summary.json', summary)
        self.rec.close()
        self.post_thread = threading.Thread(target=postprocess, args=(s['dir'],), daemon=True)
        self.post_thread.start()
        self.last_summary = summary
        self.session, self.rec = None, None
        self.action = None
        for st in self.state.values():
            st.update(level=OK, raised=False)

    @property
    def tlog_path(self):
        return os.path.join(self.session['dir'], 'mavlink.tlog') if self.session else None

    # main tick ─────────────────────────────────────────────────────────────
    def tick(self, s, now=None, mission=None, extras=None):
        """s: telemetry snapshot. mission: mission engine snapshot (stage, folder).
        Returns a list of actions for the server to carry out: [('mode', 'LOITER'|'RTL', reason)]."""
        t0 = time.perf_counter()
        now = now or self.clock()
        extras = extras or {}
        dt = (now - self._last_t) if self._last_t else 1.0 / self.cfg['rate_hz']
        self._last_t = now
        armed = bool(s.get('armed'))
        alt = s.get('alt') if _num(s.get('alt')) else 0.0
        flying = armed and (alt > 0.5 or (_num(s.get('vspd')) and abs(s.get('vspd')) > 0.3))
        stage = (mission or {}).get('stage') or 'standby'
        mfolder = ((mission or {}).get('mission') or {}).get('folder')
        watching = armed or stage in ('countdown', 'prearm', 'gonogo', 'armed', 'takeoff', 'flight', 'landed')

        if watching and not self.session:
            self._open_session(now, mfolder if stage != 'standby' else None)
        if self.session and not watching and not armed:
            self.session.setdefault('ground_since', now)
            if now - self.session['ground_since'] >= 3:
                self._close_session(now)
                return []
        elif self.session:
            self.session.pop('ground_since', None)

        mode = s.get('flight_mode')
        if self.last_mode is not None and mode != self.last_mode:
            who = 'console' if now - self.console_mode_at < 3 else 'ASC' if self.action and now - self.action.get('at', 0) < 2 else 'RC / Pixhawk'
            self._event('mode', f'Mode {self.last_mode} → {mode} ({who})', INFO)
            if self.action and self.action['stage'] in ('hold', 'rtl') and who == 'RC / Pixhawk':
                self.action = {'stage': 'stood_down', 'at': now, 'why': f'mode changed to {mode} by RC / Pixhawk'}
                self._event('asc_action', f'ASC stands down — {mode} set by RC / Pixhawk', INFO)
        self.last_mode = mode

        home = s.get('home') or {}
        pos = (s['lat'], s['lon']) if _num(s.get('lat')) and _num(s.get('lon')) and (s['lat'] or s['lon']) else None
        home_dist = _dist_m((home['lat'], home['lon']), pos) if home.get('set') and pos else None
        self.hist.append({k: s.get(k) for k in ('roll', 'lat', 'lon', 'spd', 'voltage', 'battery')})
        console_age = (now - self.console_seen_at) if self.console_seen_at else None
        ctx = {'hist': self.hist, 'dt': dt, 'armed': armed, 'flying': flying, 'real': self.real, 'home_dist': home_dist,
               'console_age': console_age, 'console_seen': self.console_seen_in_session and bool(self.session),
               'vps': extras.get('vps'), 'disk': extras.get('disk'), 'fence_alt': extras.get('fence_alt'),
               'fence_radius': extras.get('fence_radius'), 'loop_ms': self.loop_ms, 'drain_pct_per_s': self._drain_rate()}

        ran = 0
        for chk in self.checks:
            if chk.when == 'flying' and not flying or chk.when == 'armed' and not armed:
                self._set(chk, OK, '', None, now, active=False)     # shown grey in the live view
                continue
            try:
                level, msg, value = chk.fn(s, ctx)
            except Exception as e:                       # a broken check must never stop ASC
                level, msg, value = INFO, f'check error: {e}', None
            ran += 1
            self._set(chk, level, msg, value, now)
        self.checks_run += ran
        self._rate_win.append((now, ran))

        actions = self._link_loss(now, flying, mode, console_age, s) if self.session else []

        if self.session:
            self._record(s, now, dt, flying, home_dist, alt)
        self.loop_ms = (time.perf_counter() - t0) * 1000
        return actions

    def _set(self, chk, level, msg, value, now, active=True):
        st = self.state.setdefault(chk.id, {'level': OK, 'since': now, 'raised': False, 'msg': '', 'value': None, 'raised_level': OK})
        st['active'] = active
        if level != st['level']:
            st['level'], st['since'] = level, now
        st['msg'], st['value'] = msg, value
        should = level >= WARN and now - st['since'] >= chk.sustain
        if should and (not st['raised'] or st['raised_level'] != level):
            st['raised'], st['raised_level'] = True, level
            if self.session:
                self._event('check', f'{chk.label}: {msg}', level, check=chk.id, value=value)
                agg = self.session['stats'].setdefault(chk.id, {'label': chk.label, 'group': chk.group, 'raised': 0, 'warn_s': 0.0, 'crit_s': 0.0,
                                                                'first': None, 'last': None, 'worst': None, 'worst_msg': ''})
                agg['raised'] += 1
                agg['first'] = agg['first'] or round(now - self.session['start'], 1)
        elif level < WARN and st['raised']:
            st['raised'], st['raised_level'] = False, OK
            if self.session:
                self._event('check', f'{chk.label}: back to normal', OK, check=chk.id)

    def _link_loss(self, now, flying, mode, age, s):
        cfg = self.cfg
        stage = self.action['stage'] if self.action else None
        if age is not None and age < 2:                          # console is back
            if stage in ('hold', 'rtl'):
                self._event('asc_action', 'Operator console back — the operator is in control (ASC does not resume the mission)', INFO)
            self.action = None
            return []
        if not flying and stage in ('hold', 'rtl'):
            self._event('asc_action', 'Landed while the console link was down', INFO)
            self.action = None
            return []
        if not (self.act_enabled and flying and self.console_seen_in_session and age is not None):
            return []
        if stage == 'stood_down':
            return []
        if age >= cfg['rtl_after'] and stage != 'rtl' and mode not in ('RTL', 'LAND'):
            if stage == 'hold' or mode in CONSOLE_MODES:
                self.action = {'stage': 'rtl', 'at': now, 'age': round(age)}
                self.session['actions'].append({'t': round(now - self.session['start'], 1), 'action': 'RTL', 'console_age_s': round(age)})
                self._event('asc_action', f'No operator console for {age:.0f} s — RETURNING HOME (RTL)', CRIT)
                return [('mode', 'RTL', f'console link lost {age:.0f} s')]
        elif age >= cfg['hold_after'] and stage is None and mode in CONSOLE_MODES:
            self.action = {'stage': 'hold', 'at': now, 'age': round(age), 'from_mode': mode}
            self.session['console_losses'] += 1
            self.session['actions'].append({'t': round(now - self.session['start'], 1), 'action': 'LOITER', 'console_age_s': round(age)})
            self._event('asc_action', f'No operator console for {age:.0f} s — HOLDING POSITION (LOITER); RTL at {cfg["rtl_after"]:.0f} s', CRIT)
            return [('mode', 'LOITER', f'console link lost {age:.0f} s')]
        return []

    def _drain_rate(self):
        pts = [(i, h['battery']) for i, h in enumerate(self.hist) if _num(h.get('battery'))]
        if len(pts) < 100:
            return None
        drop = pts[0][1] - pts[-1][1]
        secs = (pts[-1][0] - pts[0][0]) / self.cfg['rate_hz']
        return drop / secs if drop > 0 and secs > 0 else None

    def _record(self, s, now, dt, flying, home_dist, alt):
        ses = self.session
        if s.get('armed') and not ses['armed_at']:
            ses['armed_at'] = now
        if flying:
            ses['flying_s'] += dt
        ses['max_alt'] = max(ses['max_alt'], alt or 0)
        ses['max_dist'] = max(ses['max_dist'], home_dist or 0)
        v, a, pct = s.get('voltage'), s.get('current'), s.get('battery')
        if _num(v) and v > 1:
            ses['min_volt'] = round(min(ses['min_volt'] or v, v), 2)
        if _num(a):
            ses['max_current'] = max(ses['max_current'], a)
            ses['mah'] += max(0.0, a) * dt / 3.6
        if _num(pct):
            ses['start_pct'] = ses['start_pct'] if ses['start_pct'] is not None else pct
            ses['end_pct'] = pct
        vib = [s.get(k) for k in ('vib_x', 'vib_y', 'vib_z') if _num(s.get(k))]
        if vib:
            ses['max_vib'] = max(ses['max_vib'], max(vib))
        t = round(now - ses['start'], 3)
        sample = {'t': t, **{k: _compact(s.get(k)) for k in SAMPLE_KEYS if s.get(k) is not None}}
        bad = {cid: st['level'] for cid, st in self.state.items() if st['level'] >= WARN}
        if bad:
            sample['issues'] = bad
        self.rec.write('samples.jsonl.gz', sample)
        self._ticks += 1
        if self._ticks % max(1, int(self.cfg['rate_hz'] / 5)) == 0:      # everything the consoles see, 5 Hz
            self.rec.write('telemetry_full.jsonl.gz', {'t': t, **{k: v for k, v in s.items() if k not in ('mavlink_log',)}})
        for cid, st in self.state.items():
            if st['level'] >= WARN:
                self.rec.write('focus.jsonl.gz', {'t': t, 'check': cid, 'level': LEVEL_NAME[st['level']], 'msg': st['msg'],
                                                  'value': _compact(st['value']), 'snapshot': sample})
                agg = ses['stats'].get(cid)
                if agg:
                    agg['warn_s' if st['level'] == WARN else 'crit_s'] = round(agg['warn_s' if st['level'] == WARN else 'crit_s'] + dt, 2)
                    agg['last'] = t
                    if _num(st['value']) and (agg['worst'] is None or abs(st['value']) > abs(agg['worst'])):
                        agg['worst'], agg['worst_msg'] = round(st['value'], 3), st['msg']

    def _event(self, kind, msg, level=INFO, **extra):
        now = self.clock()
        self.seq += 1
        e = {'t': now, 'seq': self.seq, 'kind': kind, 'level': LEVEL_NAME[level], 'msg': msg, **extra}
        self.events.appendleft(e)
        if self.session:
            self.rec.write('events.jsonl', dict(e, t_rel=round(now - self.session['start'], 3)))
            if kind == 'failsafe':
                self.session['failsafes'].append({'t': round(now - self.session['start'], 1), 'text': msg})

    # outputs ───────────────────────────────────────────────────────────────
    def checks_per_s(self):
        if len(self._rate_win) < 2:
            return 0
        span = self._rate_win[-1][0] - self._rate_win[0][0]
        return round(sum(n for _, n in list(self._rate_win)[1:]) / span) if span > 0 else 0

    def fault_conditions(self):
        """Findings for the shared fault list (only while watching, and only checks not already covered)."""
        out = {}
        if not self.session:
            return out
        by = {c.id: c for c in self.checks}
        for cid, st in self.state.items():
            if st['raised'] and st['raised_level'] >= WARN and by[cid].fault:
                out['asc:' + cid] = ('critical' if st['raised_level'] == CRIT else 'warning', 'ASC', st['msg'])
        if self.action and self.action['stage'] in ('hold', 'rtl'):
            out['asc:action'] = ('critical', 'ASC', 'Console link lost — ASC ' + ('holding position' if self.action['stage'] == 'hold' else 'returning home'))
        for f in self.triggered:
            if not f['cleared'] and self.clock() - f['t'] < 60:
                out['asc:fs:' + f['kind']] = ('critical', 'Pixhawk', 'FAILSAFE TRIGGERED: ' + f['text'])
        return out

    def defs(self):
        return [[c.id, c.group, c.label] for c in self.checks]

    def live(self):
        out = []
        for c in self.checks:
            st = self.state.get(c.id)
            if not st:
                continue
            lvl = -1 if not st.get('active', True) else st['level']
            v = st['value']
            row = [c.id, lvl, round(v, 3) if isinstance(v, float) else v]
            if lvl >= WARN:
                row.append(st['msg'])
            out.append(row)
        return out

    def snapshot(self, with_defs=False):
        issues = sorted(([cid, st['raised_level'], st['msg']] for cid, st in self.state.items() if st['raised']), key=lambda x: -x[1])
        now = self.clock()
        return {
            'state': 'acting' if self.action and self.action['stage'] in ('hold', 'rtl') else 'watching' if self.session else 'standby',
            'act_enabled': self.act_enabled,
            'session': os.path.basename(self.session['folder']) if self.session else None,
            'since': self.session['start'] if self.session else None,
            'checks': len(self.checks), 'checks_per_s': self.checks_per_s(),
            'issues': [{'id': i, 'level': LEVEL_NAME[l], 'msg': m} for i, l, m in issues[:10]],
            'console_age': round(now - self.console_seen_at, 1) if self.console_seen_at else None,
            'action': self.action,
            'triggered': [dict(f, age=round(now - f['t'], 1)) for f in self.triggered if now - f['t'] < 120][:5],
            'live': self.live(),
            **({'defs': self.defs()} if with_defs else {}),
            'boot': self.boot,
            'events': [{'seq': e['seq'], 't': round(e['t'], 1), 'level': e['level'], 'msg': e['msg']}
                       for e in list(self.events)[:12] if e['kind'] != 'command'],
        }


# ── after the flight: spreadsheet + charts (runs in its own thread) ─────────
CHART_SET = [
    ('altitude', 'Altitude and climb', [('alt', 'altitude (m)'), ('rangefinder_agl', 'LiDAR AGL (m)'), ('vspd', 'climb (m/s)')]),
    ('power', 'Battery', [('voltage', 'voltage (V)'), ('current', 'current (A)')]),
    ('battery_pct', 'Battery remaining', [('battery', 'remaining (%)')]),
    ('attitude', 'Attitude', [('roll', 'roll (°)'), ('pitch', 'pitch (°)')]),
    ('vibration', 'Vibration', [('vib_x', 'x (m/s²)'), ('vib_y', 'y'), ('vib_z', 'z')]),
    ('motors', 'Motor outputs', [('m1', 'M1 (µs)'), ('m2', 'M2'), ('m3', 'M3'), ('m4', 'M4')]),
    ('gps', 'GPS', [('sats', 'satellites'), ('hdop', 'HDOP')]),
    ('speed', 'Ground speed', [('spd', 'speed (m/s)')]),
    ('flow', 'Optical flow / RC', [('of_quality', 'flow quality'), ('rssi', 'RC signal (%)')]),
    ('jetson', 'Jetson', [('jetson_temp', 'temp (°C)'), ('cpu', 'CPU (%)'), ('ram', 'RAM (%)')]),
]


def postprocess(asc_dir):
    """samples.jsonl.gz → samples.csv and charts/*.png. Never raises."""
    try:
        _postprocess(asc_dir)
    except Exception as e:                     # post-flight extras must never take anything down
        print(f'[ASC] postprocess failed: {e}')


def _postprocess(asc_dir):
    import csv
    path = os.path.join(asc_dir, 'samples.jsonl.gz')
    try:
        with gzip.open(path, 'rt') as f:
            rows = [json.loads(line) for line in f if line.strip()]
    except (OSError, ValueError) as e:
        print(f'[ASC] postprocess: {e}')
        return
    if not rows:
        return
    keys = ['t'] + [k for k in SAMPLE_KEYS if any(k in r for r in rows)] + ['issues']
    try:
        with open(os.path.join(asc_dir, 'samples.csv'), 'w', newline='') as f:
            w = csv.writer(f)
            w.writerow(keys)
            for r in rows:
                w.writerow([';'.join(f'{k}={v}' for k, v in r.get('issues', {}).items()) if k == 'issues' else r.get(k, '') for k in keys])
    except OSError as e:
        print(f'[ASC] samples.csv: {e}')
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except Exception as e:                                     # charts are optional
        print(f'[ASC] charts skipped (matplotlib unavailable: {e})')
        return
    out = os.path.join(asc_dir, 'charts')
    os.makedirs(out, exist_ok=True)
    t = [r['t'] for r in rows]
    style = dict(facecolor='#060d14')
    made = []
    for name, title, series in CHART_SET:
        lines = [(lbl, [r.get(k) if _num(r.get(k)) else float('nan') for r in rows]) for k, lbl in series if any(_num(r.get(k)) for r in rows)]
        if not lines:
            continue
        fig, ax = plt.subplots(figsize=(10, 3.2), **style)
        ax.set_facecolor('#0a1520')
        for lbl, ys in lines:
            ax.plot(t, ys, linewidth=1, label=lbl)
        ax.set_title(title, color='#e8f4ff', fontsize=11, loc='left')
        ax.set_xlabel('seconds since recording started', color='#8fa3b5', fontsize=8)
        ax.tick_params(colors='#8fa3b5', labelsize=8)
        for sp in ax.spines.values():
            sp.set_color('#2a3e4e')
        ax.grid(color='#1a2a38', linewidth=.5)
        ax.legend(loc='upper right', fontsize=7, facecolor='#0a1520', edgecolor='#2a3e4e', labelcolor='#e8f4ff')
        fig.tight_layout()
        fig.savefig(os.path.join(out, f'{name}.png'), dpi=110)
        plt.close(fig)
        made.append(name)
    issues = sorted({k for r in rows for k in r.get('issues', {})})
    if issues:                                                   # which checks were not OK, and when
        fig, ax = plt.subplots(figsize=(10, 0.6 + 0.32 * len(issues)), **style)
        ax.set_facecolor('#0a1520')
        for i, cid in enumerate(issues):
            pts = [(r['t'], r['issues'][cid]) for r in rows if r.get('issues', {}).get(cid)]
            ax.scatter([p[0] for p in pts], [i] * len(pts), marker='|', s=60,
                       c=['#ff3355' if p[1] >= CRIT else '#ffb800' for p in pts])
        ax.set_xlim(t[0], t[-1])
        ax.set_ylim(-0.6, len(issues) - 0.4)
        ax.set_yticks(range(len(issues)), issues)
        ax.set_title('Checks that were not OK (yellow warning, red critical)', color='#e8f4ff', fontsize=11, loc='left')
        ax.tick_params(colors='#8fa3b5', labelsize=8)
        for sp in ax.spines.values():
            sp.set_color('#2a3e4e')
        fig.tight_layout()
        fig.savefig(os.path.join(out, 'checks_timeline.png'), dpi=110)
        plt.close(fig)
        made.append('checks_timeline')
    print(f'[ASC] post-flight files written: samples.csv, charts: {", ".join(made)}')
