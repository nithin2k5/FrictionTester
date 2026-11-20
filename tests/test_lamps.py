"""Test Console lamps: both cables + machine row read in one bit request (no hardware, no Tk window)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from friction_tester.config import DEFAULT_SETTINGS as DEFAULTS  # noqa: E402
from friction_tester.ui.test_console import SENSOR_LAMPS, TestConsolePage  # noqa: E402

ON = {"P00000", "P00003", "P00006", "P00009", "P0000A", "P0000B", "P0000D", "P0000F",   # machine scan
      "P00013", "P00017", "P00018", "P00019", "P00030", "P00034"}                        # 2026-11-09


class FakeBackend:
    def __init__(self):
        self.calls = []

    def read_bits(self, addresses):
        self.calls.append(list(addresses))
        from friction_tester.plc import xgb_address as xa
        return {a: f"P{xa.parse(a).word:04d}{xa.parse(a).bit:X}" in ON for a in addresses}


class Page:
    SIDES = TestConsolePage.SIDES
    _read_sensor_lamps = TestConsolePage._read_sensor_lamps

    def __init__(self):
        import copy
        self.settings = copy.deepcopy(DEFAULTS)
        self.be = FakeBackend()

    def _lamp_backend(self):
        return self.be


def test_machine_scan_lights_the_expected_lamps_in_one_request():
    page = Page()
    sides = [{"lamps": {}}, {"lamps": {}}]
    common = {}
    page._read_sensor_lamps(sides, common)
    assert len(page.be.calls) == 1
    select, shift = (s["lamps"] for s in sides)
    assert set(select) == set(shift) == set(SENSOR_LAMPS)
    # backup 2026-11-08: P00006 = SL Extension SOL Home -> "Backward" (P00007 End -> "Forward")
    assert select["Clamp ON"] and select["Drive OK"] and select["Backward"] and not select["Forward"]
    assert not select["Servo"]
    assert shift["Clamp ON"] and shift["Drive OK"] and shift["Backward"] and not shift["Origin"]
    assert common == {"E-stop top": True, "E-stop bottom": True, "Start": False,
                      "SL servo out": True, "SH servo out": True, "NG box": False}


def test_drive_lamps_follow_the_drive_that_moves_the_cable():
    """Servo / Drive OK / Origin lamps read the inputs of the drive that moves each cable
    (pc_profile.DRIVE_OF, drives exchanged on the machine) - the same ones the jog/test checks use."""
    from friction_tester.plc import pc_profile as pp
    for cable in pp.AXES:
        cfg = DEFAULTS["test"][cable]
        for setting, status in (("servo_input", "servo_on"), ("drive_ok_input", "alarm_normal"),
                                ("origin_input", "origin_complete")):
            assert cfg[setting] == pp.STATUS[cable][status][2], (cable, setting)


def test_servo_out_lamps_follow_the_drive_that_moves_the_cable():
    """The machine row's servo-output lamps are named by cable, like the panels above them."""
    lamps = DEFAULTS["test"]["machine_lamps"]
    assert lamps["SL servo out"] == TestConsolePage.SERVO_OUT["select"]
    assert lamps["SH servo out"] == TestConsolePage.SERVO_OUT["shift"]


def test_saved_settings_get_the_current_drive_lamps():
    """A settings.json saved by an older version keeps no stale Origin / Servo / Drive OK address."""
    from friction_tester.config import load_settings
    import json, tempfile, os
    old = {"settings_version": 8, "test": {
        "select": {"origin_input": "P00016", "servo_input": "P00010", "drive_ok_input": "P00013"},
        "shift": {"origin_input": "P00012", "servo_input": "P00014", "drive_ok_input": "P00017"}}}
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "settings.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(old, f)
        s = load_settings(path)
    for cable in ("select", "shift"):
        for key in ("origin_input", "servo_input", "drive_ok_input"):
            assert s["test"][cable][key] == DEFAULTS["test"][cable][key], (cable, key)
    assert any("origin_input" in n for n in s["migrated"])
