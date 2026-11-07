"""Pixhawk .bin download (fake ArduPilot, lossy link) and the mission video recorders."""
import json
import os
import random
import shutil
import sys
import tempfile
import threading
import time
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pixhawk_log  # noqa: E402
import video_record  # noqa: E402


class Msg:
    def __init__(self, t, **kw):
        self._t = t
        self.__dict__.update(kw)

    def get_type(self):
        return self._t


class FakeArduPilot:
    """Answers the MAVLink log protocol. Drops a share of LOG_DATA like a bad serial link."""
    def __init__(self, dl, logs, drop=0.0, stop_after=None):
        self.dl, self.logs, self.drop, self.stop_after = dl, logs, drop, stop_after
        self.target_system = self.target_component = 1
        self.mav = self
        self.sent = 0
        self.ended = False
        self.data_requests = []
        self.rng = random.Random(4)

    def _later(self, fn):
        threading.Thread(target=fn, daemon=True).start()

    def log_request_list_send(self, ts, tc, start, end):
        ids = sorted(self.logs)
        def go():
            for i in ids:
                self.dl.observe(Msg('LOG_ENTRY', id=i, num_logs=len(ids), last_log_num=ids[-1], time_utc=1700000000 + i,
                                    size=len(self.logs[i])))
        self._later(go)

    def log_request_data_send(self, ts, tc, log_id, ofs, count):
        self.data_requests.append((ofs, count))
        data = self.logs[log_id]
        end = min(len(data), ofs + count)
        def go():
            o = ofs
            while o < end:
                if self.stop_after is not None and self.sent >= self.stop_after:
                    return
                chunk = data[o:o + 90]
                self.sent += 1
                if self.rng.random() >= self.drop:
                    self.dl.observe(Msg('LOG_DATA', id=log_id, ofs=o, count=len(chunk), data=list(chunk) + [0] * (90 - len(chunk))))
                o += 90
        self._later(go)

    def log_request_end_send(self, ts, tc):
        self.ended = True


class BinLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_newest_log_downloaded_through_drops(self):
        dl = pixhawk_log.LogDownloader()
        old, new = os.urandom(5000), os.urandom(250_003)      # not a multiple of 90
        fake = FakeArduPilot(dl, {3: old, 4: new, 5: b''}, drop=0.08)
        self.assertTrue(dl.download(lambda: fake, self.tmp, lambda: False))
        with open(os.path.join(self.tmp, 'pixhawk.bin'), 'rb') as f:
            self.assertEqual(f.read(), new)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, 'pixhawk.bin.part')))
        self.assertTrue(fake.ended)
        self.assertGreater(len(fake.data_requests), 1)           # gaps were asked for again
        meta = json.load(open(os.path.join(self.tmp, 'pixhawk_bin.json')))
        self.assertEqual(meta['log_id'], 4)
        self.assertEqual(dl.snapshot()['state'], 'done')

    def test_arming_aborts_and_keeps_partial(self):
        dl = pixhawk_log.LogDownloader()
        fake = FakeArduPilot(dl, {1: os.urandom(400_000)}, stop_after=300)
        armed = {'v': False}
        threading.Timer(0.3, lambda: armed.update(v=True)).start()
        self.assertFalse(dl.download(lambda: fake, self.tmp, lambda: armed['v']))
        self.assertIn('armed', dl.snapshot()['msg'])
        self.assertTrue(os.path.exists(os.path.join(self.tmp, 'pixhawk.bin.part')))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, 'pixhawk.bin')))
        self.assertTrue(fake.ended)

    def test_no_logs_and_not_connected(self):
        dl = pixhawk_log.LogDownloader()
        fake = FakeArduPilot(dl, {})
        fake.log_request_list_send = lambda *a: dl.observe(Msg('LOG_ENTRY', id=0, num_logs=0, last_log_num=0, time_utc=0, size=0))
        self.assertFalse(dl.download(lambda: fake, self.tmp, lambda: False))
        self.assertIn('no logs', dl.snapshot()['msg'])
        self.assertFalse(dl.download(lambda: None, self.tmp, lambda: False))
        self.assertIn('not connected', dl.snapshot()['msg'])

    def test_stall_gives_up(self):
        dl = pixhawk_log.LogDownloader()
        fake = FakeArduPilot(dl, {2: os.urandom(50_000)}, stop_after=100)
        fake.log_request_data_send = (lambda orig: lambda *a: orig(*a) if not fake.data_requests else fake.data_requests.append(a))(fake.log_request_data_send)
        self.assertFalse(dl.download(lambda: fake, self.tmp, lambda: False, stall_timeout=1.0))
        self.assertIn('stopped sending', dl.snapshot()['msg'])

    def test_explicit_log_id_and_busy(self):
        dl = pixhawk_log.LogDownloader()
        a, b = os.urandom(9000), os.urandom(9000)
        fake = FakeArduPilot(dl, {1: a, 2: b})
        self.assertTrue(dl.download(lambda: fake, self.tmp, lambda: False, log_id=1))
        self.assertEqual(open(os.path.join(self.tmp, 'pixhawk.bin'), 'rb').read(), a)
        ok, _ = dl.start(lambda: fake, self.tmp, lambda: False)
        self.assertTrue(ok)
        ok2, msg = dl.start(lambda: fake, self.tmp, lambda: False)
        self.assertFalse(ok2)
        dl.thread.join(10)


