"""Simulator of the shared automatic cycle, at XG5000-address level.

Implements the backend interface used by plc.shared_cycle (read_bits,
read_words, write_bit, is_open, can_write) and reproduces the ladder
behaviour the app depends on:

* rung 2: step 0 AND (P00002 OR M00029) -> step 10, on the rising edge only
  (MOVP), so a start request left ON restarts the cycle at the next idle;
* step 10 resets M00000/M00003/M0000C/M0000D, clears D00900..D01099 and
  D01200..D01259 (not D01500..D01599), zeroes D05100/D05110;
* steps 40 / 50 store the forward / return samples, 50 increments D05110,
  51 repeats via 33 until D05110 = D00781;
* 70 writes the finals D01250 / D01253 and sets M00000 / M00003 against the
  limits, M0000C if either; 80 sets M0000D if not NG and increments D00150
  (OK) or D00152 (NG) exactly once; D00154 = D00150 + D00152.

Time is taken from an injectable clock so tests are deterministic.
Scenario hooks: queue_verdicts(), abort_at(step), reset_counters(),
set_connected(False), press_start(), fail_next_write().
"""

import random
import threading
import time

from . import xgb_address as xa

# step -> seconds spent there (scaled by `speed`)
DURATIONS = {10: 0.1, 33: 0.2, 34: 0.4, 40: 1.0, 41: 0.2, 42: 0.4, 50: 1.0, 51: 0.1,
             52: 0.5, 60: 0.6, 70: 0.2, 80: 0.1}

SAMPLE_BASES = {  # (channel, direction) -> {slot: register}
    (1, "forward"): {"P1": 900, "P2": 910, "P3": 920, "X1": 1500, "X2": 1510},
    (2, "forward"): {"P1": 1005, "P2": 1015, "P3": 1025, "X1": 1520, "X2": 1530},
    (1, "return"): {"P1": 930, "P2": 940, "P3": 950, "X1": 1550, "X2": 1560},
    (2, "return"): {"P1": 1035, "P2": 1045, "P3": 1055, "X1": 1570, "X2": 1580},
}


