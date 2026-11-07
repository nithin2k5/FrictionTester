"""Modbus access layer: address conversion, function selection, error classes,
request limits, serialized transport, stale-status inhibition, command masks.

Mock/simulation only: passing here says nothing about the real PLC's Cnet
settings, program or wiring.
"""

import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pymodbus.exceptions import ModbusIOException  # noqa: E402
from pymodbus.pdu import ExceptionResponse  # noqa: E402

from friction_tester.config import migrate  # noqa: E402
from friction_tester.devices.plc_xgb import XGBPlc  # noqa: E402
from friction_tester.plc.event_log import EventLog  # noqa: E402
from friction_tester.plc.modbus_errors import (ModbusRequestError, PlcExceptionResponse, PlcNoResponse,  # noqa: E402
                                               PlcNotOpen, check_request)
from friction_tester.plc.modbus_map import (AreaRangeError, CnetAreaMap, MappingError, ModbusMapping,  # noqa: E402
                                            ModbusPlcBackend, make_mapping)
from friction_tester.plc.pc_flow import classify_link_error  # noqa: E402
from friction_tester.plc.pc_link import PcLink  # noqa: E402
from test_pc_interface import Rig  # noqa: E402


def cnet(**bases):
    areas = {"bit_read": "P00000", "bit_write": "M00000", "word_read": "D00000", "word_write": "D00000"}
    areas.update(bases)
    return {"schema": "xgb_cnet_areas", "verified": True,
            "cnet_areas": {k: {"plc_base": v, "accessible_count": None} for k, v in areas.items()}}


# ------------------------------------------------------------------ conversion
def test_native_to_raw_offsets_with_candidate_bases():
    m = CnetAreaMap(cnet())
    r = m.plan_read_words("D04000", 21)
    assert (r.table, r.fc, r.offset, r.count) == ("input_registers", 4, 4000, 21)
    assert r.offset == 0x0FA0 and r.offset != 0x4000           # decimal 4000, not hex 4000
    w = m.plan_write_words("D04100", 3)
    assert (w.table, w.offset) == ("holding_registers", 4100)
    assert m.locate_bit("P0000A") == ("bit_read", 10)            # hex bit digit: A = 10
    assert m.locate_bit("M00051") == ("bit_write", 5 * 16 + 1)   # M00051 = word 5 bit 1 = 81
    assert m.locate_bit("M0005A") == ("bit_write", 90)
    assert m.plan_write_bit("M00029").offset == 41


def test_other_bases_give_other_offsets_or_unreachable():
    assert CnetAreaMap(cnet(word_read="D01000")).plan_read_words("D04000", 1).offset == 3000
    m = CnetAreaMap(cnet(word_read="P0020", word_write="P0030"))   # word areas in P memory
    with pytest.raises(MappingError, match="D04000-D04020 is not reachable through word_read base P0020"):
        m.plan_read_words("D04000", 21)
    with pytest.raises(MappingError):                              # below the base
        CnetAreaMap(cnet(word_read="D05000", word_write="D05000")).plan_read_words("D04000", 1)
    with pytest.raises(MappingError, match="not reachable"):       # P input is not in an M-based bit_write
        CnetAreaMap(cnet(bit_read="P00100")).locate_bit("P00002")
    with pytest.raises(MappingError, match="base not set"):
        CnetAreaMap(cnet(word_read="", word_write="")).plan_read_words("D04000", 1)


def test_confirmed_range_refuses_before_sending():
    cfg = cnet()
    cfg["cnet_areas"]["word_read"]["accessible_count"] = 994
    cfg["cnet_areas"]["word_write"]["accessible_count"] = 994
    with pytest.raises(AreaRangeError, match="0..993"):
        CnetAreaMap(cfg).plan_read_words("D04000", 21)
    assert CnetAreaMap(cfg).plan_read_words("D00973", 21).offset == 973


# ------------------------------------------------------------------ FC03 / FC04
def test_fc04_by_default_fc03_when_preferred_or_word_read_cannot_reach():
    assert CnetAreaMap(cnet()).plan_read_words("D04003", 3).fc == 4
    pref = cnet()
    pref["word_read_order"] = ["word_write", "word_read"]
    assert CnetAreaMap(pref).plan_read_words("D04003", 3).fc == 3
    fallback = CnetAreaMap(cnet(word_read="P0020"))                # word_read in P, word_write in D
    r = fallback.plan_read_words("D04003", 3)
    assert (r.fc, r.area, r.offset) == (3, "word_write", 4003)


