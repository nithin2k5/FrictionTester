"""Model Settings page — create, edit and delete rows of TBL_MODEL_MASTER.

Top: three equal boxes — Model Information (Model, Part Number, Customer
Name, ALC Code, PRN Template), then the SELECT CABLE and SHIFT CABLE test
conditions, laid out like the Test Console (Speed / Delay / Stroke beside
Position 1-3, LOAD underneath). Below: the model list, searchable; click a
row to edit it.

PRN Template choices are the .prn label files in the prn_templates folder.

Database calls run on a worker thread so a slow or missing MySQL server
never freezes the window.
"""

import os
import threading
import tkinter as tk
from decimal import Decimal
from tkinter import messagebox, ttk

from . import theme
from .. import db

CARD_BG = theme.PANEL
HEAD_BG = "#171c25"
SECTION_BG = "#141922"
PRN_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                       "prn_templates")

# Model list columns: (key, heading, width). SELECT / SHIFT show a condition summary.
MODEL_TYPES = ["", "MT", "AT"]      # blank = not set

LIST_COLS = [("MM_MODEL", "Model", 150), ("MM_TYPE", "Type", 60), ("MM_PART_NUMBER", "Part Number", 130),
             ("MM_CUSTOMER_NAME", "Customer", 130), ("MM_ALC_CODE", "ALC Code", 90),
             ("MM_PRN_TEMPLATE", "PRN Template", 170)] + [
    (key, name, 230) for key, name in db.TESTS]


def prn_templates():
    """Names of the .prn label templates in the prn_templates folder."""
    try:
        return sorted(f for f in os.listdir(PRN_DIR) if f.lower().endswith(".prn"))
    except FileNotFoundError:
        return []


def _fmt(v):
    """Display a DB value: drop trailing zeros from decimals, blank for an empty value."""
    if v is None:
        return ""
    if isinstance(v, (Decimal, float)):
        return f"{float(v):g}"
    return str(v)


def _condition_summary(row, test):
    c = lambda attr: _fmt(row.get(db.cond_col(test, attr)))
    if all(row.get(db.cond_col(test, a)) is None for a, *_ in db.CONDITION_FIELDS):
        return ""
    return f"{c('SPEED')} mm/s · {c('STROKE')} mm · {c('LOAD_MIN')} ~ {c('LOAD_MAX')} kgf"


