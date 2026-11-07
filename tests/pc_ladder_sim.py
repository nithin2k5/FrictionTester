"""Ladder-level simulator of the plc.pdf (PRINTING PROGRAM OLD) PC interface,
for the automated tests only. It implements the backend interface used by
friction_tester.plc.pc_link (read_words, write_words, read_bits, can_write,
mapping, allow_writes) and reproduces, per axis:

* D0410x -> M070x every scan, echo M070x -> D0400x, status M080x -> D0400x+3
* parameters D04200.. -> M0750.. -> D020xx (echo D04007..), and the targets
  derived on pages 20-22: meas start = origin - p*10, PULL target =
  meas start + travel*100 + D00490 (100 while M0700F is OFF)
* home / PULL / PUSH / measurement-start step sequences with their timers,
  the start conditions (word = 0, M00120, M00122) and the release waits
* measurement start giving up after 5 s (T0103)
* M00051/52/61/62 set at step 6, reset at steps 1 and 9

Faults: connected=False (reads/writes raise), fail_next_write, stuck[axis]
(the axis never reaches its target), other_writer (changes command words).
"""

ORIGIN = 1_000_000
AXES = ("shift", "select")
CMD = {"shift": {"home": (0, 9), "meas_start": (2, 8), "pull": (1, 1), "push": (1, 0)},
       "select": {"home": (0, 13), "meas_start": (2, 9), "pull": (1, 3), "push": (1, 2)}}
STATUS = {
    "shift": {"servo_on": (0, 14), "pos_complete": (0, 15), "origin_complete": (1, 0), "alarm_normal": (1, 1),
              "pull_active": (1, 6), "push_active": (1, 7), "permitted": (1, 10), "axis_error": (2, 0),
              "at_pull": (2, 2), "at_push": (2, 3), "at_meas_start": (2, 14)},
    "select": {"servo_on": (1, 2), "pos_complete": (1, 3), "origin_complete": (1, 4), "alarm_normal": (1, 5),
               "pull_active": (1, 8), "push_active": (1, 9), "permitted": (1, 11), "axis_error": (2, 1),
               "at_pull": (2, 4), "at_push": (2, 5), "at_meas_start": (2, 15)},
}
COMPLETE = {"shift": {"pull": "M00051", "push": "M00052"}, "select": {"pull": "M00061", "push": "M00062"}}
PARAM_ORDER = [("shift", k) for k in ("jog_low", "jog_high", "speed", "pull_travel", "limit_left", "limit_right")] + \
              [("select", k) for k in ("jog_low", "jog_high", "speed", "pull_travel", "limit_left", "limit_right")] + \
              [("shift", "meas_start"), ("select", "meas_start")]

# step timers (s) per sequence; None = no timer, "wait" handled in code
PULL_T = {0: 0, 1: 0, 2: 0.2, 3: 1.0, 5: 0.1, 6: 0.1, 7: 0.1}
HOME_T = {0: 0.1, 1: 0.1, 2: 0.5, 3: 0.1, 5: 1.0}
MEAS_T = {2: 0.2, 5: 0.2}


class _Mapping:
    verified = True


class Seq:
    def __init__(self):
        self.word = 0          # 0 = idle, else 1 + step
        self.t = 0.0           # time in the current step
        self.t_total = 0.0

    @property
    def step(self):
        return self.word - 1 if self.word else None

    def go(self, step):
        self.word, self.t = step + 1, 0.0

    def end(self):
        self.word, self.t, self.t_total = 0, 0.0, 0.0


class Axis:
    def __init__(self):
        self.pos = ORIGIN
        self.target = None
        self.speed = 0
        self.busy = False
        self.servo = True
        self.alarm_normal = True
        self.error = False
        self.origin_complete = True
        self.seq = {k: Seq() for k in ("home", "pull", "push", "meas")}
        self.complete = {"pull": False, "push": False}
        self.params = {}


