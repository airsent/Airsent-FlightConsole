import asyncio
import json
import os
import sys
sys.path.insert(0, "/opt/airsent")
from presence_auth import token_role
SECRET = os.environ.get("AIRSENT_PRESENCE_SECRET", "")
import websockets

# ── Shared connection state ───────────────────────────────────────────────────
# jetson["telem"] : the single inbound connection from the Jetson (telemetry)
# jetson["cmd"]   : the single inbound connection from the Jetson (commands)
# browsers["telem"]: the set of browser dashboard/pilot telemetry connections
# browsers["cmd"]  : the set of browser command connections
jetson = {
    "telem": None,
    "cmd":   None,
}
browsers = {
    "telem": set(),
    "cmd":   set(),
}

# ── Jetson-facing telemetry handler (port 9001) ───────────────────────────────
async def telem_jetson(ws):
    jetson["telem"] = ws
    print("[RELAY] Jetson telemetry connected")
    try:
        async for msg in ws:
            dead = set()
            for b in list(browsers["telem"]):
                try:
                    await b.send(msg)
                except Exception:
                    dead.add(b)
            browsers["telem"] -= dead
    finally:
        jetson["telem"] = None
        print("[RELAY] Jetson telemetry disconnected")

# ── Browser-facing telemetry handler (port 9101) ──────────────────────────────
async def telem_browser(ws):
    browsers["telem"].add(ws)
    print(f"[RELAY] Browser telem connected ({len(browsers['telem'])} total)")
    try:
        await ws.wait_closed()
    finally:
        browsers["telem"].discard(ws)
        print(f"[RELAY] Browser telem disconnected ({len(browsers['telem'])} remaining)")

# ── Jetson-facing command handler (port 9004) ─────────────────────────────────
async def cmd_jetson(ws):
    jetson["cmd"] = ws
    print("[RELAY] Jetson command channel connected")
    try:
        async for msg in ws:
            dead = set()
            for b in list(browsers["cmd"]):
                try:
                    await b.send(msg)
                except Exception:
                    dead.add(b)
            browsers["cmd"] -= dead
    finally:
        jetson["cmd"] = None
        print("[RELAY] Jetson command channel disconnected")

# ── Browser-facing command handler (port 9104) ────────────────────────────────
async def cmd_browser(ws):
    try:
        auth = json.loads(await asyncio.wait_for(ws.recv(), 8))
        token = auth.get('token', '')
        if not SECRET or auth.get('type') != 'command_auth' or token_role(token, SECRET) != 'operator':
            await ws.close(code=1008, reason='Verified operator session required')
            return
    except (Exception, asyncio.TimeoutError):
        await ws.close(code=1008, reason='Verified operator session required')
        return
    browsers["cmd"].add(ws)
    print(f"[RELAY] Browser cmd connected ({len(browsers['cmd'])} total)")
    try:
        async for msg in ws:
            if token_role(token, SECRET) != 'operator':
                await ws.close(code=1008, reason='Operator session expired')
                break
            jws = jetson["cmd"]
            if jws is None:
                try:
                    await ws.send('{"ok":false,"msg":"Jetson command channel offline"}')
                except Exception:
                    pass
                continue
            try:
                await jws.send(msg)
            except Exception:
                try:
                    await ws.send('{"ok":false,"msg":"Failed to reach Jetson"}')
                except Exception:
                    pass
    finally:
        browsers["cmd"].discard(ws)
        print(f"[RELAY] Browser cmd disconnected ({len(browsers['cmd'])} remaining)")

# ── Main ──────────────────────────────────────────────────────────────────────
async def main():
    jetson_srv   = await websockets.serve(telem_jetson,  "0.0.0.0", 9001, ping_interval=None)
    browser_srv  = await websockets.serve(telem_browser, "0.0.0.0", 9101, ping_interval=None)
    cmd_jet_srv  = await websockets.serve(cmd_jetson,    "0.0.0.0", 9004, ping_interval=None)
    cmd_brw_srv  = await websockets.serve(cmd_browser,   "0.0.0.0", 9104, ping_interval=None)
    print("[RELAY] Airsent VPS relay running")
    print("[RELAY]   Jetson telem -> ws://0.0.0.0:9001  (Jetson connects here)")
    print("[RELAY]   Browser telem-> ws://0.0.0.0:9101  (Dashboard connects here)")
    print("[RELAY]   Jetson cmd   -> ws://0.0.0.0:9004  (Jetson connects here)")
    print("[RELAY]   Browser cmd  -> ws://0.0.0.0:9104  (Dashboard connects here)")
    await asyncio.gather(
        jetson_srv.wait_closed(),
        browser_srv.wait_closed(),
        cmd_jet_srv.wait_closed(),
        cmd_brw_srv.wait_closed(),
    )

if __name__ == "__main__":
    asyncio.run(main())
