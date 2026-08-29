import asyncio
import json
import time

import websockets


PRESENCE_PORT = 9201
clients = {}


def counts():
    now = time.time()
    active = {
        ws: meta for ws, meta in clients.items()
        if now - meta.get("seen", 0) <= 20
    }
    clients.clear()
    clients.update(active)

    operators = sum(1 for meta in clients.values() if meta.get("role") == "operator")
    viewers = sum(1 for meta in clients.values() if meta.get("role") == "viewer")
    return {
        "type": "presence_counts",
        "total": operators + viewers,
        "operators": operators,
        "viewers": viewers,
    }


async def broadcast_counts():
    payload = json.dumps(counts())
    dead = []
    for ws in list(clients.keys()):
        try:
            await ws.send(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        clients.pop(ws, None)


async def presence_handler(ws):
    clients[ws] = {"role": "unknown", "page": "unknown", "seen": time.time()}
    print(f"[PRESENCE] connected {ws.remote_address} ({len(clients)} sockets)")
    await broadcast_counts()
    try:
        async for message in ws:
            try:
                data = json.loads(message)
            except Exception:
                continue

            if data.get("type") != "presence":
                continue

            role = str(data.get("role", "")).lower()
            clients[ws] = {
                "role": role if role in ("operator", "viewer") else "unknown",
                "page": str(data.get("page", "unknown"))[:80],
                "seen": time.time(),
            }
            await broadcast_counts()
    finally:
        clients.pop(ws, None)
        print(f"[PRESENCE] disconnected {ws.remote_address} ({len(clients)} sockets)")
        await broadcast_counts()


async def cleanup_loop():
    while True:
        await asyncio.sleep(5)
        await broadcast_counts()


async def main():
    server = await websockets.serve(
        presence_handler,
        "0.0.0.0",
        PRESENCE_PORT,
        max_size=100_000,
        ping_interval=20,
        ping_timeout=20,
    )
    print(f"[PRESENCE] listening on ws://0.0.0.0:{PRESENCE_PORT}")
    await asyncio.gather(cleanup_loop(), server.wait_closed())


if __name__ == "__main__":
    asyncio.run(main())
