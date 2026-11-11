"""Test controller for the PRINTING PROGRAM OLD profile (plc.pdf).

One background thread owns everything that talks to the PLC: it polls the
interface block, runs the axis handshakes (axis_ops), executes each cable
routine, coordinates the AT/MT part flow and transfers/verifies parameters.
The Tk thread only submits commands and reads `events`.

Cable routine: fixture checks (configured) -> home if needed (configured) ->
verified measurement-start positioning -> PULL / PUSH strokes (acquisition
armed before each request, window closed at completion) -> evaluation.

Part flow (application requirement - the PDF does not define barcodes):
  AT: RUN -> SELECT label -> SELECT routine -> SHIFT label -> SHIFT routine -> one part
  MT: RUN -> one label -> both routines together (only when mt_parallel_verified)
Each part is frozen at RUN (model, type, conditions, parameters, rules) and
carries a run token; anything from an older token is ignored. A part ends
exactly once as OK, NG (counted), INCOMPLETE or ABORTED (saved with reasons,
not counted). Faults latch: RUN is refused until the operator acknowledges
with STOP while the machine is idle.
"""

import datetime
import queue
import threading
import time

from . import pc_profile as pp
from .axis_ops import OpBlocked, make_op
from .modbus_errors import ModbusRequestError, PlcExceptionResponse, PlcNoResponse, PlcNotOpen
from .modbus_map import MappingError
from .pc_link import WriteBlocked, validate_params

# Link health, from the last poll/attach. Only "ok" carries usable machine status.
LINK_STATES = {
    "ok": "PLC status fresh",
    "starting": "PLC not polled yet",
    "port_closed": "PLC port not open",
    "no_response": "PLC not responding",
    "map_invalid": "PLC responding, but the PC-interface registers are not readable",
    "config": "Modbus area mapping does not reach the PC-interface registers",
    "error": "PLC read failed",
}


def classify_link_error(exc):
    """LINK_STATES key for an exception raised by a poll or attach."""
    if isinstance(exc, PlcNotOpen):
        return "port_closed"
    if isinstance(exc, PlcNoResponse):
        return "no_response"
    if isinstance(exc, PlcExceptionResponse):
        return "map_invalid"
    if isinstance(exc, (MappingError, ModbusRequestError)):
        return "config"
    return "error"

ORIGIN = 1_000_000            # D02000 / D02100 initial value [p1]
STOP_TEXT = ("Application sequence cancelled. Machine stop NOT confirmed - this PLC program has no PC stop "
             "command and no verified abort procedure; a stroke that has started finishes by itself. Use the "
             "machine's emergency stop to stop motion.")


# ------------------------------------------------------------------ evaluation
def _interp(points, pos):
    """Linear interpolation of load at stroke `pos` from (stroke, load) points
    sorted by stroke; None if pos is outside the covered stroke range."""
    pts = sorted(points)
    for (s0, l0), (s1, l1) in zip(pts, pts[1:]):
        if s0 <= pos <= s1:
            return l0 if s1 == s0 else l0 + (l1 - l0) * (pos - s0) / (s1 - s0)
    return None


CAPTURE_MARGIN = 2.0     # mm: the outer capture points lie this far inside each stroke end


def capture_positions(stroke, positions=None):
    """The stroke positions where PULL1-3 / PUSH1-3 are read, on the graph's axis (the
    stroke runs -stroke/2 .. +stroke/2): the model's Position 1-3 (e.g. -8 / 0 / +8 for a
    22 mm stroke), or -(stroke/2 - 2), 0, +(stroke/2 - 2) when the model has none.
    Empty when neither is available."""
    given = [p for p in positions or [] if p is not None]
    if given:
        return given
    if not stroke or stroke / 2 <= CAPTURE_MARGIN:
        return []
    p = stroke / 2 - CAPTURE_MARGIN
    return [-p, 0.0, p]


def centre_lvdt(pull, push, half):
    """LVDT readings of a PULL and its PUSH -> the centred stroke axis: the PULL start is
    -half and the pull direction is positive, whatever the LVDT's zero and polarity.
    A PUSH without its PULL starts at +half and runs negative. Returns (pull_x, push_x)."""
    if pull:
        ref, start = pull[0], -half
        sign = -1.0 if pull[-1] < ref else 1.0
    elif push:
        ref, start = push[0], half
        sign = -1.0 if push[-1] > ref else 1.0
    else:
        return [], []
    return tuple([round(start + sign * (v - ref), 3) for v in raw] for raw in (pull, push))


def lvdt_follows(readings, expected):
    """True when the LVDT readings moved at least half of the travel the axis was
    commanded (`expected`, mm). False = the LVDT does not follow the axis: not wired,
    wrong NI channel, or not scaled to mm."""
    return bool(readings) and max(readings) - min(readings) >= 0.5 * expected


