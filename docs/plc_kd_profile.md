# PLC profile: plc kd.pdf (client's updated reference, NLF X axis)

Source: `plc kd.pdf`, 12 pages printed 2026-11-07, footer **NEW NLF MC KOREA MODIFY 09_06_25.xgwx**,
supplied by the client as the updated reference. It is a client file, not an XG5000 read-back:
**that it is the program installed on the PLC is not confirmed.** Addresses are native XG5000
addresses; step numbers are the printout's left margin. Korean comments are damaged by the
character encoding, so meanings come from the English comments and the logic.

Mode `test.integration.mode = "kd"` (settings version 5 switches `pc_interface` to `kd`). Code:
`friction_tester/plc/kd_profile.py` (map), `kd_link.py` (reads, the only write path), `kd_flow.py`
(ORIGIN / moves), Test Console `_start_kd`. Tests: `tests/test_kd.py` against `tests/kd_sim.py`
(simulation only). The other modes (`pc_interface`, `shared_cycle`, `independent`) are unchanged.

## Program structure

| Pages / steps | Function |
|---|---|
| p1, 1-60 | Analog module (slot 1) -> SELECT load D00030 (+100 -> D00040), SHIFT load D00130 (-> D00140); scaling D00010 / D00110 |
| p2, 63 | `SRD` slot 2 axis 0 -> D00500-D00510 (X-axis status), copies to D00600.. |
| p3, 167 | Position value limits: D00735/D00745/D00755 +-20 -> D00800..D00825, +980/+1020 -> D01400.. |
| p3-4, 209-236 | Origin setting: homing state machine D05000 driven by **M00050** |
| p4, 242-276 | D00250.. scaling; jog M00020 (CCW) / M00022 (CW), speed M00021 / M00023 |
| p5, 283-327 | `DST` slot 2 axis 0 on M00030..M00034 |
| p6-10, 338-553 | Load values D00900.. -> D01200.., absolute values D01500.., results D01600-D01619, D01300.. (+100) |
| p10-12, 579-845 | Recipe: D00080 = 1..20, M00062 load D02000+100(n-1) -> D00700 (99 words), M00063 save |
| p12, 860 | Servo ON P00020 = always ON |

One positioning axis (X). The **automatic test cycle runs in the PLC**: it is the companion block
`plc Auto.pdf` of the same project (see below). kd.pdf is the manual / support side.

## Automatic cycle (plc Auto.pdf) - followed by the shared-cycle code

`plc Auto.pdf` (12 pages, same footer) is the cycle the app's `shared_cycle` code was traced from
(`docs/plc_shared_cycle.md`, PAGE 1.pdf = kd.pdf + Auto.pdf). In mode `kd` the Test Console runs that
code on the Cnet mapping: RUN pulses **M00029** (software start; physical start P00002), the step word
**D05010** is followed (0 -> 10 -> [20/30 ageing if M02000] -> 40 forward stroke sampling positions
1-3 -> 41/42 -> 50 return stroke -> 51/52 repeat until D05110 = D00781 -> 60 to 0 -> 70 judge ->
80 verdict), the verdict bits M0000C (NG) / M0000D (OK) and the OK/NG counters D00150/D00152 are
the completion evidence, finals D01250 / D01253 against D00782..D00788. A stop/interlock (P00000,
P00004, P00008, P0000A, P0000B, D00500.4) sends the step to 0 without a verdict. P0000A / P0000B are
labelled "Driver Homing" / "Driver Alarm" in this printout but "part presence" in the I/O list -
unresolved. Settings for the cycle (cable per channel, direction roles, scaling, start timeout) are in
`test.integration.shared`; the start write uses the same gate as ORIGIN.

## Status D00500-D00510

| Bit | Meaning | Bit | Meaning |
|---|---|---|---|
| D00500.0 | operating (busy) | D00500.8 / .9 | upper / lower limit detection |
| D00500.1 | error | D00500.A | emergency stop |
| D00500.2 | positioning complete | D00500.B | forward / reverse |
| D00500.4 | homing status (origin determined) | D00500.C-.F | accel / constant / decel / dwell |
| D00500.6 | stop status | D00501.5 | homing operation active |
| D00503.4 / .5 | external upper / lower limit | D00503.6 / .7 | home / near-home signal |
| D00503.C | in-position | D00504/D00505 | current position (DINT) |
| D00508 | step number | D00510 | error code |

## ORIGIN (homing) - implemented, simulated

PLC [steps 212-236]: M00050 AND D05000 = 0 -> D05000 := 10; 10 -> T0000 2 s (driver reset P00003)
-> 20; 20 -> homing instruction; with homing status D00500.4 -> D05000 := 0 and **the PLC resets
M00050**. (A T0030 / P00003 branch at step 212 and the compare at step 228 are only partly legible -
verify in XG5000.)

App (`HomeOp`): refuses unless the status is fresh, axis idle, no error, D05000 = 0 and M00050 reads
back OFF. Sets M00050 (FC05). Accepted only when D05000 leaves 0. Done when D05000 is back to 0, the
PLC has cleared M00050 and D00500.4 is ON. If D05000 never leaves 0 the app clears M00050 itself
(no late start). After acceptance the app never touches M00050; STOP cannot stop homing (no PC stop
in this program). A lost connection fails the operation; nothing is re-sent; a request that was
never accepted is cleared when the link returns, only while D05000 = 0. Failures latch a fault until
STOP acknowledges it. Every ORIGIN button homes the single X axis.

## Moves - implemented, simulated, no UI yet

M00030 position 1 (D00735), M00031 position 2 (D00745), M00032 position 3 (D00755), M00033 total
distance (D00730), M00034 home position (D00740); speed D00750. Ladder: NOT busy and the
emergency interlocks P00000 / P00004 -> DST. The PLC never resets M0003x. App (`MoveOp`): requires
homing; sets the bit, releases it once the axis is busy, done on busy OFF + positioning complete.

## Not driven by the app

Jog (needs a held request), recipe load/save (numbering vs the model list unknown), servo (always
ON), alarm reset (none from the PC; drive reset P00003). D00900-D00950 are written by the Auto
cycle's sampling windows.

## Communication requirements

* Status: a word area based at D (FC04 word read or FC03 word write) covering D00500-D00510,
  D05000, D00030/D00130.
* ORIGIN / cycle start / moves: coil writes (FC05) to M00050 / M00029 / M00030-M00034 -> the
  **bit-write area must be based at M00000** (M00050 = coil 80, M00029 = coil 41).
* Cycle: D05010, D05110, D00150-D00154, D00781, D00900.., D01250.. through a D-based word area. With any other base the mapping refuses the write - it is never
  sent to a P output.
* Writes need `machine_profile.mapping.verified` (after the XG5000 read-back),
  `kd_profile.live_writes_enabled` and `kd_profile.command_owner_confirmed` (no HMI drives the same
  bits). Check with `python tools/plc_diag.py` (default profile kd, read-only).

## Unresolved

See `kd_profile.UNRESOLVED`: installed program, Cnet areas of the PC's channel, homing contacts at
steps 212/228, DST edge behaviour, P00000/P00004 polarity, load scaling and the +100 values, the
writer of D00900-D00950, the AT/MT test sequence, recipe numbering.
