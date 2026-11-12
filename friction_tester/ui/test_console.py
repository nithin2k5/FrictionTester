"""Test Console — the measurement screen, laid out like the LabVIEW
"Friction Test Machine" it replaces.

Two cable panels side by side:
    SELECT CABLE -> Loadcell 01
    SHIFT CABLE  -> Loadcell 02
each with its test conditions, test results (PULL1-3 / PUSH1-3, means,
OK/NG), a Load-vs-Stroke graph and live LOAD / STROKE / SPEED readouts.
Underneath, per cable: the cylinder forward/backward and part-presence
lamps read from the PLC, and the clamp counter.

The page polls connected devices for the live values: LOAD comes from the
loadcell; STROKE and SPEED have no source yet and show "--". It does not
run the automatic test sequence; `CablePanel.show_results`, `set_live` and
`plot` are the hooks that sequence will feed.
"""

import datetime
import math
import os
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

import copy
import csv
import queue as _queue

from . import theme as T
from .theme import light as theme      # light industrial theme
from ..console_log import channel
from .plot import LoadStrokePlot
from ..config import save_settings
from ..plc.modbus_map import ModbusMapping, ModbusPlcBackend, make_mapping
from ..plc.part_flow import SharedCycleFlow, panel_assignments
from ..plc.shared_cycle import STEP_NAMES, CycleTracker, SharedCycleMonitor, read_snapshot
from ..plc import pc_profile as pp

NO_DATA = "—"      # shown for a value that was not measured (a measured zero shows 0)


VALUE_FG = "#0b3a75"     # measured values (dark blue, ISA-101: values stand out from labels)
FIELD_BG = T.L_SUNKEN
PULL_COLOR = "#1565c0"
PUSH_COLOR = "#d84315"
LAMP_OFF = "#c9d0d7"
LAMP_ON = "#1fa83a"
# Lamp name -> settings key (test.<cable>.<key>): cylinder position and part presence.
SENSOR_LAMPS = {"Forward": "forward_sensor", "Backward": "backward_sensor", "Part": "part_sensor",
                "Clamp ON": "clamp_on_sensor", "Clamp OFF": "clamp_off_sensor",
                "Drive OK": "drive_ok_input", "Servo": "servo_input", "Origin": "origin_input"}
LAMP_ROWS = 3
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


_ui = channel("UI")
_status = channel("STATUS")


