"""Simulator coverage for the shared PLC cycle integration.

Run:  python -m pytest tests -q
Everything here runs against plc.simulator with a fake clock - no PLC needed.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from friction_tester.plc import xgb_address as xa                      # noqa: E402
from friction_tester.plc.modbus_map import ModbusMapping, ModbusPlcBackend, MappingError  # noqa: E402
from friction_tester.plc.part_flow import SharedCycleFlow, panel_assignments  # noqa: E402
from friction_tester.plc.shared_cycle import (CycleTracker, SharedCycleMonitor, read_measurements,  # noqa: E402
                                              read_snapshot)
from friction_tester.plc.simulator import SharedCycleSimulator           # noqa: E402

CONFIRMED = {"at_cable_selection": "operator_fixture", "mt_one_cycle_tests_both": True,
             "channel_cable": {"1": "select", "2": "shift"}, "direction_role": {"forward": "pull", "return": "push"}}


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakeUI:
    def __init__(self):
        self.states, self.messages, self.shown, self.parts, self.cleared = [], [], [], [], []

    def set_state(self, s):
        self.states.append(s)

    def message(self, text, level):
        self.messages.append((level, text))

    def show_cycle(self, stage, assignments, ev):
        self.shown.append((stage, assignments, ev.data["verdict"]))

    def clear_panels(self, panels):
        self.cleared.append(panels)

    def part_done(self, record):
        self.parts.append(record)

    @property
    def last(self):
        return self.messages[-1][1] if self.messages else ""


class Rig:
    """Simulator + tracker + monitor + flow, driven synchronously by a fake clock."""

    def __init__(self, cfg=None, writes=True, **sim_kw):
        self.clock = Clock()
        self.sim = SharedCycleSimulator(clock=self.clock, speed=4.0, **sim_kw)
        self.tracker = CycleTracker(start_timeout=5.0, clock=self.clock)
        self.writes = writes
        self.monitor = SharedCycleMonitor(self.sim, self.tracker, can_write=lambda: self.writes, clock=self.clock)
        self.ui = FakeUI()
        self.flow = SharedCycleFlow(dict(CONFIRMED, **(cfg or {})), self.ui, self.monitor, lambda: self.writes)
        self.events = []
        self.poll()                      # first poll: baseline on connection

    def poll(self):
        _snap, evs = self.monitor.poll_once()
        for e in evs:
            self.events.append(e)
            self.flow.on_event(e)
            self.flow.on_global_event(e)
        return evs

    def run(self, seconds, dt=0.05):
        end = self.clock.t + seconds
        while self.clock.t < end:
            self.clock.t += dt
            self.poll()

    def until_state(self, state, limit=30):
        end = self.clock.t + limit
        while self.flow.state != state and self.clock.t < end:
            self.clock.t += 0.05
            self.poll()
        assert self.flow.state == state, (self.flow.state, self.ui.messages[-3:])

    def kinds(self):
        return [e.kind for e in self.events]


# ------------------------------------------------------------------ addresses / mapping
def test_xgb_address_parsing():
    assert xa.parse("M00029") == ("M", 2, 9)
    assert xa.parse("P0002A") == ("P", 2, 10)
    assert xa.parse("M0000D") == ("M", 0, 13)
    assert xa.parse("P00002") == ("P", 0, 2)
    assert xa.parse("D05010") == ("D", 5010, None)
    assert xa.parse("D00500.4") == ("D", 500, 4)
    assert xa.bit_number("M00029") == 41 and xa.bit_number("P00029") == 41   # different areas, same index
    assert xa.to_dint(0xFFF0, 0xFFFF) == -16 and xa.to_dint(452, 0) == 452
    assert xa.to_signed16(0xFFFE) == -2


def test_mapping_legacy_default_is_unverified_and_blocks_writes():
    m = ModbusMapping()
    assert not m.verified
    # the legacy assumption maps P00029 (idle output) and M00029 (start request) to the SAME coil
    assert m.locate("P00029") == m.locate("M00029") == ("coils", 41)
    assert m.locate("D05010") == ("holding_registers", 5010)

    class DummyPlc:
        is_open = True
        def write_coil_raw(self, *a): raise AssertionError("must not write")

    backend = ModbusPlcBackend(DummyPlc(), m, allow_writes=True)
    with pytest.raises(PermissionError):
        backend.write_bit("M00029", True)


def test_mapping_configured_areas():
    m = ModbusMapping({"verified": True, "areas": {"P": {"table": "discrete_inputs", "base": 0},
                                                   "M": {"table": "coils", "base": 8192},
                                                   "D": {"table": "holding_registers", "base": 0}}})
    assert m.locate("P0002A") == ("discrete_inputs", 42)
    assert m.locate("M00029") == ("coils", 8192 + 41)
    with pytest.raises(MappingError):
        ModbusMapping({"areas": {"D": {"table": "coils"}}}).locate("D00150")


# ------------------------------------------------------------------ AT / MT flows
def test_at_sequential_select_then_shift():
    r = Rig()
    assert r.flow.run("MODEL-AT", "AT")
    assert r.flow.state == "at_scan_select"
    r.flow.scan("shift", "WRONG-BOX")                 # wrong box refused
    assert r.flow.state == "at_scan_select" and r.sim.cycles_started == 0
    r.flow.scan("select", "LBL-SEL")
    r.until_state("at_scan_shift")
    assert r.sim.cycles_started == 1 and not r.ui.parts
    assert r.ui.shown[-1][1] == [(0, 1, True)]        # SELECT panel from channel 1 (configured)
    r.flow.scan("shift", "LBL-SHF")
    r.until_state("at_scan_select")                   # back to first scan = part complete
    assert r.sim.cycles_started == 2
    assert len(r.ui.parts) == 1                       # one part for two cable cycles
    part = r.ui.parts[0]
    assert part["verdict"] == "OK" and [s["stage"] for s in part["stages"]] == ["select", "shift"]
    assert part["label_select"] == "LBL-SEL" and part["label_shift"] == "LBL-SHF"
    assert r.ui.shown[-1][1] == [(1, 2, True)]


def test_mt_one_scan_one_start_request():
    r = Rig()
    r.flow.run("MODEL-MT", "MT")
    r.flow.scan("select", "LBL-MT")
    r.until_state("mt_scan", limit=40)
    on_writes = [w for w in r.sim.start_writes if w[1]]
    assert len(on_writes) == 1                        # exactly one M00029 pulse
    assert r.sim.start_writes[-1][1] is False         # released
    assert r.sim.cycles_started == 1
    assert len(r.ui.parts) == 1 and r.ui.parts[0]["verdict"] == "OK"
    assert len(r.ui.parts[0]["stages"]) == 1          # one shared result, not two cable verdicts


def test_fresh_ok_then_ng_and_two_identical_verdicts():
    r = Rig()
    r.sim.queue_verdicts("OK", "OK", "NG", "NG")
    r.flow.run("MODEL-MT", "MT")
    for label in ("A", "B", "C", "D"):
        r.flow.scan("select", label)
        r.until_state("mt_run", limit=2) if r.flow.state != "mt_run" else None
        r.until_state("mt_scan", limit=40)
    assert [p["verdict"] for p in r.ui.parts] == ["OK", "OK", "NG", "NG"]
    assert r.sim.cycles_started == 4
    done = [e for e in r.events if e.kind == "completed"]
    assert len(done) == 4 and all(e.data["evidence"].startswith("counter") for e in done)


def test_stale_latched_ok_at_startup_does_not_complete():
    r = Rig(stale_verdict="OK")
    assert r.events[0].kind == "connected" and r.events[0].data["stale_verdict"] == "OK"
    r.flow.run("MODEL-MT", "MT")
    r.writes = False                                  # start cannot be sent ...
    r.flow.writes_available = lambda: True            # ... although the flow thinks it may
    r.flow.scan("select", "LBL")
    r.run(1.0)
    assert not r.ui.parts                             # the latched OK bit completed nothing
    assert r.flow.state == "idle" and "disabled" in r.ui.last


def test_stale_ok_with_no_cycle_times_out():
    r = Rig(stale_verdict="OK")
    r.flow.run("MODEL-MT", "MT")
    r.sim.abort_at(10)                                # PLC never leaves idle for real
    r.flow.scan("select", "LBL")
    r.run(8.0)
    assert not r.ui.parts
    assert r.flow.state == "idle"


def test_stop_then_late_completion_is_ignored():
    r = Rig()
    r.flow.run("MODEL-MT", "MT")
    r.flow.scan("select", "LBL")
    r.run(0.6)
    assert r.sim.step != 0                            # cycle running
    r.flow.stop()
    assert "not stopped" in r.ui.last                 # says STOP is app-only
    r.run(15)                                         # PLC finishes the cycle anyway
    assert r.sim.step == 0 and not r.ui.parts
    assert not [e for e in r.events if e.kind == "completed"]


def test_disconnect_after_start_then_reconnect_is_ambiguous():
    r = Rig()
    r.flow.run("MODEL-MT", "MT")
    r.flow.scan("select", "LBL")
    r.run(0.6)
    r.sim.set_connected(False)
    r.run(10)                                         # cycle finishes while we can't see it
    r.sim.set_connected(True)
    r.run(0.5)
    assert "ambiguous" in r.kinds()
    assert not r.ui.parts and r.flow.state == "idle"
    assert r.sim.cycles_started == 1                  # never retried
    assert len([w for w in r.sim.start_writes if w[1]]) == 1


def test_early_return_to_idle_reports_no_result():
    r = Rig()
    r.flow.run("MODEL-MT", "MT")
    r.sim.abort_at(50)                                # stop input during the return stroke
    r.flow.scan("select", "LBL")
    r.run(10)
    assert not r.ui.parts and r.flow.state == "idle"
    assert "Cycle ended without confirmed result" in r.ui.last


def test_counter_reset_during_cycle_still_confirms_once():
    r = Rig()
    r.flow.run("MODEL-MT", "MT")
    r.flow.scan("select", "LBL")
    r.run(0.6)
    r.sim.reset_counters()                            # M01122 pressed mid-cycle
    r.until_state("mt_scan", limit=40)
    assert "counter_reset" in r.kinds()
    assert len(r.ui.parts) == 1 and r.ui.parts[0]["verdict"] == "OK"


def test_counter_reset_while_idle_creates_no_test():
    r = Rig()
    r.sim.reset_counters()
    r.run(1.0)
    assert "counter_reset" in r.kinds() and not r.ui.parts
    assert not [e for e in r.events if e.kind == "completed"]


def test_physical_start_without_armed_test_is_not_counted():
    r = Rig()
    r.sim.press_start()
    r.run(15)
    assert r.sim.cycles_started == 1 and not r.ui.parts


def test_missing_mappings_block_live_start_with_specific_messages():
    r = Rig(cfg={"at_cable_selection": ""})
    assert not r.flow.run("MODEL-AT", "AT")
    assert r.ui.last == "AT cable selection is not configured."
    r = Rig(cfg={"mt_one_cycle_tests_both": False})
    assert not r.flow.run("MODEL-MT", "MT")
    assert "MT shared-cycle operation is not confirmed" in r.ui.last
    r = Rig(writes=False)
    assert not r.flow.run("MODEL-MT", "MT")
    assert "mapping is not verified" in r.ui.last
    assert r.sim.cycles_started == 0


def test_unmapped_channels_are_labelled_not_assumed():
    cfg = dict(CONFIRMED, channel_cable={"1": "", "2": ""})
    assert panel_assignments(cfg, "MT", "both") == [(0, 1, False), (1, 2, False)]
    assert panel_assignments(cfg, "AT", "select") == [(0, 1, False), (1, 2, False)]
    assert panel_assignments(CONFIRMED, "AT", "shift") == [(1, 2, True)]


def test_one_production_increment_per_part_and_start_failure_not_retried():
    r = Rig()
    r.flow.run("MODEL-AT", "AT")
    r.sim.fail_next_write()
    r.flow.scan("select", "LBL")
    r.run(1.0)
    assert "start_failed" in r.kinds() and r.flow.state == "idle" and r.sim.cycles_started == 0
    r.flow.run("MODEL-AT", "AT")
    r.flow.scan("select", "S1")
    r.until_state("at_scan_shift", limit=40)
    r.flow.scan("shift", "H1")
    r.until_state("at_scan_select", limit=40)
    assert len(r.ui.parts) == 1


def test_measurements_come_from_stored_samples():
    r = Rig()
    r.flow.run("MODEL-MT", "MT")
    r.flow.scan("select", "LBL")
    r.until_state("mt_scan", limit=40)
    ev = [e for e in r.events if e.kind == "completed"][-1]
    m = ev.data["measurements"]
    assert set(m["samples"]) == {1, 2}
    for ch in (1, 2):
        assert set(m["samples"][ch]) == {"forward", "return"}
        assert set(m["samples"][ch]["forward"]) == {"P1", "P2", "P3", "X1", "X2"}
        assert all(v > 0 for v in m["samples"][ch]["forward"].values())
        assert all(v < 0 for v in m["samples"][ch]["return"].values())
    assert m["finals"][1] == r.sim._rw(1250) and m["limits"][1] == (300, 600)


def test_snapshot_reader_uses_central_map():
    clock = Clock()
    sim = SharedCycleSimulator(clock=clock)
    s = read_snapshot(sim, clock())
    assert s.step == 0 and s.idle and not s.running
    assert (s.ok_count, s.ng_count, s.total_count) == (10, 2, 12)
    m = read_measurements(sim)
    assert m["finals"] == {1: 0, 2: 0}


# ------------------------------------------------------------------ TBL_TEST_RESULTS rows
def test_result_rows_at_two_stages_share_lot():
    from friction_tester import db
    r = Rig()
    r.sim.queue_verdicts("OK", "NG")
    r.flow.run("MODEL-AT", "AT", "43794-B4000")
    r.flow.scan("select", "43794-B4000 L1")
    r.until_state("at_scan_shift", limit=40)
    r.flow.scan("shift", "43794-B4000 L2")
    r.until_state("at_scan_select", limit=40)
    rows = db.result_rows(r.ui.parts[0], "uid-1", "simulation")
    assert [x["TR_STAGE"] for x in rows] == ["SELECT", "SHIFT"]
    assert [x["TR_STAGE_RESULT"] for x in rows] == ["OK", "NG"]
    assert {x["TR_LOT_RESULT"] for x in rows} == {"NG"}            # part NG because SHIFT stage NG
    assert {x["TR_LOT_VALUE"] for x in rows} == {"43794-B4000 L1"}
    assert [x["TR_LABEL"] for x in rows] == ["43794-B4000 L1", "43794-B4000 L2"]
    assert rows[0]["TR_PART_NUMBER"] == "43794-B4000"
    assert rows[1]["TR_CH1_FINAL"] == 720 and rows[1]["TR_CH1_LIMIT_NG"] == 1
    assert rows[0]["TR_CH1_FWD_1"] > 0 and rows[0]["TR_CH1_RET_1"] < 0
    assert set(db.RESULT_COLUMNS) <= set(rows[0]) and len(db.RESULT_COLUMNS) == 28


def test_result_rows_mt_one_row():
    from friction_tester import db
    r = Rig()
    r.flow.run("MODEL-MT", "MT", "43794-B4100")
    r.flow.scan("select", "43794-B4100 L9")
    r.until_state("mt_scan", limit=40)
    rows = db.result_rows(r.ui.parts[0], "uid-2", "simulation")
    assert len(rows) == 1 and rows[0]["TR_STAGE"] == "BOTH" and rows[0]["TR_LOT_RESULT"] == "OK"
    assert rows[0]["TR_CH2_RET_3"] is not None


def test_results_schema_matches_insert_columns():
    from friction_tester import db
    for c in db.RESULT_COLUMNS:
        assert c in db.RESULTS_SCHEMA
