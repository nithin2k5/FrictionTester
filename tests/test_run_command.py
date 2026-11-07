"""RUN through test.commands.test_run in "independent" mode (set explicitly to
M00029 here - that address belongs to the other ladder, not this machine's
plc.pdf profile), against the shared-cycle simulator: one pulse per machine
cycle, gated by the verified mapping."""

import os
import sys
import queue
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from friction_tester.config import load_settings, migrate  # noqa: E402
from plc_simulator import SharedCycleSimulator  # noqa: E402
from friction_tester.ui import test_console  # noqa: E402


class Console:
    """Just enough of TestConsolePage for _start_cable_tests / _run_machine_cycle."""

    SIDES = test_console.TestConsolePage.SIDES

    def __init__(self, settings, plc):
        self.calls, self.settings, self.plc = queue.Queue(), settings, plc
        self._stop_event = test_console.threading.Event()
        self._flow, self._pending, self.messages, self.recorded = "mt_run", set(), [], []
        self.panels = [Panel(), Panel()]

    def after(self, _ms, fn, *a):               # the app's Tk after(); here a thread-safe queue
        self.calls.put((fn, a))

    def _set_status(self, text, *_a, **_k):
        self.messages.append(text)

    def _abort_flow(self, message):
        self._flow = "idle"
        self.messages.append(message)

    def _cable_test_finished(self, n):
        self._pending.discard(n)
        self.recorded.append((n, self.panels[n].last_ok))

    for name in ("_start_cable_tests", "_uses_test_run", "_run_machine_cycle", "_machine_cycle_finished"):
        locals()[name] = getattr(test_console.TestConsolePage, name)


class Panel:
    def __init__(self):
        self.last_ok = None
        self.judge = self

    def reset_results(self):
        self.last_ok = None

    def config(self, **_k):
        pass


class Plc:
    is_open = True


def run(settings, sim, cables, gated_open=True):
    c = Console(settings, Plc())
    test_console.ModbusPlcBackend = lambda plc, mapping, allow_writes: (
        sim if gated_open and mapping.verified and allow_writes else Blocked(mapping, allow_writes))
    c._start_cable_tests(cables)
    end = time.monotonic() + 20
    while time.monotonic() < end and c._flow != "idle" and len(c.recorded) < len(cables):
        try:
            fn, a = c.calls.get(timeout=0.05)
            fn(*a)
        except queue.Empty:
            pass
    return c


class Blocked:
    def __init__(self, mapping, allow):
        self.can_write = False


def settings(verified=True, live=True):
    s = load_settings(path="does-not-exist.json")
    s["test"]["commands"]["test_run"] = "M00029"      # set explicitly (independent mode, other ladder)
    sh = s["test"]["integration"]["shared"]
    sh["mapping"]["verified"], sh["live_start_enabled"] = verified, live
    return s


def test_no_m00029_default_and_old_settings_migrated():
    s = load_settings(path="does-not-exist.json")
    assert s["test"]["commands"]["test_run"] == "" and s["test"]["integration"]["mode"] == "pc_interface"
    old = migrate({"test": {"integration": {"mode": "independent"}, "commands": {"test_run": "M00029"}}})
    assert old["test"]["commands"]["test_run"] == "" and old["test"]["integration"]["mode"] == "pc_interface"
    assert old["settings_version"] == 3 and len(old["migrated"]) == 2


def test_unverified_mapping_writes_nothing():
    sim = SharedCycleSimulator(speed=50)
    c = run(settings(verified=False), sim, [0, 1])
    assert c._flow == "idle" and not c.recorded
    assert "was not written" in c.messages[-1] and "mapping is verified" in c.messages[-1]
    assert sim.step == 0


def test_mt_one_pulse_ok_for_both_cables():
    sim = SharedCycleSimulator(speed=50)
    sim.queue_verdicts("OK")
    c = run(settings(), sim, [0, 1])
    assert sorted(c.recorded) == [(0, True), (1, True)]
    assert (sim._rw(150), sim._rw(152)) == (11, 2)  # exactly one OK cycle (simulator starts at 10 OK, 2 NG)


def test_at_stage_ng():
    sim = SharedCycleSimulator(speed=50)
    sim.queue_verdicts("NG")
    c = run(settings(), sim, [0])
    assert c.recorded == [(0, False)]


def test_aborted_cycle_is_not_recorded():
    sim = SharedCycleSimulator(speed=50)
    sim.abort_at(50)
    c = run(settings(), sim, [0, 1])
    assert c._flow == "idle" and not c.recorded
    assert "without confirmed result" in c.messages[-1]
