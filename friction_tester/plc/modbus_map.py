"""How XG5000 device addresses reach the app's Modbus RTU master.

Two vocabularies are kept strictly apart:

* native PLC address - an XG5000 device address: D04000, M00051, P0000A.
  Bit devices: decimal word + one HEX bit digit (P0000A = word 0, bit 10).
* raw Modbus offset  - the zero-based address sent on the wire with a
  function code: FC04 offset 4000 (0x0FA0). Tools that display one-based
  "register numbers" (40001, 30001 ...) add their own offset; nothing here does.

CnetAreaMap (the PC-interface profile) models the XGB built-in Cnet Modbus
server as XG5000 configures it: four areas, each with a PLC start address,
and the Modbus functions that reach it are fixed by the area:

    bit_read    FC02 read discrete inputs
    bit_write   FC01 read coils, FC05/FC15 write coils
    word_read   FC04 read input registers
    word_write  FC03 read holding registers, FC06/FC16 write registers

raw offset = native index - base index, inside the base's device only
(bit areas count bits: word*16 + bit; word areas count words). An address in
another device, or below the base, is NOT reachable through that area - the
map says so instead of guessing. `accessible_count` (optional, per area) is
the number of raw offsets confirmed readable from 0; requests past it are
refused before they are sent.

Config (settings.json -> test.machine_profile.mapping):

    {"schema": "xgb_cnet_areas",
     "verified": false,            # bases checked against the live XG5000 read-back
     "verified_note": "",          # who / when / how
     "cnet_areas": {
        "bit_read":   {"plc_base": "P00000", "accessible_count": null},
        "bit_write":  {"plc_base": "M00000", "accessible_count": null},
        "word_read":  {"plc_base": "D00000", "accessible_count": null},
        "word_write": {"plc_base": "D00000", "accessible_count": null}},
     "word_read_order": ["word_read", "word_write"]}

Word-area bases are written in WORD context: "D00000", "P0020" = P word 20.
Bit-area bases are bit addresses: "M00000", "P00100" = P word 10 bit 0.
The defaults are the CANDIDATE values for the PLC_INFAC interface, not the
machine's confirmed configuration; `verified` stays false until checked.

ModbusMapping is the older per-device-letter mapping kept for the shared-cycle
integration (test.integration.shared.mapping):

    {"verified": false,
     "areas": {"P": {"table": "coils", "base": 0},
               "M": {"table": "coils", "base": 0},
               "D": {"table": "holding_registers", "base": 0}}}
"""

import re
from dataclasses import dataclass

from . import xgb_address as xa
from .modbus_errors import READ_FC, PlcCommError, check_request

BIT_TABLES = ("coils", "discrete_inputs")
WORD_TABLES = ("holding_registers", "input_registers")
MAX_BITS_PER_READ = 2000
MAX_WORDS_PER_READ = 120

DEFAULT_MAPPING = {
    "verified": False,
    "areas": {
        "P": {"table": "coils", "base": 0},
        "M": {"table": "coils", "base": 0},
        "D": {"table": "holding_registers", "base": 0},
    },
}

CNET_AREAS = {   # name -> (Modbus table used for reads, kind, functions)
    "bit_read": ("discrete_inputs", "bit", "FC02"),
    "bit_write": ("coils", "bit", "FC01 read, FC05/FC15 write"),
    "word_read": ("input_registers", "word", "FC04"),
    "word_write": ("holding_registers", "word", "FC03 read, FC06/FC16 write"),
}

DEFAULT_CNET_MAPPING = {
    "schema": "xgb_cnet_areas",
    "verified": False,
    "verified_note": "",
    "cnet_areas": {
        "bit_read": {"plc_base": "P00000", "accessible_count": None},
        "bit_write": {"plc_base": "M00000", "accessible_count": None},
        "word_read": {"plc_base": "D00000", "accessible_count": None},
        "word_write": {"plc_base": "D00000", "accessible_count": None},
    },
    "word_read_order": ["word_read", "word_write"],
}

_WORD_BASE = re.compile(r"^([PMKLFD])(\d+)$")


