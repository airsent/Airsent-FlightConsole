"""Link manager (netlink.py) decisions + the server side: reconnect on switch, video profiles, on-demand."""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import netlink as nl  # noqa: E402


def link(kind, iface='x', ok=0, fail=0):
    l = nl.Link(kind)
    l.iface, l.ok_run, l.fail_run = iface, ok, fail
    return l


class Decide(unittest.TestCase):
    def test_wifi_first(self):
        self.assertEqual(nl.choose(None, link('wifi', 'wlan', ok=1), link('cellular', 'wwan0', ok=5))[0], 'wifi')

    def test_short_wifi_blip_does_not_switch(self):
        new, why = nl.choose('wifi', link('wifi', 'wlan', fail=nl.FAIL_TO_SWITCH - 1), link('cellular', 'wwan0', ok=5))
        self.assertEqual((new, why), ('wifi', None))

    def test_wifi_dead_switches_to_cellular(self):
        new, why = nl.choose('wifi', link('wifi', 'wlan', fail=nl.FAIL_TO_SWITCH), link('cellular', 'wwan0', ok=5))
        self.assertEqual(new, 'cellular')
        self.assertIn('WiFi not reaching', why)

    def test_wifi_gone_switches_to_cellular(self):
        new, why = nl.choose('wifi', link('wifi', None), link('cellular', 'wwan0', ok=2))
        self.assertEqual((new, why), ('cellular', 'no WiFi'))

    def test_stays_on_wifi_if_cellular_is_also_dead(self):
        self.assertEqual(nl.choose('wifi', link('wifi', 'wlan', fail=9), link('cellular', 'wwan0', fail=4))[0], 'wifi')

    def test_back_to_wifi_only_after_it_is_steady(self):
        self.assertEqual(nl.choose('cellular', link('wifi', 'wlan', ok=nl.BACK_TO_WIFI - 1), link('cellular', 'wwan0', ok=9))[0], 'cellular')
        new, why = nl.choose('cellular', link('wifi', 'wlan', ok=nl.BACK_TO_WIFI), link('cellular', 'wwan0', ok=9))
        self.assertEqual(new, 'wifi')

    def test_operator_force(self):
        self.assertEqual(nl.choose('wifi', link('wifi', 'wlan', ok=9), link('cellular', 'wwan0', ok=9), 'cellular')[0], 'cellular')
        # forcing a link that isn't there falls back to automatic
        self.assertEqual(nl.choose('wifi', link('wifi', 'wlan', ok=9), link('cellular', None), 'cellular')[0], 'wifi')

    def test_link_counters(self):
        l = nl.Link('wifi')
        for r in (10.0, None, None, 12.0):
            l.update('wlan', r)
        self.assertEqual((l.ok_run, l.fail_run), (1, 0))
        self.assertEqual(l.summary()['loss_pct'], 50)
        l.update('wlan2', 5.0)                         # a new interface starts fresh
        self.assertEqual(l.summary()['loss_pct'], 0)


class UsageCounter(unittest.TestCase):
    def test_counts_and_survives_counter_reset(self):
        tmp = tempfile.mkdtemp()
        try:
            u = nl.Usage(os.path.join(tmp, 'usage.json'))
            vals = iter([(10_000_000, 5_000_000), (30_000_000, 15_000_000), (2_000_000, 1_000_000)])        # third = modem reconnected, counters reset
            orig = open

            def fake_open(path, *a, **k):
                if path.endswith('rx_bytes') or path.endswith('tx_bytes'):
                    class F:
                        def __enter__(s): return s
                        def __exit__(s, *x): pass
                        def read(s): return str(cur[0] if path.endswith('rx_bytes') else cur[1])
                    return F()
                return orig(path, *a, **k)
            nl.open = fake_open
            now = time.time()
            for v in vals:
                cur = v
                u.add('wwan0', now)
            del nl.open
            m = u.summary(now)
            self.assertAlmostEqual(m['month_mb'], 33.0)          # 20+10 MB, then 2+1 MB after the reset
            u.save(now)
            self.assertTrue(os.path.exists(os.path.join(tmp, 'usage.json')))
        finally:
            shutil.rmtree(tmp)


