import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import param_store as ps  # noqa: E402


class Msg:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def get_type(self):
        return 'PARAM_VALUE'


class FakePixhawk:
    """Answers PARAM_REQUEST_READ / PARAM_SET like ArduPilot (asynchronously)."""

    def __init__(self, store, params, refuse=(), drop_first_set=False, clamp=None):
        self.store, self.params = store, dict(params)        # name -> (value, type)
        self.refuse, self.drop_first_set, self.clamp = set(refuse), drop_first_set, clamp or {}
        self.target_system = self.target_component = 1
        self.mav = self
        self.sets = []

    def _reply(self, name):
        if name not in self.params:
            return
        v, t = self.params[name]
        threading.Timer(0.01, self.store.observe,
                        [Msg(param_id=name.encode(), param_value=v, param_type=t, param_count=len(self.params))]).start()

    def param_request_read_send(self, ts, tc, name, idx):
        self._reply(name.decode())

    def param_set_send(self, ts, tc, name, value, ptype):
        name = name.decode()
        self.sets.append((name, value))
        if self.drop_first_set:
            self.drop_first_set = False
            return
        if name in self.params and name not in self.refuse:
            lo, hi = self.clamp.get(name, (-1e9, 1e9))
            self.params[name] = (min(hi, max(lo, value)), self.params[name][1])
        self._reply(name)


PARAMS = {'RTL_ALT': (1500.0, ps.INT16), 'WPNAV_SPEED': (500.0, ps.REAL32), 'ATC_RAT_RLL_P': (0.135, ps.REAL32),
          'FS_THR_ENABLE': (1.0, ps.INT8), 'ARMING_CHECK': (1.0, ps.INT32)}


class ParamStoreTests(unittest.TestCase):
    def setUp(self):
        self.store = ps.ParamStore()

    def test_read_reports_values_and_missing_names(self):
        fc = FakePixhawk(self.store, PARAMS)
        found, missing = self.store.read(fc, ['RTL_ALT', 'atc_rat_rll_p', 'THR_MID'], timeout=0.3)
        self.assertEqual(found['RTL_ALT'], 1500)
        self.assertIsInstance(found['RTL_ALT'], int)
        self.assertAlmostEqual(found['ATC_RAT_RLL_P'], 0.135, places=5)
        self.assertEqual(missing, ['THR_MID'])

    def test_write_confirms_by_readback(self):
        fc = FakePixhawk(self.store, PARAMS)
        self.assertEqual(self.store.write(fc, 'RTL_ALT', 2000), 2000)
        self.assertEqual(fc.params['RTL_ALT'][0], 2000)
        self.assertAlmostEqual(self.store.write(fc, 'ATC_RAT_RLL_P', 0.15), 0.15, places=5)

    def test_write_retries_lost_set(self):
        fc = FakePixhawk(self.store, PARAMS, drop_first_set=True)
        self.assertEqual(self.store.write(fc, 'RTL_ALT', 2500, timeout=0.2), 2500)
        self.assertEqual(len(fc.sets), 2)

    def test_refused_and_clamped_values_fail(self):
        fc = FakePixhawk(self.store, PARAMS, refuse={'ARMING_CHECK'}, clamp={'WPNAV_SPEED': (20, 2000)})
        with self.assertRaisesRegex(RuntimeError, 'kept 1'):
            self.store.write(fc, 'ARMING_CHECK', 0)
        with self.assertRaisesRegex(RuntimeError, 'kept 2000'):
            self.store.write(fc, 'WPNAV_SPEED', 5000)

    def test_rejects_bad_input(self):
        fc = FakePixhawk(self.store, PARAMS)
        with self.assertRaisesRegex(ValueError, 'does not exist'):
            self.store.write(fc, 'THR_MID', 0.5, timeout=0.2)
        with self.assertRaisesRegex(ValueError, 'whole numbers'):
            self.store.write(fc, 'RTL_ALT', 12.5)
        with self.assertRaisesRegex(ValueError, 'does not fit'):
            self.store.write(fc, 'FS_THR_ENABLE', 300)
        with self.assertRaisesRegex(ValueError, 'Invalid parameter name'):
            self.store.write(fc, 'BAD NAME;', 1)
        with self.assertRaisesRegex(ValueError, 'finite'):
            self.store.write(fc, 'RTL_ALT', float('nan'))

    def test_write_many_stops_at_first_failure(self):
        fc = FakePixhawk(self.store, PARAMS, refuse={'ARMING_CHECK'})
        done, err = self.store.write_many(fc, {'RTL_ALT': 1800, 'ARMING_CHECK': 0, 'WPNAV_SPEED': 600})
        self.assertEqual(done, {'RTL_ALT': 1800})
        self.assertIn('ARMING_CHECK', err)
        self.assertEqual(fc.params['WPNAV_SPEED'][0], 500.0)      # not reached


if __name__ == '__main__':
    unittest.main()
