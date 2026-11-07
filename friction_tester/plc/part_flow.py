"""Operator part flow on top of the PLC's shared automatic cycle.

    AT: scan SELECT -> one cycle (SELECT stage) -> scan SHIFT -> one cycle
        (SHIFT stage) -> part complete
    MT: scan one label -> one shared cycle -> part complete

The flow never guesses machine behaviour. What the ladder does not establish
is configuration (settings.json -> test.integration.shared):

    at_cable_selection        how the machine knows which AT cable is being
                              tested. "" = not configured (AT live starts are
                              blocked). "operator_fixture" = the operator loads
                              only the active cable; one cycle per stage.
    mt_one_cycle_tests_both   true once it is confirmed that one cycle tests
                              both cables of an MT part.
    channel_cable             {"1": "select"|"shift"|"", "2": ...}
    direction_role            {"forward": "pull"|"push"|"", "return": ...}

A start is sent only through the monitor (M00029 pulse, never twice for one
stage). Verdicts are the PLC's overall verdict for the cycle; per-channel
limit flags (M00000 / M00003) are shown as channel information only.
Each confirmed part completion is reported once via ui.part_done().
"""

import datetime
import itertools

_ids = itertools.count(1)

CABLES = {"select": 0, "shift": 1}


def live_start_blocker(cfg, writes_available, reason=None):
    """Why a live start may not be sent (None if it may). `reason`: callable giving the exact cause."""
    if not writes_available:
        why = reason() if reason else None
        if why:
            return f"Live PLC start is disabled - {why}. PLC monitoring stays on."
        return ("Live PLC start is disabled: the Modbus address mapping is not verified "
                "(test.integration.shared.mapping.verified / live_start_enabled). PLC monitoring stays on.")
    return None


def stage_blocker(cfg, model_type):
    if model_type == "AT" and not cfg.get("at_cable_selection"):
        return "AT cable selection is not configured."
    if model_type == "MT" and not cfg.get("mt_one_cycle_tests_both"):
        return "MT shared-cycle operation is not confirmed (one cycle testing both cables)."
    return None


def channel_for_cable(cfg, cable):
    """Channel (1/2) configured for a cable ("select"/"shift"), or None."""
    for ch, c in (cfg.get("channel_cable") or {}).items():
        if c == cable:
            return int(ch)
    return None


def panel_assignments(cfg, model_type, stage):
    """[(panel_index, channel, mapped)] for showing one cycle's results.

    With the channel->cable mapping configured, AT fills only the active
    cable's panel from its channel and MT fills both cables. Without it, the
    two channels are shown as "Channel 1" / "Channel 2" in the two panels,
    explicitly marked as not mapped to a cable.
    """
    mapping = {int(k): v for k, v in (cfg.get("channel_cable") or {}).items() if v in CABLES}
    if model_type == "AT":
        ch = channel_for_cable(cfg, stage)
        if ch is not None:
            return [(CABLES[stage], ch, True)]
    elif len(mapping) == 2 and set(mapping.values()) == set(CABLES):
        return [(CABLES[c], ch, True) for ch, c in sorted(mapping.items())]
    return [(0, 1, False), (1, 2, False)]


