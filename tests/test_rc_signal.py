"""Exercise the production monitor without Jetson camera/serial dependencies."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest

source = ast.parse((Path(__file__).resolve().parents[1] / 'airsent_server.py').read_text())
node = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == 'RCSignalMonitor')
namespace = {}
exec(compile(ast.Module(body=[node], type_ignores=[]), 'airsent_server.py', 'exec'), namespace)
Monitor = namespace['RCSignalMonitor']

def msg(kind, **fields):
    return SimpleNamespace(get_type=lambda: kind, **fields)

class RCChecks(unittest.TestCase):
    def setUp(self):
        self.monitor = Monitor()

    def health(self, healthy=True):
        bit = 1 << 16
        self.monitor.update(msg('SYS_STATUS', onboard_control_sensors_present=bit,
            onboard_control_sensors_enabled=bit, onboard_control_sensors_health=bit if healthy else 0), 10)

    def sample(self, value=254, channels=16, now=10):
        self.monitor.update(msg('RC_CHANNELS', rssi=value, chancount=channels), now)

    def test_startup_and_refresh_have_no_signal(self):
        self.assertFalse(self.monitor.snapshot(10, True)['connected'])

    def test_rssi_alone_does_not_prove_remote_connected(self):
        self.sample()
        self.assertFalse(self.monitor.snapshot(10, True)['connected'])

    def test_healthy_receiver_with_fresh_sample(self):
        self.health(); self.sample()
        self.assertEqual(self.monitor.snapshot(11, True)['percent'], 100)

    def test_remote_off_overrides_even_full_rssi(self):
        self.health(); self.sample(); self.health(False); self.sample()
        self.assertIsNone(self.monitor.snapshot(11, True)['percent'])

    def test_unknown_rssi_keeps_connection_without_inventing_strength(self):
        self.health(); self.sample(); self.sample(255)
        result = self.monitor.snapshot(11, True)
        self.assertTrue(result['connected'])
        self.assertIsNone(result['percent'])

    def test_measured_zero_is_not_replaced_with_full_strength(self):
        self.health(); self.sample(0)
        self.assertEqual(self.monitor.snapshot(11, True)['percent'], 0)

    def test_disconnected_channels_clear_connection(self):
        self.health(); self.sample(); self.sample(254, 0)
        self.assertFalse(self.monitor.snapshot(11, True)['connected'])

    def test_telemetry_radio_cannot_supply_rc(self):
        self.health()
        self.monitor.update(msg('RADIO_STATUS', rssi=254), 10)
        self.assertFalse(self.monitor.snapshot(11, True)['connected'])

    def test_stale_health_not_kept_alive_by_rc_messages(self):
        self.health(); self.sample(now=14)
        self.assertFalse(self.monitor.snapshot(14, True)['connected'])

    def test_stale_samples_and_pixhawk_disconnect(self):
        self.health(); self.sample()
        self.assertFalse(self.monitor.snapshot(13, True)['connected'])
        self.assertFalse(self.monitor.snapshot(11, False)['connected'])

    def test_reconnect_resets_previous_evidence(self):
        self.health(); self.sample(); self.monitor.reset(); self.sample()
        self.assertFalse(self.monitor.snapshot(11, True)['connected'])

if __name__ == '__main__':
    unittest.main()
