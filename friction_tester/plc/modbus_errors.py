"""Typed results of one Modbus RTU request, so the app can tell apart

* the PLC answered with an exception (it is alive; the request was refused),
* no answer at all (timeout / serial I/O), and
* the port not being open,

and always say which function code, raw offset, quantity and station were
involved. A raw offset is the zero-based address sent on the wire; it is not
an XG5000 device address (see modbus_map.CnetAreaMap for that translation).
"""

EXCEPTION_NAMES = {
    1: "illegal function",
    2: "illegal data address",
    3: "illegal data value",
    4: "server device failure",
    5: "acknowledge",
    6: "server device busy",
    8: "memory parity error",
    10: "gateway path unavailable",
    11: "gateway target failed to respond",
}

# Modbus table <-> function codes (fixed by the protocol)
READ_FC = {"coils": 1, "discrete_inputs": 2, "holding_registers": 3, "input_registers": 4}
WRITE_SINGLE_FC = {"coils": 5, "holding_registers": 6}
WRITE_MULTI_FC = {"coils": 15, "holding_registers": 16}

# Quantity limits per request (Modbus application protocol v1.1b3)
MAX_QTY = {1: 2000, 2: 2000, 3: 125, 4: 125, 15: 1968, 16: 123, 5: 1, 6: 1}


class ModbusRequestError(ValueError):
    """The request was not sent: offset/quantity invalid for the function."""


def check_request(fc, offset, count):
    if not isinstance(offset, int) or not isinstance(count, int):
        raise ModbusRequestError(f"FC{fc:02d}: offset and quantity must be integers")
    if offset < 0 or offset > 0xFFFF:
        raise ModbusRequestError(f"FC{fc:02d}: offset {offset} is outside 0..65535")
    limit = MAX_QTY.get(fc)
    if count < 1 or (limit is not None and count > limit):
        raise ModbusRequestError(f"FC{fc:02d}: quantity {count} is outside 1..{limit}")
    if offset + count - 1 > 0xFFFF:
        raise ModbusRequestError(f"FC{fc:02d}: offset {offset} + quantity {count} runs past 65535")


class PlcCommError(RuntimeError):
    """A request did not produce data. `responded` is True only when the PLC answered."""

    responded = False
    prefix = "PLC request failed"

    def __init__(self, fc=None, offset=None, count=None, station=None, detail=""):
        self.fc, self.offset, self.count, self.station, self.detail = fc, offset, count, station, detail
        self.context = ""            # e.g. "D04000-D04020 via word_read (base D00000)"
        super().__init__(self._text())

    def _request(self):
        if self.fc is None:
            return ""
        parts = []
        if self.offset is not None:
            parts.append(f"offset {self.offset} (0x{self.offset:04X})")
        if self.count is not None:
            parts.append(f"quantity {self.count}")
        if self.station is not None:
            parts.append(f"station {self.station}")
        return f"FC{self.fc:02d}" + (" " + ", ".join(parts) if parts else "")

    def _text(self):
        req = self._request()
        text = f"{self.prefix} - {req}: {self.detail}" if req else f"{self.prefix}: {self.detail}"
        return text + (f" [{self.context}]" if self.context else "")

    def with_context(self, context):
        self.context = context
        self.args = (self._text(),)
        return self

    def __str__(self):
        return self._text()


class PlcExceptionResponse(PlcCommError):
    """The PLC answered with a Modbus exception: it is alive, the request is unusable."""

    responded = True
    prefix = "PLC responding"

    def __init__(self, fc, offset, count, station, code):
        self.code = code
        name = EXCEPTION_NAMES.get(code, "unknown exception")
        super().__init__(fc, offset, count, station, f"exception {code:02d} ({name})")


class PlcNoResponse(PlcCommError):
    """No valid answer: timeout, CRC/framing error, serial I/O failure."""

    prefix = "No response from PLC"


class PlcNotOpen(PlcCommError):
    prefix = "PLC port not open"

    def __init__(self, detail="connect it on the COM Setting page"):
        super().__init__(detail=detail)
