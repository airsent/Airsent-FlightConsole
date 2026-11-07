"""End-to-end command tests: real process_command / serve_commands with a fake Pixhawk."""
import asyncio
import json
import os
import sys
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tests'))
_argv = sys.argv
sys.argv = ['airsent_server.py', '--sim']
import airsent_server as srv  # noqa: E402
sys.argv = _argv
from test_vehicle_mission import FakeArduPilot, WP1, WP2, RTL, HOME  # noqa: E402

ARM_DISARM = 400


class FakePixhawk(FakeArduPilot):
    """Mission protocol from FakeArduPilot plus COMMAND_LONG answers."""

    def __init__(self, ack=0, ack_after=0, flying=False):
        super().__init__(srv.vehicle_mission)
        self.ack, self.ack_after, self.flying = ack, ack_after, flying
        self.commands = []

    def command_long_send(self, ts, tc, command, conf, p1, p2, *rest):
        self.commands.append((command, p1, p2))
        n = len(self.commands)
        if self.ack is None or n <= self.ack_after:
            return                                          # lost / unanswered
        result = self.ack
        if command == ARM_DISARM and p1 == 0 and self.flying and p2 != 21196:
            result = 4                                      # ArduPilot: no normal disarm in flight
        def answer():
            time.sleep(0.02)
            with srv.ack_cond:
                srv.cmd_acks[command] = (result, time.time())
                srv.ack_cond.notify_all()
        threading.Thread(target=answer, daemon=True).start()


def run(action, **kw):
    return srv.process_command(json.dumps({'action': action, 'request_id': 'r1', 'operator': 'Aarav', **kw}))


class Commands(unittest.TestCase):
    def setUp(self):
        srv.cmd_acks.clear()
        srv.vehicle_mission.reset()
        srv.telem['armed'] = False
        srv.home_state.clear(); srv.home_state.update(set=True, lat=HOME[0], lon=HOME[1])

    def tearDown(self):
        with srv.mav_lock:
            srv.mav_conn = None

    def use(self, fc):
        with srv.mav_lock:
            srv.mav_conn = fc
        return fc

    def test_emergency_stop_force_disarms_in_flight(self):
        fc = self.use(FakePixhawk(flying=True))
        srv.telem['armed'] = True
        r = run('emergency_stop')
        self.assertTrue(r['ok'], r)
        self.assertEqual(fc.commands[0], (ARM_DISARM, 0, 21196))
        self.assertEqual(r['request_id'], 'r1')

    def test_normal_disarm_in_flight_reports_refusal(self):
        self.use(FakePixhawk(flying=True))
        srv.telem['armed'] = True
        r = run('disarm')
        self.assertFalse(r['ok'])
        self.assertIn('EMERGENCY STOP', r['msg'])

    def test_emergency_stop_retries_lost_commands(self):
        fc = self.use(FakePixhawk(ack_after=2))
        srv.telem['armed'] = True
        r = run('emergency_stop')
        self.assertTrue(r['ok'], r)
        self.assertEqual(len(fc.commands), 3)

    def test_emergency_stop_unconfirmed_says_so(self):
        self.use(FakePixhawk(ack=None))
        srv.telem['armed'] = True
        r = run('emergency_stop')
        self.assertFalse(r['ok'])
        self.assertIn('RC kill switch', r['msg'])

    def test_arm_reports_refusal(self):
        self.use(FakePixhawk(ack=4))
        r = run('arm')
        self.assertFalse(r['ok'])
        self.assertIn('refused', r['msg'])

    def test_wp_upload_and_download(self):
        fc = self.use(FakePixhawk())
        r = run('wp_upload', plan={'name': 'Route', 'waypoints': [WP1, WP2, RTL]})
        self.assertTrue(r['ok'], r)
        self.assertTrue(r['vehicle_mission']['verified'])
        self.assertEqual(len(fc.stored), 6)          # home, takeoff, speed, 2 wps, rtl
        self.assertTrue(srv.vehicle_mission.snapshot()['verified'])      # what telemetry broadcasts
        d = run('wp_download')
        self.assertTrue(d['ok'], d)
        self.assertEqual(len(d['result']['waypoints']), 3)

    def test_wp_upload_rejects_bad_plan_and_armed(self):
        self.use(FakePixhawk())
        r = run('wp_upload', plan={'waypoints': [dict(WP1, alt=400)]})
        self.assertFalse(r['ok'])
        self.assertIn('altitude', r['msg'])
        srv.telem['armed'] = True
        r = run('wp_upload', plan={'waypoints': [WP1]})
        self.assertFalse(r['ok'])
        self.assertIn('armed', r['msg'])

    def test_estop_is_not_queued_behind_an_upload(self):
        self.use(FakePixhawk())
        srv.telem['armed'] = True
        real = srv.vehicle_mission.upload_plan

        def slow_upload(*a, **k):
            time.sleep(1.0)
            raise RuntimeError('slow upload finished')
        srv.vehicle_mission.upload_plan = slow_upload
        srv.telem['armed'] = False
        replies = []

        class Sink:
            async def send(self, text):
                replies.append(json.loads(text))

        async def source():
            yield json.dumps({'action': 'wp_upload', 'request_id': 'up', 'plan': {'waypoints': [WP1]}})
            await asyncio.sleep(0.05)
            yield json.dumps({'action': 'emergency_stop', 'request_id': 'stop'})
            await asyncio.sleep(1.5)

        try:
            asyncio.run(srv.serve_commands(source(), Sink()))
        finally:
            srv.vehicle_mission.upload_plan = real
        self.assertEqual([r['request_id'] for r in replies], ['stop', 'up'])


