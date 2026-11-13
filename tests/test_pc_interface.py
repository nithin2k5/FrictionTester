"""PRINTING PROGRAM OLD profile (plc.pdf) against the ladder simulator.

Simulation only: passing here does not establish wiring, Modbus mapping,
simultaneous machine operation or commissioning.
"""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from friction_tester.plc import pc_profile as pp  # noqa: E402
from friction_tester.plc.axis_ops import HomeOp, OpBlocked, StrokeOp  # noqa: E402
from friction_tester.plc.event_log import EventLog  # noqa: E402
from friction_tester.plc.pc_flow import (PcController, derived_targets, evaluate_axis,  # noqa: E402
                                         target_problems)
from friction_tester.plc.pc_link import PcLink, read_snapshot, validate_params  # noqa: E402
from friction_tester.result_store import ResultStore  # noqa: E402
from pc_ladder_sim import PcLadderSim  # noqa: E402

PARAMS = [10, 50, 100, 20, 500, 500,      # SHIFT jog low/high, speed, PULL travel, left/right limit
          10, 50, 100, 20, 500, 500,      # SELECT
          100, 100]                       # measurement start SHIFT, SELECT
COND = {"load_min": 1.0, "load_max": 10.0, "positions": [None, None, None]}
RULES = {"judge": "peak", "force_sign": "absolute", "limit_bounds": "inclusive", "min_samples": 3,
         "strokes_per_test": 1}
INFO = {"model": "M1", "part_number": "PN1", "conditions": {"select": dict(COND), "shift": dict(COND)}}


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class FakeSampler:
    """Deterministic force data per (axis, direction); records captures."""

    def __init__(self):
        self.data = {}
        self.captures = []
        self.open = set()

    def describe(self, axis):
        return {"force": {"source": "simulated", "channel": axis}, "stroke": None}

    def start(self, axis, kind, test_id, stroke_no):
        cap = (axis, kind, test_id, stroke_no)
        self.captures.append(cap)
        self.open.add(cap)
        return cap

    def stop(self, cap):
        self.open.discard(cap)
        return list(self.data.get((cap[0], cap[1]), [(float(i), 5.0, None) for i in range(5)]))


class Rig:
    def __init__(self, internal=True, skip_scan=False, timeouts=None, owner=True, verified=True,
                 completion="internal_flags", cfg=None):
        self.clock = Clock()
        self.sim = PcLadderSim(self.clock)
        self.sim.mapping.verified = verified
        self.log = EventLog(folder=None, clock=lambda: __import__("datetime").datetime(2026, 11, 7))
        self.link = PcLink(self.sim, self.log, owner_confirmed=owner, internal_enabled=internal)
        self.sampler = FakeSampler()
        conf = {"timeouts": dict({"stroke": 5.0, "home": 10.0, "alarm_reset_pulse": 0.2}, **(timeouts or {})),
                "measurement": dict(RULES), "mt_parallel_verified": True, "stroke_completion": completion,
                "home_before_test": "if_not_referenced"}
        conf.update(cfg or {})
        self.ctl = PcController(self.link, conf, self.log, self.sampler, clock=self.clock, skip_scan=skip_scan)
        self.events = []
        self.trace = []
        self.step()

    def step(self, dt=0.05):
        self.clock.t += dt
        self.sim.advance(dt)
        self.ctl.poll_once()
        while not self.ctl.events.empty():
            ev = self.ctl.events.get()
            if ev[0] != "snapshot":
                self.events.append(ev)
        self.trace.append((self.sim.D[4101], self.sim.axes["shift"].seq["pull"].step,
                           self.sim.axes["select"].seq["pull"].step))

    def run(self, seconds):
        for _ in range(int(seconds / 0.05)):
            self.step()

    def until(self, cond, limit=30.0):
        end = self.clock.t + limit
        while not cond() and self.clock.t < end:
            self.step()
        assert cond(), (self.ctl.flow, self.messages()[-4:])

    def confirm_params(self, values=PARAMS, model="M1"):
        self.ctl.submit("params", model, list(values))
        self.step()
        self.until(lambda: self.ctl.params["state"] in ("confirmed", "not_confirmed"))

    def parts(self):
        return [e[1] for e in self.events if e[0] == "part_done"]

    def messages(self):
        return [e[1] for e in self.events if e[0] == "message"]

    def run_part(self, mtype, labels=("LBL-1", "LBL-2")):
        self.ctl.submit("run", dict(INFO, type=mtype))
        self.step()
        self.ctl.submit("scan", "select", labels[0])
        if mtype == "MT":
            self.until(lambda: self.ctl.flow == "idle")
            return
        self.until(lambda: self.ctl.flow in ("at_scan_shift", "idle"))
        if self.ctl.flow == "at_scan_shift":
            self.ctl.submit("scan", "shift", labels[1])
            self.until(lambda: self.ctl.flow == "idle")

    def requests_clear(self):
        return all(self.sim.D[4100 + i] == 0 for i in range(3))


# ------------------------------------------------------------------ map / masks
def test_alarm_reset_masks_and_coupled_bits():
    # the drives are exchanged (pp.DRIVE_OF): the SHIFT cable is moved by the ladder's SELECT drive
    assert pp.DRIVE_OF == {"select": "shift", "shift": "select"}
    assert pp.COMMANDS["shift"]["alarm_reset"] == (0, 14) and pp.m_address(700, 0, 14) == "M0700E"
    assert pp.COMMANDS["select"]["alarm_reset"] == (0, 10) and pp.m_address(700, 0, 10) == "M0700A"
    assert pp.SELECTOR == {"shift": "M00205", "select": "M00105"}
    assert pp.STATUS["shift"]["servo_on"][2] == "P00014" and pp.STATUS["select"]["servo_on"][2] == "P00010"
    assert pp.STATUS["shift"]["clamp_on"][2] == "P00003"            # fixtures stay with their cable
    assert pp.param_index("select", "speed") == 2 and pp.param_index("shift", "speed") == 8
    assert "torque-limit P00031" in pp.COUPLED_BITS["M07008"] and "lamp P0002D" in pp.COUPLED_BITS["M07008"]
    assert "not alarm reset" in pp.COUPLED_BITS["M07008"]
    used = {(i, n) for ops in pp.COMMANDS.values() for i, n in ops.values()}
    assert (0, 8) not in used and (0, 15) not in used and (0, 12) not in used   # M07008, M0700F, M0700C


def test_alarm_reset_selector_pulse_and_no_retry():
    r = Rig()
    a = r.sim.axes["shift"]
    a.alarm_normal = False
    r.sim.local["shift"] = True                     # local selected: PC branch inactive
    r.step()
    r.ctl.submit("alarm_reset", "shift")
    r.step()
    assert "local/touch operation selected (M00205 ON)" in r.messages()[-1]
    assert r.sim.requests_seen["shift"]["alarm_reset"] == 0
    r.sim.local["shift"] = False
    r.step()                                         # commands use the latest poll's snapshot
    r.ctl.submit("alarm_reset", "shift")
    r.step()
    r.until(lambda: not r.ctl.manual)
    assert "alarm-normal input restored" in r.messages()[-1] and r.requests_clear()
    a.alarm_normal, r.sim.alarm_clearable["shift"] = False, False   # an alarm that does not clear
    r.step()
    r.ctl.submit("alarm_reset", "shift")
    r.step()
    r.until(lambda: not r.ctl.manual)
    r.run(3.0)
    assert "not retried" in " ".join(r.messages()[-3:])
    assert r.sim.requests_seen["shift"]["alarm_reset"] == 2      # one pulse per request, no repeats
    r2 = Rig(timeouts={"alarm_reset_pulse": None})
    r2.sim.axes["select"].alarm_normal = False
    r2.step()
    r2.ctl.submit("alarm_reset", "select")
    r2.step()
    assert "pulse length not configured" in r2.messages()[-1]


# ------------------------------------------------------------------ parameters
def test_parameters_confirmed_after_echo_and_settle():
    r = Rig()
    r.ctl.submit("params", "M1", list(PARAMS))
    r.until(lambda: r.ctl.params["state"] == "settling")
    r.until(lambda: r.ctl.params["state"] == "confirmed")
    a = r.sim.axes["shift"]
    assert a.meas_target == 1_000_000 - 100 * 10 and a.pull_target == a.meas_target + 20 * 100 + 100


def test_parameter_validation_and_derived_targets():
    bad = list(PARAMS)
    bad[12], bad[2] = 40000, None
    problems = validate_params(bad)
    assert any("Operating speed is empty" in p for p in problems) and any("0..32767" in p for p in problems)
    over = list(PARAMS)
    over[3] = 600                                     # PULL target beyond the left limit
    assert target_problems(derived_targets(over, m0700f_on=False))
    assert not target_problems(derived_targets(PARAMS, m0700f_on=False))
    r = Rig()
    r.confirm_params(values=over)
    assert r.ctl.params["state"] == "not_confirmed" and "outside the limits" in r.ctl.params["message"]


def test_disconnect_or_failed_write_during_parameter_transfer():
    r = Rig()
    r.sim.fail_next_write = True
    r.confirm_params()
    assert r.ctl.params["state"] == "not_confirmed"
    r.ctl.submit("params", "M1", list(PARAMS))
    r.step()
    r.sim.connected = False
    r.run(1.0)
    r.sim.connected = True
    r.run(1.0)
    assert r.ctl.params["state"] == "not_confirmed"
    r.ctl.submit("run", dict(INFO, type="AT"))
    r.step()
    assert "Parameters not confirmed" in r.messages()[-1]


