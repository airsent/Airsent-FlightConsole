import binascii,importlib.util,struct,unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('datalink',Path(__file__).resolve().parents[1]/'datalink_telemetry.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
def packet(counter=1,channel=1,voltage=240,percent=16384,speed=22000):
 b=bytearray([0x9b,158,0,2,0,counter%256])
 for i in range(1,9):
  b.extend(struct.pack('>BHHHHHhhBBH',i,counter,percent if i==channel else 0,percent if i==channel else 0,speed if i==channel else 0,voltage if i==channel else 0,640 if i==channel else 0,0,150,150,0))
 b.extend(binascii.crc_hqx(b,0).to_bytes(2,'little'));return bytes(b)
class DataLinkChecks(unittest.TestCase):
 def setUp(self):self.now=0;self.monitor=m.DataLinkMonitor(clock=lambda:self.now)
 def test_actual_tmotor_alpha40_idle_capture(self):
  capture=Path(__file__).parent/'fixtures/tmotor-alpha40-idle.hex'
  self.monitor.feed(bytes.fromhex(capture.read_text()));s=self.monitor.snapshot()
  self.assertEqual(s['valid_frames'],2);self.assertEqual(s['crc_errors'],0)
  self.assertEqual(s['channels'][0]['voltage'],19.7)
  self.assertTrue(all(r['connected'] for r in s['channels'][:4]))
  self.assertTrue(all(r['rpm']==0 and r['output_percent']==0 for r in s['channels'][:4]))
  self.assertIsNone(s['channels'][1]['current'])
  self.assertFalse(s['channels'][4]['connected'])
 def test_published_tx1_sample(self):
  # HW protocol 170730, page 3: CRC bytes are 1d 11 (little endian).
  frame=bytes.fromhex('9b9e0002883f')+b''.join(struct.pack('>BH',i,13)+bytes(16) for i in range(1,9))+bytes.fromhex('1d11')
  self.monitor.feed(frame);s=self.monitor.snapshot()
  self.assertEqual(s['valid_frames'],1)
  self.assertTrue(s['connected']);self.assertTrue(all(not r['connected'] for r in s['channels']))
 def test_fragmentation_and_noise(self):
  p=packet();self.monitor.feed(b'noise'+p[:50]);self.assertFalse(self.monitor.snapshot()['connected'])
  self.monitor.feed(p[50:]);s=self.monitor.snapshot();self.assertTrue(s['connected']);self.assertEqual(s['channels'][0]['rpm'],10000);self.assertEqual(s['channels'][0]['voltage'],24);self.assertAlmostEqual(s['channels'][0]['output_percent'],50,places=1)
 def test_crc_failure_recovery(self):
  p=bytearray(packet());p[12]^=1;self.monitor.feed(p+packet());self.assertEqual(self.monitor.snapshot()['valid_frames'],1);self.assertGreater(self.monitor.snapshot()['crc_errors'],0)
 def test_per_esc_freeze_despite_live_frames(self):
  self.monitor.feed(packet());self.now=3;self.monitor.feed(packet());s=self.monitor.snapshot();self.assertTrue(s['connected']);self.assertFalse(s['channels'][0]['connected']);self.assertNotIn('rpm',s['channels'][0])
 def test_fresh_counter_recovers(self):
  self.monitor.feed(packet());self.now=3;self.monitor.feed(packet(counter=2));self.assertTrue(self.monitor.snapshot()['channels'][0]['connected'])
 def test_empty_slots_unavailable(self):
  self.monitor.feed(packet());self.assertFalse(self.monitor.snapshot()['channels'][1]['connected'])
 def test_silent_link_expires(self):
  self.monitor.feed(packet());self.now=2;self.assertFalse(self.monitor.snapshot()['connected'])
 def test_bounded_noise(self):self.monitor.feed(b'\x9b'*10000);self.assertLessEqual(len(self.monitor.buffer),1)
if __name__=='__main__':unittest.main()
