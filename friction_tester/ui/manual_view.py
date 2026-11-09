"""Manual Operation view of the Test Console: per cable POSITION SET (left / right
limit), JOG CONTROL (speeds and jog buttons), POSITION MOVE (PULL / PUSH),
ACTUATOR STATUS lamps, LIVE stroke / load and the fixture CYLINDERS, with Return
back to the measurement screen. Same look as the rest of the app: dark panels,
side-panel style cards with an orange accent, magenta values.

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

# Same look as the Test Console: dark panels, side-panel cards, magenta values.
CARD = "#141922"
CARD_HEAD = "#1b212c"
FIELD_BG = "#0d1016"
VALUE_FG = "#ff6fd8"
LABEL_FG = "#c9d1dc"
LINK_FG = "#4fa3ff"
LAMP_OFF = "#24402a"
LAMP_GREEN_ON = "#3cff6a"
LAMP_RED_ON = theme.FAIL
BTN_BG, BTN_ACTIVE = "#2a313d", "#353d4b"
BTN_OFF_BG = "#1c2029"
NO_DATA = "—"

# (lamp text, source, colour when ON). Source "lamp:<name>" = Test Console cable lamp,
# "not:<name>" = inverted cable lamp, "st:<name>" = PC-interface status bit.
STATUS_LAMPS = [
    ("SERVO ON", "lamp:Servo", LAMP_GREEN_ON),
    ("ALARM", "not:Drive OK", LAMP_RED_ON),
    ("Origin O.K", "lamp:Origin", LAMP_GREEN_ON),
    ("Right (PUSH) Limit", "st:limit_right", LAMP_GREEN_ON),
    ("Left (PULL) Limit", "st:limit_left", LAMP_GREEN_ON),
]
JOG_BUTTONS = [("⏪", "fast reverse"), ("◀", "reverse"), ("▶", "forward"), ("⏩", "fast forward")]
CYLINDERS = ["Clamp Cylinder", "Removing Cylinder 1", "Removing Cylinder 2", "Loading Cylinder"]
PARAM_FIELDS = [("limit_left", "Left Limit"), ("limit_right", "Right Limit")]


def _font(size, bold=False):
    return (theme.FONT, size, "bold") if bold else (theme.FONT, size)


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
        foot.pack(side="bottom", fill="x", pady=(6, 0))
        tk.Button(foot, text="↩  Return", command=page.show_measurement, bg="#333", fg="white",
                  activebackground="#444", activeforeground="white", bd=0, padx=22, pady=5,
                  font=_font(10, True), cursor="hand2").pack()
        row = tk.Frame(self, bg=theme.BG)
        row.pack(fill="both", expand=True)
        for i, (axis, title, _short) in enumerate(sides):
            self._cable(row, axis, title).grid(row=0, column=i, sticky="nsew", padx=(0, 4) if i == 0 else (4, 0))
            row.columnconfigure(i, weight=1, uniform="manual")
        row.rowconfigure(0, weight=1)
        self.refresh_params()

    # ----------------------------------------------------------- layout
    def _card(self, parent, title):
        """Side-panel style card: title strip with an orange accent, dark body."""
        card = tk.Frame(parent, bg=CARD, highlightbackground=theme.BORDER, highlightthickness=1)
        card.pack(fill="x", pady=(0, 6))
        head = tk.Frame(card, bg=CARD_HEAD)
        head.pack(fill="x")
        tk.Frame(head, bg=theme.TITLE_FG, width=3).pack(side="left", fill="y")
        tk.Label(head, text=title.upper(), bg=CARD_HEAD, fg=LABEL_FG, font=_font(8, True)).pack(
            side="left", padx=8, pady=4)
        body = tk.Frame(card, bg=CARD)
        body.pack(fill="x", padx=10, pady=8)
        return body

    def _button(self, parent, text, command, width=18, off=False):
        bg, fg = (BTN_OFF_BG, theme.MUTED) if off else (BTN_BG, "white")
        return tk.Button(parent, text=text, command=command, bg=bg, fg=fg, activebackground=BTN_ACTIVE,
                         activeforeground="white", bd=0, width=width, pady=4, font=_font(9, True), cursor="hand2")

    def _label(self, parent, text, fg=LABEL_FG, size=10, bold=False):
        return tk.Label(parent, text=text, bg=CARD, fg=fg, font=_font(size, bold))

    def _field(self, parent, axis, key, spin=False):
        common = dict(width=7, justify="center", font=_font(11), bg=FIELD_BG, fg=VALUE_FG,
                      relief="solid", bd=1, insertbackground="white")
        if spin:
            w = tk.Spinbox(parent, from_=0, to=pp.param_max(key), increment=1, buttonbackground="#333",
                           command=lambda: self._param_edited(axis, key), **common)
        else:
            w = tk.Entry(parent, highlightthickness=0, **common)
        w.bind("<Return>", lambda _e: self._param_edited(axis, key))
        w.bind("<FocusOut>", lambda _e: self._param_edited(axis, key))
        self.fields[(axis, key)] = w
        return w

    def _cable(self, parent, axis, title):
        box = tk.Frame(parent, bg=theme.PANEL, highlightbackground=theme.BORDER, highlightthickness=1)
        tk.Label(box, text=title, bg=theme.PANEL, fg="white", font=_font(14)).pack(pady=(6, 2))
        tk.Frame(box, bg=theme.BORDER, height=1).pack(fill="x", padx=8)
        cols = tk.Frame(box, bg=theme.PANEL)
        cols.pack(fill="both", expand=True, padx=8, pady=8)
        cols.columnconfigure(0, weight=1, uniform="cols")
        cols.columnconfigure(1, weight=1, uniform="cols")
        left = tk.Frame(cols, bg=theme.PANEL)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        right = tk.Frame(cols, bg=theme.PANEL)
        right.grid(row=0, column=1, sticky="nsew", padx=(4, 0))

        # ---- POSITION SET
        body = self._card(left, "Position set")
        for r, (key, label) in enumerate(PARAM_FIELDS):
            self._label(body, label).grid(row=r, column=0, sticky="e", padx=(0, 8), pady=2)
            self._field(body, axis, key).grid(row=r, column=1, pady=2)
            self._label(body, "mm", fg=theme.HEADER_FG, size=9).grid(row=r, column=2, sticky="w", padx=4)

        # ---- JOG CONTROL
        body = self._card(left, "Jog control")
        self._label(body, "Low", fg=theme.HEADER_FG, size=9).grid(row=0, column=1)
        self._label(body, "High", fg=theme.HEADER_FG, size=9).grid(row=0, column=2)
        self._label(body, "Jog Speed").grid(row=1, column=0, sticky="e", padx=(0, 8))
        self._field(body, axis, "jog_low", spin=True).grid(row=1, column=1, padx=2)
        self._field(body, axis, "jog_high", spin=True).grid(row=1, column=2, padx=2)
        jog = tk.Frame(body, bg=CARD)
        jog.grid(row=2, column=0, columnspan=3, pady=(10, 0))
        for text, what in JOG_BUTTONS:
            tk.Button(jog, text=text, command=lambda w=what: self._not_driven(
                axis, f"Jog {w}", "jog is not a PC command in PRINTING PROGRAM OLD - use the machine's touch panel"),
                bg=BTN_BG, fg=LINK_FG, activebackground=BTN_ACTIVE, activeforeground="white", bd=0, width=4,
                pady=2, font=_font(14, True), cursor="hand2").pack(side="left", padx=3)

        # ---- POSITION MOVE (PULL / PUSH run only inside a test in this program)
        body = self._card(left, "Position move")
        for c, (text, what) in enumerate((("⇦  Pull Position", "PULL"), ("Push Position  ⇨", "PUSH"))):
            self._button(body, text, lambda w=what: self._not_driven(
                axis, f"{w} position", f"{w} runs only inside a cable test (RUN) in this PLC program"),
                width=15, off=True).grid(row=0, column=c, padx=3)

        # ---- ACTUATOR STATUS
        body = self._card(right, "Actuator status")
        lamps = {}
        for r, (text, _src, colour) in enumerate(STATUS_LAMPS):
            cv = tk.Canvas(body, width=18, height=18, bg=CARD, highlightthickness=0)
            dot = cv.create_oval(2, 2, 16, 16, fill=LAMP_OFF, outline="#111")
            cv.grid(row=r, column=0, pady=2, sticky="w")
            self._label(body, text, fg=LINK_FG).grid(row=r, column=1, sticky="w", padx=6)
            lamps[text] = (cv, dot, colour)
        self.status_lamps[axis] = lamps

        # ---- LIVE
        body = self._card(right, "Live")
        live = {}
        for r, (key, label, unit) in enumerate((("stroke", "STROKE", "mm"), ("load", "LOAD", "kgf"))):
            self._label(body, label, fg="white", size=9, bold=True).grid(row=r, column=0, sticky="e",
                                                                         padx=(0, 8), pady=2)
            v = tk.Label(body, text=NO_DATA, width=9, bg=FIELD_BG, fg=VALUE_FG, font=_font(12, True),
                         relief="solid", bd=1)
            v.grid(row=r, column=1, pady=2)
            self._label(body, unit, fg=theme.HEADER_FG, size=9).grid(row=r, column=2, sticky="w", padx=4)
            live[key] = v
        self.live[axis] = live

        # ---- CYLINDERS
        body = self._card(right, "Cylinders")
        for r, text in enumerate(CYLINDERS):
            self._button(body, text, lambda t=text: self._cylinder(axis, t), width=20).grid(row=r, column=0, pady=2)
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
            w.delete(0, "end")
            w.insert(0, "" if v is None else str(v))

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
            for text, src, _colour in STATUS_LAMPS:
                cv, dot, on_colour = self.status_lamps[axis][text]
                on = lamp_state(src, lamps, pc_snap, axis)
                cv.itemconfig(dot, fill=on_colour if on else LAMP_OFF)
            load = side.get("load")
            self.live[axis]["load"].config(text=NO_DATA if load is None else f"{load:.2f}")
            stroke = (strokes or {}).get(axis)
            self.live[axis]["stroke"].config(text=NO_DATA if stroke is None else f"{stroke:.1f}")