class MappingError(Exception):
    pass


class AreaRangeError(MappingError):
    """Inside an area's device, but past its confirmed accessible range."""


@dataclass(frozen=True)
class ModbusRequest:
    table: str            # Modbus table (determines the function code)
    offset: int           # raw zero-based offset
    count: int
    native: str = ""      # e.g. "D04000-D04020"
    area: str = ""        # Cnet area name or legacy device letter
    base: str = ""        # the area's PLC base, as configured

    @property
    def fc(self):
        return READ_FC[self.table]

    def describe(self):
        where = f" via {self.area}" + (f" (base {self.base})" if self.base else "") if self.area else ""
        return f"{self.native}{where} = FC{self.fc:02d} offset {self.offset} (0x{self.offset:04X}) x{self.count}"


def _native_range(address, count):
    if count == 1:
        return address
    a = xa.parse(address)
    if a.bit is None:
        return f"{address}-{a.area}{a.word + count - 1:05d}"
    return f"{address} +{count} bits"


# ---------------------------------------------------------------- XGB Cnet areas
class CnetAreaMap:
    schema = "xgb_cnet_areas"

    def __init__(self, cfg=None):
        cfg = cfg or DEFAULT_CNET_MAPPING
        self.verified = bool(cfg.get("verified", False))
        self.note = cfg.get("verified_note", "")
        given = cfg.get("cnet_areas") or {}
        self.areas = {}
        for name, (table, kind, fcs) in CNET_AREAS.items():
            a = dict(given.get(name) or {})
            base = (a.get("plc_base") or "").strip().upper() or None
            count = a.get("accessible_count")
            self.areas[name] = {"table": table, "kind": kind, "functions": fcs, "plc_base": base,
                                "accessible_count": int(count) if count not in (None, "") else None,
                                "base_index": None, "device": None, "problem": None}
            if base:
                try:
                    self.areas[name]["device"], self.areas[name]["base_index"] = self._parse_base(base, kind)
                except ValueError as exc:
                    self.areas[name]["problem"] = str(exc)
        order = cfg.get("word_read_order") or ["word_read", "word_write"]
        self.word_read_order = [n for n in order if n in ("word_read", "word_write")] or ["word_read"]

    @staticmethod
    def _parse_base(base, kind):
        if kind == "word":
            m = _WORD_BASE.match(base)
            if not m:
                raise ValueError(f"word-area base {base!r} must be a device and decimal word, e.g. D00000")
            return m.group(1), int(m.group(2))
        a = xa.parse(base)
        if a.area not in xa.BIT_AREAS:
            raise ValueError(f"bit-area base {base!r} must be a bit device address, e.g. M00000")
        return a.area, a.word * 16 + a.bit

    def _offset(self, name, device, index, count):
        """Raw offset of `index` in area `name`, or None if the area cannot reach it."""
        a = self.areas[name]
        if a["problem"] or a["device"] != device:
            return None
        off = index - a["base_index"]
        if off < 0:
            return None
        if a["accessible_count"] is not None and off + count > a["accessible_count"]:
            raise AreaRangeError(f"{name} (base {a['plc_base']}) is confirmed for offsets "
                                 f"0..{a['accessible_count'] - 1}; offset {off} x{count} is past that")
        return off

    def _unreachable(self, native, names):
        parts = []
        for n in names:
            a = self.areas[n]
            base = a["plc_base"] or "not set"
            parts.append(f"{n} base {base}" + (f" ({a['problem']})" if a["problem"] else ""))
        return MappingError(f"{native} is not reachable through " + " or ".join(parts))

    def _plan(self, names, device, index, count, native):
        range_errors = []
        for n in names:
            try:
                off = self._offset(n, device, index, count)
            except AreaRangeError as exc:
                range_errors.append(str(exc))
                continue
            if off is not None:
                a = self.areas[n]
                return ModbusRequest(a["table"], off, count, native, n, a["plc_base"])
        if range_errors:
            raise AreaRangeError(f"{native}: " + "; ".join(range_errors))
        raise self._unreachable(native, names)

    def plan_read_words(self, address, count):
        a = xa.parse(address)
        if a.bit is not None:
            raise MappingError(f"{address}: read the whole word")
        return self._plan(self.word_read_order, a.area, a.word, count, _native_range(address, count))

    def plan_write_words(self, address, count):
        a = xa.parse(address)
        if a.bit is not None:
            raise MappingError(f"{address}: write the whole word")
        req = self._plan(["word_write"], a.area, a.word, count, _native_range(address, count))
        return ModbusRequest("holding_registers", req.offset, count, req.native, req.area, req.base)

    def locate_bit(self, address):
        """(area name, raw offset) for one bit: bit_read (FC02) first, then bit_write (FC01)."""
        a = xa.parse(address)
        if a.bit is None:
            raise MappingError(f"{address} is a word address, not a bit")
        req = self._plan(["bit_read", "bit_write"], a.area, a.word * 16 + a.bit, 1, address)
        return req.area, req.offset

    def plan_write_bit(self, address):
        a = xa.parse(address)
        if a.bit is None:
            raise MappingError(f"{address} is a word address, not a bit")
        return self._plan(["bit_write"], a.area, a.word * 16 + a.bit, 1, address)

    def plan_read_bits(self, addresses):
        """[(ModbusRequest, [(address, index in the block), ...]), ...] in contiguous blocks."""
        by_area = {}
        for addr in addresses:
            name, off = self.locate_bit(addr)
            by_area.setdefault(name, []).append((off, addr))
        plans = []
        for name, items in by_area.items():
            a = self.areas[name]
            items.sort()
            block = []
            for off, addr in items:
                if block and off - block[0][0] + 1 > MAX_BITS_PER_READ:
                    plans.append(self._bit_block(name, a, block))
                    block = []
                block.append((off, addr))
            if block:
                plans.append(self._bit_block(name, a, block))
        return plans

    @staticmethod
    def _bit_block(name, a, block):
        lo, hi = block[0][0], block[-1][0]
        native = block[0][1] if lo == hi else f"{block[0][1]}..{block[-1][1]}"
        return (ModbusRequest(a["table"], lo, hi - lo + 1, native, name, a["plc_base"]),
                [(addr, off - lo) for off, addr in block])     # index inside the block

    def describe(self):
        parts = []
        for name, a in self.areas.items():
            base = a["plc_base"] or "not set"
            rng = f", {a['accessible_count']} confirmed" if a["accessible_count"] is not None else ""
            parts.append(f"{name} {base} ({a['functions']}{rng})")
        return ("verified" if self.verified else "UNVERIFIED") + " Cnet areas: " + "; ".join(parts)


