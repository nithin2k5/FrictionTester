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
    "daq": {
        "device": "",
        "channels": ["ai0"],
        "terminal_config": "Differential",
        "voltage_range": "±10 V",
        "sample_rate": 1000.0,
        "samples_per_channel": 100,
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
