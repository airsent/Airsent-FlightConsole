"""ASC's gps_degraded check: satellites lost against this flight's own best.

    python3 tests/test_asc_gps_degraded.py

Why it exists: ASC's other GPS checks are absolute (min_sats=10, max_hdop=1.5). ESC
switching noise taking 26 satellites to 12 is a collapse that passes every one of them in
silence. On this aircraft the modem was measured taking 26-30 satellites to 3-8, so a
relative collapse is a shape we know happens here, not a hypothetical.
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import asc                                                          # noqa: E402

CFG = asc.default_config()
CHECK = next(c for c in asc.build_checks(CFG) if c.id == 'gps_degraded')


def run(baseline, sats):
    return CHECK.fn({'sats': sats}, {'sats_baseline': baseline})


class TestGpsDegraded(unittest.TestCase):

    def test_runs_while_armed_not_only_flying(self):
        """EMI starts the moment the motors spin, which is before take-off."""
        self.assertEqual(CHECK.when, 'armed')

    def test_holds_fire_without_a_baseline(self):
        self.assertEqual(run(None, 8)[0], asc.OK)

    def test_holds_fire_on_a_weak_baseline(self):
        """A flight that never had a good fix must not be judged against it."""
        self.assertEqual(run(9, 3)[0], asc.OK, 'baseline below sats_baseline_min should not judge')

    def test_steady_satellites_are_ok(self):
        self.assertEqual(run(26, 26)[0], asc.OK)
        self.assertEqual(run(26, 24)[0], asc.OK, 'small wobble is not a finding')

    def test_warns_on_a_real_drop(self):
        level, msg, lost = run(26, 16)                               # lost 10 of 26 = 38%
        self.assertEqual(level, asc.WARN)
        self.assertIn('Down 10 satellites', msg)
        self.assertEqual(lost, 10)

    def test_critical_on_a_collapse(self):
        level, msg, lost = run(26, 10)                               # lost 16 of 26 = 62%
        self.assertEqual(level, asc.CRIT)
        self.assertIn('motor/ESC interference', msg)
        self.assertEqual(lost, 16)

    def test_the_case_the_absolute_checks_miss(self):
        """26 -> 12 is a collapse that min_sats=10 never sees. This is the whole point."""
        self.assertGreater(12, CFG['min_sats'], 'premise: 12 satellites passes min_sats')
        self.assertEqual(run(26, 12)[0], asc.CRIT)

    def test_the_measured_modem_case(self):
        """26-30 down to 3-8, as measured on this aircraft on 2026-11-16."""
        self.assertEqual(run(28, 5)[0], asc.CRIT)

    def test_boundaries(self):
        self.assertEqual(run(20, 13)[0], asc.WARN, '35% exactly should warn')
        self.assertEqual(run(20, 10)[0], asc.CRIT, 'half the satellites gone should crit')
        self.assertEqual(run(20, 14)[0], asc.OK, 'just under the warn line is OK')

    def test_more_satellites_than_baseline_is_not_a_finding(self):
        level, _, lost = run(20, 25)
        self.assertEqual(level, asc.OK)
        self.assertEqual(lost, -5)


class TestBaselineCapture(unittest.TestCase):
    """Drive the real ASC so the CAPTURE is covered, not just the comparison.

    The first version of this check only captured a baseline `while not armed`. Every test
    above still passed, because they hand the baseline in. On the aircraft ASC opens its
    session when `armed or stage in (...)`, so a direct arm-and-take-off begins ALREADY
    armed, nothing was ever captured, and the check sat at OK with a null value through a
    58% satellite collapse. These tests exist so that cannot come back.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.t = 1000.0
        self.a = asc.ASC(root=self.tmp, clock=lambda: self.t, real=False)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def tick(self, sats, armed=False, alt=0.0, n=4):
        """Feed n samples; ASC needs a few to settle a check's sustain window."""
        for _ in range(n):
            self.t += 1.0
            self.a.tick({'armed': armed, 'alt': alt, 'sats': sats, 'fix_type': 'RTK FIX',
                         'hdop': 0.7, 'flight_mode': 'GUIDED', 'ekf_healthy': True},
                        now=self.t, mission={'stage': 'armed' if armed else 'prearm'})

    def level_of(self, cid):
        row = [r for r in self.a.live() if r[0] == cid]
        return row[0][1] if row else None

    def test_baseline_is_captured_when_the_session_starts_already_armed(self):
        """The arm-and-take-off path: no disarmed samples ever arrive."""
        self.tick(26, armed=True, alt=0.0)          # armed, still on the ground
        self.assertEqual(self.a.sats_baseline, 26, 'nothing captured on a direct arm')

    def test_baseline_is_captured_on_the_ground_while_disarmed(self):
        self.tick(27, armed=False, alt=0.0)
        self.assertEqual(self.a.sats_baseline, 27)

    def test_baseline_freezes_once_flying(self):
        """Or ESC noise drags the baseline down with the signal it should measure."""
        self.tick(26, armed=True, alt=0.0)
        self.tick(30, armed=True, alt=10.0)         # airborne; must not raise the baseline
        self.assertEqual(self.a.sats_baseline, 26)
        self.tick(8, armed=True, alt=10.0)          # collapse; must not lower it either
        self.assertEqual(self.a.sats_baseline, 26)

    def test_a_collapse_in_flight_reaches_critical(self):
        self.tick(26, armed=True, alt=0.0)
        self.tick(10, armed=True, alt=10.0, n=10)   # 62% gone, past the 3 s sustain
        self.assertEqual(self.level_of('gps_degraded'), asc.CRIT)

    def test_a_steady_flight_raises_nothing(self):
        self.tick(26, armed=True, alt=0.0)
        self.tick(25, armed=True, alt=10.0, n=10)
        self.assertEqual(self.level_of('gps_degraded'), asc.OK)

    def test_baseline_resets_between_sessions(self):
        self.tick(26, armed=True, alt=0.0)
        self.assertEqual(self.a.sats_baseline, 26)
        self.a.reset()
        self.assertIsNone(self.a.sats_baseline, 'each flight is judged on its own')


class TestConfig(unittest.TestCase):
    def test_config_thresholds_are_sane(self):
        self.assertLess(CFG['sats_drop_warn'], CFG['sats_drop_crit'])
        self.assertGreater(CFG['sats_baseline_min'], CFG['min_sats'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
