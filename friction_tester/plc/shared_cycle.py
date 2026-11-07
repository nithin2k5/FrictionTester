"""Shared automatic cycle of the friction machine PLC (XG5000 ladder
"NEW NLF MC KOREA MODIFY 09_06_25", automatic program, pages 11-16).

Everything the app reads or writes is listed in POINTS / SAMPLES below, with
the ladder page it comes from. docs/plc_shared_cycle.md has the full trace.

Cycle (D05010 = step):
    0   idle                    rung 2: step 0 AND (P00002 OR M00029) -> MOVP 10
    10  start: resets M00000/M00003/M0000C/M0000D (rung 613), clears
        D00900..D01099 and D01200..D01259, zeroes D05100/D05110; with
        M02000 OFF it goes straight to 40 (20/30 = optional pre-cycling)
    40  first-direction stroke  samples D00030 / D00130 into windows
    41/42 turn-around
    50  return stroke           samples again; D05110 += 1
    51  repeat check            AVEP/DADD -> D01250 (ch1), D01253 (ch2);
                                back to 33 until D05110 = D00781
    52, 60 home, 70 limit check (sets M00000 / M00003, M0000C if either)
    80  M0000D set if not NG; ADDP D00150 (OK) / D00152 (NG) once; -> 0
Rung 598: steps 10..80 jump to 0 on P00000/P00004/P00008/P0000A/P0000B
OFF or D00500.4 OFF (stop / interlock) - a cycle can end without a verdict.

Verdict bits are latched (SET) and only cleared at the next step 10 or by
P00003, so a bit that is ON says nothing about *which* cycle produced it.
The OK/NG counters change exactly once per completed cycle; the tracker uses
them as the primary evidence of a fresh completion.
"""

import queue
import threading
import time
from dataclasses import dataclass, field

from . import xgb_address as xa

# ---------------------------------------------------------------- addresses
# name: (XG5000 address, kind, access, meaning, ladder page)
POINTS = {
    "start_request": ("M00029", "bit", "write", "software cycle start request (pulse)", 11),
    "start_input": ("P00002", "bit", "read", "physical start input", 11),
    "step": ("D05010", "int16", "read", "automatic cycle step", 11),
    "running": ("P0002A", "bit", "read", "output: cycle running (step <> 0)", 16),
    "idle": ("P00029", "bit", "read", "output: cycle idle (step = 0)", 16),
    "ok": ("M0000D", "bit", "read", "overall OK (latched)", 15),
    "ng": ("M0000C", "bit", "read", "overall NG (latched)", 15),
    "ch1_ng": ("M00000", "bit", "read", "channel 1 (SELECT) final outside D00782..D00784", 15),
    "ch2_ng": ("M00003", "bit", "read", "channel 2 (SHIFT) final outside D00786..D00788", 15),
    "ok_count": ("D00150", "uint16", "read", "PLC OK count", 15),
    "ng_count": ("D00152", "uint16", "read", "PLC NG count", 15),
    "total_count": ("D00154", "uint16", "read", "PLC total (D00150 + D00152)", 15),
    "rep_count": ("D05110", "int16", "read", "measuring repetition counter", 13),
    "rep_target": ("D00781", "int16", "read", "configured repetition target", 13),
    "live_ch1": ("D00030", "dint", "read", "processed analog reading, A/D CH0", 4),
    "live_ch2": ("D00130", "dint", "read", "processed analog reading, A/D CH1", 4),
    "position": ("D00504", "int16", "read", "position word used by the sample windows (low word)", 5),
    "final_ch1": ("D01250", "int16", "read", "final calculated value, channel 1", 14),
    "final_ch2": ("D01253", "int16", "read", "final calculated value, channel 2", 14),
    # Variable/comment table (plc kd All.pdf p5): D00782 "Select Pull Lower Limit",
    # D00784 "Select Pull Upper Limit", D00786 "Shift Pull Lower Limit", D00788 "Shift Pull Upper Limit"
    "ch1_upper": ("D00784", "int16", "read", "channel 1 (SELECT) pull upper limit", 15),
    "ch1_lower": ("D00782", "int16", "read", "channel 1 (SELECT) pull lower limit", 15),
    "ch2_upper": ("D00788", "int16", "read", "channel 2 (SHIFT) pull upper limit", 15),
    "ch2_lower": ("D00786", "int16", "read", "channel 2 (SHIFT) pull lower limit", 15),
}

START_REQUEST = POINTS["start_request"][0]

