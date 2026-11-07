import gzip
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import asc as ascmod  # noqa: E402


class Clock:
    def __init__(self):
        self.t = 1_700_000_000.0

    def __call__(self):
        return self.t


def frame(**kw):
    s = dict(pixhawk_connected=True, pixhawk_heartbeat_age=0.1, ekf_healthy=True, armed=False, flight_mode='LOITER',
             roll=1.0, pitch=-1.0, yaw=90, alt=0.0, vspd=0.0, spd=0.0, lat=33.4213, lon=-111.9327, sats=20, hdop=0.7,
             fix_type='RTK FIX', voltage=25.0, current=1.0, battery=95, vib_x=5, vib_y=5, vib_z=8,
             home={'set': True, 'lat': 33.4213, 'lon': -111.9327}, rc_signal={'input_present': True}, m1=1000, m2=1000, m3=1000, m4=1000)
    s.update(kw)
    return s


class ASCTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        cfg = ascmod.default_config()
        cfg['keep_free_gb'] = 0
        self.a = ascmod.ASC(self.tmp.name, cfg, clock=self.clock, real=False)

    def tearDown(self):
        if self.a.post_thread:
            self.a.post_thread.join(20)
        if self.a.rec:
            self.a.rec.close()
        self.tmp.cleanup()

    def run_for(self, secs, mission=None, heard=False, **kw):
        acts = []
        for _ in range(int(secs * 20)):
            self.clock.t += 0.05
            if heard:
                self.a.console_heard()
            acts += self.a.tick(frame(**kw), mission=mission, extras={'vps': True, 'disk': 100})
        return acts

    def flying(self, **kw):
        return {'armed': True, 'alt': 10.0, 'flight_mode': 'GUIDED', **kw}

    def test_session_records_and_writes_summary_on_disarm(self):
        self.run_for(1)
        self.assertIsNone(self.a.session)                          # nothing until armed
        self.run_for(5, heard=True, **self.flying(vib_z=45))       # high vibration in flight
        folder = self.a.session['folder']
        self.assertGreater(self.a.checks_per_s(), 400)             # "hundreds of checks per second"
        self.assertIn('asc:vibration', self.a.fault_conditions())
        self.run_for(4, armed=False, alt=0)                        # disarmed on the ground → closes after 3 s
        self.assertIsNone(self.a.session)
        d = os.path.join(folder, 'asc')
        summary = json.load(open(os.path.join(d, 'summary.json')))
        self.assertEqual(summary['format'], 'airsent-asc-summary-1')
        self.assertGreater(summary['checks_run'], 2000)
        self.assertEqual(summary['checks']['vibration']['raised'], 1)
        self.assertGreater(summary['checks']['vibration']['warn_s'], 3)
        self.assertEqual(summary['max_alt_m'], 10.0)
        with gzip.open(os.path.join(d, 'samples.jsonl.gz'), 'rt') as f:
            rows = [json.loads(x) for x in f]
        self.assertGreater(len(rows), 100)
        self.assertIn('vibration', rows[-1].get('issues', {}) or rows[50].get('issues', {}))
        self.assertTrue(os.path.getsize(os.path.join(d, 'focus.jsonl.gz')) > 0)
        events = [json.loads(x)['msg'] for x in open(os.path.join(d, 'events.jsonl'))]
        self.assertTrue(any('High vibration' in e for e in events))

    def test_uses_the_launch_sequencer_mission_folder(self):
        mission = {'stage': 'prearm', 'mission': {'folder': os.path.join(self.tmp.name, '2026-11-07_0900_test')}}
        self.run_for(0.5, mission=mission)
        self.assertEqual(self.a.session['folder'], mission['mission']['folder'])   # starts at pre-arm, before arming

    def test_console_link_loss_hold_then_rtl_then_restore(self):
        self.run_for(2, heard=True, **self.flying())
        acts = self.run_for(9, **self.flying())
        self.assertEqual(acts, [])
        acts = self.run_for(2, **self.flying())
        self.assertEqual([a[1] for a in acts], ['LOITER'])
        acts = self.run_for(15, **self.flying(flight_mode='LOITER'))
        self.assertEqual(acts, [])
        acts = self.run_for(6, **self.flying(flight_mode='LOITER'))
        self.assertEqual([a[1] for a in acts], ['RTL'])
        self.assertIn('asc:action', self.a.fault_conditions())
        self.run_for(1, heard=True, **self.flying(flight_mode='RTL'))
        self.assertIsNone(self.a.action)                           # console back: operator decides, no auto-resume

    def test_own_mode_change_reported_late_is_not_an_rc_takeover(self):
        # SITL finding: the new mode reaches telemetry with the next 1 Hz HEARTBEAT, often > 2 s
        # after ASC asked. ASC must still recognise its own LOITER and go on to RTL at 30 s.
        self.run_for(2, heard=True, **self.flying())
        acts = self.run_for(11, **self.flying())
        self.assertEqual([a[1] for a in acts], ['LOITER'])
        self.run_for(3.5, **self.flying())                                  # still GUIDED in telemetry
        self.run_for(14, **self.flying(flight_mode='LOITER'))
        self.assertEqual(self.a.action['stage'], 'hold')
        acts = self.run_for(3, **self.flying(flight_mode='LOITER'))
        self.assertEqual([a[1] for a in acts], ['RTL'])

    def test_obstacle_reads_the_servers_proximity_dist(self):
        self.run_for(1, heard=True, **self.flying(proximity={'obstacle': True, 'dist': 1.2}, stop_distance=3.0))
        self.run_for(1, **self.flying(proximity={'obstacle': True, 'dist': 1.2}, stop_distance=3.0))
        row = next(r for r in self.a.live() if r[0] == 'obstacle')
        self.assertEqual(row[1], ascmod.WARN)
        self.run_for(1, **self.flying(proximity={'obstacle': False, 'dist': -1.0}, stop_distance=3.0))
        row = next(r for r in self.a.live() if r[0] == 'obstacle')
        self.assertEqual(row[1], ascmod.OK)

    def test_defs_carry_units_and_limits(self):
        d = {x[0]: x for x in self.a.defs()}
        self.assertEqual(d['vibration'][3], 'm/s²')
        self.assertIn('30', d['vibration'][4])
        self.assertEqual(d['tilt'][5], 'flying')
        self.assertTrue(all(len(x) == 6 for x in d.values()))

    def test_never_fights_the_rc_pilot(self):
        self.run_for(2, heard=True, **self.flying())
        self.assertEqual(self.run_for(12, **self.flying(flight_mode='ALT_HOLD')), [])   # RC pilot flying: alert only
        self.assertIn('asc:console', self.a.fault_conditions())

    def test_stands_down_when_rc_or_pixhawk_takes_over(self):
        self.run_for(2, heard=True, **self.flying())
        self.assertEqual([a[1] for a in self.run_for(11, **self.flying())], ['LOITER'])
        self.run_for(5, **self.flying(flight_mode='LOITER'))
        self.run_for(1, **self.flying(flight_mode='ALT_HOLD'))     # pilot flips the RC mode switch
        self.assertEqual(self.a.action['stage'], 'stood_down')
        self.assertEqual(self.run_for(30, **self.flying(flight_mode='ALT_HOLD')), [])   # no RTL later

    def test_no_action_if_no_console_was_ever_connected(self):
        self.assertEqual(self.run_for(40, **self.flying()), [])

    def test_no_action_when_disabled(self):
        self.a.act_enabled = False
        self.run_for(2, heard=True, **self.flying())
        self.assertEqual(self.run_for(40, **self.flying()), [])

    def test_pixhawk_failsafe_text_is_flagged(self):
        self.run_for(1, heard=True, **self.flying())
        self.a.statustext('Radio Failsafe')
        snap = self.a.snapshot()
        self.assertEqual(snap['triggered'][0]['kind'], 'rc')
        self.assertIn('asc:fs:rc', self.a.fault_conditions())
        self.clock.t += 1
        self.a.statustext('Radio Failsafe Cleared')
        self.assertNotIn('asc:fs:rc', self.a.fault_conditions())

    def test_tilt_needs_to_be_sustained(self):
        self.run_for(1, heard=True, **self.flying())
        self.run_for(0.1, **self.flying(roll=40))
        self.assertNotIn('tilt', [i['id'] for i in self.a.snapshot()['issues']])
        self.run_for(0.5, **self.flying(roll=40))
        self.assertIn('tilt', [i['id'] for i in self.a.snapshot()['issues']])

    def test_make_room_only_below_threshold(self):
        old = os.path.join(self.tmp.name, 'old'); os.makedirs(old)
        self.assertEqual(ascmod.make_room(self.tmp.name, 0.001, min_free_gb=0.0005), [])     # plenty of space
        self.assertEqual(ascmod.make_room(self.tmp.name, 10**9, min_free_gb=10**9, protect=[old]), [])   # protected

    def test_live_view_runs_at_standby_and_greys_out_flight_checks(self):
        self.run_for(1)
        live = {r[0]: r for r in self.a.snapshot()['live']}
        self.assertEqual(len(live), len(self.a.checks))           # every check is reported
        self.assertEqual(live['tilt'][1], -1)                      # flight-only check: inactive on the ground
        self.assertEqual(live['ekf'][1], 0)                        # always-on check: OK
        self.assertEqual(self.a.fault_conditions(), {})            # no faults / recording outside a session
        self.run_for(1, ekf_healthy=False)
        live = {r[0]: r for r in self.a.snapshot()['live']}
        self.assertEqual(live['ekf'][1], ascmod.WARN)
        self.assertIn('EKF', live['ekf'][3])
        self.assertIn('defs', self.a.snapshot(with_defs=True))

    def test_recording_starts_at_mission_create_and_writes_everything(self):
        mission = {'stage': 'countdown', 'mission': {'folder': os.path.join(self.tmp.name, 'm1')}}
        self.run_for(0.5, mission=mission)
        self.assertIsNotNone(self.a.session)
        self.a.write_params({'RTL_ALT': 3000, 'FS_THR_ENABLE': 1})
        self.a.note_command('set_mode', 'Aarav', {'action': 'set_mode', 'mode': 'LOITER', 'request_id': 'x'})
        self.a.note_command('velocity', 'Aarav', {'vx': 1.5, 'vy': 0, 'vz': 0, 'yaw_rate': 0})
        self.a.note_command('mission_ping', 'Aarav', {})
        seqs = [e['seq'] for e in self.a.snapshot()['events']]
        self.assertEqual(seqs, sorted(seqs, reverse=True))
        self.run_for(3, mission=mission, **self.flying(vib_z=45))
        self.run_for(4, mission={'stage': 'standby'}, armed=False, alt=0)
        d = os.path.join(self.tmp.name, 'm1', 'asc')
        for _ in range(100):                                       # post-processing runs in a thread
            if os.path.exists(os.path.join(d, 'charts', 'checks_timeline.png')):
                break
            import time; time.sleep(0.1)
        files = set(os.listdir(d))
        for f in ('summary.json', 'samples.jsonl.gz', 'samples.csv', 'telemetry_full.jsonl.gz', 'events.jsonl',
                  'controls.jsonl.gz', 'params_start.json', 'focus.jsonl.gz', 'charts'):
            self.assertIn(f, files)
        self.assertIn('altitude.png', os.listdir(os.path.join(d, 'charts')))
        self.assertIn('checks_timeline.png', os.listdir(os.path.join(d, 'charts')))
        events = [json.loads(x)['msg'] for x in open(os.path.join(d, 'events.jsonl'))]
        self.assertTrue(any("Aarav: set_mode {'mode': 'LOITER'}" in e for e in events))
        self.assertFalse(any('mission_ping' in e for e in events))
        with gzip.open(os.path.join(d, 'controls.jsonl.gz'), 'rt') as f:
            self.assertEqual(json.loads(f.readline())['vx'], 1.5)
        self.assertEqual(json.load(open(os.path.join(d, 'params_start.json')))['params']['RTL_ALT'], 3000)
        header = open(os.path.join(d, 'samples.csv')).readline()
        self.assertTrue(header.startswith('t,') and 'voltage' in header)


if __name__ == '__main__':
    unittest.main()
