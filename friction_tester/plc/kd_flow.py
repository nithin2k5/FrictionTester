"""Controller for the plc kd.pdf profile: ORIGIN (homing) and position moves.
(The automatic test cycle - plc Auto.pdf, M00029 / D05010 - runs in the PLC and
is followed by plc.shared_cycle / plc.part_flow; this controller only homes.)

Runs on its own thread; the UI only submits commands and drains events
("message", "fault", "snapshot"), so no Tk call happens here.

HOME (M00050, state word D05000) - see kd_profile:
  start     axis idle, no error, D05000 = 0, M00050 read back OFF, writes allowed
  request   set M00050 (FC05)
  accepted  D05000 leaves 0 (fresh evidence - an echo of M00050 alone is not acceptance)
  done      D05000 back to 0 AND the PLC has cleared M00050 AND homing status D00500.4 ON,
            no error. The app never clears M00050 after acceptance (the PLC does).
  not accepted in time -> the app clears M00050 itself while D05000 is still 0,
            so a late start cannot happen.
MOVE (M00030-M00034, DST):
  start     homed, idle, no error, request bit OFF
  accepted  axis busy (D00500.0) -> the app releases the request bit
  done      busy OFF, request OFF, positioning complete D00500.2, no error

A failed operation latches a fault: new operations are refused until STOP
acknowledges it with the PLC connected and the axis idle. STOP cancels what
the app requested but cannot stop the machine: this program has no PC stop.
A lost connection fails the operation as not confirmed; nothing is re-sent.
"""

import queue
import threading
import time

from . import kd_profile as kp
from .kd_link import KdWriteBlocked
from .pc_flow import LINK_STATES, classify_link_error

DEFAULT_TIMEOUTS = {"freshness": 1.0, "accept": 3.0, "home": 60.0, "move": 30.0}


class KdOp:
    kind = ""

    def __init__(self, request_bit, label, timeouts):
        self.bit = request_bit
        self.label = label
        self.t = timeouts
        self.state = "new"
        self.result = None           # "done" / "failed" / "cancelled"
        self.message = ""
        self.t_state = None
        self.evidence = []

    @property
    def name(self):
        return f"X {self.label}"

    def _set(self, state, now):
        self.state, self.t_state = state, now

    def _elapsed(self, now):
        return now - self.t_state

    def _finish(self, result, msg, now):
        self.result, self.message = result, msg
        self._set(result, now)
        return []

    def precheck(self, snap):
        if snap.st("error"):
            raise KdWriteBlocked(f"X axis error (code {snap.error_code}) - clear it at the machine")
        if snap.st("busy"):
            raise KdWriteBlocked("X axis is busy")
        if not snap.bits:
            raise KdWriteBlocked(f"command bits not readable ({snap.bits_error or 'no data'}) - the "
                                 "bit-write area must be based at M")
        if snap.cmd(self.bit):
            raise KdWriteBlocked(f"{self.bit} is already ON in the PLC - not re-sent")

    def start(self, snap, now):
        self.precheck(snap)
        self._set("requested", now)
        return [(self.bit, True)]


class HomeOp(KdOp):
    kind = "home"

    def __init__(self, timeouts):
        super().__init__(kp.HOME_REQUEST, "ORIGIN", timeouts)

    def precheck(self, snap):
        if snap.home_state is None:
            raise KdWriteBlocked(f"homing state D05000 not readable - {snap.home_error}")
        super().precheck(snap)
        if snap.home_state != 0:
            raise KdWriteBlocked(f"homing sequence already active (D05000 = {snap.home_state})")

    def step(self, snap, now):
        """Returns bit writes [(address, value)]."""
        if self.state == "requested":
            if snap.home_state not in (0, None):
                self.evidence.append(f"D05000 = {snap.home_state}")
                self._set("homing", now)
                return []
            if self._elapsed(now) > self.t["accept"]:
                self._finish("failed", f"{self.name}: PLC did not start homing (D05000 stayed 0)", now)
                return [(self.bit, False)] if snap.cmd(self.bit) is not False else []
            return []
        if self.state == "homing":
            if snap.st("error"):
                return self._finish("failed", f"{self.name}: axis error during homing (code {snap.error_code})",
                                    now)
            if snap.home_state == 0 and snap.cmd(self.bit) is False:
                if snap.st("homed"):
                    self.evidence.append("D05000 = 0, M00050 cleared by the PLC, D00500.4 ON")
                    return self._finish("done", f"{self.name} complete", now)
                return self._finish("failed", f"{self.name}: sequence ended without homing status D00500.4", now)
            if self._elapsed(now) > self.t["home"]:
                return self._finish("failed", f"{self.name}: not finished within {self.t['home']:g} s (the PLC "
                                              "sequence has no timeout) - check the machine", now)
        return []

    def cancel(self, snap, now):
        if self.result:
            return []
        if self.state == "requested":
            self._finish("cancelled", f"{self.name} cancelled before the PLC started it", now)
            return [(self.bit, False)]
        self._finish("cancelled", f"{self.name}: homing continues in the PLC - this program has no PC stop "
                                  "command; use the machine's emergency stop if needed", now)
        return []


