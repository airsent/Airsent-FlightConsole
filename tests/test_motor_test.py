"""Props-off motor test: full control (any motor, any throttle, up to three minutes),
with the Jetson keeping the run alive so nothing is left spinning on a lost link."""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
_argv = sys.argv
sys.argv = ['airsent_server.py', '--sim']
import airsent_server as srv  # noqa: E402
sys.argv = _argv

DO_MOTOR_TEST = 209


class FakeMav:
    def __init__(self):
        self.target_system = 1
        self.target_component = 1
        self.sent = []
        self.mav = self

    def command_long_send(self, ts, tc, cmd, conf, p1, p2, p3, p4, p5, p6, p7):
        self.sent.append({'cmd': cmd, 'motor': int(p1), 'pct': p3, 'window': p4, 'order': p6})


class MotorTestBase(unittest.TestCase):
    def setUp(self):
        self.mav = FakeMav()
        srv.telem['armed'] = False
        srv.last_mav_heartbeat = 1000.0
        with srv.motor_test_lock:
            srv.motor_test_state.update({'active': False, 'motor': 0, 'pct': 0.0, 'seconds': 0.0,
                                         'sequence': False, 'stage': None, 'started': 0.0,
                                         'until': 0.0, 'by': '', 'outcome': None, 'sent': 0.0})
        patches = [mock.patch.object(srv, 'mav_conn', self.mav),
                   mock.patch.object(srv, 'wait_ack', return_value=0),
                   mock.patch.object(srv, 'log_mavlink', lambda *a, **k: None),
                   mock.patch.object(srv.time, 'time', return_value=1000.0)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self._reset)

    def _reset(self):
        """Module-level state: leave nothing running for the next test file."""
        with srv.motor_test_lock:
            srv.motor_test_state.update({'active': False, 'stage': None, 'until': 0.0,
                                         'outcome': None, 'output': None, 'idle': None})
        srv.motor_test_map.clear()

    def run_test(self, **over):
        cmd = {'motor': 1, 'throttle': 50, 'seconds': 30, 'props_off_confirmed': True, 'operator': 'Aarav'}
        cmd.update(over)
        return srv.motor_test(self.mav, cmd)

    @property
    def state(self):
        with srv.motor_test_lock:
            return dict(srv.motor_test_state)


class Validation(MotorTestBase):
    def test_full_range_is_allowed(self):
        self.assertTrue(self.run_test(throttle=100, seconds=180)['ok'])
        self.assertEqual(self.state['until'] - self.state['started'], 180)

    def test_rejects_out_of_range(self):
        self.assertIn('0–100', self.run_test(throttle=101)['msg'])
        self.assertIn('0.5–180', self.run_test(seconds=181)['msg'])
        self.assertIn('Test position must be', self.run_test(motor=5)['msg'])

    def test_blocked_while_armed(self):
        srv.telem['armed'] = True
        r = self.run_test()
        self.assertFalse(r['ok'])
        self.assertIn('armed', r['msg'])
        self.assertFalse(self.state['active'])

    def test_needs_props_off_confirmation(self):
        self.assertFalse(self.run_test(props_off_confirmed=False)['ok'])

    def test_one_at_a_time(self):
        self.assertTrue(self.run_test()['ok'])
        self.assertIn('already running', self.run_test()['msg'])

    def test_pixhawk_refusal_leaves_nothing_running(self):
        with mock.patch.object(srv, 'wait_ack', return_value=4):
            r = self.run_test()
        self.assertFalse(r['ok'])
        self.assertFalse(self.state['active'])