# ---------------------------------------------------------------- legacy mapping
class ModbusMapping:
    schema = "device_letters"

    def __init__(self, cfg=None):
        cfg = cfg or DEFAULT_MAPPING
        self.verified = bool(cfg.get("verified", False))
        self.areas = {k.upper(): dict(v) for k, v in cfg.get("areas", {}).items()}

    def locate(self, address):
        """(table, modbus address) for one XG5000 bit or word address."""
        a = xa.parse(address)
        area = self.areas.get(a.area)
        if area is None:
            raise MappingError(f"No Modbus mapping configured for area {a.area} ({address})")
        table, base = area["table"], int(area.get("base", 0))
        if a.area in xa.WORD_AREAS:
            if a.bit is not None:
                raise MappingError(f"{address}: bits inside D words are read via the word")
            if table not in WORD_TABLES:
                raise MappingError(f"Area D is mapped to {table}, which is not a register table")
            return table, base + a.word
        if table not in BIT_TABLES:
            raise MappingError(f"Area {a.area} is mapped to {table}, which is not a bit table")
        return table, base + a.word * 16 + a.bit

    def plan_read_words(self, address, count):
        table, start = self.locate(address)
        return ModbusRequest(table, start, count, _native_range(address, count), xa.parse(address).area)

    def plan_write_words(self, address, count):
        req = self.plan_read_words(address, count)
        if req.table != "holding_registers":
            raise MappingError(f"{address} maps to {req.table}, which cannot be written")
        return req

    def plan_write_bit(self, address):
        table, num = self.locate(address)
        if table != "coils":
            raise MappingError(f"{address} maps to {table}, which cannot be written")
        return ModbusRequest(table, num, 1, address, xa.parse(address).area)

    def plan_read_bits(self, addresses):
        by_table = {}
        for addr in addresses:
            table, num = self.locate(addr)
            by_table.setdefault(table, []).append((num, addr))
        plans = []
        for table, items in by_table.items():
            items.sort()
            start, span = items[0][0], items[-1][0] - items[0][0] + 1
            if span <= MAX_BITS_PER_READ:
                plans.append((ModbusRequest(table, start, span, f"{items[0][1]}..{items[-1][1]}", table),
                              [(addr, num - start) for num, addr in items]))
            else:
                for num, addr in items:
                    plans.append((ModbusRequest(table, num, 1, addr, table), [(addr, 0)]))
        return plans

    def describe(self):
        parts = [f"{k}→{v['table']}+{v.get('base', 0)}" for k, v in sorted(self.areas.items())]
        return ("verified" if self.verified else "UNVERIFIED") + " (" + ", ".join(parts) + ")"


