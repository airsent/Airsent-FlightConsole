import asyncio
import json
import os
import sys
sys.path.insert(0, "/opt/airsent")
from presence_auth import token_role
SECRET = os.environ.get("AIRSENT_PRESENCE_SECRET", "")
import websockets
from event_journal import EventJournal
JOURNAL = EventJournal(os.environ.get("AIRSENT_EVENT_DB", "/var/lib/airsent-events/events.sqlite3"))

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
    JOURNAL.append("AIRCRAFT TELEMETRY CHANNEL CONNECTED", "relay")
    print("[RELAY] Jetson telemetry connected")
    try:
        async for msg in ws:
            if jetson["telem"] is not ws:
                continue
            JOURNAL.observe(msg)
            dead = set()
            for b in list(browsers["telem"]):
                try:
                    await b.send(msg)
                except Exception:
                    dead.add(b)
            browsers["telem"] -= dead
    finally:
        if jetson["telem"] is ws:
            jetson["telem"] = None
            JOURNAL.append("AIRCRAFT TELEMETRY CHANNEL DISCONNECTED", "relay")
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

# Reads and joystick input are not operator actions worth journaling.
QUIET_ACTIONS = {'mission_ping', 'velocity', 'param_read', 'log_list', 'log_detail', 'log_file', 'get_log', 'wp_download', 'cam_watch'}
# request_id -> browser socket, so large replies (log file chunks) go only to the console that asked.
pending = {}


def remember(request_id, ws):
    if request_id:
        pending[str(request_id)] = ws
        while len(pending) > 2000:
            pending.pop(next(iter(pending)))


# ── Jetson-facing command handler (port 9004) ─────────────────────────────────
async def cmd_jetson(ws):
    jetson["cmd"] = ws
    JOURNAL.append("AIRCRAFT COMMAND CHANNEL CONNECTED", "relay")
    print("[RELAY] Jetson command channel connected")
    try:
        async for msg in ws:
            reply = None
            try:
                reply = json.loads(msg)
                # Console keep-alives, reads and joystick input are not operator actions; keep them out of the journal.
                if not reply.get('quiet'):
                    JOURNAL.append('BACKEND ' + ('REPLY' if reply.get('ok') else 'ERROR') + ': ' + str(reply.get('msg', 'Response received')), 'Jetson backend (not flight confirmation)')
            except (ValueError, AttributeError):
                pass
            if isinstance(reply, dict) and reply.get('direct'):
                target = pending.pop(str(reply.get('request_id')), None)
                if target is not None:
                    try:
                        await target.send(msg)
                    except Exception:
                        browsers["cmd"].discard(target)
                continue
            dead = set()
            for b in list(browsers["cmd"]):
                try:
                    await b.send(msg)
                except Exception:
                    dead.add(b)
            browsers["cmd"] -= dead
    finally:
        if jetson["cmd"] is ws:
            jetson["cmd"] = None
            JOURNAL.append("AIRCRAFT COMMAND CHANNEL DISCONNECTED", "relay")
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
            try:
                command = json.loads(msg)
                action = str(command.get('action', 'unknown'))[:80]
            except (ValueError, AttributeError):
                await ws.send('{"ok":false,"msg":"Invalid command JSON"}')
                continue
            quiet = action in QUIET_ACTIONS
            remember(command.get('request_id'), ws)
            if not quiet:
                JOURNAL.append('OPERATOR REQUEST: ' + action.upper(), 'operator command')
            jws = jetson["cmd"]
            if jws is None:
                if not quiet:
                    JOURNAL.append("COMMAND FAILED: Jetson command channel offline", "relay")
                try:
                    await ws.send('{"ok":false,"msg":"Jetson command channel offline"}')
                except Exception:
                    pass
                continue
            try:
                await jws.send(msg)
                if not quiet:
                    JOURNAL.append("COMMAND FORWARDED: " + action.upper(), "relay (not flight confirmation)")
            except Exception:
                JOURNAL.append("COMMAND FAILED: Failed to reach Jetson", "relay")
                try:
                    await ws.send('{"ok":false,"msg":"Failed to reach Jetson"}')
                except Exception:
                    pass
    finally:
        browsers["cmd"].discard(ws)
        print(f"[RELAY] Browser cmd disconnected ({len(browsers['cmd'])} remaining)")

async def events_browser(ws):
    """Authenticated readers share one journal; viewer sessions cannot append."""
    try:
        auth = json.loads(await asyncio.wait_for(ws.recv(), 8))
        token = auth.get('token', '')
        role = token_role(token, SECRET) if SECRET else None
        if auth.get('type') != 'events_auth' or role not in ('operator', 'viewer'):
            await ws.close(code=1008, reason='Console session required')
            return
        cursor = 0
        while True:
            if token_role(token, SECRET) not in ('operator', 'viewer'):
                await ws.close(code=1008, reason='Session expired')
                return
            rows = JOURNAL.rows(cursor)
            await ws.send(json.dumps({'type': 'events', 'events': rows}))
            if rows:
                cursor = rows[-1]['id']
                if len(rows) == 500:
                    continue
            try:
                note = json.loads(await asyncio.wait_for(ws.recv(), .4))
                if role == 'operator' and note.get('type') == 'operator_note':
                    ident = str(note.get('id', ''))[:100]
                    if ident:
                        JOURNAL.append('OPERATOR NOTE: ' + str(note.get('text', ''))[:500], 'operator console', 'note:' + ident)
            except asyncio.TimeoutError:
                pass
    except (websockets.exceptions.ConnectionClosed, ValueError, TypeError):
        pass

# ── Main ──────────────────────────────────────────────────────────────────────
async def main():
    jetson_srv   = await websockets.serve(telem_jetson,  "0.0.0.0", 9001, ping_interval=None)
    browser_srv  = await websockets.serve(telem_browser, "0.0.0.0", 9101, ping_interval=None)
    cmd_jet_srv  = await websockets.serve(cmd_jetson,    "0.0.0.0", 9004, ping_interval=None)
    cmd_brw_srv  = await websockets.serve(cmd_browser,   "0.0.0.0", 9104, ping_interval=None)
    events_srv = await websockets.serve(events_browser, "127.0.0.1", 9105, ping_interval=20)
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
        events_srv.wait_closed(),
    )

if __name__ == "__main__":
    asyncio.run(main())
