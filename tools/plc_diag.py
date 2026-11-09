"""Targeted, READ-ONLY check of the PC-interface registers (nothing is written).

    python tools/plc_diag.py                         plan + read with settings.json
    python tools/plc_diag.py --port COM10            other PC port
    python tools/plc_diag.py --base word_read=D00000 --base word_write=D00000
                                                     try other Cnet bases (read-only, not saved)
    python tools/plc_diag.py --raw 4 4000 1          one raw request: FC (1-4), offset, quantity
    python tools/plc_diag.py --profile pc            PLC_INFAC (plc.pdf) blocks instead of plc kd.pdf
    python tools/plc_diag.py --window                where the word areas stop answering (~50 single reads)
    python tools/plc_diag.py --find --port COM13     which baud rate / station answers on a port
                                                     (read only, ~1 min) - for "no response"
    python tools/plc_diag.py --scan                  map all four Modbus tables and find where each
                                                     Cnet area starts (read only, ~2 min; saves a JSON
                                                     dump in data/) - see tools/plc_scan.py

Close the app first: it holds the COM port.

For each PLC_INFAC interface block it prints the planned request (Cnet area,
function code, raw zero-based offset in decimal and hex, quantity, station)
and the outcome: the values, a Modbus exception (the PLC answered and refused
the address/range), or no response (timeout / I/O). When a block fails, the
first and last registers are tried on their own. There is no address sweep.

Default profile "kd": the client's reference program plc kd.pdf (D00500 axis
status, D05000 homing state, load cells, command bits M00020-M00063 through the
bit-write area). Profile "pc": the PLC_INFAC listing (plc.pdf, D04000..). Each
only applies if that program is installed. Readable zeros do not prove the mapping: compare the
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

KD_BLOCKS = [  # (native start, quantity, role) - plc kd.pdf
    ("D00500", 1, "X-axis status word (busy .0, error .1, pos complete .2, homed .4)"),
    ("D00500", 11, "X-axis status block D00500-D00510 (position D00504/05, error code D00510)"),
    ("D05000", 1, "homing state (0 idle, 10, 20)"),
    ("D00030", 2, "SELECT load cell value (DINT, raw)"),
    ("D00130", 2, "SHIFT load cell value (DINT, raw)"),
    ("D00080", 1, "recipe number"),
]
KD_BITS = ["M00020", "M00021", "M00022", "M00023", "M00030", "M00031", "M00032", "M00033", "M00034",
           "M00050", "M00062", "M00063"]
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


FIND_BAUDS = (57600, 115200, 38400, 19200, 9600)
FIND_PARITIES = ("N", "E", "O")
FIND_STATIONS = (1, 0, 2, 3)


def find_settings(port, bauds=FIND_BAUDS, parities=FIND_PARITIES, stations=FIND_STATIONS, client_cls=None):
    """Read-only: try FC02 (inputs P00000..) at each baud / parity / station. Any reply -
    data or a Modbus exception - means a Modbus server answered with those settings."""
    if client_cls is None:
        from pymodbus.client import ModbusSerialClient as client_cls
    print(f"Searching {port}: bauds {bauds}, parities {parities}, stations {stations} (read only)")
    found = []
    for baud in bauds:
        for parity in parities:
            client = client_cls(port=port, baudrate=baud, bytesize=8, parity=parity, stopbits=1,
                                timeout=0.3, retries=0)
            try:
                if not client.connect():
                    print(f"   could not open {port} - is the app (or another program) using it?")
                    return found
                for station in stations:
                    try:
                        resp = client.read_discrete_inputs(0, count=16, device_id=station)
                    except Exception:
                        continue
                    if resp is None:
                        continue
                    if resp.isError() and getattr(resp, "exception_code", None) is None:
                        continue                          # no reply / garbled
                    what = (f"exception {resp.exception_code}" if resp.isError()
                            else "inputs " + "".join("1" if b else "0" for b in resp.bits[:16]))
                    print(f"   ANSWER  {baud} 8{parity}1 station {station}: {what}")
                    found.append((baud, parity, station))
            finally:
                client.close()
    if not found:
        print("   no answer at any setting: the port is not wired to a Modbus port of the PLC "
              "(check the adapter, A/B wires, and that it goes to CH2 RS-485, not CH1 RS-232C)")
    else:
        b, par, st = found[0]
        print(f"   -> set COM Setting to {port}, {b}, 8{par}1, station {st}")
    return found


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port")
    ap.add_argument("--base", action="append", default=[], metavar="AREA=BASE",
                    help=f"override a Cnet area base, AREA in {', '.join(CNET_AREAS)}")
    ap.add_argument("--raw", nargs=3, type=int, metavar=("FC", "OFFSET", "QTY"))
    ap.add_argument("--profile", choices=("kd", "pc"), default="kd")
    ap.add_argument("--find", action="store_true",
                    help="try common baud rates, parities and stations on the port (read only)")
    ap.add_argument("--scan", action="store_true",
                    help="read-only scan of FC01-FC04: extent, content, and where each area starts")
    ap.add_argument("--window", action="store_true",
                    help="single FC03/FC04 reads at offsets 0, 30, 130, 500, 990-1010, 4000, 5010 to find the word window")
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

    if args.find:
        find_settings(cfg["port"])
        return

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
        if args.scan:
            import time
            import plc_scan
            out = ROOT / "data" / f"plc_scan_{time.strftime('%Y%m%d_%H%M%S')}.json"
            out.parent.mkdir(exist_ok=True)
            print("\nRead-only scan of the four Modbus tables (nothing is written):")
            plc_scan.run(plc.read_table, out)
            return
        if args.window:
            print("\nWord window (single registers, read only):")
            for fc, table in ((4, "input_registers"), (3, "holding_registers")):
                ok = []
                for off in [0, 30, 130, 500] + list(range(990, 1011)) + [4000, 5010]:
                    vals, err = read(plc, table, off, 1)
                    ok.append((off, err is None, vals[0] if vals else None))
                good = [o for o, y, _v in ok if y]
                print(f"   FC{fc:02d}: answered at {good}")
                print("         values: " + " ".join(f"{o}={v}" for o, y, v in ok if y and v))
            print("   With a D00000 base, offset n = Dn: compare offset 30 / 130 with D00030 / D00130 in XG5000.")
            return
        if args.raw:
            fc, off, qty = args.raw
            if fc not in TABLE_OF_FC:
                sys.exit("--raw: only read functions 1, 2, 3, 4")
            vals, err = read(plc, TABLE_OF_FC[fc], off, qty)
            print(f"\nraw FC{fc:02d} offset {off} (0x{off:04X}) quantity {qty} station {station}:")
            print(f"   {err if err else (fmt_words(vals) if fc in (3, 4) else vals)}")
            return
        for native, count, role in (KD_BLOCKS if args.profile == "kd" else BLOCKS):
            check_block(plc, mapping, station, native, count, role)
        if args.profile == "kd":
            print("\nCommand bits M00020-M00063 (bit_write area, FC01 - read only):")
            try:
                for req, items in mapping.plan_read_bits(KD_BITS):
                    print(f"   request: {req.area} (base {req.base}) -> FC{req.fc:02d} offset {req.offset} "
                          f"quantity {req.count} station {station}")
                    bits, err = read(plc, req.table, req.offset, req.count)
                    print(f"   result : {err}" if err else
                          "   " + " ".join(f"{a}:{'1' if bits[i] else '0'}" for a, i in items))
            except MappingError as exc:
                print(f"   not sent: {exc}")
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