# Stored samples (DMOV of the 32-bit D00030 / D00130, pages 12-13).
# Base windows: position Pn = D00735 / D00745 / D00755, window +/-20
# (D00800..D00825, page 5). Extra windows: P1+1000 and P2+1000, +/-20
# (D01400..D01430). The extras are kept apart from positions 1-3.
# (channel, direction, slot) -> register (low word; DINT occupies 2 words)
SAMPLES = {
    (1, "forward", "P1"): "D00900", (1, "forward", "P2"): "D00910", (1, "forward", "P3"): "D00920",
    (1, "forward", "X1"): "D01500", (1, "forward", "X2"): "D01510",
    (2, "forward", "P1"): "D01005", (2, "forward", "P2"): "D01015", (2, "forward", "P3"): "D01025",
    (2, "forward", "X1"): "D01520", (2, "forward", "X2"): "D01530",
    (1, "return", "P1"): "D00930", (1, "return", "P2"): "D00940", (1, "return", "P3"): "D00950",
    (1, "return", "X1"): "D01550", (1, "return", "X2"): "D01560",
    (2, "return", "P1"): "D01035", (2, "return", "P2"): "D01045", (2, "return", "P3"): "D01055",
    (2, "return", "X1"): "D01570", (2, "return", "X2"): "D01580",
}
# Step 10 clears D00900..D01099 but NOT D01500..D01599, so an extra sample
# whose window was not crossed this cycle can hold the previous cycle's value.
UNCLEARED_SLOTS = ("X1", "X2")

FINALS = {1: "final_ch1", 2: "final_ch2"}
LIMITS = {1: ("ch1_lower", "ch1_upper"), 2: ("ch2_lower", "ch2_upper")}

STEP_NAMES = {
    0: "Idle", 10: "Start", 20: "Pre-cycle", 30: "Pre-cycle", 32: "Dwell", 33: "Prepare",
    34: "Approach", 40: "Forward stroke", 41: "Turn", 42: "Turn", 50: "Return stroke",
    51: "Repeat check", 52: "Repeat check", 60: "Homing", 70: "Judging", 80: "Finishing",
}

# Word blocks read each poll (start address, count)
_SNAPSHOT_BLOCKS = [("D00030", 2), ("D00130", 2), ("D00150", 5), ("D00504", 1),
                    ("D00781", 1), ("D05010", 1), ("D05110", 1)]
_SNAPSHOT_BITS = [POINTS[k][0] for k in ("ok", "ng", "ch1_ng", "ch2_ng", "start_request",
                                         "start_input", "running", "idle")]
# Blocks read once per completed cycle
_MEASURE_BLOCKS = [("D00900", 56), ("D01005", 56), ("D01250", 6), ("D01500", 82), ("D00782", 8)]


def _word(words, base, address):
    return words[base][xa.word_number(address) - xa.word_number(base)]


@dataclass
class Snapshot:
    step: int
    running: bool
    idle: bool
    ok_bit: bool
    ng_bit: bool
    ch1_ng: bool
    ch2_ng: bool
    start_request: bool
    start_input: bool
    ok_count: int
    ng_count: int
    total_count: int
    rep_count: int
    rep_target: int
    live: dict          # {1: int, 2: int}
    position: int
    time: float = 0.0

    @property
    def verdict_bits(self):
        if self.ng_bit:
            return "NG"
        if self.ok_bit:
            return "OK"
        return None


def read_snapshot(backend, now=None):
    """One poll of the cycle state (a few block reads). Raises on any comms error."""
    bits = backend.read_bits(_SNAPSHOT_BITS)
    blocks = {start: backend.read_words(start, n) for start, n in _SNAPSHOT_BLOCKS}

    def w(addr):
        for start, n in _SNAPSHOT_BLOCKS:
            off = xa.word_number(addr) - xa.word_number(start)
            if 0 <= off < n:
                return blocks[start][off]
        raise KeyError(addr)

    p = {k: v[0] for k, v in POINTS.items()}
    return Snapshot(
        step=xa.to_signed16(w(p["step"])),
        running=bits[p["running"]], idle=bits[p["idle"]],
        ok_bit=bits[p["ok"]], ng_bit=bits[p["ng"]],
        ch1_ng=bits[p["ch1_ng"]], ch2_ng=bits[p["ch2_ng"]],
        start_request=bits[p["start_request"]], start_input=bits[p["start_input"]],
        ok_count=w(p["ok_count"]), ng_count=w(p["ng_count"]), total_count=w(p["total_count"]),
        rep_count=xa.to_signed16(w(p["rep_count"])), rep_target=xa.to_signed16(w(p["rep_target"])),
        live={1: xa.to_dint(w("D00030"), w("D00031")), 2: xa.to_dint(w("D00130"), w("D00131"))},
        position=xa.to_signed16(w(p["position"])),
        time=time.monotonic() if now is None else now,
    )


