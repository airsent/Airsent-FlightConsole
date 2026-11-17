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

## Modem bands and self-interference

`deploy/modem_bands.py` excludes the 2.3-2.7 GHz TDD bands (38, 40, 41) on both NR and LTE. The setting lives in the modem's NV memory, so it is run once per modem, not per boot. `--revert --apply` puts them back.

Measured on 2026-11-16, same sky, over a few hours:

| Modem | USB | Serving band | GPS satellites |
|---|---|---|---|
| Off the USB bus | — | — | **26-30** |
| Attached, transmitting | **3.0 (SuperSpeed)** | n41 (2.5 GHz) | **3-8** |
| Attached, idle | 2.0 | n25 (1.9 GHz) | 21-25 |
| **Attached, transmitting on cellular** | **2.0** | **n71 (600 MHz)** | **23-26, and RTK FIX** |

**It works now: RTK FIX on 5G, with no shielding and no extra antenna separation.**

**Three things changed between the first row and the last, and this data cannot rank them.** Do not read the table as proof that any one of them was the cause:

1. **USB 3.0 -> USB 2.0** (`AT+QCFG="usbspeed","20"`, and the modem moved to the 480 Mbps bus). USB 3.0's 5 Gbps signalling radiates broadband noise centred around 2.4-2.5 GHz and is a documented GNSS and WiFi offender — it needs no transmitting at all to do harm.
2. **Bands 38/40/41 excluded** by this script.
3. **The serving band became n71** (600 MHz). Its uplink is 663-698 MHz, whose second harmonic (1326-1396 MHz) falls below L1 at 1575.42 and misses it, and which is nowhere near 2.4 GHz WiFi. n71 is close to the friendliest band this modem can pick.

An earlier revision of this file concluded that band selection was irrelevant and only physical fixes could work. That was wrong: it extrapolated from an *idle* measurement to the transmitting case. The 21-25 idle row says nothing about what happens under load, and the test that mattered was done on the aircraft afterwards.

**So do not revert the band exclusion casually.** It may be load-bearing — n41 is the band that was present when GPS was dead. If you revert it to recover video throughput, re-check that RTK survives before flying.

What the change also bought:

- **Cellular got better**, because n25 FDD beats a congested n41 here: RSRP -83 -> -74 dBm, SINR 10 -> 19, 47 ms RTT at 0% loss.
- **WiFi stopped flapping.** n41 starts at 2496 MHz, about 13 MHz above the top of 2.4 GHz WiFi, and the aircraft's WiFi is on channel 6 (2437 MHz). Under n41 the WiFi probe to the VPS was failing and `airsent-netlink` logged 8 switches; afterwards the probe runs 0% loss at 40 ms and the counter stopped climbing. A WiFi link at -52 dBm negotiating MCS 3 is the signature — strong signal, collapsed rate, so the loss is interference and not range.

This also explains an asymmetry that went unexplained for a day: 110 telemetry drops in 103 minutes on 2.4 GHz WiFi against 3 in 78 minutes on 5 GHz. Prefer 5 GHz for the ground WiFi; it is free and it sits far from every band this modem uses.

**The cost, and it is now the live question:** n41 is T-Mobile's capacity layer, and the modem has settled on n71 — a 600 MHz low band with excellent reach and little capacity. Expect poor uplink throughput, which is exactly what the video stream needs. Measure the uplink and lower the video ceiling to match, and treat "RTK or video bitrate" as a real trade on this airframe until the GNSS feed is filtered and the antennas are separated. Those physical fixes are what would let the modem use a fast band again.

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