def test_writes_blocked_until_mapping_verified_and_owner_confirmed():
    for kw, text in (({"verified": False}, "not verified"), ({"owner": False}, "ownership")):
        r = Rig(**kw)
        r.confirm_params()
        assert r.ctl.params["state"] == "not_confirmed" and text in r.ctl.params["message"]
        assert not r.sim.writes


def test_parameters_frozen_during_an_active_part():
    r = Rig()
    r.confirm_params()
    r.ctl.submit("run", dict(INFO, type="AT"))
    r.step()
    new = list(PARAMS)
    new[2] = 120
    r.ctl.submit("params", "M1", new)
    r.step()
    assert "frozen" in r.messages()[-1] and r.sim.D[4202] == 100
    r.ctl.submit("scan", "select", "S")
    r.until(lambda: r.ctl.flow == "at_scan_shift")
    r.ctl.submit("scan", "shift", "H")
    r.until(lambda: r.ctl.flow == "idle")
    r.until(lambda: r.sim.D[4202] == 120)            # applied after the part


# ------------------------------------------------------------------ AT / MT
def test_at_part_order_and_one_result():
    r = Rig()
    r.confirm_params()
    r.ctl.submit("run", dict(INFO, type="AT"))
    r.step()
    r.ctl.submit("scan", "shift", "WRONG")
    r.step()
    assert "wrong field" in r.messages()[-1]
    r.ctl.submit("scan", "select", "SEL-1")
    r.until(lambda: r.sim.axes["select"].seq["pull"].word != 0)
    r.ctl.submit("scan", "select", "AGAIN")
    r.step()
    assert "test is running" in r.messages()[-1]
    r.until(lambda: r.ctl.flow == "at_scan_shift")
    assert r.sim.starts["shift"]["pull"] == 0
    r.ctl.submit("scan", "shift", "SHF-1")
    r.until(lambda: r.ctl.flow == "idle")
    parts = r.parts()
    assert len(parts) == 1 and parts[0]["verdict"] == "OK" and parts[0]["counted"]
    assert [s["stage"] for s in parts[0]["stages"]] == ["select", "shift"]
    for axis in pp.AXES:
        assert r.sim.starts[axis] == {"home": 1, "pull": 1, "push": 1, "meas": 1}   # ends back at home
    assert r.requests_clear()


def test_part_is_logged_two_seconds_after_shift():
    r = Rig()
    r.confirm_params()
    r.ctl.submit("run", dict(INFO, type="AT"))
    r.step()
    r.ctl.submit("scan", "select", "SEL-1")
    r.until(lambda: r.ctl.flow == "at_scan_shift")
    r.ctl.submit("scan", "shift", "SHF-1")
    r.until(lambda: r.ctl.part is not None and "pending" in r.ctl.part)
    held = r.clock.t
    assert not r.parts() and "in 2 s" in r.messages()[-1]          # SHIFT result shown, not logged yet
    r.ctl.submit("run", dict(INFO, type="AT"))
    r.step()
    assert "being logged" in r.messages()[-1] and not r.parts()
    r.until(lambda: r.parts())
    assert 2.0 <= r.clock.t - held < 2.2 and r.ctl.flow == "idle"
    assert r.parts()[0]["verdict"] == "OK" and r.parts()[0]["counted"]


def test_waiting_before_shift_does_not_count_against_the_part_timeout():
    """Field log: SELECT OK, the operator came back ~2 h later and pressed RUN - SHIFT was
    failed with 'part timeout (600 s)' as soon as it started."""
    r = Rig(timeouts={"part": 600.0})
    r.confirm_params()
    r.ctl.submit("run", dict(INFO, type="AT"))
    r.step()
    r.ctl.submit("scan", "select", "SEL-1")
    r.until(lambda: r.ctl.flow == "at_scan_shift")
    r.clock.t += 700.0                                              # the part waits for the SHIFT scan
    r.step()
    r.ctl.submit("scan", "shift", "SHF-1")
    r.step()
    r.ctl.submit("run", dict(INFO, type="AT"))                     # RUN while SHIFT runs: wait, not STOP
    r.step()
    assert "still running, wait" in r.messages()[-1]
    r.until(lambda: r.ctl.flow == "idle")
    assert r.parts()[0]["verdict"] == "OK" and not r.ctl.fault


def test_stop_during_the_hold_logs_the_part_not_aborted():
    r = Rig()
    r.confirm_params()
    r.run_part("MT", ("MT-1",))                                      # waits for idle: logged after the hold
    assert r.parts()[0]["verdict"] == "OK"
    r.ctl.submit("run", dict(INFO, type="MT"))
    r.step()
    r.ctl.submit("scan", "select", "MT-2")
    r.until(lambda: r.ctl.part is not None and "pending" in r.ctl.part)
    r.ctl.submit("stop")
    r.step()
    assert len(r.parts()) == 2 and r.parts()[1]["verdict"] == "OK" and r.parts()[1]["counted"]
    nohold = Rig(cfg={"result_hold_s": 0})
    nohold.confirm_params()
    nohold.run_part("MT", ("MT-3",))
    assert nohold.parts()[0]["verdict"] == "OK"


def test_mt_shared_words_without_lost_bits_and_mt_gate():
    r = Rig()
    r.confirm_params()
    r.run_part("MT", ("MT-1",))
    assert len(r.parts()) == 1 and r.parts()[0]["verdict"] == "OK"
    both = [w for _t, addr, vals in r.sim.writes if addr == "D04101" for w in vals if w & 0b1010 == 0b1010]
    assert both, "SHIFT and SELECT PULL requests were never ON together in D04101"
    for axis in pp.AXES:
        assert r.sim.requests_seen[axis]["pull"] == 1 and r.sim.requests_seen[axis]["push"] == 1
    assert r.requests_clear()
    gated = Rig(cfg={"mt_parallel_verified": False})
    gated.confirm_params()
    gated.ctl.submit("run", dict(INFO, type="MT"))
    gated.step()
    assert "not verified" in gated.messages()[-1] and gated.ctl.flow == "idle"


def test_invalid_type_and_duplicate_run_refused():
    r = Rig()
    r.confirm_params()
    r.ctl.submit("run", dict(INFO, type="XX"))
    r.step()
    assert "invalid Type" in r.messages()[-1]
    r.ctl.submit("run", dict(INFO, type="AT"))
    r.step()
    r.ctl.submit("run", dict(INFO, type="AT"))
    r.step()
    assert "already active" in r.messages()[-1]


def test_request_held_through_completion_then_released():
    r = Rig()
    r.confirm_params()
    r.run_part("AT")
    pull_bit = 1 << pp.COMMANDS["shift"]["pull"][1]     # the SHIFT cable's PULL request in D04101
    seen = [(word & pull_bit != 0, step) for word, step, _sl in r.trace if step is not None]
    for held, step in seen:
        if step <= 5:                                   # the completion flag is set at step 6
            assert held, f"PULL request released at step {step}, before the completion flag"
    # released while the sequence (and its exported "active" bit) was still in step 6/7
    assert any(not held and step in (6, 7) for held, step in seen)


def test_measurement_window_closes_at_completion_not_release():
    r = Rig()
    r.confirm_params()
    r.ctl.submit("run", dict(INFO, type="AT"))
    r.step()
    r.ctl.submit("scan", "select", "S")
    r.until(lambda: r.sim.axes["select"].complete["pull"])
    r.step()
    assert not r.sampler.open
    assert r.sim.axes["select"].seq["pull"].word != 0


def test_position_fallback_only_when_selected():
    r = Rig(internal=False)
    r.confirm_params()
    r.ctl.submit("run", dict(INFO, type="AT"))
    r.step()
    r.ctl.submit("scan", "select", "S")
    r.until(lambda: r.ctl.flow == "idle")
    assert r.parts()[0]["verdict"] == "INCOMPLETE"
    assert any("not readable" in x for x in r.parts()[0]["reasons"])
    f = Rig(internal=False, completion="position_fallback")
    f.confirm_params()
    f.run_part("AT")
    assert f.parts()[-1]["verdict"] == "OK"
    assert any("fresh target compare OFF->ON" in e for e in f.parts()[-1]["evidence"]["select"])
    f.run_part("AT")                                    # the routine ended at home: P1 is positioned again
    assert f.parts()[-1]["verdict"] == "OK"
    assert any("fresh target compare OFF->ON" in e for e in f.parts()[-1]["evidence"]["select"])


# ------------------------------------------------------------------ handshake failures
def test_echo_without_acceptance_fails():
    r = Rig()
    r.confirm_params()
    r.sim.ignore["select"] = True
    r.ctl.submit("run", dict(INFO, type="AT"))
    r.step()
    r.ctl.submit("scan", "select", "S")
    r.until(lambda: r.ctl.flow == "idle")
    assert any("no fresh sequence" in x for x in r.parts()[0]["reasons"])
    assert r.ctl.fault
    r.run(0.5)
    assert r.requests_clear()


def test_stale_completion_and_position_bits_refused():
    r = Rig()
    r.confirm_params()
    a = r.sim.axes["shift"]
    a.pos = a.pull_target
    r.step()
    snap = read_snapshot(r.sim, internal_enabled=True, now=r.clock())
    with pytest.raises(OpBlocked, match="already at the PULL target"):
        StrokeOp("shift", "pull").start(snap, r.clock())
    a.pos = a.meas_target
    a.complete["pull"] = True
    r.step()
    snap = read_snapshot(r.sim, internal_enabled=True, now=r.clock())
    with pytest.raises(OpBlocked, match="stale completion flag M00061"):
        StrokeOp("shift", "pull").start(snap, r.clock())


