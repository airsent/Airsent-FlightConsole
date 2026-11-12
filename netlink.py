#!/usr/bin/env python3
"""Airsent link manager: WiFi when it works, cellular when it doesn't.

Runs as root (systemd airsent-netlink). Every second it checks, separately on each link,
whether the VPS answers (ping bound to that interface). It then steers ONLY the drone's
traffic to the VPS with one host route:

    146.190.50.189/32 via <gateway> dev <active link> metric 50

Everything else keeps NetworkManager's normal routes, so if this service dies the Jetson
falls back to plain NetworkManager behaviour (WiFi first) — it can't strand the drone.

Rules (hysteresis so it doesn't flap):
  • WiFi → cellular after FAIL_TO_SWITCH failed probes in a row on WiFi (default 3 s),
    and only if cellular is answering.
  • cellular → WiFi after BACK_TO_WIFI good probes in a row on WiFi (default 10 s).
  • The operator can force a link from the console (writes /run/airsent-netlink/prefer).

Writes /run/airsent-netlink/status.json (read by airsent_server.py) and keeps a monthly
cellular data counter in /var/lib/airsent-netlink/usage.json.
"""
import json
import os
import re
import subprocess
import sys
import time

VPS = os.environ.get('AIRSENT_VPS_IP', '146.190.50.189')
RUN_DIR = os.environ.get('AIRSENT_NETLINK_RUN', '/run/airsent-netlink')
STATE_DIR = os.environ.get('AIRSENT_NETLINK_STATE', '/var/lib/airsent-netlink')
FAIL_TO_SWITCH = int(os.environ.get('AIRSENT_NET_FAIL_TO_SWITCH', '3'))
BACK_TO_WIFI = int(os.environ.get('AIRSENT_NET_BACK_TO_WIFI', '10'))
WIFI_MAX_LOSS = float(os.environ.get('AIRSENT_NET_WIFI_MAX_LOSS', '10'))   # % over the last 30 s to count as good again
FLAP_WINDOW = 120.0          # WiFi failing again this soon after we went back to it = flapping
BACKOFF_MAX = 300            # longest WiFi must prove itself before we return to it
CELL_IFACE = os.environ.get('AIRSENT_CELL_IFACE', '')          # override auto-detection
WIFI_IFACE = os.environ.get('AIRSENT_WIFI_IFACE', '')
PLAN_GB = float(os.environ.get('AIRSENT_CELL_PLAN_GB', '5'))         # defaults; plan.json (set with --plan) wins
BILL_DAY = int(os.environ.get('AIRSENT_CELL_BILL_DAY', '1'))
MODEM_AT = os.environ.get('AIRSENT_MODEM_AT', '')                     # override the modem's AT port
MODEM_DRIVERS = {'qmi_wwan', 'cdc_mbim', 'cdc_ether', 'cdc_ncm', 'rndis_host', 'option', 'mhi_net', 'mhi_wwan_mbim', 'ipheth'}
QUECTEL_VENDOR = '2c7c'
METRIC = 50


def sh(args, timeout=3):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return 1, ''


# ── discovering the links ──────────────────────────────────────────────────
def _driver(iface):
    try:
        return os.path.basename(os.readlink(f'/sys/class/net/{iface}/device/driver'))
    except OSError:
        return ''


def _usb_vendor(iface):
    path = os.path.realpath(f'/sys/class/net/{iface}/device')
    for _ in range(4):                       # walk up to the USB device node
        try:
            return open(os.path.join(path, 'idVendor')).read().strip()
        except OSError:
            path = os.path.dirname(path)
    return ''


