import tempfile,unittest
from pathlib import Path
from shared_state_server import State,validate,DEFAULTS
class SharedTests(unittest.TestCase):
 def setUp(self):self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'state.json';self.state=State(self.path)
 def tearDown(self):self.temp.cleanup()
 def test_persistent_operator_changes_and_conflicts(self):
  key='airsent-failsafe-activation-v1';self.state.update('operator',key,{'rc':True},0)
  self.assertTrue(State(self.path).values[key]['rc'])
  with self.assertRaises(ValueError):self.state.update('operator',key,{'rc':False},0)
  self.assertTrue(self.state.values[key]['rc'])
 def test_viewer_and_demo_cannot_write(self):
  for role in ['viewer','demo','simulation',None]:
   with self.assertRaises(PermissionError):self.state.update(role,'airsent-asc-enabled-v1',True,0)
  self.assertFalse(self.path.exists())
 def test_independent_keys_do_not_conflict(self):
  self.state.update('operator','airsent-asc-enabled-v1',True,0)
  self.state.update('operator','airsent-preflight-manual-v1',{'v-frame':'pass'},0)
  self.assertEqual(self.state.versions['airsent-preflight-manual-v1'],1)
 def test_bad_settings_rejected_without_persistence(self):
  for key,value in [('unknown',{}),('airsent-asc-enabled-v1','yes'),('airsent-failsafe-activation-v1',{'rc':'true'}),('airsent-parameter-drafts-v1',{'a':float('nan')}),('airsent_mission_v1',{'waypoints':[{'lat':999,'lon':1}]})]:
   with self.assertRaises(ValueError):self.state.update('operator',key,value,0)
  self.assertFalse(self.path.exists())
