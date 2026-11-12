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
        w = link('wifi', 'wlan', ok=nl.BACK_TO_WIFI)
        w.history = [5.0] * nl.BACK_TO_WIFI
        new, why = nl.choose('cellular', w, link('cellular', 'wwan0', ok=9))
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


class AntiFlap(unittest.TestCase):
    def lossy_wifi(self, ok_run):
        w = link('wifi', 'wlan', ok=ok_run)
        w.history = ([5.0, None, 5.0, None, 5.0] * 6)[:30 - ok_run] + [5.0] * ok_run     # ~40% loss earlier
        return w

    def test_lossy_wifi_that_answers_10_in_a_row_does_not_win(self):
        # ZD650 finding: 40-47% loss WiFi answered 10 pings in a row, won, failed, 22 switches in 4 min
        new, _ = nl.choose('cellular', self.lossy_wifi(10), link('cellular', 'eth', ok=50))
        self.assertEqual(new, 'cellular')

    def test_genuinely_good_wifi_wins_back(self):
        w = link('wifi', 'wlan', ok=30)
        w.history = [5.0] * 30
        self.assertEqual(nl.choose('cellular', w, link('cellular', 'eth', ok=50))[0], 'wifi')

    def test_backoff_requires_longer_proof(self):
        w = link('wifi', 'wlan', ok=15)
        w.history = [5.0] * 15
        self.assertEqual(nl.choose('cellular', w, link('cellular', 'eth', ok=50), back_needed=30)[0], 'cellular')
        self.assertEqual(nl.choose('cellular', w, link('cellular', 'eth', ok=50), back_needed=10)[0], 'wifi')

    def test_rsrp_percent(self):
        self.assertEqual(nl.rsrp_percent(-140), 0)
        self.assertEqual(nl.rsrp_percent(-80), 100)
        self.assertEqual(nl.rsrp_percent(-95), 75)
        self.assertIsNone(nl.rsrp_percent(None))


class MergeNetlink(unittest.TestCase):
    def test_modem_ethernet_shows_as_cellular_with_real_signal(self):
        mm = {'connected': True, 'type': 'ethernet', 'interface': 'enx6a0b', 'percent': 0, 'technology': None}
        st = {'active': 'cellular', 'cellular': {'iface': 'enx6a0b', 'answering': True, 'percent': 72, 'technology': '5G', 'rsrp': -97.0}}
        r = srv.merge_netlink(dict(mm), st)
        self.assertEqual((r['type'], r['percent'], r['technology'], r['rsrp']), ('cellular', 72, '5G', -97.0))

    def test_unknown_signal_is_unknown_not_weak(self):
        st = {'active': 'cellular', 'cellular': {'iface': 'enx', 'answering': True}}
        self.assertIsNone(srv.merge_netlink({'percent': 0}, st)['percent'])

    def test_wifi_untouched(self):
        r = {'type': 'wifi', 'percent': 80}
        self.assertEqual(srv.merge_netlink(dict(r), {'active': 'wifi'}), r)


