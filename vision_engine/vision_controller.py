"""
vision_engine/vision_controller.py
==================================
Webcam-based part verification using Template Matching (Normalized Cross-Correlation).

Methodology:
  - Teach: the operator collects reference images and boxes the part in each one.
    The image patch inside each box becomes a 'Template'. Boxes are per image,
    because a single box applied to every reference crops background wherever the
    part happened to sit elsewhere — and a background template matches the live
    background, which would pass an empty fixture.
  - Inspect: capture a live frame and search for the Template across it with
    cv2.matchTemplate.
  - Result: if the best match score >= threshold the part is present and correct.

Objects:
  A part number can be checked as up to MAX_OBJECTS separate objects (say, both
  connectors and the label). Each object has its own templates and is searched
  for exactly as a single-object part is, against the same threshold; the part
  is OK only when every object is. Combining the verdicts is the only new step,
  so an object is judged no more loosely than a whole part was. A model saved
  before objects existed reads as one object.

Wire colours:
  Each part number can also have its own wire-colour check, independent of
  its objects: a search area drawn around each connector (Connector 1,
  Connector 2) and the colours expected there, left to right. On a good part
  the strip where the wires run is found inside each area (locate_wires);
  wire_engine checks that strip on the line, sliding it within the area if
  the part sits elsewhere, and nothing outside the area. The checks are kept in
  vision_wires.json as one wire_engine profile per part number, apart from
  the template models, so re-teaching a part's pictures never touches them.
  Wire colours are checked first, then objects; a part with both is OK
  only when both pass.
"""
from __future__ import annotations

import configparser
import itertools
import json
import os
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Union

import cv2
import numpy as np

# The wire-colour check (wire_engine, from the FeederCable tester) is optional:
# without it a part is checked by its taught objects alone, and no part has
# wire colours to check.
try:
    import wire_engine
    from wire_engine.detector import _column_runs, wire_masks
except ImportError:
    wire_engine = None

from . import camera

DEFAULT_MATCH_THRESHOLD = 0.75
MAX_OBJECTS = 3
WIRE_CONNECTORS = ("Connector 1", "Connector 2")
MAX_WIRES = 8

# wire_engine colour palette, OpenCV HSV (H 0-179, S and V 0-255). Black,
# white and grey are achromatic: only pixels that are not clearly coloured
# count. Black also takes anything much darker than the box's background, as
# black insulation under the station lights reads as a dim grey. Override or
# add a colour with "colors" in vision_wires.json.
WIRE_COLORS = {
    "black":  {"hsv_ranges": [[[0, 0, 0], [179, 255, 60]]], "achromatic": True,
               "relative_dark": 0.55},
    "white":  {"hsv_ranges": [[[0, 0, 180], [179, 60, 255]]], "achromatic": True},
    "grey":   {"hsv_ranges": [[[0, 0, 61], [179, 60, 179]]], "achromatic": True},
    "red":    {"hsv_ranges": [[[0, 90, 56], [8, 255, 255]], [[170, 90, 56], [179, 255, 255]]]},
    "orange": {"hsv_ranges": [[[9, 120, 140], [20, 255, 255]]]},
    "brown":  {"hsv_ranges": [[[9, 60, 40], [20, 255, 139]]]},
    "yellow": {"hsv_ranges": [[[21, 90, 100], [34, 255, 255]]]},
    "green":  {"hsv_ranges": [[[35, 60, 40], [85, 255, 255]]]},
    "blue":   {"hsv_ranges": [[[86, 80, 40], [130, 255, 255]]]},
    "violet": {"hsv_ranges": [[[131, 50, 40], [169, 255, 255]]]},
}

_ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
_VISION_CFG_PATH = os.path.join(_ROOT_DIR, "vision_config.json")
_CAM_CFG_PATH = os.path.join(_ROOT_DIR, "camera_cfg.ini")
_MODELS_DIR = os.path.join(_ROOT_DIR, "vision_models")
_WIRES_PATH = os.path.join(_ROOT_DIR, "vision_wires.json")


