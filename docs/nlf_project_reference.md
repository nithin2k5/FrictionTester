# NLF project reference: everything in `plc kd All.pdf`

Source: `plc kd All.pdf`, 134 pages printed 2026-11-07 from XG5000, project
**NEW NLF MC KOREA MODIFY 09_06_25.xgwx**, PLC name `NewPLC`. This is the complete client project:
parameters, comment table, both program blocks, used-device list and cross-reference. It is a client
file, not a read-back from the machine. **Whether the live PLC runs it is still unconfirmed.**

Korean comments come out garbled in the printout's text, so meanings here come from the English
comments, the logic, and the rendered ladder (contact types NO/NC, parallel branches). Step numbers
are the ladder's left margin.

Page map: p1-3 cover / system variables (empty) · p4-6 variable/comment table · p7 basic parameters ·
p8-9 I/O parameters · p10-11 high-speed counter · p12-16 positioning · p17-24 PID / auto-tuning ·
p25-31 program 1 (manual / support, = `plc kd.pdf`) · p32-38 program 2 (automatic, = `plc Auto.pdf`)
· p39-120 used devices · p121-133 memory cross-reference · p134 internal Cnet.

## 1. Hardware (p8-9)

| Slot | Module | I/O | Settings |
|---|---|---|---|
| 0 | **XBC-DR32H** CPU, DC 24 V in / relay out, 32 points | P00000-P0000F in, P00020-P0002F out | input filter 3 ms; outputs ch00/01 cleared on error |
| 1 | XBF-AD04A analog input, 4 ch | (P00040-P0007F) | CH0 + CH1 enabled, **4-20 mA**, output 0-4000, filter off, count average 15; CH2/CH3 disabled (0-10 V) |
| 2 | XBF-PD02A positioning, pulse line driver, 2 axes | (P00080-P0011F) | X axis used; Y axis configured but unused |
| 3 | XBE-DR16A, DC 24 V in / relay out, 16 points | P00120-P00127 in, P00140-P00147 out | marking unit; filter 3 ms |

The user reports the machine's CPU as **XBC-DN64H**; the project says XBC-DR32H. Unresolved.

## 2. Basic parameters (p7)

* Fixed-period scan off, watchdog 50 ms, standard input filter 3 ms.
* Output during debugging ON; keep outputs on error OFF.
* Latch areas (kept through power loss): **D00000-D04999**, M00200-M01023, S000-S127,
  C0000-C1023, T0000-T0499 (100 ms), T0500-T0999 (10 ms), T1000-T1023 (1 ms).
  D05000+ is **not** latched, so homing state D05000 and cycle step D05010 restart at 0 after power-up.
* Timer bases: T0000-T0499 = 100 ms, T0500-T0999 = 10 ms, T1000-T1023 = 1 ms.
* Continue running on arithmetic error: ON.

## 3. Positioning parameters (p12-16)

| Item | X | Y |
|---|---|---|
| Pulse output | low active, CW/CCW | same |
| Bias / speed limit | 1 / 100000 pls/s | same |
| ACC/DEC No.1..4 | 500 / 1000 / 1500 / 2000 ms | same |
| S/W limits | ±2^31 pls, detection off | same |
| Upper/lower limit inputs | used | used |
| Home method | DOG/HOME(OFF), direction **CCW**, address 0 | same |
| Home speeds | high 2000, low 300 pls/s | 5000 / 500 |
| Homing ACC/DEC | 1000 / 1000 ms, dwell 0 | same |
| Jog | high 5000, low 1000 pls/s, ACC/DEC 1000 ms; inching 100 pls/s | same |
| M-code output | none | none |

The position data tables (80 steps per axis, p13-16) are all default (ABS, END, 0). Every move uses
`DST` with direct operands; the "200" operand of every DST is the dwell/acceleration word (as printed).
The ladder never runs the module's own homing instruction. The servo driver homes when it gets
P00022, and the PLC then sets a floating origin (`FLT`).

High-speed counter (p10-11) and PID / auto-tuning loops 0-15 (p17-24) are all defaults: **not used**.

## 4. Communication: internal Cnet (p134)

| | CH1 | CH2 |
|---|---|---|
| Type | RS232, null modem | RS485 |
| Data / stop / parity | 8 / 1 / none | 8 / 1 / none |
| Station | 0 | 1 |
| Mode | XGT server (LS protocol) | **Modbus RTU server** |
| Speed | not printed | not printed |

