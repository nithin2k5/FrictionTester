"""Manual Operation view of the Test Console, laid out like the machine's original
manual screen: per cable POSITION SET (left / right limit), JOG Control (speeds and
jog buttons), Actuator Status lamps, PULL / PUSH position, live Stroke / Load and
the fixture cylinders, with Return back to the measurement screen.

The view owns no PLC connection. It shows what the Test Console already reads
(the P-input lamps, the PLC status of the PC interface, the loadcells) and sends
only what the PLC program accepts from the PC:

* Left / right limit and jog speeds are PLC parameters (PRINTING PROGRAM OLD,
  D04204/D04205 SHIFT, D04210/D04211 SELECT, jog D04200/01, D04206/07). An edited
  value (Enter, or leaving the field) is sent with the model's other parameters
  through the controller's normal parameter write - the same gate as RUN
  (mapping verified, live writes enabled, command owner confirmed).
* Jog, PULL / PUSH position and the cylinders have no PC command the app drives
  in that program (jog runs from the touch panel; PULL / PUSH only inside a
  cable routine; the fixture bits M0700x also feed the print trigger P00038).
  Their buttons say so instead of writing anything.
"""

import tkinter as tk

from . import theme
from ..plc import pc_profile as pp

PANEL = "#e9ecef"          # light panels, as on the original screen
PANEL_FG = "#1f2a44"
TITLE_BG = "#f2e6b3"
FIELD_BG = "#fbf3d0"
FIELD_FG = "#d63384"
LAMP_GREEN_ON = "#3cff6a"
LAMP_RED_ON = "#ff3b30"
LAMP_IDLE = "#1d3b24"
NO_DATA = "—"

# (lamp text, source, colour when ON). Source "lamp:<name>" = Test Console cable lamp,
# "not:<name>" = inverted cable lamp, "st:<name>" = PC-interface status bit.
STATUS_LAMPS = [
    ("SERVO ON", "lamp:Servo", LAMP_GREEN_ON),
    ("ALARM", "not:Drive OK", LAMP_RED_ON),
    ("Origin O.K", "lamp:Origin", LAMP_GREEN_ON),
    ("Right(PUSH) Limit", "st:limit_right", LAMP_GREEN_ON),
    ("Left(PULL) Limit", "st:limit_left", LAMP_GREEN_ON),
]
JOG_BUTTONS = [("⏪", "fast reverse"), ("◀", "reverse"), ("▶", "forward"), ("⏩", "fast forward")]
CYLINDERS_LEFT = [("Clamp Cylinder", "clamp")]
CYLINDERS_RIGHT = [("Removing Cylinder 1", "discharge1"), ("Removing Cylinder 2", "discharge2"),
                   ("Loading Cylinder", "expand")]
PARAM_FIELDS = [("limit_left", "Left Limit"), ("limit_right", "Right Limit")]


def _font(size, bold=False):
    return ("Arial", size, "bold") if bold else ("Arial", size)


def lamp_state(source, lamps, snap, axis):
    """True / False / None (unknown) for one Actuator Status lamp."""
    kind, _, name = source.partition(":")
    if kind == "lamp":
        return lamps.get(name)
    if kind == "not":
        v = lamps.get(name)
        return None if v is None else not v
    if kind == "st" and snap is not None:
        try:
            return snap.st(axis, name)
        except Exception:
            return None
    return None


