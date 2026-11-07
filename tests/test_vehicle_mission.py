import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import vehicle_mission as vm  # noqa: E402

HOME = (33.4213, -111.9327)


class Msg:
    def __init__(self, t, **kw):
        self._t = t
        self.__dict__.update(kw)

    def get_type(self):
        return self._t


class FakeArduPilot:
    """Answers the MAVLink mission protocol the way ArduCopter does."""

    def __init__(self, link, drop_first_count=False, reject=None, corrupt_alt=None, stall_after=None):
        self.link, self.drop_first_count, self.reject = link, drop_first_count, reject
        self.corrupt_alt, self.stall_after = corrupt_alt, stall_after
        self.stored, self.incoming, self.count = [], [], 0
        self.target_system, self.target_component = 1, 1
        self.mav = self

    def _push(self, t, **kw):
        self.link.observe(Msg(t, **kw))

    # upload
    def mission_count_send(self, ts, tc, count, mission_type=0):
        if self.drop_first_count:
            self.drop_first_count = False
            return
        if self.reject:
            self._push('MISSION_ACK', type=self.reject, mission_type=0)
            return
        self.count, self.incoming = count, []
        self._push('MISSION_REQUEST_INT', seq=0, mission_type=0)

    def mission_item_int_send(self, ts, tc, seq, frame, command, current, autocontinue, p1, p2, p3, p4, x, y, z, mt=0):
        self.incoming.append({'command': command, 'frame': frame, 'p1': p1, 'p2': p2, 'p3': p3, 'p4': p4, 'x': x, 'y': y, 'z': z})
        if self.stall_after is not None and seq >= self.stall_after:
            return
        if seq + 1 < self.count:
            self._push('MISSION_REQUEST_INT', seq=seq + 1, mission_type=0)
        else:
            self.stored = [dict(i) for i in self.incoming]
            self.stored[0].update(x=int(HOME[0] * 1e7), y=int(HOME[1] * 1e7), z=360.0)   # home overwritten
            if self.corrupt_alt is not None:
                self.stored[self.corrupt_alt]['z'] += 5
            self._push('MISSION_ACK', type=0, mission_type=0)

    # download
    def mission_request_list_send(self, ts, tc, mission_type=0):
        self._push('MISSION_COUNT', count=len(self.stored), mission_type=0)

    def mission_request_int_send(self, ts, tc, seq, mission_type=0):
        i = self.stored[seq]
        self._push('MISSION_ITEM_INT', seq=seq, command=i['command'], frame=i['frame'], param1=i['p1'], param2=i['p2'],
                   param3=i['p3'], param4=i['p4'], x=i['x'], y=i['y'], z=i['z'], mission_type=0)

    def mission_ack_send(self, *a):
        pass


def plan(*wps):
    return {'name': 'Test', 'waypoints': [dict(w) for w in wps]}


WP1 = {'cmd': 'WAYPOINT', 'lat': 33.4220, 'lon': -111.9330, 'alt': 20, 'speed': 5}
WP2 = {'cmd': 'WAYPOINT', 'lat': 33.4225, 'lon': -111.9335, 'alt': 25, 'speed': 5}
WP3 = {'cmd': 'LOITER_TIME', 'lat': 33.4228, 'lon': -111.9332, 'alt': 25, 'speed': 8, 'time': 15}
RTL = {'cmd': 'RTL', 'lat': 33.4214, 'lon': -111.9328, 'alt': 30, 'speed': 8}


