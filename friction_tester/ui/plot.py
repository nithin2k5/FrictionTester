"""A small Load-vs-Stroke chart drawn on a Tk canvas (no plotting library).

Grid, tick labels, a legend, PULL/PUSH traces, and zoom/pan controls in the
same spirit as the LabVIEW graph it replaces.
"""

import math
import tkinter as tk

from . import theme as T
from .theme import light as theme      # light industrial theme

GRID = "#e1e5ea"
AXIS = T.L_TEXT_2
CURSOR = "#1f2933"      # zero-load line and stroke start line
PLOT_BG = T.L_SURFACE


def _nice_step(span, target=10):
    """A 1/2/5 x 10^n step giving roughly `target` divisions over `span`."""
    raw = span / target
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 5, 10):
        if mag * m >= raw:
            return mag * m
    return mag * 10


class LoadStrokePlot(tk.Frame):
    MARGIN_L, MARGIN_R, MARGIN_T, MARGIN_B = 62, 14, 12, 40
    DEFAULT_XLIM = (-30.0, 30.0)
    X_STEP, Y_STEP = 2.0, 0.2          # divisions of the LabVIEW graph: 2 mm, 0.20 kgf

    def __init__(self, master, xlim=DEFAULT_XLIM, ylim=(-1.8, 1.8),
                 xlabel="Stroke [mm]", ylabel="Load [kgf]", controls=True):
        """controls=False: no zoom / pan buttons (a small preview)."""
        super().__init__(master, bg=theme.PANEL)
        self.start_x = None            # x of the stroke start line (set_stroke)
        self._margin_l = self.MARGIN_L # grows with the width of the load labels (redraw)
        self.home = (tuple(xlim), tuple(ylim))
        self.xlim, self.ylim = list(xlim), list(ylim)
        self.xlabel, self.ylabel = xlabel, ylabel
        self.series = {}   # name -> {"color": c, "x": [...], "y": [...]}
        self.note = ""     # a warning drawn inside the plot (e.g. why the NI card gave no load)

        self.canvas = tk.Canvas(self, bg=PLOT_BG, highlightthickness=1,
                                highlightbackground=theme.BORDER, height=180)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _e: self.redraw())
        if not controls:
            return

        bar = tk.Frame(self, bg=theme.PANEL)
        bar.pack(fill="x", pady=(4, 0))
        for text, cmd in (("+", lambda: self.zoom(0.8)), ("−", lambda: self.zoom(1.25)),
                          ("Fit", self.fit_data),
                          ("◄", lambda: self.pan(-0.1, 0)), ("►", lambda: self.pan(0.1, 0)),
                          ("▲", lambda: self.pan(0, 0.1)), ("▼", lambda: self.pan(0, -0.1))):
            tk.Button(bar, text=text, command=cmd, bg=T.L_SURFACE_2, fg=T.L_TEXT, activebackground=T.L_BORDER_2,
                      activeforeground=T.L_TEXT, bd=0, width=3, font=(theme.FONT, 10, "bold"),
                      cursor="hand2").pack(side="left", padx=(0, 3))

    # ---------------------------------------------------------------- data
    def add_series(self, name, color):
        self.series[name] = {"color": color, "x": [], "y": []}
        self.redraw()

    def set_data(self, name, xs, ys):
        s = self.series[name]
        s["x"], s["y"] = list(xs), list(ys)
        self.redraw()

    def append(self, name, x, y):
        s = self.series[name]
        s["x"].append(x)
        s["y"].append(y)
        self.redraw()

    def clear(self):
        for s in self.series.values():
            s["x"], s["y"] = [], []
        self.redraw()

    # ---------------------------------------------------------------- view
    def _snap_load(self, v, how=round):
        """`v` moved onto a 0.20 kgf division (how = round / math.floor / math.ceil), so the
        load axis always starts and ends on a division."""
        eps = {math.floor: 1e-9, math.ceil: -1e-9}.get(how, 0.0)
        return round(how(v / self.Y_STEP + eps) * self.Y_STEP, 6)

    def zoom(self, factor):
        for lim in (self.xlim, self.ylim):
            mid, half = (lim[0] + lim[1]) / 2, (lim[1] - lim[0]) / 2 * factor
            lim[0], lim[1] = mid - half, mid + half
        lo, hi = self._snap_load(self.ylim[0]), self._snap_load(self.ylim[1])
        if hi - lo < self.Y_STEP / 2:
            hi = lo + self.Y_STEP
        self.ylim = [lo, hi]
        self.redraw()

    def pan(self, fx, fy):
        dx = (self.xlim[1] - self.xlim[0]) * fx
        dy = (self.ylim[1] - self.ylim[0]) * fy
        if fy:                               # the load axis moves by whole divisions
            dy = math.copysign(max(self.Y_STEP, self._snap_load(abs(dy))), fy)
        self.xlim = [self.xlim[0] + dx, self.xlim[1] + dx]
        self.ylim = [round(self.ylim[0] + dy, 6), round(self.ylim[1] + dy, 6)]
        self.redraw()

    def set_note(self, text):
        if (text or "") != self.note:
            self.note = text or ""
            self.redraw()

    def set_xlabel(self, text):
        if text != self.xlabel:
            self.xlabel = text
            self.redraw()

    def extend_to_data(self):
        """Grow the view (never shrink it) so every point is visible."""
        xs = [v for s in self.series.values() for v in s["x"]]
        ys = [v for s in self.series.values() for v in s["y"]]
        if not xs:
            return
        grown = False
        lo, hi = min(xs), max(xs)
        pad = max((hi - lo) * 0.05, 1e-3)
        if lo < self.xlim[0]:
            self.xlim[0], grown = lo - pad, True
        if hi > self.xlim[1]:
            self.xlim[1], grown = hi + pad, True
        if min(ys) < self.ylim[0]:           # the load axis grows by whole 0.20 divisions
            self.ylim[0], grown = self._snap_load(min(ys), math.floor), True
        if max(ys) > self.ylim[1]:
            self.ylim[1], grown = self._snap_load(max(ys), math.ceil), True
        if grown:
            self.redraw()

    def fit_data(self):
        """Fit the view to the traces (the default view when there is no data)."""
        xs = [v for s in self.series.values() for v in s["x"]]
        ys = [v for s in self.series.values() for v in s["y"]]
        if not xs:
            return self.reset_view()
        # Stroke axis: out to the 2 mm divisions around the traces
        lo = round(math.floor(min(xs) / self.X_STEP + 1e-9) * self.X_STEP, 6)
        hi = round(math.ceil(max(xs) / self.X_STEP - 1e-9) * self.X_STEP, 6)
        if hi - lo < self.X_STEP / 2:        # a single point lying on a division
            lo, hi = lo - self.X_STEP, hi + self.X_STEP
        self.xlim = [lo, hi]
        # Load axis: out to the 0.20 kgf divisions around the traces, never a finer scale
        lo, hi = self._snap_load(min(ys), math.floor), self._snap_load(max(ys), math.ceil)
        if hi - lo < self.Y_STEP / 2:        # a flat trace lying on a division
            lo, hi = round(lo - self.Y_STEP, 6), round(hi + self.Y_STEP, 6)
        self.ylim = [lo, hi]
        self.redraw()

    def set_stroke(self, stroke):
        """Scale the stroke axis to the test stroke like the LabVIEW graph: the stroke runs
        from -stroke/2 to +stroke/2, the axis spans +/-0.6 x stroke (22 mm -> +/-13.2) and a
        line marks the start of the stroke. None = the default axis, no start line."""
        if stroke:
            self.home = ((-0.6 * stroke, 0.6 * stroke), self.home[1])
            self.start_x = -stroke / 2
        else:
            self.home = (self.DEFAULT_XLIM, self.home[1])
            self.start_x = None
        self.reset_view()

    def reset_view(self):
        self.xlim, self.ylim = list(self.home[0]), list(self.home[1])
        self.redraw()

    # -------------------------------------------------------------- drawing
    def _axis(self, lim, fixed, decimals, target, finer=True):
        """(division, label format) of an axis: the LabVIEW division (2 mm / 0.20 kgf), a
        coarser 1/2/5 step once zoomed far out, a finer one - with enough decimals to tell
        the divisions apart - when zoomed far in. finer=False (the load axis) never goes
        below the fixed division and only coarsens in whole multiples of it."""
        span = lim[1] - lim[0]
        if not finer:
            step = fixed if span / fixed <= 24 else fixed * _nice_step(span / fixed, target)
            return step, "{:." + str(decimals) + "f}"
        step = fixed if span / fixed <= 24 else _nice_step(span, target)
        if span / step < 4:
            step = _nice_step(span, 8)
        decimals = max(decimals, min(6, math.ceil(-math.log10(step) - 1e-9)))
        return step, "{:." + str(decimals) + "f}"

    def _to_px(self, x, y, w, h):
        l, r, t, b = self._margin_l, w - self.MARGIN_R, self.MARGIN_T, h - self.MARGIN_B
        px = l + (x - self.xlim[0]) / (self.xlim[1] - self.xlim[0]) * (r - l)
        py = b - (y - self.ylim[0]) / (self.ylim[1] - self.ylim[0]) * (b - t)
        return px, py

    def redraw(self):
        c = self.canvas
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if w < 120 or h < 80:
            return
        font = (theme.FONT, 8)
        axes = ((self.xlim, False, 32) + self._axis(self.xlim, self.X_STEP, 1, 12),
                (self.ylim, True, 9) + self._axis(self.ylim, self.Y_STEP, 2, 9, finer=False))
        # Left margin: room for the longest load label beside the axis title
        widest = max(len(axes[1][4].format(v)) for v in self.ylim)
        self._margin_l = max(self.MARGIN_L, 32 + 6 * widest)
        l, r, t, b = self._margin_l, w - self.MARGIN_R, self.MARGIN_T, h - self.MARGIN_B

        # Grid and tick labels: 2 mm / 0.20 kgf divisions like the LabVIEW graph (a coarser
        # 1/2/5 step once the view is zoomed far out), plus a label at each end of the axis.
        for lim, horizontal, gap, step, fmt in axes:
            span = lim[1] - lim[0]
            ends = [self._to_px(v, v, w, h)[1 if horizontal else 0] for v in lim]
            end_texts = {fmt.format(v + 0.0) for v in lim}
            every = max(1, math.ceil(gap / (abs(ends[1] - ends[0]) * step / span)))   # label spacing

            def label(v, pos):
                if horizontal:
                    c.create_text(l - 6, pos, text=fmt.format(v + 0.0), anchor="e", fill=AXIS, font=font)
                else:
                    c.create_text(pos, b + 10, text=fmt.format(v + 0.0), anchor="n", fill=AXIS, font=font)

            for k in range(math.ceil(lim[0] / step - 1e-9), math.floor(lim[1] / step + 1e-9) + 1):
                v = k * step
                px, py = self._to_px(v, v, w, h)
                pos = py if horizontal else px
                if horizontal:
                    c.create_line(l, py, r, py, fill=GRID)
                else:
                    c.create_line(px, t, px, b, fill=GRID)
                if (k % every == 0 and min(abs(pos - e) for e in ends) >= gap
                        and fmt.format(v + 0.0) not in end_texts):
                    label(v, pos)                    # not where it would run into / repeat an end label
            for v, e in zip(lim, ends):
                label(v, e)
        # Zero-load line and the stroke start line
        if self.ylim[0] < 0 < self.ylim[1]:
            _, py = self._to_px(0, 0, w, h)
            c.create_line(l, py, r, py, fill=CURSOR)
        if self.start_x is not None and self.xlim[0] < self.start_x < self.xlim[1]:
            px, _ = self._to_px(self.start_x, 0, w, h)
            c.create_line(px, t, px, b, fill=CURSOR)
        c.create_rectangle(l, t, r, b, outline=AXIS)
        c.create_text((l + r) / 2, h - 4, text=self.xlabel, anchor="s", fill=T.L_TEXT, font=(theme.FONT, 9))
        c.create_text(12, (t + b) / 2, text=self.ylabel, angle=90, fill=T.L_TEXT, font=(theme.FONT, 9))

        # Traces, clipped to the plot area
        for s in self.series.values():
            pts = []
            for x, y in zip(s["x"], s["y"]):
                px, py = self._to_px(x, y, w, h)
                pts.extend((min(max(px, l), r), min(max(py, t), b)))
            if len(pts) >= 4:
                c.create_line(*pts, fill=s["color"], width=1.5)
            elif len(pts) == 2:
                c.create_oval(pts[0] - 2, pts[1] - 2, pts[0] + 2, pts[1] + 2, fill=s["color"], outline="")

        if self.note:
            c.create_text(l + 8, b - 6, text=self.note, anchor="sw", fill=theme.FAIL, font=font,
                          width=max(60, r - l - 16))

        # Legend, top right
        y = t + 8
        for name, s in self.series.items():
            c.create_rectangle(r - 86, y - 7, r - 8, y + 7, fill=T.L_SURFACE, outline=T.L_BORDER)
            c.create_text(r - 80, y, text=name, anchor="w", fill=T.L_TEXT, font=font)
            c.create_line(r - 36, y, r - 14, y, fill=s["color"], width=2)
            y += 17
