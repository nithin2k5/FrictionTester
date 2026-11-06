"""Test Console — the measurement screen, laid out like the LabVIEW
"Friction Test Machine" it replaces.

Two cable panels side by side:
    SELECT CABLE -> Servo 1 + Loadcell 01
    SHIFT CABLE  -> Servo 2 + Loadcell 02
each with its test conditions, test results (PULL1-3 / PUSH1-3, means,
OK/NG), a Load-vs-Stroke graph and live LOAD / STROKE / SPEED readouts.
Underneath, per cable: actuator control (servo on/off, alarm reset), the
front/rear sensor lamps read from the PLC, and the clamp counter.

The page polls connected devices for the live values. It does not run the
automatic test sequence; `CablePanel.show_results` and `plot` are the hooks
that sequence will feed.
"""

import datetime
import os
import threading
import time
import tkinter as tk
from tkinter import messagebox

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
        self.judge = tk.Label(top, text="– – –", bg=FIELD_BG, fg="#4f8cff", font=_font(16, True),
                              relief="solid", bd=1, width=5)
        self.judge.pack(side="left")
        tk.Label(top, text="Max", bg=theme.PANEL, fg="white", font=_font(10, True)).pack(side="left", padx=(10, 4))
        self.res = {"max": self._value(top, "0.00")}
        self.res["max"].pack(side="left")
        tk.Label(top, text="kgf", bg=theme.PANEL, fg="white", font=_font(9)).pack(side="left", padx=(3, 0))
        tk.Button(top, text="Screen Save", command=self.save_screen, bg="#333", fg="white",
                  activebackground="#444", activeforeground="white", bd=0, padx=10, pady=3,
                  font=_font(9, True), cursor="hand2").pack(side="right")
        tk.Label(top, text=title, bg=theme.PANEL, fg="white", font=_font(16)).pack(side="left", expand=True)
        tk.Frame(self, bg=theme.BORDER, height=1).pack(fill="x", padx=10)

        body = tk.Frame(self, bg=theme.PANEL)
        body.pack(fill="x", padx=10, pady=6)
        self._build_conditions(body).pack(side="left", fill="y")
        tk.Frame(body, bg=theme.BORDER, width=1).pack(side="left", fill="y", padx=8)
        self._build_results(body).pack(side="left", fill="y")

        self.plot = LoadStrokePlot(self)
        self.plot.add_series("PULL", PULL_COLOR)
        self.plot.add_series("PUSH", PUSH_COLOR)
        self.plot.pack(fill="both", expand=True, padx=10, pady=(0, 4))

        live = tk.Frame(self, bg=theme.PANEL)
        live.pack(fill="x", padx=10, pady=(2, 8))
        self.live = {}
        for key, label, unit in (("load", "LOAD", "kgf"), ("stroke", "STROKE", "mm"), ("speed", "SPEED", "mm/s")):
            tk.Label(live, text=label, bg=theme.PANEL, fg=theme.HEADER_FG, font=_font(10, True)).pack(side="left")
            self.live[key] = self._value(live, "--", width=6, size=14)
            self.live[key].pack(side="left", padx=(5, 3))
            tk.Label(live, text=unit, bg=theme.PANEL, fg="white", font=_font(9)).pack(side="left", padx=(0, 12))

    # ----------------------------------------------------------- widgets
    def _value(self, parent, text, width=5, size=11):
        return tk.Label(parent, text=text, bg=FIELD_BG, fg=VALUE_FG, font=_font(size), width=width,
                        relief="solid", bd=1, highlightthickness=0)

    def _entry(self, parent, value, width=5, fmt="{:g}"):
        e = tk.Entry(parent, bg=theme.ENTRY_BG, fg="white", insertbackground="white", justify="center",
                     font=_font(11), width=width, bd=1, relief="solid",
                     highlightbackground=theme.BORDER, highlightthickness=1)
        e.insert(0, fmt.format(value))
        return e

    def _caption(self, parent, text):
        return tk.Label(parent, text=text, bg=theme.PANEL, fg=theme.TITLE_FG, font=_font(10, True))

    def _small(self, parent, text, fg="white"):
        return tk.Label(parent, text=text, bg=theme.PANEL, fg=fg, font=_font(9))

    def _build_conditions(self, parent):
        f = tk.Frame(parent, bg=theme.PANEL)
        self._caption(f, "TEST CONDITION").grid(row=0, column=0, columnspan=6, sticky="w", pady=(0, 3))
        self._small(f, "Position", fg=theme.HEADER_FG).grid(row=0, column=4, columnspan=2, sticky="s")
        cfg = self.cfg
        self.ent = {}
        for r, (key, label, unit) in enumerate((("speed", "Speed", "mm/s"), ("delay", "Delay", "s"),
                                                ("stroke", "Stroke", "mm")), start=1):
            self._small(f, label).grid(row=r, column=0, sticky="w")
            self.ent[key] = self._entry(f, cfg[key])
            self.ent[key].grid(row=r, column=1, padx=3, pady=1)
            self._small(f, unit).grid(row=r, column=2, sticky="w", padx=(0, 8))
            self._small(f, str(r)).grid(row=r, column=3, sticky="e")
            self.ent[f"pos{r}"] = self._entry(f, cfg["positions"][r - 1])
            self.ent[f"pos{r}"].grid(row=r, column=4, padx=3, pady=1)
            self._small(f, "mm").grid(row=r, column=5, sticky="w")

        load = tk.Frame(f, bg=theme.PANEL)
        load.grid(row=4, column=0, columnspan=6, sticky="w", pady=(5, 0))
        tk.Label(load, text="LOAD", bg=theme.PANEL, fg="white", font=_font(10, True)).pack(side="left")
        self.ent["load_min"] = self._entry(load, cfg["load_min"], fmt="{:.2f}")
        self.ent["load_min"].pack(side="left", padx=4)
        self._small(load, "~").pack(side="left")
        self.ent["load_max"] = self._entry(load, cfg["load_max"], fmt="{:.2f}")
        self.ent["load_max"].pack(side="left", padx=4)
        self._small(load, "kgf").pack(side="left")
        return f

    def _build_results(self, parent):
        f = tk.Frame(parent, bg=theme.PANEL)
        self._caption(f, "TEST RESULTS").grid(row=0, column=0, columnspan=6, sticky="w", pady=(0, 3))
        for i in range(1, 4):
            for col, kind in ((0, "PULL"), (3, "PUSH")):
                self._small(f, f"{kind}{i}").grid(row=i, column=col, sticky="w")
                self.res[f"{kind.lower()}{i}"] = self._value(f, "0.00")
                self.res[f"{kind.lower()}{i}"].grid(row=i, column=col + 1, padx=3, pady=1)
                self._small(f, "kgf").grid(row=i, column=col + 2, sticky="w", padx=(0, 6))
        for col, kind in ((0, "pull"), (3, "push")):
            tk.Label(f, text="Mean", bg=theme.PANEL, fg="white", font=_font(10, True)).grid(
                row=4, column=col, sticky="w", pady=(5, 0))
            self.res[f"{kind}_mean"] = self._value(f, "0.00")
            self.res[f"{kind}_mean"].grid(row=4, column=col + 1, padx=3, pady=(5, 0))
            self._small(f, "kgf").grid(row=4, column=col + 2, sticky="w", pady=(5, 0))
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

    def __init__(self, master, settings, plc, loadcells, servos):
        super().__init__(master, bg=theme.BG)
        self.settings = settings
        self.plc = plc
        self.loadcells = loadcells
        self.servos = servos
        self._polling = False
        self._alive = True
        self._hold_until = 0.0
        self._servo_on = [False, False]

        self._build_status_bar()
        panels = tk.Frame(self, bg=theme.BG)
        panels.pack(fill="both", expand=True, pady=(6, 6))
        self.panels = []
        for i, (key, title, _) in enumerate(self.SIDES):
            p = CablePanel(panels, title, settings["test"][key])
            p.grid(row=0, column=i, sticky="nsew", padx=(0, 6) if i == 0 else (6, 0))
            panels.columnconfigure(i, weight=1, uniform="panel")
            self.panels.append(p)
        panels.rowconfigure(0, weight=1)
        self._build_bottom()

        self.bind("<Destroy>", self._on_destroy)
        self.after(200, self._poll)

    # ------------------------------------------------------------ layout
    def _build_status_bar(self):
        bar = tk.Frame(self, bg=theme.BG)
        bar.pack(fill="x")
        tag = tk.Frame(bar, bg=theme.PANEL, highlightbackground=theme.TITLE_FG, highlightthickness=0)
        tag.pack(side="left")
        tk.Label(tag, text="📈  Measurement Screen", bg=theme.PANEL, fg="white", font=_font(14),
                 padx=16, pady=6).pack()
        tk.Frame(tag, bg=theme.TITLE_FG, height=3).pack(fill="x")

        tk.Button(bar, text="Save Conditions", command=self.save_conditions, bg=theme.SAVE, fg="white",
                  activebackground=theme.SAVE, activeforeground="white", bd=0, padx=14, pady=5,
                  font=_font(10, True), cursor="hand2").pack(side="right")
        tk.Button(bar, text="Clear Results", command=self.clear_results, bg="#333", fg="white",
                  activebackground="#444", activeforeground="white", bd=0, padx=14, pady=5,
                  font=_font(10, True), cursor="hand2").pack(side="right", padx=8)

        self.status = tk.Label(bar, text="System initialization", bg=FIELD_BG, fg="white", font=_font(11),
                               relief="solid", bd=1, padx=12, pady=5, anchor="w")
        self.status.pack(side="left", fill="x", expand=True, padx=16)

    def _build_bottom(self):
        bottom = tk.Frame(self, bg=theme.BG)
        bottom.pack(fill="x")
        self.lamps, self.servo_btns, self.clamps = [], [], []
        for i, (key, _title, short) in enumerate(self.SIDES):
            cfg = self.settings["test"][key]
            box = tk.Frame(bottom, bg=theme.PANEL, highlightbackground=theme.BORDER, highlightthickness=1)
            box.grid(row=0, column=i, sticky="nsew", padx=(0, 6) if i == 0 else (6, 0))
            bottom.columnconfigure(i, weight=1, uniform="panel")

            act = tk.LabelFrame(box, text=" Actuator Control ", bg=theme.PANEL, fg="white", font=_font(10),
                                bd=1, relief="solid", labelanchor="n", padx=8, pady=4)
            act.pack(side="left", padx=10, pady=6)
            sbtn = tk.Button(act, text="⚙ SERVO ON/OFF", command=lambda n=i: self.toggle_servo(n),
                             bg="#e0e0e0", fg="black", bd=0, padx=10, pady=3, font=_font(10, True),
                             cursor="hand2", disabledforeground="#888")
            sbtn.pack(side="left", padx=(0, 6))
            tk.Button(act, text="ALM RST", command=lambda n=i: self.alarm_reset(n), bg="#555", fg="white",
                      activebackground="#666", activeforeground="white", bd=0, padx=8, pady=3,
                      font=_font(10, True), cursor="hand2").pack(side="left")
            self.servo_btns.append(sbtn)

            sens = tk.Frame(box, bg=theme.PANEL)
            sens.pack(side="left", padx=14)
            lamps = {}
            for name in ("Front Sensor", "Rear Sensor"):
                row = tk.Frame(sens, bg=theme.PANEL)
                row.pack(anchor="w", pady=1)
                cv = tk.Canvas(row, width=18, height=18, bg=theme.PANEL, highlightthickness=0)
                dot = cv.create_oval(2, 2, 16, 16, fill=LAMP_OFF, outline="#111")
                cv.pack(side="left")
                tk.Label(row, text=name, bg=theme.PANEL, fg="#4fa3ff", font=_font(11)).pack(side="left", padx=4)
                lamps[name] = (cv, dot)
            self.lamps.append(lamps)

            clamp = tk.Frame(box, bg=theme.PANEL)
            clamp.pack(side="left", padx=10)
            tk.Label(clamp, text=f"{short}-Clamp", bg=theme.PANEL, fg="white", font=_font(10)).pack(side="left")
            sp = tk.Spinbox(clamp, from_=0, to=999999, width=7, justify="center", font=_font(11),
                            bg=FIELD_BG, fg=VALUE_FG, buttonbackground="#333", relief="solid", bd=1,
                            insertbackground="white")
            sp.delete(0, "end")
            sp.insert(0, str(cfg.get("clamp_count", 0)))
            sp.pack(side="left", padx=4)
            tk.Label(clamp, text="times", bg=theme.PANEL, fg="white", font=_font(10)).pack(side="left")
            self.clamps.append(sp)

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

    def _servo_action(self, n, fn, label):
        sv = self.servos[n]
        if not sv.is_open:
            self._set_status(f"{sv.name} is not connected", theme.FAIL)
            return

        def run():
            try:
                fn(sv)
                self.after(0, lambda: self._set_status(f"{sv.name}: {label}", theme.PASS))
            except Exception as exc:
                self.after(0, lambda e=exc: self._set_status(f"{sv.name}: {e}", theme.FAIL))

        threading.Thread(target=run, daemon=True).start()

    def toggle_servo(self, n):
        turn_on = not self._servo_on[n]
        self._servo_action(n, lambda sv: sv.servo(turn_on), "servo ON" if turn_on else "servo OFF")

    def alarm_reset(self, n):
        self._servo_action(n, lambda sv: sv.alarm_reset(), "alarm reset sent")

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
                side = {"load": None, "stroke": None, "speed": None, "sv": None, "lamps": {}}
                lc, sv = self.loadcells[i], self.servos[i]
                if lc.is_open:
                    try:
                        side["load"] = lc.read()[2]
                    except Exception:
                        pass
                else:
                    snapshot["missing"].append(lc.name.split(" (")[0])
                if sv.is_open:
                    try:
                        st = sv.read_status()
                        side.update(stroke=st["position_mm"], speed=st["speed_mm_s"], sv=st["flags"]["SV"])
                    except Exception:
                        pass
                else:
                    snapshot["missing"].append(sv.name)
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
            self.panels[i].set_live(side["load"], side["stroke"], side["speed"])
            for name, (cv, dot) in self.lamps[i].items():
                cv.itemconfig(dot, fill=LAMP_ON if side["lamps"].get(name) else LAMP_OFF)
            connected = self.servos[i].is_open
            if side["sv"] is not None:
                self._servo_on[i] = side["sv"]
            label = "⚙ SERVO OFF" if self._servo_on[i] and connected else "⚙ SERVO ON"
            bg = theme.PASS if self._servo_on[i] and connected else "#e0e0e0"
            self.servo_btns[i].config(text=label, bg=bg, activebackground=bg,
                                      fg="white" if bg == theme.PASS else "black",
                                      state="normal" if connected else "disabled")
        if time.monotonic() < self._hold_until:
            return   # keep the last action's message on screen for a moment
        if snap["missing"]:
            self.status.config(text="Not connected: " + ", ".join(snap["missing"]), fg=theme.FAIL)
        else:
            self.status.config(text="Ready", fg=theme.PASS)
