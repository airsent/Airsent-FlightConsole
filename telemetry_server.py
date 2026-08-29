import asyncio
import json
import time

import websockets


JETSON_PORT = 9001
BROWSER_PORT = 9101

latest_payload = {}
latest_payload_ts = 0.0
browser_clients = set()
client_roles = {}


def console_presence():
    operators = sum(1 for role in client_roles.values() if role == "operator")
    viewers = sum(1 for role in client_roles.values() if role == "viewer")
    return {
        "total": operators + viewers,
        "operators": operators,
        "viewers": viewers,
    }


def payload_for_browser():
    payload = dict(latest_payload)
    payload["console_presence"] = console_presence()
    payload["telemetry_age"] = round(max(0.0, time.time() - latest_payload_ts), 2) if latest_payload_ts else None
    return json.dumps(payload)


async def jetson_handler(websocket):
    global latest_payload, latest_payload_ts
    print(f"[JETSON] connected: {websocket.remote_address}")
    try:
        async for message in websocket:
            try:
                data = json.loads(message)
            except Exception:
                continue
            latest_payload = data if isinstance(data, dict) else {}
            latest_payload_ts = time.time()
    finally:
        print(f"[JETSON] disconnected: {websocket.remote_address}")


async def browser_handler(websocket):
    browser_clients.add(websocket)
    client_roles[websocket] = "unknown"
    print(f"[BROWSER] connected: {websocket.remote_address} ({len(browser_clients)} total)")
    try:
        async for message in websocket:
            try:
                data = json.loads(message)
            except Exception:
                continue
            if data.get("type") == "console_presence":
                role = str(data.get("role", "")).lower()
                client_roles[websocket] = role if role in ("operator", "viewer") else "unknown"
    finally:
        browser_clients.discard(websocket)
        client_roles.pop(websocket, None)
        print(f"[BROWSER] disconnected: {websocket.remote_address} ({len(browser_clients)} total)")


async def broadcast_loop():
    while True:
        await asyncio.sleep(0.1)
        if not browser_clients:
            continue

        payload = payload_for_browser()
        dead = set()
        for ws in set(browser_clients):
            try:
                await ws.send(payload)
            except Exception:
                dead.add(ws)

        for ws in dead:
            browser_clients.discard(ws)
            client_roles.pop(ws, None)


async def main():
    jetson_server = await websockets.serve(
        jetson_handler,
        "0.0.0.0",
        JETSON_PORT,
        max_size=1_000_000,
        ping_interval=None,
    )
    browser_server = await websockets.serve(
        browser_handler,
        "0.0.0.0",
        BROWSER_PORT,
        max_size=1_000_000,
        ping_interval=None,
    )

    print(f"[VPS] Jetson telemetry input  ws://0.0.0.0:{JETSON_PORT}")
    print(f"[VPS] Browser telemetry feed ws://0.0.0.0:{BROWSER_PORT}")

    await asyncio.gather(
        broadcast_loop(),
        jetson_server.wait_closed(),
        browser_server.wait_closed(),
    )


if __name__ == "__main__":
    asyncio.run(main())
