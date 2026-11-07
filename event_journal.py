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

    def append(self, text, source='relay', dedup=None):
        stamp = datetime.now(timezone.utc).isoformat(timespec='milliseconds')
        self.db.execute('INSERT OR IGNORE INTO events(timestamp,text,source,dedup) VALUES(?,?,?,?)', (stamp, str(text)[:1000], source, dedup))
        self.db.commit()

    def rows(self, after=0, limit=500):
        return [dict(id=r[0], timestamp=r[1], time=r[1][11:19], text=r[2], source=r[3]) for r in self.db.execute('SELECT id,timestamp,text,source FROM events WHERE id>? ORDER BY id LIMIT ?', (after, limit))]

    def observe(self, raw):
        try:
            data = json.loads(raw)
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