def read_measurements(backend):
    """The PLC's stored samples, finals, channel flags and limits for the last cycle.

    Samples are raw signed 32-bit values (not scaled, not abs()). Finals are
    the PLC's own D01250 / D01253 - never recalculated here.
    """
    blocks = {start: backend.read_words(start, n) for start, n in _MEASURE_BLOCKS}

    def w(addr):
        for start, n in _MEASURE_BLOCKS:
            off = xa.word_number(addr) - xa.word_number(start)
            if 0 <= off < n:
                return blocks[start][off]
        raise KeyError(addr)

    def dint(addr):
        hi = f"D{xa.word_number(addr) + 1:05d}"
        return xa.to_dint(w(addr), w(hi))

    samples = {}
    for (ch, direction, slot), addr in SAMPLES.items():
        samples.setdefault(ch, {}).setdefault(direction, {})[slot] = dint(addr)
    p = {k: v[0] for k, v in POINTS.items()}
    bits = backend.read_bits([p["ch1_ng"], p["ch2_ng"], p["ok"], p["ng"]])
    return {
        "samples": samples,
        "finals": {ch: xa.to_signed16(w(p[name])) for ch, name in FINALS.items()},
        "limits": {ch: (xa.to_signed16(w(p[lo])), xa.to_signed16(w(p[hi]))) for ch, (lo, hi) in LIMITS.items()},
        "channel_ng": {1: bits[p["ch1_ng"]], 2: bits[p["ch2_ng"]]},
    }


# ------------------------------------------------------------------ tracker
@dataclass
class Event:
    kind: str                  # see CycleTracker / SharedCycleMonitor docstrings
    test_id: object = None
    message: str = ""
    data: dict = field(default_factory=dict)


class CycleTracker:
    """Turns successive snapshots into cycle events without double counting.

    Events: connected, disconnected, counter_reset, cycle_started,
    completed (verdict OK/NG + evidence), ended_without_result,
    start_timeout, ambiguous (connection lost while a test was armed).

    Rules:
    * On (re)connection the current counters and latched bits become the
      baseline; nothing is completed from them.
    * A test is armed just before its start request. A completion needs, after
      arming, a return to step 0 together with exactly one OK or NG counter
      increment (consistent with the verdict bits if they are set). If the
      counters were reset during the cycle, a verdict bit seen ON after the
      bits were seen cleared (step 10) is accepted instead.
    * Activity while nothing is armed (physical start, STOP-ed test) is
      tracked for display only - it never completes a test.
    * Losing the connection while armed makes that test ambiguous; it is never
      retried or completed later.
    """

    def __init__(self, start_timeout=10.0, clock=time.monotonic):
        self.start_timeout = start_timeout
        self.clock = clock
        self.connected = False
        self.last = None
        self.armed = None

    def arm(self, test_id):
        if not self.connected or self.last is None:
            raise RuntimeError("PLC state unknown - not connected")
        s = self.last
        self.armed = {"test_id": test_id, "t0": self.clock(), "ok0": s.ok_count, "ng0": s.ng_count,
                      "saw_running": s.step != 0, "saw_cleared": False, "counter_reset": False,
                      "lost": False}

    def disarm(self):
        self.armed = None

    def observe(self, snap):
        ev = []
        a = self.armed
        if snap is None:
            if self.connected:
                self.connected = False
                ev.append(Event("disconnected", a["test_id"] if a else None))
                if a:
                    a["lost"] = True
            return ev
        if not self.connected:
            self.connected = True
            stale = snap.verdict_bits
            ev.append(Event("connected", None, data={"stale_verdict": stale, "step": snap.step}))
            if a and a["lost"]:
                ev.append(Event("ambiguous", a["test_id"],
                                "PLC connection was lost during the cycle - result not confirmed"))
                self.armed = None
            self.last = snap
            return ev

        prev = self.last
        if snap.ok_count < prev.ok_count or snap.ng_count < prev.ng_count:
            ev.append(Event("counter_reset", a["test_id"] if a else None,
                            data={"ok": snap.ok_count, "ng": snap.ng_count}))
            if a:
                a["counter_reset"] = True
                a["ok0"], a["ng0"] = snap.ok_count, snap.ng_count

        if a:
            if snap.step != 0 or snap.running:
                if not a["saw_running"]:
                    ev.append(Event("cycle_started", a["test_id"], data={"step": snap.step}))
                a["saw_running"] = True
                if not snap.ok_bit and not snap.ng_bit:
                    a["saw_cleared"] = True
            dok, dng = snap.ok_count - a["ok0"], snap.ng_count - a["ng0"]
            if snap.step == 0 and (a["saw_running"] or dok or dng):
                ev.append(self._judge(a, snap, dok, dng))
                self.armed = None
            elif snap.step == 0 and self.clock() - a["t0"] > self.start_timeout:
                ev.append(Event("start_timeout", a["test_id"],
                                f"PLC did not start the cycle within {self.start_timeout:g} s"))
                self.armed = None
        self.last = snap
        return ev

    @staticmethod
    def _judge(a, snap, dok, dng):
        tid, bits = a["test_id"], snap.verdict_bits
        counts = {"ok_count": snap.ok_count, "ng_count": snap.ng_count, "total_count": snap.total_count}
        if dok + dng > 1:
            return Event("ended_without_result", tid,
                         "Cycle ended without confirmed result (more than one PLC count changed)", counts)
        counter = "OK" if (dok, dng) == (1, 0) else "NG" if (dok, dng) == (0, 1) else None
        if counter and bits in (None, counter):
            return Event("completed", tid, data=dict(counts, verdict=counter,
                                                     evidence="counter" + ("+bits" if bits else "")))
        if counter and bits != counter:
            return Event("ended_without_result", tid,
                         "Cycle ended without confirmed result (verdict bits disagree with counters)", counts)
        if a["counter_reset"] and a["saw_cleared"] and bits:
            return Event("completed", tid, data=dict(counts, verdict=bits, evidence="bits (counters were reset)"))
        return Event("ended_without_result", tid, "Cycle ended without confirmed result", counts)