def test_home_feedback_before_sequence_end_is_not_completion():
    r = Rig()
    r.confirm_params()
    r.ctl.submit("home", "shift")
    r.step()
    seq = r.sim.axes["shift"].seq["home"]
    early = False
    while r.ctl.manual and r.clock.t < 400:
        r.step()
        if r.sim.axes["shift"].origin_complete and seq.word != 0:
            early = True
        if not r.ctl.manual:
            assert seq.word == 0, "home reported done before M0200 returned to idle"
    assert early, "origin feedback never preceded the end of the sequence"
    r.run(3.0)
    assert r.sim.starts["shift"]["home"] == 1 and "HOME complete" in r.messages()[-1]


def test_home_fails_at_once_when_servo_drops_mid_home():
    r = Rig()
    r.confirm_params()
    r.ctl.submit("home", "shift")
    r.until(lambda: r.ctl.manual.get("shift") is not None and r.ctl.manual["shift"].state == "running")
    t0 = r.clock.t
    r.sim.axes["shift"].servo = False
    r.until(lambda: not r.ctl.manual)
    assert r.clock.t - t0 < 2.0, "a servo drop must not wait for the 60 s home timeout"
    assert r.ctl.fault and "drive/axis fault during homing" in r.ctl.fault
    r.run(0.5)
    assert r.requests_clear()


def test_home_from_unhomed_power_up():
    """Power-up: origin feedback OFF -> M00122 OFF -> permission OFF. Homing must still start."""
    r = Rig()
    r.confirm_params()
    a = r.sim.axes["shift"]
    a.origin_complete = False
    r.step()
    snap = read_snapshot(r.sim, now=r.clock())
    assert snap.st("shift", "permitted") is False
    r.ctl.submit("home", "shift")
    r.until(lambda: not r.ctl.manual)
    r.run(3.0)
    assert r.sim.starts["shift"]["home"] == 1, "home must run exactly once (released after acceptance)"
    assert "HOME complete" in r.messages()[-1]
    assert a.origin_complete and a.seq["home"].word == 0
    snap = read_snapshot(r.sim, now=r.clock())
    assert snap.st("shift", "permitted") is True


def test_auto_m_reads_verified_use_completion_flags():
    r = Rig(internal="auto", completion="auto")
    assert r.link.internal_enabled and r.ctl._completion() == "internal_flags"
    assert any("M-area reads verified" in m for m in r.messages())
    r.confirm_params()
    r.run_part("AT")
    assert r.parts()[0]["verdict"] == "OK"


def test_auto_m_reads_mismatch_falls_back_to_position():
    clock = Clock()
    sim = PcLadderSim(clock)
    sim.m_area_wrong = True
    link = PcLink(sim, EventLog(folder=None), owner_confirmed=True, internal_enabled="auto")
    ok, detail = link.verify_internal()
    assert not ok and "do not reach the M area" in detail and not link.internal_enabled


def test_home_on_open_homes_both_axes_once():
    """home_on_open: both axes home on the first good status read, never again after that."""
    r = Rig(cfg={"home_on_open": True})
    assert set(r.ctl.manual) == {"shift", "select"}
    r.until(lambda: not r.ctl.manual)
    r.run(3.0)
    assert r.sim.starts["shift"]["home"] == 1 and r.sim.starts["select"]["home"] == 1
    assert all(a.origin_complete and a.seq["home"].word == 0 for a in r.sim.axes.values())
    assert r.ctl.fault is None and r.requests_clear()
    homing = [e for e in r.events if e[0] == "open_home"]   # the UI locks the console in between
    assert homing[0][:2] == ("open_home", "start") and sorted(homing[0][2]) == ["SELECT", "SHIFT"]
    assert len(homing) == 2 and homing[1][:3] == ("open_home", "end", True)


def test_home_on_open_end_event_on_failure_and_on_stop():
    """The console's homing popup closes however homing ends: a failed axis, or STOP."""
    r = Rig(cfg={"home_on_open": True})
    r.until(lambda: r.ctl.manual.get("shift") is not None and r.ctl.manual["shift"].state == "running")
    r.sim.axes["shift"].servo = False
    r.until(lambda: not r.ctl.manual)
    end = [e for e in r.events if e[:2] == ("open_home", "end")]
    assert len(end) == 1 and end[0][2] is False and any("drive/axis fault" in m for m in end[0][3])

    s = Rig(cfg={"home_on_open": True})
    s.ctl.submit("stop")
    s.step()
    end = [e for e in s.events if e[:2] == ("open_home", "end")]
    assert len(end) == 1 and end[0][2] is False and not s.ctl.open_homing and not s.ctl.manual
    assert all("cancelled by STOP" in m for m in end[0][3])


def test_home_on_open_off_by_default_and_blocked_without_writes():
    assert not Rig().ctl.manual
    r = Rig(cfg={"home_on_open": True}, owner=False)
    assert not r.ctl.manual and r.sim.starts["shift"]["home"] == 0
    assert any("Automatic home on open skipped" in m for m in r.messages())
    assert not [e for e in r.events if e[0] == "open_home"]          # nothing started: no popup


def test_home_refused_when_permission_off_but_origin_on():
    r = Rig()
    r.confirm_params()
    a = r.sim.axes["shift"]
    a.target, r.sim.stuck["shift"] = a.pos + 1000, True      # positioning module busy
    r.step()
    snap = read_snapshot(r.sim, now=r.clock())
    with pytest.raises(OpBlocked, match="still busy"):
        HomeOp("shift").start(snap, r.clock())


def test_measurement_start_timeout_branch_is_a_failure():
    r = Rig()
    r.confirm_params(values=PARAMS[:12] + [200, 100])
    r.sim.stuck["shift"] = True
    r.ctl.submit("run", dict(INFO, type="AT"))
    r.step()
    r.ctl.submit("scan", "select", "S")
    r.until(lambda: r.ctl.flow == "at_scan_shift")
    r.ctl.submit("scan", "shift", "H")
    r.until(lambda: r.ctl.flow == "idle")
    assert any("timeout branch" in x for x in r.parts()[-1]["reasons"])
    assert r.parts()[-1]["verdict"] == "INCOMPLETE" and not r.parts()[-1]["counted"]


# ------------------------------------------------------------------ stale requests, STOP, disconnect, faults
def test_stale_request_bits_reported_not_resent_released_by_stop():
    r = Rig()
    r.sim.connected = False
    r.run(0.5)
    r.sim.D[4101] |= 1 << 1                          # M07011: the drive that moves the SELECT cable
    r.sim.connected = True
    r.step()
    assert r.ctl.stale == [("select", "pull")]
    r.ctl.submit("run", dict(INFO, type="AT"))
    r.step()
    assert "press STOP" in r.messages()[-1]
    before = len(r.sim.writes)
    r.ctl.submit("stop")
    r.step()
    assert r.sim.D[4101] & 0b10 == 0 and r.ctl.stale == [] and len(r.sim.writes) == before + 1
    r.run(3.0)
    r.confirm_params()
    r.run_part("AT")
    assert r.parts()[-1]["verdict"] == "OK"


def test_disconnect_during_stroke_latches_fault_no_resend():
    r = Rig()
    r.confirm_params()
    r.ctl.submit("run", dict(INFO, type="AT"))
    r.step()
    r.ctl.submit("scan", "select", "S1")
    r.until(lambda: r.sim.axes["select"].seq["pull"].word != 0)
    r.sim.connected = False
    r.run(1.0)
    r.sim.connected = True
    r.until(lambda: r.ctl.flow == "idle")
    r.run(3.0)
    part = r.parts()[-1]
    assert part["verdict"] == "INCOMPLETE" and not part["counted"]
    assert r.sim.requests_seen["select"]["pull"] == 1 and r.requests_clear()
    assert r.ctl.fault
    r.ctl.submit("run", dict(INFO, type="AT"))
    r.step()
    assert "Fault latched" in r.messages()[-1]
    r.ctl.submit("stop")
    r.step()
    assert r.ctl.fault is None
    r.confirm_params()
    r.run_part("AT")
    assert r.parts()[-1]["verdict"] == "OK"


def test_stop_then_late_completion_is_ignored():
    r = Rig()
    r.confirm_params()
    r.ctl.submit("run", dict(INFO, type="AT"))
    r.step()
    r.ctl.submit("scan", "select", "S1")
    r.until(lambda: r.sim.axes["select"].seq["pull"].step == 3)
    r.ctl.submit("stop")
    r.step()
    assert r.sim.D[4101] & 0b1000 == 0
    assert "Machine stop NOT confirmed" in r.ctl.status_summary()["stop_state"]
    r.run(5.0)
    assert r.sim.axes["select"].seq["pull"].word == 0
    assert [p["verdict"] for p in r.parts()] == ["ABORTED"] and not r.parts()[0]["counted"]
    assert not [e for e in r.events if e[0] == "axis_result"]
    r.ctl.submit("scan", "select", "LATE")
    r.step()
    assert "press RUN first" in r.messages()[-1]


def test_one_axis_fails_while_the_other_finishes():
    r = Rig(timeouts={"stroke": 3.0})
    r.confirm_params()
    r.ctl.submit("run", dict(INFO, type="MT"))
    r.step()
    r.ctl.submit("scan", "select", "MT-1")
    r.until(lambda: r.sim.axes["select"].seq["pull"].word != 0)
    r.sim.stuck["select"] = True
    r.until(lambda: r.ctl.flow == "idle", limit=40)
    assert [e[1] for e in r.events if e[0] == "axis_result"] == ["shift"]
    part = r.parts()[-1]
    assert part["verdict"] == "INCOMPLETE" and [s["verdict"] for s in part["stages"]] == ["ERROR", "OK"]
    assert r.sim.D[4101] & 0b1000 == 0


