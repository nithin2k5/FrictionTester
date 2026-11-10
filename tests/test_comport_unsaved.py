"""COM Port page: leaving with unsaved edits asks first."""

import copy
import os
import sys
import tkinter as tk

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def page(monkeypatch):
    from friction_tester.config import DEFAULT_SETTINGS
    from friction_tester.devices import Loadcell, NIDaq, XGBPlc
    from friction_tester.ui import comport_page
    saved = []
    monkeypatch.setattr(comport_page, "save_settings", lambda s: saved.append(copy.deepcopy(s)))
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.withdraw()
    p = comport_page.ComPortPage(root, copy.deepcopy(DEFAULT_SETTINGS), XGBPlc(), NIDaq(),
                                 [Loadcell(1), Loadcell(2)])
    p.saved = saved
    yield p
    root.destroy()


def test_edit_marks_unsaved_and_save_clears_it(page):
    assert not page.unsaved_changes()
    page.lc_rows[1]["id"].insert(0, "01")
    assert page.unsaved_changes()
    assert page.save() and page.saved and page.saved[-1]["loadcells"][1]["id"] == 1
    assert not page.unsaved_changes()
    page.ent_daq_rate.delete(0, "end")
    page.ent_daq_rate.insert(0, "2000")
    assert page.unsaved_changes()
    page.ent_daq_rate.delete(0, "end")
    page.ent_daq_rate.insert(0, "1000")                     # back to the saved value
    assert not page.unsaved_changes()


def test_leaving_asks_and_no_keeps_the_page(page, monkeypatch):
    from friction_tester.ui import app as app_mod
    asked = []
    monkeypatch.setattr(app_mod.messagebox, "askyesno", lambda *a, **k: asked.append(a) or False)
    fake = type("A", (), {"page": page, "root": page.master})()
    page.lc_rows[0]["id"].insert(0, "05")
    assert app_mod.App.confirm_leave(fake) is False and "not saved" in asked[0][1]
    page.save()
    assert app_mod.App.confirm_leave(fake) is True and len(asked) == 1


@pytest.fixture
def model_page(monkeypatch):
    from friction_tester import db
    from friction_tester.config import DEFAULT_SETTINGS
    from friction_tester.ui import model_page as mp
    monkeypatch.setattr(db, "init_database", lambda: None)
    monkeypatch.setattr(db, "list_models", lambda *a, **k: [])
    # database work runs in place (no worker thread: tests have no Tk main loop)
    monkeypatch.setattr(mp.ModelSettingsPage, "_run",
                        lambda self, work, _text, done=None: done(work()) if done else work())
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.withdraw()
    p = mp.ModelSettingsPage(root, copy.deepcopy(DEFAULT_SETTINGS))
    yield p
    root.destroy()


def test_model_page_unsaved_only_while_editing_and_changed(model_page):
    from friction_tester import db
    p = model_page
    assert not p.unsaved_changes()                          # idle, empty form
    p.new_model()
    assert not p.unsaved_changes()                          # NEW alone changes nothing
    p.fields["MM_MODEL"].insert(0, "M1")
    assert p.unsaved_changes()
    row = {c: None for c in p.fields}
    row.update({"MM_MODEL": "M1", db.cond_col("SELECT", "STROKE"): 10})
    p._rows = {"i1": row}
    p.tree.insert("", "end", iid="i1", values=("M1",))
    p.tree.selection_set("i1")
    p._on_select()                                          # a model loaded = saved state
    assert not p.unsaved_changes()
    w = p.fields[db.cond_col("SELECT", "STROKE")]
    w.delete(0, "end")
    w.insert(0, "12")
    assert p.unsaved_changes() and "model settings" in p.UNSAVED_TEXT
    w.delete(0, "end")
    w.insert(0, "10")
    assert not p.unsaved_changes()


def _two_models(p):
    from friction_tester import db
    rows = []
    for name, stroke in (("M1", 10), ("M2", 20)):
        r = {c: None for c in p.fields}
        r.update({"MM_MODEL": name, db.cond_col("SELECT", "STROKE"): stroke})
        rows.append(r)
    p._show_models(rows)
    return [iid for iid in p.tree.get_children()]


def _select(p, iid):
    p.tree.selection_set(iid)
    p._on_select()               # the <<TreeviewSelect>> handler (no main loop in tests)


def test_switching_model_or_new_with_unsaved_edits_asks(model_page, monkeypatch):
    from friction_tester import db
    from friction_tester.ui import model_page as mp
    p = model_page
    answers = []
    monkeypatch.setattr(mp.messagebox, "askyesno", lambda *a, **k: answers.pop(0))
    m1, m2 = _two_models(p)
    _select(p, m1)
    stroke = p.fields[db.cond_col("SELECT", "STROKE")]
    stroke.delete(0, "end")
    stroke.insert(0, "15")

    answers.append(False)                                   # No: stay on M1 with the edit
    _select(p, m2)
    assert p._editing == "M1" and stroke.get() == "15" and p.tree.selection() == (m1,)
    _select(p, m1)                                          # the restored selection: no question
    assert stroke.get() == "15" and not answers

    answers.append(False)                                   # NEW, No: edit kept
    p.new_model()
    assert p._mode == "edit" and stroke.get() == "15"

    answers.append(True)                                    # Yes: M2 opens, edit discarded
    _select(p, m2)
    assert p._editing == "M2" and stroke.get() == "20" and not p.unsaved_changes()

    p.new_model()                                           # nothing changed: no question
    assert p._mode == "new" and not answers


def test_help_page_renders_every_chapter_and_searches():
    from friction_tester.ui import help_page as hp
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.withdraw()
    try:
        page = hp.HelpPage(root)
        for i in range(len(hp.CHAPTERS)):
            page.select(i)
            assert page.title_lbl.cget("text") == hp.CHAPTERS[i][0] and page.inner.winfo_children()
        page.search.delete(0, "end")
        page.search.insert(0, "F-40")
        page._build_toc(page.search_term())
        names = [hp.CHAPTERS[i][0] for i, _l in page.toc_items]
        assert "COM Setting" in names and "About This Machine" not in names
        page.step(1)
        assert page.current == len(hp.CHAPTERS) - 1          # already on the last chapter: stays
    finally:
        root.destroy()
