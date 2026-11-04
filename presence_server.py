import asyncio
import json
import hashlib
import time
import os
from presence_auth import authenticate, issue_token, token_role



PRESENCE_PORT = 9201
clients = {}
AUTH_SECRET = ""
AUTH_PASSWORDS = {}


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
            if data.get("type") == "presence_login":
                role = authenticate(str(data.get("username", "")).lower(), str(data.get("password", "")), AUTH_PASSWORDS)
                await ws.send(json.dumps({"type": "presence_login", "role": role,
                                          "token": issue_token(role, AUTH_SECRET) if role else None}))
                if not role: await ws.close(code=1008, reason="Invalid login")
                continue
            if data.get("type") != "presence":
                continue

            role = token_role(str(data.get("token", "")), AUTH_SECRET)
            if not role:
                await ws.send(json.dumps({"type": "presence_login_required", "requestId": data.get("requestId")}))
                clients[ws]["role"] = "unknown"
                await broadcast_counts()
                continue
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
                                      "device": hashlib.sha256(clients[ws]["clientId"].encode()).hexdigest()[:10], "requestId": data.get("requestId")}))
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
    global AUTH_SECRET, AUTH_PASSWORDS
    AUTH_SECRET = os.environ["AIRSENT_PRESENCE_SECRET"]
    AUTH_PASSWORDS = {"operator": os.environ["AIRSENT_OPERATOR_PASSWORD"], "viewer": os.environ["AIRSENT_VIEWER_PASSWORD"]}
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
