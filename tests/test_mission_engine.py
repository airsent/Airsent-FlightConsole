import json
import tempfile
import unittest
from pathlib import Path

from mission_engine import MissionEngine, default_config

PIC, OTHER = 'console-pic', 'console-other'


class FakeVehicle:
    def __init__(self):
        self.armed = False
        self.calls = []
        self.goto = []              # (alt, lat, lon) of every goto_alt
        self.yaws = []
        self.alt = 0.0

    def set_mode(self, name): self.calls.append(('mode', name))
    def arm(self, force=False): self.calls.append(('arm', force) if force else ('arm',)); self.armed = True
    def disarm(self, force=False): self.calls.append(('disarm', force)); self.armed = False
    def takeoff(self, alt): self.calls.append(('takeoff', alt))
    def land(self): self.calls.append(('land',))
    def rtl(self): self.calls.append(('rtl',))
    def goto_alt(self, alt, lat=None, lon=None): self.calls.append(('goto_alt', alt)); self.goto.append((alt, lat, lon))
    def yaw(self, degrees, rate=30.0): self.calls.append(('yaw', degrees)); self.yaws.append((degrees, rate))


class MissionEngineBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = 1_000_000.0
        self.vehicle = FakeVehicle()
        self.gate_pwm = 1900          # SAFE (Motor Emergency Stop active)
        self.overrides = {}
        self.config = default_config()
        self.config.update(rc_gate_channel=7, require_rtk=True, require_esc_telemetry=True, sim=False)
        self.engine = self.make_engine()

    def make_engine(self):
        root = Path(self.tmp.name)
        return MissionEngine(root / 'state.json', root / 'missions', self.vehicle, lambda: self.now, dict(self.config))

    def snapshot(self):
        channels = [1500] * 16
        channels[6] = self.gate_pwm
        s = {
            'pixhawk_connected': True, 'armed': self.vehicle.armed, 'fix_type': 'RTK FIX', 'sats': 18, 'hdop': 0.6,
            'home': {'set': True}, 'ekf_healthy': True, 'voltage': 25.0, 'battery': 96,
            'sensor_health': {'imu': 'ok', 'baro': 'ok', 'mag': 'ok', 'prearm': 'ok'},
            'sensor_ages': {'rangefinder': 0.1, 'of': 0.1}, 'rangefinder': 0.3, 'of_quality': 200,
            'vib_x': 4, 'vib_y': 4, 'vib_z': 6, 'pilot_settings': {'fence': {'FENCE_ENABLE': 1}},
            'front_cam_active': True, 'down_cam_active': True, 'jetson_temp': 50,
            'rc_signal': {'input_present': True}, 'rc_channels': channels,
            'network_link': {'connected': True, 'type': 'wifi', 'percent': 80},
            'proximity': {'obstacle': False, 'dist': -1},
            'esc_telemetry': {'connected': True, 'channels': [
                {'channel': i, 'connected': True, 'rpm': 1500 if self.vehicle.armed else 0, 'status': 0}
                for i in range(1, 9)]},
            '_golden': {'state': 'ok', 'count': 20, 'diffs': []}, '_disk_free_gb': 120.0,
            '_vps_relay_connected': True, 'alt': self.vehicle.alt, 'roll': 0, 'pitch': 0,
        }
        s.update(self.overrides)
        return s

    def tick(self, seconds=0.0, step=0.2):
        end = self.now + seconds
        while True:
            self.engine.tick(self.snapshot())
            if self.now >= end:
                return
            self.now = min(end, self.now + step)

    def send(self, action, console=PIC, **extra):
        return self.engine.handle(dict(action=action, console_id=console, operator='Aarav', **extra))

    def create(self, total=180):
        self.send('mission_ping')
        self.tick()
        reply = self.send('mission_create', name='Mission 1', confirm='flight', total=total)
        self.assertTrue(reply['ok'], reply)
        for key in ('manual_observers',):
            self.assertTrue(self.send('mission_manual', key=key, value=True)['ok'])

    def run_to(self, remaining):
        """Advance the countdown to `remaining` seconds, keeping the PIC console alive."""
        for _ in range(2000):
            if self.engine.countdown_remaining() <= remaining or self.engine.held:
                return
            self.send('mission_ping')
            self.tick(min(1.0, self.engine.countdown_remaining() - remaining))



