# FrictionTester

Tkinter desktop app for a friction tester driven by a PLC (RS232), two loadcell indicators
(RS232) and an NI USB-6210 DAQ.

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

- **SELECT CABLE** (Loadcell 01) and **SHIFT CABLE** (Loadcell 02), each with TEST CONDITION
  (speed, delay, stroke, positions 1–3, LOAD min ~ max), TEST RESULTS (PULL1–3, PUSH1–3, means,
  Max, OK/NG — OK when every |reading| is within the LOAD range), a Load vs Stroke graph
  (zoom / Fit / pan) and live LOAD (from the loadcell), STROKE and SPEED (no source yet).
- **Screen Save** writes a PNG of the panel to `screenshots/`.
- Bottom bar per cable: Front/Rear sensor lamps (PLC bits set in `settings.json` →
  `test.<cable>.front_sensor / rear_sensor`) and the clamp counter.
- The status box lists any device that isn't connected.

The automatic test sequence (recording PULL/PUSH) is not implemented yet;
`CablePanel.show_results()`, `set_live()` and `plot` are where it will report.

## Model Settings

Models are stored in MySQL table **`TBL_MODEL_MASTER`** (database `friction` on the local MySQL
server). The connection (host, port, user, password, database) is set at the top of
`friction_tester/db.py`. The database and table are created automatically on first open.

| Column | Field |
|---|---|
| `MM_MODEL` (unique) | Model |
| `MM_PART_NUMBER` | Part Number |
| `MM_CUSTOMER_NAME` | Customer Name |
| `MM_ALC_CODE` | ALC Code |
| `MM_PRN_TEMPLATE` | PRN Template — chosen from the `.prn` files in `prn_templates/` |
| `MM_SPEED` | Speed, mm/Sec |
| `MM_DELAY_TIME` | Delay Time, Sec |
| `MM_STROKE` | Stroke, mm |
| `MM_POSITION_1/2/3` | Position 1–3, mm |
| `MM_LOAD_MIN`, `MM_LOAD_MAX` | LOAD min ~ max, kgf |

A table made by an earlier version is updated automatically (new columns added, the old
Test Cable / Saved to / Date / Test Result columns dropped).

The page lists models (searchable by model, part number, customer or ALC code) on the left and
edits the selected one on the right: NEW, SAVE (insert or update by model), DELETE, and
**APPLY → SELECT / APPLY → SHIFT**, which copy the model's test conditions into that cable panel
on the Test Console and show the model name there.

## COM Setting page

One card per device group on the left, the communication log on the right. Every row has the
same columns (Device, COM Port, Baud Rate, ID, actions, status) and a coloured status label.
**Rescan Ports** refreshes COM ports and NI devices; **Save** writes `settings.json` next to
`main.py` (git-ignored, machine-specific).

**PLC – LS XGB (RS232, Modbus RTU 8N1),** as in the EOL tester. CONNECT opens the port and checks
the PLC answers (coil 0, three tries). TEST reads the *Test bits* (XGB bit addresses such as
`P0000`, `M0010`; coil = word × 16 + hex bit, so `M0067` → 103). *Register* + READ reads D
registers (`D0001`, N points) and shows raw values plus 32-bit float in both word orders.

**Loadcells L1 / L2 – KOMEIN AM-50LW indicators (RS232, 8N1, 2400–57600 baud).** No station ID
on the page (as in the EOL tester). CONNECT detects the mode:

- If the indicator is streaming (F-40 = `000`), TEST takes the next `ST,NT,+01234.5` frame
  (`ER` is reported as an error); ZERO is unavailable.
- Otherwise it is polled in command mode with the loadcell number as ID: TEST sends `ID01P` (L1) /
  `ID02P` (L2) and parses `ID,01,+01234.5`; ZERO sends `ID01Z`. Set F-40 on the indicators to
  `001` and `002` to match.

**NI DAQ – NI USB-6210 analog inputs.** Device (prefers a 6210), wiring mode, sample rate and
samples per reading, then one row per sensor: **Loadcell 1, Loadcell 2, LVDT 1, LVDT 2**. Each row
picks a channel and shows the screw-terminal pins it uses (Differential: `ai0` → `AI0+ / AI8−`,
`ai1` → `AI1 / AI9`, …; RSE: `AIn / AI GND`; NRSE: `AIn / AI SENSE`), plus input range, scale
(units per volt), offset (V) and unit. TEST reads that sensor; TEST ALL reads all four in one
acquisition. Reading = (volts − offset) × scale, shown with the raw voltage.

Defaults: Loadcell 1 = AI0/AI8, Loadcell 2 = AI1/AI9, LVDT 1 = AI2/AI10, LVDT 2 = AI3/AI11.
