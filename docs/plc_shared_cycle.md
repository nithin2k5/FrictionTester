# Shared PLC cycle integration

Source: XG5000 ladder printout "NEW NLF MC KOREA MODIFY 09_06_25" (17 pages, printed 2026-11-06).
Code: `friction_tester/plc/` (address map, Modbus mapping, tracker, part flow; the ladder simulator
for the automated tests is `tests/plc_simulator.py`),
wired into the Test Console when `test.integration.mode = "shared_cycle"`.

Everything below was read from the printout. Items the printout does not settle are listed under
[Not established by the ladder](#not-established-by-the-ladder) and in the commissioning table.

## Hardware (page 2–3)

| Slot | Module | Use here |
|---|---|---|
| 0 | XBC-DR32H, inputs P00000–P0000F, outputs P00020–P0002F | start/stop inputs, cycle outputs |
| 1 | XBF-AD04A, CH0/CH1 enabled, 4–20 mA, output 0–4000, 15-sample average | the two analog channels |
| 2 | XBF-PD02A positioning, 2 axes | the stroke (DST moves, status read with SRD into D00500…) |
| 3 | XBE-DR16A | marking section |

## Automatic cycle (pages 11–16)

D05010 is the step.

| Step | What the ladder does |
|---|---|
| 0 | Rung 2: `D05010 = 0 AND (P00002 OR M00029) → MOVP 10 D05010`. MOVP acts on the **rising edge**: if M00029 stayed ON, the cycle would restart as soon as it returned to 0. The app therefore pulses M00029 (ON, 0.3 s, OFF). |
| 10 | Gated by D00500.0 OFF. Starts T0010 (0.1 s); clears D05100, D05110, **D00900–D01099** (FMOVP 200) and **D01200–D01259** (FMOVP 60). Rung 613 resets **M00000, M00003, M0000C, M0000D** while D05010 = 10. After T0010: M02000 OFF → 40, M02000 ON → 20 (pre-cycling 20↔30, D05100 vs D00780). |
| 33 / 34 | Prepare / approach (repeat loop entry). |
| 40 | First-direction stroke (DST to D00730). Samples taken while D00504 is inside a window (see below). |
| 41 / 42 | Turn-around. |
| 50 | Return stroke (DST to D00720). Samples again; `ADDP D05110 +1`. |
| 51 | Final-value chain (below). Back to 33 until D05110 = D00781 (M02003 selects the repeat behaviour). |
| 52 | Same repeat check after T0041. |
| 60 | Home (DST 0 → 1500). |
| 70 | Limit check: `D01250 < D00784 OR D01250 > D00782 → SET M00000`; `D01253 < D00788 OR D01253 > D00786 → SET M00003`. |
| (any) | Rung 524: `NOT(NOT M00000 AND NOT M00003) → SET M0000C` (overall NG if either channel is out of limits). |
| 80 | `NOT M0000C → SET M0000D`; `ADDP D00150 +1` if M0000D, `ADDP D00152 +1` if M0000C; then 0. |
| always | `D00154 = D00150 + D00152`. M01120 / M01121 / M01122 zero the counters. |

**Abort** (rung 598): while 10 ≤ D05010 ≤ 80, any of P00000, P00004, P00008, P0000A, P0000B or D00500.4 going **OFF** jumps to step 0. No verdict, no count.
**Reset** (rungs 613/620): P00003 resets the four verdict bits, D05100, D05110 and drives P00023.
**Outputs**: P0002A = step ≠ 0 (running), P00029 = step = 0 (idle). P00029 (output) and M00029 (start request) are different devices.

## Analog values (page 4)

```
CH0: D00000 = raw (0..4000)      CH1: D00100 = raw
     D00001 = D00000 + 9              D00101 = D00100 − 8
     D00002 = D00001 − 2000           D00102 = D00101 − 2000
     D00015 = D00002 × D00010         D00115 = D00102 × D00110      (gain words)
     D00020 = D00015 / 100            D00120 = D00115 / 100         (32-bit)
     D00030 = D00020 + D00025         D00130 = D00120 + D00125      (32-bit, offset)
```

So D00030 / D00130 are signed 32-bit values centred on 12 mA (raw 2000), scaled by D00010 /
D00110 and offset by D00025 / D00125. **Units are not stated anywhere in the program**, so the
app shows them as raw counts until `force_scale` / `force_unit` are configured.

`MOV 0 D00031` (page 12, rung 136) is behind a normally-closed `_ON` contact and **never runs**.

## Position and sample windows (page 5, 12, 13)

D00504 is copied from the positioning module status (SRD into D00500…). The windows compare it
as a **16-bit word** (`<=3`):

| Window | Lower | Upper | From |
|---|---|---|---|
| P1 | D00800 = D00735 − 20 | D00805 = D00735 + 20 | position setpoint D00735 |
| P2 | D00810 = D00745 − 20 | D00815 = D00745 + 20 | D00745 |
| P3 | D00820 = D00755 − 20 | D00825 = D00755 + 20 | D00755 |
| X1 (extra) | D01400 = D00735 + 980 | D01410 = D00735 + 1020 | P1 + 1000 |
| X2 (extra) | D01420 = D00745 + 980 | D01430 = D00745 + 1020 | P2 + 1000 |

The position's units and scale (pulses, 0.01 mm, …) are not in the printout. The app shows D00504 raw.

### Stored samples (DMOV of the 32-bit readings)

| Channel / direction | P1 | P2 | P3 | X1 | X2 |
|---|---|---|---|---|---|
| Ch1 (D00030) forward, step 40 | D00900 | D00910 | D00920 | D01500 | D01510 |
| Ch2 (D00130) forward, step 40 | D01005 | D01015 | D01025 | D01520 | D01530 |
| Ch1 (D00030) return, step 50 | D00930 | D00940 | D00950 | D01550 | D01560 |
| Ch2 (D00130) return, step 50 | D01035 | D01045 | D01055 | D01570 | D01580 |

Each value is a DINT (low word at the listed address, high word +1). In repeated cycles the last
pass overwrites earlier ones. **Step 10 clears D00900–D01099 but not D01500–D01599**: an extra
(X) value can be left over from an earlier cycle if its window was not crossed. The app shows
X1/X2 separately and never mixes them into positions 1–3.

## Final values (pages 7–9, 14)

Processing rungs (always on):

* Ch1 base samples → D01200, 02, 04, 06, 08, 10 (DMOV), negative values negated (DNEG), low words
  copied to D01212–D01217 and into the averaging group **D01600–D01609**.
* Ch2 base samples → D01220…D01230, D01232–D01237, group **D01610–D01619**.
* The groups mix sources (as printed):

| Group | Members |
|---|---|
| D01600–D01609 | Ch1 F P1, **D01500** (Ch1 F X1), Ch1 F P2, **D01510** (Ch1 F X2), Ch1 F P3, Ch1 R P1, **D01520** (Ch2 F X1), Ch1 R P2, **D01530** (Ch2 F X2), Ch1 R P3 |
| D01610–D01619 | Ch2 F P1, **D01550** (Ch1 R X1), Ch2 F P2, **D01560** (Ch1 R X2), Ch2 F P3, Ch2 R P1, **D01570** (Ch2 R X1), Ch2 R P2, **D01580** (Ch2 R X2), Ch2 R P3 |

* Rung 417 compares D01520 but negates **D01530** before copying D01530 to D01608 (as printed).
* In step 51 (page 14): `AVEP D01600 → D01700 (10)`, `AVEP D01610 → D01710 (10)`,
  `DADD D01700 + D01720 → D01740`, `DADD D01710 + D01730 → D01750`,
  `MOV D01740 → D01250`, `MOV D01750 → D01253`. MAX/MIN of D01212/D01232 go to D01251/52/54/55.
  The four `AVE … D01250/D01253` rungs on the same page are behind normally-closed `_ON` and never run.

The app **does not recalculate** anything: it reads D01250 / D01253 and the PLC's verdict bits.

## Modbus address mapping — NOT verified

`devices/plc_xgb.py` (legacy helpers) converts every bit device to coil `word × 16 + bit` and
every D word to holding register `n`, ignoring the area letter. Under that assumption
**P00029 (idle output) and M00029 (start request) are the same coil 41**, which cannot be right
for both. The XGB Cnet "Modbus" settings that define the real mapping (bit read/write area,
word read/write area) are not in the printout and not recorded in this repository.

So the shared-cycle integration uses an explicit mapping (`test.integration.shared.mapping`),
defaulting to the legacy assumption with `verified: false`. With an unverified mapping the app
monitors (labelled UNVERIFIED in the PLC strip) but **never writes**: M00029 is only written
when `mapping.verified` and `live_start_enabled` are both true.

Word order and signedness: XGB stores a double word low word first (D00030 low, D00031 high);
the app decodes D00030/D00130 and the samples that way as signed 32-bit, and 16-bit words
(step, finals, limits, position) as signed. Counters are read unsigned. Confirm on the machine
by comparing XG5000 monitor values with the app.

## AT / MT on the shared cycle

The operator flow is an application requirement; the ladder has one cycle that measures both
channels at once and one overall verdict (plus the per-channel limit flags M00000 / M00003).

* **MT**: one scan → **one** M00029 pulse → one cycle → one overall verdict → one part. Live
  start requires `mt_one_cycle_tests_both = true` (machine confirmation).
* **AT**: scan SELECT → one cycle → scan SHIFT → one cycle → one part (verdict = both stage
  verdicts OK). The scan does not tell the PLC which cable is loaded, so live start requires
  `at_cable_selection` (only `"operator_fixture"` is implemented: the operator loads only the
  active cable). Otherwise the operator sees **"AT cable selection is not configured."**
* Results go to the panels by `channel_cable` (`"1"/"2" → "select"/"shift"`) and rows by
  `direction_role` (`forward/return → pull/push`). Until set, panels show "PLC Channel n — cable
  not mapped" and rows FWD/RET, in raw counts. Missing values show "—".
* STOP cancels the **application flow only**; no stop/reset bit is written (none is labelled in
  the ladder). The screen says so.

## Completion evidence (plc/shared_cycle.py, CycleTracker)

* On (re)connection the counters and latched bits become the baseline — a stale OK/NG never
  completes a test.
* Armed just before the start request. Completed only when, after arming, the PLC is back at
  step 0 with **exactly one** OK or NG counter increment, consistent with the verdict bits. If the
  counters were reset mid-cycle, a verdict bit seen after the bits were cleared at step 10 is
  accepted instead. Otherwise: "Cycle ended without confirmed result".
* No cycle activity within `start_timeout` s → start timeout. Connection lost while armed →
  ambiguous, never completed, never retried. Activity with nothing armed (physical start, a
  stopped test) is displayed but never counted.
* PLC counts (D00150/152/154) are shown separately from the app's part counters; each confirmed
  part is counted and written (`<Saved to>/results_YYYYMMDD.csv`) once.

## Not established by the ladder

* Modbus area mapping of the XGB Cnet port (and therefore any live write).
* Which channel is the SELECT cable and which the SHIFT cable; which direction is pull/push.
* How an AT part's active cable is selected/loaded; whether one cycle tests both MT cables.
* Units/scale of D00030/D00130 (D00010, D00025, D00110, D00125) and of D00504.
* The meaning of D00500.0 / D00500.4, M02000, M02003, D00720/D00730 targets, D01720/D01730
  (added to the averages), the recipe selection D00080 / M00062 / M00063.
* Any stop or reset command the app could send (P00003 is a physical input).

## Commissioning (to be filled in with the machine technician)

| Item | Where to find it | Setting |
|---|---|---|
| Cnet Modbus areas: table + start for P, M, D | XG5000 → Cnet parameter → Modbus settings | `mapping.areas`, then `mapping.verified: true` after checking a few addresses in XG5000 monitor |
| Is writing M00029 from the PC allowed? | technician / safety owner | `live_start_enabled` |
| Channel 1 / Channel 2 → SELECT / SHIFT cable | wiring of A/D CH0/CH1 | `channel_cable` |
| Forward (step 40) / return (step 50) → pull / push | machine motion | `direction_role` |
| Units and scale of D00030 / D00130 | D00010/D00025, D00110/D00125 calibration | `force_scale`, `force_unit` |
| Units and scale of D00504 | positioning module parameters | `position_scale`, `position_unit` |
| AT: how the active cable is loaded/selected | fixture procedure | `at_cable_selection` |
| MT: one cycle tests both cables? | machine | `mt_one_cycle_tests_both` |
| Repetitions (D00781) and limits (D00782–D00788) per model | recipe D00080 / D02000+ | display only (read from PLC) |
