from pathlib import Path
import subprocess
subprocess.run(['install','-d','-o','airsent','-g','airsent','/var/lib/airsent-shared'],check=True)
Path('/etc/systemd/system/airsent-shared-state.service').write_text('''[Unit]
Description=Airsent shared console settings
After=network.target
[Service]
User=airsent
WorkingDirectory=/opt/airsent
EnvironmentFile=/etc/airsent-presence.env
ExecStart=/usr/bin/python3 /opt/airsent/shared_state_server.py
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/airsent-shared
[Install]
WantedBy=multi-user.target
''')
p=Path('/etc/nginx/sites-enabled/default');s=p.read_text()
if 'location = /shared-state' not in s:
 Path('/root/nginx.before-shared-settings').write_text(s)
 s=s.replace('location = /sequence {','''location = /shared-state {
        proxy_pass http://127.0.0.1:9203;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 86400;
    }
    location = /sequence {''');p.write_text(s)
subprocess.run(['nginx','-t'],check=True)
subprocess.run(['systemctl','daemon-reload'],check=True)
subprocess.run(['systemctl','enable','--now','airsent-shared-state'],check=True)
subprocess.run(['systemctl','reload','nginx'],check=True)
