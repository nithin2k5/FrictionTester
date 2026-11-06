"""IAI ROBO Cylinder controller (RCP2-RA4C actuators) over Modbus RTU.

From IAI "Serial Communication [Modbus Version]" (ME0162), which covers the
PCON / ACON / SCON / ERC2 controllers:

* SIO port, RS485 half duplex, 8N1; baud set by controller parameter
  (9600 ... 230400, 38400 by default). A PC needs an RS232/USB-RS485 converter.
* Modbus slave address = controller axis No. + 1 (axis 0-15 -> 1-16), so
  several controllers can share one line.

Registers used (function 03, two-word values are high word first):
    9000h PNOW  current position, signed 32, 0.01 mm
    9002h ALMC  present alarm code
    9005h DSS1  device status 1 (bits below)
    900Ah VNOW  current speed, signed 32, 0.01 mm/s
    900Ch CNOW  current ampere, 32, mA
Coils (function 05):
    0403h SON servo on   0407h ALRS alarm reset   040Bh HOME home return
"""

import threading
import time

try:
    from pymodbus.client import ModbusSerialClient
except ImportError:  # pragma: no cover - reported in the UI
    ModbusSerialClient = None

MODEL = "IAI ROBO Cylinder (RCP2-RA4C)"
BAUD_RATES = [9600, 14400, 19200, 28800, 38400, 57600, 76800, 115200, 230400]

REG_MONITOR = 0x9000     # 9000h-900Dh read as one block
MONITOR_COUNT = 14
COIL_SON = 0x0403
COIL_ALRS = 0x0407
COIL_HOME = 0x040B

# DSS1 (9005h) bits
DSS1_BITS = {
    15: "EMGS",   # emergency stop active
    13: "PWR",    # controller ready
    12: "SV",     # servo on
    10: "ALMH",   # major alarm
    9: "ALML",    # minor (message level) alarm
    7: "BKRL",    # brake released
    5: "STP",     # pause active
    4: "HEND",    # home return complete
    3: "PEND",    # positioning complete
}


def _s32(hi, lo):
    v = (hi << 16) | lo
    return v - (1 << 32) if v & 0x80000000 else v


def decode_monitor(regs):
    """Decode the 9000h-900Dh block into engineering values."""
    dss1 = regs[5]
    flags = {name: bool(dss1 >> bit & 1) for bit, name in DSS1_BITS.items()}
    return {
        "position_mm": _s32(regs[0], regs[1]) / 100.0,
        "alarm": regs[2],
        "dss1": dss1,
        "flags": flags,
        "speed_mm_s": _s32(regs[10], regs[11]) / 100.0,
        "current_ma": (regs[12] << 16) | regs[13],
    }


class _SharedLine:
    """One Modbus client per COM port, shared by every axis on that RS485 line."""

    _lines = {}
    _registry_lock = threading.Lock()

    def __init__(self, client, baudrate):
        self.client = client
        self.baudrate = baudrate
        self.users = 0
        self.lock = threading.Lock()

    @classmethod
    def acquire(cls, port, baudrate, timeout):
        with cls._registry_lock:
            line = cls._lines.get(port)
            if line is not None and line.client.is_socket_open():
                if line.baudrate != baudrate:
                    raise ValueError(f"{port} is already open at {line.baudrate} baud — "
                                     "servos on one line must use the same baud rate")
            else:
                client = ModbusSerialClient(port=port, baudrate=baudrate, bytesize=8,
                                            parity="N", stopbits=1, timeout=timeout)
                if not client.connect():
                    raise ConnectionError(f"Could not open {port}")
                line = cls._lines[port] = cls(client, baudrate)
            line.users += 1
            return line

    @classmethod
    def release(cls, port):
        with cls._registry_lock:
            line = cls._lines.get(port)
            if line is None:
                return
            line.users -= 1
            if line.users <= 0:
                line.client.close()
                del cls._lines[port]


class IAIServo:
    def __init__(self, number):
        self.number = number
        self.port = ""
        self.axis = 0
        self._line = None

    @property
    def name(self):
        return f"Servo {self.number}"

    @property
    def slave(self):
        return self.axis + 1

    @staticmethod
    def available():
        return ModbusSerialClient is not None

    @property
    def is_open(self):
        return self._line is not None and self._line.client.is_socket_open()

    def connect(self, cfg):
        if ModbusSerialClient is None:
            raise RuntimeError("pymodbus is not installed (pip install pymodbus)")
        if not cfg["port"]:
            raise ValueError("Select a COM port")
        axis = int(cfg["axis"])
        if not 0 <= axis <= 15:
            raise ValueError("Axis No. must be 0–15")
        self.disconnect()
        line = _SharedLine.acquire(cfg["port"], int(cfg["baudrate"]), timeout=0.5)
        self._line, self.port, self.axis = line, cfg["port"], axis
        try:
            self.read_status()     # confirm this axis answers
        except Exception:
            self.disconnect()
            raise

    def disconnect(self):
        if self._line is not None:
            self._line = None
            _SharedLine.release(self.port)

    def _require_open(self):
        if not self.is_open:
            raise RuntimeError(f"{self.name} is not connected")

    def read_status(self):
        self._require_open()
        with self._line.lock:
            resp = self._line.client.read_holding_registers(
                REG_MONITOR, count=MONITOR_COUNT, device_id=self.slave)
        if resp.isError():
            raise ConnectionError(f"No valid reply from axis {self.axis} (slave {self.slave}): {resp}")
        return decode_monitor(resp.registers)

    def _write_coil(self, address, value):
        self._require_open()
        with self._line.lock:
            resp = self._line.client.write_coil(address, bool(value), device_id=self.slave)
        if resp.isError():
            raise RuntimeError(f"Write to coil {address:04X}h failed: {resp}")

    def alarm_reset(self):
        """ALRS acts on the rising edge: pulse ON then OFF."""
        self._write_coil(COIL_ALRS, True)
        time.sleep(0.05)
        self._write_coil(COIL_ALRS, False)

    def servo(self, on):
        self._write_coil(COIL_SON, on)

    def home(self):
        """Start home return (rising edge). The actuator moves."""
        self._write_coil(COIL_HOME, False)
        self._write_coil(COIL_HOME, True)
        time.sleep(0.05)
        self._write_coil(COIL_HOME, False)
