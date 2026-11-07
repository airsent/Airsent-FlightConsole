"""Download the Pixhawk's DataFlash (.bin) log over MAVLink into the mission folder.

ArduPilot closes its .bin when it disarms. After the ASC session ends the server
asks for the log list, takes the newest log and pulls it with the MAVLink log protocol:

    LOG_REQUEST_LIST -> LOG_ENTRY (one per log)
    LOG_REQUEST_DATA -> LOG_DATA (90 bytes each, streamed)
    gaps are asked for again until every byte is in
    LOG_REQUEST_END   (the Pixhawk goes back to normal logging)

The reader thread owns recv(): it hands every LOG_* message to observe().
Downloading only happens on the ground; arming aborts it at once.
The file is written as pixhawk.bin.part and renamed when complete.
"""
import json
import os
import threading
import time

BLOCK = 90                      # bytes per LOG_DATA
GAP_REQUEST = 90 * 2000         # ~180 KB per re-request
IDLE_S = 0.4                    # no data this long -> ask for the next gap


class LogDownloader:
    def __init__(self, clock=time.time):
        self.clock = clock
        self.cond = threading.Condition()
        self.entries = {}
        self.list_done = False
        self.num_logs = None
        self.active = None          # {'id', 'size', 'f', 'have'(bytearray), 'got'}
        self.last_data = 0.0
        self.thread = None
        self.status = {'state': 'idle'}

    # ── fed by the MAVLink reader thread ────────────────────────────────────
    def observe(self, msg):
        t = msg.get_type()
        if t == 'LOG_ENTRY':
            with self.cond:
                self.num_logs = msg.num_logs
                if msg.num_logs:
                    self.entries[msg.id] = {'id': msg.id, 'size': msg.size, 'time_utc': msg.time_utc}
                if msg.num_logs == 0 or len(self.entries) >= msg.num_logs or msg.id == msg.last_log_num:
                    self.list_done = True
                self.cond.notify_all()
        elif t == 'LOG_DATA':
            with self.cond:
                a = self.active
                if not a or msg.id != a['id'] or msg.ofs >= a['size']:
                    return
                n = min(msg.count, a['size'] - msg.ofs)
                blk = msg.ofs // BLOCK
                if msg.ofs % BLOCK == 0 and blk < len(a['have']) and not a['have'][blk] and n > 0:
                    a['f'].seek(msg.ofs)
                    a['f'].write(bytes(msg.data[:n]))
                    a['have'][blk] = 1
                    a['got'] += n
                self.last_data = self.clock()
                self.cond.notify_all()

    def busy(self):
        return bool(self.thread and self.thread.is_alive())

    def snapshot(self):
        s = dict(self.status)
        a = self.active
        if a and s.get('state') == 'downloading':
            s['bytes'] = a['got']
            s['pct'] = round(100.0 * a['got'] / a['size'], 1) if a['size'] else 100.0
        return s

    # ── the download itself (runs in its own thread) ────────────────────────
    def start(self, mav_getter, folder, armed, log_id=None):
        if self.busy():
            return False, 'A Pixhawk log download is already running'
        self.thread = threading.Thread(target=self.download, args=(mav_getter, folder, armed, log_id), daemon=True)
        self.thread.start()
        return True, 'Pixhawk log download started'

    def _set(self, **kw):
        self.status = {'folder': os.path.basename(str(kw.get('folder') or self.status.get('folder') or '')), **kw}

    def download(self, mav_getter, folder, armed, log_id=None, list_timeout=6.0, stall_timeout=20.0):
        dest = os.path.join(folder, 'pixhawk.bin')
        part = dest + '.part'
        mav = mav_getter()
        if mav is None:
            self._set(state='error', folder=folder, msg='Pixhawk not connected')
            return False
        if armed():
            self._set(state='error', folder=folder, msg='Not downloading while armed')
            return False
        self._set(state='listing', folder=folder, msg='Asking the Pixhawk for its log list')
        with self.cond:
            self.entries, self.list_done, self.num_logs = {}, False, None
        mav.mav.log_request_list_send(mav.target_system, mav.target_component, 0, 0xFFFF)
        deadline = self.clock() + list_timeout
        with self.cond:
            while not self.list_done and self.clock() < deadline:
                self.cond.wait(0.2)
            entries = dict(self.entries)
        if not entries:
            self._set(state='error', folder=folder, msg='The Pixhawk reported no logs' if self.num_logs == 0 else 'No answer to the log list request')
            return False
        if log_id is None:
            usable = [e for e in entries.values() if e['size'] > 0]
            if not usable:
                self._set(state='error', folder=folder, msg='The newest Pixhawk logs are empty')
                return False
            entry = max(usable, key=lambda e: e['id'])
        else:
            entry = entries.get(int(log_id))
            if not entry or entry['size'] <= 0:
                self._set(state='error', folder=folder, msg=f'Log {log_id} not found on the Pixhawk')
                return False
        size = entry['size']
        os.makedirs(folder, exist_ok=True)
        f = open(part, 'wb')
        f.truncate(size)
        with self.cond:
            self.active = {'id': entry['id'], 'size': size, 'f': f, 'have': bytearray((size + BLOCK - 1) // BLOCK), 'got': 0}
            self.last_data = self.clock()
        self._set(state='downloading', folder=folder, log_id=entry['id'], size=size, bytes=0, pct=0.0,
                  msg=f"Downloading Pixhawk log {entry['id']} ({size / 1e6:.1f} MB)")
        t0 = self.clock()
        ok, why = False, ''
        try:
            mav.mav.log_request_data_send(mav.target_system, mav.target_component, entry['id'], 0, 0xFFFFFFFF)
            asked_at = self.clock()
            while True:
                if armed():
                    why = 'Drone armed — Pixhawk log download stopped'
                    break
                with self.cond:
                    self.cond.wait(0.2)
                    a = self.active
                    if a['got'] >= size:
                        ok = True
                        break
                    idle = self.clock() - self.last_data
                if idle >= stall_timeout:
                    why = 'The Pixhawk stopped sending the log'
                    break
                if idle >= IDLE_S and self.clock() - asked_at >= IDLE_S:
                    gap = self._next_gap()
                    if gap is None:
                        ok = True
                        break
                    mav = mav_getter() or mav
                    mav.mav.log_request_data_send(mav.target_system, mav.target_component, entry['id'], gap[0], gap[1])
                    asked_at = self.clock()
        finally:
            try:
                mav.mav.log_request_end_send(mav.target_system, mav.target_component)
            except Exception:
                pass
            with self.cond:
                got = self.active['got'] if self.active else 0
                self.active = None
            f.close()
        secs = max(0.001, self.clock() - t0)
        if ok:
            os.replace(part, dest)
            meta = {'log_id': entry['id'], 'size': size, 'time_utc': entry['time_utc'], 'seconds': round(secs, 1),
                    'kb_per_s': round(size / 1024 / secs, 1), 'downloaded': time.strftime('%Y-%m-%dT%H:%M:%S')}
            try:
                with open(os.path.join(folder, 'pixhawk_bin.json'), 'w') as mf:
                    json.dump(meta, mf, indent=1)
            except OSError:
                pass
            self._set(state='done', folder=folder, log_id=entry['id'], size=size, bytes=size, pct=100.0,
                      msg=f"Pixhawk log saved: pixhawk.bin ({size / 1e6:.1f} MB in {secs:.0f} s)")
            return True
        self._set(state='error', folder=folder, log_id=entry['id'], size=size, bytes=got,
                  pct=round(100.0 * got / size, 1) if size else 0, msg=f'{why} — kept the partial file (retry from Logs)')
        return False

    def _next_gap(self):
        with self.cond:
            a = self.active
            if not a:
                return None
            have = a['have']
            i = have.find(0)
            if i < 0:
                return None
            # ArduPilot serves one request at a time, so ask for one range that covers
            # every missing block in the next window (blocks we already have are ignored).
            window_end = min(len(have), i + GAP_REQUEST // BLOCK)
            last = have.rfind(0, i, window_end)
            ofs = i * BLOCK
            count = min((last + 1 - i) * BLOCK, a['size'] - ofs)
            return ofs, count
