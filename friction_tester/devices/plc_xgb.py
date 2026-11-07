"""LS XGB PLC over RS232, using Modbus RTU (8N1), as in the EOL tester.

Bit devices are named XGB style: a device letter, the word number in decimal,
then the bit within that word as one hex digit. M0067 is word 6 bit 7, so the
Modbus coil number is word * 16 + bit:

    M0067 -> 103     M009A -> 154     M1000 -> 1600     P0004 -> 4

Word devices (D0001) map straight onto holding registers.
"""

import struct
import threading
import time

from . import ports

try:
    import serial.tools.list_ports
except ImportError:  # pragma: no cover - reported in the UI
    serial = None

try:
    from pymodbus.client import ModbusSerialClient
except ImportError:  # pragma: no cover - reported in the UI
    ModbusSerialClient = None

BAUD_RATES = [9600, 19200, 38400, 57600, 115200]
CONNECT_RETRIES = 3


def bit_address(address):
    """Modbus coil number for an XGB bit address such as M009A or P0004."""
    address = address.strip().upper()
    digits = address[1:]
    if not address or not address[0].isalpha() or not digits:
        raise ValueError(f"Invalid bit address {address!r} (use e.g. P0000 or M0010)")
    try:
        return int(digits[:-1] or "0") * 16 + int(digits[-1], 16)
    except ValueError:
        raise ValueError(f"Invalid bit address {address!r} (use e.g. P0000 or M0010)")


def register_address(address):
    """Holding register number for D0001, 0x0001 or a plain number."""
    address = address.strip().upper()
    try:
        if address.startswith("D"):
            return int(address[1:])
        if address.startswith("0X"):
            return int(address, 16)
        return int(address)
    except ValueError:
        raise ValueError(f"Invalid register address {address!r} (use e.g. D0001)")


def parse_bit_list(text):
    """'P0000, P0001, M0010' -> ['P0000', 'P0001', 'M0010'] (validated)."""
    items = [a.strip().upper() for a in text.replace(";", ",").split(",") if a.strip()]
    for a in items:
        bit_address(a)
    return items


def registers_as_floats(registers):
    """Interpret register pairs as 32-bit floats in the two common word orders."""
    out = []
    for i in range(0, len(registers) - 1, 2):
        hi, lo = registers[i], registers[i + 1]
        big = struct.unpack(">f", struct.pack(">HH", hi, lo))[0]
        swapped = struct.unpack(">f", struct.pack(">HH", lo, hi))[0]
        out.append((i, big, swapped))
    return out


class XGBPlc:
    def __init__(self):
        self._client = None
        self._station = 1
        self._lock = threading.Lock()   # one Modbus transaction at a time
        self.port = ""

    @staticmethod
    def available():
        return ModbusSerialClient is not None

    @staticmethod
    def list_ports():
        if serial is None:
            return []
        return [(p.device, p.description) for p in sorted(serial.tools.list_ports.comports())]

    @property
    def is_open(self):
        return self._client is not None and self._client.is_socket_open()

    def connect(self, cfg):
        """Open the port and confirm the PLC answers (read coil 0), with retries."""
        if ModbusSerialClient is None:
            raise RuntimeError("pymodbus is not installed (pip install pymodbus)")
        if not cfg["port"]:
            raise ValueError("Select a COM port")
        station = int(cfg["station_id"])
        if not 0 <= station <= 247:
            raise ValueError("Station ID must be 0–247")

        self.disconnect()
        ports.check_free(cfg["port"], "the PLC")
        client = ModbusSerialClient(
            port=cfg["port"],
            baudrate=int(cfg["baudrate"]),
            bytesize=int(cfg["bytesize"]),
            parity=cfg["parity"],
            stopbits=int(cfg["stopbits"]),
            timeout=float(cfg["timeout"]),
        )
        last_error = "no response"
        for attempt in range(CONNECT_RETRIES):
            try:
                if client.connect():
                    resp = client.read_coils(0, count=1, device_id=station)
                    if not resp.isError():
                        self._client, self._station, self.port = client, station, cfg["port"]
                        ports.claim(self.port, "the PLC")
                        return
                    last_error = str(resp)
                else:
                    last_error = f"could not open {cfg['port']}"
            except Exception as exc:
                last_error = str(exc)
            if attempt < CONNECT_RETRIES - 1:
                time.sleep(1)
        client.close()
        raise ConnectionError(f"PLC did not respond after {CONNECT_RETRIES} attempts: {last_error}")

    def disconnect(self):
        if self._client is not None:
            try:
                self._client.close()
            finally:
                self._client = None
                ports.release(self.port, "the PLC")

    def _require_open(self):
        if not self.is_open:
            raise RuntimeError("Not connected to PLC")

    def read_bits(self, addresses):
        """[(address, True/False or error string), ...] for XGB bit addresses."""
        self._require_open()
        results = []
        with self._lock:
            for addr in addresses:
                resp = self._client.read_coils(bit_address(addr), count=1, device_id=self._station)
                results.append((addr, "error" if resp.isError() else bool(resp.bits[0])))
        return results

    def write_bit(self, address, value):
        self._require_open()
        with self._lock:
            resp = self._client.write_coil(bit_address(address), bool(value), device_id=self._station)
        if resp.isError():
            raise RuntimeError(f"Write to {address} failed: {resp}")

    # ---- raw Modbus access, used by friction_tester.plc.modbus_map ----------
    # The helpers above assume every XGB bit is coil (word*16 + bit) and every
    # D word is holding register n, ignoring the area letter. That mapping is
    # not verified for this machine; the shared-cycle integration goes through
    # an explicit, configurable mapping instead (plc/modbus_map.py).
    _READERS = {"coils": "read_coils", "discrete_inputs": "read_discrete_inputs",
                "holding_registers": "read_holding_registers", "input_registers": "read_input_registers"}

    def read_table(self, table, address, count):
        """Read `count` bits/registers from a Modbus table at a raw address."""
        self._require_open()
        method = self._READERS[table]
        with self._lock:
            resp = getattr(self._client, method)(address, count=count, device_id=self._station)
        if resp.isError():
            raise RuntimeError(f"Modbus {table} read at {address} x{count} failed: {resp}")
        if table in ("coils", "discrete_inputs"):
            return [bool(b) for b in resp.bits[:count]]
        return list(resp.registers[:count])

    def write_coil_raw(self, address, value):
        """Write one coil at a raw Modbus address (function 05)."""
        self._require_open()
        with self._lock:
            resp = self._client.write_coil(address, bool(value), device_id=self._station)
        if resp.isError():
            raise RuntimeError(f"Modbus coil write at {address} failed: {resp}")

    def read_registers(self, address, count=1):
        """Read `count` holding registers starting at a D address."""
        if not 1 <= count <= 125:
            raise ValueError("Points must be between 1 and 125")
        self._require_open()
        start = register_address(address)
        with self._lock:
            resp = self._client.read_holding_registers(start, count=count, device_id=self._station)
        if resp.isError():
            raise RuntimeError(f"Error reading registers at {address}: {resp}")
        return start, list(resp.registers)
