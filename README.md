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
- Bottom bar per cable: **Actuator Control** (SERVO ON/OFF, ALARM RESET, Move Origin),
  Front/Rear sensor lamps (PLC bits in `settings.json` → `test.<cable>.front_sensor / rear_sensor`)
  and the clamp counter.
- Right-hand panel, as on the LabVIEW screen:
  - **Model Set** — models from `TBL_MODEL_MASTER`; select one and press **Model Change** to load
    its SELECT CABLE and SHIFT CABLE conditions into the two panels.
  - **Saved to** folder (default `data/`, opened by **OPEN**) and **Saved Number** per cable.
  - **Progress Counter** (Target / Complete) and **O.K/N.G Counter** (A day / Cumulative, each
    with **Del**). Day counts reset at the date change. `record_result()` updates them.
  - **Scan Result** — SELECT and SHIFT text boxes for the scanned barcodes.
  - **Test Run, Test Stop, Move Origin** (RUN / STOP / ORIGIN).
- **EXIT** closes the app (with confirmation).
- The status box lists any device that isn't connected.

### Test flow (by model Type)

Load a model with **Model Change** (its Type, MT or AT, comes from Model Settings), then press
**RUN**. The Scan Result strip shows the current step and the box to scan into is outlined.

- **AT** — scan the **SELECT** label → SELECT CABLE test → scan the **SHIFT** label → SHIFT CABLE
  test → part complete.
- **MT** — scan **one** label → SELECT and SHIFT CABLE tests run **at the same time** → part
  complete when both finish (the label is recorded in both boxes).

After a part completes the flow waits for the next part's first scan. Scans in the wrong box or
while a test is running are refused with a message. **STOP** cancels the flow (and pulses
`test_stop` if set). Model Change is blocked while a flow is active.

Each cable test uses a PLC handshake set in `settings.json` → `test.commands`: the app pulses
`start_select` / `start_shift` and waits (up to 5 min) for `done_select` / `done_shift` to turn ON.
Until these addresses are set, the flow stops at the first test with a message saying so.

The machine buttons (Servo, Alarm Reset, Move Origin, Test Run, Test Stop) write PLC bits set in
`settings.json` → `test.commands` (e.g. `"test_run": "M0100"`). They are empty by default; until
an address is set, the button only reports that in the status box.

The automatic test sequence (recording PULL/PUSH) is not implemented yet;
`CablePanel.show_results()`, `set_live()` and `plot` are where it will report.

## Model Settings

Opening Model Settings or COM Setting shows a sign-in screen first (every time it is opened;
Cancel goes back to the previous page). The Test Console needs no sign-in. There is one temporary operator account for now; its password is stored only
as a salted hash in `friction_tester/auth.py`, which also shows how to generate a new one.

Models are stored in MySQL table **`TBL_MODEL_MASTER`** (database `friction` on the local MySQL
server). The connection (host, port, user, password, database) is set at the top of
`friction_tester/db.py`. The database and table are created automatically on first open.

| Column | Field |
|---|---|
| `MM_MODEL` (unique) | Model |
| `MM_TYPE` | Type — MT or AT (drop-down) |
| `MM_PART_NUMBER` | Part Number |
| `MM_CUSTOMER_NAME` | Customer Name |
| `MM_ALC_CODE` | ALC Code |
| `MM_PRN_TEMPLATE` | PRN Template — chosen from the `.prn` files in `prn_templates/` |
| `MM_SELECT_SPEED`, `MM_SHIFT_SPEED` | SELECT CABLE / SHIFT CABLE test condition — Speed, mm/Sec |
| `MM_SELECT_DELAY_TIME`, `MM_SHIFT_DELAY_TIME` | Delay Time, Sec |
| `MM_SELECT_STROKE`, `MM_SHIFT_STROKE` | Stroke, mm |
| `MM_SELECT_POSITION_1/2/3`, `MM_SHIFT_POSITION_1/2/3` | Position 1–3, mm |
| `MM_SELECT_LOAD_MIN/MAX`, `MM_SHIFT_LOAD_MIN/MAX` | LOAD min ~ max, kgf |

A table made by an earlier version is updated automatically, keeping its data: a single test
condition or "condition 1" becomes SELECT CABLE, "condition 2" becomes SHIFT CABLE, and the
old Test Cable / Saved to / Date / Test Result columns are dropped.

The top of the page has three boxes — Model Information, SELECT CABLE and SHIFT CABLE (each
condition laid out like the Test Console: Speed / Delay / Stroke beside Position 1–3, LOAD
underneath). The model list is below, searchable by model, part number, customer or ALC code.
Empty fields are stored as NULL.

Buttons:
- On opening, the form is locked and only **NEW** is available.
- **NEW** clears and unlocks the form. **SAVE** adds the model; if that model name already exists
  it asks before overwriting.
- Clicking a model in the list opens it for editing (the Model name is locked — it is the key).
  **SAVE** updates it, **DELETE** removes it (with confirmation) and locks the form again.
- All buttons are disabled while a database operation is running.

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
