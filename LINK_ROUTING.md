# Link routing

Two services on the Jetson decide which interface traffic leaves by. They were written independently, they do not overlap on the same route, and only one of them was in version control before 2026-11-15.

## airsent-netlink — the VPS link

`netlink.py`, installed as `/usr/local/bin/airsent-netlink` by `deploy/deploy_jetson.sh`, unit `deploy/airsent-netlink.service`.

Manages exactly one route — a **host route to the VPS**:

```
146.190.50.189/32 via <gateway> dev <active link> metric 50
```

WiFi when it works, cellular when it does not: WiFi → cellular after 3 consecutive failed probes, cellular → WiFi after 10 consecutive good ones (the WiFi threshold grows when WiFi flaps). The operator can pin a link from the console by writing `/run/airsent-netlink/prefer`. State is published to `/run/airsent-netlink/status.json`; cellular usage is counted in `/var/lib/airsent-netlink/usage.json` against `AIRSENT_CELL_PLAN_GB` (default 5).

It detects cellular through ModemManager, or — since `deploy_jetson.sh` disables ModemManager for the RM520N in ECM mode — by finding the interface itself.

On every real switch it also calls `redial_tunnel()`, which restarts `airsent-tunnel` so remote SSH follows the new link instead of waiting for `autossh` to time out. An abandoned tunnel socket whose old interface is still up keeps answering the VPS's keepalive probes, so it is never reaped and holds `127.0.0.1:2222` against every retry — one measured at 21 minutes. The restart is issued with `systemctl --no-block` and a 5 s cap, because this runs in the main loop and must never delay a route decision in flight. See REMOTE_ACCESS.md, "VPS side".

## airsent-route — the default route

`deploy/airsent-route.sh`, installed at `/usr/local/bin/airsent-route.sh`, unit `deploy/airsent-route.service`. Added to this repo on 2026-11-15; before that it existed only on the aircraft's filesystem and would have been lost in a reflash.

Every 5 seconds it takes the first `enx*` interface, pings `1.1.1.1` through it, and on success installs

```
default via <gateway> dev <enx*> metric 50
```

removing it again when the ping fails. The gateway is read from the interface's existing default route, falling back to a hardcoded `192.168.225.1`.

## How they interact

They do **not** fight. A `/32` host route is more specific than a default route, so traffic to the VPS always follows `airsent-netlink`'s choice no matter what `airsent-route` does. Telemetry, commands and the reverse tunnel are all VPS-bound and so are governed by the link manager alone.

What `airsent-route` governs is **everything else**. While the modem is attached and answering, all non-VPS traffic leaves over cellular even when WiFi is healthy — DNS, NTP, `apt`, Tailscale, GitHub, any fetch from the Jetson.

Two consequences worth deciding about deliberately:

- It is the opposite policy to the link manager's. `airsent-netlink` prefers WiFi; `airsent-route` prefers cellular. Each is coherent for the route it owns, but nothing states which policy the aircraft is supposed to have overall.
- That traffic is invisible to the data accounting. `airsent-netlink` counts cellular bytes against the plan for the link it manages; bytes `airsent-route` sends over the modem are not counted, so the 5 GB plan can be consumed without the console showing it.

Neither has been changed. Resolving it means choosing the intended policy first:

- If WiFi-first is the intent throughout, `airsent-route` should prefer WiFi as well, or be retired and the default route folded into `airsent-netlink`, which already knows which link is healthy.
- If cellular-default is deliberate — keeping the aircraft off a field WiFi it should not depend on — then the data accounting needs to see it.

## Intentional limits

`airsent-route` matches `enx*` only. A modem that enumerates as `wwan0` is invisible to it, though `airsent-netlink` would still find it.

Neither service manages the LAN. Addresses on a directly connected network are reached by the kernel's connected routes and are unaffected by both.

## Checks

```bash
ip route show default                      # what airsent-route has installed
ip route show 146.190.50.189               # what airsent-netlink has installed
cat /run/airsent-netlink/status.json       # active link, per-link signal
systemctl status airsent-route airsent-netlink
```
