"""
LS XGB / XBC-DN64H - IAI RCP2 Manual Jog GUI (standalone).

Uses the PLC's existing PC interface buffers (PRINTING PROGRAM OLD / PLC backup 2026-11-08):
  PC -> PLC: D04100..D04102 (bit command words, copied to M0700..M0702 every scan)
  PLC -> PC: D04000..D04002 (echo of M0700..M0702), D04003..D04005 (M0800..M0802 status),
             D04006 (M0850 word, NOT a confirmed position register)

Communication: Modbus RTU over the XGB built-in Cnet CH2 (RS-485), the same link the main app
uses (settings.json "plc": COM13, 115200 8N1, station 1). Word areas are based at D00000:
FC04 reads status (D04000 = offset 4000), FC03/FC06 read/write the command words.

Address LED monitor: one LED per PLC bit address (command echo, jog echo, axis status),
updated from the same D04000..D04006 poll. Event log: every command written, every LED
change, safety stops and communication errors, shown in the GUI and appended to
logs/manual_jog_YYYYMMDD.log next to this script.

IMPORTANT:
- Do NOT run this and the main Friction Tester app at the same time: both write D04100.
- The PLC has no PC-link watchdog (M00010 is unused): a jog bit left ON keeps jogging until a
  soft limit. This GUI clears it on release, on focus loss, when a safety check fails, after a
  comm error and on close, but it is not an emergency-stop device. Keep the hardwired safety
  circuit active.
- Start with the machine mechanically safe, actuator clear, and low jog speed (D02022/D02122).
- Jog direction must be verified on the machine.
"""

import os
import tkinter as tk
from tkinter import ttk, messagebox
import struct
import threading
import time

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    serial = None
    list_ports = None


# ------------------------- PLC memory map -------------------------
# The ladder copies D04100..D04102 into M0700..M0702.
# M0701 jog commands are bits 4..11.
D_CMD1 = 4100       # M0700
D_JOG = 4101        # M0701
D_CMD3 = 4102       # M0702 (not used by this jog panel)
D_ECHO0 = 4000      # D04000..D04002 = echo of M0700..M0702
D_STATUS0 = 4003    # M0800
D_STATUS1 = 4004    # M0801
D_STATUS2 = 4005    # M0802
D_WORD_FB = 4006    # M0850

MANUAL_ENABLE = 1 << 15      # M0700F
SH_SERVO_REQUEST = 1 << 7    # M07007
SL_SERVO_REQUEST = 1 << 11   # M0700B

JOG_BITS = {
    "SH_LEFT_HIGH": 1 << 4,   # M07014
    "SH_LEFT_LOW":  1 << 5,   # M07015
    "SH_RIGHT_LOW": 1 << 6,   # M07016
    "SH_RIGHT_HIGH":1 << 7,   # M07017
    "SL_LEFT_HIGH": 1 << 8,   # M07018
    "SL_LEFT_LOW":  1 << 9,   # M07019
    "SL_RIGHT_LOW": 1 << 10,  # M0701A
    "SL_RIGHT_HIGH":1 << 11,  # M0701B
}
ALL_JOG_MASK = sum(JOG_BITS.values())

# ------------------------- Address LED map -------------------------
# (group, M address, description, word index in the D04000..D04006 poll, bit, ON colour)
# Colour meaning: green = healthy/ON is good, red = fault/limit, amber = command active.
LED_POINTS = [
    ("cmd",    "M07007", "SH servo ON request",   0, 7,  "green"),
    ("cmd",    "M0700B", "SL servo ON request",   0, 11, "green"),
    ("cmd",    "M0700F", "Manual enable",         0, 15, "amber"),
    ("jog",    "M07014", "SH left high",          1, 4,  "amber"),
    ("jog",    "M07015", "SH left low",           1, 5,  "amber"),
    ("jog",    "M07016", "SH right low",          1, 6,  "amber"),
    ("jog",    "M07017", "SH right high",         1, 7,  "amber"),
    ("jog",    "M07018", "SL left high",          1, 8,  "amber"),
    ("jog",    "M07019", "SL left low",           1, 9,  "amber"),
    ("jog",    "M0701A", "SL right low",          1, 10, "amber"),
    ("jog",    "M0701B", "SL right high",         1, 11, "amber"),
    ("status", "M0800D", "E-stop top (ON=OK)",    3, 13, "green"),
    ("status", "M0800E", "SH servo",              3, 14, "green"),
    ("status", "M08011", "SH no alarm",           4, 1,  "green"),
    ("status", "M0801A", "SH movement enable",    4, 10, "green"),
    ("status", "M0801C", "SH left limit",         4, 12, "red"),
    ("status", "M0801D", "SH right limit",        4, 13, "red"),
    ("status", "M08020", "SH axis error",         5, 0,  "red"),
    ("status", "M08026", "E-stop bottom (ON=OK)", 5, 6,  "green"),
    ("status", "M08012", "SL servo",              4, 2,  "green"),
    ("status", "M08015", "SL no alarm",           4, 5,  "green"),
    ("status", "M0801B", "SL movement enable",    4, 11, "green"),
    ("status", "M0801E", "SL left limit",         4, 14, "red"),
    ("status", "M0801F", "SL right limit",        4, 15, "red"),
    ("status", "M08021", "SL axis error",         5, 1,  "red"),
]