IDR = b'\x00\x00\x00\x01\x40\x01\x0c\x01' + b'\x00' * 40        # VPS ... (keyframe packet)
P = b'\x00\x00\x00\x01\x02\x01' + b'\x11' * 40                  # trailing picture


class VideoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        video_record.finalizer.join(30)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_front_waits_for_keyframe_and_stops(self):
        r = video_record.FrontRecorder()
        r.packet(IDR)                                     # not recording: ignored
        vdir = os.path.join(self.tmp, 'video')
        r.set_folder(vdir)
        r.packet(P)                                       # mid-GOP: skipped
        self.assertFalse(r.recording)
        r.packet(IDR); r.packet(P); r.packet(P)
        self.assertTrue(r.recording)
        self.assertEqual(r.frames, 3)
        r.f.flush()
        raw = open(os.path.join(vdir, 'front.h265'), 'rb').read() if os.path.exists(os.path.join(vdir, 'front.h265')) else None
        self.assertEqual(raw, IDR + P + P)
        r.set_folder(None)
        self.assertFalse(r.recording)
        r.packet(IDR)
        video_record.finalizer.join(30)
        meta = json.load(open(os.path.join(vdir, 'video.json')))
        self.assertEqual(meta['front']['frames'], 3)
        self.assertTrue(os.path.exists(os.path.join(self.tmp, meta['front']['file'])))   # mp4 or the raw file
        self.assertEqual(r.snapshot()['mb'], round(len(IDR + P + P) / 1e6, 1))

    def test_down_records_frames_and_packs(self):
        try:
            import cv2  # noqa: F401
        except ImportError:
            self.skipTest('opencv not installed')
        frame = {'f': None}
        r = video_record.DownRecorder(lambda: frame['f'], fps=10)
        vdir = os.path.join(self.tmp, 'video')
        self.assertFalse(r.step())                          # not recording
        r.set_folder(vdir)
        self.assertFalse(r.step())                          # no camera frame yet
        for i in range(12):
            img = np.zeros((720, 1280, 3), np.uint8)
            img[:, :, 1] = i * 20
            frame['f'] = img
            self.assertTrue(r.step())
        self.assertEqual(r.frames, 12)
        r.set_folder(None)
        video_record.finalizer.join(60)
        meta = json.load(open(os.path.join(vdir, 'video.json')))
        self.assertEqual(meta['down']['frames'], 12)
        out = os.path.join(self.tmp, meta['down']['file'])
        self.assertTrue(os.path.getsize(out) > 0)
        if shutil.which('ffmpeg'):
            self.assertEqual(meta['down']['file'], 'video/down.mp4')
            self.assertFalse(os.path.exists(os.path.join(vdir, 'down.mjpeg')))

    def test_write_error_stops_cleanly(self):
        r = video_record.FrontRecorder()
        blocker = os.path.join(self.tmp, 'file')
        open(blocker, 'w').close()
        r.set_folder(os.path.join(blocker, 'video'))      # cannot create a folder under a file
        r.packet(IDR)
        self.assertFalse(r.recording)
        self.assertTrue(r.snapshot()['error'])


if __name__ == '__main__':
    unittest.main()
