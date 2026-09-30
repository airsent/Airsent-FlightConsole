# Map fix and a new DigitalOcean Droplet

The dashboard and mission planner now share the CARTO dark tile source. The mission planner previously used Stadia tiles without production authentication. Edit console-config.js to choose a tile provider and its required attribution; check your provider's terms for production use. Stadia can also be used after registering your domain: https://docs.stadiamaps.com/authentication/ . Tiles load directly from the provider, not from the Droplet. CARTO now requires a basemaps API key; configure it before using this tile source.

The dashboard now resizes the correct Leaflet instance after switching back from video. Telemetry rejects invalid coordinates while allowing valid locations on the equator and prime meridian. The all-zero coordinate remains the application's no-fix sentinel.

## Deploy the website and live map telemetry

Use a new Ubuntu 24.04 Droplet and an SSH key. Replace DROPLET_IP and console.example.com below with your actual values. Set a DNS A record for your console subdomain to DROPLET_IP. Remove any conflicting AAAA record unless you configured IPv6.

From your Mac, in this project folder:

```sh
scp -r . root@DROPLET_IP:/opt/airsent
ssh root@DROPLET_IP
```

On the Droplet:

```sh
apt update
apt install -y nginx python3-venv certbot python3-certbot-nginx
useradd --system --home /opt/airsent --shell /usr/sbin/nologin airsent
python3 -m venv /opt/airsent/.venv
/opt/airsent/.venv/bin/pip install -r /opt/airsent/deploy/requirements.txt
chown -R airsent:airsent /opt/airsent
cp /opt/airsent/deploy/airsent-*.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now airsent-telemetry airsent-presence
```

Edit /opt/airsent/deploy/nginx.conf: replace console.example.com with your domain. Then:

```sh
cp /opt/airsent/deploy/nginx.conf /etc/nginx/sites-available/airsent
ln -s /etc/nginx/sites-available/airsent /etc/nginx/sites-enabled/airsent
nginx -t
systemctl reload nginx
certbot --nginx -d console.example.com
```

In the DigitalOcean firewall allow inbound TCP 80/443; restrict SSH (22) to your own and the Jetson's trusted access addresses, or use a private VPN for SSH. No public Python relay ports are needed. Open https://console.example.com/ and select the real/viewer dashboard to see live telemetry. Demo mode deliberately uses simulated positions. apiBase defaults to the website's origin, so a new domain requires no JavaScript URL edits. Serve through HTTP/HTTPS, not file://.

## Connect the drone / Jetson to the new Droplet

airsent_server.py stays on the Jetson with its existing DepthAI, MAVLink, OpenCV, camera and model dependencies. It does not run on a generic Droplet. The cloud only serves the UI and relays telemetry.

On the Jetson, use an SSH tunnel with a restricted SSH account configured for local TCP forwarding on the Droplet (use your actual SSH user):

```sh
ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -L 19001:127.0.0.1:9001 SSH_USER@DROPLET_IP
```

Keep the tunnel running; in another Jetson terminal:

```sh
export VPS_TELEM_URL=ws://127.0.0.1:19001
python3 airsent_server.py
```

Use a systemd service for the SSH tunnel and existing Jetson process if you want automatic startup/reconnect. A private VPN is another option. Do not expose the unauthenticated drone input port 9001 to the public internet.

## What is and is not included

This setup supports website, presence and live map telemetry. The uploaded ZIP does not contain the VPS command relay expected on ports 9004/9104, or a MediaMTX video deployment. Consequently remote ARM/control and cameras require those additional services; /cmd and /video are intentionally not exposed by this example. The Jetson command relay will retry until configured. VPS_CMD_URL and RTMP_HOST are now environment variables for your eventual tunnel/VPN endpoints. Existing preflight, launch and logs pages still target their separate localhost:8765 protocol; those aren't migrated by this map fix.

Do not open command ports publicly: UI operator/viewer selection is not server-side authorization. Configure authenticated operator access before enabling remote control. Protect private telemetry/UI with a VPN or Nginx authentication if required.

## Verify and troubleshoot

```sh
systemctl status airsent-telemetry airsent-presence
journalctl -u airsent-telemetry -n 50 --no-pager
```

The telemetry service should log a Jetson connection and a browser connection. In browser developer tools, /telem should upgrade with HTTP 101 and receive JSON containing lat/lon. No location movement usually means the Jetson isn't connected or lacks a GPS fix. Missing tiles show an on-map error; inspect tile requests and provider configuration. Reload after changing console-config.js.

Reference: https://www.digitalocean.com/community/tutorials/how-to-configure-buildbot-with-ssl-using-an-nginx-reverse-proxy (WebSocket proxy pattern); https://websockets.readthedocs.io/en/stable/reference/asyncio/server.html .

## Existing console deployment

The configured deployment at console.airsent.tech uses 146.190.50.189 and reuses its existing airsent-relay and MediaMTX services. The standalone example above is for a fresh Droplet; do not start a second telemetry service on occupied ports. This public repository uses YOUR_CARTO_BASEMAP_KEY as a placeholder. Configure console-config.js with your own restricted key before deploying; do not overwrite the configured server copy with the placeholder.