if __name__ == '__main__':
    unittest.main()


class ParamsAndModes(unittest.TestCase):
    def setUp(self):
        from test_param_store import FakePixhawk as ParamFC, PARAMS
        srv.param_store.reset()
        srv.telem['armed'] = False
        self.fc = ParamFC(srv.param_store, PARAMS)
        with srv.mav_lock:
            srv.mav_conn = self.fc

    def tearDown(self):
        with srv.mav_lock:
            srv.mav_conn = None

    def test_param_read_and_write(self):
        r = run('param_read', names=['RTL_ALT', 'THR_MID'])
        self.assertTrue(r['ok'], r)
        self.assertEqual(r['values'], {'RTL_ALT': 1500})
        self.assertEqual(r['missing'], ['THR_MID'])
        w = run('param_write', params={'RTL_ALT': 2000, 'WPNAV_SPEED': 600})
        self.assertTrue(w['ok'], w)
        self.assertEqual(self.fc.params['RTL_ALT'][0], 2000)

    def test_param_write_blocked_when_armed(self):
        srv.telem['armed'] = True
        w = run('param_write', params={'RTL_ALT': 2000})
        self.assertFalse(w['ok'])
        self.assertEqual(self.fc.sets, [])

    def test_only_console_modes_allowed(self):
        fc = FakePixhawk()
        with srv.mav_lock:
            srv.mav_conn = fc
        self.assertFalse(run('set_mode', mode='FLIP')['ok'])
        self.assertFalse(run('set_mode', mode='STABILIZE')['ok'])
        self.assertEqual(fc.commands, [])
        self.assertTrue(run('set_mode', mode='LOITER')['ok'])
        self.assertTrue(run('set_mode', mode='ALTHOLD')['ok'])