def test_make_mapping_keeps_the_legacy_schema_for_shared_cycle():
    assert isinstance(make_mapping(cnet()), CnetAreaMap)
    legacy = make_mapping({"verified": False, "areas": {"D": {"table": "holding_registers", "base": 0}}})
    assert isinstance(legacy, ModbusMapping) and legacy.plan_read_words("D05010", 1).fc == 3


# ------------------------------------------------------------------ transport
class FakeClient:
    def __init__(self, reply=None, raise_exc=None, delay=0.0):
        self.reply, self.raise_exc, self.delay = reply, raise_exc, delay
        self.calls = []
        self.active = 0
        self.max_active = 0
        self._guard = threading.Lock()

    def is_socket_open(self):
        return True

    def _do(self, name, offset, **kw):
        with self._guard:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(self.delay)
            self.calls.append((name, offset, kw))
            if self.raise_exc:
                raise self.raise_exc
            return self.reply(name, offset, kw) if callable(self.reply) else self.reply
        finally:
            with self._guard:
                self.active -= 1

    def __getattr__(self, name):
        if name.startswith(("read_", "write_")):
            return lambda offset, *a, **kw: self._do(name, offset, **kw)
        raise AttributeError(name)


class Regs:
    def __init__(self, n):
        self.registers = [0] * n
        self.bits = [False] * n

    def isError(self):
        return False


def plc_with(client):
    plc = XGBPlc()
    plc._client, plc._station = client, 1
    return plc


def test_exception_02_is_a_response_timeout_is_not():
    plc = plc_with(FakeClient(reply=ExceptionResponse(3, 2)))
    with pytest.raises(PlcExceptionResponse) as e:
        plc.read_table("holding_registers", 4000, 21)
    assert e.value.responded and e.value.code == 2
    assert str(e.value) == ("PLC responding - FC03 offset 4000 (0x0FA0), quantity 21, station 1: "
                            "exception 02 (illegal data address)")
    assert classify_link_error(e.value) == "map_invalid"

    plc = plc_with(FakeClient(raise_exc=ModbusIOException("No response received after 3 retries")))
    with pytest.raises(PlcNoResponse) as e:
        plc.read_table("input_registers", 4000, 1)
    assert not e.value.responded and "FC04 offset 4000" in str(e.value)
    assert classify_link_error(e.value) == "no_response"

    closed = XGBPlc()
    with pytest.raises(PlcNotOpen):
        closed.read_table("holding_registers", 0, 1)
    assert classify_link_error(PlcNotOpen()) == "port_closed"


def test_function_codes_sent_for_each_table():
    client = FakeClient(reply=lambda name, off, kw: Regs(kw.get("count", 1)))
    plc = plc_with(client)
    for table in ("coils", "discrete_inputs", "holding_registers", "input_registers"):
        plc.read_table(table, 10, 2)
    plc.write_registers_raw(4100, [1])
    assert [c[0] for c in client.calls] == ["read_coils", "read_discrete_inputs", "read_holding_registers",
                                            "read_input_registers", "write_registers"]
    assert all(c[2].get("device_id") == 1 for c in client.calls)


def test_invalid_lengths_are_refused_before_sending():
    for fc, off, qty in ((3, 0, 126), (4, 0, 0), (3, 65535, 2), (1, 0, 2001), (16, 0, 124), (3, -1, 1)):
        with pytest.raises(ModbusRequestError):
            check_request(fc, off, qty)
    client = FakeClient(reply=lambda name, off, kw: Regs(kw["count"]))
    with pytest.raises(ModbusRequestError):
        plc_with(client).read_table("holding_registers", 0, 126)
    assert client.calls == []
    short = plc_with(FakeClient(reply=lambda name, off, kw: Regs(kw["count"] - 1)))
    with pytest.raises(PlcNoResponse, match="short reply"):
        short.read_table("input_registers", 4000, 21)


