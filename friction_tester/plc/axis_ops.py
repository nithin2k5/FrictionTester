"""Request/completion handshakes for one axis operation (plc.pdf ladder).

Each operation is a small state machine with no I/O: start() checks the
preconditions and returns the request bit to set, observe() takes each new
snapshot and returns bit changes to apply (the release), until `result` is
"done" or "failed". The controller applies the changes through PcLink.

What the ladder does (SHIFT shown; SELECT is the same with its own words):

PULL / PUSH (pages 5-11), sequence word M0201 / M0202:
  step 0 is SET by request AND word = 0 AND M00120 (servo ON, no alarm) AND
  M00122 (home, PULL, PUSH words all 0); steps 1-4 copy the target and start
  the move; step 5 waits NOT busy AND CurPos = target (no timeout); step 6 sets
  M00051/M00052 (complete); step 7 WAITS FOR THE REQUEST TO BE RELEASED; step 9
  clears the word and M00051/52. While the word is non-zero, M08016/M08017
  (active) is ON and M0801A (permitted) is OFF.
  -> hold the request until completion, then release, then wait for "active" OFF.
  Completion evidence: M00051/52 if readable, else the target-position bit
  (CurPos = target) with positioning complete while the sequence is active AND
  the request held for the ladder's fixed step time (stroke_min_hold), since
  the axis may arrive during step 3. The target bit must be OFF before the
  request - a bit that was already ON is never taken as the end of a new stroke.

HOME (pages 3-5), word M0020: step 0 is SET by request AND word = 0 ... so a
  request still held when the sequence ends starts homing again. The sequence
  is visible only through M0801A (permitted) going OFF (M00122 includes M0020).
  -> request until permitted drops, release at once, wait for permitted ON with
  origin complete (P00012).

MEASUREMENT START (pages 14-16), word M0204: SET by request AND word = 0;
  step 5 advances on CurPos = D02018 OR after T0103 (5 s); step 7 waits for the
  request to be released. The word is not exported and is not part of M00122,
  so permitted does not drop.
  -> request; done when the measurement-start position bit is ON with
  positioning complete after a minimum dwell; release; settle.

A lost connection fails the operation as not confirmed; the request is
released when the link is back and is never re-sent automatically.
"""

from . import pc_profile as pp

DEFAULT_TIMEOUTS = {
    "echo": 1.5,          # request visible in D0400x
    "accept": 3.0,        # sequence started (activity / permitted OFF)
    "stroke": 30.0,       # PULL / PUSH reaches its target
    "home": 60.0,         # homing finished
    "meas_start": 8.0,    # PLC gives up after 5 s; position must be reached by then
    "release": 3.0,       # sequence ends after the release
    # PULL/PUSH without M00051..: the ladder reaches step 6 no sooner than
    # T0512 0.2 + T0513 1.0 + T0514 0.1 + T0515 0.1 = 1.4 s after step 0, and
    # the axis can reach its target during step 3. Hold the request at least
    # this long after acceptance so the release lands on step 7.
    "stroke_min_hold": 1.5,
    "meas_dwell": 1.0,    # minimum time the measurement-start request is held
    "meas_settle": 0.5,   # after releasing measurement start (sequence end not visible)
}


class OpBlocked(Exception):
    pass


class AxisOp:
    kind = ""

    def __init__(self, axis, test_id=None, timeouts=None, log=None):
        self.axis = axis
        self.test_id = test_id
        self.t = dict(DEFAULT_TIMEOUTS, **(timeouts or {}))
        self.log = log
        self.idx, self.n = pp.COMMANDS[axis][self.kind]
        self.state = "new"
        self.result = None            # "done" / "failed" / "cancelled"
        self.message = ""
        self.evidence = []
        self.needs_release = False    # request bit may still be ON in the PLC
        self.t0 = self.t_state = None

    # ---------------------------------------------------------------- helpers
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
        self._set(result, now)
        self._note("RESULT" if result == "done" else "FAIL", msg, evidence="; ".join(self.evidence) or "-")
        if release:
            self.needs_release = True
            return self._request(False)
        return {}

    def _common_ready(self, snap):
        st = lambda name: snap.st(self.axis, name)
        if st("axis_error"):
            raise OpBlocked(f"{pp.AXIS_NAME[self.axis]}: PLC axis error")
        if st("servo_on") is not True:
            raise OpBlocked(f"{pp.AXIS_NAME[self.axis]}: servo is not ON")
        if st("alarm_normal") is not True:
            raise OpBlocked(f"{pp.AXIS_NAME[self.axis]}: drive alarm (alarm-normal signal OFF)")
        if st("pull_active") or st("push_active"):
            raise OpBlocked(f"{pp.AXIS_NAME[self.axis]}: a PULL/PUSH sequence is still active")
        if st("permitted") is not True:
            raise OpBlocked(f"{pp.AXIS_NAME[self.axis]}: motion not permitted (home running or not ready)")

    def _elapsed(self, now):
        return now - self.t_state

    # ---------------------------------------------------------------- API
    def start(self, snap, now):
        if snap is None:
            raise OpBlocked("PLC not connected")
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
            return self._finish("failed", f"PLC connection lost during {self.name} - not confirmed, "
                                          "the request is released when the link is back and is not re-sent", now)
        return self.step(snap, now)

    def cancel(self, now, reason="stopped by the operator"):
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
        if snap.st(self.axis, self.target_bit):
            raise OpBlocked(f"{self.name}: the axis is already at the {self.kind.upper()} target - "
                            "a stale position bit cannot confirm a new stroke")
        if snap.complete_bit(self.axis, self.kind):
            raise OpBlocked(f"{self.name}: completion bit {pp.INTERNAL_COMPLETE[self.axis][self.kind]} "
                            "is already ON")

    def step(self, snap, now):
        st = lambda name: snap.st(self.axis, name)
        if self.state == "requested":
            if snap.echo_bit(self.idx, self.n):
                if "echo" not in self.evidence:
                    self.evidence.append("echo")
                    self._note("ECHO", "request echoed")
                if st(self.active_bit):
                    self.evidence.append("sequence active")
                    self._set("accepted", now)
                    return {}
            elif self._elapsed(now) > self.t["echo"]:
                return self._finish("failed", f"{self.name}: request not echoed by the PLC", now, release=True)
            if self._elapsed(now) > self.t["accept"]:
                return self._finish("failed", f"{self.name}: PLC did not start the sequence "
                                              "(not permitted, or the request was not accepted)", now, release=True)
            return {}
        if self.state == "accepted":
            if not st(self.active_bit):
                return self._finish("failed", f"{self.name}: sequence ended before completion", now, release=True)
            done_bit = snap.complete_bit(self.axis, self.kind)
            if done_bit is not None:
                complete = done_bit
                basis = pp.INTERNAL_COMPLETE[self.axis][self.kind]
            else:
                held = self._elapsed(now) >= self.t["stroke_min_hold"]
                complete = bool(st(self.target_bit)) and bool(st("pos_complete")) and held
                basis = (f"target position (fresh) + positioning complete, request held "
                         f"{self.t['stroke_min_hold']:g} s (ladder step timing)")
            if complete:
                self.evidence.append(basis)
                self._set("releasing", now)
                self._note("COMPLETE", basis)
                return self._request(False)
            if self._elapsed(now) > self.t["stroke"]:
                return self._finish("failed", f"{self.name}: target not reached within {self.t['stroke']:g} s "
                                              "- the PLC sequence has no timeout and may still be waiting; "
                                              "check the machine", now, release=True)
            return {}
        if self.state == "releasing":
            if not st(self.active_bit) and snap.echo_bit(self.idx, self.n) is False:
                self.needs_release = False
                self.evidence.append("sequence released")
                return self._finish("done", f"{self.name} complete", now)
            if self._elapsed(now) > self.t["release"]:
                return self._finish("failed", f"{self.name}: sequence did not release after the request was "
                                              "cleared", now)
        return {}