class ManualView(tk.Frame):
    def __init__(self, master, page, sides):
        """page: the TestConsolePage (settings, ctl, _set_status, show_measurement)."""
        super().__init__(master, bg=theme.BG)
        self.page = page
        self.sides = sides
        self.fields = {}         # (axis, key) -> Entry / Spinbox
        self.status_lamps = {}   # axis -> {text: (canvas, dot, colour)}
        self.live = {}           # axis -> {"stroke": Label, "load": Label}

        foot = tk.Frame(self, bg=theme.BG)          # packed first: never squeezed out
        foot.pack(side="bottom", fill="x", pady=(8, 2))
        row = tk.Frame(self, bg=theme.BG)
        row.pack(fill="both", expand=True)
        for i, (axis, title, _short) in enumerate(sides):
            self._cable(row, axis, title).grid(row=0, column=i, sticky="nsew", padx=(0, 6) if i == 0 else (6, 0))
            row.columnconfigure(i, weight=1, uniform="manual")
        row.rowconfigure(0, weight=1)

        tk.Button(foot, text="↩  Return", command=page.show_measurement, bg="#e0e0e0", fg="black",
                  activebackground="#f0f0f0", bd=0, padx=22, pady=5, font=_font(10, True),
                  cursor="hand2").pack()
        self.refresh_params()

    # ----------------------------------------------------------- layout
    def _button(self, parent, text, command, width=16, disabled_look=False):
        bg = "#cfd3d8" if disabled_look else "#f4f5f7"
        fg = "#7a808a" if disabled_look else "#1f2a44"
        return tk.Button(parent, text=text, command=command, bg=bg, fg=fg, activebackground="#ffffff",
                         activeforeground=fg, relief="raised", bd=1, width=width, pady=3,
                         font=_font(9, True), cursor="hand2")

    def _field(self, parent, axis, key, spin=False):
        common = dict(width=7, justify="center", font=_font(11), bg=FIELD_BG, fg=FIELD_FG,
                      relief="solid", bd=1, insertbackground=FIELD_FG)
        if spin:
            w = tk.Spinbox(parent, from_=0, to=pp.param_max(key), increment=1, **common,
                           command=lambda: self._param_edited(axis, key))
        else:
            w = tk.Entry(parent, **common)
        w.bind("<Return>", lambda _e: self._param_edited(axis, key))
        w.bind("<FocusOut>", lambda _e: self._param_edited(axis, key))
        self.fields[(axis, key)] = w
        return w

    def _cable(self, parent, axis, title):
        box = tk.Frame(parent, bg=PANEL, highlightbackground="#9aa1ab", highlightthickness=1)
        tk.Label(box, text=f" {title} ", bg=TITLE_BG, fg="black", font=_font(12, True), bd=1,
                 relief="solid").place(x=14, y=-2)
        inner = tk.Frame(box, bg=PANEL)
        inner.pack(fill="both", expand=True, padx=14, pady=(26, 10))
        inner.columnconfigure(0, weight=1)
        inner.columnconfigure(1, weight=1)

        # ---- left column: POSITION SET, JOG Control
        left = tk.Frame(inner, bg=PANEL)
        left.grid(row=0, column=0, sticky="nw")
        tk.Label(left, text="POSITION SET", bg=PANEL, fg=PANEL_FG, font=_font(10)).grid(
            row=0, column=0, columnspan=3, pady=(0, 6))
        for r, (key, label) in enumerate(PARAM_FIELDS, start=1):
            tk.Label(left, text=label, bg=PANEL, fg="#2f6fd6", font=_font(10)).grid(row=r, column=0, sticky="e",
                                                                                  padx=(0, 6), pady=2)
            self._field(left, axis, key).grid(row=r, column=1, pady=2)
            tk.Label(left, text="mm", bg=PANEL, fg=PANEL_FG, font=_font(9)).grid(row=r, column=2, sticky="w", padx=4)

        tk.Label(left, text="JOG Control", bg=PANEL, fg=PANEL_FG, font=_font(10)).grid(
            row=3, column=0, columnspan=3, pady=(16, 2))
        tk.Label(left, text="Low", bg=PANEL, fg=PANEL_FG, font=_font(9)).grid(row=4, column=1)
        tk.Label(left, text="High", bg=PANEL, fg=PANEL_FG, font=_font(9)).grid(row=4, column=2)
        tk.Label(left, text="Jog Speed", bg=PANEL, fg=PANEL_FG, font=_font(10)).grid(row=5, column=0, sticky="e",
                                                                                  padx=(0, 6))
        self._field(left, axis, "jog_low", spin=True).grid(row=5, column=1, padx=2)
        self._field(left, axis, "jog_high", spin=True).grid(row=5, column=2, padx=2)

        jog = tk.Frame(left, bg=PANEL)
        jog.grid(row=6, column=0, columnspan=3, pady=(14, 6))
        for text, what in JOG_BUTTONS:
            tk.Button(jog, text=text, command=lambda w=what: self._not_driven(
                axis, f"Jog {w}", "jog is not a PC command in PRINTING PROGRAM OLD - use the machine's touch panel"),
                bg="#dfe6f3", fg="#1d4fbf", activebackground="#ffffff", relief="raised", bd=1, width=4,
                font=_font(14, True), cursor="hand2").pack(side="left", padx=3)

        pp_row = tk.Frame(left, bg=PANEL)
        pp_row.grid(row=7, column=0, columnspan=3, pady=(18, 4))
        for text, what in (("⇦ Pull Position", "PULL"), ("Push Position ⇨", "PUSH")):
            self._button(pp_row, text, lambda w=what: self._not_driven(
                axis, f"{w} position", f"{w} runs only inside a cable test (RUN) in this PLC program"),
                width=14, disabled_look=True).pack(side="left", padx=3)

        for text, what in CYLINDERS_LEFT:
            self._button(left, f"☰  {text}", lambda w=what, t=text: self._cylinder(axis, t)).grid(
                row=8, column=0, columnspan=3, pady=(30, 0))

        # ---- right column: Actuator Status, Stroke / Load, cylinders
        right = tk.Frame(inner, bg=PANEL)
        right.grid(row=0, column=1, sticky="nw", padx=(16, 0))
        tk.Label(right, text="Actuator Status", bg=PANEL, fg=PANEL_FG, font=_font(10)).grid(
            row=0, column=0, columnspan=3, pady=(0, 6), sticky="w")
        lamps = {}
        for r, (text, _src, colour) in enumerate(STATUS_LAMPS, start=1):
            cv = tk.Canvas(right, width=20, height=20, bg=PANEL, highlightthickness=0)
            dot = cv.create_oval(2, 2, 18, 18, fill=LAMP_IDLE, outline="#333")
            cv.grid(row=r, column=0, pady=3, sticky="w")
            tk.Label(right, text=text, bg=PANEL, fg="#2f6fd6", font=_font(10)).grid(row=r, column=1, columnspan=2,
                                                                                  sticky="w", padx=4)
            lamps[text] = (cv, dot, colour)
        self.status_lamps[axis] = lamps

        live = {}
        for r, (key, label, unit) in enumerate((("stroke", "Stroke", "mm"), ("load", "Load", "Kgf")), start=7):
            tk.Label(right, text=label, bg=PANEL, fg="#2f6fd6", font=_font(10)).grid(row=r, column=0, sticky="e",
                                                                                  pady=(14 if r == 7 else 2, 2))
            v = tk.Label(right, text=NO_DATA, width=8, bg=FIELD_BG, fg=FIELD_FG, font=_font(11), relief="solid", bd=1)
            v.grid(row=r, column=1, padx=4, pady=(14 if r == 7 else 2, 2))
            tk.Label(right, text=unit, bg=PANEL, fg=PANEL_FG, font=_font(9)).grid(row=r, column=2, sticky="w")
            live[key] = v
        self.live[axis] = live

        cyl = tk.Frame(right, bg=PANEL)
        cyl.grid(row=9, column=0, columnspan=3, pady=(18, 0), sticky="w")
        for text, _what in CYLINDERS_RIGHT:
            self._button(cyl, f"⏏  {text}", lambda t=text: self._cylinder(axis, t), width=21).pack(pady=2)
        return box

    # ---------------------------------------------------------- actions
    def _not_driven(self, axis, what, why):
        self.page._set_status(f"{pp.AXIS_NAME[axis]} {what}: {why}", theme.BUSY, 6)

    def _cylinder(self, axis, text):
        self._not_driven(axis, text, "fixture outputs are not driven by the app (their PC bits M0700x also "
                                     "feed the print trigger P00038) - use the machine's touch panel")

    def _values(self):
        values = self.page.settings["test"].get("plc_params")
        if not values or len(values) != len(pp.PARAMS):
            return None
        return list(values)

    def refresh_params(self):
        """Fill the fields from the loaded model's PLC parameters (test.plc_params)."""
        values = self._values()
        for (axis, key), w in self.fields.items():
            v = "" if values is None else values[pp.param_index(axis, key)]
            text = "" if v is None else str(v)
            if isinstance(w, tk.Spinbox):
                w.delete(0, "end")
                w.insert(0, text)
            else:
                w.delete(0, "end")
                w.insert(0, text)

    def _param_edited(self, axis, key):
        w = self.fields[(axis, key)]
        text = w.get().strip()
        values = self._values()
        label = f"{pp.AXIS_NAME[axis]} {pp.PARAM_LABELS[key]}"
        if values is None:
            if text:
                self.page._set_status(f"{label}: load a model first (its PLC parameters are not loaded)",
                                      theme.FAIL, 5)
            return
        i = pp.param_index(axis, key)
        old = values[i]
        if text == ("" if old is None else str(old)):
            return
        try:
            v = int(text)
        except ValueError:
            self.page._set_status(f"{label}: '{text}' is not a whole number", theme.FAIL, 5)
            self.refresh_params()
            return
        if not 0 <= v <= pp.param_max(key):
            self.page._set_status(f"{label}: {v} is outside 0..{pp.param_max(key)}", theme.FAIL, 5)
            self.refresh_params()
            return
        values[i] = v
        self.page.settings["test"]["plc_params"] = values
        model = self.page.settings["test"]["select"].get("model")
        ctl = getattr(self.page, "ctl", None)
        if getattr(self.page, "pc", False) and ctl is not None and model:
            ctl.submit("params", model, values)
            self.page._set_status(f"{label} = {v}: sent with the model's PLC parameters (not saved to the model)",
                                  theme.UPDATE, 5)
        else:
            self.page._set_status(f"{label} = {v}: kept for this session - no PLC parameter write in this mode",
                                  theme.BUSY, 5)

    # ---------------------------------------------------------- refresh
    def update_view(self, sides, pc_snap=None, strokes=None):
        """UI thread. sides: the Test Console poll result per cable ({"lamps", "load"})."""
        for (axis, _t, _s), side in zip(self.sides, sides):
            lamps = side.get("lamps") or {}
            for text, src, colour in STATUS_LAMPS:
                cv, dot, on_colour = self.status_lamps[axis][text]
                on = lamp_state(src, lamps, pc_snap, axis)
                cv.itemconfig(dot, fill=on_colour if on else LAMP_IDLE)
            load = side.get("load")
            self.live[axis]["load"].config(text=NO_DATA if load is None else f"{load:.2f}")
            stroke = (strokes or {}).get(axis)
            self.live[axis]["stroke"].config(text=NO_DATA if stroke is None else f"{stroke:.1f}")
