"""COM Port page: leaving with unsaved edits asks first."""

import copy
import os
import sys
import tkinter as tk

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def page(monkeypatch):
    from friction_tester.config import DEFAULT_SETTINGS
    from friction_tester.devices import Loadcell, NIDaq, XGBPlc
    from friction_tester.ui import comport_page
    saved = []
    monkeypatch.setattr(comport_page, "save_settings", lambda s: saved.append(copy.deepcopy(s)))
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.withdraw()
    p = comport_page.ComPortPage(root, copy.deepcopy(DEFAULT_SETTINGS), XGBPlc(), NIDaq(),
                                 [Loadcell(1), Loadcell(2)])
    p.saved = saved
    yield p
    root.destroy()


def test_edit_marks_unsaved_and_save_clears_it(page):
    assert not page.unsaved_changes()
    page.lc_rows[1]["id"].insert(0, "01")
    assert page.unsaved_changes()
    assert page.save() and page.saved and page.saved[-1]["loadcells"][1]["id"] == 1
    assert not page.unsaved_changes()
    page.ent_daq_rate.delete(0, "end")
    page.ent_daq_rate.insert(0, "2000")
    assert page.unsaved_changes()
    page.ent_daq_rate.delete(0, "end")
    page.ent_daq_rate.insert(0, "1000")                     # back to the saved value
    assert not page.unsaved_changes()


def test_leaving_asks_and_no_keeps_the_page(page, monkeypatch):
    from friction_tester.ui import app as app_mod
    asked = []
    monkeypatch.setattr(app_mod.messagebox, "askyesno", lambda *a, **k: asked.append(a) or False)
    fake = type("A", (), {"page": page, "root": page.master})()
    page.lc_rows[0]["id"].insert(0, "05")
    assert app_mod.App.confirm_leave(fake) is False and "not saved" in asked[0][1]
    page.save()
    assert app_mod.App.confirm_leave(fake) is True and len(asked) == 1