Modbus areas: **read bit P00000, write bit M00000, read word D00000, write word D00000**. The app's
default mapping is exactly this. The live PC port answers Modbus at station 1, so it is on CH2.

## 5. Variable / comment table (p4-6), complete

**Axis status (SRD copy, p26 step 63):** D00500.0 operating · .1 error · .2 positioning complete ·
.3 M-code · .4 homing status · .6 stop · .8/.9 upper/lower limit detection · .A emergency stop ·
.B forward/reverse · .C/.D/.E/.F accel/constant/decel/dwell. D00501.0 position control · .1 speed
control · .2 linear interp. · .4 circular interp. · .5 homing operation · .6/.7 position/speed sync ·
.8/.9 low/high-speed jog · .A inching. D00502.0 X · .1 Y · .2 encoder · .3 master(1)/slave(0).
D00503.4/.5 external upper/lower limit · .6 home signal · .7 near-home (DOG) · .C in-position ·
.D deviation-counter clear output. D00504/505 current position (DINT) · D00506/507 current speed ·
D00508 step number · D00509 M-code · D00510 error code.

**Bits:** M0000C NG · M0000D OK · M00120 marking start · M00121 marking timer ON · M00130 marking
position advance · M00131 marking advance · M00140 marking position alarm · M00141 marking alarm ·
M0014F marking all-alarm · M00150 marking enable/disable pulse · M00151 marking used/not used ·
M00152 marking one cycle · M02000 ageing manual/automatic · M02003 display all data / last data.

**Inputs:** P00000 emergency stop (EMG) · P00002 start switch · P00003 reset · P00004 external
emergency stop · P00008 driver ON · P00009 completion signal · P0000A driver homing · P0000B driver
alarm status · P00019 driver position SET · P0001A driver home · P0001B drive alarm status.
Marking inputs: P00120 position advance · P00121 position retract · P00122 retract · P00123 advance ·
P00124 time OK.

**Outputs:** P00020 servo ON · P00022 homing · P00023 driver alarm reset · P00024 start lamp ·
P00026 reset lamp · P00027 buzzer · P00028/29/2A tower lamp red/yellow/green · P0002B tower buzzer ·
P0002F start lamp · P00030, P00038 servo ON · P00140 marking position solenoid · P00141 marking
solenoid · P00142 marking timer ON.

**Steps / timers:** S000.01 marking position advance · .02 advance · .03 retract · .04 position
retract · .05 end. T0025 marking position alarm delay · T0026 marking alarm delay · T0120 marking
advance delay.

**Module labels:** U01.00.0 A/D error · U01.00.F A/D ready · U01.01.0-.3 CH0-3 operating · U01.11.0
error clear · U01.02-U01.05 CH0-3 converted value · U02.00.F positioning ready · U02.01.0/.1/.2 X jog
CW / CCW / low-high speed · U02.01.3 X completion clear · U02.01.4-.7 Y the same.

**Words:** D00030 select load cell value · D00130 shift load cell value · D00730 total distance ·
D00735 position 1 · D00740 home position · D00745 position 2 · D00750 speed · D00755 position 3 ·
D00760 ageing + stroke · D00770 ageing - stroke · D00780 ageing counter · D00781 measurement count ·
D00782 "select pull lower limit" · D00783 "lower limit value" · D00784 "select pull upper limit" ·
D00786 "shift pull lower limit" · D00788 "shift pull upper limit" · D00790 ageing speed · D00792
select push upper limit · D00794 shift push lower limit · D00795 stop waiting time · D00796 shift
push upper limit · D00800/805, D00810/815, D00820/825 position 1/2/3 lower/upper window · D00910
second position load · D00930 PULL1 load · D01025 SHIFT load 3 · D01200 first position load · D01204
third position load · D01206/08/10 PULL1/2/3 load · D01220/22/24 SHIFT load 1/2/3 · D01226/28/30
SHIFT PULL1/2/3 · D05100, D05110 execution counters.

**Not used anywhere in the ladder** (cross-reference): D00783, D00792, D00794, D00796 (the push
limits), M00150, M00151, P00019, P0001A, P0001B, P00024, P00030, P00038.

> **Limit names vs logic (important).** Step 70 (p36, rung 501) sets M00000 when
> `D01250 < D00784` **or** `D01250 > D00782`, and M00003 when `D01253 < D00788` or `D01253 > D00786`.
> The ladder therefore uses **D00784 / D00788 as lower bounds and D00782 / D00786 as upper bounds**.
> Read with the comment-table names, the OR would be true for every value and every part would be NG.
> The app follows the ladder. Check on the HMI which value the operators enter into D00782.

