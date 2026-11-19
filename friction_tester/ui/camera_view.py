"""Live camera view for the Test Console: one configured camera slot (camera_cfg.ini,
set up on the Vision page), letterboxed to whatever size the widget gets.

The device is shared through vision_engine.camera, so the view and an inspection
read one stream instead of each opening the camera. Call stop() when done.
"""

import configparser
import os
import tkinter as tk

from . import theme as T

# OpenCV and Pillow are loaded only once a camera is configured (see _start), so a
# PC without cameras never pays for them when the Test Console opens.
cv2 = Image = ImageTk = None

_CAM_CFG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                             "camera_cfg.ini")

BG = "#07080b"              # dark letterbox, like the Vision page image views
TEXT = "#9aa4b1"
LIVE = "#00e676"
REFRESH_MS = 100


def _load_cam_cfg(slot):
    """camN_* from camera_cfg.ini (the same keys the Vision page writes)."""
    cfg = configparser.ConfigParser()
    cfg.read(_CAM_CFG_PATH)
    k = f"cam{slot}_"
    return {"index": cfg.getint("CAMERA", k + "index", fallback=-1),
            "width": cfg.getint("CAMERA", k + "width", fallback=640),
            "height": cfg.getint("CAMERA", k + "height", fallback=480),
            "enabled": cfg.getboolean("CAMERA", k + "enabled", fallback=False)}

class CameraView(tk.Frame):
    """Camera `slot` (1 or 2): caption strip with a state dot, then the picture."""

    def __init__(self, master, slot):
        super().__init__(master, bg=T.L_SURFACE)
        self.slot = slot
        self._stream = None
        self._photo = None
        self._after = None
        self._shown = None             # placeholder text on screen, so it isn't redrawn every tick

        head = tk.Frame(self, bg=T.L_SURFACE)
        head.pack(fill="x")
        self._dot = tk.Label(head, text="●", bg=T.L_SURFACE, fg=T.L_TEXT_3, font=(T.FONT, 9))
        self._dot.pack(side="left")
        tk.Label(head, text=f"CAMERA {slot}", bg=T.L_SURFACE, fg=T.L_TEXT_2,
                 font=(T.FONT, 8, "bold")).pack(side="left", padx=(2, 0))
        self._info = tk.Label(head, text="", bg=T.L_SURFACE, fg=T.L_TEXT_3, font=(T.FONT, 8))
        self._info.pack(side="right")
        self._canvas = tk.Canvas(self, bg=BG, highlightthickness=1, highlightbackground=T.L_BORDER_2,
                                 width=1, height=1)
        self._canvas.pack(fill="both", expand=True, pady=(1, 0))
        self._canvas.bind("<Configure>", lambda _e: self._redraw_placeholder())

        self._start()

    # ------------------------------------------------------------ stream
    def _start(self):
        cam = _load_cam_cfg(self.slot)
        if not cam["enabled"] or cam["index"] < 0:
            self._placeholder("Not configured — set it up on the Vision page")
            return
        global cv2, Image, ImageTk
        try:
            import cv2
            from PIL import Image, ImageTk
            from vision_engine import camera
        except ImportError:
            self._placeholder("OpenCV / Pillow not installed")
            return
        self._stream = camera.acquire(cam["index"], cam["width"], cam["height"])
        self._info.config(text=f"#{cam['index']}")
        self._placeholder(f"Starting camera {self.slot}…")
        self._tick()

    def stop(self):
        """Stop refreshing and let go of the camera. Safe to call twice."""
        if self._after is not None:
            try:
                self.after_cancel(self._after)
            except tk.TclError:
                pass
            self._after = None
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.release()
            except Exception:
                pass

    def _tick(self):
        self._after = None
        stream = self._stream
        if stream is None:
            return
        frame = stream.latest()
        if frame is not None:
            self._show(frame)
            self._dot.config(fg=LIVE)
        elif not stream.is_alive():
            self.stop()
            self._dot.config(fg=T.L_NG)
            self._placeholder(f"Camera {self.slot} not responding")
            return
        self._after = self.after(REFRESH_MS, self._tick)

    # ------------------------------------------------------------ drawing
    def _show(self, frame):
        cv = self._canvas
        cw, ch = cv.winfo_width(), cv.winfo_height()
        if cw < 4 or ch < 4:
            return
        fh, fw = frame.shape[:2]
        scale = min(cw / fw, ch / fh)
        dw, dh = max(1, int(fw * scale)), max(1, int(fh * scale))
        img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)).resize((dw, dh), Image.Resampling.BILINEAR)
        self._photo = ImageTk.PhotoImage(img)
        cv.delete("all")
        cv.create_image(cw // 2, ch // 2, image=self._photo, anchor="center")
        self._shown = None

    def _placeholder(self, text):
        self._shown = text
        self._redraw_placeholder()

    def _redraw_placeholder(self):
        if self._shown is None:
            return
        cv = self._canvas
        cv.delete("all")
        cv.create_text(max(cv.winfo_width(), 2) // 2, max(cv.winfo_height(), 2) // 2, text=self._shown,
                       fill=TEXT, font=(T.FONT, 9), justify="center", width=max(cv.winfo_width() - 8, 20))
