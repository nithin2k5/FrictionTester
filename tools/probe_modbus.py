"""Find which Modbus addresses the PLC answers (READ ONLY - nothing is written).

    python tools/probe_modbus.py            uses the PLC COM settings in settings.json
    python tools/probe_modbus.py COM5       other port (same baud/parity/station)

Close the app first: it holds the COM port.

For holding and input registers it reports the highest readable address and
whether 4000 (D04000 in the current mapping) and 4100 (D04100) answer. With
the XGB built-in Modbus slave the word map starts at the "word read area"
set in XG5000 (serial port -> Modbus settings), so register 0 is not
necessarily D0000. Then it prints a few registers near the top of the range
so they can be compared with the D04000.. values in XG5000's monitor.
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

TABLES = {"holding_registers": "read_holding_registers", "input_registers": "read_input_registers",
          "coils": "read_coils", "discrete_inputs": "read_discrete_inputs"}
SPOTS = [0, 1, 100, 1000, 2000, 3000, 4000, 4020, 4100, 4102, 5000, 8000, 10000, 16000, 32000, 65535]


def main(argv):
    cfg = dict(load_settings()["plc"])
    if len(argv) > 1:
        cfg["port"] = argv[1]
    if not cfg["port"]:
        sys.exit("No PLC COM port in settings.json - pass one: python tools/probe_modbus.py COM5")
    station = int(cfg["station_id"])
    client = ModbusSerialClient(port=cfg["port"], baudrate=int(cfg["baudrate"]), bytesize=int(cfg["bytesize"]),
                                parity=cfg["parity"], stopbits=int(cfg["stopbits"]), timeout=float(cfg["timeout"]))
    if not client.connect():
        sys.exit(f"Could not open {cfg['port']} (is the app still running?)")
    print(f"{cfg['port']} {cfg['baudrate']} {cfg['bytesize']}{cfg['parity']}{cfg['stopbits']} station {station}\n")

    def ok(table, addr, count=1):
        try:
            r = getattr(client, TABLES[table])(addr, count=count, device_id=station)
        except Exception as exc:
            return None, f"no answer ({exc})"
        if r.isError():
            return None, f"exception {getattr(r, 'exception_code', '?')}"
        return (r.bits[:count] if table in ("coils", "discrete_inputs") else r.registers[:count]), ""

    try:
        for table in TABLES:
            print(f"== {table}")
            for a in SPOTS:
                val, err = ok(table, a)
                print(f"   {a:>6}: {val[0] if val else err}")
            # highest readable address (binary search; assumes one contiguous range from 0)
            if ok(table, 0)[0] is not None:
                lo, hi = 0, 65535
                while lo < hi:
                    mid = (lo + hi + 1) // 2
                    if ok(table, mid)[0] is not None:
                        lo = mid
                    else:
                        hi = mid - 1
                print(f"   highest readable address: {lo}  (range 0..{lo}, {lo + 1} points)")
                if table == "holding_registers" and lo >= 20:
                    vals, _ = ok(table, lo - 20, 21)
                    if vals:
                        print(f"   last 21 registers {lo - 20}..{lo}: {vals}")
            print()
    finally:
        client.close()


if __name__ == "__main__":
    main(sys.argv)