def test_missing_invalid_and_insufficient_samples_are_incomplete():
    r = Rig()
    r.confirm_params()
    r.sampler.data[("shift", "push")] = []
    r.sampler.data[("select", "pull")] = [(0.0, None, None)] * 4 + [(1.0, 5.0, None)]
    r.run_part("MT")
    part = r.parts()[-1]
    assert part["verdict"] == "INCOMPLETE" and not part["counted"]
    text = " ".join(part["reasons"])
    assert "PUSH stroke 1: 0 valid samples" in text and "PULL stroke 1: 1 valid samples" in text


def test_exactly_one_result_per_part():
    r = Rig()
    r.confirm_params()
    r.run_part("AT", ("A1", "B1"))
    r.run_part("MT", ("C1",))
    r.run(3.0)
    parts = r.parts()
    assert [p["counted"] for p in parts] == [True, True] and len({p["part_id"] for p in parts}) == 2


# ------------------------------------------------------------------ evaluation
def test_evaluation_rules_are_explicit():
    s = [{"pull": [(i, v, None) for i, v in enumerate((2.0, -6.0, 3.0))],
          "push": [(i, 3.0, None) for i in range(3)]}]
    assert evaluate_axis(s, COND, {})["verdict"] == "INCOMPLETE"
    ok = evaluate_axis(s, COND, RULES)
    assert ok["verdict"] == "OK" and ok["pull"] == [6.0]
    signed = evaluate_axis(s, COND, dict(RULES, force_sign="signed"))
    assert signed["verdict"] == "NG" and signed["pull"] == [-6.0]
    edge = [{"pull": [(i, 10.0, None) for i in range(3)], "push": [(i, 3.0, None) for i in range(3)]}]
    assert evaluate_axis(edge, COND, RULES)["verdict"] == "OK"
    assert evaluate_axis(edge, COND, dict(RULES, limit_bounds="exclusive"))["verdict"] == "NG"
    pos = [{"pull": [(0, 2.0, 0.0), (1, 4.0, 10.0), (2, 6.0, 20.0)],
            "push": [(0, 3.0, 0.0), (1, 3.0, 10.0), (2, 3.0, 20.0)]}]
    # judge = positions: the load at the model's Position 1-3 on the centred stroke axis (the
    # LVDT's PULL start = -stroke/2); without positions -(stroke/2 - 2), 0, +(stroke/2 - 2)
    given = evaluate_axis(pos, dict(COND, stroke=20.0, positions=[-6.0, 0.0, 8.0]), dict(RULES, judge="positions"))
    assert given["positions"] == [-6.0, 0.0, 8.0] and [round(v, 6) for v in given["pull"]] == [2.8, 4.0, 5.6]
    res = evaluate_axis(pos, dict(COND, stroke=20.0), dict(RULES, judge="positions"))
    assert res["positions"] == [-8.0, 0.0, 8.0] and res["stroke_source"] == "lvdt"
    assert [round(v, 6) for v in res["pull"]] == [2.4, 4.0, 5.6] and res["push"] == [3.0, 3.0, 3.0]
    assert res["verdict"] == "OK"
    out = evaluate_axis(pos, dict(COND, stroke=30.0), dict(RULES, judge="positions"))   # LVDT covers -15..+5
    assert out["verdict"] == "INCOMPLETE" and "13 mm not covered" in out["problems"][0]
    none = evaluate_axis(pos, COND, dict(RULES, judge="positions"))
    assert none["verdict"] == "INCOMPLETE" and "Stroke condition is empty" in none["problems"][0]
    # no LVDT: INCOMPLETE, with or without a Speed condition (no speed x time stroke)
    timed = [{"pull": [(10.0, 2.0, None), (10.22, 4.0, None), (10.44, 6.0, None)],
              "push": [(11.0, 3.0, None), (11.22, 5.0, None), (11.44, 7.0, None)]}]
    cmd = evaluate_axis(timed, dict(COND, stroke=22.0, speed=50.0), dict(RULES, judge="positions"))
    assert cmd["verdict"] == "INCOMPLETE" and cmd["stroke_source"] is None
    assert cmd["positions"] == [-9.0, 0.0, 9.0] and cmd["pull"] == [None, None, None]
    assert "no stroke data for positional sampling (no LVDT reading)" in cmd["problems"][0]
    assert evaluate_axis(timed, dict(COND, stroke=22.0), dict(RULES, judge="positions"))["verdict"] == "INCOMPLETE"
    # an LVDT that is read but does not follow the axis (not wired / not scaled): INCOMPLETE
    flat = [{d: [(t, v, 4.97) for t, v, _s in s] for d, s in timed[0].items()}]
    stuck = evaluate_axis(flat, dict(COND, stroke=22.0, speed=50.0), dict(RULES, judge="positions"))
    assert stuck["verdict"] == "INCOMPLETE" and stuck["stroke_source"] is None
    assert "LVDT reading moved only 0.00 of 22 mm" in stuck["problems"][0]


# ------------------------------------------------------------------ result saving
def test_failed_save_retry_without_duplicates(tmp_path):
    table, fail = [], {"n": 1}

    def insert(rows):
        if any(r["TR_PART_UID"] == rows[0]["TR_PART_UID"] for r in table):
            return 0
        table.extend(rows)
        if fail["n"]:                                  # saved, but the acknowledgement is lost
            fail["n"] -= 1
            raise ConnectionError("lost acknowledgement")
        return len(rows)

    rows = lambda rec: [{"TR_PART_UID": rec["part_id"], "TR_STAGE": s} for s in ("SELECT", "SHIFT")]
    path = str(tmp_path / "unsaved.json")
    store = ResultStore(path, insert, rows)
    rec = {"part_id": "P1", "counted": True}
    store.submit(rec)
    for _ in range(200):
        if not store._busy:
            break
        time.sleep(0.01)
    assert store.pending() == ["P1"] and store.events.get_nowait()[0] == "failed"
    assert ResultStore(path, insert, rows).pending() == ["P1"]       # survives a restart
    store.save_now()
    assert store.pending() == [] and len(table) == 2
    assert store.events.get_nowait()[0] == "saved" and store.events.empty()
    assert store.submit(rec) is False and len(table) == 2           # duplicate submission ignored


# ---------------------------------------------------------------- PC jog (backup 2026-11-08)
def _jog_bit(r, axis, op):
    idx, n = pp.COMMANDS[axis][op]
    return bool((r.sim.D[4100 + idx] >> n) & 1)


def _hold(r, axis, op, seconds):
    """The UI repeats the jog command while the button is down (every 0.2 s)."""
    for _ in range(int(seconds / 0.2)):
        r.ctl.submit("jog", axis, op)
        r.run(0.2)


def test_jog_bits_follow_the_comment_table():
    assert pp.command_name("select", "jog_left_high").endswith("M07014")     # drives exchanged
    assert pp.command_name("select", "jog_right_high").endswith("M07017")
    assert pp.command_name("shift", "jog_left_high").endswith("M07018")
    assert pp.command_name("shift", "jog_right_high").endswith("M0701B")


def test_jog_runs_while_held_and_stops_on_release():
    r = Rig()
    r.confirm_params()
    start = r.sim.axes["shift"].pos
    _hold(r, "shift", "jog_left_low", 1.0)
    assert _jog_bit(r, "shift", "jog_left_low") and r.sim.axes["shift"].jogging == "left"
    assert r.sim.axes["shift"].pos > start
    r.ctl.submit("jog", "shift", None)
    r.run(0.1)                                         # the PLC scans the released bit
    assert not _jog_bit(r, "shift", "jog_left_low") and r.sim.axes["shift"].jogging is None
    assert not r.ctl.jogs


def test_jog_dead_man_drops_the_bit_without_refresh():
    r = Rig()
    r.confirm_params()
    r.ctl.submit("jog", "select", "jog_right_high")
    r.step()
    assert _jog_bit(r, "select", "jog_right_high")
    r.run(1.0)                                         # no refresh (UI hung, button lost)
    assert not _jog_bit(r, "select", "jog_right_high")
    assert any("stopped refreshing" in m for m in r.messages())


def test_jog_refused_at_limit_without_servo_or_with_alarm():
    r = Rig()
    r.confirm_params()                                 # SH left limit 500 -> origin + 50000
    r.sim.axes["shift"].pos = 1_000_000 + 60_000       # beyond it
    r.run(0.2)
    r.ctl.submit("jog", "shift", "jog_left_high")
    r.step()
    assert "limit left reached" in r.messages()[-1] and not _jog_bit(r, "shift", "jog_left_high")
    r.sim.axes["select"].servo = False
    r.run(0.2)
    r.ctl.submit("jog", "select", "jog_left_low")
    r.step()
    assert "servo is not ON" in r.messages()[-1] and not _jog_bit(r, "select", "jog_left_low")
    r.sim.axes["select"].servo, r.sim.axes["select"].alarm_normal = True, False
    r.run(0.2)
    r.ctl.submit("jog", "select", "jog_left_low")
    r.step()
    assert "drive alarm" in r.messages()[-1]


def test_jog_released_by_stop_and_on_reconnection_after_a_lost_link():
    r = Rig()
    r.confirm_params()
    _hold(r, "shift", "jog_right_low", 0.4)
    r.ctl.submit("stop")
    r.step()
    assert not _jog_bit(r, "shift", "jog_right_low")
    _hold(r, "shift", "jog_right_low", 0.4)
    r.sim.connected = False
    r.run(0.3)
    assert not r.ctl.jogs and _jog_bit(r, "shift", "jog_right_low")     # PLC keeps it while unreachable
    r.sim.connected = True
    r.run(0.5)
    assert not _jog_bit(r, "shift", "jog_right_low")                    # released on reconnection


