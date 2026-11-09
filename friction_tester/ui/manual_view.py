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
        self.fields = {}         # (axis, key) -> Entry
        self.status_lamps = {}   # axis -> {text: (canvas, dot, colour)}
        self.live = {}           # axis -> {"stroke": Label, "load": Label}

        # As on the original: the two group boxes as tall as their content, Return just below.
        row = tk.Frame(self, bg=theme.BG)
        row.pack(fill="x", pady=(4, 0))
        for i, (axis, title, _short) in enumerate(sides):
            self._cable(row, axis, title).grid(row=0, column=i, sticky="nsew", padx=(0, 4) if i == 0 else (4, 0))
            row.columnconfigure(i, weight=1, uniform="manual")
        foot = tk.Frame(self, bg=theme.BG)
        foot.pack(fill="x", pady=(14, 0))
        tk.Button(foot, text="↩  Return", command=page.show_measurement, bg="#333", fg="white",
                  activebackground="#444", activeforeground="white", bd=0, padx=22, pady=5,
                  font=_font(10, True), cursor="hand2").pack()
        self.refresh_params()

    # ----------------------------------------------------------- layout
    # Row for row as the machine's original manual screen: a group box per cable with
    # its title tab; the left half holds POSITION SET, JOG Control, Pull / Push Position
    # and Clamp Cylinder, the right half Actuator Status, Stroke / Load and the other
    # cylinders. Rows are shared, so items that sit side by side on the original
    # (Pull / Push with Stroke / Load, Clamp with Removing Cylinder 2) line up here too.
    LABEL_W = 10             # left-half labels, right-aligned
    VALUE_LABEL_W = 7        # Stroke / Load labels

    def _button(self, parent, text, command, width=18, off=False):
        bg, fg = (BTN_OFF_BG, theme.MUTED) if off else (BTN_BG, "white")
        return tk.Button(parent, text=text, command=command, bg=bg, fg=fg, activebackground=BTN_ACTIVE,
                         activeforeground="white", bd=0, width=width, pady=4, font=_font(9, True), cursor="hand2")

    def _label(self, parent, text, fg=LABEL_FG, size=10, bold=False, width=None, anchor="center"):
        kw = {"width": width} if width else {}
        return tk.Label(parent, text=text, bg=theme.PANEL, fg=fg, font=_font(size, bold), anchor=anchor, **kw)

    def _field(self, parent, axis, key):
        common = dict(width=7, justify="center", font=_font(11), bg=FIELD_BG, fg=VALUE_FG,
                      relief="solid", bd=1, insertbackground="white")
        w = tk.Entry(parent, highlightthickness=0, **common)
        w.bind("<Return>", lambda _e: self._param_edited(axis, key))
        w.bind("<FocusOut>", lambda _e: self._param_edited(axis, key))
        self.fields[(axis, key)] = w
        return w

    def _row(self, parent):
        return tk.Frame(parent, bg=theme.PANEL)

    def _cable(self, parent, axis, title):
        box = tk.LabelFrame(parent, text=f"  {title}  ", bg=theme.PANEL, fg=theme.TITLE_FG,
                            font=_font(13, True), bd=1, relief="solid", labelanchor="nw",
                            highlightbackground=theme.BORDER)
        g = tk.Frame(box, bg=theme.PANEL)
        g.pack(fill="both", expand=True, padx=14, pady=(6, 10))
        g.columnconfigure(0, weight=1, uniform="half")
        g.columnconfigure(1, weight=1, uniform="half")
        lw, PAD = self.LABEL_W, 3
        L = dict(column=0, pady=PAD)
        R = dict(column=1, sticky="w", padx=(24, 0), pady=PAD)

        # rows 0-7: POSITION SET + JOG Control | Actuator Status
        self._label(g, "POSITION SET", fg="white", size=10).grid(row=0, **L)
        self._label(g, "Actuator Status", fg="white", size=10, anchor="w").grid(row=0, **R)
        for r, (key, label) in enumerate(PARAM_FIELDS, start=1):
            f = self._row(g)
            self._label(f, label, fg=LINK_FG, width=lw, anchor="e").pack(side="left", padx=(0, 8))
            self._field(f, axis, key).pack(side="left")
            self._label(f, "mm", fg=theme.HEADER_FG, size=9, width=4, anchor="w").pack(side="left", padx=(6, 0))
            f.grid(row=r, **L)
        self._label(g, "JOG Control", fg="white", size=10).grid(row=4, column=0, pady=(16, PAD))
        f = self._row(g)                                # Low / High headings over their fields
        self._label(f, "Low", fg=theme.HEADER_FG, size=9).grid(row=0, column=1)
        self._label(f, "High", fg=theme.HEADER_FG, size=9).grid(row=0, column=2)
        self._label(f, "Jog Speed", fg=LINK_FG, width=lw, anchor="e").grid(row=1, column=0, padx=(0, 8))
        self._field(f, axis, "jog_low").grid(row=1, column=1, padx=(0, 6))
        self._field(f, axis, "jog_high").grid(row=1, column=2)
        f.grid(row=5, column=0, rowspan=2, pady=PAD)
        f = self._row(g)
        for text, what in JOG_BUTTONS:
            tk.Button(f, text=text, command=lambda w=what: self._not_driven(
                axis, f"Jog {w}", "jog is not a PC command in PRINTING PROGRAM OLD - use the machine's touch panel"),
                bg=BTN_BG, fg=LINK_FG, activebackground=BTN_ACTIVE, activeforeground="white", bd=0, width=4,
                pady=2, font=_font(15, True), cursor="hand2").pack(side="left", padx=4)
        f.grid(row=7, column=0, pady=(12, 0))

        lamps = {}                                      # evenly spaced, as on the original
        col = self._row(g)
        for text, _src, colour in STATUS_LAMPS:
            f = self._row(col)
            cv = tk.Canvas(f, width=18, height=18, bg=theme.PANEL, highlightthickness=0)
            dot = cv.create_oval(2, 2, 16, 16, fill=LAMP_OFF, outline="#111")
            cv.pack(side="left")
            self._label(f, text, fg=LINK_FG, anchor="w").pack(side="left", padx=(8, 0))
            f.pack(anchor="w", pady=4)
            lamps[text] = (cv, dot, colour)
        col.grid(row=1, rowspan=6, column=1, sticky="nw", padx=(24, 0))
        self.status_lamps[axis] = lamps

        g.rowconfigure(8, minsize=34)                   # gap, as on the original

        # rows 9-10: Pull / Push Position | Stroke / Load
        f = self._row(g)
        for text, what in (("⇦  Pull Position", "PULL"), ("Push Position  ⇨", "PUSH")):
            self._button(f, text, lambda w=what: self._not_driven(
                axis, f"{w} position", f"{w} runs only inside a cable test (RUN) in this PLC program"),
                width=14, off=True).pack(side="left", padx=4)
        f.grid(row=9, column=0, rowspan=2)
        live = {}
        for r, (key, label, unit) in enumerate((("stroke", "Stroke", "mm"), ("load", "Load", "Kgf")), start=9):
            f = self._row(g)
            self._label(f, label, fg=LINK_FG, width=self.VALUE_LABEL_W, anchor="e").pack(side="left", padx=(0, 8))
            v = tk.Label(f, text=NO_DATA, width=9, bg=FIELD_BG, fg=VALUE_FG, font=_font(12, True),
                         relief="solid", bd=1)
            v.pack(side="left")
            self._label(f, unit, fg=theme.HEADER_FG, size=9, width=4, anchor="w").pack(side="left", padx=(6, 0))
            f.grid(row=r, **R)
            live[key] = v
        self.live[axis] = live

        g.rowconfigure(11, minsize=28)                  # gap

        # rows 12-14: Clamp Cylinder (level with Removing Cylinder 2) | the other cylinders
        self._button(g, "☰  Clamp Cylinder", lambda: self._cylinder(axis, "Clamp Cylinder"),
                     width=18).grid(row=13, column=0, pady=PAD)
        for r, (icon, text) in enumerate((("⏏", "Removing Cylinder 1"), ("⏏", "Removing Cylinder 2"),
                                          ("☰", "Loading Cylinder")), start=12):
            self._button(g, f"{icon}  {text}", lambda t=text: self._cylinder(axis, t), width=20).grid(row=r, **R)
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
