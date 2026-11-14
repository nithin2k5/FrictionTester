"""plc kd.pdf profile (homing, moves) against tests/kd_sim.py.

Simulation only: it says nothing about the installed PLC program, the Cnet
Modbus areas, the wiring or the real homing behaviour.
"""

import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from friction_tester.config import migrate  # noqa: E402
from friction_tester.plc import kd_profile as kp  # noqa: E402
from friction_tester.plc.event_log import EventLog  # noqa: E402
from friction_tester.plc.kd_flow import KdController  # noqa: E402
from friction_tester.plc.kd_link import KdLink  # noqa: E402
from friction_tester.plc.modbus_map import CnetAreaMap, ModbusPlcBackend, MappingError  # noqa: E402
from kd_sim import KdSim  # noqa: E402


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class Rig:
    def __init__(self, owner=True, verified=True, writes=True):
        self.clock = Clock()
        self.sim = KdSim()
        self.sim.mapping.verified = verified
        self.sim.allow_writes = writes
        self.log = EventLog(folder=None, clock=lambda: datetime.datetime(2026, 11, 7))
        self.link = KdLink(self.sim, self.log, owner_confirmed=owner)
        self.ctl = KdController(self.link, {"timeouts": {"home": 10.0, "move": 5.0}}, self.log, clock=self.clock)
        self.events = []
        self.step()

    def step(self, dt=0.1):
        self.clock.t += dt
        self.sim.advance(dt)
        self.ctl.poll_once()
        while not self.ctl.events.empty():
            ev = self.ctl.events.get()
            if ev[0] != "snapshot":
                self.events.append(ev)

    def run(self, seconds):
        for _ in range(int(round(seconds / 0.1))):
            self.step()

    def messages(self):
        return [e[1] for e in self.events if e[0] == "message"]


def test_home_pulses_m00050_and_waits_for_the_plc_to_finish():
    r = Rig()
    r.ctl.submit("home")
    r.step()
    assert r.sim.writes == [(r.sim.writes[0][0], "M00050", True)]
    r.run(1.0)
    assert r.ctl.op.state == "homing"                      # accepted: D05000 left 0
    # released on acceptance: no rung resets M00050 and P00022 needs it OFF [kd All p27, step 236]
    assert [w[1:] for w in r.sim.writes] == [("M00050", True), ("M00050", False)]
    assert r.sim.P["P00022"] is True
    r.run(3.0)
    assert r.ctl.op is None and r.messages()[-1] == "X ORIGIN complete"
    assert r.sim.M["M00050"] is False and r.sim.home_started == 1
    r.run(5.0)
    assert r.sim.home_started == 1 and r.sim.D[5000] == 0  # no re-trigger at state 0


def test_simulated_ladder_hangs_while_m00050_is_held():
    # Documents why the app must release M00050: held ON, the driver is never commanded.
    sim = KdSim()
    sim.M["M00050"] = True
    for _ in range(100):
        sim.advance(0.1)
    assert sim.D[5000] == 10 and sim.P["P00022"] is False and sim.home_started == 1


def test_home_not_accepted_clears_the_request():
    r = Rig()
    r.sim.ignore_home = True
    r.ctl.submit("home")
    r.run(4.0)
    assert "did not start homing" in r.messages()[-1]
    assert r.sim.writes[-1][1:] == ("M00050", False) and r.ctl.fault
    r.ctl.submit("home")
    r.step()
    assert "fault latched" in r.messages()[-1]


def test_home_refused_without_writes_or_with_unreadable_bits():
    r = Rig(verified=False)
    r.ctl.submit("home")
    r.step()
    assert "mapping is not verified" in r.messages()[-1] and r.sim.writes == []
    r = Rig(owner=False)
    r.ctl.submit("home")
    r.step()
    assert "ownership not confirmed" in r.messages()[-1] and r.sim.writes == []
    r = Rig()
    r.sim.refuse_bits = True
    r.step()
    r.ctl.submit("home")
    r.step()
    assert "command bits not readable" in r.messages()[-1] and r.sim.writes == []


def test_home_refused_when_busy_in_error_or_already_requested():
    r = Rig()
    r.sim.axis_error(42)
    r.step()
    r.ctl.submit("home")
    r.step()
    assert "error (code 42)" in r.messages()[-1]
    r = Rig()
    r.sim.M["M00050"] = True            # left ON by someone else, PLC not homing (ignored)
    r.sim.ignore_home = True
    r.step()
    r.ctl.submit("home")
    r.step()
    assert "already ON" in r.messages()[-1] and r.sim.writes == []