def test_jog_needs_the_write_gate():
    r = Rig(verified=False)
    r.ctl.submit("jog", "shift", "jog_left_low")
    r.step()
    assert "not verified" in r.messages()[-1] and not _jog_bit(r, "shift", "jog_left_low")


def test_plc_params_from_cable_conditions():
    from friction_tester import db
    row = {}
    for t in ("SHIFT", "SELECT"):
        row.update({f"MM_{t}_SPEED": 10, f"MM_{t}_STROKE": 5, f"MM_{t}_POSITION_1": -30,
                    f"MM_{t}_POSITION_2": -5, f"MM_{t}_POSITION_3": 30})
    values = db.plc_params_from_row(row)
    # jog low = speed / 2, jog high = speed, speed, PULL = stroke, limits = Stroke / 2 + 2 mm
    # rounded up (Position 1-3 are capture points, not sent); start centred: Stroke / 2 x 10
    # (stroke 5 -> start 2.5 mm on the - side)
    assert values == [5, 10, 10, 5, 5, 5] * 2 + [25, 25]
    t = derived_targets(values, False)["shift"]
    assert t["start"] == 1_000_000 - 250 and t["pull"] == t["start"] + 500 + 100
    assert not validate_params(values) and not target_problems(derived_targets(values, False))
    # the model from the machine: Stroke 30 with Position 1 / 3 = -10 / +10 inside the stroke
    for t in ("SHIFT", "SELECT"):
        row.update({f"MM_{t}_STROKE": 30, f"MM_{t}_POSITION_1": -10, f"MM_{t}_POSITION_3": 10})
    values = db.plc_params_from_row(row)
    t = derived_targets(values, False)["select"]
    assert (t["right_limit"], t["start"], t["pull"], t["left_limit"]) == (998300, 998500, 1001600, 1001700)
    assert not target_problems(derived_targets(values, False))


def test_db_schema_names_defined():
    """init_database needs these module names (a lost SCHEMA broke the Model page once)."""
    from friction_tester import db
    for name in ("SCHEMA", "RESULTS_SCHEMA", "ADMIN_SCHEMA", "LOGIN_HISTORY_SCHEMA", "RENAMED_COLUMNS",
                 "RETIRED_COLUMNS"):
        assert getattr(db, name), name
    assert "MM_SELECT_POSITION_1" in db.SCHEMA


def test_sampler_records_why_loadcell_reads_fail():
    from friction_tester.plc.pc_measure import Sampler

    class Lc:
        def __init__(self, is_open, err=None):
            self.is_open, self.err = is_open, err

        def read(self):
            if self.err:
                raise TimeoutError(self.err)
            return None, "ID001,+0001.00", 1.0

    settings = {"test": {"machine_profile": {"measurement": {"force_source": "serial",
                                                             "loadcell": {"select": 1, "shift": 2}}}}}
    s = Sampler([Lc(True), Lc(False)], None, settings, period=0.005)
    for axis, expect in (("select", None), ("shift", "loadcell L2 is not connected")):
        cap = s.start(axis, "pull", "t", 1)
        time.sleep(0.05)
        samples = s.stop(cap)
        assert samples
        if expect is None:
            assert cap.last_error is None and all(x[1] == 1.0 for x in samples)
        else:
            assert expect in cap.last_error and all(x[1] is None for x in samples)


def test_run_waits_for_both_axes_at_origin():
    """RUN pressed while both axes are homing waits, then starts by itself when both are home."""
    r = Rig()
    r.confirm_params()
    r.ctl.submit("home", "all")
    r.step()
    assert set(r.ctl.manual) == {"shift", "select"}
    r.ctl.submit("run", dict(INFO, type="AT"))
    r.step()
    assert r.ctl.part is None and r.ctl.pending_run is not None
    assert "Waiting for" in r.messages()[-1]
    assert r.sim.starts["select"]["pull"] == 0
    r.until(lambda: r.ctl.part is not None, limit=60)          # armed once both are home
    assert r.sim.starts["select"]["home"] == 1 and r.sim.starts["shift"]["home"] == 1
    assert r.ctl.pending_run is None


def test_run_homes_an_axis_that_is_not_at_origin_first():
    r = Rig(skip_scan=True)
    r.confirm_params()
    r.sim.axes["shift"].origin_complete = False
    r.step()
    r.ctl.submit("run", dict(INFO, type="AT"))
    r.step()
    assert set(r.ctl.manual) == {"shift"} and r.ctl.part is None
    r.until(lambda: r.ctl.part is not None, limit=30)
    assert r.sim.axes["shift"].origin_complete and r.sim.starts["shift"]["home"] == 1


def test_stop_cancels_a_waiting_run():
    r = Rig()
    r.confirm_params()
    r.ctl.submit("home", "all")
    r.step()
    r.ctl.submit("run", dict(INFO, type="AT"))
    r.step()
    assert r.ctl.pending_run is not None
    r.ctl.submit("stop")
    r.step()
    assert r.ctl.pending_run is None
    r.until(lambda: not r.ctl.manual, limit=30)
    r.run(1.0)
    assert r.ctl.part is None and not r.parts()


class FakeDaq:
    """NIDaq stand-in: connect() opens, read_block() returns BLOCK samples of fixed volts
    per channel (a list in `volts` is one block's samples as given)."""
    BLOCK = 4

    def __init__(self, volts=None, fail=None):
        self.volts, self.fail, self.cfg, self.reads = volts or {}, fail, None, 0

    @property
    def is_open(self):
        return self.cfg is not None

    def connect(self, cfg):
        if self.fail:
            raise RuntimeError(self.fail)
        self.cfg = cfg

    def read_block(self):
        self.reads += 1
        time.sleep(0.002)                          # a real block takes its acquisition time
        return {ch: list(v) if isinstance(v, list) else [v] * self.BLOCK for ch, v in self.volts.items()}


def _sampler_settings(force_source="serial", stroke=None, graph_force="result"):
    return {"daq": {"device": "Dev1", "terminal_config": "Differential", "sample_rate": 1000.0,
                    "samples_per_channel": 100,
                    "sensors": [{"name": "Loadcell 1", "channel": "ai0", "range": "±10 V", "scale": 2.0,
                                 "offset_v": 0.0, "unit": "kgf"},
                                {"name": "LVDT 1", "channel": "ai2", "range": "±10 V", "scale": 5.0,
                                 "offset_v": 1.0, "unit": "mm"}]},
            "test": {"machine_profile": {"measurement": {
                "force_source": force_source, "graph_force": graph_force,
                "loadcell": {"select": 1, "shift": 2},
                "daq_force": {"select": "Loadcell 1", "shift": ""},
                "stroke": stroke if stroke is not None else {"select": "LVDT 1", "shift": ""}}}}}


class _Lc:
    is_open = True

    def read(self):
        return None, "ID001,+0000.50", 0.5


def test_sampler_opens_the_ni_card_and_reads_load_and_stroke_in_one_block():
    from friction_tester.plc.pc_measure import Sampler
    daq = FakeDaq({"ai0": 0.25, "ai2": 3.0})
    s = Sampler([_Lc(), _Lc()], daq, _sampler_settings("daq"))
    load, stroke, raw_load, raw_stroke = s.read_once("select")
    assert (load, stroke, raw_load, raw_stroke) == (0.5, 10.0, 0.25, 3.0)
    assert daq.reads == 1                                          # one acquisition per sample
    assert [c["channel"] for c in daq.cfg["channels"]] == ["ai2", "ai0"]
    assert daq.cfg["samples_per_channel"] == 50                    # 50 ms block at 1000 S/s


def test_graph_takes_every_analog_sample_of_the_ni_card():
    """graph_force = "daq": each sample of a block is a graph point (load against LVDT),
    not one averaged point per result sample; the result takes the newest ~10 ms."""
    from friction_tester.plc.pc_measure import Sampler
    ramp = [0.01 * i for i in range(50)]                           # one 50 ms block at 1000 S/s
    daq = FakeDaq({"ai0": ramp, "ai2": [1.0 + v for v in ramp]})
    s = Sampler([_Lc(), _Lc()], daq, _sampler_settings("serial", graph_force="daq"), period=0.005)
    cap = s.start("select", "pull", "t", 1)
    time.sleep(0.05)
    s.stop(cap)
    time.sleep(0.02)                                               # the reader thread ends
    assert s._ni_thread is None and s._ni_live is None
    assert len(cap.graph) >= 50 and len(cap.graph) % 50 == 0       # whole blocks, no result points
    first = cap.graph[:50]
    assert [round(g[1], 6) for g in first] == [round(2.0 * v, 6) for v in ramp]      # x 2.0 kgf/V
    assert [round(g[2], 6) for g in first] == [round(5.0 * v, 6) for v in ramp]      # (V - 1.0) x 5.0
    assert round(first[-1][0] - first[0][0], 6) == 0.049           # 1 ms apart
    load, stroke = cap.samples[-1][1:3]
    assert load == 0.5 and round(stroke, 6) == round(5.0 * sum(ramp[-10:]) / 10, 6)   # newest 10 ms


