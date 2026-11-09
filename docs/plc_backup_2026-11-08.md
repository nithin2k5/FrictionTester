# PLC backup of 2026-11-08 (PLCBCKUP_SURESH08_11_2026.pdf)

**Source:** `PLCBCKUP_SURESH08_11_2026.pdf`. It is 10 pages, made with Microsoft Print to PDF on
2026-11-08 at 23:40, and is the XG5000 **variable/comment table** of the program backed up from the
machine's PLC. It holds 560 devices, each with a type, an English comment, and the XG5000 "in use"
checkbox (사용 유무).

The file has no ladder, no I/O parameters and no Cnet settings. Because of that, the PLC's CH2
Modbus areas are still unknown. The "in use" flags were read from the boxes on the rendered pages.
Ticked boxes carry about twice the ink of empty ones, so the 491 used and 69 unused devices separate
cleanly; page 1 was checked by eye.

## What it settles

* **Program:** the program in the PLC is the one with the PC word interface (PRINTING PROGRAM OLD /
  PLC_INFAC, `docs/plc_pc_interface.md`). Its registers are D04000–D04020 "Transmission Data",
  D04100–D04102 "Receive BIT Data", D04200–D04213 "Receive WORD Data", M0700–M0702 "From PC BIT
  Receive", M0750–M0763 "From PC WORD Receive", M0800–M0802 "To PC BIT Transmit" and M0850. The
  app's mode `pc_interface` is the right one for this machine.
* **I/O names:** they match the wiring seen on the machine (2026-11-09 scan): start = P0000E
  "Measurement Start Switch", emergency stops P0000F "Top" and P0000D "Bottom", servo-ON outputs
  P00030 / P00034.
* **PC jog exists and is used:** M07014–M07017 (SH) and M07018–M0701B (SL), each "Jog Left High,
  Left Low, Right Low, Right High". The app now jogs from the Manual view (hold-to-jog).
* **No PC stop:** M0701C / M0701D "Operation Stop" are listed but not used.
* **No link watchdog:** M00010 "PC Communication Check" is not used, so the PLC does not notice a
  lost PC link.

## Changes made in the app

| Area | Change |
|---|---|
| `plc/pc_profile.py` | jog commands M07014–M0701B; status names from the comment table (product detection SL / SH, measurement start switch, emergency stop top / bottom); stop / watchdog facts in `UNRESOLVED` |
| `plc/pc_flow.py` | hold-to-jog: `("jog", axis, op)` repeated while held, `None` to release; dead-man `timeouts.jog_hold` (0.6 s); released on STOP, on closing, and on reconnection after a lost link; refused without the write gate, servo, alarm-normal, with an axis error or at the soft limit |
| Manual view | jog buttons ⏪ ◀ ▶ ⏩ = left high, left low, right low, right high; press to jog, release to stop |
| Lamps | SELECT Forward / Backward were swapped: Forward = P00007 "SL Extension SOL End", Backward = P00006 "Home" (SHIFT already P00001 / P00000); Machine lamps renamed: SH / SL servo out (P00030 / P00034), plus NG box P0001A |

## Device names (selection)

