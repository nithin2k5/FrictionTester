"""COM Setting page.

One card per device group, stacked on the left; the communication log fills
the right. Every device row has the same columns:

    Device | COM Port | Baud Rate | ID | actions | status

* PLC — LS XGB built-in Cnet CH2, RS-485 (USB adapter), Modbus RTU 8N1: CONNECT /
  TEST, plus test bits and a D-register read.
* Loadcells — two KOMEIN AM-50LW indicators: CONNECT / TEST / ZERO.
* NI DAQ — NI USB-6210 analog input: TEST.
"""

import threading
import time
import tkinter as tk
from tkinter import ttk

from . import theme as T
from .theme import light as theme      # light industrial theme
from ..config import daq_value, save_settings
from ..devices import loadcell, ni_daq, plc_xgb

CABLE_OF = {"1": "Select", "2": "Shift"}      # device / sensor number -> the cable it measures


def _with_cable(name):
    """Label of a load cell or NI sensor row with its cable: 'L1 (Select)', 'LVDT 2 (Shift)'.
    Display only - the settings keep the plain name."""
    cable = CABLE_OF.get(name[-1:])
    return f"{name} ({cable})" if cable else name


# Shared column widths so every card's rows line up with the others.
COLS = (("Device", 145), ("COM Port", 130), ("Baud Rate", 105), ("ID", 84), ("", 268), ("Status", 0))

def _id_text(value):
    """Indicator ID (F-40) box: blank = detect on connect, 0 = stream mode, 1-99 = command mode."""
    dev = loadcell.parse_id(value)
    return "" if dev is None else f"{dev:02d}"


CARD_BG = theme.PANEL
HEAD_BG = T.L_SURFACE_2
ROW_BG = T.L_SUNKEN


class StatusPill(tk.Frame):
    """A coloured dot and a short state text."""

    COLORS = {"idle": theme.MUTED, "ok": theme.PASS, "busy": theme.BUSY, "error": theme.FAIL}

    def __init__(self, master):
        bg = master["bg"]
        super().__init__(master, bg=bg)
        self.dot = tk.Canvas(self, width=12, height=12, bg=bg, highlightthickness=0)
        self._oval = self.dot.create_oval(2, 2, 11, 11, fill=theme.MUTED, outline="")
        self.dot.pack(side="left")
        self.text = tk.Label(self, text="Not connected", bg=bg, fg=theme.MUTED, font=(theme.FONT, 10), anchor="w")
        self.text.pack(side="left", padx=(6, 0))

    def set(self, state, text):
        color = self.COLORS[state]
        self.dot.itemconfig(self._oval, fill=color)
        self.text.config(text=text, fg=T.L_TEXT if state == "ok" else color)