class HomeOp(AxisOp):
    kind = "home"

    def precheck(self, snap):
        self._common_ready(snap)

    def step(self, snap, now):
        st = lambda name: snap.st(self.axis, name)
        if self.state == "requested":
            if snap.echo_bit(self.idx, self.n) and "echo" not in self.evidence:
                self.evidence.append("echo")
                self._note("ECHO", "request echoed")
            if st("permitted") is False:
                if st("servo_on") is not True or st("alarm_normal") is not True:
                    return self._finish("failed", f"{self.name}: servo/alarm changed instead of homing",
                                        now, release=True)
                self.evidence.append("sequence started (permitted OFF)")
                self._set("running", now)
                return self._request(False)     # release now, or the ladder re-homes at the end
            if self._elapsed(now) > self.t["accept"]:
                return self._finish("failed", f"{self.name}: PLC did not start homing", now, release=True)
            return {}
        if self.state == "running":
            if st("permitted") and st("origin_complete") and snap.echo_bit(self.idx, self.n) is False:
                self.needs_release = False
                self.evidence.append("permitted ON + origin complete")
                return self._finish("done", f"{self.name} complete", now)
            if self._elapsed(now) > self.t["home"]:
                return self._finish("failed", f"{self.name}: not finished within {self.t['home']:g} s "
                                              "(the PLC sequence has no timeout)", now)
        return {}


class MeasStartOp(AxisOp):
    kind = "meas_start"

    def precheck(self, snap):
        self._common_ready(snap)

    def step(self, snap, now):
        st = lambda name: snap.st(self.axis, name)
        if st("pull_active") or st("push_active"):
            return self._finish("failed", f"{self.name}: a PULL/PUSH sequence started during positioning",
                                now, release=True)
        if self.state == "requested":
            if snap.echo_bit(self.idx, self.n):
                self.evidence.append("echo")
                self._note("ECHO", "request echoed")
                self._set("moving", now)
            elif self._elapsed(now) > self.t["echo"]:
                return self._finish("failed", f"{self.name}: request not echoed by the PLC", now, release=True)
            return {}
        if self.state == "moving":
            if st("at_meas_start") and st("pos_complete") and self._elapsed(now) >= self.t["meas_dwell"]:
                was = self.baseline.st(self.axis, "at_meas_start")
                self.evidence.append("measurement-start position + positioning complete after "
                                     f"{self.t['meas_dwell']:g} s" + (" (axis was already there)" if was else ""))
                self._set("releasing", now)
                return self._request(False)
            if self._elapsed(now) > self.t["meas_start"]:
                return self._finish("failed", f"{self.name}: measurement-start position not reached (the PLC "
                                              "sequence gives up after 5 s)", now, release=True)
            return {}
        if self.state == "releasing":
            if self._elapsed(now) >= self.t["meas_settle"] and snap.echo_bit(self.idx, self.n) is False:
                self.needs_release = False
                if not st("at_meas_start"):
                    return self._finish("failed", f"{self.name}: left the measurement-start position", now)
                return self._finish("done", f"{self.name} complete", now)
            if self._elapsed(now) > self.t["release"]:
                return self._finish("failed", f"{self.name}: release not echoed", now)
        return {}


def make_op(axis, kind, **kw):
    if kind in ("pull", "push"):
        return StrokeOp(axis, kind, **kw)
    if kind == "home":
        return HomeOp(axis, **kw)
    if kind == "meas_start":
        return MeasStartOp(axis, **kw)
    raise ValueError(kind)