class SharedCycleSimulator:
    def __init__(self, clock=time.monotonic, speed=1.0, seed=1, stale_verdict=None,
                 ok_count=10, ng_count=2, repetitions=1):
        self.clock = clock
        self.speed = speed
        self.rng = random.Random(seed)
        self._lock = threading.RLock()
        self.bits = {}                 # "M00029" -> bool (normalised address strings)
        self.words = {}                # word number -> 16-bit value
        self.connected = True
        self._fail_next_write = False
        self._verdicts = []            # queued "OK" / "NG" for the next cycles
        self._abort_step = None
        self._prev_start_cond = False
        self._step_since = clock()
        self.cycles_started = 0
        self.start_writes = []         # log of (time, value) writes to M00029
        # initial PLC state
        self._w(5010, 0)
        self._w(781, repetitions)
        self._w(150, ok_count)
        self._w(152, ng_count)
        self._w(154, ok_count + ng_count)
        for ch, (lo, hi) in {1: (300, 600), 2: (300, 600)}.items():
            self._w(784 if ch == 1 else 788, lo)
            self._w(782 if ch == 1 else 786, hi)
        if stale_verdict == "OK":
            self._b("M0000D", True)
        elif stale_verdict == "NG":
            self._b("M0000C", True)
            self._b("M00000", True)
        self._update_outputs()

    # ------------------------------------------------------------ helpers
    def _w(self, n, v):
        self.words[n] = v & 0xFFFF

    def _rw(self, n):
        return self.words.get(n, 0)

    def _wd(self, n, v):           # DINT, low word first
        v &= 0xFFFFFFFF
        self._w(n, v & 0xFFFF)
        self._w(n + 1, v >> 16)

    @staticmethod
    def _key(addr):
        a = xa.parse(addr)
        return f"{a.area}{a.word:04d}{a.bit:X}"

    def _b(self, addr, v):
        self.bits[self._key(addr)] = bool(v)

    def _rb(self, addr):
        return self.bits.get(self._key(addr), False)

    @property
    def step(self):
        return xa.to_signed16(self._rw(5010))

    def _set_step(self, s):
        self._w(5010, s)
        self._step_since = self.clock()
        self._enter(s)
        self._update_outputs()

    def _update_outputs(self):
        s = self.step
        self._b("P0002A", s != 0)
        self._b("P00029", s == 0)

    # ------------------------------------------------------------ scenario hooks
    def queue_verdicts(self, *verdicts):
        with self._lock:
            self._verdicts.extend(verdicts)

    def abort_at(self, step):
        """Next time the cycle reaches `step`, jump to 0 (rung 598: stop/interlock)."""
        with self._lock:
            self._abort_step = step

    def reset_counters(self):
        """M01122: D00150 = D00152 = D00154 = 0."""
        with self._lock:
            for n in (150, 152, 154):
                self._w(n, 0)

    def set_connected(self, connected):
        with self._lock:
            self.connected = connected

    def fail_next_write(self):
        with self._lock:
            self._fail_next_write = True

    def press_start(self):
        """Physical start button P00002 pressed and released."""
        with self._lock:
            self._b("P00002", True)
            self._update()
            self._b("P00002", False)
            self._update()

    # ------------------------------------------------------------ backend interface
    @property
    def is_open(self):
        return self.connected

    @property
    def can_write(self):
        return True

    def _check(self):
        if not self.connected:
            raise ConnectionError("simulated PLC disconnected")

    def read_bits(self, addresses):
        with self._lock:
            self._check()
            self._update()
            return {a: self._rb(a) for a in addresses}

    def read_words(self, address, count):
        with self._lock:
            self._check()
            self._update()
            start = xa.word_number(address)
            return [self._rw(start + i) for i in range(count)]

    def write_bit(self, address, value):
        with self._lock:
            self._check()
            if self._fail_next_write:
                self._fail_next_write = False
                raise ConnectionError("simulated write failure")
            if xa.parse(address).area == "P":
                raise PermissionError("physical inputs cannot be written")
            self._b(address, value)
            if self._key(address) == self._key("M00029"):
                self.start_writes.append((self.clock(), bool(value)))
            self._update()

    # ------------------------------------------------------------ ladder model
    def _update(self):
        # rung 2, rising edge of (step 0 AND (P00002 OR M00029))
        cond = self.step == 0 and (self._rb("P00002") or self._rb("M00029"))
        if cond and not self._prev_start_cond:
            self.cycles_started += 1
            self._set_step(10)
        self._prev_start_cond = cond
        # advance through timed steps (possibly several per call)
        for _ in range(50):
            s = self.step
            if s == 0:
                break
            if self._abort_step is not None and s == self._abort_step:
                self._abort_step = None
                self._set_step(0)
                break
            if self.clock() - self._step_since < DURATIONS.get(s, 0.1) / self.speed:
                break
            self._leave(s)
            self._set_step(self._next(s))
            cond = self.step == 0 and (self._rb("P00002") or self._rb("M00029"))
            self._prev_start_cond = cond if self.step != 0 else self._prev_start_cond
        # position word D00504 moves during the strokes (raw, arbitrary scale)
        s = self.step
        if s in (40, 50):
            frac = min((self.clock() - self._step_since) / (DURATIONS[s] / self.speed), 1.0)
            pos = int(200 + 2400 * (frac if s == 40 else 1 - frac))
            self._w(504, pos)
            sign = 1 if s == 40 else -1
            self._wd(30, sign * (450 + self.rng.randint(-15, 15)))
            self._wd(130, sign * (430 + self.rng.randint(-15, 15)))
        # rung 524 and D00154 are evaluated every scan
        if self._rb("M00000") or self._rb("M00003"):
            self._b("M0000C", True)
        self._w(154, self._rw(150) + self._rw(152))

    def _next(self, s):
        if s == 10:
            return 40                  # M02000 OFF: no pre-cycling
        if s == 51:
            return 33 if self._rw(5110) != self._rw(781) else 52
        return {33: 34, 34: 40, 40: 41, 41: 42, 42: 50, 50: 51, 52: 60, 60: 70, 70: 80, 80: 0}[s]

    def _enter(self, s):
        if s == 10:
            for addr in ("M00000", "M00003", "M0000C", "M0000D"):
                self._b(addr, False)
            for n in list(range(900, 1100)) + list(range(1200, 1260)):
                self._w(n, 0)
            self._w(5100, 0)
            self._w(5110, 0)
            self._verdict = self._verdicts.pop(0) if self._verdicts else "OK"

    def _leave(self, s):
        if s == 40:
            self._store("forward")
        elif s == 50:
            self._store("return")
            self._w(5110, self._rw(5110) + 1)
        elif s == 70:
            ng = self._verdict == "NG"
            final1 = 720 if ng else 450 + self.rng.randint(-20, 20)
            final2 = 430 + self.rng.randint(-20, 20)
            self._w(1250, final1)
            self._w(1253, final2)
            lo1, hi1 = xa.to_signed16(self._rw(784)), xa.to_signed16(self._rw(782))
            lo2, hi2 = xa.to_signed16(self._rw(788)), xa.to_signed16(self._rw(786))
            if not lo1 <= final1 <= hi1:
                self._b("M00000", True)
            if not lo2 <= final2 <= hi2:
                self._b("M00003", True)
            if self._rb("M00000") or self._rb("M00003"):
                self._b("M0000C", True)
        elif s == 80:
            if not self._rb("M0000C"):
                self._b("M0000D", True)
            if self._rb("M0000D"):
                self._w(150, self._rw(150) + 1)
            if self._rb("M0000C"):
                self._w(152, self._rw(152) + 1)

    def _store(self, direction):
        sign = 1 if direction == "forward" else -1
        for ch in (1, 2):
            base = 450 if ch == 1 else 430
            for slot, reg in SAMPLE_BASES[(ch, direction)].items():
                self._wd(reg, sign * (base + self.rng.randint(-25, 25)))
        # live values follow the last stroke
        self._wd(30, sign * 450)
        self._wd(130, sign * 430)
