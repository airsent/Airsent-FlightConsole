"""Flight log analyser: rules on synthetic reports, the server actions, and (when a SITL log is
available locally) a full run on a real ArduCopter .bin."""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import log_analysis as la  # noqa: E402

SITL_BIN = os.environ.get('AIRSENT_TEST_BIN', '/tmp/sitlfly/logs/00000001.BIN')


def base_report(**over):
    r = {'params': {'MOT_HOVER_LEARN': 0, 'INS_HNTCH_ENABLE': 0}, 'flight': {'flying_s': 90},
         'vibration': {'0': {'X': {'mean': 5, 'max': 8}, 'Y': {'mean': 5, 'max': 9}, 'Z': {'mean': 8, 'max': 12},
                             'clip_in_flight': 0, 'clip_total': 0}},
         'throttle': {'hover_out_mean': 0.38, 'learned_hover': 0.38, 'param_hover': 0.38},
         'motors': {'spread_pct': 3, 'ccw_minus_cw_pct': 1, 'front_minus_rear_pct': 1, 'right_minus_left_pct': 0,
                    'saturated_pct_of_flight': 0, 'hardest': 'M1 front-right'},
         'attitude': {'roll_err_rms_deg': 0.6, 'roll_err_max_deg': 3, 'pitch_err_rms_deg': 0.7, 'pitch_err_max_deg': 3,
                      'yaw_err_rms_deg': 1},
         'altitude': {'err_rms_m': 0.1, 'err_max_m': 0.4}, 'position': {'err_rms_m': 0.3, 'err_max_m': 1, 'err_p95_m': 0.6},
         'battery': {'min_cell_v': 3.8, 'sag_v': 1.0, 'cells_guess': 6},
         'gps': {'min_sats': 18, 'max_hdop': 0.7}, 'events': {'errors': []},
         'spectrum': {'available': True, 'peaks': [{'hz': 72.0, 'x_floor': 30}]}}
    for k, v in over.items():
        r[k] = {**r.get(k, {}), **v} if isinstance(v, dict) else v
    return r


def verdict(r):
    f, p = la.rules(r)
    return ('no_go' if any(x['level'] == 'critical' for x in f) else 'caution' if any(x['level'] == 'warning' for x in f) else 'go'), f, p


class Rules(unittest.TestCase):
    def test_clean_flight_is_go_and_proposes_the_notch(self):
        v, f, p = verdict(base_report())
        self.assertEqual(v, 'go')
        got = {x['param']: x['value'] for x in p}
        self.assertEqual(got, {'INS_HNTCH_ENABLE': 1, 'INS_HNTCH_MODE': 1, 'INS_HNTCH_FREQ': 72, 'INS_HNTCH_BW': 36,
                               'INS_HNTCH_REF': 0.38})

    def test_notch_not_proposed_when_already_on(self):
        _, _, p = verdict(base_report(params={'INS_HNTCH_ENABLE': 1}))
        self.assertFalse([x for x in p if x['param'].startswith('INS_HNTCH')])

    def test_no_batch_data_asks_for_it(self):
        _, _, p = verdict(base_report(spectrum={'available': False, 'why': 'x'}))
        self.assertIn('INS_LOG_BAT_MASK', [x['param'] for x in p])

    def test_high_vibration_and_clipping_is_no_go(self):
        v, f, _ = verdict(base_report(vibration={'0': {'X': {'mean': 40, 'max': 70}, 'Y': {'mean': 5, 'max': 9},
                                                       'Z': {'mean': 8, 'max': 12}, 'clip_in_flight': 300}}))
        self.assertEqual(v, 'no_go')
        self.assertTrue(any('clipped' in x['text'] for x in f))

    def test_poor_attitude_tracking_is_no_go(self):
        v, f, _ = verdict(base_report(attitude={'roll_err_rms_deg': 4.2, 'roll_err_max_deg': 18}))
        self.assertEqual(v, 'no_go')

    def test_heavy_aircraft_and_cg(self):
        v, f, _ = verdict(base_report(throttle={'hover_out_mean': 0.56}, motors={'front_minus_rear_pct': -11}))
        self.assertEqual(v, 'caution')
        txt = ' '.join(x['text'] for x in f)
        self.assertIn('56%', txt)
        self.assertIn('fore/aft', txt)

    def test_hover_learn_off_proposes_learned_value(self):
        _, _, p = verdict(base_report(throttle={'learned_hover': 0.452, 'param_hover': 0.35}))
        self.assertIn({'param': 'MOT_THST_HOVER', 'value': 0.452}, [{'param': x['param'], 'value': x['value']} for x in p])

    def test_low_cell_voltage(self):
        v, f, _ = verdict(base_report(battery={'min_cell_v': 3.3}))
        self.assertEqual(v, 'no_go')