class MissionEngineTests(MissionEngineBase):
    # ── bench test (props off) ───────────────────────────────────────────────
    def test_test_mode_bypasses_checks_force_arms_and_takes_off(self):
        # outdoors-but-not-ready: no RTK, weak GPS, EKF degraded, low battery, parameters not approved, no ESC telemetry
        self.overrides = {'fix_type': '3D FIX', 'sats': 7, 'hdop': 2.6, 'ekf_healthy': False, 'battery': 43,
                          '_golden': {'state': 'diff', 'count': 20, 'diffs': ['DISARM_DELAY=10']},
                          'esc_telemetry': {'connected': False, 'channels': []}}
        self.send('mission_ping')
        self.tick()
        r = self.send('mission_create', name='test', confirm='flight', total=180, bench=True)
        self.assertFalse(r['ok'])
        self.assertIn("'test'", r['msg'])                         # own confirm word
        r = self.send('mission_create', name='test', confirm='test', total=180, bench=True)
        self.assertTrue(r['ok'], r)
        # no manual confirmations, no GO press: nothing holds in test mode
        self.run_to(59.5)
        self.assertFalse(self.engine.held, self.engine.hold_reason)
        gps = next(c for c in self.engine.checks if c['id'] == 'gps_fix')
        self.assertEqual(gps['raw'], 'warn')
        self.assertIn('TEST MODE', gps['note'])
        self.gate_pwm = 1000
        self.run_to(9.5)
        self.assertEqual(self.engine.stage, 'armed')
        self.assertIn(('arm', True), self.vehicle.calls)          # force-arm past ArduPilot pre-arm
        self.run_to(0)
        self.tick(0.4)
        self.assertEqual(self.engine.stage, 'takeoff', self.engine.hold_reason)
        self.assertIn(('takeoff', 5.0), self.vehicle.calls)       # motors spin up and it flies

    def test_no_wind_weather_item(self):
        self.assertFalse(any('wind' in k for k in __import__('mission_engine').MANUAL_CHECKS))
        self.assertFalse(self.send('mission_manual', key='manual_wind', value=True)['ok'])

    def test_test_mode_still_needs_the_kill_switch_released_to_arm(self):
        self.send('mission_ping')
        self.tick()
        self.assertTrue(self.send('mission_create', name='t', confirm='test', total=180, bench=True)['ok'])
        self.run_to(9.5)                                           # switch still in SAFE (motor stop active)
        self.assertTrue(self.engine.held)
        self.assertIn('RC arm switch', self.engine.hold_reason)
        self.assertNotIn(('arm', True), self.vehicle.calls)

    def test_real_flight_is_not_waived(self):
        self.overrides = {'fix_type': '3D FIX'}
        self.create()
        self.run_to(170)
        self.assertTrue(self.engine.held)
        self.assertIn('GPS', self.engine.hold_reason)

    def test_countdown_list_trimmed(self):
        import mission_engine as me
        self.assertEqual(list(me.MANUAL_CHECKS), ['manual_observers'])        # no wind, airspace, payload
        self.assertFalse(me.default_config()['require_rtk'])                  # 3D fix is enough to lift off
        self.tick()
        ids = {c['id'] for c in self.engine.checks}
        self.assertNotIn('obstacles', ids)

    def test_soft_checks_warn_instead_of_holding(self):
        # LiDAR inside its min range on the ground (reads 0), battery % estimate low, front camera
        # offline, no ESC telemetry: all warnings now — none of them holds a real countdown.
        self.config.update(require_rtk=False, require_esc_telemetry=False)
        self.engine = self.make_engine()
        self.overrides = {'rangefinder': 0.0, 'battery': 35, 'front_cam_active': False,
                          'esc_telemetry': {'connected': False, 'channels': []}}
        self.create()
        self.run_to(61)
        self.assertFalse(self.engine.held, self.engine.hold_reason)
        by = {c['id']: c for c in self.engine.checks}
        self.assertEqual(by['lidar']['raw'], 'pass')
        self.assertEqual(by['lidar']['value'], 'LIVE (below min range)')
        self.assertEqual(by['battery_pct']['raw'], 'warn')
        self.assertEqual(by['front_cam']['raw'], 'warn')
        self.assertEqual(by['motor_health']['raw'], 'warn')

    def test_hard_checks_still_hold(self):
        self.config.update(require_rtk=False, require_esc_telemetry=False)
        self.engine = self.make_engine()
        self.overrides = {'sensor_ages': {'rangefinder': 10, 'of': 0.1}}       # LiDAR stopped reporting
        self.create()
        self.run_to(170)
        self.assertTrue(self.engine.held)
        self.assertIn('LiDAR', self.engine.hold_reason)

    def test_battery_voltage_still_holds(self):
        self.overrides = {'voltage': 21.0}                                     # voltage is the hard battery check
        self.create()
        self.run_to(170)
        self.assertTrue(self.engine.held)
        self.assertIn('voltage', self.engine.hold_reason.lower())

    def test_esc_reporting_a_stopped_motor_still_holds(self):
        self.config.update(require_esc_telemetry=False)
        self.engine = self.make_engine()
        self.create()
        self.run_to(59.5)
        self.assertTrue(self.send('mission_go')['ok'])
        self.gate_pwm = 1000
        self.run_to(9.5)
        self.overrides = {'esc_telemetry': {'connected': True, 'channels': [
            {'channel': i, 'connected': True, 'rpm': 0 if i == 3 else 1500, 'status': 0} for i in range(1, 9)]}}
        self.run_to(0)
        self.tick(0.4)
        self.assertTrue(self.engine.held)
        self.assertIn('Motor health', self.engine.hold_reason)

    def test_3d_fix_is_enough_without_rtk(self):
        self.config.update(require_rtk=False)
        self.engine = self.make_engine()
        self.overrides = {'fix_type': '3D FIX'}
        self.create()
        self.run_to(61)
        self.assertFalse(self.engine.held, self.engine.hold_reason)
        gps = next(c for c in self.engine.checks if c['id'] == 'gps_fix')
        self.assertEqual(gps['raw'], 'pass')

    # ── happy path ───────────────────────────────────────────────────────────
    def test_full_mission_from_creation_to_safed(self):
        self.create()
        self.tick()
        self.assertEqual(self.engine.stage, 'prearm')          # 180 s countdown starts at the handover
        self.run_to(60.5)
        self.assertFalse(self.engine.held, self.engine.hold_reason)
        self.run_to(59.5)
        self.assertEqual(self.engine.stage, 'gonogo')
        self.assertTrue(self.send('mission_go')['ok'])
        self.gate_pwm = 1000                                   # RC pilot flips to ARM
        self.run_to(9.5)
        self.assertEqual(self.engine.stage, 'armed')
        self.assertIn(('mode', 'GUIDED'), self.vehicle.calls)
        self.assertIn(('arm',), self.vehicle.calls)
        self.run_to(0)
        self.tick(0.2)
        self.assertEqual(self.engine.stage, 'takeoff', self.engine.hold_reason)
        self.assertIn(('takeoff', 5.0), self.vehicle.calls)
        self.vehicle.alt = 4.8
        self.tick(1)
        self.assertEqual(self.engine.stage, 'flight')
        motor = next(c for c in self.engine.checks if c['id'] == 'motor_health')
        self.assertEqual(motor['raw'], 'pass', 'motor health measured at idle must stay latched in flight')
        self.vehicle.alt = 0
        self.vehicle.armed = False
        self.tick(1)
        self.assertEqual(self.engine.stage, 'landed')
        self.gate_pwm = 1900
        self.tick(1)
        self.assertEqual(self.engine.stage, 'safed')
        folder = Path(self.engine.mission['folder'])
        self.assertTrue(self.send('mission_close')['ok'])
        self.assertEqual(self.engine.stage, 'standby')
        for name in ('mission.json', 'events.jsonl', 'telemetry.jsonl', 'checks_handover.json',
                     'params_at_arm.json', 'checks_t0.json', 'summary.json'):
            self.assertTrue((folder / name).exists(), name)
        summary = json.loads((folder / 'summary.json').read_text())
        self.assertEqual(summary['result'], 'COMPLETED')
        self.assertTrue(folder.name.endswith('_Mission-1'))

    # ── holds ────────────────────────────────────────────────────────────────
    def test_rtk_float_holds_and_resume_requires_the_fix(self):
        self.create(300)
        self.assertEqual(self.engine.stage, 'countdown')
        self.overrides['fix_type'] = 'RTK FLOAT'
        self.run_to(170)
        self.assertTrue(self.engine.held)
        self.assertIn('RTK', self.engine.hold_reason)
        self.assertIn('check:gps_fix', self.engine.faults.active)
        self.assertFalse(self.send('mission_resume')['ok'])
        self.overrides.pop('fix_type')
        self.tick()
        reply = self.send('mission_resume')
        self.assertTrue(reply['ok'], reply)
        self.assertFalse(self.engine.held)

    def test_missing_go_or_rc_gate_holds_before_arming(self):
        self.create()
        self.run_to(9.5)                       # no GO, switch still SAFE
        self.assertTrue(self.engine.held)
        self.assertNotIn(('arm',), self.vehicle.calls)
        self.assertEqual(self.engine.stage, 'armed')

    def test_hold_while_armed_disarms_and_resume_restarts_go_nogo(self):
        self.create()
        self.run_to(59.5)
        self.send('mission_go')
        self.gate_pwm = 1000
        self.run_to(5)
        self.assertTrue(self.vehicle.armed)
        self.assertTrue(self.send('mission_hold')['ok'])
        self.assertFalse(self.vehicle.armed)
        self.tick()
        reply = self.send('mission_resume')
        self.assertTrue(reply['ok'], reply)
        self.assertEqual(self.engine.stage, 'gonogo')
        self.assertIsNone(self.engine.go)
        self.assertAlmostEqual(self.engine.countdown_remaining(), 60, delta=0.5)

    def test_long_hold_recycles_to_vehicle_handover(self):
        self.create()
        self.run_to(90)
        self.send('mission_hold')
        self.now += 150
        self.send('mission_ping')
        self.tick()
        self.assertTrue(self.send('mission_resume')['ok'])
        self.assertAlmostEqual(self.engine.countdown_remaining(), 180, delta=0.5)

    def test_rc_switch_to_safe_after_arming_holds_and_disarms(self):
        self.create()
        self.run_to(59.5)
        self.send('mission_go')
        self.gate_pwm = 1000
        self.run_to(6)
        self.gate_pwm = 1900
        self.tick(0.4)
        self.assertTrue(self.engine.held)
        self.assertFalse(self.vehicle.armed)

    def test_weak_motor_blocks_takeoff(self):
        self.create()
        self.run_to(59.5)
        self.send('mission_go')
        self.gate_pwm = 1000
        self.run_to(1)
        esc = self.snapshot()['esc_telemetry']
        esc['channels'][2]['rpm'] = 600
        self.overrides['esc_telemetry'] = esc
        self.run_to(0)
        self.tick(0.2)
        self.assertTrue(self.engine.held)
        self.assertIn('Motor health', self.engine.hold_reason)
        self.assertNotIn(('takeoff', 5.0), self.vehicle.calls)
        self.assertFalse(self.vehicle.armed)

    def test_manual_checks_are_due_at_go_nogo_not_at_handover(self):
        self.send('mission_ping'); self.tick()
        self.assertTrue(self.send('mission_create', name='M', confirm='flight', total=180)['ok'])
        self.run_to(61)
        self.assertFalse(self.engine.held, self.engine.hold_reason)
        self.run_to(59.5)
        self.assertTrue(self.engine.held)
        self.assertIn('observers', self.engine.hold_reason)

    # ── restart, abort, roles ────────────────────────────────────────────────
    def test_jetson_restart_comes_back_held(self):
        self.create()
        self.run_to(120)
        self.now += 5
        restarted = self.make_engine()
        self.assertTrue(restarted.held)
        self.assertIn('restarted', restarted.hold_reason)
        self.assertAlmostEqual(restarted.countdown_remaining(), 115, delta=1)

    def test_abort_before_takeoff_disarms_any_operator(self):
        self.create()
        self.run_to(59.5)
        self.send('mission_go')
        self.gate_pwm = 1000
        self.run_to(5)
        reply = self.send('mission_abort', console=OTHER)
        self.assertTrue(reply['ok'], reply)
        self.assertEqual(self.engine.stage, 'aborted')
        self.assertFalse(self.vehicle.armed)
        self.assertTrue(self.send('mission_close')['ok'])
        self.assertEqual(self.engine.stage, 'standby')

    def test_abort_in_flight_lands_or_returns(self):
        self.engine.stage = 'flight'
        self.engine.takeoff_at = self.now
        self.assertTrue(self.send('mission_abort', mode='rtl')['ok'])
        self.assertIn(('rtl',), self.vehicle.calls)
        self.assertTrue(self.send('mission_abort')['ok'])
        self.assertIn(('land',), self.vehicle.calls)

    def test_only_pic_can_resume_or_go_and_pic_cannot_be_stolen(self):
        self.create()
        self.send('mission_hold')
        self.assertFalse(self.send('mission_resume', console=OTHER)['ok'])
        self.assertFalse(self.send('mission_claim_pic', console=OTHER)['ok'])
        self.now += 20                         # PIC goes silent
        self.assertTrue(self.send('mission_claim_pic', console=OTHER)['ok'])
        self.assertEqual(self.engine.pic_console, OTHER)

    def test_create_requires_confirmation_safe_switch_and_disarmed(self):
        self.send('mission_ping'); self.tick()
        self.assertFalse(self.send('mission_create', name='M', confirm='nope')['ok'])
        self.gate_pwm = 1000
        self.tick()
        reply = self.send('mission_create', name='M', confirm='flight')
        self.assertFalse(reply['ok'])
        self.assertIn('RC arm switch', reply['msg'])
        self.gate_pwm = 1900
        self.vehicle.armed = True
        self.tick()
        self.assertFalse(self.send('mission_create', name='M', confirm='flight')['ok'])

    def test_takeoff_watchdog_aborts_when_it_does_not_climb(self):
        self.engine.stage = 'takeoff'
        self.engine.takeoff_at = self.now
        self.vehicle.armed = True
        self.gate_pwm = 1000
        self.tick(7)
        self.assertEqual(self.engine.stage, 'landed')
        self.assertIn(('disarm', False), self.vehicle.calls)
        self.assertIn('No climb', self.engine.abort['reason'])

    def test_takeoff_watchdog_disarms_on_tip_over(self):
        self.engine.stage = 'takeoff'
        self.engine.takeoff_at = self.now
        self.vehicle.armed = True
        self.overrides['roll'] = 40
        self.tick(0.4)
        self.assertIn(('disarm', True), self.vehicle.calls)

    # ── faults ───────────────────────────────────────────────────────────────
    def test_acknowledged_fault_stays_active_until_cleared(self):
        self.engine.faults.update({'x': ('warning', 'Test', 'something')})
        fault_id = self.engine.faults.active['x']['id']
        self.send('fault_ack', fault_id=fault_id)
        self.assertEqual(self.engine.faults.active['x']['acked_by'], 'Aarav')
        self.engine.faults.update({})
        self.assertIn('x', self.engine.faults.active)       # debounce
        self.now += 3
        self.engine.faults.update({})
        self.assertNotIn('x', self.engine.faults.active)
        self.assertEqual(self.engine.faults.snapshot()['recent'][0]['key'], 'x')

    def test_snapshot_marks_future_gates_pending(self):
        self.overrides['fix_type'] = '3D FIX'
        self.tick()
        snap = self.engine.snapshot()
        gps = next(c for c in snap['checks'] if c['id'] == 'gps_fix')
        self.assertEqual(gps['status'], 'pending')
        self.assertEqual(gps['raw'], 'fail')
        json.dumps(snap)


