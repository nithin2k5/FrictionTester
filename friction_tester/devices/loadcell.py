"""KOMEIN AM-50LW digital indicator (loadcell) on RS232, standard protocol.

Port: 8 data bits, no parity, 1 stop bit; 2400-57600 baud (indicator F-41).
The indicator's ID Number (F-40) picks the mode:

* ID 000 - stream mode. The indicator sends continuously:
      ST,NT,+01234.5<CR><LF>     (NT = normal, ER = error)
* ID 01-99 - command mode. The PC sends ID<nn><cmd> with no terminator:
      P = send current value   Z = zero   H = hold   R = release hold
  and the indicator answers a P with:
      ID,01,+01234.5<CR><LF>

The data field is 8 characters including the sign; the decimal point is
wherever the indicator's F-00 setting puts it, so the number is used as sent.

The app has no ID setting: on connect it listens for stream frames, and if
none arrive it uses command mode with ID = loadcell number (L1 = 01, L2 = 02).
"""

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


def open_port(port, baudrate, tries=3, wait=0.7):
    """Open an 8N1 port. "Access is denied" is retried a few times (Windows can
    hold a just-closed port for a moment), then reported in plain words."""
    for attempt in range(tries):
        try:
            return serial.Serial(port=port, baudrate=baudrate, bytesize=8, parity="N", stopbits=1,
                                 timeout=0.5, write_timeout=0.5)
        except serial.SerialException as exc:
            denied = "access is denied" in str(exc).lower() or "permissionerror" in str(exc).lower()
            if not denied:
                raise
            if attempt == tries - 1:
                raise PortBusyError(
                    f"Windows refused {port} (Access is denied): the port is already open somewhere. "
                    f"Most often an earlier FrictionTester is still running in the background — check "
                    f"Task Manager → Details for python.exe / pythonw.exe and end it, then press CONNECT. "
                    f"Otherwise replug the USB-serial adapter or restart the PC.") from exc
            time.sleep(wait)


class PortBusyError(Exception):
    pass


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
        """Open the port and work out the indicator's mode.

        There is no ID setting on the page (as in the EOL tester): if the
        indicator is already streaming ST frames it is in stream mode,
        otherwise it is polled in command mode with its loadcell number as
        the ID (L1 -> ID01P, L2 -> ID02P).
        """
        if serial is None:
            raise RuntimeError("pyserial is not installed (pip install pyserial)")
        if not cfg["port"]:
            raise ValueError("Select a COM port")
        # One open at a time: the startup auto-connect and the COM Setting
        # CONNECT button use the same object. A second caller waits here and
        # then finds the port already open instead of opening it twice
        # (which Windows refuses with "Access is denied").
        with self._conn_lock:
            if (self.is_open and self.port == cfg["port"]
                    and self._ser.baudrate == int(cfg["baudrate"])):
                return
            self._close()
            ports.check_free(cfg["port"], self.name)
            self._ser = open_port(cfg["port"], int(cfg["baudrate"]))
            self.port = cfg["port"]
            ports.claim(self.port, self.name)
            try:
                self.device_id = STREAM_ID if self._is_streaming() else self.number
            except Exception:
                self._close()
                raise

    def _is_streaming(self):
        """True if a stream-mode frame (ST,...) arrives within ~0.5 s."""
        self._ser.reset_input_buffer()
        self._ser.readline()                       # skip a possibly partial line
        line = self._ser.readline().decode("ascii", errors="replace").strip()
        return line.upper().startswith("ST,")

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
