"""Request/acceptance/completion/release handshakes for one axis operation,
traced from plc.pdf (PRINTING PROGRAM OLD). SHIFT words shown; SELECT uses
M0210..M0214 and its own bits.

Each operation is a state machine with no I/O: start() checks preconditions
and returns the request bit to set; observe() takes each new snapshot and
returns bit changes (the release) until `result` is set. A command echo only
proves the PLC copied the word - acceptance and completion are checked
separately.

PULL / PUSH (pages 5-11), sequence word M0201 / M0202, active M08016 / M08017:
  step 0 SET by request AND word = 0 AND M00120 AND M00122; step 1 resets
  M00051/52; steps 2-4 load the target and start the move; step 5 waits
  NOT busy AND CurPos = target (no PLC timeout); step 6 SETS M00051/52;
  step 7 waits for the request to be RELEASED; step 9 clears the word and
  M00051/52. "Active" stays ON through the release wait, so the request is
  cleared on completion - not after active drops.
  Completion = M00051/52/61/62 from this execution (native M read, verified).
  There is no other completion source: an unreadable flag fails the stroke.
  The measurement window ends at completion (`measure_end`), not at the release.
  Before the next stroke: active OFF, completion flag OFF, permission back.

HOME (pages 3-5), word M0200: step 0 SET by request AND word = 0 ..., so a held
  request re-homes after the reset. Acceptance = motion permission (M0801A/B,
  which needs M0200 = 0) dropping; the request is released at once. Done only
  when permission is restored (sequence word back to idle), origin feedback
  (P00012/P00016) is ON and no axis error remains - origin feedback alone
  arrives at step 3, before the sequence ends.

MEASUREMENT START (pages 14-16), word M0204 (not exported, not in M00122):
  step 5 advances on CurPos = D02018 OR T0103; step 7 waits for release. The
  step bits must be readable (native M reads): acceptance = chain active; at
  step 7 the target compare (M0802E) and in-position must be ON - otherwise
  the timeout branch was taken and positioning FAILED. Unreadable step bits
  fail the operation. M08023 and M0802E are the same compare and are never
  counted as two pieces of evidence.

ALARM RESET (pages 27-28): M0700A -> P00033 / M0700E -> P00037 only while the
  selector M00105 / M00205 is OFF. One pulse of the configured length per
  operator request; if the alarm-normal input does not return, it is
  reported and not retried.

A lost connection fails the operation as not confirmed; the request is
released when the link is back and is never re-sent.
"""

from . import pc_profile as pp

DEFAULT_TIMEOUTS = {
    "freshness": 1.0,     # snapshot age allowed when starting an operation
    "echo": 1.5,          # request visible in D0400x
    "accept": 3.0,        # sequence started
    "stroke": 30.0,       # PULL / PUSH completion
    "home": 60.0,
    "meas_start": 8.0,
    "release": 3.0,       # sequence reset after the release
    "alarm_reset_pulse": None,  # s - from the drive requirements; None = alarm reset disabled
    "alarm_reset_check": 3.0,   # alarm-normal must return within this time
}


class OpBlocked(Exception):
    pass