if __name__ == '__main__':
    unittest.main()


class FaultAckTests(unittest.TestCase):
    def setUp(self):
        from mission_engine import FaultBook
        self.t = [1000.0]
        self.book = FaultBook(clock=lambda: self.t[0])

    def tick(self, conds, dt=0.5):
        self.t[0] += dt
        self.book.update(conds)

    def active(self):
        return self.book.snapshot()['active']

    def test_ack_keeps_fault_listed_but_marked(self):
        self.tick({'esc': ('warning', 'ESC', 'not connected')})
        fid = self.active()[0]['id']
        self.book.ack(fid, 'Aarav')
        self.tick({'esc': ('warning', 'ESC', 'not connected')})
        self.assertEqual(len(self.active()), 1)
        self.assertEqual(self.active()[0]['acked_by'], 'Aarav')

    def test_ack_all(self):
        self.tick({'a': ('warning', 'X', 'a'), 'b': ('critical', 'Y', 'b')})
        self.book.ack('all', 'Aarav')
        self.assertTrue(all(f['acked_by'] == 'Aarav' for f in self.active()))

    def test_flickering_fault_keeps_its_ack(self):
        c = {'link': ('warning', 'Network', 'cellular weak')}
        self.tick(c); self.book.ack(self.active()[0]['id'], 'Aarav')
        self.tick({}, dt=3)            # clears
        self.assertEqual(self.active(), [])
        self.tick(c, dt=5)             # back 5 s later
        self.assertEqual(self.active()[0]['acked_by'], 'Aarav')

    def test_ack_forgotten_after_a_while(self):
        c = {'link': ('warning', 'Network', 'cellular weak')}
        self.tick(c); self.book.ack(self.active()[0]['id'], 'Aarav')
        self.tick({}, dt=3)
        self.tick(c, dt=120)
        self.assertIsNone(self.active()[0]['acked_by'])

    def test_escalation_needs_a_new_ack(self):
        self.tick({'batt': ('warning', 'Battery', 'low')})
        self.book.ack(self.active()[0]['id'], 'Aarav')
        self.tick({'batt': ('critical', 'Battery', 'critical')})
        self.assertIsNone(self.active()[0]['acked_by'])
        self.book.ack(self.active()[0]['id'], 'Aarav')
        self.tick({}, dt=3)
        self.tick({'batt': ('critical', 'Battery', 'critical')}, dt=1)
        self.assertEqual(self.active()[0]['acked_by'], 'Aarav')

