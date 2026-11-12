"""Front camera (OAK-D) reported connected while it is running, not only before DepthAI boots it."""
import os
import sys
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
_argv = sys.argv
sys.argv = ['airsent_server.py', '--sim']
import airsent_server as srv  # noqa: E402
sys.argv = _argv


def lsusb(text):
    return mock.patch.object(srv.subprocess, 'run', return_value=mock.Mock(stdout=text))


class FrontCamStatus(unittest.TestCase):
    def test_unbooted_oak_d(self):
        with lsusb('Bus 002 Device 004: ID 03e7:2485 Intel Movidius MyriadX\n'):
            self.assertTrue(srv.check_oak_d_connected())

    def test_running_oak_d(self):   # product ID after DepthAI boots it — was reported OFFLINE
        with lsusb('Bus 002 Device 005: ID 03e7:f63b Intel Myriad VPU [Movidius Neural Compute Stick]\n'):
            self.assertTrue(srv.check_oak_d_connected())

    def test_no_camera(self):
        with lsusb('Bus 001 Device 002: ID 2c7c:0801 Quectel Wireless Solutions RM520N\n'):
            self.assertFalse(srv.check_oak_d_connected())

    def test_recent_frames_mean_connected(self):
        srv.cam_device_available = False
        srv.front_frame_at[0] = time.time()
        self.assertTrue(srv.front_cam_active())
        srv.front_frame_at[0] = time.time() - 30
        self.assertFalse(srv.front_cam_active())


if __name__ == '__main__':
    unittest.main()
