"""Report page — search, review and export TBL_TEST_RESULTS.

Layout: a filter bar, summary tiles for the parts found, the result list (one row
per cable test: PULL 1-3 / PUSH 1-3 and the largest value in kgf) and, beside it,
the selected cycle - its verdict, part details, values against the LOAD limits,
the stored Load-Stroke graph (click to enlarge) and why it was NG / INCOMPLETE.
Database calls run on worker threads so a slow or missing MySQL server never
freezes the window.
"""

import csv
import datetime
import os
import threading
import tkinter as tk
from decimal import Decimal
from tkinter import filedialog, messagebox, ttk

from . import theme as T
from .theme import light as theme      # light industrial theme
from .. import db
from .plot import LoadStrokePlot
from .test_console import PULL_COLOR, PUSH_COLOR

CARD_BG = theme.PANEL
HEAD_BG = T.L_SURFACE_2
LIMIT = 2000                            # rows fetched per search
DETAIL_W = 430                          # width of the selected-cycle panel

RESULTS = ["All"] + list(db.PART_RESULTS)
PERIODS = {"Today": 0, "Last 7 days": 6, "Last 30 days": 29, "Custom": None}   # days back from today
STAGES = ["All", "SELECT", "SHIFT"]
VERDICT_COLOR = {"OK": T.L_OK, "NG": T.L_NG, "INCOMPLETE": T.L_WARN, "ABORTED": T.L_TEXT_3}

# Values of a cable: PULL 1-3 = CH1 FWD 1-3, PUSH 1-3 = CH1 RET 1-3, Max = CH1 FINAL (db._pc_result_rows)
PULL_COLS = [f"TR_CH1_FWD_{i}" for i in (1, 2, 3)]
PUSH_COLS = [f"TR_CH1_RET_{i}" for i in (1, 2, 3)]
CH2_COLS = [f"TR_CH2_{d}_{i}" for d in ("FWD", "RET") for i in (1, 2, 3)] + ["TR_CH2_FINAL"]

# Result list: (column, heading, width, anchor)
LIST_COLS = ([("TR_DATETIME", "Date / time", 140, "w"), ("TR_STAGE", "Cable", 70, "center"),
              ("TR_LABEL", "Label", 130, "w"), ("TR_STAGE_RESULT", "Result", 118, "center"),
              ("TR_LOT_RESULT", "Part", 118, "center")]
             + [(c, f"PULL {i}", 68, "e") for i, c in enumerate(PULL_COLS, 1)]
             + [(c, f"PUSH {i}", 68, "e") for i, c in enumerate(PUSH_COLS, 1)]
             + [("TR_CH1_FINAL", "Max", 64, "e"), ("TR_MODEL", "Model", 130, "w")])
NUMERIC_COLS = set(PULL_COLS + PUSH_COLS + CH2_COLS + ["TR_CH1_FINAL"])

# CSV headings for the stored columns
CSV_HEADINGS = {"ID": "ID", "TR_PART_UID": "Part UID", "TR_DATETIME": "Date / time", "TR_MODEL": "Model",
                "TR_MODEL_TYPE": "Type", "TR_PART_NUMBER": "Part number", "TR_STAGE": "Cable",
                "TR_LABEL": "Label", "TR_STAGE_RESULT": "Cable result", "TR_CH1_FINAL": "Max",
                "TR_CH2_FINAL": "CH2 final", "TR_CH1_LIMIT_NG": "NG flag", "TR_CH2_LIMIT_NG": "CH2 NG flag",
                "TR_LOT_VALUE": "Lot", "TR_LOT_RESULT": "Part result", "TR_SOURCE": "Source"}
CSV_HEADINGS.update({c: f"PULL {i}" for i, c in enumerate(PULL_COLS, 1)})
CSV_HEADINGS.update({c: f"PUSH {i}" for i, c in enumerate(PUSH_COLS, 1)})
CSV_HEADINGS.update({c: c.replace("TR_", "").replace("_", " ") for c in CH2_COLS if c != "TR_CH2_FINAL"})


