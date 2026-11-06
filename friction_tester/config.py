"""Load and save connection settings as JSON."""

import json
from copy import deepcopy
from pathlib import Path

SETTINGS_FILE = Path(__file__).resolve().parent.parent / "settings.json"

DEFAULT_SETTINGS = {
    # LS XGB over RS232, Modbus RTU
    "plc": {
        "port": "",
        "baudrate": 9600,
        "station_id": 1,
        "bytesize": 8,
        "parity": "N",
        "stopbits": 1,
        "timeout": 1.0,
        "test_bits": "P0000, P0001, M0000",
        "reg_address": "D0000",
        "reg_points": 2,
    },
    # Two KOMEIN AM-50LW indicators on RS232. No ID setting: stream mode is
    # detected on connect, otherwise ID = loadcell number (ID01P / ID02P).
    "loadcells": [
        {"port": "", "baudrate": 9600},
        {"port": "", "baudrate": 9600},
    ],
    # Test Console conditions per cable, plus the PLC bits for its sensor
    # lamps (XGB addresses — adjust to the machine's I/O list).
    "test": {
        "select": {"model": "", "speed": 50.0, "delay": 5.0, "stroke": 50.0, "positions": [-20.0, 0.0, 20.0],
                   "load_min": 0.30, "load_max": 0.60, "clamp_count": 0,
                   "front_sensor": "P0000", "rear_sensor": "P0001"},
        "shift": {"model": "", "speed": 50.0, "delay": 5.0, "stroke": 50.0, "positions": [-20.0, 0.0, 20.0],
                  "load_min": 0.30, "load_max": 0.60, "clamp_count": 0,
                  "front_sensor": "P0002", "rear_sensor": "P0003"},
        # PLC bits the Test Console buttons write (XGB addresses, e.g. "M0100").
        # Empty = not configured: the button only reports that. Fill these in
        # from the machine's I/O list.
        "commands": {
            "servo_select": "", "alarm_reset_select": "", "origin_select": "",
            "servo_shift": "", "alarm_reset_shift": "", "origin_shift": "",
            "test_run": "", "test_stop": "", "move_origin": "",
            # Test flow handshake per cable: the app pulses start_<cable> to
            # begin that cable's test and waits for done_<cable> to turn ON.
            "start_select": "", "done_select": "",
            "start_shift": "", "done_shift": "",
        },
        "model_type": "",          # MT or AT of the loaded model (set by Model Change)
        # How the Test Console talks to the PLC (docs/plc_shared_cycle.md).
        #   "independent": per-cable start_<cable>/done_<cable> handshake above
        #   "shared_cycle": the ladder's one automatic cycle (M00029 / D05010)
        "integration": {
            "mode": "independent",
            "shared": {
                # Writing M00029 needs BOTH live_start_enabled and mapping.verified.
                "live_start_enabled": False,
                "poll_interval": 0.25,
                "start_timeout": 10.0,
                # XG5000 area -> Modbus table/offset. Default = the legacy driver's
                # assumption; NOT verified against the PLC's Cnet Modbus settings.
                "mapping": {
                    "verified": False,
                    "areas": {
                        "P": {"table": "coils", "base": 0},
                        "M": {"table": "coils", "base": 0},
                        "D": {"table": "holding_registers", "base": 0},
                    },
                },
                # Machine facts the ladder does not establish - set when confirmed:
                "at_cable_selection": "",            # "" or "operator_fixture"
                "mt_one_cycle_tests_both": False,
                "channel_cable": {"1": "", "2": ""},  # "select" / "shift"
                "direction_role": {"forward": "", "return": ""},  # "pull" / "push"
                "force_scale": 0.0, "force_unit": "",       # 0 = show raw PLC counts
                "position_scale": 0.0, "position_unit": "",
            },
        },
        # Counters on the right-hand panel; day counts reset when the date changes.
        "counters": {
            "date": "", "ok_day": 0, "ng_day": 0, "ok_total": 0, "ng_total": 0,
            "target": 0, "complete": 0, "saved_select": 0, "saved_shift": 0,
        },
        "save_dir": "data",
    },
    # NI USB-6210: one entry per analog sensor. In Differential mode channel
    # aiN uses pins AIN (+) and AI(N+8) (-), e.g. Loadcell 1 on AI0 / AI8.
    # value = (volts - offset_v) * scale  -> shown in `unit`.
    "daq": {
        "device": "",
        "terminal_config": "Differential",
        "sample_rate": 1000.0,
        "samples_per_channel": 100,
        "sensors": [
            {"name": "Loadcell 1", "channel": "ai0", "range": "±10 V", "scale": 1.0, "offset_v": 0.0, "unit": "kgf"},
            {"name": "Loadcell 2", "channel": "ai1", "range": "±10 V", "scale": 1.0, "offset_v": 0.0, "unit": "kgf"},
            {"name": "LVDT 1", "channel": "ai2", "range": "±10 V", "scale": 1.0, "offset_v": 0.0, "unit": "mm"},
            {"name": "LVDT 2", "channel": "ai3", "range": "±10 V", "scale": 1.0, "offset_v": 0.0, "unit": "mm"},
        ],
    },
}


def _merge(defaults, loaded):
    """Recursively overlay loaded values on defaults, ignoring unknown keys."""
    result = deepcopy(defaults)
    for key, value in loaded.items():
        if key not in result:
            continue
        if isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _merge(result[key], value)
        elif isinstance(result[key], list) and isinstance(value, list):
            # Same length as the defaults; each dict item merged over its default.
            result[key] = [
                _merge(d, value[i]) if i < len(value) and isinstance(d, dict) and isinstance(value[i], dict) else d
                for i, d in enumerate(result[key])
            ]
        else:
            result[key] = value
    return result


def load_settings(path=SETTINGS_FILE):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return _merge(DEFAULT_SETTINGS, json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return deepcopy(DEFAULT_SETTINGS)


def save_settings(settings, path=SETTINGS_FILE):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)
