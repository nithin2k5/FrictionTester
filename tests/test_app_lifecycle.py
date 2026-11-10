"""App page switching: the Test Console's background work stops when it is left."""

import copy
import os
import sys
import threading
import time
import tkinter as tk

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _controllers():
    return [t for t in threading.enumerate()
            if getattr(getattr(t, "_target", None), "__qualname__", "") == "PcController._run" and t.is_alive()]


@pytest.fixture
def app(monkeypatch):
    from friction_tester.config import DEFAULT_SETTINGS
    from friction_tester.devices import Loadcell, NIDaq, XGBPlc
    from friction_tester.ui import app as app_mod
    from friction_tester.ui import test_console as tc
    monkeypatch.setattr(tc, "save_settings", lambda s: None)
    settings = copy.deepcopy(DEFAULT_SETTINGS)
    settings["test"]["integration"]["mode"] = "pc_interface"
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.withdraw()
    a = app_mod.App(root, settings, XGBPlc(), NIDaq(), [Loadcell(1), Loadcell(2)])
    yield a, root
    a._shutdown_page()
    root.destroy()


def _pump(root, n=10):
    for _ in range(n):
        root.update()
        time.sleep(0.02)


def test_leaving_the_test_console_stops_its_controller(app):
    a, root = app
    _pump(root)
    first = a.page.ctl
    assert first._thread.is_alive()
    a.load_page("help")
    _pump(root)
    assert not first._thread.is_alive()
    a.load_page("test_console")
    _pump(root)
    assert len(_controllers()) == 1


def test_leaving_during_a_test_asks_first(app, monkeypatch):
    from friction_tester.ui import app as app_mod
    a, root = app
    _pump(root)
    answers = []
    monkeypatch.setattr(app_mod.messagebox, "askyesno", lambda *x, **k: answers.pop(0))
    monkeypatch.setattr(a.page, "test_in_progress", lambda: "A test is running")
    ctl = a.page.ctl
    answers.append(False)                       # No: stay, the test keeps running
    a.load_page("help")
    _pump(root)
    assert a.current_page == "test_console" and ctl._thread.is_alive() and not answers
    answers.append(True)                        # Yes: leave, the controller stops
    a.load_page("help")
    _pump(root)
    assert a.current_page == "help" and not ctl._thread.is_alive()
