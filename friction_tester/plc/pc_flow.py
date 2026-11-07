"""Test controller for the PRINTING PROGRAM OLD profile (plc.pdf).

One background thread owns everything that talks to the PLC: it polls the
interface block, drives the axis operations (axis_ops), runs each cable test
(measurement-start positioning, then PULL/PUSH strokes with acquisition, then
evaluation), coordinates the AT/MT part flow, and transfers/verifies the model
parameters. The Tk thread only submits commands and reads events from
`events` (it never blocks on the PLC).

Part flow (barcode workflow preserved; test.skip_scan still honoured):
  AT: RUN -> scan SELECT -> SELECT test -> scan SHIFT -> SHIFT test -> part
  MT: RUN -> scan one label -> SELECT and SHIFT tests together -> part
Each axis is tracked on its own; a part is finished exactly once, when every
cable it needs has a result. A cable whose machine operation fails leaves the
part INCOMPLETE (not counted, not saved); a cable whose measurements are
missing or out of limits is NG.
"""

import datetime
import queue
import threading
import time

from . import pc_profile as pp
from .axis_ops import OpBlocked, make_op
from .pc_link import WriteBlocked, validate_params

CABLE_OF_AXIS = {"select": "select", "shift": "shift"}


# ------------------------------------------------------------------ evaluation
def _crossing_values(samples, positions):
    """Load where the stroke passes each position (nearest sample), None if never."""
    out = []
    for pos in positions:
        if pos is None:
            out.append(None)
            continue
        best = None
        for _t, load, stroke in samples:
            if stroke is None or load is None:
                continue
            d = abs(stroke - pos)
            if best is None or d < best[0]:
                best = (d, load)
        out.append(None if best is None else best[1])
    return out


def evaluate_axis(strokes, cond, mcfg):
    """strokes: [{"pull": [samples], "push": [samples]}, ...] with samples (t, load, stroke).

    Judgement modes (machine_profile.measurement.judge):
      "peak"      - PULL / PUSH = largest |load| of each stroke direction (default)
      "strokes"   - PULL1-3 / PUSH1-3 = peak of strokes 1-3 (repeated strokes)
      "positions" - PULL1-3 / PUSH1-3 = load at positions 1-3 (needs a stroke source)
    OK only when every required value exists and lies within LOAD min..max.
    """
    judge = mcfg.get("judge", "peak")
    lo, hi = cond.get("load_min"), cond.get("load_max")
    problems = []
    peaks = {"pull": [], "push": []}
    for s in strokes:
        for d in ("pull", "push"):
            vals = [abs(v[1]) for v in s.get(d) or [] if v[1] is not None]
            peaks[d].append(max(vals) if vals else None)
    positions = cond.get("positions") or [None, None, None]
    if judge == "positions":
        last = strokes[-1] if strokes else {}
        values = {d: [None if v is None else abs(v) for v in _crossing_values(last.get(d) or [], positions)]
                  for d in ("pull", "push")}
        if not any(p is not None for p in positions):
            problems.append("judge = positions but no positions are set")
    elif judge == "strokes":
        values = {d: (peaks[d] + [None, None, None])[:3] for d in ("pull", "push")}
        values = {d: v[:max(1, min(3, len(strokes)))] for d, v in values.items()}
    else:
        values = {d: [peaks[d][-1] if peaks[d] else None] for d in ("pull", "push")}
    if lo is None or hi is None:
        problems.append("LOAD limits are not set")
    required = [(d, i, v) for d in ("pull", "push") for i, v in enumerate(values[d])]
    for d, i, v in required:
        if v is None:
            problems.append(f"{d.upper()}{i + 1 if len(values[d]) > 1 else ''}: no valid force samples")
        elif lo is not None and hi is not None and not lo <= v <= hi:
            problems.append(f"{d.upper()}{i + 1 if len(values[d]) > 1 else ''} {v:.3f} outside {lo}..{hi}")
    counts = {d: sum(len(s.get(d) or []) for s in strokes) for d in ("pull", "push")}
    all_vals = [v for d in values for v in values[d] if v is not None]
    return {
        "ok": not problems,
        "verdict": "OK" if not problems else "NG",
        "judge": judge,
        "pull": values["pull"], "push": values["push"],
        "peak_pull": max([p for p in peaks["pull"] if p is not None], default=None),
        "peak_push": max([p for p in peaks["push"] if p is not None], default=None),
        "max": max(all_vals, default=None),
        "sample_counts": counts,
        "problems": problems,
        "limits": [lo, hi],
    }


