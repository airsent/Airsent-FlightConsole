import unittest
from types import SimpleNamespace
from health_telemetry import HealthTelemetry,gpu_percent
class HealthTests(unittest.TestCase):
 def status(self,**kwargs):return SimpleNamespace(get_type=lambda:'SYS_STATUS',onboard_control_sensors_present=15,onboard_control_sensors_enabled=15,onboard_control_sensors_health=15,voltage_battery=0,current_battery=50,battery_remaining=96,**kwargs)
 def test_sensor_masks_and_stale(self):
  h=HealthTelemetry();h.observe(self.status(),10);s,p=h.snapshot(11,True);self.assertEqual(s['imu'],'ok');self.assertEqual(s['outputs'],'not_reported');self.assertIsNone(p['voltage']);self.assertEqual(h.snapshot(16,True)[0]['imu'],'unknown')
 def test_battery_status_preferred_and_missing_voltage_is_unknown(self):
  h=HealthTelemetry();h.observe(self.status(),10)
  battery=SimpleNamespace(get_type=lambda:'BATTERY_STATUS',id=0,voltages=[22000]+[65535]*9,current_battery=50,battery_remaining=95)
  h.observe(battery,10);s,p=h.snapshot(11,True);self.assertEqual((p['voltage'],p['current'],p['battery']),(22,.5,95));self.assertIsNone(h.snapshot(11,False)[1]['voltage'])
 def test_noise_level_voltage_does_not_become_valid_power(self):
  h=HealthTelemetry();battery=SimpleNamespace(get_type=lambda:'BATTERY_STATUS',id=0,voltages=[1,1,1,1]+[65535]*6,current_battery=50,battery_remaining=91)
  h.observe(battery,10);self.assertIsNone(h.snapshot(11,True)[1]['voltage'])
 def test_gpu_permille_and_invalid(self):
  self.assertEqual(gpu_percent(lambda p:'50'),5)
  self.assertEqual(gpu_percent(lambda p:'0'),0)
  self.assertIsNone(gpu_percent(lambda p:'nan'))