class ModelSettingsPage(tk.Frame):
    def __init__(self, master, settings):
        super().__init__(master, bg=theme.BG)
        self.settings = settings
        self._alive = True
        self.bind("<Destroy>", lambda e: setattr(self, "_alive", False) if e.widget is self else None)

        self._build_toolbar()
        self._build_form().pack(fill="x", pady=(10, 0))
        self._build_list().pack(fill="both", expand=True, pady=(12, 0))
        self.status = tk.Label(self, text="", bg=theme.BG, fg=theme.MUTED, font=(theme.FONT, 10), anchor="w")
        self.status.pack(fill="x", pady=(6, 0))

        # Form mode: "idle" (locked, nothing selected), "new" (adding a model)
        # or "edit" (the selected model; its name is the key, so it's locked).
        self._mode = "idle"
        self._editing = None      # model name being edited
        self._busy = False
        self._clear_form()
        self._apply_mode()
        self._run(self._init_and_load, "Connecting to database…", self._show_models)

    # ============================================================ layout
    def _label(self, parent, text, fg="white", size=10, bold=False, **kw):
        return tk.Label(parent, text=text, bg=parent["bg"], fg=fg,
                        font=(theme.FONT, size, "bold") if bold else (theme.FONT, size), **kw)

    def _button(self, parent, text, command, bg, fg="white"):
        return tk.Button(parent, text=text, command=command, bg=bg, fg=fg, activebackground=bg,
                         activeforeground=fg, font=(theme.FONT, 10, "bold"), bd=0, padx=16, pady=5,
                         cursor="hand2", disabledforeground="#8a8f99")

    def _entry(self, parent, width, justify="left"):
        e = tk.Entry(parent, bg=theme.ENTRY_BG, fg="white", insertbackground="white", justify=justify,
                     font=(theme.FONT, 11), width=width, bd=1, relief="solid",
                     highlightbackground=theme.BORDER, highlightthickness=1,
                     disabledbackground="#151920", disabledforeground="#8a93a3")
        return e

    def _card(self, title, subtitle=""):
        card = tk.Frame(self, bg=CARD_BG, highlightbackground=theme.BORDER, highlightthickness=1)
        head = tk.Frame(card, bg=HEAD_BG)
        head.pack(fill="x")
        tk.Frame(head, bg=theme.TITLE_FG, width=4).pack(side="left", fill="y")
        self._label(head, title, size=12, bold=True).pack(side="left", padx=(10, 8), pady=7)
        if subtitle:
            self._label(head, subtitle, fg=theme.MUTED, size=9).pack(side="left", pady=7)
        return card, head

    def _build_toolbar(self):
        bar = tk.Frame(self, bg=theme.BG)
        bar.pack(fill="x")
        self._label(bar, "Model Master", size=15).pack(side="left")
        self._label(bar, db.TABLE, fg=theme.MUTED, size=10).pack(side="left", padx=12, pady=(4, 0))
        self.btn_delete = self._button(bar, "DELETE", self.delete_model, theme.FAIL)
        self.btn_delete.pack(side="right", padx=(8, 0))
        self.btn_save = self._button(bar, "SAVE", self.save_model, theme.SAVE)
        self.btn_save.pack(side="right", padx=(8, 0))
        self.btn_new = self._button(bar, "NEW", self.new_model, "#4a505c")
        self.btn_new.pack(side="right", padx=(8, 0))

    def _section(self, parent, title, subtitle):
        """A bordered box with a title strip; returns (box, body)."""
        box = tk.Frame(parent, bg=SECTION_BG, highlightbackground=theme.BORDER, highlightthickness=1)
        head = tk.Frame(box, bg=HEAD_BG)
        head.pack(fill="x")
        tk.Frame(head, bg=theme.TITLE_FG, width=4).pack(side="left", fill="y")
        self._label(head, title, size=11, bold=True).pack(side="left", padx=(10, 8), pady=6)
        self._label(head, subtitle, fg=theme.MUTED, size=9).pack(side="left", pady=6)
        body = tk.Frame(box, bg=SECTION_BG)
        body.pack(fill="both", expand=True, padx=12, pady=(10, 12))
        return box, body

    def _build_form(self):
        """Model Information | SELECT CABLE | SHIFT CABLE, three equal boxes."""
        top = tk.Frame(self, bg=theme.BG)
        self.fields = {}
        boxes = [self._build_info(top)] + [self._build_condition(top, key, name) for key, name in db.TESTS]
        # With a shared uniform group, column widths follow the weights: the
        # condition boxes hold two columns of fields, so they get more room.
        for i, (box, weight) in enumerate(zip(boxes, (4, 5, 5))):
            box.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 6, 0 if i == len(boxes) - 1 else 6))
            top.columnconfigure(i, weight=weight, uniform="form")
        return top

    def _build_info(self, parent):
        box, body = self._section(parent, "Model Information", "")
        body.columnconfigure(1, weight=1)
        for r, (col, label, _sql) in enumerate(db.HEADER_FIELDS):
            self._label(body, label).grid(row=r, column=0, sticky="w", pady=3)
            if col == "MM_PRN_TEMPLATE":
                w = ttk.Combobox(body, font=(theme.FONT, 10), state="readonly")
                # Re-list the folder each time the drop-down opens, so new files show up.
                w.config(postcommand=lambda w=w: w.config(values=[""] + prn_templates()))
            elif col == "MM_TYPE":
                w = ttk.Combobox(body, font=(theme.FONT, 10), state="readonly", values=MODEL_TYPES, width=8)
            else:
                w = self._entry(body, 20)
            w.grid(row=r, column=1, sticky="w" if col == "MM_TYPE" else "ew", padx=(12, 0), pady=3)
            self.fields[col] = w
        return box

    def _build_condition(self, parent, test, name):
        """One test's condition, in the Test Console's layout:

            Speed  [  ] mm/Sec    Position 1 [  ] mm
            Delay  [  ] Sec       Position 2 [  ] mm
            Stroke [  ] mm        Position 3 [  ] mm
            LOAD   [  ]  ~  [  ] kgf
        """
        box, body = self._section(parent, name, "Test Condition")
        left = (("SPEED", "Speed"), ("DELAY_TIME", "Delay"), ("STROKE", "Stroke"))
        right = (("POSITION_1", "Position 1"), ("POSITION_2", "Position 2"), ("POSITION_3", "Position 3"))
        units = {attr: unit for attr, _l, unit, _s in db.CONDITION_FIELDS}

        def cell(row, col, attr, label):
            self._label(body, label).grid(row=row, column=col, sticky="w", pady=3)
            e = self._entry(body, 6, justify="center")
            e.grid(row=row, column=col + 1, sticky="w", padx=(8, 4), pady=3)
            self._label(body, units[attr], fg=theme.MUTED, size=9).grid(
                row=row, column=col + 2, sticky="w", padx=(0, 12 if col == 0 else 0))
            self.fields[db.cond_col(test, attr)] = e

        for r, ((la, ll), (ra, rl)) in enumerate(zip(left, right)):
            cell(r, 0, la, ll)
            cell(r, 3, ra, rl)

        # LOAD min ~ max, min under the left entries and max under the right ones
        self._label(body, "LOAD", bold=True).grid(row=3, column=0, sticky="w", pady=(8, 3))
        lo = self._entry(body, 6, justify="center")
        lo.grid(row=3, column=1, sticky="w", padx=(8, 4), pady=(8, 3))
        self._label(body, "~", fg=theme.MUTED).grid(row=3, column=3, sticky="e", pady=(8, 3))
        hi = self._entry(body, 6, justify="center")
        hi.grid(row=3, column=4, sticky="w", padx=(8, 4), pady=(8, 3))
        self._label(body, "kgf", fg=theme.MUTED, size=9).grid(row=3, column=5, sticky="w", pady=(8, 3))
        self.fields[db.cond_col(test, "LOAD_MIN")] = lo
        self.fields[db.cond_col(test, "LOAD_MAX")] = hi
        return box

    def _build_list(self):
        card, head = self._card("Models")
        self.search = self._entry(head, 22)
        self.search.config(font=(theme.FONT, 10))
        self.search.pack(side="right", padx=8)
        self.search.bind("<KeyRelease>", lambda _e: self._schedule_search())
        self._label(head, "Search", fg=theme.MUTED, size=9).pack(side="right")

        box = tk.Frame(card, bg=CARD_BG)
        box.pack(fill="both", expand=True, padx=8, pady=8)
        self.tree = ttk.Treeview(box, columns=[c for c, _, _ in LIST_COLS], show="headings",
                                 selectmode="browse", style="Dark.Treeview")
        for col, heading, width in LIST_COLS:
            self.tree.heading(col, text=heading)
            self.tree.column(col, width=width, minwidth=50,
                             anchor="w" if col in ("MM_MODEL", "MM_PRN_TEMPLATE") else "center")
        sb = ttk.Scrollbar(box, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.tag_configure("odd", background="#141922")
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._on_select())
        self._rows = {}
        self._search_job = None
        return card

    # ============================================================ form data
    def _set_field(self, col, value):
        w = self.fields[col]
        state = str(w.cget("state"))
        w.config(state="normal")          # a disabled widget ignores set/insert
        if isinstance(w, ttk.Combobox):
            w.set(_fmt(value))
        else:
            w.delete(0, "end")
            w.insert(0, _fmt(value))
        w.config(state=state)

    def _clear_form(self):
        for col in db.COLUMNS:
            self._set_field(col, None)

    def _apply_mode(self):
        """Lock/unlock the form and enable only the buttons that make sense."""
        mode, busy = self._mode, self._busy
        for col, w in self.fields.items():
            if mode == "idle" or (mode == "edit" and col == "MM_MODEL"):
                w.config(state="disabled")
            else:
                w.config(state="readonly" if isinstance(w, ttk.Combobox) else "normal")
        self.btn_new.config(state="disabled" if busy else "normal")
        self.btn_save.config(state="normal" if mode in ("new", "edit") and not busy else "disabled")
        self.btn_delete.config(state="normal" if mode == "edit" and not busy else "disabled")

    def _read_form(self):
        row = {}
        for col in db.COLUMNS:
            text = self.fields[col].get().strip()
            if col in db.NUMERIC:
                if text == "":
                    row[col] = None
                    continue
                try:
                    row[col] = float(text)
                except ValueError:
                    raise ValueError(f"{db.LABELS[col]} must be a number")
            else:
                row[col] = text or None
        if not row["MM_MODEL"]:
            raise ValueError("Model is required")
        for key, name in db.TESTS:
            lo, hi = row[db.cond_col(key, "LOAD_MIN")], row[db.cond_col(key, "LOAD_MAX")]
            if lo is not None and hi is not None and lo > hi:
                raise ValueError(f"{name}: LOAD min is above LOAD max")
        return row

    def new_model(self):
        """NEW: an empty, unlocked form for a new model."""
        self.tree.selection_remove(self.tree.selection())
        self._mode, self._editing = "new", None
        self._apply_mode()
        self._clear_form()
        self.fields["MM_MODEL"].focus_set()
        self._set_status("New model: fill in the fields and press SAVE", theme.MUTED)

    def _on_select(self):
        """Clicking a model opens it for editing."""
        sel = self.tree.selection()
        if not sel or sel[0] not in self._rows or self._busy:
            return
        row = self._rows[sel[0]]
        self._mode, self._editing = "edit", row["MM_MODEL"]
        self._apply_mode()
        for col in db.COLUMNS:
            self._set_field(col, row.get(col))

    # ============================================================ database
    def _run(self, work, busy_text, done=None):
        """Run work() on a thread; done(result) back on the Tk thread.

        Buttons are disabled while it runs, so a slow database can't be
        sent the same action twice.
        """
        self._set_status(busy_text, theme.BUSY)
        self._busy = True
        self._apply_mode()

        def worker():
            try:
                result, error = work(), None
            except Exception as exc:
                result, error = None, exc
            if self._alive:
                self.after(0, lambda: self._finish(result, error, done))

        threading.Thread(target=worker, daemon=True).start()

    def _finish(self, result, error, done):
        if not self._alive:
            return
        self._busy = False
        self._apply_mode()
        if error is not None:
            self._set_status(f"Database error: {error}", theme.FAIL)
            return
        if done:
            done(result)

    def _set_status(self, text, color=theme.MUTED):
        self.status.config(text=text, fg=color)

    def _init_and_load(self):
        db.init_database()
        return db.list_models()

    def _show_models(self, rows, select=None):
        self.tree.delete(*self.tree.get_children())
        self._rows = {}
        for i, r in enumerate(rows):
            values = [_condition_summary(r, c) if c in db.TEST_KEYS else _fmt(r.get(c))
                      for c, _, _ in LIST_COLS]
            iid = self.tree.insert("", "end", values=values, tags=("odd",) if i % 2 else ())
            self._rows[iid] = r
            if select and r["MM_MODEL"] == select:
                self.tree.selection_set(iid)
                self.tree.see(iid)
        self._set_status(f"{len(rows)} model(s) in {db.TABLE}", theme.PASS)

    def refresh(self, select=None):
        term = self.search.get().strip()
        self._run(lambda: db.list_models(term), "Loading models…",
                  lambda rows: self._show_models(rows, select))

    def _schedule_search(self):
        if self._search_job:
            self.after_cancel(self._search_job)
        self._search_job = self.after(300, self.refresh)

    def save_model(self):
        """SAVE: add the new model, or update the one being edited."""
        if self._mode not in ("new", "edit"):
            return
        try:
            row = self._read_form()
        except ValueError as exc:
            messagebox.showerror("Save Model", str(exc), parent=self)
            return
        model = row["MM_MODEL"]

        def saved(_):
            self._set_status(f"Saved {model}", theme.PASS)
            self.refresh(select=model)        # selecting it reopens it for editing

        def write():
            self._run(lambda: db.save_model(row), f"Saving {model}…", saved)

        if self._mode == "edit":
            write()
            return

        # New model: don't silently overwrite one that already exists.
        def checked(existing):
            if existing and not messagebox.askyesno(
                    "Save Model", f"Model {model} already exists.\nOverwrite it with these values?", parent=self):
                self._set_status(f"{model} not saved", theme.MUTED)
                return
            write()

        self._run(lambda: db.get_model(model), f"Checking {model}…", checked)

    def delete_model(self):
        """DELETE: remove the model being edited (the selected one)."""
        model = self._editing
        if self._mode != "edit" or not model:
            return
        if not messagebox.askyesno("Delete Model", f"Delete model {model} from {db.TABLE}?", parent=self):
            return

        def done(count):
            self.tree.selection_remove(self.tree.selection())
            self._mode, self._editing = "idle", None
            self._apply_mode()
            self._clear_form()
            self.refresh()
            self._set_status(f"Deleted {model}" if count else f"{model} was not in the table",
                             theme.PASS if count else theme.MUTED)

        self._run(lambda: db.delete_model(model), f"Deleting {model}…", done)