class ModemAT(unittest.TestCase):
    """AT+QENG="servingcell" replies from the RM520N (formats from Quectel's RG/RM5xx AT manual)."""
    LTE = ('\r\n+QENG: "servingcell","NOCONN","LTE","FDD",310,260,1A2B3C4,123,5110,12,5,5,9C41,-97,-11,-68,13,9,190,-\r\n\r\nOK\r\n')
    NSA = ('\r\n+QENG: "servingcell","NOCONN"\r\n'
           '+QENG: "LTE","FDD",310,260,1A2B3C4,123,66986,71,5,5,9C41,-101,-12,-70,8,10,190,-\r\n'
           '+QENG: "NR5G-NSA",310,260,512,-92,18,-11,632256,41,12,1\r\n\r\nOK\r\n')
    SA = ('\r\n+QENG: "servingcell","NOCONN","NR5G-SA","TDD",310,260,1A2B3C4D5,512,9C41,520110,41,12,-88,-10,22,1,-\r\n\r\nOK\r\n')

    def test_lte(self):
        s = nl.parse_qeng(self.LTE)
        self.assertEqual((s['technology'], s['rsrp'], s['rsrq']), ('LTE', -97.0, -11.0))
        self.assertEqual(s['percent'], nl.rsrp_percent(-97))

    def test_5g_nsa_uses_nr_and_keeps_lte_anchor(self):
        s = nl.parse_qeng(self.NSA)
        self.assertEqual((s['technology'], s['rsrp'], s['sinr'], s['rsrq'], s['lte_rsrp']), ('5G NSA', -92.0, 18.0, -11.0, -101.0))

    def test_5g_sa(self):
        s = nl.parse_qeng(self.SA)
        self.assertEqual((s['technology'], s['rsrp'], s['rsrq'], s['sinr']), ('5G SA', -88.0, -10.0, 22.0))

    def test_no_service(self):
        self.assertEqual(nl.parse_qeng('\r\n+QENG: "servingcell","SEARCH"\r\n\r\nOK\r\n'), {})
        self.assertEqual(nl.parse_qeng('\r\nERROR\r\n'), {})
        self.assertEqual(nl.parse_qeng(''), {})

    def test_nsa_with_no_nr_reading_falls_back_to_lte(self):
        s = nl.parse_qeng(self.NSA.replace('512,-92,', '512,-32768,'))
        self.assertEqual((s['technology'], s['rsrp']), ('LTE', -101.0))

    def test_operator(self):
        self.assertEqual(nl.parse_cops('\r\n+COPS: 0,0,"T-Mobile",13\r\n\r\nOK\r\n'), 'T-Mobile')
        self.assertIsNone(nl.parse_cops('\r\n+COPS: 0\r\n\r\nOK\r\n'))


class BillingCycle(unittest.TestCase):
    def at(self, day):
        return time.mktime(time.strptime(day + ' 12:00', '%Y-%m-%d %H:%M'))

    def test_cycle(self):
        self.assertEqual(nl.billing_cycle(self.at('2026-11-20'), 15), ('2026-11-15', '2026-12-15'))
        self.assertEqual(nl.billing_cycle(self.at('2026-11-10'), 15), ('2026-10-15', '2026-11-15'))
        self.assertEqual(nl.billing_cycle(self.at('2026-01-05'), 15), ('2025-12-15', '2026-01-15'))
        self.assertEqual(nl.billing_cycle(self.at('2026-02-28'), 31), ('2026-02-28', '2026-03-31'))
        self.assertEqual(nl.billing_cycle(self.at('2026-11-01'), 1), ('2026-11-01', '2026-12-01'))

    def test_left_with_plan_and_used_adjustment(self):
        with tempfile.TemporaryDirectory() as tmp:
            u = nl.Usage(os.path.join(tmp, 'usage.json'))
            u.d = {'2026-11': {'rx': 0, 'tx': 0, 'days': {'2026-11-10': {'rx': 400e6, 'tx': 100e6},
                                                         '2026-11-16': {'rx': 200e6, 'tx': 50e6}}}}
            now = self.at('2026-11-20')
            s = u.summary(now, {'plan_gb': 10, 'bill_day': 15})
            self.assertEqual((s['cycle_mb'], s['left_gb'], s['resets']), (250.0, 9.75, '2026-12-15'))
            s = u.summary(now, {'plan_gb': 10, 'bill_day': 15, 'adjust': {'cycle': '2026-11-15', 'mb': 2750}})
            self.assertEqual((s['cycle_mb'], s['left_gb'], s['plan_pct']), (3000.0, 7.0, 30.0))
            s = u.summary(now, {'plan_gb': 10, 'bill_day': 15, 'adjust': {'cycle': '2026-10-15', 'mb': 2750}})
            self.assertEqual(s['cycle_mb'], 250.0)              # last cycle's correction doesn't carry over
