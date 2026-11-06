"""Model Settings page — create, edit and delete rows of TBL_MODEL_MASTER.

Left: the model list (searchable). Right: the selected model laid out like the
LabVIEW model sheet — header fields, then "- Test Condition -". APPLY TO TEST
CONSOLE copies the model's test conditions into the matching cable panel
(SELECT CABLE or SHIFT CABLE).

Database calls run on a worker thread so a slow or missing MySQL server
never freezes the window.
"""

import datetime
import threading
import tkinter as tk
from decimal import Decimal
from tkinter import messagebox, ttk

from . import theme
from .. import db
from ..config import save_settings

CARD_BG = theme.PANEL
HEAD_BG = "#171c25"
CABLES = ["SELECT CABLE", "SHIFT CABLE"]
RESULTS = ["", "O.K", "N.G"]

# Model list columns: (db column, heading, width)
LIST_COLS = [("MM_MODEL", "Model", 150), ("MM_TEST_CABLE", "Test Cable", 120),
             ("MM_SPEED", "Speed", 55), ("MM_STROKE", "Stroke", 55),
             ("LOAD", "LOAD (kgf)", 90), ("MM_DATE", "Date", 160), ("MM_TEST_RESULT", "Result", 55)]


def _fmt(v):
    """Display a DB value: drop trailing zeros from decimals, blank for None."""
    if v is None:
        return ""
    if isinstance(v, (Decimal, float)):
        return f"{float(v):g}"
    if isinstance(v, datetime.datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    return str(v)


class ModelSettingsPage(tk.Frame):
    def __init__(self, master, settings):
        super().__init__(master, bg=theme.BG)
        self.settings = settings
        self._alive = True
        self.bind("<Destroy>", lambda e: setattr(self, "_alive", False) if e.widget is self else None)

        self._build_toolbar()
        body = tk.Frame(self, bg=theme.BG)
        body.pack(fill="both", expand=True, pady=(10, 0))
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        self._build_list(body).grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        self._build_form(body).grid(row=0, column=1, sticky="ns")

        self.status = tk.Label(self, text="", bg=theme.BG, fg=theme.MUTED, font=(theme.FONT, 10), anchor="w")
        self.status.pack(fill="x", pady=(6, 0))

        self.new_model()
        self._run(self._init_and_load, "Connecting to database…", self._show_models)

    # ============================================================ layout
    def _label(self, parent, text, fg="white", size=10, bold=False, **kw):
        return tk.Label(parent, text=text, bg=parent["bg"], fg=fg,
                        font=(theme.FONT, size, "bold") if bold else (theme.FONT, size), **kw)

    def _button(self, parent, text, command, bg, fg="white"):
        return tk.Button(parent, text=text, command=command, bg=bg, fg=fg, activebackground=bg,
                         activeforeground=fg, font=(theme.FONT, 10, "bold"), bd=0, padx=14, pady=5,
                         cursor="hand2", disabledforeground="#8a8f99")

    def _card(self, parent, title, subtitle=""):
        card = tk.Frame(parent, bg=CARD_BG, highlightbackground=theme.BORDER, highlightthickness=1)
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
        self.btn_apply = self._button(bar, "APPLY TO TEST CONSOLE", self.apply_to_console, theme.UPDATE)
        self.btn_apply.pack(side="right", padx=(8, 0))
        self.btn_delete = self._button(bar, "DELETE", self.delete_model, theme.FAIL)
        self.btn_delete.pack(side="right", padx=(8, 0))
        self.btn_save = self._button(bar, "SAVE", self.save_model, theme.SAVE)
        self.btn_save.pack(side="right", padx=(8, 0))
        self._button(bar, "NEW", self.new_model, "#4a505c").pack(side="right", padx=(8, 0))

    def _build_list(self, parent):
        card, head = self._card(parent, "Models")
        self.search = tk.Entry(head, bg=theme.ENTRY_BG, fg="white", insertbackground="white",
                               font=(theme.FONT, 10), width=22, bd=1, relief="solid",
                               highlightbackground=theme.BORDER, highlightthickness=1)
        self.search.pack(side="right", padx=8)
        self.search.bind("<KeyRelease>", lambda _e: self._schedule_search())
        self._label(head, "Search", fg=theme.MUTED, size=9).pack(side="right")

        box = tk.Frame(card, bg=CARD_BG)
        box.pack(fill="both", expand=True, padx=8, pady=8)
        self.tree = ttk.Treeview(box, columns=[c for c, _, _ in LIST_COLS], show="headings",
                                 selectmode="browse", style="Dark.Treeview")
        for col, heading, width in LIST_COLS:
            self.tree.heading(col, text=heading)
            self.tree.column(col, width=width, minwidth=40, anchor="w" if col == "MM_MODEL" else "center")
        sb = ttk.Scrollbar(box, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.tag_configure("odd", background="#141922")
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._on_select())
        self._rows = {}
        self._search_job = None
        return card

    def _build_form(self, parent):
        card, _ = self._card(parent, "Model Details")
        form = tk.Frame(card, bg=CARD_BG)
        form.pack(fill="both", expand=True, padx=14, pady=10)
        self.fields = {}

        def entry(width=16):
            return tk.Entry(form, bg=theme.ENTRY_BG, fg="white", insertbackground="white",
                            font=(theme.FONT, 11), width=width, bd=1, relief="solid",
                            highlightbackground=theme.BORDER, highlightthickness=1,
                            readonlybackground="#151920", disabledbackground="#151920")

        def combo(values, width=14):
            return ttk.Combobox(form, values=values, font=(theme.FONT, 10), width=width, state="readonly")

        row = 0

        def add(col, widget, unit=""):
            nonlocal row
            label = next(lbl for c, lbl, _ in db.FIELDS if c == col)
            self._label(form, label).grid(row=row, column=0, sticky="w", pady=3)
            widget.grid(row=row, column=1, sticky="w", padx=(10, 6), pady=3)
            if unit:
                self._label(form, unit, fg=theme.MUTED, size=9).grid(row=row, column=2, sticky="w")
            self.fields[col] = widget
            row += 1

        add("MM_MODEL", entry(20))
        add("MM_TEST_CABLE", combo(CABLES))
        add("MM_SAVED_TO", entry(20))
        date = entry(20)
        add("MM_DATE", date)
        date.config(state="readonly")
        add("MM_TEST_RESULT", combo(RESULTS, width=8))

        self._label(form, "- Test Condition -", fg=theme.TITLE_FG, bold=True).grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(12, 4))
        row += 1
        for col, _label, unit in db.FIELDS:
            if col in ("MM_SPEED", "MM_DELAY_TIME", "MM_STROKE", "MM_POSITION_1", "MM_POSITION_2",
                       "MM_POSITION_3"):
                e = entry(8)
                e.config(justify="center")
                add(col, e, unit)

        # LOAD min ~ max on one line, as on the sheet
        self._label(form, "LOAD").grid(row=row, column=0, sticky="w", pady=3)
        load = tk.Frame(form, bg=CARD_BG)
        load.grid(row=row, column=1, columnspan=2, sticky="w", padx=(10, 0), pady=3)
        for col, sep in (("MM_LOAD_MIN", "~"), ("MM_LOAD_MAX", "kgf")):
            e = tk.Entry(load, bg=theme.ENTRY_BG, fg="white", insertbackground="white", justify="center",
                         font=(theme.FONT, 11), width=8, bd=1, relief="solid",
                         highlightbackground=theme.BORDER, highlightthickness=1)
            e.pack(side="left")
            self._label(load, sep, fg=theme.MUTED, size=9).pack(side="left", padx=6)
            self.fields[col] = e
        return card

    # ============================================================ form data
    def _set_field(self, col, value):
        w = self.fields[col]
        text = _fmt(value)
        if isinstance(w, ttk.Combobox):
            w.set(text)
            return
        readonly = w.cget("state") == "readonly"
        if readonly:
            w.config(state="normal")
        w.delete(0, "end")
        w.insert(0, text)
        if readonly:
            w.config(state="readonly")

    def _read_form(self):
        row = {}
        for col, label, _ in db.FIELDS:
            if col == "MM_DATE":
                continue
            text = self.fields[col].get().strip()
            if col in db.NUMERIC:
                if text == "":
                    row[col] = None
                    continue
                try:
                    row[col] = float(text)
                except ValueError:
                    raise ValueError(f"{label} must be a number")
            else:
                row[col] = text or None
        if not row["MM_MODEL"]:
            raise ValueError("Model is required")
        if row["MM_LOAD_MIN"] is not None and row["MM_LOAD_MAX"] is not None \
                and row["MM_LOAD_MIN"] > row["MM_LOAD_MAX"]:
            raise ValueError("LOAD min is above LOAD max")
        row["MM_DATE"] = datetime.datetime.now().replace(microsecond=0)
        return row

    def new_model(self):
        """Clear the form, prefilled with the default test conditions."""
        self.tree.selection_remove(self.tree.selection())
        defaults = self.settings["test"]["select"]
        values = {
            "MM_TEST_CABLE": "SELECT CABLE", "MM_TEST_RESULT": "",
            "MM_SPEED": defaults["speed"], "MM_DELAY_TIME": defaults["delay"], "MM_STROKE": defaults["stroke"],
            "MM_POSITION_1": defaults["positions"][0], "MM_POSITION_2": defaults["positions"][1],
            "MM_POSITION_3": defaults["positions"][2],
            "MM_LOAD_MIN": defaults["load_min"], "MM_LOAD_MAX": defaults["load_max"],
        }
        for col in db.COLUMNS:
            self._set_field(col, values.get(col))
        self.fields["MM_MODEL"].focus_set()

    def _on_select(self):
        sel = self.tree.selection()
        if sel and sel[0] in self._rows:
            row = self._rows[sel[0]]
            for col in db.COLUMNS:
                self._set_field(col, row.get(col))

    # ============================================================ database
    def _run(self, work, busy_text, done=None):
        """Run work() on a thread; done(result) back on the Tk thread."""
        self._set_status(busy_text, theme.BUSY)

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
            load = f"{_fmt(r['MM_LOAD_MIN'])} ~ {_fmt(r['MM_LOAD_MAX'])}"
            values = [load if c == "LOAD" else _fmt(r.get(c)) for c, _, _ in LIST_COLS]
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
        try:
            row = self._read_form()
        except ValueError as exc:
            messagebox.showerror("Save Model", str(exc), parent=self)
            return
        self._set_field("MM_DATE", row["MM_DATE"])

        def done(_):
            self._set_status(f"Saved {row['MM_MODEL']}", theme.PASS)
            self.refresh(select=row["MM_MODEL"])

        self._run(lambda: db.save_model(row), f"Saving {row['MM_MODEL']}…", done)

    def delete_model(self):
        model = self.fields["MM_MODEL"].get().strip()
        if not model:
            return
        if not messagebox.askyesno("Delete Model", f"Delete model {model} from {db.TABLE}?", parent=self):
            return

        def done(count):
            self._set_status(f"Deleted {model}" if count else f"{model} was not in the table",
                             theme.PASS if count else theme.MUTED)
            self.new_model()
            self.refresh()

        self._run(lambda: db.delete_model(model), f"Deleting {model}…", done)

    def apply_to_console(self):
        """Copy this model's test conditions into the matching Test Console panel."""
        try:
            row = self._read_form()
        except ValueError as exc:
            messagebox.showerror("Apply", str(exc), parent=self)
            return
        cable = row["MM_TEST_CABLE"] or "SELECT CABLE"
        key = "shift" if cable == "SHIFT CABLE" else "select"
        missing = [lbl for c, lbl, _ in db.FIELDS if c in db.NUMERIC and row[c] is None]
        if missing:
            messagebox.showerror("Apply", "Fill in: " + ", ".join(missing), parent=self)
            return
        self.settings["test"][key].update({
            "speed": row["MM_SPEED"], "delay": row["MM_DELAY_TIME"], "stroke": row["MM_STROKE"],
            "positions": [row["MM_POSITION_1"], row["MM_POSITION_2"], row["MM_POSITION_3"]],
            "load_min": row["MM_LOAD_MIN"], "load_max": row["MM_LOAD_MAX"],
            "model": row["MM_MODEL"],
        })
        save_settings(self.settings)
        self._set_status(f"{row['MM_MODEL']} applied to {cable} on the Test Console", theme.PASS)
