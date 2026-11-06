# FrictionTester

Tkinter desktop app for a friction tester driven by a PLC (RS232) and an NI USB-6210 DAQ.

## Setup

1. Install the [NI-DAQmx driver](https://www.ni.com/en/support/downloads/drivers/download.ni-daq-mx.html) (required for the USB-6210).
2. Install Python packages:
   ```
   pip install -r requirements.txt
   ```
3. Run:
   ```
   python main.py
   ```

## COM Setting page

The interface follows the FeederCable tester: a black header (logo, page title, clock), sidebar
tiles, and a dark settings panel with one row per device. Each row's **TEST** button shows
TESTING… → PASS / FAIL, and details go to the log box at the bottom.

**PLC – LS XGB (RS232, Modbus RTU 8N1),** laid out like the EOL tester: COM port, baud rate,
station ID, CONNECT / TEST and a status cell. CONNECT opens the port and checks the PLC answers
(coil 0, three tries). TEST reads the *Test Bits* (XGB bit addresses such as `P0000`, `M0010`;
coil = word × 16 + hex bit, so `M0067` → 103). *PLC Register Read* reads D registers
(`D0001`, N points) and shows raw values plus 32-bit float in both word orders.

**Loadcell 01 / 02 (RS232, 8N1),** same protocol as the EOL tester: COM port, baud rate,
CONNECT / TEST and status. TEST sends `ID01P` / `ID02P`, logs the reply, and shows the reading
(second comma-separated field) in the status cell.

**NI USB-6210:** NI device (prefers a 6210), AI channels (e.g. `ai0, ai2` or `ai0-ai3`), terminal
config (Differential uses ai0–ai7), input range (±10 / ±5 / ±1 / ±0.2 V), sample rate (≤ 250 kS/s
aggregate) and samples per channel. TEST acquires one block and logs the mean voltage per channel.

**Update** rescans COM ports and NI devices. **Save** writes `settings.json` next to `main.py`
(git-ignored, machine-specific).

Other sidebar pages (Test Console, Settings, Report, Help) are placeholders for now.
