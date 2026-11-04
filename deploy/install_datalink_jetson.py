"""Install the read-only Datalink reader while preserving Jetson configuration.

Requires an explicit operator confirmation that motors are stopped, props are
removed and aircraft is disarmed. This script sends no vehicle commands.
"""
import argparse
import ast
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import urllib.request

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--ref', required=True, help='Immutable Git commit SHA')
parser.add_argument('--server', default='/home/airsent/airsent_server.py')
parser.add_argument('--confirm-disarmed', action='store_true')
args = parser.parse_args()
if not args.confirm_disarmed:
    parser.error('Stop motors, remove props, disarm, then pass --confirm-disarmed')
if not re.fullmatch(r'[0-9a-f]{7,40}', args.ref):
    parser.error('--ref must be a Git commit SHA')
server = Path(args.server).resolve()
root = server.parent
port = '/dev/serial/by-id/usb-Silicon_Labs_CP2102_USB_to_UART_Bridge_Controller_0001-if00-port0'
if not Path(port).exists():
    raise SystemExit('CP2102 adapter not present; no changes made')
base = f'https://raw.githubusercontent.com/airsent/Airsent-FlightConsole/{args.ref}/'
with urllib.request.urlopen(base+'datalink_telemetry.py', timeout=20) as response:
    module = response.read().decode()
ast.parse(module)
original = server.read_text()
updated = original
if 'from datalink_telemetry import start_monitor' not in updated:
    updated = updated.replace('import os\n', 'import os\nfrom datalink_telemetry import start_monitor\n', 1)
if "payload['esc_telemetry'] = esc_monitor.snapshot()" not in updated:
    marker = "    payload['mavlink_log'] = log"
    if marker not in updated:
        raise SystemExit('Unexpected backend layout; no changes made')
    updated = updated.replace(marker, marker+"\n    payload['esc_telemetry'] = esc_monitor.snapshot()", 1)
if 'esc_monitor = start_monitor()' not in updated:
    marker = 'if __name__ == "__main__":\n'
    if marker not in updated:
        raise SystemExit('Missing backend startup marker; no changes made')
    updated = updated.replace(marker, marker+
        f'    os.environ.setdefault("DATALINK_PORT", {port!r})\n'+
        '    os.environ.setdefault("DATALINK_MOTOR_POLES", "22")\n'+
        '    esc_monitor = start_monitor()\n', 1)
ast.parse(updated)
if 'from datalink_telemetry import start_monitor' not in updated:
    raise SystemExit('Could not attach reader; no changes made')
# Find only Python processes whose script argument is this exact backend file.
pids = []
for proc in Path('/proc').iterdir():
    if not proc.name.isdigit() or int(proc.name) == os.getpid():
        continue
    try:
        argv = proc.joinpath('cmdline').read_bytes().split(b'\0')
        argv = [a.decode() for a in argv if a]
        if not argv or 'python' not in Path(argv[0]).name:
            continue
        script = next((a for a in argv[1:] if not a.startswith('-')), '')
        cwd = proc.joinpath('cwd').resolve()
        if script and (cwd/script).resolve() == server:
            pids.append(int(proc.name))
    except (OSError, UnicodeError):
        pass
stamp = time.strftime('%Y%m%d-%H%M%S')
backup = root/f'airsent_server.py.backup-datalink-{stamp}'
backup.write_text(original)
module_path = root/'datalink_telemetry.py'
if module_path.exists():
    module_path.with_name(f'datalink_telemetry.py.backup-{stamp}').write_bytes(module_path.read_bytes())
for pid in pids:
    os.kill(pid, signal.SIGTERM)
for _ in range(50):
    if all(not Path('/proc', str(pid)).exists() for pid in pids):
        break
    time.sleep(.1)
else:
    raise SystemExit('Backend did not stop; files unchanged')
try:
    module_path.write_text(module)
    staging = server.with_suffix('.py.new')
    staging.write_text(updated)
    staging.replace(server)
    env = os.environ.copy()
    env['DATALINK_PORT'] = port
    env['DATALINK_MOTOR_POLES'] = '22'
    log_path = root/'airsent_server.log'
    with log_path.open('ab') as log:
        process = subprocess.Popen([sys.executable, '-u', str(server)], cwd=root,
            env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True)
    time.sleep(2)
    if process.poll() is not None:
        raise RuntimeError('Backend exited; inspect '+str(log_path))
    print('Installed. Backend PID:', process.pid)
    print('Backup:', backup)
    print('Serial:', port, '@ 115200; motor poles: 22')
    print('No motor commands were sent. Hardware motor test remains pending.')
except Exception:
    server.write_text(original)
    print('Restored original backend file. Check backend log before restarting.', file=sys.stderr)
    raise
