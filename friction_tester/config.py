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
        "select": {"speed": 50.0, "delay": 5.0, "stroke": 50.0, "positions": [-20.0, 0.0, 20.0],
                   "load_min": 0.30, "load_max": 0.60, "clamp_count": 0,
                   "front_sensor": "P0000", "rear_sensor": "P0001"},
        "shift": {"speed": 50.0, "delay": 5.0, "stroke": 50.0, "positions": [-20.0, 0.0, 20.0],
                  "load_min": 0.30, "load_max": 0.60, "clamp_count": 0,
                  "front_sensor": "P0002", "rear_sensor": "P0003"},
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
