"""Authenticated WebSocket proxy for the isolated simulated aircraft only."""
import asyncio
import os
from urllib.parse import urlparse, parse_qs
import websockets
from presence_auth import token_role

async def handler(ws, *args):
    path = getattr(getattr(ws, 'request', None), 'path', None) or getattr(ws, 'path', '')
    request = urlparse(path)
    token = parse_qs(request.query).get('session', [''])[0]
    if token_role(token, os.environ['AIRSENT_PRESENCE_SECRET']) != 'simulation':
        await ws.close(code=1008, reason='Simulation login required')
        return
    port = {'/simulation/telem':18765, '/simulation/cmd':18767}.get(request.path)
    if port is None:
        await ws.close(code=1008, reason='Unknown simulation channel');return
    try:
        async with websockets.connect(f'ws://127.0.0.1:{port}', max_size=1_000_000) as backend:
            async def forward(source, target):
                async for message in source: await target.send(message)
            jobs = [asyncio.create_task(forward(ws, backend)), asyncio.create_task(forward(backend, ws))]
            try:
                await asyncio.wait(jobs, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for job in jobs: job.cancel()
                await asyncio.gather(*jobs, return_exceptions=True)
    except Exception:
        await ws.close(code=1011, reason='Simulator connection unavailable')

async def main():
    async with websockets.serve(handler, '127.0.0.1', 19760, max_size=1_000_000, ping_interval=20, ping_timeout=20):
        await asyncio.Future()

if __name__ == '__main__': asyncio.run(main())