@dataclass
class ObjectResult:
    """How one object of a part fared in an inspection."""
    name: str
    ok: bool
    score: float = 0.0
    box: Optional[Tuple[int, int, int, int]] = None         # (x, y, w, h) in frame


@dataclass
class VisionResult:
    """Structured result from a vision inspection."""
    ok: bool
    judgement: str              # "OK", "NG", "ERROR"
    part_number: str = ""
    match_score: float = 0.0    # 1.0 = perfect match, lower = worse
    threshold: float = 0.0
    processing_time_ms: int = 0
    error: Optional[str] = None

    # Diagnostics for the settings-page test view. Production ignores these;
    # they exist so the UI can show *where* the match landed instead of only a
    # number, without duplicating the matching logic outside inspect().
    match_box: Optional[Tuple[int, int, int, int]] = None   # (x, y, w, h) in frame
    frame: Optional[np.ndarray] = None                      # frame that was judged
    # One entry per taught object. match_score / match_box above describe the
    # weakest object, which is the one that decides the verdict -- for a
    # single-object part that is simply the part, as before.
    objects: List[ObjectResult] = field(default_factory=list)
    # The wire-colour check, if the part has one, and each detection's own
    # verdict (None when the part doesn't use that detection).
    wires: Optional[wire_engine.InspectionResult] = None
    objects_ok: Optional[bool] = None
    wires_ok: Optional[bool] = None


def _wires_look_real(wires, on, fills, h, min_fill):
    """Do these wire runs look like one cable's wires rather than a fixture?

    `wires` is [(start, end, score, colour)] left to right, in the columns of
    a band `h` rows tall; `on` the band's mask per expected colour and `fills`
    its column fill per colour. The wires must be long and thin, of similar
    widths, side by side, with background on both sides, and most of each
    colour in the band must be its wires: a jig block or plate of a wire's
    colour fails that. Returns (share, x1, x2) -- the weakest colour's share
    and the group's columns with a margin -- or None.
    """
    width = len(next(iter(fills.values())))
    widths = [e - s for s, e, _, _ in wires]
    if not widths or max(widths) > 2.0 * min(widths):
        return None
    med = float(np.median(widths))
    if 2 * med > h:
        return None                 # a wire is long and thin; this is a block
    centers = [(s + e) / 2 for s, e, _, _ in wires]
    if any(b - a > 4.0 * med for a, b in zip(centers, centers[1:])):
        return None
    # Background both sides: no other expected colour runs through the
    # columns just outside the group. (The edge wire's own colour may: a run
    # is the wire's core, and its soft edge spills past it. A block of that
    # colour beside it fails the share test.)
    need = max(3, int(med / 2))
    left, right = wires[0][0], wires[-1][1]
    if left < need or right > width - need:
        return None
    if any(float(fills[n][cols].max()) >= min_fill
           for cols, edge in ((np.r_[left - need:left], wires[0][3]),
                              (np.r_[right:right + need], wires[-1][3]))
           for n in fills if n != edge):
        return None
    pad = int(min(2 * med, left, width - right))
    x1, x2 = left - pad, right + pad
    share = 1.0
    for n, mask in on.items():
        total = int(mask[:, x1:x2].sum())
        inside = sum(int(mask[:, max(0, s - 2):e + 2].sum()) for s, e, _, wn in wires if wn == n)
        share = min(share, inside / total if total else 0.0)
    return (share, x1, x2) if share >= 0.5 else None


def _load_wires_file() -> dict:
    """vision_wires.json: {"profiles": {part_number: wire_engine profile}, and
    optionally "colors" / "detection" overriding wire_engine's settings}."""
    if wire_engine is not None and os.path.exists(_WIRES_PATH):
        try:
            data = wire_engine.load_config(_WIRES_PATH)
            if isinstance(data, dict):
                data.setdefault("profiles", {})
                return data
        except (OSError, ValueError):
            pass
    return {"profiles": {}}


