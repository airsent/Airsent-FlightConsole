"""Mission upload / download / verify for ArduCopter over MAVLink.

The console's mission planner sends a plan (waypoints with lat/lon/alt/cmd/speed).
build_items() turns it into ArduPilot mission items and rejects anything unsafe.
VehicleMission.upload() runs the MAVLink mission protocol, then downloads the
mission back off the Pixhawk and checks every item matches what was sent. Only a
verified mission is reported as "on the drone".

The MAVLink reader thread owns recv(); it hands mission messages to observe().
"""
import hashlib
import json
import math
import queue
import threading
import time

# MAV_CMD numbers
NAV_WAYPOINT, NAV_LOITER_UNLIM, NAV_LOITER_TURNS, NAV_LOITER_TIME = 16, 17, 18, 19
NAV_RTL, NAV_LAND, NAV_TAKEOFF, DO_CHANGE_SPEED = 20, 21, 22, 178
# MAV_FRAME
FRAME_GLOBAL, FRAME_MISSION, FRAME_REL_ALT, FRAME_REL_ALT_INT = 0, 2, 3, 6
MAV_MISSION_ACCEPTED = 0
ACK_NAMES = {0: 'ACCEPTED', 1: 'ERROR', 2: 'UNSUPPORTED_FRAME', 3: 'UNSUPPORTED', 4: 'NO_SPACE',
             5: 'INVALID', 6: 'INVALID_PARAM1', 7: 'INVALID_PARAM2', 8: 'INVALID_PARAM3',
             9: 'INVALID_PARAM4', 10: 'INVALID_PARAM5_X', 11: 'INVALID_PARAM6_Y', 12: 'INVALID_PARAM7',
             13: 'INVALID_SEQUENCE', 14: 'DENIED', 15: 'OPERATION_CANCELLED'}

PLANNER_CMDS = {'WAYPOINT': NAV_WAYPOINT, 'LOITER_UNLIM': NAV_LOITER_UNLIM, 'LOITER_TURNS': NAV_LOITER_TURNS,
                'LOITER_TIME': NAV_LOITER_TIME, 'RTL': NAV_RTL, 'LAND': NAV_LAND, 'TAKEOFF': NAV_TAKEOFF}
CMD_NAMES = {v: k for k, v in PLANNER_CMDS.items()}
CMD_NAMES[DO_CHANGE_SPEED] = 'CHANGE_SPEED'

LIMITS = {'max_items': 200, 'max_alt': 120.0, 'min_alt': 2.0, 'max_speed': 15.0, 'min_speed': 1.0,
          'max_range': 2000.0, 'loiter_time': 10.0, 'loiter_turns': 1.0}


def _dist_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _item(command, frame=FRAME_REL_ALT, p1=0.0, p2=0.0, p3=0.0, p4=0.0, lat=0.0, lon=0.0, alt=0.0):
    return {'command': command, 'frame': frame, 'p1': float(p1), 'p2': float(p2), 'p3': float(p3), 'p4': float(p4),
            'x': int(round(lat * 1e7)), 'y': int(round(lon * 1e7)), 'z': float(alt)}


def plan_signature(waypoints):
    """Stable hash of the planner waypoints, so every console can tell whether the
    plan on screen is the one on the drone."""
    norm = []
    for w in waypoints:
        cmd = str(w.get('cmd') or 'WAYPOINT').upper()
        if cmd == 'TAKEOFF':
            continue                                  # take-off is in place; its map position is not stored
        if cmd == 'RTL':
            norm.append(['RTL'])                      # RTL has no position or altitude of its own
            continue
        row = [cmd, round(float(w.get('lat', 0)), 6), round(float(w.get('lon', 0)), 6)]
        if cmd != 'LAND':
            row += [round(float(w.get('alt', 0)), 1), round(float(w.get('speed') or 0), 1)]
        norm.append(row)
    return hashlib.sha1(json.dumps(norm).encode()).hexdigest()[:12]


