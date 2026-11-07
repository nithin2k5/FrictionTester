"""Targeted, READ-ONLY check of the PC-interface registers (nothing is written).

    python tools/plc_diag.py                         plan + read with settings.json
    python tools/plc_diag.py --port COM10            other PC port
    python tools/plc_diag.py --base word_read=D00000 --base word_write=D00000
                                                     try other Cnet bases (read-only, not saved)
    python tools/plc_diag.py --raw 4 4000 1          one raw request: FC (1-4), offset, quantity

Close the app first: it holds the COM port.

For each PLC_INFAC interface block it prints the planned request (Cnet area,
function code, raw zero-based offset in decimal and hex, quantity, station)
and the outcome: the values, a Modbus exception (the PLC answered and refused
the address/range), or no response (timeout / I/O). When a block fails, the
first and last registers are tried on their own. There is no address sweep.

These expectations only apply if the installed PLC program is the PLC_INFAC
listing (plc.pdf, PRINTING PROGRAM OLD.xgwx); the NLF program has no
D04000/D04100 interface. Readable zeros do not prove the mapping: compare the
values with XG5000's online device monitor, ideally a status bit you can
change (servo ON, a cylinder sensor).
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from friction_tester.config import load_settings  # noqa: E402
from friction_tester.devices.plc_xgb import XGBPlc  # noqa: E402
from friction_tester.plc.modbus_errors import PlcCommError, ModbusRequestError, READ_FC  # noqa: E402
from friction_tester.plc.modbus_map import CnetAreaMap, MappingError, CNET_AREAS  # noqa: E402

BLOCKS = [  # (native start, quantity, role) - PLC_INFAC interface (plc.pdf pages 29-31)
    ("D04000", 1, "first command echo word (M0700)"),
    ("D04003", 3, "status words (M0800-M0802)"),
    ("D04100", 3, "PC command words - READ ONLY here"),
    ("D04200", 14, "parameter words (M0750-M0763)"),
    ("D04000", 21, "the app's poll block D04000-D04020"),
]
INPUTS = [f"P0000{d:X}" for d in range(16)]
TABLE_OF_FC = {fc: t for t, fc in READ_FC.items()}


def fmt_words(vals):
    return " ".join(f"{v}(0x{v:04X})" for v in vals)


def read(plc, table, offset, count):
    try:
        return plc.read_table(table, offset, count), None
    except (PlcCommError, ModbusRequestError) as exc:
        return None, exc


def check_block(plc, mapping, station, native, count, role):
    print(f"\n{native} x{count} - {role}")
    try:
        req = mapping.plan_read_words(native, count)
    except MappingError as exc:
        print(f"   not sent: {exc}")
        return
    print(f"   request: {req.area} (base {req.base}) -> FC{req.fc:02d} offset {req.offset} "
          f"(0x{req.offset:04X}) quantity {req.count} station {station}")
    vals, err = read(plc, req.table, req.offset, req.count)
    if err is None:
        print(f"   values : {fmt_words(vals)}")
        return
    print(f"   result : {err}")
    if count > 1:
        for off, name in ((req.offset, native), (req.offset + count - 1, "last of block")):
            v, e = read(plc, req.table, off, 1)
            print(f"   single offset {off} (0x{off:04X}) [{name}]: {fmt_words(v) if e is None else e}")


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port")
    ap.add_argument("--base", action="append", default=[], metavar="AREA=BASE",
                    help=f"override a Cnet area base, AREA in {', '.join(CNET_AREAS)}")
    ap.add_argument("--raw", nargs=3, type=int, metavar=("FC", "OFFSET", "QTY"))
    args = ap.parse_args(argv)

    settings = load_settings()
    cfg = dict(settings["plc"])
    if args.port:
        cfg["port"] = args.port
    if not cfg["port"]:
        sys.exit("No PLC COM port in settings.json - pass --port COM10")
    mcfg = dict(settings["test"]["machine_profile"].get("mapping") or {})
    areas = {k: dict(v) for k, v in (mcfg.get("cnet_areas") or {}).items()}
    for item in args.base:
        name, _, base = item.partition("=")
        if name not in CNET_AREAS or not base:
            sys.exit(f"--base {item!r}: use AREA=BASE with AREA in {', '.join(CNET_AREAS)}")
        areas.setdefault(name, {})["plc_base"] = base
    mcfg["cnet_areas"] = areas
    mapping = CnetAreaMap(mcfg)

    plc = XGBPlc()
    try:
        plc.connect(cfg)
    except Exception as exc:
        sys.exit(f"Could not connect to {cfg['port']}: {exc} (is the app still running?)")
    station = int(cfg["station_id"])
    print(f"PC port {cfg['port']} {cfg['baudrate']} {cfg['bytesize']}{cfg['parity']}{cfg['stopbits']}, "
          f"station {station}, timeout {cfg['timeout']} s")
    print(f"Mapping: {mapping.describe()}" + ("  [--base overrides, not saved]" if args.base else ""))
    try:
        if args.raw:
            fc, off, qty = args.raw
            if fc not in TABLE_OF_FC:
                sys.exit("--raw: only read functions 1, 2, 3, 4")
            vals, err = read(plc, TABLE_OF_FC[fc], off, qty)
            print(f"\nraw FC{fc:02d} offset {off} (0x{off:04X}) quantity {qty} station {station}:")
            print(f"   {err if err else (fmt_words(vals) if fc in (3, 4) else vals)}")
            return
        for native, count, role in BLOCKS:
            check_block(plc, mapping, station, native, count, role)
        print("\nP inputs P00000-P0000F (bit_read area):")
        try:
            plans = mapping.plan_read_bits(INPUTS)
            for req, items in plans:
                print(f"   request: {req.area} (base {req.base}) -> FC{req.fc:02d} offset {req.offset} "
                      f"quantity {req.count} station {station}")
                bits, err = read(plc, req.table, req.offset, req.count)
                if err:
                    print(f"   result : {err}")
                else:
                    print("   " + " ".join(f"{a[-1]}:{'1' if bits[i] else '0'}" for a, i in items))
        except MappingError as exc:
            print(f"   not sent: {exc}")
        print("\nCompare every value with XG5000's online device monitor (D04000.., P0000x). Zeros alone "
              "prove nothing; check one status that you can change.")
    finally:
        plc.disconnect()


if __name__ == "__main__":
    main(sys.argv[1:])
