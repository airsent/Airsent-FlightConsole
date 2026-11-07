import os
import sys
import tempfile
import unittest
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import golden_params as gp  # noqa: E402


class Msg:
    def __init__(self, name, value):
        self.param_id, self.param_value = name.encode(), value


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


class Mav:
    def __init__(self):
        self.target_system = self.target_component = 1
        self.mav = self
        self.asked = []

    def param_request_read_send(self, ts, tc, name, idx):
        self.asked.append(name.decode())


class GoldenTests(unittest.TestCase):
    def make(self, rules):
        f = tempfile.NamedTemporaryFile('w', suffix='.json', delete=False)
        json.dump({'params': rules}, f); f.close()
        self.addCleanup(os.remove, f.name)
        self.clock = Clock()
        return gp.GoldenParams(f.name, clock=self.clock)

    def test_new_firmware_name_is_accepted(self):
        g = self.make({'ARMING_CHECK': {'value': 1, 'alt': {'ARMING_SKIPCHK': {'value': 0}}}, 'FS_THR_ENABLE': {'value': 1}})
        mav = Mav(); g.request(mav)
        self.assertEqual(set(mav.asked), {'ARMING_CHECK', 'ARMING_SKIPCHK', 'FS_THR_ENABLE'})
        g.observe(Msg('ARMING_SKIPCHK', 0)); g.observe(Msg('FS_THR_ENABLE', 1))
        self.assertEqual(g.snapshot()['state'], 'ok')
        g.observe(Msg('ARMING_SKIPCHK', 4))
        snap = g.snapshot()
        self.assertEqual(snap['state'], 'diff')
        self.assertIn('ARMING_SKIPCHK=4', snap['diffs'][0])

    def test_old_firmware_name_still_works(self):
        g = self.make({'ARMING_CHECK': {'value': 1, 'alt': {'ARMING_SKIPCHK': {'value': 0}}}})
        g.request(Mav()); g.observe(Msg('ARMING_CHECK', 1))
        self.assertEqual(g.snapshot()['state'], 'ok')

    def test_name_never_answered_is_reported_not_loading_forever(self):
        g = self.make({'RTL_ALT': {'min': 1000, 'max': 5000}})
        g.request(Mav())
        self.assertEqual(g.snapshot()['state'], 'loading')
        self.clock.t += gp.ABSENT_AFTER + 1
        snap = g.snapshot()
        self.assertEqual(snap['state'], 'diff')
        self.assertIn('not on this Pixhawk', snap['diffs'][0])


if __name__ == '__main__':
    unittest.main()
