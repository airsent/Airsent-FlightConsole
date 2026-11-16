# Remote access

Reaching the VPS and the Jetson from a laptop, for diagnosis and deploys.

## VPS

`root@146.190.50.189`, hostname `Console-sfo3`, Ubuntu 24.04, OpenSSH 9.6. Key authentication; it has a public address, so nothing special is needed:

```bash
ssh root@146.190.50.189
```

Services there: `airsent-relay`, `airsent-presence`, `airsent-sequence`, `airsent-shared-state`, `airsent-simulation-backend`, `airsent-simulation-gateway`, `airsent-sitl`, plus `nginx` and `mediamtx`. Deploy with `deploy/deploy_vps.sh`, run on the VPS itself.

## Jetson

Hostname `airsent-desktop`, user `airsent`. Three ways in, in order of when each works:

| Path | When it works |
|---|---|
| `ssh airsent@192.168.55.1` | USB-C cable to the laptop (the NVIDIA link-local network) |
| `ssh airsent@<lan-ip>` | Same wifi network; the address comes from DHCP and changes |
| `ssh -J root@146.190.50.189 -p 2222 airsent@127.0.0.1` | Anywhere, via the reverse tunnel below |

### Why a reverse tunnel

In flight the Jetson is on cellular, behind carrier-grade NAT. It has no public address and nothing can open a connection *to* it — the telemetry you see in the console is the Jetson pushing *outbound* to `ws://146.190.50.189:9001`. Inbound SSH is impossible on that path.

`airsent-tunnel.service` keeps an outbound SSH connection open to the VPS that forwards the VPS's `127.0.0.1:2222` back to the Jetson's own port 22. Because the Jetson dials out, NAT is irrelevant. `-J` (ProxyJump) then hops through the VPS to that forwarded port.

`autossh`, not plain `ssh`: the link drops on every wifi→cellular handoff, and plain `ssh` would die there and stay dead.

### Install

On the Jetson, from the repo:

```bash
sudo python3 deploy/install_reverse_tunnel.py
```

It installs `autossh`, generates `/etc/airsent/tunnel_key`, writes and starts `airsent-tunnel.service`, then prints the public key with the exact command to authorize it. The service retries every 10 s until that key is authorized, so install order does not matter.

### VPS side

Already provisioned:

- **User `tunnel`** — system user, shell `/usr/sbin/nologin`, exists only to hold the forwarded port.
- **`/etc/ssh/sshd_config.d/70-airsent-tunnel.conf`** — `ClientAliveInterval 30`, `ClientAliveCountMax 3`, `TCPKeepAlive yes`. Without these a dead tunnel leaves `127.0.0.1:2222` bound and the Jetson cannot re-bind when it comes back; `ExitOnForwardFailure=yes` on the Jetson side then makes the retry fail forever.

  No `Match` block, deliberately. `Include /etc/ssh/sshd_config.d/*.conf` sits at line 12 of the main `sshd_config`, so a `Match` in a drop-in would capture every directive below it in the main file.

- **`/home/tunnel/.ssh/authorized_keys`** — one line per aircraft, restrictions in the key options rather than in `sshd_config`:

  ```
  restrict,port-forwarding,permitlisten="127.0.0.1:2222" ssh-ed25519 AAAA… airsent-jetson-tunnel
  ```

  `restrict` turns off everything (pty, agent forwarding, X11, user-rc); `port-forwarding` re-enables only forwarding; `permitlisten` allows binding that one port and nothing else. The key cannot run a command or open a shell on the VPS.

### Checking it

```bash
# on the Jetson
systemctl status airsent-tunnel
journalctl -u airsent-tunnel -f

# on the VPS — is the aircraft currently connected?
ss -tlnp | grep 2222        # bound = tunnel up
```

## Intentional limits

The tunnel carries SSH only — one forwarded port, 22 on the Jetson. It is not a VPN and not a telemetry path: telemetry keeps going over the existing `ws://146.190.50.189:9001` relay, which is unaffected.

Port `2222` holds exactly one aircraft. A second Jetson needs its own port and its own `permitlisten`; set `AIRSENT_TUNNEL_PORT` when installing.

The Jetson is only reachable while it has a link. Nothing here reaches an aircraft that is powered off or out of coverage, and the tunnel does not come up before `network-online.target`.

Reaching the Jetson is not the same as commanding the aircraft. Arming and flight commands go through the console and the Pixhawk, not through this path.