class BuildItems(unittest.TestCase):
    def test_adds_takeoff_speed_and_keeps_order(self):
        items, warnings = vm.build_items(plan(WP1, WP2, WP3, RTL), HOME)
        cmds = [i['command'] for i in items]
        self.assertEqual(cmds, [vm.NAV_WAYPOINT, vm.NAV_TAKEOFF, vm.DO_CHANGE_SPEED, vm.NAV_WAYPOINT, vm.NAV_WAYPOINT,
                                vm.DO_CHANGE_SPEED, vm.NAV_LOITER_TIME, vm.NAV_RTL])
        self.assertEqual(items[1]['z'], 20)             # take off to the first waypoint altitude
        self.assertEqual(items[6]['p1'], 15)            # loiter time
        self.assertEqual(items[3]['x'], 334220000)
        self.assertEqual(warnings, [])

    def test_rejects_unsafe_plans(self):
        bad = [
            (plan(), 'no waypoints'),
            (plan(dict(WP1, alt=500)), 'altitude'),
            (plan(dict(WP1, alt=0.5)), 'altitude'),
            (plan(dict(WP1, speed=40)), 'speed'),
            (plan(dict(WP1, lat=0, lon=0)), '0,0'),
            (plan(dict(WP1, lat=34.0)), 'from home'),
            (plan(WP1, RTL, WP2), 'nothing after RTL'),
            (plan(dict(WP1, cmd='DANCE')), 'unsupported'),
            (plan(dict(WP1, alt='abc')), 'invalid number'),
        ]
        for p, why in bad:
            with self.assertRaises(ValueError, msg=why) as ctx:
                vm.build_items(p, HOME)
            self.assertIn(why.split()[0].lower(), str(ctx.exception).lower())

    def test_warnings(self):
        _, w = vm.build_items(plan(WP1, WP2), None)
        self.assertTrue(any('Home position unknown' in x for x in w))
        self.assertTrue(any('does not end with RTL or LAND' in x for x in w))


class Transfer(unittest.TestCase):
    def setUp(self):
        self.link = vm.VehicleMission()

    def test_upload_verify_download_roundtrip(self):
        fc = FakeArduPilot(self.link)
        p = plan(WP1, WP2, WP3, RTL)
        r = self.link.upload_plan(fc, p, HOME, who='Aarav')
        self.assertEqual(r['items'], 8)
        self.assertEqual(r['nav_items'], 5)
        st = self.link.snapshot()
        self.assertTrue(st['verified'])
        self.assertEqual(st['signature'], vm.plan_signature(p['waypoints']))
        d = self.link.download_plan(fc, HOME)
        self.assertEqual([w['cmd'] for w in d['waypoints']], ['WAYPOINT', 'WAYPOINT', 'LOITER_TIME', 'RTL'])
        self.assertEqual(d['signature'], vm.plan_signature(p['waypoints']))   # same plan -> same signature
        self.assertTrue(self.link.snapshot()['verified'])

    def test_lost_count_is_resent(self):
        fc = FakeArduPilot(self.link, drop_first_count=True)
        self.link.upload_plan(fc, plan(WP1, RTL), HOME)
        self.assertTrue(self.link.snapshot()['verified'])

    def test_rejection_is_reported(self):
        fc = FakeArduPilot(self.link, reject=4)
        with self.assertRaisesRegex(RuntimeError, 'NO_SPACE'):
            self.link.upload_plan(fc, plan(WP1, RTL), HOME)
        self.assertFalse(self.link.snapshot()['known'])

    def test_readback_mismatch_is_not_verified(self):
        fc = FakeArduPilot(self.link, corrupt_alt=3)
        with self.assertRaisesRegex(RuntimeError, 'read-back does not match'):
            self.link.upload_plan(fc, plan(WP1, WP2, RTL), HOME)
        self.assertFalse(self.link.snapshot()['verified'])

    def test_stalled_pixhawk_times_out(self):
        fc = FakeArduPilot(self.link, stall_after=1)
        with self.assertRaisesRegex(RuntimeError, 'stopped responding|timed out'):
            self.link.upload(fc, vm.build_items(plan(WP1, WP2, RTL), HOME)[0], step_timeout=0.05, total_timeout=2)

    def test_one_transfer_at_a_time(self):
        self.link._begin()
        try:
            with self.assertRaises(vm.MissionBusy):
                self.link.upload(FakeArduPilot(self.link), [])
        finally:
            self.link._end()

    def test_ignores_messages_when_idle(self):
        self.link.observe(Msg('MISSION_ACK', type=0, mission_type=0))
        self.assertTrue(self.link._q.empty())


if __name__ == '__main__':
    unittest.main()
