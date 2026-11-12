"""Fly the Airsent server against real ArduCopter SITL and check every safety path.

    python3 tests/sitl/run.py [--ardupilot ~/ardupilot] [--only name,name] [--keep]

Starts SITL (fresh EEPROM) and `airsent_server.py --sim`, then runs the scenarios in order.
Writes a report to tests/sitl/last_report.json and prints PASS/FAIL per scenario.
Needs a built SITL: cd ardupilot && ./waf configure --board sitl --disable-DroneCAN && ./waf copter
"""
import argparse
import glob
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
from harness import Console, Vehicle  # noqa: E402

HOME = (33.4213, -111.9327, 360, 0)


def dist_m(a, b):
    r = 6371000.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def offset(lat, lon, north, east):
    return lat + north / 111320.0, lon + east / (111320.0 * math.cos(math.radians(lat)))


class Failed(Exception):
    pass


class Rig:
    def __init__(self, ardupilot, workdir):
        self.ap, self.wd = ardupilot, workdir
        self.sitl = self.server = None
        self.log_lines = []

    def start(self):
        shutil.rmtree(self.wd, ignore_errors=True)          # fresh EEPROM, logs, mission state and folders
        os.makedirs(self.wd, exist_ok=True)
        self.start_sitl()
        # SITL's simulated battery sensor has fixed scaling: approve SITL's values for those two only.
        golden = json.load(open(os.path.join(REPO, 'golden_params.json')))
        golden['params']['BATT_VOLT_MULT'] = {'min': 10.0, 'max': 10.2, 'why': 'SITL sensor scaling'}
        golden['params']['BATT_AMP_PERVLT'] = {'min': 16.9, 'max': 17.1, 'why': 'SITL sensor scaling'}
        # The simulated frame is tuned for 1000-2000 µs; the ZD650's 1100-1940 ESC range makes it unable
        # to descend in SITL (it climbed with 0 % throttle and its land detector disarmed it at 40 m).
        golden['params']['MOT_PWM_MIN'] = {'value': 1000, 'why': 'SITL frame'}
        golden['params']['MOT_PWM_MAX'] = {'value': 2000, 'why': 'SITL frame'}
        # SITL's frame is a 3S quad (its thrust scales with voltage, so 25 V makes it climb at zero throttle):
        # same failsafe logic, 3S thresholds.
        golden['params']['BATT_LOW_VOLT'] = {'value': 10.8, 'why': '3S equivalent of 21.6 V'}
        golden['params']['BATT_CRT_VOLT'] = {'value': 10.5, 'why': '3S equivalent of 21.0 V'}
        gpath = os.path.join(self.wd, 'golden_sitl.json')
        json.dump(golden, open(gpath, 'w'))
        env = dict(os.environ, AIRSENT_GOLDEN_FILE=gpath, AIRSENT_MISSION_DIR=os.path.join(self.wd, 'missions'),
                   AIRSENT_MISSION_STATE=os.path.join(self.wd, 'state.json'), HOME=self.wd,
                   AIRSENT_BIN_DELAY_S='3', PYTHONUNBUFFERED='1',
                   AIRSENT_MAX_HDOP='1.3', AIRSENT_BATT_CELLS='3', AIRSENT_MIN_ARM_VOLTAGE='11.4')        # SITL's simulated u-blox always reports HDOP 1.21
        self.server = subprocess.Popen([sys.executable, '-u', os.path.join(REPO, 'airsent_server.py'), '--sim'], cwd=REPO,
                                       env=env, stdout=open(os.path.join(self.wd, 'server.log'), 'w'), stderr=subprocess.STDOUT)
        end = time.time() + 40
        while time.time() < end:
            if 'Pixhawk connected' in self.server_log():
                break
            time.sleep(0.5)
        else:
            raise RuntimeError('server never connected to SITL')
        time.sleep(2)

    def start_sitl(self):
        binary = os.path.join(self.ap, 'build', 'sitl', 'bin', 'arducopter')
        parm = os.path.join(self.ap, 'Tools', 'autotest', 'default_params', 'copter.parm')
        self.sitl = subprocess.Popen([binary, '--model', '+', '--speedup', '1', '--defaults', parm, '-I0',
                                      '--home', ','.join(map(str, HOME))], cwd=self.wd,
                                     stdout=open(os.path.join(self.wd, 'sitl.log'), 'a'), stderr=subprocess.STDOUT)
        time.sleep(3)

    def power_cycle_sitl(self):
        """Like pulling the Pixhawk's power: the process dies; EEPROM (parameters) survives."""
        self.sitl.kill()
        self.sitl.wait(10)
        time.sleep(4)
        self.start_sitl()

    def server_log(self):
        try:
            return open(os.path.join(self.wd, 'server.log')).read()
        except OSError:
            return ''

    def pause_server(self):
        self.server.send_signal(signal.SIGSTOP)

    def resume_server(self):
        self.server.send_signal(signal.SIGCONT)

    def stop(self):
        for p in (self.server, self.sitl):
            if p and p.poll() is None:
                try:
                    p.send_signal(signal.SIGCONT)
                except Exception:
                    pass
                p.terminate()
                try:
                    p.wait(10)
                except subprocess.TimeoutExpired:
                    p.kill()