class Flight1ProfileTests(MissionEngineBase):
    """FLIGHT 1: take off to 2 m, automatic check, climb to 5 m, hover, land."""
    def setUp(self):
        super().setUp()
        self.overrides.update({'flight_mode': 'GUIDED', 'lat': 33.4213, 'lon': -111.9327})

    def launch(self):
        self.send('mission_ping'); self.tick()
        r = self.send('mission_create', name='flight 1', confirm='flight', total=180, profile='flight1')
        self.assertTrue(r['ok'], r)
        self.assertEqual(self.engine.mission['profile'], 'flight1')
        self.send('mission_manual', key='manual_observers', value=True)
        # jump to T-0 with the aircraft armed (the countdown itself is covered by other tests)
        self.engine.stage = 'takeoff'
        self.engine.takeoff_at = self.now
        self.vehicle.armed = True
        self.gate_pwm = 1000

    def fly(self, seconds, alt, **extra):
        self.vehicle.alt = alt
        self.overrides.update(extra)
        end = self.now + seconds
        while self.now < end:
            self.send('mission_ping')
            self.tick(0.5)

    def test_takes_off_to_2m(self):
        self.launch()
        self.engine._enter('takeoff', self.snapshot(), self.now)
        self.assertIn(('takeoff', 2.0), self.vehicle.calls)
        self.assertEqual(self.engine.snapshot()['config']['takeoff_alt'], 2.0)

    def test_full_profile(self):
        self.launch()
        self.fly(2, 2.0)
        self.assertEqual(self.engine.stage, 'flight')
        self.assertEqual(self.engine.step['name'], 'hold_low')
        self.fly(21, 2.05)
        self.assertIn(('goto_alt', 5.0), self.vehicle.calls)
        self.assertEqual(self.engine.step['name'], 'climb')
        self.assertTrue(self.engine.step['checks']['ok'])
        self.fly(3, 4.8)
        self.assertEqual(self.engine.step['name'], 'hold_high')
        self.assertNotIn(('land',), self.vehicle.calls)
        self.fly(31, 5.0)
        self.assertIn(('land',), self.vehicle.calls)
        self.assertFalse(self.engine.step['failed'])
        self.fly(5, 0.1)
        self.vehicle.armed = False
        self.fly(1, 0.0)
        self.assertEqual(self.engine.stage, 'landed')
        self.assertTrue(any('CHECK PASSED' in e['msg'] for e in self.engine.events))

    def test_failed_check_lands_from_2m(self):
        self.launch()
        self.fly(2, 2.0)
        self.fly(21, 2.0, roll=14)                     # leaning 14° in a hover
        self.assertIn(('land',), self.vehicle.calls)
        self.assertNotIn(('goto_alt', 5.0), self.vehicle.calls)
        self.assertTrue(self.engine.step['failed'])
        self.assertIn('tilted', self.engine.step['why'])

    def test_drift_and_height_failures(self):
        self.launch()
        self.fly(2, 2.0)
        self.fly(21, 2.9, lat=33.4213 + 3 / 111320)    # 3 m north, 0.9 m too high
        why = self.engine.step['why']
        self.assertIn('drifted', why)
        self.assertIn('height wandered', why)

    def test_violent_hover_lands_immediately(self):
        self.launch()
        self.fly(2, 2.0)
        self.fly(1, 2.0, vib_x=75)
        self.assertIn(('land',), self.vehicle.calls)
        self.assertEqual(self.engine.step['name'], 'landing')

    def test_pilot_takeover_stops_the_profile(self):
        self.launch()
        self.fly(2, 2.0)
        self.fly(1, 2.0, flight_mode='LOITER')
        self.assertEqual(self.engine.step['name'], 'released')
        self.fly(30, 2.0)
        self.assertNotIn(('goto_alt', 5.0), self.vehicle.calls)
        self.assertNotIn(('land',), self.vehicle.calls)

    def test_climb_timeout_lands(self):
        self.launch()
        self.fly(2, 2.0)
        self.fly(21, 2.0)
        self.fly(22, 3.0)                              # stuck at 3 m
        self.assertIn(('land',), self.vehicle.calls)
        self.assertIn('did not reach', self.engine.step['why'])

    def test_restart_mid_profile_hands_over(self):
        self.launch()
        self.fly(2, 2.0)
        self.engine = self.make_engine()               # Jetson restarts
        self.assertEqual(self.engine.step['name'], 'released')
        self.fly(30, 2.0)
        self.assertNotIn(('goto_alt', 5.0), self.vehicle.calls)

    def test_profile_validation(self):
        self.send('mission_ping'); self.tick()
        self.assertFalse(self.send('mission_create', name='x', confirm='flight', profile='loop-de-loop')['ok'])
        self.assertFalse(self.send('mission_create', name='x', confirm='test', bench=True, profile='route')['ok'])

    def test_hover_profile_unchanged(self):
        self.create()
        self.assertEqual(self.engine.mission['profile'], 'hover')
        self.assertEqual(self.engine.takeoff_alt(), 5.0)


