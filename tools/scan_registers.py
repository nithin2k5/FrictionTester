"""Locate the D area in the PLC's Modbus map (READ ONLY - nothing is written).

    python tools/scan_registers.py            PLC COM settings from settings.json
    python tools/scan_registers.py COM10      other port (same baud/parity/station)

Close the app first: it holds the COM port. Takes about a minute.

1. Reads every holding register 0..65535 in aligned blocks of 16 and prints
   the readable ranges (the XGB map has one block per device area, with gaps).
2. Searches for 1,000,000 stored as a 32-bit value (words 16960, 15 in either
   order). plc.pdf sets D02000 and D02100 to it, so two hits 100 apart give
   the D base: D04000 = base + 4000.
3. Prints every non-zero register so the result can be checked against the
   XG5000 monitor.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from friction_tester.config import load_settings  # noqa: E402

try:
    from pymodbus.client import ModbusSerialClient
except ImportError:
    sys.exit("pymodbus is not installed (pip install pymodbus)")

BLOCK = 16
LOW, HIGH = 1_000_000 & 0xFFFF, 1_000_000 >> 16      # 16960, 15


def main(argv):
    cfg = dict(load_settings()["plc"])
    if len(argv) > 1:
        cfg["port"] = argv[1]
    if not cfg["port"]:
        sys.exit("No PLC COM port in settings.json - pass one: python tools/scan_registers.py COM10")
    station = int(cfg["station_id"])
    client = ModbusSerialClient(port=cfg["port"], baudrate=int(cfg["baudrate"]), bytesize=int(cfg["bytesize"]),
                                parity=cfg["parity"], stopbits=int(cfg["stopbits"]), timeout=float(cfg["timeout"]))
    if not client.connect():
        sys.exit(f"Could not open {cfg['port']} (is the app still running?)")
    print(f"{cfg['port']} {cfg['baudrate']} {cfg['bytesize']}{cfg['parity']}{cfg['stopbits']} station {station}")

    regs = {}
    try:
        for start in range(0, 65536, BLOCK):
            if start % 4096 == 0:
                print(f"  scanning {start:5d}..{start + 4095:5d}", flush=True)
            try:
                r = client.read_holding_registers(start, count=BLOCK, device_id=station)
            except Exception as exc:
                print(f"  no answer at {start}: {exc}")
                continue
            if not r.isError():
                for i, v in enumerate(r.registers[:BLOCK]):
                    regs[start + i] = v
    finally:
        client.close()

    print("\n== readable holding-register ranges")
    addrs = sorted(regs)
    ranges = []
    for a in addrs:
        if ranges and a == ranges[-1][1] + 1:
            ranges[-1][1] = a
        else:
            ranges.append([a, a])
    for lo, hi in ranges:
        print(f"   {lo:5d}..{hi:5d}  (0x{lo:04X}..0x{hi:04X}, {hi - lo + 1} words)")

    print("\n== 1,000,000 as a 32-bit value (D02000 / D02100 per plc.pdf)")
    hits = []
    for a in addrs:
        nxt = regs.get(a + 1)
        if regs[a] == LOW and nxt == HIGH:
            hits.append((a, "low word first"))
        elif regs[a] == HIGH and nxt == LOW:
            hits.append((a, "high word first"))
    for a, order in hits:
        print(f"   register {a} (0x{a:04X}), {order}")
    pairs = [(a, b) for a, _ in hits for b, _ in hits if b - a == 100]
    for a, _b in pairs:
        base = a - 2000
        d4000 = base + 4000
        print(f"   -> D02000 = {a}, D02100 = {a + 100}: D base = {base}; D04000 = register {d4000}, "
              f"D04100 = register {d4000 + 100} ({'readable' if d4000 in regs else 'NOT readable'})")
    if not hits:
        print("   not found (D02000/D02100 may not hold 1,000,000 right now)")

    print("\n== non-zero registers")
    nz = [(a, regs[a]) for a in addrs if regs[a]]
    for a, v in nz[:300]:
        print(f"   {a:5d} (0x{a:04X}): {v}")
    if len(nz) > 300:
        print(f"   ... {len(nz) - 300} more")
    if not nz:
        print("   none")


if __name__ == "__main__":
    main(sys.argv)
