import ast,json,math,socket,subprocess,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
source=ast.parse((Path(__file__).resolve().parents[1]/'airsent_server.py').read_text())
nodes=[n for n in source.body if isinstance(n,ast.FunctionDef) and n.name in ('signal_percent','read_network_link')]
ns=dict(math=math,socket=socket,json=json,subprocess=subprocess,VPS_TELEM_URL='ws://146.190.50.189:9001')
exec(compile(ast.Module(body=nodes,type_ignores=[]),'airsent_server.py','exec'),ns)
class NetworkChecks(unittest.TestCase):
 def sample(self,kind='wifi',tech=None,quality='73',match=True,state='100 (connected)',fail=False):
  def run(args,**kwargs):
   if fail:raise FileNotFoundError()
   if args[:2]==['ip','-j']:out=json.dumps([{'dev':'wl0' if kind=='wifi' else 'wwan0'}])
   elif args[0]=='mmcli':out=json.dumps({'modem':{'generic':{'state':'connected','ports':['wwan0 (net)'] if match else ['wwan9 (net)'],'signal-quality':{'value':quality},'access-technologies':tech}}})
   elif 'GENERAL.TYPE' in args:out=kind
   elif 'GENERAL.STATE' in args:out=state
   elif 'GENERAL.CONNECTION' in args:out='Home profile'
   elif '802-11-wireless.ssid' in args:out='TheChair'
   else:out='*:84\n:50'
   return SimpleNamespace(stdout=out)
  return ns['read_network_link'](run)
 def test_home_wifi_routing_and_strength(self):
  d=self.sample();self.assertEqual((d['type'],d['ssid'],d['percent'],d['connected']),('wifi','TheChair',84,True))
 def test_cellular_5g_string_percent(self):
  d=self.sample('gsm',['5gnr']);self.assertEqual((d['type'],d['technology'],d['percent']),('cellular','5gnr',73))
 def test_lte_not_assumed_5g(self):self.assertEqual(self.sample('gsm',['lte'])['technology'],'lte')
 def test_no_quality_still_connected(self):
  d=self.sample('gsm',None,'unknown');self.assertTrue(d['connected']);self.assertIsNone(d['percent'])
 def test_unrelated_modem_does_not_supply_strength(self):self.assertIsNone(self.sample('gsm',['5gnr'],match=False)['technology'])
 def test_missing_tools_and_disconnected_interface(self):
  self.assertFalse(self.sample(fail=True)['connected']);self.assertFalse(self.sample(state='30 (disconnected)')['connected'])
 def test_ethernet_not_cellular(self):self.assertEqual(self.sample('ethernet')['type'],'ethernet')
 def test_modem_control_device_mapped_to_ip_interface(self):
  def run(args,**kw):
   if args[0]=='ip':out='[{"dev":"wwan0"}]'
   elif 'GENERAL.TYPE' in args:raise subprocess.CalledProcessError(10,args)
   elif 'DEVICE,TYPE' in args:out='wl0:wifi\ncdc-wdm0:gsm'
   elif 'GENERAL.IP-IFACE' in args:out='wwan0'
   elif 'GENERAL.STATE' in args:out='100 (connected)'
   elif args[0]=='mmcli':out=json.dumps({'modem':{'generic':{'primary-port':'cdc-wdm0','access-technologies':['5gnr'],'signal-quality':{'value':'80'}}}})
   else:raise AssertionError(args)
   return SimpleNamespace(stdout=out)
  d=ns['read_network_link'](run)
  self.assertTrue(d['connected']);self.assertEqual(d['technology'],'5gnr')
 def test_invalid_percent(self):
  for v in [None,True,'nan','inf','255',-1]:self.assertIsNone(ns['signal_percent'](v))
if __name__=='__main__':unittest.main()