class Flight2ProfileTests(MissionEngineBase):
    """FLIGHT 2: 5/10/15/20 m staircase with a check at each, 360° yaw, 5 m box, home at 20 m, land."""
    LAT, LON = 33.4213, -111.9327

    def setUp(self):
        super().setUp()
        self.overrides.update({'flight_mode': 'GUIDED', 'lat': self.LAT, 'lon': self.LON, 'yaw': 90.0})

    def launch(self):
        self.send('mission_ping'); self.tick()
        r = self.send('mission_create', name='flight 2', confirm='flight', total=180, profile='flight2')
        self.assertTrue(r['ok'], r)
        self.send('mission_manual', key='manual_observers', value=True)
        self.vehicle.armed = True
        self.gate_pwm = 1000
        self.engine.stage = 'takeoff'
        self.engine._enter('takeoff', self.snapshot(), self.now)     # sets the home point and the box

    def fly(self, seconds, follow=True, **extra):
        """Fly the aircraft toward whatever the Jetson last commanded."""
        self.overrides.update(extra)
        end = self.now + seconds
        while self.now < end:
            if follow:
                if self.vehicle.goto:
                    alt, lat, lon = self.vehicle.goto[-1]
                    self.vehicle.alt += max(-1.0, min(1.0, alt - self.vehicle.alt))          # 2 m/s
                    if lat is not None:
                        self.overrides.update(lat=lat, lon=lon)
            self.send('mission_ping')
            self.tick(0.5)

    def test_takes_off_to_5m_and_starts_checking(self):
        self.launch()
        self.engine._enter('takeoff', self.snapshot(), self.now)
        self.assertIn(('takeoff', 5.0), self.vehicle.calls)
        self.vehicle.alt = 5.0
        self.fly(2, follow=False)
        self.assertEqual(self.engine.stage, 'flight')
        self.assertEqual((self.engine.step['name'], self.engine.step['i']), ('hold', 0))
        view = self.engine.snapshot()['step']
        self.assertEqual(view['labels'][view['pos']], '5 m')

    def test_staircase_then_yaw(self):
        self.launch()
        self.vehicle.alt = 5.0
        self.fly(2, follow=False)
        self.fly(16, follow=False)                       # 15 s hold at 5 m, passes
        self.assertIn(('goto_alt', 10.0), self.vehicle.calls)
        self.assertEqual(self.engine.step['name'], 'climb')
        for alt in (10.0, 15.0, 20.0):
            self.fly(6, follow=True)                     # climbs there
            self.assertEqual(self.engine.step['name'], 'hold', alt)
            self.fly(16, follow=False)
        self.assertEqual(self.engine.step['name'], 'yaw')
        self.assertEqual(self.vehicle.yaws, [(360.0, 30.0)])
        self.assertEqual([r['alt'] for r in self.engine.step['results']], [5.0, 10.0, 15.0, 20.0])
        self.assertTrue(all(r['ok'] for r in self.engine.step['results']))
        self.assertNotIn(('land',), self.vehicle.calls)

    def _to_yaw(self):
        self.launch()
        self.vehicle.alt = 5.0
        self.fly(2, follow=False)
        for _ in range(4):
            self.fly(16, follow=False)
            self.fly(6, follow=True)
        self.assertEqual(self.engine.step['name'], 'yaw')

    def test_yaw_then_box_then_home_then_land(self):
        self._to_yaw()
        for deg in range(0, 361, 30):                    # the aircraft turns through a full circle
            self.overrides['yaw'] = (90 + deg) % 360
            self.fly(1, follow=False)
        self.assertEqual(self.engine.step['name'], 'leg')
        corners = self.engine.step['corners']
        self.assertEqual(len(corners), 4)
        # a closed 5 m square: the last corner is the take-off point
        self.assertLess(abs(corners[3][0] - self.LAT) * 111320, 0.01)
        self.assertLess(abs(corners[3][1] - self.LON) * 111320 * 0.835, 0.01)
        self.fly(30, follow=True)                        # the four sides, home, then land
        self.assertEqual([g[0] for g in self.vehicle.goto[-6:]], [20.0] * 6)
        self.assertEqual(self.vehicle.goto[-1][1:], (self.LAT, self.LON))     # last target: the home point
        self.assertIn(('land',), self.vehicle.calls)
        self.assertFalse(self.engine.step['failed'])
        self.assertTrue(any('box complete' in e['msg'] for e in self.engine.events))

    def test_failed_check_lands_where_it_is(self):
        self.launch()
        self.vehicle.alt = 5.0
        self.fly(2, follow=False)
        self.fly(16, follow=False)
        self.fly(6, follow=True)
        self.fly(16, follow=False, roll=14)              # leaning at 10 m
        self.assertIn(('land',), self.vehicle.calls)
        self.assertNotIn(('goto_alt', 15.0), self.vehicle.calls)
        self.assertTrue(self.engine.step['failed'])
        self.assertEqual(self.engine.step['at_pos'], 1)
        self.assertIn('10 m', self.engine.step['why'])

    def test_yaw_never_finishes_lands(self):
        self._to_yaw()
        self.fly(32, follow=False)                       # heading never changes
        self.assertIn(('land',), self.vehicle.calls)
        self.assertIn('360° turn not finished', self.engine.step['why'])

    def test_box_side_not_reached_lands(self):
        self._to_yaw()
        for deg in range(0, 361, 30):
            self.overrides['yaw'] = (90 + deg) % 360
            self.fly(1, follow=False)
        self.fly(27, follow=False)                       # drone never moves
        self.assertIn('not reached', self.engine.step['why'])
        self.assertIn(('land',), self.vehicle.calls)

    def test_pilot_takeover_and_restart(self):
        self.launch()
        self.vehicle.alt = 5.0
        self.fly(2, follow=False)
        self.fly(1, follow=False, flight_mode='LOITER')
        self.assertEqual(self.engine.step['name'], 'released')
        self.fly(30, follow=False)
        self.assertNotIn(('goto_alt', 10.0), self.vehicle.calls)
        self.overrides['flight_mode'] = 'GUIDED'
        self.engine = self.make_engine()
        self.assertEqual(self.engine.step['name'], 'released')

    def test_violent_vibration_lands_immediately(self):
        self.launch()
        self.vehicle.alt = 5.0
        self.fly(2, follow=False)
        self.fly(1, follow=False, vib_x=75)
        self.assertIn(('land',), self.vehicle.calls)

    def test_flight1_unaffected(self):
        self.send('mission_ping'); self.tick()
        self.assertTrue(self.send('mission_create', name='f1', confirm='flight', total=180, profile='flight1')['ok'])
        self.assertEqual(self.engine.takeoff_alt(), 2.0)

    def test_home_point_set_at_takeoff_and_box_planned(self):
        self.launch()
        snap = self.engine.snapshot()
        self.assertEqual((snap['home_point']['lat'], snap['home_point']['lon']), (self.LAT, self.LON))
        self.assertTrue(any(e['kind'] == 'home' and 'HOME POINT SET' in e['msg'] for e in self.engine.events))
        plan = snap['plan']
        self.assertEqual(len(plan['path']), 5)
        self.assertEqual(plan['sides_done'], 0)
        # heading 90° (east): the first side goes east, the box closes on the home point
        self.assertGreater(plan['path'][1][1], self.LON)
        self.assertAlmostEqual(plan['path'][1][0], self.LAT, places=6)
        self.assertAlmostEqual(plan['path'][4][0], self.LAT, places=6)
        self.assertAlmostEqual(plan['path'][4][1], self.LON, places=6)
        self.assertEqual(snap['pos'], [self.LAT, self.LON])

    def test_plan_progress_and_persistence(self):
        self._to_yaw()
        for deg in range(0, 361, 30):
            self.overrides['yaw'] = (90 + deg) % 360
            self.fly(1, follow=False)
        for _ in range(60):                       # fly the box a bit at a time
            self.fly(1, follow=True)
            if self.engine.step['name'] != 'leg':
                break
        self.assertEqual(self.engine.snapshot()['plan']['sides_done'], 4)
        restarted = self.make_engine()
        self.assertEqual(restarted.home_point['lat'], self.LAT)
        self.assertEqual(len(restarted.plan_corners), 4)

    def test_hover_profile_also_sets_home(self):
        self.send('mission_ping'); self.tick()
        self.assertTrue(self.send('mission_create', name='h', confirm='flight', total=180)['ok'])
        self.vehicle.armed = True; self.gate_pwm = 1000
        self.engine.stage = 'takeoff'
        self.engine._enter('takeoff', self.snapshot(), self.now)
        self.assertEqual(self.engine.snapshot()['home_point']['lon'], self.LON)
        self.assertIsNone(self.engine.snapshot()['plan'])
