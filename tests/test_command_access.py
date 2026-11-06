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
  target=Socket({});ns=dict(asyncio=asyncio,json=json,SECRET='test-secret',token_role=token_role,jetson={'cmd':target},browsers={'cmd':set()})
  exec(compile(ast.Module(body=[node],type_ignores=[]),'relay.py','exec'),ns)
  socket=Socket({'type':'command_auth','token':issue_token(role,'test-secret')},['test-only-command'])
  await ns['cmd_browser'](socket)
  return socket,target
 async def test_viewer_and_simulation_never_forward_to_real_drone(self):
  for role in ('viewer','simulation'):
   socket,target=await self.run_role(role);self.assertEqual(socket.closed['code'],1008);self.assertEqual(target.sent,[])
 async def test_verified_operator_forwarded_to_fake_target(self):
  socket,target=await self.run_role('operator');self.assertIsNone(socket.closed);self.assertEqual(target.sent,['test-only-command'])
 async def test_unsigned_first_command_rejected(self):
  ns=dict(asyncio=asyncio,json=json,SECRET='test-secret',token_role=token_role)
  exec(compile(ast.Module(body=[node],type_ignores=[]),'relay.py','exec'),ns)
  socket=Socket({'action':'arm'});await ns['cmd_browser'](socket);self.assertEqual(socket.closed['code'],1008)
