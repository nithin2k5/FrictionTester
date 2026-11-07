"""PLC access for the plc kd.pdf profile.

KdLink.read() takes one snapshot: the axis status block D00500-D00510 and the
homing state D05000 are required (a failure raises and the controller treats
the status as gone); command-bit readback (M00020..M00063, FC01 through the
bit-write area), the interlock inputs and the load cells are best effort and
come back as None when they cannot be read.

KdLink.set_bit() is the only write path. It writes one coil (FC05) and only
for the bits in kd_profile.WRITABLE_BITS, and only when the Cnet mapping is
verified, writes are enabled and command ownership is confirmed.
"""

import threading
import time
from dataclasses import dataclass, field

from . import kd_profile as kp


class KdWriteBlocked(Exception):
    pass


@dataclass
class KdSnapshot:
    time: float
    status: list                                   # D00500..D00510
    home_state: int                                # D05000
    bits: dict = field(default_factory=dict)       # {"M00050": bool} or {} when not readable
    bits_error: str = ""
    inputs: dict = field(default_factory=dict)     # {"P00000": bool}
    loads: dict = field(default_factory=dict)      # {"select": int}

    def st(self, name):
        idx, n = kp.STATUS_BITS[name]
        return kp.bit(self.status, idx, n)

    @property
    def position(self):
        lo, hi = kp.POSITION_WORDS
        return kp.dint(self.status[lo], self.status[hi])

    @property
    def error_code(self):
        return int(self.status[kp.ERROR_CODE_WORD])

    def cmd(self, address):
        """Read-back of a command bit, None if the bits could not be read."""
        return self.bits.get(address)


class KdLink:
    def __init__(self, backend, log, owner_confirmed=False):
        self.backend = backend
        self.log = log
        self.owner_confirmed = owner_confirmed
        self._lock = threading.Lock()

    def read(self, now=None):
        start, count = kp.STATUS_BLOCK
        status = self.backend.read_words(start, count)
        if len(status) < count:
            raise RuntimeError(f"short read: {len(status)} of {count} words")
        home_state = int(self.backend.read_words(kp.HOME_STATE, 1)[0])
        snap = KdSnapshot(time=time.monotonic() if now is None else now, status=list(status),
                          home_state=home_state)
        try:
            snap.bits = dict(self.backend.read_bits(kp.COMMAND_BITS))
        except Exception as exc:
            snap.bits, snap.bits_error = {}, str(exc)
        try:
            snap.inputs = dict(self.backend.read_bits(list(kp.INTERLOCK_INPUTS)))
        except Exception:
            snap.inputs = {}
        loads = {}
        for cable, addr in kp.LOADS.items():
            try:
                lo, hi = self.backend.read_words(addr, 2)
                loads[cable] = kp.dint(lo, hi)
            except Exception:
                loads[cable] = None
        snap.loads = loads
        return snap

    def write_blocker(self):
        mapping = getattr(self.backend, "mapping", None)
        if mapping is None or not mapping.verified:
            return ("PLC writes are off: the Modbus area mapping is not verified against the PLC's live "
                    "Cnet settings (machine_profile.mapping.verified - see docs/xg5000_modbus_verification.md)")
        if not getattr(self.backend, "allow_writes", False):
            return "PLC writes are off: kd_profile.live_writes_enabled is false"
        if not self.owner_confirmed:
            return ("PLC writes are off: command ownership not confirmed - nothing else (HMI) may drive "
                    "M00030-M00034 / M00050 (kd_profile.command_owner_confirmed)")
        return None

    def set_bit(self, address, value, reason=""):
        if address not in kp.WRITABLE_BITS:
            raise KdWriteBlocked(f"{address} is not a bit this app writes")
        blocker = self.write_blocker()
        if blocker:
            raise KdWriteBlocked(blocker)
        with self._lock:
            try:
                self.backend.write_bit(address, bool(value))
            except Exception as exc:
                self.log.log("WRITE-FAIL", reason, bit=address, value=int(bool(value)), error=exc)
                raise
        self.log.log("WRITE", reason, bit=address, value=int(bool(value)))
