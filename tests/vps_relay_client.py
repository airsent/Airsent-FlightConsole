"""Exercise the aircraft's VPS telemetry relay client against a stand-in VPS.

    python3 tests/vps_relay_client.py            # needs SITL on tcp:127.0.0.1:5760

`--sim` deliberately disables the VPS relays (see SIMULATION.md, "Backend
isolation"), so the SITL rig never runs `vps_telem_relay()` — the code that carries
telemetry to the ground in flight has no coverage from the normal gate. This fills
that gap without needing an aircraft or the production relay.

It checks that the relay connects with the configured keepalive, streams telemetry
at the expected rate, and — the branch that only runs when something has already
gone wrong — reports uptime, link, message count and slowest send when the link
dies.

Start SITL first:  cd ~/airsent-sitl && docker compose up -d
"""
import asyncio
import os
import sys

sys.argv = ["airsent_server.py", "--sim"]
HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("VPS_TELEM_URL", "ws://127.0.0.1:19001")
sys.path.insert(0, os.path.dirname(HERE))

import websockets                                                   # noqa: E402
import airsent_server as srv                                        # noqa: E402

DROP_AFTER = 12
FAILED = []
seen = {"msgs": 0, "conns": 0}


def check(ok, label):
    print(("  PASS  " if ok else "  FAIL  ") + label)
    if not ok:
        FAILED.append(label)


class Stub:
    """Stands in for hardware objects that only exist on the aircraft.

    build_telemetry_payload() reaches for the ESC monitor, which is created only
    when real hardware is attached. Without it the send loop cannot run at all.
    """
    def __getattr__(self, name): return Stub()
    def __call__(self, *a, **k): return Stub()
    def __bool__(self): return False
    def __iter__(self): return iter(())
    def __float__(self): return 0.0
    def __int__(self): return 0


async def stand_in(ws):
    """Accept telemetry, then sever the link so the error path runs."""
    seen["conns"] += 1
    first = seen["conns"] == 1

    async def reader():
        async for _ in ws:
            seen["msgs"] += 1

    task = asyncio.create_task(reader())
    if first:
        await asyncio.sleep(DROP_AFTER)
        await ws.close(1011, "simulated link loss")
    else:
        await asyncio.sleep(30)
    task.cancel()


async def main():
    print("vps relay client\n")
    check(srv.WS_PING_INTERVAL == 10, f"ping interval is {srv.WS_PING_INTERVAL}s (want 10)")
    check(srv.WS_PING_TIMEOUT == 30, f"ping timeout is {srv.WS_PING_TIMEOUT}s (want 30)")

    for name in ("esc_monitor",):
        if not hasattr(srv, name):
            setattr(srv, name, Stub())

    lines = []
    real_print = print

    import builtins
    builtins.print = lambda *a, **k: (lines.append(" ".join(str(x) for x in a)), real_print(*a, **k))[1]
    try:
        server = await websockets.serve(stand_in, "127.0.0.1", 19001, ping_interval=None)
        task = asyncio.create_task(srv.vps_telem_relay())
        await asyncio.sleep(DROP_AFTER + 8)
        task.cancel()
        server.close()
        await server.wait_closed()
    finally:
        builtins.print = real_print

    connected = [l for l in lines if "Telemetry relay connected" in l]
    errors = [l for l in lines if "Relay error after" in l]

    check(bool(connected), "relay connected to the stand-in VPS")
    check(seen["msgs"] > 100, f"streamed telemetry ({seen['msgs']} messages in {DROP_AFTER}s)")
    check(bool(errors), "the drop produced a diagnostic line")
    if errors:
        line = errors[0]
        check("msgs sent" in line and "slowest send" in line,
              "the line reports message count and slowest send")
        check(f"{DROP_AFTER}s up" in line,
              f"the line reports uptime accurately ({line.split(',')[0].split('after')[-1].strip()})")
        check(len(connected) > 1, "relay reconnected after the drop")

    print()
    if FAILED:
        print(f"FAIL: {len(FAILED)} check(s) failed")
        sys.exit(1)
    print("PASS: keepalive applied, telemetry streamed, drop diagnosed, relay reconnected")


if __name__ == "__main__":
    asyncio.run(main())