def test_sampler_reopens_the_ni_card_when_the_daq_settings_change():
    from friction_tester.plc.pc_measure import Sampler
    daq = FakeDaq({"ai0": 0.25, "ai1": 0.75, "ai2": 3.0})
    settings = _sampler_settings("daq")
    s = Sampler([_Lc(), _Lc()], daq, settings)
    assert s.read_once("select")[0] == 0.5
    first = daq.cfg
    assert s.read_once("select")[0] == 0.5 and daq.cfg is first    # unchanged settings: same task
    # COM Port page SAVE replaces settings["daq"]: Loadcell 1 moved to ai1, wiring RSE
    new = {**settings["daq"], "terminal_config": "RSE",
           "sensors": [dict(sen, channel="ai1") if sen["name"] == "Loadcell 1" else sen
                       for sen in settings["daq"]["sensors"]]}
    settings["daq"] = new
    assert s.read_once("select")[0] == 1.5                         # the new channel, not the old task
    assert [c["channel"] for c in daq.cfg["channels"]] == ["ai2", "ai1"]
    assert daq.cfg["terminal_config"] == "RSE"


def test_sampler_keeps_the_serial_load_when_the_ni_card_fails():
    from friction_tester.plc.pc_measure import Sampler
    s = Sampler([_Lc(), _Lc()], FakeDaq(fail="Dev1 not found"), _sampler_settings("serial"))
    load, stroke, _raw, _rs = s.read_once("select")
    assert load == 0.5 and stroke is None and "Dev1 not found" in s.daq_error


def test_sampler_keeps_the_capture_for_the_graph():
    from friction_tester.plc.pc_measure import Sampler
    s = Sampler([_Lc(), _Lc()], FakeDaq({"ai2": 2.0}), _sampler_settings("serial"), period=0.005)
    cap = s.start("select", "pull", "t", 1)
    time.sleep(0.04)
    s.stop(cap)
    assert s.captures["select"] is cap and cap.samples[0][1:3] == (0.5, 5.0)


class _SlowOpenDaq(FakeDaq):
    """The first DAQmx task of a process takes seconds to create."""

    def __init__(self, volts, open_s):
        super().__init__(volts)
        self.open_s = open_s

    def connect(self, cfg):
        time.sleep(self.open_s)
        super().connect(cfg)


def test_a_slow_ni_card_open_does_not_cost_the_stroke_its_samples():
    """Field log: the first PULL after startup had 0 valid samples - the capture loop
    opened the NI card itself and the serial load waited behind it for the whole stroke."""
    from friction_tester.plc.pc_measure import Sampler
    s = Sampler([_Lc(), _Lc()], _SlowOpenDaq({"ai2": 2.0}, open_s=0.6), _sampler_settings("serial"),
                period=0.005)
    cap = s.start("select", "pull", "t", 1)
    time.sleep(0.3)                                                # the stroke ends before the card opens
    t0 = time.monotonic()
    samples = s.stop(cap)
    assert time.monotonic() - t0 < 0.5                             # the capture thread was not stuck
    assert len(samples) >= 10 and all(x[1] == 0.5 for x in samples)  # serial load sampled throughout
    assert all(x[2] is None for x in samples)                      # no LVDT yet - not invented
    assert "still opening" in cap.lvdt_note
    time.sleep(0.5)                                                # the reader thread finishes and ends


def test_prepare_opens_the_ni_card_before_the_first_stroke():
    from friction_tester.plc.pc_measure import Sampler
    daq = _SlowOpenDaq({"ai2": 2.0}, open_s=0.1)
    s = Sampler([_Lc(), _Lc()], daq, _sampler_settings("serial"), period=0.005)
    th = s.prepare()
    assert s.prepare() is th                                       # one open at a time
    th.join(1.0)
    assert daq.is_open and s._prep_thread is None
    cap = s.start("select", "pull", "t", 1)
    time.sleep(0.04)
    s.stop(cap)
    assert cap.samples[0][1:3] == (0.5, 5.0)                       # LVDT from the first sample on
    assert Sampler([_Lc(), _Lc()], None, _sampler_settings("serial")).prepare() is None


def test_stop_records_a_capture_thread_that_is_still_blocked(monkeypatch):
    from friction_tester.plc.pc_measure import Sampler

    class Hang:
        is_open = True

        def read(self):
            time.sleep(0.4)
            return None, "ID001,+0000.50", 0.5

    s = Sampler([Hang(), Hang()], None, _sampler_settings("serial", stroke={"select": "", "shift": ""}))
    monkeypatch.setattr(Sampler, "STOP_JOIN_S", 0.05)
    cap = s.start("select", "pull", "t", 1)
    time.sleep(0.05)
    assert s.stop(cap) == [] and "still blocked" in cap.last_error
    time.sleep(0.4)


def test_graph_draws_the_lvdt_stroke_and_restarts_per_stroke(monkeypatch):
    import tkinter as tk
    from friction_tester.ui import test_console
    popups = []
    monkeypatch.setattr(test_console.messagebox, "showerror", lambda *a, **k: popups.append(a))
    from friction_tester.plc.pc_measure import Capture
    from friction_tester.ui.test_console import CablePanel
    try:
        root = tk.Tk()
    except tk.TclError:
        import pytest
        pytest.skip("no display")
    root.withdraw()
    try:
        import copy
        from friction_tester.config import DEFAULT_SETTINGS
        panel = CablePanel(root, "SELECT CABLE", copy.deepcopy(DEFAULT_SETTINGS["test"]["select"]))
        pull = Capture("select", "pull", "t", 1)
        pull.graph += [(10.0, 0.1, -5.0), (10.1, None, -4.0), (10.2, 0.3, -3.0)]
        panel.draw_capture(pull)
        assert panel.plot.series["PULL"]["x"] == [-5.0, -3.0] and panel.plot.xlabel == "Stroke [mm]"
        pull.graph.append((10.3, 0.4, -2.0))
        panel.draw_capture(pull)                                     # only the new sample is added
        assert panel.plot.series["PULL"]["y"] == [0.1, 0.3, 0.4]
        push = Capture("select", "push", "t", 1)
        push.graph += [(11.0, -0.2, 6.0), (11.5, -0.1, None)]
        panel.draw_capture(push)                                     # same stroke: PULL stays
        assert panel.plot.series["PULL"]["y"] and panel.plot.series["PUSH"]["x"] == [6.0]
        pull2 = Capture("select", "pull", "t", 2)                    # next stroke: fresh graph
        pull2.graph += [(20.0, 0.2, None), (20.5, 0.3, None)]
        panel.draw_capture(pull2)
        assert not panel.plot.series["PUSH"]["x"] and not panel.plot.series["PULL"]["x"]
        assert not panel.plot.note and not popups                    # still running: not reported yet
        pull2.stop_event.set()                                       # ended without an LVDT reading
        panel.draw_capture(pull2)
        root.update()
        assert not panel.plot.series["PULL"]["x"] and panel.plot.xlabel == "Stroke [mm]"
        assert "No LVDT reading" in panel.plot.note and len(popups) == 1
    finally:
        root.destroy()


def test_graph_without_lvdt_is_not_drawn_and_reports_it_once_a_test(monkeypatch):
    """Speed and Stroke set, no LVDT: nothing is drawn (no speed x time stroke); a red note
    and one error popup a test say why. The scale stays the LabVIEW one."""
    import tkinter as tk
    from friction_tester.ui import test_console
    popups = []
    monkeypatch.setattr(test_console.messagebox, "showerror", lambda *a, **k: popups.append(a))
    from friction_tester.plc.pc_measure import Capture
    from friction_tester.ui.test_console import CablePanel
    try:
        root = tk.Tk()
    except tk.TclError:
        import pytest
        pytest.skip("no display")
    root.withdraw()
    try:
        import copy
        from friction_tester.config import DEFAULT_SETTINGS
        cfg = dict(copy.deepcopy(DEFAULT_SETTINGS["test"]["select"]), speed=50.0, stroke=22.0)
        panel = CablePanel(root, "SELECT CABLE", cfg)
        assert [round(v, 6) for v in panel.plot.xlim] == [-13.2, 13.2] and panel.plot.start_x == -11.0
        pull = Capture("select", "pull", "t", 1)
        pull.lvdt_note = "NI card: Dev1 not found"
        pull.graph += [(10.0 + i * 0.03, 0.4, None) for i in range(10)]
        panel.draw_capture(pull)                                     # 10 samples without a stroke
        root.update()
        assert not panel.plot.series["PULL"]["x"] and len(popups) == 1
        assert "SELECT CABLE" in popups[0][1] and "Dev1 not found" in popups[0][1]
        assert "No LVDT reading" in panel.plot.note
        push = Capture("select", "push", "t", 1)
        push.graph += [(12.0, 0.0, None), (12.2, -0.3, None)]
        push.stop_event.set()
        panel.draw_capture(push)
        root.update()
        assert not panel.plot.series["PUSH"]["x"] and len(popups) == 1   # same test: one popup
        assert panel.plot.xlabel == "Stroke [mm]"
        assert [round(v, 6) for v in panel.plot.xlim] == [-13.2, 13.2]   # the scale did not move
        again = Capture("select", "pull", "t2", 1)                    # the next test reports it again
        again.graph += [(20.0, 0.1, None)]
        again.stop_event.set()
        panel.draw_capture(again)
        root.update()
        assert len(popups) == 2
    finally:
        root.destroy()