## 6. Program 1: manual / support (p25-31, steps 1-862)

| Step | Logic |
|---|---|
| 1-60 | Analog: `U01.02 -> D00000`, `+9 -> D00001`, `-2000 -> D00002 -> D00005`, `x D00010 -> D00015`, `/100 -> D00020`, `+ D00025 -> D00030` (DINT); `D00030 + 100 -> D00040`. CH1 the same with `-8`: D00100..D00130, `D00130 + 100 -> D00140`. All gated by U01.00.F ready. |
| 63 | `SRD 2 0 D00500` (X status), copies D00500-D00510 to D00600-D00610, and the status bits as chains to P00500-P00506 (internal, unused). |
| 167 | Windows: D00800/805 = D00735 ∓ 20, D01400/1410 = D00735 + 980/1020, D00810/815 = D00745 ∓ 20, D01420/1430 = D00745 + 980/1020, D00820/825 = D00755 ∓ 20. |
| 209 | `TON T0030 4 s`, always (fires once after RUN). |
| 212 | D05000 = 0 AND (**M00050** OR T0030 rising OR **P00003**) -> D05000 := 10. |
| 220 | D05000 = 10 -> TON T0000 2 s; T0000 AND **P0000A** -> D05000 := 20. |
| 228 | D05000 = 20 -> `FLT 2 0` (floating origin); D00500.4 -> D05000 := 0. |
| 236 | D05000 = 10 AND **NOT M00050** AND NOT P00003 -> **P00022** (driver homing). |
| 242-252 | D00250/252 = D00210/212 x 1000; D00254/256 = D00214/216 x 20 (jog/speed scaling, results unused). |
| 262-276 | Jog, needs D00500.4 (homed) AND P00000 AND P00004: M00020 or M00021 -> X jog CCW; M00022 or M00023 -> X jog CW; M00020 or M00023 -> jog high speed. |
| 283-327 | P00000 AND M0003x AND NOT D00500.0 AND P00004 -> `DST 2 0 <pos> D00750 200 0 0`: M00030 D00735, M00031 D00745, M00032 D00755, M00033 D00730, M00034 D00740. |
| 338-553 | Ch1 samples -> D01200..D01210 (DMOV), negatives negated, low words to D01212-D01217 and D01600-D01609; ch2 -> D01220..D01230, D01232-D01237, D01610-D01619; D01300-D01310 and D01320-D01330 = +100. |
| 579-845 | Recipe: D00080 = n (1..20); M00062 -> `GMOVP D02000+100(n-1) D00700 99` (load); M00063 -> `GMOVP D00700 D02000+100(n-1) 99` (save). |
| 860 | P00020 servo ON = always. |

**M00050 is never reset by the PLC.** It appears only at 212 (NO) and 236 (NC). Whoever sets it must
release it once D05000 leaves 0: otherwise P00022 never turns on, and homing restarts at state 0.
The app does this (`plc/kd_flow.py`, `HomeOp`).

## 7. Program 2: automatic cycle (p32-38, steps 2-748)

