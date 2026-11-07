"""Force / stroke acquisition during a measuring stroke (reuses the existing
serial loadcell and NI DAQ modules).

machine_profile.measurement:
    force_source:  "serial" (KOMEIN loadcells, default) or "daq" (NI DAQ sensor rows)
    loadcell:      {"select": 1, "shift": 2}        serial loadcell number per cable
    daq_force:     {"select": "Loadcell 1", "shift": "Loadcell 2"}   DAQ sensor names
    stroke:        {"select": "", "shift": ""}      DAQ sensor name of the LVDT, "" = none
The cable assignment, calibration, units and sign are NOT established by the
PLC program; the defaults follow the Test Console (SELECT = Loadcell 01,
SHIFT = Loadcell 02) and must be confirmed on the machine.

    calibration:   {"select": {"scale": 1.0, "offset": 0.0, "unit": "", "verified": False}, ...}
                   load = reading * scale + offset (serial: indicator value; daq: sensor row value)
start() begins a capture on its own thread before the stroke request is
written; stop() ends it and returns [(t, load, stroke, raw_load, raw_stroke), ...].
A failed read records None (invalid) - no other sensor is substituted.
"""

import threading
import time


class Capture:
    def __init__(self, axis, kind, test_id, stroke_no):
        self.axis, self.kind, self.test_id, self.stroke_no = axis, kind, test_id, stroke_no
        self.samples = []
        self.errors = 0
        self.stop_event = threading.Event()
        self.thread = None


class Sampler:
    def __init__(self, loadcells, daq, settings, period=0.02):
        self.loadcells = loadcells
        self.daq = daq
        self.settings = settings
        self.period = period
        self.latest = {}               # axis -> (load, stroke) for the screen
        self._active = set()           # axes being captured (the screen's poll leaves them alone)

    @property
    def mcfg(self):
        return self.settings["test"]["machine_profile"]["measurement"]

    def active_axes(self):
        return set(self._active)

    def _sensor(self, name):
        for s in self.settings["daq"]["sensors"]:
            if s["name"] == name:
                return s
        return None

    def _daq_value(self, name):
        """(value, volts) of a DAQ sensor row, or (None, None)."""
        s = self._sensor(name)
        if not s or self.daq is None or not self.daq.is_open:
            return None, None
        volts = self.daq.read_average().get(s["channel"])
        if volts is None:
            return None, None
        return (volts - float(s.get("offset_v", 0.0))) * float(s.get("scale", 1.0)), volts

    def describe(self, axis):
        """Source, channel, units and calibration status recorded with each result."""
        m = self.mcfg
        cal = (m.get("calibration") or {}).get(axis) or {}
        if m.get("force_source", "serial") == "daq":
            name = (m.get("daq_force") or {}).get(axis, "")
            s = self._sensor(name) or {}
            force = {"source": "NI DAQ", "channel": f"{name} {s.get('channel', '?')}", "unit": s.get("unit", "")}
        else:
            force = {"source": "serial loadcell", "channel": f"L{(m.get('loadcell') or {}).get(axis, '?')}",
                     "unit": cal.get("unit", "")}
        stroke = (m.get("stroke") or {}).get(axis, "")
        return {"force": force, "stroke": stroke or None,
                "calibration": {"scale": cal.get("scale", 1.0), "offset": cal.get("offset", 0.0),
                                "unit": cal.get("unit", ""), "verified": bool(cal.get("verified"))},
                "period_s": self.period}

    def read_once(self, axis):
        m = self.mcfg
        raw_load = stroke = raw_stroke = None
        if m.get("force_source", "serial") == "daq":
            value, raw_load = self._daq_value((m.get("daq_force") or {}).get(axis, ""))
        else:
            value = None
            n = int((m.get("loadcell") or {}).get(axis, 0) or 0)
            lc = self.loadcells[n - 1] if 1 <= n <= len(self.loadcells) else None
            if lc is not None and lc.is_open:
                _sent, raw_load, value = lc.read()
        cal = (m.get("calibration") or {}).get(axis) or {}
        load = None if value is None else value * float(cal.get("scale", 1.0)) + float(cal.get("offset", 0.0))
        name = (m.get("stroke") or {}).get(axis, "")
        if name:
            stroke, raw_stroke = self._daq_value(name)
        return load, stroke, raw_load, raw_stroke

    def start(self, axis, kind, test_id, stroke_no):
        cap = Capture(axis, kind, test_id, stroke_no)

        def run():
            self._active.add(axis)
            try:
                while not cap.stop_event.is_set():
                    t = time.monotonic()
                    try:
                        load, stroke, raw_load, raw_stroke = self.read_once(axis)
                    except Exception:
                        load = stroke = raw_load = raw_stroke = None
                        cap.errors += 1
                    cap.samples.append((t, load, stroke, raw_load, raw_stroke))
                    self.latest[axis] = (load, stroke)
                    cap.stop_event.wait(self.period)
            finally:
                self._active.discard(axis)

        cap.thread = threading.Thread(target=run, daemon=True)
        cap.thread.start()
        return cap

    def stop(self, cap):
        cap.stop_event.set()
        if cap.thread:
            cap.thread.join(2.0)
        return list(cap.samples)
