"""Receive-only Datalink TX1 telemetry. No motor commands or serial writes.

Packet layout: HW Data Link protocol 170730 (TX1), also to be verified against
actual T-Motor ALPHA captures before field use. RPM scaling follows ArduPilot's
DataLink driver (raw speed * 10 / motor poles); verify with a tachometer.
"""
import binascii
import os
import struct
import threading
import time


class DataLinkMonitor:
    def __init__(self, poles=22, clock=time.monotonic):
        self.poles = poles
        self.clock = clock
        self.buffer = bytearray()
        self.channels = {}
        self.last_frame = None
        self.frames = 0
        self.crc_errors = 0
        self.error = None
        self.lock = threading.Lock()

    def feed(self, data):
        with self.lock:
            self.buffer.extend(data)
            while self.buffer:
                start = self.buffer.find(b'\x9b\x9e')
                if start < 0:
                    self.buffer[:] = self.buffer[-1:] if self.buffer[-1:] == b'\x9b' else b''
                    break
                del self.buffer[:start]
                if len(self.buffer) < 160:
                    break
                frame = bytes(self.buffer[:160])
                if frame[2] not in (0, 1) or frame[3] != 2:
                    del self.buffer[0]
                    continue
                crc = binascii.crc_hqx(frame[:-2], 0)
                # Published version-0 examples and version-1 implementations
                # use different CRC byte orders; the full payload must match.
                if crc not in (int.from_bytes(frame[-2:], 'big'), int.from_bytes(frame[-2:], 'little')):
                    self.crc_errors += 1
                    del self.buffer[0]
                    continue
                del self.buffer[:160]
                records = [struct.unpack_from('>BHHHHHhhBBH', frame, 6+i*19) for i in range(8)]
                if sorted(row[0] for row in records) != list(range(1, 9)):
                    continue
                now = self.clock()
                self.frames += 1
                self.last_frame = now
                self.error = None
                for channel, counter, inp, out, speed, voltage, current, phase, mos, cap, status in records:
                    if inp > 32767 or out > 32767:
                        continue
                    previous = self.channels.get(channel)
                    if previous and previous['counter'] == counter:
                        continue  # Repeated aggregate frames cannot keep an old ESC sample fresh.
                    if not any((inp, out, speed, voltage, current, phase, status)):
                        continue  # Empty slots are not proof of an attached ESC.
                    self.channels[channel] = {
                        'channel': channel, 'counter': counter, 'sample_at': now,
                        'input_percent': round(inp*100/32767, 2),
                        'output_percent': round(out*100/32767, 2),
                        'speed_raw': speed,
                        'rpm': round(speed*10/self.poles) if self.poles else None,
                        'voltage': voltage/10, 'current': None if current == -1 else current/64,
                        'phase_current': None if phase == -1 else phase/64, 'status': status,
                    }

    def snapshot(self):
        with self.lock:
            now = self.clock()
            age = now-self.last_frame if self.last_frame is not None else None
            connected = age is not None and 0 <= age < 2
            channels = []
            for channel in range(1, 9):
                record = self.channels.get(channel)
                sample_age = now-record['sample_at'] if record else None
                fresh = bool(connected and sample_age is not None and 0 <= sample_age < 2)
                row = {k:v for k,v in record.items() if k != 'sample_at'} if fresh else {'channel':channel}
                row.update(connected=fresh, age=round(sample_age, 3) if sample_age is not None else None)
                channels.append(row)
            return {'source':'Datalink TX1', 'connected':connected,
                    'age':round(age,3) if age is not None else None,
                    'motor_poles':self.poles, 'valid_frames':self.frames,
                    'crc_errors':self.crc_errors, 'error':self.error, 'channels':channels}


def serial_reader(monitor, port, baud=115200):
    import serial
    while True:
        try:
            # RX-only. Deassert modem-control lines before opening the adapter.
            stream = serial.Serial(port=None, baudrate=baud, timeout=.2, exclusive=True)
            stream.dtr = False
            stream.rts = False
            stream.port = port
            with stream:
                while True:
                    data = stream.read(min(4096, max(1, stream.in_waiting)))
                    if data:
                        monitor.feed(data)
        except (OSError, serial.SerialException) as exc:
            with monitor.lock:
                monitor.last_frame = None
                monitor.channels.clear()
                monitor.buffer.clear()
                monitor.error = str(exc)
            time.sleep(2)


def start_monitor():
    poles = int(os.environ.get('DATALINK_MOTOR_POLES', '22'))
    if not 2 <= poles <= 100 or poles % 2:
        raise ValueError('DATALINK_MOTOR_POLES must be an even pole count between 2 and 100')
    monitor = DataLinkMonitor(poles=poles)
    port = os.environ.get('DATALINK_PORT')
    if port:
        threading.Thread(target=serial_reader, args=(monitor, port), daemon=True).start()
    else:
        monitor.error = 'Datalink serial port is not configured'
    return monitor