def stroke_points(pull, push, stroke, speed):
    """Valid samples [(t, load, lvdt), ...] of one PULL / PUSH -> ({"pull": [(x, load)],
    "push": [...]}, source) on the centred stroke axis. Source "lvdt" when the LVDT was
    read and followed the stroke; otherwise "speed x time" (the commanded stroke: PULL
    -stroke/2 -> +stroke/2, PUSH back), with the reason when an LVDT reading was
    rejected; None when neither is available."""
    half = stroke / 2
    lv = {"pull": [s for s in pull if s[2] is not None], "push": [s for s in push if s[2] is not None]}
    source = "speed x time"
    if lv["pull"] or lv["push"]:
        moved = [s[2] for s in (lv["pull"] or lv["push"])]
        if lvdt_follows(moved, stroke) or not speed:
            xs = centre_lvdt([s[2] for s in lv["pull"]], [s[2] for s in lv["push"]], half)
            return {d: list(zip(x, (s[1] for s in lv[d]))) for d, x in zip(("pull", "push"), xs)}, "lvdt"
        source = (f"speed x time - the LVDT reading moved only {max(moved) - min(moved):.2f} of "
                  f"{stroke:g} mm")
    if not speed:
        return {"pull": [], "push": []}, None
    out = {}
    for d, samples in (("pull", pull), ("push", push)):
        moved = [min((s[0] - samples[0][0]) * speed, stroke) for s in samples]
        out[d] = [(m - half if d == "pull" else half - m, s[1]) for m, s in zip(moved, samples)]
    return out, source