class MoveOp(KdOp):
    kind = "move"

    def __init__(self, move, timeouts):
        bit, _reg, label = kp.MOVES[move]
        super().__init__(bit, label, timeouts)
        self.move = move

    def precheck(self, snap):
        super().precheck(snap)
        if not snap.st("homed"):
            raise KdWriteBlocked("X axis is not homed - press ORIGIN first")

    def step(self, snap, now):
        if self.state == "requested":
            if snap.st("busy"):
                self.evidence.append("axis busy")
                self._set("moving", now)
                return [(self.bit, False)]          # release once the PLC has started the move
            if self._elapsed(now) > self.t["accept"]:
                self._finish("failed", f"{self.name}: the axis did not start", now)
                return [(self.bit, False)]
            return []
        if self.state == "moving":
            if snap.st("error"):
                return self._finish("failed", f"{self.name}: axis error (code {snap.error_code})", now)
            if not snap.st("busy") and snap.cmd(self.bit) is False:
                if snap.st("pos_complete"):
                    self.evidence.append(f"positioning complete at {snap.position}")
                    return self._finish("done", f"{self.name} reached ({snap.position})", now)
                return self._finish("failed", f"{self.name}: axis stopped without positioning complete", now)
            if self._elapsed(now) > self.t["move"]:
                return self._finish("failed", f"{self.name}: not finished within {self.t['move']:g} s", now)
        return []

    def cancel(self, snap, now):
        if self.result:
            return []
        self._finish("cancelled", f"{self.name} cancelled by STOP (the move itself is not stopped by the PC)",
                     now)
        return [(self.bit, False)]