class AxisOp:
    kind = ""

    def __init__(self, axis, test_id=None, timeouts=None, log=None, completion="internal_flags"):
        self.axis = axis
        self.test_id = test_id
        self.t = dict(DEFAULT_TIMEOUTS, **{k: v for k, v in (timeouts or {}).items() if k in DEFAULT_TIMEOUTS})
        self.log = log
        self.completion = completion
        self.idx, self.n = pp.COMMANDS[axis][self.kind]
        self.state = "new"
        self.result = None            # "done" / "failed" / "cancelled"
        self.message = ""
        self.evidence = []
        self.needs_release = False
        self.measure_end = False      # the measuring part of the operation is over
        self.t0 = self.t_state = None
        self.baseline = None

    @property
    def name(self):
        return f"{pp.AXIS_NAME[self.axis]} {self.kind.replace('_', '-').upper()}"

    def _note(self, kind, msg="", **f):
        if self.log:
            self.log.log(kind, f"{self.name}: {msg}", test=self.test_id, **f)

    def _set(self, state, now):
        self.state, self.t_state = state, now
        self._note("STATE", state)

    def _request(self, on):
        return {(self.idx, self.n): on}

    def _finish(self, result, msg, now, release=False):
        self.result, self.message = result, msg
        self.measure_end = True
        self._set(result, now)
        self._note("RESULT" if result == "done" else "FAIL", msg, evidence="; ".join(self.evidence) or "-")
        if release:
            self.needs_release = True
            return self._request(False)
        return {}

    def _elapsed(self, now):
        return now - self.t_state

    def _common_ready(self, snap, need_permit=True):
        st = lambda name: snap.st(self.axis, name)
        a = pp.AXIS_NAME[self.axis]
        if st("axis_error"):
            raise OpBlocked(f"{a}: PLC positioning error")
        if st("servo_on") is not True:
            raise OpBlocked(f"{a}: servo ON feedback is OFF")
        if st("alarm_normal") is not True:
            raise OpBlocked(f"{a}: drive alarm-normal input is OFF")
        if st("pull_active") or st("push_active"):
            raise OpBlocked(f"{a}: a PULL/PUSH sequence is still active")
        if need_permit and st("permitted") is not True:
            raise OpBlocked(f"{a}: motion permission is OFF")
        step = snap.meas_step(self.axis)
        if step is not None and step != -1:
            raise OpBlocked(f"{a}: the measurement-start sequence is still at step {step}")

    def start(self, snap, now):
        if snap is None:
            raise OpBlocked("PLC not connected")
        if now - snap.time > self.t["freshness"]:
            raise OpBlocked(f"PLC data is {now - snap.time:.1f} s old")
        self.precheck(snap)
        self.baseline = snap
        self.t0 = now
        self.needs_release = True
        self._set("requested", now)
        self._note("REQUEST", pp.command_name(self.axis, self.kind))
        return self._request(True)

    def observe(self, snap, now):
        if self.result:
            return {}
        if snap is None:
            return self._finish("failed", f"PLC connection lost during {self.name} - not confirmed; the request "
                                          "is released when the link is back and is not re-sent", now)
        return self.step(snap, now)

    def cancel(self, now, reason="cancelled by STOP"):
        if self.result:
            return {}
        return self._finish("cancelled", reason, now, release=True)

    def precheck(self, snap):
        raise NotImplementedError

    def step(self, snap, now):
        raise NotImplementedError


class StrokeOp(AxisOp):
    """PULL or PUSH."""

    def __init__(self, axis, kind, **kw):
        self.kind = kind
        super().__init__(axis, **kw)
        self.target_bit = "at_pull" if kind == "pull" else "at_push"
        self.active_bit = f"{kind}_active"

    def precheck(self, snap):
        self._common_ready(snap)
        flag = snap.complete_bit(self.axis, self.kind)
        addr = pp.INTERNAL_COMPLETE[self.axis][self.kind]
        if self.completion == "internal_flags" and flag is None:
            raise OpBlocked(f"{self.name}: completion flag {addr} is not readable (native M reads not enabled/"
                            "verified)")
        if flag:
            raise OpBlocked(f"{self.name}: stale completion flag {addr} is ON before the request")
        if snap.st(self.axis, self.target_bit):
            raise OpBlocked(f"{self.name}: the axis is already at the {self.kind.upper()} target - a stale "
                            "position compare cannot confirm a new stroke")

    def step(self, snap, now):
        st = lambda name: snap.st(self.axis, name)
        if self.state == "requested":
            if snap.echo_bit(self.idx, self.n) and "echo" not in self.evidence:
                self.evidence.append("echo")
                self._note("ECHO", "request copied (echo only - not acceptance)")
            if snap.echo_bit(self.idx, self.n) and st(self.active_bit) and st("permitted") is False:
                self.evidence.append("fresh sequence (active ON, permission OFF)")
                self._set("accepted", now)
                return {}
            if not snap.echo_bit(self.idx, self.n) and self._elapsed(now) > self.t["echo"]:
                return self._finish("failed", f"{self.name}: request not echoed by the PLC", now, release=True)
            if self._elapsed(now) > self.t["accept"]:
                return self._finish("failed", f"{self.name}: echoed but no fresh sequence started (not accepted)",
                                    now, release=True)
            return {}
        if self.state == "accepted":
            if st("axis_error") or st("alarm_normal") is False or st("servo_on") is False:
                return self._finish("failed", f"{self.name}: drive/axis fault during the stroke", now, release=True)
            if not st(self.active_bit):
                return self._finish("failed", f"{self.name}: sequence ended before completion", now, release=True)
            flag = snap.complete_bit(self.axis, self.kind)
            if flag is None:
                return self._finish("failed", f"{self.name}: completion flag became unreadable", now,
                                    release=True)
            complete, basis = flag, pp.INTERNAL_COMPLETE[self.axis][self.kind]
            if complete:
                self.evidence.append(basis)
                self.measure_end = True
                self._set("releasing", now)
                self._note("COMPLETE", basis)
                return self._request(False)
            if self._elapsed(now) > self.t["stroke"]:
                return self._finish("failed", f"{self.name}: no completion within {self.t['stroke']:g} s - the PLC "
                                              "sequence has no timeout and may still be waiting; check the machine",
                                    now, release=True)
            return {}
        if self.state == "releasing":
            flag = snap.complete_bit(self.axis, self.kind)
            if (not st(self.active_bit) and snap.echo_bit(self.idx, self.n) is False and not flag
                    and st("permitted")):
                self.needs_release = False
                self.evidence.append("sequence reset, flag cleared, permission back")
                return self._finish("done", f"{self.name} complete", now)
            if self._elapsed(now) > self.t["release"]:
                return self._finish("failed", f"{self.name}: sequence did not reset after the release", now)
        return {}


