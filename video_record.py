"""Camera recording into the mission folder while an ASC session is open.

Front (OAK-D Lite): the camera encodes H.265 itself (DepthAI VideoEncoder), so the
Jetson only appends bytes to <mission>/video/front.h265 — no Jetson CPU, and it does
not depend on the SRT stream or the VPS. Recording starts at the next keyframe.

Down (IMX219): the Orin Nano has no hardware encoder, so frames are saved as JPEGs
in one crash-safe .mjpeg file at a low rate (640x480 @ 10 fps by default).

Both raw files survive a power cut. When recording stops they are packed into .mp4
with ffmpeg at low priority (front: copied, no re-encode; down: x264). If ffmpeg is
missing or fails, the raw file is kept as it is.
"""
import json
import os
import shutil
import subprocess
import threading
import time

VPS_NAL = b'\x00\x00\x01\x40\x01'       # H.265 VPS — sent by the encoder just before every keyframe


def h265_keyframe(data):
    return VPS_NAL in bytes(data[:256])


class _Finalizer:
    """Runs ffmpeg conversions one at a time in the background."""
    def __init__(self):
        self.lock = threading.Lock()
        self.threads = []

    def run(self, fn, *args):
        t = threading.Thread(target=self._wrap, args=(fn,) + args, daemon=True)
        self.threads = [x for x in self.threads if x.is_alive()] + [t]
        t.start()
        return t

    def _wrap(self, fn, *args):
        with self.lock:
            try:
                fn(*args)
            except Exception as e:                  # never take the server down
                print(f'[VIDEO] finalize: {e}')

    def join(self, timeout=None):
        for t in list(self.threads):
            t.join(timeout)


finalizer = _Finalizer()