def evaluate_axis(strokes, cond, mcfg):
    """strokes: [{"pull": [(t, load, stroke)], "push": [...]}, ...].

    Rules (machine_profile.measurement), all required - none is chosen silently:
      judge:        "peak"      PULL / PUSH = largest-magnitude load of the last stroke
                    "strokes"   PULL1-3 / PUSH1-3 = peak of strokes 1-3 (repeated strokes)
                    "positions" PULL1-3 / PUSH1-3 = load of the last stroke at the model's
                                Position 1-3, or at -(stroke/2 - 2), 0, +(stroke/2 - 2) when
                                the model has none (capture_positions), linear interpolation
                                on the centred
                                stroke axis of the graph (stroke_points: LVDT, or
                                speed x time when no LVDT is read)
      force_sign:   "absolute" (compare |load|) or "signed" (compare load as delivered)
      limit_bounds: "inclusive" (min <= v <= max) or "exclusive" (min < v < max)
      min_samples:  valid samples needed per stroke direction
    Verdict: OK, NG (a value outside the limits), or INCOMPLETE (rule not
    configured, limits missing, too few or invalid samples, position not covered).
    """
    judge, sign, bounds = mcfg.get("judge") or "", mcfg.get("force_sign") or "", mcfg.get("limit_bounds") or ""
    min_n = int(mcfg.get("min_samples", 3) or 1)
    lo, hi = cond.get("load_min"), cond.get("load_max")
    incomplete, ng = [], []
    positions, stroke_source = [], None
    if judge not in ("peak", "strokes", "positions"):
        incomplete.append("measurement.judge is not configured")
    if sign not in ("absolute", "signed"):
        incomplete.append("measurement.force_sign is not configured")
    if bounds not in ("inclusive", "exclusive"):
        incomplete.append("measurement.limit_bounds is not configured")
    if lo is None or hi is None:
        incomplete.append("LOAD limits are not set")
    f = abs if sign == "absolute" else (lambda v: v)

    def valid(samples):
        return [s for s in samples or [] if s[1] is not None]

    peaks = {"pull": [], "push": []}
    for i, s in enumerate(strokes):
        for d in ("pull", "push"):
            v = valid(s.get(d))
            if len(v) < min_n:
                incomplete.append(f"{d.upper()} stroke {i + 1}: {len(v)} valid samples (< {min_n})")
                peaks[d].append(None)
            else:
                peaks[d].append(f(max((x[1] for x in v), key=abs)))
    values = {"pull": [None], "push": [None]}
    if judge == "peak":
        values = {d: [peaks[d][-1] if peaks[d] else None] for d in values}
    elif judge == "strokes":
        values = {d: peaks[d][:3] for d in values}
    elif judge == "positions":
        if not cond.get("stroke"):
            incomplete.append("judge = positions but the Stroke condition is empty")
        else:
            positions = capture_positions(cond["stroke"], cond.get("positions"))
            if not positions:
                incomplete.append("judge = positions but the model has no Position 1-3 and the Stroke "
                                  f"is under {2 * CAPTURE_MARGIN:g} mm")
        last = strokes[-1] if strokes else {}
        points = {"pull": [], "push": []}
        if positions:
            points, stroke_source = stroke_points(valid(last.get("pull")), valid(last.get("push")),
                                                  cond["stroke"], cond.get("speed"))
        for d in values:
            pts = points[d]
            if positions and len(pts) < min_n:
                incomplete.append(f"{d.upper()}: no stroke data for positional sampling"
                                  + ("" if stroke_source else " (no LVDT reading and no Speed condition)"))
            vals = []
            for p in positions:
                v = _interp(pts, p) if len(pts) >= 2 else None
                if v is None and len(pts) >= min_n:
                    incomplete.append(f"{d.upper()}: position {p:g} mm not covered by the stroke data "
                                      f"({min(x for x, _ in pts):.1f} .. {max(x for x, _ in pts):.1f} mm, "
                                      f"{stroke_source})")
                vals.append(None if v is None else f(v))
            values[d] = vals or [None]
    for d in values:
        for i, v in enumerate(values[d]):
            tag = f"{d.upper()}{i + 1 if len(values[d]) > 1 else ''}"
            if v is None:
                if not incomplete:
                    incomplete.append(f"{tag}: no value")
            elif lo is not None and hi is not None and bounds:
                inside = lo <= v <= hi if bounds == "inclusive" else lo < v < hi
                if not inside:
                    ng.append(f"{tag} {v:.3f} outside {lo}..{hi} ({bounds})")
    verdict = "INCOMPLETE" if incomplete else ("NG" if ng else "OK")
    all_vals = [v for d in values for v in values[d] if v is not None]
    graph = {}
    for d in ("pull", "push"):
        pts = valid(strokes[-1].get(d)) if strokes else []
        step = max(1, len(pts) // 200)
        t0 = pts[0][0] if pts else 0
        graph[d] = [(round(x[0] - t0, 4), x[1], x[2]) for x in pts[::step]]
    return {
        "ok": verdict == "OK", "verdict": verdict,
        "judge": judge, "positions": positions, "stroke_source": stroke_source, "force_sign": sign, "limit_bounds": bounds, "min_samples": min_n,
        "pull": values["pull"], "push": values["push"],
        "max": max(all_vals, key=abs, default=None),
        "sample_counts": {d: sum(len(valid(s.get(d))) for s in strokes) for d in ("pull", "push")},
        "invalid_counts": {d: sum(len(s.get(d) or []) - len(valid(s.get(d))) for s in strokes)
                           for d in ("pull", "push")},
        "problems": incomplete + ng,
        "limits": [lo, hi],
        "graph": graph,
    }


def derived_targets(values, m0700f_on):
    """PLC targets the ladder derives from a parameter set [p20-22] (for validation
    only - the app never applies D00490 itself)."""
    d00490 = 0 if m0700f_on else 100
    out = {}
    for axis in pp.AXES:
        v = {k: values[pp.param_index(axis, k)] for k in pp.PARAM_KEYS}
        start = ORIGIN - v["meas_start"] * 10
        out[axis] = {"start": start, "pull": start + v["pull_travel"] * 100 + d00490,
                     "left_limit": ORIGIN + v["limit_left"] * 100, "right_limit": ORIGIN - v["limit_right"] * 100}
    return out


def target_problems(targets):
    probs = []
    for axis, t in targets.items():
        if not t["right_limit"] <= t["start"] <= t["pull"] <= t["left_limit"]:
            probs.append(f"{pp.AXIS_NAME[axis]}: targets outside the limits (right {t['right_limit']} <= start "
                         f"{t['start']} <= PULL {t['pull']} <= left {t['left_limit']} required)")
    return probs


# ------------------------------------------------------------------ one cable routine
class CableRoutine:
    def __init__(self, axis, test_id, cond, mcfg, sampler, ctl):
        self.axis, self.test_id, self.cond, self.mcfg, self.sampler, self.ctl = axis, test_id, cond, mcfg, sampler, ctl
        self.plan = None
        self.step = 0
        self.op = None
        self.capture = None
        self.read_errors = set()        # why force reads failed during this routine
        self.strokes = []
        self.evidence = []
        self.result = None
        self.state = "ready"
        self.meta = sampler.describe(axis) if hasattr(sampler, "describe") else {}

    def build_plan(self, snap):
        st = lambda n: snap.st(self.axis, n)
        checks = (self.ctl.cfg.get("fixture_checks") or {}).get(self.axis) or []
        for name in checks:
            if st(name) is not True:
                raise OpBlocked(f"{pp.AXIS_NAME[self.axis]}: fixture check '{name}' is not ON")
        if not checks:
            self.evidence.append("fixture checks not configured")
        plan = []
        home = self.ctl.cfg.get("home_before_test", "if_not_referenced")
        if home == "always" or (home == "if_not_referenced" and st("origin_complete") is not True):
            plan.append("home")
        observable = snap.meas_step(self.axis) is not None
        if not observable and not plan and st("at_meas_start") and st("pos_complete"):
            self.evidence.append("already on the measurement-start compare and in position; sequence not "
                                 "observable - no positioning requested")
        else:
            plan.append("meas_start")
        n = max(1, int(self.mcfg.get("strokes_per_test", 1)))
        self.plan = plan + ["pull", "push"] * n

    def close_capture(self):
        if self.capture is None:
            return None
        cap = self.capture
        samples = self.sampler.stop(cap)
        self.capture = None
        if getattr(cap, "last_error", None):
            self.read_errors.add(f"{cap.kind.upper()} read error: {cap.last_error}")
        return samples

    def stroke_done(self, kind, samples):
        if kind == "pull":
            self.strokes.append({"pull": samples or [], "push": []})
        else:
            if not self.strokes:
                self.strokes.append({"pull": [], "push": []})
            self.strokes[-1]["push"] = samples or []


# ------------------------------------------------------------------ controller
class PcController:
    def __init__(self, link, cfg, log, sampler, interval=0.1, clock=time.monotonic, skip_scan=False):
        self.link, self.cfg, self.log, self.sampler = link, cfg, log, sampler
        self.interval, self.clock, self.skip_scan = interval, clock, skip_scan
        self.timeouts = cfg.get("timeouts") or {}
        # "internal_flags", "position_fallback", or "auto": the PLC's completion flags when
        # native M reads are verified, otherwise target compare + in-position
        self.completion = cfg.get("stroke_completion", "internal_flags")
        self.events = queue.Queue()
        self.commands = queue.Queue()
        self.connected = False
        self.link_error = None          # why the last read/attach failed (None = fine)
        self.link_state = "starting"    # LINK_STATES key
        self.snap = None
        self.stale = []
        self.pending_release = set()
        self.manual = {}
        self.jogs = {}                  # axis -> {"op", "bit", "last"}: PC jog bits the app holds
        self.routines = {}
        self.flow = "idle"
        self.part = None
        self.fault = None
        self.stop_state = ""
        self.deferred_params = None
        # Home both axes once, on the first good status read after the Test Console opens
        self.home_on_open = bool(cfg.get("home_on_open", False))
        self.pending_run = None         # RUN waiting for both axes to reach origin
        self.params = {"state": "not_confirmed", "values": None, "model": None, "message": "no model loaded"}
        self._seq = 0
        self._stop = threading.Event()
        self._thread = None

    # ============================================================ public
    def submit(self, *cmd):
        self.commands.put(cmd)

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop_thread(self):
        self._stop.set()

    def busy(self):
        return bool(self.routines) or bool(self.manual) or bool(self.jogs) or self.flow != "idle"

    @property
    def tests(self):                  # kept for callers of the first version
        return self.routines

    # ============================================================ loop
    def _run(self):
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception as exc:
                self.log.log("ERROR", f"controller: {exc!r}")
                self._latch(f"controller error: {exc}")
            self._stop.wait(self.interval)
        self._release_everything("application closing")

    def _emit(self, kind, *data):
        self.events.put((kind,) + data)

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
            if self.connected:
                self.log.log("DISCONNECT", str(exc))
            self._link_failed(exc, "status read")
        self._connection(snap, now)
        self.snap = snap
        if self.home_on_open and self.connected and snap is not None:
            self._home_on_open(now)
        self._check_jogs(now)
        self._check_params(snap, now)
        for axis, op in list(self.manual.items()):
            self._apply(op.observe(snap, now), op)
            if op.result:
                self._op_ended(op)
                del self.manual[axis]
                self._msg(op.message, "pass" if op.result == "done" else "fail")
                if op.result == "failed" and op.kind != "alarm_reset":
                    self._latch(op.message)
        if self.pending_run is not None and not self.manual:
            self._start_pending_run(now)
        for r in list(self.routines.values()):
            self._advance(r, snap, now)
        self._advance_flow(now)
        self._emit("snapshot", snap, self.status_summary())

    # ------------------------------------------------------------ connection
    def _connection(self, snap, now):
        if snap is None:
            if self.connected:
                for axis, held in list(self.jogs.items()):      # cannot write now: drop on return
                    self.pending_release.add(held["bit"])
                    self.log.log("JOG", f"{pp.AXIS_NAME[axis]} link lost while jogging - released on reconnection")
                if self.jogs:
                    self._msg("PLC connection lost while jogging - the PLC keeps jogging until a soft limit or "
                              "until the link returns (the bit is released then)", "fail")
                self.jogs.clear()
                self.connected = False
                self.link.words = None
                if self.busy():
                    self._latch("PLC connection lost during operation - machine state unknown")
                self._msg("PLC connection lost - running operations are not confirmed and will not be "
                          "re-sent; the machine is not assumed stopped", "fail")
            return
        if not self.connected:
            try:
                stale = self.link.attach()
            except Exception as exc:
                self._link_failed(exc, f"command-word read {pp.CMD_WORDS[0]}-{pp.CMD_WORDS[-1]}")
                return
            self.connected = True
            if self.link_error:
                self.log.log("LINK-OK", "PLC reads work again", was=self.link_error)
            self.link_error = None
            ours = [s for s in stale if pp.MOTION_BITS[s] in self.pending_release]
            self.stale = [s for s in stale if s not in ours]
            if self.stale:
                names = ", ".join(pp.command_name(a, o) for a, o in self.stale)
                self._msg(f"PC request bits are ON in the PLC: {names}. They are not re-sent; inspect the machine "
                          "and press STOP to release them.", "fail")
            self.log.log("CONNECT", "", stale=len(self.stale), pending_release=len(self.pending_release))
            if self.link.internal_mode == "auto" and not self.link.internal_enabled:
                try:
                    ok, detail = self.link.verify_internal()
                except Exception as exc:
                    ok, detail = False, f"bit read failed: {exc}"
                self.log.log("M-READ", detail, ok=ok)
                self._msg(f"M-area reads verified ({detail}) - stroke end from the PLC's completion flags"
                          if ok else f"M-area reads not usable: {detail} - stroke end from "
                          f"{self._completion()}", "pass" if ok else "busy")
        self.link_state = "ok"
        if self.pending_release and self.link.words is not None:
            try:
                self.link.set_bits({b: False for b in self.pending_release}, "release after failed/cancelled op")
                self.pending_release.clear()
            except WriteBlocked:
                self.pending_release.clear()
            except Exception as exc:
                self.log.log("RELEASE-FAIL", str(exc))

    def _link_failed(self, exc, what):
        state = classify_link_error(exc)
        why = f"{what}: {exc}"
        if why != self.link_error:                # log each new reason once, not every poll
            self.log.log("LINK-FAIL", why, state=state)
        self.link_state, self.link_error = state, why

    def _not_connected(self):
        label = LINK_STATES.get(self.link_state, "PLC not connected")
        return f"{label} - {self.link_error}" if self.link_error else label

    def _status_age(self):
        return None if self.snap is None else max(0.0, self.clock() - self.snap.time)

    def _apply(self, changes, op):
        if not changes:
            return
        try:
            self.link.set_bits(changes, op.name)
        except Exception as exc:
            self.pending_release.update(k for k, v in changes.items() if not v)
            if not op.result:
                op.result, op.message = "failed", f"{op.name}: command write failed ({exc})"
                op.measure_end = True
            self.log.log("WRITE-FAIL", f"{op.name}: {exc}")

    def _op_ended(self, op):
        if op.needs_release:
            self.pending_release.add((op.idx, op.n))

    def _can_move(self):
        # Link/register problems first: they are the root cause, and enabling
        # writes would not get past them.
        if not self.connected or self.snap is None:
            return self._not_connected()
        age = self._status_age()
        if age is not None and age > float(self.timeouts.get("freshness", 1.0)):
            return f"PLC status is stale ({age:.1f} s old) - no motion until a fresh poll succeeds"
        blocker = self.link.write_blocker()
        if blocker:
            return blocker
        if self.stale:
            return "PC request bits left ON in the PLC - inspect the machine and press STOP to release them"
        return None

    def _start_op(self, op, now):
        why = self._can_move()
        if why:
            return why
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
            if p["state"] in ("writing", "settling", "confirmed"):
                p.update(state="not_confirmed", message="connection lost - parameters not confirmed")
                self.log.log("PARAMS", p["message"])
            return
        if p["state"] == "pending" and not self.busy() and all(snap.axis_idle(a) for a in pp.AXES):
            self._write_params(now)
        elif p["state"] == "writing":
            if snap.param_echo == p["values"]:
                p.update(state="settling", t=now, message="echo matches; waiting for the PLC calculations")
            elif now - p["t"] > float(self.timeouts.get("params", 3.0)):
                p.update(state="not_confirmed", message="readback D04007-D04020 did not match in time")
                self.log.log("PARAMS", p["message"], echo=snap.param_echo, sent=p["values"])
                self._msg(f"PLC parameters not confirmed: {p['message']}", "fail")
        elif p["state"] == "settling":
            if snap.param_echo != p["values"]:
                p.update(state="not_confirmed", message="echo changed while settling")
            elif now - p["t"] >= float(self.timeouts.get("params_settle", 0.5)):
                p.update(state="confirmed", message="echo verified (derived targets checked against the ladder "
                                                    "formulas, not read back)")
                self.log.log("PARAMS", "confirmed", model=p["model"])
                self._msg(f"PLC parameters for {p['model']} confirmed by readback", "pass")
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
        p.update(state="writing", t=now, message="written, waiting for the echo")

    def _request_params(self, model, values):
        problems = validate_params(values)
        if not problems:
            m0700f = bool(self.snap and self.snap.echo_bit(0, 15))
            problems = target_problems(derived_targets(values, m0700f))
        if problems:
            self.params = {"state": "not_confirmed", "values": None, "model": model, "message": "; ".join(problems)}
            self._msg(f"PLC parameters for {model} not valid: {problems[0]}", "fail")
            return
        blocker = self.link.write_blocker()
        self.params = {"state": "not_confirmed" if blocker else "pending", "values": list(values),
                       "model": model, "message": blocker or "waiting for both axes to be idle"}
        self.log.log("PARAMS", "requested", model=model, blocked=blocker or "-")

    # ------------------------------------------------------------ UI commands
    def _handle(self, cmd, now):
        kind = cmd[0]
        if kind == "params":
            if self.part is not None:
                self.deferred_params = (cmd[1], cmd[2])
                self._msg("Model parameters are frozen for the active part - applied after it ends", "busy")
                return
            self._request_params(cmd[1], cmd[2])
        elif kind == "run":
            self._cmd_run(cmd[1], now)
        elif kind == "scan":
            self._cmd_scan(cmd[1], cmd[2], now)
        elif kind == "stop":
            self._cmd_stop(now)
        elif kind in ("home", "alarm_reset"):
            for axis in (pp.AXES if cmd[1] == "all" else [cmd[1]]):
                self._cmd_manual(kind, axis, now)
        elif kind == "jog":
            self._cmd_jog(cmd[1], cmd[2], now)

    # ------------------------------------------------------------ jog
    # Hold-to-jog (PC jog bits M07014-M0701B, plc.pdf p17-19): the PLC jogs while the
    # bit is ON and stops when it drops; it does not watch the PC link (M00010 unused),
    # so the app holds a bit only while the operator holds the button. The UI repeats
    # ("jog", axis, op) while the button is down and sends ("jog", axis, None) on
    # release; without a repeat for timeouts.jog_hold seconds the bit is dropped.
    def _jog_hold(self):
        return float(self.timeouts.get("jog_hold", 0.6))

    def _jog_blocker(self, axis, op):
        a = pp.AXIS_NAME[axis]
        if axis in self.manual or axis in self.routines or self.part is not None:
            return "a part or operation is active"
        if self.fault:
            return f"fault latched ({self.fault}) - acknowledge with STOP first"
        why = self._can_move()
        if why:
            return why
        snap = self.snap
        if not snap.st(axis, "servo_on"):
            return f"{a} servo is not ON (P0001{'0' if axis == 'shift' else '4'})"
        if not snap.st(axis, "alarm_normal"):
            return f"{a} drive alarm"
        if snap.st(axis, "axis_error"):
            return f"{a} axis error"
        limit = "limit_left" if "left" in op else "limit_right"
        if snap.st(axis, limit):
            return f"{a} {limit.replace('_', ' ')} reached"
        return None

    def _cmd_jog(self, axis, op, now):
        if op is None:
            self._jog_release(axis, "button released")
            return
        held = self.jogs.get(axis)
        if held and held["op"] == op:
            held["last"] = now                     # refresh while the button is held
            return
        if op not in pp.JOG_OPS:
            self._msg(f"unknown jog {op!r}", "fail")
            return
        why = self._jog_blocker(axis, op)
        if why:
            self._jog_release(axis, "refused")
            self._msg(f"{pp.AXIS_NAME[axis]} jog not started: {why}", "fail")
            return
        changes = {pp.COMMANDS[axis][o]: (o == op) for o in pp.JOG_OPS}
        try:
            self.link.set_bits(changes, f"{pp.AXIS_NAME[axis]} {op}")
        except Exception as exc:
            self.pending_release.update(pp.COMMANDS[axis][o] for o in pp.JOG_OPS)
            self.jogs.pop(axis, None)
            self._msg(f"{pp.AXIS_NAME[axis]} jog not started: {exc}", "fail")
            return
        self.jogs[axis] = {"op": op, "bit": pp.COMMANDS[axis][op], "last": now}
        self.log.log("JOG", f"{pp.AXIS_NAME[axis]} {op}", bit=pp.command_name(axis, op))

    def _jog_release(self, axis, reason):
        held = self.jogs.pop(axis, None)
        if held is None:
            return
        try:
            self.link.set_bits({held["bit"]: False}, f"{pp.AXIS_NAME[axis]} jog release ({reason})")
        except Exception as exc:
            self.pending_release.add(held["bit"])
            self.log.log("RELEASE-FAIL", f"jog {axis}: {exc}")
        self.log.log("JOG", f"{pp.AXIS_NAME[axis]} released", reason=reason)

    def _check_jogs(self, now):
        for axis, held in list(self.jogs.items()):
            if now - held["last"] > self._jog_hold():
                self._jog_release(axis, "no refresh from the button")
                self._msg(f"{pp.AXIS_NAME[axis]} jog stopped: the button stopped refreshing", "busy")

    def _cmd_manual(self, kind, axis, now):
        a = pp.AXIS_NAME[axis]
        if axis in self.manual or axis in self.routines or self.part is not None:
            self._msg(f"{a} {kind.replace('_', ' ')} refused: a part or operation is active", "busy")
            return
        if kind == "home" and self.fault:
            self._msg(f"{a} home refused: fault latched ({self.fault}) - acknowledge with STOP first", "fail")
            return
        op = make_op(axis, kind, test_id="manual", timeouts=self.timeouts, log=self.log)
        why = self._start_op(op, now)
        if why:
            self._msg(f"{a} {kind.replace('_', ' ')} not started: {why}", "fail")
            return
        self.manual[axis] = op
        self._msg(f"{op.name}…", "busy")

    def _start_pending_run(self, now):
        """Homing for a waiting RUN has ended: start the test if both axes are at origin."""
        info, self.pending_run = self.pending_run, None
        away = [pp.AXIS_NAME[a] for a in pp.AXES
                if self.snap is None or self.snap.st(a, "origin_complete") is not True]
        if self.fault or away:
            why = self.fault or f"{' and '.join(away)} not at origin"
            self._msg(f"Test not started: {why}", "fail")
            return
        self.log.log("PART", "both axes at origin - starting the waiting RUN")
        self._cmd_run(info, now)

    def _completion(self):
        """The stroke completion source in use now."""
        if self.completion == "auto":
            return "internal_flags" if self.link.internal_enabled else "position_fallback"
        return self.completion

    def _home_on_open(self, now):
        """One automatic home of both axes after the Test Console opens (home_on_open).

        Tried once on the first good status read; not repeated after a reconnect.
        Skipped (and reported) if PC request bits were found ON in the PLC; every
        other refusal comes from the normal home checks.
        """
        self.home_on_open = False
        if self.stale:
            self._msg("Automatic home on open skipped: PC request bits are ON in the PLC - "
                      "press STOP, then ORIGIN", "fail")
            return
        if self.link.write_blocker():
            self._msg(f"Automatic home on open skipped: {self.link.write_blocker()}", "busy")
            return
        self.log.log("HOME", "automatic home on open", axes=",".join(pp.AXES))
        for axis in pp.AXES:
            self._cmd_manual("home", axis, now)

    def _cmd_stop(self, now):
        if self.pending_run is not None:
            self.pending_run = None
            self._msg("Waiting RUN cancelled", "busy")
        for axis in list(self.jogs):
            self._jog_release(axis, "STOP")
        cancelled = []
        for axis, op in list(self.manual.items()):
            self._apply(op.cancel(now), op)
            self._op_ended(op)
            cancelled.append(op.name)
        self.manual.clear()
        for r in list(self.routines.values()):
            if r.op and not r.op.result:
                self._apply(r.op.cancel(now), r.op)
                self._op_ended(r.op)
                cancelled.append(r.op.name)
            r.close_capture()
        self.routines.clear()
        if self.part is not None:
            self._finalize("ABORTED", ["stopped by the operator"] + [f"{n} cancelled" for n in cancelled])
        self._set_flow("idle")
        if cancelled:
            self.stop_state = STOP_TEXT
            self._msg(STOP_TEXT, "busy")
            return
        if self.stale and self.link.words is not None:
            try:
                self.link.set_bits({pp.MOTION_BITS[s]: False for s in self.stale}, "operator release")
                self._msg("Left-over PC request bits released", "pass")
                self.stale = []
            except Exception as exc:
                self._msg(f"Could not release the request bits: {exc}", "fail")
                return
        if self.fault:
            why = self._recovery_blocker()
            if why:
                self._msg(f"Fault not cleared: {why}", "fail")
                return
            self.log.log("FAULT", "acknowledged by the operator", fault=self.fault)
            self._msg(f"Fault acknowledged: {self.fault}", "pass")
            self.fault = None
            self._emit("fault", None)
        self.stop_state = ""
        self._msg("Application flow idle", "busy")

    def _recovery_blocker(self):
        if not self.connected or self.snap is None:
            return self._not_connected()
        for axis in pp.AXES:
            if self.snap.st(axis, "pull_active") or self.snap.st(axis, "push_active"):
                return f"{pp.AXIS_NAME[axis]} sequence still active"
            if self.snap.st(axis, "axis_error"):
                return f"{pp.AXIS_NAME[axis]} positioning error"
        return None

    def run_blocker(self, info):
        if self.fault:
            return f"Fault latched: {self.fault} - inspect the machine, then press STOP to acknowledge"
        if not info.get("model"):
            return "RUN: load a model first (Model Change)"
        if info.get("type") not in ("MT", "AT"):
            return f"RUN: model {info['model']} has an invalid Type ({info.get('type') or 'none'})"
        if info["type"] == "MT" and not self.cfg.get("mt_parallel_verified"):
            return ("MT needs both axes to run together, which is not verified on this machine "
                    "(machine_profile.mt_parallel_verified)")
        why = self._can_move()
        if why:
            return why
        p = self.params
        if p["state"] != "confirmed" or p["model"] != info["model"]:
            return f"Parameters not confirmed for {info['model']}: {p['message']}"
        return None

    def _cmd_run(self, info, now):
        if self.flow == "at_scan_shift" and self.skip_scan and self.part:
            self._start_stage(["shift"], "at_run_shift", now)
            return
        if self.part is not None or any(op.kind != "home" for op in self.manual.values()):
            self._msg("RUN refused: a part or operation is already active - press STOP to cancel it", "busy")
            return
        # Both axes must be at origin first: home the ones that are not, and start when both are.
        away = [a for a in pp.AXES if self.snap is None or self.snap.st(a, "origin_complete") is not True]
        if self.manual or away:
            for axis in away:
                if axis not in self.manual:
                    self._cmd_manual("home", axis, now)
            if any(a not in self.manual for a in away):
                self._msg("RUN not started: an axis is not at origin and could not be homed", "fail")
                return
            self.pending_run = info
            names = " and ".join(pp.AXIS_NAME[a] for a in self.manual)
            self._msg(f"Waiting for {names} to reach origin - the test starts when both are home", "busy")
            return
        why = self.run_blocker(info)
        if why:
            self._msg(why, "fail")
            return
        self._seq += 1
        frozen = dict(info, params=list(self.params["values"]), measurement=dict(self.cfg.get("measurement") or {}))
        self.part = {"id": f"P{self._seq:05d}-{datetime.datetime.now():%Y%m%d%H%M%S}", "info": frozen,
                     "labels": {}, "results": {}, "failed": {}, "evidence": {}, "acq": {},
                     "t0": now, "started": datetime.datetime.now().isoformat(timespec="seconds")}
        self.stop_state = ""
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
            self._msg("Scan refused: press RUN first", "busy")
        elif s.endswith("_run") or "_run_" in s:
            self._msg("Scan refused: a test is running", "busy")
        else:
            self._msg(f"Scan refused: wrong field ({box})", "busy")

    # ------------------------------------------------------------ flow
    def _set_flow(self, state):
        if state != self.flow:
            self.flow = state
            self.log.log("FLOW", state)
        self._emit("flow", state)

    def _start_stage(self, axes, run_state, now):
        info = self.part["info"]
        self._set_flow(run_state)
        for axis in axes:
            tid = f"{self.part['id']}-{axis}"
            self.routines[axis] = CableRoutine(axis, tid, info["conditions"][axis], info["measurement"],
                                               self.sampler, self)
            self._emit("axis_started", axis)
        self._msg(" + ".join(pp.AXIS_NAME[a] for a in axes) + " routine running…", "info")

    def _fail_routine(self, r, message):
        r.close_capture()
        r.result = {"failed": message}
        r.state = "failed"

    def _advance(self, r, snap, now):
        if self.part is None or not r.test_id.startswith(self.part["id"]):
            del self.routines[r.axis]              # stale run token
            return
        if r.state == "ready" and r.op is None:
            if r.plan is None:
                try:
                    if snap is None:
                        raise OpBlocked(self._not_connected())
                    r.build_plan(snap)
                except OpBlocked as exc:
                    self._fail_routine(r, str(exc))
            if r.state == "ready" and r.step >= len(r.plan):
                r.result = evaluate_axis(r.strokes, r.cond, r.mcfg)
                if r.result["verdict"] == "INCOMPLETE" and r.read_errors:
                    r.result["problems"] = r.result["problems"] + sorted(r.read_errors)
                r.state = "done"
                self.log.log("EVALUATE", f"{pp.AXIS_NAME[r.axis]}: {r.result['verdict']}", test=r.test_id,
                             problems="; ".join(r.result["problems"]) or "-")
            elif r.state == "ready":
                kind = r.plan[r.step]
                op = make_op(r.axis, kind, test_id=r.test_id, timeouts=self.timeouts, log=self.log,
                             completion=self._completion())
                if kind in ("pull", "push"):              # acquisition armed before the request
                    r.capture = self.sampler.start(r.axis, kind, r.test_id, len(r.strokes) + (kind == "pull"))
                why = self._start_op(op, now)
                if why:
                    self._fail_routine(r, f"{op.name} not started: {why}")
                else:
                    r.op = op
                    r.state = "running"
        elif r.state == "running":
            op = r.op
            self._apply(op.observe(snap, now), op)
            if op.measure_end and r.capture is not None and op.kind in ("pull", "push"):
                samples = r.close_capture()               # window ends at completion, not at the release
                if op.result in (None, "done"):
                    r.stroke_done(op.kind, samples)
            if op.result:
                self._op_ended(op)
                r.evidence.extend(f"{op.name}: {e}" for e in op.evidence)
                if op.result != "done":
                    self._fail_routine(r, op.message)
                else:
                    r.op = None
                    r.step += 1
                    r.state = "ready"
        if r.state in ("done", "failed"):
            del self.routines[r.axis]
            self._routine_finished(r)

    def _routine_finished(self, r):
        part = self.part
        part["evidence"][r.axis] = r.evidence
        part["acq"][r.axis] = r.meta
        if r.state == "failed":
            part["failed"][r.axis] = r.result["failed"]
            self._latch(f"{pp.AXIS_NAME[r.axis]}: {r.result['failed']}")
            self._msg(f"{pp.AXIS_NAME[r.axis]}: {r.result['failed']}", "fail")
        else:
            part["results"][r.axis] = r.result
            self._emit("axis_result", r.axis, r.result)

    def _advance_flow(self, now):
        part = self.part
        if part is None:
            return
        limit = float(self.timeouts.get("part", 600.0))
        if now - part["t0"] > limit and self.routines:
            for r in list(self.routines.values()):
                if r.op and not r.op.result:
                    self._apply(r.op.cancel(now, "part timeout"), r.op)
                    self._op_ended(r.op)
                r.close_capture()
                part["failed"][r.axis] = f"part timeout ({limit:g} s)"
            self.routines.clear()
            self._latch(f"part timeout ({limit:g} s)")
        if self.routines or self.flow not in ("at_run_select", "at_run_shift", "mt_run"):
            return
        if part["failed"]:
            self._finalize("INCOMPLETE", [f"{pp.AXIS_NAME[a]}: {m}" for a, m in part["failed"].items()])
            return
        if self.flow == "at_run_select":
            res = part["results"]["select"]
            if res["verdict"] == "INCOMPLETE":
                self._finalize("INCOMPLETE", [f"SELECT: {p}" for p in res["problems"]])
                return
            self._set_flow("at_scan_shift")
            nxt = "press RUN for the SHIFT routine" if self.skip_scan else "scan the SHIFT label"
            self._msg(f"SELECT {res['verdict']} - {nxt}", "pass" if res["ok"] else "fail")
            return
        verdicts = [part["results"][c]["verdict"] for c in ("select", "shift")]
        if "INCOMPLETE" in verdicts:
            reasons = [f"{pp.AXIS_NAME[c]}: {p}" for c in ("select", "shift")
                       for p in part["results"][c]["problems"] if part["results"][c]["verdict"] == "INCOMPLETE"]
            self._finalize("INCOMPLETE", reasons)
        else:
            self._finalize("OK" if verdicts == ["OK", "OK"] else "NG", [])

    def _finalize(self, verdict, reasons):
        """End the active part exactly once."""
        part, self.part = self.part, None
        if part is None:
            return
        info = part["info"]
        counted = verdict in ("OK", "NG")
        stages = []
        for c in ("select", "shift"):
            if c in part["results"]:
                res = part["results"][c]
                stages.append({"stage": c, "verdict": res["verdict"], "result": res})
            elif c in part["failed"]:
                stages.append({"stage": c, "verdict": "ERROR", "result": {"problems": [part["failed"][c]]}})
        record = {
            "profile": pp.PROFILE_ID, "part_id": part["id"],
            "time": datetime.datetime.now().isoformat(timespec="seconds"), "started": part["started"],
            "model": info["model"], "type": info["type"], "part_number": info.get("part_number", ""),
            "label_select": part["labels"].get("select", ""), "label_shift": part["labels"].get("shift", ""),
            "verdict": verdict, "counted": counted, "reasons": reasons,
            "verdict_basis": "app judgement of measured loads per cable (machine_profile.measurement)",
            "stages": stages, "cables": [s["stage"] for s in stages if s["verdict"] in ("OK", "NG")],
            "snapshot": {"conditions": info["conditions"], "params": info["params"],
                         "measurement": info["measurement"]},
            "evidence": part["evidence"], "acquisition": part["acq"],
        }
        self._set_flow("idle")
        self.log.log("PART", "finalized", part=part["id"], verdict=verdict, counted=counted,
                     reasons="; ".join(reasons) or "-")
        self._emit("part_done", record)
        level = "pass" if verdict == "OK" else "fail"
        if counted:
            nxt = "press RUN for the next part" if self.skip_scan else "press RUN and scan the next part"
            self._msg(f"Part complete - {verdict}. {nxt}", level)
        else:
            self._msg(f"Part {verdict} - saved with reasons, not counted: {'; '.join(reasons)[:200]}", level)
        if self.deferred_params:
            model, values = self.deferred_params
            self.deferred_params = None
            self._request_params(model, values)

    def _release_everything(self, reason):
        for r in self.routines.values():          # stop a measuring stroke's loadcell / NI capture
            try:
                r.close_capture()
            except Exception as exc:
                self.log.log("RELEASE-FAIL", f"{reason}: capture: {exc}")
        changes = {held["bit"]: False for held in self.jogs.values()}
        self.jogs.clear()
        for op in list(self.manual.values()) + [r.op for r in self.routines.values() if r.op]:
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
            "connected": self.connected, "link_error": self.link_error, "link_state": self.link_state,
            "link_label": LINK_STATES.get(self.link_state, ""), "status_age": self._status_age(), "flow": self.flow, "params": dict(self.params),
            "ops": {a: (op.name, op.state) for a, op in self.manual.items()},
            "jogs": {a: held["op"] for a, held in self.jogs.items()},
            "routines": {a: (r.op.name if r.op else "", r.op.state if r.op else r.state)
                         for a, r in self.routines.items()},
            "stale": list(self.stale), "fault": self.fault, "stop_state": self.stop_state,
            "write_blocker": self.link.write_blocker(), "completion": self._completion(),
        }
