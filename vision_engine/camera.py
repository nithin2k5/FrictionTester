"""
vision_engine/camera.py
=======================
Single owner per physical camera device.

A DirectShow device accepts only one cv2.VideoCapture at a time, so the live
preview and the inspection path must share one stream rather than each opening
the device. Consumers acquire a reference-counted stream by index; the device is
opened on the first acquire and released when the last reference goes away.
"""
import configparser
import os
import threading
import time
from typing import Optional

import cv2
import numpy as np

_REGISTRY_LOCK = threading.Lock()
_STREAMS: dict = {}
# Index -> the reader thread of the newest stream ever opened on it, live or
# closing. A new stream waits for it before opening the device; see _loop.
_LAST_READER: dict = {}

# How long a new stream waits for the previous one on its device to let go.
_HANDOVER_TIMEOUT = 10.0

# Device index -> (flip top to bottom, mirror left to right). Applied to every
# frame the stream serves, so the live previews, teaching, Run Test and the line
# all see the same orientation -- a taught template only matches frames flipped
# the way its references were.
_CAM_CFG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "camera_cfg.ini")
_NO_FLIP = (False, False)
_FLIP: dict = {}
# Unsaved flips a settings dialog is previewing. Kept apart from _FLIP so that
# a device opening (which re-reads the config) cannot undo them mid-preview.
_FLIP_PREVIEW: dict = {}

# (vertical, horizontal) -> cv2.flip code.
_FLIP_CODES = {(True, False): 0, (False, True): 1, (True, True): -1}

# Cameras run at the largest size they support, up to CAMERA_CAP -- there is
# no setting for it. DirectShow snaps an oversized request to the nearest mode
# it has, which for a request this big is the camera's largest one. The cap
# keeps a multi-megapixel camera from streaming frames nobody needs: the live
# views are smaller than this, and vision works on a reduced copy anyway.
_MAX_REQUEST = 10000
CAMERA_CAP = (1280, 720)

# Vision (teaching and inspection) works on frames no wider than this. Template
# matching costs grow with the pixel count, so inspecting a full 1280x720 frame
# took twice as long as 640x480 and a 5 MP camera took ~15x as long -- the
# cycle's vision step went from 1-2 s to 10-15 s. Teaching uses the same
# reduced frames, so references and inspected frames always share one scale.
VISION_WIDTH = 640


def max_resolution(cap) -> Optional[tuple]:
    """(width, height) of the largest mode an open cv2.VideoCapture supports,
    capped at CAMERA_CAP.

    Switches `cap` to that mode as a side effect. Measured from a real frame
    rather than CAP_PROP_*, which some drivers report before the switch has
    actually taken. None if the camera stops delivering frames.
    """
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, _MAX_REQUEST)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, _MAX_REQUEST)
    ret, frame = cap.read()
    if not ret or frame is None:
        return None
    w, h = int(frame.shape[1]), int(frame.shape[0])
    if w > CAMERA_CAP[0] or h > CAMERA_CAP[1]:
        # Larger than the cap: ask for the cap and take the mode the driver
        # snaps to (the nearest one it has).
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_CAP[0])
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_CAP[1])
        ret, frame = cap.read()
        if not ret or frame is None:
            return None
        w, h = int(frame.shape[1]), int(frame.shape[0])
    return w, h


def for_vision(frame: Optional[np.ndarray]) -> Optional[np.ndarray]:
    """`frame` reduced to VISION_WIDTH wide (aspect kept) for teaching and
    inspection. Frames already that narrow come back unchanged, so applying
    it twice is harmless and a 640x480 camera behaves exactly as before."""
    if frame is None or frame.shape[1] <= VISION_WIDTH:
        return frame
    h = max(1, round(frame.shape[0] * VISION_WIDTH / frame.shape[1]))
    return cv2.resize(frame, (VISION_WIDTH, h), interpolation=cv2.INTER_AREA)


def _flipped(index: int) -> tuple:
    return _FLIP_PREVIEW.get(index, _FLIP.get(index, _NO_FLIP))


def reload_flips():
    """Re-read each configured camera's flip settings from camera_cfg.ini.
    Running streams consult them on every frame, so a saved change shows at once."""
    cfg = configparser.ConfigParser()
    cfg.read(_CAM_CFG_PATH)
    flips = {}
    for cid in (1, 2):
        index = cfg.getint("CAMERA", f"cam{cid}_index", fallback=-1)
        if index >= 0 and cfg.getboolean("CAMERA", f"cam{cid}_enabled", fallback=False):
            v, h = flips.get(index, _NO_FLIP)
            flips[index] = (
                v or cfg.getboolean("CAMERA", f"cam{cid}_flip", fallback=False),
                h or cfg.getboolean("CAMERA", f"cam{cid}_mirror", fallback=False))
    _FLIP.clear()
    _FLIP.update(flips)


def preview_flip(index: int, flip: bool, mirror: bool = False):
    """Flip one device's frames without touching the config, for a settings
    dialog to show the choice live. end_flip_preview() drops it again."""
    _FLIP_PREVIEW[index] = (bool(flip), bool(mirror))


def end_flip_preview():
    """Back to the saved orientation: drop every preview, re-read the config."""
    _FLIP_PREVIEW.clear()
    reload_flips()

# ~1s of failed reads at the 20ms retry interval.
_MAX_CONSECUTIVE_READ_FAILURES = 50


