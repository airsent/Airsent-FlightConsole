import unittest
from unittest.mock import patch
import presence_server as presence

class PresenceTests(unittest.TestCase):
    def tearDown(self): presence.clients.clear()
    def test_distinct_devices_and_roles(self):
        presence.clients.update({1:dict(clientId='a',role='operator',seen=100),2:dict(clientId='b',role='viewer',seen=100)})
        with patch('presence_server.time.time',return_value=101):
            self.assertEqual(presence.counts(),dict(type='presence_counts',total=2,operators=1,viewers=1))
    def test_multiple_tabs_count_once_and_latest_role_wins(self):
        presence.clients.update({1:dict(clientId='a',role='operator',seen=99),2:dict(clientId='a',role='viewer',seen=100)})
        with patch('presence_server.time.time',return_value=101):
            self.assertEqual(presence.counts()['total'],1)
            self.assertEqual(presence.counts()['viewers'],1)
        presence.clients.pop(2)
        with patch('presence_server.time.time',return_value=101):self.assertEqual(presence.counts()['total'],1)
    def test_stale_demo_and_unknown_not_counted(self):
        presence.clients.update({1:dict(role='viewer',seen=1),2:dict(role='demo',seen=100),3:dict(role='unknown',seen=100)})
        with patch('presence_server.time.time',return_value=101):self.assertEqual(presence.counts()['total'],0)