def test_backend_errors_name_the_native_range_and_area():
    plc = plc_with(FakeClient(reply=ExceptionResponse(4, 2)))
    backend = ModbusPlcBackend(plc, CnetAreaMap(cnet()))
    with pytest.raises(PlcExceptionResponse) as e:
        backend.read_words("D04000", 21)
    assert "FC04 offset 4000" in str(e.value) and "D04000-D04020 via word_read (base D00000)" in str(e.value)


def test_transport_is_serialized_across_threads():
    client = FakeClient(reply=lambda name, off, kw: Regs(kw.get("count", 1)), delay=0.002)
    plc = plc_with(client)

    def worker():
        for _ in range(15):
            plc.read_table("input_registers", 4000, 21)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(client.calls) == 90 and client.max_active == 1


# ------------------------------------------------------------------ controller
def test_map_error_blocks_motion_with_the_exact_request():
    r = Rig()
    assert r.ctl.link_state == "ok"

    def refuse(address, count):
        raise PlcExceptionResponse(4, 4000, 21, 1, 2)
    r.sim.read_words = refuse
    r.step()
    assert r.ctl.snap is None and r.ctl.link_state == "map_invalid"
    writes = len(r.sim.writes)
    r.ctl.submit("home", "shift")
    r.step()
    msg = r.messages()[-1]
    assert "PLC responding, but the PC-interface registers are not readable" in msg
    assert "FC04 offset 4000 (0x0FA0), quantity 21, station 1: exception 02" in msg
    assert len(r.sim.writes) == writes                              # nothing was written


def test_stale_status_inhibits_motion():
    r = Rig()
    assert r.ctl._can_move() is None
    r.clock.t += 5.0                                                # no poll for 5 s
    assert "stale" in r.ctl._can_move()


def test_recovery_does_not_resend_motion():
    r = Rig()
    r.ctl.submit("home", "shift")
    r.step()
    r.sim.read_words = lambda a, c: (_ for _ in ()).throw(PlcNoResponse(4, 4000, 21, 1, "timeout"))
    r.run(0.3)
    assert r.ctl.link_state == "no_response"
    seen = r.sim.requests_seen["shift"]["home"]
    del r.sim.read_words                                            # link back
    r.run(1.0)
    assert r.sim.requests_seen["shift"]["home"] == seen             # home not re-requested


# ------------------------------------------------------------------ command masks
class WordBackend:
    can_write = True

    def __init__(self, words):
        self.words = list(words)
        self.writes = []
        self.mapping = type("M", (), {"verified": True})()
        self.allow_writes = True

    def read_words(self, address, count):
        start = int(address[1:]) - 4100
        return self.words[start:start + count]

    def write_words(self, address, values):
        self.writes.append((address, list(values)))
        self.words[int(address[1:]) - 4100] = values[0]


def test_set_bits_preserves_unrelated_command_bits():
    be = WordBackend([0x00F0, 0x1234, 0x8001])
    link = PcLink(be, EventLog(folder=None), owner_confirmed=True)
    link.attach()
    link.set_bits({(0, 9): True}, "home")                           # SHIFT home M07009
    assert be.writes == [("D04100", [0x02F0])]
    link.set_bits({(0, 9): False, (0, 10): True}, "alarm reset")    # M0700A, home released
    assert be.writes[-1] == ("D04100", [0x04F0])
    assert be.words[1:] == [0x1234, 0x8001]                         # other words never written
    assert len(be.writes) == 2


def test_migration_converts_old_mapping_and_resets_verified():
    old = {"settings_version": 3, "test": {"machine_profile": {"mapping": {
        "verified": True, "areas": {"D": {"table": "holding_registers", "base": 0}}}}}}
    new = migrate(old)
    m = new["test"]["machine_profile"]["mapping"]
    assert new["settings_version"] == 4 and m["schema"] == "xgb_cnet_areas" and m["verified"] is False
    assert m["cnet_areas"]["word_read"]["plc_base"] == "D00000"
    assert any("Cnet-area schema" in n for n in new["migrated"])


def test_register_error_is_reported_before_writes_off():
    r = Rig(verified=False)                                         # writes off

    def refuse(address, count):
        raise PlcExceptionResponse(4, 4000, 21, 1, 2)
    r.sim.read_words = refuse
    r.step()
    r.ctl.submit("home", "shift")
    r.step()
    msg = r.messages()[-1]
    assert "registers are not readable" in msg and "exception 02" in msg and "writes are off" not in msg
