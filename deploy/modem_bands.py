"""Keep the 5G modem off the bands that deafen the aircraft's own receivers.

    sudo python3 deploy/modem_bands.py                 # dry run — shows what would change
    sudo python3 deploy/modem_bands.py --apply         # exclude the 2.3-2.7 GHz TDD bands
    sudo python3 deploy/modem_bands.py --revert --apply # put every band back

Why: on 2026-11-16 the rover held 29-30 satellites with the RM520N off the USB bus and
4-6 with it attached and transmitting, same sky, minutes apart. RTK was impossible on
cellular and the aircraft could not meet its own prearm GPS checks (AHRS_GPS_MINSATS=6,
GPS_HDOP_GOOD=1.4). WiFi went to 10% packet loss at the same moment.

The modem was serving on NR band n41. n41 is 2496-2690 MHz, which starts about 13 MHz
above the top of 2.4 GHz WiFi (2400-2483.5) — so it desenses the WiFi front end by sheer
adjacency. It also explains an asymmetry that went unexplained for a day: 110 telemetry
drops in 103 minutes on 2.4 GHz WiFi against 3 in 78 minutes on 5 GHz.

For GPS the mechanism is not a harmonic — n41 has no simple relationship to L1 at
1575.42 MHz. It is front-end overload: a ~200 mW transmitter tens of centimetres from an
antenna listening for -130 dBm satellites is about 150 dB of dynamic range, and no
practical filter in front of a wideband GNSS LNA keeps it out of compression.

So this excludes the whole 2.3-2.7 GHz TDD cluster, which is the part that sits on top of
WiFi: bands 38 (2570-2620), 40 (2300-2400) and 41 (2496-2690), on both NR and LTE.
Everything else stays enabled, so the modem still has T-Mobile's n71/n25 and LTE B2/B66
to fall back to and coverage is kept.

THE COST, stated plainly: n41/B41 is T-Mobile's capacity layer. Excluding it will reduce
uplink throughput, and the video stream is the thing that needs it. This trades video
bitrate for satellites. That is the right trade for an aircraft that has to navigate, but
it is a trade — measure the uplink afterwards and lower the video ceiling to match.

The setting lives in the modem's own NV memory and survives power cycles, so this is run
once per modem, not per boot. Ground use only: it forces a re-registration, which drops
the cellular link for a few seconds.
"""
import argparse
import importlib.util
import os
import sys
import time
from importlib.machinery import SourceFileLoader

# The AT helpers already live in the link manager, and they are careful: find_at_port()
# matches the Quectel vendor id and interface number, so it never writes to the ESC Data
# Link adapter, which is also a ttyUSB.
NETLINK = os.environ.get('AIRSENT_NETLINK_BIN', '/usr/local/bin/airsent-netlink')

# 2.3-2.7 GHz TDD. T-Mobile US does not deploy 38 or 40 domestically, so in practice this
# gives up band 41 and costs nothing else.
DROP = (38, 40, 41)


def load_netlink():
    if not os.path.exists(NETLINK):
        sys.exit(f'{NETLINK} not found — run deploy/deploy_jetson.sh first')
    loader = SourceFileLoader('nl', NETLINK)          # installed without a .py suffix
    spec = importlib.util.spec_from_loader('nl', loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def mask(nl, port, key):
    """Current band mask for 'nr5g_band' or 'lte_band', as a list of ints."""
    reply = nl.at_query(port, f'AT+QNWPREFCFG="{key}"', timeout=3.0)
    for line in reply.splitlines():
        if '+QNWPREFCFG:' in line and key in line:
            raw = line.split(',', 1)[1].strip()
            return [int(b) for b in raw.split(':') if b.strip().isdigit()]
    return []


def serving(nl, port):
    info = nl.at_query(port, 'AT+QCAINFO', timeout=3.0)
    for line in info.splitlines():
        if '+QCAINFO:' in line:
            return line.strip()
    return '(no serving cell reported)'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='write it; without this, dry run')
    ap.add_argument('--revert', action='store_true', help='re-enable every band')
    args = ap.parse_args()

    nl = load_netlink()
    port = nl.find_at_port()
    if not port:
        sys.exit('No modem AT port. Is the RM520N attached? (lsusb | grep 2c7c)')
    if not nl.at_query(port, 'AT', timeout=3.0):
        sys.exit(f'{port} did not answer AT — modem busy or wrong port')
    print(f'AT port:  {port}')
    print(f'Serving:  {serving(nl, port)}')

    plan = {}
    for key in ('nr5g_band', 'lte_band'):
        have = mask(nl, port, key)
        if not have:
            print(f'{key}: could not read the current mask — skipping')
            continue
        want = sorted(set(have) | set(DROP)) if args.revert else [b for b in have if b not in DROP]
        if want == have:
            print(f'{key}: already as wanted ({len(have)} bands) — nothing to do')
            continue
        if not want:
            sys.exit(f'{key}: refusing to write an empty band mask')
        plan[key] = want
        gone = sorted(set(have) - set(want))
        added = sorted(set(want) - set(have))
        print(f'{key}: {len(have)} -> {len(want)} bands'
              + (f' | removing {gone}' if gone else '')
              + (f' | restoring {added}' if added else ''))

    if not plan:
        return
    if not args.apply:
        print('\nDry run. Add --apply to write it. Ground use only: the modem re-registers '
              'and the cellular link drops for a few seconds.')
        return

    for key, want in plan.items():
        value = ':'.join(str(b) for b in want)
        reply = nl.at_query(port, f'AT+QNWPREFCFG="{key}",{value}', timeout=5.0)
        print(f'\nwrote {key}: {"OK" if "OK" in reply else reply.strip()[:200]}')

    # The mask is in NV immediately, but the modem has to re-select a cell to act on it.
    print('\nWaiting up to 90 s for it to re-register…', flush=True)
    end = time.time() + 90
    while time.time() < end:
        cell = serving(nl, port)
        if 'BAND' in cell:
            print(f'Serving now: {cell}')
            break
        time.sleep(5)
    else:
        print('Still no serving cell. If it does not come back, revert: '
              'sudo python3 deploy/modem_bands.py --revert --apply')

    for key in plan:
        print(f'Read back {key}: {mask(nl, port, key)}')
    print('\nNow measure what it cost: uplink throughput, and satellites with video running.')


if __name__ == '__main__':
    main()