def test_graph_centres_the_lvdt_stroke_on_the_stroke_condition(monkeypatch):
    """The LVDT reads an absolute position (never negative here); with a Stroke condition
    the PULL start is drawn at -stroke/2 and the pull direction is positive."""
    import tkinter as tk
    from friction_tester.ui import test_console
    popups = []
    monkeypatch.setattr(test_console.messagebox, "showerror", lambda *a, **k: popups.append(a))
    from friction_tester.plc.pc_measure import Capture
    from friction_tester.ui.test_console import CablePanel
    try:
        root = tk.Tk()
    except tk.TclError:
        import pytest
        pytest.skip("no display")
    root.withdraw()
    try:
        import copy
        from friction_tester.config import DEFAULT_SETTINGS
        cfg = dict(copy.deepcopy(DEFAULT_SETTINGS["test"]["select"]), speed=50.0, stroke=22.0)
        panel = CablePanel(root, "SELECT CABLE", cfg)
        pull = Capture("select", "pull", "t", 1)
        pull.graph += [(10.0, 0.0, 40.0), (10.2, 0.4, 51.0), (10.4, 0.4, 62.0)]
        panel.draw_capture(pull)
        assert panel.plot.series["PULL"]["x"] == [-11.0, 0.0, 11.0] and panel.plot.xlabel == "Stroke [mm]"
        push = Capture("select", "push", "t", 1)
        push.graph += [(11.0, -0.3, 62.0), (11.2, -0.3, 40.0)]
        panel.draw_capture(push)
        assert panel.plot.series["PUSH"]["x"] == [11.0, -11.0]
        pull2 = Capture("select", "pull", "t", 2)                    # LVDT wired the other way round
        pull2.graph += [(20.0, 0.0, 62.0), (20.2, 0.4, 51.0)]
        panel.draw_capture(pull2)
        assert panel.plot.series["PULL"]["x"] == [-11.0, 0.0] and not panel.plot.series["PUSH"]["x"]
        pull3 = Capture("select", "pull", "t", 3)                    # LVDT read but not moving
        pull3.graph += [(30.0, 0.0, 4.97), (30.2, 0.4, 4.97), (30.9, 0.4, 4.98)]
        panel.draw_capture(pull3)
        assert panel.plot.series["PULL"]["x"] == [-11.0]             # drawn as read: one point
        assert not popups                                            # reported when the stroke ends
        pull3.stop_event.set()
        panel.draw_capture(pull3)
        root.update()
        assert panel.plot.xlabel == "Stroke [mm]" and len(popups) == 1
        assert "LVDT moved only 0.00 mm" in panel.plot.note and "22.0 mm" in popups[0][1]
    finally:
        root.destroy()


def test_graph_has_three_averaged_points_per_2_mm_of_stroke():
    """The NI card delivers every analog sample; the graph draws one point per 2/3 mm of
    LVDT stroke, the mean of the samples in it, so the sensor noise is not drawn."""
    import tkinter as tk
    from friction_tester.plc.pc_measure import Capture
    from friction_tester.ui.test_console import CablePanel
    try:
        root = tk.Tk()
    except tk.TclError:
        import pytest
        pytest.skip("no display")
    root.withdraw()
    try:
        import copy
        from friction_tester.config import DEFAULT_SETTINGS
        panel = CablePanel(root, "SELECT CABLE", copy.deepcopy(DEFAULT_SETTINGS["test"]["select"]))
        pull = Capture("select", "pull", "t", 1)
        # 2 mm of stroke, 100 samples per mm, the load 0.30 kgf +/- 0.05 of noise
        pull.graph += [(10.0 + i * 0.001, 0.30 + (0.05 if i % 2 else -0.05), 40.0 + i * 0.01)
                       for i in range(200)]
        panel.draw_capture(pull)
        xs, ys = panel.plot.series["PULL"]["x"], panel.plot.series["PULL"]["y"]
        assert len(xs) == 3 and xs == sorted(xs)                     # 1.5 points per mm
        assert all(abs(y - 0.30) < 0.002 for y in ys)                # the noise is averaged out
        assert abs(xs[0] - 40.33) < 0.01 and abs(xs[1] - xs[0] - 2 / 3) < 0.01
        pull.graph += [(10.2 + i * 0.001, 0.50, 42.0) for i in range(50)]   # dwell at the stroke end
        panel.draw_capture(pull)
        assert len(xs) == 4 and ys[-1] == 0.50                       # one point, not a blob
    finally:
        root.destroy()


def test_sampler_retries_the_ni_card_after_a_failed_open():
    from friction_tester.plc.pc_measure import Sampler
    daq = FakeDaq({"ai0": 0.25, "ai2": 3.0}, fail="Dev1 not found")
    s = Sampler([_Lc(), _Lc()], daq, _sampler_settings("daq"))
    s.DAQ_RETRY_S = 0.0
    try:
        s.read_once("select")
        raise AssertionError("NI force read should fail while the card is missing")
    except RuntimeError as exc:
        assert "Dev1 not found" in str(exc)
    daq.fail = None                                        # card plugged in / device selected
    assert s.read_once("select")[0] == 0.5 and s.daq_error is None


def test_results_from_serial_graph_from_the_ni_card():
    from friction_tester.plc.pc_measure import Sampler
    daq = FakeDaq({"ai0": 0.25, "ai2": 3.0})
    s = Sampler([_Lc(), _Lc()], daq, _sampler_settings("serial", graph_force="daq"), period=0.005)
    cap = s.start("select", "pull", "t", 1)
    time.sleep(0.04)
    s.stop(cap)
    assert cap.samples[0][1:4] == (0.5, 10.0, "ID001,+0000.50")   # result: serial loadcell
    assert cap.graph[0][1:] == (0.5, 10.0)                          # graph: NI 0.25 V x 2.0, LVDT
    assert len(cap.graph) > len(cap.samples)                        # every analog sample, not one per result
    time.sleep(0.02)                                                # the reader thread ends
    daq.volts["ai0"] = 0.1
    g = {}
    assert s.read_once("select", g)[0] == 0.5 and g == {"load": 0.2, "stroke": 10.0}


def test_graph_keeps_the_ni_point_when_the_serial_read_fails():
    from friction_tester.plc.pc_measure import Sampler

    class Bad:
        is_open = True

        def read(self):
            raise TimeoutError("no reply")

    s = Sampler([Bad(), Bad()], FakeDaq({"ai0": 0.25, "ai2": 3.0}),
                _sampler_settings("serial", graph_force="daq"), period=0.005)
    cap = s.start("select", "pull", "t", 1)
    time.sleep(0.03)
    s.stop(cap)
    assert cap.samples[0][1] is None and cap.graph[0][1:] == (0.5, 10.0)


def test_closing_mid_stroke_stops_the_capture_and_releases_the_bit():
    """App close / leaving the Test Console during a stroke: the capture ends and the request bit drops."""
    from friction_tester.plc import pc_profile as pp
    r = Rig()
    r.confirm_params()
    r.ctl.submit("run", dict(INFO, type="AT"))
    r.step()
    r.ctl.submit("scan", "select", "S")
    r.until(lambda: r.sampler.open, limit=30)            # a measuring stroke's capture is running
    idx, n = pp.COMMANDS["select"]["pull"]
    assert r.sim.D[4100 + idx] >> n & 1                    # PULL request bit ON
    r.ctl._release_everything("application closing")
    assert not r.sampler.open
    assert not r.sim.D[4100 + idx] >> n & 1                # released


def test_measurement_start_waits_for_in_position_at_the_release_step():
    """The PLC reaches the release step on its position compare; the drive's in-position
    signal can follow a poll later (seen on the machine, 2026-11-11). That is not the
    timeout branch - only a target that stays unreached is."""
    from friction_tester.plc.axis_ops import make_op

    class Snap:
        def __init__(self, **on):
            self.on = on

        def st(self, _axis, name):
            return self.on.get(name, False)

        def meas_step(self, _axis):
            return pp.MEAS_RELEASE_STEP

        def echo_bit(self, _idx, _n):
            return True

    def moving():
        op = make_op("shift", "meas_start")
        op.state, op.t_state = "moving", 0.0
        return op

    op = moving()
    assert op.step(Snap(at_meas_start=True), 0.0) == {} and op.result is None      # in-position not yet ON
    assert op.step(Snap(at_meas_start=True, pos_complete=True), 0.1) and op.state == "releasing"
    op = moving()
    op.step(Snap(at_meas_start=True), 0.0)
    op.step(Snap(at_meas_start=True), 2.5)
    assert op.result == "failed" and "in-position signal is OFF" in op.message and "timeout branch" in op.message


def test_graph_falls_back_to_the_result_load_when_the_ni_card_fails():
    from friction_tester.plc.pc_measure import Sampler
    s = Sampler([_Lc(), _Lc()], FakeDaq(fail="Select a DAQ device"),
                _sampler_settings("serial", graph_force="daq"), period=0.005)
    cap = s.start("select", "pull", "t", 1)
    time.sleep(0.03)
    s.stop(cap)
    assert cap.graph[0][1] == 0.5                                   # the serial load, so the graph still draws
    assert "Select a DAQ device" in cap.graph_note and "result load cell" in cap.graph_note
    ok = Sampler([_Lc(), _Lc()], FakeDaq({"ai0": 0.25}), _sampler_settings("serial", graph_force="daq"))
    g = {}
    ok.read_once("select", g)
    assert g["load"] == 0.5 and "note" not in g                     # NI card fine: no note
    assert g["stroke"] is None and g["lvdt"] == "no reading for 'LVDT 1'"   # why there is no stroke
    assert "Select a DAQ device" in cap.lvdt_note                   # the failed card: no LVDT either


