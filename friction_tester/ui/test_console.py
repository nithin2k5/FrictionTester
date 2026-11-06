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

from . import theme
from .plot import LoadStrokePlot
from ..config import save_settings

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

        # Title row with Screen Save
        top = tk.Frame(self, bg=theme.PANEL)
        top.pack(fill="x", padx=10, pady=(6, 2))
        # OK / NG judgement, left of the title
        self.judge = tk.Label(top, text="– – –", bg=FIELD_BG, fg="#4f8cff", font=_font(13, True),
                              relief="solid", bd=1, width=5)
        self.judge.pack(side="left")
        tk.Label(top, text="Max", bg=theme.PANEL, fg="white", font=_font(9, True)).pack(side="left", padx=(8, 3))
        self.res = {"max": self._value(top, "0.00")}
        self.res["max"].pack(side="left")
        tk.Label(top, text="kgf", bg=theme.PANEL, fg="white", font=_font(8)).pack(side="left", padx=(2, 0))
        tk.Button(top, text="Screen Save", command=self.save_screen, bg="#333", fg="white",
                  activebackground="#444", activeforeground="white", bd=0, padx=8, pady=2,
                  font=_font(8, True), cursor="hand2").pack(side="right")
        tk.Label(top, text=title, bg=theme.PANEL, fg="white", font=_font(14)).pack(side="left", expand=True)
        tk.Frame(self, bg=theme.BORDER, height=1).pack(fill="x", padx=8)
        self.model_lbl = tk.Label(self, text="", bg=theme.PANEL, fg=theme.HEADER_FG,
                                  font=_font(9, True), anchor="w")
        self.model_lbl.pack(fill="x", padx=10, pady=(2, 0))
        self._show_model(cfg.get("model", ""))

        self._build_tables().pack(fill="x", padx=8, pady=4)

        self.plot = LoadStrokePlot(self)
        self.plot.add_series("PULL", PULL_COLOR)
        self.plot.add_series("PUSH", PUSH_COLOR)
        self.plot.pack(fill="both", expand=True, padx=8, pady=(0, 4))

        live = tk.Frame(self, bg=theme.PANEL)
        live.pack(fill="x", padx=8, pady=(2, 6))
        self.live = {}
        for key, label, unit in (("load", "LOAD", "kgf"), ("stroke", "STROKE", "mm"), ("speed", "SPEED", "mm/s")):
            tk.Label(live, text=label, bg=theme.PANEL, fg=theme.HEADER_FG, font=_font(9, True)).pack(side="left")
            self.live[key] = self._value(live, "--", width=6, size=11)
            self.live[key].pack(side="left", padx=(4, 2))
            tk.Label(live, text=unit, bg=theme.PANEL, fg="white", font=_font(8)).pack(side="left", padx=(0, 8))

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
        cell = {"padx": 2, "pady": 1}

        def text(col, row, t, bold=False, fg="white", sticky="w", **kw):
            tk.Label(f, text=t, bg=theme.PANEL, fg=fg, font=_font(9 if bold else 8, bold)).grid(
                row=row, column=col, sticky=sticky, **kw)

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
        for i in range(1, 4):
            for col, kind in ((7, "PULL"), (10, "PUSH")):
                text(col, i, f"{kind}{i}")
                self.res[f"{kind.lower()}{i}"] = self._value(f, "0.00")
                self.res[f"{kind.lower()}{i}"].grid(row=i, column=col + 1, **cell)
                text(col + 2, i, "kgf", padx=(0, 3))
        for col, kind in ((7, "pull"), (10, "push")):
            text(col, 4, "Mean", bold=True)
            self.res[f"{kind}_mean"] = self._value(f, "0.00")
            self.res[f"{kind}_mean"].grid(row=4, column=col + 1, **cell)
            text(col + 2, 4, "kgf", padx=(0, 3))

        # Same height for every data row, so the two tables share baselines
        f.rowconfigure(0, minsize=20)
        for r in range(1, 5):
            f.rowconfigure(r, minsize=26, uniform="rows")
        return f

    # ------------------------------------------------------------- data
    def read_conditions(self):
        """Validated test conditions from the entry boxes (also stored in cfg)."""
        try:
            vals = {k: float(e.get()) for k, e in self.ent.items()}
        except ValueError:
            raise ValueError("Test conditions must be numbers")
        if vals["load_min"] > vals["load_max"]:
            raise ValueError("LOAD minimum is above the maximum")
        if vals["speed"] <= 0 or vals["stroke"] <= 0 or vals["delay"] < 0:
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
        for i in range(3):
            self.res[f"pull{i + 1}"].config(text=f"{pulls[i]:.2f}" if i < len(pulls) else "0.00")
            self.res[f"push{i + 1}"].config(text=f"{pushes[i]:.2f}" if i < len(pushes) else "0.00")
        r = evaluate(pulls, pushes, cond["load_min"], cond["load_max"])
        self.res["pull_mean"].config(text=f"{r['pull_mean']:.2f}")
        self.res["push_mean"].config(text=f"{r['push_mean']:.2f}")
        self.res["max"].config(text=f"{r['max']:.2f}")
        self.judge.config(text="OK" if r["ok"] else "NG", fg=theme.PASS if r["ok"] else theme.FAIL)
        return r

    def reset_results(self):
        for lbl in self.res.values():
            lbl.config(text="0.00")
        self.judge.config(text="– – –", fg="#4f8cff")
        self.plot.clear()

    def set_live(self, load=None, stroke=None, speed=None):
        for key, val, fmt in (("load", load, "{:.2f}"), ("stroke", stroke, "{:.2f}"), ("speed", speed, "{:.1f}")):
            self.live[key].config(text="--" if val is None else fmt.format(val))

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

    def __init__(self, master, settings, plc, loadcells):
        super().__init__(master, bg=theme.BG)
        self.settings = settings
        self.plc = plc
        self.loadcells = loadcells
        self._polling = False
        self._alive = True
        self._hold_until = 0.0

        self.counters = settings["test"]["counters"]
        self._roll_day()

        self._build_status_bar()
        body = tk.Frame(self, bg=theme.BG)
        body.pack(fill="both", expand=True, pady=(6, 0))
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)

        left = tk.Frame(body, bg=theme.BG)
        left.grid(row=0, column=0, sticky="nsew")
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

        self.bind("<Destroy>", self._on_destroy)
        self.after(200, self._poll)
        self._load_model_list()

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
        card.pack(fill="x", pady=(0, 6))
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
        self.current_model.pack(fill="x")
        lb_frame = tk.Frame(body, bg=self.CARD)
        lb_frame.pack(fill="x", pady=(5, 5))
        self.model_list = tk.Listbox(lb_frame, height=3, bg=self.TILE, fg="#d6dbe4", font=_font(9),
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
                  activebackground="#1e88e5", activeforeground="white", bd=0, pady=4, font=_font(9, True),
                  cursor="hand2").pack(fill="x")

        # ---- Production: target / complete + progress bar ----------------
        body = self._side_card(side, "Production")
        row = tk.Frame(body, bg=self.CARD)
        row.pack(fill="x")
        row.columnconfigure((0, 1), weight=1, uniform="prod")
        tgt = tk.Frame(row, bg=self.TILE, highlightbackground="#232a36", highlightthickness=1)
        tgt.grid(row=0, column=0, sticky="nsew", padx=(0, 3))
        self.target = tk.Spinbox(tgt, from_=0, to=999999, width=6, justify="center", font=_font(16, True),
                                 bg=self.TILE, fg="white", buttonbackground="#232a36", relief="flat", bd=0,
                                 insertbackground="white", command=self._save_counters)
        self.target.delete(0, "end")
        self.target.insert(0, str(c["target"]))
        self.target.bind("<FocusOut>", lambda _e: self._save_counters())
        self.target.bind("<Return>", lambda _e: self._save_counters())
        self.target.pack(pady=(2, 0))
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
        row = tk.Frame(body, bg=self.CARD)
        row.pack(fill="x", pady=(6, 0))
        row.columnconfigure((0, 1), weight=1, uniform="saved")
        t, sel = self._tile(row, "SELECT SAVED", c["saved_select"], "white", size=13)
        t.grid(row=0, column=0, sticky="nsew", padx=(0, 3))
        t, shf = self._tile(row, "SHIFT SAVED", c["saved_shift"], "white", size=13)
        t.grid(row=0, column=1, sticky="nsew", padx=(3, 0))
        self.lbl_saved = [sel, shf]

        # ---- Test control ------------------------------------------------
        ctl = tk.Frame(side, bg=theme.BG)
        ctl.pack(fill="x", side="bottom")
        ctl.columnconfigure((0, 1), weight=1, uniform="ctl")
        for col, (text, cmd, bg, abg) in enumerate((
                ("▶  TEST RUN", lambda: self.plc_pulse("test_run", "Test run"), theme.PASS, "#43a047"),
                ("■  TEST STOP", lambda: self.plc_pulse("test_stop", "Test stop"), theme.FAIL, "#e53935"))):
            tk.Button(ctl, text=text, command=cmd, bg=bg, fg="white", activebackground=abg, activeforeground="white",
                      bd=0, pady=7, font=_font(10, True), cursor="hand2").grid(
                row=0, column=col, sticky="ew", padx=(0, 3) if col == 0 else (3, 0))
        tk.Button(ctl, text="↺  MOVE ORIGIN", command=lambda: self.plc_pulse("move_origin", "Move origin"),
                  bg="#2a313d", fg="white", activebackground="#353d4b", activeforeground="white", bd=0, pady=5,
                  font=_font(9, True), cursor="hand2").grid(row=1, column=0, columnspan=2, sticky="ew", pady=(5, 0))

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
        def worker():
            try:
                from .. import db
                db.init_database()
                rows, error = db.list_models(), None
            except Exception as exc:
                rows, error = [], exc
            if self._alive:
                self.after(0, lambda: self._show_model_list(rows, error))

        threading.Thread(target=worker, daemon=True).start()

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
        sel = self.model_list.curselection()
        if not sel or sel[0] >= len(self._models):
            self._set_status("Model Change: select a model in the list first", theme.BUSY)
            return
        row = self._models[sel[0]]

        def num(v):
            return None if v is None else float(v)

        for (key, _title, _s), panel, test in zip(self.SIDES, self.panels, ("SELECT", "SHIFT")):
            g = lambda attr: num(row.get(f"MM_{test}_{attr}"))
            cond = {"speed": g("SPEED"), "delay": g("DELAY_TIME"), "stroke": g("STROKE"),
                    "positions": [g("POSITION_1"), g("POSITION_2"), g("POSITION_3")],
                    "load_min": g("LOAD_MIN"), "load_max": g("LOAD_MAX")}
            panel.load_conditions(row["MM_MODEL"], cond)
            panel.reset_results()
        self.current_model.config(text=row["MM_MODEL"])
        save_settings(self.settings)
        self._set_status(f"Model changed to {row['MM_MODEL']}", theme.PASS)

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
        target = self.target.get().strip()
        self.counters["target"] = int(target) if target.isdigit() else 0
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

    def _set_status(self, text, color="white"):
        """Show an action's message; polling won't overwrite it for 3 s."""
        self.status.config(text=text, fg=color)
        self._hold_until = time.monotonic() + 3

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
            if not self.plc.is_open:
                snapshot["missing"].append("PLC")
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
            self.panels[i].set_live(load=side["load"])
            for name, (cv, dot) in self.lamps[i].items():
                cv.itemconfig(dot, fill=LAMP_ON if side["lamps"].get(name) else LAMP_OFF)
        if time.monotonic() < self._hold_until:
            return   # keep the last action's message on screen for a moment
        if snap["missing"]:
            self.status.config(text="Not connected: " + ", ".join(snap["missing"]), fg=theme.FAIL)
        else:
            self.status.config(text="Ready", fg=theme.PASS)