class KdController:
    def __init__(self, link, cfg, log, clock=time.monotonic):
        self.link = link
        self.cfg = cfg or {}
        self.log = log
        self.clock = clock
        self.timeouts = dict(DEFAULT_TIMEOUTS, **{k: v for k, v in (self.cfg.get("timeouts") or {}).items()
                                                 if k in DEFAULT_TIMEOUTS and v is not None})
        self.interval = float(self.cfg.get("poll_interval", 0.2))
        self.commands = queue.Queue()
        self.events = queue.Queue()
        self.snap = None
        self.connected = False
        self.link_state, self.link_error = "starting", None
        self.op = None
        self.fault = None
        self.pending_release = set()     # bits to clear once the link is back (never set again)
        self._stop = threading.Event()
        self._thread = None

    # ------------------------------------------------------------ public
    def submit(self, *cmd):
        self.commands.put(cmd)

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop_thread(self):
        self._stop.set()

    def busy(self):
        return self.op is not None

    def _run(self):
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception as exc:
                self.log.log("ERROR", f"kd controller: {exc!r}")
                self._latch(f"controller error: {exc}")
            self._stop.wait(self.interval)

    # ------------------------------------------------------------ loop
    def _emit(self, *ev):
        self.events.put(ev)

    def _msg(self, text, level="info"):
        self._emit("message", text, level)

    def _latch(self, reason):
        if self.fault is None:
            self.fault = reason
            self.log.log("FAULT", reason)
            self._emit("fault", reason)

    def poll_once(self):
        now = self.clock()
        while True:
            try:
                cmd = self.commands.get_nowait()
            except queue.Empty:
                break
            self._handle(cmd, now)
        try:
            snap = self.link.read(now)
        except Exception as exc:
            snap = None
            state = classify_link_error(exc)
            why = f"status read: {exc}"
            if why != self.link_error:
                self.log.log("LINK-FAIL", why, state=state)
            self.link_state, self.link_error = state, why
        self.snap = snap
        if snap is None:
            if self.connected:
                self.connected = False
                if self.op is not None:
                    self.op.result, self.op.message = "failed", (f"{self.op.name}: PLC connection lost - not "
                                                                 "confirmed; nothing is re-sent")
                    if self.op.state == "requested":
                        self.pending_release.add(self.op.bit)
                    self._end_op()
                self._msg("PLC connection lost - the machine is not assumed stopped", "fail")
        else:
            if not self.connected:
                self.connected = True
                if self.link_error:
                    self.log.log("LINK-OK", "PLC reads work again", was=self.link_error)
                self.link_error = None
            self.link_state = "ok"
            self._release_pending(snap)
            if self.op is not None:
                self._write(self.op.step(snap, now), self.op.name)
                if self.op.result:
                    self._end_op()
        self._emit("snapshot", snap, self.status_summary())

    def _release_pending(self, snap):
        for address in list(self.pending_release):
            # M00050 is only cleared while the sequence has not started (D05000 = 0)
            if address == kp.HOME_REQUEST and snap.home_state != 0:      # also None: unknown, leave it
                self.pending_release.discard(address)
                continue
            if snap.cmd(address) is False:
                self.pending_release.discard(address)
                continue
            try:
                self.link.set_bit(address, False, "release after a lost connection")
                self.pending_release.discard(address)
            except KdWriteBlocked:
                self.pending_release.discard(address)
            except Exception as exc:
                self.log.log("RELEASE-FAIL", str(exc), bit=address)

    def _write(self, changes, reason):
        for address, value in changes:
            try:
                self.link.set_bit(address, value, reason)
            except Exception as exc:
                if not value:
                    self.pending_release.add(address)
                if self.op is not None and not self.op.result:
                    self.op.result, self.op.message = "failed", f"{self.op.name}: command write failed ({exc})"

    def _end_op(self):
        op, self.op = self.op, None
        self.log.log("RESULT" if op.result == "done" else "FAIL", f"{op.name}: {op.message}",
                     evidence="; ".join(op.evidence) or "-")
        self._msg(op.message, "pass" if op.result == "done" else ("busy" if op.result == "cancelled" else "fail"))
        if op.result == "failed":
            self._latch(op.message)

    # ------------------------------------------------------------ commands
    def _not_ready(self, now):
        if not self.connected or self.snap is None:
            label = LINK_STATES.get(self.link_state, "PLC not connected")
            return f"{label} - {self.link_error}" if self.link_error else label
        age = now - self.snap.time
        if age > float(self.timeouts["freshness"]):
            return f"PLC status is stale ({age:.1f} s old)"
        return self.link.write_blocker()

    def _handle(self, cmd, now):
        kind = cmd[0]
        if kind == "stop":
            self._cmd_stop(now)
        elif kind in ("home", "move"):
            label = "ORIGIN" if kind == "home" else kp.MOVES.get(cmd[1], ("", "", cmd[1]))[2]
            if self.op is not None:
                self._msg(f"X {label} refused: {self.op.name} is still running", "busy")
                return
            if self.fault:
                self._msg(f"X {label} refused: fault latched ({self.fault}) - acknowledge with STOP", "fail")
                return
            why = self._not_ready(now)
            if why:
                self._msg(f"X {label} not started: {why}", "fail")
                return
            op = HomeOp(self.timeouts) if kind == "home" else MoveOp(cmd[1], self.timeouts)
            try:
                changes = op.start(self.snap, now)
            except KdWriteBlocked as exc:
                self._msg(f"X {label} not started: {exc}", "fail")
                return
            self.op = op
            self._write(changes, op.name)
            if op.result:
                self._end_op()
                return
            self.log.log("REQUEST", op.name, bit=op.bit)
            self._msg(f"{op.name}…", "busy")

    def _cmd_stop(self, now):
        if self.op is not None:
            self._write(self.op.cancel(self.snap, now), self.op.name)
            self._end_op()
            return
        if self.fault:
            if self.connected and self.snap is not None and not self.snap.st("busy") and not self.snap.st("error"):
                self.log.log("FAULT", "acknowledged by the operator", fault=self.fault)
                self._msg(f"Fault acknowledged: {self.fault}", "pass")
                self.fault = None
                self._emit("fault", None)
            else:
                self._msg("Fault not acknowledged: PLC not connected, axis busy or in error", "fail")
            return
        self._msg("Nothing to stop (this PLC program has no PC stop command)", "busy")

    def status_summary(self):
        return {"link_state": self.link_state, "link_label": LINK_STATES.get(self.link_state, ""),
                "link_error": self.link_error, "fault": self.fault,
                "op": (self.op.name, self.op.state) if self.op else None,
                "write_blocker": self.link.write_blocker(),
                "status_age": None if self.snap is None else max(0.0, self.clock() - self.snap.time)}
