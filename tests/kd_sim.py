"""Simulator of the plc kd.pdf homing / move logic, for the automated tests only.

Implements the backend interface used by plc.kd_link (read_words, read_bits,
write_bit, mapping, allow_writes). Reproduces steps 212-236 of plc kd All.pdf
p27: D05000 = 0 AND M00050 -> 10; P00022 (driver homing command) = state 10 AND
NOT M00050; the driver homes for 1.5 s and then sets P0000A; T0000 (2 s) AND
P0000A -> 20; FLT -> D00500.4 ON -> 0. The PLC never resets M00050 (nor M0003x),
so a request left ON never commands the driver and re-triggers at state 0.
DST moves on a rising edge of M00030-M00034 (busy 1 s, then positioning complete).
Faults: connected=False, ignore_home, axis_error, refuse_bits (bit area not readable).
"""

from friction_tester.plc import kd_profile as kp
from friction_tester.plc.modbus_errors import PlcNoResponse
from friction_tester.plc.modbus_map import MappingError


class _Mapping:
    verified = True


class KdSim:
    def __init__(self):
        self.mapping = _Mapping()
        self.allow_writes = True
        self.connected = True
        self.ignore_home = False
        self.refuse_bits = False
        self.D = {500 + i: 0 for i in range(11)}
        self.D[5000] = 0
        self.D[30], self.D[31], self.D[130], self.D[131] = 1234, 0, 5678, 0
        for reg, val in (("D00735", 1000), ("D00745", 2000), ("D00755", 3000), ("D00730", 4000), ("D00740", 0)):
            self.D[int(reg[1:])] = val
        self.M = {a: False for a in kp.COMMAND_BITS}
        self.P = {"P00000": False, "P00004": False, "P0000A": False, "P00022": False}
        self._drv = None                 # time the driver's homing completes (P0000A)
        self.writes = []
        self.home_started = 0
        self.moves_started = []
        self._t = 0.0
        self._busy_until = None
        self._prev = dict(self.M)
        self.position = 0

    # -------------------------------------------------------------- backend
    def _check(self, fc, off, n):
        if not self.connected:
            raise PlcNoResponse(fc, off, n, 1, "timeout")

    def read_words(self, address, count):
        start = int(address[1:])
        self._check(4, start, count)
        return [self.D.get(start + i, 0) for i in range(count)]

    def read_bits(self, addresses):
        self._check(1, 0, len(addresses))
        out = {}
        for a in addresses:
            if a.startswith("M"):
                if self.refuse_bits:
                    raise MappingError(f"{a} is not reachable through bit_read base P00000 or bit_write base P00100")
                out[a] = self.M[a]
            else:
                out[a] = self.P[a]
        return out

    def write_bit(self, address, value):
        self._check(5, 0, 1)
        self.M[address] = bool(value)
        self.writes.append((round(self._t, 2), address, bool(value)))

    # -------------------------------------------------------------- ladder
    def _set_status(self, name, on):
        idx, n = kp.STATUS_BITS[name]
        w = 500 + idx
        self.D[w] = (self.D[w] | (1 << n)) if on else (self.D[w] & ~(1 << n) & 0xFFFF)

    def advance(self, dt):
        self._t += dt
        # homing [steps 212-236]
        s = self.D[5000]
        if s == 0 and self.M["M00050"] and not self.ignore_home:            # step 212
            self.D[5000], self._h = 10, self._t
            self.home_started += 1
        elif s == 10:
            p22 = not self.M["M00050"]                                      # step 236
            if p22 and not self.P["P00022"]:                                # driver starts homing
                self.P["P0000A"], self._drv = False, self._t + 1.5
                self._set_status("homed", False)
                self._set_status("busy", True)
            self.P["P00022"] = p22
            if self._drv is not None and self._t >= self._drv:
                self.P["P0000A"], self._drv = True, None
                self._set_status("busy", False)
                self.position = 0
            if self._t - self._h >= 2.0 and self.P["P0000A"]:              # step 220
                self.D[5000] = 20
        elif s == 20:                                                       # step 228: FLT
            self.P["P00022"] = False
            self._set_status("homed", True)
            self.D[5000] = 0
        # DST moves on a rising edge [steps 283-327]
        for move, (bit, reg, _label) in kp.MOVES.items():
            if self.M[bit] and not self._prev[bit] and self._busy_until is None:
                self._busy_until = self._t + 1.0
                self._target = self.D[int(reg[1:])]
                self._set_status("busy", True)
                self._set_status("pos_complete", False)
                self.moves_started.append(move)
        if self._busy_until is not None and self._t >= self._busy_until:
            self._busy_until = None
            self.position = self._target
            self._set_status("busy", False)
            self._set_status("pos_complete", True)
        self.D[504], self.D[505] = self.position & 0xFFFF, (self.position >> 16) & 0xFFFF
        self._prev = dict(self.M)

    def axis_error(self, code=101):
        self._set_status("error", True)
        self.D[510] = code