def build_items(plan, home=None, limits=None):
    """Planner plan -> (items, warnings). Raises ValueError with a readable reason.

    items[0] is the home slot (ArduPilot overwrites it). A NAV_TAKEOFF is added
    when the plan does not start with one, so AUTO can start from the ground.
    DO_CHANGE_SPEED is inserted whenever the planned speed changes.
    """
    lim = dict(LIMITS, **(limits or {}))
    wps = (plan or {}).get('waypoints') or []
    if not wps:
        raise ValueError('The plan has no waypoints')
    if len(wps) > lim['max_items']:
        raise ValueError(f"Too many waypoints ({len(wps)} > {lim['max_items']})")
    warnings = []
    clean = []
    for i, w in enumerate(wps, 1):
        cmd = str(w.get('cmd') or 'WAYPOINT').upper()
        if cmd not in PLANNER_CMDS:
            raise ValueError(f'WP {i}: unsupported command {cmd}')
        try:
            lat, lon = float(w.get('lat')), float(w.get('lon'))
            alt = float(w.get('alt') if w.get('alt') is not None else 0)
            speed = float(w.get('speed')) if w.get('speed') not in (None, '') else None
            radius = float(w.get('radius') or 0)
        except (TypeError, ValueError):
            raise ValueError(f'WP {i}: invalid number')
        if not all(math.isfinite(v) for v in (lat, lon, alt)) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError(f'WP {i}: invalid position')
        if abs(lat) < 1e-6 and abs(lon) < 1e-6:
            raise ValueError(f'WP {i}: position is 0,0')
        if cmd not in ('RTL', 'LAND') and not (lim['min_alt'] <= alt <= lim['max_alt']):
            raise ValueError(f"WP {i}: altitude {alt:g} m outside {lim['min_alt']:g}-{lim['max_alt']:g} m")
        if speed is not None and not (lim['min_speed'] <= speed <= lim['max_speed']):
            raise ValueError(f"WP {i}: speed {speed:g} m/s outside {lim['min_speed']:g}-{lim['max_speed']:g} m/s")
        if home and cmd != 'RTL':
            d = _dist_m(home[0], home[1], lat, lon)
            if d > lim['max_range']:
                raise ValueError(f"WP {i}: {d:.0f} m from home (limit {lim['max_range']:.0f} m)")
        if clean and clean[-1]['cmd'] in ('RTL', 'LAND'):
            raise ValueError(f"WP {i}: nothing after {clean[-1]['cmd']} would run — remove it or move the {clean[-1]['cmd']} to the end")
        if cmd == 'LOITER_UNLIM' and i != len(wps):
            warnings.append(f'WP {i}: LOITER UNLIM never finishes — the waypoints after it will not run')
        clean.append({'cmd': cmd, 'lat': lat, 'lon': lon, 'alt': alt, 'speed': speed, 'radius': radius,
                      'time': w.get('time'), 'turns': w.get('turns')})
    if not home:
        warnings.append('Home position unknown — distance from home was not checked')
    if clean[-1]['cmd'] not in ('RTL', 'LAND'):
        warnings.append('The mission does not end with RTL or LAND — the drone will hover at the last waypoint')

    first = clean[0]
    hlat, hlon = (home[0], home[1]) if home else (first['lat'], first['lon'])
    items = [_item(NAV_WAYPOINT, FRAME_GLOBAL, lat=hlat, lon=hlon, alt=0)]          # seq 0: home slot
    if first['cmd'] != 'TAKEOFF':
        first_alt = next((c['alt'] for c in clean if c['cmd'] not in ('RTL', 'LAND')), 5.0)
        items.append(_item(NAV_TAKEOFF, alt=max(lim['min_alt'], first_alt)))         # take off in place
    cur_speed = None
    for c in clean:
        if c['speed'] is not None and c['speed'] != cur_speed and c['cmd'] not in ('RTL', 'LAND', 'TAKEOFF'):
            items.append(_item(DO_CHANGE_SPEED, FRAME_MISSION, p1=1, p2=c['speed'], p3=-1))
            cur_speed = c['speed']
        cmd = c['cmd']
        if cmd == 'WAYPOINT':
            items.append(_item(NAV_WAYPOINT, lat=c['lat'], lon=c['lon'], alt=c['alt']))
        elif cmd == 'TAKEOFF':
            items.append(_item(NAV_TAKEOFF, alt=c['alt']))
        elif cmd == 'LOITER_UNLIM':
            items.append(_item(NAV_LOITER_UNLIM, p3=c['radius'], lat=c['lat'], lon=c['lon'], alt=c['alt']))
        elif cmd == 'LOITER_TURNS':
            turns = float(c['turns'] or lim['loiter_turns'])
            items.append(_item(NAV_LOITER_TURNS, p1=turns, p3=c['radius'], lat=c['lat'], lon=c['lon'], alt=c['alt']))
        elif cmd == 'LOITER_TIME':
            secs = float(c['time'] or lim['loiter_time'])
            items.append(_item(NAV_LOITER_TIME, p1=secs, lat=c['lat'], lon=c['lon'], alt=c['alt']))
        elif cmd == 'LAND':
            items.append(_item(NAV_LAND, lat=c['lat'], lon=c['lon'], alt=0))
        elif cmd == 'RTL':
            items.append(_item(NAV_RTL))
    return items, warnings