def make_mapping(cfg):
    """CnetAreaMap for the new schema, ModbusMapping for the older one."""
    if cfg and (cfg.get("schema") == CnetAreaMap.schema or "cnet_areas" in cfg):
        return CnetAreaMap(cfg)
    return ModbusMapping(cfg)


# ---------------------------------------------------------------- backend
class ModbusPlcBackend:
    """XG5000-address view of a PLC reached through devices.plc_xgb.XGBPlc.

    Every request is planned by the mapping (native address -> function, raw
    offset, quantity), checked against the protocol limits and then sent
    through the PLC object's single serialized transaction path. Transport
    errors are re-raised with the native range and area attached.
    """

    def __init__(self, plc, mapping, allow_writes=False):
        self.plc = plc
        self.mapping = mapping
        self.allow_writes = allow_writes
        self.last_request = None       # ModbusRequest of the last attempt (diagnostics)

    @property
    def is_open(self):
        return self.plc.is_open

    @property
    def can_write(self):
        return self.allow_writes and self.mapping.verified

    def _read(self, req):
        self.last_request = req
        check_request(req.fc, req.offset, req.count)
        try:
            return self.plc.read_table(req.table, req.offset, req.count)
        except PlcCommError as exc:
            raise exc.with_context(req.describe())

    def read_bits(self, addresses):
        """{address: bool} for XG5000 bit addresses."""
        out = {}
        for req, items in self.mapping.plan_read_bits(addresses):
            bits = self._read(req)
            for addr, i in items:
                out[addr] = bits[i]
        return out

    def read_words(self, address, count):
        """`count` raw 16-bit words starting at a D address (unsigned 0..65535)."""
        a = xa.parse(address)
        words = []
        while count > 0:
            n = min(count, MAX_WORDS_PER_READ)
            req = self.mapping.plan_read_words(f"{a.area}{a.word + len(words):05d}", n)
            words.extend(self._read(req))
            count -= n
        return words

    def write_bit(self, address, value):
        if not self.can_write:
            raise PermissionError("PLC writes are disabled: the Modbus mapping is not verified "
                                  "or live start is not enabled")
        req = self.mapping.plan_write_bit(address)
        self.last_request = req
        try:
            self.plc.write_coil_raw(req.offset, value)
        except PlcCommError as exc:
            raise exc.with_context(req.describe())

    def write_words(self, address, values):
        """Write consecutive D words (only with a verified mapping and writes enabled)."""
        if not self.can_write:
            raise PermissionError("PLC writes are disabled: the Modbus mapping is not verified "
                                  "or live writes are not enabled")
        req = self.mapping.plan_write_words(address, len(values))
        self.last_request = req
        check_request(16, req.offset, req.count)
        try:
            self.plc.write_registers_raw(req.offset, values)
        except PlcCommError as exc:
            raise exc.with_context(req.describe().replace(f"FC{req.fc:02d}", "FC16"))