| Device | Comment | Used |
|---|---|---|
| M07000 / M07001 / M07002 | From PC – SH Extension SOL / Eject SOL 1 / Eject SOL 2 Output | yes |
| M07003 / M07004 / M07005 | From PC – SL Extension SOL / Eject SOL 1 / Eject SOL 2 Output | yes |
| M07006 | From PC – PC Buzzer Output | yes |
| M07007 / M0700B | From PC – SH / SL Servo ON Output | yes |
| M07008 / M0701E / M0701F | From PC – Indicator Lamp Green / Red / Yellow | yes |
| M07009 / M0700D | From PC – SH / SL Home Position Movement Output | yes |
| M0700A / M0700E | From PC – SH / SL Alarm Reset Output | yes |
| M0700C | From PC – SL Torque Limit Output | yes |
| M0700F | From PC – Manual Operation | yes |
| M07010 / M07011 | From PC – SH Compression / Tension Execute | yes |
| M07012 / M07013 | From PC – SL Compression / Tension Execute | yes |
| M07014–M07017 | From PC – SH Jog Left High / Left Low / Right Low / Right High | yes |
| M07018–M0701B | From PC – SL Jog Left High / Left Low / Right Low / Right High | yes |
| M0701C / M0701D | From PC – SH / SL Operation Stop | **no** |
| M07020 / M07021 | From PC – SH / SL Clamp SOL | yes |
| M07028 / M07029 | From PC – SH / SL Measurement Position Movement | yes |
| M08000–M08005 | To PC – SH Extension SOL Home / End, Clamp OFF / ON, Eject SOL 2 Home / End | yes |
| M08006–M08009 | To PC – SL Extension SOL Home / End, Clamp OFF / ON | yes |
| M0800A / M0800B | To PC – SL / SH Product Detection | yes |
| M0800C / M0800D / M08026 | To PC – Measurement Start Switch / Emergency Stop Switch / Emergency Stop Bottom | yes |
| M0800E–M08011 | To PC – SH Servo ON Status / Position Confirmation / Zero Point / No Alarm | yes |
| M08012–M08015 | To PC – SL the same | yes |
| M08016–M08019 | To PC – SH Tension / Compression, SL Tension / Compression In Progress | yes |
| M0801A / M0801B | To PC – SH / SL Movement Enable | yes |
| M0801C–M0801F | To PC – SH Left / Right Limit, SL Left / Right Limit | yes |
| M08020 / M08021 | To PC – SH / SL PLC Alarm | yes |
| M08022–M08025 | To PC – SH Tension / Compression Position, SL Tension / Compression Position | yes |
| M0802E / M0802F | To PC – SH / SL Measurement Position Movement Complete | yes |
| M00051 / M00052 / M00061 / M00062 | SH / SL PC Tension / Compression Movement Complete | yes |
| M00105 / M00205 | SH / SL Touch Operation Selection | yes |
| P00000–P00005 | SH Extension SOL Home / End, Clamp OFF / ON, Eject SOL 2 Home / End | yes |
| P00006–P00009 | SL Extension SOL Home / End, Clamp OFF / ON | yes |
| P0000A / P0000B | SL / SH Product Detection | yes |
| P0000D / P0000E / P0000F | Emergency Stop Bottom / Measurement Start Switch / Emergency Stop Top | yes |
| P00010–P00013 | SH Servo ON Status / Position Confirmation Complete / Zero Point Confirmation Complete / No Alarm | yes |
| P00014–P00017 | SL the same | yes |
| P0001A | NG BOX | yes |
| P00024–P00029 | SH Extension / Eject 1 / Eject 2 SOL, SL Extension / Eject 1 / Eject 2 SOL Output | yes |
| P0002A–P0002D | Buzzer, Indicator Lamp Red / Yellow / Green | yes |
| P0002E / P0002F | SH / SL Clamp SOL | yes |
| P00030–P00033 | SH Servo ON / Torque Limit / Home Position Movement / Alarm Reset Output | yes |
| P00034–P00037 | SL the same | yes |
| D02014 / D02016, D02114 / D02116 | SH / SL Left / Right Limit | yes |
| D02022–D02034, D02122–D02134 | SH / SL-PC Jog Low / High, Operation Speed, Tension Position, Left / Right Limit, Start Position | yes |
| D03002–D03016, D03102–D03116 | SH / SL storage copies of those parameters | yes |
| M0030 / M0031 | SH / SL PLC Error Code | yes |

## Not used (69)

M00010 "PC Communication Check"; M01000–M01004 and M01100–M01104 (SOL / servo / torque output
copies); M0701C, M0701D (Operation Stop); M07022–M07027, M0702A–M0702F, M08027–M0802D (spare PC
bits); P00020–P00023 (motor pulse / direction outputs, driven by the positioning function, not as
devices); T0000, T0051; D00514–D00518, D00564–D00568, D01001, D01101; D04103–D04119 and
D04214–D04219 (the unused ends of the receive blocks).

## Still open

The backup does not show the PLC's Cnet / Modbus settings. On the machine, the PC still cannot read
D04000 (exception 02), so the CH2 word areas still have to be set to D00000 on site
(`docs/site_visit_checklist.md`).
