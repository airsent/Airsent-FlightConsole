"""Server wiring: video follows the ASC session; the .bin download is queued after it closes."""
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tests'))
_argv = sys.argv
sys.argv = ['airsent_server.py', '--sim']
import airsent_server as srv  # noqa: E402
sys.argv = _argv
from test_recording import FakeArduPilot, IDR  # noqa: E402


class Wiring(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.folder = os.path.join(self.tmp, '2026-11-07_1200_test')
        os.makedirs(self.folder)
        self.saved_root = srv.MISSION_ROOT
        srv._rec_state.update(folder=None, bin_at=None, bin_folder=None)
        srv.telem['armed'] = False
        srv.asc_disk[0] = 200.0

    def tearDown(self):
        srv.asc_engine.session = None
        srv.recording_tick(0)
        srv._rec_state.update(folder=None, bin_at=None, bin_folder=None)
        with srv.mav_lock:
            srv.mav_conn = None
        if srv.bin_log.thread:
            srv.bin_log.thread.join(10)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_session_drives_video_then_bin(self):
        fake = FakeArduPilot(srv.bin_log, {7: os.urandom(20_000)})
        with srv.mav_lock:
            srv.mav_conn = fake
        srv.asc_engine.session = {'folder': self.folder}
        srv.recording_tick(100.0)
        vdir = os.path.join(self.folder, 'video')
        self.assertEqual(srv.front_rec.folder, vdir)
        self.assertEqual(srv.down_rec.folder, vdir)
        srv.front_rec.packet(IDR)
        self.assertTrue(srv.recording_payload()['video']['front']['recording'])
        srv.asc_engine.session = None
        srv.recording_tick(101.0)
        self.assertIsNone(srv.front_rec.folder)
        self.assertIsNone(srv.bin_log.thread if srv.bin_log.thread and srv.bin_log.thread.is_alive() else None)
        srv.recording_tick(101.0 + srv.BIN_DELAY_S + 0.1)
        srv.bin_log.thread.join(10)
        self.assertTrue(os.path.exists(os.path.join(self.folder, 'pixhawk.bin')))
        self.assertEqual(srv.recording_payload()['bin_log']['state'], 'done')

    def test_rearm_before_delay_skips_download(self):
        with srv.mav_lock:
            srv.mav_conn = FakeArduPilot(srv.bin_log, {1: b'x' * 900})
        srv.asc_engine.session = {'folder': self.folder}
        srv.recording_tick(10.0)
        srv.asc_engine.session = None
        srv.recording_tick(11.0)
        srv.telem['armed'] = True
        before = srv.bin_log.thread
        srv.recording_tick(30.0)
        self.assertIs(srv.bin_log.thread, before)

    def test_low_disk_no_video(self):
        srv.asc_disk[0] = 1.0
        srv.asc_engine.session = {'folder': self.folder}
        srv.recording_tick(5.0)
        self.assertIsNone(srv.front_rec.folder)

    def test_bin_download_command(self):
        srv.MISSION_ROOT = self.tmp
        try:
            r = srv.process_command(json.dumps({'action': 'bin_download', 'folder': os.path.basename(self.folder), 'request_id': 'x'}))
        finally:
            srv.MISSION_ROOT = self.saved_root
        self.assertFalse(r['ok'])                       # no Pixhawk in this test
        self.assertIn('bin_download', srv.SLOW_ACTIONS)


if __name__ == '__main__':
    unittest.main()
