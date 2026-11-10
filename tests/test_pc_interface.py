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
    assert pp.COMMANDS["shift"]["alarm_reset"] == (0, 10) and pp.m_address(700, 0, 10) == "M0700A"
    assert pp.COMMANDS["select"]["alarm_reset"] == (0, 14) and pp.m_address(700, 0, 14) == "M0700E"
    assert pp.SELECTOR == {"shift": "M00105", "select": "M00205"}
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
    assert "local/touch operation selected (M00105 ON)" in r.messages()[-1]
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
        assert r.sim.starts[axis] == {"home": 0, "pull": 1, "push": 1, "meas": 1}
    assert r.requests_clear()


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
    seen = [(word & 0b10 != 0, step) for word, step, _sl in r.trace if step is not None]
    for held, step in seen:
        if step <= 5:                                   # M00051 is set at step 6
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
    f.run_part("AT")                                    # axis now already on the start compare
    assert any("no positioning requested" in e for e in f.parts()[-1]["evidence"]["select"])


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
    with pytest.raises(OpBlocked, match="stale completion flag M00051"):
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


def test_home_on_open_off_by_default_and_blocked_without_writes():
    assert not Rig().ctl.manual
    r = Rig(cfg={"home_on_open": True}, owner=False)
    assert not r.ctl.manual and r.sim.starts["shift"]["home"] == 0
    assert any("Automatic home on open skipped" in m for m in r.messages())


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
    r.sim.D[4101] |= 1 << 1
    r.sim.connected = True
    r.step()
    assert r.ctl.stale == [("shift", "pull")]
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
    res = evaluate_axis(pos, dict(COND, positions=[5.0, 15.0, None]), dict(RULES, judge="positions"))
    assert res["pull"] == [3.0, 5.0] and res["verdict"] == "OK"
    out = evaluate_axis(pos, dict(COND, positions=[25.0]), dict(RULES, judge="positions"))
    assert out["verdict"] == "INCOMPLETE" and "not covered" in out["problems"][0]


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
    assert pp.command_name("shift", "jog_left_high").endswith("M07014")
    assert pp.command_name("shift", "jog_right_high").endswith("M07017")
    assert pp.command_name("select", "jog_left_high").endswith("M07018")
    assert pp.command_name("select", "jog_right_high").endswith("M0701B")


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
    # jog low = speed / 2, jog high = speed, speed, PULL = stroke, left = P3, right = -P1;
    # start centred: Stroke / 2 x 10 (stroke 5 -> start 2.5 mm on the - side)
    assert values == [5, 10, 10, 5, 30, 30] * 2 + [25, 25]
    t = derived_targets(values, False)["shift"]
    assert t["start"] == 1_000_000 - 250 and t["pull"] == t["start"] + 500 + 100
    assert not validate_params(values) and not target_problems(derived_targets(values, False))


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
    """NIDaq stand-in: connect() opens, read_average() returns fixed volts."""

    def __init__(self, volts=None, fail=None):
        self.volts, self.fail, self.cfg, self.reads = volts or {}, fail, None, 0

    @property
    def is_open(self):
        return self.cfg is not None

    def connect(self, cfg):
        if self.fail:
            raise RuntimeError(self.fail)
        self.cfg = cfg

    def read_average(self):
        self.reads += 1
        return dict(self.volts)


def _sampler_settings(force_source="serial", stroke=None):
    return {"daq": {"device": "Dev1", "terminal_config": "Differential", "sample_rate": 1000.0,
                    "samples_per_channel": 100,
                    "sensors": [{"name": "Loadcell 1", "channel": "ai0", "range": "±10 V", "scale": 2.0,
                                 "offset_v": 0.0, "unit": "kgf"},
                                {"name": "LVDT 1", "channel": "ai2", "range": "±10 V", "scale": 5.0,
                                 "offset_v": 1.0, "unit": "mm"}]},
            "test": {"machine_profile": {"measurement": {
                "force_source": force_source, "loadcell": {"select": 1, "shift": 2},
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
    assert daq.cfg["samples_per_channel"] == 10                    # ~10 ms block


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


def test_graph_draws_stroke_or_time_and_restarts_per_stroke():
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
        pull.samples += [(10.0, 0.1, -5.0, 0, 0), (10.1, None, -4.0, 0, 0), (10.2, 0.3, -3.0, 0, 0)]
        panel.draw_capture(pull)
        assert panel.plot.series["PULL"]["x"] == [-5.0, -3.0] and panel.plot.xlabel == "Stroke [mm]"
        pull.samples.append((10.3, 0.4, -2.0, 0, 0))
        panel.draw_capture(pull)                                     # only the new sample is added
        assert panel.plot.series["PULL"]["y"] == [0.1, 0.3, 0.4]
        push = Capture("select", "push", "t", 1)
        push.samples += [(11.0, -0.2, 6.0, 0, 0), (11.5, -0.1, None, 0, 0)]
        panel.draw_capture(push)                                     # same stroke: PULL stays
        assert panel.plot.series["PULL"]["y"] and panel.plot.series["PUSH"]["x"] == [6.0]
        pull2 = Capture("select", "pull", "t", 2)                    # next stroke: fresh graph
        pull2.samples += [(20.0, 0.2, None, 0, 0), (20.5, 0.3, None, 0, 0)]
        panel.draw_capture(pull2)
        assert not panel.plot.series["PUSH"]["x"] and panel.plot.series["PULL"]["x"] == [0.0, 0.5]
        assert panel.plot.xlabel == "Time [s]"                       # no LVDT reading: Load vs time
    finally:
        root.destroy()