class HomeOp(AxisOp):
    """The home rung needs only M00120 + no error/busy, not M00122 - and M00122
    includes origin feedback, so an axis that was never homed (power-up) has
    permission OFF. Two acceptance paths:
      homed start   (permission ON):  permission drops -> release at once.
      un-homed start (permission OFF, origin OFF): no drop to see; hold the
        request until origin feedback turns ON (step 3, sequence already past
        step 0, so the release cannot cancel it) -> release.
    Completion is the same for both."""
    kind = "home"
    unhomed = False

    def precheck(self, snap):
        self._common_ready(snap, need_permit=False)
        a = pp.AXIS_NAME[self.axis]
        permitted, origin = snap.st(self.axis, "permitted"), snap.st(self.axis, "origin_complete")
        if permitted is None or origin is None:
            raise OpBlocked(f"{a}: motion permission / origin feedback not readable")
        if not permitted and origin:
            raise OpBlocked(f"{a}: motion permission is OFF although origin feedback is ON - a sequence or the "
                            "positioning module is still busy; wait or clear it at the machine")
        self.unhomed = not permitted

    def step(self, snap, now):
        st = lambda name: snap.st(self.axis, name)
        if self.state == "requested" and self.unhomed:
            if snap.echo_bit(self.idx, self.n) and "echo" not in self.evidence:
                self.evidence.append("echo")
                self._note("ECHO", "request copied (echo only - not acceptance)")
            if st("servo_on") is not True or st("alarm_normal") is not True or st("axis_error"):
                return self._finish("failed", f"{self.name}: drive state changed instead of homing",
                                    now, release=True)
            if snap.echo_bit(self.idx, self.n) and st("origin_complete"):
                self.evidence.append("un-homed start: origin feedback OFF -> ON (sequence step 3)")
                self._set("running", now)
                return self._request(False)
            if not snap.echo_bit(self.idx, self.n) and self._elapsed(now) > self.t["echo"]:
                return self._finish("failed", f"{self.name}: request not echoed by the PLC", now, release=True)
            if self._elapsed(now) > self.t["home"]:
                return self._finish("failed", f"{self.name}: origin feedback did not come ON within "
                                              f"{self.t['home']:g} s - PLC did not home", now, release=True)
            return {}
        if self.state == "requested":
            if snap.echo_bit(self.idx, self.n) and "echo" not in self.evidence:
                self.evidence.append("echo")
                self._note("ECHO", "request copied (echo only - not acceptance)")
            if snap.echo_bit(self.idx, self.n) and st("permitted") is False:
                if st("servo_on") is not True or st("alarm_normal") is not True or st("axis_error"):
                    return self._finish("failed", f"{self.name}: drive state changed instead of homing",
                                        now, release=True)
                self.evidence.append("sequence started (permission OFF)")
                self._set("running", now)
                return self._request(False)       # release now: a held request re-homes after the reset
            if self._elapsed(now) > self.t["accept"]:
                return self._finish("failed", f"{self.name}: PLC did not start homing", now, release=True)
            return {}
        if self.state == "running":
            if (st("permitted") and st("origin_complete") and not st("axis_error")
                    and snap.echo_bit(self.idx, self.n) is False):
                self.needs_release = False
                self.evidence.append("permission restored (M0200 idle) + origin feedback + no error")
                return self._finish("done", f"{self.name} complete", now)
            if self._elapsed(now) > self.t["home"]:
                return self._finish("failed", f"{self.name}: not finished within {self.t['home']:g} s "
                                              "(the PLC sequence has no timeout)", now)
        return {}


