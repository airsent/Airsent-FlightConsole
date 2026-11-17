"""ASC's gps_degraded check: satellites lost against this flight's own best.

    python3 tests/test_asc_gps_degraded.py

Why it exists: ASC's other GPS checks are absolute (min_sats=10, max_hdop=1.5). ESC
switching noise taking 26 satellites to 12 is a collapse that passes every one of them in
silence. On this aircraft the modem was measured taking 26-30 satellites to 3-8, so a
relative collapse is a shape we know happens here, not a hypothetical.
"""
import os
import sys
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


class TestBaselineTracking(unittest.TestCase):
    """The baseline must freeze at arming, or ESC noise drags it down with the signal."""

    def test_baseline_is_exposed_and_resets(self):
        a = asc.ASC(root='/tmp/asc-test-unused')
        self.assertIsNone(a.sats_baseline)

    def test_config_thresholds_are_sane(self):
        self.assertLess(CFG['sats_drop_warn'], CFG['sats_drop_crit'])
        self.assertGreater(CFG['sats_baseline_min'], CFG['min_sats'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
