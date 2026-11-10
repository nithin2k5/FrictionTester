"""KOMEIN AM-50LW digital indicator (loadcell) on RS232, standard protocol.

Port: 8 data bits, no parity, 1 stop bit; 2400-57600 baud (indicator F-41).
The indicator's ID Number (F-40) picks the mode:

* ID 000 - stream mode. The indicator sends continuously:
      ST,NT,+01234.5<CR><LF>     (NT = normal, ER = error)
* ID 01-99 - command mode. The PC sends ID<nn><cmd> with no terminator:
      P = send current value   Z = zero   H = hold   R = release hold
  and the indicator answers a P with:
      ID,01,+01234.5<CR><LF>     (some units: ID001,+01234.5<CR><LF>)

The data field is 8 characters including the sign; the decimal point is
wherever the indicator's F-00 setting puts it, so the number is used as sent.

The ID must be entered per loadcell on the COM Port page (settings
loadcells[i].id: 0 = stream, 1-99 = command mode) and must match the
indicator's F-40. It is used exactly as given - there is no detection.
Each indicator has its own RS232 port, so two indicators may share an ID.
"""

import re
import threading
import time

from . import ports

try:
    import serial
except ImportError:  # pragma: no cover - reported in the UI
    serial = None

MODEL = "KOMEIN AM-50LW"
BAUD_RATES = [2400, 4800, 9600, 19200, 38400, 57600]
STREAM_ID = 0

CMD_READ, CMD_ZERO, CMD_HOLD, CMD_RELEASE = "P", "Z", "H", "R"


class IndicatorError(Exception):
    pass


def parse_frame(line, expected_id=None):
    """Parse one AM-50LW line. Returns the reading as a float.

    Raises IndicatorError for an error frame, a reply from another ID, or a
    line that isn't a recognisable frame.
    """
    fields = [f.strip() for f in line.strip().split(",")]
    # Some AM-50LW units join the ID to the header: "ID001,+0000.01" (seen on the machine,
    # 2026-11-07) instead of the manual's "ID,01,+0000.01".
    joined = re.match(r"^ID(\d{1,3})$", fields[0].upper()) if len(fields) == 2 else None
    if joined:
        fields = ["ID", joined.group(1), fields[1]]
    if len(fields) < 3:
        raise IndicatorError(f"Unrecognised frame: {line!r}")
    head, mid, data = fields[0].upper(), fields[1].upper(), fields[2]
    if head == "ST":
        if mid == "ER":
            raise IndicatorError("Indicator reports an error (ER) — check overload / sensor")
    elif head == "ID":
        if expected_id is not None and mid.isdigit() and int(mid) != expected_id:
            raise IndicatorError(f"Reply from ID {mid}, expected {expected_id:02d}")
    else:
        raise IndicatorError(f"Unrecognised frame: {line!r}")
    try:
        return float(data)
    except ValueError:
        raise IndicatorError(f"Bad data field {data!r} in {line!r}")


PortBusyError = ports.PortBusyError


def parse_id(value):
    """Settings "id" -> 0..99. The ID is required (no detection)."""
    text = "" if value is None else str(value).strip()
    if not text or text.lower() == "auto":
        raise ValueError("Enter the indicator ID (F-40): 00 = stream mode, 01-99 = command mode")
    try:
        n = int(text.split()[0])
    except ValueError:
        raise ValueError(f"Indicator ID must be 00-99 (got {value})")
    if not 0 <= n <= 99:
        raise ValueError(f"Indicator ID must be 00-99 (got {value})")
    return n


def open_port(port, baudrate, tries=3, wait=0.7):
    """Open an 8N1 port. "Access is denied" is retried a few times (Windows can
    hold a just-closed port for a moment), then reported in plain words."""
    for attempt in range(tries):
        try:
            return serial.Serial(port=port, baudrate=baudrate, bytesize=8, parity="N", stopbits=1,
                                 timeout=0.5, write_timeout=0.5)
        except serial.SerialException as exc:
            if not ports.is_access_denied(exc):
                raise
            if attempt == tries - 1:
                raise ports.busy_error(port) from exc
            time.sleep(wait)


