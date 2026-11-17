"""gps_vs_current(): does the report catch ESC noise deafening the GNSS receiver?

    python3 tests/test_gps_vs_current.py

Real logs cannot cover this yet — every flight log on the aircraft as of 2026-11-16 was
a ground test with under 2 A peak, so the interesting branch never runs on them. These
are synthetic records shaped like the DataFlash ones the parser yields.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import log_analysis as la                                            # noqa: E402


class Rec:
    """Stands in for a pymavlink DataFlash record."""
    def __init__(self, **kw):
        self.__dict__.update(kw)


def log(samples):
    """samples: [(t_s, current_a, nsats, hdop)] -> the dict read_log() would produce."""
    return {
        'GPS': [Rec(I=0, TimeUS=int(t * 1e6), NSats=n, HDop=h, Status=6) for t, _, n, h in samples],
        'BAT': [Rec(Inst=0, TimeUS=int(t * 1e6), Curr=c, Volt=22.2, CurrTot=0) for t, c, _, _ in samples],
    }


class TestGpsVsCurrent(unittest.TestCase):

    def test_refuses_when_motors_never_spun(self):
        """A bench log must say so, not publish a table of noise."""
        r = la.gps_vs_current(log([(t, 0.8, 26, 0.9) for t in range(20)]))
        self.assertFalse(r['available'])
        self.assertIn('motors never spun', r['why'])
        self.assertEqual(r['peak_current_a'], 0.8)

    def test_refuses_without_records(self):
        self.assertFalse(la.gps_vs_current({'GPS': [], 'BAT': []})['available'])

    def test_clean_aircraft_shows_no_drop(self):
        """Current rises to hover and the fix does not care."""
        s = [(t, 2.0, 26, 0.9) for t in range(10)] + [(10 + t, 35.0, 26, 0.9) for t in range(10)]
        r = la.gps_vs_current(log(s))
        self.assertTrue(r['available'])
        self.assertEqual(r['peak_current_a'], 35.0)
        self.assertEqual(r['sats_lost_at_high_current'], 0)
        self.assertIn('0-5A', r['bands'])
        self.assertIn('30-45A', r['bands'])
        self.assertEqual(r['bands']['30-45A']['sats_mean'], 26)

    def test_esc_noise_is_caught(self):
        """Satellites collapse as current rises — the thing this exists to find."""
        s = ([(t, 2.0, 26, 0.9) for t in range(10)]
             + [(10 + t, 20.0, 20, 1.3) for t in range(10)]
             + [(20 + t, 40.0, 11, 2.4) for t in range(10)])
        r = la.gps_vs_current(log(s))
        self.assertTrue(r['available'])
        self.assertEqual(r['sats_lost_at_high_current'], 15)
        self.assertEqual(r['bands']['0-5A']['sats_mean'], 26)
        self.assertEqual(r['bands']['30-45A']['sats_mean'], 11)
        self.assertGreater(r['bands']['30-45A']['hdop_mean'], r['bands']['0-5A']['hdop_mean'])

    def test_bands_are_exclusive_and_counted(self):
        """Every GPS fix lands in exactly one band."""
        s = [(t, float(t) * 5, 25, 1.0) for t in range(12)]          # 0 A .. 55 A
        r = la.gps_vs_current(log(s))
        self.assertEqual(sum(b['samples'] for b in r['bands'].values()), 12)
        self.assertIn('45A+', r['bands'])

    def test_pairs_gps_to_the_nearest_current_sample(self):
        """GPS at 9.9 s must take the 10 s current reading, not the 0 s one."""
        d = {
            'GPS': [Rec(I=0, TimeUS=int(9.9e6), NSats=12, HDop=2.0, Status=6)],
            'BAT': [Rec(Inst=0, TimeUS=0, Curr=1.0, Volt=22.2, CurrTot=0),
                    Rec(Inst=0, TimeUS=int(10e6), Curr=40.0, Volt=21.0, CurrTot=0)],
        }
        r = la.gps_vs_current(d)
        self.assertTrue(r['available'])
        self.assertIn('30-45A', r['bands'])
        self.assertEqual(r['bands']['30-45A']['samples'], 1)
        self.assertNotIn('0-5A', r['bands'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