# Bit names for decoding the command words this GUI writes (D04100 echoes in D04000, etc.).
_ECHO_TO_CMD = {0: D_CMD1, 1: D_JOG}
COMMAND_BIT_NAMES = {}
for _g, _addr, _desc, _wi, _bit, _c in LED_POINTS:
    if _wi in _ECHO_TO_CMD:
        COMMAND_BIT_NAMES.setdefault(_ECHO_TO_CMD[_wi], {})[_bit] = f"{_addr} {_desc}"

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
LOG_MAX_LINES = 1000

# Default link = the main app's settings.json "plc" section (found live on the machine 2026-11-09).
DEFAULT_PORT = "COM13"
DEFAULT_BAUD = "115200"
DEFAULT_PARITY = "N"
DEFAULT_STOP = "1"
DEFAULT_STATION = "1"


def crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


MODBUS_EXCEPTIONS = {1: "illegal function", 2: "illegal data address (outside the Cnet area)",
                     3: "illegal data value", 4: "slave device failure"}


class ModbusRtuSerial:
    """Minimal Modbus RTU master for the XGB built-in Cnet CH2 (RS-485) server.

    Cnet areas on this machine (settings.json machine_profile.mapping):
      word_read  base D00000 -> FC04 offset = D number   (D04000 = FC04 4000)
      word_write base D00000 -> FC03 / FC06 offset = D number
    """

    def __init__(self, port, baudrate=115200, parity="N", stopbits=1,
                 bytesize=8, station=1, timeout=0.5):
        if serial is None:
            raise RuntimeError("pyserial is not installed. Run: pip install pyserial")
        self.port_name = port
        self.station = int(station)
        if not 1 <= self.station <= 247:
            raise ValueError("Modbus station must be 1..247")
        self.ser = serial.Serial(
            port=port,
            baudrate=int(baudrate),
            bytesize=int(bytesize),
            parity={"N": serial.PARITY_NONE, "E": serial.PARITY_EVEN,
                    "O": serial.PARITY_ODD}[parity.upper()],
            stopbits=float(stopbits),
            timeout=float(timeout),
            write_timeout=float(timeout),
        )
        self.lock = threading.Lock()
        self.on_write = None    # callback(d_address, value) after a confirmed write

    def _frame(self, pdu: bytes) -> bytes:
        body = bytes([self.station]) + pdu
        crc = crc16_modbus(body)
        return body + bytes([crc & 0xFF, crc >> 8])

    def _read_exact(self, n):
        data = self.ser.read(n)
        if len(data) != n:
            raise TimeoutError("No complete PLC response. Check COM port / baud / parity / station.")
        return data

    def _exchange(self, pdu: bytes, expected_len) -> bytes:
        """Send one request; expected_len(header3) -> total response length."""
        with self.lock:
            self.ser.reset_input_buffer()
            self.ser.write(self._frame(pdu))
            self.ser.flush()
            head = self._read_exact(3)
            if head[0] != self.station:
                raise RuntimeError(f"Response from station {head[0]}, expected {self.station}")
            if head[1] & 0x80:
                rest = self._read_exact(2)
                code = head[2]
                raise RuntimeError(f"PLC Modbus exception {code}: "
                                   f"{MODBUS_EXCEPTIONS.get(code, 'unknown')}")
            if head[1] != pdu[0]:
                raise RuntimeError(f"Function code mismatch: sent {pdu[0]}, got {head[1]}")
            resp = head + self._read_exact(expected_len(head) - 3)
            crc = crc16_modbus(resp[:-2])
            if resp[-2] != (crc & 0xFF) or resp[-1] != (crc >> 8):
                raise RuntimeError("CRC error in PLC response")
            return resp

    def _read(self, fc, d_address, count):
        pdu = bytes([fc]) + struct.pack(">HH", int(d_address), int(count))
        resp = self._exchange(pdu, lambda h: 3 + h[2] + 2)
        if resp[2] != 2 * count:
            raise RuntimeError(f"Expected {2 * count} data bytes, got {resp[2]}")
        return list(struct.unpack(f">{count}H", resp[3:3 + 2 * count]))

    def read_status_words(self, d_address, count=1):
        """D words through the word_read area (FC04)."""
        return self._read(4, d_address, count)

    def read_command_words(self, d_address, count=1):
        """D words through the word_write area (FC03) - the PC's own command words."""
        return self._read(3, d_address, count)

    def write_word(self, d_address: int, value: int):
        value = int(value) & 0xFFFF
        pdu = bytes([6]) + struct.pack(">HH", int(d_address), value)
        resp = self._exchange(pdu, lambda h: 8)
        if resp[1:6] != pdu:
            raise RuntimeError(f"Write echo mismatch: {resp!r}")
        if self.on_write:
            self.on_write(int(d_address), value)

    def close(self):
        if self.ser and self.ser.is_open:
            self.ser.close()


