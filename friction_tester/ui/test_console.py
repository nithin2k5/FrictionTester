"""Test Console — the measurement screen, laid out like the LabVIEW
"Friction Test Machine" it replaces.

Two cable panels side by side:
    SELECT CABLE -> Loadcell 01
    SHIFT CABLE  -> Loadcell 02
each with its test conditions, test results (PULL1-3 / PUSH1-3, means,
OK/NG), a Load-vs-Stroke graph and live LOAD / STROKE / SPEED readouts.
Underneath, per cable: the front/rear sensor lamps read from the PLC and
the clamp counter.

The page polls connected devices for the live values: LOAD comes from the
loadcell; STROKE and SPEED have no source yet and show "--". It does not
run the automatic test sequence; `CablePanel.show_results`, `set_live` and
`plot` are the hooks that sequence will feed.
"""

import datetime
import os
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

import copy
import csv
import queue as _queue

from . import theme
from .plot import LoadStrokePlot
from ..config import save_settings
from ..plc.modbus_map import ModbusMapping, ModbusPlcBackend
from ..plc.part_flow import SharedCycleFlow, panel_assignments
from ..plc.shared_cycle import STEP_NAMES, CycleTracker, SharedCycleMonitor

NO_DATA = "—"      # shown for a value that was not measured (a measured zero shows 0)

# In simulation the simulator stands in for the PLC: its mapping is exact and
# writing the start request is harmless. These assumptions apply to the
# simulator only and are never written to settings.json.
SIMULATION_PROFILE = {
    "mode": "shared_cycle",
    "shared": {"live_start_enabled": True, "at_cable_selection": "operator_fixture",
               "mt_one_cycle_tests_both": True},
}


def effective_integration(settings, simulator=None):
    """test.integration from settings, with the simulation profile laid over it."""
    cfg = copy.deepcopy(settings["test"]["integration"])
    if simulator is not None:
        cfg["mode"] = SIMULATION_PROFILE["mode"]
        cfg["shared"].update(SIMULATION_PROFILE["shared"])
    return cfg

VALUE_FG = "#ff6fd8"     # measured values (magenta, as on the LabVIEW screen)
FIELD_BG = "#0d1016"
PULL_COLOR = "#4fc3f7"
PUSH_COLOR = "#ff5252"
LAMP_OFF = "#24402a"
LAMP_ON = "#3cff6a"
POLL_MS = 300
SCREENSHOT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                              "screenshots")


def evaluate(pulls, pushes, load_min, load_max):
    """Means and the OK/NG judgement: every |reading| must lie in [min, max]."""
    values = [abs(v) for v in list(pulls) + list(pushes)]
    pull_mean = sum(pulls) / len(pulls) if pulls else 0.0
    push_mean = sum(pushes) / len(pushes) if pushes else 0.0
    ok = bool(values) and all(load_min <= v <= load_max for v in values)
    return {"pull_mean": pull_mean, "push_mean": push_mean,
            "max": max(values) if values else 0.0, "ok": ok}


def _font(size, bold=False):
    return (theme.FONT, size, "bold") if bold else (theme.FONT, size)