def items_match(sent, got):
    """Compare what was uploaded with what the Pixhawk returned. seq 0 (home) is skipped."""
    if len(sent) != len(got):
        return f'Pixhawk holds {len(got)} items, expected {len(sent)}'
    rel = {FRAME_REL_ALT, FRAME_REL_ALT_INT}
    for seq in range(1, len(sent)):
        a, b = sent[seq], got[seq]
        if a['command'] != b['command']:
            return f'Item {seq}: command {b["command"]} on the Pixhawk, sent {a["command"]}'
        if not (a['frame'] == b['frame'] or (a['frame'] in rel and b['frame'] in rel)):
            return f'Item {seq}: frame {b["frame"]} on the Pixhawk, sent {a["frame"]}'
        if abs(a['x'] - b['x']) > 1 or abs(a['y'] - b['y']) > 1:
            return f'Item {seq}: position differs on the Pixhawk'
        if abs(a['z'] - b['z']) > 0.05:
            return f'Item {seq}: altitude {b["z"]:.2f} m on the Pixhawk, sent {a["z"]:.2f} m'
        for p in ('p1', 'p2', 'p3', 'p4'):
            if abs(a[p] - b[p]) > 0.01 and not (math.isnan(a[p]) and math.isnan(b[p])):
                return f'Item {seq}: {p} {b[p]:g} on the Pixhawk, sent {a[p]:g}'
    return None


def items_to_plan(items, home=None):
    """Pixhawk items -> planner waypoints (for Download)."""
    wps, speed, last = [], None, home
    for it in items[1:]:
        cmd, lat, lon = it['command'], it['x'] / 1e7, it['y'] / 1e7
        if cmd == DO_CHANGE_SPEED:
            speed = it['p2'] if it['p2'] > 0 else speed
            continue
        name = CMD_NAMES.get(cmd)
        if name is None:
            continue
        if cmd == NAV_TAKEOFF and abs(lat) < 1e-6 and abs(lon) < 1e-6:
            continue                    # take-off-in-place: added automatically on upload
        if cmd == NAV_RTL or (abs(lat) < 1e-6 and abs(lon) < 1e-6):
            if not last:
                continue
            lat, lon = last
        wp = {'cmd': name, 'lat': lat, 'lon': lon, 'alt': round(it['z'], 2), 'radius': it['p3'] or 5}
        if speed:
            wp['speed'] = speed
        if cmd == NAV_LOITER_TIME:
            wp['time'] = it['p1']
        if cmd == NAV_LOITER_TURNS:
            wp['turns'] = it['p1']
        wps.append(wp)
        last = (lat, lon)
    return wps


class MissionBusy(RuntimeError):
    pass