class PcLadderSim:
    def __init__(self, clock):
        self.clock = clock
        self.D = {n: 0 for n in list(range(4000, 4021)) + list(range(4100, 4103)) + list(range(4200, 4214))}
        self.axes = {a: Axis() for a in AXES}
        self.connected = True
        self.fail_next_write = False
        self.stuck = {a: False for a in AXES}
        self.writes = []                     # (time, address, values)
        self.requests_seen = {a: {k: 0 for k in CMD[a]} for a in AXES}   # rising edges of request bits
        self.starts = {a: {k: 0 for k in ("home", "pull", "push", "meas")} for a in AXES}  # sequences started
        self._last_req = {a: {k: False for k in CMD[a]} for a in AXES}
        self.mapping = _Mapping()
        self.allow_writes = True
        self.can_write = True
        self.scan = 0.01
        self._t = clock()
        self._update_status()

    # ------------------------------------------------------------ backend
    def _check(self):
        if not self.connected:
            raise ConnectionError("simulated PLC disconnected")

    def read_words(self, address, count):
        self._check()
        start = int(address[1:])
        return [self.D.get(start + i, 0) for i in range(count)]

    def write_words(self, address, values):
        self._check()
        if self.fail_next_write:
            self.fail_next_write = False
            raise ConnectionError("simulated write failure")
        start = int(address[1:])
        for i, v in enumerate(values):
            self.D[start + i] = int(v) & 0xFFFF
        self.writes.append((self.clock(), address, list(values)))

    def read_bits(self, addresses):
        self._check()
        out = {}
        for a in addresses:
            for axis in AXES:
                for kind, name in COMPLETE[axis].items():
                    if name == a:
                        out[a] = self.axes[axis].complete[kind]
        return out

    # ------------------------------------------------------------ ladder
    def req(self, axis, kind):
        idx, n = CMD[axis][kind]
        return bool((self.D[4100 + idx] >> n) & 1)

    def advance(self, seconds):
        """Run scans up to clock()."""
        end = self.clock()
        while self._t < end:
            dt = min(self.scan, end - self._t)
            self._t += dt
            self._scan(dt)

    def _ready(self, a):            # M00120 and M00121 parts
        return a.servo and a.alarm_normal and not a.error

    def _m00122(self, a):
        return (a.seq["home"].word == 0 and a.seq["pull"].word == 0 and a.seq["push"].word == 0
                and not a.error and not a.busy and a.origin_complete)

    def _scan(self, dt):
        # parameters: D04200.. -> M0750.. -> D020xx, echo D04007..
        for i, (axis, key) in enumerate(PARAM_ORDER):
            v = self.D[4200 + i]
            self.axes[axis].params[key] = v
            self.D[4007 + i] = v
        m0700f = bool((self.D[4100] >> 15) & 1)
        d00490 = 0 if m0700f else 100
        for axis in AXES:
            a = self.axes[axis]
            p = a.params
            a.meas_target = ORIGIN - p.get("meas_start", 0) * 10
            a.pull_target = a.meas_target + p.get("pull_travel", 0) * 100 + d00490
            a.speed = max(1, p.get("speed", 0)) * 100
            for kind in CMD[axis]:
                r = self.req(axis, kind)
                if r and not self._last_req[axis][kind]:
                    self.requests_seen[axis][kind] += 1
                self._last_req[axis][kind] = r
            self._home(axis, a, dt)
            self._stroke(axis, a, "pull", a.pull_target, dt)
            self._stroke(axis, a, "push", a.meas_target, dt)
            self._meas(axis, a, dt)
            self._move(axis, a, dt)
        for i in range(3):
            self.D[4000 + i] = self.D[4100 + i]
        self._update_status()

    def _move(self, axis, a, dt):
        if a.target is None or self.stuck[axis]:
            a.busy = a.target is not None
            return
        step = a.speed * dt
        if abs(a.target - a.pos) <= step:
            a.pos, a.target, a.busy = a.target, None, False
        else:
            a.pos += step if a.target > a.pos else -step
            a.pos = round(a.pos)
            a.busy = True

    def _timer(self, s, table, dt):
        s.t += dt
        s.t_total += dt
        lim = table.get(s.step)
        return lim is None or s.t >= lim

    def _home(self, axis, a, dt):
        s = a.seq["home"]
        if s.word == 0:
            if self.req(axis, "home") and self._ready(a) and not a.busy:
                s.go(0)
                self.starts[axis]["home"] += 1
            return
        done = self._timer(s, HOME_T, dt)
        st = s.step
        if st == 2 and s.t <= dt:            # entering step 2: home output
            a.origin_complete = False
            a.target = ORIGIN
        if st == 3:
            done = done and a.origin_complete
        if st in (0, 1, 2, 3, 4, 5, 6, 7) and done:
            if st == 5:
                a.pos = ORIGIN               # PRS preset
            s.go(st + 1)
        elif st == 8:
            s.end()
        if a.target is None and not a.busy and st == 3:
            a.origin_complete = True

    def _stroke(self, axis, a, kind, target, dt):
        s = a.seq[kind]
        if s.word == 0:
            if self.req(axis, kind) and self._ready(a) and self._m00122(a):
                s.go(0)
                self.starts[axis][kind] += 1
            return
        done = self._timer(s, PULL_T, dt)
        st = s.step
        if st == 1:
            a.complete[kind] = False
        if st == 3 and s.t <= dt:
            a.target = target
        if st == 5:
            done = done and not a.busy and a.pos == target
        if st == 6:
            a.complete[kind] = True
        if st == 7:
            done = done and not self.req(axis, kind)
        if st == 9:
            a.complete[kind] = False
            s.end()
            return
        if done:
            s.go(st + 1)

    def _meas(self, axis, a, dt):
        s = a.seq["meas"]
        if s.word == 0:
            if self.req(axis, "meas_start") and self._ready(a) and self._m00122(a):
                s.go(0)
                self.starts[axis]["meas"] += 1
            return
        done = self._timer(s, MEAS_T, dt)
        st = s.step
        if st == 2 and s.t <= dt:
            a.target = a.meas_target
        if st == 5:
            done = (done and not a.busy and a.pos == a.meas_target) or s.t_total >= 5.0
        if st == 7:
            done = not self.req(axis, "meas_start")
        if st == 9:
            s.end()
            return
        if done:
            s.go(st + 1)

    def _update_status(self):
        words = [0, 0, 0]
        for axis in AXES:
            a = self.axes[axis]
            vals = {
                "servo_on": a.servo, "pos_complete": not a.busy, "origin_complete": a.origin_complete,
                "alarm_normal": a.alarm_normal, "pull_active": a.seq["pull"].word != 0,
                "push_active": a.seq["push"].word != 0,
                "permitted": self._ready(a) and self._m00122(a), "axis_error": a.error,
                "at_pull": a.pos == getattr(a, "pull_target", None),
                "at_push": a.pos == getattr(a, "meas_target", None),
                "at_meas_start": a.pos == getattr(a, "meas_target", None),
            }
            for name, (idx, n) in STATUS[axis].items():
                if vals[name]:
                    words[idx] |= 1 << n
        for i in range(3):
            self.D[4003 + i] = words[i]