class CameraStream:
    """Background reader for one camera index. Serves the most recent frame."""

    def __init__(self, index: int, width: int, height: int):
        self.index = index
        self._width = width
        self._height = height
        self._cap = None
        self._frame = None
        self._frames_read = 0
        self._frame_lock = threading.Lock()
        self._running = False
        self._refs = 0
        self._opened = threading.Event()
        self._open_ok = False
        self._thread = None
        self._prev_reader = None

    # ── lifecycle ───────────────────────────────────────────────────────────

    def _start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        # One cv2.VideoCapture per device at a time. release() only waits 2s
        # for its reader, and a DirectShow open alone can take longer, so a
        # stream released mid-open (a background camera check, say, just as
        # Teach New Part opens) could still be holding the device when the
        # next stream opened it -- two captures on one device, which crashes
        # the process outright. Waiting here, on the reader thread, keeps
        # the UI responsive while it happens.
        prev = self._prev_reader
        self._prev_reader = None
        if prev is not None:
            prev.join(_HANDOVER_TIMEOUT)
            if prev.is_alive():
                # Still stuck in the driver; opening now is the crash itself.
                self._running = False
                self._opened.set()
                return
        if not self._running:
            # Released while waiting -- never open the device at all.
            self._opened.set()
            return

        cap = cv2.VideoCapture(self.index, cv2.CAP_DSHOW)
        if cap.isOpened():
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
            self._cap = cap
            self._open_ok = True
        else:
            cap.release()
            self._running = False
        self._opened.set()

        # A device can open and then never deliver a frame (unplugged mid-session,
        # held by another process). Give up rather than let a reader block until
        # its timeout — an inspection stalling the test cycle is worse than a
        # fast "camera unavailable".
        consecutive_failures = 0
        while self._running:
            ret, frame = self._cap.read()
            if ret:
                code = _FLIP_CODES.get(_flipped(self.index))
                if code is not None:
                    frame = cv2.flip(frame, code)
                with self._frame_lock:
                    self._frame = frame
                    self._frames_read += 1
                consecutive_failures = 0
            else:
                consecutive_failures += 1
                if consecutive_failures >= _MAX_CONSECUTIVE_READ_FAILURES:
                    self._running = False
                    break
                time.sleep(0.02)

        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def wait_until_open(self, timeout: float = 5.0) -> bool:
        self._opened.wait(timeout)
        return self._open_ok

    def is_alive(self) -> bool:
        """False once the reader has given up on the device."""
        return self._running

    def release(self):
        with _REGISTRY_LOCK:
            self._refs -= 1
            if self._refs > 0:
                return
            _STREAMS.pop(self.index, None)
        self._running = False
        # Block until this device's cv2.VideoCapture is actually closed before
        # returning. DirectShow tolerates only one capture per device -- without
        # this, a caller that immediately re-acquires the same index (a new
        # CameraStream, on a new thread) can open a second cv2.VideoCapture
        # while this one is still mid-read or mid-release, which is a known way
        # to hard-crash (segfault) the process rather than raise a catchable
        # Python exception.
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    # ── frames ──────────────────────────────────────────────────────────────

    def latest(self) -> Optional[np.ndarray]:
        """Most recent frame without waiting, or None if none captured yet."""
        with self._frame_lock:
            return self._frame.copy() if self._frame is not None else None

    def read(self, timeout: float = 5.0, settle_frames: int = 30) -> Optional[np.ndarray]:
        """Newest frame, once the device has produced at least `settle_frames`.

        The settle count lets auto-exposure stabilise on a stream that has just
        been opened; an already-running preview satisfies it immediately because
        the counter is cumulative over the stream's life. 30 frames is ~1s at
        30fps -- judging a cold device sooner than that matches a template
        against a still-darkening frame and scores it far below what the same
        part scores once exposure settles.
        """
        if not self.wait_until_open(timeout):
            return None
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._frame_lock:
                if self._frames_read >= settle_frames and self._frame is not None:
                    return self._frame.copy()
            if not self._running:
                return None
            time.sleep(0.02)
        with self._frame_lock:
            return self._frame.copy() if self._frame is not None else None


def acquire(index: int, width: int = 640, height: int = 480) -> Optional[CameraStream]:
    """Reference-counted handle to the stream for `index`. Call release() when done."""
    if index < 0:
        return None
    with _REGISTRY_LOCK:
        stream = _STREAMS.get(index)
        if stream is None:
            # Pick up the saved flip settings whenever a device is opened.
            reload_flips()
            stream = CameraStream(index, width, height)
            _STREAMS[index] = stream
            stream._refs = 1
            prev = _LAST_READER.get(index)
            stream._prev_reader = prev if prev is not None and prev.is_alive() else None
            stream._start()
            _LAST_READER[index] = stream._thread
        else:
            stream._refs += 1
    return stream


def is_live(index: int) -> bool:
    with _REGISTRY_LOCK:
        return index in _STREAMS


def grab(index: int, width: int = 640, height: int = 480,
         timeout: float = 5.0) -> Optional[np.ndarray]:
    """One frame from `index`, reusing a live stream if one is already open."""
    stream = acquire(index, width, height)
    if stream is None:
        return None
    try:
        return stream.read(timeout=timeout)
    finally:
        stream.release()
