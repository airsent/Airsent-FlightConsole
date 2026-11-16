"""Install the reverse SSH tunnel on the Jetson, so the aircraft is reachable from anywhere.

    sudo python3 deploy/install_reverse_tunnel.py

The Jetson flies behind carrier NAT on cellular: nothing can open a connection *to* it.
This holds an outbound SSH connection to the VPS that forwards the VPS's 127.0.0.1:2222
back to the Jetson's own port 22, so `ssh -J root@VPS -p 2222 airsent@127.0.0.1` reaches it.

Prints the public key to authorize on the VPS. The tunnel retries until that is done.
See REMOTE_ACCESS.md.
"""
from pathlib import Path
import os, subprocess, sys

VPS = os.environ.get('AIRSENT_VPS_IP', '146.190.50.189')
PORT = os.environ.get('AIRSENT_TUNNEL_PORT', '2222')
KEY = Path('/etc/airsent/tunnel_key')

if os.geteuid() != 0:
    sys.exit('Run as root: sudo python3 deploy/install_reverse_tunnel.py')
if Path('/etc/hostname').read_text().strip() == 'Console-sfo3':
    sys.exit('This is the VPS — the tunnel is installed on the Jetson.')

# autossh restarts the tunnel when the link drops; plain ssh would die on the first wifi→cellular handoff.
if subprocess.run(['which', 'autossh'], capture_output=True).returncode != 0:
    print('installing autossh…')
    subprocess.run(['apt-get', 'install', '-y', 'autossh'], check=True)

KEY.parent.mkdir(parents=True, exist_ok=True)
if not KEY.exists():
    subprocess.run(['ssh-keygen', '-t', 'ed25519', '-N', '', '-f', str(KEY),
                    '-C', 'airsent-jetson-tunnel'], check=True)
    print(f'generated {KEY}')
KEY.chmod(0o600)

Path('/etc/systemd/system/airsent-tunnel.service').write_text(f'''[Unit]
Description=Airsent reverse SSH tunnel (makes this Jetson reachable through the VPS)
After=network-online.target airsent-netlink.service
Wants=network-online.target

[Service]
Type=simple
# 0 = restart even when the very first connection fails (no link yet at boot).
Environment=AUTOSSH_GATETIME=0
ExecStart=/usr/bin/autossh -M 0 -N \\
  -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \\
  -o ExitOnForwardFailure=yes -o StrictHostKeyChecking=accept-new \\
  -o UserKnownHostsFile=/etc/airsent/tunnel_known_hosts \\
  -o IdentitiesOnly=yes -i {KEY} \\
  -R 127.0.0.1:{PORT}:127.0.0.1:22 tunnel@{VPS}
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
''')
subprocess.run(['systemctl', 'daemon-reload'], check=True)
subprocess.run(['systemctl', 'enable', '--now', 'airsent-tunnel'], check=True)

pub = Path(str(KEY) + '.pub').read_text().strip()
print(f'''
airsent-tunnel installed and started. It will keep retrying until the key below is authorized.

Authorize it — paste this ON YOUR MAC, all of it, including the two EOF lines:

ssh root@{VPS} 'cat >> /home/tunnel/.ssh/authorized_keys' <<'EOF'
restrict,port-forwarding,permitlisten="127.0.0.1:{PORT}" {pub}
EOF

Then reach this Jetson from anywhere:

  ssh -J root@{VPS} -p {PORT} airsent@127.0.0.1

Check it: systemctl status airsent-tunnel · journalctl -u airsent-tunnel -f
''')
