"""Manual Operation view: parameter edits go through the controller; other buttons write nothing."""

import os
import sys
import tkinter as tk

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from friction_tester.plc import pc_profile as pp  # noqa: E402
from friction_tester.ui.manual_view import ManualView, lamp_state  # noqa: E402

SIDES = (("select", "SELECT CABLE", "SL"), ("shift", "SHIFT CABLE", "SH"))


class FakeCtl:
    def __init__(self):
        self.sent = []

    def submit(self, *cmd):
        self.sent.append(cmd)


class FakePage:
    pc = True

    def __init__(self):
        self.settings = {"test": {"plc_params": [2, 20, 10, 30, 50, 50, 2, 20, 10, 30, 50, 50, 5, 5],
                                  "select": {"model": "0705CAJ00511N"}}}
        self.ctl = FakeCtl()
        self.messages = []

    def _set_status(self, text, color="white", hold=3):
        self.messages.append(text)

    def show_measurement(self):
        pass


@pytest.fixture(scope="module")
def root():
    try:
        r = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    r.withdraw()
    yield r
    r.destroy()


@pytest.fixture
def view(root):
    top = tk.Toplevel(root)
    page = FakePage()
    v = ManualView(top, page, SIDES)
    yield v, page
    top.destroy()


def test_fields_show_the_model_parameters(view):
    v, _page = view
    assert v.fields[("shift", "limit_left")].get() == "50"
    assert v.fields[("select", "jog_high")].get() == "20"


def test_edited_limit_is_sent_with_all_parameters(view):
    v, page = view
    w = v.fields[("shift", "limit_left")]
    w.delete(0, "end")
    w.insert(0, "60")
    v._param_edited("shift", "limit_left")
    assert page.ctl.sent and page.ctl.sent[-1][0] == "params"
    values = page.ctl.sent[-1][2]
    assert values[pp.param_index("shift", "limit_left")] == 60 and len(values) == len(pp.PARAMS)


def test_bad_values_are_refused_without_a_write(view):
    v, page = view
    for text in ("abc", "70000"):
        w = v.fields[("select", "limit_right")]
        w.delete(0, "end")
        w.insert(0, text)
        v._param_edited("select", "limit_right")
    assert page.ctl.sent == []
    assert v.fields[("select", "limit_right")].get() == "50"        # restored


def test_jog_cylinders_and_pull_push_write_nothing(view):
    v, page = view
    v._not_driven("shift", "Jog forward", "x")
    v._cylinder("select", "Clamp Cylinder")
    assert page.ctl.sent == [] and len(page.messages) == 2


def test_lamp_sources():
    lamps = {"Servo": True, "Drive OK": False}
    assert lamp_state("lamp:Servo", lamps, None, "shift") is True
    assert lamp_state("not:Drive OK", lamps, None, "shift") is True          # ALARM lit
    assert lamp_state("not:Origin", lamps, None, "shift") is None
    assert lamp_state("st:limit_left", lamps, None, "shift") is None
