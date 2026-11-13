#!/usr/bin/env python3
"""Flight 1 parameter setup — run on the Jetson (over SSH):

    python3 ~/Airsent-FlightConsole/deploy/flight1_setup.py          # show what it would change
    python3 ~/Airsent-FlightConsole/deploy/flight1_setup.py --apply  # write it and read it back

Talks to the running airsent-server on its local command port, so it uses the same checked,
read-back parameter write as the console's Parameters page (and is refused while armed).

  RTL_ALT_M        -> 20 m   (it was 105 m, above the 30 m fence; RTL_ALT in cm on older firmware)
  INS_LOG_BAT_MASK -> 1      (raw IMU logging for the vibration spectrum; needs a Pixhawk reboot)
"""
import argparse
import json
import sys

try:
    from websockets.sync.client import connect
except ImportError:
    sys.exit("python 'websockets' is missing: pip3 install websockets")

p = argparse.ArgumentParser()
p.add_argument('--apply', action='store_true')
p.add_argument('--url', default='ws://127.0.0.1:8767')
p.add_argument('--rtl-alt', type=float, default=20.0, help='metres (default 20)')
args = p.parse_args()

ws = connect(args.url, max_size=50_000_000)
n = [0]


def call(action, **data):
    n[0] += 1
    rid = f'setup{n[0]}'
    ws.send(json.dumps({'action': action, 'request_id': rid, 'console_id': 'flight1-setup', 'operator': 'ssh setup', **data}))
    while True:
        r = json.loads(ws.recv(timeout=40))
        if r.get('request_id') == rid:
            return r


def values(names):
    r = call('param_read', names=names)
    if not r.get('ok'):
        sys.exit(f"param_read failed: {r.get('msg')}")
    return {k: (v['value'] if isinstance(v, dict) else v) for k, v in (r.get('values') or {}).items()}


have = values(['RTL_ALT_M', 'RTL_ALT', 'INS_LOG_BAT_MASK', 'FENCE_ENABLE', 'FENCE_ALT_MAX'])
print('Pixhawk now:', json.dumps(have))
rtl_name = 'RTL_ALT_M' if 'RTL_ALT_M' in have else 'RTL_ALT'
if rtl_name not in have:
    sys.exit('Neither RTL_ALT_M nor RTL_ALT exists on this vehicle — not touching anything.')
rtl_val = args.rtl_alt if rtl_name == 'RTL_ALT_M' else args.rtl_alt * 100       # RTL_ALT is in centimetres
fence = have.get('FENCE_ALT_MAX')
if have.get('FENCE_ENABLE') and fence is not None and args.rtl_alt >= fence:
    sys.exit(f'RTL altitude {args.rtl_alt:g} m is not below the fence ({fence:g} m) — pick a lower one with --rtl-alt.')
want = {rtl_name: rtl_val, 'INS_LOG_BAT_MASK': 1}
todo = {k: v for k, v in want.items() if k not in have or abs(have[k] - v) > 1e-6}
print('Wanted:    ', json.dumps(want))
if not todo:
    print('Already set — nothing to do.')
    sys.exit(0)
print('Will change:', json.dumps({k: [have.get(k), v] for k, v in todo.items()}))
if not args.apply:
    print('\nDry run. Add --apply to write it (aircraft must be disarmed).')
    sys.exit(0)
r = call('param_write', params=todo)
print(r.get('msg'))
if not r.get('ok'):
    sys.exit(1)
back = values(list(todo))
ok = all(abs(back.get(k, -1e9) - v) < 1e-6 for k, v in todo.items())
print('Read back: ', json.dumps(back), '→', 'OK' if ok else 'MISMATCH')
if 'INS_LOG_BAT_MASK' in todo:
    print('\nINS_LOG_BAT_MASK only takes effect after a reboot: power-cycle the Pixhawk (and let the Jetson reconnect).')
sys.exit(0 if ok else 1)
