import tempfile,threading,time,unittest
from pathlib import Path
from types import SimpleNamespace
from pilot_settings import PilotSettings,PARAMETERS
class SettingsTests(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.s=PilotSettings(Path(self.tmp.name)/'settings.json')
 def tearDown(self):self.tmp.cleanup()
 def msg(self,name,value):return SimpleNamespace(param_id=name,param_value=value,param_type=9)
 def mav(self,callback):return SimpleNamespace(target_system=1,target_component=1,mav=SimpleNamespace(param_set_send=callback,param_request_read_send=lambda *args:None))
 def test_param_mapping_and_readback(self):
  for key,(name,scale,lo,hi) in PARAMETERS.items():
   self.s.observe(self.msg(name,lo*scale));sent=[]
   def write(sys,comp,param,value,kind):sent.append((param,value));self.s.observe(self.msg(param,value))
   result=self.s.apply(self.mav(write),key,hi,self.s.revision)
   self.assertEqual(sent,[(name.encode(),hi*scale)]);self.assertEqual(result['values'][key],hi)
 def test_view_values_are_unknown_without_fresh_readback(self):
  self.assertIsNone(self.s.snapshot()['values']['rth_alt'])
  self.s.observe(self.msg('RTL_ALT',3000));self.assertEqual(self.s.snapshot()['values']['rth_alt'],30)
  self.assertIsNone(self.s.snapshot(False)['values']['rth_alt'])
 def test_rejection_and_timeout_never_claim_applied(self):
  self.s.observe(self.msg('RTL_ALT',3000))
  with self.assertRaisesRegex(ValueError,'differs'):self.s.apply(self.mav(lambda *x:self.s.observe(self.msg('RTL_ALT',3000))),'rth_alt',40,self.s.revision,timeout=.01)
  with self.assertRaisesRegex(ValueError,'No aircraft confirmation'):self.s.apply(self.mav(lambda *x:None),'rth_alt',40,self.s.revision,timeout=.01)
  self.assertEqual(self.s.snapshot()['values']['rth_alt'],30)
 def test_companion_settings_persist_and_invalid_values_reject(self):
  self.s.apply(None,'move_speed',4,self.s.revision);self.assertEqual(PilotSettings(self.s.path).values['move_speed'],4)
  for key,value in [('move_speed',99),('detection','false'),('units','bad'),('max_alt',float('nan'))]:
   with self.assertRaises(ValueError):self.s.apply(None,key,value,self.s.revision)
 def test_armed_stale_revision_and_unavailable_parameters_reject(self):
  for kwargs in [{'armed':True},{'revision':999}]:
   args={'revision':self.s.revision,**kwargs}
   with self.assertRaises(ValueError):self.s.apply(None,'move_speed',4,**args)
  with self.assertRaisesRegex(ValueError,'not available'):self.s.apply(None,'rth_alt',40,self.s.revision)
 def test_manual_vectors_clamped_and_nonfinite_rejected(self):
  x,y,z,yaw=self.s.clamp_velocity(3,4,-9,8);self.assertAlmostEqual((x*x+y*y)**.5,2);self.assertEqual((z,yaw),(-1,.5))
  with self.assertRaises(ValueError):self.s.clamp_velocity(float('nan'),0,0,0)
 def test_new_firmware_names_use_metres_without_centimetre_scaling(self):
  for key,name,value in [('rth_alt','RTL_ALT_M',40),('max_speed','WP_SPD',6)]:
   self.s.observe(self.msg(name,15 if key=='rth_alt' else 5));sent=[]
   def write(sys,comp,param,target,kind):sent.append(target);self.s.observe(self.msg(param,target))
   result=self.s.apply(self.mav(write),key,value,self.s.revision)
   self.assertEqual(sent,[value]);self.assertEqual(result['values'][key],value);self.assertEqual(result['parameters'][key],name)
 def test_reset_clears_parameter_confirmation(self):
  self.s.observe(self.msg('RTL_ALT',3000));self.s.reset();self.assertIsNone(self.s.snapshot()['values']['rth_alt'])
