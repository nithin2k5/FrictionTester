"""How XG5000 device areas are exposed to the app's Modbus RTU master.

The XGB built-in Cnet port acts as a Modbus RTU *server*; which Modbus table
and offset each XG5000 area appears at is set in XG5000 (Cnet "Modbus"
settings: bit read/write area and word read/write area start addresses).
Those settings are NOT in the supplied ladder printout, and the repository
holds no record of them. The legacy driver (devices/plc_xgb.py) assumed:

    any bit device  -> coil  word*16 + bit   (area letter ignored, so P00002
                                              and M00002 would both be coil 2)
    D word          -> holding register n

That assumption is kept here as the *default* so read-only monitoring has
something to show, but the mapping is marked unverified and the app will not
write to the PLC (no M00029 start request) until `verified` is set after the
mapping has been checked against the PLC's Cnet settings.

Config (settings.json -> test.integration.shared.mapping):

    {"verified": false,
     "areas": {"P": {"table": "coils", "base": 0},
               "M": {"table": "coils", "base": 0},
               "D": {"table": "holding_registers", "base": 0}}}

For bit areas the Modbus address is base + word*16 + bit; for D it is base + word.
"""

from . import xgb_address as xa

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


class MappingError(Exception):
    pass


class ModbusMapping:
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

    def describe(self):
        parts = [f"{k}→{v['table']}+{v.get('base', 0)}" for k, v in sorted(self.areas.items())]
        return ("verified" if self.verified else "UNVERIFIED") + " (" + ", ".join(parts) + ")"


class ModbusPlcBackend:
    """XG5000-address view of a PLC reached through devices.plc_xgb.XGBPlc.

    read_bits / read_words group requests into contiguous blocks per Modbus
    table so one poll is a handful of requests, not one per address.
    """

    def __init__(self, plc, mapping, allow_writes=False):
        self.plc = plc
        self.mapping = mapping
        self.allow_writes = allow_writes

    @property
    def is_open(self):
        return self.plc.is_open

    @property
    def can_write(self):
        return self.allow_writes and self.mapping.verified

    def read_bits(self, addresses):
        """{address: bool} for XG5000 bit addresses."""
        located = {a: self.mapping.locate(a) for a in addresses}
        out = {}
        by_table = {}
        for addr, (table, num) in located.items():
            by_table.setdefault(table, []).append((num, addr))
        for table, items in by_table.items():
            items.sort()
            start = items[0][0]
            span = items[-1][0] - start + 1
            if span <= MAX_BITS_PER_READ:
                bits = self.plc.read_table(table, start, span)
                for num, addr in items:
                    out[addr] = bits[num - start]
            else:
                for num, addr in items:
                    out[addr] = self.plc.read_table(table, num, 1)[0]
        return out

    def read_words(self, address, count):
        """`count` raw 16-bit words starting at a D address (unsigned 0..65535)."""
        table, start = self.mapping.locate(address)
        words = []
        while count > 0:
            n = min(count, MAX_WORDS_PER_READ)
            words.extend(self.plc.read_table(table, start, n))
            start += n
            count -= n
        return words

    def write_bit(self, address, value):
        if not self.can_write:
            raise PermissionError("PLC writes are disabled: the Modbus mapping is not verified "
                                  "or live start is not enabled")
        table, num = self.mapping.locate(address)
        if table != "coils":
            raise MappingError(f"{address} maps to {table}, which cannot be written")
        self.plc.write_coil_raw(num, value)

    def write_words(self, address, values):
        """Write consecutive D words (only with a verified mapping and writes enabled)."""
        if not self.can_write:
            raise PermissionError("PLC writes are disabled: the Modbus mapping is not verified "
                                  "or live writes are not enabled")
        table, start = self.mapping.locate(address)
        if table != "holding_registers":
            raise MappingError(f"{address} maps to {table}, which cannot be written")
        self.plc.write_registers_raw(start, values)