def _save_wires_file(data: dict):
    if wire_engine is None:
        raise ValueError("Wire-colour checks need wire_engine, which is not installed.")
    # Written aside and swapped in, so a crash mid-write can't leave every
    # part's wire checks unreadable.
    tmp = _WIRES_PATH + ".tmp"
    wire_engine.save_config(data, tmp)
    os.replace(tmp, _WIRES_PATH)


def _default_config() -> dict:
    return {
        "vision_enabled": True,
        "camera_source": "cam1",
        "match_threshold": DEFAULT_MATCH_THRESHOLD,
        "part_mapping": {},
    }


def load_vision_config() -> dict:
    cfg = _default_config()
    if os.path.exists(_VISION_CFG_PATH):
        try:
            with open(_VISION_CFG_PATH, "r") as f:
                cfg.update(json.load(f))
        except (OSError, ValueError):
            pass
    return cfg


def save_vision_config(cfg: dict):
    with open(_VISION_CFG_PATH, "w") as f:
        json.dump(cfg, f, indent=4)
        f.write("\n")


def get_vision_controller() -> "VisionController":
    return VisionController()


class VisionController:
    """Headless part verification against taught templates."""

    def __init__(self):
        self.config = load_vision_config()
        self._model_cache: Dict[str, dict] = {}

    def reload_config(self):
        self.config = load_vision_config()
        self._model_cache.clear()

    # ── Camera ──────────────────────────────────────────────────────────────

    def cam_settings(self) -> tuple:
        """(index, width, height) for the configured camera source."""
        source = self.config.get("camera_source", "cam1")
        if not os.path.exists(_CAM_CFG_PATH):
            return -1, 640, 480
        cfg = configparser.ConfigParser()
        cfg.read(_CAM_CFG_PATH)
        return (
            cfg.getint("CAMERA", f"{source}_index", fallback=-1),
            cfg.getint("CAMERA", f"{source}_width", fallback=640),
            cfg.getint("CAMERA", f"{source}_height", fallback=480),
        )

    def _capture_frame(self) -> Optional[np.ndarray]:
        index, width, height = self.cam_settings()
        if index < 0:
            return None
        return camera.grab(index, width, height)

    def get_status(self) -> str:
        index, width, height = self.cam_settings()
        if index < 0:
            return "NO_CAMERA"
        return "READY" if camera.grab(index, width, height) is not None else "CAMERA_ERROR"

    # ── Model I/O ───────────────────────────────────────────────────────────

    def _model_path(self, part_number: str) -> Optional[str]:
        filename = self.config.get("part_mapping", {}).get(part_number)
        if not filename:
            return None
        path = os.path.join(_MODELS_DIR, filename)
        return path if os.path.exists(path) else None

    def _load_model(self, part_number: str) -> Optional[dict]:
        if part_number in self._model_cache:
            return self._model_cache[part_number]

        path = self._model_path(part_number)
        if path is None:
            return None

        try:
            data = np.load(path, allow_pickle=True)
            model_cfg = json.loads(str(data["config"]))

            objects = []
            for k, meta in enumerate(model_cfg.get("pieces") or [{}]):
                # Object 1 keeps the pre-objects key names, so a model saved
                # before objects existed loads as a one-object model untouched.
                # The file says "pieces" / "piece{k}_" -- objects' first name --
                # and keeps saying it, so models already taught still load.
                prefix = "template_" if k == 0 else f"piece{k}_template_"
                templates = []
                i = 0
                while f"{prefix}{i}" in data:
                    templates.append(data[f"{prefix}{i}"])
                    i += 1
                name = meta.get("name") or ""
                if name == f"Piece {k + 1}":
                    name = ""           # the old default name, shown as the new one
                objects.append({"name": name or f"Object {k + 1}",
                               "templates": templates})

            model_cfg["objects"] = objects
            model_cfg["templates"] = objects[0]["templates"]
            self._model_cache[part_number] = model_cfg
            return model_cfg
        except (OSError, ValueError, KeyError):
            return None

    def has_model(self, part_number: str) -> bool:
        return self._model_path(part_number) is not None

    def get_mapped_parts(self) -> dict:
        return dict(self.config.get("part_mapping", {}))

    # ── Wire colours ────────────────────────────────────────────────────────

    def wire_profile(self, part_number: str) -> Optional[dict]:
        """A part's wire-colour check as a wire_engine profile, or None."""
        profile = _load_wires_file()["profiles"].get(part_number)
        return profile if profile and profile.get("regions") else None

    def has_wire_checks(self, part_number: str) -> bool:
        return self.wire_profile(part_number) is not None

    def has_vision(self, part_number: str) -> bool:
        """Does the part have anything for vision to check -- taught objects,
        wire colours, or both?"""
        return self.has_model(part_number) or self.has_wire_checks(part_number)

    def parts_with_wires(self) -> List[str]:
        return sorted(p for p, prof in _load_wires_file()["profiles"].items()
                      if prof and prof.get("regions"))

    @staticmethod
    def wire_color_names() -> List[str]:
        names = dict.fromkeys(WIRE_COLORS)
        names.update(dict.fromkeys(_load_wires_file().get("colors") or {}))
        return list(names)

    def _wire_engine_config(self, part_number: str, profile: dict) -> dict:
        data = _load_wires_file()
        colors = dict(WIRE_COLORS)
        colors.update(data.get("colors") or {})
        return {"colors": colors, "detection": data.get("detection") or {},
                "profiles": {part_number: profile}}

    def locate_wires(self, frame: np.ndarray, search_box, expected: List[str]) -> Optional[dict]:
        """Find the wires box inside a connector's search area on a good part.

        A wire only counts where it runs through most of the box's height, so
        a box around the whole connector (boot, grommet, foam) misses a wire
        that shows only briefly. This scans bands of several heights down
        `search_box` (x1, y1, x2, y2, frame pixels) for the expected wires in
        order, shaped like one cable -- similar widths, side by side, with
        background on both sides -- and keeps the band where the wires'
        colours belong most to the wires themselves (a jig block of a wire's
        colour fails that), then the cleanest read, then the taller band. The
        winner must read the same through wire_engine, as it will on the line.
        Returns {"box", "wire_signature"} in frame pixels, or None.
        """
        cfg = self._wire_engine_config("", {})
        colors, det = cfg["colors"], cfg["detection"]
        min_fill = det.get("min_column_fill", 0.35)
        min_width = det.get("min_wire_width_px", 3)
        min_sat = det.get("colored_min_saturation", 110)
        min_chroma = det.get("colored_min_chroma", 40)
        max_ratio = det.get("max_wire_width_ratio", 2.5)

        fh, fw = frame.shape[:2]
        x1, y1, x2, y2 = (int(v) for v in search_box)
        x1, x2 = sorted((max(0, x1), min(fw, x2)))
        y1, y2 = sorted((max(0, y1), min(fh, y2)))
        if x2 - x1 < 8 or y2 - y1 < 8 or not expected:
            return None
        expected = list(expected)
        area = frame[y1:y2, x1:x2]
        ah, aw = area.shape[:2]
        counts = Counter(expected)
        names = list(counts)

        on = {n: m > 0 for n, m in wire_masks(area, colors, names, min_sat, min_chroma).items()}
        # Column sums of each colour over any band of rows, in O(1) per band.
        cum = {n: np.vstack([np.zeros((1, aw), np.float32),
                             np.cumsum(m, axis=0, dtype=np.float32)]) for n, m in on.items()}

        candidates = []         # (key, box in area coordinates)
        for h in sorted({int(ah * k) for k in (1.0, 0.8, 0.65, 0.5, 0.4, 0.3, 0.22, 0.15, 0.1)},
                        reverse=True):
            if h < 12:
                continue
            for y0 in range(0, ah - h + 1, max(3, h // 5)):
                fills = {n: (cum[n][y0 + h] - cum[n][y0]) / h for n in names}
                runs = {n: _column_runs(fills[n], min_fill, min_width, counts[n] + 3)
                        for n in names}
                if any(len(runs[n]) < counts[n] for n in names):
                    continue
                for pick in itertools.product(*(itertools.combinations(runs[n], counts[n])
                                                for n in names)):
                    wires = sorted((s, e, sc, n) for n, group in zip(names, pick)
                                   for s, e, sc in group)
                    if [w[3] for w in wires] != expected:
                        continue
                    real = _wires_look_real(wires, {n: on[n][y0:y0 + h] for n in names},
                                            fills, h, min_fill)
                    if real is None:
                        continue
                    share, bx1, bx2 = real
                    key = (round(share, 1), round(min(w[2] for w in wires), 1), h)
                    candidates.append((key, (bx1, y0, bx2, y0 + h)))

        for _, (bx1, by1, bx2, by2) in sorted(candidates, key=lambda c: c[0], reverse=True)[:20]:
            dets = wire_engine.detect_wires(area[by1:by2, bx1:bx2], colors, counts, min_fill,
                                            min_width, min_sat, min_chroma, max_ratio)
            if [d.color for d in dets] != expected:
                continue
            box = (x1 + bx1, y1 + by1, x1 + bx2, y1 + by2)
            signature = wire_engine.wire_signature(frame, box, None, colors, expected, det)
            if signature is not None:
                return {"box": [int(v) for v in box], "wire_signature": signature}
        return None

    def inspect_wires(self, part_number: str, frame: np.ndarray,
                      profile: Optional[dict] = None) -> Optional[wire_engine.InspectionResult]:
        """Run wire_engine over the part's connector boxes in `frame`.

        `profile` stands in for the saved one, so the wire editor can check
        boxes and colours it has not saved yet through exactly this path.
        """
        profile = profile or self.wire_profile(part_number)
        if not profile:
            return None
        frame = camera.for_vision(frame)
        cfg = self._wire_engine_config(part_number, profile)
        result = wire_engine.inspect(frame, cfg, part_number)

        # A strip found by sliding over the search area (the part sat
        # elsewhere) only counts if it looks like real wires, by the same
        # rules the strip was found with: a jig block and a dark gap of the
        # right colours can match the wires' widths and spacing. Otherwise
        # the connector is judged on its saved strip alone.
        det = cfg["detection"]
        min_fill = det.get("min_column_fill", 0.35)
        for i, (region, res) in enumerate(zip(profile["regions"], result.regions)):
            if res.located_by != "wires" or not res.passed:
                continue
            x1, y1, x2, y2 = res.box
            on = {n: m > 0 for n, m in wire_masks(
                frame[y1:y2, x1:x2], cfg["colors"], list(Counter(res.expected_order)),
                det.get("colored_min_saturation", 110), det.get("colored_min_chroma", 40)).items()}
            wires = [(d.x_start, d.x_end, d.score, d.color) for d in res.detections]
            if _wires_look_real(wires, on, {n: m.mean(axis=0) for n, m in on.items()},
                                y2 - y1, min_fill) is None:
                fixed = {k: v for k, v in region.items() if k != "wire_signature"}
                cfg["profiles"][part_number] = dict(profile, regions=[fixed])
                result.regions[i] = wire_engine.inspect(frame, cfg, part_number).regions[0]
                cfg["profiles"][part_number] = profile
        return result

    def set_wire_profile(self, part_number: str, image_size: Tuple[int, int],
                         regions: List[dict]):
        """Save a part's wire check: `regions` is [{"name", "box": [x1, y1, x2, y2],
        "expected_order": [colour, ...]}] in `image_size` (w, h) pixels, each
        optionally with the "search_box" it was found in and its
        "wire_signature" (see locate_wires)."""
        known = set(self.wire_color_names())
        for r in regions:
            order = r.get("expected_order") or []
            if not 1 <= len(order) <= MAX_WIRES:
                raise ValueError(f"{r.get('name')} needs 1 to {MAX_WIRES} wire colours.")
            unknown = [c for c in order if c not in known]
            if unknown:
                raise ValueError(f"{r.get('name')}: unknown colour {', '.join(unknown)}.")
            x1, y1, x2, y2 = r.get("box") or (0, 0, 0, 0)
            if x2 - x1 < 4 or y2 - y1 < 4:
                raise ValueError(f"{r.get('name')} has no box drawn.")
        if not regions:
            raise ValueError("Set up at least one connector.")
        data = _load_wires_file()
        data["profiles"][part_number] = {
            "image_size": [int(image_size[0]), int(image_size[1])],
            "regions": [dict({"name": r["name"], "box": [int(v) for v in r["box"]],
                              "expected_order": list(r["expected_order"])},
                             **({"search_box": [int(v) for v in r["search_box"]],
                                 "wire_signature": r["wire_signature"]}
                                if r.get("search_box") and r.get("wire_signature") else {}))
                        for r in regions],
        }
        _save_wires_file(data)

    def delete_wire_profile(self, part_number: str):
        data = _load_wires_file()
        if data["profiles"].pop(part_number, None) is not None:
            _save_wires_file(data)

    # ── Production Inspection ───────────────────────────────────────────────

    def inspect(self, part_number: str, frame: Optional[np.ndarray] = None) -> VisionResult:
        """Judge one frame against a part's wire colours, then its taught objects.

        Wire colours are the first check and object detection follows. The
        two are independent: a part may have either or both, each is
        judged on its own (objects_ok / wires_ok), and the part is OK only
        when every detection it has passes. By default captures a fresh frame
        from the configured camera — the same path production uses. Pass
        `frame` to judge a still image instead, without needing the part in
        front of a camera at all.
        """
        start = time.time()

        def _error(msg: str) -> VisionResult:
            return VisionResult(
                ok=False, judgement="ERROR", part_number=part_number, error=msg,
                processing_time_ms=int((time.time() - start) * 1000),
            )

        if not self.config.get("vision_enabled", True):
            return _error("Vision inspection disabled")

        model = self._load_model(part_number)
        wire_profile = self.wire_profile(part_number)
        if model is None and wire_profile is None:
            return _error(f"No vision model or wire colours for '{part_number}'")

        objects = model.get("objects", []) if model is not None else []
        if model is not None and (not objects or not all(p["templates"] for p in objects)):
            return _error("Model contains no templates")

        if frame is None:
            frame = self._capture_frame()
            if frame is None:
                return _error("Camera not available")
        # Judged at the reduced vision size, the size every part is taught at
        # (see camera.VISION_WIDTH) -- a no-op for frames already that small.
        frame = camera.for_vision(frame)

        # ── 1. Wire colours ─────────────────────────────────────────────────
        wires, wires_ok = None, None
        if wire_profile is not None:
            wires = self.inspect_wires(part_number, frame, wire_profile)
            wires_ok = wires.passed
            for reg in wires.regions:
                print(f"[VISION DEBUG] pno={part_number} wires={reg.name!r} "
                      f"expected={reg.expected_order} found={reg.detected_order} "
                      f"ok={reg.passed}")

        # ── 2. Object detection ─────────────────────────────────────────────
        gray_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        threshold = (model or {}).get("match_threshold",
                                      self.config.get("match_threshold", DEFAULT_MATCH_THRESHOLD))

        # Each object is searched for on its own, exactly as a whole part used
        # to be: the best of its templates anywhere in the frame.
        results = []
        for obj in objects:
            templates = obj["templates"]
            best_score = -1.0
            best_box = None
            compared = 0
            for template in templates:
                if template.shape[0] > gray_frame.shape[0] or template.shape[1] > gray_frame.shape[1]:
                    continue
                res = cv2.matchTemplate(gray_frame, template, cv2.TM_CCOEFF_NORMED)
                _, max_val, _, max_loc = cv2.minMaxLoc(res)
                if max_val > best_score:
                    best_score = max_val
                    best_box = (max_loc[0], max_loc[1], template.shape[1], template.shape[0])
                compared += 1

            print(f"[VISION DEBUG] pno={part_number} object={obj['name']!r} "
                  f"frame={gray_frame.shape[1]}x{gray_frame.shape[0]} "
                  f"brightness={gray_frame.mean():.1f} templates={[(t.shape[1], t.shape[0]) for t in templates]} "
                  f"compared={compared} best_score={best_score:.3f}")

            if compared == 0:
                return _error(
                    f"Every template{' of ' + obj['name'] if len(objects) > 1 else ''} "
                    f"is larger than this frame "
                    f"({gray_frame.shape[1]}x{gray_frame.shape[0]}) — "
                    f"re-teach the part at this resolution, or use a larger test image"
                )
            results.append(ObjectResult(name=obj["name"], ok=best_score >= threshold,
                                       score=best_score, box=best_box))

        weakest = min(results, key=lambda r: r.score) if results else None
        objects_ok = all(r.ok for r in results) if results else None

        elapsed = int((time.time() - start) * 1000)

        ok = objects_ok is not False and wires_ok is not False
        problems = []
        if wires_ok is False:
            problems.append("Wires: " + "; ".join(
                f"{r.name} {r.message}" for r in wires.regions if not r.passed))
        if objects_ok is False:
            if len(results) == 1:
                problems.append(f"No match found (score {weakest.score:.2f} < {threshold})")
            else:
                problems.append("Not found: " + ", ".join(
                    f"{r.name} ({r.score:.2f})" for r in results if not r.ok))
        return VisionResult(
            ok=ok, judgement="OK" if ok else "NG", part_number=part_number,
            match_score=weakest.score if weakest else 0.0, threshold=threshold,
            processing_time_ms=elapsed,
            error=" · ".join(problems) or None,
            match_box=weakest.box if weakest else None, frame=frame, objects=results,
            wires=wires, objects_ok=objects_ok, wires_ok=wires_ok,
        )

    # ── Model Building ──────────────────────────────────────────────────────

    def build_and_save_model(
        self, part_number: str, images: List[np.ndarray],
        roi: Union[dict, List[dict], List[List[dict]]],
        match_threshold: float = DEFAULT_MATCH_THRESHOLD,
        object_names: Optional[List[str]] = None,
    ) -> str:
        """Crop the templates for every object of a part and save them as its model.

        `roi` is a single box applied to every image, one box per image, or --
        for a part checked as several objects -- one list of boxes per image,
        holding each object's box in the same order on every image. Per-image
        boxes matter whenever the part is not rigidly fixtured: a shared box
        lands on background in any reference where the part sat elsewhere, and a
        background template matches the live background at a high score — which
        would pass an empty fixture.
        """
        rois = list(roi) if isinstance(roi, (list, tuple)) else [roi] * len(images)
        if len(rois) != len(images):
            raise ValueError(
                f"Got {len(rois)} regions for {len(images)} reference images."
            )
        # Normalise to one list of boxes per image.
        rois = [list(r) if isinstance(r, (list, tuple)) else [r] for r in rois]
        n_objects = len(rois[0]) if rois else 0
        if not 1 <= n_objects <= MAX_OBJECTS:
            raise ValueError(f"A part needs 1 to {MAX_OBJECTS} objects, got {n_objects}.")
        if any(len(r) != n_objects for r in rois):
            raise ValueError("Every reference image needs a box for every object.")
        names = [(n or "").strip() for n in list(object_names or [])[:n_objects]]
        names += [""] * (n_objects - len(names))
        names = [n or f"Object {k + 1}" for k, n in enumerate(names)]

        def _where(n, k):
            return f"reference image {n}" + (f", {names[k]}" if n_objects > 1 else "")

        templates = [[] for _ in range(n_objects)]
        for n, (img, boxes) in enumerate(zip(images, rois), start=1):
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img
            for k, r in enumerate(boxes):
                if r is None:
                    raise ValueError(f"No region marked on {_where(n, k)}.")
                x, y, w, h = r["x"], r["y"], r["width"], r["height"]
                if w < 10 or h < 10:
                    raise ValueError(f"Region on {_where(n, k)} is too small.")
                if y + h > gray.shape[0] or x + w > gray.shape[1]:
                    raise ValueError(f"Region on {_where(n, k)} falls outside it.")
                templates[k].append(gray[y:y + h, x:x + w])

        model_cfg = {
            "part_number": part_number,
            "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "pieces": [{"name": names[k], "rois": [b[k] for b in rois]}
                       for k in range(n_objects)],
            "rois": [b[0] for b in rois],   # object 1, for readers from before objects
            "roi": rois[0][0],              # older readers expect a single region
            "match_threshold": match_threshold,
            "num_references": len(images),
        }

        os.makedirs(_MODELS_DIR, exist_ok=True)
        filename = f"{part_number}.npz"
        model_path = os.path.join(_MODELS_DIR, filename)

        save_dict = {"config": json.dumps(model_cfg)}
        for k, object_templates in enumerate(templates):
            prefix = "template_" if k == 0 else f"piece{k}_template_"
            for i, t in enumerate(object_templates):
                save_dict[f"{prefix}{i}"] = t
        np.savez_compressed(model_path, **save_dict)

        self.config.setdefault("part_mapping", {})[part_number] = filename
        save_vision_config(self.config)
        self._model_cache.pop(part_number, None)

        return model_path

    def delete_model(self, part_number: str):
        path = self._model_path(part_number)
        if path and os.path.exists(path):
            os.remove(path)
        self.config.get("part_mapping", {}).pop(part_number, None)
        save_vision_config(self.config)
        self._model_cache.pop(part_number, None)

    def model_info(self, part_number: str) -> Optional[dict]:
        """Metadata for a taught part, or None if it has no usable model."""
        model = self._load_model(part_number)
        if model is None:
            return None
        templates = model.get("templates", [])
        roi = model.get("roi") or {}
        rois = model.get("rois") or ([roi] if roi else [])
        sizes = ([(t.shape[1], t.shape[0]) for t in templates] if templates else
                 [(r.get("width", 0), r.get("height", 0)) for r in rois])
        objects = model.get("objects", [])
        return {
            "objects": len(objects),
            "object_names": [p["name"] for p in objects],
            "object_template_sizes": [
                (p["templates"][0].shape[1], p["templates"][0].shape[0])
                if p["templates"] else (0, 0) for p in objects],
            "references": model.get("num_references", len(templates)),
            "created": model.get("created", "—"),
            "threshold": model.get("match_threshold", self.config.get("match_threshold", DEFAULT_MATCH_THRESHOLD)),
            "roi": roi,
            "rois": rois,
            "template_size": sizes[0] if sizes else (0, 0),
            "template_sizes": sizes,
            "uniform_templates": len(set(sizes)) <= 1,
        }

    def set_model_threshold(self, part_number: str, threshold: float):
        """Rewrite a taught model's own threshold.

        A model carries the threshold it was taught with and that value wins over
        the global default at inspection time, so tuning a part has to reach into
        the .npz rather than the config file.
        """
        path = self._model_path(part_number)
        if path is None:
            raise ValueError(f"No model file mapped to '{part_number}'.")

        data = np.load(path, allow_pickle=True)
        model_cfg = json.loads(str(data["config"]))
        model_cfg["match_threshold"] = float(threshold)

        # Every array rides along untouched -- all objects' templates, not just
        # the first object's.
        save_dict = {k: data[k] for k in data.files if k != "config"}
        save_dict["config"] = json.dumps(model_cfg)
        data.close()
        np.savez_compressed(path, **save_dict)
        self._model_cache.pop(part_number, None)

    def map_model_file(self, part_number: str, filename: str):
        """Point a part number at an existing model file in vision_models/."""
        if not os.path.exists(os.path.join(_MODELS_DIR, filename)):
            raise ValueError(f"Model file '{filename}' not found.")
        self.config.setdefault("part_mapping", {})[part_number] = filename
        save_vision_config(self.config)
        self._model_cache.pop(part_number, None)
