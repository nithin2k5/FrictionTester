"""Load and save connection settings as JSON."""

import json
from copy import deepcopy
from pathlib import Path

SETTINGS_FILE = Path(__file__).resolve().parent.parent / "settings.json"

# settings.json layout version. 2 = machine profile "PRINTING PROGRAM OLD"
# (plc.pdf, PC word interface) is this machine's integration.
SETTINGS_VERSION = 2

DEFAULT_SETTINGS = {
    "settings_version": SETTINGS_VERSION,
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
    # Test Console conditions per cable (empty until a model is loaded or they
    # are typed in), plus the PLC bits for its sensor lamps (XGB addresses —
    # adjust to the machine's I/O list).
    "test": {
        "select": {"model": "", "speed": None, "delay": None, "stroke": None, "positions": [None, None, None],
                   "load_min": None, "load_max": None, "clamp_count": 0,
                   "front_sensor": "P0000", "rear_sensor": "P0001"},
        "shift": {"model": "", "speed": None, "delay": None, "stroke": None, "positions": [None, None, None],
                  "load_min": None, "load_max": None, "clamp_count": 0,
                  "front_sensor": "P0002", "rear_sensor": "P0003"},
        # PLC bits the Test Console buttons write (XGB addresses, e.g. "M0100").
        # Empty = not configured: the button only reports that. Fill these in
        # from the machine's I/O list.
        "commands": {
            "servo_select": "", "alarm_reset_select": "", "origin_select": "",
            "servo_shift": "", "alarm_reset_shift": "", "origin_shift": "",
            # test_run: used by RUN in "independent" mode when no start_<cable>
            # bit is set. Empty: the earlier M00029 start belonged to a different
            # PLC program and is not part of this machine's profile.
            "test_run": "", "test_stop": "", "move_origin": "",
            # Test flow handshake per cable: the app pulses start_<cable> to
            # begin that cable's test and waits for done_<cable> to turn ON.
            "start_select": "", "done_select": "",
            "start_shift": "", "done_shift": "",
        },
        # TEMPORARY: true = RUN starts the test without scanning labels (no
        # label / part-number check; AT: press RUN again for the SHIFT stage).
        # Set false to bring back the scan flow.
        "skip_scan": True,
        "model_type": "",          # MT or AT of the loaded model (set by Model Change)
        "part_number": "",         # part number of the loaded model; scanned labels must contain it
        # How the Test Console talks to the PLC.
        #   "pc_interface": THIS machine - plc.pdf "PRINTING PROGRAM OLD", PC word
        #                   interface D04100-D04102 / D04000-D04020 / D04200-D04213
        #                   (docs/plc_pc_interface.md, settings in machine_profile below)
        #   "independent":  per-cable start_<cable>/done_<cable> handshake above
        #   "shared_cycle": a different ladder ("NEW NLF MC KOREA", M00029 / D05010;
        #                   docs/plc_shared_cycle.md) - not this machine's program
        "integration": {
            "mode": "pc_interface",
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
        # PRINTING PROGRAM OLD profile (friction_tester/plc/pc_profile.py).
        # Nothing is written to the PLC until mapping.verified, live_writes_enabled
        # and command_owner_confirmed are all true (see the commissioning table in
        # docs/plc_pc_interface.md).
        "machine_profile": {
            "id": "printing_program_old_pc_words",
            "version": 1,
            "mapping": {
                "verified": False,
                "areas": {
                    "D": {"table": "holding_registers", "base": 0},
                    "M": {"table": "coils", "base": 0},
                },
            },
            "live_writes_enabled": False,
            "command_owner_confirmed": False,     # no other program writes D04100-D04102
            "internal_bits_enabled": False,       # read M00051/52/61/62 (M-area mapping verified)
            "poll_interval": 0.1,
            "timeouts": {
                "echo": 1.5, "accept": 3.0, "stroke": 30.0, "home": 60.0, "meas_start": 8.0,
                "release": 3.0, "meas_dwell": 1.0, "meas_settle": 0.5, "stroke_min_hold": 1.5,
                "params": 3.0,
            },
            "measurement": {
                "force_source": "serial",          # "serial" (KOMEIN) or "daq"
                "loadcell": {"select": 1, "shift": 2},
                "daq_force": {"select": "Loadcell 1", "shift": "Loadcell 2"},
                "stroke": {"select": "", "shift": ""},   # DAQ sensor name of the LVDT, "" = none
                "judge": "peak",                   # "peak" | "strokes" | "positions"
                "strokes_per_test": 1,
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


def migrate(loaded):
    """Bring a settings.json from an earlier version up to SETTINGS_VERSION.

    Keeps every user setting; changes only what the new machine profile
    replaces, and records what it changed under "migrated".
    """
    version = int(loaded.get("settings_version", 1) or 1)
    notes = []
    if version < 2:
        test = loaded.setdefault("test", {})
        integ = test.setdefault("integration", {})
        old_mode = integ.get("mode", "independent")
        if old_mode != "pc_interface":
            integ["mode"] = "pc_interface"
            notes.append(f"integration.mode {old_mode} -> pc_interface (plc.pdf profile)")
        cmds = test.get("commands") or {}
        if str(cmds.get("test_run", "")).strip().upper() == "M00029":
            cmds["test_run"] = ""
            notes.append("commands.test_run M00029 removed (belonged to a different PLC program)")
        loaded["settings_version"] = 2
    if notes:
        loaded["migrated"] = notes
    return loaded


def load_settings(path=SETTINGS_FILE):
    try:
        with open(path, "r", encoding="utf-8") as f:
            loaded = migrate(json.load(f))
        settings = _merge(DEFAULT_SETTINGS, loaded)
        if loaded.get("migrated"):
            settings["migrated"] = loaded["migrated"]
    except (FileNotFoundError, json.JSONDecodeError):
        settings = deepcopy(DEFAULT_SETTINGS)
    return settings


def save_settings(settings, path=SETTINGS_FILE):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)