class Loadcell:
    """One AM-50LW indicator."""

    def __init__(self, number):
        self.number = number          # position on the machine (L1, L2)
        self.port = ""
        self.device_id = 1
        self._ser = None
        self._lock = threading.Lock()         # one command/reply at a time
        self._conn_lock = threading.Lock()    # one connect/disconnect at a time

    @property
    def name(self):
        return f"Loadcell {self.number:02d} (L{self.number})"

    @property
    def stream_mode(self):
        return self.device_id == STREAM_ID

    @staticmethod
    def available():
        return serial is not None

    @property
    def is_open(self):
        return self._ser is not None and self._ser.is_open

    def connect(self, cfg):
        """Open the port with the ID from the COM Port page (00 = stream mode,
        01-99 = command mode ID<nn>P). The ID is required."""
        if serial is None:
            raise RuntimeError("pyserial is not installed (pip install pyserial)")
        if not cfg["port"]:
            raise ValueError("Select a COM port")
        # One open at a time: the startup auto-connect and the COM Setting
        # CONNECT button use the same object. A second caller waits here and
        # then finds the port already open instead of opening it twice
        # (which Windows refuses with "Access is denied").
        with self._conn_lock:
            want = parse_id(cfg.get("id"))
            if (self.is_open and self.port == cfg["port"]
                    and self._ser.baudrate == int(cfg["baudrate"]) and want == self.device_id):
                return
            self._close()
            ports.check_free(cfg["port"], self.name)
            self._ser = open_port(cfg["port"], int(cfg["baudrate"]))
            self.port = cfg["port"]
            ports.claim(self.port, self.name)
            self.device_id = want

    @property
    def mode_text(self):
        return "stream mode" if self.stream_mode else f"command mode, ID {self.device_id:02d}"

    def disconnect(self):
        with self._conn_lock:
            # Let a read in progress finish (≤ 1 s) so the port isn't closed under it.
            got = self._lock.acquire(timeout=1.0)
            try:
                self._close()
            finally:
                if got:
                    self._lock.release()

    def _close(self):
        if self._ser is not None:
            try:
                self._ser.close()
            finally:
                self._ser = None
                ports.release(self.port, self.name)

    def _require_open(self):
        if not self.is_open:
            raise RuntimeError(f"{self.name} is not connected")

    def _command(self, cmd):
        """Send ID<nn><cmd>; returns the text sent."""
        if self.stream_mode:
            raise RuntimeError("Commands need command mode — set the indicator ID (F-40) to 01–99")
        text = f"ID{self.device_id:02d}{cmd}"
        self._ser.reset_input_buffer()
        self._ser.write(text.encode("ascii"))
        return text

    def read(self):
        """Get one reading. Returns (sent command or None, raw line, value)."""
        self._require_open()
        with self._lock:
            self._require_open()                  # may have been closed while waiting
            if self.stream_mode:
                # Drop whatever is buffered (possibly half a line), then take
                # the next complete frame.
                self._ser.reset_input_buffer()
                self._ser.readline()
                sent = None
            else:
                sent = self._command(CMD_READ)
            raw = self._ser.readline().decode("ascii", errors="replace").strip()
        if not raw:
            raise TimeoutError("No response received — check port, baud rate and indicator ID (F-40)")
        return sent, raw, parse_frame(raw, None if self.stream_mode else self.device_id)

    def zero(self):
        return self._simple(CMD_ZERO)

    def hold(self):
        return self._simple(CMD_HOLD)

    def release(self):
        return self._simple(CMD_RELEASE)

    def _simple(self, cmd):
        self._require_open()
        with self._lock:
            self._require_open()
            sent = self._command(cmd)
            time.sleep(0.1)
            # The manual defines no reply for Z/H/R; drop anything that came back.
            self._ser.reset_input_buffer()
        return sent