# ------------------------------------------------------------------ monitor
class SharedCycleMonitor:
    """Polls the PLC on a background thread and owns the start pulse.

    poll_once() does one read, feeds the tracker, finishes a pending start
    pulse and executes a requested start, and returns (snapshot, events).
    run in a thread via start(); results go to `self.queue` for the UI.

    Start request (M00029): only when the PLC is connected and idle and writes
    are allowed. The tracker is armed first, then M00029 is set ON and set OFF
    again PULSE_S later. A failed write is reported, never retried. Extra
    events: start_sent, start_refused, start_failed, start_release_failed.
    """

    PULSE_S = 0.3

    def __init__(self, backend, tracker, can_write, interval=0.25, clock=time.monotonic):
        self.backend = backend
        self.tracker = tracker
        self.can_write = can_write
        self.interval = interval
        self.clock = clock
        self.queue = queue.Queue()
        self._lock = threading.Lock()
        self._pending_start = None
        self._release_at = None
        self._stop = threading.Event()
        self._thread = None

    # ---- called from the UI thread
    def request_start(self, test_id):
        with self._lock:
            self._pending_start = test_id

    def cancel(self):
        """Forget the armed test (app STOP). The PLC cycle itself is not stopped."""
        with self._lock:
            self._pending_start = None
            self.tracker.disarm()

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    # ---- monitor thread
    def _run(self):
        while not self._stop.is_set():
            self.queue.put(self.poll_once())
            self._stop.wait(self.interval)

    def poll_once(self):
        now = self.clock()
        try:
            snap = read_snapshot(self.backend, now)
        except Exception:
            snap = None
        with self._lock:
            events = self.tracker.observe(snap)
            if self._release_at is not None and now >= self._release_at:
                self._release_at = None
                try:
                    self.backend.write_bit(START_REQUEST, False)
                except Exception as exc:
                    events.append(Event("start_release_failed", None,
                                        f"Could not switch {START_REQUEST} OFF ({exc}). The PLC restarts the "
                                        "cycle when it returns to idle while it is ON - check the PLC."))
            tid, self._pending_start = self._pending_start, None
            if tid is not None:
                events.extend(self._start(tid, snap, now))
        for e in events:
            if e.kind == "completed":
                try:
                    e.data["measurements"] = read_measurements(self.backend)
                except Exception as exc:
                    e.data["measurements"] = None
                    e.data["measure_error"] = str(exc)
        return snap, events

    def _start(self, tid, snap, now):
        if snap is None:
            return [Event("start_refused", tid, "PLC not connected - start not sent")]
        if snap.step != 0:
            return [Event("start_refused", tid, f"PLC cycle is not idle (step {snap.step}) - start not sent")]
        if not self.can_write():
            return [Event("start_refused", tid, "Live PLC start is disabled - start not sent")]
        self.tracker.arm(tid)
        try:
            self.backend.write_bit(START_REQUEST, True)
        except Exception as exc:
            self.tracker.disarm()
            return [Event("start_failed", tid, f"Start request {START_REQUEST} failed ({exc}). "
                                               "It may or may not have reached the PLC; it will not be retried.")]
        self._release_at = now + self.PULSE_S
        return [Event("start_sent", tid, f"Start requested ({START_REQUEST} pulse)")]
