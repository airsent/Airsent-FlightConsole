"""Jetson-owned launch sequence: countdown, holds, GO/NO-GO, arming, takeoff and faults.

The Jetson is the single source of truth. Every console only displays the
snapshot broadcast in telemetry and sends requests; nothing here trusts a
browser's clock or state.

Stages (in order):
  standby -> countdown -> prearm (vehicle handover) -> gonogo -> armed (T-10)
  -> takeoff (T-0) -> flight -> landed -> safed            (or aborted)

Rules:
  * A check is *gated* to a stage. Once the sequence reaches that stage, a
    failing check HOLDS the countdown with that check's message.
  * A hold disarms the aircraft if it is armed on the ground.
  * Resume needs the pilot in command (PIC) and every gated check passing.
    Resuming at/after T-60 restarts from T-60 (GO/NO-GO is re-confirmed).
    A hold longer than RECYCLE_AFTER restarts from the vehicle handover.
  * Abort is one request, from any operator, and never needs a password.
  * After a Jetson restart an active countdown comes back HELD, never running.

`vehicle` is any object with: set_mode(name), arm(), disarm(force=False),
takeoff(alt), land(), rtl(). The server supplies a MAVLink implementation.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import threading
import time
from collections import deque
from pathlib import Path

STAGES = ['standby', 'countdown', 'prearm', 'gonogo', 'armed', 'takeoff',
          'flight', 'landed', 'safed', 'aborted']
STAGE_INDEX = {name: i for i, name in enumerate(STAGES)}
PRE_TAKEOFF = ('countdown', 'prearm', 'gonogo', 'armed')
ACTIVE = ('countdown', 'prearm', 'gonogo', 'armed', 'takeoff', 'flight', 'landed')

# Gate -> first stage at which a failing check blocks the sequence.
GATE_STAGE = {'create': 'countdown', 'countdown': 'countdown', 'prearm': 'prearm',
              'gonogo': 'gonogo', 'armed': 'armed', 'takeoff': 'takeoff'}

MANUAL_CHECKS = {
    'manual_wind':      'Wind and weather within limits',
    'manual_airspace':  'Airspace authorization (LAANC) confirmed',
    'manual_observers': 'Visual observers in position',
    'manual_payload':   'Payload secured',
}


def env_flag(name, default):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ('1', 'true', 'yes', 'on')


def env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def default_config(sim=False):
    channel = os.environ.get('AIRSENT_RC_GATE_CHANNEL')
    return {
        'handover_at': 180,          # T-3:00 vehicle handover
        'gonogo_at': 60,             # T-60 GO/NO-GO poll
        'arm_at': 10,                # T-10 arm (needs DISARM_DELAY > 10 on the Pixhawk)
        'takeoff_alt': env_float('AIRSENT_TAKEOFF_ALT', 5.0),
        'recycle_after': 120,        # hold longer than this -> back to T-3:00
        'min_total': 60, 'max_total': 900,
        'require_rtk': env_flag('AIRSENT_REQUIRE_RTK', True),
        'require_esc_telemetry': env_flag('AIRSENT_REQUIRE_ESC_TELEMETRY', True),
        'rc_gate_channel': int(channel) if channel and channel.isdigit() else None,
        # ArduPilot "Motor Emergency Stop" (RCx_OPTION=31): HIGH = stopped.
        'rc_gate_arm_max_pwm': int(env_float('AIRSENT_RC_GATE_ARM_MAX_PWM', 1300)),
        'rc_gate_safe_min_pwm': int(env_float('AIRSENT_RC_GATE_SAFE_MIN_PWM', 1700)),
        'min_arm_voltage': env_float('AIRSENT_MIN_ARM_VOLTAGE', 22.8),   # 6S @ 3.8 V/cell
        'min_battery_pct': env_float('AIRSENT_MIN_BATTERY_PCT', 60),
        'min_sats': int(env_float('AIRSENT_MIN_SATS', 10)),
        'max_hdop': env_float('AIRSENT_MAX_HDOP', 1.2),
        'max_vibration': env_float('AIRSENT_MAX_VIBRATION', 30.0),
        'min_disk_gb': env_float('AIRSENT_MIN_DISK_GB', 5.0),
        'motor_spread': env_float('AIRSENT_MOTOR_RPM_SPREAD', 0.25),
        'motor_count': int(env_float('AIRSENT_MOTOR_COUNT', 4)),
        'max_jetson_temp': env_float('AIRSENT_MAX_JETSON_TEMP', 80),
        'sim': sim,
    }


def fmt_clock(seconds):
    seconds = max(0, int(math.ceil(seconds)))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def safe_name(name):
    cleaned = re.sub(r'[^A-Za-z0-9._ -]+', '', str(name or '')).strip()
    cleaned = re.sub(r'\s+', '-', cleaned)[:48].strip('.-')
    return cleaned


def rc_gate_position(snapshot, config):
    """SAFE / ARM / UNKNOWN from the configured RC channel, or SIM in SITL."""
    channel = config.get('rc_gate_channel')
    if channel is None:
        return 'SIM' if config.get('sim') else 'NOT_CONFIGURED'
    rc = snapshot.get('rc_signal') or {}
    channels = snapshot.get('rc_channels') or []
    if not rc.get('input_present') or not (1 <= channel <= len(channels)):
        return 'UNKNOWN'
    pwm = channels[channel - 1]
    if not isinstance(pwm, (int, float)) or pwm <= 0 or pwm == 65535:
        return 'UNKNOWN'
    if pwm <= config['rc_gate_arm_max_pwm']:
        return 'ARM'
    if pwm >= config['rc_gate_safe_min_pwm']:
        return 'SAFE'
    return 'UNKNOWN'


# ── Checks ────────────────────────────────────────────────────────────────────
def check(cid, group, label, gate, ok, value='', need='', warn=False, note=''):
    """ok: True -> pass, False -> fail (or warn if warn=True), None -> unknown/fail."""
    if ok is True:
        raw = 'pass'
    elif ok is None:
        raw = 'fail'
    else:
        raw = 'warn' if warn else 'fail'
    return {'id': cid, 'group': group, 'label': label, 'gate': gate, 'raw': raw,
            'value': value, 'need': need, 'note': note}


def evaluate_checks(s, engine):
    """s is the telemetry snapshot plus Jetson extras. Returns the ordered check list."""
    cfg = engine.config
    out = []
    now = s.get('_now', time.time())
    px = bool(s.get('pixhawk_connected'))
    out.append(check('pixhawk', 'Link', 'Flight controller connected', 'create', px,
                     'CONNECTED' if px else 'OFFLINE', 'heartbeat < 3 s'))
    disarmed = not s.get('armed')
    if engine.stage in ('standby', 'countdown', 'prearm', 'gonogo'):
        out.append(check('disarmed', 'Vehicle', 'Disarmed on the ground before arming', 'create', disarmed,
                         'DISARMED' if disarmed else 'ARMED', 'disarmed'))
    disk = s.get('_disk_free_gb')
    out.append(check('disk', 'Jetson', 'Mission log storage free', 'create',
                     disk is not None and disk >= cfg['min_disk_gb'],
                     f"{disk:.1f} GB" if disk is not None else '--', f">= {cfg['min_disk_gb']:.0f} GB"))
    relay = s.get('_vps_relay_connected')
    out.append(check('vps_link', 'Link', 'Jetson to console relay', 'countdown', bool(relay),
                     'CONNECTED' if relay else 'DOWN', 'connected'))
    pic_age = engine.pic_age(now)
    out.append(check('pic_console', 'Link', 'Pilot-in-command console connected', 'countdown',
                     pic_age is not None and pic_age < 6,
                     f"{pic_age:.0f}s ago" if pic_age is not None else 'NO PIC', 'seen < 6 s'))
    net = s.get('network_link') or {}
    pct = net.get('percent')
    net_ok = bool(net.get('connected'))
    if cfg.get('sim') and not net_ok:            # SITL machine: no modem / NetworkManager to read
        out.append(check('network', 'Link', 'Network link quality', 'prearm', True, 'SIM (not checked)', 'connected, >= 30%'))
    else:
        out.append(check('network', 'Link', 'Network link quality', 'prearm',
                         net_ok and (pct is None or pct >= 30) if net_ok else False,
                         f"{(net.get('type') or '--').upper()} {pct if pct is not None else '--'}%",
                         'connected, >= 30%', warn=net_ok))

    gate = rc_gate_position(s, cfg)
    gate_label = {'SIM': 'SIM (no RC gate)', 'NOT_CONFIGURED': 'NOT CONFIGURED'}.get(gate, gate)
    if engine.stage in ('standby', 'countdown', 'prearm'):
        safe_ok = gate == 'SAFE' or gate == 'SIM'
        out.append(check('rc_gate_safe', 'RC', 'RC arm switch in SAFE until GO/NO-GO', 'create',
                         safe_ok, gate_label, 'SAFE'))
    else:
        arm_ok = gate == 'ARM' or gate == 'SIM'
        out.append(check('rc_gate_arm', 'RC', 'RC arm switch in ARM (pilot sees area clear)', 'armed',
                         arm_ok, gate_label, 'ARM by T-10'))
    rc = s.get('rc_signal') or {}
    out.append(check('rc_link', 'RC', 'RC receiver signal present', 'prearm',
                     bool(rc.get('input_present')) or cfg.get('sim'),
                     'PRESENT' if rc.get('input_present') else 'NO SIGNAL', 'present'))

    golden = s.get('_golden') or {}
    gstate = golden.get('state')
    diffs = golden.get('diffs') or []
    out.append(check('golden_params', 'Config', 'Pixhawk parameters match the approved set', 'prearm',
                     gstate == 'ok', {'ok': 'MATCH', 'loading': 'READING...', 'unavailable': 'NO GOLDEN FILE'}.get(gstate, f"{len(diffs)} DIFFERENT"),
                     f"{golden.get('count', 0)} params", note='; '.join(diffs[:6])))
    sh = s.get('sensor_health') or {}
    prearm = sh.get('prearm')
    statustext = s.get('_last_prearm') or ''
    out.append(check('ardupilot_prearm', 'Config', 'ArduPilot pre-arm checks', 'prearm', prearm == 'ok',
                     'PASS' if prearm == 'ok' else (statustext[:60] or (prearm or 'unknown').upper().replace('_', ' ')),
                     'all pass'))

    fix = s.get('fix_type') or 'NO FIX'
    rtk = fix == 'RTK FIX'
    if cfg['require_rtk']:
        out.append(check('gps_fix', 'GPS', 'GPS fix: RTK fixed', 'prearm', rtk, fix, 'RTK FIX'))
    else:
        out.append(check('gps_fix', 'GPS', 'GPS fix (RTK not required by config)', 'prearm',
                         fix in ('3D FIX', 'DGPS', 'RTK FLOAT', 'RTK FIX'), fix, '3D FIX or better'))
    sats = s.get('sats') or 0
    out.append(check('gps_sats', 'GPS', 'Satellites', 'prearm', sats >= cfg['min_sats'], str(sats), f">= {cfg['min_sats']}"))
    hdop = s.get('hdop')
    out.append(check('gps_hdop', 'GPS', 'Horizontal dilution (HDOP)', 'prearm',
                     isinstance(hdop, (int, float)) and 0 < hdop <= cfg['max_hdop'],
                     f"{hdop:.2f}" if isinstance(hdop, (int, float)) else '--', f"<= {cfg['max_hdop']}"))
    home = s.get('home') or {}
    out.append(check('home', 'GPS', 'Home position set', 'prearm', bool(home.get('set')),
                     'SET' if home.get('set') else 'NOT SET', 'set'))
    out.append(check('ekf', 'Estimator', 'EKF healthy', 'prearm', bool(s.get('ekf_healthy')) and px,
                     'HEALTHY' if s.get('ekf_healthy') and px else 'UNHEALTHY', 'healthy'))

    volt = s.get('voltage')
    out.append(check('battery_volt', 'Battery', 'Battery voltage', 'prearm',
                     isinstance(volt, (int, float)) and volt >= cfg['min_arm_voltage'],
                     f"{volt:.1f} V" if isinstance(volt, (int, float)) and volt > 1 else '--', f">= {cfg['min_arm_voltage']} V"))
    batt = s.get('battery')
    out.append(check('battery_pct', 'Battery', 'Battery remaining', 'prearm',
                     isinstance(batt, (int, float)) and batt >= cfg['min_battery_pct'],
                     f"{batt:.0f}%" if isinstance(batt, (int, float)) else '--', f">= {cfg['min_battery_pct']:.0f}%"))

    for key, label in (('imu', 'IMU'), ('baro', 'Barometer')):
        st = sh.get(key)
        out.append(check(key, 'Sensors', label, 'prearm', st == 'ok', (st or 'unknown').upper(), 'OK'))
    mag = sh.get('mag')
    out.append(check('mag', 'Sensors', 'Compass', 'prearm', mag == 'ok',
                     (mag or 'unknown').upper().replace('_', ' '), 'OK', warn=mag in (None, 'unknown', 'not_reported'),
                     note='' if mag == 'ok' else 'compass health not reported by the Pixhawk; ArduPilot pre-arm still covers it'))
    ages = s.get('sensor_ages') or {}
    rng_age = ages.get('rangefinder')
    out.append(check('lidar', 'Sensors', 'LiDAR rangefinder (H-Flow)', 'prearm',
                     rng_age is not None and rng_age < 3 and (s.get('rangefinder') or 0) > 0,
                     f"{s.get('rangefinder'):.2f} m" if (s.get('rangefinder') or 0) > 0 else 'OFFLINE', 'live'))
    of_age = ages.get('of')
    out.append(check('flow', 'Sensors', 'Optical flow (H-Flow)', 'prearm',
                     of_age is not None and of_age < 3,
                     f"Q {s.get('of_quality', 0)}" if of_age is not None and of_age < 3 else 'OFFLINE', 'live'))
    vib = [s.get(k) for k in ('vib_x', 'vib_y', 'vib_z')]
    if all(isinstance(v, (int, float)) for v in vib):
        out.append(check('vibration', 'Sensors', 'Vibration', 'prearm', max(vib) < cfg['max_vibration'],
                         f"{max(vib):.1f} m/s²", f"< {cfg['max_vibration']:.0f}"))
    else:
        out.append(check('vibration', 'Sensors', 'Vibration', 'prearm', False, 'NO DATA', 'reported', warn=True))

    fence = (s.get('pilot_settings') or {}).get('fence') or {}
    fence_on = fence.get('FENCE_ENABLE') == 1
    out.append(check('fence', 'Safety', 'Geofence and max altitude active on Pixhawk', 'prearm', fence_on,
                     'ENABLED' if fence_on else ('DISABLED' if fence.get('FENCE_ENABLE') == 0 else 'UNKNOWN'), 'enabled'))

    if cfg.get('sim'):                           # SITL has no cameras
        out.append(check('front_cam', 'Jetson', 'Front camera (obstacle detection)', 'prearm', True, 'SIM (no camera)', 'streaming'))
        out.append(check('down_cam', 'Jetson', 'Down camera', 'prearm', True, 'SIM (no camera)', 'streaming'))
    else:
        out.append(check('front_cam', 'Jetson', 'Front camera (obstacle detection)', 'prearm',
                         bool(s.get('front_cam_active')), 'STREAMING' if s.get('front_cam_active') else 'OFFLINE', 'streaming'))
        out.append(check('down_cam', 'Jetson', 'Down camera', 'prearm', bool(s.get('down_cam_active')),
                         'STREAMING' if s.get('down_cam_active') else 'OFFLINE', 'streaming', warn=True))
    temp = s.get('jetson_temp')
    out.append(check('jetson_temp', 'Jetson', 'Jetson temperature', 'prearm',
                     isinstance(temp, (int, float)) and temp < cfg['max_jetson_temp'],
                     f"{temp:.0f} °C" if isinstance(temp, (int, float)) else '--', f"< {cfg['max_jetson_temp']:.0f} °C",
                     warn=not isinstance(temp, (int, float))))

    for key, label in MANUAL_CHECKS.items():
        ticked = engine.manual.get(key)
        out.append(check(key, 'Manual', label, 'gonogo', bool(ticked),
                         f"✓ {ticked['by']}" if ticked else 'NOT CONFIRMED', 'confirm by T-60'))

    out.append(check('go_poll', 'GO/NO-GO', 'Pilot in command GO (area and props clear)', 'armed',
                     bool(engine.go), f"GO · {engine.go['by']}" if engine.go else 'WAITING', 'GO by T-10'))

    prox = s.get('proximity') or {}
    obstacle = bool(prox.get('obstacle'))
    out.append(check('obstacles', 'Safety', 'No obstacle close to the aircraft', 'takeoff', not obstacle,
                     f"OBSTACLE {prox.get('dist', -1):.1f} m" if obstacle else 'CLEAR', 'clear'))

    if engine.stage in ('armed', 'takeoff'):
        armed = bool(s.get('armed'))
        out.append(check('armed_confirmed', 'Vehicle', 'Aircraft confirms ARMED', 'takeoff', armed,
                         'ARMED' if armed else 'NOT ARMED', 'armed'))
    out.append(engine.motor_check(s))
    return out


class FaultBook:
    """Active and recent faults. Acknowledging silences a fault; it never clears it."""
    def __init__(self, clock=time.time, clear_after=2.0, history=200):
        self.clock = clock
        self.clear_after = clear_after
        self.active = {}
        self.recent = deque(maxlen=history)
        self.revision = 0
        self._seq = 0
        self.on_change = None

    def update(self, conditions):
        """conditions: key -> (severity, source, message) for every fault that is true now."""
        now = self.clock()
        for key, (severity, source, message) in conditions.items():
            fault = self.active.get(key)
            if fault is None:
                self._seq += 1
                fault = {'id': f"F{self._seq:05d}", 'key': key, 'severity': severity, 'source': source,
                         'message': message, 'since': now, 'acked_by': None, 'acked_at': None,
                         'cleared_at': None, 'last_true': now}
                self.active[key] = fault
                self.revision += 1
                if self.on_change:
                    self.on_change('raised', fault)
            else:
                fault['last_true'] = now
                if fault['message'] != message or fault['severity'] != severity:
                    fault['message'], fault['severity'] = message, severity
                    self.revision += 1
        for key in list(self.active):
            if key not in conditions and now - self.active[key]['last_true'] >= self.clear_after:
                fault = self.active.pop(key)
                fault['cleared_at'] = now
                self.recent.appendleft(fault)
                self.revision += 1
                if self.on_change:
                    self.on_change('cleared', fault)

    def ack(self, fault_id, who):
        for fault in self.active.values():
            if fault_id in (fault['id'], 'all') and not fault['acked_by']:
                fault['acked_by'], fault['acked_at'] = who, self.clock()
                self.revision += 1
                if self.on_change:
                    self.on_change('acked', fault)
        return True

    def snapshot(self, recent=25):
        clean = lambda f: {k: v for k, v in f.items() if k != 'last_true'}
        order = {'critical': 0, 'warning': 1, 'info': 2}
        active = sorted(self.active.values(), key=lambda f: (order.get(f['severity'], 3), f['since']))
        return {'revision': self.revision, 'active': [clean(f) for f in active],
                'recent': [clean(f) for f in list(self.recent)[:recent]],
                'counts': {sev: sum(1 for f in self.active.values() if f['severity'] == sev)
                           for sev in ('critical', 'warning', 'info')}}


class MissionRecorder:
    """Writes everything about one mission into its own folder on the Jetson SSD."""
    def __init__(self, root):
        self.root = Path(root)
        self.folder = None
        self._files = {}
        self._lock = threading.Lock()
        self.last_telemetry = 0.0

    def open(self, folder):
        self.close()
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)

    def append(self, name, record):
        if not self.folder:
            return
        with self._lock:
            handle = self._files.get(name)
            if handle is None:
                handle = open(self.folder / name, 'a', buffering=1)
                self._files[name] = handle
            handle.write(json.dumps(record, default=str, separators=(',', ':')) + '\n')

    def write(self, name, data):
        if not self.folder:
            return
        temporary = self.folder / (name + '.tmp')
        temporary.write_text(json.dumps(data, indent=2, default=str))
        temporary.replace(self.folder / name)

    def close(self):
        with self._lock:
            for handle in self._files.values():
                try:
                    handle.close()
                except OSError:
                    pass
            self._files.clear()


class MissionEngine:
    def __init__(self, state_path, missions_root, vehicle=None, clock=time.time, config=None):
        self.state_path = Path(state_path)
        self.recorder = MissionRecorder(missions_root)
        self.missions_root = Path(missions_root)
        self.vehicle = vehicle
        self.clock = clock
        self.config = config or default_config()
        self.lock = threading.RLock()
        self.faults = FaultBook(clock)
        self.faults.on_change = self._fault_changed
        self.consoles = {}
        self.events = deque(maxlen=60)
        self.checks = []
        self.revision = 0
        self.last_snapshot = {}
        self._reset_state()
        self._load()

    # ── state ────────────────────────────────────────────────────────────────
    def _reset_state(self):
        self.stage = 'standby'
        self.mission = None
        self.total = 180
        self.deadline = None
        self.remaining = 180.0
        self.held = False
        self.hold_reason = ''
        self.hold_since = None
        self.go = None
        self.manual = {}
        self.pic_console = None
        self.pic_name = None
        self.arm_sent_at = None
        self.takeoff_at = None
        self.flight_started = None
        self.landed_at = None
        self.abort = None
        self.motor_samples = []
        self.max_alt = 0.0
        self.handover_logged = False
        self._kill_logged = False

    PERSIST = ('stage', 'mission', 'total', 'deadline', 'remaining', 'held', 'hold_reason', 'hold_since',
               'go', 'manual', 'pic_console', 'pic_name', 'arm_sent_at', 'takeoff_at', 'flight_started',
               'landed_at', 'abort', 'max_alt', 'handover_logged')

    def _save(self):
        data = {k: getattr(self, k) for k in self.PERSIST}
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(data, default=str))
        temporary.replace(self.state_path)

    def _load(self):
        if not self.state_path.exists():
            return
        try:
            data = json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            return
        for key in self.PERSIST:
            if key in data:
                setattr(self, key, data[key])
        if self.mission and self.mission.get('folder'):
            self.recorder.open(self.mission['folder'])
        if self.stage in PRE_TAKEOFF:
            if self.deadline is not None and not self.held:
                self.remaining = max(0.0, self.deadline - self.clock())
            self.deadline = None
            self.held = True
            self.hold_since = self.clock()
            self.hold_reason = 'Jetson server restarted — confirm before resuming'
            self._event('hold', self.hold_reason, 'Jetson')
        elif self.stage in ('takeoff', 'flight', 'landed'):
            self._event('info', 'Jetson server restarted during flight — monitoring resumed', 'Jetson')
        self._save()

    def _event(self, kind, message, who='Jetson'):
        record = {'t': self.clock(), 'kind': kind, 'msg': message, 'who': who,
                  'stage': self.stage, 'clock': self.clock_text()}
        self.events.appendleft(record)
        self.recorder.append('events.jsonl', record)
        self.revision += 1
        print(f"[MISSION] {kind.upper()}: {message} ({who})")

    def _fault_changed(self, change, fault):
        if self.mission and self.stage in ACTIVE:
            self.recorder.append('faults.jsonl', {'change': change, **{k: v for k, v in fault.items() if k != 'last_true'}})

    # ── helpers ──────────────────────────────────────────────────────────────
    def countdown_remaining(self, now=None):
        now = self.clock() if now is None else now
        if self.stage in PRE_TAKEOFF and self.deadline is not None and not self.held:
            return max(0.0, self.deadline - now)
        return float(self.remaining)

    def clock_text(self):
        if self.stage in ('takeoff', 'flight', 'landed', 'safed') and self.takeoff_at:
            return 'T+' + fmt_clock(self.clock() - self.takeoff_at)
        if self.stage in PRE_TAKEOFF or self.stage == 'aborted':
            return 'T-' + fmt_clock(self.countdown_remaining())
        return 'T-' + fmt_clock(self.total)

    def pic_age(self, now):
        info = self.consoles.get(self.pic_console) if self.pic_console else None
        return now - info['last'] if info else None

    def _stage_for(self, remaining):
        c = self.config
        if remaining <= 0:
            return 'takeoff'
        if remaining <= c['arm_at']:
            return 'armed'
        if remaining <= c['gonogo_at']:
            return 'gonogo'
        if remaining <= min(c['handover_at'], self.total):
            return 'prearm'
        return 'countdown'

    def blocking(self, stage=None):
        stage = stage or self.stage
        level = STAGE_INDEX[stage]
        return [c for c in self.checks
                if c['raw'] == 'fail' and STAGE_INDEX[GATE_STAGE[c['gate']]] <= level]

    def motor_check(self, s):
        cfg = self.config
        esc = s.get('esc_telemetry') or {}
        channels = [c for c in esc.get('channels') or [] if c.get('connected')][:cfg['motor_count']]
        if not esc.get('connected') or len(channels) < cfg['motor_count']:
            if cfg['require_esc_telemetry'] and not cfg.get('sim'):
                return check('motor_health', 'Motors', 'Motor health at idle (ESC telemetry)', 'takeoff', False,
                             f"ESC TELEMETRY {len(channels)}/{cfg['motor_count']}", 'all motors reporting')
            return check('motor_health', 'Motors', 'Motor health at idle (ESC telemetry)', 'takeoff', False,
                         'NOT VERIFIED', 'ESC telemetry', warn=True,
                         note='ESC telemetry unavailable; motor health not verified')
        latched = getattr(self, '_motor_latched', None)
        if self.stage in ('takeoff', 'flight', 'landed', 'safed') and latched:
            return latched                      # result measured at idle before takeoff
        if self.stage != 'armed' or not self.arm_sent_at or self.clock() - self.arm_sent_at < 3:
            self._motor_latched = None
            return check('motor_health', 'Motors', 'Motor health at idle (ESC telemetry)', 'takeoff', None,
                         'WAITING FOR SPIN-UP', 'evaluated after arming')
        rpms = [c.get('rpm') or 0 for c in channels]
        errors = [c['channel'] for c in channels if c.get('status')]
        mean = sum(rpms) / len(rpms) if rpms else 0
        spread = (max(rpms) - min(rpms)) / mean if mean > 0 else 1
        ok = mean > 0 and min(rpms) > 0 and spread <= cfg['motor_spread'] and not errors
        value = ' / '.join(str(r) for r in rpms) + ' rpm'
        note = f"ESC status flags on {errors}" if errors else ('a motor is not spinning' if min(rpms) <= 0 else '')
        self._motor_latched = check('motor_health', 'Motors', 'Motor health at idle (ESC telemetry)', 'takeoff', ok, value,
                                    f"all spinning, spread <= {cfg['motor_spread']:.0%}", note=note)
        return self._motor_latched

    # ── requests from consoles ───────────────────────────────────────────────
    def handle(self, cmd):
        """Returns a reply dict (echoing request_id). Never raises for bad input."""
        reply = self._handle(cmd)
        if cmd.get('request_id') is not None:
            reply['request_id'] = str(cmd.get('request_id'))[:64]
        return reply

    def _handle(self, cmd):
        with self.lock:
            action = cmd.get('action', '')
            console = str(cmd.get('console_id') or '')[:64]
            who = str(cmd.get('operator') or 'operator')[:40]
            now = self.clock()
            try:
                if action == 'mission_ping':
                    if console:
                        self.consoles[console] = {'last': now, 'operator': who, 'page': str(cmd.get('page', ''))[:40]}
                    return {'ok': True, 'type': 'mission_ping_ack', 'quiet': True}
                if console:
                    self.consoles.setdefault(console, {'last': now, 'operator': who, 'page': ''})['last'] = now
                if action == 'mission_create':
                    return self._create(cmd, console, who, now)
                if action == 'mission_abort':
                    return self._abort(cmd, who, now)
                if action == 'mission_hold':
                    if self.stage not in PRE_TAKEOFF:
                        raise ValueError('Hold is only available before takeoff')
                    self._hold(f"Hold requested by {who}", who)
                    return self._ok('Countdown held')
                if action == 'fault_ack':
                    self.faults.ack(str(cmd.get('fault_id', '')), who)
                    return self._ok('Fault acknowledged')
                if action == 'mission_claim_pic':
                    age = self.pic_age(now)
                    if self.pic_console and self.pic_console != console and age is not None and age < 15:
                        raise ValueError('The pilot in command is still connected')
                    self.pic_console, self.pic_name = console, who
                    self._event('pic', f"{who} is now pilot in command", who)
                    self._save()
                    return self._ok('You are pilot in command')
                # Everything below is pilot-in-command only.
                if self.stage != 'standby' and console != self.pic_console:
                    raise ValueError(f"Only the pilot in command ({self.pic_name or 'unknown'}) can do this")
                if action == 'mission_resume':
                    return self._resume(who, now)
                if action == 'mission_go':
                    if self.stage not in ('gonogo', 'prearm'):
                        raise ValueError('GO is accepted from the vehicle handover until T-10')
                    if self.held:
                        raise ValueError('Resume the countdown first')
                    self.go = {'by': who, 'at': now}
                    self._event('go', 'GO — area and props clear', who)
                    self._save()
                    return self._ok('GO recorded')
                if action == 'mission_manual':
                    key = cmd.get('key')
                    if key not in MANUAL_CHECKS:
                        raise ValueError('Unknown manual check')
                    if self.stage not in PRE_TAKEOFF:
                        raise ValueError('Manual checks are confirmed during the countdown')
                    if cmd.get('value'):
                        self.manual[key] = {'by': who, 'at': now}
                        self._event('check', f"Confirmed: {MANUAL_CHECKS[key]}", who)
                    else:
                        self.manual.pop(key, None)
                        self._event('check', f"Unconfirmed: {MANUAL_CHECKS[key]}", who)
                    self._save()
                    return self._ok('Manual check updated')
                if action == 'mission_close':
                    if self.stage not in ('safed', 'aborted'):
                        raise ValueError('Close the mission after the aircraft is safed or aborted')
                    self._close(who)
                    return self._ok('Mission closed')
                raise ValueError(f'Unknown mission action: {action}')
            except ValueError as exc:
                return {'ok': False, 'type': 'mission_result', 'msg': str(exc)}

    def _ok(self, msg):
        self.revision += 1
        return {'ok': True, 'type': 'mission_result', 'msg': msg}

    def _create(self, cmd, console, who, now):
        if self.stage != 'standby':
            raise ValueError('A mission is already active')
        if str(cmd.get('confirm', '')).strip().lower() != 'flight':
            raise ValueError("Type 'flight' to confirm a real flight")
        name = safe_name(cmd.get('name'))
        if not name:
            raise ValueError('Mission name is required')
        total = cmd.get('total', 180)
        if isinstance(total, bool) or not isinstance(total, (int, float)) or not \
                self.config['min_total'] <= total <= self.config['max_total']:
            raise ValueError(f"Countdown must be {self.config['min_total']}-{self.config['max_total']} seconds")
        if not console:
            raise ValueError('Console identity missing')
        blocking = [c for c in self.checks if c['gate'] == 'create' and c['raw'] == 'fail'
                    and c['id'] != 'pic_console']
        if blocking:
            raise ValueError('Cannot start: ' + blocking[0]['label'] + f" ({blocking[0]['value']})")
        stamp = time.strftime('%Y-%m-%d_%H%M', time.localtime(now))
        folder = self.missions_root / f"{stamp}_{name}"
        suffix = 2
        while folder.exists():
            folder = self.missions_root / f"{stamp}_{name}-{suffix}"
            suffix += 1
        self._reset_state()
        self.mission = {'name': str(cmd.get('name'))[:60], 'slug': name, 'folder': str(folder),
                        'created_at': now, 'operator': who, 'pack_id': str(cmd.get('pack_id') or '')[:40],
                        'route': bool(cmd.get('route'))}
        self.recorder.open(folder)
        self.total = int(total)
        self.remaining = float(total)
        self.deadline = now + total
        self.stage = 'countdown'
        self.pic_console, self.pic_name = console, who
        self.consoles[console] = {'last': now, 'operator': who, 'page': ''}
        self.recorder.write('mission.json', {**self.mission, 'config': self.config})
        self._event('create', f"Mission '{self.mission['name']}' created, countdown T-{fmt_clock(total)} started", who)
        self._save()
        return self._ok(f"Mission started: {folder.name}")

    def _hold(self, reason, who='Jetson'):
        if self.held or self.stage not in PRE_TAKEOFF:
            return
        self.remaining = self.countdown_remaining()
        self.deadline = None
        self.held = True
        self.hold_reason = reason
        self.hold_since = self.clock()
        self._event('hold', reason, who)
        if self.stage == 'armed' and self.vehicle:
            self.vehicle.disarm()
            self._event('disarm', 'Disarmed because the countdown is on hold', 'Jetson')
        self._save()

    def _resume(self, who, now):
        if self.stage not in PRE_TAKEOFF or not self.held:
            raise ValueError('The countdown is not on hold')
        cfg = self.config
        held_for = now - (self.hold_since or now)
        remaining = self.remaining
        note = ''
        if held_for > cfg['recycle_after'] and remaining < min(cfg['handover_at'], self.total):
            remaining = float(min(cfg['handover_at'], self.total))
            self.handover_logged = False
            note = f" — held {held_for:.0f}s, recycled to T-{fmt_clock(remaining)}"
        if remaining <= cfg['gonogo_at']:
            remaining = float(cfg['gonogo_at'])
            note = note or f" — restarting from T-{fmt_clock(remaining)}"
        if remaining <= cfg['gonogo_at'] or note:
            self.go = None
        stage = self._stage_for(remaining)
        fails = [c for c in self.checks if c['raw'] == 'fail' and c['id'] != 'go_poll'
                 and c['id'] != 'rc_gate_arm' and STAGE_INDEX[GATE_STAGE[c['gate']]] <= STAGE_INDEX[stage]]
        if fails:
            raise ValueError('Cannot resume: ' + fails[0]['label'] + f" ({fails[0]['value']})")
        if self.vehicle and self.last_snapshot.get('armed'):
            raise ValueError('Aircraft still reports ARMED — disarm before resuming')
        self.held = False
        self.hold_reason = ''
        self.hold_since = None
        self.arm_sent_at = None
        self.remaining = remaining
        self.deadline = now + remaining
        self.stage = stage
        self._event('resume', 'Countdown resumed' + note, who)
        self._save()
        return self._ok('Countdown resumed' + note)

    def _abort(self, cmd, who, now):
        if self.stage in PRE_TAKEOFF:
            if self.vehicle and (self.stage == 'armed' or self.last_snapshot.get('armed')):
                self.vehicle.disarm()
            self.remaining = self.countdown_remaining()
            self.deadline = None
            self.held = False
            self.abort = {'reason': str(cmd.get('reason') or 'Aborted by operator')[:120], 'by': who, 'at': now,
                          'mode': 'disarm'}
            self.stage = 'aborted'
            self._event('abort', 'ABORTED before takeoff — ' + self.abort['reason'], who)
            self._save()
            return self._ok('Mission aborted')
        if self.stage in ('takeoff', 'flight'):
            mode = 'rtl' if cmd.get('mode') == 'rtl' else 'land'
            if self.vehicle:
                (self.vehicle.rtl if mode == 'rtl' else self.vehicle.land)()
            self.abort = {'reason': str(cmd.get('reason') or 'Aborted by operator')[:120], 'by': who, 'at': now,
                          'mode': mode}
            self._event('abort', f"ABORT in flight — {'RETURN TO HOME' if mode == 'rtl' else 'LAND NOW'}", who)
            self._save()
            return self._ok('Abort: ' + ('returning home' if mode == 'rtl' else 'landing'))
        raise ValueError('Nothing to abort')

    def _close(self, who):
        summary = self.summary()
        self.recorder.write('summary.json', summary)
        self._event('close', f"Mission closed ({summary['result']})", who)
        self.recorder.close()
        self._reset_state()
        self._save()

    def summary(self):
        now = self.clock()
        return {
            'mission': self.mission, 'result': 'ABORTED' if self.abort and not self.flight_started else
            ('COMPLETED WITH ABORT' if self.abort else 'COMPLETED'),
            'abort': self.abort, 'takeoff_at': self.takeoff_at, 'landed_at': self.landed_at,
            'flight_seconds': round((self.landed_at or now) - self.takeoff_at, 1) if self.takeoff_at else 0,
            'max_alt_m': round(self.max_alt, 2),
            'faults': [f for f in self.faults.snapshot(200)['recent']],
            'events': list(self.events)[::-1],
        }

    # ── periodic update ──────────────────────────────────────────────────────
    def tick(self, s, extra_faults=None):
        """Call ~5 Hz with the latest telemetry snapshot (+ Jetson extras)."""
        with self.lock:
            now = self.clock()
            s = dict(s)
            s['_now'] = now
            self.last_snapshot = s
            self.checks = evaluate_checks(s, self)
            self._advance(s, now)
            # Re-evaluate after a stage change so the display is current.
            self.checks = evaluate_checks(s, self)
            self._update_faults(s, extra_faults or {})
            if self.mission and self.stage in ACTIVE and now - self.recorder.last_telemetry >= 0.2:
                self.recorder.last_telemetry = now
                record = {k: v for k, v in s.items() if not k.startswith('_') and k not in
                          ('mavlink_log', 'mission', 'faults', 'detections')}
                record.update(t=now, mission_stage=self.stage, mission_clock=self.clock_text())
                self.recorder.append('telemetry.jsonl', record)

    def _advance(self, s, now):
        cfg = self.config
        if self.stage in PRE_TAKEOFF and not self.held:
            remaining = self.countdown_remaining(now)
            target = self._stage_for(remaining)
            # Never skip ahead past a stage whose gate is failing.
            for stage in STAGES[STAGE_INDEX[self.stage]:STAGE_INDEX[target] + 1]:
                if stage != self.stage:
                    if stage == 'takeoff':
                        blockers = self.blocking('takeoff')
                        if blockers:     # stay at T-0 on the ground; the hold disarms
                            first = blockers[0]
                            self._hold(f"{first['label']}: {first['value']}" + (f" ({first['note']})" if first['note'] else ''))
                            return
                    self.stage = stage
                    self._enter(stage, s, now)
                    if self.held or self.stage not in PRE_TAKEOFF:
                        return
                blockers = self.blocking(stage)
                if blockers:
                    first = blockers[0]
                    self._hold(f"{first['label']}: {first['value']}" + (f" ({first['note']})" if first['note'] else ''))
                    return
            gate = rc_gate_position(s, cfg)
            if self.stage == 'armed':
                if gate == 'SAFE':
                    self._hold('RC arm switch moved to SAFE')
                    return
                if self.arm_sent_at and now - self.arm_sent_at > 4 and not s.get('armed'):
                    reason = s.get('_last_prearm') or 'no reason reported'
                    self._hold(f"Aircraft did not arm ({reason})")
                    return
            if self.stage == 'takeoff':
                return
        elif self.stage == 'takeoff':
            self._watch_takeoff(s, now)
        elif self.stage == 'flight':
            alt = s.get('alt') or 0
            self.max_alt = max(self.max_alt, alt)
            if not s.get('armed'):
                self.stage = 'landed'
                self.landed_at = now
                self._event('landed', 'Aircraft disarmed — landed')
                self._save()
        elif self.stage == 'landed':
            gate = rc_gate_position(s, cfg)
            if gate in ('SAFE', 'SIM', 'NOT_CONFIGURED') and not s.get('armed'):
                self.stage = 'safed'
                self._event('safed', 'SAFED — RC switch in SAFE, aircraft disarmed. Safe to approach.')
                self.recorder.write('summary.json', self.summary())
                self._save()
        if self.stage in ('takeoff', 'flight'):
            gate = rc_gate_position(s, cfg)
            if gate == 'SAFE' and not self._kill_logged:
                self._kill_logged = True
                self._event('kill', 'RC kill switch to SAFE in flight — motors stopped by Pixhawk', 'RC pilot')

    def _enter(self, stage, s, now):
        cfg = self.config
        if stage == 'prearm' and not self.handover_logged:
            self.handover_logged = True
            self._event('handover', 'VEHICLE HANDOVER — full pre-arm check')
            self.recorder.write('checks_handover.json', {'t': now, 'checks': self.checks})
        elif stage == 'gonogo':
            self._event('gonogo', 'GO/NO-GO — pilot: confirm area clear, RC pilot: switch to ARM')
        elif stage == 'armed':
            blockers = self.blocking('armed')
            if blockers:
                return  # _advance holds with the reason
            if self.vehicle:
                self.vehicle.set_mode('GUIDED')
                self.vehicle.arm()
            self.arm_sent_at = now
            self.motor_samples = []
            self._event('arm', f"ARM — motors to idle (T-{fmt_clock(cfg['arm_at'])})")
            self.recorder.write('params_at_arm.json', {'t': now, 'golden': s.get('_golden')})
        elif stage == 'takeoff':
            if not s.get('armed'):
                self.stage = 'armed'
                self._hold('Aircraft is not armed at T-0')
                return
            if self.vehicle:
                self.vehicle.takeoff(cfg['takeoff_alt'])
            self.takeoff_at = now
            self.remaining = 0
            self.deadline = None
            self._kill_logged = False
            self.recorder.write('checks_t0.json', {'t': now, 'checks': self.checks})
            self._event('takeoff', f"T-0 — TAKEOFF to {cfg['takeoff_alt']:.0f} m")
        self._save()

    def _watch_takeoff(self, s, now):
        cfg = self.config
        t = now - (self.takeoff_at or now)
        alt = s.get('alt') or 0
        self.max_alt = max(self.max_alt, alt)
        tilt = max(abs(s.get('roll') or 0), abs(s.get('pitch') or 0))
        if not s.get('armed') and t > 1:
            self.stage = 'landed'
            self.landed_at = now
            self._event('landed', 'Aircraft disarmed during takeoff')
        elif t < 3 and tilt > 25 and alt < 1.0:
            if self.vehicle:
                self.vehicle.disarm(force=True)
            self.abort = {'reason': f'Tip-over on takeoff ({tilt:.0f}°)', 'by': 'Jetson', 'at': now, 'mode': 'disarm'}
            self._event('abort', f"TAKEOFF ABORT — tilt {tilt:.0f}° near the ground, disarmed")
            self.stage = 'landed'
            self.landed_at = now
        elif t > 6 and alt < 0.5:
            if self.vehicle:
                if alt < 0.3:
                    self.vehicle.disarm()
                else:
                    self.vehicle.land()
            self.abort = {'reason': f'No climb after {t:.0f}s (alt {alt:.1f} m)', 'by': 'Jetson', 'at': now,
                          'mode': 'disarm' if alt < 0.3 else 'land'}
            self._event('abort', f"TAKEOFF ABORT — no climb after {t:.0f}s")
            self.stage = 'landed' if alt < 0.3 else 'flight'
            if self.stage == 'landed':
                self.landed_at = now
        elif alt >= 0.9 * cfg['takeoff_alt']:
            self.stage = 'flight'
            self.flight_started = now
            route = bool((self.mission or {}).get('route'))
            self._event('flight', f"Climb complete — hovering at {alt:.1f} m" + (', starting the route (AUTO)' if route else ''))
            if route and self.vehicle:
                self.vehicle.set_mode('AUTO')
        else:
            return
        self._save()

    def _update_faults(self, s, extra):
        conditions = dict(extra)
        if self.mission and self.stage in PRE_TAKEOFF:
            for c in self.blocking(self.stage):
                conditions['check:' + c['id']] = ('critical', 'Countdown', f"{c['label']}: {c['value']}")
            if self.held:
                conditions['mission:hold'] = ('critical', 'Countdown', f"HOLD — {self.hold_reason}")
        if self.stage in ('takeoff', 'flight') and self.abort:
            conditions['mission:abort'] = ('critical', 'Countdown', f"ABORT — {self.abort['reason']}")
        self.faults.update(conditions)

    # ── broadcast ────────────────────────────────────────────────────────────
    def snapshot(self):
        with self.lock:
            now = self.clock()
            level = STAGE_INDEX[self.stage]
            checks = []
            for c in self.checks:
                gate_level = STAGE_INDEX[GATE_STAGE[c['gate']]]
                shown = c['raw'] if (self.stage != 'standby' and level >= gate_level) or c['raw'] == 'pass' else 'pending'
                if self.stage in ('flight', 'landed', 'safed', 'aborted') and c['gate'] in ('takeoff', 'armed'):
                    shown = c['raw'] if c['raw'] == 'pass' else 'pending'
                checks.append({**c, 'status': shown})
            cfg = self.config
            gate = rc_gate_position(self.last_snapshot, cfg)
            pic_age = self.pic_age(now)
            esc = (self.last_snapshot.get('esc_telemetry') or {}).get('channels') or []
            motors = [{'ch': c.get('channel'), 'rpm': c.get('rpm'), 'current': c.get('current'),
                       'connected': c.get('connected'), 'status': c.get('status')}
                      for c in esc[:cfg['motor_count']]]
            return {
                'v': 1, 'revision': self.revision, 'server_time': now,
                'stage': self.stage, 'held': self.held, 'hold_reason': self.hold_reason,
                'hold_elapsed': round(now - self.hold_since, 1) if self.held and self.hold_since else 0,
                'mission': self.mission, 'total': self.total,
                'remaining': round(self.countdown_remaining(now), 2),
                't_plus': round(now - self.takeoff_at, 1) if self.takeoff_at else None,
                'clock': self.clock_text(),
                'marks': {'handover': min(cfg['handover_at'], self.total), 'gonogo': cfg['gonogo_at'],
                          'arm': cfg['arm_at']},
                'go': self.go, 'manual': self.manual, 'rc_gate': gate,
                'pic': {'console': self.pic_console, 'name': self.pic_name,
                        'connected': pic_age is not None and pic_age < 6},
                'consoles': [{'console': k, 'operator': v['operator'], 'page': v['page'],
                              'age': round(now - v['last'], 1)} for k, v in self.consoles.items()
                             if now - v['last'] < 30],
                'checks': checks, 'motors': motors,
                'events': list(self.events)[:20], 'abort': self.abort,
                'max_alt': round(self.max_alt, 2),
                'config': {k: cfg[k] for k in ('takeoff_alt', 'require_rtk', 'require_esc_telemetry',
                                                'rc_gate_channel', 'handover_at', 'gonogo_at', 'arm_at',
                                                'recycle_after', 'sim')},
            }


def disk_free_gb(path):
    try:
        Path(path).mkdir(parents=True, exist_ok=True)
        return shutil.disk_usage(path).free / 1e9
    except OSError:
        return None
