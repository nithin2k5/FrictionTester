"""RS232 connection to the PLC via pyserial."""

try:
    import serial
    import serial.tools.list_ports
except ImportError:  # pragma: no cover - reported in the UI
    serial = None

BAUD_RATES = [1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200]
DATA_BITS = [7, 8]
PARITY_OPTIONS = ["None", "Even", "Odd", "Mark", "Space"]
STOP_BITS = ["1", "1.5", "2"]
FLOW_CONTROL = ["None", "RTS/CTS", "XON/XOFF", "DSR/DTR"]
TERMINATORS = {"CR": b"\r", "LF": b"\n", "CR+LF": b"\r\n", "None": b""}


class PLCSerial:
    def __init__(self):
        self._serial = None

    @staticmethod
    def available():
        return serial is not None

    @staticmethod
    def list_ports():
        """Return [(device, description), ...] for every serial port present."""
        if serial is None:
            return []
        return [(p.device, p.description) for p in sorted(serial.tools.list_ports.comports())]

    @property
    def is_open(self):
        return self._serial is not None and self._serial.is_open

    def connect(self, cfg):
        if serial is None:
            raise RuntimeError("pyserial is not installed (pip install pyserial)")
        if not cfg["port"]:
            raise ValueError("Select a COM port")
        self.disconnect()

        parity = {
            "None": serial.PARITY_NONE,
            "Even": serial.PARITY_EVEN,
            "Odd": serial.PARITY_ODD,
            "Mark": serial.PARITY_MARK,
            "Space": serial.PARITY_SPACE,
        }[cfg["parity"]]
        stopbits = {
            "1": serial.STOPBITS_ONE,
            "1.5": serial.STOPBITS_ONE_POINT_FIVE,
            "2": serial.STOPBITS_TWO,
        }[str(cfg["stopbits"])]
        flow = cfg["flow_control"]

        self._serial = serial.Serial(
            port=cfg["port"],
            baudrate=int(cfg["baudrate"]),
            bytesize=int(cfg["bytesize"]),
            parity=parity,
            stopbits=stopbits,
            timeout=float(cfg["timeout"]),
            write_timeout=float(cfg["timeout"]),
            rtscts=flow == "RTS/CTS",
            xonxoff=flow == "XON/XOFF",
            dsrdtr=flow == "DSR/DTR",
        )
        self._terminator = TERMINATORS.get(cfg["terminator"], b"\r")

    def disconnect(self):
        if self._serial is not None:
            try:
                self._serial.close()
            finally:
                self._serial = None

    def send(self, text):
        """Write a command and return the response line (empty string on timeout)."""
        if not self.is_open:
            raise RuntimeError("PLC port is not open")
        self._serial.reset_input_buffer()
        self._serial.write(text.encode("ascii") + self._terminator)
        self._serial.flush()
        if self._terminator:
            raw = self._serial.read_until(self._terminator)
        else:
            raw = self._serial.read(self._serial.in_waiting or 1)
        return raw.decode("ascii", errors="replace").strip()