class SharedCycleFlow:
    """State machine; `ui` provides set_state(state), message(text, level),
    show_cycle(stage, assignments, event), clear_panels(indices), part_done(record).
    `monitor` provides request_start(test_id) and cancel()."""

    def __init__(self, cfg, ui, monitor, writes_available, skip_scan=False, write_blocker=None):
        self.cfg = cfg
        # skip_scan (temporary): RUN starts each stage directly - no labels.
        # AT: RUN starts SELECT, RUN again starts SHIFT. Never auto-starts.
        self.skip_scan = skip_scan
        self.ui = ui
        self.monitor = monitor
        self.writes_available = writes_available   # callable -> bool
        self.write_blocker = write_blocker         # callable -> reason text or None (optional)
        self.state = "idle"
        self.model = None
        self.model_type = None
        self.labels = {}
        self.stages = []
        self.test_id = None
        self.stage = None

    # ------------------------------------------------------------ operator actions
    def run(self, model, model_type, part_number=""):
        if self.skip_scan and self.state == "at_scan_shift":
            self._start_stage("shift", "at_run_shift")
            return True
        if self.state != "idle":
            self.ui.message("A test is already in progress - press STOP to cancel it", "busy")
            return False
        if not model:
            self.ui.message("RUN: load a model first (Model Change)", "fail")
            return False
        if model_type not in ("MT", "AT"):
            self.ui.message(f"RUN: model {model} has no Type - set MT or AT in Model Settings", "fail")
            return False
        blocker = stage_blocker(self.cfg, model_type) or live_start_blocker(self.cfg, self.writes_available(), self.write_blocker)
        if blocker:
            self.ui.message(blocker, "fail")
            return False
        self.model, self.model_type, self.part_number = model, model_type, part_number
        self._new_part(announce=not self.skip_scan)
        if self.skip_scan:
            if model_type == "MT":
                self._start_stage("both", "mt_run")
            else:
                self._start_stage("select", "at_run_select")
        return True

    def _new_part(self, announce=True):
        self.labels, self.stages = {}, []
        self._set("mt_scan" if self.model_type == "MT" else "at_scan_select")
        if announce:
            self.ui.message(f"{self.model} ({self.model_type}): scan the "
                            f"{'label' if self.model_type == 'MT' else 'SELECT label'}", "pass")

    def scan(self, box, label):
        label = (label or "").strip()
        if not label:
            return
        s = self.state
        if s == "at_scan_select" and box == "select":
            self.labels["select"] = label
            self._start_stage("select", "at_run_select")
        elif s == "at_scan_shift" and box == "shift":
            self.labels["shift"] = label
            self._start_stage("shift", "at_run_shift")
        elif s == "mt_scan":
            self.labels = {"select": label, "shift": label}
            self._start_stage("both", "mt_run")
        elif s == "idle":
            self.ui.message("Press RUN before scanning", "busy")
        elif s in ("at_scan_select", "at_scan_shift"):
            self.ui.message(f"Scan the {'SELECT' if s == 'at_scan_select' else 'SHIFT'} label", "busy")
        else:
            self.ui.message("Test running - wait for it to finish", "busy")

    def stop(self):
        if self.state == "idle":
            return
        self.monitor.cancel()
        self.test_id = None
        self._set("idle")
        self.ui.message("Application flow stopped. The PLC cycle is not stopped by the app - "
                        "use the machine's stop if it is running.", "busy")

    # ------------------------------------------------------------ cycle
    def _start_stage(self, stage, run_state):
        blocker = stage_blocker(self.cfg, self.model_type) or live_start_blocker(self.cfg, self.writes_available(), self.write_blocker)
        if blocker:
            self._abort(blocker)
            return
        self.stage = stage
        self.test_id = next(_ids)
        self._set(run_state)
        panels = [CABLES[stage]] if stage in CABLES else [0, 1]
        self.ui.clear_panels(panels)
        self.monitor.request_start(self.test_id)
        self.ui.message("Requesting PLC cycle start…", "info")

    def on_event(self, ev):
        """Feed every monitor event here (UI thread)."""
        if ev.test_id is None or ev.test_id != self.test_id:
            return                     # unassociated: physical start, stopped test, connection notices
        k = ev.kind
        if k == "start_sent":
            self.ui.message("PLC start requested - waiting for the cycle…", "info")
        elif k == "cycle_started":
            self.ui.message(f"PLC cycle running ({self.stage.upper() if self.stage != 'both' else 'SELECT + SHIFT'})",
                            "info")
        elif k == "completed":
            self._completed(ev)
        elif k in ("start_refused", "start_failed", "start_timeout", "ended_without_result", "ambiguous"):
            self._abort(ev.message or "Cycle ended without confirmed result")
        elif k == "disconnected":
            self.ui.message("PLC connection lost - waiting; the result of this cycle cannot be confirmed", "fail")

    def on_global_event(self, ev):
        """Events not tied to a test (start pulse release failure)."""
        if ev.kind == "start_release_failed":
            self.monitor.cancel()
            self.test_id = None
            self._set("idle")
            self.ui.message(ev.message, "fail")

    def _completed(self, ev):
        verdict = ev.data["verdict"]
        stage = self.stage
        self.ui.show_cycle(stage, panel_assignments(self.cfg, self.model_type, stage
                                                     if stage in CABLES else "both"), ev)
        self.stages.append({"stage": stage, "verdict": verdict, "evidence": ev.data.get("evidence"),
                            "ok_count": ev.data.get("ok_count"), "ng_count": ev.data.get("ng_count"),
                            "finals": (ev.data.get("measurements") or {}).get("finals"),
                            "measurements": ev.data.get("measurements")})
        self.test_id = None
        if stage == "select":
            self._set("at_scan_shift")
            nxt = "press RUN for the SHIFT stage" if self.skip_scan else "scan the SHIFT label"
            self.ui.message(f"SELECT stage: overall PLC verdict {verdict} - {nxt}",
                            "pass" if verdict == "OK" else "fail")
            return
        part_ok = all(s["verdict"] == "OK" for s in self.stages)
        record = {
            "time": datetime.datetime.now().isoformat(timespec="seconds"),
            "model": self.model, "type": self.model_type, "part_number": getattr(self, "part_number", ""),
            "label_select": self.labels.get("select", ""), "label_shift": self.labels.get("shift", ""),
            "verdict": "OK" if part_ok else "NG",
            "verdict_basis": "overall PLC verdict" + (" per stage" if self.model_type == "AT" else ""),
            "stages": self.stages,
            "cables": ["select", "shift"],
        }
        self.ui.part_done(record)
        if self.skip_scan:
            self._set("idle")                      # next part only on the next RUN
            nxt = "Press RUN for the next part"
        else:
            self._new_part(announce=False)
            nxt = "Scan the next label"
        self.ui.message(f"Part complete - {record['verdict']} ({record['verdict_basis']}). {nxt}",
                        "pass" if part_ok else "fail")

    def _abort(self, message):
        self.monitor.cancel()
        self.test_id = None
        self._set("idle")
        self.ui.message(message, "fail")

    def _set(self, state):
        self.state = state
        self.ui.set_state(state)
