import json
import tempfile
import unittest
from pathlib import Path
from event_journal import EventJournal

class JournalTests(unittest.TestCase):
    def test_restart_history_and_idempotency(self):
        with tempfile.TemporaryDirectory() as root:
            path=str(Path(root)/'events.db')
            journal=EventJournal(path)
            journal.append('first',dedup='one');journal.append('duplicate',dedup='one')
            journal.db.close()
            journal=EventJournal(path);journal.append('second')
            self.assertEqual([x['text'] for x in journal.rows()],['first','second'])
            self.assertEqual([x['id'] for x in journal.rows(1)],[2])
            journal.db.close()

    def test_only_fresh_observed_mode_and_arm_transitions(self):
        with tempfile.TemporaryDirectory() as root:
            journal=EventJournal(str(Path(root)/'events.db'))
            frame=dict(pixhawk_connected=True,telemetry_age=0,flight_mode='LOITER',armed=False)
            journal.observe(json.dumps(frame));journal.observe(json.dumps(frame))
            journal.observe(json.dumps(dict(frame,flight_mode='GUIDED',telemetry_age=4)))
            journal.observe(json.dumps(dict(frame,flight_mode='GUIDED',pixhawk_connected=False)))
            self.assertEqual(len(journal.rows()),1)
            journal.observe(json.dumps(dict(frame,flight_mode='GUIDED')))
            self.assertEqual(len(journal.rows()),2)
            self.assertIn('DISARMED',journal.rows()[-1]['text'])
            journal.db.close()