# ------------------------------------------------------------------ one cable test
class AxisTest:
    """measurement start -> (PULL, PUSH) x strokes -> evaluation, for one axis."""

    def __init__(self, axis, test_id, cond, mcfg, sampler, timeouts, log):
        self.axis, self.test_id, self.cond, self.mcfg = axis, test_id, cond, mcfg
        self.sampler, self.timeouts, self.log = sampler, timeouts, log
        n = max(1, int(mcfg.get("strokes_per_test", 1)))
        self.plan = ["meas_start"] + ["pull", "push"] * n
        self.step = 0
        self.op = None
        self.capture = None
        self.strokes = []
        self.result = None          # dict from evaluate_axis, or {"failed": message}
        self.state = "ready"

    def next_op(self):
        kind = self.plan[self.step]
        self.op = make_op(self.axis, kind, test_id=self.test_id, timeouts=self.timeouts, log=self.log)
        return self.op

    def op_started(self):
        kind = self.op.kind
        if kind in ("pull", "push"):
            self.capture = self.sampler.start(self.axis, kind, self.test_id, len(self.strokes) + (kind == "pull"))

    def op_finished(self):
        op = self.op
        samples = self.sampler.stop(self.capture) if self.capture is not None else None
        self.capture = None
        if op.result != "done":
            self.result = {"failed": op.message}
            self.state = "failed"
            return
        if op.kind == "pull":
            self.strokes.append({"pull": samples or [], "push": []})
        elif op.kind == "push":
            if not self.strokes:
                self.strokes.append({"pull": [], "push": []})
            self.strokes[-1]["push"] = samples or []
        self.step += 1
        self.op = None
        if self.step >= len(self.plan):
            self.result = evaluate_axis(self.strokes, self.cond, self.mcfg)
            self.state = "done"
            self.log.log("EVALUATE", f"{pp.AXIS_NAME[self.axis]}: {self.result['verdict']}",
                         test=self.test_id, problems="; ".join(self.result["problems"]) or "-")


