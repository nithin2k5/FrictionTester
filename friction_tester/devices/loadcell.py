"""Loadcell indicator on RS232, using the same protocol as the EOL tester.

8N1 serial. Polling with "ID<nn>P" (nn = two-digit loadcell number, e.g.
ID01P) returns one comma-separated line; the reading is the second field.
"""

import threading

try:
    import serial
except ImportError:  # pragma: no cover - reported in the UI
    serial = None

BAUD_RATES = [9600, 19200, 38400, 57600, 115200]


class Loadcell:
    def __init__(self, number):
        self.number = number
        self.port = ""
        self._ser = None
        self._lock = threading.Lock()

    @property
    def tag(self):
        return f"{self.number:02d}"

    @property
    def name(self):
        return f"Loadcell {self.tag} (L{self.number})"

    @staticmethod
    def available():
        return serial is not None

    @property
    def is_open(self):
        return self._ser is not None and self._ser.is_open

    def connect(self, cfg):
        if serial is None:
            raise RuntimeError("pyserial is not installed (pip install pyserial)")
        if not cfg["port"]:
            raise ValueError("Select a COM port")
        self.disconnect()
        self._ser = serial.Serial(
            port=cfg["port"], baudrate=int(cfg["baudrate"]),
            bytesize=8, parity="N", stopbits=1,
            timeout=0.5, write_timeout=0.5,
        )
        self.port = cfg["port"]

    def disconnect(self):
        if self._ser is not None:
            try:
                self._ser.close()
            finally:
                self._ser = None

    def read(self):
        """Poll once. Returns (command, raw reply, value) — value is a float or None."""
        if not self.is_open:
            raise RuntimeError(f"Loadcell {self.tag} is not connected")
        command = f"ID{self.tag}P"
        with self._lock:
            self._ser.reset_input_buffer()
            self._ser.reset_output_buffer()
            self._ser.write(command.encode())
            reply = self._ser.readline().decode("utf-8", errors="replace").strip()
        if not reply:
            raise TimeoutError("No response received")
        parts = reply.split(",")
        value = None
        if len(parts) > 1:
            try:
                value = float(parts[1].strip())
            except ValueError:
                pass
        return command, reply, value