class FailsafeTelemetry(unittest.TestCase):
    def test_payload_has_values_and_rules(self):
        import re
        from test_param_store import Msg
        srv.param_store.reset()
        srv.param_store.observe(Msg(param_id=b'FS_THR_ENABLE', param_value=1.0, param_type=2, param_count=900))
        srv.param_store.observe(Msg(param_id=b'BATT_LOW_VOLT', param_value=21.6, param_type=9, param_count=900))
        srv._failsafe_rules_sent[0] = 0
        out = srv.failsafe_payload(time.time())
        self.assertEqual(out['failsafe_params'], {'FS_THR_ENABLE': 1, 'BATT_LOW_VOLT': 21.6})
        self.assertEqual(out['failsafe_rules']['FS_THR_ENABLE'], {'value': 1})
        self.assertNotIn('why', json.dumps(out['failsafe_rules']))
        self.assertNotIn('failsafe_rules', srv.failsafe_payload(time.time()))      # rules only every 10 s

    def test_web_and_jetson_use_the_same_parameter_list(self):
        import re
        js = open(os.path.join(ROOT, 'failsafe-onboard.js')).read()
        names = set(re.findall(r"name: '([A-Z0-9_]+)'", js))
        self.assertEqual(names, set(srv.FAILSAFE_PARAMS))
        golden = json.load(open(os.path.join(ROOT, 'golden_params.json')))['params']
        for n in ('FS_THR_ENABLE', 'FS_GCS_ENABLE', 'BATT_FS_LOW_ACT', 'FS_EKF_ACTION', 'FENCE_ENABLE'):
            self.assertIn(n, srv.FAILSAFE_PARAMS)
            self.assertIn(n, golden)


class ASCWiring(unittest.TestCase):
    def test_console_commands_feed_asc_and_toggle(self):
        srv.asc_engine.console_seen_at = None
        run('mission_ping', console_id='c1')
        self.assertIsNotNone(srv.asc_engine.console_seen_at)
        before = srv.asc_engine.console_mode_at
        with srv.mav_lock:
            srv.mav_conn = FakePixhawk()
        try:
            run('set_mode', mode='LOITER')
        finally:
            with srv.mav_lock:
                srv.mav_conn = None
        self.assertGreater(srv.asc_engine.console_mode_at, before)
        srv.ASC_STATE_FILE = os.path.join(os.path.dirname(__file__), '_asc_state_test.json')
        try:
            r = run('asc_set', act_enabled=False)
            self.assertTrue(r['ok'])
            self.assertFalse(srv.asc_engine.act_enabled)
            run('asc_set', act_enabled=True)
            self.assertTrue(srv.asc_engine.act_enabled)
        finally:
            if os.path.exists(srv.ASC_STATE_FILE):
                os.remove(srv.ASC_STATE_FILE)

    def test_payload_carries_asc_snapshot(self):
        srv.esc_monitor = None
        p = srv.build_telemetry_payload(include_mission=True)
        self.assertIn(p['asc']['state'], ('standby', 'watching', 'acting'))
        self.assertLess(len(json.dumps(p['asc'])), 2500)
        self.assertNotIn('asc', srv.build_telemetry_payload(include_mission=False))


class StreamRates(unittest.TestCase):
    def test_fast_link_sets_per_message_rates(self):
        fc = FakePixhawk()
        old = srv.PIXHAWK_BAUD
        try:
            srv.PIXHAWK_BAUD = 921600
            srv.configure_streams(fc)
            reqs = [c for c in fc.commands if c[0] == 511]          # MAV_CMD_SET_MESSAGE_INTERVAL
            self.assertEqual(len(reqs), len(srv.FAST_STREAMS))
            self.assertIn((511, 30, 33333), reqs)                    # ATTITUDE (id 30) every 33 ms = 30 Hz
        finally:
            srv.PIXHAWK_BAUD = old

    def test_slow_link_keeps_the_old_request(self):
        fc = FakePixhawk()
        fc.mav.request_data_stream_send = lambda *a: fc.commands.append(('stream',) + a[2:])
        old = srv.PIXHAWK_BAUD
        try:
            srv.PIXHAWK_BAUD = 57600
            self.assertIn('10 Hz', srv.configure_streams(fc))
            self.assertEqual(fc.commands, [('stream', srv.mavutil.mavlink.MAV_DATA_STREAM_ALL, 10, 1)])
        finally:
            srv.PIXHAWK_BAUD = old
