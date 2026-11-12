"""Report page — view and export TBL_TEST_RESULTS.

Filters on top, part summary tiles, the result rows (one per PLC cycle), and
the selected row's channel readings underneath. Database calls run on a
worker thread so a slow or missing MySQL server never freezes the window.
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
TILE = T.L_SUNKEN

# (column, heading, width)
LIST_COLS = [("TR_DATETIME", "Date / time", 150), ("TR_MODEL", "Model", 120), ("TR_MODEL_TYPE", "Type", 50),
             ("TR_PART_NUMBER", "Part number", 115), ("TR_STAGE", "Stage", 65), ("TR_LABEL", "Label", 190),
             ("TR_STAGE_RESULT", "Cycle", 55), ("TR_CH1_FINAL", "Ch1 final", 75), ("TR_CH2_FINAL", "Ch2 final", 75),
             ("TR_LOT_VALUE", "Lot value", 190), ("TR_LOT_RESULT", "Lot", 50)]


def _fmt(v):
    if v is None:
        return "—"
    if isinstance(v, Decimal):
        return f"{float(v):g}"
    if isinstance(v, datetime.datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    return str(v)


class ReportPage(tk.Frame):
    def __init__(self, master, settings):
        super().__init__(master, bg=theme.BG)
        self.settings = settings
        self._alive = True
        self.bind("<Destroy>", lambda e: setattr(self, "_alive", False) if e.widget is self else None)
        self.rows = []
        self._iid_rows = {}

        self._build_filters()
        self._build_summary()
        self._build_table()
        self._build_detail()
        self.status = tk.Label(self, text="", bg=theme.BG, fg=theme.MUTED, font=(theme.FONT, 10), anchor="w")
        self.status.pack(fill="x", pady=(6, 0))
        self.search()

    # ------------------------------------------------------------ layout
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

    def _button(self, parent, text, command, bg):
        return tk.Button(parent, text=text, command=command, bg=bg, fg="white", activebackground=bg,
                         activeforeground="white", font=(theme.FONT, 10, "bold"), bd=0, padx=16, pady=5,
                         cursor="hand2", disabledforeground="#dfe4ea")

    def _card(self, title):
        card = tk.Frame(self, bg=CARD_BG, highlightbackground=theme.BORDER, highlightthickness=1)
        head = tk.Frame(card, bg=HEAD_BG)
        head.pack(fill="x")
        tk.Frame(head, bg=theme.TITLE_FG, width=4).pack(side="left", fill="y")
        self._label(head, title, size=12, bold=True).pack(side="left", padx=(10, 8), pady=6)
        return card, head

    def _build_filters(self):
        bar = tk.Frame(self, bg=theme.BG)
        bar.pack(fill="x")
        self._label(bar, "Test Results", size=15).pack(side="left")
        self._label(bar, db.RESULTS_TABLE, fg=theme.MUTED, size=10).pack(side="left", padx=(12, 0), pady=(4, 0))
        self._button(bar, "EXPORT CSV", self.export_csv, T.L_NEUTRAL_BTN).pack(side="right")

        f = tk.Frame(self, bg=CARD_BG, highlightbackground=theme.BORDER, highlightthickness=1)
        f.pack(fill="x", pady=(10, 0))
        row = tk.Frame(f, bg=CARD_BG)
        row.pack(fill="x", padx=12, pady=8)
        today = datetime.date.today().isoformat()
        self._label(row, "From", fg=theme.MUTED, size=9, bold=True).pack(side="left")
        self.ent_from = self._entry(row, 11, today)
        self.ent_from.pack(side="left", padx=(6, 14), ipady=2)
        self._label(row, "To", fg=theme.MUTED, size=9, bold=True).pack(side="left")
        self.ent_to = self._entry(row, 11, today)
        self.ent_to.pack(side="left", padx=(6, 14), ipady=2)
        self._label(row, "Model", fg=theme.MUTED, size=9, bold=True).pack(side="left")
        self.ent_model = self._entry(row, 14)
        self.ent_model.pack(side="left", padx=(6, 14), ipady=2)
        self._label(row, "Label / lot", fg=theme.MUTED, size=9, bold=True).pack(side="left")
        self.ent_text = self._entry(row, 20)
        self.ent_text.pack(side="left", padx=(6, 14), ipady=2)
        self._label(row, "Result", fg=theme.MUTED, size=9, bold=True).pack(side="left")
        self.cb_result = ttk.Combobox(row, values=["All", "OK", "NG"], state="readonly", width=5, font=(theme.FONT, 10),
                                      style="L.TCombobox")
        self.cb_result.set("All")
        self.cb_result.pack(side="left", padx=(6, 14))
        self.cb_result.bind("<<ComboboxSelected>>", lambda _e: self.search())
        self.btn_search = self._button(row, "SEARCH", self.search, theme.UPDATE)
        self.btn_search.pack(side="right")

    def _build_summary(self):
        row = tk.Frame(self, bg=theme.BG)
        row.pack(fill="x", pady=(10, 0))
        self.tiles = {}
        for i, (key, caption, color) in enumerate((("parts", "PARTS", T.L_TEXT), ("ok", "OK", theme.PASS),
                                                   ("ng", "NG", theme.FAIL), ("yield", "YIELD", T.L_PRIMARY),
                                                   ("cycles", "PLC CYCLES", theme.MUTED))):
            t = tk.Frame(row, bg=TILE, highlightbackground=theme.BORDER, highlightthickness=1)
            t.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 8, 0))
            row.columnconfigure(i, weight=1, uniform="tiles")
            val = tk.Label(t, text="—", bg=TILE, fg=color, font=(theme.FONT, 18, "bold"))
            val.pack(pady=(6, 0))
            tk.Label(t, text=caption, bg=TILE, fg=theme.MUTED, font=(theme.FONT, 8, "bold")).pack(pady=(0, 6))
            self.tiles[key] = val

    def _build_table(self):
        card, head = self._card("Results")
        card.pack(fill="both", expand=True, pady=(10, 0))
        self.lbl_count = self._label(head, "", fg=theme.MUTED, size=9)
        self.lbl_count.pack(side="right", padx=10)
        box = tk.Frame(card, bg=CARD_BG)
        box.pack(fill="both", expand=True, padx=8, pady=8)
        self.tree = ttk.Treeview(box, columns=[c for c, _, _ in LIST_COLS], show="headings",
                                 selectmode="browse", style="L.Treeview")
        for col, heading, width in LIST_COLS:
            self.tree.heading(col, text=heading)
            self.tree.column(col, width=width, minwidth=40,
                             anchor="w" if col in ("TR_LABEL", "TR_LOT_VALUE", "TR_MODEL") else "center")
        sb = ttk.Scrollbar(box, orient="vertical", command=self.tree.yview, style="L.Vertical.TScrollbar")
        xsb = ttk.Scrollbar(box, orient="horizontal", command=self.tree.xview, style="L.Horizontal.TScrollbar")
        self.tree.configure(yscrollcommand=sb.set, xscrollcommand=xsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        xsb.grid(row=1, column=0, sticky="ew")
        box.rowconfigure(0, weight=1)
        box.columnconfigure(0, weight=1)
        self.tree.tag_configure("odd", background=T.L_SUNKEN)
        self.tree.tag_configure("ng", foreground=T.L_NG)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._show_detail())

    def _build_detail(self):
        card, head = self._card("Selected cycle")
        card.pack(fill="x", pady=(10, 0))
        self.lbl_detail = self._label(head, "Click a row to see its readings and graph", fg=theme.MUTED, size=9)
        self.lbl_detail.pack(side="left", padx=4)
        body = tk.Frame(card, bg=CARD_BG)
        body.pack(fill="x", padx=12, pady=8)
        grid = tk.Frame(body, bg=CARD_BG)
        grid.pack(side="left", anchor="n")
        # The stored Load-Stroke graph of the selected row (TR_GRAPH), as the Test Console drew it
        self.plot = LoadStrokePlot(body)
        self.plot.canvas.config(height=220)
        self.plot.add_series("PULL", PULL_COLOR)
        self.plot.add_series("PUSH", PUSH_COLOR)
        self.plot.pack(side="left", fill="both", expand=True, padx=(16, 0))
        self._graph_for = None            # ID of the row whose graph was last requested
        self.cells = {}
        heads = ["", "FWD 1", "FWD 2", "FWD 3", "RET 1", "RET 2", "RET 3", "Final", "Limit"]
        for c, h in enumerate(heads):
            self._label(grid, h, fg=theme.HEADER_FG, size=9, bold=True).grid(row=0, column=c, padx=6, sticky="w")
        for r, ch in enumerate((1, 2), start=1):
            self._label(grid, f"Channel {ch}", bold=True).grid(row=r, column=0, padx=6, pady=2, sticky="w")
            keys = [f"TR_CH{ch}_FWD_{i}" for i in (1, 2, 3)] + [f"TR_CH{ch}_RET_{i}" for i in (1, 2, 3)] + \
                   [f"TR_CH{ch}_FINAL", f"TR_CH{ch}_LIMIT_NG"]
            for c, key in enumerate(keys, start=1):
                lbl = tk.Label(grid, text="—", bg=TILE, fg="#0b3a75", font=(theme.FONT, 10), width=8,
                               highlightbackground=theme.BORDER, highlightthickness=1)
                lbl.grid(row=r, column=c, padx=3, pady=2)
                self.cells[key] = lbl

    # ------------------------------------------------------------ data
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
        result = self.cb_result.get()
        return dict(date_from=d_from, date_to=d_to, model=self.ent_model.get().strip(),
                    text=self.ent_text.get().strip(), lot_result="" if result == "All" else result)

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
        self.rows = rows
        self.tree.delete(*self.tree.get_children())
        self._iid_rows = {}
        for i, r in enumerate(rows):
            tags = (("odd",) if i % 2 else ()) + (("ng",) if r.get("TR_LOT_RESULT") == "NG" else ())
            iid = self.tree.insert("", "end", values=[_fmt(r.get(c)) for c, _, _ in LIST_COLS], tags=tags)
            self._iid_rows[iid] = r
        parts = {}
        for r in rows:
            parts[r["TR_PART_UID"]] = r.get("TR_LOT_RESULT")
        ok = sum(1 for v in parts.values() if v == "OK")
        ng = sum(1 for v in parts.values() if v == "NG")
        self.tiles["parts"].config(text=str(len(parts)))
        self.tiles["ok"].config(text=str(ok))
        self.tiles["ng"].config(text=str(ng))
        self.tiles["yield"].config(text=f"{ok / len(parts) * 100:.1f}%" if parts else "—")
        self.tiles["cycles"].config(text=str(len(rows)))
        self.lbl_count.config(text=f"{len(rows)} rows" + (" (limit reached)" if len(rows) >= 2000 else ""))
        self.status.config(text=f"{len(parts)} part(s), {len(rows)} PLC cycle row(s)", fg=theme.PASS)
        for lbl in self.cells.values():
            lbl.config(text="—")
        self.lbl_detail.config(text="Click a row to see its readings and graph")
        self._graph_for = None
        self.plot.clear()
        self.plot.set_note("")

    def _show_detail(self):
        sel = self.tree.selection()
        if not sel or sel[0] not in self._iid_rows:
            return
        r = self._iid_rows[sel[0]]
        for key, lbl in self.cells.items():
            v = r.get(key)
            if key.endswith("LIMIT_NG"):
                lbl.config(text="—" if v is None else ("NG" if v else "OK"),
                           fg=theme.FAIL if v else theme.PASS if v is not None else theme.MUTED)
            else:
                lbl.config(text=_fmt(v), fg="#0b3a75")
        self.lbl_detail.config(text=f"{_fmt(r.get('TR_DATETIME'))} · {r.get('TR_MODEL')} · {r.get('TR_STAGE')} · "
                                    f"{r.get('TR_LABEL')} · source {r.get('TR_SOURCE')}")
        self._load_graph(r["ID"])

    def _load_graph(self, row_id):
        """Fetch the row's stored graph on a worker thread and draw it (the newest click wins)."""
        self._graph_for = row_id
        self.plot.clear()
        self.plot.set_note("Loading graph…")
        result = {}

        def worker():
            try:
                result["graph"], result["error"] = db.get_test_graph(row_id), None
            except Exception as exc:
                result["graph"], result["error"] = None, exc

        def collect():
            if not self._alive or self._graph_for != row_id:
                return
            if "graph" not in result:
                self.after(100, collect)
                return
            if result["error"] is not None:
                self.plot.set_note(f"Graph not loaded: {result['error']}")
            else:
                self.show_graph(result["graph"])

        threading.Thread(target=worker, daemon=True).start()
        self.after(100, collect)

    def show_graph(self, graph):
        """Draw a stored graph ({"pull": [[x, load]], "push": [...], "stroke": ...}) or say
        why there is none."""
        self.plot.set_stroke((graph or {}).get("stroke"))
        self.plot.clear()
        pull, push = ((graph or {}).get(d) or [] for d in ("pull", "push"))
        if not (pull or push):
            self.plot.set_note("No graph stored for this cycle (saved before graphs were stored, "
                               "or no LVDT stroke was read)")
            return
        self.plot.set_note("")
        for name, pts in (("PULL", pull), ("PUSH", push)):
            self.plot.set_data(name, [p[0] for p in pts], [p[1] for p in pts])
        self.plot.extend_to_data()

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
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(cols)
                for r in self.rows:
                    w.writerow([_fmt(r.get(c)) if r.get(c) is not None else "" for c in cols])
        except OSError as exc:
            messagebox.showerror("Export", f"Could not write the file:\n{exc}", parent=self)
            return
        self.status.config(text=f"Exported {len(self.rows)} rows to {os.path.basename(path)}", fg=theme.PASS)