def test_connection_lost_during_homing_is_not_resent():
    r = Rig()
    r.ctl.submit("home")
    r.run(1.0)
    r.sim.connected = False
    r.run(0.5)
    assert r.ctl.link_state == "no_response" and r.ctl.fault
    r.sim.connected = True
    r.run(4.0)
    assert r.sim.home_started == 1 and [w for w in r.sim.writes if w[2]] == [r.sim.writes[0]]


def test_lost_before_acceptance_releases_m00050_after_reconnect():
    r = Rig()
    r.sim.ignore_home = True
    r.ctl.submit("home")
    r.step()
    r.sim.connected = False
    r.run(0.3)
    r.sim.connected = True
    r.run(0.3)
    assert r.sim.M["M00050"] is False and r.sim.writes[-1][1:] == ("M00050", False)


def test_stop_during_homing_does_not_claim_a_machine_stop():
    r = Rig()
    r.ctl.submit("home")
    r.run(1.0)
    r.ctl.submit("stop")
    r.step()
    assert "no PC stop command" in r.messages()[-1] and len(r.sim.writes) == 2   # set + release only


def test_move_requires_homing_then_releases_its_bit():
    r = Rig()
    r.ctl.submit("move", "pos2")
    r.step()
    assert "not homed" in r.messages()[-1] and r.sim.writes == []
    r.ctl.submit("home")
    r.run(4.0)
    r.ctl.submit("move", "pos2")
    r.run(2.0)
    assert r.messages()[-1] == "X Position 2 reached (2000)"
    assert [w[1:] for w in r.sim.writes[-2:]] == [("M00031", True), ("M00031", False)]
    assert r.sim.moves_started == ["pos2"]


def test_stale_status_blocks_motion():
    r = Rig()
    r.clock.t += 5.0
    r.ctl._handle(("home",), r.clock.t)
    assert "stale" in r.ctl.events.queue[-1][1] and r.sim.writes == []


def test_coil_write_goes_to_m_offset_only_with_an_m_based_bit_area():
    class Plc:
        is_open = True

        def __init__(self):
            self.coils = []

        def write_coil_raw(self, off, value):
            self.coils.append((off, value))

    good = {"schema": "xgb_cnet_areas", "verified": True, "cnet_areas": {
        "bit_read": {"plc_base": "P00000"}, "bit_write": {"plc_base": "M00000"},
        "word_read": {"plc_base": "D00000"}, "word_write": {"plc_base": "D00000"}}}
    plc = Plc()
    ModbusPlcBackend(plc, CnetAreaMap(good), allow_writes=True).write_bit("M00050", True)
    assert plc.coils == [(80, True)]                       # M00050 = word 5 bit 0 = coil 80
    bad = dict(good, cnet_areas=dict(good["cnet_areas"], bit_write={"plc_base": "P00100"}))
    plc = Plc()
    try:
        ModbusPlcBackend(plc, CnetAreaMap(bad), allow_writes=True).write_bit("M00050", True)
        raise AssertionError("expected MappingError")
    except MappingError:
        pass
    assert plc.coils == []                                 # never written to a P output


def test_only_home_and_move_bits_are_writable():
    assert kp.WRITABLE_BITS == {"M00050", "M00030", "M00031", "M00032", "M00033", "M00034"}


def test_settings_migrate_pc_interface_to_kd():
    new = migrate({"settings_version": 4, "test": {"integration": {"mode": "pc_interface"}}})
    assert new["test"]["integration"]["mode"] == "kd" and new["settings_version"] == 8
    other = migrate({"settings_version": 4, "test": {"integration": {"mode": "shared_cycle"}}})
    assert other["test"]["integration"]["mode"] == "shared_cycle"


def test_home_state_unreadable_keeps_status_and_refuses_origin():
    r = Rig()
    real = r.sim.read_words

    def no_d05000(address, count):
        if address == "D05000":
            from friction_tester.plc.modbus_errors import PlcExceptionResponse
            raise PlcExceptionResponse(4, 5000, 1, 1, 2)
        return real(address, count)
    r.sim.read_words = no_d05000
    r.step()
    assert r.ctl.link_state == "ok" and r.ctl.snap.home_state is None
    r.ctl.submit("home")
    r.step()
    assert "D05000 not readable" in r.messages()[-1] and "exception 02" in r.messages()[-1]
    assert r.sim.writes == []