class Run:
    def __init__(self, rig):
        self.rig = rig
        self.v = Vehicle()
        self.c = Console()
        self.notes = []

    # ── helpers ───────────────────────────────────────────────────────────
    def note(self, msg):
        self.notes.append(msg)
        print('   ·', msg, flush=True)

    def expect(self, cond, msg):
        if not cond:
            raise Failed(msg)

    def ok(self, reply, what):
        if not reply.get('ok'):
            raise Failed(f"{what}: {reply.get('msg')}")
        return reply

    def wait_ready(self, timeout=90):
        """EKF + GPS good and server telemetry fresh."""
        end = time.time() + timeout
        while time.time() < end:
            t = self.c.telem
            if t.get('pixhawk_connected') and t.get('ekf_healthy') and t.get('fix_type') in ('3D FIX', 'DGPS', 'RTK FLOAT', 'RTK FIX') \
                    and (t.get('home') or {}).get('set'):
                return
            time.sleep(0.5)
        raise Failed('vehicle never became ready (EKF/GPS/home)')

    def ensure_ground(self, timeout=120):
        if self.v.get('armed'):
            self.c.send('set_mode', mode='LAND')
            if not self.v.wait(lambda s: not s['armed'], timeout):
                self.c.send('emergency_stop')
                self.v.wait(lambda s: not s['armed'], 10)
        self.v.wait(lambda s: s['alt'] < 1.0, 30)
        time.sleep(4)            # let the ASC session close

    def takeoff(self, alt=10):
        self.ok(self.c.send('set_mode', mode='GUIDED'), 'set GUIDED')
        self.expect(self.v.wait(lambda s: s['mode'] == 'GUIDED', 5), 'vehicle did not switch to GUIDED')
        self.ok(self.c.send('arm'), 'arm')
        self.ok(self.c.send('takeoff', alt=alt), 'takeoff')
        self.expect(self.v.wait(lambda s: s['alt'] > alt * 0.9, 40), f"did not climb to {alt} m (at {self.v.get('alt'):.1f})")

    def asc(self):
        return (self.c.mission_block.get('asc') or {})

    def latest_mission(self):
        root = os.path.join(self.rig.wd, 'missions')
        dirs = sorted((os.path.join(root, d) for d in os.listdir(root)), key=os.path.getmtime) if os.path.isdir(root) else []
        return dirs[-1] if dirs else None

    def keep_console_alive(self, seconds, every=1.0):
        end = time.time() + seconds
        while time.time() < end:
            self.c.ping()
            time.sleep(every)

    # ── scenarios ─────────────────────────────────────────────────────────
    def s_configure_like_zd650(self):
        """Make SITL a ZD650 (H-Flow, approved failsafes; 3S physics): write the approved (golden) parameter set through the console; golden check goes green."""
        # The ZD650's H-Flow: LiDAR + optical flow (SITL's simulated ones); takes effect after a power cycle
        for n, val in (('RNGFND1_TYPE', 100), ('RNGFND1_MAX', 10), ('RNGFND1_MAX_CM', 1000), ('RNGFND1_MIN', 0.2),
                       ('RNGFND1_MIN_CM', 5), ('RNGFND1_ORIENT', 25), ('FLOW_TYPE', 10), ('SIM_FLOW_ENABLE', 1),
                       ('SIM_SONAR_POS_Z', -0.12)):    # sensor 12 cm above the ground, like on the ZD650
            self.v.set_param(n, val, timeout=2)
        n0 = self.rig.server_log().count('Pixhawk connected')
        self.rig.power_cycle_sitl()
        end = time.time() + 60
        while time.time() < end and self.rig.server_log().count('Pixhawk connected') <= n0:
            time.sleep(0.5)
        time.sleep(3)
        self.v = Vehicle()
        self.wait_ready(120)
        golden = json.load(open(os.path.join(self.rig.wd, 'golden_sitl.json')))['params']
        want = {}
        for name, rule in golden.items():
            if 'value' in rule:
                want[name] = rule['value']
            elif 'in' in rule:
                want[name] = rule['in'][0]
            else:
                want[name] = rule['min'] if name != 'FENCE_ALT_MAX' else 60
        want.pop('BATT_VOLT_MULT', None)          # SITL's simulated sensor needs its own scaling
        want.pop('BATT_AMP_PERVLT', None)
        r = self.ok(self.c.send('param_write', params=want, timeout=60), 'write golden set')
        self.note(r['msg'])
        end = time.time() + 45
        g = {}
        while time.time() < end:
            m = self.c.mission_block.get('mission') or {}
            g = next((c for c in (m.get('checks') or []) if c.get('id') == 'golden_params'), {})
            if g.get('raw') == 'pass':
                break
            time.sleep(1)
        self.note(f"golden check: {g.get('value')} {g.get('note') or ''}")
        self.expect(g.get('raw') == 'pass', f"golden parameter check not green: {g.get('value')} {g.get('note')}")
        time.sleep(3)
        self.note(f"battery reads {self.c.telem.get('voltage')} V; LiDAR {self.c.telem.get('rangefinder')} m; flow quality {self.c.telem.get('of_quality')}")
        self.expect(11.5 < (self.c.telem.get('voltage') or 0) < 13, f"battery should read ~12.6 V, reads {self.c.telem.get('voltage')}")

    def s_basic_flight_and_recording(self):
        """GUIDED arm → takeoff 10 m → LAND. ASC session, charts, pixhawk.bin in the mission folder."""
        before = self.latest_mission()
        self.takeoff(10)
        a = self.asc()
        self.expect(a.get('state') in ('watching', 'acting'), f"ASC not watching in flight (state={a.get('state')})")
        self.expect((a.get('checks_per_s') or 0) > 200, f"ASC check rate low: {a.get('checks_per_s')}")
        self.note(f"ASC {a.get('checks_per_s')} checks/s, live rows {len(a.get('live') or [])}")
        self.ok(self.c.send('set_mode', mode='LAND'), 'LAND')
        self.expect(self.v.wait(lambda s: not s['armed'], 60), 'did not land and disarm')
        folder = None
        end = time.time() + 60
        while time.time() < end:
            folder = self.latest_mission()
            if folder and folder != before and os.path.exists(os.path.join(folder, 'pixhawk.bin')) \
                    and glob.glob(os.path.join(folder, 'asc', 'charts', '*.png')):
                break
            time.sleep(1)
        self.expect(folder and folder != before, 'no new mission folder')
        for f in ('asc/summary.json', 'asc/samples.csv', 'asc/mavlink.tlog', 'asc/events.jsonl', 'asc/params_start.json', 'pixhawk.bin'):
            self.expect(os.path.exists(os.path.join(folder, f)), f'missing {f}')
        sm = json.load(open(os.path.join(folder, 'asc', 'summary.json')))
        self.expect(sm['max_alt_m'] > 8, f"summary max_alt {sm['max_alt_m']}")
        self.expect(sm['flying_s'] > 5, f"summary flying_s {sm['flying_s']}")
        size = os.path.getsize(os.path.join(folder, 'pixhawk.bin'))
        from pymavlink import mavutil
        m = mavutil.mavlink_connection(os.path.join(folder, 'pixhawk.bin'))
        arm_seen = False
        while True:
            x = m.recv_match(type=['ARM', 'EV'])
            if x is None:
                break
            if x.get_type() == 'ARM':
                arm_seen = True
                break
        self.expect(arm_seen, 'pixhawk.bin does not contain the flight (no ARM record)')
        self.note(f"pixhawk.bin {size / 1e6:.1f} MB with the flight; charts {len(glob.glob(os.path.join(folder, 'asc', 'charts', '*.png')))}")
        end = time.time() + 8                    # the mission block arrives every 2 s on the ground
        while time.time() < end and (self.c.mission_block.get('bin_log') or {}).get('state') != 'done':
            time.sleep(0.5)
        bl = self.c.mission_block.get('bin_log') or {}
        self.expect(bl.get('state') == 'done', f"bin_log telemetry state {bl}")

    def s_mission_upload_auto(self):
        """Upload a 2-waypoint route through the server, verify download, fly it in AUTO, RTL home."""
        h = self.c.telem['home']
        a = offset(h['lat'], h['lon'], 40, 0)
        b = offset(h['lat'], h['lon'], 40, 40)
        plan = {'waypoints': [{'cmd': 'WAYPOINT', 'lat': a[0], 'lon': a[1], 'alt': 15, 'speed': 5},
                              {'cmd': 'WAYPOINT', 'lat': b[0], 'lon': b[1], 'alt': 15},
                              {'cmd': 'RTL', 'lat': h['lat'], 'lon': h['lon']}]}
        r = self.ok(self.c.send('wp_upload', plan=plan, timeout=30), 'wp_upload')
        self.note(r['msg'])
        d = self.ok(self.c.send('wp_download', timeout=30), 'wp_download')
        self.expect(len(d['result']['waypoints']) >= 3, f"downloaded {len(d['result']['waypoints'])} waypoints")
        vm = self.c.mission_block.get('vehicle_mission') or {}
        self.note(f"vehicle_mission state: {vm.get('state') or vm.get('status')}")
        self.ok(self.c.send('set_mode', mode='GUIDED'), 'GUIDED')
        self.ok(self.c.send('arm'), 'arm')
        self.ok(self.c.send('set_auto'), 'AUTO')
        self.expect(self.v.wait(lambda s: s['mode'] == 'AUTO', 20), f"not in AUTO (mode {self.v.get('mode')})")  # GUIDED takeoff first, AUTO from 2 m
        # keep the console alive so ASC does not hold on console loss
        end = time.time() + 120
        reached_b = False
        while time.time() < end:
            self.c.ping()
            lat, lon = self.v.get('lat'), self.v.get('lon')
            if lat and dist_m((lat, lon), b) < 6:
                reached_b = True
            if not self.v.get('armed') and reached_b:
                break
            time.sleep(1)
        self.expect(reached_b, 'never reached waypoint 2')
        self.expect(not self.v.get('armed'), 'mission did not end with RTL landing + disarm')
        self.expect(dist_m((self.v.get('lat'), self.v.get('lon')), (h['lat'], h['lon'])) < 5, 'did not land at home')
        self.note('flew both waypoints and RTL-landed at home')

    def s_estop_in_air(self):
        """EMERGENCY STOP at 10 m force-disarms the motors."""
        self.takeoff(10)
        t0 = time.time()
        r = self.c.send('emergency_stop')
        self.note(f"E-STOP reply: {r.get('msg')}")
        self.expect(self.v.wait(lambda s: not s['armed'], 3), 'motors still armed 3 s after E-STOP')
        self.note(f'disarmed {time.time() - t0:.2f} s after E-STOP')
        self.expect(r.get('ok'), f"E-STOP reply not ok: {r.get('msg')}")
        self.v.wait(lambda s: s['alt'] < 0.5, 20)

    def s_normal_disarm_refused_in_air(self):
        """A normal DISARM in the air is refused by ArduPilot and reported honestly."""
        self.takeoff(8)
        r = self.c.send('disarm')
        self.expect(not r.get('ok'), 'normal disarm in flight was accepted?!')
        self.expect(self.v.get('armed'), 'vehicle disarmed in the air from a normal disarm')
        self.note(f"reply: {r.get('msg')}")
        self.ensure_ground()

    def s_console_loss_hold_then_rtl(self):
        """Operator console goes silent in GUIDED: ASC → LOITER at ~10 s, RTL at ~30 s."""
        self.ok(self.c.send('asc_set', act_enabled=True), 'ASC action on')
        self.takeoff(10)
        self.ok(self.c.send('goto', **dict(zip(('lat', 'lon'), offset(self.c.telem['home']['lat'], self.c.telem['home']['lon'], 25, 0))), alt=10), 'goto')
        self.keep_console_alive(8)
        t0 = time.time()                       # console goes quiet now (telemetry still flows)
        self.expect(self.v.wait(lambda s: s['mode'] in ('LOITER', 'BRAKE'), 16), f"no hold after console loss (mode {self.v.get('mode')})")
        t_hold = time.time() - t0
        hold = self.v.get('mode')
        self.expect(self.v.wait(lambda s: s['mode'] == 'RTL', 30), f"no RTL after console loss (mode {self.v.get('mode')})")
        t_rtl = time.time() - t0
        self.note(f'{hold} hold after {t_hold:.1f} s, RTL after {t_rtl:.1f} s of silence')
        self.expect(8 <= t_hold <= 13, f'hold timing {t_hold:.1f} s')
        self.expect(26 <= t_rtl <= 34, f'RTL timing {t_rtl:.1f} s')
        a = self.asc()
        self.note(f"ASC state {a.get('state')}, action {a.get('action')}")
        self.expect(self.v.wait(lambda s: not s['armed'], 90), 'RTL did not land')

    def s_console_back_cancels(self):
        """Console comes back during the hold: ASC stops acting; operator keeps control."""
        self.takeoff(10)
        self.keep_console_alive(3)
        self.expect(self.v.wait(lambda s: s['mode'] in ('LOITER', 'BRAKE'), 16), 'no hold')
        self.keep_console_alive(4)
        a = self.asc()
        self.note(f"after console returns: ASC state {a.get('state')}, mode {self.v.get('mode')}")
        time.sleep(1)
        self.expect(self.v.get('mode') != 'RTL', 'ASC still went to RTL after the console came back')
        self.ok(self.c.send('set_mode', mode='GUIDED'), 'operator takes GUIDED back')
        self.keep_console_alive(25, 1)
        self.expect(self.v.get('mode') == 'GUIDED', f"operator lost control to ASC (mode {self.v.get('mode')})")
        self.ensure_ground()

    def s_jetson_heartbeat_failsafe(self):
        """Server frozen in flight → Pixhawk GCS failsafe (FS_GCS_ENABLE=1 RTL) fires on its own."""
        w = self.ok(self.c.send('param_write', params={'FS_GCS_ENABLE': 1, 'FS_GCS_TIMEOUT': 5}, timeout=20), 'param_write FS_GCS')
        self.note(w['msg'])
        self.takeoff(10)                       # GUIDED: the Jetson is flying the drone
        t0 = time.time()
        self.rig.pause_server()
        try:
            got = self.v.wait(lambda s: s['mode'] in ('RTL', 'LAND', 'SMART_RTL'), 15)
            texts = self.v.texts_since(t0)
        finally:
            self.rig.resume_server()
        self.expect(got, f"no GCS failsafe after the Jetson went silent (mode {self.v.get('mode')}, texts {texts[-5:]})")
        self.note(f"Pixhawk → {self.v.get('mode')} {time.time() - t0:.1f} s after the Jetson froze; {[x for x in texts if 'ailsafe' in x][:2]}")
        time.sleep(3)
        fs = (self.c.mission_block.get('failsafe_params') or {})
        self.note(f"server back; FS_GCS_ENABLE in telemetry: {(fs.get('FS_GCS_ENABLE') or fs.get('values', {}).get('FS_GCS_ENABLE'))}")
        self.expect(self.v.wait(lambda s: not s['armed'], 90), 'did not land after GCS failsafe')
        ev = [e for e in (self.asc().get('events') or [])]
        self.note(f"ASC events: {[e['msg'] for e in ev[:4]]}")

    def s_battery_failsafe(self):
        """Battery sags below BATT_LOW_VOLT → Pixhawk failsafe; server and ASC report it."""
        self.ok(self.c.send('param_write', params={'BATT_LOW_TIMER': 3}, timeout=20), 'battery timer')
        self.takeoff(10)
        self.ok(self.c.send('set_mode', mode='LOITER'), 'LOITER')
        t0 = time.time()
        self.v.set_param('SIM_BATT_VOLTAGE', 10.65)
        got = self.v.wait(lambda s: s['mode'] in ('RTL', 'LAND', 'SMART_RTL'), 25)
        texts = self.v.texts_since(t0)
        time.sleep(2)
        t = self.c.telem
        cell = [r for r in (self.asc().get('live') or []) if r[0] == 'cell_voltage']
        self.note(f"while low: server voltage {t.get('voltage')} V; ASC cell_voltage row {cell}")
        self.v.set_param('SIM_BATT_VOLTAGE', 12.6)
        self._battery_tripped = True
        self.expect(got, f"no battery failsafe (mode {self.v.get('mode')}, texts {texts[-4:]})")
        self.note(f"battery failsafe → {self.v.get('mode')}; Pixhawk said {[x for x in texts if 'att' in x.lower()][:2]}")
        faults = self.c.mission_block.get('faults') or {}
        self.note(f"faults block: {str(faults)[:200]}")
        self.expect(self.v.wait(lambda s: not s['armed'], 90), 'did not land after battery failsafe')


    def s_geofence(self):
        """Altitude fence 20 m: commanding a climb through it is stopped (avoidance) or ends in the fence RTL; ASC warns."""
        self.ok(self.c.send('param_write', params={'FENCE_ALT_MAX': 20}, timeout=20), 'fence ceiling 20 m')
        self.takeoff(10)
        t0 = time.time()
        warned = False
        top = 0.0
        while time.time() - t0 < 30:
            self.c.send('velocity', vz=-1.5)          # NED: negative = up
            top = max(top, self.v.get('alt'))
            if any(r[0] == 'fence_alt' and r[1] >= 2 for r in (self.asc().get('live') or [])):
                warned = True
            if self.v.get('mode') == 'RTL':
                break
            time.sleep(0.2)
        texts = self.v.texts_since(t0)
        self.expect(top <= 21.0, f"climbed through the 20 m fence to {top:.1f} m")
        how = 'fence RTL' if self.v.get('mode') == 'RTL' else f'held at {top:.1f} m by fence avoidance'
        self.note(f"commanded climb for 30 s: {how}; ASC warned near the ceiling: {warned}")
        self.expect(warned, 'ASC never warned about the fence ceiling')
        self.ensure_ground()
        time.sleep(3)
        self.ok(self.c.send('param_write', params={'FENCE_ALT_MAX': 60}, timeout=20), 'fence back to 60 m')

    def s_joystick_and_stale_stop(self):
        """Velocity commands move the drone; when they stop the drone stops (stale protection)."""
        self.takeoff(10)
        p0 = (self.v.get('lat'), self.v.get('lon'))
        for _ in range(15):                      # 3 s at 5 Hz, 3 m/s north
            self.c.send('velocity', vx=3.0)
            time.sleep(0.2)
        moved = dist_m(p0, (self.v.get('lat'), self.v.get('lon')))
        self.expect(moved > 4, f'moved only {moved:.1f} m with joystick')
        time.sleep(4)
        p1 = (self.v.get('lat'), self.v.get('lon'))
        time.sleep(3)
        drift = dist_m(p1, (self.v.get('lat'), self.v.get('lon')))
        self.note(f'joystick moved {moved:.1f} m; drift after release {drift:.2f} m in 3 s')
        self.expect(drift < 2.0, f'kept moving after the joystick stopped ({drift:.1f} m)')
        self.ensure_ground()

    def s_goto_and_rtl(self):
        """Tap-to-go 60 m away, then RTL brings it home."""
        start = (self.v.get('lat'), self.v.get('lon'))      # ArduPilot sets home where it arms
        h = {'lat': start[0], 'lon': start[1]}
        self.takeoff(12)
        tgt = offset(h['lat'], h['lon'], -30, 50)
        r = self.ok(self.c.send('goto', lat=tgt[0], lon=tgt[1], alt=12), 'goto')
        self.note(r['msg'])
        end = time.time() + 60
        while time.time() < end and dist_m((self.v.get('lat'), self.v.get('lon')), tgt) > 4:
            self.c.ping()
            time.sleep(1)
        d = dist_m((self.v.get('lat'), self.v.get('lon')), tgt)
        self.expect(d <= 4, f'goto ended {d:.1f} m from the target')
        bad = self.c.send('goto', lat=tgt[0] + 0.05, lon=tgt[1], alt=12)
        self.expect(not bad.get('ok'), 'goto 5 km away accepted')
        self.note(f"far goto refused in the air: {bad.get('msg')}")
        self.ok(self.c.send('set_mode', mode='RTL'), 'RTL')
        end = time.time() + 90
        while time.time() < end and self.v.get('armed'):
            self.c.ping()
            time.sleep(1)
        self.expect(not self.v.get('armed'), 'RTL did not land')
        dh = dist_m((self.v.get('lat'), self.v.get('lon')), (h['lat'], h['lon']))
        self.expect(dh < 5, f'RTL landed {dh:.1f} m from home')

    def s_params_roundtrip(self):
        """param_read / param_write from the console stick on the vehicle (checked on the other port)."""
        names = ['FS_GCS_ENABLE', 'FS_THR_ENABLE', 'RTL_ALT', 'RTL_ALT_M', 'BATT_LOW_VOLT', 'WPNAV_SPEED', 'WP_SPD', 'ARMING_CHECK']
        r = self.ok(self.c.send('param_read', names=names, timeout=30), 'param_read')
        self.note(f"read {sorted(r['values'])}; not on this firmware: {r.get('missing')}")
        target = {'RTL_ALT': 3000, 'FS_THR_ENABLE': 1, 'WPNAV_SPEED': 600}
        w = self.ok(self.c.send('param_write', params=target, timeout=30), 'param_write')
        for n, val in target.items():
            got = self.v.get_param(n)
            self.expect(got is not None and abs(got - val) < 0.01, f'{n} on vehicle = {got}, wanted {val}')
        self.note(w['msg'])
        bad = self.c.send('param_write', params={'NOT_A_PARAM_X': 1}, timeout=20)
        self.expect(not bad.get('ok'), 'unknown parameter write reported ok')
        self.note(f"unknown parameter: {bad.get('msg')}")
        rev = self.c.mission_block.get('param_rev') or {}
        self.note(f"param_rev broadcast: rev {rev.get('rev')} by {rev.get('by')}")

    def s_writes_blocked_when_armed(self):
        """Parameter writes and mission uploads are refused while armed."""
        self.takeoff(6)
        r1 = self.c.send('param_write', params={'RTL_ALT': 2500})
        r2 = self.c.send('wp_upload', plan={'waypoints': [{'lat': 33.4214, 'lon': -111.9327, 'alt': 10}]})
        self.expect(not r1.get('ok') and not r2.get('ok'), 'writes accepted in flight')
        self.note(f"{r1.get('msg')} | {r2.get('msg')}")
        self.ensure_ground()

    def s_launch_sequence(self):
        """Countdown mission: create → GO → arms at T-10 → takes off at T-0 → hover → abort RTL → safed → close."""
        r = self.c.send('mission_create', name='sitl run', total=60, confirm='flight', timeout=10)
        self.ok(r, 'mission_create')
        self.note(r['msg'])
        for k in ('manual_observers',):
            self.c.send('mission_manual', key=k, value=True)
        t0 = time.time()
        stage = None
        holds = []
        went = False
        while time.time() - t0 < 110:
            self.c.ping()
            m = (self.c.mission_block.get('mission') or {})
            stage = m.get('stage')
            if stage in ('gonogo', 'prearm') and not went and not m.get('held'):
                g = self.c.send('mission_go')
                went = g.get('ok')
                self.note(f"GO at stage {stage}: {g.get('msg')}")
            if stage == 'flight' or (m.get('held') and stage not in (None, 'countdown')):
                break
            time.sleep(1)
        m = self.c.mission_block.get('mission') or {}
        if m.get('held'):
            raise Failed(f"countdown held: {m.get('hold_reason')} (stage {stage})")
        self.expect(stage == 'flight', f"launch ended in stage {stage}")
        self.expect(self.v.get('armed') and self.v.get('alt') > 3, f"not flying after T-0 (alt {self.v.get('alt'):.1f})")
        self.note(f"T-0 takeoff done, hovering at {self.v.get('alt'):.1f} m in {self.v.get('mode')}")
        a = self.c.send('mission_abort', mode='rtl', reason='SITL test')
        self.ok(a, 'abort rtl')
        end = time.time() + 90
        while time.time() < end:
            self.c.ping()
            if (self.c.mission_block.get('mission') or {}).get('stage') == 'safed':
                break
            time.sleep(1)
        st = (self.c.mission_block.get('mission') or {}).get('stage')
        self.expect(st == 'safed', f'not safed after landing (stage {st})')
        cl = self.c.send('mission_close')
        self.ok(cl, 'mission_close')
        folder = self.latest_mission()
        end = time.time() + 40
        while time.time() < end and not os.path.exists(os.path.join(folder, 'pixhawk.bin')):
            time.sleep(1)
        self.note(f"mission folder {os.path.basename(folder)}: {sorted(os.listdir(folder))}")
        self.expect(os.path.exists(os.path.join(folder, 'pixhawk.bin')), 'no pixhawk.bin in the launch mission folder')
        self.expect(os.path.exists(os.path.join(folder, 'asc', 'summary.json')), 'no ASC summary in the launch mission folder')

    def s_flight1_profile(self):
        """FLIGHT 1 countdown: 2 m → automatic check → climb to 5 m → 30 s hover → land → safed."""
        r = self.c.send('mission_create', name='sitl flight 1', total=60, confirm='flight', profile='flight1', timeout=10)
        self.ok(r, 'mission_create flight1')
        self.c.send('mission_manual', key='manual_observers', value=True)
        t0, went, trace, stage = time.time(), False, [], None
        while time.time() - t0 < 260:
            self.c.ping()
            m = self.c.mission_block.get('mission') or {}
            stage = m.get('stage')
            if stage in ('gonogo', 'prearm') and not went and not m.get('held'):
                went = self.c.send('mission_go').get('ok')
            if m.get('held') and stage not in (None, 'countdown'):
                raise Failed(f"countdown held: {m.get('hold_reason')} (stage {stage})")
            trace.append((time.time() - t0, self.v.get('alt') or 0, (m.get('step') or {}).get('name')))
            if stage in ('landed', 'safed') or (stage == 'standby' and trace and max(a for _, a, _ in trace) > 1):
                break
            time.sleep(0.5)
        steps = [n for _, _, n in trace if n]
        seen = list(dict.fromkeys(steps))
        low = [a for _, a, n in trace if n == 'hold_low']
        high = [a for _, a, n in trace if n == 'hold_high']
        self.note(f"profile steps seen: {seen}; low hover {min(low or [0]):.1f}–{max(low or [0]):.1f} m, "
                  f"high hover {min(high or [0]):.1f}–{max(high or [0]):.1f} m, max {max(a for _, a, _ in trace):.1f} m")
        self.expect(seen[:4] == ['hold_low', 'climb', 'hold_high', 'landing'], f'unexpected step order {seen}')
        self.expect(low and 1.5 < sum(low) / len(low) < 2.5, 'did not hover at ~2 m')
        self.expect(high and max(high) > 4.5, 'did not reach 5 m')
        end = time.time() + 60
        while time.time() < end and (self.c.mission_block.get('mission') or {}).get('stage') not in ('landed', 'safed'):
            self.c.ping(); time.sleep(1)
        st = (self.c.mission_block.get('mission') or {}).get('stage')
        self.expect(st in ('landed', 'safed'), f'did not land (stage {st})')
        ev = ' | '.join(e.get('msg', '') for e in (self.c.mission_block.get('mission') or {}).get('events', []))
        self.expect('CHECK PASSED' in ev, 'no step-check event')
        folder = self.latest_mission()
        self.expect(os.path.exists(os.path.join(folder, 'flight1_step_check.json')), 'no flight1_step_check.json')
        self.c.send('mission_close')

    def s_pixhawk_reboot_reconnect(self):
        """Pixhawk power-cycles on the ground: the server notices, reconnects and streams again."""
        n0 = self.rig.server_log().count('Pixhawk connected')
        t0 = time.time()
        self.rig.power_cycle_sitl()
        end = time.time() + 60
        while time.time() < end and self.rig.server_log().count('Pixhawk connected') <= n0:
            time.sleep(0.5)
        self.expect(self.rig.server_log().count('Pixhawk connected') > n0, 'server did not reconnect after the power cycle')
        self.note(f"reconnected {time.time() - t0:.0f} s after the power cut")
        self.v = Vehicle()
        self.wait_ready(120)
        self.expect(self.c.send('param_read', names=['FS_GCS_ENABLE']).get('ok'), 'commands do not work after the reconnect')

    def s_estop_while_uploading(self):
        """E-STOP is not queued behind a slow command."""
        self.takeoff(8)
        import threading
        threading.Thread(target=lambda: self.c.send('param_read', names=['RTL_ALT'] * 1 + ['SERIAL%d_BAUD' % i for i in range(8)], timeout=30), daemon=True).start()
        time.sleep(0.05)
        t0 = time.time()
        r = self.c.send('emergency_stop')
        self.expect(self.v.wait(lambda s: not s['armed'], 3), 'E-STOP did not disarm while a slow command ran')
        self.note(f"E-STOP answered in {time.time() - t0:.2f} s during a param read: {r.get('msg')}")
        self.v.wait(lambda s: s['alt'] < 0.5, 20)

    SCENARIOS = ['s_configure_like_zd650', 's_launch_sequence', 's_flight1_profile', 's_params_roundtrip', 's_basic_flight_and_recording', 's_mission_upload_auto', 's_estop_in_air',
                 's_normal_disarm_refused_in_air', 's_writes_blocked_when_armed', 's_joystick_and_stale_stop', 's_goto_and_rtl',
                 's_console_loss_hold_then_rtl', 's_console_back_cancels', 's_jetson_heartbeat_failsafe',
                 's_geofence', 's_estop_while_uploading', 's_battery_failsafe', 's_pixhawk_reboot_reconnect']


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ardupilot', default=os.path.expanduser('~/ardupilot'))
    ap.add_argument('--work', default=os.path.join(tempfile.gettempdir(), 'airsent_sitl'))
    ap.add_argument('--only', default='')
    ap.add_argument('--keep', action='store_true', help='leave SITL + server running afterwards')
    args = ap.parse_args()
    rig = Rig(args.ardupilot, args.work)
    rig.start()
    run = Run(rig)
    results = []
    try:
        run.wait_ready()
        names = [n if n.startswith('s_') else 's_' + n for n in args.only.split(',') if n] or Run.SCENARIOS
        for name in names:
            fn = getattr(run, name)
            print(f'\n▶ {name}: {fn.__doc__.strip().splitlines()[0]}', flush=True)
            run.notes = []
            t0 = time.time()
            try:
                run.wait_ready()
                fn()
                res = 'PASS'
                err = ''
            except Failed as e:
                res, err = 'FAIL', str(e)
            except Exception as e:
                res, err = 'ERROR', f'{type(e).__name__}: {e}\n{traceback.format_exc()}'
            print(f"  {res} ({time.time() - t0:.0f} s) {err}", flush=True)
            results.append({'scenario': name, 'result': res, 'error': err, 'notes': list(run.notes), 'seconds': round(time.time() - t0)})
            try:
                run.ensure_ground()
            except Exception:
                pass
    finally:
        tb = [l for l in rig.server_log().splitlines() if 'Traceback' in l or 'Error' in l]
        json.dump({'results': results, 'server_errors': tb[-50:]}, open(os.path.join(HERE, 'last_report.json'), 'w'), indent=1)
        if not args.keep:
            rig.stop()
    passed = sum(r['result'] == 'PASS' for r in results)
    print(f'\n{passed}/{len(results)} scenarios passed')
    sys.exit(0 if passed == len(results) else 1)


if __name__ == '__main__':
    main()