def _ffmpeg(args, timeout=3600):
    exe = shutil.which('ffmpeg')
    if not exe:
        return False
    cmd = [exe, '-hide_banner', '-loglevel', 'error', '-y'] + args
    if shutil.which('nice'):
        cmd = ['nice', '-n', '19'] + cmd
    try:
        return subprocess.run(cmd, timeout=timeout, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _meta(folder, name, info):
    path = os.path.join(folder, 'video.json')
    try:
        data = json.load(open(path))
    except (OSError, ValueError):
        data = {}
    data[name] = info
    try:
        with open(path, 'w') as f:
            json.dump(data, f, indent=1)
    except OSError:
        pass


class _Track:
    raw_name = ''
    kind = ''

    def __init__(self, clock=time.time):
        self.clock = clock
        self.lock = threading.Lock()
        self.folder = None
        self.f = None
        self.bytes = 0
        self.frames = 0
        self.started = None
        self.total_bytes = 0          # this session, survives close until the next open
        self.error = None
        self._flushed = 0.0

    @property
    def recording(self):
        return self.f is not None

    def set_folder(self, folder):
        """folder: <mission>/video while recording, None to stop."""
        if folder == self.folder:
            return
        with self.lock:
            old = self._close_locked()
            self.folder = folder
            self.bytes = self.frames = 0
            self.started = None
            self.error = None
            if folder:
                self.total_bytes = 0
        if old:
            finalizer.run(self.finalize, *old)

    def _open_locked(self):
        try:
            os.makedirs(self.folder, exist_ok=True)
            self.f = open(os.path.join(self.folder, self.raw_name), 'ab')
            self.started = self.clock()
        except OSError as e:
            self.error = str(e)
            self.folder = None
            print(f'[VIDEO] {self.kind}: {e}')

    def _close_locked(self):
        if not self.f:
            return None
        try:
            self.f.close()
        except OSError:
            pass
        self.f = None
        secs = max(0.001, self.clock() - (self.started or self.clock()))
        fps = self.frames / secs if self.frames else 0
        return (self.folder, self.frames, round(secs, 1), round(fps, 2))

    def _append(self, data):
        try:
            self.f.write(data)
        except OSError as e:
            self.error = str(e)
            print(f'[VIDEO] {self.kind} write: {e} — stopped')
            self.f.close()
            self.f = None
            self.folder = None
            return
        now = self.clock()
        if now - self._flushed >= 1.0:            # at most 1 s of video lost on a power cut
            self._flushed = now
            try:
                self.f.flush()
            except OSError:
                pass
        n = len(data)
        self.bytes += n
        self.total_bytes += n
        self.frames += 1

    def snapshot(self):
        return {'recording': self.recording, 'mb': round(self.total_bytes / 1e6, 1), 'frames': self.frames,
                'error': self.error}


class FrontRecorder(_Track):
    raw_name = 'front.h265'
    kind = 'front'

    def __init__(self, clock=time.time):
        super().__init__(clock)
        self.wait_key = True

    def set_folder(self, folder):
        if folder != self.folder:
            self.wait_key = True
        super().set_folder(folder)

    def packet(self, data):
        """One encoded H.265 frame from the OAK-D. Called for every frame, recording or not."""
        if not self.folder:
            return
        with self.lock:
            if not self.folder:
                return
            if self.wait_key:
                if not h265_keyframe(data):
                    return
                self.wait_key = False
            if not self.f:
                self._open_locked()
                if not self.f:
                    return
            self._append(bytes(data))

    def finalize(self, folder, frames, secs, fps):
        raw = os.path.join(folder, self.raw_name)
        mp4 = os.path.join(folder, 'front.mp4')
        rate = fps if 1 <= fps <= 60 else 30
        info = {'frames': frames, 'seconds': secs, 'fps': fps, 'codec': 'H.265', 'source': 'OAK-D Lite (on-camera encoder)'}
        if frames and _ffmpeg(['-r', f'{rate}', '-f', 'hevc', '-i', raw, '-c', 'copy', '-tag:v', 'hvc1',
                               '-movflags', '+faststart', mp4]) and os.path.getsize(mp4) > 0:
            os.remove(raw)
            info['file'] = 'video/front.mp4'
        else:
            info['file'] = 'video/front.h265'
        _meta(folder, 'front', info)


class DownRecorder(_Track):
    raw_name = 'down.mjpeg'
    kind = 'down'

    def __init__(self, get_frame, fps=10, size=(640, 480), quality=80, clock=time.time, encode=None):
        super().__init__(clock)
        self.get_frame = get_frame
        self.fps = max(1.0, float(fps))
        self.size = size
        self.quality = quality
        self.encode = encode or self._cv2_jpeg
        self.thread = None

    def _cv2_jpeg(self, frame):
        import cv2
        if frame.shape[1] != self.size[0] or frame.shape[0] != self.size[1]:
            frame = cv2.resize(frame, self.size, interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, self.quality])
        return buf.tobytes() if ok else None

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def step(self):
        """Take one frame if recording. Returns True when a frame was written."""
        if not self.folder:
            return False
        frame = self.get_frame()
        if frame is None:
            return False
        data = self.encode(frame)
        if not data:
            return False
        with self.lock:
            if not self.folder:
                return False
            if not self.f:
                self._open_locked()
                if not self.f:
                    return False
            self._append(data)
        return True

    def _run(self):
        period = 1.0 / self.fps
        while True:
            t0 = time.time()
            try:
                self.step()
            except Exception as e:
                print(f'[VIDEO] down: {e}')
            time.sleep(max(0.01 if self.folder else 0.5, period - (time.time() - t0)))

    def finalize(self, folder, frames, secs, fps):
        raw = os.path.join(folder, self.raw_name)
        mp4 = os.path.join(folder, 'down.mp4')
        rate = fps if 1 <= fps <= 60 else self.fps
        info = {'frames': frames, 'seconds': secs, 'fps': fps, 'source': 'IMX219 down camera'}
        if frames and _ffmpeg(['-f', 'mjpeg', '-r', f'{rate}', '-i', raw, '-c:v', 'libx264', '-preset', 'veryfast',
                               '-crf', '26', '-pix_fmt', 'yuv420p', '-threads', '2', '-movflags', '+faststart', mp4]) \
                and os.path.getsize(mp4) > 0:
            os.remove(raw)
            info.update(file='video/down.mp4', codec='H.264')
        else:
            info.update(file='video/down.mjpeg', codec='MJPEG')
        _meta(folder, 'down', info)
