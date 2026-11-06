"""COM Setting page: PLC over RS232 and the NI USB-6210, in the FeederCable style.

One banded row per device holding its main settings and a TEST button that
turns PASS/FAIL; secondary settings sit on plain rows underneath, and every
test writes to the log box at the bottom.
"""

import threading
import tkinter as tk
from tkinter import ttk

from . import theme
from ..config import save_settings
from ..devices import ni_daq, plc_serial

SERIAL_COLS = (("Device", 200), ("COM Port", 140), ("Baud Rate", 140), ("Format", 120), ("Action", 130))
DAQ_COLS = (("Device", 200), ("NI Device", 140), ("AI Channels", 140), ("Terminal", 120),
            ("Input Range", 120), ("Action", 130))

# data bits / parity / stop bits shorthand, e.g. "8N1"
FORMATS = ["8N1", "8E1", "8O1", "7E1", "7O1", "8N2", "7N2"]
_PARITY_CODE = {"None": "N", "Even": "E", "Odd": "O", "Mark": "M", "Space": "S"}
_PARITY_NAME = {v: k for k, v in _PARITY_CODE.items()}


def _fmt_from_cfg(cfg):
    return f"{cfg['bytesize']}{_PARITY_CODE.get(cfg['parity'], 'N')}{cfg['stopbits']}"


def _fmt_to_cfg(fmt):
    return {"bytesize": int(fmt[0]), "parity": _PARITY_NAME[fmt[1]], "stopbits": fmt[2:]}


