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
