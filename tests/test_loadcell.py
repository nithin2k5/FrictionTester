"""KOMEIN AM-50LW frame parsing (no hardware)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from friction_tester.devices.loadcell import IndicatorError, parse_frame  # noqa: E402


def test_frames_from_the_manual_and_the_machine():
    assert parse_frame("ST,NT,+01234.5") == 1234.5
    assert parse_frame("ID,01,+0012.5", 1) == 12.5
    assert parse_frame("ID001,+0000.01", 1) == 0.01          # seen on the machine (joined ID)


def test_wrong_id_error_and_garbage_are_refused():
    with pytest.raises(IndicatorError, match="expected 01"):
        parse_frame("ID002,+0000.01", 1)
    with pytest.raises(IndicatorError, match="error"):
        parse_frame("ST,ER,+0000.00")
    for bad in ("XX,1", "ID001,abc", "garbage"):
        with pytest.raises(IndicatorError):
            parse_frame(bad, 1)


class FakeIndicator:
    """Answers "ID<nn>P" only for its own ID, like an AM-50LW in command mode."""

    def __init__(self, dev_id):
        self.dev_id, self.out, self.is_open = dev_id, [], True

    def reset_input_buffer(self):
        self.out.clear()

    def write(self, data):
        text = data.decode("ascii")
        if text == f"ID{self.dev_id:02d}P":
            self.out.append(f"ID{self.dev_id:03d},+0001.50\r\n".encode("ascii"))

    def readline(self):
        return self.out.pop(0) if self.out else b""


def test_find_id_uses_factory_id_when_l2_is_set_to_01():
    from friction_tester.devices.loadcell import Loadcell
    lc = Loadcell(2)
    lc._ser = FakeIndicator(1)
    assert lc._find_id() == 1
    lc.device_id = 1
    sent, raw, value = lc.read()
    assert sent == "ID01P" and value == 1.5


def test_find_id_prefers_own_number_and_falls_back_to_it():
    from friction_tester.devices.loadcell import Loadcell
    lc = Loadcell(2)
    lc._ser = FakeIndicator(2)
    assert lc._find_id() == 2
    lc._ser = FakeIndicator(42)          # nothing in 01-09 answers
    assert lc._find_id() == 2


def test_given_id_is_used_without_probing():
    from friction_tester.devices import loadcell as lcmod
    assert lcmod.parse_id("auto") is None and lcmod.parse_id("") is None and lcmod.parse_id(None) is None
    assert lcmod.parse_id("00 stream") == 0 and lcmod.parse_id("05") == 5 and lcmod.parse_id(7) == 7
    import pytest
    with pytest.raises(ValueError):
        lcmod.parse_id(150)
