import queue
import time

import serial
import serial.tools.list_ports

MODE_SERIAL = "Serial (COM/VCP)"
MODE_KEYBOARD = "HID keyboard"
MODE_HID_RAW = "HID raw (POS)"
MODES = [MODE_SERIAL, MODE_KEYBOARD, MODE_HID_RAW]


class BarcodeReader:
    """Digitus DA-81001 in USB serial (COM/VCP) mode; every scan is sent as one terminated line."""
    portHandle = None

    def __init__(self, port, baudrate=9600, timeout=0.5):
        self.port = port
        self.description = port
        self.portHandle = serial.Serial(port, baudrate=baudrate, timeout=timeout)

    def close(self):
        if self.portHandle is not None:
            self.portHandle.close()
            self.portHandle = None

    def readBarcode(self):
        """Return the next scanned barcode, or None if nothing was scanned within the timeout."""
        line = self.portHandle.readline()
        if not line.endswith(b'\n') and not line.endswith(b'\r'):
            return None
        return line.decode(errors='replace').strip() or None

    @staticmethod
    def listPorts():
        return [p.device for p in serial.tools.list_ports.comports()]


class KeyboardBarcodeReader:
    """HID keyboard (wedge) mode: the scanner types the code plus Enter; captured by a global key hook."""

    def __init__(self, maxKeyGap=0.1, minLength=3, timeout=0.5):
        from pynput import keyboard
        self.description = "HID keyboard"
        self._keyboard = keyboard
        self._maxKeyGap = maxKeyGap  # slower typing is a human, not a scanner
        self._minLength = minLength
        self._timeout = timeout
        self._buffer = []
        self._lastKey = 0.0
        self._codes = queue.Queue()
        self._listener = keyboard.Listener(on_press=self._onPress)
        self._listener.start()

    def _onPress(self, key):
        now = time.monotonic()
        if now - self._lastKey > self._maxKeyGap:
            self._buffer = []
        self._lastKey = now
        if key == self._keyboard.Key.enter:
            if len(self._buffer) >= self._minLength:
                self._codes.put(''.join(self._buffer))
            self._buffer = []
        elif getattr(key, 'char', None):
            self._buffer.append(key.char)

    def close(self):
        self._listener.stop()

    def readBarcode(self):
        try:
            return self._codes.get(timeout=self._timeout)
        except queue.Empty:
            return None


class HidBarcodeReader:
    """HID raw (POS) mode: reads input reports directly from the device."""

    def __init__(self, path, description="HID device", timeout=0.5):
        import hid
        self.description = description
        self._timeoutMs = int(timeout * 1000)
        self._device = hid.device()
        self._device.open_path(path)

    def close(self):
        if self._device is not None:
            self._device.close()
            self._device = None

    def readBarcode(self):
        report = self._device.read(64, self._timeoutMs)
        if not report:
            return None
        # Assumed POS layout: [report id, length, data...]; falls back to all printable bytes.
        if len(report) > 2 and 0 < report[1] <= len(report) - 2:
            data = bytes(report[2:2 + report[1]])
        else:
            data = bytes(b for b in report if 32 <= b < 127)
        return data.decode(errors='replace').strip() or None

    @staticmethod
    def listDevices():
        """Return {label: path} for all HID devices."""
        import hid
        devices = {}
        for d in hid.enumerate():
            label = f"{d['vendor_id']:04X}:{d['product_id']:04X}  {d.get('product_string') or 'unknown'}"
            if label in devices:
                label += f" [{len(devices)}]"
            devices[label] = d['path']
        return devices


if __name__ == '__main__':
    ports = BarcodeReader.listPorts()
    print(f'Ports: {ports}')
    reader = BarcodeReader(ports[0] if ports else '/dev/ttyACM0')
    try:
        while True:
            code = reader.readBarcode()
            if code:
                print(f'Barcode: {code}')
    except KeyboardInterrupt:
        pass
    finally:
        reader.close()
