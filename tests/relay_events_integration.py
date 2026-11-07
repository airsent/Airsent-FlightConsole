"""Run with websockets installed. Uses ephemeral loopback ports and a fake Jetson only."""
import asyncio, importlib.util, json, os, sys, tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import websockets
from presence_auth import issue_token

async def main():
    with tempfile.TemporaryDirectory() as root:
        os.environ['AIRSENT_EVENT_DB']=str(Path(root)/'events.db')
        os.environ['AIRSENT_PRESENCE_SECRET']='isolated-test-secret'
        spec=importlib.util.spec_from_file_location('test_relay',Path(__file__).resolve().parents[1]/'deploy/relay.py')
        relay=importlib.util.module_from_spec(spec);spec.loader.exec_module(relay)
        class FakeJetson:
            sent=[]
            async def send(self,msg):self.sent.append(msg)
        fake=FakeJetson();relay.jetson['cmd']=fake
        async with websockets.serve(relay.events_browser,'127.0.0.1',0) as events, websockets.serve(relay.cmd_browser,'127.0.0.1',0) as commands:
            event_url='ws://127.0.0.1:'+str(events.sockets[0].getsockname()[1])
            command_url='ws://127.0.0.1:'+str(commands.sockets[0].getsockname()[1])
            async def reader(role):
                ws=await websockets.connect(event_url)
                await ws.send(json.dumps(dict(type='events_auth',token=issue_token(role,relay.SECRET))))
                await ws.recv();return ws
            readers=[await reader(role) for role in ('operator','operator','viewer')]
            async with websockets.connect(command_url) as cmd:
                await cmd.send(json.dumps(dict(type='command_auth',token=issue_token('operator',relay.SECRET))))
                # Deliberately a fake-only action: never connects to hardware.
                await cmd.send('{"action":"test_only"}')
                for ws in readers:
                    batch=json.loads(await asyncio.wait_for(ws.recv(),3))['events']
                    assert [x['text'] for x in batch]==['OPERATOR REQUEST: TEST_ONLY','COMMAND FORWARDED: TEST_ONLY']
                assert fake.sent==['{"action":"test_only"}']
                await readers[2].send(json.dumps(dict(type='operator_note',id='viewer-forged',text='forged')))
                await asyncio.sleep(.5)
                assert len(relay.JOURNAL.rows())==2
                await readers[0].send(json.dumps(dict(type='operator_note',id='same-note',text='Shared test note')))
                await asyncio.sleep(.5)
                await readers[0].send(json.dumps(dict(type='operator_note',id='same-note',text='Shared test note')))
                await asyncio.sleep(.5)
                assert len(relay.JOURNAL.rows())==3
            for ws in readers:await ws.close()
            async with websockets.connect(event_url) as refreshed:
                await refreshed.send(json.dumps(dict(type='events_auth',token=issue_token('viewer',relay.SECRET))))
                assert len(json.loads(await refreshed.recv())['events'])==3
            async with websockets.connect(event_url) as demo:
                await demo.send(json.dumps(dict(type='events_auth',token=issue_token('simulation',relay.SECRET))))
                try:await demo.recv();assert False,'Simulation accepted'
                except websockets.exceptions.ConnectionClosed:pass
        relay.JOURNAL.db.close()
        from event_journal import EventJournal
        restored=EventJournal(os.environ['AIRSENT_EVENT_DB']);assert len(restored.rows())==3;restored.db.close()
        print('PASS: two operators + viewer receive identical history; refresh/restart persist; viewer writes and simulation rejected; notes deduplicated; fake target only')
asyncio.run(main())