_argv = sys.argv
sys.argv = ['airsent_server.py', '--sim']
import airsent_server as srv  # noqa: E402
sys.argv = _argv


class ServerSide(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        srv.netlink.update(status=None, epoch=0, active=None)

    def tearDown(self):
        shutil.rmtree(self.tmp)
        srv.netlink.update(status=None, epoch=0, active=None)

    def status(self, active, **kw):
        st = {'at': time.time(), 'active': active, 'prefer': 'auto', 'wifi': {}, 'cellular': {}, 'data': {}, 'switches': 0, **kw}
        path = os.path.join(self.tmp, 'status.json')
        json.dump(st, open(path, 'w'))
        return srv.read_netlink(path=path)

    def test_switch_bumps_epoch_once(self):
        self.assertIsNone(srv.netlink_step(self.status('wifi')))           # first report is not a switch
        self.assertEqual(srv.netlink['epoch'], 0)
        ch = srv.netlink_step(self.status('cellular', last_switch={'reason': 'WiFi not reaching the VPS for 3 s'}))
        self.assertEqual(ch[:2], ('wifi', 'cellular'))
        self.assertEqual(srv.netlink['epoch'], 1)
        self.assertIsNone(srv.netlink_step(self.status('cellular')))
        self.assertEqual(srv.netlink['epoch'], 1)

    def test_stale_status_is_ignored(self):
        path = os.path.join(self.tmp, 'status.json')
        json.dump({'at': time.time() - 30, 'active': 'cellular'}, open(path, 'w'))
        self.assertIsNone(srv.read_netlink(path=path))

    def test_video_profile_and_on_demand_follow_the_link(self):
        srv.netlink_step(self.status('wifi'))
        self.assertEqual(srv.video_profile('front')['max'], 10000)
        self.assertTrue(srv.cam_wanted('front'))                        # WiFi: always streaming
        srv.netlink_step(self.status('cellular'))
        self.assertLessEqual(srv.video_profile('front')['max'], 2000)
        srv.cam_watch['front'] = 0
        self.assertFalse(srv.cam_wanted('front'))                       # cellular: only while watched
        r = srv.process_command(json.dumps({'action': 'cam_watch', 'cams': ['front'], 'console_id': 'c1'}))
        self.assertTrue(r['ok'] and r['quiet'])
        self.assertTrue(srv.cam_wanted('front'))
        self.assertFalse(srv.cam_wanted('down'))
        self.assertTrue(srv.cam_wanted('front', now=time.time() + srv.WATCH_HOLD_S + 1) is False)

    def test_net_prefer(self):
        srv.NETLINK_PREFER = os.path.join(self.tmp, 'prefer')
        r = srv.process_command(json.dumps({'action': 'net_prefer', 'mode': 'cellular'}))
        self.assertTrue(r['ok'])
        self.assertEqual(open(srv.NETLINK_PREFER).read(), 'cellular')
        srv.telem['armed'] = True
        try:
            r = srv.process_command(json.dumps({'action': 'net_prefer', 'mode': 'wifi'}))
            self.assertFalse(r['ok'])
            self.assertTrue(srv.process_command(json.dumps({'action': 'net_prefer', 'mode': 'auto'}))['ok'])
        finally:
            srv.telem['armed'] = False
        self.assertFalse(srv.process_command(json.dumps({'action': 'net_prefer', 'mode': 'bogus'}))['ok'])

    def test_payload(self):
        srv.netlink_step(self.status('cellular', data={'month_mb': 120.5}))
        p = srv.netlink_payload()
        self.assertEqual(p['active'], 'cellular')
        self.assertEqual(p['data']['month_mb'], 120.5)
        self.assertIn('front', p['video'])


if __name__ == '__main__':
    unittest.main()