class Running(MotorTestBase):
    def at(self, now, beat_age=0.5):
        srv.last_mav_heartbeat = now - beat_age        # the link is alive unless a test says otherwise
        with mock.patch.object(srv.time, 'time', return_value=now):
            return srv._motor_test_tick(now)

    def test_never_hands_the_pixhawk_a_long_timeout(self):
        self.run_test(seconds=180)
        windows = [s['window'] for s in self.mav.sent if s['cmd'] == DO_MOTOR_TEST and s['pct'] > 0]
        self.assertTrue(windows and max(windows) <= srv.MOTOR_TEST_WINDOW,
                        f'a {max(windows)}s timeout would outlive the link')

    def test_keeps_the_run_alive_then_ends_on_time(self):
        self.run_test(motor=2, throttle=40, seconds=10)
        self.mav.sent.clear()
        for t in (1001.0, 1002.0, 1005.0, 1009.5):
            self.assertTrue(self.at(t), f'should still be running at {t}')
        refreshes = [s for s in self.mav.sent if s['pct'] == 40 and s['motor'] == 2]
        self.assertGreaterEqual(len(refreshes), 4)
        self.assertFalse(self.at(1010.1))
        self.assertEqual(self.state['outcome'], 'finished')
        self.assertTrue(all(s['pct'] == 0 for s in self.mav.sent[-4:]), 'every motor should be cut at the end')

    def test_the_pixhawks_own_test_arming_does_not_cut_the_run(self):
        # ArduCopter arms the motors for the duration of a motor test, so the heartbeat reports
        # armed while this runs. Treating that as an abort used to kill every run after ~1 s.
        self.run_test(seconds=60)
        srv.telem['armed'] = True
        self.assertTrue(self.at(1002.0), 'the run must survive the Pixhawk arming its own test')
        self.assertTrue(self.state['active'])

    def test_cannot_start_while_armed(self):
        srv.telem['armed'] = True
        self.assertIn('armed', self.run_test()['msg'])
        self.assertFalse(self.state['active'])

    def test_lost_pixhawk_cuts_the_test(self):
        self.run_test(seconds=60)
        self.assertFalse(self.at(1002.0, beat_age=12.0))   # no heartbeat for 12 s
        self.assertEqual(self.state['outcome'], 'Pixhawk link lost')

    def test_stop_ends_it_and_cuts_every_motor(self):
        self.run_test(seconds=120)
        self.mav.sent.clear()
        r = srv.motor_test(self.mav, {'stop': True, 'operator': 'Aarav'})
        self.assertTrue(r['ok'])
        self.assertFalse(self.state['active'])
        self.assertEqual(sorted(s['motor'] for s in self.mav.sent), [1, 2, 3, 4])
        self.assertTrue(all(s['pct'] == 0 for s in self.mav.sent))

    def test_all_in_turn_walks_the_motors(self):
        self.run_test(motor=0, throttle=25, seconds=10)
        self.assertEqual(self.state['until'] - self.state['started'], 40)
        seen = []
        for t in (1001.0, 1011.0, 1021.0, 1031.0, 1039.0):
            self.at(t)
            seen.append(self.state['stage'])
        self.assertEqual(seen, [1, 2, 3, 4, 4])
        self.assertFalse(self.at(1040.1))

    def test_snapshot_reports_progress(self):
        self.run_test(motor=3, throttle=60, seconds=100)
        snap = srv.motor_test_snapshot(now=1025.0)
        self.assertTrue(snap['active'])
        self.assertEqual((snap['elapsed'], snap['remaining'], snap['total']), (25.0, 75.0, 100.0))
        self.assertEqual(snap['limits'], {'max_pct': 100.0, 'max_seconds': 180.0})
        self.assertNotIn('sent', snap)


if __name__ == '__main__':
    unittest.main()


class LearnsTheMapping(MotorTestBase):
    """ArduCopter's param1 is the frame's test position, not the output number — so the real
    mapping is learned from the servo output that actually answers."""

    def at(self, now, outputs):
        for i, v in outputs.items():
            srv.telem[f'm{i}'] = v
        srv.last_mav_heartbeat = now - 0.5
        with mock.patch.object(srv.time, 'time', return_value=now):
            return srv._motor_test_tick(now)

    def setUp(self):
        super().setUp()
        srv.motor_test_map.clear()
        for i in (1, 2, 3, 4):
            srv.telem[f'm{i}'] = 1000

    def test_position_two_reported_as_the_output_that_moved(self):
        self.run_test(motor=2, throttle=40, seconds=20)
        self.assertEqual(self.mav.sent[0]['motor'], 2, 'param1 must carry the test position')
        self.at(1001.0, {1: 1000, 2: 1000, 3: 1000, 4: 1400})      # an X quad answers on output 4
        self.assertEqual(self.state['output'], 4)
        self.assertEqual(srv.motor_test_snapshot(now=1001.0)['map'], {2: 4})

    def test_ignores_noise_until_one_output_clearly_leads(self):
        self.run_test(motor=1, throttle=40, seconds=20)
        self.at(1001.0, {1: 1012, 2: 1008, 3: 1010, 4: 1006})      # idle jitter only
        self.assertIsNone(self.state['output'])
        self.assertEqual(srv.motor_test_map, {})

    def test_sequence_relearns_at_each_position(self):
        self.run_test(motor=0, throttle=30, seconds=10)
        self.at(1001.0, {1: 1300, 2: 1000, 3: 1000, 4: 1000})
        self.assertEqual((self.state['stage'], self.state['output']), (1, 1))
        self.at(1011.0, {1: 1000, 2: 1000, 3: 1000, 4: 1300})
        self.assertEqual(self.state['stage'], 2)
        self.at(1012.0, {1: 1000, 2: 1000, 3: 1000, 4: 1300})
        self.assertEqual(self.state['output'], 4)
        self.assertEqual(srv.motor_test_map, {1: 1, 2: 4})
