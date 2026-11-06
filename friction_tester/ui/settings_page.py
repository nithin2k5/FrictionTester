"""Communication settings page: PLC (RS232) and NI USB-6210."""

import tkinter as tk
from tkinter import messagebox, ttk

from ..config import save_settings
from ..devices import plc_serial, ni_daq

PAD = {"padx": 6, "pady": 4}


class StatusLamp(ttk.Frame):
    """Small coloured dot + text showing connection state."""

    COLORS = {"connected": "#2e9e44", "disconnected": "#9a9a9a", "error": "#d03b3b"}

    def __init__(self, master):
        super().__init__(master)
        self._canvas = tk.Canvas(self, width=14, height=14, highlightthickness=0)
        self._dot = self._canvas.create_oval(2, 2, 12, 12, fill=self.COLORS["disconnected"], outline="")
        self._canvas.pack(side="left")
        self._label = ttk.Label(self, text="Disconnected")
        self._label.pack(side="left", padx=(4, 0))

    def set(self, state, text):
        self._canvas.itemconfigure(self._dot, fill=self.COLORS[state])
        self._label.configure(text=text)


class SettingsPage(ttk.Frame):
    def __init__(self, master, settings, plc, daq):
        super().__init__(master, padding=10)
        self.settings = settings
        self.plc = plc
        self.daq = daq

        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)

        self._build_plc_frame().grid(row=0, column=0, sticky="nsew", **PAD)
        self._build_daq_frame().grid(row=0, column=1, sticky="nsew", **PAD)

        bottom = ttk.Frame(self)
        bottom.grid(row=1, column=0, columnspan=2, sticky="ew", **PAD)
        ttk.Button(bottom, text="Save Settings", command=self.save).pack(side="right")

        self.refresh_ports()
        self.refresh_devices()

    # ------------------------------------------------------------------ PLC
    def _build_plc_frame(self):
        cfg = self.settings["plc"]
        frame = ttk.LabelFrame(self, text="PLC  –  RS232", padding=10)
        frame.columnconfigure(1, weight=1)

        self.plc_port = tk.StringVar(value=cfg["port"])
        self.plc_baud = tk.StringVar(value=str(cfg["baudrate"]))
        self.plc_bytesize = tk.StringVar(value=str(cfg["bytesize"]))
        self.plc_parity = tk.StringVar(value=cfg["parity"])
        self.plc_stopbits = tk.StringVar(value=str(cfg["stopbits"]))
        self.plc_flow = tk.StringVar(value=cfg["flow_control"])
        self.plc_timeout = tk.StringVar(value=str(cfg["timeout"]))
        self.plc_term = tk.StringVar(value=cfg["terminator"])
        self.plc_cmd = tk.StringVar(value=cfg["test_command"])

        row = 0
        ttk.Label(frame, text="COM Port").grid(row=row, column=0, sticky="w", **PAD)
        self.plc_port_cb = ttk.Combobox(frame, textvariable=self.plc_port, width=28)
        self.plc_port_cb.grid(row=row, column=1, sticky="ew", **PAD)
        ttk.Button(frame, text="⟳", width=3, command=self.refresh_ports).grid(row=row, column=2, **PAD)

        fields = [
            ("Baud Rate", self.plc_baud, plc_serial.BAUD_RATES),
            ("Data Bits", self.plc_bytesize, plc_serial.DATA_BITS),
            ("Parity", self.plc_parity, plc_serial.PARITY_OPTIONS),
            ("Stop Bits", self.plc_stopbits, plc_serial.STOP_BITS),
            ("Flow Control", self.plc_flow, plc_serial.FLOW_CONTROL),
            ("Terminator", self.plc_term, list(plc_serial.TERMINATORS)),
        ]
        self.plc_inputs = [self.plc_port_cb]
        for label, var, values in fields:
            row += 1
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", **PAD)
            cb = ttk.Combobox(frame, textvariable=var, values=[str(v) for v in values], state="readonly")
            cb.grid(row=row, column=1, sticky="ew", **PAD)
            self.plc_inputs.append(cb)

        row += 1
        ttk.Label(frame, text="Timeout (s)").grid(row=row, column=0, sticky="w", **PAD)
        sp = ttk.Spinbox(frame, textvariable=self.plc_timeout, from_=0.1, to=10, increment=0.1)
        sp.grid(row=row, column=1, sticky="ew", **PAD)
        self.plc_inputs.append(sp)

        row += 1
        btns = ttk.Frame(frame)
        btns.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(10, 4))
        self.plc_connect_btn = ttk.Button(btns, text="Connect", command=self.toggle_plc)
        self.plc_connect_btn.pack(side="left", padx=6)
        self.plc_status = StatusLamp(btns)
        self.plc_status.pack(side="left", padx=10)

        row += 1
        test = ttk.LabelFrame(frame, text="Test Communication", padding=6)
        test.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        test.columnconfigure(1, weight=1)
        ttk.Label(test, text="Command").grid(row=0, column=0, sticky="w", **PAD)
        ttk.Entry(test, textvariable=self.plc_cmd).grid(row=0, column=1, sticky="ew", **PAD)
        self.plc_send_btn = ttk.Button(test, text="Send", command=self.send_plc, state="disabled")
        self.plc_send_btn.grid(row=0, column=2, **PAD)
        ttk.Label(test, text="Response").grid(row=1, column=0, sticky="w", **PAD)
        self.plc_response = ttk.Label(test, text="—", relief="sunken", anchor="w")
        self.plc_response.grid(row=1, column=1, columnspan=2, sticky="ew", **PAD)

        if not plc_serial.PLCSerial.available():
            self.plc_status.set("error", "pyserial not installed")
            self.plc_connect_btn.configure(state="disabled")
        return frame

    def refresh_ports(self):
        ports = plc_serial.PLCSerial.list_ports()
        self._port_map = {f"{dev} – {desc}": dev for dev, desc in ports}
        self.plc_port_cb.configure(values=list(self._port_map))
        current = self.plc_port.get()
        # Show the saved port with its description if it's present.
        for label, dev in self._port_map.items():
            if dev == current or label == current:
                self.plc_port.set(label)
                break
        else:
            if not current and self._port_map:
                self.plc_port.set(next(iter(self._port_map)))

    def _selected_port(self):
        value = self.plc_port.get().strip()
        return self._port_map.get(value, value.split(" ")[0] if value else "")

    def _plc_config(self):
        try:
            timeout = float(self.plc_timeout.get())
        except ValueError:
            raise ValueError("Timeout must be a number")
        return {
            "port": self._selected_port(),
            "baudrate": int(self.plc_baud.get()),
            "bytesize": int(self.plc_bytesize.get()),
            "parity": self.plc_parity.get(),
            "stopbits": self.plc_stopbits.get(),
            "flow_control": self.plc_flow.get(),
            "timeout": timeout,
            "terminator": self.plc_term.get(),
            "test_command": self.plc_cmd.get(),
        }

    def toggle_plc(self):
        if self.plc.is_open:
            self.plc.disconnect()
            self._set_plc_connected(False)
            self.plc_status.set("disconnected", "Disconnected")
            return
        try:
            cfg = self._plc_config()
            self.plc.connect(cfg)
        except Exception as exc:
            self.plc_status.set("error", "Error")
            messagebox.showerror("PLC Connection", str(exc), parent=self)
            return
        self.settings["plc"] = cfg
        self._set_plc_connected(True)
        self.plc_status.set("connected", f"Connected  ({cfg['port']} @ {cfg['baudrate']})")

    def _set_plc_connected(self, connected):
        self.plc_connect_btn.configure(text="Disconnect" if connected else "Connect")
        self.plc_send_btn.configure(state="normal" if connected else "disabled")
        for w in self.plc_inputs:
            if connected:
                w.configure(state="disabled")
            else:
                w.configure(state="normal" if w is self.plc_port_cb or isinstance(w, ttk.Spinbox) else "readonly")

    def send_plc(self):
        cmd = self.plc_cmd.get()
        if not cmd:
            return
        try:
            reply = self.plc.send(cmd)
        except Exception as exc:
            self.plc_response.configure(text=f"Error: {exc}")
            return
        self.plc_response.configure(text=reply or "(no response – timeout)")

    # ------------------------------------------------------------------ DAQ
    def _build_daq_frame(self):
        cfg = self.settings["daq"]
        frame = ttk.LabelFrame(self, text="NI USB-6210  –  Analog Input", padding=10)
        frame.columnconfigure(1, weight=1)

        self.daq_device = tk.StringVar(value=cfg["device"])
        self.daq_term = tk.StringVar(value=cfg["terminal_config"])
        self.daq_range = tk.StringVar(value=cfg["voltage_range"])
        self.daq_rate = tk.StringVar(value=str(cfg["sample_rate"]))
        self.daq_samples = tk.StringVar(value=str(cfg["samples_per_channel"]))

        row = 0
        ttk.Label(frame, text="Device").grid(row=row, column=0, sticky="w", **PAD)
        self.daq_device_cb = ttk.Combobox(frame, textvariable=self.daq_device, width=28)
        self.daq_device_cb.grid(row=row, column=1, sticky="ew", **PAD)
        ttk.Button(frame, text="⟳", width=3, command=self.refresh_devices).grid(row=row, column=2, **PAD)

        row += 1
        ttk.Label(frame, text="AI Channels").grid(row=row, column=0, sticky="nw", **PAD)
        lb_frame = ttk.Frame(frame)
        lb_frame.grid(row=row, column=1, sticky="ew", **PAD)
        self.daq_channels = tk.Listbox(lb_frame, selectmode="multiple", height=6, exportselection=False)
        sb = ttk.Scrollbar(lb_frame, orient="vertical", command=self.daq_channels.yview)
        self.daq_channels.configure(yscrollcommand=sb.set)
        self.daq_channels.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        for i, ch in enumerate(ni_daq.AI_CHANNELS):
            self.daq_channels.insert("end", ch)
            if ch in cfg["channels"]:
                self.daq_channels.selection_set(i)

        row += 1
        ttk.Label(frame, text="Terminal Config").grid(row=row, column=0, sticky="w", **PAD)
        term_cb = ttk.Combobox(frame, textvariable=self.daq_term, values=ni_daq.TERMINAL_CONFIGS, state="readonly")
        term_cb.grid(row=row, column=1, sticky="ew", **PAD)

        row += 1
        ttk.Label(frame, text="Input Range").grid(row=row, column=0, sticky="w", **PAD)
        range_cb = ttk.Combobox(frame, textvariable=self.daq_range, values=list(ni_daq.VOLTAGE_RANGES), state="readonly")
        range_cb.grid(row=row, column=1, sticky="ew", **PAD)

        row += 1
        ttk.Label(frame, text="Sample Rate (S/s)").grid(row=row, column=0, sticky="w", **PAD)
        rate_sp = ttk.Spinbox(frame, textvariable=self.daq_rate, from_=1, to=ni_daq.MAX_AGGREGATE_RATE, increment=100)
        rate_sp.grid(row=row, column=1, sticky="ew", **PAD)

        row += 1
        ttk.Label(frame, text="Samples / Channel").grid(row=row, column=0, sticky="w", **PAD)
        samp_sp = ttk.Spinbox(frame, textvariable=self.daq_samples, from_=1, to=100000, increment=10)
        samp_sp.grid(row=row, column=1, sticky="ew", **PAD)

        self.daq_inputs = [self.daq_device_cb, self.daq_channels, term_cb, range_cb, rate_sp, samp_sp]

        row += 1
        btns = ttk.Frame(frame)
        btns.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(10, 4))
        self.daq_connect_btn = ttk.Button(btns, text="Connect", command=self.toggle_daq)
        self.daq_connect_btn.pack(side="left", padx=6)
        self.daq_status = StatusLamp(btns)
        self.daq_status.pack(side="left", padx=10)

        row += 1
        test = ttk.LabelFrame(frame, text="Test Acquisition", padding=6)
        test.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        test.columnconfigure(1, weight=1)
        self.daq_read_btn = ttk.Button(test, text="Read", command=self.read_daq, state="disabled")
        self.daq_read_btn.grid(row=0, column=0, **PAD)
        self.daq_reading = ttk.Label(test, text="—", relief="sunken", anchor="w")
        self.daq_reading.grid(row=0, column=1, sticky="ew", **PAD)

        if not ni_daq.NIDaq.available():
            self.daq_status.set("error", "nidaqmx not installed")
            self.daq_connect_btn.configure(state="disabled")
        return frame

    def refresh_devices(self):
        devices = ni_daq.NIDaq.list_devices()
        self._device_map = {f"{name} – {product}": name for name, product in devices}
        self.daq_device_cb.configure(values=list(self._device_map))
        current = self.daq_device.get()
        for label, name in self._device_map.items():
            if name == current or label == current:
                self.daq_device.set(label)
                break
        else:
            # Prefer a USB-6210 if one is attached.
            if not current:
                for label in self._device_map:
                    if "6210" in label:
                        self.daq_device.set(label)
                        break
                else:
                    if self._device_map:
                        self.daq_device.set(next(iter(self._device_map)))
        if ni_daq.NIDaq.available() and not devices:
            self.daq_status.set("error", "No NI-DAQmx devices found")

    def _selected_device(self):
        value = self.daq_device.get().strip()
        return self._device_map.get(value, value.split(" ")[0] if value else "")

    def _daq_config(self):
        try:
            rate = float(self.daq_rate.get())
            samples = int(self.daq_samples.get())
        except ValueError:
            raise ValueError("Sample rate and samples/channel must be numbers")
        if samples < 1:
            raise ValueError("Samples / channel must be at least 1")
        return {
            "device": self._selected_device(),
            "channels": [self.daq_channels.get(i) for i in self.daq_channels.curselection()],
            "terminal_config": self.daq_term.get(),
            "voltage_range": self.daq_range.get(),
            "sample_rate": rate,
            "samples_per_channel": samples,
        }

    def toggle_daq(self):
        if self.daq.is_open:
            self.daq.disconnect()
            self._set_daq_connected(False)
            self.daq_status.set("disconnected", "Disconnected")
            return
        try:
            cfg = self._daq_config()
            self.daq.connect(cfg)
        except Exception as exc:
            self.daq_status.set("error", "Error")
            messagebox.showerror("DAQ Connection", str(exc), parent=self)
            return
        self.settings["daq"] = cfg
        self._set_daq_connected(True)
        self.daq_status.set("connected", f"Connected  ({cfg['device']}, {len(cfg['channels'])} ch)")

    def _set_daq_connected(self, connected):
        self.daq_connect_btn.configure(text="Disconnect" if connected else "Connect")
        self.daq_read_btn.configure(state="normal" if connected else "disabled")
        for w in self.daq_inputs:
            if connected:
                w.configure(state="disabled")
            elif isinstance(w, ttk.Combobox) and w is not self.daq_device_cb:
                w.configure(state="readonly")
            else:
                w.configure(state="normal")

    def read_daq(self):
        try:
            values = self.daq.read_average()
        except Exception as exc:
            self.daq_reading.configure(text=f"Error: {exc}")
            return
        self.daq_reading.configure(text="   ".join(f"{ch}: {v:+.4f} V" for ch, v in values.items()))

    # --------------------------------------------------------------- common
    def save(self):
        try:
            self.settings["plc"] = self._plc_config()
            self.settings["daq"] = self._daq_config()
            save_settings(self.settings)
        except Exception as exc:
            messagebox.showerror("Save Settings", str(exc), parent=self)
            return
        messagebox.showinfo("Save Settings", "Settings saved.", parent=self)