def find_links():
    """{'wifi': iface|None, 'cellular': iface|None}"""
    links = {'wifi': None, 'cellular': None}
    _, out = sh(['nmcli', '-t', '-f', 'DEVICE,TYPE,STATE', 'device'])
    for row in out.splitlines():
        dev, kind, state = (row.split(':') + ['', ''])[:3]
        if kind == 'wifi' and state.startswith('connected') and not links['wifi']:
            links['wifi'] = dev
        if kind in ('gsm', 'cdma') and state.startswith('connected') and not links['cellular']:
            _, ip_iface = sh(['nmcli', '-g', 'GENERAL.IP-IFACE', 'device', 'show', dev])
            links['cellular'] = ip_iface or dev
    if WIFI_IFACE:
        links['wifi'] = WIFI_IFACE if _is_up(WIFI_IFACE) else None
    if CELL_IFACE:
        links['cellular'] = CELL_IFACE if os.path.exists(f'/sys/class/net/{CELL_IFACE}') else None
    elif not links['cellular']:
        # USB modems in ECM/RNDIS/MBIM mode show up as plain interfaces (wwan0, usb2, eth1…)
        try:
            names = sorted(os.listdir('/sys/class/net'))
        except OSError:
            names = []
        for iface in names:
            if iface.startswith(('wwan', 'rmnet_mhi')) or (_usb_vendor(iface) == QUECTEL_VENDOR and _driver(iface) in MODEM_DRIVERS):
                if _is_up(iface):
                    links['cellular'] = iface
                    break
    return links


def _is_up(iface):
    try:
        return open(f'/sys/class/net/{iface}/operstate').read().strip() in ('up', 'unknown')
    except OSError:
        return False


def gateway(iface):
    """Next hop on that interface (None = point-to-point link, route by device alone)."""
    _, out = sh(['ip', '-j', 'route', 'show', 'default', 'dev', iface])
    try:
        for r in json.loads(out or '[]'):
            if r.get('gateway'):
                return r['gateway']
    except ValueError:
        pass
    return None


# ── probing ────────────────────────────────────────────────────────────────
PING_RTT = re.compile(r'time[=<]([\d.]+)')


def probe(iface):
    """One ping to the VPS forced out of `iface`. Returns rtt ms or None."""
    code, out = sh(['ping', '-n', '-c', '1', '-W', '1', '-I', iface, VPS], timeout=2)
    if code != 0:
        return None
    m = PING_RTT.search(out)
    return float(m.group(1)) if m else 0.0


class Link:
    def __init__(self, kind):
        self.kind = kind
        self.iface = None
        self.ok_run = 0          # consecutive good probes
        self.fail_run = 0        # consecutive failed probes
        self.history = []        # last 30 results (rtt or None)

    def update(self, iface, rtt):
        if iface != self.iface:
            self.iface, self.ok_run, self.fail_run, self.history = iface, 0, 0, []
        if iface is None:
            return
        self.history = (self.history + [rtt])[-30:]
        if rtt is None:
            self.fail_run, self.ok_run = self.fail_run + 1, 0
        else:
            self.ok_run, self.fail_run = self.ok_run + 1, 0

    def loss_pct(self):
        return 100.0 * sum(r is None for r in self.history) / len(self.history) if self.history else 100.0

    @property
    def healthy(self):
        return self.iface is not None and self.fail_run == 0 and self.ok_run >= 1

    def summary(self):
        good = [r for r in self.history if r is not None]
        return {'iface': self.iface, 'up': self.iface is not None, 'answering': self.healthy,
                'rtt_ms': round(sum(good[-5:]) / len(good[-5:]), 1) if good else None,
                'loss_pct': round(100 * (1 - len(good) / len(self.history))) if self.history else None,
                'fail_run': self.fail_run, 'ok_run': self.ok_run}


# ── deciding ───────────────────────────────────────────────────────────────
def choose(active, wifi, cell, prefer='auto', back_needed=None):
    """Pure decision so it can be unit-tested. Returns (new_active, reason or None).
    back_needed: seconds of good WiFi required before leaving cellular (grows when WiFi flaps)."""
    back_needed = back_needed or BACK_TO_WIFI
    if prefer in ('wifi', 'cellular'):
        want = wifi if prefer == 'wifi' else cell
        if want.iface:
            return (prefer, None if active == prefer else f'operator forced {prefer.upper()}')
    if active == 'cellular':
        # WiFi must be genuinely good again, not just answering: a lossy WiFi that answers
        # 10 pings in a row and then drops caused 22 switches in 4 minutes on the ZD650.
        if wifi.iface and wifi.ok_run >= back_needed and wifi.loss_pct() <= WIFI_MAX_LOSS:
            return 'wifi', f'WiFi good again for {back_needed} s (loss {wifi.loss_pct():.0f}%)'
        if not cell.iface and wifi.iface:
            return 'wifi', 'cellular link gone'
        return 'cellular', None
    # active is wifi or None
    if wifi.iface and (wifi.fail_run < FAIL_TO_SWITCH or not cell.healthy):
        return 'wifi', None if active == 'wifi' else 'WiFi available'
    if cell.healthy:
        why = 'no WiFi' if not wifi.iface else f'WiFi not reaching the VPS for {wifi.fail_run} s'
        return 'cellular', why
    return (active or ('wifi' if wifi.iface else None)), None


