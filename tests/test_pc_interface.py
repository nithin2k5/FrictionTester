"""PRINTING PROGRAM OLD profile (plc.pdf) against the ladder simulator.

Covers: AT and MT operation, both axes sharing a command word, requests held
until completion then released, stale completion/position bits, disconnects
during parameter transfer and movement, STOP followed by a late completion,
missing force samples, one axis failing while the other succeeds, and exactly
one stored result / production increment per part.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from friction_tester.plc import pc_profile as pp  # noqa: E402
from friction_tester.plc.axis_ops import OpBlocked, StrokeOp  # noqa: E402
from friction_tester.plc.event_log import EventLog  # noqa: E402
from friction_tester.plc.pc_flow import PcController, evaluate_axis  # noqa: E402
from friction_tester.plc.pc_link import PcLink, read_snapshot, validate_params  # noqa: E402
from pc_ladder_sim import PcLadderSim  # noqa: E402

PARAMS = [10, 50, 100, 20, 500, 500,      # SHIFT jog low/high, speed, PULL travel, limits
          10, 50, 100, 20, 500, 500,      # SELECT
          100, 100]                       # measurement start SHIFT, SELECT
COND = {"load_min": 1.0, "load_max": 10.0, "positions": [None, None, None]}
INFO = {"model": "M1", "part_number": "PN1", "conditions": {"select": dict(COND), "shift": dict(COND)}}


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class FakeSampler:
    """Returns configured samples per (axis, direction); records captures."""

    def __init__(self):
        self.data = {}
        self.captures = []

    def start(self, axis, kind, test_id, stroke_no):
        cap = (axis, kind, test_id, stroke_no)
        self.captures.append(cap)
        return cap

    def stop(self, cap):
        return list(self.data.get((cap[0], cap[1]), [(0.0, 5.0, None)] * 5))


class Rig:
    def __init__(self, internal=False, skip_scan=False, timeouts=None, owner=True, verified=True):
        self.clock = Clock()
        self.sim = PcLadderSim(self.clock)
        self.sim.mapping.verified = verified
        self.log = EventLog(folder=None, clock=lambda: __import__("datetime").datetime(2026, 11, 7))
        self.link = PcLink(self.sim, self.log, owner_confirmed=owner, internal_enabled=internal)
        self.sampler = FakeSampler()
        cfg = {"timeouts": dict({"stroke": 5.0, "home": 10.0}, **(timeouts or {})),
               "measurement": {"judge": "peak", "strokes_per_test": 1}}
        self.ctl = PcController(self.link, cfg, self.log, self.sampler, clock=self.clock, skip_scan=skip_scan)
        self.events = []
        self.trace = []                  # per poll: (req bits D04101, sim shift pull step)
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
        self.step()                       # request taken: pending / writing, or refused
        self.until(lambda: self.ctl.params["state"] in ("confirmed", "not_confirmed"))

    def parts(self):
        return [e[1] for e in self.events if e[0] == "part_done"]

    def messages(self):
        return [e[1] for e in self.events if e[0] == "message"]

    def run_part(self, mtype, labels=("LBL-1", "LBL-2")):
        self.ctl.submit("run", dict(INFO, type=mtype))
        self.step()
        if mtype == "MT":
            self.ctl.submit("scan", "select", labels[0])
            self.until(lambda: self.ctl.flow == "idle")
        else:
            self.ctl.submit("scan", "select", labels[0])
            self.until(lambda: self.ctl.flow in ("at_scan_shift", "idle"))
            if self.ctl.flow == "at_scan_shift":
                self.ctl.submit("scan", "shift", labels[1])
                self.until(lambda: self.ctl.flow == "idle")

    def all_requests_clear(self):
        return all(self.sim.D[4100 + i] == 0 for i in range(3))


# ------------------------------------------------------------------ parameters
def test_parameters_confirmed_by_readback_and_targets_follow_ladder_scaling():
    r = Rig()
    r.confirm_params()
    assert r.ctl.params["state"] == "confirmed"
    a = r.sim.axes["shift"]
    assert a.meas_target == 1_000_000 - 100 * 10                      # D02000 - p*10
    assert a.pull_target == a.meas_target + 20 * 100 + 100            # + travel*100 + D00490


def test_parameter_validation():
    bad = list(PARAMS)
    bad[12] = 40000                       # measurement start goes through 16-bit signed MUL
    bad[2] = None
    problems = validate_params(bad)
    assert any("Operating speed is empty" in p for p in problems)
    assert any("0..32767" in p for p in problems)


def test_disconnect_during_parameter_transfer_leaves_not_confirmed():
    r = Rig()
    r.sim.fail_next_write = True          # partial / failed write
    r.confirm_params()
    assert r.ctl.params["state"] == "not_confirmed"
    r.ctl.submit("params", "M1", list(PARAMS))
    r.step()                              # written, waiting for readback ...
    r.step(0.0)
    r.sim.connected = False               # ... and the link drops
    r.run(1.0)
    assert r.ctl.params["state"] == "not_confirmed"
    r.sim.connected = True
    r.run(1.0)
    assert r.ctl.params["state"] == "not_confirmed"       # never silently re-confirmed
    r.ctl.submit("run", dict(INFO, type="MT"))
    r.step()
    assert "Parameters not confirmed" in r.messages()[-1]


def test_writes_blocked_until_mapping_verified_and_owner_confirmed():
    for kw, text in (({"verified": False}, "not verified"), ({"owner": False}, "ownership")):
        r = Rig(**kw)
        r.confirm_params()
        assert r.ctl.params["state"] == "not_confirmed" and text in r.ctl.params["message"]
        assert not r.sim.writes


# ------------------------------------------------------------------ AT / MT
def test_at_part_two_scans_one_result():
    r = Rig()
    r.confirm_params()
    r.run_part("AT", ("SEL-1", "SHF-1"))
    parts = r.parts()
    assert len(parts) == 1 and parts[0]["verdict"] == "OK"
    assert [s["stage"] for s in parts[0]["stages"]] == ["select", "shift"]
    assert parts[0]["label_select"] == "SEL-1" and parts[0]["label_shift"] == "SHF-1"
    for axis in pp.AXES:
        assert r.sim.starts[axis] == {"home": 0, "pull": 1, "push": 1, "meas": 1}
    assert r.all_requests_clear()


def test_mt_both_axes_share_command_words_without_losing_bits():
    r = Rig()
    r.confirm_params()
    r.run_part("MT", ("MT-1",))
    assert len(r.parts()) == 1 and r.parts()[0]["verdict"] == "OK"
    both_pull = [w for _t, addr, vals in r.sim.writes if addr == "D04101" for w in vals if w & 0b1010 == 0b1010]
    assert both_pull, "SHIFT and SELECT PULL requests were never ON together in D04101"
    for axis in pp.AXES:
        assert r.sim.requests_seen[axis]["pull"] == 1 and r.sim.requests_seen[axis]["push"] == 1
    assert r.all_requests_clear()


def test_request_held_until_completion_then_released():
    r = Rig()
    r.confirm_params()
    r.run_part("AT")
    shift_pull = [(word & 0b10 != 0, step) for word, step, _sl in r.trace if step is not None]
    assert shift_pull
    for held, step in shift_pull:
        if step <= 6:
            assert held, f"PULL request released at step {step}, before completion"
    assert any(step == 7 for _h, step in shift_pull), "sequence never waited for the release (step 7)"
    assert any(not held for held, step in shift_pull if step >= 7)


def test_home_request_released_before_the_sequence_ends():
    r = Rig()
    r.confirm_params()
    r.ctl.submit("home", "shift")
    r.until(lambda: not r.ctl.manual)
    r.run(3.0)
    assert r.sim.starts["shift"]["home"] == 1           # a held request would re-home at the end
    assert "SHIFT HOME complete" in r.messages()[-1]
    assert r.all_requests_clear()


def test_internal_completion_bits_used_when_enabled():
    r = Rig(internal=True)
    r.confirm_params()
    r.run_part("AT")
    assert any("M00051" in line for line in r.log.tail(300))
    assert len(r.parts()) == 1


def test_skip_scan_at_needs_run_for_shift():
    r = Rig(skip_scan=True)
    r.confirm_params()
    r.ctl.submit("run", dict(INFO, type="AT"))
    r.until(lambda: r.ctl.flow == "at_scan_shift")
    r.run(2.0)
    assert r.ctl.flow == "at_scan_shift" and r.sim.starts["shift"]["pull"] == 0
    r.ctl.submit("run", dict(INFO, type="AT"))
    r.until(lambda: r.ctl.flow == "idle")
    assert len(r.parts()) == 1


# ------------------------------------------------------------------ stale signals
def test_stale_position_and_completion_bits_never_confirm_a_new_stroke():
    r = Rig(internal=True)
    r.confirm_params()
    a = r.sim.axes["shift"]
    a.pos = a.pull_target                 # axis already at the PULL target
    r.sim.advance(0.0)
    r.step()
    snap = read_snapshot(r.sim, internal_enabled=True)
    with pytest.raises(OpBlocked, match="already at the PULL target"):
        StrokeOp("shift", "pull").start(snap, r.clock())
    a.pos = a.meas_target
    a.complete["pull"] = True             # completion bit left ON
    r.step()
    snap = read_snapshot(r.sim, internal_enabled=True)
    with pytest.raises(OpBlocked, match="M00051"):
        StrokeOp("shift", "pull").start(snap, r.clock())


def test_stale_request_bits_at_attach_are_reported_not_resent():
    clock = Clock()
    r = Rig()
    r.sim.connected = False
    r.run(0.5)
    r.sim.D[4101] |= 1 << 1               # SHIFT PULL request left ON by an earlier session
    r.sim.connected = True
    r.step()
    assert r.ctl.stale == [("shift", "pull")]
    r.ctl.submit("run", dict(INFO, type="MT"))
    r.step()
    assert "press STOP" in r.messages()[-1]
    writes_before = len(r.sim.writes)
    r.ctl.submit("stop")
    r.step()
    assert r.sim.D[4101] & 0b10 == 0 and r.ctl.stale == []
    assert len(r.sim.writes) == writes_before + 1               # only the release was written
    r.run(3.0)                                                  # the PLC's PULL ends after the release
    r.confirm_params()
    r.run_part("MT")
    assert len(r.parts()) == 1
    del clock


# ------------------------------------------------------------------ disconnects, STOP, failures
def test_disconnect_during_movement_is_not_confirmed_or_resent():
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
    assert r.parts() == []
    assert any("INCOMPLETE" in m for m in r.messages())
    assert r.sim.requests_seen["select"]["pull"] == 1          # never re-sent
    assert r.all_requests_clear()                              # released after reconnecting


def test_stop_then_late_completion_is_ignored():
    r = Rig()
    r.confirm_params()
    r.ctl.submit("run", dict(INFO, type="AT"))
    r.step()
    r.ctl.submit("scan", "select", "S1")
    r.until(lambda: r.sim.axes["select"].seq["pull"].step == 3)
    r.ctl.submit("stop")
    r.step()
    assert r.sim.D[4101] & 0b1000 == 0                         # request released at once
    assert any("no PC stop command" in m for m in r.messages())
    r.run(5.0)                                                 # PLC finishes the stroke by itself
    assert r.sim.axes["select"].seq["pull"].word == 0
    assert r.parts() == [] and not [e for e in r.events if e[0] == "axis_result"]
    assert r.ctl.flow == "idle"


def test_missing_force_samples_make_the_part_ng():
    r = Rig()
    r.confirm_params()
    r.sampler.data[("shift", "push")] = []                     # no samples at all
    r.sampler.data[("select", "pull")] = [(0.0, None, None)] * 4   # reads failed
    r.run_part("MT")
    parts = r.parts()
    assert len(parts) == 1 and parts[0]["verdict"] == "NG"
    problems = {s["stage"]: s["result"]["problems"] for s in parts[0]["stages"]}
    assert any("no valid force samples" in p for p in problems["shift"])
    assert any("no valid force samples" in p for p in problems["select"])


def test_one_axis_fails_while_the_other_succeeds():
    r = Rig(timeouts={"stroke": 3.0})
    r.confirm_params()
    r.sim.stuck["select"] = False
    r.ctl.submit("run", dict(INFO, type="MT"))
    r.step()
    r.ctl.submit("scan", "select", "MT-1")
    r.until(lambda: r.sim.axes["select"].seq["pull"].word != 0)
    r.sim.stuck["select"] = True                               # SELECT never reaches its PULL target
    r.until(lambda: r.ctl.flow == "idle", limit=40)
    results = [e for e in r.events if e[0] == "axis_result"]
    assert [e[1] for e in results] == ["shift"]                # SHIFT finished on its own
    assert r.parts() == [] and any("INCOMPLETE" in m for m in r.messages())
    assert r.sim.D[4101] & 0b1000 == 0                         # SELECT PULL request released


def test_measurement_start_gives_up_after_plc_timeout():
    r = Rig()
    r.confirm_params(values=PARAMS[:12] + [200, 100])          # new SHIFT start position
    r.sim.stuck["shift"] = True
    r.ctl.submit("run", dict(INFO, type="AT", conditions=INFO["conditions"]))
    r.step()
    r.ctl.submit("scan", "select", "S")
    r.until(lambda: r.ctl.flow == "at_scan_shift")
    r.ctl.submit("scan", "shift", "H")
    r.until(lambda: r.ctl.flow == "idle", limit=30)
    assert any("measurement-start position not reached" in m for m in r.messages())
    assert r.parts() == []


def test_exactly_one_result_and_increment_per_part():
    r = Rig()
    r.confirm_params()
    r.run_part("AT", ("A1", "B1"))
    r.run_part("MT", ("C1",))
    r.run(3.0)
    parts = r.parts()
    assert len(parts) == 2 and len({p["part_id"] for p in parts}) == 2
    assert sum(1 for line in r.log.tail(300) if "PART" in line and "complete" in line) == 2


# ------------------------------------------------------------------ evaluation
def test_evaluation_rules():
    ok = evaluate_axis([{"pull": [(0, 2.0, None), (1, -6.0, None)], "push": [(0, 3.0, None)]}],
                       COND, {"judge": "peak"})
    assert ok["ok"] and ok["pull"] == [6.0] and ok["push"] == [3.0]
    out = evaluate_axis([{"pull": [(0, 12.0, None)], "push": [(0, 3.0, None)]}], COND, {"judge": "peak"})
    assert not out["ok"] and "outside" in out["problems"][0]
    pos = evaluate_axis([{"pull": [(0, 2.0, 0.0), (1, 4.0, 5.0), (2, 6.0, 10.0)], "push": [(0, 3.0, 5.0)]}],
                        dict(COND, positions=[0.0, 5.0, None]), {"judge": "positions"})
    assert pos["pull"] == [2.0, 4.0, None] and not pos["ok"]   # position 3 not set -> missing
    no_lim = evaluate_axis([{"pull": [(0, 2.0, None)], "push": [(0, 3.0, None)]}],
                           dict(COND, load_min=None), {"judge": "peak"})
    assert not no_lim["ok"]


def test_shared_command_word_keeps_the_other_axis_bits():
    r = Rig()
    r.link.set_bits({pp.COMMANDS["select"]["pull"]: True}, "t")
    r.link.set_bits({pp.COMMANDS["shift"]["pull"]: True}, "t")
    assert r.sim.D[4101] == 0b1010
    r.link.set_bits({pp.COMMANDS["select"]["pull"]: False}, "t")
    assert r.sim.D[4101] == 0b0010