def test_result_stores_the_drawn_graph_and_the_report_page_shows_it():
    """evaluate_axis keeps the Load-Stroke graph as drawn (centred, 1.5 averaged points
    per mm); it is saved to TBL_TEST_GRAPHS with its result row and the Report page draws
    it for the selected row."""
    import json
    from friction_tester import db
    from friction_tester.plc.pc_flow import graph_curve
    # LVDT 40 -> 62 mm over a 22 mm PULL, 100 samples per mm, then back for the PUSH
    pull = [(i * 0.01, 0.40, 40.0 + i * 0.01) for i in range(2200)]
    push = [(i * 0.01, -0.20, 62.0 - i * 0.01) for i in range(2200)]
    curve = graph_curve(pull, push, 22.0)
    assert 32 <= len(curve["pull"]) <= 35 and 32 <= len(curve["push"]) <= 35
    assert abs(curve["pull"][0][0] + 11) < 1 and abs(curve["pull"][-1][0] - 11) < 1
    assert abs(curve["push"][0][0] - 11) < 1 and all(p[1] == -0.2 for p in curve["push"])
    assert graph_curve([(0, 0.4, None)] * 5, [], 22.0) == {"pull": [], "push": []}   # no LVDT, no graph

    cond = dict(COND, load_min=0.0, load_max=1.0, stroke=22.0)
    res = evaluate_axis([{"pull": pull, "push": push}], cond, RULES)
    assert res["curve"]["stroke"] == 22.0 and res["curve"]["pull"] == curve["pull"]
    record = {"profile": "printing_program_old_pc_words", "time": "2026-11-12T10:00:00", "model": "M",
              "type": "AT", "verdict": res["verdict"], "label_select": "L1",
              "stages": [{"stage": "select", "verdict": res["verdict"], "result": json.loads(json.dumps(res))}]}
    row = db.result_rows(record, "uid", "test")[0]
    assert "GRAPH" not in db.RESULT_COLUMNS
    stored = json.loads(row["GRAPH"])
    assert stored["pull"] == curve["pull"] and stored["push"] == curve["push"]
    assert "curve" not in json.loads(row["TR_DETAIL"])["result"]          # not stored twice

    class Cur:                                   # each graph is inserted with its result row's ID
        def __init__(self):
            self.sql, self.lastrowid = [], 0

        def execute(self, sql, args=()):
            self.sql.append((sql.split()[2], list(args)))
            if sql.startswith(f"INSERT INTO {db.RESULTS_TABLE}"):
                self.lastrowid += 1

        def fetchone(self):
            return (0,)

    from contextlib import contextmanager
    cur = Cur()
    saved = db.cursor
    db.cursor = contextmanager(lambda **_kw: (yield cur))
    try:
        no_graph = dict(row, TR_STAGE="SHIFT", GRAPH=None)
        assert db.insert_test_results([row, no_graph]) == 2
    finally:
        db.cursor = saved
    graphs = [a for t, a in cur.sql if t == db.GRAPHS_TABLE]
    assert [t for t, _a in cur.sql].count(db.RESULTS_TABLE) == 2
    assert graphs == [[1, "uid", "SELECT", row["GRAPH"]]]

    _check_report_page(stored, curve, row)


def _check_report_page(stored, curve, row):
    """The Report page with the database stubbed: list, filters, sorting, the selected
    cycle (values against the limits, problems) and the graph preview / enlarge."""
    import datetime
    import json
    import tkinter as tk
    from decimal import Decimal
    from friction_tester.ui import report_page
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.withdraw()
    calls = []
    t = datetime.datetime(2026, 11, 12, 10, 0, 0)
    base = {c: None for c in report_page.db.RESULT_COLUMNS if c not in report_page.db.BULK_COLUMNS}
    rows = [dict(base, ID=1, TR_PART_UID="p1", TR_DATETIME=t, TR_MODEL="M", TR_STAGE="SELECT", TR_LABEL="L1",
                 TR_STAGE_RESULT="OK", TR_LOT_RESULT="NG", TR_CH1_FWD_1=Decimal("0.380"), TR_CH1_FINAL=Decimal("0.4")),
            dict(base, ID=2, TR_PART_UID="p1", TR_DATETIME=t, TR_MODEL="M", TR_STAGE="SHIFT", TR_LABEL="L1",
                 TR_STAGE_RESULT="NG", TR_LOT_RESULT="NG", TR_CH1_FWD_1=Decimal("0.700"), TR_CH1_FINAL=Decimal("0.7")),
            dict(base, ID=3, TR_PART_UID="p2", TR_DATETIME=t - datetime.timedelta(hours=1), TR_MODEL="M",
                 TR_STAGE="SELECT", TR_STAGE_RESULT="OK", TR_LOT_RESULT="OK", TR_CH1_FWD_1=Decimal("0.2"))]
    saved = (report_page.db.init_database, report_page.db.list_test_results, report_page.db.get_test_record)
    report_page.db.init_database = lambda: None
    report_page.db.list_test_results = lambda **kw: (calls.append(kw), rows)[1]
    report_page.db.get_test_record = lambda rid: {"graph": stored, "detail": {"result": {
        "limits": [0.0, 0.6], "positions": [-8.0, 0.0, 8.0], "problems": ["PULL1 0.700 outside 0.0..0.6"]}}}
    try:
        page = report_page.ReportPage(root, {})
        page.pack()
        root.update()
        page._show_rows(rows)
        assert page.tiles["parts"]["text"] == "2" and page.tiles["ng"]["text"] == "1"
        assert page.tiles["yield"]["text"] == "50.0%"
        iids = page.tree.get_children()
        assert page.tree.set(iids[0], "TR_CH1_FWD_1") == "0.38"                 # PULL 1 in kgf
        assert "NG" not in page.tree.item(iids[0], "tags")                     # the OK cable of an NG part
        assert "NG" in page.tree.item(iids[1], "tags")
        page.cb_stage.set("SHIFT")
        page.cb_result.set("NG")
        f = page._filters()
        assert f["stage"] == "SHIFT" and f["lot_result"] == "NG"
        page.sort_by("TR_CH1_FWD_1")                                          # numeric sort, largest first
        assert [page._iid_rows[i]["ID"] for i in page.tree.get_children()] == [2, 1, 3]
        page.tree.selection_set(page.tree.get_children()[0])                  # the NG cable (0.70)
        page._show_detail()
        page.show_record(page_record := report_page.db.get_test_record(2))
        assert page.badge["text"] == "NG" and page.cells["TR_CH1_FWD_1"]["text"] == "0.70"
        assert str(page.cells["TR_CH1_FWD_1"]["fg"]).lower() == report_page.T.L_NG.lower()   # outside 0..0.6
        assert "0.00 ~ 0.60" in page.lbl_limits["text"] and "P1 -8" in page.lbl_positions["text"]
        assert "outside" in page.lbl_problems["text"]
        assert page.plot.series["PULL"]["x"] == [p[0] for p in curve["pull"]] and page.plot.note == ""
        assert int(page.plot.canvas["width"]) <= report_page.DETAIL_W              # a small preview ...
        big = page.enlarge_graph()                                           # ... a click enlarges it
        plots = [w for w in big.winfo_children() if isinstance(w, report_page.LoadStrokePlot)]
        assert len(plots) == 1 and plots[0].series["PULL"]["x"] == page.plot.series["PULL"]["x"]
        big.destroy()
        page.show_graph(None)
        assert page.plot.series["PULL"]["x"] == [] and "No graph stored" in page.plot.note
        assert page.enlarge_graph() is None                                  # nothing to enlarge
        assert page_record["detail"] and json.loads(row["GRAPH"]) == stored
    finally:
        (report_page.db.init_database, report_page.db.list_test_results, report_page.db.get_test_record) = saved
        root.destroy()


def test_console_clears_after_both_cables_are_logged():
    """An OK / NG part saved to the database: results, graphs and labels are cleared for the
    next part of the same model; a part already running is left alone (INCOMPLETE / ABORTED parts
    never get here: only OK / NG saves call _prepare_next_part)."""
    import copy
    import tkinter as tk
    from types import SimpleNamespace
    from friction_tester.config import DEFAULT_SETTINGS
    from friction_tester.ui import test_console as tc
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.withdraw()
    try:
        settings = copy.deepcopy(DEFAULT_SETTINGS)
        settings["test"]["select"]["model"] = "0705CAJ00511N"
        page = tc.TestConsolePage.__new__(tc.TestConsolePage)
        tk.Frame.__init__(page, root)
        page.settings, page._flow, page.status = settings, "idle", tk.Label(page)
        page.ctl = SimpleNamespace(part=None)
        page.sampler = SimpleNamespace(captures={"select": object(), "shift": object()})
        page.panels = [tc.CablePanel(page, title, copy.deepcopy(settings["test"][key]))
                       for key, title, _s in page.SIDES]
        page.scan_entries = {"select": tk.Entry(page)}
        page.scan_entries["select"].insert(0, "LBL-1")

        def filled():
            for p in page.panels:
                p.res["max"].config(text="0.45")
                p.judge.config(text="OK")
                p.plot.set_data("PULL", [-11, 0, 11], [0.3, 0.4, 0.35])
        filled()
        page.ctl.part = {"id": "P2"}                                   # a new part already running
        page._prepare_next_part({"verdict": "OK"})
        assert page.panels[0].res["max"]["text"] == "0.45" and page.sampler.captures
        page.ctl.part = None
        page._prepare_next_part({"verdict": "OK"})
        for p in page.panels:
            assert p.res["max"]["text"] == tc.NO_DATA and p.plot.series["PULL"]["x"] == []
            assert p.judge["text"] != "OK"
        assert not page.sampler.captures and page.scan_entries["select"].get() == ""
        assert "ready for the next 0705CAJ00511N part" in page.status["text"]
    finally:
        root.destroy()
