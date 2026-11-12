"""Force / stroke acquisition during a measuring stroke (reuses the existing
serial loadcell and NI DAQ modules).

machine_profile.measurement:
    force_source:  "serial" (KOMEIN loadcells, default) or "daq" (NI DAQ sensor rows)
    loadcell:      {"select": 1, "shift": 2}        serial loadcell number per cable
    daq_force:     {"select": "Loadcell 1", "shift": "Loadcell 2"}   DAQ sensor names
    stroke:        {"select": "", "shift": ""}      DAQ sensor name of the LVDT, "" = none
    graph_force:   "result" (graph = the result load) or "daq" (graph = the NI card daq_force row,
                   results stay on force_source)
The NI card task is opened here (device / wiring / sensor rows from the COM Port
page) for the rows these name, with a ~10 ms averaging block per sample.
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
        self.graph = []                # [(t, graph load, stroke), ...] for the Test Console graph
        self.graph_note = None         # why the graph load is not the NI card's (shown on the graph)
        self.lvdt_note = None          # why the last sample has no LVDT stroke (graph error popup)
        self.errors = 0
        self.last_error = None         # text of the last failed read (why samples are invalid)
        self.stop_event = threading.Event()
        self.thread = None


class Sampler:
    DAQ_RETRY_S = 5.0

    def __init__(self, loadcells, daq, settings, period=0.02):
        self.loadcells = loadcells
        self.daq = daq
        self.settings = settings
        self.period = period
        self.latest = {}               # axis -> (load, stroke) for the screen
        self._active = set()           # axes being captured (the screen's poll leaves them alone)
        self.captures = {}             # axis -> the latest Capture (the graph draws its samples)
        self.daq_error = None          # why the NI card could not be opened (retried each stroke
        self._daq_failed_at = 0.0      # and every DAQ_RETRY_S seconds)
        self._daq_lock = threading.Lock()
        self._daq_cfg = None           # the task settings this sampler opened the NI card with

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

    def _daq_names(self):
        m = self.mcfg
        names = [(m.get("stroke") or {}).get(a, "") for a in ("select", "shift")]
        if m.get("force_source", "serial") == "daq" or m.get("graph_force") == "daq":
            names += [(m.get("daq_force") or {}).get(a, "") for a in ("select", "shift")]
        return [n for n in names if n]

    def _ensure_daq(self):
        """Open the NI card task for the sensor rows the test reads (COM Port page
        settings). A short averaging block (~10 ms) keeps the sample rate up."""
        if self.daq is None:
            return False
        d = self.settings.get("daq") or {}
        chans = []
        for n in self._daq_names():
            sen = self._sensor(n)
            if sen and sen["channel"] not in [c["channel"] for c in chans]:
                chans.append({"channel": sen["channel"], "range": sen.get("range", "±10 V")})
        rate = float(d.get("sample_rate") or 1000.0)
        cfg = {"device": d.get("device", ""), "terminal_config": d.get("terminal_config", "Differential"),
               "sample_rate": rate, "samples_per_channel": max(1, int(rate * 0.01)), "channels": chans}
        # An open task is kept only while it still matches the settings: a channel, range,
        # wiring or device changed on the COM Port page reopens it instead of reading the old one.
        if self.daq.is_open and cfg == self._daq_cfg:
            return True
        if self.daq_error and time.monotonic() - self._daq_failed_at < self.DAQ_RETRY_S:
            return False
        self._daq_cfg = None
        try:
            if not chans:
                raise ValueError("no NI card sensor rows match the stroke / force names")
            self.daq.connect(cfg)
        except Exception as exc:
            self.daq_error = str(exc) or type(exc).__name__
            self._daq_failed_at = time.monotonic()
            return False
        self._daq_cfg = cfg
        self.daq_error = None
        return True

    def _daq_value(self, name, block=None):
        """(value, volts) of a DAQ sensor row, or (None, None). `block` is one
        read_average() result, so load and stroke come from the same acquisition."""
        s = self._sensor(name)
        if not s or self.daq is None or not self.daq.is_open:
            return None, None
        volts = (block if block is not None else self.daq.read_average()).get(s["channel"])
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
        graph = (f"NI DAQ {(m.get('daq_force') or {}).get(axis, '')}" if m.get("graph_force") == "daq"
                 else "result force")
        return {"force": force, "stroke": stroke or None, "graph_force": graph,
                "calibration": {"scale": cal.get("scale", 1.0), "offset": cal.get("offset", 0.0),
                                "unit": cal.get("unit", ""), "verified": bool(cal.get("verified"))},
                "period_s": self.period}

    def read_once(self, axis, graph=None):
        """(load, stroke, raw_load, raw_stroke) for the result. With graph_force = "daq"
        the graph load is the NI card's daq_force row (the result load stays the serial
        loadcell); `graph`, a dict, receives {"load", "stroke"} even if the result read fails,
        and graph["lvdt"] says why when there is no LVDT stroke.
        When the NI card gives no load the graph falls back to the result load and
        graph["note"] says why."""
        m = self.mcfg
        raw_load = stroke = raw_stroke = None
        name = (m.get("stroke") or {}).get(axis, "")
        block = None
        force_daq = m.get("force_source", "serial") == "daq"
        graph_daq = m.get("graph_force") == "daq"
        if force_daq or graph_daq or name:
            with self._daq_lock:
                try:
                    if not self._ensure_daq():
                        raise RuntimeError(f"NI card not open: {self.daq_error or 'no device'}")
                    block = self.daq.read_average()
                except Exception:
                    if force_daq:
                        raise
                    block = {}         # NI card read failed: no stroke, the serial force still counts
        if name:
            stroke, raw_stroke = self._daq_value(name, block)
        if graph is not None:
            graph["stroke"] = stroke
            if stroke is None:                 # why there is no LVDT stroke (the graph needs it)
                graph["lvdt"] = ("no LVDT sensor row is selected (measurement.stroke)" if not name
                                 else "NI card: " + self.daq_error if self.daq_error
                                 else f"no reading for '{name}'" if self._sensor(name)
                                 else f"no sensor row named '{name}'")
            graph["load"] = None
            if graph_daq:
                row = (m.get("daq_force") or {}).get(axis, "")
                graph["load"] = self._daq_value(row, block)[0]
                if graph["load"] is None:      # why the NI card gave no graph load (shown on the graph)
                    graph["note"] = "NI card: " + (self.daq_error or (
                        f"no reading for '{row}'" if self._sensor(row) else f"no sensor row named '{row}'"))
        if force_daq:
            value, raw_load = self._daq_value((m.get("daq_force") or {}).get(axis, ""), block)
        else:
            value = None
            n = int((m.get("loadcell") or {}).get(axis, 0) or 0)
            lc = self.loadcells[n - 1] if 1 <= n <= len(self.loadcells) else None
            if lc is None or not lc.is_open:
                raise RuntimeError(f"loadcell L{n} is not connected (COM Port page)")
            _sent, raw_load, value = lc.read()
        cal = (m.get("calibration") or {}).get(axis) or {}
        load = None if value is None else value * float(cal.get("scale", 1.0)) + float(cal.get("offset", 0.0))
        if graph is not None and graph["load"] is None:
            graph["load"] = load       # graph_force "result", or the NI card gave nothing: the result load
            if graph_daq and load is not None:
                graph["note"] += " - graph drawn from the result load cell"
        return load, stroke, raw_load, raw_stroke

    def start(self, axis, kind, test_id, stroke_no):
        cap = Capture(axis, kind, test_id, stroke_no)
        self.captures[axis] = cap
        self.daq_error = None          # try the NI card again for this stroke

        def run():
            self._active.add(axis)
            try:
                while not cap.stop_event.is_set():
                    t = time.monotonic()
                    g = {}
                    try:
                        load, stroke, raw_load, raw_stroke = self.read_once(axis, g)
                    except Exception as exc:
                        load = stroke = raw_load = raw_stroke = None
                        cap.errors += 1
                        cap.last_error = str(exc) or type(exc).__name__
                    cap.samples.append((t, load, stroke, raw_load, raw_stroke))
                    cap.graph.append((t, g.get("load"), g.get("stroke")))
                    cap.graph_note = g.get("note")
                    cap.lvdt_note = g.get("lvdt")
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