class CablePanel(tk.Frame):
    """One cable: conditions, results, graph and live readouts."""

    def __init__(self, master, title, cfg):
        super().__init__(master, bg=theme.PANEL, highlightbackground=theme.BORDER, highlightthickness=1)
        self.cfg = cfg
        self.title = title
        self.last_ok = None              # OK/NG of the last measured test, None if not measured

        # Title row with Screen Save
        top = tk.Frame(self, bg=theme.PANEL)
        top.pack(fill="x", padx=10, pady=(6, 2))
        # OK / NG judgement, left of the title
        self.judge = tk.Label(top, text="– – –", bg=FIELD_BG, fg=T.L_TEXT_3, font=_font(13, True),
                              relief="flat", bd=0, highlightthickness=1, highlightbackground=T.L_BORDER, width=5)
        self.judge.pack(side="left")
        self.max_lbl = tk.Label(top, text="Max", bg=theme.PANEL, fg=T.L_TEXT, font=_font(9, True))
        self.max_lbl.pack(side="left", padx=(8, 3))
        self.res = {"max": self._value(top, NO_DATA)}
        self.res["max"].pack(side="left")
        self.max_unit = tk.Label(top, text="kgf", bg=theme.PANEL, fg=T.L_TEXT, font=_font(8))
        self.max_unit.pack(side="left", padx=(2, 0))
        tk.Button(top, text="Screen Save", command=self.save_screen, bg=T.L_NEUTRAL_BTN, fg="white",
                  activebackground="#4b5560", activeforeground="white", bd=0, padx=8, pady=2,
                  font=_font(8, True), cursor="hand2").pack(side="right")
        tk.Label(top, text=title, bg=theme.PANEL, fg=T.L_TEXT, font=_font(14)).pack(side="left", expand=True)
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
        self._gcap = None          # capture being drawn, how many of its samples are drawn,
        self._gn = 0               # its first sample time, and the stroke number on the graph
        self._gt0 = None
        self._gstroke = None
        self._gmiss = 0            # samples of the capture being drawn that had no LVDT stroke
        self._gerr = None          # test id the LVDT error popup was last shown for (once a test)
        self._gspeed = self._glen = None   # Speed / Stroke conditions of the stroke on the graph
        self._graw = {"PULL": [], "PUSH": []}   # LVDT readings of the traces on the graph
        self._gtimes = {"PULL": [], "PUSH": []}  # and their times since the capture began
        self._gbins = {"PULL": {}, "PUSH": {}}   # stroke bin -> [point index, samples] (GRAPH_PER_MM)
        self._scale_graph()

        live = tk.Frame(self, bg=theme.PANEL)
        live.pack(fill="x", padx=8, pady=(2, 6))
        self.live, self.live_units = {}, {}
        for key, label, unit in (("load", "LOAD", "kgf"), ("stroke", "STROKE", "mm"), ("speed", "SPEED", "mm/s")):
            tk.Label(live, text=label, bg=theme.PANEL, fg=theme.HEADER_FG, font=_font(9, True)).pack(side="left")
            self.live[key] = self._value(live, NO_DATA, width=6, size=11)
            self.live[key].pack(side="left", padx=(4, 2))
            self.live_units[key] = tk.Label(live, text=unit, bg=theme.PANEL, fg=T.L_TEXT, font=_font(8))
            self.live_units[key].pack(side="left", padx=(0, 8))

    # ----------------------------------------------------------- widgets
    def _value(self, parent, text, width=5, size=10):
        # A 1px highlight ring like the entries', so result boxes and
        # condition boxes are the same height and line up row for row.
        return tk.Label(parent, text=text, bg=FIELD_BG, fg=VALUE_FG, font=_font(size), width=width,
                        relief="flat", bd=0, highlightthickness=1, highlightbackground=T.L_BORDER)

    def _entry(self, parent, value, width=5, fmt="{:g}"):
        e = tk.Entry(parent, bg=theme.ENTRY_BG, fg=T.L_TEXT, insertbackground=T.L_TEXT, justify="center",
                     font=_font(10), width=width, bd=0, relief="flat",
                     highlightbackground=T.L_BORDER, highlightcolor=T.L_PRIMARY, highlightthickness=1)
        if value is not None:                # empty (null) conditions stay blank
            e.insert(0, fmt.format(value))
        return e

    def _caption(self, parent, text):
        return tk.Label(parent, text=text, bg=theme.PANEL, fg=theme.TITLE_FG, font=_font(9, True))

    def _small(self, parent, text, fg=T.L_TEXT):
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
        if not any(s["x"] for s in self.plot.series.values()):
            self._scale_graph()              # empty graph: rescale to the new stroke

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

        def text(col, row, t, bold=False, fg=T.L_TEXT, sticky="w", **kw):
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
        from .. import db
        db.check_positions(self.title, vals["pos1"], vals["pos3"])
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
        self.judge.config(text="– – –", fg=T.L_TEXT_3)
        self.plot.clear()
        self.plot.set_note(None)
        self._scale_graph()
        self._gcap, self._gstroke = None, None

    def _condition(self, key):
        """A test condition as typed in its box (None when empty or not a number)."""
        try:
            return float(self.ent[key].get().strip())
        except ValueError:
            return None

    def _scale_graph(self):
        """Stroke axis of the graph from the Stroke condition (+/-0.6 x stroke, start line)."""
        self.plot.set_stroke(self._condition("stroke"))

    LVDT_MISSING = 10      # samples without an LVDT stroke before the graph reports it
    from ..plc.pc_flow import GRAPH_PER_MM   # graph points per mm of stroke: each the mean of its samples

    def draw_capture(self, cap):
        """Add a measuring stroke's new graph points (Capture.graph: the NI card load when
        graph_force = "daq") to the graph, Load against the LVDT stroke (NI card) like the
        LabVIEW screen. The LVDT reads an absolute position, so with a Stroke condition it
        is centred like the LabVIEW graph: the PULL start becomes -stroke/2 and the pull
        direction is positive.
        The trace has GRAPH_PER_MM points per mm of stroke: every sample (the NI card
        delivers each analog sample) falls into a 1/GRAPH_PER_MM mm bin of the LVDT
        reading, and a bin is drawn as one point, the mean load at the mean stroke of its
        samples - a smooth line like the LabVIEW graph instead of the sensor noise.
        The LVDT is the only stroke axis. When it gives no reading nothing is drawn, and
        when it does not follow the axis the trace is drawn as read; either way a red note
        on the graph and an error popup (once a test) say why.
        Each stroke number starts a fresh graph; its PULL and PUSH share it."""
        name = "PULL" if cap.kind == "pull" else "PUSH"
        if cap is not self._gcap:
            if cap.stroke_no != self._gstroke:
                self.plot.clear()
                self._gstroke = cap.stroke_no
                self._graw = {"PULL": [], "PUSH": []}
                self._gtimes = {"PULL": [], "PUSH": []}
                self._gbins = {"PULL": {}, "PUSH": {}}
                self.plot.set_xlabel("Stroke [mm]")
            else:
                self.plot.series[name]["x"], self.plot.series[name]["y"] = [], []
                self._graw[name], self._gtimes[name], self._gbins[name] = [], [], {}
            self._gcap, self._gn, self._gt0, self._gmiss = cap, 0, None, 0
            self._gspeed, self._glen = self._condition("speed"), self._condition("stroke")
        new = cap.graph[self._gn:]
        self._gn += len(new)
        series = self.plot.series[name]
        drawn = False
        for t, load, stroke in new:
            if load is None:
                continue
            if self._gt0 is None:
                self._gt0 = t
            if stroke is None:
                self._gmiss += 1
                continue
            raw, times, drawn = self._graw[name], self._gtimes[name], True
            entry = self._gbins[name].setdefault(math.floor(stroke * self.GRAPH_PER_MM), [len(raw), 0])
            i, n = entry
            entry[1] = n + 1
            if n == 0:                       # a new bin: the next point of the trace
                raw.append(stroke)
                times.append(t - self._gt0)
                series["x"].append(stroke)
                series["y"].append(load)
            else:                            # running mean of the bin's samples
                raw[i] += (stroke - raw[i]) / (n + 1)
                series["y"][i] += (load - series["y"][i]) / (n + 1)
                series["x"][i] = raw[i]
                times[i] = t - self._gt0
        if drawn and self._glen:
            self._centre_stroke()
        ended = (cap.stop_event.is_set() and not (cap.thread and cap.thread.is_alive())
                 and self._gn == len(cap.graph))
        problem = None
        if not self._graw[name]:
            if self._gmiss >= self.LVDT_MISSING or (ended and self._gmiss):
                problem = ("No LVDT reading - the graph is not drawn ("
                           + (getattr(cap, "lvdt_note", None) or "no stroke value from the NI card") + ")")
        elif ended:
            problem = self._lvdt_stuck(name)
        self.plot.set_note(problem or getattr(cap, "graph_note", None))
        if problem:
            self._lvdt_error(cap, problem)
        if drawn:
            self.plot.redraw()
            self.plot.extend_to_data()

    def _centre_stroke(self):
        """LVDT readings -> the graph's stroke axis: the PULL start is -stroke/2 and the
        pull direction is positive, whatever the LVDT's zero and polarity. A PUSH drawn
        without its PULL starts at +stroke/2 and runs negative."""
        from ..plc.pc_flow import centre_lvdt
        xs = centre_lvdt(self._graw["PULL"], self._graw["PUSH"], self._glen / 2)
        self.plot.series["PULL"]["x"], self.plot.series["PUSH"]["x"] = xs

    def _lvdt_stuck(self, name):
        """Why a finished trace's LVDT readings cannot be the stroke (the LVDT did not
        follow the axis: not wired, wrong NI channel, or not scaled to mm), else None."""
        from ..plc.pc_flow import lvdt_follows
        if not (self._gspeed and self._glen):
            return None
        raw = self._graw[name]
        expected = min(max(self._gtimes[name]) * self._gspeed, self._glen)
        if expected < 1.0 or lvdt_follows(raw, expected):
            return None
        return (f"The LVDT moved only {max(raw) - min(raw):.2f} mm while the axis travelled about "
                f"{expected:.1f} mm - check its wiring and the Scale / Offset of its NI card row")

    def _lvdt_error(self, cap, text):
        """Error popup for a graph without a usable LVDT stroke, once a test."""
        if self._gerr == cap.test_id:
            return
        self._gerr = cap.test_id
        self.after_idle(lambda: messagebox.showerror("LVDT", f"{self.title}: {text}", parent=self))

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

    def show_pc_result(self, res, source_text):
        """PULL1-3 / PUSH1-3, means, max and OK/NG of a plc.pdf-profile cable test."""
        def fmt(v):
            return NO_DATA if v is None else f"{v:.2f}"

        for d in ("pull", "push"):
            vals = list(res.get(d) or [])
            for i in range(3):
                self.res[f"{d}{i + 1}"].config(text=fmt(vals[i]) if i < len(vals) else NO_DATA)
            present = [v for v in vals if v is not None]
            self.res[f"{d}_mean"].config(text=fmt(sum(present) / len(present)) if present else NO_DATA)
        self.res["max"].config(text=fmt(res.get("max")))
        verdict = res.get("verdict") or ("OK" if res.get("ok") else "NG")
        self.judge.config(text=verdict if verdict != "INCOMPLETE" else "INCMP",
                          fg={"OK": theme.PASS, "NG": theme.FAIL}.get(verdict, theme.BUSY))
        self.last_ok = None              # counted per part by the controller
        problems = res.get("problems") or []
        counts = res.get("sample_counts") or {}
        at = ""
        if res.get("judge") == "positions" and res.get("positions"):
            at = (" at " + " / ".join(f"{p:g}" for p in res["positions"]) + " mm"
                  + (f" ({res['stroke_source']})" if res.get("stroke_source") else ""))
        self.set_source(f"{source_text} · judge: {res.get('judge')}{at} · samples PULL {counts.get('pull', 0)} / "
                        f"PUSH {counts.get('push', 0)}" + (f"\n{'; '.join(problems)}" if problems else ""))

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

    def __init__(self, master, settings, plc, loadcells, daq=None):
        super().__init__(master, bg=theme.BG)
        self.settings = settings
        self.plc = plc
        self.loadcells = loadcells
        self.daq = daq
        self.integration = copy.deepcopy(settings["test"]["integration"])
        # "kd" = client reference (plc kd.pdf + plc Auto.pdf): the PLC runs the test cycle
        # (M00029 / D05010, followed like shared_cycle) and ORIGIN is M00050 (kd controller).
        self.shared = self.integration["mode"] in ("shared_cycle", "kd")
        self.pc = self.integration["mode"] == "pc_interface"     # plc.pdf profile
        self.kd = self.integration["mode"] == "kd"               # plc kd.pdf profile (client reference)
        self.ctl = None
        self.sampler = None
        self.monitor = None
        self.flow = None
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
        if self.shared or self.pc or self.kd:
            self._build_plc_strip(left)      # left column only, so the side panel keeps its height
        # Measurement view (cable panels + bottom bar) and the Manual Operation view share
        # the left column; the Manual button in the status bar switches between them.
        self.measure_view = tk.Frame(left, bg=theme.BG)
        self.measure_view.pack(fill="both", expand=True)
        panels = tk.Frame(self.measure_view, bg=theme.BG)
        panels.pack(fill="both", expand=True, pady=(0, 6))
        self.panels = []
        for i, (key, title, _) in enumerate(self.SIDES):
            p = CablePanel(panels, title, settings["test"][key])
            p.grid(row=0, column=i, sticky="nsew", padx=(0, 4) if i == 0 else (4, 0))
            panels.columnconfigure(i, weight=1, uniform="panel")
            self.panels.append(p)
        panels.rowconfigure(0, weight=1)
        self._build_bottom(self.measure_view)
        from .manual_view import ManualView
        self.manual = ManualView(left, self, self.SIDES)       # packed by show_manual()
        self._pc_snap = None

        self._build_side_panel(body).grid(row=0, column=1, sticky="ns", padx=(8, 0))
        self._show_current_model()

        self.bind("<Destroy>", self._on_destroy)
        self._log_buttons(self)
        self.after(200, self._poll)
        self._load_model_list()
        if self.kd:
            self._start_kd()
        elif self.shared:
            self._start_shared_cycle()
        elif self.pc:
            self._start_pc_interface()
        else:
            for n, p in enumerate(self.panels):
                p.set_source(f"Source: serial loadcell L{n + 1} (live LOAD) · results: start/done handshake")

    # ------------------------------------------------------------ layout
    def _build_status_bar(self):
        bar = tk.Frame(self, bg=theme.BG)
        bar.pack(fill="x")
        tag = tk.Frame(bar, bg=theme.PANEL, highlightbackground=theme.TITLE_FG, highlightthickness=0)
        tag.pack(side="left")
        self.screen_tag = tk.Label(tag, text="📈  Measurement Screen", bg=theme.PANEL, fg=T.L_TEXT, font=_font(14),
                                   padx=16, pady=6)
        self.screen_tag.pack()
        tk.Frame(tag, bg=theme.TITLE_FG, height=3).pack(fill="x")

        tk.Button(bar, text="⏻  EXIT", command=self.exit_app, bg=T.L_NG, fg="white",
                  activebackground="#a61f1f", activeforeground="white", bd=0, padx=16, pady=5,
                  font=_font(10, True), cursor="hand2").pack(side="right")
        tk.Button(bar, text="Save Conditions", command=self.save_conditions, bg=theme.SAVE, fg="white",
                  activebackground=theme.SAVE, activeforeground="white", bd=0, padx=14, pady=5,
                  font=_font(10, True), cursor="hand2").pack(side="right", padx=(0, 8))
        tk.Button(bar, text="Clear Results", command=self.clear_results, bg=T.L_NEUTRAL_BTN, fg="white",
                  activebackground="#4b5560", activeforeground="white", bd=0, padx=14, pady=5,
                  font=_font(10, True), cursor="hand2").pack(side="right", padx=8)
        self.manual_btn = tk.Button(bar, text="🕹  Manual", command=self.toggle_manual, bg=T.L_NEUTRAL_BTN, fg="white",
                                    activebackground="#4b5560", activeforeground="white", bd=0, padx=14, pady=5,
                                    font=_font(10, True), cursor="hand2")
        self.manual_btn.pack(side="right", padx=(8, 0))

        self.status = tk.Label(bar, text="System initialization", bg=FIELD_BG, fg=T.L_TEXT, font=_font(11),
                               relief="flat", bd=0, highlightthickness=1, highlightbackground=T.L_BORDER, padx=12, pady=5, anchor="w")
        self.status.pack(side="left", fill="x", expand=True, padx=16)

    # ---------------------------------------------------- shared PLC cycle
    def _build_plc_strip(self, parent):
        """One line of live PLC cycle state above the cable panels."""
        strip = tk.Frame(parent, bg=T.L_SURFACE, highlightbackground=theme.BORDER, highlightthickness=1)
        strip.pack(fill="x", pady=(0, 4))
        self.plc_badge = tk.Label(strip, text="PLC", bg=T.L_SURFACE, fg=theme.MUTED, font=_font(9, True), padx=8)
        self.plc_badge.pack(side="left")
        self.plc_strip = tk.Label(strip, text="Connecting…", bg=T.L_SURFACE, fg=T.L_TEXT_2, font=_font(9),
                                  anchor="w")
        self.plc_strip.pack(side="left", fill="x", expand=True)
        self.plc_mode = tk.Label(strip, text="", bg=T.L_SURFACE, fg=theme.MUTED, font=_font(8, True), padx=8)
        self.plc_mode.pack(side="right")
        if self.kd:                              # second line: X axis / homing (plc kd.pdf)
            axis = tk.Frame(parent, bg=T.L_SURFACE, highlightbackground=theme.BORDER, highlightthickness=1)
            axis.pack(fill="x", pady=(0, 4))
            tk.Label(axis, text="AXIS", bg=T.L_SURFACE, fg=theme.MUTED, font=_font(9, True), padx=8).pack(side="left")
            self.plc_axis = tk.Label(axis, text="Connecting…", bg=T.L_SURFACE, fg=T.L_TEXT_2, font=_font(9),
                                     anchor="w")
            self.plc_axis.pack(side="left", fill="x", expand=True)

    def _start_shared_cycle(self, backend=None, write_blocker=None):
        shared = self.integration["shared"]
        if backend is None:
            mapping = ModbusMapping(shared.get("mapping"))
            backend = ModbusPlcBackend(self.plc, mapping, allow_writes=bool(shared.get("live_start_enabled")))
        mapping = backend.mapping
        mapping_text = "map " + ("verified" if mapping.verified else "UNVERIFIED")
        self.backend = backend
        writes = lambda: bool(getattr(backend, "can_write", False))
        tracker = CycleTracker(start_timeout=float(shared.get("start_timeout", 10.0)))
        self.monitor = SharedCycleMonitor(backend, tracker, can_write=writes,
                                          interval=float(shared.get("poll_interval", 0.25)))
        self.flow = SharedCycleFlow(shared, self, self.monitor, writes, skip_scan=self._skip_scan(),
                                    write_blocker=write_blocker)
        what = "writes" if self.kd else "live start"
        self.plc_mode.config(text=f"{mapping_text} · {what} {'ON' if writes() else 'OFF'}",
                             fg=theme.MUTED if writes() else theme.FAIL)
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
                f"Source: PLC Channel {ch}" + ("" if mapped else " — cable not mapped") +
                (" · PLC auto cycle (plc Auto.pdf)" if self.kd else " · shared cycle"))
        self._live_assign = assign
        self.monitor.start()
        self.after(100, self._drain_monitor)

    # ============================================ plc.pdf PC-interface profile
    def _start_pc_interface(self):
        from ..plc.event_log import EventLog
        from ..plc.pc_flow import PcController
        from ..plc.pc_link import PcLink
        from ..plc.pc_measure import Sampler
        mp = self.settings["test"]["machine_profile"]
        mapping = make_mapping(mp.get("mapping"))
        backend = ModbusPlcBackend(self.plc, mapping, allow_writes=bool(mp.get("live_writes_enabled")))
        self.pc_log = EventLog(self._result_dir())
        self.pc_log.log("MAPPING", mapping.describe())
        link = PcLink(backend, self.pc_log, owner_confirmed=bool(mp.get("command_owner_confirmed")),
                      internal_enabled=(("auto" if mp.get("internal_bits_enabled") == "auto"
                                         else bool(mp.get("internal_bits_enabled"))) if mapping.verified
                                        else False))
        self.sampler = Sampler(self.loadcells, self.daq, self.settings)
        cfg = copy.deepcopy(mp)
        from .. import db
        from ..result_store import ResultStore
        self.store = ResultStore(
            os.path.join(self._result_dir(), "unsaved_results.json"),
            insert_rows=lambda rows: (db.init_database(), db.insert_test_results(rows))[1],
            make_rows=lambda rec: db.result_rows(rec, rec["part_id"], "PLC pc interface"))
        if self.store.pending():
            self.store.retry()
        self.after(15000, self._retry_saves)
        unverified = [k for k, v in (mp.get("commissioning") or {}).items() if v != "verified"]
        self.ctl = PcController(link, cfg, self.pc_log, self.sampler,
                                interval=float(mp.get("poll_interval", 0.1)), skip_scan=self._skip_scan())
        self.pc_log.log("START", pp.PROFILE_TITLE, version=mp.get("version"),
                        mapping=mapping.describe(), writes=link.write_blocker() or "enabled",
                        unverified=",".join(unverified) or "-")
        m = mp.get("measurement") or {}
        for (key, _t, _s), panel in zip(self.SIDES, self.panels):
            if m.get("force_source", "serial") == "serial":
                src = f"serial loadcell L{(m.get('loadcell') or {}).get(key, '?')}"
            else:
                src = f"NI DAQ {(m.get('daq_force') or {}).get(key, '?')}"
            stroke = (m.get("stroke") or {}).get(key) or "not configured"
            graph = (f" · graph NI DAQ {(m.get('daq_force') or {}).get(key, '?')}"
                     if m.get("graph_force") == "daq" else "")
            panel.set_source(f"Source: {src} · stroke {stroke}{graph} · {pp.PROFILE_TITLE}")
            panel.set_result_labels("PULL", "PUSH", "", "Max")
        values = self.settings["test"].get("plc_params")
        if values and self.settings["test"]["select"].get("model"):
            self.ctl.submit("params", self.settings["test"]["select"]["model"], list(values))
        self.ctl.start()
        self.after(100, self._drain_pc)

    def _pc_info(self):
        t = self.settings["test"]
        conds = {key: panel.read_conditions() for (key, _t, _s), panel in zip(self.SIDES, self.panels)}
        return {"model": t["select"].get("model"), "type": t.get("model_type", ""),
                "part_number": t.get("part_number", ""), "conditions": conds}

    def _drain_pc(self):
        """UI thread: apply what the controller thread produced (it never touches Tk)."""
        if not self._alive or self.ctl is None:
            return
        last = None
        while True:
            try:
                ev = self.ctl.events.get_nowait()
            except _queue.Empty:
                break
            kind = ev[0]
            if kind == "snapshot":
                last = ev
            elif kind == "message":
                self.message(ev[1], ev[2])
            elif kind == "flow":
                self._set_flow(ev[1])
            elif kind == "axis_started":
                n = [k for k, _t, _s in self.SIDES].index(ev[1])
                if self.sampler is not None:
                    self.sampler.captures.pop(ev[1], None)     # the previous part's strokes
                self.panels[n].reset_results()
                self.panels[n].judge.config(text="TEST", fg=theme.BUSY)
            elif kind == "axis_result":
                n = [k for k, _t, _s in self.SIDES].index(ev[1])
                self.panels[n].show_pc_result(ev[2], f"Source: {pp.PROFILE_TITLE}")
            elif kind == "part_done":
                self._pc_part_done(ev[1])
            elif kind == "fault" and ev[1]:
                self._set_status(f"FAULT latched: {ev[1]} - inspect the machine, then STOP to acknowledge",
                                 theme.FAIL, 15)
        while True:
            try:
                sev = self.store.events.get_nowait()
            except _queue.Empty:
                break
            if sev[0] == "saved":
                rec = sev[1]
                if rec.get("counted"):
                    self._count_part(rec)
                self.pc_log.log("SAVED", rec["part_id"], verdict=rec["verdict"], counted=rec.get("counted"))
                if rec["verdict"] in ("OK", "NG"):
                    self._set_status(f"Part {rec['verdict']} saved", theme.PASS if rec["verdict"] == "OK"
                                     else theme.FAIL, 4)
            else:
                self.pc_log.log("SAVE-FAIL", sev[1]["part_id"], error=sev[2])
                self._set_status(f"Part {sev[1]['part_id']} NOT SAVED ({sev[2]}) - retrying; RUN is blocked "
                                 "until it is saved", theme.FAIL, 15)
        if last is not None:
            self._show_pc_status(last[1], last[2])
        self.after(100, self._drain_pc)

    # ============================================ plc kd.pdf profile (client reference)
    def _start_kd(self):
        from ..plc.event_log import EventLog
        from ..plc.kd_flow import KdController
        from ..plc.kd_link import KdLink
        from ..plc import kd_profile as kp
        kd_cfg = self.settings["test"].get("kd_profile") or {}
        mapping = make_mapping(self.settings["test"]["machine_profile"].get("mapping"))
        owner = bool(kd_cfg.get("command_owner_confirmed"))
        # One backend for homing (M00050) and the cycle start (M00029); its writes also need ownership.
        backend = ModbusPlcBackend(self.plc, mapping,
                                   allow_writes=bool(kd_cfg.get("live_writes_enabled")) and owner)
        self.pc_log = EventLog(self._result_dir())
        self.pc_log.log("MAPPING", mapping.describe())
        link = KdLink(backend, self.pc_log, owner_confirmed=owner, read_loads=False)
        self.pc_log.log("START", kp.PROFILE_TITLE, writes=link.write_blocker() or "enabled",
                        unresolved=len(kp.UNRESOLVED))
        cfg = copy.deepcopy(kd_cfg)
        cfg["poll_interval"] = max(float(cfg.get("poll_interval") or 0.5), 0.5)   # the cycle monitor polls too
        self.ctl = KdController(link, cfg, self.pc_log)
        self.ctl.start()
        self.after(100, self._drain_kd)
        self._start_shared_cycle(backend=backend, write_blocker=link.write_blocker)

    def _drain_kd(self):
        """UI thread: apply what the kd controller thread produced."""
        if not self._alive or self.ctl is None:
            return
        last = None
        while True:
            try:
                ev = self.ctl.events.get_nowait()
            except _queue.Empty:
                break
            if ev[0] == "snapshot":
                last = ev
            elif ev[0] == "message":
                self.message(ev[1], ev[2])
            elif ev[0] == "fault" and ev[1]:
                self._set_status(f"FAULT latched: {ev[1]} - inspect the machine, then STOP to acknowledge",
                                 theme.FAIL, 15)
        if last is not None:
            self._show_kd_status(last[1], last[2])
        self.after(100, self._drain_kd)

    def _show_kd_status(self, snap, summary):
        if snap is None:
            label = summary.get("link_label") or "PLC status not available"
            why = summary.get("link_error")
            self.plc_axis.config(text=f"{label} - no motion" + (f"   |   {why}" if why else ""))
        else:
            flags = [("BUSY", snap.st("busy")), ("ERR", snap.st("error")), ("HOMED", snap.st("homed")),
                     ("POS-OK", snap.st("pos_complete")), ("LIM+", snap.st("upper_limit")),
                     ("LIM-", snap.st("lower_limit")), ("EMG", snap.st("emergency_stop"))]
            x = "X: " + (" ".join(n for n, v in flags if v) or "-") + f"  pos {snap.position}"
            if snap.st("error"):
                x += f"  err {snap.error_code}"
            hs = snap.home_state if snap.home_state is not None else "n/a"
            home = f"home step D05000={hs}" + (
                f" M00050={int(bool(snap.cmd('M00050')))}" if snap.bits else " (M bits not readable)")
            raw = " ".join(f"{a} {int(bool(v))}" for a, v in snap.inputs.items()) or "P inputs n/a"
            fault = f"   |   FAULT: {summary['fault']}" if summary.get("fault") else ""
            op = f"   |   {summary['op'][0]}: {summary['op'][1]}" if summary.get("op") else ""
            self.plc_axis.config(text=f"{x}   |   {home}   |   raw {raw}{op}{fault}")

    def _retry_saves(self):
        if not self._alive:
            return
        if self.store.pending():
            self.store.retry()
        self.after(15000, self._retry_saves)

    def _pc_part_done(self, record):
        """A finalized part: one CSV line, then the database (counters follow the confirmed save)."""
        try:
            self._append_result_row(record)
        except Exception as exc:
            self._set_status(f"Result file could not be written: {exc}", theme.FAIL, 10)
        self.store.submit(record)

    def _show_pc_status(self, snap, summary):
        self._pc_snap = snap
        p = summary["params"]
        badge = {"confirmed": theme.PASS, "writing": theme.UPDATE, "pending": theme.BUSY}.get(p["state"], theme.FAIL)
        if snap is None:
            self.plc_badge.config(fg=theme.FAIL)
            # Port state, PLC answer and register-map validity are different
            # things: say which one failed, with the exact request.
            label = summary.get("link_label") or "PLC status not available"
            why = summary.get("link_error")
            self.plc_strip.config(text=f"{label} - no motion, no results confirmed"
                                       + (f"   |   {why}" if why else ""))
        else:
            parts = []
            for axis in pp.AXES:
                st = lambda n: snap.st(axis, n)
                flags = [("SV", st("servo_on")), ("ALM-OK", st("alarm_normal")), ("PERMIT", st("permitted")),
                         ("PULL", st("pull_active")), ("PUSH", st("push_active")), ("@START", st("at_meas_start")),
                         ("@PULL", st("at_pull")), ("CLAMP-ON", st("clamp_on")), ("ERR", st("axis_error"))]
                parts.append(f"{pp.AXIS_NAME[axis]}: " + (" ".join(n for n, v in flags if v) or "-"))
            raw = (f"raw P0000F {int(bool(snap.common('p0000f_input')))} P0000D "
                   f"{int(bool(snap.common('p0000d_input')))} · M0800A {int(bool(snap.common('m0800a_cond')))} "
                   f"M0800B {int(bool(snap.common('m0800b_cond')))}")
            fault = f"   |   FAULT: {summary['fault']}" if summary.get("fault") else ""
            stop = "   |   app cancelled - machine stop NOT confirmed" if summary.get("stop_state") else ""
            self.plc_badge.config(fg=theme.FAIL if summary.get("fault") else
                                  (theme.UPDATE if self.ctl.busy() else theme.PASS))
            self.plc_strip.config(text="   |   ".join(parts) + f"   |   {raw}{fault}{stop}")
        blocker = summary.get("write_blocker")
        self.plc_mode.config(text=(f"params: {p['state'].replace('_', ' ')}"
                                   + (" · writes OFF" if blocker else "")), fg=badge)
        if self.sampler is not None:
            active = self.sampler.active_axes()
            for (key, _t, _s), panel in zip(self.SIDES, self.panels):
                if key in active and key in self.sampler.latest:
                    load, stroke = self.sampler.latest[key]
                    panel.set_live_values(load, stroke, "", "")
                cap = self.sampler.captures.get(key)
                if cap is not None:
                    panel.draw_capture(cap)

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
        color = {"pass": theme.PASS, "fail": theme.FAIL, "busy": theme.BUSY, "info": theme.UPDATE}.get(level, T.L_TEXT)
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
        self._count_part(record)
        try:
            self._append_result_row(record)
        except Exception as exc:
            self._set_status(f"Part counted, but the result file could not be written: {exc}", theme.FAIL, 10)
        self._save_result_rows(record)

    def _count_part(self, record):
        """Production and OK/NG counters for one finished part."""
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

    def _save_result_rows(self, record):
        """Write the part's rows to TBL_TEST_RESULTS on a worker thread."""
        import uuid
        from .. import db
        source = "PLC kd auto cycle" if self.kd else ("PLC shared cycle" if self.shared else
                                                       ("PLC pc interface" if self.pc else "PLC"))
        rows = db.result_rows(record, uuid.uuid4().hex, source)
        result = {}

        def worker():
            try:
                db.init_database()
                result["n"], result["error"] = db.insert_test_results(rows), None
            except Exception as exc:
                result["n"], result["error"] = 0, exc

        def collect():
            if not self._alive:
                return
            if "n" not in result:
                self.after(200, collect)
            elif result["error"] is not None:
                self._set_status(f"Part counted, but saving to {db.RESULTS_TABLE} failed: {result['error']}",
                                 theme.FAIL, 10)

        t = threading.Thread(target=worker, daemon=True)
        self._save_threads = [x for x in getattr(self, "_save_threads", []) if x.is_alive()] + [t]
        t.start()
        self.after(200, collect)

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

    def _small_button(self, parent, text, command, bg=T.L_SURFACE_2, fg=T.L_TEXT, width=12):
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
            act = tk.LabelFrame(box, text=" Actuator Control ", bg=theme.PANEL, fg=T.L_TEXT, font=_font(9),
                                bd=1, relief="solid", labelanchor="n", padx=6, pady=3)
            act.pack(side="left", padx=(8, 6), pady=4)
            sbtn = self._small_button(act, "⚙ SERVO ON", lambda k=key, n=i: self.toggle_servo(k, n), width=11)
            sbtn.pack(side="left", padx=(0, 4))
            self.servo_btns.append(sbtn)
            self._small_button(act, "ALARM RESET", lambda k=key: self.plc_pulse(f"alarm_reset_{k}", "Alarm reset"),
                               bg=T.L_NEUTRAL_BTN, fg="white", width=11).pack(side="left", padx=(0, 4))
            self._small_button(act, "↺ Move Origin", lambda k=key: self.plc_pulse(f"origin_{k}", "Move origin"),
                               width=11).pack(side="left")

            sens = tk.Frame(box, bg=theme.PANEL)
            sens.pack(side="left", padx=6, pady=4)
            lamps = {}
            for j, name in enumerate(SENSOR_LAMPS):
                row = tk.Frame(sens, bg=theme.PANEL)
                row.grid(row=j % LAMP_ROWS, column=j // LAMP_ROWS, sticky="w", pady=1, padx=(0, 8))
                cv = tk.Canvas(row, width=16, height=16, bg=theme.PANEL, highlightthickness=0)
                dot = cv.create_oval(2, 2, 14, 14, fill=LAMP_OFF, outline=T.L_BORDER)
                cv.pack(side="left")
                tk.Label(row, text=name, bg=theme.PANEL, fg=T.L_TEXT, font=_font(9)).pack(side="left", padx=3)
                lamps[name] = (cv, dot)
            self.lamps.append(lamps)

            clamp = tk.Frame(box, bg=theme.PANEL)
            clamp.pack(side="left", padx=4)
            tk.Label(clamp, text=f"{short}-Clamp", bg=theme.PANEL, fg=T.L_TEXT, font=_font(9)).pack(anchor="w")
            row = tk.Frame(clamp, bg=theme.PANEL)
            row.pack(anchor="w")
            sp = tk.Spinbox(row, from_=0, to=999999, width=6, justify="center", font=_font(10),
                            bg=FIELD_BG, fg=VALUE_FG, buttonbackground=T.L_SURFACE_2, relief="flat", bd=1, highlightthickness=1, highlightbackground=T.L_BORDER,
                            insertbackground=T.L_TEXT)
            sp.delete(0, "end")
            sp.insert(0, str(cfg.get("clamp_count", 0)))
            sp.pack(side="left")
            tk.Label(row, text="times", bg=theme.PANEL, fg=T.L_TEXT, font=_font(8)).pack(side="left", padx=3)
            self.clamps.append(sp)

        # Machine-wide inputs/outputs (test.machine_lamps), read with the cable lamps;
        # a narrow third column so the window does not get taller.
        self.machine_lamps = {}
        common = self.settings["test"].get("machine_lamps") or {}
        if common:
            box = tk.Frame(bottom, bg=theme.PANEL, highlightbackground=theme.BORDER, highlightthickness=1)
            box.grid(row=0, column=len(self.SIDES), sticky="ns", padx=(8, 0))
            tk.Label(box, text="Machine", bg=theme.PANEL, fg=T.L_TEXT, font=_font(9)).grid(
                row=0, column=0, rowspan=LAMP_ROWS, sticky="n", padx=(6, 2), pady=4)
            for j, name in enumerate(common):
                row = tk.Frame(box, bg=theme.PANEL)
                row.grid(row=j % LAMP_ROWS, column=1 + j // LAMP_ROWS, sticky="w", padx=(6, 6), pady=1)
                cv = tk.Canvas(row, width=16, height=16, bg=theme.PANEL, highlightthickness=0)
                dot = cv.create_oval(2, 2, 14, 14, fill=LAMP_OFF, outline=T.L_BORDER)
                cv.pack(side="left")
                tk.Label(row, text=name, bg=theme.PANEL, fg=T.L_TEXT, font=_font(9)).pack(side="left", padx=3)
                self.machine_lamps[name] = (cv, dot)

    # ------------------------------------------------------- side panel
    # Same card style as the COM Setting page: a title strip with an orange
    # accent, a dark body, large coloured number tiles.
    SIDE_W = 270
    CARD = T.L_SURFACE
    CARD_HEAD = T.L_SURFACE_2
    TILE = T.L_SUNKEN

    def _side_card(self, parent, title, right=None, expand=False):
        """A card with a title strip; `right` builds an optional widget on the strip.
        expand=True: the card takes the panel's spare height."""
        card = tk.Frame(parent, bg=self.CARD, highlightbackground=theme.BORDER, highlightthickness=1)
        card.pack(fill="both" if expand else "x", expand=expand, pady=(0, 4))
        head = tk.Frame(card, bg=self.CARD_HEAD)
        head.pack(fill="x")
        tk.Frame(head, bg=theme.TITLE_FG, width=3).pack(side="left", fill="y")
        tk.Label(head, text=title.upper(), bg=self.CARD_HEAD, fg=T.L_TEXT_2, font=_font(8, True)).pack(
            side="left", padx=8, pady=2)
        if right:
            right(head).pack(side="right", padx=6)
        body = tk.Frame(card, bg=self.CARD)
        body.pack(fill="both" if expand else "x", expand=expand, padx=8, pady=4)
        return body

    def _link(self, parent, text, command, fg=theme.HEADER_FG):
        """A small flat text button for secondary actions (reset, open)."""
        return tk.Button(parent, text=text, command=command, bg=parent["bg"], fg=fg, activebackground=parent["bg"],
                         activeforeground=T.L_PRIMARY_DK, bd=0, font=_font(8, True), cursor="hand2", padx=2, pady=0)

    def _tile(self, parent, caption, value, color, size=18):
        """A number tile: big value with a small caption under it. Returns the value label."""
        t = tk.Frame(parent, bg=self.TILE, highlightbackground=T.L_BORDER_2, highlightthickness=1)
        val = tk.Label(t, text=str(value), bg=self.TILE, fg=color, font=_font(size, True))
        val.pack(pady=(1, 0))
        tk.Label(t, text=caption, bg=self.TILE, fg=theme.MUTED, font=_font(7, True)).pack(pady=(0, 2))
        return t, val

    def _build_side_panel(self, parent):
        side = tk.Frame(parent, bg=theme.BG, width=self.SIDE_W)
        side.pack_propagate(False)
        c = self.counters

        # ---- Test control ------------------------------------------------
        # Packed first so the buttons always keep their place at the foot of the panel.
        ctl = tk.Frame(side, bg=theme.BG)
        ctl.pack(fill="x", side="bottom", pady=(3, 0))
        # The test flow's current step, right above the buttons that drive it
        self.flow_lbl = tk.Label(ctl, text="Press RUN", bg=self.CARD, fg=theme.MUTED, font=_font(9, True),
                                 pady=2, highlightbackground=theme.BORDER, highlightthickness=1)
        self.flow_lbl.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 3))
        # One row: RUN | STOP | ORIGIN
        for col in range(3):
            ctl.columnconfigure(col, weight=1, uniform="ctl")
        for col, (text, cmd, bg, abg) in enumerate((
                ("▶ RUN", self.start_flow, theme.PASS, "#256b29"),
                ("■ STOP", self.stop_flow, theme.FAIL, "#a61f1f"),
                ("↺ ORIGIN", lambda: self.plc_pulse("move_origin", "Move origin"), T.L_NEUTRAL_BTN, "#4b5560"))):
            tk.Button(ctl, text=text, command=cmd, bg=bg, fg="white", activebackground=abg, activeforeground="white",
                      bd=0, pady=7, font=_font(11, True), cursor="hand2").grid(
                row=1, column=col, sticky="nsew", padx=(0 if col == 0 else 3, 0))

        # ---- Model -----------------------------------------------------
        # The loaded model (name / type) in the box, its part number on the title strip
        def part_label(head):
            self.part_lbl = tk.Label(head, text="Part —", bg=self.CARD_HEAD, fg=T.L_TEXT_2, font=_font(8))
            return self.part_lbl

        body = self._side_card(side, "Model", right=part_label)
        self.current_model = tk.Label(body, text=self.settings["test"]["select"].get("model") or "No model loaded",
                                      bg=self.TILE, fg=theme.TITLE_FG, font=_font(12, True), anchor="w",
                                      padx=8, pady=3, highlightbackground=T.L_BORDER_2, highlightthickness=1)
        self.current_model.pack(fill="x", pady=(0, 4))
        # Model number: type or scan it, or pick it from the drop-down list of models,
        # then Enter or MODEL CHANGE fetches it from the database.
        mrow = tk.Frame(body, bg=self.CARD)
        mrow.pack(fill="x")
        tk.Label(mrow, text="MODEL NO", bg=self.CARD, fg=theme.MUTED, font=_font(8, True)).pack(side="left")
        self.model_entry = ttk.Combobox(mrow, values=[], font=_font(10), height=15, style="L.TCombobox")
        self.model_entry.pack(side="left", fill="x", expand=True, padx=(6, 0))
        self.model_entry.bind("<Return>", lambda _e: self.model_change())
        self._models = []
        tk.Button(body, text="MODEL CHANGE", command=self.model_change, bg=theme.UPDATE, fg="white",
                  activebackground=T.L_PRIMARY_DK, activeforeground="white", bd=0, pady=4, font=_font(9, True),
                  cursor="hand2").pack(fill="x", pady=(4, 0))

        # ---- Production: target / complete + progress bar ----------------
        body = self._side_card(side, "Production")
        row = tk.Frame(body, bg=self.CARD)
        row.pack(fill="x")
        row.columnconfigure((0, 1), weight=1, uniform="prod")
        tgt = tk.Frame(row, bg=self.TILE, highlightbackground=T.L_BORDER_2, highlightthickness=1)
        tgt.grid(row=0, column=0, sticky="nsew", padx=(0, 3))
        # Plain text box, whole numbers only; empty = target not set (RUN refuses to start)
        self.target = tk.Entry(tgt, width=7, justify="center", font=_font(14, True), bg=self.TILE, fg=T.L_TEXT,
                               relief="flat", bd=0, insertbackground=T.L_TEXT, highlightthickness=1,
                               highlightbackground=self.TILE, highlightcolor=theme.UPDATE)
        digits_only = self.target.register(lambda p: p == "" or (p.isdigit() and len(p) <= 6))
        self.target.configure(validate="key", validatecommand=(digits_only, "%P"))
        if c.get("target"):
            self.target.insert(0, str(c["target"]))
        self.target.bind("<Return>", lambda _e: self.set_target())
        self.target.bind("<KP_Enter>", lambda _e: self.set_target())
        self.target.bind("<FocusOut>", lambda _e: self._discard_target_edit())
        self.target.pack(pady=(1, 0), padx=6, fill="x")
        tk.Label(tgt, text="TARGET", bg=self.TILE, fg=theme.MUTED, font=_font(7, True)).pack(pady=(0, 2))
        t, self.lbl_complete = self._tile(row, "COMPLETE", c["complete"], T.L_PRIMARY, size=14)
        t.grid(row=0, column=1, sticky="nsew", padx=(3, 0))
        prow = tk.Frame(body, bg=self.CARD)
        prow.pack(fill="x", pady=(4, 0))
        self.progress_lbl = tk.Label(prow, text="", bg=self.CARD, fg=theme.MUTED, font=_font(8, True))
        self.progress_lbl.pack(side="right", padx=(6, 0))
        self.progress = tk.Canvas(prow, height=8, bg=self.TILE, highlightthickness=0)
        self.progress.pack(side="left", fill="x", expand=True)
        self.progress.bind("<Configure>", lambda _e: self._draw_progress())

        # ---- Results: O.K / N.G, today large, cumulative small -----------
        body = self._side_card(side, "Results")
        row = tk.Frame(body, bg=self.CARD)
        row.pack(fill="x")
        row.columnconfigure((0, 1), weight=1, uniform="res")
        t, ok_day = self._tile(row, "O.K  TODAY", c["ok_day"], theme.PASS, size=16)
        t.grid(row=0, column=0, sticky="nsew", padx=(0, 3))
        t, ng_day = self._tile(row, "N.G  TODAY", c["ng_day"], theme.FAIL, size=16)
        t.grid(row=0, column=1, sticky="nsew", padx=(3, 0))
        # Cumulative counts on the left, the reset links on the right of the same line
        tot = tk.Frame(body, bg=self.CARD)
        tot.pack(fill="x", pady=(3, 0))
        self._link(tot, "↺ TOTAL", lambda: self.reset_counts("total"), fg=T.L_PRIMARY).pack(side="right")
        self._link(tot, "↺ TODAY", lambda: self.reset_counts("day"), fg=T.L_PRIMARY).pack(side="right", padx=(0, 6))
        tk.Label(tot, text="Total", bg=self.CARD, fg=theme.MUTED, font=_font(8)).pack(side="left")
        ok_total = tk.Label(tot, text=str(c["ok_total"]), bg=self.CARD, fg=theme.PASS, font=_font(10, True))
        ok_total.pack(side="left", padx=(8, 0))
        tk.Label(tot, text="/", bg=self.CARD, fg=theme.MUTED, font=_font(9)).pack(side="left", padx=3)
        ng_total = tk.Label(tot, text=str(c["ng_total"]), bg=self.CARD, fg=theme.FAIL, font=_font(10, True))
        ng_total.pack(side="left")
        self.lbl_ok = [ok_day, ok_total]
        self.lbl_ng = [ng_day, ng_total]

        # ---- Saved data --------------------------------------------------
        # Title strip: saved counts per cable, then OPEN
        def saved_head(head):
            f = tk.Frame(head, bg=self.CARD_HEAD)
            self._link(f, "📂 OPEN", self.open_saved, fg=T.L_PRIMARY).pack(side="right", padx=(10, 0))
            self.lbl_saved = []
            for name, value in (("SH", c["saved_shift"]), ("SL", c["saved_select"])):   # SHIFT / SELECT
                lbl = tk.Label(f, text=str(value), bg=self.CARD_HEAD, fg=T.L_TEXT, font=_font(9, True))
                lbl.pack(side="right")
                tk.Label(f, text=name, bg=self.CARD_HEAD, fg=theme.MUTED, font=_font(7, True)).pack(
                    side="right", padx=(8, 3))
                self.lbl_saved.insert(0, lbl)      # keep order: [SELECT, SHIFT]
            return f

        body = self._side_card(side, "Saved", right=saved_head)
        self.save_dir = tk.Entry(body, bg=self.TILE, fg=T.L_TEXT, insertbackground=T.L_TEXT, font=_font(9),
                                 relief="flat", bd=0, highlightthickness=1, highlightbackground=T.L_BORDER_2)
        self.save_dir.insert(0, self.settings["test"]["save_dir"])
        self.save_dir.bind("<FocusOut>", lambda _e: self._save_counters())
        self.save_dir.pack(fill="x", ipady=2)

        # ---- Scan results (barcode scanner input, one per cable) ---------
        body = self._side_card(side, "Scan Result")
        body.columnconfigure(1, weight=1)
        self.scan_entries = {}
        for r, (key, label) in enumerate((("select", "SELECT"), ("shift", "SHIFT"))):
            tk.Label(body, text=label, bg=self.CARD, fg=theme.MUTED, font=_font(8, True), width=7,
                     anchor="w").grid(row=r, column=0, sticky="w", pady=1)
            e = tk.Entry(body, bg=self.TILE, fg=T.L_TEXT, insertbackground=T.L_TEXT, font=_font(10),
                         relief="flat", bd=0, highlightthickness=1, highlightbackground=T.L_BORDER_2,
                         highlightcolor=theme.UPDATE)
            e.grid(row=r, column=1, sticky="ew", pady=1, ipady=1)
            # A barcode scanner types the label and ends with Enter.
            e.bind("<Return>", lambda _e, k=key: self.on_scan(k))
            self.scan_entries[key] = e

        self.after(50, self._draw_progress)
        return side

    def _draw_progress(self):
        """Complete / Target as a bar; full and green once the target is reached."""
        cv = self.progress
        cv.delete("all")
        w = cv.winfo_width()
        target, done = self.counters.get("target", 0), self.counters.get("complete", 0)
        self.progress_lbl.config(text=f"{done} / {target}  ·  {min(100, round(100 * done / target))} %"
                                 if target > 0 else "no target")
        if w <= 1 or target <= 0:
            return
        frac = min(done / target, 1.0)
        cv.create_rectangle(0, 0, int(w * frac), 8, fill=theme.PASS if frac >= 1 else T.L_PRIMARY, width=0)

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
        """EXIT: close the app through the same shutdown as the window's close button."""
        busy = self.test_in_progress()
        text = (f"{busy}.\n\nClose the Friction Tester anyway? The part will not be recorded."
                if busy else "Close the Friction Tester?")
        if not messagebox.askyesno("Exit", text, icon="warning" if busy else "question", parent=self):
            return
        self.winfo_toplevel().event_generate("<<AppExit>>")      # App.close(), already confirmed

    def test_in_progress(self):
        """Why closing now would lose a part, or None."""
        state = self.flow.state if self.shared and self.flow else self._flow
        if "_run" in state:
            return "A test is running"
        if state == "at_scan_shift":
            return "This part's SELECT test is done but its SHIFT test is not"
        return None

    def pending_saves(self):
        """Result rows still being written to the database."""
        return [t for t in getattr(self, "_save_threads", []) if t.is_alive()]

    def shutdown(self):
        """Stop all background work before the devices are closed (app close).

        Nothing is written to the PLC: a running machine cycle is left to the
        machine. Returns the worker threads to wait for. Safe to call twice.
        """
        if getattr(self, "_shut_down", False):
            return []
        self._shut_down = True
        self._alive = False
        self._stop_event.set()
        self._pending.clear()
        threads = [t for t in (getattr(self, "_poll_thread", None),) if t]
        if self.ctl is not None:
            self.ctl.submit("stop")
            self.ctl.stop_thread()       # its loop releases any request bit on the way out
            if self.ctl._thread:
                threads.append(self.ctl._thread)
        if self.monitor:
            self.monitor.cancel()
            self.monitor.stop()
            if self.monitor._thread:
                threads.append(self.monitor._thread)
        return threads

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
        _ui.info(f"{label} -> {command}")
        if self.kd:
            if command in ("move_origin", "origin_select", "origin_shift"):
                self.ctl.submit("home")            # one X axis in plc kd.pdf: every ORIGIN homes it
            else:
                self._set_status(f"{label}: not assigned in plc kd.pdf (no PC alarm reset; drive reset is "
                                 "P00003 in the homing sequence) - see docs/plc_kd_profile.md", theme.BUSY, 6)
            return
        if self.pc:
            homes = {"move_origin": "all", "origin_select": "select", "origin_shift": "shift"}
            resets = {"alarm_reset_select": "select", "alarm_reset_shift": "shift"}
            if command in homes:
                self.ctl.submit("home", homes[command])
            elif command in resets:
                self.ctl.submit("alarm_reset", resets[command])
            else:
                self._set_status(f"{label}: not assigned in this PLC program (plc.pdf) - see the unresolved "
                                 "list in docs/plc_pc_interface.md", theme.BUSY, 6)
            return
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

    # "Servo ON Output" (PLC backup 2026-11-08: P00030 = the ladder's SHIFT drive, P00034 = SELECT), by cable
    SERVO_OUT = {cable: {"shift": "P00030", "select": "P00034"}[drive] for cable, drive in pp.DRIVE_OF.items()}

    def _servo_explanation(self, key, n):
        """PRINTING PROGRAM OLD: the PLC drives servo ON itself (automatic branch, M0700F OFF);
        say what the lamps show instead of offering a button that cannot help."""
        poll = getattr(self, "_last_poll", None) or {}
        sides = poll.get("sides") or []
        fb = (sides[n].get("lamps") or {}).get("Servo") if n < len(sides) else None
        out_addr = self.SERVO_OUT[key]
        names = {str(a).strip().upper(): name for name, a in (self.settings["test"].get("machine_lamps") or {}).items()}
        out = (poll.get("common") or {}).get(names.get(out_addr)) if out_addr in names else None
        axis = pp.AXIS_NAME[key]
        if out and not fb:
            return (f"{axis} servo: the PLC already sends servo ON ({out_addr} ON) but the drive does not confirm "
                    f"it (servo status {pp.STATUS[key]['servo_on'][2]} OFF) - check the servo drive at "
                    "the machine (display, main power, reset after an emergency stop). The app cannot fix this.")
        if out and fb:
            return f"{axis} servo is ON (PLC output {out_addr} and drive status both ON)."
        if out is False:
            return f"{axis} servo OFF ({out_addr} OFF): " + self._servo_blockers(key, poll.get("common") or {}, names)
        return (f"{axis} servo: the PLC switches servo ON itself in this program; servo state not read yet "
                "(PLC lamps not available).")

    def _servo_blockers(self, key, common, names):
        """Why the PLC holds its servo ON output OFF, from the rung (plc.pdf p27-28):
        P00030 = [(M00105 AND M00109) OR (NOT M00105 AND M0700F AND M07007) OR (NOT M00105 AND T0050)]
                 AND P00013 AND P0000D AND P0000F        (SELECT: P00034, M00205, M00209, M0700B, P00017)."""
        snap = getattr(self, "_pc_snap", None)

        def estop(status_name, addr):
            v = snap.common(status_name) if snap is not None else None
            return common.get(names.get(addr)) if v is None and addr in names else v

        why = []
        if estop("p0000f_input", "P0000F") is False:
            why.append("emergency stop TOP is pressed (P0000F OFF) - release it")
        if estop("p0000d_input", "P0000D") is False:
            why.append("emergency stop BOTTOM is pressed (P0000D OFF) - release it")
        alarm_addr = pp.STATUS[key]["alarm_normal"][2]
        if snap is not None and snap.st(key, "alarm_normal") is False:
            why.append(f"the servo drive is in alarm ({alarm_addr} OFF) - clear the alarm at the drive / touch panel")
        sel_addr = pp.SELECTOR[key]
        sel = snap.selector(key) if snap is not None else None
        if sel:
            why.append(f"the touch panel is in touch operation ({sel_addr} ON) - press servo ON on the touch panel, "
                       "or switch touch operation off so the PLC turns the servo on itself")
        if why:
            return "; ".join(why) + "."
        if sel is False:
            return ("emergency stops, drive alarm and touch-operation select are all normal, so the PLC start-up "
                    "timer T0050 has not finished - check the PLC is in RUN (power the PLC off and on).")
        return (f"emergency stops and drive alarm read normal; touch-operation select {sel_addr} cannot be read here. "
                f"Either touch operation is ON at the touch panel (press servo ON there or switch it off), or the "
                "PLC has not finished start-up (T0050) - check the PLC is in RUN.")

    def toggle_servo(self, key, n):
        """Servo ON/OFF: a maintained PLC bit."""
        _ui.info(f"servo toggle -> {key}")
        if self.kd:
            self._set_status("Servo ON/OFF: not assigned in plc kd.pdf (servo ON output P00020 is always ON)",
                             theme.BUSY, 6)
            return
        if self.pc:
            self._set_status(self._servo_explanation(key, n), theme.BUSY, 10)
            return
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
                       bg=theme.PASS if on else T.L_SURFACE_2, activebackground=theme.PASS if on else T.L_SURFACE_2,
                       fg="white" if on else T.L_TEXT)

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
        if error is not None:
            self.model_entry.config(values=[])
            self._set_status(f"Model list not loaded - database unavailable ({error})", theme.FAIL, 8)
            return
        self.model_entry.config(values=[r["MM_MODEL"] for r in rows])

    def model_change(self):
        """Load the selected model: its SELECT CABLE and SHIFT CABLE conditions."""
        if self._flow != "idle":
            self._set_status("Model Change: press STOP first — a test is in progress", theme.BUSY)
            return
        model = self.model_entry.get().strip()
        if not model:
            self._set_status("Model Change: type, scan or pick a model number first", theme.BUSY)
            return
        self.fetch_model(model)

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
        from .. import db
        values = db.plc_params_from_row(row)
        self.settings["test"]["plc_params"] = values
        if self.pc and self.ctl is not None:
            self.ctl.submit("params", row["MM_MODEL"], values)
        if getattr(self, "manual", None) is not None:
            self.manual.refresh_params()
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
        self.part_lbl.config(text=f"Part  {part}" if part else "Part —")
        self.model_entry.delete(0, "end")
        if model:
            self.model_entry.insert(0, model)

    def _label_matches_part(self, label, key="select"):
        """Scanned-label check per test.label_rule:
        contains_part_number - the label contains the model's part number (spaces/hyphens ignored)
        regex                - the label matches the pattern set for that field
        none                 - no format rule defined (accepted, and the status says so)"""
        import re
        rule = self.settings["test"].get("label_rule") or {"mode": "contains_part_number"}
        mode = rule.get("mode", "contains_part_number")
        if mode == "none":
            self._set_status("No label format rule defined (test.label_rule) - label accepted unchecked",
                             theme.BUSY, 4)
            return True
        if mode == "regex":
            pattern = (rule.get(key) or "").strip()
            if not pattern:
                self._set_status(f"No label pattern defined for {key.upper()} (test.label_rule.{key})",
                                 theme.BUSY, 4)
                return True
            return re.fullmatch(pattern, label) is not None
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
        if self._skip_scan() and state == "at_scan_shift":
            text = "AT · Press RUN for SHIFT"
        self.flow_lbl.config(text=text, fg=color)
        # Outline the box the operator should scan into next
        expect = {"at_scan_select": "select", "at_scan_shift": "shift", "mt_scan": "select"}.get(state)
        if self._skip_scan():
            expect = None
        for key, e in self.scan_entries.items():
            e.config(highlightbackground=theme.TITLE_FG if key == expect else T.L_BORDER_2,
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
        if self._flow == "idle":
            try:                             # typed (manual) conditions must be in range too
                for panel in self.panels:
                    panel.read_conditions()
            except ValueError as exc:
                messagebox.showerror("Test Condition", str(exc), parent=self)
                self._set_status(f"RUN blocked: {exc}", theme.FAIL, hold=6)
                return
        if self._flow == "idle" and not self._skip_scan() and not self.settings["test"].get("part_number"):
            messagebox.showwarning("Part not set", "The loaded model has no Part Number.\n\n"
                                   "Set it in Model Settings, then load the model again.", parent=self)
            return
        if self.shared:
            t = self.settings["test"]
            if self.flow.run(t["select"].get("model"), t.get("model_type", ""), t.get("part_number", "")):
                for e in self.scan_entries.values():
                    e.delete(0, "end")
            return
        if self.pc:
            if self._flow == "idle" and self.store.pending():
                self.store.retry()
                self._set_status(f"RUN blocked: {len(self.store.pending())} finished part(s) not saved yet - "
                                 "retrying the database", theme.FAIL, 8)
                return
            if self._flow == "idle":
                for e in self.scan_entries.values():
                    e.delete(0, "end")
            self.ctl.submit("run", self._pc_info())
            return
        if self._skip_scan() and self._flow == "at_scan_shift":
            self._set_flow("at_run_shift")         # skip scan: RUN starts the SHIFT stage
            self._start_cable_tests([1])
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
        if self._skip_scan():
            if mtype == "MT":
                self._set_flow("mt_run")
                self._start_cable_tests([0, 1])
            else:
                self._set_flow("at_run_select")
                self._start_cable_tests([0])
            return
        self._set_flow(self._first_scan_state())
        self._set_status(f"{model} ({mtype}): scan the {'label' if mtype == 'MT' else 'SELECT label'}",
                         theme.PASS)

    def _skip_scan(self):
        """Temporary: RUN starts the test without label scans (test.skip_scan)."""
        return bool(self.settings["test"].get("skip_scan"))

    def stop_flow(self):
        """STOP: cancel the flow and tell the machine to stop (if configured)."""
        if self.shared:
            self.flow.stop()             # app flow only - no stop command is sent to the PLC
            if self.kd:
                self.ctl.submit("stop")  # cancels an ORIGIN request the PLC has not taken yet
            return
        if self.pc:
            self.ctl.submit("stop")      # releases requests; neither PLC program has a PC stop command
            return
        if self._flow == "idle":
            self.plc_pulse("test_stop", "Test stop")
            return
        self._stop_event.set()
        self._pending.clear()
        for p in self.panels:
            if p.judge.cget("text") == "TEST":
                p.judge.config(text="– – –", fg=T.L_TEXT_3)
        self._set_flow("idle")
        self._set_status("Test stopped", theme.BUSY)
        if self.settings["test"]["commands"].get("test_stop", "").strip() and self.plc.is_open:
            self.plc_pulse("test_stop", "Test stop")

    def on_scan(self, key):
        """A label was scanned (or typed + Enter) into the SELECT or SHIFT box."""
        label = self.scan_entries[key].get().strip()
        if not label:
            return
        _ui.info(f"scan {key.upper()}: {label!r}")
        if self._flow in ("at_scan_select", "at_scan_shift", "mt_scan") and not self._label_matches_part(label, key):
            part = self.settings["test"].get("part_number", "") or "(none set)"
            messagebox.showerror("Wrong label",
                                 f"The scanned label does not match the loaded part.\n\n"
                                 f"Expected part: {part}\nScanned: {label}", parent=self)
            self.scan_entries[key].delete(0, "end")
            self._set_status(f"Label rejected: expected part {part}", theme.FAIL, hold=6)
            return
        if self.shared or self.pc:
            if self._flow == "mt_scan":
                for e in self.scan_entries.values():
                    if e.get().strip() != label:
                        e.delete(0, "end")
                        e.insert(0, label)
            if self.pc:
                self.ctl.submit("scan", key, label)
            else:
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
        if self._uses_test_run(cables):
            for n in cables:
                self.panels[n].reset_results()
                self.panels[n].judge.config(text="TEST", fg=theme.BUSY)
            self._run_machine_cycle(cables)
            return
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

    # ------------------------------------------- RUN through test_run (M00029)
    def _uses_test_run(self, cables):
        """True when RUN should start the machine with test.commands.test_run:
        it is set and none of these cables has its own start/done bits."""
        cmds = self.settings["test"]["commands"]
        if not cmds.get("test_run", "").strip():
            return False
        key = lambda n, kind: cmds.get(f"{kind}_{self.SIDES[n][0]}", "").strip()
        return not any(key(n, "start") or key(n, "done") for n in cables)

    def _run_machine_cycle(self, cables):
        """One pulse of test_run starts ONE ladder cycle (both channels), so an
        MT part gets one pulse and each AT stage one. The end of the cycle is
        read from the ladder (step D05010 back to 0 with one OK or NG counter
        increment, as in shared-cycle mode).

        The pulse is written only through the explicit Modbus mapping, and only
        when it is verified and live start is enabled: with the legacy
        word*16+bit assumption M00029 and the output P00029 land on the same
        coil (docs/plc_shared_cycle.md). Nothing is retried.
        """
        addr = self.settings["test"]["commands"]["test_run"].strip()
        shared = self.settings["test"]["integration"]["shared"]
        if not self.plc.is_open:
            self._abort_flow(f"RUN ({addr}): PLC not connected")
            return
        mapping = ModbusMapping(shared.get("mapping"))
        backend = ModbusPlcBackend(self.plc, mapping, allow_writes=bool(shared.get("live_start_enabled")))
        if not backend.can_write:
            missing = [w for w, ok in (("the Modbus address mapping is verified", mapping.verified),
                                       ("live start is enabled", shared.get("live_start_enabled"))) if not ok]
            self._abort_flow(
                f"RUN: {addr} was not written — PLC writes stay blocked until "
                f"{' and '.join(missing)} (settings.json → test.integration.shared: mapping.verified, "
                f"live_start_enabled). Under the unverified mapping {addr} and P00029 are the same coil.")
            return
        tracker = CycleTracker(start_timeout=float(shared.get("start_timeout", 10.0)))
        stop = self._stop_event
        names = " + ".join(self.SIDES[n][1] for n in cables)

        def snapshot():
            try:
                return read_snapshot(backend)
            except Exception:
                return None

        def run():
            try:
                tracker.observe(snapshot())               # baseline: current counters and step
                if not tracker.connected:
                    raise RuntimeError("PLC state could not be read")
                if tracker.last.step != 0:
                    raise RuntimeError(f"machine is already in a cycle (step {tracker.last.step}) — "
                                       "wait for it to finish")
                tracker.arm(names)
                backend.write_bit(addr, True)
                time.sleep(0.3)
                backend.write_bit(addr, False)
            except Exception as exc:
                self.after(0, lambda e=exc: self._abort_flow(f"RUN ({addr}): {e}"))
                return
            deadline = time.monotonic() + 300
            while not stop.is_set():
                for ev in tracker.observe(snapshot()):
                    if ev.kind == "completed":
                        ok = ev.data["verdict"] == "OK"
                        self.after(0, lambda o=ok: self._machine_cycle_finished(cables, o))
                        return
                    if ev.kind in ("ended_without_result", "start_timeout", "ambiguous"):
                        self.after(0, lambda m=ev.message: self._abort_flow(f"{names}: {m}"))
                        return
                if time.monotonic() > deadline:
                    self.after(0, lambda: self._abort_flow(f"{names}: cycle did not finish within 5 minutes"))
                    return
                time.sleep(0.25)

        threading.Thread(target=run, daemon=True).start()

    def _machine_cycle_finished(self, cables, ok):
        """The PLC's overall verdict for the cycle applies to every cable it tested."""
        for n in cables:
            panel = self.panels[n]
            panel.last_ok = ok
            panel.judge.config(text="OK" if ok else "NG", fg=theme.PASS if ok else theme.FAIL)
        for n in cables:
            self._cable_test_finished(n)

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
            self._set_status("SELECT CABLE done — " + ("press RUN for the SHIFT stage" if self._skip_scan()
                                                         else "scan the SHIFT label"), theme.PASS)
        elif self._pending:
            return                              # MT: waiting for the other cable
        else:
            for e in self.scan_entries.values():
                e.delete(0, "end")
            if self._skip_scan():
                self._set_flow("idle")          # next part only on the next RUN
                self._set_status("Part complete — press RUN for the next part", theme.PASS)
            else:
                self._set_flow(self._first_scan_state())
                self._set_status("Part complete — scan the next label", theme.PASS)

    def _abort_flow(self, message):
        self._stop_event.set()
        self._pending.clear()
        for p in self.panels:
            if p.judge.cget("text") == "TEST":
                p.judge.config(text="– – –", fg=T.L_TEXT_3)
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
            self.shutdown()          # backstop: normally the app has already done this

    def _log_buttons(self, widget):
        """Print every button press on this page to the terminal."""
        for w in widget.winfo_children():
            if isinstance(w, tk.Button):
                w.bind("<ButtonRelease-1>", self._log_click, add="+")
            self._log_buttons(w)

    def _log_click(self, event):
        w = event.widget
        try:
            # Tk only runs the command if the pointer is still on an enabled button.
            if str(w["state"]) == "disabled" or w.winfo_containing(event.x_root, event.y_root) is not w:
                return
            _ui.info(f"button {w['text'].strip()!r}")
            self._last_status = None      # print the press's result even if it repeats the last message
        except Exception:
            pass

    # ------------------------------------------------------- manual view
    def manual_shown(self):
        return getattr(self, "manual", None) is not None and self.manual.winfo_ismapped()

    def show_manual(self):
        self.measure_view.pack_forget()
        self.manual.refresh_params()
        self.manual.pack(fill="both", expand=True)
        self.screen_tag.config(text="🕹  Manual Operation")
        self.manual_btn.config(text="📈  Measurement")

    def jog(self, axis, op, repeat=False):
        """Manual view: start / refresh (op) or stop (None) a PC jog. Returns False if not sent."""
        if not (self.pc and self.ctl is not None):
            if op is not None and not repeat:
                self._set_status(f"{pp.AXIS_NAME[axis]} jog: PC jog exists only in the PRINTING PROGRAM OLD "
                                 "profile (mode pc_interface)", theme.BUSY, 5)
            return False
        self.ctl.submit("jog", axis, op)
        return True

    def show_measurement(self):
        self.manual.jog_release()
        self.manual.pack_forget()
        self.measure_view.pack(fill="both", expand=True)
        self.screen_tag.config(text="📈  Measurement Screen")
        self.manual_btn.config(text="🕹  Manual")

    def toggle_manual(self):
        if self.manual_shown():
            self.show_measurement()
        else:
            self.show_manual()

    def _set_status(self, text, color=T.L_TEXT, hold=3):
        """Show an action's message; polling won't overwrite it for `hold` seconds."""
        if text and text != getattr(self, "_last_status", None):
            _status.info(text)
        self._last_status = text
        self.status.config(text=text, fg=color)
        self._hold_until = time.monotonic() + hold

    def _poll(self):
        """Every POLL_MS, read the connected devices on a worker thread."""
        if not self._alive:
            return
        snapshot, self._poll_snapshot = getattr(self, "_poll_snapshot", None), None
        if snapshot is not None:           # what the worker read: applied here, on the UI thread
            self._apply(snapshot)
        if not self._polling:
            self._polling = True
            self._poll_thread = threading.Thread(target=self._poll_worker, daemon=True)
            self._poll_thread.start()
        self.after(POLL_MS, self._poll)

    def _poll_worker(self):
        snapshot = {"missing": [], "sides": [], "common": {}}
        try:
            if not self.plc.is_open and not self.shared:
                snapshot["missing"].append("PLC")      # shared mode: the PLC strip shows it
            m = self.settings["test"]["machine_profile"].get("measurement") or {}
            ni = self.sampler is not None and m.get("force_source", "serial") == "daq"
            for i, (key, _t, _s) in enumerate(self.SIDES):
                side = {"load": None, "stroke": None, "lamps": {}}
                lc = self.loadcells[i]
                capturing = self.sampler is not None and key in self.sampler.active_axes()
                if capturing:
                    side["capturing"] = True           # the capture owns the readouts
                elif ni:                               # load (and stroke) from the NI card
                    try:
                        side["load"], side["stroke"] = self.sampler.read_once(key)[:2]
                    except Exception:
                        if "NI card" not in snapshot["missing"]:
                            snapshot["missing"].append("NI card")
                elif lc.is_open:
                    try:
                        side["load"] = lc.read()[2]
                    except Exception:
                        pass
                else:
                    snapshot["missing"].append(lc.name.split(" (")[0])
                snapshot["sides"].append(side)
            if self.plc.is_open:
                self._read_sensor_lamps(snapshot["sides"], snapshot["common"])
        finally:
            self._poll_snapshot = snapshot     # the worker never touches Tk: _poll applies it
            self._polling = False

    def _lamp_backend(self):
        """Read-only view of the P inputs through machine_profile.mapping (bit_read area, FC02)."""
        if getattr(self, "_lamp_be", None) is None:
            mapping = make_mapping(self.settings["test"]["machine_profile"].get("mapping"))
            self._lamp_be = ModbusPlcBackend(self.plc, mapping, allow_writes=False)
        return self._lamp_be

    def _read_sensor_lamps(self, sides, common=None):
        """All lamps (both cables + machine row) in as few Modbus reads as the mapping allows
        (worker thread). Each value goes into side["lamps"] or `common`."""
        try:
            wanted = []        # (target dict, name, address)
            for (key, _t, _s), side in zip(self.SIDES, sides):
                cfg = self.settings["test"][key]
                for name, setting in SENSOR_LAMPS.items():
                    addr = str(cfg.get(setting, "")).strip().upper()
                    if addr:
                        wanted.append((side["lamps"], name, addr))
            if common is not None:
                for name, addr in (self.settings["test"].get("machine_lamps") or {}).items():
                    addr = str(addr or "").strip().upper()
                    if addr:
                        wanted.append((common, name, addr))
            if not wanted:
                return
            bits = self._lamp_backend().read_bits(sorted({a for _t, _n, a in wanted}))
            for target, name, addr in wanted:
                target[name] = bool(bits[addr])
        except Exception as exc:
            if str(exc) != getattr(self, "_lamp_error", None):
                _ui.warning(f"sensor lamps not read: {exc}")
            self._lamp_error = str(exc)
        else:
            self._lamp_error = None

    def _apply(self, snap):
        if not self._alive:
            return
        self._last_poll = snap
        for i, side in enumerate(snap["sides"]):
            if not self.shared and not side.get("capturing"):   # shared: the PLC monitor owns them
                self.panels[i].set_live(load=side["load"], stroke=side.get("stroke"))
            for name, (cv, dot) in self.lamps[i].items():
                cv.itemconfig(dot, fill=LAMP_ON if side["lamps"].get(name) else LAMP_OFF)
        for name, (cv, dot) in getattr(self, "machine_lamps", {}).items():
            cv.itemconfig(dot, fill=LAMP_ON if snap.get("common", {}).get(name) else LAMP_OFF)
        if self.manual_shown():
            latest = getattr(self.sampler, "latest", None) or {}
            strokes = {k: v[1] for k, v in latest.items() if isinstance(v, (tuple, list)) and len(v) > 1}
            self.manual.update_view(snap["sides"], self._pc_snap, strokes)
        if time.monotonic() < self._hold_until:
            return   # keep the last action's message on screen for a moment
        if snap["missing"]:
            self.status.config(text="Not connected: " + ", ".join(snap["missing"]), fg=theme.FAIL)
        else:
            self.status.config(text="Ready", fg=theme.PASS)
