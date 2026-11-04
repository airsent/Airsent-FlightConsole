"""Shared display countdown. This service never sends vehicle commands."""
import asyncio
import hmac
import json
import math
import os
from pathlib import Path
import time


class Sequence:
    def __init__(self, path, clock=time.time):
        self.path, self.clock = Path(path), clock
        self.data = dict(state='standby', total=180, remaining=180, handoverAt=120,
                         deadline=None, abortReason='', revision=0)
        if self.path.exists():
            self.data.update(json.loads(self.path.read_text()))

    def snapshot(self):
        now = self.clock()
        d = dict(self.data)
        if d['state'] == 'counting':
            d['remaining'] = max(0, math.ceil(d['deadline'] - now))
            if now >= d['deadline']:
                d['state'] = 'launch'
        d['launchElapsed'] = max(0, math.floor(now - d['deadline'])) if d['state'] == 'launch' else 0
        d['handoverTriggered'] = d['state'] == 'launch' or (d['state'] != 'standby' and d['remaining'] <= d['handoverAt'])
        d.update(type='sequence_state', serverTime=now * 1000)
        return d

    def command(self, message):
        d = self.snapshot()
        cmd = message.get('cmd')
        if message.get('revision') != self.data['revision']:
            raise ValueError('State changed. Try again.')
        if cmd == 'configure':
            if d['state'] != 'standby':
                raise ValueError('Reset before changing countdown settings.')
            total = message.get('total', d['total'])
            handover = message.get('handoverAt', d['handoverAt'])
            if isinstance(total, bool) or not isinstance(total, int) or not 10 <= total <= 86400:
                raise ValueError('Countdown must be 10–86400 seconds.')
            if isinstance(handover, bool) or not isinstance(handover, int) or not 0 <= handover <= total:
                raise ValueError('Handover must be within the countdown.')
            d.update(total=total, remaining=total, handoverAt=handover)
        elif cmd == 'start' and d['state'] == 'standby':
            d.update(state='counting', remaining=d['total'], deadline=self.clock() + d['total'], abortReason='')
        elif cmd == 'hold' and d['state'] == 'counting':
            d.update(state='hold', deadline=None)
        elif cmd == 'resume' and d['state'] == 'hold':
            d.update(state='counting', deadline=self.clock() + d['remaining'])
        elif cmd == 'abort' and d['state'] in ('counting', 'hold'):
            d.update(state='abort', deadline=None, abortReason='Aborted by operator')
        elif cmd == 'reset':
            d.update(state='standby', remaining=d['total'], deadline=None, abortReason='')
        else:
            raise ValueError('Command is not available in this state.')
        d['revision'] += 1
        self.data = {k: d[k] for k in self.data}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.data))
        temporary.replace(self.path)
        return self.snapshot()


async def main():
    import websockets
    password = os.environ['AIRSENT_OPERATOR_PASSWORD']
    sequence = Sequence(os.environ.get('AIRSENT_SEQUENCE_STATE', '/var/lib/airsent-sequence/state.json'))
    clients = set()

    async def handler(ws, *args):
        authorized = False
        clients.add(ws)
        await ws.send(json.dumps(sequence.snapshot()))
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                    if not isinstance(msg, dict):
                        raise ValueError('Invalid message')
                    if msg.get('type') == 'sequence_auth':
                        authorized = hmac.compare_digest(str(msg.get('password', '')), password)
                        await ws.send(json.dumps(dict(type='sequence_auth', authorized=authorized)))
                        if not authorized:
                            await ws.close(code=1008, reason='Operator authentication failed')
                            return
                    elif msg.get('type') == 'sequence_command':
                        if not authorized:
                            raise ValueError('Operator sign-in required')
                        sequence.command(msg)
                        await broadcast()
                except (ValueError, TypeError, KeyError) as exc:
                    await ws.send(json.dumps(dict(type='sequence_error', message=str(exc))))
        finally:
            clients.discard(ws)

    async def broadcast():
        payload = json.dumps(sequence.snapshot())
        if clients:
            await asyncio.gather(*(ws.send(payload) for ws in list(clients)), return_exceptions=True)

    async with websockets.serve(handler, '127.0.0.1', int(os.environ.get('AIRSENT_SEQUENCE_PORT', '9202')), max_size=4096,
                                origins=['https://console.airsent.tech', 'http://localhost:8080'],
                                ping_interval=20, ping_timeout=20):
        while True:
            await broadcast()
            await asyncio.sleep(0.5)


if __name__ == '__main__':
    asyncio.run(main())