# ── applying ───────────────────────────────────────────────────────────────
def apply_route(iface):
    if not iface:
        sh(['ip', 'route', 'del', f'{VPS}/32', 'metric', str(METRIC)])
        return True
    gw = gateway(iface)
    args = ['ip', 'route', 'replace', f'{VPS}/32'] + (['via', gw] if gw else []) + ['dev', iface, 'metric', str(METRIC)]
    code, _ = sh(args)
    return code == 0


def current_route_iface():
    _, out = sh(['ip', '-j', 'route', 'show', f'{VPS}/32'])
    try:
        for r in json.loads(out or '[]'):
            if r.get('metric') == METRIC:
                return r.get('dev')
    except ValueError:
        pass
    return None


# ── data counter ───────────────────────────────────────────────────────────
class Usage:
    def __init__(self, path):
        self.path = path
        try:
            self.d = json.load(open(path))
        except (OSError, ValueError):
            self.d = {}
        self.last = {}
        self.saved = 0

    def add(self, iface, now):
        if not iface:
            return
        try:
            rx = int(open(f'/sys/class/net/{iface}/statistics/rx_bytes').read())
            tx = int(open(f'/sys/class/net/{iface}/statistics/tx_bytes').read())
        except (OSError, ValueError):
            return
        prev = self.last.get(iface)
        self.last[iface] = (rx, tx)
        if prev is None:
            return
        drx = rx - prev[0] if rx >= prev[0] else rx          # counter reset (modem reconnect)
        dtx = tx - prev[1] if tx >= prev[1] else tx
        month, day = time.strftime('%Y-%m', time.localtime(now)), time.strftime('%Y-%m-%d', time.localtime(now))
        m = self.d.setdefault(month, {'rx': 0, 'tx': 0, 'days': {}})
        m['rx'] += drx
        m['tx'] += dtx
        dd = m['days'].setdefault(day, {'rx': 0, 'tx': 0})
        dd['rx'] += drx
        dd['tx'] += dtx
        for k in sorted(self.d)[:-3]:                        # keep three months
            self.d.pop(k, None)
        if now - self.saved > 30:
            self.save(now)

    def save(self, now):
        self.saved = now
        tmp = self.path + '.tmp'
        try:
            with open(tmp, 'w') as f:
                json.dump(self.d, f)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def cycle_mb(self, start_day):
        """MB used since start_day ('YYYY-MM-DD'), from the per-day counters."""
        total = 0
        for m in self.d.values():
            for day, v in m.get('days', {}).items():
                if day >= start_day:
                    total += v.get('rx', 0) + v.get('tx', 0)
        return total / 1e6

    def summary(self, now, plan=None):
        plan = plan or {}
        month, day = time.strftime('%Y-%m', time.localtime(now)), time.strftime('%Y-%m-%d', time.localtime(now))
        m = self.d.get(month, {'rx': 0, 'tx': 0, 'days': {}})
        d = m['days'].get(day, {'rx': 0, 'tx': 0})
        plan_gb = plan.get('plan_gb', PLAN_GB)
        start, reset = billing_cycle(now, plan.get('bill_day', BILL_DAY))
        adj = plan.get('adjust') or {}
        used = self.cycle_mb(start) + (adj.get('mb', 0) if adj.get('cycle') == start else 0)
        used = max(0.0, used)
        return {'month_mb': round((m['rx'] + m['tx']) / 1e6, 1), 'today_mb': round((d['rx'] + d['tx']) / 1e6, 1),
                'up_mb_month': round(m['tx'] / 1e6, 1), 'plan_gb': plan_gb,
                'cycle_mb': round(used, 1), 'cycle_start': start, 'resets': reset,
                'left_gb': round(max(0.0, plan_gb - used / 1000), 2) if plan_gb else None,
                'plan_pct': round(100 * used / (plan_gb * 1000), 1) if plan_gb else None}


