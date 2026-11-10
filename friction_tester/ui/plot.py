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
ZERO = "#9aa5b1"
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

    def __init__(self, master, xlim=(-30.0, 30.0), ylim=(-1.8, 1.8),
                 xlabel="Stroke [mm]", ylabel="Load [kgf]"):
        super().__init__(master, bg=theme.PANEL)
        self.home = (tuple(xlim), tuple(ylim))
        self.xlim, self.ylim = list(xlim), list(ylim)
        self.xlabel, self.ylabel = xlabel, ylabel
        self.series = {}   # name -> {"color": c, "x": [...], "y": [...]}

        self.canvas = tk.Canvas(self, bg=PLOT_BG, highlightthickness=1,
                                highlightbackground=theme.BORDER, height=180)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _e: self.redraw())

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
    def zoom(self, factor):
        for lim in (self.xlim, self.ylim):
            mid, half = (lim[0] + lim[1]) / 2, (lim[1] - lim[0]) / 2 * factor
            lim[0], lim[1] = mid - half, mid + half
        self.redraw()

    def pan(self, fx, fy):
        dx = (self.xlim[1] - self.xlim[0]) * fx
        dy = (self.ylim[1] - self.ylim[0]) * fy
        self.xlim = [self.xlim[0] + dx, self.xlim[1] + dx]
        self.ylim = [self.ylim[0] + dy, self.ylim[1] + dy]
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
        for lim, lo, hi in ((self.xlim, min(xs), max(xs)), (self.ylim, min(ys), max(ys))):
            pad = max((hi - lo) * 0.05, 1e-3)
            if lo < lim[0]:
                lim[0], grown = lo - pad, True
            if hi > lim[1]:
                lim[1], grown = hi + pad, True
        if grown:
            self.redraw()

    def fit_data(self):
        """Fit the view to the traces (the default view when there is no data)."""
        xs = [v for s in self.series.values() for v in s["x"]]
        ys = [v for s in self.series.values() for v in s["y"]]
        if not xs:
            return self.reset_view()
        for lim, lo, hi in ((self.xlim, min(xs), max(xs)), (self.ylim, min(ys), max(ys))):
            span = hi - lo or max(abs(hi), 1.0)
            lim[0], lim[1] = lo - span * 0.08, hi + span * 0.08
        self.redraw()

    def reset_view(self):
        self.xlim, self.ylim = list(self.home[0]), list(self.home[1])
        self.redraw()

    # -------------------------------------------------------------- drawing
    def _to_px(self, x, y, w, h):
        l, r, t, b = self.MARGIN_L, w - self.MARGIN_R, self.MARGIN_T, h - self.MARGIN_B
        px = l + (x - self.xlim[0]) / (self.xlim[1] - self.xlim[0]) * (r - l)
        py = b - (y - self.ylim[0]) / (self.ylim[1] - self.ylim[0]) * (b - t)
        return px, py

    def redraw(self):
        c = self.canvas
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if w < 120 or h < 80:
            return
        l, r, t, b = self.MARGIN_L, w - self.MARGIN_R, self.MARGIN_T, h - self.MARGIN_B
        font = (theme.FONT, 8)

        # Grid and tick labels
        for lim, horizontal in ((self.xlim, False), (self.ylim, True)):
            step = _nice_step(lim[1] - lim[0], 12 if not horizontal else 9)
            v = (int(lim[0] / step) - 1) * step
            while v <= lim[1] + 1e-9:
                if v >= lim[0] - 1e-9:
                    if horizontal:
                        _, py = self._to_px(0, v, w, h)
                        c.create_line(l, py, r, py, fill=ZERO if abs(v) < step / 1e6 else GRID)
                        c.create_text(l - 6, py, text=f"{v:.2f}", anchor="e", fill=AXIS, font=font)
                    else:
                        px, _ = self._to_px(v, 0, w, h)
                        c.create_line(px, t, px, b, fill=ZERO if abs(v) < step / 1e6 else GRID)
                        c.create_text(px, b + 10, text=f"{v:.1f}", anchor="n", fill=AXIS, font=font)
                v += step
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
                c.create_line(*pts, fill=s["color"], width=2)
            elif len(pts) == 2:
                c.create_oval(pts[0] - 2, pts[1] - 2, pts[0] + 2, pts[1] + 2, fill=s["color"], outline="")

        # Legend, top right
        y = t + 8
        for name, s in self.series.items():
            c.create_rectangle(r - 86, y - 7, r - 8, y + 7, fill=T.L_SURFACE, outline=T.L_BORDER)
            c.create_text(r - 80, y, text=name, anchor="w", fill=T.L_TEXT, font=font)
            c.create_line(r - 36, y, r - 14, y, fill=s["color"], width=2)
            y += 17
