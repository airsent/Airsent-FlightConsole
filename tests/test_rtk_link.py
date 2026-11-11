"""Ground station / RTK base inferred from the rover's RTK fix."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
_argv = sys.argv
sys.argv = ['airsent_server.py', '--sim']
import airsent_server as srv  # noqa: E402
sys.argv = _argv


class RtkLink(unittest.TestCase):
    def setUp(self):
        srv.rtk_link.update(last=0.0, since=0.0, fix=None, ever=False)

    def test_never_seen(self):
        p = srv.rtk_link_payload(1000)
        self.assertEqual((p['online'], p['ever'], p['age']), (False, False, None))

    def test_dgps_is_not_the_base(self):
        srv.rtk_corrections_seen(4, 1000)            # SBAS can give DGPS without any base
        self.assertFalse(srv.rtk_link_payload(1000.5)['ever'])

    def test_rtk_fix_means_online_then_lost(self):
        srv.rtk_corrections_seen(5, 1000)
        srv.rtk_corrections_seen(6, 1005)
        p = srv.rtk_link_payload(1006)
        self.assertEqual((p['online'], p['fix'], p['for_s']), (True, 'RTK FIX', 6))
        srv.rtk_corrections_seen(3, 1007)            # dropped back to 3D: corrections stopped
        p = srv.rtk_link_payload(1005 + srv.RTK_ONLINE_S + 1)
        self.assertFalse(p['online'])
        self.assertTrue(p['ever'])
        self.assertGreater(p['age'], srv.RTK_ONLINE_S)

    def test_in_telemetry(self):
        if not hasattr(srv, 'esc_monitor'):
            srv.esc_monitor = None                   # created in main() on the Jetson
        srv.rtk_corrections_seen(6, srv.time.time())
        self.assertTrue(srv.build_telemetry_payload()['rtk_corrections']['online'])


if __name__ == '__main__':
    unittest.main()