class VehicleMission:
    WANT = ('MISSION_REQUEST', 'MISSION_REQUEST_INT', 'MISSION_ACK', 'MISSION_COUNT', 'MISSION_ITEM_INT', 'MISSION_ITEM')

    def __init__(self, clock=time.time):
        self.clock = clock
        self._q = queue.Queue()
        self._active = False
        self._lock = threading.Lock()
        self.state = {'known': False}

    # called from the MAVLink reader thread
    def observe(self, msg):
        if self._active and msg.get_type() in self.WANT:
            self._q.put(msg)

    def reset(self):
        """Pixhawk reconnected: what is on board is unknown again."""
        self.state = {'known': False}

    def snapshot(self):
        return dict(self.state)

    # ── protocol ────────────────────────────────────────────────────────────
    def _begin(self):
        if not self._lock.acquire(blocking=False):
            raise MissionBusy('Another mission transfer is in progress')
        while not self._q.empty():
            self._q.get_nowait()
        self._active = True

    def _end(self):
        self._active = False
        self._lock.release()

    def _wait(self, types, deadline, seq=None):
        while True:
            left = deadline - self.clock()
            if left <= 0:
                return None
            try:
                msg = self._q.get(timeout=min(left, 0.5))
            except queue.Empty:
                continue
            t = msg.get_type()
            if t == 'MISSION_ACK' and 'MISSION_ACK' not in types:
                if getattr(msg, 'mission_type', 0) == 0 and msg.type != MAV_MISSION_ACCEPTED:
                    raise RuntimeError('Pixhawk rejected the mission: ' + ACK_NAMES.get(msg.type, str(msg.type)))
                continue
            if t in types and (seq is None or getattr(msg, 'seq', None) == seq):
                return msg

    def upload(self, mav, items, step_timeout=1.5, total_timeout=30.0):
        self._begin()
        try:
            ts, tc = mav.target_system, mav.target_component
            deadline = self.clock() + total_timeout
            mav.mav.mission_count_send(ts, tc, len(items), 0)
            sent, tries, highest = set(), 0, -1
            while True:
                msg = self._wait(('MISSION_REQUEST', 'MISSION_REQUEST_INT', 'MISSION_ACK'),
                                 min(deadline, self.clock() + step_timeout))
                if msg is None:
                    if self.clock() >= deadline:
                        raise RuntimeError(f'Upload timed out after {len(sent)}/{len(items)} items')
                    tries += 1
                    if tries > 3:
                        raise RuntimeError(f'Pixhawk stopped responding after {len(sent)}/{len(items)} items')
                    if highest < 0:
                        mav.mav.mission_count_send(ts, tc, len(items), 0)   # count was lost
                    continue
                tries = 0
                if msg.get_type() == 'MISSION_ACK':
                    if getattr(msg, 'mission_type', 0) != 0:
                        continue
                    if msg.type != MAV_MISSION_ACCEPTED:
                        raise RuntimeError('Pixhawk rejected the mission: ' + ACK_NAMES.get(msg.type, str(msg.type)))
                    if len(sent) < len(items):
                        raise RuntimeError(f'Pixhawk accepted after only {len(sent)}/{len(items)} items')
                    return
                seq = msg.seq
                if not 0 <= seq < len(items):
                    raise RuntimeError(f'Pixhawk asked for item {seq} of {len(items)}')
                it = items[seq]
                mav.mav.mission_item_int_send(ts, tc, seq, it['frame'], it['command'], 1 if seq == 0 else 0, 1,
                                              it['p1'], it['p2'], it['p3'], it['p4'], it['x'], it['y'], it['z'], 0)
                sent.add(seq)
                highest = max(highest, seq)
        finally:
            self._end()

    def download(self, mav, step_timeout=1.5, total_timeout=30.0):
        self._begin()
        try:
            ts, tc = mav.target_system, mav.target_component
            deadline = self.clock() + total_timeout
            count = None
            for _ in range(3):
                mav.mav.mission_request_list_send(ts, tc, 0)
                msg = self._wait(('MISSION_COUNT',), min(deadline, self.clock() + step_timeout))
                if msg is not None:
                    count = msg.count
                    break
            if count is None:
                raise RuntimeError('Pixhawk did not report its mission')
            items = []
            for seq in range(count):
                msg = None
                for _ in range(3):
                    mav.mav.mission_request_int_send(ts, tc, seq, 0)
                    msg = self._wait(('MISSION_ITEM_INT',), min(deadline, self.clock() + step_timeout), seq=seq)
                    if msg is not None:
                        break
                if msg is None:
                    raise RuntimeError(f'Pixhawk did not send item {seq} of {count}')
                items.append({'command': msg.command, 'frame': msg.frame, 'p1': msg.param1, 'p2': msg.param2,
                              'p3': msg.param3, 'p4': msg.param4, 'x': msg.x, 'y': msg.y, 'z': msg.z})
            mav.mav.mission_ack_send(ts, tc, MAV_MISSION_ACCEPTED, 0)
            return items
        finally:
            self._end()

    # ── console actions ─────────────────────────────────────────────────────
    def upload_plan(self, mav, plan, home=None, who='Operator', limits=None):
        items, warnings = build_items(plan, home, limits)
        t0 = self.clock()
        self.upload(mav, items)
        got = self.download(mav)
        problem = items_match(items, got)
        sig = plan_signature(plan.get('waypoints') or [])
        if problem:
            self.state = {'known': True, 'verified': False, 'count': len(got), 'signature': None,
                          'name': plan.get('name') or '', 'by': who, 'at': self.clock(), 'problem': problem}
            raise RuntimeError('Uploaded, but the read-back does not match: ' + problem)
        nav = sum(1 for it in items[1:] if it['command'] != DO_CHANGE_SPEED)
        self.state = {'known': True, 'verified': True, 'count': len(items), 'nav_count': nav, 'signature': sig,
                      'name': plan.get('name') or '', 'by': who, 'at': self.clock()}
        return {'items': len(items), 'nav_items': nav, 'warnings': warnings, 'signature': sig,
                'seconds': round(self.clock() - t0, 1)}

    def download_plan(self, mav, home=None, who='Operator'):
        items = self.download(mav)
        wps = items_to_plan(items, home)
        known_sig = self.state.get('signature') if self.state.get('verified') else None
        sig = plan_signature(wps) if wps else None
        self.state = {'known': True, 'verified': bool(known_sig and known_sig == sig), 'count': len(items),
                      'nav_count': len(wps), 'signature': sig, 'name': self.state.get('name', ''),
                      'by': self.state.get('by', who), 'at': self.state.get('at', self.clock())}
        return {'waypoints': wps, 'items': len(items), 'signature': sig}
