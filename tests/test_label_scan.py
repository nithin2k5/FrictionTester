"""Scanned labels: traceability code and the model check (no hardware, no database)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from friction_tester.label_scan import ScanError, parse  # noqa: E402

M = dict(alc="AB12", model="58810-K1000")


def test_the_three_label_kinds():
    full = parse("AB1258810-K1000TR000123", **M)
    assert full["trace"] == "TR000123" and full["format"] == "{ALC}{MODEL}{TRACE}"
    assert full["alc"] == "AB12" and full["model"] == "58810-K1000"
    alc = parse("AB12TR000124", **M)
    assert alc["trace"] == "TR000124" and alc["format"] == "{ALC}{TRACE}" and alc["model"] == ""
    model = parse("58810-K1000TR000125", **M)
    assert model["trace"] == "TR000125" and model["format"] == "{MODEL}{TRACE}" and model["alc"] == ""


def test_separators_and_case():
    assert parse("ab12 58810-k1000 TR9", **M)["trace"] == "TR9"
    assert parse("AB12-TR9", **M)["trace"] == "TR9"
    assert parse("AB12;58810-K1000;TR9", **M)["trace"] == "TR9"


def test_a_label_of_another_model_is_refused():
    with pytest.raises(ScanError, match="does not match the loaded model"):
        parse("ZZ99TR000123", **M)
    with pytest.raises(ScanError, match="does not match"):
        parse("AB12", **M)                         # no traceability code
    with pytest.raises(ScanError, match="empty"):
        parse("  ", **M)


def test_formats_from_settings_and_a_model_without_alc():
    fmts = ["{TRACE}/{PART}"]
    assert parse("X77/PN-1", fmts, part="PN-1")["trace"] == "X77"
    assert parse("58810-K1000TR1", alc="", model="58810-K1000")["format"] == "{MODEL}{TRACE}"
    with pytest.raises(ScanError, match="no ALC Code or model number"):
        parse("AB12TR1", ["{ALC}{TRACE}"], alc="", model="")
    with pytest.raises(ScanError, match="no {TRACE}"):
        parse("AB12", ["{ALC}"], **M)


def test_label_printer_fills_the_trace_fields():
    from friction_tester import label_printer
    f = label_printer.fields({"label_shift": "AB12TR2", "trace_shift": "TR2", "cable": "SHIFT", "verdict": "OK"})
    assert f["TRACE"] == "TR2" and f["TRACE_SHIFT"] == "TR2" and f["TRACE_SELECT"] == ""
    assert f["LABEL"] == "AB12TR2" and f["CABLE"] == "SHIFT"
    out = label_printer.render(b"^FD{TRACE}^FS {CABLE}", f)
    assert out == b"^FDTR2^FS SHIFT"


def test_settings_v11_turns_scanning_on():
    from friction_tester.config import migrate
    new = migrate({"settings_version": 10, "test": {"skip_scan": True, "label_rule": {"mode": "none"}}})
    assert new["settings_version"] == 11 and new["test"]["skip_scan"] is False
    assert "label_rule" not in new["test"] and "scanned label" in new["migrated"][0]


# ------------------------------------------------------------------ Test Console scan logic
class _Entry:
    def __init__(self, text=""):
        self.text = text

    def get(self):
        return self.text

    def delete(self, *_a):
        self.text = ""

    def insert(self, _i, text):
        self.text = text


class _Ctl:
    def __init__(self):
        self.sent = []

    def submit(self, *cmd):
        self.sent.append(cmd)


class _SyncThread:
    def __init__(self, target, daemon=None):
        self.target = target

    def start(self):
        self.target()


def _console(monkeypatch, scanned=None):
    """The scan methods of TestConsolePage on a stand-in page (fake DB, printer, Tk)."""
    from friction_tester import db
    from friction_tester.ui import test_console as tc
    taken = {} if scanned is None else scanned
    errors = []

    def claim(trace, label, model="", model_type="", cable="", alc=""):
        if trace in taken:
            raise db.DuplicateTrace(trace, {"TS_RESULT": taken[trace], "TS_CABLE": "select"})
        taken[trace] = "TESTING"

    monkeypatch.setattr(db, "init_database", lambda: None)
    monkeypatch.setattr(db, "claim_trace", claim)
    monkeypatch.setattr(db, "finish_trace", lambda t, uid, res: taken.__setitem__(t, res))
    monkeypatch.setattr(db, "release_trace", lambda t: taken.pop(t, None))
    monkeypatch.setattr(tc.threading, "Thread", _SyncThread)
    monkeypatch.setattr(tc.messagebox, "showerror", lambda title, text, parent=None: errors.append(title))

    class Page:
        for name in ("on_scan", "_dispatch_scan", "_parse_label", "_claim_scan", "_log_scan",
                     "_finish_scans", "_release_scans", "_skip_scan", "_pc_part_done"):
            locals()[name] = getattr(tc.TestConsolePage, name)

        def __init__(self):
            self.settings = {"test": {"select": {"model": "58810-K1000"}, "model_type": "AT", "part_number": "",
                                      "model_info": {"MM_ALC_CODE": "AB12"}, "skip_scan": False,
                                      "scan": {"formats": ["{ALC}{MODEL}{TRACE}", "{ALC}{TRACE}",
                                                           "{MODEL}{TRACE}"], "unique_trace": True}}}
            self.scan_entries = {"select": _Entry(), "shift": _Entry()}
            self._claims, self._scan_busy, self._alive = {}, False, True
            self._flow, self.pc, self.shared = "at_scan_select", True, False
            self.ctl, self.status, self.printed = _Ctl(), [], []

        def after(self, _ms, fn):
            fn()

        def _set_status(self, text, *_a, **_k):
            self.status.append(text)

        def _print_label(self, record, what="Part OK"):
            if record.get("verdict") == "OK":
                self.printed.append((record.get("cable"), record.get("trace_select") or record.get("trace_shift")))

        def _append_result_row(self, record):
            pass

        def scan(self, key, text):
            self.scan_entries[key].text = text
            self.on_scan(key)

    page = Page()
    page.store = type("Store", (), {"submit": lambda self, rec: None})()
    return page, taken, errors


def test_console_at_scans_start_only_unique_matching_labels_and_print_per_cable(monkeypatch):
    page, taken, errors = _console(monkeypatch, scanned={"TR-OLD": "OK"})
    page.scan("select", "ZZ99TR1")                                  # another model's label
    assert errors == ["Wrong label"] and not page.ctl.sent and page.scan_entries["select"].get() == ""
    page.scan("select", "AB12TR-OLD")                               # tested before
    assert errors[-1] == "Label already tested" and not page.ctl.sent
    page.scan("select", "AB12TR-S1")
    assert page.ctl.sent == [("scan", "select", "AB12TR-S1", "TR-S1")] and taken["TR-S1"] == "TESTING"
    page._flow = "at_scan_shift"
    page.scan("shift", "AB12TR-S1")                                 # SELECT's code again for SHIFT
    assert errors[-1] == "Label already tested" and len(page.ctl.sent) == 1
    page.scan("shift", "58810-K1000TR-H1")
    assert page.ctl.sent[-1] == ("scan", "shift", "58810-K1000TR-H1", "TR-H1")
    page._flow = "idle"
    page._pc_part_done({"part_id": "P1", "type": "AT", "model": "58810-K1000", "verdict": "NG",
                        "trace_select": "TR-S1", "trace_shift": "TR-H1",
                        "stages": [{"stage": "select", "verdict": "OK"}, {"stage": "shift", "verdict": "NG"}]})
    assert taken["TR-S1"] == "OK" and taken["TR-H1"] == "NG"
    assert page.printed == []                                       # SHIFT NG: no label at all


def test_console_at_prints_both_labels_only_when_both_cables_pass(monkeypatch):
    page, taken, errors = _console(monkeypatch)
    rec = {"part_id": "P4", "type": "AT", "model": "58810-K1000", "trace_select": "TR-S4",
           "trace_shift": "TR-H4", "label_select": "AB12TR-S4", "label_shift": "AB12TR-H4"}
    page._pc_part_done(dict(rec, verdict="NG", stages=[{"stage": "select", "verdict": "NG"},
                                                       {"stage": "shift", "verdict": "OK"}]))
    assert page.printed == []                                       # SELECT NG: none either
    page._pc_part_done(dict(rec, verdict="OK", stages=[{"stage": "select", "verdict": "OK"},
                                                       {"stage": "shift", "verdict": "OK"}]))
    assert page.printed == [("SELECT", "TR-S4"), ("SHIFT", "TR-H4")]


def test_console_mt_one_label_for_both_and_stop_frees_an_untested_code(monkeypatch):
    page, taken, errors = _console(monkeypatch)
    page.settings["test"]["model_type"], page._flow = "MT", "mt_scan"
    page.scan("select", "AB1258810-K1000TR-M1")
    assert page.ctl.sent == [("scan", "select", "AB1258810-K1000TR-M1", "TR-M1")]
    assert page.scan_entries["shift"].get() == "AB1258810-K1000TR-M1"
    page._flow = "idle"
    page._pc_part_done({"part_id": "P2", "type": "MT", "verdict": "OK", "trace_select": "TR-M1",
                        "trace_shift": "TR-M1", "stages": [{"stage": "select", "verdict": "OK"},
                                                           {"stage": "shift", "verdict": "OK"}]})
    assert taken == {"TR-M1": "OK"} and page.printed == [("BOTH", "TR-M1")]
    # AT: SHIFT scanned, then STOP before its test started -> the SHIFT code is not kept
    page.settings["test"]["model_type"], page._flow = "AT", "at_scan_shift"
    page.scan("shift", "AB12TR-H9")
    page._pc_part_done({"part_id": "P3", "type": "AT", "verdict": "ABORTED",
                        "stages": [{"stage": "select", "verdict": "OK"}]})
    assert "TR-H9" not in taken


def test_console_scan_is_refused_when_the_database_cannot_check(monkeypatch):
    from friction_tester import db
    page, taken, errors = _console(monkeypatch)

    def down(*_a, **_k):
        raise OSError("MySQL down")

    monkeypatch.setattr(db, "claim_trace", down)
    page.scan("select", "AB12TR-S1")
    assert not page.ctl.sent and "needs the database" in page.status[-1]
