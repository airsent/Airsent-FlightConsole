"""Check the relay's aircraft-facing connection hygiene. Run on the VPS:

    AIRSENT_EVENT_DB=/tmp/relay-test.sqlite3 python3 tests/relay_connection_hygiene.py

Covers the three things that were wrong on 2026-11-15:
  1. a non-websocket probe raised InvalidUpgrade and logged a traceback (120/day),
  2. a reconnecting Jetson left its old socket open and nothing closed it,
  3. with ping_interval=None the relay never reaped an aircraft that vanished —
     seven live sockets on 9001 for one aircraft after 57 days of uptime.

Uses loopback ports in the 19000s so it never touches the live relay.
"""
import asyncio
import os
import socket
import sys
import urllib.error
import urllib.request

os.environ.setdefault("AIRSENT_EVENT_DB", "/tmp/relay-test.sqlite3")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "deploy"))
import websockets
import relay

FAILED = []


def check(ok, label):
    print(("  PASS  " if ok else "  FAIL  ") + label)
    if not ok:
        FAILED.append(label)


async def test_rejects_probe():
    """A scanner's plain GET gets 426, not a traceback."""
    server = await websockets.serve(relay.telem_jetson, "127.0.0.1", 19001,
                                    ping_interval=None, process_request=relay.reject_non_websocket)
    try:
        # In a thread: urlopen blocks, and the server shares this event loop.
        def probe():
            try:
                urllib.request.urlopen("http://127.0.0.1:19001/", timeout=5)
                return None
            except urllib.error.HTTPError as e:
                return e.code

        status = await asyncio.to_thread(probe)
        check(status == 426, f"plain HTTP probe answered {status} (want 426 Upgrade Required)")

        # and a real websocket still gets through on the same port
        async with websockets.connect("ws://127.0.0.1:19001") as ws:
            check(ws.state.name == "OPEN", "a genuine websocket still connects on that port")
    finally:
        server.close()
        await server.wait_closed()


async def test_supersedes_old_connection():
    """A second aircraft connection hangs up the first instead of leaking it."""
    relay.jetson["telem"] = None
    server = await websockets.serve(relay.telem_jetson, "127.0.0.1", 19002, ping_interval=None)
    try:
        first = await websockets.connect("ws://127.0.0.1:19002")
        await asyncio.sleep(0.2)
        second = await websockets.connect("ws://127.0.0.1:19002")
        await asyncio.sleep(0.5)

        closed = False
        try:
            await asyncio.wait_for(first.recv(), timeout=3)
        except websockets.exceptions.ConnectionClosed:
            closed = True
        except asyncio.TimeoutError:
            closed = False
        check(closed, "the superseded connection was closed by the relay")
        check(relay.jetson["telem"] is not None, "the newest connection is the live one")

        await second.close()
        try:
            await first.close()
        except Exception:
            pass
    finally:
        server.close()
        await server.wait_closed()


async def test_reaps_silent_peer():
    """An aircraft that stops answering pings is dropped.

    Same mechanism as production, with a 1 s interval so the test is quick;
    production uses 20 s/20 s.
    """
    relay.jetson["telem"] = None
    server = await websockets.serve(relay.telem_jetson, "127.0.0.1", 19003,
                                    ping_interval=1, ping_timeout=1)
    try:
        # Raw socket: completes the handshake, then goes silent — never pongs.
        # Blocking calls go in a thread so the server's loop keeps running.
        def shake():
            s = socket.create_connection(("127.0.0.1", 19003), timeout=5)
            s.send(b"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\nUpgrade: websocket\r\n"
                   b"Connection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
                   b"Sec-WebSocket-Version: 13\r\n\r\n")
            return s, s.recv(1024)

        raw, handshake = await asyncio.to_thread(shake)
        check(b"101" in handshake, "silent peer completed the websocket handshake")

        # Ping timeout fires at ~2 s, but the relay then waits on a close handshake
        # the dead peer never completes, so the slot clears a few seconds later.
        # Poll rather than guess; production (20 s/20 s) takes roughly a minute.
        deadline = asyncio.get_event_loop().time() + 20
        while relay.jetson["telem"] is not None and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.25)
        check(relay.jetson["telem"] is None,
              "relay reaped the peer that stopped answering pings")
        raw.close()
    finally:
        server.close()
        await server.wait_closed()


async def main():
    print("relay connection hygiene")
    for t in (test_rejects_probe, test_supersedes_old_connection, test_reaps_silent_peer):
        print(f"\n{t.__name__}:")
        await t()
    print()
    if FAILED:
        print(f"FAIL: {len(FAILED)} check(s) failed")
        sys.exit(1)
    print("PASS: probes rejected cleanly, superseded sockets closed, silent peers reaped")


if __name__ == "__main__":
    asyncio.run(main())
