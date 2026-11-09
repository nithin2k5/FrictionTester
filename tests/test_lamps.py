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
    assert select["Clamp ON"] and select["Drive OK"] and select["Forward"] and not select["Servo"]
    assert shift["Clamp ON"] and shift["Drive OK"] and shift["Backward"] and not shift["Origin"]
    assert common == {"E-stop upper": True, "E-stop lower": True, "Start": False,
                      "SH out P00030": True, "SL out P00034": True}