class Led(tk.Canvas):
    """Round LED: True = lit in its colour, False = dark, None = unknown (no data)."""

    COLORS = {"green": "#22c55e", "red": "#ef4444", "amber": "#f59e0b", "blue": "#3b82f6"}
    OFF = "#475569"
    UNKNOWN = "#e2e8f0"

    def __init__(self, parent, color="green", size=16, bg=None):
        super().__init__(parent, width=size, height=size, highlightthickness=0, bd=0, bg=bg)
        self.on_fill = self.COLORS.get(color, color)
        self.oval = self.create_oval(2, 2, size - 2, size - 2, fill=self.UNKNOWN, outline="#1e293b")
        self.state = None

    def set(self, state):
        if state == self.state:
            return
        self.state = state
        fill = self.UNKNOWN if state is None else (self.on_fill if state else self.OFF)
        self.itemconfigure(self.oval, fill=fill)


class ManualJogApp:
    def __init__(self, root):
        self.root = root
        self.root.title("IAI RCP2 | XGB PLC Manual Jog")
        self.root.geometry("1200x880")
        self.root.minsize(1000, 700)
        self.plc = None
        self.connected = False
        self.polling = False
        self.manual_enabled = False
        self.active_jog = None
        self.status = {}
        self.last_error = ""
        self.leds = {}          # address -> (Led, word index, bit, description)
        self.prev_bits = {}     # address -> last polled state, for change logging
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.bind("<FocusOut>", self._on_focus_out)
        self._refresh_ports()
        # Start full screen. F11 toggles, Esc leaves full screen (window stays maximised).
        self.root.bind("<F11>", lambda e: self._set_fullscreen(not self.root.attributes("-fullscreen")))
        self.root.bind("<Escape>", lambda e: self._set_fullscreen(False))
        self._set_fullscreen(True)
        self._log("Manual jog GUI started. Log file: " + self._log_path())

    def _set_fullscreen(self, on):
        self.root.attributes("-fullscreen", bool(on))
        if not on:
            try:
                self.root.state("zoomed")   # Windows: maximised with title bar and taskbar
            except tk.TclError:
                pass

    def _build(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Title.TLabel", font=("Segoe UI", 17, "bold"))
        style.configure("Section.TLabel", font=("Segoe UI", 11, "bold"))
        style.configure("Status.TLabel", font=("Segoe UI", 10))
        style.configure("Addr.TLabel", font=("Consolas", 10, "bold"))
        style.configure("Jog.TButton", font=("Segoe UI", 11, "bold"), padding=12)
        style.configure("Stop.TButton", font=("Segoe UI", 12, "bold"), padding=12)
        self.frame_bg = style.lookup("TFrame", "background") or "#dcdad5"

        outer = ttk.Frame(self.root, padding=14)
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer)
        header.pack(fill="x")
        ttk.Label(header, text="IAI RCP2  |  MANUAL JOG CONTROL", style="Title.TLabel").pack(side="left")
        self.connection_label = ttk.Label(header, text="DISCONNECTED", foreground="#b45309")
        self.connection_label.pack(side="right")

        # Connection settings
        conn = ttk.LabelFrame(outer, text="1. RS-485 / PLC communication settings", padding=10)
        conn.pack(fill="x", pady=(12, 8))
        self.port_var = tk.StringVar()
        self.baud_var = tk.StringVar(value=DEFAULT_BAUD)
        self.parity_var = tk.StringVar(value=DEFAULT_PARITY)
        self.stop_var = tk.StringVar(value=DEFAULT_STOP)
        self.station_var = tk.StringVar(value=DEFAULT_STATION)
        ttk.Label(conn, text="COM Port").grid(row=0, column=0, padx=4, sticky="w")
        self.port_combo = ttk.Combobox(conn, textvariable=self.port_var, width=12, state="readonly")
        self.port_combo.grid(row=1, column=0, padx=4, sticky="w")
        ttk.Button(conn, text="Refresh", command=self._refresh_ports).grid(row=1, column=1, padx=4)
        ttk.Label(conn, text="Baud").grid(row=0, column=2, padx=4, sticky="w")
        ttk.Combobox(conn, textvariable=self.baud_var, values=["9600", "19200", "38400", "57600", "115200"], width=10).grid(row=1, column=2, padx=4)
        ttk.Label(conn, text="Parity").grid(row=0, column=3, padx=4, sticky="w")
        ttk.Combobox(conn, textvariable=self.parity_var, values=["N", "E", "O"], width=5, state="readonly").grid(row=1, column=3, padx=4)
        ttk.Label(conn, text="Stop bits").grid(row=0, column=4, padx=4, sticky="w")
        ttk.Combobox(conn, textvariable=self.stop_var, values=["1", "2"], width=5, state="readonly").grid(row=1, column=4, padx=4)
        ttk.Label(conn, text="Station").grid(row=0, column=5, padx=4, sticky="w")
        ttk.Entry(conn, textvariable=self.station_var, width=7).grid(row=1, column=5, padx=4)
        self.connect_btn = ttk.Button(conn, text="Connect / Read Test", command=self.connect)
        self.connect_btn.grid(row=1, column=6, padx=(12, 4))
        self.disconnect_btn = ttk.Button(conn, text="Disconnect", command=self.disconnect, state="disabled")
        self.disconnect_btn.grid(row=1, column=7, padx=4)
        ttk.Label(conn, text="Protocol: Modbus RTU (XGB Cnet CH2, RS-485). Close the main Friction Tester app first - "
                             "only one program may own the COM port and D04100.",
                  foreground="#555").grid(row=2, column=0, columnspan=8, sticky="w", pady=(8, 0))

        # Safety / manual controls
        safety = ttk.LabelFrame(outer, text="2. Manual enable and servo requests", padding=10)
        safety.pack(fill="x", pady=8)
        self.manual_btn = ttk.Button(safety, text="ENABLE MANUAL", command=self.enable_manual, state="disabled")
        self.manual_btn.pack(side="left", padx=5)
        self.manual_off_btn = ttk.Button(safety, text="DISABLE MANUAL", command=self.disable_manual, state="disabled")
        self.manual_off_btn.pack(side="left", padx=5)
        self.sh_servo_btn = ttk.Button(safety, text="SH SERVO ON REQUEST", command=lambda: self.set_servo("SH", True), state="disabled")
        self.sh_servo_btn.pack(side="left", padx=5)
        self.sh_servo_off_btn = ttk.Button(safety, text="SH SERVO OFF", command=lambda: self.set_servo("SH", False), state="disabled")
        self.sh_servo_off_btn.pack(side="left", padx=5)
        self.sl_servo_btn = ttk.Button(safety, text="SL SERVO ON REQUEST", command=lambda: self.set_servo("SL", True), state="disabled")
        self.sl_servo_btn.pack(side="left", padx=5)
        self.sl_servo_off_btn = ttk.Button(safety, text="SL SERVO OFF", command=lambda: self.set_servo("SL", False), state="disabled")
        self.sl_servo_off_btn.pack(side="left", padx=5)

        # Footer (packed before the expanding areas so it always stays visible)
        footer = ttk.Frame(outer)
        footer.pack(side="bottom", fill="x", pady=(4, 0))
        msgs = ttk.Frame(footer)
        msgs.pack(side="left", fill="x", expand=True)
        self.feedback_var = tk.StringVar(value="Not connected. No PLC commands will be sent.")
        ttk.Label(msgs, textvariable=self.feedback_var, foreground="#334155").pack(anchor="w")
        self.comm_var = tk.StringVar(value="")
        ttk.Label(msgs, textvariable=self.comm_var, foreground="#64748b").pack(anchor="w")
        ttk.Button(footer, text="STOP ALL JOG COMMANDS", style="Stop.TButton", command=self.stop_all).pack(side="right")

        # Address LED monitor + event log
        bottom = ttk.Frame(outer)
        bottom.pack(side="bottom", fill="x", pady=8)
        self._led_panel(bottom)
        self._log_panel(bottom)

        # Jog panels
        jogs = ttk.Frame(outer)
        jogs.pack(fill="both", expand=True, pady=8)
        self._axis_panel(jogs, "SH  |  SHIFT LINE", "SH", 0)
        self._axis_panel(jogs, "SL  |  SELECT LINE", "SL", 1)

    def _led_panel(self, parent):
        panel = ttk.LabelFrame(parent, text="3. Address LED monitor (live PLC read, D04000..D04006)", padding=8)
        panel.pack(side="left", fill="y")
        groups = {
            "cmd": ttk.LabelFrame(panel, text="Command echo D04000", padding=6),
            "jog": ttk.LabelFrame(panel, text="Jog echo D04001", padding=6),
            "status": ttk.LabelFrame(panel, text="Axis status D04003..D04005", padding=6),
        }
        groups["cmd"].grid(row=0, column=0, sticky="nw", padx=4)
        groups["jog"].grid(row=0, column=1, rowspan=2, sticky="nw", padx=4)
        groups["status"].grid(row=0, column=2, rowspan=2, sticky="nw", padx=4)
        rows = {"cmd": 0, "jog": 0, "status": 0}
        for group, addr, desc, wi, bit, color in LED_POINTS:
            n = rows[group]
            rows[group] += 1
            r, c = (n % 7, (n // 7) * 3) if group == "status" else (n, 0)
            frame = groups[group]
            led = Led(frame, color, bg=self.frame_bg)
            led.grid(row=r, column=c, padx=(0 if c == 0 else 12, 4), pady=2)
            ttk.Label(frame, text=addr, style="Addr.TLabel").grid(row=r, column=c + 1, sticky="w")
            ttk.Label(frame, text=desc).grid(row=r, column=c + 2, sticky="w", padx=(6, 0))
            self.leds[addr] = (led, wi, bit, desc)

        word = ttk.LabelFrame(panel, text="D04006 / M0850 word", padding=6)
        word.grid(row=1, column=0, sticky="nw", padx=4, pady=(6, 0))
        self.word_fb_var = tk.StringVar(value="----")
        ttk.Label(word, textvariable=self.word_fb_var, style="Addr.TLabel").pack(anchor="w")
        ttk.Label(word, text="not a confirmed\nposition register", foreground="#64748b").pack(anchor="w")

        legend = ttk.Frame(panel)
        legend.grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))
        for color, text in (("green", "ON / healthy"), ("amber", "command ON"),
                            ("red", "limit / fault ON"), (None, "no data")):
            led = Led(legend, color or "green", size=12, bg=self.frame_bg)
            led.set(True if color else None)
            led.pack(side="left", padx=(0, 3))
            ttk.Label(legend, text=text, foreground="#475569").pack(side="left", padx=(0, 12))
        Led_off = Led(legend, "green", size=12, bg=self.frame_bg)
        Led_off.set(False)
        Led_off.pack(side="left", padx=(0, 3))
        ttk.Label(legend, text="OFF", foreground="#475569").pack(side="left")

    def _log_panel(self, parent):
        panel = ttk.LabelFrame(parent, text="4. Event log", padding=8)
        panel.pack(side="left", fill="both", expand=True, padx=(8, 0))
        bar = ttk.Frame(panel)
        bar.pack(fill="x", pady=(0, 4))
        ttk.Button(bar, text="Clear view", command=self._clear_log_view).pack(side="left")
        ttk.Button(bar, text="Open log folder", command=self._open_log_folder).pack(side="left", padx=6)
        body = ttk.Frame(panel)
        body.pack(fill="both", expand=True)
        self.log_text = tk.Text(body, height=9, width=40, font=("Consolas", 9), wrap="none",
                                state="disabled", bg="#0f172a", fg="#e2e8f0", relief="flat")
        yscroll = ttk.Scrollbar(body, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=yscroll.set)
        yscroll.pack(side="right", fill="y")
        self.log_text.pack(side="left", fill="both", expand=True)
        for level, color in (("INFO", "#e2e8f0"), ("CMD", "#93c5fd"), ("PLC", "#86efac"),
                             ("WARN", "#fcd34d"), ("ERR", "#fca5a5")):
            self.log_text.tag_configure(level, foreground=color)

    def _axis_panel(self, parent, title, axis, col):
        panel = ttk.LabelFrame(parent, text=title, padding=12)
        panel.grid(row=0, column=col, sticky="nsew", padx=6)
        parent.columnconfigure(col, weight=1)
        parent.rowconfigure(0, weight=1)
        ttk.Label(panel, text="Press and HOLD a jog button. Release to stop that jog command.",
                  foreground="#475569").pack(pady=(0, 10))
        grid = ttk.Frame(panel)
        grid.pack(expand=True)
        # Layout: left and right columns, high/low speed, separate stop in middle
        buttons = [
            ("◀ LEFT\nHIGH", f"{axis}_LEFT_HIGH", 0, 0),
            ("◀ LEFT\nLOW", f"{axis}_LEFT_LOW", 1, 0),
            ("RIGHT ▶\nHIGH", f"{axis}_RIGHT_HIGH", 0, 2),
            ("RIGHT ▶\nLOW", f"{axis}_RIGHT_LOW", 1, 2),
        ]
        for text, key, row, colx in buttons:
            b = tk.Button(grid, text=text, width=13, height=2, bg="#dbeafe",
                          activebackground="#93c5fd", font=("Segoe UI", 10, "bold"),
                          relief="raised")
            b.grid(row=row, column=colx, padx=8, pady=6)
            b.bind("<ButtonPress-1>", lambda e, k=key: self.jog_start(k))
            b.bind("<ButtonRelease-1>", lambda e, k=key: self.jog_stop(k))
            b.bind("<Leave>", lambda e, k=key: self.jog_stop(k))
            b.configure(state="disabled")
            setattr(self, f"btn_{key}", b)
        stop = ttk.Button(grid, text="STOP", command=self.stop_all)
        stop.grid(row=0, column=1, rowspan=2, padx=12, ipadx=8, ipady=12)
        setattr(self, f"{axis.lower()}_stop_btn", stop)
        ttk.Label(panel, text="Jog direction must be verified against the actual machine before production use.",
                  foreground="#9a3412", wraplength=390).pack(pady=(10, 0))

    # ------------------------- log -------------------------
    @staticmethod
    def _log_path():
        return os.path.join(LOG_DIR, f"manual_jog_{time.strftime('%Y%m%d')}.log")

    def _log(self, msg, level="INFO"):
        line = f"{time.strftime('%H:%M:%S')} [{level:<4}] {msg}\n"
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line, level)
        excess = int(self.log_text.index("end-1c").split(".")[0]) - LOG_MAX_LINES
        if excess > 0:
            self.log_text.delete("1.0", f"{excess + 1}.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            with open(self._log_path(), "a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d ") + line)
        except OSError:
            pass    # the on-screen log still works if the file cannot be written

    def _feedback(self, msg, level="INFO"):
        self.feedback_var.set(msg)
        self._log(msg, level)

    def _log_write(self, d_address, value):
        names = COMMAND_BIT_NAMES.get(d_address, {})
        on = [name for bit, name in sorted(names.items()) if value & (1 << bit)]
        self._log(f"WRITE D{d_address:05d} <- 0x{value:04X}  ON: {', '.join(on) or 'none'}", "CMD")

    def _clear_log_view(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _open_log_folder(self):
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            os.startfile(LOG_DIR)
        except Exception as exc:
            messagebox.showinfo("Log folder", f"{LOG_DIR}\n\n({exc})")

    def _set_leds_unknown(self):
        for led, _wi, _bit, _desc in self.leds.values():
            led.set(None)
        self.word_fb_var.set("----")
        self.prev_bits = {}

    # ------------------------- connection -------------------------
    def _refresh_ports(self):
        if list_ports is None:
            ports = []
        else:
            ports = [p.device for p in list_ports.comports()]
        self.port_combo["values"] = ports
        if ports and self.port_var.get() not in ports:
            self.port_var.set(DEFAULT_PORT if DEFAULT_PORT in ports else ports[0])

    def connect(self):
        if self.connected:
            return
        if serial is None:
            messagebox.showerror("Missing package", "Install pyserial first:\n\npip install pyserial")
            return
        if not self.port_var.get():
            messagebox.showwarning("COM port required", "Select the RS-485 COM port.")
            return
        self._log(f"Connecting {self.port_var.get()} {self.baud_var.get()} {self.parity_var.get()}"
                  f"8{self.stop_var.get()} station {self.station_var.get().strip()}")
        try:
            self.plc = ModbusRtuSerial(
                self.port_var.get(), self.baud_var.get(), self.parity_var.get(),
                self.stop_var.get(), 8, self.station_var.get().strip(), 0.5
            )
            self.plc.on_write = self._log_write
            # Read-only connection test: status block and command words.
            self.plc.read_status_words(D_ECHO0, 7)
            cmd = self.plc.read_command_words(D_CMD1, 2)
            self.connected = True
            self.last_error = ""
            self.connection_label.configure(text="CONNECTED – MODBUS RTU", foreground="#15803d")
            self.connect_btn.configure(state="disabled")
            self.disconnect_btn.configure(state="normal")
            for w in [self.manual_btn, self.manual_off_btn, self.sh_servo_btn,
                      self.sh_servo_off_btn, self.sl_servo_btn, self.sl_servo_off_btn]:
                w.configure(state="normal")
            for key in JOG_BITS:
                getattr(self, f"btn_{key}").configure(state="normal")
            self._log(f"Read D04100 = 0x{cmd[0]:04X}, D04101 = 0x{cmd[1]:04X}")
            self.manual_enabled = bool(cmd[0] & MANUAL_ENABLE)
            if cmd[1] & ALL_JOG_MASK:
                # A jog bit left ON by an earlier session keeps the axis moving: clear it now.
                self.plc.write_word(D_JOG, cmd[1] & (~ALL_JOG_MASK & 0xFFFF))
                self._feedback("Connected. A stale jog bit was ON in D04101 and has been cleared.", "WARN")
            else:
                self._feedback("Connected. Read-only status test passed."
                               + (" Manual bit M0700F is already ON." if self.manual_enabled
                                  else " Manual movement is still disabled."))
            self.polling = True
            self._poll()
        except Exception as exc:
            self.connected = False
            self.polling = False
            if self.plc:
                try:
                    self.plc.close()
                except Exception:
                    pass
                self.plc = None
            self._log(f"Connection failed: {exc}", "ERR")
            messagebox.showerror("PLC connection failed", str(exc))

    def disconnect(self):
        if not self.connected:
            return
        # Attempt to remove jog/manual command before closing.
        try:
            self.stop_all()
            self._write_cmd_word(D_CMD1, MANUAL_ENABLE, False)
        except Exception as exc:
            self._log(f"Could not clear commands before disconnect: {exc}", "ERR")
        self.polling = False
        self.connected = False
        self.manual_enabled = False
        self.status = {}
        if self.plc:
            self.plc.close()
            self.plc = None
        self._set_leds_unknown()
        self.connection_label.configure(text="DISCONNECTED", foreground="#b45309")
        self.connect_btn.configure(state="normal")
        self.disconnect_btn.configure(state="disabled")
        for w in [self.manual_btn, self.manual_off_btn, self.sh_servo_btn,
                  self.sh_servo_off_btn, self.sl_servo_btn, self.sl_servo_off_btn]:
            w.configure(state="disabled")
        for key in JOG_BITS:
            getattr(self, f"btn_{key}").configure(state="disabled")
        self._feedback("Disconnected. No new commands can be sent.")

    # ------------------------- commands -------------------------
    def _read_cmd_word(self, address):
        return self.plc.read_command_words(address, 1)[0]

    def _write_cmd_word(self, address, mask, turn_on):
        # Read-modify-write preserves unrelated PC command bits in the same word.
        old = self._read_cmd_word(address)
        new = (old | mask) if turn_on else (old & (~mask & 0xFFFF))
        self.plc.write_word(address, new)

    def enable_manual(self):
        if not self.connected:
            return
        if not messagebox.askyesno("Manual mode", "Enable the PLC's existing PC manual-operation bit M0700F?\n\nEnsure the machine is clear and safe."):
            return
        try:
            self._write_cmd_word(D_CMD1, MANUAL_ENABLE, True)
            self.manual_enabled = True
            self._feedback("Manual-operation request sent (M0700F). Servo requests remain separate.")
        except Exception as exc:
            self._fault(exc)

    def disable_manual(self):
        if not self.connected:
            return
        self.stop_all()
        try:
            self._write_cmd_word(D_CMD1, MANUAL_ENABLE, False)
            self.manual_enabled = False
            self._feedback("Manual-operation request cleared.")
        except Exception as exc:
            self._fault(exc)

    def set_servo(self, axis, on):
        if not self.connected:
            return
        bit = SH_SERVO_REQUEST if axis == "SH" else SL_SERVO_REQUEST
        action = "ON" if on else "OFF"
        if on and not messagebox.askyesno(f"{axis} Servo ON request",
                f"Send {axis} servo ON request through the existing PLC bit?\n\nConfirm the actuator area is clear."):
            return
        try:
            self._write_cmd_word(D_CMD1, bit, on)
            self._feedback(f"{axis} servo {action} request sent. Verify actual servo status in live feedback.")
        except Exception as exc:
            self._fault(exc)

    def _safe_to_jog(self, key):
        s = self.status
        axis = key[:2]
        if not self.manual_enabled:
            return False, "Enable Manual first."
        if not s:
            return False, "PLC status not available."
        if s.get("estop_top") or s.get("estop_bottom"):
            return False, "Emergency-stop input is active. Jog blocked."
        if s.get(f"{axis.lower()}_axis_err", False):
            return False, f"{axis} positioning axis error is active."
        if not s.get(f"{axis.lower()}_servo", False):
            return False, f"{axis} servo status is OFF."
        if not s.get(f"{axis.lower()}_alarm_ok", False):
            return False, f"{axis} alarm/no-alarm status is not healthy."
        if not s.get(f"{axis.lower()}_enable", False):
            return False, f"{axis} movement-enable status is OFF."
        direction = "left" if "_LEFT_" in key else "right"
        if s.get(f"{axis.lower()}_{direction}", False):
            return False, f"{axis} {direction} limit is active."
        return True, ""

    def jog_start(self, key):
        if not self.connected or self.active_jog:
            return
        ok, reason = self._safe_to_jog(key)
        if not ok:
            self._feedback(f"JOG BLOCKED ({key}): " + reason, "WARN")
            return
        try:
            # Clear other jog bits first, then assert exactly one direction/speed bit.
            current = self._read_cmd_word(D_JOG)
            new = (current & (~ALL_JOG_MASK & 0xFFFF)) | JOG_BITS[key]
            self.plc.write_word(D_JOG, new)
            self.active_jog = key
            self._feedback(f"{key}: command ON. Release button to stop.")
        except Exception as exc:
            self._fault(exc)

    def _clear_jog_bits(self, attempts=3):
        """Clear every jog bit in D04101. Retried: a missed release keeps the axis moving."""
        last = None
        for _ in range(attempts):
            try:
                current = self._read_cmd_word(D_JOG)
                self.plc.write_word(D_JOG, current & (~ALL_JOG_MASK & 0xFFFF))
                self.active_jog = None
                return True
            except Exception as exc:
                last = exc
                self._log(f"Jog clear attempt failed: {exc}", "WARN")
        self.active_jog = None
        self._fault(last, clear_jog=False)
        self._feedback("JOG RELEASE FAILED - use the hardwired E-STOP if the axis is still moving. "
                       f"({last})", "ERR")
        return False

    def jog_stop(self, key):
        if not self.connected or self.active_jog != key:
            return
        if self._clear_jog_bits():
            self._feedback(f"{key}: jog command released.")

    def stop_all(self):
        if not self.connected:
            return
        if self._clear_jog_bits():
            self._feedback("All GUI jog bits cleared.")

    # ------------------------- polling -------------------------
    def _poll(self):
        if not self.connected or not self.polling:
            return
        try:
            words = self.plc.read_status_words(D_ECHO0, 7)   # D04000..D04006 in one request
            if self.last_error:
                self._log("PLC communication restored.", "INFO")
                self.last_error = ""
            echo1, w0, w1, w2, fb = words[1], words[3], words[4], words[5], words[6]
            self.status = {
                "sh_servo": bool(w0 & (1 << 14)),       # M0800E (P00010)
                # E-stop inputs are healthy when ON: M0800D = P0000F top, M08026 = P0000D bottom.
                "estop_top": not (w0 & (1 << 13)),      # M0800D
                "sh_alarm_ok": bool(w1 & (1 << 1)),     # M08011 (P00013)
                "sl_servo": bool(w1 & (1 << 2)),        # M08012 (P00014)
                "sl_alarm_ok": bool(w1 & (1 << 5)),     # M08015 (P00017)
                "sh_enable": bool(w1 & (1 << 10)),      # M0801A
                "sl_enable": bool(w1 & (1 << 11)),      # M0801B
                "sh_left": bool(w1 & (1 << 12)),        # M0801C
                "sh_right": bool(w1 & (1 << 13)),       # M0801D
                "sl_left": bool(w1 & (1 << 14)),        # M0801E
                "sl_right": bool(w1 & (1 << 15)),       # M0801F
                "sh_axis_err": bool(w2 & (1 << 0)),     # M08020 (_POS_X_Err)
                "sl_axis_err": bool(w2 & (1 << 1)),     # M08021 (_POS_Y_Err)
                "estop_bottom": not (w2 & (1 << 6)),    # M08026
            }
            # Address LEDs + change log (first poll after connect only sets the baseline).
            first = not self.prev_bits
            for addr, (led, wi, bit, desc) in self.leds.items():
                state = bool(words[wi] & (1 << bit))
                led.set(state)
                if not first and self.prev_bits.get(addr) != state:
                    self._log(f"{addr} {desc}: {'ON' if state else 'OFF'}", "PLC")
                self.prev_bits[addr] = state
            if first:
                on = [a for a, v in self.prev_bits.items() if v]
                self._log("Initial PLC bits ON: " + (", ".join(on) or "none"), "PLC")
            self.word_fb_var.set(f"{fb}  (0x{fb:04X})")
            # Release a held jog as soon as a safety condition goes bad.
            if self.active_jog:
                ok, reason = self._safe_to_jog(self.active_jog)
                if not ok and self._clear_jog_bits():
                    self._feedback("JOG STOPPED: " + reason, "WARN")
            # Kept separate from position: D04006/M0850 is not a confirmed position register.
            self.comm_var.set(f"PLC communication OK {time.strftime('%H:%M:%S')} | "
                              f"Jog echo M0701: 0x{echo1 & ALL_JOG_MASK:04X} | "
                              f"Manual GUI state: {'ENABLED' if self.manual_enabled else 'DISABLED'}")
        except Exception as exc:
            self._fault(exc)
        if self.connected and self.polling:
            self.root.after(300, self._poll)

    def _fault(self, exc, clear_jog=True):
        if str(exc) != self.last_error:
            # Logged once per distinct error, not on every 300 ms retry.
            self._log(f"COMMUNICATION / COMMAND ERROR: {exc}", "ERR")
        self.last_error = str(exc)
        # Stale status must never allow a jog.
        self.status = {}
        self._set_leds_unknown()
        self.comm_var.set("COMMUNICATION / COMMAND ERROR: " + str(exc))
        if clear_jog and self.active_jog:
            # Best effort: the PLC keeps jogging while the bit is ON.
            try:
                current = self._read_cmd_word(D_JOG)
                self.plc.write_word(D_JOG, current & (~ALL_JOG_MASK & 0xFFFF))
            except Exception:
                self._feedback("JOG RELEASE FAILED after a communication error - "
                               "use the hardwired E-STOP if the axis is still moving.", "ERR")
        self.active_jog = None

    def _on_focus_out(self, event):
        # A button release can be lost when the window loses focus mid-jog.
        if self.active_jog and event.widget is self.root:
            self._log("Window lost focus during jog - stopping.", "WARN")
            self.stop_all()

    def on_close(self):
        if self.connected:
            if not messagebox.askyesno("Exit", "Clear GUI jog commands and disconnect?"):
                return
            self.disconnect()
        self._log("Manual jog GUI closed.")
        self.root.destroy()


def main():
    root = tk.Tk()
    app = ManualJogApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
