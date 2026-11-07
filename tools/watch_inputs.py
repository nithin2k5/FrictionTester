"""Watch the PLC inputs P0000-P000F live (READ ONLY - nothing is written).

    python tools/watch_inputs.py            PLC COM settings from settings.json
    python tools/watch_inputs.py COM10      other port (same baud/parity/station)

Close the app first: it holds the COM port. Ctrl+C to stop.

Reads Modbus bits 0-15 from both coils (function 01) and discrete inputs
(function 02) and prints a line whenever one changes. Move a cylinder, put a
part in, press START: the table whose bit follows the sensor is the one the
P inputs live in. With the XGB Cnet server, FC02 reads the bit_read area
(machine_profile.mapping.cnet_areas.bit_read).
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from friction_tester.config import load_settings  # noqa: E402

try:
    from pymodbus.client import ModbusSerialClient
except ImportError:
    sys.exit("pymodbus is not installed (pip install pymodbus)")

# Machine I/O list
NAMES = {0x0: "SHIFT cylinder backward", 0x1: "SHIFT cylinder forward",
         0x6: "SELECT cylinder forward", 0x7: "SELECT cylinder backward",
         0xA: "SELECT part presence", 0xB: "SHIFT part presence",
         0xE: "START", 0xF: "EMERGENCY button"}
COUNT = 16


def label(n):
    return f"P000{n:X}" + (f" {NAMES[n]}" if n in NAMES else "")


def main(argv):
    cfg = dict(load_settings()["plc"])
    if len(argv) > 1:
        cfg["port"] = argv[1]
    if not cfg["port"]:
        sys.exit("No PLC COM port in settings.json - pass one: python tools/watch_inputs.py COM10")
    station = int(cfg["station_id"])
    client = ModbusSerialClient(port=cfg["port"], baudrate=int(cfg["baudrate"]), bytesize=int(cfg["bytesize"]),
                                parity=cfg["parity"], stopbits=int(cfg["stopbits"]), timeout=float(cfg["timeout"]))
    if not client.connect():
        sys.exit(f"Could not open {cfg['port']} (is the app still running?)")
    print(f"{cfg['port']} {cfg['baudrate']} {cfg['bytesize']}{cfg['parity']}{cfg['stopbits']} station {station}")

    tables = {"coils": client.read_coils, "discrete_inputs": client.read_discrete_inputs}

    def read_all():
        out = {}
        for name, read in tables.items():
            try:
                r = read(0, count=COUNT, device_id=station)
                out[name] = None if r.isError() else [bool(b) for b in r.bits[:COUNT]]
            except Exception:
                out[name] = None
        return out

    last = read_all()
    print(f"\n{'bit':<35} {'coils':>6} {'discrete_inputs':>16}")
    for n in range(COUNT):
        row = [("ERR" if last[t] is None else ("ON" if last[t][n] else "-")) for t in tables]
        print(f"{n:>2} {label(n):<32} {row[0]:>6} {row[1]:>16}")
    print("\nWatching - move a cylinder, insert a part, press START (Ctrl+C to stop)\n")
    try:
        while True:
            time.sleep(0.2)
            now = read_all()
            for t in tables:
                if now[t] is None or last[t] is None:
                    if (now[t] is None) != (last[t] is None):
                        print(f"{time.strftime('%H:%M:%S')}  {t}: {'read failed' if now[t] is None else 'reading again'}")
                    continue
                for n in range(COUNT):
                    if now[t][n] != last[t][n]:
                        print(f"{time.strftime('%H:%M:%S')}  {t:<15} bit {n:>2}  {label(n):<30} "
                              f"{'OFF -> ON' if now[t][n] else 'ON -> OFF'}", flush=True)
            last = now
    except KeyboardInterrupt:
        print("stopped")
    finally:
        client.close()


if __name__ == "__main__":
    main(sys.argv)
