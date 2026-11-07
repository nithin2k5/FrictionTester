"""Switch the PRINTING PROGRAM OLD profile on for testing on the machine.

    python tools/enable_machine_test.py            enable (backs up settings.json first)
    python tools/enable_machine_test.py --disable  switch PLC writes off again
    python tools/enable_machine_test.py --show     print the current values only

Edits settings.json next to main.py in place; every other setting (COM ports,
counters, ...) is kept. A copy is saved as settings.json.bak-YYYYMMDD-HHMMSS.

What "enable" sets (and what it deliberately leaves off):
  integration.mode                 pc_interface
  mapping.verified                 unchanged - set it by hand only after the XG5000
                                   read-back and targeted reads match
                                   (docs/xg5000_modbus_verification.md, tools/plc_diag.py)
  live_writes_enabled              true
  command_owner_confirmed          true   <- no other program writes D04100-D04102
  internal_bits_enabled            true   (M00051.. completion flags read natively)
  measurement judge/sign/bounds    peak / absolute / inclusive  (commissioning values)
  mt_parallel_verified             unchanged (false: test AT first)
  timeouts.alarm_reset_pulse       unchanged (None: alarm reset stays disabled)
  commissioning.*                  unchanged - nothing is marked "verified"
"""

import datetime
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from friction_tester.config import SETTINGS_FILE, load_settings, save_settings  # noqa: E402

KEYS = [("integration.mode", ("test", "integration", "mode")),
        ("mapping.verified", ("test", "machine_profile", "mapping", "verified")),
        ("live_writes_enabled", ("test", "machine_profile", "live_writes_enabled")),
        ("command_owner_confirmed", ("test", "machine_profile", "command_owner_confirmed")),
        ("internal_bits_enabled", ("test", "machine_profile", "internal_bits_enabled")),
        ("stroke_completion", ("test", "machine_profile", "stroke_completion")),
        ("mt_parallel_verified", ("test", "machine_profile", "mt_parallel_verified")),
        ("measurement.judge", ("test", "machine_profile", "measurement", "judge")),
        ("measurement.force_sign", ("test", "machine_profile", "measurement", "force_sign")),
        ("measurement.limit_bounds", ("test", "machine_profile", "measurement", "limit_bounds")),
        ("timeouts.alarm_reset_pulse", ("test", "machine_profile", "timeouts", "alarm_reset_pulse"))]

ENABLE = {"integration.mode": "pc_interface", "live_writes_enabled": True,
          "command_owner_confirmed": True, "internal_bits_enabled": True,
          "measurement.judge": "peak", "measurement.force_sign": "absolute",
          "measurement.limit_bounds": "inclusive"}
DISABLE = {"mapping.verified": False, "live_writes_enabled": False, "command_owner_confirmed": False}


def get(d, path):
    for k in path:
        d = d[k]
    return d


def put(d, path, value):
    for k in path[:-1]:
        d = d[k]
    d[path[-1]] = value


def show(settings):
    for name, path in KEYS:
        print(f"  {name:<28} {get(settings, path)!r}")


def main(argv, path=SETTINGS_FILE):
    path = Path(path)
    settings = load_settings(path)
    if "--show" in argv:
        print(f"{path}:")
        show(settings)
        return 0
    changes = DISABLE if "--disable" in argv else ENABLE
    if path.exists():
        backup = path.with_name(f"{path.name}.bak-{datetime.datetime.now():%Y%m%d-%H%M%S}")
        shutil.copy2(path, backup)
        print(f"Backup: {backup}")
    paths = dict(KEYS)
    for name, value in changes.items():
        put(settings, paths[name], value)
    settings["test"]["machine_profile"].setdefault("notes", []).append(
        f"{datetime.datetime.now():%Y-%m-%d %H:%M} machine test {'disabled' if '--disable' in argv else 'enabled'} "
        "with tools/enable_machine_test.py")
    save_settings(settings, path)
    print(f"{'Disabled' if '--disable' in argv else 'Enabled'} in {path}:")
    show(settings)
    if not get(settings, paths["mapping.verified"]):
        print("\nmapping.verified is false: the app still writes nothing. Read the PLC's Cnet Modbus settings "
              "back in XG5000, enter them under machine_profile.mapping.cnet_areas, check them with "
              "tools/plc_diag.py against the XG5000 monitor, then set mapping.verified by hand "
              "(docs/xg5000_modbus_verification.md).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