def test_auto_cycle_snapshot_reads_through_the_cnet_areas():
    """plc Auto.pdf cycle (shared_cycle reader) planned on the candidate Cnet areas."""
    from friction_tester.plc.shared_cycle import read_snapshot

    class Plc:
        is_open = True

        def __init__(self):
            self.calls = []

        def read_table(self, table, off, n):
            self.calls.append((table, off, n))
            if table in ("coils", "discrete_inputs"):
                return [False] * n
            return [7 if off == 5010 else 0] * n                  # D05010 step 7... any value

    cfg = {"schema": "xgb_cnet_areas", "verified": True, "cnet_areas": {
        "bit_read": {"plc_base": "P00000"}, "bit_write": {"plc_base": "M00000"},
        "word_read": {"plc_base": "D00000"}, "word_write": {"plc_base": "D00000"}}}
    plc = Plc()
    snap = read_snapshot(ModbusPlcBackend(plc, CnetAreaMap(cfg)))
    assert snap.step == 7
    tables = {c[0] for c in plc.calls}
    assert tables == {"input_registers", "coils", "discrete_inputs"}       # FC04, FC01 (M), FC02 (P)
    assert ("input_registers", 5010, 1) in plc.calls


def test_settings_v6_fills_at_procedure_channels_and_directions_only_when_empty():
    empty = {"settings_version": 5, "test": {"integration": {"mode": "kd", "shared": {
        "at_cable_selection": "", "channel_cable": {"1": "", "2": ""},
        "direction_role": {"forward": "", "return": ""}}}}}
    sh = migrate(empty)["test"]["integration"]["shared"]
    assert sh["at_cable_selection"] == "operator_fixture"
    assert sh["channel_cable"] == {"1": "select", "2": "shift"}
    assert sh["direction_role"] == {"forward": "push", "return": "pull"}
    kept = {"settings_version": 5, "test": {"integration": {"shared": {
        "at_cable_selection": "operator_fixture", "channel_cable": {"1": "shift", "2": "select"},
        "direction_role": {"forward": "pull", "return": "push"}}}}}
    sh = migrate(kept)["test"]["integration"]["shared"]
    assert sh["channel_cable"] == {"1": "shift", "2": "select"} and sh["direction_role"]["forward"] == "pull"


def test_run_blocker_names_the_kd_settings():
    from friction_tester.plc.part_flow import live_start_blocker
    link = KdLink(KdSim(), EventLog(folder=None), owner_confirmed=False)
    msg = live_start_blocker({}, False, link.write_blocker)
    assert "kd_profile.command_owner_confirmed" in msg and "integration.shared" not in msg
    assert "integration.shared" in live_start_blocker({}, False)          # shared_cycle mode unchanged


def test_migration_scales_the_ni_load_cells_10_kgf_per_volt():
    from friction_tester.config import migrate
    old = {"settings_version": 6, "daq": {"sensors": [
        {"name": "Loadcell 1", "scale": 1.0}, {"name": "Loadcell 2", "scale": 4.9},
        {"name": "LVDT 1", "scale": 1.0}]}}
    new = migrate(old)
    assert [s["scale"] for s in new["daq"]["sensors"]] == [10.0, 4.9, 10.0]   # a set scale is kept
    assert new["settings_version"] == 8 and "Loadcell 1" in new["migrated"][0] and "LVDT 1" in new["migrated"][1]


def test_migration_scales_the_lvdts_10_mm_per_volt():
    from friction_tester.config import load_settings, migrate
    old = {"settings_version": 7, "daq": {"sensors": [
        {"name": "Loadcell 1", "scale": 1.0}, {"name": "LVDT 1", "scale": 1.0}, {"name": "LVDT 2", "scale": 12.5}]}}
    new = migrate(old)
    assert [s["scale"] for s in new["daq"]["sensors"]] == [1.0, 10.0, 12.5]   # loadcell and a set LVDT scale kept
    assert new["settings_version"] == 8 and new["migrated"] == ["daq LVDT 1: scale 1 -> 10 mm/V (LVDT 10 V over 100 mm)"]
    fresh = {s["name"]: s["scale"] for s in load_settings(path="does-not-exist.json")["daq"]["sensors"]}
    assert fresh["LVDT 1"] == fresh["LVDT 2"] == 10.0