def billing_cycle(now, bill_day):
    """(start 'YYYY-MM-DD', next reset 'YYYY-MM-DD') of the cycle containing `now`.
    A bill day past the end of a month (e.g. 31) falls on that month's last day."""
    import calendar
    t = time.localtime(now)

    def on(y, mo):
        while mo < 1:
            y, mo = y - 1, mo + 12
        while mo > 12:
            y, mo = y + 1, mo - 12
        return f'{y:04d}-{mo:02d}-{min(bill_day, calendar.monthrange(y, mo)[1]):02d}'
    today = time.strftime('%Y-%m-%d', t)
    this = on(t.tm_year, t.tm_mon)
    if today >= this:
        return this, on(t.tm_year, t.tm_mon + 1)
    return on(t.tm_year, t.tm_mon - 1), this


def read_plan():
    try:
        p = json.load(open(os.path.join(STATE_DIR, 'plan.json')))
        return p if isinstance(p, dict) else {}
    except (OSError, ValueError):
        return {}


_signal_setup = [0.0]


def _num(v):
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def rsrp_percent(rsrp):
    """RSRP (dBm) to 0-100 %: -140 dBm = 0 %, -80 dBm or better = 100 %."""
    return None if rsrp is None else int(max(0, min(100, round((rsrp + 140) / 60 * 100))))


# ── modem signal, straight from the modem (Quectel AT) ──────────────────────
# ModemManager can't help with the RM520N in ECM mode: it reports 0 %, "no extended signal
# capabilities", doesn't know 5G, and thinks the modem is detached while data flows.
_RSRP_OK = range(-156, -30)


def _ints(fields):
    out = []
    for f in fields:
        try:
            out.append(int(float(f.strip().strip('"'))))
        except ValueError:
            out.append(None)
    return out


def parse_qeng(text):
    """Parse AT+QENG="servingcell" (LTE, NR5G-NSA, NR5G-SA). Returns {} if nothing usable."""
    lte = nr = None
    for line in text.splitlines():
        if '+QENG:' not in line:
            continue
        f = [x.strip().strip('"') for x in line.split(':', 1)[1].split(',')]
        for i, tok in enumerate(f):
            n = _ints(f[i + 1:])
            g = lambda k: n[k - 1] if len(n) >= k else None      # field k after the technology name
            if tok == 'LTE':
                lte = {'rsrp': g(11), 'rsrq': g(12)}
            elif tok == 'NR5G-NSA':
                nr = {'rsrp': g(4), 'sinr': g(5), 'rsrq': g(6), 'mode': 'NSA'}
            elif tok == 'NR5G-SA':
                nr = {'rsrp': g(10), 'rsrq': g(11), 'sinr': g(12), 'mode': 'SA'}
    for blk, label in ((nr, None), (lte, 'LTE')):
        if blk and blk.get('rsrp') in _RSRP_OK:
            tech = label or ('5G SA' if blk['mode'] == 'SA' else '5G NSA')
            out = {'technology': tech, 'rsrp': float(blk['rsrp']), 'percent': rsrp_percent(blk['rsrp'])}
            if blk.get('rsrq') is not None and -45 <= blk['rsrq'] <= 0:
                out['rsrq'] = float(blk['rsrq'])
            if blk.get('sinr') is not None and -30 <= blk['sinr'] <= 50:
                out['sinr'] = float(blk['sinr'])
            if label is None and lte and lte.get('rsrp') in _RSRP_OK:
                out['lte_rsrp'] = float(lte['rsrp'])          # NSA: the LTE anchor carries the control link
            return out
    return {}


def parse_cops(text):
    m = re.search(r'\+COPS:\s*\d+\s*,\s*\d+\s*,\s*"([^"]+)"', text)
    return m.group(1) if m else None


