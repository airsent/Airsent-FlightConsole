import asyncio
import json
import hashlib
import time



PRESENCE_PORT = 9201
clients = {}


def counts():
    active = {}
    for ws, meta in clients.items():
        if meta.get("role") not in ("operator", "viewer"):
            continue
        key = meta.get("clientId") or str(id(ws))
        if key not in active or meta["seen"] > active[key]["seen"]:
            active[key] = meta
    operators = sum(meta["role"] == "operator" for meta in active.values())
    viewers = sum(meta["role"] == "viewer" for meta in active.values())
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


async def presence_handler(ws, *args):
    clients[ws] = {"role": "unknown", "page": "unknown", "seen": time.time()}
    print(f"[PRESENCE] connected {ws.remote_address} ({len(clients)} sockets)")
    await broadcast_counts()
    try:
        async for message in ws:
            try:
                data = json.loads(message)
            except Exception:
                continue

            if not isinstance(data, dict):
                continue
            if data.get("type") != "presence":
                continue

            role = str(data.get("role", "")).lower()
            previous = clients.get(ws, {})
            clients[ws] = {
                "role": role if role in ("operator", "viewer") else "unknown",
                "page": str(data.get("page", "unknown"))[:80],
                "clientId": str(data.get("clientId", ""))[:80],
                "seen": time.time(),
            }
            if previous.get("clientId") != clients[ws]["clientId"] or previous.get("role") != role:
                tag = hashlib.sha256(clients[ws]["clientId"].encode()).hexdigest()[:10]
                print(f"[PRESENCE] registered {role} device={tag} sockets={len(clients)}", flush=True)
            await ws.send(json.dumps({"type": "presence_registered", "role": clients[ws]["role"],
                                      "device": hashlib.sha256(clients[ws]["clientId"].encode()).hexdigest()[:10]}))
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
    import websockets
    server = await websockets.serve(
        presence_handler,
        "127.0.0.1",
        PRESENCE_PORT,
        max_size=4096,
        ping_interval=20,
        ping_timeout=20,
    )
    print(f"[PRESENCE] listening on ws://0.0.0.0:{PRESENCE_PORT}")
    await asyncio.gather(cleanup_loop(), server.wait_closed())


if __name__ == "__main__":
    asyncio.run(main())
