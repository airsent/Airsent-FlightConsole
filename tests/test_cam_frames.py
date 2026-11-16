"""A camera that is plugged in but producing nothing used to read as nominal everywhere:
front_cam_ok() is satisfied by the USB device being present, and the streaming loop waited
for frames in silence. Both now report the truth."""
import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
_argv, sys.argv = sys.argv, ['airsent_server.py', '--sim']
import airsent_server as srv  # noqa: E402
sys.argv = _argv


class FrameFreshness(unittest.TestCase):
    def setUp(self):
        srv.front_frame_at[0] = 0.0
        srv.downcam_frame_at[0] = 0.0

    def test_no_frames_ever(self):
        self.assertFalse(srv.cam_frames_fresh('front'))
        self.assertFalse(srv.cam_frames_fresh('down'))

    def test_recent_frames_are_fresh_old_ones_are_not(self):
        now = time.time()
        srv.front_frame_at[0] = now - 1
        srv.downcam_frame_at[0] = now - 30
        self.assertTrue(srv.cam_frames_fresh('front', now))
        self.assertFalse(srv.cam_frames_fresh('down', now))

    def test_a_present_but_silent_camera_is_visible_in_telemetry(self):
        # the USB device is there, so front_cam_ok() is happy...
        setattr(srv, 'cam_device_available', True)
        self.addCleanup(setattr, srv, 'cam_device_available', False)
        self.assertTrue(srv.front_cam_active())
        # ...but no pictures have arrived, and the console is told
        self.assertFalse(srv.netlink_payload()['video']['front_frames'])


class IdleReason(unittest.TestCase):
    def test_repeats_are_suppressed_but_a_change_speaks_at_once(self):
        said, state = [], [0.0, '']
        out = sys.stdout

        class Cap:
            def write(self, s):
                if s.strip():
                    said.append(s.strip())

            def flush(self):
                pass

        sys.stdout = Cap()
        try:
            srv._say_idle(state, 'SRT', 'no frames')
            srv._say_idle(state, 'SRT', 'no frames')
            srv._say_idle(state, 'SRT', 'no frames')
            srv._say_idle(state, 'SRT', 'nobody watching')
        finally:
            sys.stdout = out
        self.assertEqual(said, ['[SRT] no frames', '[SRT] nobody watching'])


if __name__ == '__main__':
    unittest.main()