def find_at_port():
    """The modem's AT port: Quectel (2c7c) USB interface 03, else 02. Never touches other
    USB serial devices (the ESC Data Link adapter is a ttyUSB too)."""
    if MODEM_AT:
        return MODEM_AT if os.path.exists(MODEM_AT) else None
    found = {}
    try:
        names = os.listdir('/sys/class/tty')
    except OSError:
        return None
    for name in names:
        if not name.startswith('ttyUSB'):
            continue
        dev = os.path.realpath(f'/sys/class/tty/{name}/device')
        try:
            ifnum = open(os.path.join(os.path.dirname(dev), 'bInterfaceNumber')).read().strip()
            vendor = open(os.path.join(os.path.dirname(os.path.dirname(dev)), 'idVendor')).read().strip()
        except OSError:
            continue
        if vendor == QUECTEL_VENDOR:
            found[ifnum] = '/dev/' + name
    return found.get('03') or found.get('02')


def at_query(port, cmd, timeout=2.0):
    """Send one AT command, return the reply text ('' on any failure)."""
    import termios
    try:
        fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    except OSError:
        return ''
    try:
        attrs = termios.tcgetattr(fd)
        attrs[0] = attrs[1] = attrs[3] = 0                   # raw
        attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
        attrs[4] = attrs[5] = termios.B115200
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        termios.tcflush(fd, termios.TCIOFLUSH)
        os.write(fd, (cmd + '\r').encode())
        buf, end = b'', time.time() + timeout
        while time.time() < end:
            try:
                chunk = os.read(fd, 4096)
            except BlockingIOError:
                chunk = b''
            if chunk:
                buf += chunk
                if re.search(rb'\r\n(OK|ERROR|\+CME ERROR[^\r]*)\r\n', buf):
                    break
            else:
                time.sleep(0.05)
        return buf.decode(errors='replace')
    except OSError:
        return ''
    finally:
        os.close(fd)


def cell_signal():
    """Signal from the modem itself; ModemManager only as a last resort."""
    port = find_at_port()
    if port:
        out = parse_qeng(at_query(port, 'AT+QENG="servingcell"'))
        op = parse_cops(at_query(port, 'AT+COPS?'))
        if op:
            out['operator'] = op
        if out.get('percent') is not None:
            out['source'] = 'at'
            return out
    return mm_signal()


def mm_signal():
    now = time.time()
    if now - _signal_setup[0] > 600:
        sh(['mmcli', '-m', 'any', '--signal-setup=5'], timeout=5)
        _signal_setup[0] = now
    out = {}
    code, raw = sh(['mmcli', '-m', 'any', '--signal-get', '--output-json'], timeout=4)
    if code == 0:
        try:
            sig = json.loads(raw).get('modem', {}).get('signal', {})
        except ValueError:
            sig = {}
        for tech, label in (('5g', '5G'), ('lte', 'LTE'), ('umts', '3G')):
            blk = sig.get(tech) or {}
            rsrp = _num(blk.get('rsrp'))
            if rsrp is not None:
                out = {'technology': label, 'rsrp': rsrp, 'rsrq': _num(blk.get('rsrq')),
                       'sinr': _num(blk.get('snr') if blk.get('snr') is not None else blk.get('sinr')),
                       'percent': rsrp_percent(rsrp)}
                break
    return out


def read_prefer():
    try:
        p = open(os.path.join(RUN_DIR, 'prefer')).read().strip()
        return p if p in ('auto', 'wifi', 'cellular') else 'auto'
    except OSError:
        return 'auto'


