"""COM Setting page.

PLC slot follows the EOL tester: LS XGB over RS232 (Modbus RTU), with COM
port, baud rate, station ID, CONNECT / TEST and a status cell, plus a PLC
register read underneath. The NI USB-6210 has its own row with a TEST button.
Visual style follows FeederCable: banded rows that light up under the pointer
and one log box for everything.
"""

import threading
import tkinter as tk
from tkinter import ttk

from . import theme
from ..config import save_settings
from ..devices import loadcell, ni_daq, plc_xgb

PLC_COLS = (("Device", 200), ("COM Port", 140), ("Baud Rate", 140), ("Station ID", 110),
            ("Action", 300), ("Status", 0))
DAQ_COLS = (("Device", 200), ("NI Device", 140), ("AI Channels", 140), ("Terminal", 120),
            ("Input Range", 120), ("Action", 130))

CONNECT_BG = theme.PASS
DISCONNECT_BG = theme.FAIL


class ComPortPage(tk.Frame):
    def __init__(self, master, settings, plc, daq, loadcells):
        super().__init__(master, bg=theme.PANEL, bd=1, relief="solid",
                         highlightbackground=theme.BORDER, highlightthickness=1)
        self.settings = settings
        self.plc = plc
        self.daq = daq
        self.loadcells = loadcells
        self.lc_rows = []   # one dict of widgets per loadcell

        self._build_title()
        self._build_plc_section()
        self._build_daq_section()
        self._build_bottom()
        self._build_log()

        self.refresh_hardware(log=False)
        self._show_plc_state()
        for i in range(len(self.loadcells)):
            self._show_lc_state(i)
        if not plc_xgb.XGBPlc.available():
            self.log("pymodbus is not installed — run: pip install pymodbus")
        if not ni_daq.NIDaq.available():
            self.log("nidaqmx is not installed — run: pip install nidaqmx")

    # --------------------------------------------------------------- helpers
    def _label(self, parent, text, **kw):
        opts = {"bg": parent["bg"], "fg": "white", "font": (theme.FONT, 11)}
        opts.update(kw)
        return tk.Label(parent, text=text, **opts)

    def _entry(self, parent, value, width=12, digits_only=False):
        e = tk.Entry(parent, bg=theme.ENTRY_BG, fg="white", insertbackground="white",
                     font=(theme.FONT, 11), width=width, bd=1, relief="solid",
                     highlightbackground=theme.BORDER, highlightthickness=1,
                     disabledbackground="#16191f", disabledforeground=theme.MUTED)
        e.insert(0, str(value))
        if digits_only:
            check = e.register(lambda p: p == "" or p.isdigit())
            e.configure(validate="key", validatecommand=(check, "%P"))
        return e

    def _combo(self, parent, values, current, width=12):
        values = [str(v) for v in values]
        current = str(current)
        if current and current not in values:
            values.append(current)
        cb = ttk.Combobox(parent, values=values, font=(theme.FONT, 10), width=width, state="readonly")
        if current:
            cb.set(current)
        elif values:
            cb.current(0)
        return cb

    def _button(self, parent, text, command, bg=theme.TEST_IDLE, fg="black", width=9):
        return tk.Button(parent, text=text, bg=bg, fg=fg, activebackground=bg,
                         font=(theme.FONT, 10, "bold"), width=width, bd=0, cursor="hand2",
                         disabledforeground="#888", command=command)

    @staticmethod
    def _grid_cols(frame, cols):
        # Fixed widths keep rows aligned; a zero width takes the slack.
        for i, (_, w) in enumerate(cols):
            frame.columnconfigure(i, minsize=w, weight=0 if w else 1)
        if all(w for _, w in cols):
            frame.columnconfigure(len(cols), weight=1)
        return frame

    def _section_heading(self, text):
        self._label(self, text, fg=theme.TITLE_FG, font=(theme.FONT, 11, "bold")).pack(
            anchor="w", padx=30, pady=(14, 0))

    def _table(self, cols):
        table = tk.Frame(self, bg=theme.PANEL)
        table.pack(fill="x", padx=20, pady=(6, 8))
        header = self._grid_cols(tk.Frame(table, bg=theme.PANEL), cols)
        header.pack(fill="x")
        for i, (h, _) in enumerate(cols):
            tk.Label(header, text=h, bg=theme.PANEL, fg=theme.HEADER_FG,
                     font=(theme.FONT, 10, "bold")).grid(row=0, column=i, sticky="w", padx=(10, 0), pady=(0, 6))
        tk.Frame(table, bg="#2b3242", height=1).pack(fill="x")
        return table

    def _device_row(self, table, cols, index, name, makers):
        """A banded row: the device name, then one widget per maker callable.

        Plain frames and labels in the row take the band colour (and the hover
        colour); buttons and inputs keep their own.
        """
        band = theme.BAND[index % 2]
        row = self._grid_cols(tk.Frame(table, bg=band), cols)
        row.pack(fill="x")
        tk.Label(row, text=name, bg=band, fg="white", font=(theme.FONT, 11), anchor="w").grid(
            row=0, column=0, sticky="w", padx=(10, 0), pady=7)
        built = []
        for col, make in enumerate(makers, start=1):
            w = make(row)
            w.grid(row=0, column=col, sticky="w", padx=(10, 0), pady=4)
            built.append(w)

        def descendants(w):
            for c in w.winfo_children():
                yield c
                yield from descendants(c)

        def tint(color):
            for w in (row, *descendants(row)):
                if type(w) in (tk.Frame, tk.Label):
                    w.config(bg=color)

        # Tk sends <Leave> to the frame when the pointer moves onto a child,
        # so every widget in the row carries the same pair of bindings.
        for w in (row, *descendants(row)):
            w.bind("<Enter>", lambda _e: tint(theme.BAND_HOVER), add="+")
            w.bind("<Leave>", lambda _e, c=band: tint(c), add="+")
        return built

    def _option_row(self):
        row = tk.Frame(self, bg=theme.PANEL)
        row.pack(fill="x", padx=35, pady=(0, 8))
        return row

    def _ui(self, fn, *args, **kw):
        """Run a widget update on the Tk thread."""
        self.after(0, lambda: fn(*args, **kw))

    def log(self, msg):
        def _append():
            self.log_text.config(state="normal")
            self.log_text.insert("end", msg + "\n")
            self.log_text.see("end")
            self.log_text.config(state="disabled")
        self.after(0, _append)

    # ----------------------------------------------------------------- title
    def _build_title(self):
        bar = tk.Frame(self, bg=theme.PANEL)
        bar.pack(fill="x", padx=20, pady=15)
        self._label(bar, "COM Setting", font=(theme.FONT, 14)).pack(side="left")
        tk.Frame(self, bg=theme.BORDER, height=1).pack(fill="x", padx=20)

    # ------------------------------------------------------------------- PLC
    def _build_plc_section(self):
        cfg = self.settings["plc"]
        self._section_heading("Serial Devices (RS232)")
        table = self._table(PLC_COLS)

        def make_actions(parent):
            f = tk.Frame(parent, bg=parent["bg"])
            self.btn_plc_connect = self._button(f, "CONNECT", self.toggle_plc, bg=CONNECT_BG, fg="white")
            self.btn_plc_connect.pack(side="left", padx=(0, 6))
            self.btn_plc_test = self._button(f, "TEST", self.test_plc)
            self.btn_plc_test.pack(side="left")
            return f

        self.cb_plc_port, self.cb_plc_baud, self.ent_plc_station, _actions, self.lbl_plc_status = self._device_row(
            table, PLC_COLS, 0, "PLC (LS XGB)",
            [
                lambda p: self._combo(p, [], cfg["port"], width=14),
                lambda p: self._combo(p, plc_xgb.BAUD_RATES, cfg["baudrate"], width=11),
                lambda p: self._entry(p, cfg["station_id"], width=7, digits_only=True),
                make_actions,
                lambda p: tk.Label(p, text="Not connected", bg=p["bg"], fg=theme.MUTED, font=(theme.FONT, 10)),
            ],
        )
        self.ent_plc_station.config(justify="center")

        for i, lc in enumerate(self.loadcells):
            self._build_loadcell_row(table, i, lc)

        row = self._option_row()
        self._label(row, f"Loadcells: {loadcell.MODEL}, 8N1. Station ID = indicator ID Number (F-40): "
                         "00 = stream mode, 01–99 = command mode (polled with ID01P).",
                    fg=theme.MUTED, font=(theme.FONT, 10)).pack(side="left")

        # TEST reads these bit devices
        row = self._option_row()
        self._label(row, "Test Bits", font=(theme.FONT, 11, "bold"), width=18, anchor="w").pack(side="left")
        self.ent_plc_bits = self._entry(row, cfg["test_bits"], width=30)
        self.ent_plc_bits.pack(side="left", padx=(0, 12))
        self._label(row, "XGB bit addresses read by TEST, e.g. P0000, P0001, M0010",
                    fg=theme.MUTED, font=(theme.FONT, 10)).pack(side="left")

        # PLC register read (D devices)
        row = self._option_row()
        self._label(row, "PLC Register Read", font=(theme.FONT, 11, "bold"), width=18, anchor="w").pack(side="left")
        self._label(row, "Address (e.g. D0001)", fg=theme.MUTED, font=(theme.FONT, 10)).pack(side="left")
        self.ent_reg_addr = self._entry(row, cfg["reg_address"], width=10)
        self.ent_reg_addr.pack(side="left", padx=(6, 18))
        self._label(row, "Points", fg=theme.MUTED, font=(theme.FONT, 10)).pack(side="left")
        self.ent_reg_points = self._entry(row, cfg["reg_points"], width=5, digits_only=True)
        self.ent_reg_points.pack(side="left", padx=(6, 18))
        self.btn_reg_read = self._button(row, "READ", self.read_registers, bg=theme.UPDATE, fg="white")
        self.btn_reg_read.pack(side="left")

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
        self.btn_plc_connect.config(text="DISCONNECT" if connected else "CONNECT",
                                    bg=DISCONNECT_BG if connected else CONNECT_BG,
                                    activebackground=DISCONNECT_BG if connected else CONNECT_BG,
                                    state="normal")
        self.btn_plc_test.config(state="normal" if connected else "disabled")
        self.btn_reg_read.config(state="normal" if connected else "disabled")
        for w in (self.cb_plc_port, self.cb_plc_baud):
            w.config(state="disabled" if connected else "readonly")
        self.ent_plc_station.config(state="disabled" if connected else "normal")
        if connected:
            self.lbl_plc_status.config(text=f"Connected on {self.plc.port}", fg=theme.PASS)
        elif error:
            self.lbl_plc_status.config(text="Connection failed", fg=theme.FAIL)
        else:
            self.lbl_plc_status.config(text="Not connected", fg=theme.MUTED)

    def toggle_plc(self):
        if self.plc.is_open:
            self.plc.disconnect()
            self.log("PLC disconnected.")
            self._show_plc_state()
            return
        try:
            cfg = self._plc_config()
        except ValueError as exc:
            self.log(f"PLC: {exc}")
            return
        self.btn_plc_connect.config(text="CONNECTING...", bg=theme.BUSY, state="disabled")
        self.lbl_plc_status.config(text="Connecting...", fg=theme.BUSY)

        def run():
            self.log(f"Connecting to PLC on {cfg['port']} ({cfg['baudrate']} 8N1, station {cfg['station_id']})...")
            try:
                self.plc.connect(cfg)
                self.settings["plc"] = cfg
                self.log(f"Connected to PLC on {cfg['port']}.")
                self._ui(self._show_plc_state)
            except Exception as exc:
                self.log(f"PLC Error: {exc}")
                self._ui(self._show_plc_state, error=True)

        threading.Thread(target=run, daemon=True).start()

    def test_plc(self):
        try:
            bits = plc_xgb.parse_bit_list(self.ent_plc_bits.get())
        except ValueError as exc:
            self.log(f"PLC: {exc}")
            return
        if not bits:
            self.log("PLC: enter at least one bit address under Test Bits.")
            return

        def run():
            self.log("Reading bits:")
            try:
                for addr, value in self.plc.read_bits(bits):
                    text = "Error reading coil" if value == "error" else ("ON" if value else "OFF")
                    self.log(f"  {addr} --> {text}")
            except Exception as exc:
                self.log(f"PLC Error: {exc}")
                self._ui(self._after_plc_failure)

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
                self.log(f"PLC Error: {exc}")
                self._ui(self._after_plc_failure)
                return
            self.log(f"Reading {points} holding register(s) from {addr}:")
            for i, v in enumerate(regs):
                self.log(f"  D{start + i:04d} = {v}  (0x{v:04X}, signed {v - 65536 if v >= 32768 else v})")
            for i, big, swapped in plc_xgb.registers_as_floats(regs):
                self.log(f"  D{start + i:04d}-D{start + i + 1:04d} as float: "
                         f"{big:.6g} (high word first) / {swapped:.6g} (low word first)")

        threading.Thread(target=run, daemon=True).start()

    def _after_plc_failure(self):
        # A dropped port leaves pymodbus closed; reflect that on the row.
        if not self.plc.is_open:
            self._show_plc_state(error=True)

    # ------------------------------------------------------------- loadcells
    def _build_loadcell_row(self, table, i, lc):
        cfg = self.settings["loadcells"][i]
        w = {}

        def make_actions(parent):
            f = tk.Frame(parent, bg=parent["bg"])
            w["connect"] = self._button(f, "CONNECT", lambda: self.toggle_loadcell(i), bg=CONNECT_BG, fg="white")
            w["connect"].pack(side="left", padx=(0, 6))
            w["test"] = self._button(f, "TEST", lambda: self.test_loadcell(i))
            w["test"].pack(side="left", padx=(0, 6))
            w["zero"] = self._button(f, "ZERO", lambda: self.zero_loadcell(i), bg="#555", fg="white", width=7)
            w["zero"].pack(side="left")
            return f

        w["port"], w["baud"], w["id"], _actions, w["status"] = self._device_row(
            table, PLC_COLS, i + 1, lc.name,
            [
                lambda p: self._combo(p, [], cfg["port"], width=14),
                lambda p: self._combo(p, loadcell.BAUD_RATES, cfg["baudrate"], width=11),
                lambda p: self._entry(p, f"{int(cfg['device_id']):02d}", width=7, digits_only=True),
                make_actions,
                lambda p: tk.Label(p, text="Not connected", bg=p["bg"], fg=theme.MUTED, font=(theme.FONT, 10)),
            ],
        )
        w["id"].config(justify="center")
        self.lc_rows.append(w)

    def _lc_config(self, i):
        w = self.lc_rows[i]
        dev_id = w["id"].get().strip()
        if not dev_id.isdigit() or int(dev_id) > 99:
            raise ValueError("Indicator ID must be 00 (stream) or 01–99")
        return {"port": w["port"].get().strip(), "baudrate": int(w["baud"].get()), "device_id": int(dev_id)}

    def _show_lc_state(self, i, error=None, text=None):
        lc, w = self.loadcells[i], self.lc_rows[i]
        connected = lc.is_open
        bg = DISCONNECT_BG if connected else CONNECT_BG
        w["connect"].config(text="DISCONNECT" if connected else "CONNECT", bg=bg,
                            activebackground=bg, state="normal")
        w["test"].config(state="normal" if connected else "disabled")
        w["zero"].config(state="normal" if connected and not lc.stream_mode else "disabled")
        for key in ("port", "baud"):
            w[key].config(state="disabled" if connected else "readonly")
        w["id"].config(state="disabled" if connected else "normal")
        if text:
            w["status"].config(text=text, fg=theme.PASS if connected else theme.FAIL)
        elif connected:
            w["status"].config(text=f"Connected on {lc.port}", fg=theme.PASS)
        elif error:
            w["status"].config(text="Connection failed", fg=theme.FAIL)
        else:
            w["status"].config(text="Not connected", fg=theme.MUTED)

    def toggle_loadcell(self, i):
        lc = self.loadcells[i]
        if lc.is_open:
            lc.disconnect()
            self.log(f"{lc.name} disconnected.")
            self._show_lc_state(i)
            return
        try:
            cfg = self._lc_config(i)
            lc.connect(cfg)
        except Exception as exc:
            self.log(f"{lc.name} Error: {exc}")
            self._show_lc_state(i, error=True)
            return
        self.settings["loadcells"][i] = cfg
        mode = "stream mode" if lc.stream_mode else f"command mode, ID {lc.device_id:02d}"
        self.log(f"Connected to {lc.name} ({loadcell.MODEL}) on {cfg['port']} ({cfg['baudrate']} 8N1, {mode}).")
        self._show_lc_state(i)

    def test_loadcell(self, i):
        lc = self.loadcells[i]

        def run():
            try:
                sent, reply, value = lc.read()
            except Exception as exc:
                self.log(f"{lc.name}: {exc}")
                self._ui(self._show_lc_state, i, error=True)
                return
            prefix = f"Sent: {sent}   " if sent else "Stream: "
            self.log(f"{lc.name}  {prefix}Response: {reply}   Value: {value:g}")
            self._ui(self._show_lc_state, i, text=f"Connected on {lc.port}  |  {value:g}")

        threading.Thread(target=run, daemon=True).start()

    def zero_loadcell(self, i):
        lc = self.loadcells[i]

        def run():
            try:
                sent = lc.zero()
            except Exception as exc:
                self.log(f"{lc.name}: {exc}")
                return
            self.log(f"{lc.name}  Sent: {sent} (zero)")

        threading.Thread(target=run, daemon=True).start()

    # ------------------------------------------------------------------- DAQ
    def _build_daq_section(self):
        cfg = self.settings["daq"]
        self._section_heading("NI DAQ (USB)")
        table = self._table(DAQ_COLS)
        (self.cb_daq_dev, self.ent_daq_ch, self.cb_daq_term,
         self.cb_daq_range, self.btn_daq_test) = self._device_row(
            table, DAQ_COLS, 0, "NI USB-6210",
            [
                lambda p: self._combo(p, [], cfg["device"], width=14),
                lambda p: self._entry(p, ", ".join(cfg["channels"]), width=14),
                lambda p: self._combo(p, ni_daq.TERMINAL_CONFIGS, cfg["terminal_config"], width=11),
                lambda p: self._combo(p, list(ni_daq.VOLTAGE_RANGES), cfg["voltage_range"], width=9),
                lambda p: self._button(p, "TEST", self.test_daq),
            ],
        )

        row = self._option_row()
        self._label(row, "Sample Rate (S/s)").pack(side="left")
        self.ent_daq_rate = self._entry(row, cfg["sample_rate"], width=10)
        self.ent_daq_rate.pack(side="left", padx=(10, 25))
        self._label(row, "Samples / Channel").pack(side="left")
        self.ent_daq_samples = self._entry(row, cfg["samples_per_channel"], width=8)
        self.ent_daq_samples.pack(side="left", padx=(10, 12))
        self._label(row, "Channels: e.g. ai0, ai2 or ai0-ai3", fg=theme.MUTED,
                    font=(theme.FONT, 10)).pack(side="left", padx=(13, 0))

    def _daq_config(self):
        try:
            rate = float(self.ent_daq_rate.get())
            samples = int(self.ent_daq_samples.get())
        except ValueError:
            raise ValueError("Sample rate and samples/channel must be numbers")
        if samples < 1:
            raise ValueError("Samples / channel must be at least 1")
        return {
            "device": self.cb_daq_dev.get().strip(),
            "channels": ni_daq.parse_channels(self.ent_daq_ch.get()),
            "terminal_config": self.cb_daq_term.get(),
            "voltage_range": self.cb_daq_range.get(),
            "sample_rate": rate,
            "samples_per_channel": samples,
        }

    def test_daq(self):
        btn = self.btn_daq_test
        try:
            cfg = self._daq_config()
        except ValueError as exc:
            self.log(f"NI USB-6210: {exc}")
            return

        def run():
            self._ui(btn.config, text="TESTING...", bg=theme.BUSY, fg="black")
            self.log(f"Testing NI USB-6210 on {cfg['device'] or '(none)'} "
                     f"[{', '.join(cfg['channels'])}], {cfg['terminal_config']}, {cfg['voltage_range']}...")
            try:
                self.daq.connect(cfg)
                try:
                    values = self.daq.read_average()
                finally:
                    self.daq.disconnect()
                self.log("NI USB-6210 Success: " + "   ".join(f"{ch} = {v:+.4f} V" for ch, v in values.items()))
                self._ui(btn.config, text="PASS", bg=theme.PASS, fg="white")
            except Exception as exc:
                self.log(f"NI USB-6210 Error: {exc}")
                self._ui(btn.config, text="FAIL", bg=theme.FAIL, fg="white")

        threading.Thread(target=run, daemon=True).start()

    # ---------------------------------------------------------------- bottom
    def _build_bottom(self):
        bar = tk.Frame(self, bg=theme.PANEL)
        bar.pack(side="bottom", fill="x", padx=20, pady=(0, 20))
        tk.Button(bar, text="Update", bg=theme.UPDATE, fg="white", font=(theme.FONT, 11, "bold"),
                  width=12, bd=0, pady=5, cursor="hand2",
                  command=self.refresh_hardware).pack(side="right", padx=(5, 0))
        tk.Button(bar, text="Save", bg=theme.SAVE, fg="white", font=(theme.FONT, 11, "bold"),
                  width=12, bd=0, pady=5, cursor="hand2", command=self.save).pack(side="right", padx=5)
        tk.Button(bar, text="Clear Log", bg="#333", fg="white", font=(theme.FONT, 10, "bold"),
                  width=10, bd=0, pady=5, cursor="hand2", command=self.clear_log).pack(side="left")

    def _build_log(self):
        frame = tk.Frame(self, bg=theme.PANEL, bd=1, relief="solid",
                         highlightbackground=theme.BORDER, highlightthickness=1)
        frame.pack(side="top", fill="both", expand=True, padx=20, pady=(10, 20))
        sb = ttk.Scrollbar(frame, orient="vertical")
        sb.pack(side="right", fill="y")
        self.log_text = tk.Text(frame, bg=theme.PANEL, fg="white", font=("Consolas", 11), bd=0, height=8,
                                yscrollcommand=sb.set)
        self.log_text.pack(fill="both", expand=True, padx=10, pady=10)
        sb.config(command=self.log_text.yview)
        self.log_text.config(state="disabled")

    def clear_log(self):
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.config(state="disabled")

    def refresh_hardware(self, log=True):
        """Rescan COM ports and NI devices (the Update button)."""
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
            self.btn_daq_test.config(text="TEST", bg=theme.TEST_IDLE, fg="black")

    def save(self):
        try:
            self.settings["plc"] = self._plc_config()
            self.settings["loadcells"] = [self._lc_config(i) for i in range(len(self.loadcells))]
            self.settings["daq"] = self._daq_config()
            save_settings(self.settings)
        except Exception as exc:
            self.log(f"Settings not saved: {exc}")
            return
        self.log("Settings saved successfully!")