@unittest.skipUnless(os.path.isfile(SITL_BIN), 'no SITL .bin available locally')
class RealLog(unittest.TestCase):
    def test_full_analysis_of_a_sitl_flight(self):
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copy(SITL_BIN, os.path.join(tmp, 'pixhawk.bin'))
            rep = la.analyse_folder(tmp)
            self.assertTrue(os.path.isfile(os.path.join(tmp, 'analysis', 'report.json')))
            self.assertGreater(rep['flight']['flying_s'], 30)
            self.assertIn(rep['verdict'], ('go', 'caution', 'no_go'))
            self.assertLess(len(json.dumps(rep)), 60000)        # small enough to send to the reviewer
            ser = json.load(open(os.path.join(tmp, 'analysis', 'series.json')))
            for k in ('alt', 'alt_target', 'throttle', 'roll', 'roll_des', 'm1', 'm4', 'vibe_x', 'volt', 'curr', 'pos_err'):
                self.assertIn(k, ser['series'])
            self.assertEqual(ser['t0_offset_s'], -15.0)            # aligned to take-off
            self.assertTrue(ser['spectrum'] and ser['modes'])
            self.assertTrue(rep['has_series'])
            self.assertNotIn('_curve', rep['spectrum'])


_argv = sys.argv
sys.argv = ['airsent_server.py', '--sim']
import airsent_server as srv  # noqa: E402
sys.argv = _argv


class ServerActions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp, 'm1'))
        self.p = mock.patch.object(srv, 'MISSION_ROOT', self.tmp)
        self.p.start()

    def tearDown(self):
        self.p.stop()
        shutil.rmtree(self.tmp)

    def run_cmd(self, **cmd):
        return srv._process_command(json.dumps(cmd))

    def test_log_actions_work_without_the_pixhawk(self):
        with mock.patch.object(srv, 'mav_conn', None):
            r = self.run_cmd(action='log_list')
        self.assertTrue(r['ok'], r)
        self.assertEqual([m['folder'] for m in r['missions']], ['m1'])

    def test_analyse_needs_a_bin(self):
        r = self.run_cmd(action='log_analyze', folder='m1')
        self.assertFalse(r['ok'])
        self.assertIn('pixhawk.bin', r['msg'])

    def test_analyse_runs_and_shows_in_detail(self):
        open(os.path.join(self.tmp, 'm1', 'pixhawk.bin'), 'wb').write(b'x')
        fake = {'verdict': 'go', 'analysed_s': 0.1, 'findings': [], 'proposals': []}

        def fake_analyse(path, progress=None):
            os.makedirs(os.path.join(path, 'analysis'), exist_ok=True)
            json.dump(fake, open(os.path.join(path, 'analysis', 'report.json'), 'w'))
            return fake
        with mock.patch.object(la, 'analyse_folder', fake_analyse):
            self.assertTrue(self.run_cmd(action='log_analyze', folder='m1')['ok'])
            for _ in range(50):
                if (srv.analysis_jobs.get('m1') or {}).get('state') == 'done':
                    break
                time.sleep(0.05)
        d = self.run_cmd(action='log_detail', folder='m1')
        self.assertEqual(d['analysis']['report']['verdict'], 'go')
        self.assertEqual(d['analysis']['job']['state'], 'done')

    def test_save_review_and_applied(self):
        r = self.run_cmd(action='log_ai_save', folder='m1', operator='Aarav', review={'verdict': 'caution', 'summary': 's'})
        self.assertTrue(r['ok'])
        r = self.run_cmd(action='log_ai_save', folder='m1', operator='Aarav', applied={'values': {'INS_HNTCH_FREQ': 72}})
        ai = json.load(open(os.path.join(self.tmp, 'm1', 'analysis', 'ai.json')))
        self.assertEqual(ai['reviews'][0]['by'], 'Aarav')
        self.assertEqual(ai['applied'][0]['values'], {'INS_HNTCH_FREQ': 72})

    def test_paths_cannot_escape_the_mission_folder(self):
        r = self.run_cmd(action='log_ai_save', folder='../../etc', review={'x': 1})
        self.assertFalse(r['ok'])


if __name__ == '__main__':
    unittest.main()
