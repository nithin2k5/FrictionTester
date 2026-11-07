"""The PC word interface of the PRINTING PROGRAM OLD profile (plc.pdf).

PcLink is the ONLY writer of D04100-D04102 in this app. Every bit change goes
through set_bits(), under one lock: the current value of all three command
words is kept here and only the word that changes is written, so one axis'
request never clears the other axis' bits (SHIFT and SELECT share D04101 and
D04102). The ladder copies D0410x -> M070x every scan and echoes M070x ->
D0400x, which is what the operations check.

Reads take the whole interface block D04000-D04020 in one request (command
echoes, status words, parameter echoes), plus the internal completion bits
M00051/52/61/62 when their mapping has been verified and enabled.
"""

import threading
import time
from dataclasses import dataclass, field

from . import pc_profile as pp


@dataclass
class PcSnapshot:
    time: float
    echo: list                     # D04000..D04002 (M0700..M0702 as seen by the PLC)
    status: list                   # D04003..D04005
    param_echo: list               # D04007..D04020
    internal: dict = field(default_factory=dict)   # {"M00051": bool, ...} or {}

    def st(self, axis, name):
        idx, n, _src = pp.STATUS[axis][name]
        return pp.bit(self.status, idx, n)

    def common(self, name):
        idx, n, _src = pp.COMMON_STATUS[name]
        return pp.bit(self.status, idx, n)

    def echo_bit(self, idx, n):
        return pp.bit(self.echo, idx, n)

    def complete_bit(self, axis, op):
        """M00051/52/61/62 when read, else None."""
        return self.internal.get(pp.INTERNAL_COMPLETE[axis].get(op, ""), None)

    def selector(self, axis):
        """M00105 / M00205 (ON = local branch) when read, else None."""
        return self.internal.get(pp.SELECTOR[axis])

    def meas_step(self, axis):
        """Active step of the measurement-start chain (-1 = idle), None if not read."""
        bits = [self.internal.get(a) for a in pp.MEAS_STEP_BITS[axis]]
        if any(b is None for b in bits):
            return None
        on = [k for k, b in enumerate(bits) if b]
        return on[0] if on else -1

    def axis_idle(self, axis):
        """No PULL/PUSH sequence active and motion permitted (home not running)."""
        return (self.st(axis, "pull_active") is False and self.st(axis, "push_active") is False
                and self.st(axis, "permitted") is True)


def read_snapshot(backend, internal_enabled=False, now=None):
    """One poll. Raises on a communication error."""
    start, count = pp.READ_BLOCK
    words = backend.read_words(start, count)
    if len(words) < count:
        raise RuntimeError(f"short read: {len(words)} of {count} words")
    internal = {}
    if internal_enabled:
        addrs = [a for axis in pp.AXES for a in pp.INTERNAL_COMPLETE[axis].values()]
        addrs += list(pp.SELECTOR.values())
        addrs += [a for axis in pp.AXES for a in pp.MEAS_STEP_BITS[axis]]
        internal = dict(backend.read_bits(addrs))
    return PcSnapshot(time=time.monotonic() if now is None else now,
                      echo=list(words[0:3]), status=list(words[3:6]), param_echo=list(words[7:21]),
                      internal=internal)


class WriteBlocked(Exception):
    pass


class PcLink:
    def __init__(self, backend, log, owner_confirmed=False, internal_enabled=False):
        self.backend = backend
        self.log = log
        self.owner_confirmed = owner_confirmed
        self.internal_enabled = internal_enabled
        self.words = None              # last command words written/adopted, None = unknown
        self.uncertain = False         # a command write failed: the PLC's words are unknown
        self._lock = threading.Lock()

    # ------------------------------------------------------------ reading
    def read(self, now=None):
        return read_snapshot(self.backend, self.internal_enabled, now)

    # ------------------------------------------------------------ writing
    @property
    def can_write(self):
        return bool(getattr(self.backend, "can_write", False)) and self.owner_confirmed

    def write_blocker(self):
        """Why the app may not write the interface words (None if it may)."""
        if not getattr(self.backend, "mapping", None) or not self.backend.mapping.verified:
            return ("PLC writes are off: the Modbus area mapping is not verified against the PLC's live "
                    "Cnet settings (machine_profile.mapping.verified - see docs/xg5000_modbus_verification.md)")
        if not getattr(self.backend, "allow_writes", False):
            return "PLC writes are off: machine_profile.live_writes_enabled is false"
        if not self.owner_confirmed:
            return ("PLC writes are off: command-word ownership is not confirmed - no other program may "
                    "write D04100-D04102 (machine_profile.command_owner_confirmed)")
        return None

    def attach(self):
        """Adopt the PLC's current D04100-D04102 as the starting point.

        Returns the motion-request bits found ON (left by an earlier session or
        another writer). They are reported, never re-sent and never cleared
        without the operator.
        """
        with self._lock:
            self.words = [int(w) for w in self.backend.read_words(pp.CMD_WORDS[0], 3)]
            self.uncertain = False
        stale = [(axis, op) for (axis, op), (idx, n) in pp.MOTION_BITS.items()
                 if (self.words[idx] >> n) & 1]
        self.log.log("ATTACH", "command words adopted",
                     words=" ".join(f"{w:04X}" for w in self.words),
                     stale=",".join(f"{a}.{o}" for a, o in stale) or "-")
        return stale

    def set_bits(self, changes, reason=""):
        """changes: {(word index, bit): bool}. Writes only the words that change."""
        blocker = self.write_blocker()
        if blocker:
            raise WriteBlocked(blocker)
        with self._lock:
            if self.words is None:
                raise WriteBlocked("command words not read yet (no PLC connection)")
            new = list(self.words)
            for (idx, n), on in changes.items():
                new[idx] = (new[idx] | (1 << n)) if on else (new[idx] & ~(1 << n) & 0xFFFF)
            for idx in range(3):
                if new[idx] != self.words[idx]:
                    try:
                        self.backend.write_words(pp.CMD_WORDS[idx], [new[idx]])
                    except Exception as exc:
                        self.uncertain = True
                        self.log.log("WRITE-FAIL", reason, word=pp.CMD_WORDS[idx], value=f"{new[idx]:04X}",
                                     error=exc)
                        raise
                    self.log.log("WRITE", reason, word=pp.CMD_WORDS[idx],
                                 old=f"{self.words[idx]:04X}", new=f"{new[idx]:04X}")
                    self.words[idx] = new[idx]

    def requested(self, idx, n):
        return self.words is not None and bool((self.words[idx] >> n) & 1)

    def write_params(self, values):
        """D04200-D04213 in one write (callers make sure both axes are idle)."""
        blocker = self.write_blocker()
        if blocker:
            raise WriteBlocked(blocker)
        if len(values) != 14:
            raise ValueError("14 parameter words expected")
        self.backend.write_words(pp.PARAM_WORDS[0], [int(v) for v in values])
        self.log.log("PARAM-WRITE", "", values=",".join(str(int(v)) for v in values))


def validate_params(values):
    """values: list of 14 (int or None) in D04200 order. Returns list of problems."""
    problems = []
    for i, ((axis, key, label, _dst, _sc), v) in enumerate(zip(pp.PARAMS, values)):
        name = f"{pp.AXIS_NAME[axis]} {label}"
        if v is None:
            problems.append(f"{name} is empty")
            continue
        if not isinstance(v, int) or isinstance(v, bool):
            problems.append(f"{name} must be a whole number")
        elif not 0 <= v <= pp.param_max(key):
            problems.append(f"{name} must be 0..{pp.param_max(key)} (got {v})")
    return problems
