"""LS XGB PLC over a serial port, Modbus RTU (8N1). On this machine: built-in Cnet CH2, RS-485,
through a USB-RS485 adapter (COM10, station 1); the adapter switches the RS-485 direction, so the
port is used like any COM port.

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
    from pymodbus.exceptions import ModbusIOException, ConnectionException
except ImportError:  # pragma: no cover - reported in the UI
    ModbusSerialClient = None

    class ModbusIOException(Exception):
        pass

    class ConnectionException(Exception):
        pass

from ..plc.modbus_errors import (READ_FC, PlcCommError, PlcExceptionResponse, PlcNoResponse, PlcNotOpen,
                                 check_request)

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
        self._lock = threading.Lock()        # one Modbus transaction at a time
        self._conn_lock = threading.Lock()   # one connect/disconnect at a time
        self._cfg = None
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

        # One open at a time: the startup auto-connect and the COM Setting
        # CONNECT button use the same object. A second caller waits here and
        # then finds the port already open instead of opening it twice
        # (which Windows refuses with "Access is denied").
        with self._conn_lock:
            if self.is_open and self._cfg == self._key(cfg):
                return
            self._disconnect()
            ports.check_free(cfg["port"], "the PLC")
            self._open(cfg, station)

    @staticmethod
    def _key(cfg):
        return (cfg["port"].strip().upper(), int(cfg["baudrate"]), int(cfg["bytesize"]), cfg["parity"],
                int(cfg["stopbits"]), int(cfg["station_id"]))

    def _open(self, cfg, station):
        client = ModbusSerialClient(
            port=cfg["port"],
            baudrate=int(cfg["baudrate"]),
            bytesize=int(cfg["bytesize"]),
            parity=cfg["parity"],
            stopbits=int(cfg["stopbits"]),
            timeout=float(cfg["timeout"]),
        )
        last_error = "no response"
        busy = False
        for attempt in range(CONNECT_RETRIES):
            try:
                if client.connect():
                    busy = False
                    resp = client.read_coils(0, count=1, device_id=station)
                    if not resp.isError():
                        self._client, self._station, self.port = client, station, cfg["port"]
                        self._cfg = self._key(cfg)
                        ports.claim(self.port, "the PLC")
                        return
                    last_error = str(resp)
                else:
                    # pymodbus only says "could not open": ask Windows why.
                    client.close()
                    busy = ports.probe_busy(cfg["port"])
                    last_error = f"could not open {cfg['port']}"
            except Exception as exc:
                busy = ports.is_access_denied(exc)
                last_error = str(exc)
            if attempt < CONNECT_RETRIES - 1:
                time.sleep(1)
        client.close()
        if busy:
            raise ports.busy_error(cfg["port"])
        raise ConnectionError(f"PLC did not respond after {CONNECT_RETRIES} attempts: {last_error}")

    def disconnect(self):
        with self._conn_lock:
            self._disconnect()

    def _disconnect(self):
        if self._client is None:
            return
        # Let a Modbus transaction in progress finish (≤ 2 s) before closing.
        got = self._lock.acquire(timeout=2.0)
        try:
            if self._client is not None:
                try:
                    self._client.close()
                finally:
                    self._client, self._cfg = None, None
                    ports.release(self.port, "the PLC")
        finally:
            if got:
                self._lock.release()

    def _require_open(self):
        if not self.is_open:
            raise PlcNotOpen()

    # ---- the single transaction path ----------------------------------------
    # Every request from every thread (Test Console poll, PC-interface
    # controller, COM Setting tools) passes through _call under self._lock, so
    # requests on the serial line never interleave. Results are classified:
    # PlcExceptionResponse (the PLC answered with an exception), PlcNoResponse
    # (timeout / I/O), PlcNotOpen. Nothing is retried here.
    def _call(self, fc, offset, qty, method, *args, **kwargs):
        check_request(fc, offset, qty)
        self._require_open()
        station = self._station
        with self._lock:
            client = self._client
            if client is None:
                raise PlcNotOpen()
            try:
                resp = getattr(client, method)(offset, *args, device_id=station, **kwargs)
            except (ModbusIOException, ConnectionException, OSError) as exc:
                raise PlcNoResponse(fc, offset, qty, station, str(exc) or type(exc).__name__) from exc
            except PlcCommError:
                raise
            except Exception as exc:
                raise PlcNoResponse(fc, offset, qty, station, f"{type(exc).__name__}: {exc}") from exc
        if resp is None:
            raise PlcNoResponse(fc, offset, qty, station, "empty response")
        if resp.isError():
            code = getattr(resp, "exception_code", None)
            if isinstance(code, int) and code:
                raise PlcExceptionResponse(fc, offset, qty, station, code)
            raise PlcNoResponse(fc, offset, qty, station, str(resp))
        return resp

    def read_bits(self, addresses):
        """[(address, True/False or error string), ...] - COM Setting test tool only.

        Legacy helper: coil = word*16 + bit with the area letter ignored. The
        PC-interface profile never uses it (see plc/modbus_map.py)."""
        results = []
        for addr in addresses:
            try:
                resp = self._call(1, bit_address(addr), 1, "read_coils", count=1)
                results.append((addr, bool(resp.bits[0])))
            except PlcExceptionResponse as exc:
                results.append((addr, f"error: {exc.detail}"))
        return results

    def write_bit(self, address, value):
        """Legacy helper (independent mode): coil word*16 + bit, area letter ignored."""
        self._call(5, bit_address(address), 1, "write_coil", bool(value))

    # ---- raw Modbus access, used by friction_tester.plc.modbus_map ----------
    # Offsets here are raw, zero-based protocol addresses. Translating XG5000
    # device addresses to them is the mapping's job (plc/modbus_map.py).
    _READERS = {"coils": "read_coils", "discrete_inputs": "read_discrete_inputs",
                "holding_registers": "read_holding_registers", "input_registers": "read_input_registers"}

    def read_table(self, table, address, count):
        """Read `count` bits/registers from a Modbus table at a raw offset."""
        fc = READ_FC[table]
        resp = self._call(fc, address, count, self._READERS[table], count=count)
        if table in ("coils", "discrete_inputs"):
            bits = [bool(b) for b in resp.bits[:count]]
            if len(bits) < count:
                raise PlcNoResponse(fc, address, count, self._station, f"short reply: {len(bits)} bits")
            return bits
        regs = list(resp.registers[:count])
        if len(regs) < count:
            raise PlcNoResponse(fc, address, count, self._station, f"short reply: {len(regs)} registers")
        return regs

    def write_coil_raw(self, address, value):
        """Write one coil at a raw offset (function 05)."""
        self._call(5, address, 1, "write_coil", bool(value))

    def write_registers_raw(self, address, values):
        """Write consecutive holding registers at a raw offset (function 16)."""
        values = [int(v) & 0xFFFF for v in values]
        self._call(16, address, len(values), "write_registers", values)

    def read_registers(self, address, count=1):
        """COM Setting tool: `count` holding registers from offset = D number (legacy assumption)."""
        start = register_address(address)
        resp = self._call(3, start, count, "read_holding_registers", count=count)
        return start, list(resp.registers)