def _fmt(v):
    if v is None or v == "":
        return "—"
    if isinstance(v, (Decimal, float)):
        return f"{float(v):.2f}"
    if isinstance(v, datetime.datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    return str(v)


def _csv_value(v):
    """A stored value for the CSV: full precision, blank when missing."""
    if v is None:
        return ""
    if isinstance(v, Decimal):
        return f"{float(v):g}"
    return _fmt(v)


def _num(v):
    return None if v is None else float(v)


def _sort_key(col):
    def key(row):
        v = row.get(col)
        if v is None or v == "":
            return (1, "")
        if col in NUMERIC_COLS:
            return (0, float(v))
        return (0, v if isinstance(v, datetime.datetime) else str(v).upper())
    return key


def _draw_graph(plot, graph):
    """Draw a stored graph on a LoadStrokePlot; False (and nothing drawn) when it has no points."""
    plot.set_stroke((graph or {}).get("stroke"))
    plot.clear()
    pull, push = ((graph or {}).get(d) or [] for d in ("pull", "push"))
    if not (pull or push):
        return False
    plot.set_note("")
    for name, pts in (("PULL", pull), ("PUSH", push)):
        plot.set_data(name, [p[0] for p in pts], [p[1] for p in pts])
    plot.extend_to_data()
    return True


class ReportPage(tk.Frame):
    def __init__(self, master, settings):
        super().__init__(master, bg=theme.BG)
        self.settings = settings
        self._alive = True
        self.bind("<Destroy>", lambda e: setattr(self, "_alive", False) if e.widget is self else None)
        self.rows = []
        self._iid_rows = {}
        self._sort = ("TR_DATETIME", True)      # newest first, as the database returns them

        self._build_header()
        self._build_filters()
        self._build_summary()
        main = tk.Frame(self, bg=theme.BG)
        main.pack(fill="both", expand=True, pady=(10, 0))
        main.columnconfigure(0, weight=1)
        main.rowconfigure(0, weight=1)
        self._build_table(main)
        self._build_detail(main)
        self.status = tk.Label(self, text="", bg=theme.BG, fg=theme.MUTED, font=(theme.FONT, 10), anchor="w")
        self.status.pack(fill="x", pady=(6, 0))
        self.search()

    # ------------------------------------------------------------ widgets
    def _label(self, parent, text, fg=T.L_TEXT, size=10, bold=False, **kw):
        return tk.Label(parent, text=text, bg=parent["bg"], fg=fg,
                        font=(theme.FONT, size, "bold") if bold else (theme.FONT, size), **kw)

    def _entry(self, parent, width, value=""):
        e = tk.Entry(parent, bg=theme.ENTRY_BG, fg=T.L_TEXT, insertbackground=T.L_TEXT, font=(theme.FONT, 10),
                     width=width, bd=0, relief="flat", highlightbackground=T.L_BORDER, highlightthickness=1,
                     highlightcolor=theme.UPDATE)
        e.insert(0, value)
        e.bind("<Return>", lambda _e: self.search())
        return e

    def _button(self, parent, text, command, bg, fg="white", padx=16, size=10):
        return tk.Button(parent, text=text, command=command, bg=bg, fg=fg, activebackground=bg,
                         activeforeground=fg, font=(theme.FONT, size, "bold"), bd=0, padx=padx, pady=5,
                         cursor="hand2", disabledforeground="#dfe4ea")

    def _card(self, parent, title):
        card = tk.Frame(parent, bg=CARD_BG, highlightbackground=theme.BORDER, highlightthickness=1)
        head = tk.Frame(card, bg=HEAD_BG)
        head.pack(fill="x")
        tk.Frame(head, bg=theme.TITLE_FG, width=4).pack(side="left", fill="y")
        self._label(head, title, size=12, bold=True).pack(side="left", padx=(10, 8), pady=6)
        return card, head

    def _field(self, parent, caption):
        """A filter field: a small caption above the widget packed into the returned frame."""
        f = tk.Frame(parent, bg=parent["bg"])
        f.pack(side="left", padx=(0, 14), anchor="s")
        self._label(f, caption.upper(), fg=theme.MUTED, size=8, bold=True).pack(anchor="w", pady=(0, 2))
        return f

    # ------------------------------------------------------------ layout
    def _build_header(self):
        bar = tk.Frame(self, bg=theme.BG)
        bar.pack(fill="x")
        self._label(bar, "Test Results", size=16, bold=True).pack(side="left")
        self._label(bar, "every cable test saved by the Test Console", fg=theme.MUTED, size=10).pack(
            side="left", padx=(12, 0), pady=(5, 0))
        self._button(bar, "EXPORT CSV", self.export_csv, T.L_NEUTRAL_BTN).pack(side="right")

    def _build_filters(self):
        f = tk.Frame(self, bg=CARD_BG, highlightbackground=theme.BORDER, highlightthickness=1)
        f.pack(fill="x", pady=(10, 0))
        row = tk.Frame(f, bg=CARD_BG)
        row.pack(fill="x", padx=12, pady=(8, 10))
        # the buttons first, so they keep their room on a narrow window
        self.btn_search = self._button(row, "SEARCH", self.search, theme.UPDATE)
        self.btn_search.pack(side="right", anchor="s")
        self._button(row, "RESET", self.reset_filters, T.L_SURFACE_2, fg=T.L_TEXT).pack(
            side="right", anchor="s", padx=(0, 8))
        today = datetime.date.today().isoformat()
        self.cb_period = ttk.Combobox(self._field(row, "Period"), values=list(PERIODS), state="readonly", width=11,
                                      font=(theme.FONT, 10), style="L.TCombobox")
        self.cb_period.set("Today")
        self.cb_period.pack(ipady=1)
        self.cb_period.bind("<<ComboboxSelected>>", lambda _e: self.set_period(PERIODS[self.cb_period.get()]))
        self.ent_from = self._entry(self._field(row, "From"), 11, today)
        self.ent_from.pack(ipady=3)
        self.ent_to = self._entry(self._field(row, "To"), 11, today)
        self.ent_to.pack(ipady=3)
        for e in (self.ent_from, self.ent_to):                 # a typed date is a custom period
            e.bind("<Key>", lambda ev: ev.keysym != "Return" and self.cb_period.set("Custom"), add="+")
        self.ent_model = self._entry(self._field(row, "Model"), 13)
        self.ent_model.pack(ipady=3)
        self.ent_text = self._entry(self._field(row, "Label / lot"), 15)
        self.ent_text.pack(ipady=3)
        self.cb_stage = self._combo(self._field(row, "Cable"), STAGES, 7)
        self.cb_result = self._combo(self._field(row, "Result"), RESULTS, 11)

    def _combo(self, parent, values, width):
        cb = ttk.Combobox(parent, values=values, state="readonly", width=width, font=(theme.FONT, 10),
                          style="L.TCombobox")
        cb.set(values[0])
        cb.pack(ipady=1)
        cb.bind("<<ComboboxSelected>>", lambda _e: self.search())
        return cb

    def _build_summary(self):
        row = tk.Frame(self, bg=theme.BG)
        row.pack(fill="x", pady=(10, 0))
        self.tiles = {}
        for i, (key, caption, color) in enumerate((
                ("parts", "PARTS", T.L_PRIMARY), ("ok", "OK", theme.PASS), ("ng", "NG", theme.FAIL),
                ("other", "INCOMPLETE / ABORTED", T.L_WARN), ("yield", "YIELD", T.L_TEXT_2))):
            t = tk.Frame(row, bg=CARD_BG, highlightbackground=theme.BORDER, highlightthickness=1)
            t.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 8, 0))
            row.columnconfigure(i, weight=1, uniform="tiles")
            tk.Frame(t, bg=color, width=5).pack(side="left", fill="y")
            inner = tk.Frame(t, bg=CARD_BG)
            inner.pack(side="left", fill="both", expand=True, padx=12, pady=4)
            tk.Label(inner, text=caption, bg=CARD_BG, fg=theme.MUTED, font=(theme.FONT, 8, "bold")).pack(anchor="w")
            val = tk.Label(inner, text="—", bg=CARD_BG, fg=color if key != "yield" else T.L_TEXT,
                           font=(theme.FONT, 17, "bold"))
            val.pack(anchor="w")
            self.tiles[key] = val

    def _build_table(self, parent):
        card, head = self._card(parent, "Results")
        card.grid(row=0, column=0, sticky="nsew")
        self.lbl_count = self._label(head, "", fg=theme.MUTED, size=9)
        self.lbl_count.pack(side="right", padx=10)
        self._label(head, "values in kgf · click a heading to sort", fg=theme.MUTED, size=9).pack(side="left")
        box = tk.Frame(card, bg=CARD_BG)
        box.pack(fill="both", expand=True, padx=8, pady=8)
        self.tree = ttk.Treeview(box, columns=[c for c, *_ in LIST_COLS], show="headings",
                                 selectmode="browse", style="L.Treeview")
        for col, heading, width, anchor in LIST_COLS:
            self.tree.heading(col, text=heading, command=lambda c=col: self.sort_by(c))
            self.tree.column(col, width=width, minwidth=40, anchor=anchor, stretch=col == "TR_MODEL")
        sb = ttk.Scrollbar(box, orient="vertical", command=self.tree.yview, style="L.Vertical.TScrollbar")
        xsb = ttk.Scrollbar(box, orient="horizontal", command=self.tree.xview, style="L.Horizontal.TScrollbar")
        self.tree.configure(yscrollcommand=sb.set, xscrollcommand=xsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        xsb.grid(row=1, column=0, sticky="ew")
        box.rowconfigure(0, weight=1)
        box.columnconfigure(0, weight=1)
        self.tree.tag_configure("odd", background=T.L_SUNKEN)
        self.tree.tag_configure("NG", foreground=T.L_NG)
        self.tree.tag_configure("INCOMPLETE", foreground=T.L_WARN)
        self.tree.tag_configure("ABORTED", foreground=T.L_TEXT_3)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._show_detail())
        self.empty_list = self._label(box, "", fg=theme.MUTED, size=11)

    def _build_detail(self, parent):
        card, head = self._card(parent, "Selected cycle")
        card.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        card.configure(width=DETAIL_W)
        card.grid_propagate(False)
        card.pack_propagate(False)
        self.badge = tk.Label(head, text="", bg=HEAD_BG, fg="white", font=(theme.FONT, 12, "bold"), padx=12)
        self.badge.pack(side="right", padx=8, pady=4)

        self.detail_empty = self._label(card, "Select a row in the list\nto see its values and graph",
                                        fg=theme.MUTED, size=11, justify="center")
        self.detail_body = body = tk.Frame(card, bg=CARD_BG)
        wrap = DETAIL_W - 34

        # Part details: caption and value on one line; the time on its own row, then two pairs a row
        info = tk.Frame(body, bg=CARD_BG)
        info.pack(side="top", fill="x", pady=(0, 6))
        self.info = {}
        for key, caption, r, c in (("TR_DATETIME", "Time", 0, 0),
                                   ("TR_MODEL", "Model", 1, 0), ("TR_STAGE", "Cable", 1, 2),
                                   ("TR_LABEL", "Label", 2, 0), ("TR_MODEL_TYPE", "Type", 2, 2),
                                   ("TR_PART_NUMBER", "Part no.", 3, 0), ("TR_LOT_RESULT", "Part", 3, 2)):
            self._label(info, caption, fg=theme.MUTED, size=9).grid(row=r, column=c, sticky="w", padx=(0, 6), pady=1)
            self.info[key] = self._label(info, "—", size=10, anchor="w")
            self.info[key].grid(row=r, column=c + 1, columnspan=3 if key == "TR_DATETIME" else 1, sticky="w",
                                padx=(0, 12), pady=1)
        info.columnconfigure(1, weight=1)

        # Values: PULL / PUSH 1-3 (and the second channel of the older PLC profile)
        tk.Frame(body, bg=T.L_BORDER_2, height=1).pack(side="top", fill="x", pady=(0, 6))
        vals = tk.Frame(body, bg=CARD_BG)
        vals.pack(side="top", fill="x")
        for c, h in enumerate(("kgf", "1", "2", "3")):
            self._label(vals, h, fg=theme.HEADER_FG, size=9, bold=True).grid(row=0, column=c, sticky="w", padx=(0, 6))
        self.cells, self._ch2_rows = {}, []
        for r, (name, cols, color) in enumerate((("PULL", PULL_COLS, PULL_COLOR), ("PUSH", PUSH_COLS, PUSH_COLOR),
                                                 ("CH2 FWD", CH2_COLS[0:3], T.L_TEXT_2),
                                                 ("CH2 RET", CH2_COLS[3:6], T.L_TEXT_2)), start=1):
            widgets = [self._label(vals, name, fg=color, bold=True)]
            widgets[0].grid(row=r, column=0, sticky="w", padx=(0, 6), pady=2)
            for c, key in enumerate(cols, start=1):
                lbl = tk.Label(vals, text="—", bg=T.L_SUNKEN, fg=T.L_TEXT, font=(theme.FONT, 11, "bold"), width=8,
                               highlightbackground=T.L_BORDER, highlightthickness=1)
                lbl.grid(row=r, column=c, padx=3, pady=1)
                self.cells[key] = lbl
                widgets.append(lbl)
            if name.startswith("CH2"):
                self._ch2_rows.append(widgets)
        line = tk.Frame(body, bg=CARD_BG)
        line.pack(side="top", fill="x", pady=(6, 0))
        self.lbl_max = self._label(line, "Max —", bold=True)
        self.lbl_max.pack(side="left")
        self.lbl_limits = self._label(line, "Limits —", fg=T.L_TEXT_2)
        self.lbl_limits.pack(side="left", padx=(16, 0))
        self.lbl_positions = self._label(body, "", fg=theme.MUTED, size=9, anchor="w")
        self.lbl_positions.pack(side="top", fill="x")

        # Why the cycle was NG / INCOMPLETE: under the graph, only when there is a reason
        self.lbl_problems = self._label(body, "", fg=theme.FAIL, size=9, anchor="w", justify="left",
                                        wraplength=wrap)

        # The stored Load-Stroke graph (TBL_TEST_GRAPHS) as a preview filling the height left;
        # a click enlarges it
        cap = tk.Frame(body, bg=CARD_BG)
        cap.pack(side="top", fill="x", pady=(8, 2))
        self._label(cap, "LOAD - STROKE", fg=theme.MUTED, size=8, bold=True).pack(side="left")
        hint = self._label(cap, "click to enlarge ⤢", fg=T.L_PRIMARY, size=8, cursor="hand2")
        hint.pack(side="right")
        hint.bind("<Button-1>", lambda _e: self.enlarge_graph())
        self.plot = LoadStrokePlot(body, controls=False)
        self.plot.canvas.config(width=wrap, height=120, cursor="hand2")
        self.plot.add_series("PULL", PULL_COLOR)
        self.plot.add_series("PUSH", PUSH_COLOR)
        self.plot.pack(side="top", fill="both", expand=True)
        self.plot.canvas.bind("<Button-1>", lambda _e: self.enlarge_graph())

        self._record_for = None           # ID of the row whose detail / graph was last requested
        self._row = None                  # the selected row
        self._graph = None                # the graph shown in the preview (None = none)
        self._graph_title = ""
        self._show_empty_detail()

    # ------------------------------------------------------------ filters
    def set_period(self, days):
        """Quick period: today (0) or the last `days` + 1 days, then search. None = Custom
        (the dates as typed)."""
        if days is None:
            return
        name = next((k for k, v in PERIODS.items() if v == days), "Custom")
        if self.cb_period.get() != name:
            self.cb_period.set(name)
        today = datetime.date.today()
        for entry, value in ((self.ent_from, today - datetime.timedelta(days=days)), (self.ent_to, today)):
            entry.delete(0, "end")
            entry.insert(0, value.isoformat())
        self.search()

    def reset_filters(self):
        for entry in (self.ent_model, self.ent_text):
            entry.delete(0, "end")
        self.cb_stage.set(STAGES[0])
        self.cb_result.set(RESULTS[0])
        self.set_period(0)

    def _filters(self):
        def date(entry, name):
            text = entry.get().strip()
            if not text:
                return None
            try:
                return datetime.date.fromisoformat(text)
            except ValueError:
                raise ValueError(f"{name} date must be YYYY-MM-DD (e.g. {datetime.date.today().isoformat()})")
        d_from, d_to = date(self.ent_from, "From"), date(self.ent_to, "To")
        if d_from and d_to and d_from > d_to:
            raise ValueError("From date is after To date")
        result, stage = self.cb_result.get(), self.cb_stage.get()
        return dict(date_from=d_from, date_to=d_to, model=self.ent_model.get().strip(),
                    text=self.ent_text.get().strip(), lot_result="" if result == "All" else result,
                    stage="" if stage == "All" else stage, limit=LIMIT)

    # ------------------------------------------------------------ data
    def search(self):
        try:
            filters = self._filters()
        except ValueError as exc:
            messagebox.showerror("Search", str(exc), parent=self)
            return
        self.status.config(text="Loading test results…", fg=theme.BUSY)
        self.btn_search.config(state="disabled")
        result = {}

        def worker():
            try:
                db.init_database()
                result["rows"], result["error"] = db.list_test_results(**filters), None
            except Exception as exc:
                result["rows"], result["error"] = [], exc

        def collect():
            if not self._alive:
                return
            if "rows" not in result:
                self.after(100, collect)
                return
            self.btn_search.config(state="normal")
            if result["error"] is not None:
                self.status.config(text=f"Database error: {result['error']}", fg=theme.FAIL)
                return
            self._show_rows(result["rows"])

        threading.Thread(target=worker, daemon=True).start()
        self.after(100, collect)

    def _show_rows(self, rows):
        self.rows = list(rows)
        self._apply_sort()
        self._fill_tree()
        parts = {}
        for r in self.rows:
            parts[r.get("TR_PART_UID")] = r.get("TR_LOT_RESULT")
        n = {v: sum(1 for x in parts.values() if x == v) for v in db.PART_RESULTS}
        self.tiles["parts"].config(text=str(len(parts)))
        self.tiles["ok"].config(text=str(n["OK"]))
        self.tiles["ng"].config(text=str(n["NG"]))
        self.tiles["other"].config(text=str(n["INCOMPLETE"] + n["ABORTED"]))
        self.tiles["yield"].config(text=f"{n['OK'] / len(parts) * 100:.1f}%" if parts else "—")
        more = " - limit reached, narrow the search" if len(self.rows) >= LIMIT else ""
        self.lbl_count.config(text=f"{len(self.rows)} rows{more}")
        self.status.config(text=f"{len(parts)} part(s), {len(self.rows)} cable test(s)",
                           fg=theme.PASS if self.rows else theme.MUTED)
        self._show_empty_detail()

    def _fill_tree(self):
        self.tree.delete(*self.tree.get_children())
        self._iid_rows = {}
        for i, r in enumerate(self.rows):
            verdict = r.get("TR_STAGE_RESULT")              # the cable's own result colours its row
            tags = (("odd",) if i % 2 else ()) + ((verdict,) if verdict in VERDICT_COLOR else ())
            values = [r[c].strftime("%Y-%m-%d %H:%M") if isinstance(r.get(c), datetime.datetime) else _fmt(r.get(c))
                      for c, *_ in LIST_COLS]                 # the list shows minutes; the panel the seconds
            iid = self.tree.insert("", "end", values=values, tags=tags)
            self._iid_rows[iid] = r
        if self.rows:
            self.empty_list.place_forget()
        else:
            self.empty_list.config(text="No test results for these filters")
            self.empty_list.place(relx=0.5, rely=0.4, anchor="center")
        col, rev = self._sort
        for c, heading, *_ in LIST_COLS:
            self.tree.heading(c, text=heading + ((" ▼" if rev else " ▲") if c == col else ""))

    def _apply_sort(self):
        col, rev = self._sort
        filled = [r for r in self.rows if r.get(col) not in (None, "")]
        blank = [r for r in self.rows if r.get(col) in (None, "")]
        self.rows = sorted(filled, key=_sort_key(col), reverse=rev) + blank   # blanks always last

    def sort_by(self, col):
        """Sort the list by a column; the same heading again reverses it."""
        cur, rev = self._sort
        self._sort = (col, not rev if col == cur else col == "TR_DATETIME" or col in NUMERIC_COLS)
        selected = self._row
        self._apply_sort()
        self._fill_tree()
        for iid, r in self._iid_rows.items():
            if r is selected:
                self.tree.selection_set(iid)
                self.tree.see(iid)
                break

    # ------------------------------------------------------------ selected cycle
    def _show_empty_detail(self):
        self._row, self._record_for, self._graph = None, None, None
        self.badge.config(text="", bg=HEAD_BG)
        self.detail_body.pack_forget()
        self.detail_empty.pack(expand=True)
        self.plot.clear()
        self.plot.set_note("")

    def _show_detail(self):
        sel = self.tree.selection()
        if not sel or sel[0] not in self._iid_rows:
            return
        r = self._iid_rows[sel[0]]
        if r is self._row:
            return
        self._row = r
        self.detail_empty.pack_forget()
        self.detail_body.pack(fill="both", expand=True, padx=12, pady=10)
        verdict = r.get("TR_STAGE_RESULT") or r.get("TR_LOT_RESULT") or ""
        self.badge.config(text=verdict or "—", bg=VERDICT_COLOR.get(verdict, T.L_TEXT_3))
        for key, lbl in self.info.items():
            v = r.get(key)
            lbl.config(text=_fmt(v), fg=VERDICT_COLOR.get(v, T.L_TEXT) if key == "TR_LOT_RESULT" else T.L_TEXT)
        for key, lbl in self.cells.items():
            lbl.config(text=_fmt(r.get(key)), fg=T.L_TEXT)
        has_ch2 = any(r.get(c) is not None for c in CH2_COLS)
        for widgets in self._ch2_rows:
            for w in widgets:
                (w.grid if has_ch2 else w.grid_remove)()
        self.lbl_max.config(text=f"Max {_fmt(r.get('TR_CH1_FINAL'))} kgf")
        self.lbl_limits.config(text="Limits …")
        self.lbl_positions.config(text="")
        self._set_problems([], None)
        self._graph_title = (f"{r.get('TR_STAGE')} · {r.get('TR_MODEL')} · {r.get('TR_LABEL') or '—'} · "
                             f"{_fmt(r.get('TR_DATETIME'))} · {verdict or '—'}")
        self._load_record(r["ID"])

    def _load_record(self, row_id):
        """Fetch the row's detail and graph on a worker thread (the newest click wins)."""
        self._record_for, self._graph = row_id, None
        self.plot.clear()
        self.plot.set_note("Loading graph…")
        result = {}

        def worker():
            try:
                result["record"], result["error"] = db.get_test_record(row_id), None
            except Exception as exc:
                result["record"], result["error"] = None, exc

        def collect():
            if not self._alive or self._record_for != row_id:
                return
            if "record" not in result:
                self.after(100, collect)
                return
            if result["error"] is not None:
                self.lbl_limits.config(text="Limits —")
                self.plot.set_note(f"Not loaded: {result['error']}")
            else:
                self.show_record(result["record"])

        threading.Thread(target=worker, daemon=True).start()
        self.after(100, collect)

    def show_record(self, record):
        """Show what was fetched for the selected row: limits, positions, problems, graph."""
        record = record or {}
        detail = record.get("detail") or {}
        res = detail.get("result") or {}
        lo, hi = (res.get("limits") or [None, None])[:2]
        if lo is not None and hi is not None:
            self.lbl_limits.config(text=f"Limits {float(lo):.2f} ~ {float(hi):.2f} kgf")
            absolute = res.get("force_sign") != "signed"
            for key in PULL_COLS + PUSH_COLS:
                v = _num((self._row or {}).get(key))
                if v is not None:
                    v = abs(v) if absolute else v
                    self.cells[key].config(fg=T.L_TEXT if lo <= v <= hi else T.L_NG)
        else:
            self.lbl_limits.config(text="Limits —")
        positions = [p for p in res.get("positions") or [] if p is not None]
        self.lbl_positions.config(text=("Read at " + " · ".join(f"P{i} {p:g}" for i, p in enumerate(positions, 1))
                                        + " mm") if positions else "")
        self._set_problems(res.get("problems") or detail.get("reasons") or [],
                           (self._row or {}).get("TR_STAGE_RESULT"))
        self.show_graph(record.get("graph"))

    def _set_problems(self, problems, verdict):
        """Why the cycle was NG / INCOMPLETE under the graph; hidden when there is none."""
        if problems:
            self.lbl_problems.config(text="\n".join(f"• {p}" for p in problems[:4]),
                                     fg=T.L_NG if verdict == "NG" else T.L_WARN)
            self.lbl_problems.pack(side="bottom", fill="x", pady=(4, 0), before=self.plot)
        else:
            self.lbl_problems.config(text="")
            self.lbl_problems.pack_forget()

    def show_graph(self, graph):
        """Draw a stored graph ({"pull": [[x, load]], "push": [...], "stroke": ...}) in the
        preview, or say why there is none."""
        self._graph = graph if _draw_graph(self.plot, graph) else None
        if self._graph is None:
            self.plot.set_note("No graph stored for this cycle")

    def enlarge_graph(self):
        """The previewed graph in a large window, with the zoom / pan controls."""
        if self._graph is None:
            return None
        top = tk.Toplevel(self)
        top.title(f"Load-Stroke graph · {self._graph_title}")
        top.configure(bg=theme.PANEL)
        top.transient(self.winfo_toplevel())
        root = self.winfo_toplevel()
        w, h = max(700, int(root.winfo_width() * 0.75)), max(450, int(root.winfo_height() * 0.75))
        top.geometry(f"{w}x{h}+{root.winfo_rootx() + (root.winfo_width() - w) // 2}"
                     f"+{root.winfo_rooty() + (root.winfo_height() - h) // 3}")
        self._label(top, self._graph_title, size=11, bold=True).pack(anchor="w", padx=12, pady=(10, 4))
        big = LoadStrokePlot(top)
        big.add_series("PULL", PULL_COLOR)
        big.add_series("PUSH", PUSH_COLOR)
        big.pack(fill="both", expand=True, padx=12)
        tk.Button(top, text="CLOSE", command=top.destroy, bg=T.L_NEUTRAL_BTN, fg="white", bd=0, padx=18,
                  pady=4, font=(theme.FONT, 10, "bold"), cursor="hand2").pack(anchor="e", padx=12, pady=10)
        top.bind("<Escape>", lambda _e: top.destroy())
        _draw_graph(big, self._graph)
        top.focus_set()
        return top

    # ------------------------------------------------------------ export
    def export_csv(self):
        if not self.rows:
            messagebox.showinfo("Export", "There are no rows to export. Run a search first.", parent=self)
            return
        name = f"test_results_{datetime.datetime.now():%Y%m%d_%H%M%S}.csv"
        path = filedialog.asksaveasfilename(parent=self, title="Export test results", initialfile=name,
                                            defaultextension=".csv", filetypes=[("CSV", "*.csv")])
        if not path:
            return
        cols = ["ID"] + [c for c in db.RESULT_COLUMNS if c not in db.BULK_COLUMNS]
        if not any(r.get(c) is not None for r in self.rows for c in CH2_COLS + ["TR_CH2_LIMIT_NG"]):
            cols = [c for c in cols if c not in CH2_COLS and c != "TR_CH2_LIMIT_NG"]   # unused channel
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow([CSV_HEADINGS.get(c, c) for c in cols])
                for r in self.rows:
                    w.writerow([_csv_value(r.get(c)) for c in cols])
        except OSError as exc:
            messagebox.showerror("Export", f"Could not write the file:\n{exc}", parent=self)
            return
        self.status.config(text=f"Exported {len(self.rows)} rows to {os.path.basename(path)}", fg=theme.PASS)
