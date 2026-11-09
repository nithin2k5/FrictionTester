# Site visit checklist: PLC communication settings (about 30 minutes)

**Why:** the PC (COM10 → PLC CH2, Modbus RTU, station 1, 57600 8N1) can read the PLC's inputs,
but not its D registers, so ORIGIN and RUN cannot work. Evidence from 2026-11-09:
- D05000 is refused (exception 02), and D00000–D00510 all read 0.
- Start moves P0000E and the E-stop moves P0000F. That is the PRINTING PROGRAM OLD wiring, not
  the NLF project in `plc kd All.pdf`.

**Bring:** a laptop with XG5000, and a loader cable for the CPU (or an RS232 cable to Cnet CH1),
plus this page. **Don't change** the ladder program, the serial settings of CH2, or CH1.

## Before you go (remote, read only)

Close the app on the machine PC and run:

```
python tools/plc_diag.py --scan --port COM10
```

Send the printed summary (and the file `data/plc_scan_*.json`) to the developer. It shows where
each Modbus area currently starts, so the change below is known in advance.

## On site

| # | Step | Record |
|---|---|---|
| 1 | Machine stopped and safe. Close the friction tester app on the PC. | |
| 2 | XG5000 → Online → connect to the PLC (loader port or CH1). Note the **CPU type** and **RUN/STOP** state. | CPU: ______  state: ______ |
| 3 | Online → **Read** (upload) program + parameters. **Save As** `NLF_backup_<date>.xgwx`. | file: ______ |
| 4 | Program identity: project name, and Find → `D04100` and `M00050`. Which is present? | name: ______  D04100 ☐  M00050 ☐ |
| 5 | Open **Internal Cnet** parameters → CH2. **Photograph** the screen before changing anything. | photo ☐ |
| 6 | CH2 must stay: RS485, Modbus RTU server, station 1, 57600, 8/none/1. | as found: ______ |
| 7 | Write down the four Modbus areas **as found**. | bit read ____ · bit write ____ · word read ____ · word write ____ |
| 8 | Set: **read bit P00000 · write bit M00000 · read word D00000 · write word D00000**. | |
| 9 | Online → **Write** → *parameters only* (not the program). Restart/reset the CPU if XG5000 asks. | |
| 10 | Online → Read the parameters back and check that the four areas are as set. Photograph. | photo ☐ |
| 11 | Disconnect XG5000. Start the app on the PC, then close it again and run the checks below. | |

## Checks after the change (on the machine PC, app closed)

```
python tools/plc_diag.py --raw 4 500 11      # axis status: NOT all zero (homed bit D00500.4)
python tools/plc_diag.py --raw 4 5000 1      # homing state D05000: answers (no exception 02)
python tools/plc_diag.py --raw 4 30 2        # select load cell: changes when the load cell is pressed
python tools/plc_diag.py --scan              # FC03/FC04 now reach ~10240 words
```

Compare offset 500 and offset 30 with XG5000's device monitor (D00500, D00030) if XG5000 is still
connected. Send the outputs and photos to the developer. Writes in the app stay **off** until the
values are confirmed; the developer then sets `mapping.verified`.

## If anything goes wrong

Reconnect XG5000 and write the parameters from the backup file in step 3 (parameters only), then
read them back. The machine runs exactly as before. The PC link only affects the friction tester app.

## Open decision (not for this visit unless agreed)

If step 4 shows PRINTING PROGRAM OLD, the client must decide whether that program stays (then the
app uses its `pc_interface` mode, D04000–D04213) or the NLF program (`plc kd All.pdf`) is loaded. The
project is for an XBC-DR32H; check the CPU from step 2 first. That is a program change for the PLC
technician, not part of this checklist.
