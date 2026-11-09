"""tools/plc_scan.py against a fake XGB whose Cnet areas start at known places (no hardware)."""

import os
import random
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tools"))

import plc_scan  # noqa: E402


class FakeXgb:
    """P area of 1024 words; each Modbus table is a window onto P starting at a given word."""

    def __init__(self, bases, seed=1):
        rnd = random.Random(seed)
        self.P = [0] * 1024
        for w in rnd.sample(range(1024), 40):
            self.P[w] = rnd.randint(1, 0xFFFF)
        self.P[0] = 0b1110111001001001                 # inputs pattern seen on the machine
        self.bases = bases                              # table -> P word where it starts
        self.calls = 0

    def read_table(self, table, offset, qty):
        self.calls += 1
        base = self.bases[table]
        if table in ("coils", "discrete_inputs"):
            first = base * 16 + offset
            if offset + qty > (1024 - base) * 16:
                raise RuntimeError("exception 02")
            return [bool((self.P[(first + i) // 16] >> ((first + i) % 16)) & 1) for i in range(qty)]
        if offset + qty > 1024 - base:
            raise RuntimeError("exception 02")
        return self.P[base + offset: base + offset + qty]


def test_scan_finds_extent_and_base_of_every_table(tmp_path):
    plc = FakeXgb({"discrete_inputs": 0, "coils": 10, "input_registers": 20, "holding_registers": 30})
    out = tmp_path / "scan.json"
    lines = []
    res = plc_scan.run(plc.read_table, out, log=lines.append)
    t = res["tables"]
    assert t["discrete_inputs"]["count"] == 16384
    assert t["coils"]["count"] == 16224                 # the machine's FC01 extent (P word 10)
    assert t["input_registers"]["count"] == 1004        # the machine's FC04 extent (P word 20)
    assert t["holding_registers"]["count"] == 994
    assert t["coils"]["p_shift"] == 10
    assert t["input_registers"]["p_shift"] == 20
    assert t["holding_registers"]["p_shift"] == 30
    text = "\n".join(lines)
    assert "starts at P0020" in text and "P0010" in text and out.exists()


def test_non_p_tables_and_all_zero_tables_are_not_aligned():
    class Mixed(FakeXgb):
        def read_table(self, table, offset, qty):
            if table == "input_registers":
                if offset + qty > 1000:
                    raise RuntimeError("exception 02")
                return [(offset + i) * 7 + 1 for i in range(qty)]     # D-like data, not P
            if table == "holding_registers":
                if offset + qty > 1000:
                    raise RuntimeError("exception 02")
                return [0] * qty
            return super().read_table(table, offset, qty)

    plc = Mixed({"discrete_inputs": 0, "coils": 0, "input_registers": 0, "holding_registers": 0})
    lines = []
    res = plc_scan.run(plc.read_table, None, log=lines.append)
    assert "p_shift" not in res["tables"]["input_registers"]
    assert any("no match with the P image" in s for s in lines)
    assert any("all zero" in s for s in lines)


def test_extent_helpers():
    assert plc_scan.find_extent(lambda off: off <= 993) == 993
    assert plc_scan.find_extent(lambda off: False) is None
    assert plc_scan.find_extent(lambda off: True) == plc_scan.TOP
    assert plc_scan.pack_bits([True] + [False] * 15 + [None] * 16) == [1, None]
    assert plc_scan.size_candidates(1004, "word")[0] == "P0020"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