class CablePanel(tk.Frame):
    """One cable: conditions, results, graph and live readouts."""

    def __init__(self, master, title, cfg):
        super().__init__(master, bg=theme.PANEL, highlightbackground=theme.BORDER, highlightthickness=1)
        self.cfg = cfg
        self.last_ok = None              # OK/NG of the last measured test, None if not measured

        # Title row with Screen Save
        top = tk.Frame(self, bg=theme.PANEL)
        top.pack(fill="x", padx=10, pady=(6, 2))
        # OK / NG judgement, left of the title
        self.judge = tk.Label(top, text="– – –", bg=FIELD_BG, fg="#4f8cff", font=_font(13, True),
                              relief="solid", bd=1, width=5)
        self.judge.pack(side="left")
        self.max_lbl = tk.Label(top, text="Max", bg=theme.PANEL, fg="white", font=_font(9, True))
        self.max_lbl.pack(side="left", padx=(8, 3))
        self.res = {"max": self._value(top, NO_DATA)}
        self.res["max"].pack(side="left")
        self.max_unit = tk.Label(top, text="kgf", bg=theme.PANEL, fg="white", font=_font(8))
        self.max_unit.pack(side="left", padx=(2, 0))
        tk.Button(top, text="Screen Save", command=self.save_screen, bg="#333", fg="white",
                  activebackground="#444", activeforeground="white", bd=0, padx=8, pady=2,
                  font=_font(8, True), cursor="hand2").pack(side="right")
        tk.Label(top, text=title, bg=theme.PANEL, fg="white", font=_font(14)).pack(side="left", expand=True)
        tk.Frame(self, bg=theme.BORDER, height=1).pack(fill="x", padx=8)
        self.model_lbl = tk.Label(self, text="", bg=theme.PANEL, fg=theme.HEADER_FG,
                                  font=_font(9, True), anchor="w")
        self.model_lbl.pack(fill="x", padx=10, pady=(2, 0))
        self._show_model(cfg.get("model", ""))
        # Where the numbers on this panel come from (PLC channel, serial loadcell, ...)
        self.source_lbl = tk.Label(self, text="", bg=theme.PANEL, fg=theme.MUTED, font=_font(8),
                                   anchor="w", justify="left")
        self.source_lbl.pack(fill="x", padx=10)

        self._build_tables().pack(fill="x", padx=8, pady=4)

        self.plot = LoadStrokePlot(self)
        self.plot.add_series("PULL", PULL_COLOR)
        self.plot.add_series("PUSH", PUSH_COLOR)
        self.plot.pack(fill="both", expand=True, padx=8, pady=(0, 4))

        live = tk.Frame(self, bg=theme.PANEL)
        live.pack(fill="x", padx=8, pady=(2, 6))
        self.live, self.live_units = {}, {}
        for key, label, unit in (("load", "LOAD", "kgf"), ("stroke", "STROKE", "mm"), ("speed", "SPEED", "mm/s")):
            tk.Label(live, text=label, bg=theme.PANEL, fg=theme.HEADER_FG, font=_font(9, True)).pack(side="left")
            self.live[key] = self._value(live, NO_DATA, width=6, size=11)
            self.live[key].pack(side="left", padx=(4, 2))
            self.live_units[key] = tk.Label(live, text=unit, bg=theme.PANEL, fg="white", font=_font(8))
            self.live_units[key].pack(side="left", padx=(0, 8))

    # ----------------------------------------------------------- widgets
    def _value(self, parent, text, width=5, size=10):
        # A 1px highlight ring like the entries', so result boxes and
        # condition boxes are the same height and line up row for row.
        return tk.Label(parent, text=text, bg=FIELD_BG, fg=VALUE_FG, font=_font(size), width=width,
                        relief="solid", bd=1, highlightthickness=1, highlightbackground=theme.BORDER)

    def _entry(self, parent, value, width=5, fmt="{:g}"):
        e = tk.Entry(parent, bg=theme.ENTRY_BG, fg="white", insertbackground="white", justify="center",
                     font=_font(10), width=width, bd=1, relief="solid",
                     highlightbackground=theme.BORDER, highlightthickness=1)
        if value is not None:                # empty (null) conditions stay blank
            e.insert(0, fmt.format(value))
        return e

    def _caption(self, parent, text):
        return tk.Label(parent, text=text, bg=theme.PANEL, fg=theme.TITLE_FG, font=_font(9, True))

    def _small(self, parent, text, fg="white"):
        return tk.Label(parent, text=text, bg=theme.PANEL, fg=fg, font=_font(8))

    def _show_model(self, model):
        self.model_lbl.config(text=f"Model  {model}" if model else "Model  —")

    def load_conditions(self, model, cond):
        """Fill TEST CONDITION from a model (Model Change) and remember it."""
        values = {"speed": cond["speed"], "delay": cond["delay"], "stroke": cond["stroke"],
                  "pos1": cond["positions"][0], "pos2": cond["positions"][1], "pos3": cond["positions"][2],
                  "load_min": cond["load_min"], "load_max": cond["load_max"]}
        for key, value in values.items():
            e = self.ent[key]
            e.delete(0, "end")
            fmt = "{:.2f}" if key.startswith("load") else "{:g}"
            e.insert(0, "" if value is None else fmt.format(value))
        self.cfg.update(cond, model=model)
        self._show_model(model)

    def _build_tables(self):
        """TEST CONDITION and TEST RESULTS in one grid, so their rows line up.

        Columns:  0 label | 1 entry | 2 unit | 3 pos no. | 4 entry | 5 unit | 6 divider |
                  7 PULLn | 8 value | 9 kgf | 10 PUSHn | 11 value | 12 kgf
        Rows:     0 captions | 1 Speed / PULL1 | 2 Delay / PULL2 | 3 Stroke / PULL3 | 4 LOAD / Mean
        """
        f = tk.Frame(self, bg=theme.PANEL)
        cfg = self.cfg
        self.ent = {}
        # Entry and value boxes stretch with their columns (weights set
        # below), so the two tables fill the panel width with no gap.
        cell = {"padx": 2, "pady": 1, "sticky": "ew"}

        def text(col, row, t, bold=False, fg="white", sticky="w", **kw):
            lbl = tk.Label(f, text=t, bg=theme.PANEL, fg=fg, font=_font(9 if bold else 8, bold))
            lbl.grid(row=row, column=col, sticky=sticky, **kw)
            return lbl

        # Captions
        self._caption(f, "TEST CONDITION").grid(row=0, column=0, columnspan=3, sticky="w")
        text(3, 0, "Position", fg=theme.HEADER_FG, sticky="", columnspan=3)
        self._caption(f, "TEST RESULTS").grid(row=0, column=7, columnspan=6, sticky="w")

        # Test conditions, rows 1-3
        for r, (key, label, unit) in enumerate((("speed", "Speed", "mm/s"), ("delay", "Delay", "s"),
                                                ("stroke", "Stroke", "mm")), start=1):
            text(0, r, label)
            self.ent[key] = self._entry(f, cfg[key])
            self.ent[key].grid(row=r, column=1, **cell)
            text(2, r, unit, padx=(0, 3))
            text(3, r, str(r), sticky="e")
            self.ent[f"pos{r}"] = self._entry(f, cfg["positions"][r - 1])
            self.ent[f"pos{r}"].grid(row=r, column=4, **cell)
            text(5, r, "mm")

        # LOAD min ~ max, row 4, sharing the entry columns above
        text(0, 4, "LOAD", bold=True)
        self.ent["load_min"] = self._entry(f, cfg["load_min"], fmt="{:.2f}")
        self.ent["load_min"].grid(row=4, column=1, **cell)
        text(3, 4, "~", sticky="e")
        self.ent["load_max"] = self._entry(f, cfg["load_max"], fmt="{:.2f}")
        self.ent["load_max"].grid(row=4, column=4, **cell)
        text(5, 4, "kgf")

        # Divider between the two tables
        tk.Frame(f, bg=theme.BORDER, width=1).grid(row=0, column=6, rowspan=5, sticky="ns", padx=5)

        # Test results, rows 1-3, then the means on row 4
        self.row_lbls, self.res_units = {}, []
        for i in range(1, 4):
            for col, kind in ((7, "PULL"), (10, "PUSH")):
                self.row_lbls[f"{kind.lower()}{i}"] = text(col, i, f"{kind}{i}")
                self.res[f"{kind.lower()}{i}"] = self._value(f, NO_DATA)
                self.res[f"{kind.lower()}{i}"].grid(row=i, column=col + 1, **cell)
                self.res_units.append(text(col + 2, i, "kgf", padx=(0, 3)))
        for col, kind in ((7, "pull"), (10, "push")):
            self.row_lbls[f"{kind}_mean"] = text(col, 4, "Mean", bold=True)
            self.res[f"{kind}_mean"] = self._value(f, NO_DATA)
            self.res[f"{kind}_mean"].grid(row=4, column=col + 1, **cell)
            self.res_units.append(text(col + 2, 4, "kgf", padx=(0, 3)))

        # Same height for every data row, so the two tables share baselines
        for col in (1, 4, 8, 11):          # condition entries and result values
            f.columnconfigure(col, weight=1, uniform="boxes")
        f.rowconfigure(0, minsize=20)
        for r in range(1, 5):
            f.rowconfigure(r, minsize=26, uniform="rows")
        return f

    # ------------------------------------------------------------- data
    def read_conditions(self):
        """Validated test conditions from the entry boxes (also stored in cfg)."""
        vals = {}
        for k, e in self.ent.items():        # a blank box is an empty (null) condition
            text = e.get().strip()
            try:
                vals[k] = float(text) if text else None
            except ValueError:
                raise ValueError("Test conditions must be numbers (or left empty)")
        if vals["load_min"] is not None and vals["load_max"] is not None and vals["load_min"] > vals["load_max"]:
            raise ValueError("LOAD minimum is above the maximum")
        if (vals["speed"] is not None and vals["speed"] <= 0) or \
                (vals["stroke"] is not None and vals["stroke"] <= 0) or \
                (vals["delay"] is not None and vals["delay"] < 0):
            raise ValueError("Speed and stroke must be > 0, delay ≥ 0")
        self.cfg.update({
            "speed": vals["speed"], "delay": vals["delay"], "stroke": vals["stroke"],
            "positions": [vals["pos1"], vals["pos2"], vals["pos3"]],
            "load_min": vals["load_min"], "load_max": vals["load_max"],
        })
        return self.cfg

    def show_results(self, pulls, pushes):
        """Fill PULL1-3 / PUSH1-3, the means and OK/NG from measured loads."""
        cond = self.read_conditions()
        if cond["load_min"] is None or cond["load_max"] is None:
            raise ValueError("LOAD limits are empty - load a model or enter them before judging")
        for i in range(3):
            self.res[f"pull{i + 1}"].config(text=f"{pulls[i]:.2f}" if i < len(pulls) else "0.00")
            self.res[f"push{i + 1}"].config(text=f"{pushes[i]:.2f}" if i < len(pushes) else "0.00")
        r = evaluate(pulls, pushes, cond["load_min"], cond["load_max"])
        self.res["pull_mean"].config(text=f"{r['pull_mean']:.2f}")
        self.res["push_mean"].config(text=f"{r['push_mean']:.2f}")
        self.res["max"].config(text=f"{r['max']:.2f}")
        self.judge.config(text="OK" if r["ok"] else "NG", fg=theme.PASS if r["ok"] else theme.FAIL)
        self.last_ok = r["ok"]
        return r

    def reset_results(self):
        self.last_ok = None              # no judgement for the current test yet
        for lbl in self.res.values():
            lbl.config(text=NO_DATA)     # "—" = not measured, distinct from a measured 0
        self.judge.config(text="– – –", fg="#4f8cff")
        self.plot.clear()

    def set_source(self, text):
        self.source_lbl.config(text=text)

    def set_result_labels(self, first, second, unit, final_label="Max"):
        """Relabel the result rows (PULL/PUSH, or FWD/RET until the direction is mapped)."""
        for i in range(1, 4):
            self.row_lbls[f"pull{i}"].config(text=f"{first}{i}")
            self.row_lbls[f"push{i}"].config(text=f"{second}{i}")
        for lbl in self.res_units:
            lbl.config(text=unit)
        self.max_lbl.config(text=final_label)
        self.max_unit.config(text=unit)

    def show_plc_channel(self, channel, meas, verdict, mapped, shared_cfg):
        """Show one PLC channel's stored samples, final value and the overall verdict.

        Positions 1-3 go in the result rows; the two extra windows are listed
        separately. Values are raw PLC counts unless a force scale is set.
        Means are not calculated here - the PLC's final value is shown instead.
        """
        roles = shared_cfg.get("direction_role") or {}
        scale = float(shared_cfg.get("force_scale") or 0)
        unit = (shared_cfg.get("force_unit") or "") if scale else "raw"

        def fmt(v):
            if v is None:
                return NO_DATA
            return f"{v * scale:.2f}" if scale else f"{v:d}"

        if roles.get("forward") in ("pull", "push") and roles.get("return") in ("pull", "push") \
                and roles["forward"] != roles["return"]:
            first_dir = "forward" if roles["forward"] == "pull" else "return"
            second_dir = "return" if first_dir == "forward" else "forward"
            self.set_result_labels("PULL", "PUSH", unit, "Final")
        else:
            first_dir, second_dir = "forward", "return"
            self.set_result_labels("FWD", "RET", unit, "Final")
        samples = (meas or {}).get("samples", {}).get(channel, {})
        for i in range(1, 4):
            self.res[f"pull{i}"].config(text=fmt(samples.get(first_dir, {}).get(f"P{i}")))
            self.res[f"push{i}"].config(text=fmt(samples.get(second_dir, {}).get(f"P{i}")))
        self.res["pull_mean"].config(text=NO_DATA)
        self.res["push_mean"].config(text=NO_DATA)
        final = (meas or {}).get("finals", {}).get(channel)
        self.res["max"].config(text=fmt(final))
        self.judge.config(text=verdict, fg=theme.PASS if verdict == "OK" else theme.FAIL)
        self.last_ok = None              # counted per part by the flow, not per panel
        ch_ng = (meas or {}).get("channel_ng", {}).get(channel)
        lo_hi = (meas or {}).get("limits", {}).get(channel)
        extras = []
        for d, tag in (("forward", "F"), ("return", "R")):
            xs = samples.get(d, {})
            extras.append(f"{tag} {fmt(xs.get('X1'))} / {fmt(xs.get('X2'))}")
        who = f"PLC Channel {channel}" + ("" if mapped else " — cable not mapped")
        limit = "" if ch_ng is None else f" · Ch{channel} limit {'NG' if ch_ng else 'OK'}"
        rng = f" ({lo_hi[0]}..{lo_hi[1]})" if lo_hi else ""
        self.set_source(f"Source: {who} · overall PLC verdict{limit}{rng}\n"
                        f"Extra windows (+1000): {'   '.join(extras)}")
        if meas is None:
            self.set_source(f"Source: {who} · overall PLC verdict - stored samples could not be read")

    def set_live_values(self, load, stroke, load_unit, stroke_unit):
        for key, val, unit in (("load", load, load_unit), ("stroke", stroke, stroke_unit)):
            if val is None:
                self.live[key].config(text=NO_DATA)
            elif isinstance(val, float):
                self.live[key].config(text=f"{val:.2f}")
            else:
                self.live[key].config(text=str(val))
            self.live_units[key].config(text=unit)
        self.live["speed"].config(text=NO_DATA)

    def set_live(self, load=None, stroke=None, speed=None):
        for key, val, fmt in (("load", load, "{:.2f}"), ("stroke", stroke, "{:.2f}"), ("speed", speed, "{:.1f}")):
            self.live[key].config(text=NO_DATA if val is None else fmt.format(val))

    def save_screen(self):
        """Save a PNG of this panel to ./screenshots."""
        try:
            from PIL import ImageGrab
        except ImportError:
            messagebox.showerror("Screen Save", "Pillow is not installed (pip install pillow).", parent=self)
            return
        self.update_idletasks()
        # Tk reports logical pixels; the grab is in physical pixels. Scale by
        # the ratio between the two so the crop is right at any Windows zoom.
        full = ImageGrab.grab()
        k = full.width / self.winfo_screenwidth()
        x, y = self.winfo_rootx(), self.winfo_rooty()
        box = tuple(int(v * k) for v in (x, y, x + self.winfo_width(), y + self.winfo_height()))
        os.makedirs(SCREENSHOT_DIR, exist_ok=True)
        path = os.path.join(SCREENSHOT_DIR, datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + ".png")
        full.crop(box).save(path)
        messagebox.showinfo("Screen Save", f"Saved:\n{path}", parent=self)


