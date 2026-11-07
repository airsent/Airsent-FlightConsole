import ast, asyncio, json, unittest
from pathlib import Path
from presence_auth import issue_token, token_role
source=ast.parse((Path(__file__).resolve().parents[1]/'deploy/relay.py').read_text())
node=next(n for n in source.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='cmd_browser')
class Socket:
 def __init__(self,auth,messages=()): self.auth=auth;self.messages=iter(messages);self.closed=None;self.sent=[]
 async def recv(self):return json.dumps(self.auth)
 async def close(self,**kw):self.closed=kw
 async def send(self,message):self.sent.append(message)
 def __aiter__(self):return self
 async def __anext__(self):
  try:return next(self.messages)
  except StopIteration:raise StopAsyncIteration
class AccessTests(unittest.IsolatedAsyncioTestCase):
 async def run_role(self,role):
  target=Socket({});ns=dict(asyncio=asyncio,json=json,SECRET='test-secret',token_role=token_role,jetson={'cmd':target},browsers={'cmd':set()},JOURNAL=type('Journal',(),{'append':lambda *a:None})(),QUIET_ACTIONS=set(),remember=lambda *a:None)
  exec(compile(ast.Module(body=[node],type_ignores=[]),'relay.py','exec'),ns)
  socket=Socket({'type':'command_auth','token':issue_token(role,'test-secret')},['{"action":"test_only"}'])
  await ns['cmd_browser'](socket)
  return socket,target
 async def test_viewer_and_simulation_never_forward_to_real_drone(self):
  for role in ('viewer','simulation'):
   socket,target=await self.run_role(role);self.assertEqual(socket.closed['code'],1008);self.assertEqual(target.sent,[])
 async def test_verified_operator_forwarded_to_fake_target(self):
  socket,target=await self.run_role('operator');self.assertIsNone(socket.closed);self.assertEqual(target.sent,['{"action":"test_only"}'])
 async def test_unsigned_first_command_rejected(self):
  ns=dict(asyncio=asyncio,json=json,SECRET='test-secret',token_role=token_role)
  exec(compile(ast.Module(body=[node],type_ignores=[]),'relay.py','exec'),ns)
  socket=Socket({'action':'arm'});await ns['cmd_browser'](socket);self.assertEqual(socket.closed['code'],1008)

class DirectReplies(unittest.IsolatedAsyncioTestCase):
 async def test_direct_reply_goes_only_to_the_requester(self):
  fns={n.name:n for n in source.body if isinstance(n,(ast.AsyncFunctionDef,ast.FunctionDef)) and n.name in ('cmd_jetson','remember')}
  a,b=Socket({}),Socket({})
  ns=dict(asyncio=asyncio,json=json,jetson={'cmd':None},browsers={'cmd':{a,b}},pending={},JOURNAL=type('J',(),{'append':lambda *x:None})(),print=lambda *x:None)
  exec(compile(ast.Module(body=[fns['remember'],fns['cmd_jetson']],type_ignores=[]),'relay.py','exec'),ns)
  ns['remember']('r1',a)
  jet=Socket({},[json.dumps({'ok':True,'request_id':'r1','direct':True,'data':'x'}),json.dumps({'ok':True,'msg':'hello'})])
  await ns['cmd_jetson'](jet)
  self.assertEqual(len(a.sent),2)          # its own direct reply + the broadcast
  self.assertEqual(len(b.sent),1)          # only the broadcast
  self.assertNotIn('direct',b.sent[0])