class ComPortPage(tk.Frame):
    def __init__(self, master, settings, plc, daq, loadcells):
        super().__init__(master, bg=theme.BG)
        self.settings = settings
        self.plc = plc
        self.daq = daq
        self.loadcells = loadcells
        self.lc_rows = []

        self._build_toolbar()
        body = tk.Frame(self, bg=theme.BG)
        body.pack(fill="both", expand=True, pady=(10, 0))
        body.columnconfigure(0, weight=0)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        left = tk.Frame(body, bg=theme.BG)
        left.grid(row=0, column=0, sticky="nsw", padx=(0, 12))
        self._build_plc_card(left)
        self._build_loadcell_card(left)
        self._build_daq_card(left)
        self._build_log_card(body).grid(row=0, column=1, sticky="nsew")

        self.refresh_hardware(log=False)
        self._show_plc_state()
        for i in range(len(self.loadcells)):
            self._show_lc_state(i)
        self.after(1000, self._watch_devices)
        if not plc_xgb.XGBPlc.available():
            self.log("pymodbus is not installed — run: pip install pymodbus")
        if not ni_daq.NIDaq.available():
            self.log("nidaqmx is not installed — run: pip install nidaqmx (and install the NI-DAQmx driver)")
        self._saved_state = self._form_state()     # what the fields held when the page opened

    # ======================================================= unsaved edits
    UNSAVED_TEXT = "The COM Port settings are not saved."

    def _form_state(self):
        """Every editable field as typed (PLC, loadcells, NI DAQ device and sensor rows)."""
        fields = [self.cb_plc_port, self.cb_plc_baud, self.ent_plc_station, self.ent_plc_bits,
                  self.ent_reg_addr, self.ent_reg_points, self.cb_daq_dev, self.cb_daq_term,
                  self.ent_daq_rate, self.ent_daq_samples]
        for w in self.lc_rows:
            fields += [w["port"], w["baud"], w["id"]]
        for w in self.sensor_rows:
            fields += [w["channel"], w["range"], w["scale"], w["offset"], w["unit"]]
        return tuple(f.get().strip() for f in fields)

    def unsaved_changes(self):
        """True if a field was changed since the page opened or since the last Save."""
        try:
            return self._form_state() != self._saved_state
        except tk.TclError:                         # widgets already destroyed
            return False

    # ============================================================ helpers
    def _label(self, parent, text, fg=T.L_TEXT, size=10, bold=False, **kw):
        return tk.Label(parent, text=text, bg=parent["bg"], fg=fg,
                        font=(theme.FONT, size, "bold") if bold else (theme.FONT, size), **kw)

    def _entry(self, parent, value, width=10, digits_only=False, justify="left"):
        e = tk.Entry(parent, bg=theme.ENTRY_BG, fg=T.L_TEXT, insertbackground=T.L_TEXT, justify=justify,
                     font=(theme.FONT, 10), width=width, bd=0, relief="flat",
                     highlightbackground=T.L_BORDER, highlightcolor=T.L_PRIMARY, highlightthickness=1,
                     disabledbackground=T.L_SUNKEN, disabledforeground=theme.MUTED)
        e.insert(0, str(value))
        if digits_only:
            check = e.register(lambda p: p == "" or p.isdigit())
            e.configure(validate="key", validatecommand=(check, "%P"))
        return e

    def _combo(self, parent, values, current, width=11):
        values = [str(v) for v in values]
        current = str(current)
        if current and current not in values:
            values.append(current)
        cb = ttk.Combobox(parent, values=values, font=(theme.FONT, 10), width=width, state="readonly",
                          style="L.TCombobox")
        if current:
            cb.set(current)
        elif values:
            cb.current(0)
        return cb

    def _button(self, parent, text, command, kind="secondary", width=8):
        bg, fg = {"primary": (theme.PASS, "white"), "secondary": (T.L_SURFACE_2, T.L_TEXT),
                  "muted": (T.L_NEUTRAL_BTN, "white"), "info": (theme.UPDATE, "white")}[kind]
        edge = T.L_BORDER if kind == "secondary" else bg
        return tk.Button(parent, text=text, command=command, bg=bg, fg=fg, activebackground=bg,
                         activeforeground=fg, font=(theme.FONT, 9, "bold"), width=width, bd=0, pady=3,
                         highlightbackground=edge, highlightthickness=1,
                         cursor="hand2", disabledforeground=T.L_TEXT_3 if kind == "secondary" else "#dfe4ea")

    @staticmethod
    def _cols(frame, cols):
        for i, (_, w) in enumerate(cols):
            frame.columnconfigure(i, minsize=w, weight=0 if w else 1)

    def _card(self, parent, title, subtitle):
        """A bordered card with a title strip; returns the body frame."""
        card = tk.Frame(parent, bg=CARD_BG, highlightbackground=theme.BORDER, highlightthickness=1)
        card.pack(fill="x", pady=(0, 12))
        head = tk.Frame(card, bg=HEAD_BG)
        head.pack(fill="x")
        tk.Frame(head, bg=theme.TITLE_FG, width=4).pack(side="left", fill="y")
        self._label(head, title, size=12, bold=True).pack(side="left", padx=(10, 8), pady=7)
        self._label(head, subtitle, fg=theme.MUTED, size=9).pack(side="left", pady=7)
        body = tk.Frame(card, bg=CARD_BG)
        body.pack(fill="x", padx=12, pady=(6, 10))
        return body

    def _table_header(self, body, cols):
        head = tk.Frame(body, bg=CARD_BG)
        head.pack(fill="x")
        self._cols(head, cols)
        for i, (h, _) in enumerate(cols):
            self._label(head, h, fg=theme.HEADER_FG, size=9, bold=True).grid(
                row=0, column=i, sticky="w", padx=(8, 0), pady=(0, 4))

    def _row(self, body, cols, name, makers):
        """A device row; makers build the widgets for columns 1..n."""
        row = tk.Frame(body, bg=ROW_BG)
        row.pack(fill="x", pady=2)
        self._cols(row, cols)
        self._label(row, name, size=10, bold=True, anchor="w").grid(row=0, column=0, sticky="w", padx=(8, 0), pady=6)
        built = []
        for col, make in enumerate(makers, start=1):
            w = make(row)
            w.grid(row=0, column=col, sticky="w", padx=(8, 0), pady=4)
            built.append(w)
        return built

    def _ui(self, fn, *args, **kw):
        self.after(0, lambda: fn(*args, **kw))

    def log(self, msg):
        def _append():
            self.log_text.config(state="normal")
            self.log_text.insert("end", msg + "\n")
            self.log_text.see("end")
            self.log_text.config(state="disabled")
        self.after(0, _append)

    # ============================================================ toolbar
    def _build_toolbar(self):
        bar = tk.Frame(self, bg=theme.BG)
        bar.pack(fill="x")
        self._label(bar, "Device Connections", size=15).pack(side="left")
        self._label(bar, "Connect and test each device, then Save.", fg=theme.MUTED, size=10).pack(
            side="left", padx=14, pady=(4, 0))
        for text, cmd, bg in (("Save", self.save, theme.SAVE), ("Rescan Ports", self.refresh_hardware, theme.UPDATE)):
            tk.Button(bar, text=text, command=cmd, bg=bg, fg="white", activebackground=bg,
                      activeforeground="white", font=(theme.FONT, 10, "bold"), bd=0, padx=18, pady=5,
                      cursor="hand2").pack(side="right", padx=(8, 0))

    # ================================================================ PLC
    def _build_plc_card(self, parent):
        cfg = self.settings["plc"]
        body = self._card(parent, "PLC", "LS XGB  ·  CH2 RS-485  ·  Modbus RTU 8N1")
        self._table_header(body, COLS)

        def actions(p):
            f = tk.Frame(p, bg=p["bg"])
            self.btn_plc_connect = self._button(f, "CONNECT", self.toggle_plc, "primary", width=11)
            self.btn_plc_connect.pack(side="left", padx=(0, 6))
            self.btn_plc_test = self._button(f, "TEST", self.test_plc)
            self.btn_plc_test.pack(side="left")
            return f

        self.cb_plc_port, self.cb_plc_baud, self.ent_plc_station, _a, self.plc_status = self._row(
            body, COLS, "XGB", [
                lambda p: self._combo(p, [], cfg["port"]),
                lambda p: self._combo(p, plc_xgb.BAUD_RATES, cfg["baudrate"], width=9),
                lambda p: self._entry(p, cfg["station_id"], width=5, digits_only=True, justify="center"),
                actions,
                StatusPill,
            ])

        tools = tk.Frame(body, bg=CARD_BG)
        tools.pack(fill="x", pady=(8, 0))
        self._label(tools, "Test bits", fg=theme.HEADER_FG, size=9, bold=True).pack(side="left", padx=(8, 6))
        self.ent_plc_bits = self._entry(tools, cfg["test_bits"], width=22)
        self.ent_plc_bits.pack(side="left")
        self._label(tools, "Register", fg=theme.HEADER_FG, size=9, bold=True).pack(side="left", padx=(20, 6))
        self.ent_reg_addr = self._entry(tools, cfg["reg_address"], width=8, justify="center")
        self.ent_reg_addr.pack(side="left")
        self._label(tools, "Points", fg=theme.HEADER_FG, size=9, bold=True).pack(side="left", padx=(12, 6))
        self.ent_reg_points = self._entry(tools, cfg["reg_points"], width=4, digits_only=True, justify="center")
        self.ent_reg_points.pack(side="left")
        self.btn_reg_read = self._button(tools, "READ", self.read_registers, "info", width=7)
        self.btn_reg_read.pack(side="left", padx=(10, 0))

    def _plc_config(self):
        station = self.ent_plc_station.get().strip()
        if not station.isdigit():
            raise ValueError("Station ID must be a number")
        try:
            points = int(self.ent_reg_points.get() or 1)
        except ValueError:
            raise ValueError("Points must be a number")
        cfg = dict(self.settings["plc"])   # keeps bytesize/parity/stopbits/timeout
        cfg.update({
            "port": self.cb_plc_port.get().strip(),
            "baudrate": int(self.cb_plc_baud.get()),
            "station_id": int(station),
            "test_bits": self.ent_plc_bits.get().strip(),
            "reg_address": self.ent_reg_addr.get().strip().upper(),
            "reg_points": points,
        })
        return cfg

    def _show_plc_state(self, error=None):
        connected = self.plc.is_open
        self._plc_shown, self._plc_busy = connected, False
        self.btn_plc_connect.config(text="DISCONNECT" if connected else "CONNECT",
                                    bg=theme.FAIL if connected else theme.PASS,
                                    activebackground=theme.FAIL if connected else theme.PASS, state="normal")
        for b in (self.btn_plc_test, self.btn_reg_read):
            b.config(state="normal" if connected else "disabled")
        for w in (self.cb_plc_port, self.cb_plc_baud):
            w.config(state="disabled" if connected else "readonly")
        self.ent_plc_station.config(state="disabled" if connected else "normal")
        if connected:
            self.plc_status.set("ok", f"Connected · {self.plc.port}")
        elif error:
            self.plc_status.set("error", "Connection failed")
        else:
            self.plc_status.set("idle", "Not connected")

    def toggle_plc(self):
        if getattr(self, "_plc_shown", False):          # act on what the button shows
            self.plc.disconnect()
            self.log("PLC disconnected.")
            self._show_plc_state()
            return
        try:
            cfg = self._plc_config()
        except ValueError as exc:
            self.log(f"PLC: {exc}")
            return
        self._plc_busy = True
        self.btn_plc_connect.config(text="CONNECTING…", bg=theme.BUSY, state="disabled")
        self.plc_status.set("busy", "Connecting…")

        def run():
            self.log(f"Connecting to PLC on {cfg['port']} ({cfg['baudrate']} 8N1, station {cfg['station_id']})…")
            try:
                self.plc.connect(cfg)
                self.settings["plc"] = cfg
                self.log(f"PLC connected on {cfg['port']}.")
                self._ui(self._show_plc_state)
            except Exception as exc:
                self.log(f"PLC error: {exc}")
                self._ui(self._show_plc_state, error=True)

        threading.Thread(target=run, daemon=True).start()

    def test_plc(self):
        try:
            bits = plc_xgb.parse_bit_list(self.ent_plc_bits.get())
        except ValueError as exc:
            self.log(f"PLC: {exc}")
            return
        if not bits:
            self.log("PLC: enter at least one bit address under Test bits.")
            return

        def run():
            try:
                results = self.plc.read_bits(bits)
            except Exception as exc:
                self.log(f"PLC error: {exc}")
                self._ui(self._after_plc_failure)
                return
            self.log("PLC bits (FC01, coil = word*16+bit, raw):  " + "   ".join(
                f"{a}={f'ERR ({v})' if isinstance(v, str) else ('ON' if v else 'OFF')}" for a, v in results))

        threading.Thread(target=run, daemon=True).start()

    def read_registers(self):
        addr = self.ent_reg_addr.get().strip().upper()
        try:
            plc_xgb.register_address(addr)
            points = int(self.ent_reg_points.get() or 1)
        except ValueError as exc:
            self.log(f"PLC: {exc}")
            return

        def run():
            try:
                start, regs = self.plc.read_registers(addr, points)
            except Exception as exc:
                self.log(f"PLC error: {exc}")
                self._ui(self._after_plc_failure)
                return
            self.log(f"PLC holding registers (FC03) from raw offset {start} - equals {addr} only if the "
                     "PLC's word-write area starts at D00000:")
            for i, v in enumerate(regs):
                self.log(f"  offset {start + i:5d} = {v}  (0x{v:04X}, signed {v - 65536 if v >= 32768 else v})")
            for i, big, swapped in plc_xgb.registers_as_floats(regs):
                self.log(f"  D{start + i:04d}-D{start + i + 1:04d} float: "
                         f"{big:.6g} (high word first) / {swapped:.6g} (low word first)")

        threading.Thread(target=run, daemon=True).start()

    def _after_plc_failure(self):
        if not self.plc.is_open:
            self._show_plc_state(error=True)

    # ========================================================== loadcells
    def _build_loadcell_card(self, parent):
        body = self._card(parent, "Loadcells", f"{loadcell.MODEL}  ·  RS232 8N1  ·  ID: blank = auto, 00 = stream, 01-99 = command mode")
        self._table_header(body, COLS)
        for i, lc in enumerate(self.loadcells):
            self._build_loadcell_row(body, i, lc)

    def _build_loadcell_row(self, body, i, lc):
        cfg = self.settings["loadcells"][i]
        w = {}

        def actions(p):
            f = tk.Frame(p, bg=p["bg"])
            w["connect"] = self._button(f, "CONNECT", lambda: self.toggle_loadcell(i), "primary", width=11)
            w["connect"].pack(side="left", padx=(0, 6))
            w["test"] = self._button(f, "TEST", lambda: self.test_loadcell(i))
            w["test"].pack(side="left", padx=(0, 6))
            w["zero"] = self._button(f, "ZERO", lambda: self.zero_loadcell(i), "muted", width=6)
            w["zero"].pack(side="left")
            return f

        w["port"], w["baud"], w["id"], _a, w["status"] = self._row(
            body, COLS, _with_cable(f"L{lc.number}"), [
                lambda p: self._combo(p, [], cfg["port"]),
                lambda p: self._combo(p, loadcell.BAUD_RATES, cfg["baudrate"], width=9),
                lambda p: self._entry(p, _id_text(cfg.get("id", "auto")), width=5, digits_only=True,
                                      justify="center"),
                actions,
                StatusPill,
            ])
        self.lc_rows.append(w)

    def _lc_config(self, i):
        w = self.lc_rows[i]
        dev = loadcell.parse_id(w["id"].get())
        return {"port": w["port"].get().strip(), "baudrate": int(w["baud"].get()),
                "id": "auto" if dev is None else dev}

    def _show_lc_state(self, i, error=None, text=None):
        lc, w = self.loadcells[i], self.lc_rows[i]
        connected = lc.is_open
        w["shown"] = connected
        w["busy"] = False
        bg = theme.FAIL if connected else theme.PASS
        w["connect"].config(text="DISCONNECT" if connected else "CONNECT", bg=bg, activebackground=bg,
                            state="normal")
        w["test"].config(state="normal" if connected else "disabled")
        w["zero"].config(state="normal" if connected and not lc.stream_mode else "disabled")
        for key in ("port", "baud"):
            w[key].config(state="disabled" if connected else "readonly")
        w["id"].config(state="disabled" if connected else "normal")
        if text:
            w["status"].set("error" if error else "ok", text)
        elif connected:
            w["status"].set("ok", f"Connected · {lc.port} · {'stream' if lc.stream_mode else f'ID{lc.device_id:02d}'}")
        elif error:
            w["status"].set("error", "Connection failed")
        else:
            w["status"].set("idle", "Not connected")

    def _watch_devices(self):
        """Pick up connections made in the background (startup auto-connect)."""
        if not self.winfo_exists():
            return
        if not getattr(self, "_plc_busy", False) and getattr(self, "_plc_shown", None) != self.plc.is_open:
            self._show_plc_state()
        for i, lc in enumerate(self.loadcells):
            w = self.lc_rows[i]
            if not w.get("busy") and w.get("shown") != lc.is_open:
                self._show_lc_state(i)
        self.after(1000, self._watch_devices)

    def toggle_loadcell(self, i):
        lc = self.loadcells[i]
        if self.lc_rows[i].get("shown"):          # act on what the button shows
            lc.disconnect()
            self.log(f"{lc.name} disconnected.")
            self._show_lc_state(i)
            return
        try:
            cfg = self._lc_config(i)
        except ValueError as exc:
            self.log(f"{lc.name}: {exc}")
            self._show_lc_state(i, error=True, text="ID must be 00-99")
            return
        w = self.lc_rows[i]
        w["busy"] = True
        w["connect"].config(text="CONNECTING…", bg=theme.BUSY, state="disabled")
        w["status"].set("busy", "Connecting…")

        def run():
            try:
                lc.connect(cfg)
            except Exception as exc:
                self.log(f"{lc.name} error: {exc}")
                self._ui(self._show_lc_state, i, error=True)
                return
            self.settings["loadcells"][i] = cfg
            self.log(f"{lc.name} connected on {cfg['port']} ({cfg['baudrate']} 8N1, {lc.mode_text}).")
            self._ui(self._show_lc_state, i)

        threading.Thread(target=run, daemon=True).start()

    def test_loadcell(self, i):
        lc = self.loadcells[i]

        def run():
            try:
                sent, reply, value = lc.read()
            except Exception as exc:
                self.log(f"{lc.name}: {exc}")
                self._ui(self._show_lc_state, i, error=True, text="No response")
                return
            prefix = f"sent {sent}, " if sent else "stream, "
            self.log(f"{lc.name}: {prefix}reply {reply!r} → {value:g}")
            self._ui(self._show_lc_state, i, text=f"{lc.port} · {value:g}")

        threading.Thread(target=run, daemon=True).start()

    def zero_loadcell(self, i):
        lc = self.loadcells[i]

        def run():
            try:
                sent = lc.zero()
            except Exception as exc:
                self.log(f"{lc.name}: {exc}")
                return
            self.log(f"{lc.name}: sent {sent} (zero)")

        threading.Thread(target=run, daemon=True).start()

    # ================================================================ DAQ
    def _build_daq_card(self, parent):
        cfg = self.settings["daq"]
        body = self._card(parent, "NI DAQ", "NI USB-6210  ·  USB  ·  analog inputs for loadcells and LVDTs")

        # Device-wide settings
        top = tk.Frame(body, bg=CARD_BG)
        top.pack(fill="x", pady=(0, 6))
        self._label(top, "Device", fg=theme.HEADER_FG, size=9, bold=True).pack(side="left", padx=(8, 6))
        self.cb_daq_dev = self._combo(top, [], cfg["device"], width=10)
        self.cb_daq_dev.pack(side="left")
        self.btn_daq_dev_test = self._button(top, "TEST DEVICE", self.test_daq_device, width=11)
        self.btn_daq_dev_test.pack(side="left", padx=(6, 0))
        self._label(top, "Wiring", fg=theme.HEADER_FG, size=9, bold=True).pack(side="left", padx=(16, 6))
        self.cb_daq_term = self._combo(top, ni_daq.TERMINAL_CONFIGS, cfg["terminal_config"], width=10)
        self.cb_daq_term.pack(side="left")
        self.cb_daq_term.bind("<<ComboboxSelected>>", lambda _e: self._on_terminal_change())
        self._label(top, "Rate", fg=theme.HEADER_FG, size=9, bold=True).pack(side="left", padx=(16, 6))
        self.ent_daq_rate = self._entry(top, f"{cfg['sample_rate']:g}", width=7, justify="center")
        self.ent_daq_rate.pack(side="left")
        self._label(top, "S/s", fg=theme.MUTED, size=9).pack(side="left", padx=(3, 0))
        self._label(top, "Samples", fg=theme.HEADER_FG, size=9, bold=True).pack(side="left", padx=(16, 6))
        self.ent_daq_samples = self._entry(top, cfg["samples_per_channel"], width=5, justify="center")
        self.ent_daq_samples.pack(side="left")
        self.btn_daq_all = self._button(top, "LIVE", self.toggle_live, "info", width=9)
        self.btn_daq_all.pack(side="right", padx=(0, 4))

        # Result of TEST DEVICE
        dev_row = tk.Frame(body, bg=CARD_BG)
        dev_row.pack(fill="x", pady=(0, 6))
        self._label(dev_row, "Device status", fg=theme.HEADER_FG, size=9, bold=True).pack(side="left", padx=(8, 8))
        self.daq_dev_status = StatusPill(dev_row)
        self.daq_dev_status.pack(side="left")
        self.daq_dev_status.set("idle", "Not tested")

        # One row per sensor
        cols = (("Sensor", 150), ("Channel", 70), ("Pins", 112), ("Range", 78), ("Unit", 52),
                ("Scale /V", 70), ("Offset", 70), ("", 60), ("Known", 62), ("", 56), ("Reading", 0))
        self._table_header(body, cols)
        terminal = cfg["terminal_config"]
        self.sensor_rows = []
        for i, s in enumerate(cfg["sensors"]):
            row = tk.Frame(body, bg=ROW_BG)
            row.pack(fill="x", pady=2)
            self._cols(row, cols)
            w = {"name": s["name"], "zero_v": None}
            self._label(row, _with_cable(s["name"]), bold=True, anchor="w").grid(row=0, column=0, sticky="w", padx=(8, 0), pady=5)
            w["channel"] = self._combo(row, ni_daq.channels_for(terminal), s["channel"], width=4)
            w["channel"].bind("<<ComboboxSelected>>", lambda _e, n=i: self._update_pins(n))
            w["pins"] = self._label(row, "", fg=T.L_PRIMARY, anchor="w")
            w["range"] = self._combo(row, list(ni_daq.VOLTAGE_RANGES), s["range"], width=6)
            w["unit"] = self._entry(row, s["unit"], width=4, justify="center")
            w["scale"] = self._entry(row, f"{s['scale']:.6g}", width=8, justify="center")
            w["offset"] = self._entry(row, f"{s['offset']:.6g}", width=8, justify="center")
            w["zero"] = self._button(row, "ZERO", lambda n=i: self.zero_sensor(n), "muted", width=6)
            w["known"] = self._entry(row, "", width=6, justify="center")
            w["cal"] = self._button(row, "CAL", lambda n=i: self.cal_sensor(n), "muted", width=5)
            w["reading"] = StatusPill(row)
            w["reading"].set("idle", "—")
            for col, key in enumerate(("channel", "pins", "range", "unit", "scale", "offset",
                                       "zero", "known", "cal", "reading"), start=1):
                w[key].grid(row=0, column=col, sticky="w", padx=(8, 0), pady=3)
            self.sensor_rows.append(w)
            self._update_pins(i)

        self._label(body, "Calibrate: nothing on the sensor → ZERO.  Put a known load on, type it in Known → CAL.  "
                          "Then Save.", fg=theme.MUTED, size=9).pack(anchor="w", padx=8, pady=(6, 0))
        self._label(body, "Reading = volts × scale + offset (offset in the row's unit).  Differential: ai0 uses "
                          "pins AI0+ / AI8−, ai1 → AI1 / AI9, …", fg=theme.MUTED, size=9).pack(anchor="w", padx=8)
        self._live = False
        self._volts = {}                       # newest {channel: mean volts} from LIVE or a one-shot read
        self.bind("<Destroy>", lambda e: setattr(self, "_live", False) if e.widget is self else None, add="+")

    def _update_pins(self, i):
        w = self.sensor_rows[i]
        ch = w["channel"].get()
        w["pins"].config(text=ni_daq.pins_for(ch, self.cb_daq_term.get()) if ch else "")

    def _on_terminal_change(self):
        """Wiring mode changed: restrict channel lists and refresh the pins."""
        valid = ni_daq.channels_for(self.cb_daq_term.get())
        for i, w in enumerate(self.sensor_rows):
            w["channel"].config(values=valid)
            if w["channel"].get() not in valid:
                w["channel"].set(valid[min(i, len(valid) - 1)])
            self._update_pins(i)

    def _daq_config(self):
        try:
            rate = float(self.ent_daq_rate.get())
            samples = int(self.ent_daq_samples.get())
        except ValueError:
            raise ValueError("Rate and samples must be numbers")
        if samples < 1:
            raise ValueError("Samples must be at least 1")
        sensors = []
        for w in self.sensor_rows:
            try:
                scale, offset = float(w["scale"].get()), float(w["offset"].get())
            except ValueError:
                raise ValueError(f"{w['name']}: scale and offset must be numbers")
            sensors.append({"name": w["name"], "channel": w["channel"].get(), "range": w["range"].get(),
                            "scale": scale, "offset": offset, "unit": w["unit"].get().strip()})
        return {
            "device": self.cb_daq_dev.get().strip(),
            "terminal_config": self.cb_daq_term.get(),
            "sample_rate": rate,
            "samples_per_channel": samples,
            "sensors": sensors,
        }

    def _safe_ui(self, fn, *args, **kw):
        """_ui from a worker thread that may outlive the page."""
        try:
            self._ui(fn, *args, **kw)
        except (tk.TclError, RuntimeError):
            pass

    def test_daq_device(self):
        """NI-DAQmx self-test of the selected device alone — no sensors involved."""
        name = self.cb_daq_dev.get().strip()
        self.btn_daq_dev_test.config(text="TESTING…", state="disabled")
        self.daq_dev_status.set("busy", "Self-test running…")

        def run():
            self.log(f"NI DAQ: self-test of {name or '(no device selected)'}…")
            try:
                info = ni_daq.NIDaq.test_device(name)
            except Exception as exc:
                self.log(f"NI DAQ device FAIL: {exc}")
                self._safe_ui(self.daq_dev_status.set, "error", f"FAIL · {exc}")
            else:
                text = (f"PASS · {info['product']} · S/N {info['serial']} · "
                        f"{info['ai_channels']} AI channels")
                self.log(f"NI DAQ device {info['name']}: {text}")
                self._safe_ui(self.daq_dev_status.set, "ok", text)
            self._safe_ui(self.btn_daq_dev_test.config, text="TEST DEVICE", state="normal")

        threading.Thread(target=run, daemon=True).start()

    # ---------------------------------------------------- readings
    def _show_readings(self):
        """Show every row's value from the newest volts, with the scale / offset as typed now."""
        for w in self.sensor_rows:
            v = self._volts.get(w["channel"].get())
            if v is None:
                continue
            unit = w["unit"].get().strip()
            try:
                value = daq_value({"scale": float(w["scale"].get()), "offset": float(w["offset"].get())}, v)
            except ValueError:
                w["reading"].set("error", f"scale / offset not a number   ({v:+.4f} V)")
                continue
            w["reading"].set("ok", f"{value:.3f} {unit}   ({v:+.4f} V)")

    def toggle_live(self):
        """LIVE: read all four rows continuously until pressed again (or the page closes)."""
        if self._live:
            self._live = False
            return
        try:
            cfg = self._daq_config()
        except ValueError as exc:
            self.log(f"NI DAQ: {exc}")
            return
        if self.daq.is_open:
            self.log("NI DAQ: the card is in use (a test is running) — LIVE not started")
            return
        ranges = {}
        for s in cfg["sensors"]:
            ranges.setdefault(s["channel"], s["range"])
        task_cfg = dict(cfg, channels=[{"channel": ch, "range": r} for ch, r in ranges.items()])
        self._live = True
        self.btn_daq_all.config(text="STOP LIVE")
        for w in self.sensor_rows:
            w["reading"].set("busy", "Reading…")

        def run():
            self.log(f"NI DAQ: LIVE on {cfg['device'] or '(no device)'} ({', '.join(ranges)})")
            try:
                self.daq.connect(task_cfg)
                try:
                    while self._live:
                        self._volts = self.daq.read_average()
                        self._safe_ui(self._show_readings)
                        time.sleep(0.2)
                finally:
                    self.daq.disconnect()
            except Exception as exc:
                self.log(f"NI DAQ error: {exc}")
                for w in self.sensor_rows:
                    self._safe_ui(w["reading"].set, "error", "FAIL")
            self._live = False
            self._safe_ui(self.btn_daq_all.config, text="LIVE")
            self.log("NI DAQ: LIVE stopped")

        threading.Thread(target=run, daemon=True).start()

    def _with_volts(self, i, then):
        """Call then(volts) on the UI thread with row i's voltage: the LIVE value if LIVE runs,
        otherwise one fresh averaged read of that channel."""
        w = self.sensor_rows[i]
        ch = w["channel"].get()
        if self._live:
            if ch in self._volts:
                then(self._volts[ch])
            else:
                self.log(f"{w['name']}: {ch} is not in the running LIVE read — stop and restart LIVE")
            return
        try:
            cfg = self._daq_config()
        except ValueError as exc:
            self.log(f"NI DAQ: {exc}")
            return
        if self.daq.is_open:
            self.log("NI DAQ: the card is in use (a test is running)")
            return
        task_cfg = dict(cfg, channels=[{"channel": ch, "range": w["range"].get()}])
        for b in (w["zero"], w["cal"]):
            b.config(state="disabled")
        w["reading"].set("busy", "Reading…")

        def run():
            try:
                self.daq.connect(task_cfg)
                try:
                    v = self.daq.read_average()[ch]
                finally:
                    self.daq.disconnect()
            except Exception as exc:
                self.log(f"NI DAQ error: {exc}")
                self._safe_ui(w["reading"].set, "error", "FAIL")
            else:
                self._volts[ch] = v
                self._safe_ui(then, v)
                self._safe_ui(self._show_readings)
            for b in (w["zero"], w["cal"]):
                self._safe_ui(b.config, state="normal")

        threading.Thread(target=run, daemon=True).start()

    @staticmethod
    def _set_entry(entry, value):
        entry.delete(0, "end")
        entry.insert(0, f"{value:.6g}")

    def zero_sensor(self, i):
        """ZERO: the voltage now (nothing on the sensor) becomes the zero of the row."""
        w = self.sensor_rows[i]
        try:
            scale = float(w["scale"].get())
        except ValueError:
            self.log(f"{w['name']}: scale is not a number")
            return

        def apply(v):
            w["zero_v"] = v
            self._set_entry(w["offset"], round(-v * scale, 6) + 0.0)
            self._show_readings()
            self.log(f"{w['name']}: zero at {v:+.4f} V → offset {w['offset'].get()} {w['unit'].get().strip()}"
                     "  (Save to keep it)")

        self._with_volts(i, apply)

    def cal_sensor(self, i):
        """CAL: the known load now on the sensor sets the scale; the zero stays where it is."""
        w = self.sensor_rows[i]
        unit = w["unit"].get().strip()
        try:
            known = float(w["known"].get())
        except ValueError:
            self.log(f"{w['name']}: type the load now on the sensor in Known (in {unit or 'the row unit'}), "
                     "then press CAL")
            return
        if known == 0:
            self.log(f"{w['name']}: Known must not be 0 — for zero press ZERO")
            return
        zero_v = w["zero_v"]
        if zero_v is None:                      # no ZERO this session: the zero the saved offset implies
            try:
                scale, offset = float(w["scale"].get()), float(w["offset"].get())
            except ValueError:
                self.log(f"{w['name']}: scale / offset not a number — press ZERO first")
                return
            if scale == 0:
                self.log(f"{w['name']}: press ZERO first (with nothing on the sensor)")
                return
            zero_v = -offset / scale

        def apply(v):
            if abs(v - zero_v) < 0.0005:
                self.log(f"{w['name']}: {v:+.4f} V is the same as zero ({zero_v:+.4f} V) — "
                         "is the load on, and is the sensor wired to this channel?")
                return
            scale = known / (v - zero_v)
            self._set_entry(w["scale"], scale)
            self._set_entry(w["offset"], round(-zero_v * scale, 6) + 0.0)
            self._show_readings()
            self.log(f"{w['name']}: zero {zero_v:+.4f} V, {known:g} {unit} at {v:+.4f} V → "
                     f"scale {w['scale'].get()} {unit}/V, offset {w['offset'].get()} {unit}  (Save to keep it)")

        self._with_volts(i, apply)

    # ================================================================ log
    def _build_log_card(self, parent):
        card = tk.Frame(parent, bg=CARD_BG, highlightbackground=theme.BORDER, highlightthickness=1)
        head = tk.Frame(card, bg=HEAD_BG)
        head.pack(fill="x")
        tk.Frame(head, bg=theme.TITLE_FG, width=4).pack(side="left", fill="y")
        self._label(head, "Communication Log", size=12, bold=True).pack(side="left", padx=10, pady=7)
        self._button(head, "CLEAR", self.clear_log, "muted", width=7).pack(side="right", padx=8)
        box = tk.Frame(card, bg=CARD_BG)
        box.pack(fill="both", expand=True, padx=8, pady=8)
        sb = ttk.Scrollbar(box, orient="vertical", style="L.Vertical.TScrollbar")
        sb.pack(side="right", fill="y")
        self.log_text = tk.Text(box, bg=T.L_SUNKEN, fg=T.L_TEXT, font=("Consolas", 10), bd=0, wrap="word",
                                padx=8, pady=6, yscrollcommand=sb.set, width=40)
        self.log_text.pack(fill="both", expand=True)
        sb.config(command=self.log_text.yview)
        self.log_text.config(state="disabled")
        return card

    def clear_log(self):
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.config(state="disabled")

    # ============================================================= common
    def refresh_hardware(self, log=True):
        """Rescan COM ports and NI devices."""
        ports = plc_xgb.XGBPlc.list_ports()
        port_names = [dev for dev, _ in ports]
        for cb in [self.cb_plc_port] + [w["port"] for w in self.lc_rows]:
            current = cb.get()
            cb.config(values=port_names or ([current] if current else []))
        if not self.cb_plc_port.get() and port_names:
            self.cb_plc_port.set(port_names[0])

        devices = ni_daq.NIDaq.list_devices()
        dev_names = [name for name, _ in devices]
        current = self.cb_daq_dev.get()
        self.cb_daq_dev.config(values=dev_names or ([current] if current else []))
        if not current and dev_names:
            preferred = [n for n, product in devices if "6210" in product]
            self.cb_daq_dev.set((preferred or dev_names)[0])

        if log:
            self.log("COM ports: " + (", ".join(f"{d} ({desc})" for d, desc in ports) or "none found"))
            self.log("NI devices: " + (", ".join(f"{n} ({p})" for n, p in devices) or "none found"))

    def save(self):
        try:
            self.settings["plc"] = self._plc_config()
            self.settings["loadcells"] = [self._lc_config(i) for i in range(len(self.loadcells))]
            self.settings["daq"] = self._daq_config()
            save_settings(self.settings)
        except Exception as exc:
            self.log(f"Settings not saved: {exc}")
            return False
        self._saved_state = self._form_state()
        self.log("Settings saved.")
        return True