def main():
    os.makedirs(RUN_DIR, exist_ok=True)
    os.makedirs(STATE_DIR, exist_ok=True)
    os.chmod(RUN_DIR, 0o1777)                 # the server (user airsent) writes 'prefer' here
    usage = Usage(os.path.join(STATE_DIR, 'usage.json'))
    wifi, cell = Link('wifi'), Link('cellular')
    active, switches, last_switch = None, 0, None
    back_needed, to_wifi_at = BACK_TO_WIFI, 0.0
    links, links_at, sig, sig_at = {}, 0, {}, 0
    plan, plan_at = read_plan(), time.time()
    print(f'[NETLINK] steering traffic to {VPS}: WiFi first, cellular fallback', flush=True)
    while True:
        t0 = time.time()
        if t0 - links_at > 5:
            links, links_at = find_links(), t0
        wifi.update(links.get('wifi'), probe(links['wifi']) if links.get('wifi') else None)
        cell.update(links.get('cellular'), probe(links['cellular']) if links.get('cellular') else None)
        prefer = read_prefer()
        new, reason = choose(active, wifi, cell, prefer, back_needed)
        iface = {'wifi': wifi.iface, 'cellular': cell.iface}.get(new)
        if new != active or current_route_iface() != iface:
            if apply_route(iface):
                if new != active:
                    if new == 'wifi' and active == 'cellular':
                        to_wifi_at = t0                                       # a real return to WiFi
                    elif active == 'wifi' and new == 'cellular' and t0 - to_wifi_at < FLAP_WINDOW:
                        back_needed = min(BACKOFF_MAX, back_needed * 3)       # flapping: make WiFi prove itself longer
                        reason = f'{reason}; WiFi unstable — next return to WiFi needs {back_needed} s of good WiFi'
                    switches += 1
                    last_switch = {'at': t0, 'from': active, 'to': new, 'reason': reason}
                    print(f'[NETLINK] {active} → {new}: {reason}', flush=True)
                active = new
        if active == 'wifi' and back_needed > BACK_TO_WIFI and t0 - to_wifi_at > 600:
            back_needed = BACK_TO_WIFI                                        # stable for 10 min: forgive
        usage.add(cell.iface, t0)
        if t0 - plan_at > 30:
            plan, plan_at = read_plan(), t0
        if cell.iface and t0 - sig_at > 10:
            sig, sig_at = cell_signal(), t0
        status = {'at': t0, 'active': active, 'iface': iface, 'prefer': prefer, 'switches': switches, 'last_switch': last_switch,
                  'back_to_wifi_s': back_needed,
                  'wifi': wifi.summary(), 'cellular': {**cell.summary(), **(sig if cell.iface else {})},
                  'data': usage.summary(t0, plan)}
        tmp = os.path.join(RUN_DIR, 'status.json.tmp')
        try:
            with open(tmp, 'w') as f:
                json.dump(status, f)
            os.chmod(tmp, 0o644)
            os.replace(tmp, os.path.join(RUN_DIR, 'status.json'))
        except OSError:
            pass
        time.sleep(max(0.05, 1.0 - (time.time() - t0)))


def set_plan(argv):
    """sudo airsent-netlink --plan 10 --bill-day 15 [--used 3.2]
    Plan size (GB) and the day of the month it resets. --used = GB used so far this cycle,
    from the T-Mobile app; the Jetson counts on from there."""
    import argparse
    ap = argparse.ArgumentParser(prog='airsent-netlink')
    ap.add_argument('--plan', type=float, required=True, help='plan size in GB')
    ap.add_argument('--bill-day', type=int, default=None, help='day of the month the data resets (1-31)')
    ap.add_argument('--used', type=float, default=None, help='GB already used this cycle (from the T-Mobile app)')
    a = ap.parse_args(argv)
    plan = read_plan()
    plan['plan_gb'] = a.plan
    if a.bill_day is not None:
        if not 1 <= a.bill_day <= 31:
            sys.exit('--bill-day must be 1-31')
        plan['bill_day'] = a.bill_day
    now = time.time()
    start, reset = billing_cycle(now, plan.get('bill_day', BILL_DAY))
    if a.used is not None:
        counted = Usage(os.path.join(STATE_DIR, 'usage.json')).cycle_mb(start)
        plan['adjust'] = {'cycle': start, 'mb': a.used * 1000 - counted}
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(os.path.join(STATE_DIR, 'plan.json'), 'w') as f:
        json.dump(plan, f)
    u = Usage(os.path.join(STATE_DIR, 'usage.json')).summary(now, plan)
    print(f"Plan {u['plan_gb']} GB, cycle {u['cycle_start']} → resets {u['resets']}: "
          f"{u['cycle_mb'] / 1000:.2f} GB used, {u['left_gb']} GB left")


if __name__ == '__main__':
    if len(sys.argv) > 1:
        set_plan(sys.argv[1:])
        sys.exit(0)
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
