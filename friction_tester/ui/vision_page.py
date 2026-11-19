"""Vision page: object detection by template matching (vision_engine).

Teach, tune and verify the models that check a part is present and correct,
and set up the inspection cameras. Ported from the FeederCable tester's Vision
Settings page (object detection only; its wire-colour check is left out).

A vision dataset is kept per model (TBL_MODEL_MASTER.MM_MODEL). The page is a
thin shell over vision_engine.vision_controller: it never matches or judges
anything itself, so what is verified here is exactly what inspect() does.
"""
import os
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from . import theme

try:
    import cv2
    _cv2_ok = True
except ImportError:
    _cv2_ok = False

try:
    from PIL import Image, ImageTk
    _pil_ok = True
except ImportError:
    _pil_ok = False


# ── Palette: the app's light theme (theme.py) ──────────────────────────────────
BG          = theme.L_BG
PANEL       = theme.L_SURFACE
PANEL_ALT   = theme.L_SUNKEN
LINE        = theme.L_BORDER
FIELD       = theme.L_SURFACE
TXT         = theme.L_TEXT
TXT_DIM     = theme.L_TEXT_2
TXT_FAINT   = theme.L_TEXT_3
ACCENT      = theme.L_PRIMARY
OK_GREEN    = theme.L_OK
NG_RED      = theme.L_NG
WARN        = theme.L_WARN
CHIP        = theme.L_SURFACE_2         # status pill, idle verdict strip
VERDICT_BG  = {"OK": "#e8f5e9", "NG": "#ffebee", "ERROR": theme.L_WARN_BG}

BTN_PRIMARY = (theme.L_PRIMARY, theme.L_PRIMARY_DK)
BTN_SUCCESS = ("#2e7d32", "#1b5e20")
BTN_DANGER  = ("#c62828", "#b71c1c")
BTN_NEUTRAL = (theme.L_NEUTRAL_BTN, "#4b5866")
BTN_OFF     = "#cfd5dc"                 # a disabled button

# Drawn over camera images (dark letterbox), so they stay bright.
IMG_BG      = "#07080b"
IMG_TEXT    = "#9aa4b1"
IMG_OK      = "#00e676"
IMG_NG      = "#ff5252"
IMG_WARN    = "#ffab00"
IMG_LINE    = "#2a2f38"

# camera_cfg.ini and vision_models/ sit in the project root, where
# vision_engine looks for them.
_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_CAM_CFG_PATH = os.path.join(_ROOT_DIR, "camera_cfg.ini")
_MODELS_DIR = os.path.join(_ROOT_DIR, "vision_models")

# ── Camera config (same file and keys test_console reads) ──────────────────────

def _load_cam_cfg(cam_id: int = 1) -> dict:
    """camN_* settings for camera slot `cam_id` (1 or 2)."""
    import configparser
    cfg = configparser.ConfigParser()
    cfg.read(_CAM_CFG_PATH)
    k = "cam%d_" % cam_id
    return {
        "index":   cfg.getint("CAMERA", k + "index", fallback=-1),
        "width":   cfg.getint("CAMERA", k + "width", fallback=640),
        "height":  cfg.getint("CAMERA", k + "height", fallback=480),
        "enabled": cfg.getboolean("CAMERA", k + "enabled", fallback=False),
        "flip":    cfg.getboolean("CAMERA", k + "flip", fallback=False),
        "mirror":  cfg.getboolean("CAMERA", k + "mirror", fallback=False),
    }


def _save_cam_cfg(index: int, width: int, height: int, enabled: bool, flip: bool = False,
                  mirror: bool = False, cam_id: int = 1):
    """Update camN_* for slot `cam_id` in place, leaving the other slot and
    any other keys untouched."""
    import configparser
    cfg = configparser.ConfigParser()
    cfg.read(_CAM_CFG_PATH)
    if not cfg.has_section("CAMERA"):
        cfg.add_section("CAMERA")
    k = "cam%d_" % cam_id
    cfg.set("CAMERA", k + "index", str(index))
    cfg.set("CAMERA", k + "width", str(width))
    cfg.set("CAMERA", k + "height", str(height))
    cfg.set("CAMERA", k + "enabled", str(bool(enabled)))
    cfg.set("CAMERA", k + "flip", str(bool(flip)))
    cfg.set("CAMERA", k + "mirror", str(bool(mirror)))
    with open(_CAM_CFG_PATH, "w") as f:
        cfg.write(f)


def _for_vision(frame):
    """A camera or imported frame at the size vision is taught and judged at
    (vision_engine.camera.VISION_WIDTH). Every frame teaching sees goes
    through this, so references, drawn boxes and inspection share one scale."""
    from vision_engine import camera
    return camera.for_vision(frame)


def _probe_cameras(max_index: int = 6):
    """Indices that open *and* deliver a frame, with their maximum resolution
    (cameras always run at it -- there is no resolution setting).

    A device that opens but never yields a frame is worse than no device at all —
    it looks configured and then fails mid-cycle — so opening is not enough to
    call a camera present.
    """
    found = []
    if not _cv2_ok:
        return found
    from vision_engine import camera
    for i in range(max_index):
        cap = None
        try:
            cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
            if cap.isOpened():
                ret, _ = cap.read()
                size = camera.max_resolution(cap) if ret else None
                if size:
                    found.append({"index": i, "width": size[0], "height": size[1]})
        except Exception:
            pass
        finally:
            if cap is not None:
                cap.release()
    return found


# ── Model master (TBL_MODEL_MASTER: the models the Test Console runs) ──────────

def _fetch_master_parts():
    """[(model, part number), ...] from the model master, or None if the
    database can't be reached. Best effort: callers fall back to free-text
    entry when it returns None instead of blocking teaching."""
    try:
        from .. import db
        return [(str(r["MM_MODEL"]), r.get("MM_PART_NUMBER") or "") for r in db.list_models()]
    except Exception:
        return None


def _part_choice_label(pno, pname):
    return "%s — %s" % (pno, pname) if pname else pno


class _PnoField:
    """Model input: a locked-to-the-master combobox when the model master is
    reachable, a free-text Entry when it isn't. Exposes the same get / set /
    lock / focus_set / bind / pack surface either way so call sites don't need
    to know which one is live underneath.
    """

    def __init__(self, parent, master_parts, font_size=15):
        self._by_label = {}
        self.is_master_backed = master_parts is not None
        if master_parts:
            labels = []
            for pno, pname in master_parts:
                label = _part_choice_label(pno, pname)
                self._by_label[label] = pno
                labels.append(label)
            self.widget = ttk.Combobox(parent, state="readonly", values=labels,
                                       font=("Consolas", font_size))
        else:
            self.widget = tk.Entry(parent, bg=FIELD, fg=TXT,
                                   font=("Consolas", font_size),
                                   insertbackground=TXT, relief="flat",
                                   highlightthickness=1, highlightbackground=LINE,
                                   highlightcolor=ACCENT)

    def get(self) -> str:
        raw = self.widget.get().strip()
        return self._by_label.get(raw, raw).strip()

    def set(self, pno):
        if self.is_master_backed:
            label = next((l for l, p in self._by_label.items() if p == pno), pno)
            self.widget.set(label)
        else:
            self.widget.delete(0, "end")
            self.widget.insert(0, pno)

    def lock(self):
        if self.is_master_backed:
            self.widget.config(state="disabled")
        else:
            self.widget.config(state="readonly", readonlybackground=FIELD, fg=TXT_DIM)

    def focus_set(self):
        self.widget.focus_set()

    def pack(self, **kw):
        self.widget.pack(**kw)

    def bind(self, sequence, func):
        self.widget.bind(sequence, func)
        # A readonly combobox never emits KeyRelease from a pick — it emits
        # its own selection event — so route both through the same handler.
        if self.is_master_backed and sequence == "<KeyRelease>":
            self.widget.bind("<<ComboboxSelected>>", func)


# ── Small widget helpers ───────────────────────────────────────────────────────

def _btn(parent, text, kind=BTN_NEUTRAL, command=None, width=None, font_size=11, pady=6):
    bg, hover = kind
    b = tk.Button(parent, text=text, bg=bg, fg="white", bd=0, relief="flat",
                  activebackground=hover, activeforeground="white",
                  font=("Arial", font_size, "bold"), padx=12, pady=pady,
                  cursor="hand2", command=command,
                  disabledforeground=theme.L_TEXT_3)
    if width:
        b.config(width=width)
    b._colors = (bg, hover)
    b.bind("<Enter>", lambda e: b.config(bg=hover) if str(b["state"]) != "disabled" else None)
    b.bind("<Leave>", lambda e: b.config(bg=bg) if str(b["state"]) != "disabled" else None)
    return b


def _set_btn_enabled(btn, enabled: bool):
    if enabled:
        btn.config(state="normal", bg=btn._colors[0], cursor="hand2")
    else:
        btn.config(state="disabled", bg=BTN_OFF, cursor="arrow")


def _card(parent, title, subtitle=None):
    """Bordered panel with a title strip. Returns the body frame to fill."""
    outer = tk.Frame(parent, bg=LINE, bd=0)
    inner = tk.Frame(outer, bg=PANEL)
    inner.pack(fill="both", expand=True, padx=1, pady=1)

    head = tk.Frame(inner, bg=PANEL)
    head.pack(fill="x", padx=14, pady=(10, 8))
    tk.Label(head, text=title, bg=PANEL, fg=TXT, font=("Arial", 13, "bold")).pack(side="left")
    if subtitle:
        tk.Label(head, text=subtitle, bg=PANEL, fg=TXT_FAINT,
                 font=("Arial", 11)).pack(side="left", padx=(10, 0))
    tk.Frame(inner, bg=LINE, height=1).pack(fill="x")

    body = tk.Frame(inner, bg=PANEL)
    body.pack(fill="both", expand=True, padx=14, pady=12)
    outer.head = head
    outer.body = body
    return outer


def _kv_row(parent, label, value="—", value_fg=TXT, mono=False):
    """One label/value line. Returns the value label so callers can update it."""
    f = tk.Frame(parent, bg=parent["bg"])
    f.pack(fill="x", pady=2)
    tk.Label(f, text=label, bg=parent["bg"], fg=TXT_DIM, font=("Arial", 11),
             width=11, anchor="w").pack(side="left")
    v = tk.Label(f, text=value, bg=parent["bg"], fg=value_fg, anchor="w",
                 font=("Consolas", 11) if mono else ("Arial", 9, "bold"))
    v.pack(side="left", fill="x", expand=True)
    return v


def _to_photo(img_bgr, box_w, box_h):
    """BGR ndarray → PhotoImage scaled to fit (box_w, box_h). Returns (photo, scale)."""
    rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    ih, iw = rgb.shape[:2]
    scale = min(box_w / iw, box_h / ih)
    dw, dh = max(1, int(iw * scale)), max(1, int(ih * scale))
    im = Image.fromarray(rgb).resize((dw, dh), Image.Resampling.BILINEAR)
    return ImageTk.PhotoImage(im), scale


def _threshold_caption(value: float) -> tuple:
    """Plain-language reading of a correlation threshold."""
    if value < 0.55:
        return "Very lenient — almost any frame will pass", NG_RED
    if value < 0.68:
        return "Lenient — tolerates lighting and position drift", WARN
    if value < 0.85:
        return "Balanced — recommended for production", OK_GREEN
    if value < 0.94:
        return "Strict — needs consistent lighting and fixturing", WARN
    return "Very strict — near-identical frames only", NG_RED


# ═══════════════════════════════════════════════════════════════════════════════
# ROI marker
# ═══════════════════════════════════════════════════════════════════════════════

