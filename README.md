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

## Test Console

Opens at startup; saved devices are connected automatically in the background.
Laid out like the LabVIEW *Friction Test Machine* measurement screen:

- **SELECT CABLE** (Servo 1 + Loadcell 01) and **SHIFT CABLE** (Servo 2 + Loadcell 02), each with
  TEST CONDITION (speed, delay, stroke, positions 1–3, LOAD min ~ max), TEST RESULTS (PULL1–3,
  PUSH1–3, means, Max, OK/NG — OK when every |reading| is within the LOAD range), a Load vs
  Stroke graph (zoom / Fit / pan) and live LOAD, STROKE, SPEED.
- **Screen Save** writes a PNG of the panel to `screenshots/`.
- Bottom bar per cable: SERVO ON/OFF, ALM RST, Front/Rear sensor lamps (PLC bits set in
  `settings.json` → `test.<cable>.front_sensor / rear_sensor`), clamp counter.
- The status box lists any device that isn't connected.

The automatic test sequence (moving the actuator and recording PULL/PUSH) is not implemented yet;
`CablePanel.show_results()` and `CablePanel.plot` are where it will report.

## COM Setting page

The interface follows the FeederCable tester: a black header (logo, page title, clock), sidebar
tiles, and a dark settings panel with one row per device. Each row's **TEST** button shows
TESTING… → PASS / FAIL, and details go to the log box at the bottom.

**PLC – LS XGB (RS232, Modbus RTU 8N1),** laid out like the EOL tester: COM port, baud rate,
station ID, CONNECT / TEST and a status cell. CONNECT opens the port and checks the PLC answers
(coil 0, three tries). TEST reads the *Test Bits* (XGB bit addresses such as `P0000`, `M0010`;
coil = word × 16 + hex bit, so `M0067` → 103). *PLC Register Read* reads D registers
(`D0001`, N points) and shows raw values plus 32-bit float in both word orders.

**Loadcell 01 / 02 – KOMEIN AM-50LW indicators (RS232, 8N1, 2400–57600 baud).** Station ID is
the indicator's ID Number (F-40):

- `01–99` command mode: TEST sends `ID01P` and parses the reply `ID,01,+01234.5`; ZERO sends `ID01Z`.
- `00` stream mode: TEST takes the next `ST,NT,+01234.5` frame (`ER` is reported as an error).

The reading is shown in the status cell and the log.

**Servo 1 / 2 – IAI ROBO Cylinder controllers (RCP2-RA4C actuators), Modbus RTU 8N1** on the
controller's SIO port (RS485, so the PC needs an RS232/USB-RS485 converter; IAI default 38400 baud).
Station ID is the controller's axis No. 0–15 (Modbus slave = axis + 1); both servos may share one
COM port. TEST is read-only: position (9000h, mm), speed (900Ah), current (900Ch), alarm code
(9002h) and status bits from 9005h (SV, HEND, PEND, EMGS, ALMH, …). ALM RST pulses coil 0407h.
Nothing on this page moves the actuators.

**NI USB-6210:** NI device (prefers a 6210), AI channels (e.g. `ai0, ai2` or `ai0-ai3`), terminal
config (Differential uses ai0–ai7), input range (±10 / ±5 / ±1 / ±0.2 V), sample rate (≤ 250 kS/s
aggregate) and samples per channel. TEST acquires one block and logs the mean voltage per channel.

**Update** rescans COM ports and NI devices. **Save** writes `settings.json` next to `main.py`
(git-ignored, machine-specific).
