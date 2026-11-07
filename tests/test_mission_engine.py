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
        self.alt = 0.0

    def set_mode(self, name): self.calls.append(('mode', name))
    def arm(self): self.calls.append(('arm',)); self.armed = True
    def disarm(self, force=False): self.calls.append(('disarm', force)); self.armed = False
    def takeoff(self, alt): self.calls.append(('takeoff', alt))
    def land(self): self.calls.append(('land',))
    def rtl(self): self.calls.append(('rtl',))


class MissionEngineTests(unittest.TestCase):
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
        for key in ('manual_wind', 'manual_airspace', 'manual_observers', 'manual_payload'):
            self.assertTrue(self.send('mission_manual', key=key, value=True)['ok'])

    def run_to(self, remaining):
        """Advance the countdown to `remaining` seconds, keeping the PIC console alive."""
        for _ in range(2000):
            if self.engine.countdown_remaining() <= remaining or self.engine.held:
                return
            self.send('mission_ping')
            self.tick(min(1.0, self.engine.countdown_remaining() - remaining))

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
        self.assertIn('Wind', self.engine.hold_reason)

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