class MeasStartOp(AxisOp):
    kind = "meas_start"

    def precheck(self, snap):
        self._common_ready(snap)
        if snap.meas_step(self.axis) is None:
            raise OpBlocked(f"{self.name}: sequence {pp.SEQ_WORDS[self.axis]['meas_start']} is not readable "
                            "(native M reads not enabled/verified)")

    def step(self, snap, now):
        st = lambda name: snap.st(self.axis, name)
        if st("pull_active") or st("push_active"):
            return self._finish("failed", f"{self.name}: a PULL/PUSH sequence started during positioning",
                                now, release=True)
        if st("axis_error"):
            return self._finish("failed", f"{self.name}: PLC positioning error", now, release=True)
        step = snap.meas_step(self.axis)
        if step is None:
            return self._finish("failed", f"{self.name}: sequence {pp.SEQ_WORDS[self.axis]['meas_start']} "
                                          "became unreadable", now, release=True)
        return self._step_observed(snap, now, step)

    def _step_observed(self, snap, now, step):
        st = lambda name: snap.st(self.axis, name)
        if self.state == "requested":
            if snap.echo_bit(self.idx, self.n) and step >= 0:
                self.evidence.append(f"sequence {pp.SEQ_WORDS[self.axis]['meas_start']} active (step {step})")
                self._set("moving", now)
            elif self._elapsed(now) > self.t["accept"]:
                return self._finish("failed", f"{self.name}: no fresh sequence started", now, release=True)
            return {}
        if self.state == "moving":
            if step == pp.MEAS_RELEASE_STEP:
                if st("at_meas_start") and st("pos_complete"):
                    self.evidence.append("step 7 with target compare + in-position")
                    self._set("releasing", now)
                    return self._request(False)
                return self._finish("failed", f"{self.name}: the sequence advanced by its timeout branch without "
                                              "reaching the target", now, release=True)
            if step == -1:
                return self._finish("failed", f"{self.name}: sequence reset before the release step", now,
                                    release=True)
            if self._elapsed(now) > self.t["meas_start"]:
                return self._finish("failed", f"{self.name}: positioning not finished within "
                                              f"{self.t['meas_start']:g} s", now, release=True)
            return {}
        if self.state == "releasing":
            if step == -1 and snap.echo_bit(self.idx, self.n) is False:
                self.needs_release = False
                if not st("at_meas_start"):
                    return self._finish("failed", f"{self.name}: left the target after the release", now)
                self.evidence.append("sequence reset")
                return self._finish("done", f"{self.name} complete", now)
            if self._elapsed(now) > self.t["release"]:
                return self._finish("failed", f"{self.name}: sequence did not reset after the release", now)
        return {}


class AlarmResetOp(AxisOp):
    kind = "alarm_reset"

    def precheck(self, snap):
        if self.t["alarm_reset_pulse"] in (None, 0):
            raise OpBlocked(f"{self.name}: pulse length not configured (machine_profile.timeouts."
                            "alarm_reset_pulse, from the drive requirements)")
        if snap.st(self.axis, "alarm_normal") is True:
            raise OpBlocked(f"{self.name}: alarm-normal input is already ON - nothing to reset")
        sel = snap.selector(self.axis)
        if sel:
            raise OpBlocked(f"{self.name}: local/touch operation selected ({pp.SELECTOR[self.axis]} ON) - the PC "
                            "reset branch is inactive")
        if sel is None:
            self.evidence.append(f"selector {pp.SELECTOR[self.axis]} not readable - PLC arbitration decides")

    def step(self, snap, now):
        if self.state == "requested":
            if snap.echo_bit(self.idx, self.n) and "echo" not in self.evidence:
                self.evidence.append("echo")
            if self._elapsed(now) >= float(self.t["alarm_reset_pulse"]):
                if "echo" not in self.evidence:
                    return self._finish("failed", f"{self.name}: request not echoed", now, release=True)
                self._set("checking", now)
                return self._request(False)
            return {}
        if self.state == "checking":
            if snap.echo_bit(self.idx, self.n) is False and snap.st(self.axis, "alarm_normal"):
                self.needs_release = False
                return self._finish("done", f"{self.name}: alarm-normal input restored", now)
            if self._elapsed(now) > float(self.t["alarm_reset_check"]):
                self.needs_release = snap.echo_bit(self.idx, self.n) is not False
                return self._finish("failed", f"{self.name}: alarm persists after one reset pulse - not retried; "
                                              "check the drive", now)
        return {}


def make_op(axis, kind, **kw):
    if kind in ("pull", "push"):
        return StrokeOp(axis, kind, **kw)
    return {"home": HomeOp, "meas_start": MeasStartOp, "alarm_reset": AlarmResetOp}[kind](axis, **kw)