class RoiView(tk.Canvas):
    """Image view with a draggable, resizable region-of-interest marker.

    The marker lives as canvas items on top of the image instead of being burnt
    into the pixel buffer, so it stays sharp at any display scale and can be
    nudged or resized after it is drawn — the previous builder could only throw
    a box away and start over.

    Interaction:
      drag on empty space  -> draw a new box
      drag inside the box  -> move it
      drag a handle        -> resize from that edge or corner
      Delete / Escape      -> clear

    With a locked size (see lock_size) the handles disappear and a click drops a
    box of that exact size where you point, so a box drawn on one image can be
    repositioned onto the part in the next without changing what it crops.
    """

    HANDLE = 4          # handle half-size, screen px
    GRAB = 7            # grab tolerance around an edge, screen px
    MIN_ROI = 10        # smallest useful template, image px

    _CURSORS = {
        "nw": "size_nw_se", "se": "size_nw_se",
        "ne": "size_ne_sw", "sw": "size_ne_sw",
        "n": "sb_v_double_arrow", "s": "sb_v_double_arrow",
        "w": "sb_h_double_arrow", "e": "sb_h_double_arrow",
        "move": "fleur",
    }

    def __init__(self, parent, on_change=None, editable=True, **kw):
        super().__init__(parent, bg=IMG_BG, highlightthickness=0,
                         bd=0, cursor="crosshair", **kw)
        self._on_change = on_change
        self._editable = editable
        self._image = None          # BGR ndarray
        self._roi = None            # {"x","y","width","height"} in image coords
        self._view = None           # (scale, ox, oy, dw, dh)
        self._photo = None
        self._drag = None
        self._placeholder = "No image"
        self._hint = None
        self._accent = IMG_OK
        self._locked_size = None    # (w, h) in image px, or None for free drawing
        self._overlays = []         # [(roi, color, label)] drawn read-only

        self.bind("<Configure>", lambda e: self._redraw())
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Motion>", self._on_hover)
        self.bind("<Leave>", lambda e: self.config(cursor="crosshair"))

    # -- public API ---------------------------------------------------------

    def set_image(self, img, keep_roi=True):
        prev_shape = None if self._image is None else self._image.shape[:2]
        self._image = img
        if img is not None:
            if not keep_roi:
                self._roi = None
            elif prev_shape and prev_shape != img.shape[:2]:
                self._roi = None        # a box means nothing at a new resolution
        self._redraw()

    def get_image(self):
        return self._image

    def set_placeholder(self, text):
        self._placeholder = text
        if self._image is None:
            self._redraw()

    def set_hint(self, text):
        """Caption drawn along the bottom of the view."""
        self._hint = text
        self._redraw()

    def set_editable(self, editable):
        self._editable = editable
        self.config(cursor="crosshair" if editable else "arrow")
        self._redraw()

    def set_accent(self, color):
        self._accent = color
        self._redraw()

    def set_overlays(self, overlays):
        """Read-only labelled boxes drawn alongside the editable one, as
        [(roi, color, label), ...] -- the other objects of a part while one is
        being boxed, or every object's match in a test result."""
        self._overlays = [o for o in (overlays or []) if o[0]]
        self._redraw()

    def lock_size(self, wh):
        """Pin the box to (w, h), or pass None to allow free drawing again."""
        self._locked_size = tuple(wh) if wh else None
        self._redraw()

    def _place_locked(self, cx, cy):
        """A locked-size box centred on (cx, cy), kept inside the image."""
        w, h = self._locked_size
        ih, iw = self._image.shape[:2]
        w, h = min(w, iw), min(h, ih)
        x = min(max(cx - w // 2, 0), iw - w)
        y = min(max(cy - h // 2, 0), ih - h)
        return {"x": int(x), "y": int(y), "width": int(w), "height": int(h)}

    def get_roi(self):
        return dict(self._roi) if self._roi else None

    def set_roi(self, roi, notify=True):
        self._roi = dict(roi) if roi else None
        self._redraw()
        if notify:
            self._notify()

    def clear_roi(self, event=None):
        self._roi = None
        self._redraw()
        self._notify()

    # -- coordinate mapping -------------------------------------------------

    def _compute_view(self):
        if self._image is None:
            self._view = None
            return
        cw, ch = self.winfo_width(), self.winfo_height()
        if cw < 5 or ch < 5:
            self._view = None
            return
        ih, iw = self._image.shape[:2]
        scale = min(cw / iw, ch / ih)
        dw, dh = max(1, int(iw * scale)), max(1, int(ih * scale))
        self._view = (scale, (cw - dw) // 2, (ch - dh) // 2, dw, dh)

    def _to_screen(self, ix, iy):
        s, ox, oy, _, _ = self._view
        return ox + ix * s, oy + iy * s

    def _to_image(self, sx, sy):
        s, ox, oy, _, _ = self._view
        ih, iw = self._image.shape[:2]
        ix = min(max((sx - ox) / s, 0), iw)
        iy = min(max((sy - oy) / s, 0), ih)
        return int(round(ix)), int(round(iy))

    def _roi_screen(self):
        r = self._roi
        x0, y0 = self._to_screen(r["x"], r["y"])
        x1, y1 = self._to_screen(r["x"] + r["width"], r["y"] + r["height"])
        return x0, y0, x1, y1

    # -- drawing ------------------------------------------------------------

    def _redraw(self):
        self.delete("all")
        self._compute_view()
        cw, ch = max(self.winfo_width(), 1), max(self.winfo_height(), 1)

        if self._view is None:
            self.create_text(cw // 2, ch // 2, text=self._placeholder,
                             fill=IMG_TEXT, font=("Arial", 13), justify="center")
            return

        s, ox, oy, dw, dh = self._view
        self._photo, _ = _to_photo(self._image, dw, dh)
        self.create_image(ox, oy, image=self._photo, anchor="nw")
        self.create_rectangle(ox, oy, ox + dw, oy + dh, outline=IMG_LINE)

        if self._roi:
            self._draw_marker()
        elif self._drag and self._drag["mode"] == "new":
            x0, y0 = self._to_screen(*self._drag["anchor"])
            x1, y1 = self._to_screen(*self._drag["cursor"])
            self.create_rectangle(x0, y0, x1, y1, outline=IMG_WARN, width=1, dash=(4, 3))

        for roi, color, label in self._overlays:
            x0, y0 = self._to_screen(roi["x"], roi["y"])
            x1, y1 = self._to_screen(roi["x"] + roi["width"], roi["y"] + roi["height"])
            self.create_rectangle(x0, y0, x1, y1, outline=color, width=2, dash=(6, 3))
            if label:
                ly = y0 - 10 if y0 - 10 > oy + 8 else y1 + 11
                tid = self.create_text(x0 + 2, ly, text=label, fill=IMG_BG, anchor="w",
                                       font=("Consolas", 10, "bold"))
                bx0, by0, bx1, by1 = self.bbox(tid)
                rid = self.create_rectangle(bx0 - 3, by0 - 1, bx1 + 3, by1 + 1,
                                            fill=color, outline="")
                self.tag_raise(tid, rid)

        if self._hint:
            self.create_text(cw // 2, ch - 12, text=self._hint, fill=IMG_TEXT,
                             font=("Arial", 11))

    def _draw_marker(self):
        s, ox, oy, dw, dh = self._view
        x0, y0, x1, y1 = self._roi_screen()

        # Dim everything outside the ROI so the taught region reads at a glance.
        for box in ((ox, oy, ox + dw, y0), (ox, y1, ox + dw, oy + dh),
                    (ox, y0, x0, y1), (x1, y0, ox + dw, y1)):
            if box[2] > box[0] and box[3] > box[1]:
                self.create_rectangle(*box, fill="#000000", outline="",
                                      stipple="gray50")

        self.create_rectangle(x0, y0, x1, y1, outline=self._accent, width=2)

        # Corner arms read as a machine-vision reticle rather than a plain box.
        arm = min(18, max(6, int((x1 - x0) // 4)), max(6, int((y1 - y0) // 4)))
        for cx, cy, sx, sy in ((x0, y0, 1, 1), (x1, y0, -1, 1),
                               (x0, y1, 1, -1), (x1, y1, -1, -1)):
            self.create_line(cx, cy, cx + arm * sx, cy, fill=self._accent, width=4)
            self.create_line(cx, cy, cx, cy + arm * sy, fill=self._accent, width=4)

        if self._editable and self._locked_size is None:
            for _, hx, hy in self._handles(x0, y0, x1, y1):
                self.create_rectangle(hx - self.HANDLE, hy - self.HANDLE,
                                      hx + self.HANDLE, hy + self.HANDLE,
                                      fill=self._accent, outline=IMG_BG)

        label = "%d x %d px" % (self._roi["width"], self._roi["height"])
        ly = y0 - 11 if y0 - 11 > oy + 8 else y1 + 12
        tid = self.create_text(x0 + 2, ly, text=label, fill=IMG_BG, anchor="w",
                               font=("Consolas", 11, "bold"))
        bx0, by0, bx1, by1 = self.bbox(tid)
        rid = self.create_rectangle(bx0 - 4, by0 - 2, bx1 + 4, by1 + 2,
                                    fill=self._accent, outline="")
        self.tag_raise(tid, rid)

    @staticmethod
    def _handles(x0, y0, x1, y1):
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        return (("nw", x0, y0), ("n", mx, y0), ("ne", x1, y0),
                ("w", x0, my), ("e", x1, my),
                ("sw", x0, y1), ("s", mx, y1), ("se", x1, y1))

    # -- hit testing --------------------------------------------------------

    def _hit(self, sx, sy):
        """'nw'..'se' for a handle, 'move' inside the box, or None."""
        if not self._roi or self._view is None:
            return None
        x0, y0, x1, y1 = self._roi_screen()
        if self._locked_size is None:
            for name, hx, hy in self._handles(x0, y0, x1, y1):
                if abs(sx - hx) <= self.GRAB and abs(sy - hy) <= self.GRAB:
                    return name
        if x0 <= sx <= x1 and y0 <= sy <= y1:
            return "move"
        return None

    def _on_hover(self, event):
        if not self._editable or self._drag:
            return
        self.config(cursor=self._CURSORS.get(self._hit(event.x, event.y), "crosshair"))

    # -- interaction --------------------------------------------------------

    def _on_press(self, event):
        if not self._editable or self._view is None:
            return
        self.focus_set()
        hit = self._hit(event.x, event.y)
        if hit:
            self._drag = {"mode": hit, "roi0": dict(self._roi),
                          "origin": self._to_image(event.x, event.y)}
        elif self._locked_size is not None:
            # Locked: drop the box where they pointed and let the same gesture
            # nudge it, rather than making them draw a box that cannot resize.
            pt = self._to_image(event.x, event.y)
            self._roi = self._place_locked(*pt)
            self._drag = {"mode": "move", "roi0": dict(self._roi), "origin": pt}
            self._redraw()
            self._notify()
        else:
            anchor = self._to_image(event.x, event.y)
            self._drag = {"mode": "new", "anchor": anchor, "cursor": anchor}
            self._roi = None
            self._redraw()

    def _on_drag(self, event):
        if not self._drag:
            return
        pt = self._to_image(event.x, event.y)
        mode = self._drag["mode"]

        if mode == "new":
            self._drag["cursor"] = pt
        elif mode == "move":
            r0 = self._drag["roi0"]
            ox_, oy_ = self._drag["origin"]
            ih, iw = self._image.shape[:2]
            nx = min(max(r0["x"] + pt[0] - ox_, 0), iw - r0["width"])
            ny = min(max(r0["y"] + pt[1] - oy_, 0), ih - r0["height"])
            self._roi = {"x": nx, "y": ny,
                         "width": r0["width"], "height": r0["height"]}
        else:
            self._roi = self._resized(self._drag["roi0"], mode, pt)

        self._redraw()
        if mode != "new":
            self._notify(final=False)

    def _resized(self, r0, mode, pt):
        left, top = r0["x"], r0["y"]
        right, bottom = r0["x"] + r0["width"], r0["y"] + r0["height"]
        px, py = pt
        if "n" in mode:
            top = min(py, bottom - self.MIN_ROI)
        if "s" in mode:
            bottom = max(py, top + self.MIN_ROI)
        if "w" in mode:
            left = min(px, right - self.MIN_ROI)
        if "e" in mode:
            right = max(px, left + self.MIN_ROI)
        return {"x": int(left), "y": int(top),
                "width": int(right - left), "height": int(bottom - top)}

    def _on_release(self, event):
        if not self._drag:
            return
        if self._drag["mode"] == "new":
            (ax, ay), (bx, by) = self._drag["anchor"], self._drag["cursor"]
            x, y = min(ax, bx), min(ay, by)
            w, h = abs(bx - ax), abs(by - ay)
            self._roi = ({"x": x, "y": y, "width": w, "height": h}
                         if w >= self.MIN_ROI and h >= self.MIN_ROI else None)
        self._drag = None
        self._redraw()
        self._notify()

    def _notify(self, final=True):
        """`final` is False for the intermediate states of a drag, so listeners
        can keep cheap readouts live but defer expensive redraws to the release."""
        if self._on_change:
            self._on_change(self.get_roi(), final)


# ═══════════════════════════════════════════════════════════════════════════════
# Page
# ═══════════════════════════════════════════════════════════════════════════════

_ORPHAN = "!unmapped:"


class VisionPage(tk.Frame):
    """The Vision page as a widget for the app's content area."""

    def __init__(self, master):
        super().__init__(master, bg=BG)
        render(self)


def render(parent):
    """Build the Vision page inside `parent`."""
    from vision_engine.vision_controller import (
        VisionController, load_vision_config, save_vision_config,
        DEFAULT_MATCH_THRESHOLD,
    )

    v_cfg = load_vision_config()
    ctrl = VisionController()
    alive = {"page": True}

    style = ttk.Style()
    style.configure("Vis.Horizontal.TScale", background=PANEL, troughcolor=theme.L_SURFACE_2,
                    bordercolor=LINE, darkcolor=ACCENT, lightcolor=ACCENT)

    content = tk.Frame(parent, bg=BG)
    content.pack(fill="both", expand=True)
    content.columnconfigure(0, weight=1)
    content.rowconfigure(2, weight=1)

    # ── Header ─────────────────────────────────────────────────────────────
    header = tk.Frame(content, bg=BG)
    header.grid(row=0, column=0, sticky="ew", pady=(0, 12))

    title_box = tk.Frame(header, bg=BG)
    title_box.pack(side="left")
    tk.Label(title_box, text="Vision Inspection", bg=BG, fg=TXT,
             font=("Arial", 17, "bold")).pack(anchor="w")
    tk.Label(title_box, text="Object detection by template matching "
                             "(normalised cross-correlation)",
             bg=BG, fg=TXT_FAINT, font=("Arial", 11)).pack(anchor="w")

    pill = tk.Frame(header, bg=CHIP, padx=12, pady=7, highlightbackground=LINE,
                    highlightthickness=1)
    pill.pack(side="right")
    pill_dot = tk.Label(pill, text="●", bg=CHIP, fg=TXT_FAINT, font=("Arial", 14))
    pill_dot.pack(side="left", padx=(0, 7))
    pill_txt = tk.Label(pill, text="Checking camera…", bg=CHIP, fg=TXT_DIM,
                        font=("Arial", 11, "bold"))
    pill_txt.pack(side="left")

    if not _cv2_ok or not _pil_ok:
        missing = " and ".join(n for n, ok in
                               (("opencv-python", _cv2_ok), ("Pillow", _pil_ok)) if not ok)
        bar = tk.Frame(content, bg=theme.L_WARN_BG)
        bar.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        tk.Label(bar, text="  %s is not installed — vision is unavailable until it is."
                           % missing, bg=theme.L_WARN_BG, fg=WARN,
                 font=("Arial", 11, "bold"), pady=6).pack(anchor="w")

    # ── Body: parts table + right rail ─────────────────────────────────────
    body = tk.Frame(content, bg=BG)
    body.grid(row=2, column=0, sticky="nsew")
    body.columnconfigure(0, weight=1)
    body.rowconfigure(0, weight=1)

    parts_card = _card(body, "Taught Models",
                       "each model maps to one template dataset")
    parts_card.grid(row=0, column=0, sticky="nsew", padx=(0, 12))

    rail = tk.Frame(body, bg=BG, width=310)
    rail.grid(row=0, column=1, sticky="ns")
    rail.grid_propagate(False)

    # ── Parts table ────────────────────────────────────────────────────────
    pb = parts_card.body
    toolbar = tk.Frame(pb, bg=PANEL)
    toolbar.pack(fill="x", pady=(0, 10))

    table_wrap = tk.Frame(pb, bg=LINE)
    table_wrap.pack(fill="both", expand=True)

    cols = ("part", "file", "objects", "refs", "roi", "thresh", "taught", "status")
    heads = {"part": ("MODEL", 150, "w"), "file": ("MODEL FILE", 150, "w"),
             "objects": ("OBJECTS", 70, "center"),
             "refs": ("REFS", 55, "center"), "roi": ("TEMPLATE", 100, "center"),
             "thresh": ("THRESHOLD", 90, "center"),
             "taught": ("TAUGHT", 145, "center"), "status": ("STATUS", 120, "w")}

    tree = ttk.Treeview(table_wrap, columns=cols, show="headings",
                        style="L.Treeview", selectmode="browse")
    sb = ttk.Scrollbar(table_wrap, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=sb.set)
    sb.pack(side="right", fill="y", padx=(0, 1), pady=1)
    tree.pack(fill="both", expand=True, padx=1, pady=1)

    for c in cols:
        text, width, anchor = heads[c]
        tree.heading(c, text=text)
        tree.column(c, width=width, anchor=anchor,
                    stretch=(c in ("part", "file", "status")))

    tree.tag_configure("ready", foreground=TXT)
    tree.tag_configure("problem", foreground=WARN)
    tree.tag_configure("empty", foreground=TXT_FAINT)

    # Contextual strip under the table: explains the selected row and carries
    # the one action that only makes sense for unmapped files.
    detail = tk.Frame(pb, bg=PANEL, height=26)
    detail.pack(fill="x", pady=(8, 0))
    detail.pack_propagate(False)
    detail_lbl = tk.Label(detail, text="", bg=PANEL, fg=TXT_FAINT,
                          font=("Arial", 11), anchor="w")
    detail_lbl.pack(side="left", fill="x", expand=True)
    btn_map = _btn(detail, "Map to Part…", BTN_PRIMARY, pady=3, font_size=10)

    def _refresh_table(select=None):
        if not alive["page"]:
            return
        remembered = select or (tree.selection()[0] if tree.selection() else None)
        tree.delete(*tree.get_children())
        ctrl.reload_config()

        mapped = ctrl.get_mapped_parts()
        mapped_files = set()
        for pno, filename in sorted(mapped.items()):
            mapped_files.add(filename)
            info = ctrl.model_info(pno)
            if info is None:
                tree.insert("", "end", iid=pno, tags=("problem",),
                            values=(pno, filename, "—", "—", "—", "—", "—",
                                    "FILE MISSING"))
            else:
                tw, th = info["template_size"]
                sizes = ("%d x %d" % (tw, th) if info["uniform_templates"]
                         else "varied (%d)" % len(set(info["template_sizes"])))
                # A threshold this low passes almost any frame, so the part looks
                # guarded while nothing is really being checked. Say so.
                weak = info["threshold"] < 0.55
                tree.insert("", "end", iid=pno, tags=("problem" if weak else "ready",),
                            values=(pno, filename, info["objects"],
                                    info["references"],
                                    sizes,
                                    "%.2f" % info["threshold"],
                                    str(info["created"]).replace("T", "  "),
                                    "Threshold too low" if weak else "Ready"))

        # Files on disk that no part number resolves to. Production cannot reach
        # these, so surface them rather than letting them look installed.
        if os.path.isdir(_MODELS_DIR):
            for f in sorted(os.listdir(_MODELS_DIR)):
                if f.endswith(".npz") and f not in mapped_files:
                    tree.insert("", "end", iid=_ORPHAN + f, tags=("problem",),
                                values=("—", f, "—", "—", "—", "—", "—", "NOT MAPPED"))

        if not tree.get_children():
            tree.insert("", "end", iid="!none", tags=("empty",),
                        values=("—", "No models taught yet", "", "", "", "", "",
                                "Start with “Teach New Model”"))

        if remembered and tree.exists(remembered):
            tree.selection_set(remembered)
            tree.see(remembered)
        _on_select()
        _refresh_coverage()

    def _selection():
        """(kind, value) where kind is 'part', 'orphan' or None."""
        sel = tree.selection()
        if not sel or sel[0] == "!none":
            return None, None
        if sel[0].startswith(_ORPHAN):
            return "orphan", sel[0][len(_ORPHAN):]
        return "part", sel[0]

    # ── Right rail: camera ─────────────────────────────────────────────────
    cam_card = _card(rail, "Camera")
    cam_card.pack(fill="x")
    cb = cam_card.body
    cam_device = _kv_row(cb, "Device", "—", mono=True)
    cam_res = _kv_row(cb, "Resolution", "—", mono=True)
    cam_state = _kv_row(cb, "State", "Checking…", value_fg=TXT_DIM)
    cam2_row = _kv_row(cb, "Camera 2", "—", mono=True)

    cam_btns = tk.Frame(cb, bg=PANEL)
    cam_btns.pack(fill="x", pady=(10, 0))
    btn_cam_cfg = _btn(cam_btns, "Configure…", BTN_NEUTRAL)
    btn_cam_cfg.pack(side="left")
    btn_cam_check = _btn(cam_btns, "Re-check", BTN_NEUTRAL)
    btn_cam_check.pack(side="left", padx=(6, 0))

    def _paint_camera(state_text, color, dot_color=None):
        if not alive["page"]:
            return
        cam_state.config(text=state_text, fg=color)
        pill_txt.config(text=state_text, fg=color)
        pill_dot.config(fg=dot_color or color)

    def _refresh_camera():
        cam = _load_cam_cfg()
        idx, w, h = cam["index"], cam["width"], cam["height"]
        configured = cam["enabled"] and idx >= 0
        cam_device.config(text=("Camera %d" % idx) if configured else "Not configured",
                          fg=TXT if configured else TXT_FAINT)
        flips = [t for t, on in (("flipped", cam["flip"]), ("mirrored", cam["mirror"])) if on]
        cam_res.config(text="  ·  ".join(["%d x %d" % (w, h)] + flips)
                       if configured else "—",
                       fg=TXT if configured else TXT_FAINT)
        cam2 = _load_cam_cfg(2)
        on2 = cam2["enabled"] and cam2["index"] >= 0
        cam2_row.config(text=("Camera %d  ·  %d x %d" % (cam2["index"], cam2["width"], cam2["height"]))
                        if on2 else "Not configured",
                        fg=TXT if on2 else TXT_FAINT)

        if not _cv2_ok:
            _paint_camera("OpenCV missing", NG_RED)
            return
        if not configured:
            _paint_camera("No camera configured", WARN)
            return
        _paint_camera("Checking camera…", TXT_DIM)

        def _work():
            try:
                status = ctrl.get_status()
            except Exception:
                status = "CAMERA_ERROR"
            text, color = {
                "READY": ("Camera %d ready" % idx, OK_GREEN),
                "NO_CAMERA": ("No camera configured", WARN),
            }.get(status, ("Camera %d not responding" % idx, NG_RED))
            try:
                if alive["page"]:
                    parent.after(0, lambda: _paint_camera(text, color))
            except Exception:
                pass

        threading.Thread(target=_work, daemon=True).start()

    btn_cam_check.config(command=lambda: _refresh_camera())

    def _configure_camera():
        if _open_camera_dialog(parent):
            _refresh_camera()

    btn_cam_cfg.config(command=_configure_camera)

    # ── Right rail: part coverage ────────────────────────────────────────────
    # The parts table above audits datasets — files that exist and whether
    # they're wired up. This audits the other direction: real parts in the
    # master that the line will build with no vision dataset at all, which is
    # the actual production exposure, not a stray file on disk.
    cov_card = _card(rail, "Model Coverage", "models with no vision dataset")
    cov_card.pack(fill="x", pady=(12, 0))
    cvb = cov_card.body
    cov_summary = tk.Label(cvb, text="Checking…", bg=PANEL, fg=TXT_DIM,
                           font=("Arial", 11, "bold"), anchor="w")
    cov_summary.pack(fill="x")
    cov_list = tk.Listbox(cvb, bg=PANEL_ALT, fg=WARN, font=("Consolas", 11),
                          bd=0, relief="flat", highlightthickness=0, height=6,
                          selectmode="browse", activestyle="none")
    cov_list.pack(fill="x", pady=(6, 0))

    def _refresh_coverage():
        if not alive["page"]:
            return
        master_parts = _fetch_master_parts()
        cov_list.delete(0, "end")
        if master_parts is None:
            cov_summary.config(text="Model master unreachable", fg=WARN)
            cov_list.insert("end", "  Could not reach the model master.")
            return
        if not master_parts:
            cov_summary.config(text="No models in the master yet", fg=TXT_FAINT)
            return
        missing = [pno for pno, _ in master_parts if not ctrl.has_vision(pno)]
        covered = len(master_parts) - len(missing)
        cov_summary.config(text="%d of %d models have vision" % (covered, len(master_parts)),
                           fg=OK_GREEN if not missing else WARN)
        for pno in missing:
            cov_list.insert("end", "  " + pno)

    # ── Right rail: inspection settings ────────────────────────────────────
    insp_card = _card(rail, "Inspection")
    insp_card.pack(fill="x", pady=(12, 0))
    ib = insp_card.body

    enabled_var = tk.BooleanVar(value=v_cfg.get("vision_enabled", True))
    thresh_var = tk.DoubleVar(
        value=float(v_cfg.get("match_threshold", DEFAULT_MATCH_THRESHOLD)))
    initial = (enabled_var.get(), round(thresh_var.get(), 2))

    def _toggle_vision_enabled():
        """Applied on the spot rather than left for Save: switching vision off
        takes the camera out of the verdict for every model, so it is not left
        pending to be carried in later with an unrelated edit. (The page itself
        needs a sign-in, recorded in the login history.)"""
        nonlocal initial
        want = enabled_var.get()
        v_cfg["vision_enabled"] = want
        save_vision_config(v_cfg)
        ctrl.reload_config()
        initial = (want, initial[1])
        _set_btn_enabled(btn_save, round(thresh_var.get(), 2) != initial[1])
        dirty_lbl.config(text="Vision check " + ("ENABLED" if want else "DISABLED"),
                         fg=OK_GREEN if want else WARN)
        # Hand the strip back to the threshold's dirty state once the
        # confirmation has been read, instead of blanking it over an edit that
        # really is still unsaved.
        parent.after(2400, lambda: _on_settings_change() if alive["page"] else None)

    chk = tk.Checkbutton(ib, text="  Vision enabled", variable=enabled_var,
                         bg=PANEL, fg=TXT, selectcolor=FIELD, activebackground=PANEL,
                         activeforeground=TXT, font=("Arial", 12, "bold"),
                         bd=0, highlightthickness=0, anchor="w", cursor="hand2",
                         command=_toggle_vision_enabled)
    chk.pack(fill="x")
    tk.Label(ib, text="When off, the test cycle skips vision entirely. "
                      "Changing it is saved at once.",
             bg=PANEL, fg=TXT_FAINT, font=("Arial", 10), anchor="w",
             wraplength=265, justify="left").pack(fill="x", padx=(22, 0), pady=(0, 12))

    tk.Frame(ib, bg=LINE, height=1).pack(fill="x", pady=(0, 12))

    th_head = tk.Frame(ib, bg=PANEL)
    th_head.pack(fill="x")
    tk.Label(th_head, text="Default match threshold", bg=PANEL, fg=TXT,
             font=("Arial", 11, "bold")).pack(side="left")
    th_val = tk.Label(th_head, text="0.75", bg=PANEL, fg=ACCENT,
                      font=("Consolas", 14, "bold"))
    th_val.pack(side="right")

    scale = ttk.Scale(ib, from_=0.40, to=0.99, orient="horizontal",
                      variable=thresh_var, style="Vis.Horizontal.TScale")
    scale.pack(fill="x", pady=(6, 2))

    ticks = tk.Frame(ib, bg=PANEL)
    ticks.pack(fill="x")
    tk.Label(ticks, text="lenient", bg=PANEL, fg=TXT_FAINT,
             font=("Arial", 10)).pack(side="left")
    tk.Label(ticks, text="strict", bg=PANEL, fg=TXT_FAINT,
             font=("Arial", 10)).pack(side="right")

    th_caption = tk.Label(ib, text="", bg=PANEL, fg=TXT_DIM, font=("Arial", 10),
                          wraplength=265, justify="left", anchor="w")
    th_caption.pack(fill="x", pady=(8, 0))

    tk.Label(ib, text="Applies to models taught from now on. Each taught model keeps "
                      "the threshold it was saved with — change one from the table.",
             bg=PANEL, fg=TXT_FAINT, font=("Arial", 10), wraplength=265,
             justify="left", anchor="w").pack(fill="x", pady=(8, 0))

    # ── Footer ─────────────────────────────────────────────────────────────
    footer = tk.Frame(content, bg=BG)
    footer.grid(row=3, column=0, sticky="ew", pady=(12, 0))
    dirty_lbl = tk.Label(footer, text="", bg=BG, fg=ACCENT, font=("Arial", 11, "bold"))
    dirty_lbl.pack(side="right", padx=(0, 12))
    btn_save = _btn(footer, "Save Settings", BTN_PRIMARY, font_size=12, pady=8)
    btn_save.pack(side="right")

    def _on_settings_change(*_a):
        val = round(thresh_var.get(), 2)
        th_val.config(text="%.2f" % val)
        caption, color = _threshold_caption(val)
        th_caption.config(text=caption, fg=color)
        changed = (enabled_var.get(), val) != initial
        dirty_lbl.config(text="Unsaved changes" if changed else "", fg=ACCENT)
        _set_btn_enabled(btn_save, changed)

    thresh_var.trace_add("write", _on_settings_change)
    enabled_var.trace_add("write", _on_settings_change)

    def _save_settings():
        nonlocal initial
        val = round(thresh_var.get(), 2)
        v_cfg["vision_enabled"] = enabled_var.get()
        v_cfg["match_threshold"] = val
        save_vision_config(v_cfg)
        ctrl.reload_config()
        initial = (enabled_var.get(), val)
        _set_btn_enabled(btn_save, False)
        dirty_lbl.config(text="Saved", fg=OK_GREEN)
        parent.after(1800, lambda: dirty_lbl.config(text="", fg=ACCENT)
                     if alive["page"] else None)

    btn_save.config(command=_save_settings)
    _on_settings_change()

    # ── Row actions ────────────────────────────────────────────────────────

    def _teach(part_number=None):
        cam = _load_cam_cfg()
        saved = _open_teach_wizard(parent, cam, part_number)
        if saved:
            _refresh_table(select=saved)

    def _reteach():
        kind, value = _selection()
        if kind == "part":
            _teach(value)

    def _run_test():
        kind, value = _selection()
        if kind == "part" and ctrl.has_vision(value):
            _open_test_dialog(parent, ctrl, value, on_changed=_refresh_table)

    def _set_threshold():
        kind, value = _selection()
        if kind != "part":
            return
        info = ctrl.model_info(value)
        if info is None:
            return
        if _open_threshold_dialog(parent, ctrl, value, info["threshold"]):
            _refresh_table(select=value)

    def _map_orphan():
        kind, filename = _selection()
        if kind != "orphan":
            return
        pno = _prompt_part_number(
            parent, "Map Model File",
            "Model that should use “%s”:" % filename,
            taken=set(ctrl.get_mapped_parts()))
        if not pno:
            return
        try:
            ctrl.map_model_file(pno, filename)
        except ValueError as e:
            messagebox.showerror("Map Model", str(e), parent=parent)
            return
        _refresh_table(select=pno)

    def _delete():
        kind, value = _selection()
        if kind == "orphan":
            if not messagebox.askyesno(
                    "Delete Model File",
                    "Permanently delete the unmapped file “%s”?" % value,
                    parent=parent):
                return
            try:
                os.remove(os.path.join(_MODELS_DIR, value))
            except OSError as e:
                messagebox.showerror("Delete", str(e), parent=parent)
                return
        elif kind == "part" and ctrl.has_model(value):
            if not messagebox.askyesno(
                    "Delete Dataset",
                    "Delete the vision dataset for model “%s”?\n\n"
                    "The model will no longer be checked by vision." % value,
                    parent=parent):
                return
            ctrl.delete_model(value)
        else:
            return
        _refresh_table()

    btn_teach = _btn(toolbar, "+  Teach New Model", BTN_SUCCESS,
                     command=lambda: _teach(None), pady=7)
    btn_teach.pack(side="left")
    btn_reteach = _btn(toolbar, "Re-teach", BTN_NEUTRAL, command=_reteach, pady=7)
    btn_reteach.pack(side="left", padx=(8, 0))
    btn_test = _btn(toolbar, "Run Test", BTN_PRIMARY, command=_run_test, pady=7)
    btn_test.pack(side="left", padx=(8, 0))
    btn_thresh = _btn(toolbar, "Threshold…", BTN_NEUTRAL, command=_set_threshold, pady=7)
    btn_thresh.pack(side="left", padx=(8, 0))
    btn_del = _btn(toolbar, "Delete", BTN_DANGER, command=_delete, pady=7)
    btn_del.pack(side="left", padx=(8, 0))
    btn_map.config(command=_map_orphan)

    def _on_select(event=None):
        kind, value = _selection()
        is_part = kind == "part"
        info = ctrl.model_info(value) if is_part else None
        usable = info is not None
        for b, on in ((btn_reteach, is_part), (btn_test, is_part and ctrl.has_vision(value)),
                      (btn_thresh, usable), (btn_del, kind is not None)):
            _set_btn_enabled(b, on)

        if kind == "orphan":
            detail_lbl.config(
                text="Not mapped to any model — inspection cannot use this file.",
                fg=WARN)
            btn_map.pack(side="right")
        else:
            btn_map.pack_forget()
            if is_part and not usable:
                detail_lbl.config(
                    text="Mapped file is missing from vision_models — re-teach this model.",
                    fg=WARN)
            elif usable and info["threshold"] < 0.55:
                detail_lbl.config(
                    text="This model passes at a %.2f match — near enough to accept any "
                         "frame. Raise it with “Threshold…”." % info["threshold"],
                    fg=WARN)
            elif is_part:
                detail_lbl.config(
                    text="Run Test captures a live frame and judges it exactly as "
                         "inspection does.", fg=TXT_FAINT)
            else:
                detail_lbl.config(text="", fg=TXT_FAINT)

    tree.bind("<<TreeviewSelect>>", _on_select)
    tree.bind("<Double-1>", lambda e: _run_test())
    tree.bind("<Delete>", lambda e: _delete())

    # ── Teardown ───────────────────────────────────────────────────────────
    def _on_destroy(event):
        if event.widget is content:
            alive["page"] = False

    content.bind("<Destroy>", _on_destroy)

    _refresh_table()
    _refresh_camera()


# ═══════════════════════════════════════════════════════════════════════════════
# Teach wizard
# ═══════════════════════════════════════════════════════════════════════════════

MIN_REFS = 3
MAX_REFS = 12
# One colour per object, used for its box in the wizard and in test results.
OBJECT_COLORS = (IMG_OK, "#40c4ff", "#ff80ab")


def _dialog(parent, title, width, height, x=None, y=None):
    """Modal toplevel, centred on the app window (or at x, y), styled like the
    page."""
    win = tk.Toplevel(parent)
    # Hidden until it has its size and place: the update_idletasks below would
    # otherwise map it at Tk's default 200x200 first, a small window flashing
    # up and vanishing before the real one appears.
    win.withdraw()
    win.title(title)
    win.configure(bg=BG)
    win.transient(parent.winfo_toplevel())
    win.resizable(True, True)
    root = parent.winfo_toplevel()
    root.update_idletasks()
    if x is None:
        x = root.winfo_rootx() + (root.winfo_width() - width) // 2
    if y is None:
        y = root.winfo_rooty() + (root.winfo_height() - height) // 3
    win.geometry("%dx%d+%d+%d" % (width, height, max(x, 0), max(y, 0)))
    win.deiconify()
    win.grab_set()
    return win


def _work_area(widget):
    """(x, y, width, height) of the screen minus the taskbar. Falls back to the
    whole screen less a taskbar's worth where Windows can't be asked."""
    try:
        import ctypes
        from ctypes import wintypes
        rect = wintypes.RECT()
        # SPI_GETWORKAREA
        if ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(rect), 0):
            # On this rig Windows reports the whole screen as the work area,
            # taskbar included, so always keep a taskbar's height clear.
            h = min(rect.bottom - rect.top, widget.winfo_screenheight() - 48)
            return rect.left, rect.top, rect.right - rect.left, h
    except Exception:
        pass
    return 0, 0, widget.winfo_screenwidth(), widget.winfo_screenheight() - 48


def _dialog_header(win, title, subtitle, compact=False):
    """Title strip. `compact` puts the subtitle beside the title on one slim
    line, for a dialog whose every spare pixel of height goes to an image."""
    bar = tk.Frame(win, bg=PANEL)
    bar.pack(fill="x")
    inner = tk.Frame(bar, bg=PANEL)
    inner.pack(fill="x", padx=18, pady=3 if compact else 12)
    side = "left" if compact else "top"
    tk.Label(inner, text=title, bg=PANEL, fg=TXT,
             font=("Arial", 14 if compact else 15, "bold")).pack(side=side, anchor="w")
    tk.Label(inner, text=subtitle, bg=PANEL, fg=TXT_FAINT,
             font=("Arial", 11)).pack(side=side, anchor="w",
                                      padx=(14, 0) if compact else 0)
    tk.Frame(win, bg=LINE, height=1).pack(fill="x")
    return bar


def _step(parent, number, title):
    """Numbered step block in the wizard rail. Returns its body frame."""
    wrap = tk.Frame(parent, bg=BG)
    wrap.pack(fill="x", pady=(0, 14))
    head = tk.Frame(wrap, bg=BG)
    head.pack(fill="x")
    badge = tk.Label(head, text=str(number), bg=LINE, fg=TXT,
                     font=("Arial", 11, "bold"), width=3)
    badge.pack(side="left")
    tk.Label(head, text=title, bg=BG, fg=TXT,
             font=("Arial", 12, "bold")).pack(side="left", padx=(8, 0))
    body = tk.Frame(wrap, bg=BG)
    body.pack(fill="x", padx=(34, 0), pady=(6, 0))
    wrap.badge = badge
    return body, badge


def _open_teach_wizard(parent, cam, part_number=None):
    """Teach or re-teach one part. Returns the saved part number, or None."""
    if not _cv2_ok or not _pil_ok:
        messagebox.showerror("Vision", "OpenCV and Pillow are required to teach a model.",
                             parent=parent)
        return None

    from vision_engine.vision_controller import (
        VisionController, DEFAULT_MATCH_THRESHOLD, MAX_OBJECTS)
    from vision_engine import camera

    ctrl = VisionController()
    existing_parts = set(ctrl.get_mapped_parts())
    reteach = part_number is not None
    # Re-teaching keeps whatever threshold the part was tuned to; only a brand
    # new part inherits the page default.
    info = ctrl.model_info(part_number) if reteach else None
    threshold = (info or {}).get(
        "threshold", ctrl.config.get("match_threshold", DEFAULT_MATCH_THRESHOLD))

    # As large as fits comfortably: the image is where every box is drawn, and
    # the more pixels it gets the more precisely a box lands on the same spot
    # of the part in every reference. Not maximised -- that left Save Dataset
    # under the taskbar -- but sized inside the work area with room to spare.
    wx, wy, ww, wh = _work_area(parent)
    tw, th = max(900, int(ww * 0.92)), max(600, wh - 90)
    win = _dialog(parent, "Teach Model", tw, th, wx + (ww - tw) // 2, wy + 10)
    _dialog_header(
        win,
        "Re-teach “%s”" % part_number if reteach else "Teach New Model",
        "Capture the good part a few times, then box it on every reference.",
        compact=True)

    alive = {"v": True}
    refs = []                       # [{"img", "label", "thumb", "rois"}]
    sel = {"i": None}
    # A part is checked as 1..MAX_OBJECTS objects. Every reference carries one
    # box slot per possible object; only the first `n` count. `k` is the object
    # being boxed right now.
    obj = {"n": max(1, (info or {}).get("objects", 1)), "k": 0}
    old_names = (info or {}).get("object_names", [])
    object_names = [tk.StringVar(value=old_names[k] if k < len(old_names)
                                and old_names[k] != "Object %d" % (k + 1) else "")
                   for k in range(MAX_OBJECTS)]

    def _object_name(k):
        return object_names[k].get().strip() or "Object %d" % (k + 1)
    live = {"on": False}
    stream = {"s": None}
    ref_size = {"wh": None}

    # ── Footer: checklist + actions ────────────────────────────────────────
    # Packed before the body: the packer serves slaves in packing order, so a
    # body packed first claims the height it wants and leaves the footer with
    # the remainder — which collapsed these buttons to a sliver as soon as the
    # reference strip grew. Claiming the footer's space up front keeps them
    # whole no matter how many references are loaded.
    foot = tk.Frame(win, bg=PANEL)
    foot.pack(side="bottom", fill="x")
    tk.Frame(win, bg=LINE, height=1).pack(side="bottom", fill="x")
    foot_in = tk.Frame(foot, bg=PANEL)
    foot_in.pack(fill="x", padx=18, pady=5)

    checklist = tk.Label(foot_in, text="", bg=PANEL, fg=TXT_DIM, font=("Consolas", 11),
                         anchor="w", justify="left")
    checklist.pack(side="left")

    btn_save = _btn(foot_in, "Save Dataset", BTN_SUCCESS, font_size=12, pady=4)
    btn_save.pack(side="right")
    btn_cancel = _btn(foot_in, "Cancel", BTN_NEUTRAL, font_size=12, pady=4)
    btn_cancel.pack(side="right", padx=(0, 8))

    body = tk.Frame(win, bg=BG)
    body.pack(fill="both", expand=True, padx=10, pady=4)
    body.columnconfigure(0, weight=1)
    body.rowconfigure(0, weight=1)

    # ── Left: image view + view toolbar ────────────────────────────────────
    left = tk.Frame(body, bg=BG)
    left.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
    left.rowconfigure(0, weight=1)
    left.columnconfigure(0, weight=1)

    view_wrap = tk.Frame(left, bg=LINE)
    view_wrap.grid(row=0, column=0, sticky="nsew")
    view = RoiView(view_wrap, on_change=lambda r, final: _roi_changed(r, final))
    view.pack(fill="both", expand=True, padx=1, pady=1)

    view_bar = tk.Frame(left, bg=BG)
    view_bar.grid(row=1, column=0, sticky="ew", pady=(4, 0))
    frame_lbl = tk.Label(view_bar, text="", bg=BG, fg=TXT_DIM, font=("Consolas", 11))
    frame_lbl.pack(side="right")
    btn_live = _btn(view_bar, "Live View", BTN_NEUTRAL)
    btn_capture = _btn(view_bar, "◉  Capture Frame", BTN_SUCCESS)
    btn_import = _btn(view_bar, "Import Files…", BTN_NEUTRAL)

    # ── Right: steps ───────────────────────────────────────────────────────
    # Scrollable: at a full dozen references the thumbnail and crop strips are
    # taller than the window, and the crops are the one place an operator can
    # see that a box missed the part — they have to stay reachable.
    # 356 = the 34px step indent + a 4-wide thumbnail strip (304px) + the
    # scrollbar, so the fourth column isn't sliced off.
    rail_wrap = tk.Frame(body, bg=BG, width=356)
    rail_wrap.grid(row=0, column=1, sticky="ns")
    # pack_propagate, not grid_propagate: the steps inside are packed, so the
    # grid call was a no-op and the rail grew with every reference added.
    rail_wrap.pack_propagate(False)

    rail_canvas = tk.Canvas(rail_wrap, bg=BG, highlightthickness=0, bd=0)
    rail_sb = ttk.Scrollbar(rail_wrap, orient="vertical", command=rail_canvas.yview)
    rail_canvas.configure(yscrollcommand=rail_sb.set)
    rail_sb.pack(side="right", fill="y")
    rail_canvas.pack(side="left", fill="both", expand=True)

    rail = tk.Frame(rail_canvas, bg=BG)
    rail_window = rail_canvas.create_window((0, 0), window=rail, anchor="nw")

    def _rail_resized(_event=None):
        rail_canvas.configure(scrollregion=rail_canvas.bbox("all"))
        rail_canvas.itemconfigure(rail_window, width=rail_canvas.winfo_width())

    rail.bind("<Configure>", _rail_resized)
    rail_canvas.bind("<Configure>", _rail_resized)

    def _rail_wheel(event):
        try:
            rail_canvas.yview_scroll(-1 * (event.delta // 120), "units")
        except Exception:
            pass

    # Bound while the pointer is over the rail rather than globally, so the
    # wheel keeps working normally everywhere else.
    rail_canvas.bind("<Enter>", lambda e: rail_canvas.bind_all("<MouseWheel>", _rail_wheel))
    rail_canvas.bind("<Leave>", lambda e: rail_canvas.unbind_all("<MouseWheel>"))

    s1, b1 = _step(rail, 1, "Model")
    master_parts = _fetch_master_parts()
    ent_pno = _PnoField(s1, master_parts)
    ent_pno.pack(fill="x", ipady=5)
    pno_note = tk.Label(s1, text="", bg=BG, fg=TXT_FAINT, font=("Arial", 10),
                        anchor="w", wraplength=270, justify="left")
    pno_note.pack(fill="x", pady=(4, 0))
    if master_parts is None:
        tk.Label(s1, text="Could not reach the model master — typed value won't "
                          "be checked against the model master.",
                 bg=BG, fg=WARN, font=("Arial", 10), wraplength=270,
                 justify="left", anchor="w").pack(fill="x", pady=(2, 0))
    if reteach:
        ent_pno.set(part_number)
        ent_pno.lock()
        pno_note.config(text="Saving replaces the existing dataset for this model.",
                        fg=WARN)
    else:
        ent_pno.focus_set()

    s2, b2 = _step(rail, 2, "Reference images")
    tk.Label(s2, text="Capture the same good part %d+ times — vary position and "
                      "lighting slightly, the way the line will." % MIN_REFS,
             bg=BG, fg=TXT_FAINT, font=("Arial", 10), wraplength=270,
             justify="left", anchor="w").pack(fill="x", pady=(0, 8))
    refs_count = tk.Label(s2, text="", bg=BG, fg=TXT_DIM, font=("Arial", 11, "bold"),
                          anchor="w")
    refs_count.pack(fill="x")
    thumbs = tk.Frame(s2, bg=BG)
    thumbs.pack(fill="x", pady=(6, 0))

    s_p, b_p = _step(rail, 3, "Objects to check")
    tk.Label(s_p, text="Check up to %d separate objects of this part — every one must "
                       "be found for an OK. Each is matched exactly as a whole part "
                       "is, at the same threshold." % MAX_OBJECTS,
             bg=BG, fg=TXT_FAINT, font=("Arial", 10), wraplength=270,
             justify="left", anchor="w").pack(fill="x", pady=(0, 8))
    count_row = tk.Frame(s_p, bg=BG)
    count_row.pack(fill="x")
    tk.Label(count_row, text="Objects", bg=BG, fg=TXT_DIM,
             font=("Arial", 11, "bold")).pack(side="left", padx=(0, 10))
    count_btns = []
    for n in range(1, MAX_OBJECTS + 1):
        b = _btn(count_row, str(n), BTN_NEUTRAL, pady=3, font_size=11, width=3,
                 command=lambda n=n: _set_object_count(n))
        b.pack(side="left", padx=(0, 4))
        count_btns.append(b)
    object_rows = tk.Frame(s_p, bg=BG)
    object_rows.pack(fill="x", pady=(8, 0))

    s3, b3 = _step(rail, 4, "Target box")
    tk.Label(s3, text="Box the object on every reference. The box carries over to the "
                      "next image — click to drop it on the object there.",
             bg=BG, fg=TXT_FAINT, font=("Arial", 10), wraplength=270,
             justify="left", anchor="w").pack(fill="x", pady=(0, 8))
    boxing_lbl = tk.Label(s3, text="", bg=BG, fg=TXT, font=("Arial", 11, "bold"),
                          anchor="w")
    boxing_lbl.pack(fill="x", pady=(0, 4))
    roi_row = tk.Frame(s3, bg=BG)
    roi_row.pack(fill="x")
    roi_lbl = tk.Label(roi_row, text="Not drawn", bg=BG, fg=WARN,
                       font=("Consolas", 12, "bold"), anchor="w")
    roi_lbl.pack(side="left")
    btn_clear_roi = _btn(roi_row, "Clear", BTN_NEUTRAL, pady=3, font_size=10,
                         command=lambda: view.clear_roi())
    btn_clear_roi.pack(side="right")

    lock_var = tk.BooleanVar(value=True)
    tk.Checkbutton(s3, text="  Same size on every reference", variable=lock_var,
                   bg=BG, fg=TXT_DIM, selectcolor=FIELD, activebackground=BG,
                   activeforeground=TXT, font=("Arial", 10), bd=0,
                   highlightthickness=0, anchor="w", cursor="hand2",
                   command=lambda: _apply_lock()).pack(fill="x", pady=(8, 0))
    tk.Label(s3, text="Matching is not scale-invariant, so crops of different sizes "
                      "are not directly comparable. Unlock only if the part changes "
                      "size between references.",
             bg=BG, fg=TXT_FAINT, font=("Arial", 10), wraplength=270,
             justify="left", anchor="w").pack(fill="x", pady=(2, 0))

    tk.Label(s3, text="TEMPLATES", bg=BG, fg=TXT_FAINT,
             font=("Arial", 10, "bold"), anchor="w").pack(fill="x", pady=(10, 2))
    tk.Label(s3, text="The actual pixels each reference contributes — a crop showing "
                      "background means that box missed the part.",
             bg=BG, fg=TXT_FAINT, font=("Arial", 10), wraplength=270,
             justify="left", anchor="w").pack(fill="x", pady=(0, 6))
    crops = tk.Frame(s3, bg=BG)
    crops.pack(fill="x")

    result = {"saved": None}

    # ── Behaviour ──────────────────────────────────────────────────────────

    def _complete(ref):
        """Every object in use is boxed on this reference."""
        return all(ref["rois"][k] for k in range(obj["n"]))

    def _gates():
        pno = ent_pno.get().strip()
        return {
            "Model": bool(pno),
            "%d+ references" % MIN_REFS: len(refs) >= MIN_REFS,
            ("Box on every reference" if obj["n"] == 1 else
             "Every object boxed"): bool(refs) and all(_complete(r) for r in refs),
        }

    def _refresh_gates(*_a):
        gates = _gates()
        checklist.config(text="   ".join(
            ("✓ " if ok else "○ ") + name for name, ok in gates.items()))
        _set_btn_enabled(btn_save, all(gates.values()))

        ok1, ok2, ok3 = gates.values()
        for badge, ok in ((b1, ok1), (b2, ok2), (b_p, True), (b3, ok3)):
            badge.config(bg=OK_GREEN if ok else LINE,
                         fg="white" if ok else TXT)
        _paint_object_rows()

        n = len(refs)
        refs_count.config(
            text="%d captured%s" % (n, "" if n >= MIN_REFS
                                    else "  ·  %d more needed" % (MIN_REFS - n)),
            fg=OK_GREEN if n >= MIN_REFS else WARN)

        if not reteach:
            pno = ent_pno.get().strip()
            if pno and pno in existing_parts:
                pno_note.config(text="“%s” is already taught — saving replaces it."
                                     % pno, fg=WARN)
            else:
                pno_note.config(text="", fg=TXT_FAINT)

    def _apply_lock():
        """Pin the box size to this object's first box drawn, unless the operator
        opts out. Each object has its own size -- a connector and a label are
        rarely the same shape."""
        k = obj["k"]
        first = next((r["rois"][k] for r in refs if r["rois"][k]), None)
        if lock_var.get() and first:
            view.lock_size((first["width"], first["height"]))
        else:
            view.lock_size(None)

    def _paint_overlays():
        """The other objects' boxes on this reference, so the operator can see
        what is already boxed while working on one object."""
        i = sel["i"]
        if i is None or not (0 <= i < len(refs)) or obj["n"] == 1:
            view.set_overlays([])
            return
        view.set_overlays([(refs[i]["rois"][k], OBJECT_COLORS[k], _object_name(k))
                           for k in range(obj["n"]) if k != obj["k"]])

    def _set_object(k):
        """Switch which object the view is boxing."""
        obj["k"] = k
        view.set_accent(OBJECT_COLORS[k])
        i = sel["i"]
        if i is not None and 0 <= i < len(refs):
            _apply_lock()
            view.set_roi(refs[i]["rois"][k], notify=False)
            _roi_changed(refs[i]["rois"][k])
        else:
            _paint_object_rows()
            _paint_crops()

    def _set_object_count(n):
        obj["n"] = n
        if obj["k"] >= n:
            _set_object(n - 1)
        # Seed any newly added object onto the current reference the same way
        # a new reference is seeded, so its box is ready to be dragged over.
        if sel["i"] is not None:
            _show_ref(sel["i"])
        else:
            _paint_thumbs()
            _paint_crops()
            _refresh_gates()

    # One row per possible object, built once and shown or hidden by the count:
    # rebuilding them on every refresh would pull a name box out from under
    # the operator mid-word.
    object_row_widgets = []
    for k in range(MAX_OBJECTS):
        row = tk.Frame(object_rows, bg=BG, cursor="hand2")
        inner = tk.Frame(row, bg=BG)
        inner.pack(fill="x", padx=1, pady=1)
        dot = tk.Label(inner, text="●", bg=BG, fg=OBJECT_COLORS[k],
                       font=("Arial", 13), cursor="hand2")
        dot.pack(side="left", padx=(6, 4))
        ent = tk.Entry(inner, textvariable=object_names[k], bg=FIELD, fg=TXT,
                       font=("Arial", 11), insertbackground=TXT, relief="flat",
                       width=14, highlightthickness=1, highlightbackground=LINE,
                       highlightcolor=OBJECT_COLORS[k])
        ent.pack(side="left", pady=4, ipady=2)
        count = tk.Label(inner, text="", bg=BG, font=("Arial", 10, "bold"))
        count.pack(side="right", padx=6)
        for w in (row, inner, dot, count):
            w.bind("<Button-1>", lambda e, k=k: _set_object(k))
        ent.bind("<FocusIn>", lambda e, k=k: _set_object(k) if obj["k"] != k else None)
        object_names[k].trace_add("write", lambda *_a: (_paint_object_rows(),
                                                        _paint_overlays()))
        object_row_widgets.append((row, inner, dot, count))

    def _paint_object_rows():
        n, active = obj["n"], obj["k"]
        for idx, b in enumerate(count_btns):
            b._colors = BTN_PRIMARY if idx + 1 == n else BTN_NEUTRAL
            b.config(bg=b._colors[0])
        boxing_lbl.config(text=("Boxing: %s" % _object_name(active)) if n > 1 else "",
                          fg=OBJECT_COLORS[active])
        for k, (row, inner, dot, count) in enumerate(object_row_widgets):
            if k >= n or n == 1:
                row.pack_forget()
                continue
            row.pack(fill="x", pady=(0, 4))
            selected = k == active
            row.config(bg=OBJECT_COLORS[k] if selected else LINE)
            for w in (inner, dot, count):
                w.config(bg=PANEL if selected else BG)
            done = sum(1 for r in refs if r["rois"][k])
            count.config(text="%d/%d boxed" % (done, len(refs)),
                         fg=OK_GREEN if refs and done == len(refs) else WARN)

    def _roi_changed(roi, final=True):
        i = sel["i"]
        if i is not None and 0 <= i < len(refs):
            refs[i]["rois"][obj["k"]] = roi
        if roi:
            roi_lbl.config(text="%d × %d px" % (roi["width"], roi["height"]),
                           fg=OK_GREEN)
        else:
            roi_lbl.config(text="Not drawn", fg=WARN)
        _set_btn_enabled(btn_clear_roi, roi is not None)
        if not final:
            return          # mid-drag: the readout is live, the strips are not
        _apply_lock()
        _paint_overlays()
        _paint_thumbs()
        _paint_crops()
        _refresh_gates()

    def _set_live(on):
        live["on"] = on and stream["s"] is not None
        if live["on"]:
            sel["i"] = None
            view.set_roi(None, notify=False)
            view.set_overlays([])
            view.set_editable(False)
            view.set_hint("Live view — capture a frame to draw the target box")
            frame_lbl.config(text="LIVE  ·  camera %d" % cam["index"])
        else:
            view.set_editable(True)
            view.set_hint("Drag to box the part")
        _paint_buttons()

    def _show_ref(i):
        if not (0 <= i < len(refs)):
            return
        live["on"] = False
        sel["i"] = i
        # Seed each object from the nearest reference that already has it boxed,
        # in either direction, so the operator nudges an existing box onto the
        # object instead of redrawing it — whatever order they work through the
        # images in.
        for k in range(obj["n"]):
            if refs[i]["rois"][k] is None:
                near = min((j for j in range(len(refs)) if refs[j]["rois"][k]),
                           key=lambda j: abs(j - i), default=None)
                if near is not None:
                    refs[i]["rois"][k] = dict(refs[near]["rois"][k])
        k = obj["k"]
        view.set_image(refs[i]["img"])
        _apply_lock()
        view.set_roi(refs[i]["rois"][k], notify=False)
        view.set_editable(True)
        view.set_hint("Click to place the box on the part"
                      if view._locked_size else "Drag to box the part")
        frame_lbl.config(text="REFERENCE %d of %d  ·  %s"
                              % (i + 1, len(refs), refs[i]["label"]))
        _roi_changed(refs[i]["rois"][k])
        _paint_thumbs()
        _paint_buttons()

    def _paint_buttons():
        has_cam = stream["s"] is not None
        btn_live.pack_forget(); btn_capture.pack_forget(); btn_import.pack_forget()
        if has_cam:
            btn_capture.pack(side="left")
            btn_live.pack(side="left", padx=(8, 0))
            _set_btn_enabled(btn_live, not live["on"])
            btn_import.pack(side="left", padx=(8, 0))
        else:
            btn_import.pack(side="left")

    def _paint_thumbs():
        for w in thumbs.winfo_children():
            w.destroy()
        for i, ref in enumerate(refs):
            r, c = divmod(i, 4)
            selected = (i == sel["i"])
            # An unboxed reference is the one thing that blocks saving, so it is
            # marked on the strip rather than only in the checklist.
            edge = OK_GREEN if selected else (LINE if _complete(ref) else WARN)
            cell = tk.Frame(thumbs, bg=edge, cursor="hand2")
            cell.grid(row=r, column=c, padx=(0, 6), pady=(0, 6))
            holder = tk.Frame(cell, bg=IMG_BG)
            holder.pack(padx=2, pady=2)
            lbl = tk.Label(holder, image=ref["thumb"], bd=0, cursor="hand2")
            lbl.pack()
            for w in (cell, holder, lbl):
                w.bind("<Button-1>", lambda e, i=i: _show_ref(i))
            x = tk.Label(cell, text="✕", bg=edge,
                         fg="white" if selected else TXT_DIM,
                         font=("Arial", 9, "bold"), cursor="hand2")
            x.place(relx=1.0, rely=0.0, anchor="ne")
            x.bind("<Button-1>", lambda e, i=i: _remove_ref(i))

    def _crop(ref, k=None):
        """The pixels object k (default: the one being boxed) takes from ref."""
        r = ref["rois"][obj["k"] if k is None else k]
        if not r:
            return None
        return ref["img"][r["y"]:r["y"] + r["height"], r["x"]:r["x"] + r["width"]]

    def _paint_crops():
        for w in crops.winfo_children():
            w.destroy()
        drawn = 0
        for i, ref in enumerate(refs):
            patch = _crop(ref)
            if patch is None or patch.size == 0:
                continue
            r, c = divmod(drawn, 4)
            drawn += 1
            cell = tk.Frame(crops, bg=OBJECT_COLORS[obj["k"]] if i == sel["i"] else LINE,
                            cursor="hand2")
            cell.grid(row=r, column=c, padx=(0, 6), pady=(0, 6))
            holder = tk.Frame(cell, bg=IMG_BG, width=66, height=50)
            holder.pack_propagate(False)
            holder.pack(padx=2, pady=2)
            photo, _ = _to_photo(patch, 62, 46)
            ref["crop_photo"] = photo          # keep a reference alive
            lbl = tk.Label(holder, image=photo, bd=0, bg=IMG_BG, cursor="hand2")
            lbl.pack(expand=True)
            for w in (cell, holder, lbl):
                w.bind("<Button-1>", lambda e, i=i: _show_ref(i))

    def _add_ref(img, label):
        if len(refs) >= MAX_REFS:
            messagebox.showinfo("References",
                                "%d reference images is the maximum." % MAX_REFS,
                                parent=win)
            return False
        h, w = img.shape[:2]
        if ref_size["wh"] is None:
            ref_size["wh"] = (w, h)
        elif (w, h) != ref_size["wh"]:
            return False
        thumb, _ = _to_photo(img, 66, 50)
        refs.append({"img": img, "label": label, "thumb": thumb,
                     "rois": [None] * MAX_OBJECTS})
        return True

    def _remove_ref(i):
        if not (0 <= i < len(refs)):
            return
        refs.pop(i)
        if not refs:
            ref_size["wh"] = None
            view.lock_size(None)
            view.set_image(None)
            view.set_placeholder("No reference images yet")
            sel["i"] = None
            _set_live(stream["s"] is not None)
            _roi_changed(None)
        else:
            _show_ref(min(i, len(refs) - 1))
        _paint_thumbs()
        _paint_crops()
        _refresh_gates()

    def _capture():
        s = stream["s"]
        if s is None:
            return
        frame = _for_vision(s.latest() if live["on"] else s.read(timeout=3.0))
        if frame is None:
            messagebox.showwarning("Capture", "No frame from the camera yet.",
                                   parent=win)
            return
        if not _add_ref(frame.copy(), "live"):
            messagebox.showwarning(
                "Capture",
                "The camera changed resolution mid-session.\n\n"
                "Remove the existing references and start again.", parent=win)
            return
        _show_ref(len(refs) - 1)
        _refresh_gates()

    def _import():
        paths = filedialog.askopenfilenames(
            parent=win, title="Select Reference Images",
            filetypes=[("Image files", "*.png *.jpg *.jpeg *.bmp *.webp"), ("All files", "*.*")])
        if not paths:
            return
        skipped = []
        for p in paths:
            img = _for_vision(cv2.imread(p))
            if img is None:
                skipped.append((os.path.basename(p), "unreadable"))
                continue
            if not _add_ref(img, os.path.basename(p)):
                skipped.append((os.path.basename(p),
                                "%d×%d" % (img.shape[1], img.shape[0])))
        if refs:
            # Land on the first reference still needing a box, so the operator
            # works forward through them rather than starting at the end.
            _show_ref(next((i for i, r in enumerate(refs) if not _complete(r)), 0))
        _refresh_gates()
        if skipped:
            need = "%d×%d" % ref_size["wh"] if ref_size["wh"] else "the camera resolution"
            messagebox.showwarning(
                "Some files skipped",
                "Every reference must be %s so one template fits every frame:\n\n%s"
                % (need, "\n".join("  •  %s  (%s)" % s for s in skipped)),
                parent=win)

    btn_capture.config(command=_capture)
    btn_import.config(command=_import)
    btn_live.config(command=lambda: _set_live(True))

    def _odd_crops():
        """Indices of crops that don't look like the others.

        A box left behind on background still produces a valid template, and
        max-of-N scoring means one background template is enough to pass an empty
        fixture. Correlating every crop against the first one catches that before
        it reaches the line.
        """
        odd = []
        for k in range(obj["n"]):
            patches = [_crop(r, k) for r in refs]
            if any(p is None or p.size == 0 for p in patches):
                continue
            grays = [cv2.cvtColor(p, cv2.COLOR_BGR2GRAY) if len(p.shape) == 3 else p
                     for p in patches]
            h, w = grays[0].shape[:2]
            for i, g in enumerate(grays[1:], start=1):
                probe = cv2.resize(g, (w, h)) if g.shape[:2] != (h, w) else g
                score = cv2.matchTemplate(grays[0], probe, cv2.TM_CCOEFF_NORMED)[0][0]
                if score < 0.35:
                    odd.append((i, k, score))
        return odd

    def _stand_ins():
        """Objects that would still be 'found' with the object itself gone.

        Every object is searched for across the whole frame, so an object that
        looks like another one -- two identical connectors, say -- can be
        matched on its twin, and the part passes with one of them missing.
        Blank each object out of the first reference and search for it there:
        a score at the threshold means something else stands in for it.
        """
        if obj["n"] == 1 or not refs:
            return []
        img = cv2.cvtColor(refs[0]["img"], cv2.COLOR_BGR2GRAY)
        found = []
        for k in range(obj["n"]):
            r = refs[0]["rois"][k]
            t = img[r["y"]:r["y"] + r["height"], r["x"]:r["x"] + r["width"]]
            blanked = img.copy()
            blanked[r["y"]:r["y"] + r["height"], r["x"]:r["x"] + r["width"]] = int(img.mean())
            score = cv2.minMaxLoc(cv2.matchTemplate(blanked, t, cv2.TM_CCOEFF_NORMED))[1]
            if score >= threshold:
                found.append((k, score))
        return found

    def _save():
        pno = ent_pno.get().strip()
        n = obj["n"]
        rois = [r["rois"][:n] for r in refs]
        if not (pno and len(refs) >= MIN_REFS and all(all(b) for b in rois)):
            return
        if any(r["width"] < RoiView.MIN_ROI or r["height"] < RoiView.MIN_ROI
               for b in rois for r in b):
            messagebox.showerror("Target Box",
                                 "One of the boxes is too small to match reliably.",
                                 parent=win)
            return

        odd = _odd_crops()
        if odd:
            listing = "\n".join(
                "  •  Reference %d%s  (similarity %.2f)"
                % (i + 1, ", " + _object_name(k) if n > 1 else "", s)
                for i, k, s in odd)
            if not messagebox.askyesno(
                    "Check the Boxes",
                    "These crops do not resemble the first one:\n\n%s\n\n"
                    "That usually means the box missed the part on those images. "
                    "A template of plain background will match the empty fixture "
                    "and pass it.\n\nSave anyway?" % listing, parent=win):
                return

        twins = _stand_ins()
        if twins:
            listing = "\n".join("  •  %s  (found elsewhere at %.2f)" % (_object_name(k), s)
                                for k, s in twins)
            if not messagebox.askyesno(
                    "Objects Look Alike",
                    "With these objects removed from the first reference, something "
                    "else in the frame still matches them:\n\n%s\n\n"
                    "Most often two objects are identical. Each can then be matched "
                    "on the other, so the part could pass with one missing. Box "
                    "something that tells them apart, or check them as one object."
                    "\n\nSave anyway?" % listing, parent=win):
                return

        if not reteach and pno in existing_parts and not messagebox.askyesno(
                "Replace Dataset",
                "“%s” already has a vision dataset.\n\nReplace it?" % pno, parent=win):
            return
        cw, ch = cam["width"], cam["height"]
        rw, rh = ref_size["wh"]
        if cam["enabled"] and cam["index"] >= 0 and (rw, rh) != (cw, ch):
            if not messagebox.askyesno(
                    "Resolution Mismatch",
                    "References are %d×%d but the camera is configured for %d×%d.\n\n"
                    "Inspection will fail if the template does not fit a live frame.\n\n"
                    "Save anyway?" % (rw, rh, cw, ch), parent=win):
                return
        try:
            ctrl.build_and_save_model(
                part_number=pno, images=[r["img"] for r in refs],
                roi=rois, match_threshold=float(threshold),
                object_names=[_object_name(k) for k in range(n)])
        except Exception as e:
            messagebox.showerror("Save Failed", str(e), parent=win)
            return
        result["saved"] = pno
        _close()

    btn_save.config(command=_save)

    def _close():
        alive["v"] = False
        if stream["s"] is not None:
            try:
                stream["s"].release()
            except Exception:
                pass
            stream["s"] = None
        try:
            # Closing with the pointer over the rail would otherwise leave the
            # wheel bound to a destroyed canvas.
            rail_canvas.unbind_all("<MouseWheel>")
        except Exception:
            pass
        try:
            win.grab_release()
        except Exception:
            pass
        win.destroy()

    btn_cancel.config(command=_close)
    win.protocol("WM_DELETE_WINDOW", _close)
    win.bind("<Escape>", lambda e: _close())
    ent_pno.bind("<KeyRelease>", _refresh_gates)

    # ── Camera bring-up ────────────────────────────────────────────────────
    if cam["enabled"] and cam["index"] >= 0:
        stream["s"] = camera.acquire(cam["index"], cam["width"], cam["height"])
        view.set_placeholder("Starting camera %d…" % cam["index"])
        _set_live(True)
    else:
        view.set_placeholder("No camera configured\n\n"
                             "Import reference images, or set a camera up first.")
        _set_live(False)

    def _tick():
        if not alive["v"]:
            return
        s = stream["s"]
        if live["on"] and s is not None:
            frame = _for_vision(s.latest())
            if frame is not None:
                view.set_image(frame)
            elif not s.is_alive():
                live["on"] = False
                view.set_image(None)
                view.set_placeholder("Camera %d stopped responding" % cam["index"])
                frame_lbl.config(text="CAMERA UNAVAILABLE")
        try:
            win.after(60, _tick)
        except Exception:
            pass

    _paint_buttons()
    _roi_changed(None)
    _paint_crops()
    _refresh_gates()
    _tick()

    parent.wait_window(win)
    return result["saved"]


def _draw_score_meter(canvas, score, threshold, verdict_color):
    """Horizontal 0–1 correlation bar with the threshold marked on it."""
    canvas.delete("all")
    w = max(canvas.winfo_width(), 1)
    h = canvas.winfo_height()
    top, bot = 8, h - 16

    canvas.create_rectangle(0, top, w, bot, fill=theme.L_SURFACE_2, outline=LINE)
    if score is not None and score > 0:
        canvas.create_rectangle(0, top, w * min(max(score, 0.0), 1.0), bot,
                                fill=verdict_color, outline="")
    tx = w * min(max(threshold, 0.0), 1.0)
    canvas.create_line(tx, top - 4, tx, bot + 4, fill=TXT, width=2)
    canvas.create_text(tx, h - 5, text="threshold %.2f" % threshold,
                       fill=TXT_DIM, font=("Consolas", 9),
                       anchor="e" if tx > w * 0.6 else "w")
    canvas.create_text(2, h - 5, text="0.0", fill=TXT_FAINT,
                       font=("Consolas", 9), anchor="w")


def _open_test_dialog(parent, ctrl, part_number, on_changed=None):
    """Run the production inspect() path against the live camera, or a still image."""
    from vision_engine.vision_controller import VisionResult
    from vision_engine import camera

    win = _dialog(parent, "Inspection Test", 900, 740)
    _dialog_header(win, "Inspection Test — %s" % part_number,
                   "Runs the same match path inspection uses — against the "
                   "live camera, or a still image you supply.")

    verdict = tk.Frame(win, bg=CHIP, height=54)
    verdict.pack(fill="x")
    verdict.pack_propagate(False)
    verdict_lbl = tk.Label(verdict, text="RUNNING…", bg=CHIP, fg=TXT_DIM,
                           font=("Arial", 22, "bold"))
    verdict_lbl.pack(side="left", padx=18)
    verdict_note = tk.Label(verdict, text="", bg=CHIP, fg=TXT_DIM,
                            font=("Arial", 11), anchor="e", justify="right")
    verdict_note.pack(side="right", padx=18)

    body = tk.Frame(win, bg=BG)
    body.pack(fill="both", expand=True, padx=14, pady=12)
    body.columnconfigure(0, weight=1)
    body.rowconfigure(0, weight=1)

    view_wrap = tk.Frame(body, bg=LINE)
    view_wrap.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
    view = RoiView(view_wrap, editable=False)
    view.pack(fill="both", expand=True, padx=1, pady=1)
    view.set_placeholder("Capturing…")

    rail = tk.Frame(body, bg=BG, width=250)
    rail.grid(row=0, column=1, sticky="ns")
    rail.grid_propagate(False)

    metrics = _card(rail, "Result")
    metrics.pack(fill="x")
    mb = metrics.body
    m_source = _kv_row(mb, "Source", "Live camera", mono=False)
    m_score = _kv_row(mb, "Score", "—", mono=True)
    m_thresh = _kv_row(mb, "Threshold", "—", mono=True)
    m_time = _kv_row(mb, "Time", "—", mono=True)
    m_refs = _kv_row(mb, "References", "—", mono=True)
    m_tmpl = _kv_row(mb, "Template", "—", mono=True)

    meter = tk.Canvas(mb, bg=PANEL, height=34, highlightthickness=0, bd=0)
    meter.pack(fill="x", pady=(10, 0))

    # Per-object scores, inside the result card so the hint below stays in
    # view; only shown for a part taught as more than one object.
    object_list = tk.Frame(mb, bg=PANEL)

    hint = tk.Label(rail, text="", bg=BG, fg=TXT_DIM, font=("Arial", 10),
                    wraplength=240, justify="left", anchor="w")
    hint.pack(fill="x", pady=(12, 0))

    tk.Frame(win, bg=LINE, height=1).pack(fill="x")
    foot = tk.Frame(win, bg=PANEL)
    foot.pack(fill="x")
    foot_in = tk.Frame(foot, bg=PANEL)
    foot_in.pack(fill="x", padx=18, pady=12)
    btn_close = _btn(foot_in, "Close", BTN_NEUTRAL, font_size=12, pady=8)
    btn_close.pack(side="right")
    btn_rerun = _btn(foot_in, "Run Again", BTN_PRIMARY, font_size=12, pady=8)
    btn_rerun.pack(side="right", padx=(0, 8))
    btn_source = _btn(foot_in, "Test Image…", BTN_NEUTRAL, font_size=12, pady=8)
    btn_source.pack(side="right", padx=(0, 8))
    btn_tune = _btn(foot_in, "Adjust Threshold…", BTN_NEUTRAL, font_size=12, pady=8)
    btn_tune.pack(side="left")

    alive = {"v": True}
    busy = {"v": False}
    last = {"result": None}
    source = {"kind": "camera", "image": None, "label": None}

    # Hold the camera open for as long as the dialog is up. A cold device needs
    # ~1s of frames before exposure settles (camera.read's settle count), and
    # opening it can take seconds more; paying that on every "Run Again"
    # made each run slow. Held here, only the first run waits.
    cam_index, cam_w, cam_h = ctrl.cam_settings()
    stream = {"s": camera.acquire(cam_index, cam_w, cam_h) if _cv2_ok else None}

    def _run():
        """Start one inspection. Capture and matching run on a worker thread:
        done on the Tk thread they froze the whole app for the seconds a cold
        camera takes to open and settle, long enough for Windows to mark it
        Not Responding -- which read as the app crashing."""
        if not alive["v"] or busy["v"]:
            return
        busy["v"] = True
        verdict_lbl.config(text="RUNNING…", fg=TXT_DIM)
        verdict.config(bg=CHIP)
        for w_ in (verdict_lbl, verdict_note):
            w_.config(bg=CHIP)
        verdict_note.config(text="Capturing from the camera…" if source["kind"] == "camera"
                            else "Judging the image…", fg=TXT_DIM)
        for b in (btn_rerun, btn_source, btn_tune):
            _set_btn_enabled(b, False)

        ctrl.reload_config()
        kind, image, s = source["kind"], source["image"], stream["s"]
        # The worker only fills this in; the Tk thread polls for it. Tk calls
        # made from another thread are refused unless the main thread is
        # sitting in mainloop, so the worker never touches a widget.
        done = {}

        def _poll():
            if not alive["v"]:
                busy["v"] = False
                return
            if "result" in done:
                _show(done["result"])
            else:
                win.after(50, _poll)

        def _work():
            try:
                if kind == "image":
                    result = ctrl.inspect(part_number, frame=image)
                else:
                    frame = s.read(timeout=5.0) if s is not None else None
                    if frame is None:
                        result = VisionResult(ok=False, judgement="ERROR",
                                              part_number=part_number,
                                              error="Camera not available")
                    else:
                        result = ctrl.inspect(part_number, frame=frame)
            except Exception as e:
                result = VisionResult(ok=False, judgement="ERROR",
                                      part_number=part_number, error=str(e))
            done["result"] = result

        threading.Thread(target=_work, daemon=True).start()
        win.after(50, _poll)

    def _show(result):
        busy["v"] = False
        if not alive["v"]:
            return
        last["result"] = result
        _set_btn_enabled(btn_source, True)

        colors = {"OK": OK_GREEN, "NG": NG_RED, "ERROR": WARN}
        color = colors.get(result.judgement, TXT_DIM)
        # The image overlays sit on a dark picture, so they use the bright set.
        img_color = {"OK": IMG_OK, "NG": IMG_NG}.get(result.judgement, IMG_WARN)
        verdict.config(bg=VERDICT_BG.get(result.judgement, CHIP))
        for w_ in (verdict_lbl, verdict_note):
            w_.config(bg=VERDICT_BG.get(result.judgement, CHIP))
        verdict_lbl.config(text=result.judgement, fg=color)
        verdict_note.config(text=result.error or "Part found", fg=color)

        info = ctrl.model_info(part_number) or {}
        m_source.config(text="Live camera" if source["kind"] == "camera" else source["label"])
        m_score.config(text="%.4f" % result.match_score if result.match_score > 0 else "—",
                       fg=color)
        m_thresh.config(text="%.2f" % result.threshold if result.threshold else
                        "%.2f" % info.get("threshold", 0.0), fg=TXT)
        m_time.config(text="%d ms" % result.processing_time_ms, fg=TXT)
        m_refs.config(text=str(info.get("references", "—")), fg=TXT)
        tw, th = info.get("template_size", (0, 0))
        multi = len(result.objects) > 1
        m_tmpl.config(text=("%d objects" % len(result.objects)) if multi else
                      "%d x %d" % (tw, th) if tw else "—", fg=TXT)

        for w_ in object_list.winfo_children():
            w_.destroy()
        if multi:
            object_list.pack(fill="x", pady=(10, 0))
            tk.Frame(object_list, bg=LINE, height=1).pack(fill="x", pady=(0, 6))
            for p in result.objects:
                _kv_row(object_list, p.name, "%.4f  %s" % (p.score, "✓" if p.ok else "✗"),
                        value_fg=OK_GREEN if p.ok else NG_RED, mono=True)
        else:
            object_list.pack_forget()

        def _box(b):
            return {"x": b[0], "y": b[1], "width": b[2], "height": b[3]} if b else None

        if result.frame is not None:
            view.set_image(result.frame)
            view.set_accent(img_color)
            if multi:
                # Every object's match, each coloured by its own verdict, rather
                # than one box that could only speak for the weakest object.
                view.set_roi(None, notify=False)
                view.set_overlays([(_box(p.box), IMG_OK if p.ok else IMG_NG,
                                    "%s %.2f" % (p.name, p.score))
                                   for p in result.objects])
            else:
                view.set_overlays([])
                if result.match_box:
                    view.set_roi(_box(result.match_box), notify=False)
            view.set_hint("Best match found in this " +
                          ("image" if source["kind"] == "image" else "frame"))
        else:
            view.set_image(None)
            view.set_placeholder(result.error or "No frame captured")

        thr = result.threshold or info.get("threshold", 0.0)
        _draw_score_meter(meter, result.match_score, thr, color)

        if result.judgement == "NG" and multi:
            hint.config(
                text="Every object must reach %.2f. If %s is genuinely present and "
                     "correct, re-teach with more reference images or lower this "
                     "model's threshold."
                     % (thr, ", ".join(p.name for p in result.objects if not p.ok)), fg=WARN)
        elif result.judgement == "NG":
            hint.config(
                text="The best match scored %.2f against a %.2f threshold. If the model "
                     "is genuinely present and correct, either re-teach it with more "
                     "reference images or lower this model's threshold."
                     % (result.match_score, thr), fg=WARN)
        elif result.judgement == "ERROR":
            hint.config(text="Nothing was judged — fix the error above and run again.",
                        fg=WARN)
        else:
            hint.config(text="Headroom above threshold: %+.2f."
                             % (result.match_score - thr), fg=TXT_DIM)
        _set_btn_enabled(btn_rerun, True)
        _set_btn_enabled(btn_tune, bool(info))

    def _pick_image():
        if source["kind"] == "image":
            # Already testing an image — the button toggles back to the camera.
            source["kind"], source["image"], source["label"] = "camera", None, None
            btn_source.config(text="Test Image…")
            _run()
            return

        path = filedialog.askopenfilename(
            parent=win, title="Select Test Image",
            filetypes=[("Image files", "*.png *.jpg *.jpeg *.bmp *.webp"), ("All files", "*.*")])
        if not path:
            return
        img = _for_vision(cv2.imread(path))
        if img is None:
            messagebox.showerror("Test Image", "Could not read that image file.", parent=win)
            return
        source["kind"] = "image"
        source["image"] = img
        source["label"] = os.path.basename(path)
        btn_source.config(text="Use Live Camera")
        _run()

    def _tune():
        info = ctrl.model_info(part_number)
        if info and _open_threshold_dialog(parent, ctrl, part_number,
                                           info["threshold"], anchor=win):
            if on_changed:
                on_changed()
            _run()

    def _close():
        alive["v"] = False
        if stream["s"] is not None:
            try:
                stream["s"].release()
            except Exception:
                pass
            stream["s"] = None
        try:
            win.grab_release()
        except Exception:
            pass
        win.destroy()

    btn_rerun.config(command=_run)
    btn_source.config(command=_pick_image)
    btn_tune.config(command=_tune)
    btn_close.config(command=_close)
    win.protocol("WM_DELETE_WINDOW", _close)
    win.bind("<Escape>", lambda e: _close())
    meter.bind("<Configure>", lambda e: last["result"] and _draw_score_meter(
        meter, last["result"].match_score,
        last["result"].threshold or (ctrl.model_info(part_number) or {}).get("threshold", 0.0),
        {"OK": OK_GREEN, "NG": NG_RED}.get(last["result"].judgement, WARN)))

    win.after(120, _run)
    parent.wait_window(win)


# ═══════════════════════════════════════════════════════════════════════════════
# Small dialogs
# ═══════════════════════════════════════════════════════════════════════════════

def _open_threshold_dialog(parent, ctrl, part_number, current, anchor=None):
    """Edit one taught part's own threshold. Returns True if it was saved."""
    host = anchor or parent
    win = _dialog(host, "Match Threshold", 430, 300)
    _dialog_header(win, "Threshold — %s" % part_number,
                   "Only this model is affected.")

    body = tk.Frame(win, bg=BG)
    body.pack(fill="both", expand=True, padx=20, pady=16)

    var = tk.DoubleVar(value=float(current))
    row = tk.Frame(body, bg=BG)
    row.pack(fill="x")
    tk.Label(row, text="Match threshold", bg=BG, fg=TXT,
             font=("Arial", 12, "bold")).pack(side="left")
    val = tk.Label(row, text="%.2f" % current, bg=BG, fg=ACCENT,
                   font=("Consolas", 17, "bold"))
    val.pack(side="right")

    ttk.Scale(body, from_=0.40, to=0.99, orient="horizontal", variable=var,
              style="Vis.Horizontal.TScale").pack(fill="x", pady=(8, 4))

    caption = tk.Label(body, text="", bg=BG, fg=TXT_DIM, font=("Arial", 11),
                       wraplength=380, justify="left", anchor="w")
    caption.pack(fill="x", pady=(6, 0))
    tk.Label(body, text="Lower it if good parts are being rejected; raise it if a "
                        "wrong or missing part still passes.",
             bg=BG, fg=TXT_FAINT, font=("Arial", 10), wraplength=380,
             justify="left", anchor="w").pack(fill="x", pady=(10, 0))

    def _upd(*_a):
        v = round(var.get(), 2)
        val.config(text="%.2f" % v)
        text, color = _threshold_caption(v)
        caption.config(text=text, fg=color)

    var.trace_add("write", _upd)
    _upd()

    saved = {"v": False}

    tk.Frame(win, bg=LINE, height=1).pack(fill="x")
    foot = tk.Frame(win, bg=PANEL)
    foot.pack(fill="x")
    foot_in = tk.Frame(foot, bg=PANEL)
    foot_in.pack(fill="x", padx=18, pady=12)

    def _save():
        try:
            ctrl.set_model_threshold(part_number, round(var.get(), 2))
        except Exception as e:
            messagebox.showerror("Threshold", str(e), parent=win)
            return
        saved["v"] = True
        win.destroy()

    _btn(foot_in, "Save", BTN_PRIMARY, command=_save, font_size=10,
         pady=8).pack(side="right")
    _btn(foot_in, "Cancel", BTN_NEUTRAL, command=win.destroy, font_size=10,
         pady=8).pack(side="right", padx=(0, 8))
    win.bind("<Escape>", lambda e: win.destroy())

    host.wait_window(win)
    return saved["v"]


def _prompt_part_number(parent, title, prompt, taken=()):
    """Ask for a part number, backed by the part master when it's reachable.

    Returns the upper-cased value, or None.
    """
    win = _dialog(parent, title, 430, 250)
    _dialog_header(win, title, prompt)

    body = tk.Frame(win, bg=BG)
    body.pack(fill="both", expand=True, padx=20, pady=18)
    master_parts = _fetch_master_parts()
    field = _PnoField(body, master_parts, font_size=14)
    field.pack(fill="x", ipady=6)
    field.focus_set()
    note = tk.Label(body, text="", bg=BG, fg=WARN, font=("Arial", 10), anchor="w",
                    wraplength=380, justify="left")
    note.pack(fill="x", pady=(6, 0))
    if master_parts is None:
        note.config(text="Could not reach the model master — typed value won't "
                         "be checked against the model master.")

    out = {"v": None}

    def _check(*_a):
        v = field.get()
        if v and v in taken:
            note.config(text="“%s” is already mapped — saving will re-point it." % v)
        elif master_parts is None:
            note.config(text="Could not reach the model master — typed value "
                             "won't be checked against the model master.")
        else:
            note.config(text="")

    def _ok(event=None):
        v = field.get()
        if not v:
            note.config(text="Enter a model.")
            return
        out["v"] = v
        win.destroy()

    field.bind("<KeyRelease>", _check)
    field.bind("<Return>", _ok)
    win.bind("<Escape>", lambda e: win.destroy())

    tk.Frame(win, bg=LINE, height=1).pack(fill="x")
    foot = tk.Frame(win, bg=PANEL)
    foot.pack(fill="x")
    foot_in = tk.Frame(foot, bg=PANEL)
    foot_in.pack(fill="x", padx=18, pady=12)
    _btn(foot_in, "OK", BTN_PRIMARY, command=_ok, font_size=12, pady=8).pack(side="right")
    _btn(foot_in, "Cancel", BTN_NEUTRAL, command=win.destroy, font_size=10,
         pady=8).pack(side="right", padx=(0, 8))

    parent.wait_window(win)
    return out["v"]


# ═══════════════════════════════════════════════════════════════════════════════
# Camera configuration
# ═══════════════════════════════════════════════════════════════════════════════

def _open_camera_dialog(parent):
    """Pick and verify the inspection cameras (slot 1 and slot 2). Returns
    True if the config changed."""
    if not _cv2_ok or not _pil_ok:
        messagebox.showerror("Camera", "OpenCV and Pillow are required.", parent=parent)
        return False

    from vision_engine import camera

    slot = {"id": 1}
    cam = _load_cam_cfg(1)
    win = _dialog(parent, "Camera Configuration", 780, 700)
    _dialog_header(win, "Inspection Cameras",
                   "Pick Camera 1 or Camera 2, then its device. The preview is "
                   "the exact feed inspection will use.")

    # Footer packed before the body so it keeps its height: packed after, it
    # got whatever the body left and the buttons fell off the bottom.
    foot = tk.Frame(win, bg=PANEL)
    foot.pack(side="bottom", fill="x")
    tk.Frame(win, bg=LINE, height=1).pack(side="bottom", fill="x")
    foot_in = tk.Frame(foot, bg=PANEL)
    foot_in.pack(fill="x", padx=18, pady=12)
    btn_save = _btn(foot_in, "Save", BTN_PRIMARY, font_size=12, pady=8)
    btn_save.pack(side="right")
    btn_cancel = _btn(foot_in, "Cancel", BTN_NEUTRAL, font_size=12, pady=8)
    btn_cancel.pack(side="right", padx=(0, 8))

    alive = {"v": True}
    stream = {"s": None, "index": None}
    found = {"cams": []}
    changed = {"v": False}

    body = tk.Frame(win, bg=BG)
    body.pack(fill="both", expand=True, padx=14, pady=12)
    body.columnconfigure(0, weight=1)
    body.rowconfigure(0, weight=1)

    prev_wrap = tk.Frame(body, bg=LINE)
    prev_wrap.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
    preview = RoiView(prev_wrap, editable=False)
    preview.pack(fill="both", expand=True, padx=1, pady=1)
    preview.set_placeholder("Scanning for cameras…")

    rail = tk.Frame(body, bg=BG, width=260)
    rail.grid(row=0, column=1, sticky="ns")
    rail.grid_propagate(False)

    card = _card(rail, "Device")
    card.pack(fill="x")
    cb_body = card.body

    # Which of the two camera slots is being set up. Each slot is saved on
    # its own; switching loads that slot's saved settings.
    slot_row = tk.Frame(cb_body, bg=PANEL)
    slot_row.pack(fill="x", pady=(0, 10))
    slot_btns = {}
    for sid in (1, 2):
        b = tk.Button(slot_row, text="Camera %d" % sid, font=("Arial", 11, "bold"),
                      bd=0, pady=5, cursor="hand2",
                      command=lambda s_=sid: _switch_slot(s_))
        b.pack(side="left", fill="x", expand=True, padx=(0, 4) if sid == 1 else (4, 0))
        slot_btns[sid] = b

    def _paint_slots():
        for sid, b in slot_btns.items():
            on = sid == slot["id"]
            bg = BTN_PRIMARY[0] if on else BTN_NEUTRAL[0]
            b.config(bg=bg, fg="white", activebackground=bg, activeforeground="white")
    _paint_slots()

    tk.Label(cb_body, text="Device", bg=PANEL, fg=TXT_DIM, font=("Arial", 11),
             anchor="w").pack(fill="x")
    dev_var = tk.StringVar()
    cmb_dev = ttk.Combobox(cb_body, textvariable=dev_var, state="readonly",
                           values=["Scanning…"], font=("Arial", 11))
    cmb_dev.pack(fill="x", pady=(3, 10))

    tk.Label(cb_body, text="The camera always runs at its maximum resolution, "
                           "up to 1280 x 720 (shown next to it above). Vision "
                           "works on a 640-wide copy, so a camera with a "
                           "different shape means re-teaching every model.",
             bg=PANEL, fg=TXT_FAINT, font=("Arial", 10), wraplength=215,
             justify="left", anchor="w").pack(fill="x")

    flip_var = tk.BooleanVar(value=cam["flip"])
    tk.Checkbutton(cb_body, text="  Flip image top to bottom", variable=flip_var,
                   bg=PANEL, fg=TXT, selectcolor=FIELD, activebackground=PANEL,
                   activeforeground=TXT, font=("Arial", 11, "bold"), bd=0,
                   highlightthickness=0, anchor="w", cursor="hand2",
                   command=lambda: _apply_flip()).pack(fill="x", pady=(10, 0))
    mirror_var = tk.BooleanVar(value=cam["mirror"])
    tk.Checkbutton(cb_body, text="  Flip image left to right", variable=mirror_var,
                   bg=PANEL, fg=TXT, selectcolor=FIELD, activebackground=PANEL,
                   activeforeground=TXT, font=("Arial", 11, "bold"), bd=0,
                   highlightthickness=0, anchor="w", cursor="hand2",
                   command=lambda: _apply_flip()).pack(fill="x", pady=(4, 0))
    tk.Label(cb_body, text="For a camera mounted upside down or seeing the part "
                           "mirrored. Models taught before changing these must be "
                           "re-taught.",
             bg=PANEL, fg=TXT_FAINT, font=("Arial", 10), wraplength=215,
             justify="left", anchor="w").pack(fill="x")

    btn_rescan = _btn(cb_body, "Re-scan", BTN_NEUTRAL, pady=5)
    btn_rescan.pack(fill="x", pady=(10, 0))

    status = tk.Label(rail, text="", bg=BG, fg=TXT_DIM, font=("Arial", 11),
                      wraplength=250, justify="left", anchor="w")
    status.pack(fill="x", pady=(12, 0))


    DISABLED = "Disabled (no vision capture)"

    def _stop_stream():
        if stream["s"] is not None:
            try:
                stream["s"].release()
            except Exception:
                pass
            stream["s"] = None
            stream["index"] = None

    def _selected():
        label = dev_var.get()
        for c in found["cams"]:
            if label == "Camera %d  (%dx%d)" % (c["index"], c["width"], c["height"]):
                return c
        return None

    def _selected_index():
        c = _selected()
        return c["index"] if c else -1

    def _apply_flip():
        """Show the flip on the preview straight away. Only Save writes it; any
        other way out puts the saved orientation back (see _close)."""
        if stream["index"] is not None:
            camera.preview_flip(stream["index"], flip_var.get(), mirror_var.get())

    def _on_device_change(*_a):
        idx = _selected_index()
        _stop_stream()
        preview.set_image(None)
        if idx < 0:
            preview.set_placeholder("Camera disabled — vision will not run.")
            status.config(text="Vision inspection needs a camera.", fg=WARN)
            return
        c = _selected()
        w, h = c["width"], c["height"]
        preview.set_placeholder("Opening camera %d…" % idx)
        status.config(text="", fg=TXT_DIM)
        stream["s"] = camera.acquire(idx, w, h)
        stream["index"] = idx
        _apply_flip()

    cmb_dev.bind("<<ComboboxSelected>>", _on_device_change)

    def _device_label_for(c):
        if c["enabled"] and c["index"] >= 0:
            for f in found["cams"]:
                if f["index"] == c["index"]:
                    return "Camera %d  (%dx%d)" % (f["index"], f["width"], f["height"])
        return DISABLED

    def _switch_slot(sid):
        if sid == slot["id"]:
            return
        # Unsaved flips previewed for the other slot are dropped with it.
        camera.end_flip_preview()
        slot["id"] = sid
        cam.clear()
        cam.update(_load_cam_cfg(sid))
        flip_var.set(cam["flip"])
        mirror_var.set(cam["mirror"])
        _paint_slots()
        if found["cams"]:
            dev_var.set(_device_label_for(cam))
            _on_device_change()

    def _scan():
        _stop_stream()
        preview.set_image(None)
        preview.set_placeholder("Scanning for cameras…")
        cmb_dev.config(values=["Scanning…"], state="disabled")
        dev_var.set("Scanning…")
        _set_btn_enabled(btn_rescan, False)

        def _work():
            cams = _probe_cameras()

            def _apply():
                if not alive["v"]:
                    return
                found["cams"] = cams
                labels = [DISABLED] + ["Camera %d  (%dx%d)"
                                       % (c["index"], c["width"], c["height"])
                                       for c in cams]
                cmb_dev.config(values=labels, state="readonly")
                pick = DISABLED
                for lab, c in zip(labels[1:], cams):
                    if c["index"] == cam["index"] and cam["enabled"]:
                        pick = lab
                dev_var.set(pick)
                _set_btn_enabled(btn_rescan, True)
                if not cams:
                    preview.set_placeholder("No camera detected.\n\n"
                                            "Check the USB connection and re-scan.")
                    status.config(text="Nothing responded on indexes 0–5.", fg=NG_RED)
                else:
                    status.config(text="%d camera(s) detected." % len(cams), fg=TXT_DIM)
                _on_device_change()

            try:
                win.after(0, _apply)
            except Exception:
                pass

        threading.Thread(target=_work, daemon=True).start()

    btn_rescan.config(command=_scan)

    def _tick():
        if not alive["v"]:
            return
        s = stream["s"]
        if s is not None:
            frame = s.latest()
            if frame is not None:
                preview.set_image(frame)
                preview.set_hint("%d x %d" % (frame.shape[1], frame.shape[0]))
            elif not s.is_alive():
                preview.set_image(None)
                preview.set_placeholder("Camera %d is not delivering frames."
                                        % stream["index"])
                status.config(text="The device opened but produced no video. It may be "
                                   "in use by another program.", fg=NG_RED)
                _stop_stream()
        try:
            win.after(60, _tick)
        except Exception:
            pass

    def _save():
        c = _selected()
        idx = c["index"] if c else -1
        # A disabled camera keeps the size it had.
        w, h = (c["width"], c["height"]) if c else (cam["width"], cam["height"])
        if idx >= 0 and stream["s"] is not None and stream["s"].latest() is None:
            if not messagebox.askyesno(
                    "No Preview",
                    "No frames have arrived from camera %d yet.\n\nSave anyway?" % idx,
                    parent=win):
                return
        other = _load_cam_cfg(2 if slot["id"] == 1 else 1)
        if idx >= 0 and other["enabled"] and other["index"] == idx:
            if not messagebox.askyesno(
                    "Same Device",
                    "Camera %d already uses this device (index %d).\n\n"
                    "Use it for Camera %d as well?" % (2 if slot["id"] == 1 else 1, idx, slot["id"]),
                    parent=win):
                return
        _save_cam_cfg(idx, w, h, idx >= 0, flip_var.get(), mirror_var.get(),
                      cam_id=slot["id"])
        changed["v"] = True
        _close()

    def _close():
        alive["v"] = False
        _stop_stream()
        camera.end_flip_preview()
        try:
            win.grab_release()
        except Exception:
            pass
        win.destroy()

    btn_save.config(command=_save)
    btn_cancel.config(command=_close)
    win.protocol("WM_DELETE_WINDOW", _close)
    win.bind("<Escape>", lambda e: _close())

    _scan()
    _tick()
    parent.wait_window(win)
    return changed["v"]