# ------------------------------------------------------------------ controller
class PcController:
    def __init__(self, link, cfg, log, sampler, interval=0.1, clock=time.monotonic, skip_scan=False):
        self.link, self.cfg, self.log, self.sampler = link, cfg, log, sampler
        self.interval, self.clock, self.skip_scan = interval, clock, skip_scan
        self.timeouts = cfg.get("timeouts") or {}
        self.mcfg = cfg.get("measurement") or {}
        self.events = queue.Queue()
        self.commands = queue.Queue()
        self.connected = False
        self.snap = None
        self.stale = []                 # motion bits found ON at attach, not ours
        self.pending_release = set()    # (idx, n) to clear once connected (failed/cancelled ops)
        self.manual = {}                # axis -> AxisOp started by the operator (home)
        self.tests = {}                 # axis -> AxisTest
        self.flow = "idle"
        self.part = None
        self.params = {"state": "not_confirmed", "values": None, "model": None, "message": "no model loaded"}
        self._seq = 0
        self._stop = threading.Event()
        self._thread = None

    # ============================================================ public (any thread)
    def submit(self, *cmd):
        self.commands.put(cmd)

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop_thread(self):
        self._stop.set()

    def busy(self):
        return bool(self.tests) or bool(self.manual) or self.flow != "idle"

    # ============================================================ thread
    def _run(self):
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception as exc:          # never let the loop die silently
                self.log.log("ERROR", f"controller: {exc!r}")
                self._emit("message", f"PLC controller error: {exc}", "fail")
            self._stop.wait(self.interval)
        self._release_everything("application closing")

    def _emit(self, kind, *data):
        self.events.put((kind,) + data)

    def _msg(self, text, level="info"):
        self._emit("message", text, level)

    # ============================================================ one cycle
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
            if self.connected:
                self.log.log("DISCONNECT", str(exc))
        self._connection(snap, now)
        self.snap = snap
        self._check_params(snap, now)
        for axis, op in list(self.manual.items()):
            self._apply(op.observe(snap, now), op)
            if op.result:
                self._op_ended(op)
                del self.manual[axis]
                self._msg(op.message, "pass" if op.result == "done" else "fail")
        for axis, t in list(self.tests.items()):
            self._advance_test(t, snap, now)
        self._advance_flow(now)
        self._emit("snapshot", snap, self.status_summary())

    # ------------------------------------------------------------ connection
    def _connection(self, snap, now):
        if snap is None:
            if self.connected:
                self.connected = False
                self.link.words = None
                self._msg("PLC connection lost - running operations are not confirmed and will not be "
                          "re-sent", "fail")
            return
        if not self.connected:
            self.connected = True
            try:
                stale = self.link.attach()
            except Exception as exc:
                self.connected = False
                self.log.log("ATTACH-FAIL", str(exc))
                return
            ours = [s for s in stale if pp.MOTION_BITS[s] in self.pending_release]
            self.stale = [s for s in stale if s not in ours]
            if self.stale:
                names = ", ".join(pp.command_name(a, o) for a, o in self.stale)
                self._msg(f"PC request bits are ON in the PLC: {names}. They are not re-sent; press STOP to "
                          "release them before testing.", "fail")
            self.log.log("CONNECT", "", stale=len(self.stale), pending_release=len(self.pending_release))
        if self.pending_release and self.link.words is not None:
            changes = {b: False for b in self.pending_release}
            try:
                self.link.set_bits(changes, "release after failed/cancelled operation")
                self.pending_release.clear()
            except WriteBlocked:
                self.pending_release.clear()      # writes are off: nothing of ours can be ON
            except Exception as exc:
                self.log.log("RELEASE-FAIL", str(exc))

    def _apply(self, changes, op):
        if not changes:
            return
        try:
            self.link.set_bits(changes, op.name)
        except Exception as exc:
            if any(not v for v in changes.values()):
                self.pending_release.update(k for k, v in changes.items() if not v)
            op.result = op.result or "failed"
            op.message = op.message or f"{op.name}: command write failed ({exc})"
            self.log.log("WRITE-FAIL", op.message)

    def _op_ended(self, op):
        if op.needs_release:
            self.pending_release.add((op.idx, op.n))

    def _start_op(self, op, now):
        """Start an operation; returns None or the reason it can't start."""
        blocker = self.link.write_blocker()
        if blocker:
            return blocker
        if not self.connected or self.snap is None:
            return "PLC not connected"
        if self.stale:
            return "PC request bits left ON in the PLC - press STOP to release them first"
        try:
            changes = op.start(self.snap, now)
        except OpBlocked as exc:
            return str(exc)
        self._apply(changes, op)
        if op.result:
            self._op_ended(op)
            return op.message
        return None

    # ------------------------------------------------------------ parameters
    def _check_params(self, snap, now):
        p = self.params
        if snap is None:
            if p["state"] in ("writing", "confirmed"):
                p.update(state="not_confirmed", message="connection lost - parameters not confirmed")
                self.log.log("PARAMS", p["message"])
            return
        if p["state"] == "pending" and not self.busy() and all(snap.axis_idle(a) for a in pp.AXES):
            self._write_params(now)
        elif p["state"] == "writing":
            if snap.param_echo == p["values"]:
                p.update(state="confirmed", message="confirmed by readback (D04007-D04020)")
                self.log.log("PARAMS", "confirmed", model=p["model"])
                self._msg(f"PLC parameters for {p['model']} confirmed by readback", "pass")
            elif now - p["t"] > float(self.timeouts.get("params", 3.0)):
                p.update(state="not_confirmed", message="readback did not match within the time limit")
                self.log.log("PARAMS", "readback mismatch", echo=snap.param_echo, sent=p["values"])
                self._msg("PLC parameters not confirmed: readback D04007-D04020 does not match", "fail")
        elif p["state"] == "confirmed" and snap.param_echo != p["values"]:
            p.update(state="not_confirmed", message="parameter echo changed in the PLC")
            self.log.log("PARAMS", p["message"], echo=snap.param_echo)
            self._msg("PLC parameters changed in the PLC - not confirmed any more", "fail")

    def _write_params(self, now):
        p = self.params
        try:
            self.link.write_params(p["values"])
        except Exception as exc:
            p.update(state="not_confirmed", message=f"write failed: {exc}")
            self.log.log("PARAMS", p["message"])
            self._msg(f"PLC parameters not confirmed: {exc}", "fail")
            return
        p.update(state="writing", t=now, message="written, waiting for readback")

    # ------------------------------------------------------------ commands from the UI
    def _handle(self, cmd, now):
        kind = cmd[0]
        if kind == "params":
            _k, model, values = cmd
            problems = validate_params(values)
            if problems:
                self.params = {"state": "not_confirmed", "values": None, "model": model,
                               "message": "; ".join(problems)}
                self._msg(f"PLC parameters for {model} not valid: {problems[0]}", "fail")
                return
            blocker = self.link.write_blocker()
            self.params = {"state": "not_confirmed" if blocker else "pending", "values": list(values),
                           "model": model, "message": blocker or "waiting for both axes to be idle"}
            self.log.log("PARAMS", "requested", model=model, blocked=blocker or "-")
        elif kind == "run":
            self._cmd_run(cmd[1], now)
        elif kind == "scan":
            self._cmd_scan(cmd[1], cmd[2], now)
        elif kind == "stop":
            self._cmd_stop(now)
        elif kind == "home":
            for axis in (pp.AXES if cmd[1] == "all" else [cmd[1]]):
                self._cmd_home(axis, now)

    def _cmd_home(self, axis, now):
        if axis in self.manual or axis in self.tests:
            self._msg(f"{pp.AXIS_NAME[axis]}: an operation is already running", "busy")
            return
        op = make_op(axis, "home", test_id="manual", timeouts=self.timeouts, log=self.log)
        why = self._start_op(op, now)
        if why:
            self._msg(f"{pp.AXIS_NAME[axis]} home not started: {why}", "fail")
            return
        self.manual[axis] = op
        self._msg(f"{pp.AXIS_NAME[axis]} homing…", "busy")

    def _cmd_stop(self, now):
        stopped = []
        for axis, op in list(self.manual.items()):
            self._apply(op.cancel(now), op)
            self._op_ended(op)
            stopped.append(op.name)
            del self.manual[axis]
        for axis, t in list(self.tests.items()):
            if t.op and not t.op.result:
                self._apply(t.op.cancel(now), t.op)
                self._op_ended(t.op)
                stopped.append(t.op.name)
            if t.capture is not None:
                self.sampler.stop(t.capture)
        self.tests.clear()
        if self.part:
            self.log.log("PART", "abandoned by STOP", part=self.part["id"])
        self.part = None
        self._set_flow("idle")
        if self.stale and self.link.words is not None:
            try:
                self.link.set_bits({pp.MOTION_BITS[s]: False for s in self.stale}, "operator release")
                self._msg("Left-over PC request bits released", "pass")
                self.stale = []
            except Exception as exc:
                self._msg(f"Could not release the request bits: {exc}", "fail")
                return
        if stopped:
            self._msg("Application stopped and the request was released. The PLC finishes a stroke that "
                      "has already started by itself - this PLC program has no PC stop command; use the "
                      "machine's emergency stop to stop motion.", "busy")
        else:
            self._msg("Application flow stopped", "busy")

    def _cmd_run(self, info, now):
        """info: {"model", "type", "part_number", "conditions": {"select": cond, "shift": cond}}"""
        if self.flow == "at_scan_shift" and self.skip_scan:
            self._start_stage(["shift"], "at_run_shift", now)
            return
        if self.flow != "idle" or self.tests or self.manual:
            self._msg("A test or operation is already running - press STOP to cancel it", "busy")
            return
        why = self.run_blocker(info)
        if why:
            self._msg(why, "fail")
            return
        self._seq += 1
        self.part = {"id": f"P{self._seq:05d}-{datetime.datetime.now():%H%M%S}", "info": info,
                     "labels": {}, "results": {}, "failed": {}}
        self.log.log("PART", "armed", part=self.part["id"], model=info["model"], type=info["type"])
        if self.skip_scan:
            if info["type"] == "MT":
                self._start_stage(["select", "shift"], "mt_run", now)
            else:
                self._start_stage(["select"], "at_run_select", now)
        else:
            self._set_flow("mt_scan" if info["type"] == "MT" else "at_scan_select")
            self._msg(f"{info['model']} ({info['type']}): scan the "
                      f"{'label' if info['type'] == 'MT' else 'SELECT label'}", "pass")

    def run_blocker(self, info):
        if not info.get("model"):
            return "RUN: load a model first (Model Change)"
        if info.get("type") not in ("MT", "AT"):
            return f"RUN: model {info['model']} has no Type (MT/AT)"
        blocker = self.link.write_blocker()
        if blocker:
            return blocker
        if not self.connected:
            return "PLC not connected"
        if self.stale:
            return "PC request bits left ON in the PLC - press STOP to release them first"
        p = self.params
        if p["state"] != "confirmed" or p["model"] != info["model"]:
            return f"Parameters not confirmed for {info['model']}: {p['message']}"
        return None

    def _cmd_scan(self, box, label, now):
        s = self.flow
        if s == "at_scan_select" and box == "select":
            self.part["labels"]["select"] = label
            self._start_stage(["select"], "at_run_select", now)
        elif s == "at_scan_shift" and box == "shift":
            self.part["labels"]["shift"] = label
            self._start_stage(["shift"], "at_run_shift", now)
        elif s == "mt_scan":
            self.part["labels"] = {"select": label, "shift": label}
            self._start_stage(["select", "shift"], "mt_run", now)
        elif s == "idle":
            self._msg("Press RUN before scanning", "busy")
        else:
            self._msg("Scan refused - wrong box or a test is running", "busy")

    # ------------------------------------------------------------ flow
    def _set_flow(self, state):
        self.flow = state
        self.log.log("FLOW", state)
        self._emit("flow", state)

    def _start_stage(self, axes, run_state, now):
        info = self.part["info"]
        self._set_flow(run_state)
        for axis in axes:
            tid = f"{self.part['id']}-{axis}"
            t = AxisTest(axis, tid, info["conditions"][axis], self.mcfg, self.sampler, self.timeouts, self.log)
            self.tests[axis] = t
            self._emit("axis_started", axis)
        self._msg(" + ".join(pp.AXIS_NAME[a] for a in axes) + " test running…", "info")

    def _advance_test(self, t, snap, now):
        if t.op is None and t.state == "ready":
            op = t.next_op()
            why = self._start_op(op, now)
            if why:
                op.result, op.message = "failed", f"{op.name} not started: {why}"
                t.op_finished()
            else:
                t.op_started()
                t.state = "running"
                return
        elif t.op is not None and not t.op.result:
            self._apply(t.op.observe(snap, now), t.op)
            if t.op.result:
                self._op_ended(t.op)
                t.op_finished()
                if t.state == "running":
                    t.state = "ready"
        if t.state in ("done", "failed"):
            del self.tests[t.axis]
            self._axis_finished(t)

    def _axis_finished(self, t):
        part = self.part
        if part is None:
            return
        if t.state == "failed":
            part["failed"][t.axis] = t.result["failed"]
            self._msg(f"{pp.AXIS_NAME[t.axis]}: {t.result['failed']}", "fail")
        else:
            part["results"][t.axis] = {"result": t.result, "strokes": len(t.strokes)}
            self._emit("axis_result", t.axis, t.result)

    def _advance_flow(self, now):
        part = self.part
        if part is None or self.tests:
            return
        f = self.flow
        if f not in ("at_run_select", "at_run_shift", "mt_run"):
            return
        if part["failed"]:
            names = ", ".join(f"{pp.AXIS_NAME[a]} ({m})" for a, m in part["failed"].items())
            self.log.log("PART", "incomplete - not counted, not saved", part=part["id"], failed=names)
            self._msg(f"Part INCOMPLETE - not counted or saved: {names}", "fail")
            self.part = None
            self._set_flow("idle")
            return
        if f == "at_run_select":
            self._set_flow("at_scan_shift")
            nxt = "press RUN for the SHIFT test" if self.skip_scan else "scan the SHIFT label"
            self._msg(f"SELECT {part['results']['select']['result']['verdict']} - {nxt}", "pass")
            return
        self._finish_part()

    def _finish_part(self):
        part, info = self.part, self.part["info"]
        cables = ["select", "shift"]
        ok = all(part["results"][c]["result"]["ok"] for c in cables)
        record = {
            "profile": pp.PROFILE_ID,
            "time": datetime.datetime.now().isoformat(timespec="seconds"),
            "model": info["model"], "type": info["type"], "part_number": info.get("part_number", ""),
            "label_select": part["labels"].get("select", ""), "label_shift": part["labels"].get("shift", ""),
            "verdict": "OK" if ok else "NG",
            "verdict_basis": "app judgement of measured loads per cable",
            "stages": [{"stage": c, "verdict": part["results"][c]["result"]["verdict"],
                        "result": part["results"][c]["result"]} for c in cables],
            "cables": cables,
            "part_id": part["id"],
        }
        self.part = None
        self._set_flow("idle")
        self.log.log("PART", "complete", part=part["id"], verdict=record["verdict"])
        self._emit("part_done", record)
        nxt = "press RUN for the next part" if self.skip_scan else "press RUN and scan the next part"
        self._msg(f"Part complete - {record['verdict']}. {nxt}", "pass" if ok else "fail")

    def _release_everything(self, reason):
        changes = {}
        for op in list(self.manual.values()) + [t.op for t in self.tests.values() if t.op]:
            if op and op.needs_release:
                changes[(op.idx, op.n)] = False
        changes.update({b: False for b in self.pending_release})
        if changes and self.link.words is not None:
            try:
                self.link.set_bits(changes, reason)
            except Exception as exc:
                self.log.log("RELEASE-FAIL", f"{reason}: {exc}")

    # ------------------------------------------------------------ display
    def status_summary(self):
        return {
            "connected": self.connected,
            "flow": self.flow,
            "params": dict(self.params),
            "ops": {a: (op.name, op.state) for a, op in self.manual.items()},
            "tests": {a: (t.op.name if t.op else "", t.op.state if t.op else t.state) for a, t in self.tests.items()},
            "stale": list(self.stale),
            "write_blocker": self.link.write_blocker(),
        }