class TestConsolePage(tk.Frame):
    SIDES = (("select", "SELECT CABLE", "SL"), ("shift", "SHIFT CABLE", "SH"))

    def __init__(self, master, settings, plc, loadcells, simulator=None):
        super().__init__(master, bg=theme.BG)
        self.settings = settings
        self.plc = plc
        self.loadcells = loadcells
        self.simulator = simulator
        self.integration = effective_integration(settings, simulator)
        self.shared = self.integration["mode"] == "shared_cycle"
        self.monitor = None
        self._polling = False
        self._alive = True
        self._hold_until = 0.0

        self.counters = settings["test"]["counters"]
        self._roll_day()
        # Test flow state (see start_flow / on_scan)
        self._flow = "idle"
        self._pending = set()            # cables whose test is still running
        self._stop_event = threading.Event()

        self._build_status_bar()
        body = tk.Frame(self, bg=theme.BG)
        body.pack(fill="both", expand=True, pady=(6, 0))
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)

        left = tk.Frame(body, bg=theme.BG)
        left.grid(row=0, column=0, sticky="nsew")
        if self.shared:
            self._build_plc_strip(left)      # left column only, so the side panel keeps its height
        panels = tk.Frame(left, bg=theme.BG)
        panels.pack(fill="both", expand=True, pady=(0, 6))
        self.panels = []
        for i, (key, title, _) in enumerate(self.SIDES):
            p = CablePanel(panels, title, settings["test"][key])
            p.grid(row=0, column=i, sticky="nsew", padx=(0, 4) if i == 0 else (4, 0))
            panels.columnconfigure(i, weight=1, uniform="panel")
            self.panels.append(p)
        panels.rowconfigure(0, weight=1)
        self._build_bottom(left)

        self._build_side_panel(body).grid(row=0, column=1, sticky="ns", padx=(8, 0))
        self._show_current_model()

        self.bind("<Destroy>", self._on_destroy)
        self.after(200, self._poll)
        self._load_model_list()
        if self.shared:
            self._start_shared_cycle()
        else:
            for n, p in enumerate(self.panels):
                p.set_source(f"Source: serial loadcell L{n + 1} (live LOAD) · results: start/done handshake")

    # ------------------------------------------------------------ layout
    def _build_status_bar(self):
        bar = tk.Frame(self, bg=theme.BG)
        bar.pack(fill="x")
        tag = tk.Frame(bar, bg=theme.PANEL, highlightbackground=theme.TITLE_FG, highlightthickness=0)
        tag.pack(side="left")
        tk.Label(tag, text="📈  Measurement Screen", bg=theme.PANEL, fg="white", font=_font(14),
                 padx=16, pady=6).pack()
        tk.Frame(tag, bg=theme.TITLE_FG, height=3).pack(fill="x")

        tk.Button(bar, text="⏻  EXIT", command=self.exit_app, bg="#b71c1c", fg="white",
                  activebackground="#d32f2f", activeforeground="white", bd=0, padx=16, pady=5,
                  font=_font(10, True), cursor="hand2").pack(side="right")
        tk.Button(bar, text="Save Conditions", command=self.save_conditions, bg=theme.SAVE, fg="white",
                  activebackground=theme.SAVE, activeforeground="white", bd=0, padx=14, pady=5,
                  font=_font(10, True), cursor="hand2").pack(side="right", padx=(0, 8))
        tk.Button(bar, text="Clear Results", command=self.clear_results, bg="#333", fg="white",
                  activebackground="#444", activeforeground="white", bd=0, padx=14, pady=5,
                  font=_font(10, True), cursor="hand2").pack(side="right", padx=8)

        self.status = tk.Label(bar, text="System initialization", bg=FIELD_BG, fg="white", font=_font(11),
                               relief="solid", bd=1, padx=12, pady=5, anchor="w")
        self.status.pack(side="left", fill="x", expand=True, padx=16)

    # ---------------------------------------------------- shared PLC cycle
    def _build_plc_strip(self, parent):
        """One line of live PLC cycle state above the cable panels."""
        strip = tk.Frame(parent, bg="#10141b", highlightbackground=theme.BORDER, highlightthickness=1)
        strip.pack(fill="x", pady=(0, 4))
        self.plc_badge = tk.Label(strip, text="PLC", bg="#10141b", fg=theme.MUTED, font=_font(9, True), padx=8)
        self.plc_badge.pack(side="left")
        self.plc_strip = tk.Label(strip, text="Connecting…", bg="#10141b", fg="#c9d1dc", font=_font(9),
                                  anchor="w")
        self.plc_strip.pack(side="left", fill="x", expand=True)
        self.plc_mode = tk.Label(strip, text="", bg="#10141b", fg=theme.MUTED, font=_font(8, True), padx=8)
        self.plc_mode.pack(side="right")

    def _start_shared_cycle(self):
        shared = self.integration["shared"]
        if self.simulator is not None:
            backend = self.simulator
            mapping_text = "simulator"
        else:
            mapping = ModbusMapping(shared.get("mapping"))
            backend = ModbusPlcBackend(self.plc, mapping, allow_writes=bool(shared.get("live_start_enabled")))
            mapping_text = "map " + ("verified" if mapping.verified else "UNVERIFIED")
        self.backend = backend
        writes = lambda: bool(getattr(backend, "can_write", False))
        tracker = CycleTracker(start_timeout=float(shared.get("start_timeout", 10.0)))
        self.monitor = SharedCycleMonitor(backend, tracker, can_write=writes,
                                          interval=float(shared.get("poll_interval", 0.25)))
        self.flow = SharedCycleFlow(shared, self, self.monitor, writes)
        mode = "SIMULATION · " if self.simulator is not None else ""
        self.plc_mode.config(text=f"{mode}{mapping_text} · live start {'ON' if writes() else 'OFF'}",
                             fg=theme.BUSY if self.simulator is not None else
                             (theme.MUTED if writes() else theme.FAIL))
        assign = panel_assignments(shared, "MT", "both")
        roles = shared.get("direction_role") or {}
        directions_mapped = {roles.get("forward"), roles.get("return")} == {"pull", "push"}
        unit = (shared.get("force_unit") or "") if float(shared.get("force_scale") or 0) else "raw"
        for p in self.panels:                      # labels say what is known, before the first result
            if directions_mapped:
                p.set_result_labels("PULL", "PUSH", unit, "Final")
            else:
                p.set_result_labels("FWD", "RET", unit, "Final")
        for panel_idx, ch, mapped in assign:
            self.panels[panel_idx].set_source(
                f"Source: PLC Channel {ch}" + ("" if mapped else " — cable not mapped") + " · shared cycle")
        self._live_assign = assign
        self.monitor.start()
        self.after(100, self._drain_monitor)

    def _drain_monitor(self):
        """UI thread: take what the monitor thread produced (it never touches Tk)."""
        if not self._alive:
            return
        last = None
        try:
            while True:
                snap, events = self.monitor.queue.get_nowait()
                last = (snap,)
                for ev in events:
                    self.flow.on_event(ev)
                    self.flow.on_global_event(ev)
                    if ev.kind == "connected" and ev.data.get("stale_verdict"):
                        self._set_status(f"PLC connected - a previous {ev.data['stale_verdict']} verdict is "
                                         "still latched; it will not be used for the next part", theme.MUTED, 5)
                    elif ev.kind == "counter_reset":
                        self._set_status("PLC OK/NG counters were reset on the PLC", theme.MUTED, 5)
        except _queue.Empty:
            pass
        if last is not None:
            self._show_plc_snapshot(last[0])
        self.after(100, self._drain_monitor)

    def _show_plc_snapshot(self, s):
        shared = self.integration["shared"]
        if s is None:
            self.plc_badge.config(fg=theme.FAIL)
            self.plc_strip.config(text="PLC not reachable - monitoring paused, no results can be confirmed")
            for panel_idx, _ch, _m in self._live_assign:
                self.panels[panel_idx].set_live_values(None, None, "raw", "raw")
            return
        self.plc_badge.config(fg=theme.PASS if s.step == 0 else theme.UPDATE)
        state = "RUNNING" if s.step else "IDLE"
        verdict = s.verdict_bits or "—"
        self.plc_strip.config(text=(
            f"Step {s.step} {STEP_NAMES.get(s.step, '')} · {state} · Rep {s.rep_count}/{s.rep_target} · "
            f"Ch1 {s.live[1]} · Ch2 {s.live[2]} · Pos {s.position} (raw) · "
            f"latched {verdict} · PLC OK {s.ok_count} NG {s.ng_count} Total {s.total_count}"))
        fscale = float(shared.get("force_scale") or 0)
        pscale = float(shared.get("position_scale") or 0)
        funit = (shared.get("force_unit") or "") if fscale else "raw"
        punit = (shared.get("position_unit") or "") if pscale else "raw"
        pos = s.position * pscale if pscale else s.position
        for panel_idx, ch, _mapped in self._live_assign:
            load = s.live[ch] * fscale if fscale else s.live[ch]
            self.panels[panel_idx].set_live_values(load, pos, funit, punit)

    # ---- callbacks used by plc.part_flow.SharedCycleFlow
    def set_state(self, state):
        self._set_flow(state)

    def message(self, text, level="info"):
        color = {"pass": theme.PASS, "fail": theme.FAIL, "busy": theme.BUSY, "info": theme.UPDATE}.get(level, "white")
        self._set_status(text, color, hold=10 if level == "fail" else 4)

    def clear_panels(self, indices):
        for i in indices:
            self.panels[i].reset_results()
            self.panels[i].judge.config(text="TEST", fg=theme.BUSY)

    def show_cycle(self, stage, assignments, ev):
        meas = ev.data.get("measurements")
        for panel_idx, ch, mapped in assignments:
            self.panels[panel_idx].show_plc_channel(ch, meas, ev.data["verdict"], mapped,
                                                    self.integration["shared"])

    def part_done(self, record):
        self.record_part(record)

    def record_part(self, record):
        """One confirmed part: production counters once, and one row in the Saved-to folder."""
        self._roll_day()
        c = self.counters
        key = "ok" if record["verdict"] == "OK" else "ng"
        c[f"{key}_day"] += 1
        c[f"{key}_total"] += 1
        c["complete"] += 1
        for cable in record.get("cables", []):
            c[f"saved_{cable}"] += 1
        self._show_counters()
        self._save_counters()
        try:
            self._append_result_row(record)
        except Exception as exc:
            self._set_status(f"Part counted, but the result file could not be written: {exc}", theme.FAIL, 10)

    def _result_dir(self):
        path = self.save_dir.get().strip() or "data"
        if not os.path.isabs(path):
            path = os.path.join(os.path.dirname(SCREENSHOT_DIR), path)
        os.makedirs(path, exist_ok=True)
        return path

    def _append_result_row(self, record):
        path = os.path.join(self._result_dir(), f"results_{datetime.date.today():%Y%m%d}.csv")
        new = not os.path.exists(path)
        stages = "; ".join(
            f"{s['stage']}={s['verdict']} (PLC OK {s.get('ok_count')}/NG {s.get('ng_count')}, "
            f"finals {s.get('finals')}, {s.get('evidence')})" for s in record["stages"])
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["time", "model", "type", "label_select", "label_shift", "verdict", "verdict_basis",
                            "stages"])
            w.writerow([record["time"], record["model"], record["type"], record["label_select"],
                        record["label_shift"], record["verdict"], record["verdict_basis"], stages])

    def _small_button(self, parent, text, command, bg="#e0e0e0", fg="black", width=12):
        return tk.Button(parent, text=text, command=command, bg=bg, fg=fg, activebackground=bg,
                         activeforeground=fg, bd=0, width=width, pady=3, font=_font(9, True), cursor="hand2")

    def _build_bottom(self, parent):
        bottom = tk.Frame(parent, bg=theme.BG)
        bottom.pack(fill="x")
        self.lamps, self.clamps, self.servo_btns = [], [], []
        self._servo_on = [False, False]
        for i, (key, _title, short) in enumerate(self.SIDES):
            cfg = self.settings["test"][key]
            box = tk.Frame(bottom, bg=theme.PANEL, highlightbackground=theme.BORDER, highlightthickness=1)
            box.grid(row=0, column=i, sticky="nsew", padx=(0, 4) if i == 0 else (4, 0))
            bottom.columnconfigure(i, weight=1, uniform="panel")

            # Actuator Control: servo on/off, alarm reset, move origin (PLC bits)
            act = tk.LabelFrame(box, text=" Actuator Control ", bg=theme.PANEL, fg="white", font=_font(9),
                                bd=1, relief="solid", labelanchor="n", padx=6, pady=3)
            act.pack(side="left", padx=(8, 6), pady=4)
            sbtn = self._small_button(act, "⚙ SERVO ON", lambda k=key, n=i: self.toggle_servo(k, n), width=11)
            sbtn.pack(side="left", padx=(0, 4))
            self.servo_btns.append(sbtn)
            self._small_button(act, "ALARM RESET", lambda k=key: self.plc_pulse(f"alarm_reset_{k}", "Alarm reset"),
                               bg="#4a505c", fg="white", width=11).pack(side="left", padx=(0, 4))
            self._small_button(act, "↺ Move Origin", lambda k=key: self.plc_pulse(f"origin_{k}", "Move origin"),
                               width=11).pack(side="left")

            sens = tk.Frame(box, bg=theme.PANEL)
            sens.pack(side="left", padx=6, pady=4)
            lamps = {}
            for name in ("Front Sensor", "Rear Sensor"):
                row = tk.Frame(sens, bg=theme.PANEL)
                row.pack(anchor="w", pady=1)
                cv = tk.Canvas(row, width=16, height=16, bg=theme.PANEL, highlightthickness=0)
                dot = cv.create_oval(2, 2, 14, 14, fill=LAMP_OFF, outline="#111")
                cv.pack(side="left")
                tk.Label(row, text=name, bg=theme.PANEL, fg="#4fa3ff", font=_font(9)).pack(side="left", padx=3)
                lamps[name] = (cv, dot)
            self.lamps.append(lamps)

            clamp = tk.Frame(box, bg=theme.PANEL)
            clamp.pack(side="left", padx=4)
            tk.Label(clamp, text=f"{short}-Clamp", bg=theme.PANEL, fg="white", font=_font(9)).pack(anchor="w")
            row = tk.Frame(clamp, bg=theme.PANEL)
            row.pack(anchor="w")
            sp = tk.Spinbox(row, from_=0, to=999999, width=6, justify="center", font=_font(10),
                            bg=FIELD_BG, fg=VALUE_FG, buttonbackground="#333", relief="solid", bd=1,
                            insertbackground="white")
            sp.delete(0, "end")
            sp.insert(0, str(cfg.get("clamp_count", 0)))
            sp.pack(side="left")
            tk.Label(row, text="times", bg=theme.PANEL, fg="white", font=_font(8)).pack(side="left", padx=3)
            self.clamps.append(sp)

    # ------------------------------------------------------- side panel
    # Same card style as the COM Setting page: a title strip with an orange
    # accent, a dark body, large coloured number tiles.
    SIDE_W = 250
    CARD = "#141922"
    CARD_HEAD = "#1b212c"
    TILE = "#0d1117"

    def _side_card(self, parent, title, right=None):
        """A card with a title strip; `right` builds an optional widget on the strip."""
        card = tk.Frame(parent, bg=self.CARD, highlightbackground=theme.BORDER, highlightthickness=1)
        card.pack(fill="x", pady=(0, 5))
        head = tk.Frame(card, bg=self.CARD_HEAD)
        head.pack(fill="x")
        tk.Frame(head, bg=theme.TITLE_FG, width=3).pack(side="left", fill="y")
        tk.Label(head, text=title.upper(), bg=self.CARD_HEAD, fg="#c9d1dc", font=_font(8, True)).pack(
            side="left", padx=8, pady=4)
        if right:
            right(head).pack(side="right", padx=6)
        body = tk.Frame(card, bg=self.CARD)
        body.pack(fill="x", padx=8, pady=6)
        return body

    def _link(self, parent, text, command, fg=theme.HEADER_FG):
        """A small flat text button for secondary actions (reset, open)."""
        return tk.Button(parent, text=text, command=command, bg=parent["bg"], fg=fg, activebackground=parent["bg"],
                         activeforeground="white", bd=0, font=_font(8, True), cursor="hand2", padx=2, pady=0)

    def _tile(self, parent, caption, value, color, size=18):
        """A number tile: big value with a small caption under it. Returns the value label."""
        t = tk.Frame(parent, bg=self.TILE, highlightbackground="#232a36", highlightthickness=1)
        val = tk.Label(t, text=str(value), bg=self.TILE, fg=color, font=_font(size, True))
        val.pack(pady=(2, 0))
        tk.Label(t, text=caption, bg=self.TILE, fg=theme.MUTED, font=_font(7, True)).pack(pady=(0, 3))
        return t, val

    def _build_side_panel(self, parent):
        side = tk.Frame(parent, bg=theme.BG, width=self.SIDE_W)
        side.pack_propagate(False)
        c = self.counters

        # ---- Model -----------------------------------------------------
        body = self._side_card(side, "Model")
        self.current_model = tk.Label(body, text=self.settings["test"]["select"].get("model") or "No model loaded",
                                      bg=self.TILE, fg=theme.TITLE_FG, font=_font(12, True), anchor="w",
                                      padx=8, pady=3, highlightbackground="#232a36", highlightthickness=1)
        # (the model name is shown in the MODEL NO box below; this label is kept for status text only)
        # Model number box: type or scan a model number + Enter to fetch it from the database
        mrow = tk.Frame(body, bg=self.CARD)
        mrow.pack(fill="x")
        tk.Label(mrow, text="MODEL NO", bg=self.CARD, fg=theme.MUTED, font=_font(8, True)).pack(side="left")
        self.model_entry = tk.Entry(mrow, bg=self.TILE, fg="white", insertbackground="white", font=_font(10),
                                    relief="flat", bd=0, highlightthickness=1, highlightbackground="#232a36",
                                    highlightcolor=theme.UPDATE)
        self.model_entry.pack(side="left", fill="x", expand=True, padx=(6, 0), ipady=2)
        self.model_entry.bind("<Return>", lambda _e: self.fetch_model(self.model_entry.get()))
        self.part_lbl = tk.Label(body, text="Part —", bg=self.CARD, fg="#c9d1dc", font=_font(9), anchor="w")
        self.part_lbl.pack(fill="x", pady=(1, 0))
        lb_frame = tk.Frame(body, bg=self.CARD)
        lb_frame.pack(fill="x", pady=(2, 3))
        self.model_list = tk.Listbox(lb_frame, height=2, bg=self.TILE, fg="#d6dbe4", font=_font(9),
                                     selectbackground="#2a4a7a", selectforeground="white", relief="flat", bd=0,
                                     highlightthickness=1, highlightbackground="#232a36", highlightcolor="#2a4a7a",
                                     activestyle="none", exportselection=False)
        sb = ttk.Scrollbar(lb_frame, orient="vertical", command=self.model_list.yview)
        self.model_list.config(yscrollcommand=sb.set)
        self.model_list.pack(side="left", fill="x", expand=True)
        sb.pack(side="right", fill="y")
        self.model_list.bind("<Double-Button-1>", lambda _e: self.model_change())
        self._models = []
        tk.Button(body, text="MODEL CHANGE", command=self.model_change, bg=theme.UPDATE, fg="white",
                  activebackground="#1e88e5", activeforeground="white", bd=0, pady=2, font=_font(9, True),
                  cursor="hand2").pack(fill="x")

        # ---- Production: target / complete + progress bar ----------------
        body = self._side_card(side, "Production")
        row = tk.Frame(body, bg=self.CARD)
        row.pack(fill="x")
        row.columnconfigure((0, 1), weight=1, uniform="prod")
        tgt = tk.Frame(row, bg=self.TILE, highlightbackground="#232a36", highlightthickness=1)
        tgt.grid(row=0, column=0, sticky="nsew", padx=(0, 3))
        # Plain text box, whole numbers only; empty = target not set (RUN refuses to start)
        self.target = tk.Entry(tgt, width=7, justify="center", font=_font(16, True), bg=self.TILE, fg="white",
                               relief="flat", bd=0, insertbackground="white", highlightthickness=1,
                               highlightbackground=self.TILE, highlightcolor=theme.UPDATE)
        digits_only = self.target.register(lambda p: p == "" or (p.isdigit() and len(p) <= 6))
        self.target.configure(validate="key", validatecommand=(digits_only, "%P"))
        if c.get("target"):
            self.target.insert(0, str(c["target"]))
        self.target.bind("<Return>", lambda _e: self.set_target())
        self.target.bind("<KP_Enter>", lambda _e: self.set_target())
        self.target.bind("<FocusOut>", lambda _e: self._discard_target_edit())
        self.target.pack(pady=(2, 0), padx=6, fill="x")
        tk.Label(tgt, text="TARGET", bg=self.TILE, fg=theme.MUTED, font=_font(7, True)).pack(pady=(0, 3))
        t, self.lbl_complete = self._tile(row, "COMPLETE", c["complete"], "#4fa3ff", size=16)
        t.grid(row=0, column=1, sticky="nsew", padx=(3, 0))
        self.progress = tk.Canvas(body, height=8, bg=self.TILE, highlightthickness=0)
        self.progress.pack(fill="x", pady=(6, 0))
        self.progress.bind("<Configure>", lambda _e: self._draw_progress())

        # ---- Results: O.K / N.G, today large, cumulative small -----------
        def reset_links(head):
            f = tk.Frame(head, bg=self.CARD_HEAD)
            self._link(f, "Reset total", lambda: self.reset_counts("total")).pack(side="right")
            self._link(f, "Reset today", lambda: self.reset_counts("day")).pack(side="right", padx=(0, 6))
            return f

        body = self._side_card(side, "Results", right=reset_links)
        row = tk.Frame(body, bg=self.CARD)
        row.pack(fill="x")
        row.columnconfigure((0, 1), weight=1, uniform="res")
        t, ok_day = self._tile(row, "O.K  TODAY", c["ok_day"], theme.PASS, size=20)
        t.grid(row=0, column=0, sticky="nsew", padx=(0, 3))
        t, ng_day = self._tile(row, "N.G  TODAY", c["ng_day"], theme.FAIL, size=20)
        t.grid(row=0, column=1, sticky="nsew", padx=(3, 0))
        tot = tk.Frame(body, bg=self.CARD)
        tot.pack(fill="x", pady=(6, 0))
        tk.Label(tot, text="Cumulative  O.K / N.G", bg=self.CARD, fg=theme.MUTED, font=_font(8)).pack(side="left")
        ok_total = tk.Label(tot, text=str(c["ok_total"]), bg=self.CARD, fg=theme.PASS, font=_font(10, True))
        ok_total.pack(side="left", padx=(8, 0))
        tk.Label(tot, text="/", bg=self.CARD, fg=theme.MUTED, font=_font(9)).pack(side="left", padx=3)
        ng_total = tk.Label(tot, text=str(c["ng_total"]), bg=self.CARD, fg=theme.FAIL, font=_font(10, True))
        ng_total.pack(side="left")
        self.lbl_ok = [ok_day, ok_total]
        self.lbl_ng = [ng_day, ng_total]

        # ---- Saved data --------------------------------------------------
        body = self._side_card(side, "Saved Data",
                               right=lambda p: self._link(p, "📂 OPEN", self.open_saved, fg="#4fa3ff"))
        self.save_dir = tk.Entry(body, bg=self.TILE, fg="#d6dbe4", insertbackground="white", font=_font(9),
                                 relief="flat", bd=0, highlightthickness=1, highlightbackground="#232a36")
        self.save_dir.insert(0, self.settings["test"]["save_dir"])
        self.save_dir.bind("<FocusOut>", lambda _e: self._save_counters())
        self.save_dir.pack(fill="x", ipady=3)
        # Saved counts on one compact line: "Saved   SELECT 23   SHIFT 23"
        row = tk.Frame(body, bg=self.CARD)
        row.pack(fill="x", pady=(5, 0))
        tk.Label(row, text="Saved", bg=self.CARD, fg=theme.MUTED, font=_font(8)).pack(side="left")
        self.lbl_saved = []
        for name, value in (("SHIFT", c["saved_shift"]), ("SELECT", c["saved_select"])):
            lbl = tk.Label(row, text=str(value), bg=self.CARD, fg="white", font=_font(11, True))
            lbl.pack(side="right")
            tk.Label(row, text=name, bg=self.CARD, fg=theme.MUTED, font=_font(8, True)).pack(
                side="right", padx=(12, 4))
            self.lbl_saved.insert(0, lbl)          # keep order: [SELECT, SHIFT]

        # ---- Scan results (barcode scanner input, one per cable) ---------
        # The title strip shows the test flow's current step.
        def step_label(head):
            self.flow_lbl = tk.Label(head, text="Press RUN", bg=self.CARD_HEAD, fg=theme.MUTED,
                                     font=_font(8, True))
            return self.flow_lbl

        body = self._side_card(side, "Scan Result", right=step_label)
        body.columnconfigure(1, weight=1)
        self.scan_entries = {}
        for r, (key, label) in enumerate((("select", "SELECT"), ("shift", "SHIFT"))):
            tk.Label(body, text=label, bg=self.CARD, fg=theme.MUTED, font=_font(8, True), width=7,
                     anchor="w").grid(row=r, column=0, sticky="w", pady=2)
            e = tk.Entry(body, bg=self.TILE, fg="white", insertbackground="white", font=_font(10),
                         relief="flat", bd=0, highlightthickness=1, highlightbackground="#232a36",
                         highlightcolor=theme.UPDATE)
            e.grid(row=r, column=1, sticky="ew", pady=1, ipady=1)
            # A barcode scanner types the label and ends with Enter.
            e.bind("<Return>", lambda _e, k=key: self.on_scan(k))
            self.scan_entries[key] = e

        # ---- Test control ------------------------------------------------
        ctl = tk.Frame(side, bg=theme.BG)
        ctl.pack(fill="x", side="bottom")
        # One row: RUN | STOP | ORIGIN (RUN and STOP wider)
        for col, weight in enumerate((4, 4, 3)):
            ctl.columnconfigure(col, weight=weight, uniform="ctl")
        for col, (text, cmd, bg, abg) in enumerate((
                ("▶ RUN", self.start_flow, theme.PASS, "#43a047"),
                ("■ STOP", self.stop_flow, theme.FAIL, "#e53935"),
                ("↺ ORIGIN", lambda: self.plc_pulse("move_origin", "Move origin"), "#2a313d", "#353d4b"))):
            tk.Button(ctl, text=text, command=cmd, bg=bg, fg="white", activebackground=abg, activeforeground="white",
                      bd=0, pady=5, font=_font(10 if col < 2 else 9, True), cursor="hand2").grid(
                row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 3, 0))

        self.after(50, self._draw_progress)
        return side

    def _draw_progress(self):
        """Complete / Target as a bar; full and green once the target is reached."""
        cv = self.progress
        cv.delete("all")
        w = cv.winfo_width()
        target, done = self.counters.get("target", 0), self.counters.get("complete", 0)
        if w <= 1 or target <= 0:
            return
        frac = min(done / target, 1.0)
        cv.create_rectangle(0, 0, int(w * frac), 8, fill=theme.PASS if frac >= 1 else "#4fa3ff", width=0)

    # ----------------------------------------------------------- actions
    def save_conditions(self):
        try:
            for i, ((key, _t, _s), panel) in enumerate(zip(self.SIDES, self.panels)):
                panel.read_conditions()
                count = self.clamps[i].get().strip()
                self.settings["test"][key]["clamp_count"] = int(count) if count.isdigit() else 0
            save_settings(self.settings)
        except Exception as exc:
            messagebox.showerror("Save Conditions", str(exc), parent=self)
            return
        self._set_status("Test conditions saved", theme.PASS)

    def clear_results(self):
        for p in self.panels:
            p.reset_results()

    def exit_app(self):
        """EXIT: close the app the same way as the window's close button."""
        if not messagebox.askyesno("Exit", "Close the Friction Tester?", parent=self):
            return
        top = self.winfo_toplevel()
        handler = top.protocol("WM_DELETE_WINDOW")
        if handler:
            top.tk.eval(handler)
        else:
            top.destroy()

    # ------------------------------------------------- PLC command bits
    def _command_address(self, command, label):
        """The PLC bit for a button, or None (status says why)."""
        addr = self.settings["test"]["commands"].get(command, "").strip()
        if not addr:
            self._set_status(f"{label}: no PLC address set (settings.json → test.commands.{command})",
                             theme.BUSY)
            return None
        if not self.plc.is_open:
            self._set_status(f"{label}: PLC not connected", theme.FAIL)
            return None
        return addr

    def plc_pulse(self, command, label):
        """Momentary command: set the bit, hold 0.3 s, clear it."""
        addr = self._command_address(command, label)
        if not addr:
            return

        def run():
            try:
                self.plc.write_bit(addr, True)
                time.sleep(0.3)
                self.plc.write_bit(addr, False)
                self.after(0, lambda: self._set_status(f"{label} sent ({addr})", theme.PASS))
            except Exception as exc:
                self.after(0, lambda e=exc: self._set_status(f"{label}: {e}", theme.FAIL))

        threading.Thread(target=run, daemon=True).start()

    def toggle_servo(self, key, n):
        """Servo ON/OFF: a maintained PLC bit."""
        label = f"{self.SIDES[n][1]} servo"
        addr = self._command_address(f"servo_{key}", label)
        if not addr:
            return
        turn_on = not self._servo_on[n]

        def run():
            try:
                self.plc.write_bit(addr, turn_on)
            except Exception as exc:
                self.after(0, lambda e=exc: self._set_status(f"{label}: {e}", theme.FAIL))
                return
            self._servo_on[n] = turn_on
            self.after(0, self._show_servo_buttons)
            self.after(0, lambda: self._set_status(f"{label} {'ON' if turn_on else 'OFF'}", theme.PASS))

        threading.Thread(target=run, daemon=True).start()

    def _show_servo_buttons(self):
        for n, btn in enumerate(self.servo_btns):
            on = self._servo_on[n]
            btn.config(text="⚙ SERVO OFF" if on else "⚙ SERVO ON",
                       bg=theme.PASS if on else "#e0e0e0", activebackground=theme.PASS if on else "#e0e0e0",
                       fg="white" if on else "black")

    # ------------------------------------------------------- model set
    def _load_model_list(self):
        """Fill the Model Set list from TBL_MODEL_MASTER (on a worker thread)."""
        # The page is built before the Tk main loop starts, so the worker
        # must not call Tk itself; it leaves the result here and the main
        # thread picks it up.
        result = {}

        def worker():
            try:
                from .. import db
                db.init_database()
                result["rows"], result["error"] = db.list_models(), None
            except Exception as exc:
                result["rows"], result["error"] = [], exc

        def collect():
            if not self._alive:
                return
            if "rows" in result:
                self._show_model_list(result["rows"], result["error"])
            else:
                self.after(100, collect)

        threading.Thread(target=worker, daemon=True).start()
        self.after(100, collect)

    def _show_model_list(self, rows, error):
        if not self._alive:
            return
        self._models = rows
        self.model_list.delete(0, "end")
        if error is not None:
            self.model_list.insert("end", "(database unavailable)")
            return
        for r in rows:
            part = r.get("MM_PART_NUMBER") or ""
            self.model_list.insert("end", f"{r['MM_MODEL']}   {part}".rstrip())
        current = self.settings["test"]["select"].get("model")
        for i, r in enumerate(rows):
            if r["MM_MODEL"] == current:
                self.model_list.selection_set(i)
                self.model_list.see(i)

    def model_change(self):
        """Load the selected model: its SELECT CABLE and SHIFT CABLE conditions."""
        if self._flow != "idle":
            self._set_status("Model Change: press STOP first — a test is in progress", theme.BUSY)
            return
        sel = self.model_list.curselection()
        if not sel or sel[0] >= len(self._models):
            self._set_status("Model Change: select a model in the list first", theme.BUSY)
            return
        self.fetch_model(self._models[sel[0]]["MM_MODEL"])

    def fetch_model(self, model):
        """Fetch a model's part and specifications from TBL_MODEL_MASTER, then load them."""
        model = (model or "").strip()
        if not model:
            return
        if self._flow != "idle":
            self._set_status("Model Change: press STOP first — a test is in progress", theme.BUSY)
            return
        result = {}

        def worker():
            try:
                from .. import db
                result["row"], result["error"] = db.get_model(model), None
            except Exception as exc:
                result["row"], result["error"] = None, exc

        def collect():
            if not self._alive:
                return
            if "row" not in result:
                self.after(100, collect)
                return
            if result["error"] is not None:
                messagebox.showerror("Model", f"Could not read the model database:\n{result['error']}", parent=self)
            elif result["row"] is None:
                messagebox.showerror("Model not found",
                                     f"Model {model} is not in Model Settings.\nCheck the model number.", parent=self)
            else:
                self._apply_model(result["row"])

        self._set_status(f"Fetching model {model}…", theme.UPDATE)
        threading.Thread(target=worker, daemon=True).start()
        self.after(100, collect)

    def _apply_model(self, row):
        """Load a fetched model: part number, type and both cables' specifications."""

        def num(v):
            return None if v is None else float(v)

        for (key, _title, _s), panel, test in zip(self.SIDES, self.panels, ("SELECT", "SHIFT")):
            g = lambda attr: num(row.get(f"MM_{test}_{attr}"))
            cond = {"speed": g("SPEED"), "delay": g("DELAY_TIME"), "stroke": g("STROKE"),
                    "positions": [g("POSITION_1"), g("POSITION_2"), g("POSITION_3")],
                    "load_min": g("LOAD_MIN"), "load_max": g("LOAD_MAX")}
            panel.load_conditions(row["MM_MODEL"], cond)
            panel.reset_results()
        model_type = (row.get("MM_TYPE") or "").strip().upper()
        self.settings["test"]["model_type"] = model_type
        self.settings["test"]["part_number"] = (row.get("MM_PART_NUMBER") or "").strip()
        self._show_current_model()
        save_settings(self.settings)
        self._set_status(f"Model changed to {row['MM_MODEL']}" + (f" ({model_type})" if model_type else ""),
                         theme.PASS)

    def _show_current_model(self):
        t = self.settings["test"]
        model, mtype = t["select"].get("model"), t.get("model_type", "")
        self.current_model.config(text=(f"{model}  ·  {mtype}" if mtype else model) if model else "No model loaded")
        part = t.get("part_number", "")
        self.part_lbl.config(text=(f"Part  {part}" if part else "Part —") + (f"   ·   Type  {mtype}" if mtype else ""))
        self.model_entry.delete(0, "end")
        if model:
            self.model_entry.insert(0, model)

    def _label_matches_part(self, label):
        """A scanned label must contain the loaded model's part number (spaces/hyphens ignored)."""
        norm = lambda x: "".join(ch for ch in x.upper() if ch.isalnum())
        part = self.settings["test"].get("part_number", "")
        return bool(part) and norm(part) in norm(label)

    # ------------------------------------------------------- test flow
    # AT: scan SELECT label -> SELECT test -> scan SHIFT label -> SHIFT test.
    # MT: scan one label -> SELECT and SHIFT tests run at the same time.
    # After a part completes the flow waits for the next part's first scan;
    # STOP returns it to idle.
    FLOW_TEXT = {
        "idle": ("Press RUN", theme.MUTED),
        "at_scan_select": ("AT · Scan SELECT label", theme.TITLE_FG),
        "at_run_select": ("AT · SELECT testing", theme.UPDATE),
        "at_scan_shift": ("AT · Scan SHIFT label", theme.TITLE_FG),
        "at_run_shift": ("AT · SHIFT testing", theme.UPDATE),
        "mt_scan": ("MT · Scan label", theme.TITLE_FG),
        "mt_run": ("MT · Both testing", theme.UPDATE),
    }

    def _set_flow(self, state):
        self._flow = state
        text, color = self.FLOW_TEXT[state]
        self.flow_lbl.config(text=text, fg=color)
        # Outline the box the operator should scan into next
        expect = {"at_scan_select": "select", "at_scan_shift": "shift", "mt_scan": "select"}.get(state)
        for key, e in self.scan_entries.items():
            e.config(highlightbackground=theme.TITLE_FG if key == expect else "#232a36",
                     highlightthickness=2 if key == expect else 1)
        if expect:
            self.scan_entries[expect].focus_set()

    def _first_scan_state(self):
        return "mt_scan" if self.settings["test"].get("model_type") == "MT" else "at_scan_select"

    def set_target(self):
        """Enter in the Target box: store the typed target."""
        text = self.target.get().strip()
        value = int(text) if text.isdigit() else 0
        if value <= 0:
            self._set_status("Target must be a whole number above 0", theme.FAIL, hold=5)
            return "break"
        self.counters["target"] = value
        self._save_counters()
        self._set_status(f"Target set: {value}", theme.PASS, hold=4)
        # brief green outline to confirm, then hand the focus back to the page
        self.target.config(highlightbackground=theme.PASS, highlightcolor=theme.PASS)
        self.after(900, lambda: self.target.config(highlightbackground=self.TILE, highlightcolor=theme.UPDATE)
                   if self._alive else None)
        self.focus_set()
        return "break"

    def _discard_target_edit(self):
        """Leaving the box without Enter: show the stored target again."""
        stored = self.counters.get("target", 0)
        shown = self.target.get().strip()
        if shown != (str(stored) if stored else ""):
            self.target.delete(0, "end")
            if stored:
                self.target.insert(0, str(stored))
            self._set_status("Target not changed: type the number and press Enter to set it", theme.BUSY, hold=5)

    def _target_is_set(self):
        """RUN needs a production target. Warn with a pop-up when it is missing."""
        if self.counters.get("target", 0) > 0:
            return True
        messagebox.showwarning("Target not set",
                               "The production target is not set.\n\n"
                               "Please set the target before starting the test.", parent=self)
        self.target.focus_set()
        self._set_status("RUN: set the production target first", theme.FAIL, hold=6)
        return False

    def start_flow(self):
        """RUN: arm the flow for the loaded model's type."""
        if self._flow == "idle" and not self._target_is_set():
            return
        if self._flow == "idle" and not self.settings["test"].get("part_number"):
            messagebox.showwarning("Part not set", "The loaded model has no Part Number.\n\n"
                                   "Set it in Model Settings, then load the model again.", parent=self)
            return
        if self.shared:
            t = self.settings["test"]
            if self.flow.run(t["select"].get("model"), t.get("model_type", "")):
                for e in self.scan_entries.values():
                    e.delete(0, "end")
            return
        if self._flow != "idle":
            self._set_status("A test is already in progress — press STOP to cancel it", theme.BUSY)
            return
        t = self.settings["test"]
        model, mtype = t["select"].get("model"), t.get("model_type", "")
        if not model:
            self._set_status("RUN: load a model first (Model Change)", theme.FAIL)
            return
        if mtype not in ("MT", "AT"):
            self._set_status(f"RUN: model {model} has no Type — set MT or AT in Model Settings, "
                             "then Model Change again", theme.FAIL)
            return
        self._stop_event.clear()
        for e in self.scan_entries.values():
            e.delete(0, "end")
        self._set_flow(self._first_scan_state())
        self._set_status(f"{model} ({mtype}): scan the {'label' if mtype == 'MT' else 'SELECT label'}",
                         theme.PASS)

    def stop_flow(self):
        """STOP: cancel the flow and tell the machine to stop (if configured)."""
        if self.shared:
            self.flow.stop()             # app flow only - no stop command is sent to the PLC
            return
        if self._flow == "idle":
            self.plc_pulse("test_stop", "Test stop")
            return
        self._stop_event.set()
        self._pending.clear()
        for p in self.panels:
            if p.judge.cget("text") == "TEST":
                p.judge.config(text="– – –", fg="#4f8cff")
        self._set_flow("idle")
        self._set_status("Test stopped", theme.BUSY)
        if self.settings["test"]["commands"].get("test_stop", "").strip() and self.plc.is_open:
            self.plc_pulse("test_stop", "Test stop")

    def on_scan(self, key):
        """A label was scanned (or typed + Enter) into the SELECT or SHIFT box."""
        label = self.scan_entries[key].get().strip()
        if not label:
            return
        if self._flow in ("at_scan_select", "at_scan_shift", "mt_scan") and not self._label_matches_part(label):
            part = self.settings["test"].get("part_number", "") or "(none set)"
            messagebox.showerror("Wrong label",
                                 f"The scanned label does not match the loaded part.\n\n"
                                 f"Expected part: {part}\nScanned: {label}", parent=self)
            self.scan_entries[key].delete(0, "end")
            self._set_status(f"Label rejected: expected part {part}", theme.FAIL, hold=6)
            return
        if self.shared:
            if self._flow == "mt_scan":
                for e in self.scan_entries.values():
                    if e.get().strip() != label:
                        e.delete(0, "end")
                        e.insert(0, label)
            self.flow.scan(key, label)
            return
        state = self._flow
        if state == "at_scan_select" and key == "select":
            self._set_flow("at_run_select")
            self._start_cable_tests([0])
        elif state == "at_scan_shift" and key == "shift":
            self._set_flow("at_run_shift")
            self._start_cable_tests([1])
        elif state == "mt_scan":
            # MT: one label for the part; it is recorded against both cables.
            for e in self.scan_entries.values():
                if e.get().strip() != label:
                    e.delete(0, "end")
                    e.insert(0, label)
            self._set_flow("mt_run")
            self._start_cable_tests([0, 1])
        elif state == "idle":
            self._set_status("Press RUN before scanning", theme.BUSY)
        else:
            expected = {"at_scan_select": "SELECT", "at_scan_shift": "SHIFT"}.get(state)
            self._set_status(f"Scan the {expected} label" if expected else "Test running — wait for it to finish",
                             theme.BUSY)

    def _start_cable_tests(self, cables):
        self._pending = set(cables)
        names = " + ".join(self.SIDES[n][1] for n in cables)
        self._set_status(f"{names} test running…", theme.UPDATE)
        for n in cables:
            if self._flow == "idle":          # an earlier start failed and aborted the flow
                return
            panel = self.panels[n]
            panel.reset_results()
            panel.judge.config(text="TEST", fg=theme.BUSY)
            self._run_cable_test(n)

    def _run_cable_test(self, n):
        """Start one cable's test on the machine and wait for it to finish.

        PLC handshake (settings.json → test.commands): pulse start_<cable>,
        then wait for done_<cable> to turn ON.
        """
        key, name = self.SIDES[n][0], self.SIDES[n][1]
        cmds = self.settings["test"]["commands"]
        start, done = cmds.get(f"start_{key}", "").strip(), cmds.get(f"done_{key}", "").strip()
        if not start or not done:
            self._abort_flow(f"{name}: PLC start/done bits are not set "
                             f"(settings.json → test.commands.start_{key} / done_{key})")
            return
        if not self.plc.is_open:
            self._abort_flow(f"{name}: PLC not connected")
            return
        stop = self._stop_event

        def run():
            try:
                self.plc.write_bit(start, True)
                time.sleep(0.3)
                self.plc.write_bit(start, False)
                deadline = time.monotonic() + 300
                while not stop.is_set():
                    if self.plc.read_bits([done])[0][1] is True:
                        self.after(0, lambda: self._cable_test_finished(n))
                        return
                    if time.monotonic() > deadline:
                        self.after(0, lambda: self._abort_flow(f"{name}: no 'done' signal within 5 minutes"))
                        return
                    time.sleep(0.1)
            except Exception as exc:
                self.after(0, lambda e=exc: self._abort_flow(f"{name}: {e}"))

        threading.Thread(target=run, daemon=True).start()

    def _cable_test_finished(self, n):
        if not self._alive or n not in self._pending or self._stop_event.is_set():
            return
        self._pending.discard(n)
        panel = self.panels[n]
        if panel.last_ok is not None:          # a result was measured for this cable
            self.record_result(n, panel.last_ok)
        elif panel.judge.cget("text") == "TEST":
            panel.judge.config(text="DONE", fg=theme.MUTED)
        if self._flow == "at_run_select":
            self._set_flow("at_scan_shift")
            self._set_status("SELECT CABLE done — scan the SHIFT label", theme.PASS)
        elif self._pending:
            return                              # MT: waiting for the other cable
        else:
            for e in self.scan_entries.values():
                e.delete(0, "end")
            self._set_flow(self._first_scan_state())
            self._set_status("Part complete — scan the next label", theme.PASS)

    def _abort_flow(self, message):
        self._stop_event.set()
        self._pending.clear()
        for p in self.panels:
            if p.judge.cget("text") == "TEST":
                p.judge.config(text="– – –", fg="#4f8cff")
        self._set_flow("idle")
        self._set_status(message, theme.FAIL, hold=15)

    # -------------------------------------------------------- counters
    def _roll_day(self):
        """Start the day counts again when the date has changed."""
        today = datetime.date.today().isoformat()
        if self.counters.get("date") != today:
            self.counters.update(date=today, ok_day=0, ng_day=0)

    def _show_counters(self):
        c = self.counters
        for lbl, v in zip(self.lbl_ok + self.lbl_ng + self.lbl_saved + [self.lbl_complete],
                          (c["ok_day"], c["ok_total"], c["ng_day"], c["ng_total"],
                           c["saved_select"], c["saved_shift"], c["complete"])):
            lbl.config(text=str(v))
        self._draw_progress()

    def _save_counters(self):
        # The target is only changed by set_target() (Enter in the Target box).
        self._draw_progress()
        self.settings["test"]["save_dir"] = self.save_dir.get().strip() or "data"
        save_settings(self.settings)

    def reset_counts(self, which):
        label = "today's" if which == "day" else "cumulative"
        if not messagebox.askyesno("Del", f"Reset the {label} O.K/N.G counts to 0?", parent=self):
            return
        self.counters.update({f"ok_{which}": 0, f"ng_{which}": 0})
        self._show_counters()
        self._save_counters()

    def record_result(self, n, ok):
        """Count one finished test on cable n (0 = SELECT, 1 = SHIFT).

        The test sequence calls this after saving a result.
        """
        self._roll_day()
        c = self.counters
        key = "ok" if ok else "ng"
        c[f"{key}_day"] += 1
        c[f"{key}_total"] += 1
        c["saved_select" if n == 0 else "saved_shift"] += 1
        c["complete"] += 1
        self._show_counters()
        self._save_counters()

    def open_saved(self):
        """OPEN: show the 'Saved to' folder in Explorer."""
        path = self.save_dir.get().strip() or "data"
        if not os.path.isabs(path):
            path = os.path.join(os.path.dirname(SCREENSHOT_DIR), path)
        try:
            os.makedirs(path, exist_ok=True)
            os.startfile(path)
        except Exception as exc:
            self._set_status(f"OPEN: {exc}", theme.FAIL)
            return
        self._save_counters()

    # ----------------------------------------------------------- polling
    def _on_destroy(self, event):
        if event.widget is self:
            self._alive = False
            if self.monitor:
                self.monitor.stop()

    def _set_status(self, text, color="white", hold=3):
        """Show an action's message; polling won't overwrite it for `hold` seconds."""
        self.status.config(text=text, fg=color)
        self._hold_until = time.monotonic() + hold

    def _poll(self):
        """Every POLL_MS, read the connected devices on a worker thread."""
        if not self._alive:
            return
        if not self._polling:
            self._polling = True
            threading.Thread(target=self._poll_worker, daemon=True).start()
        self.after(POLL_MS, self._poll)

    def _poll_worker(self):
        snapshot = {"missing": [], "sides": []}
        try:
            if not self.plc.is_open and not self.shared:
                snapshot["missing"].append("PLC")      # shared mode: the PLC strip shows it
            for i, (key, _t, _s) in enumerate(self.SIDES):
                side = {"load": None, "lamps": {}}
                lc = self.loadcells[i]
                if lc.is_open:
                    try:
                        side["load"] = lc.read()[2]
                    except Exception:
                        pass
                else:
                    snapshot["missing"].append(lc.name.split(" (")[0])
                if self.plc.is_open:
                    cfg = self.settings["test"][key]
                    try:
                        bits = self.plc.read_bits([cfg["front_sensor"], cfg["rear_sensor"]])
                        side["lamps"] = {"Front Sensor": bits[0][1] is True, "Rear Sensor": bits[1][1] is True}
                    except Exception:
                        pass
                snapshot["sides"].append(side)
        finally:
            self._polling = False
        if self._alive:
            self.after(0, lambda: self._apply(snapshot))

    def _apply(self, snap):
        if not self._alive:
            return
        for i, side in enumerate(snap["sides"]):
            if not self.shared:                       # shared mode: the PLC monitor owns LOAD/STROKE
                self.panels[i].set_live(load=side["load"])
            for name, (cv, dot) in self.lamps[i].items():
                cv.itemconfig(dot, fill=LAMP_ON if side["lamps"].get(name) else LAMP_OFF)
        if time.monotonic() < self._hold_until:
            return   # keep the last action's message on screen for a moment
        if snap["missing"]:
            self.status.config(text="Not connected: " + ", ".join(snap["missing"]), fg=theme.FAIL)
        else:
            self.status.config(text="Ready", fg=theme.PASS)
