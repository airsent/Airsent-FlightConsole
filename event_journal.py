"""Durable, append-only console audit history; never replays aircraft commands."""
import json
import os
import sqlite3
from datetime import datetime, timezone

class EventJournal:
    def __init__(self, path):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, timestamp TEXT NOT NULL, text TEXT NOT NULL, source TEXT NOT NULL, dedup TEXT UNIQUE)')
        self.db.commit()
        self.last_state = None
        self._seen = set()          # dedup keys already written (skips a DB round trip per telemetry frame)

    def append(self, text, source='relay', dedup=None):
        stamp = datetime.now(timezone.utc).isoformat(timespec='milliseconds')
        self.db.execute('INSERT OR IGNORE INTO events(timestamp,text,source,dedup) VALUES(?,?,?,?)', (stamp, str(text)[:1000], source, dedup))
        self.db.commit()

    def rows(self, after=0, limit=500):
        return [dict(id=r[0], timestamp=r[1], time=r[1][11:19], text=r[2], source=r[3]) for r in self.db.execute('SELECT id,timestamp,text,source FROM events WHERE id>? ORDER BY id LIMIT ?', (after, limit))]

    def _once(self, key, text, source):
        if key in self._seen:
            return
        if len(self._seen) > 5000:
            self._seen.clear()
        self._seen.add(key)
        self.append(text, source, key)

    def observe_mission(self, data):
        """Launch-sequencer events and newly raised Jetson faults ride on telemetry;
        record each one once so every console's event log shows them."""
        mission = data.get('mission')
        if isinstance(mission, dict):
            folder = ((mission.get('mission') or {}).get('folder') or '')[-80:]
            events = [e for e in (mission.get('events') or []) if isinstance(e, dict) and e.get('msg')]
            for e in sorted(events, key=lambda e: e.get('t') or 0):
                clock = (str(e.get('clock') or '') + ' ').lstrip()
                self._once(f"msn:{folder}:{e.get('t')}:{e.get('kind')}:{str(e.get('msg'))[:120]}",
                           f"MISSION {clock}{e.get('msg')}", 'launch sequencer · ' + str(e.get('who') or 'Jetson'))
        faults = data.get('faults')
        if isinstance(faults, dict):
            for f in faults.get('active') or []:
                if isinstance(f, dict) and f.get('message'):
                    self._once(f"fault:{f.get('id')}:{f.get('since')}",
                               f"FAULT {str(f.get('severity') or '').upper()}: {f.get('message')}", 'Jetson faults')

    def observe(self, raw):
        try:
            data = json.loads(raw)
            if '"mission"' in raw or '"faults"' in raw:
                self.observe_mission(data)
            if data.get('pixhawk_connected') is not True or float(data.get('telemetry_age', 99)) >= 3 or float(data.get('pixhawk_heartbeat_age', 0)) >= 3:
                return
            state = (data.get('flight_mode'), data.get('armed'))
            if not isinstance(state[0], str) or not isinstance(state[1], bool):
                return
            if state != self.last_state:
                self.append('AIRCRAFT OBSERVED: ' + state[0] + ' / ' + ('ARMED' if state[1] else 'DISARMED'), 'aircraft telemetry')
                self.last_state = state
        except (ValueError, TypeError, AttributeError):
            pass