class ComPortPage(tk.Frame):
    def __init__(self, master, settings, plc, daq):
        super().__init__(master, bg=theme.PANEL, bd=1, relief="solid",
                         highlightbackground=theme.BORDER, highlightthickness=1)
        self.settings = settings
        self.plc = plc
        self.daq = daq

        self._build_title()
        self._build_serial_section()
        self._build_daq_section()
        self._build_bottom()
        self._build_log()

        self.refresh_hardware(log=False)
        if not plc_serial.PLCSerial.available():
            self.log("pyserial is not installed — run: pip install pyserial")
        if not ni_daq.NIDaq.available():
            self.log("nidaqmx is not installed — run: pip install nidaqmx")

    # --------------------------------------------------------------- helpers
    def _label(self, parent, text, **kw):
        opts = {"bg": parent["bg"], "fg": "white", "font": (theme.FONT, 11)}
        opts.update(kw)
        return tk.Label(parent, text=text, **opts)

    def _entry(self, parent, value, width=12):
        e = tk.Entry(parent, bg=theme.ENTRY_BG, fg="white", insertbackground="white",
                     font=(theme.FONT, 11), width=width, bd=1, relief="solid",
                     highlightbackground=theme.BORDER, highlightthickness=1)
        e.insert(0, str(value))
        return e

    def _combo(self, parent, values, current, width=12, readonly=True):
        values = [str(v) for v in values]
        current = str(current)
        if current and current not in values:
            values.append(current)
        cb = ttk.Combobox(parent, values=values, font=(theme.FONT, 10), width=width,
                          state="readonly" if readonly else "normal")
        if current:
            cb.set(current)
        elif values:
            cb.current(0)
        return cb

    @staticmethod
    def _grid_cols(frame, cols):
        # Fixed column widths keep every row aligned; the slack goes to a
        # trailing column so the table stays gathered on the left.
        for i, (_, w) in enumerate(cols):
            frame.columnconfigure(i, minsize=w, weight=0)
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

    def _device_row(self, table, cols, index, name, widgets):
        """A banded row: name label, then `widgets` (built by callables) in order."""
        band = theme.BAND[index % 2]
        row = self._grid_cols(tk.Frame(table, bg=band), cols)
        row.pack(fill="x")
        name_lbl = tk.Label(row, text=name, bg=band, fg="white", font=(theme.FONT, 11), anchor="w")
        name_lbl.grid(row=0, column=0, sticky="w", padx=(10, 0), pady=7)
        built = []
        for col, make in enumerate(widgets, start=1):
            w = make(row)
            w.grid(row=0, column=col, sticky="w", padx=(10, 0), pady=4)
            built.append(w)

        # Light the whole strip under the pointer. Every child gets the same
        # bindings, because Tk sends <Leave> to the frame on entering a child.
        def tint(color):
            row.config(bg=color)
            name_lbl.config(bg=color)

        for w in (row, name_lbl, *built):
            w.bind("<Enter>", lambda _e: tint(theme.BAND_HOVER), add="+")
            w.bind("<Leave>", lambda _e, c=band: tint(c), add="+")
        return built

    def _test_button(self, parent, command):
        return tk.Button(parent, text="TEST", bg=theme.TEST_IDLE, fg="black",
                         font=(theme.FONT, 10, "bold"), width=9, bd=0, cursor="hand2", command=command)

    def _option_row(self):
        row = tk.Frame(self, bg=theme.PANEL)
        row.pack(fill="x", padx=35, pady=(0, 8))
        return row

    def _set_btn(self, btn, text, bg, fg="white"):
        self.after(0, lambda: btn.config(text=text, bg=bg, fg=fg))

    def log(self, msg):
        """Thread-safe append to the log box."""
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

    # ---------------------------------------------------------------- serial
    def _build_serial_section(self):
        cfg = self.settings["plc"]
        self._section_heading("Serial (RS232)")
        table = self._table(SERIAL_COLS)
        self.cb_plc_port, self.cb_plc_baud, self.cb_plc_fmt, self.btn_plc_test = self._device_row(
            table, SERIAL_COLS, 0, "PLC",
            [
                lambda p: self._combo(p, [], cfg["port"]),
                lambda p: self._combo(p, plc_serial.BAUD_RATES, cfg["baudrate"]),
                lambda p: self._combo(p, FORMATS, _fmt_from_cfg(cfg), width=7),
                lambda p: self._test_button(p, self.test_plc),
            ],
        )

        row = self._option_row()
        self._label(row, "Flow Control").pack(side="left")
        self.cb_plc_flow = self._combo(row, plc_serial.FLOW_CONTROL, cfg["flow_control"], width=10)
        self.cb_plc_flow.pack(side="left", padx=(10, 25))
        self._label(row, "Terminator").pack(side="left")
        self.cb_plc_term = self._combo(row, list(plc_serial.TERMINATORS), cfg["terminator"], width=7)
        self.cb_plc_term.pack(side="left", padx=(10, 25))
        self._label(row, "Timeout (s)").pack(side="left")
        self.ent_plc_timeout = self._entry(row, cfg["timeout"], width=6)
        self.ent_plc_timeout.pack(side="left", padx=(10, 0))

        row = self._option_row()
        self._label(row, "Test Command").pack(side="left")
        self.ent_plc_cmd = self._entry(row, cfg["test_command"], width=24)
        self.ent_plc_cmd.pack(side="left", padx=(10, 12))
        self._label(row, "Sent on TEST — leave blank to only check the port opens",
                    fg=theme.MUTED, font=(theme.FONT, 10)).pack(side="left")

    def _plc_config(self):
        try:
            timeout = float(self.ent_plc_timeout.get())
        except ValueError:
            raise ValueError("Timeout must be a number")
        cfg = {
            "port": self.cb_plc_port.get().strip(),
            "baudrate": int(self.cb_plc_baud.get()),
            "flow_control": self.cb_plc_flow.get(),
            "timeout": timeout,
            "terminator": self.cb_plc_term.get(),
            "test_command": self.ent_plc_cmd.get(),
        }
        cfg.update(_fmt_to_cfg(self.cb_plc_fmt.get()))
        return cfg

    def test_plc(self):
        btn = self.btn_plc_test
        try:
            cfg = self._plc_config()
        except ValueError as exc:
            self.log(f"PLC: {exc}")
            return
        if not cfg["port"]:
            self.log("PLC: No port selected.")
            return

        def run():
            self._set_btn(btn, "TESTING...", theme.BUSY, "black")
            self.log(f"Testing PLC on {cfg['port']} ({cfg['baudrate']}, "
                     f"{self.cb_plc_fmt.get()}, flow {cfg['flow_control']})...")
            try:
                self.plc.connect(cfg)
                try:
                    cmd = cfg["test_command"]
                    if cmd:
                        reply = self.plc.send(cmd)
                        if reply:
                            self.log(f"PLC Success: '{cmd}' -> {reply}")
                            self._set_btn(btn, "PASS", theme.PASS)
                        else:
                            self.log(f"PLC Warning: {cfg['port']} opened, but no reply to '{cmd}'.")
                            self._set_btn(btn, "FAIL", theme.FAIL)
                    else:
                        self.log(f"PLC Success: {cfg['port']} opened.")
                        self._set_btn(btn, "PASS", theme.PASS)
                finally:
                    self.plc.disconnect()
            except Exception as exc:
                self.log(f"PLC Error: {exc}")
                self._set_btn(btn, "FAIL", theme.FAIL)

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
                lambda p: self._combo(p, [], cfg["device"]),
                lambda p: self._entry(p, ", ".join(cfg["channels"]), width=14),
                lambda p: self._combo(p, ni_daq.TERMINAL_CONFIGS, cfg["terminal_config"], width=11),
                lambda p: self._combo(p, list(ni_daq.VOLTAGE_RANGES), cfg["voltage_range"], width=9),
                lambda p: self._test_button(p, self.test_daq),
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
            self._set_btn(btn, "TESTING...", theme.BUSY, "black")
            self.log(f"Testing NI USB-6210 on {cfg['device'] or '(none)'} "
                     f"[{', '.join(cfg['channels'])}], {cfg['terminal_config']}, {cfg['voltage_range']}...")
            try:
                self.daq.connect(cfg)
                try:
                    values = self.daq.read_average()
                finally:
                    self.daq.disconnect()
                self.log("NI USB-6210 Success: " + "   ".join(f"{ch} = {v:+.4f} V" for ch, v in values.items()))
                self._set_btn(btn, "PASS", theme.PASS)
            except Exception as exc:
                self.log(f"NI USB-6210 Error: {exc}")
                self._set_btn(btn, "FAIL", theme.FAIL)

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

    def _build_log(self):
        frame = tk.Frame(self, bg=theme.PANEL, bd=1, relief="solid",
                         highlightbackground=theme.BORDER, highlightthickness=1)
        frame.pack(side="top", fill="both", expand=True, padx=20, pady=(10, 20))
        self.log_text = tk.Text(frame, bg=theme.PANEL, fg="white", font=("Consolas", 11), bd=0, height=8)
        self.log_text.pack(fill="both", expand=True, padx=10, pady=10)
        self.log_text.config(state="disabled")

    def refresh_hardware(self, log=True):
        """Rescan COM ports and NI devices (the Update button)."""
        ports = plc_serial.PLCSerial.list_ports()
        port_names = [dev for dev, _ in ports]
        current = self.cb_plc_port.get()
        self.cb_plc_port.config(values=port_names or ([current] if current else []))
        if not current and port_names:
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
            for btn in (self.btn_plc_test, self.btn_daq_test):
                btn.config(text="TEST", bg=theme.TEST_IDLE, fg="black")

    def save(self):
        try:
            self.settings["plc"] = self._plc_config()
            self.settings["daq"] = self._daq_config()
            save_settings(self.settings)
        except Exception as exc:
            self.log(f"Settings not saved: {exc}")
            return
        self.log("Settings saved successfully!")
