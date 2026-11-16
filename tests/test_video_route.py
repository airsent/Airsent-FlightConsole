"""The video push goes to srt://HOST:8890 (UDP). The reachability gate used to open a TCP
connection to port 1935 (RTMP) — a different protocol on a different port — so it could
block streaming while SRT was fine, or wave it through while SRT was blocked."""
import os
import socket
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
_argv, sys.argv = sys.argv, ['airsent_server.py', '--sim']
import airsent_server as srv  # noqa: E402
sys.argv = _argv


class VideoRouteGate(unittest.TestCase):
    def test_gate_targets_the_srt_endpoint_not_rtmp(self):
        with mock.patch.object(srv.socket, 'socket') as sock:
            self.assertTrue(srv.vps_route_ok())
        sock.assert_called_once_with(socket.AF_INET, socket.SOCK_DGRAM)
        sock.return_value.connect.assert_called_once_with((srv.RTMP_HOST, srv.SRT_PORT))
        self.assertEqual(srv.SRT_PORT, 8890)
        self.assertNotEqual(srv.SRT_PORT, srv.RTMP_PORT)

    def test_closes_the_socket_even_when_connect_fails(self):
        with mock.patch.object(srv.socket, 'socket') as sock:
            sock.return_value.connect.side_effect = OSError('Network is unreachable')
            self.assertFalse(srv.vps_route_ok())
        sock.return_value.close.assert_called_once()

    def test_no_tcp_probe_remains(self):
        with open(os.path.join(ROOT, 'airsent_server.py')) as f:
            src = f.read()
        self.assertNotIn('rtmp_port_reachable', src)
        self.assertNotIn('create_connection((RTMP_HOST', src)


if __name__ == '__main__':
    unittest.main()