| Step | D05010 | Logic |
|---|---|---|
| 2 | 0 | P00002 OR **M00029** -> `MOVP 10` (edge: pulse M00029) |
| 11 | 10 | NOT busy: T0010 0.1 s; clear D05100, D05110, D00900-D01099, D01200-D01259; then M02000 OFF -> 40, ON -> 20 |
| 46 | 20 | ageing + stroke: `DST D00760 at D00790`; done (P00009 + T0011 0.1 s) -> 30 |
| 64 | 30 | ageing - stroke: `DST D00770 at D00790`; done -> D05100 < D00780: ++D05100, -> 20; = D00780 -> 32 |
| 96 | 32 | T0017 0.2 s -> 33 |
| 104 | 33 | T0044 = D00795 (stop waiting time) -> 34; `D00302 = D00720 + 200` |
| 119 | 34 | `DST D00302 at 1000`; done (T0050 0.2 s) -> 40 |
| 142 | 40 | `DST D00730 at D00750`; inside each window store D00030 / D00130 (see shared-cycle doc); done (T0013 0.5 s) -> 41 |
| 231 | 41 | T0042 = D00795 -> 42; `D00300 = D00730 - 200` |
| 246 | 42 | `DST D00300 at 1000`; done (T0051 0.2 s) -> 50 |
| 266 | 50 | `DST D00720 at D00750`; sample pulls; done (T0014 0.5 s): D05110 < D00781 -> ++D05110, 51; = -> 51 |
| 368 | 51 | T0040 0.1 s; D01800 = D00781 - 1; averages D01600 -> D01700, D01610 -> D01710 (10), MAX/MIN of D01212/D01232 -> D01251/52/54/55, D01250 = D01700 + D01720, D01253 = D01710 + D01730; M02003 OFF or count reached -> 52; M02003 ON and count not reached -> 33 after T0055 0.5 s |
| 466 | 52 | T0041 0.5 s: D05110 <> D00781 -> 33, = -> 60 |
| 481 | 60 | `DST 0 at 15000`; done (T0018 0.2 s) -> 70 |
| 501 | 70 | T0015 0.2 s; limit check (box above) sets M00000 / M00003; -> 80 |
| 524 | any | NOT(NOT M00000 AND NOT M00003) -> SET M0000C (NG) |
| 528 | 80 | T0016 0.1 s; NOT M0000C -> SET M0000D; ++D00150 (OK) / ++D00152 (NG); -> 0 |
| 552 | always | D00154 = D00150 + D00152 |
| 558-566 | | M01120 zero D00150 · M01121 zero D00152 · M01122 zero all three |
| 577-586 | 10..80 | TMR T0186 (cycle time, 0.1 s) -> D05051; reset at step 10 |
| 590 | | M0000C -> T0187 10 s; buzzer P00027 and P0002D flash (T2S) until T0187 |
| 598 | 10..80 | NC P00000, P00004, P00008, P0000A, P0000B or D00500.4 (any one OFF) -> D05010 := 0 (abort, no verdict) |
| 613 | | P00003 or step 10 -> RESET M00000, M00003, M0000C, M0000D |
| 620 | | P00003 -> D05100 = D05110 = 0, P00023 (driver alarm reset), `CLR 2 0 1` |
| 632 | | P0002E = step 41; P0002F (start lamp) = step 80, 32, 10, 52, or 51 with T0040 |
| 649-655 | | P0002A = step <> 0; P00029 = step 0; P00028 (red) = NOT P00008 / NOT P0000A / NOT P0000B / NOT D00500.4; with 1 s flicker -> P0002B and P00026 |
| 664 | | M03000 = all marking sensors |
| 674-744 | | Marking: M0000D↑ / M0000C↑ / M00152↑ start M00120 (stops on S000.05, P00003, NOT P00000, M0014F); S000 stepper: 01 position advance (P00140) -> P00120 + T0120 0.3 s -> 02 advance (P00141, timer P00142) -> P00123 + P00124 -> 03 retract -> P00122 -> 04 position retract -> P00121 -> 05 end. T0025 / T0026 3 s -> M00140 / M00141 alarms -> M0014F |

Inputs in "done" conditions: **P00009 = driver completion signal**. D00720 (return target) has no
comment and is part of the recipe area D00700-D00798.

## 8. What the app uses (mode `kd`)

| Function | Devices | Access |
|---|---|---|
| Status | D00500-D00510, D05000, D00030/D00130 | FC04 word read |
| ORIGIN | M00050: set, released on D05000 <> 0 | FC05, coil 80 |
| RUN | M00029 pulse; follow D05010, M0000C/D, D00150/152/154 | FC05 coil 41 + reads |
| Results | D01250/D01253, limits D00782-D00788, samples D00900.., D01500.. | FC04 |
| Moves (no UI) | M00030-M00034 | FC05 |

Not driven: jog, recipe, counter reset (M01120-M01122), marking, ageing (M02000), M02003.

## 9. Still open

1. Live program and Cnet parameters: XG5000 read-back (the printout is not proof).
2. Word reach: on the machine FC04 refused offset 5000, but this project reads D05000 / D05010 /
   D05110 at those offsets. Run `python tools/plc_diag.py --window`.
3. CPU XBC-DN64H (user) vs XBC-DR32H (project).
4. D00782 / D00784 (and 786 / 788): which value the HMI writes into each word (see box in section 5).
5. P0000A / P0000B: "driver homing / driver alarm" here, "part presence" in the client I/O list.
6. Units: load cells (D00010/D00025 scaling) and position (pulses).
