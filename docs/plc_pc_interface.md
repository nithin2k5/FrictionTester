# PLC profile: PRINTING PROGRAM OLD (PC word interface)

Source: XG5000 printout `plc.pdf`, file `PRINTING PROGRAM OLD.xgwx`, 33 pages, printed 2026-11-06.
This is **this machine's** program. The earlier "NEW NLF MC KOREA" ladder (M00029 start, D05010 step,
M0000C/M0000D verdict; `docs/plc_shared_cycle.md`) is a different program and is not used here.

Code: `friction_tester/plc/pc_profile.py` (address map), `pc_link.py` (reads, single command-word
owner, parameter transfer), `axis_ops.py` (handshakes), `pc_flow.py` (controller, AT/MT flow,
evaluation), `pc_measure.py` (acquisition), `event_log.py`. Mode: `test.integration.mode = "pc_interface"`;
settings: `test.machine_profile` (version 1).

SH = SHIFT cable (X axis), SL = SELECT cable (Y axis). Bit n is zero-based (D04101 bit 3 = M07013).

## Interface (pages 29–32, all every scan)

| Direction | Words | Ladder |
|---|---|---|
| PC → PLC commands | D04100, D04101, D04102 | `MOV D0410x M070x` (p29) |
| Command echo | D04000, D04001, D04002 | `MOV M070x D0400x` (p30) |
| Status | D04003, D04004, D04005 | `MOV M080x D0400x+3` (p31) |
| Parameters | D04200–D04213 | `MOV D042xx M07yy` (p30), then `MOV M07yy D020xx` + `MOV 0` upper word (p25–27) |
| Parameter echo | D04007–D04020 | `MOV M0750..M0763 D04007..D04020` (p32) |
| (unused) | D04006 ← M0850 | p31, purpose not stated |

The app reads D04000–D04020 in one request and writes only D04100–D04102 and D04200–D04213.

## Motion commands

| Action | SHIFT | SELECT | Sequence word | Start condition (as printed) |
|---|---|---|---|---|
| Home | D04100.9 / M07009 | D04100.13 / M0700D | M0020 / M0210 | request AND word = 0 AND M00120 AND NOT axis error AND NOT busy (p3–4) |
| Measurement start | D04102.8 / M07028 | D04102.9 / M07029 | M0204 / M0214 | request AND word = 0 AND M00120 AND M00122 (p14–16) |
| PULL | D04101.1 / M07011 | D04101.3 / M07013 | M0201 / M0211 | (local M0010A OR M00125 OR request) AND word = 0 AND M00120 AND M00122 (p5–8) |
| PUSH | D04101.0 / M07010 | D04101.2 / M07012 | M0202 / M0212 | (local M0010B OR M00126 OR request) AND word = 0 AND M00120 AND M00122 (p8–11) |

M00120 = servo ON AND alarm-normal (p3). M00122 = home, PULL and PUSH words all 0 AND M00121
(no axis error, origin determined, not busy, origin complete).

### Sequences and the handshake the app uses

**PULL / PUSH** (steps M0201x, BSFT): 0 → 1 (reset M00051) → 2 (0.2 s, DMOV target → D01000)
→ 3 (1.0 s) → 4 → 5 (NOT busy AND CurPos = target, **no timeout**) → 6 (0.1 s, **SET M00051**)
→ 7 (**waits until every PULL request is released**) → 9 (word = 0, reset M00051). PULL target
D02004, PUSH target D02018 (= measurement start).

App: precheck (servo, alarm-normal, no error, no active sequence, permitted, **target bit OFF**,
completion bit OFF) → set the request → echo seen → "active" (M08016…) ON → completion → release
→ "active" OFF. Completion = M00051/52/61/62 when the M-area mapping is verified and enabled;
otherwise target bit + positioning complete while active **and** the request held ≥ 1.5 s after
acceptance (step timers 0.2 + 1.0 + 0.1 + 0.1 s), because the axis can arrive during step 3.

**Home** (M0020x): 0 (0.1 s) → 1 (0.1 s) → 2 (0.5 s, home output P00032 / P00036) → 3 (origin
complete P00012) → 5 (1.0 s, origin determined, PRS D02000) → 8 (word = 0). Because step 0 is set
by request AND word = 0, a request still ON at the end would home again. App: request until
"motion permitted" (M0801A/B) drops, release at once, done on permitted ON + origin complete.

**Measurement start** (M0204x): step 5 advances on CurPos = D02018 **or after T0103 = 5 s**; step 7
waits for release. The word is not exported and not part of M00122. App: request, done when the
measurement-start bit (M0802E/F) and positioning complete are ON after ≥ 1.0 s, release, settle 0.5 s;
fails if the position is not reached in 8 s.

Disconnect during any operation: the operation fails as not confirmed; its request is released
once the link is back and is **never re-sent**. Request bits found ON at connection (earlier session
or another writer) are reported and block testing until STOP releases them.

## Status (D04003–D04005)

| Signal | SHIFT | SELECT | From |
|---|---|---|---|
| Servo ON | D04003.14 / M0800E | D04004.2 / M08012 | P00010 / P00014 |
| Positioning complete | D04003.15 / M0800F | D04004.3 / M08013 | P00011 / P00015 |
| Origin complete | D04004.0 / M08010 | D04004.4 / M08014 | P00012 / P00016 |
| Alarm-normal | D04004.1 / M08011 | D04004.5 / M08015 | P00013 / P00017 (NO contact in M00120 → ON = no alarm) |
| PULL active | D04004.6 / M08016 | D04004.8 / M08018 | M00127 = M0201 ≠ 0 / M00227 |
| PUSH active | D04004.7 / M08017 | D04004.9 / M08019 | M00128 = M0202 ≠ 0 / M00228 |
| Motion permitted | D04004.10 / M0801A | D04004.11 / M0801B | M00120 AND M00122 |
| Soft limits | D04004.12, .13 | D04004.14, .15 | M00123/124 (CurPos ≥ D02014 / ≤ D02016) |
| PLC axis error | D04005.0 / M08020 | D04005.1 / M08021 | K04201 / K04301 |
| At PULL target | D04005.2 / M08022 | D04005.4 / M08024 | CurPos = D02004 / D02104 |
| At PUSH / start target | D04005.3 / M08023 | D04005.5 / M08025 | CurPos = D02018 / D02118 |
| At measurement start | D04005.14 / M0802E | D04005.15 / M0802F | CurPos = D02018 / D02118 (same compare) |
| Clamp OFF / ON | D04003.2 / .3 | D04003.8 / .9 | P00002/3, P00008/9 |
| "Product" (raw) | D04003.11 / M0800B | D04003.10 / M0800A | NOT M00025 / NOT M00026 — see unresolved |
| Start switch, E-stops | D04003.12 (P0000E), D04003.13 (P0000F upper), D04005.6 (P0000D lower) | | |

## Parameters (D04200–D04213)

| Word | SHIFT → | Word | SELECT → | Scaling (p20–22) |
|---|---|---|---|---|
| D04200 | D02022 low jog speed | D04206 | D02122 | ×100 → K0481 / K0521 |
| D04201 | D02024 high jog speed | D04207 | D02124 | ×100 → K0479 / K0519 |
| D04202 | D02026 operating speed | D04208 | D02126 | ×100 → D02002 / D02102 → D01002 / D01102 |
| D04203 | D02028 PULL travel | D04209 | D02128 | target = start + ×100 + D00490 |
| D04204 | D02030 left limit | D04210 | D02130 | D02000 + ×100 → D02014 |
| D04205 | D02032 right limit | D04211 | D02132 | D02000 − ×100 → D02016 |
| D04212 | D02034 measurement start | D04213 | D02134 | D02000 − **MUL** ×10 → D02018 (16-bit signed: 0..32767) |

D02000 / D02100 = 1 000 000 (origin value, p1; PRS after homing). D00490 = 100 while M0700F is OFF,
0 while ON (p20). The transfer runs every scan, so a new value changes the PLC's targets at once:
the app writes parameters only while both axes are idle and allows a test only after the full set
reads back equal in D04007–D04020. A failed write, a mismatch or a disconnect leaves the model
"Parameters not confirmed". Values: whole numbers 0..65535 (measurement start 0..32767). Stored per
model in `TBL_MODEL_MASTER` columns `MM_<SELECT|SHIFT>_PLC_*` (Model Settings → PLC Parameters).

## Unresolved — needs the machine / original project

1. **Modbus mapping** of the XGB Cnet port for the D area (and the M area for M00051/52/61/62):
   table, offset, word order. Default = holding register n, unverified; no writes until verified.
2. **Command-word ownership**: confirm no other program writes D04100–D04102.
3. **M0800A / M0800B** ("product detection" in the brief) come from NOT M00026 / NOT M00025; on p23
   M00025 = NOT P0000A AND P00019 and M00026 = NOT P0000B AND P00018 (P00018/19 unlabelled), and the
   same M00025/M00026 are SELECT-cycle PULL/PUSH requests (p7, p10, p14). If P00019 is ON with P0000A
   OFF, SELECT PULL could start on its own and could not pass its release step. Shown raw only.
4. **No PLC timeout** for home / PULL / PUSH (T0100–T0102 are unused); the app times out but cannot
   stop an axis. Measurement start ends after 5 s even off position.
5. **Units**: counts per mm and speed units of the positioning module (×100 / ×10 scaling only).
6. **M0700F** (D04100.15): changes the PULL target by 100 counts and the servo-ON / P00031 logic. Not written.
7. **M07008** drives lamp P0002D (p29) and, with M0700F, P00031 (p27); M0700A / M0700E look like alarm
   reset (P00033 / P00037). No button is assigned.
8. **Servo ON** from the PC (M07007 / M0700B with M0700F) — not used.
9. **No overall stop command** in this program: STOP releases the app's requests; a stroke that has
   started finishes by itself. Use the machine's emergency stop to stop motion.
10. **Printing P00038** is triggered by M07000→M07002 and M07003→M07004 (p32–33), so the fixture
    solenoid bits are not written by the app.
11. **Fixture**: clamp polarity and sequence; the SHIFT clamp output P0002E is gated by NOT M00205
    (the SELECT touch-select bit) as printed. No automatic clamping.
12. **Measurement**: loadcell ↔ cable (default SELECT = L1, SHIFT = L2), stroke/LVDT source, calibration,
    zero, sign, and whether PULL1–3 / PUSH1–3 are positions or repeated strokes (`measurement.judge`).
13. **Model fields with no PLC address**: delay time, positions 1–3, LOAD min/max (app judgement only).
14. Ladder quirks as printed: SH PUSH release step checks M0010A (local PULL), not M0010B (p9); the SH
    repeat cycle waits on M00061 (an SL bit, p12).

## Commissioning

| Item | Setting (`test.machine_profile`) |
|---|---|
| Verify D-area mapping (read D04003–D04005 in the app vs. XG5000 monitor; write one parameter word and read its echo) | `mapping.areas.D`, then `mapping.verified: true` |
| Allow PC writes | `live_writes_enabled: true` |
| Only this app writes D04100–D04102 | `command_owner_confirmed: true` |
| M-area mapping verified (M00051 etc.) | `mapping.areas.M`, `internal_bits_enabled: true` |
| Loadcell / DAQ per cable, LVDT, judgement | `measurement.*` |
| Timeouts (s) | `timeouts.*` |

Every request, echo, state change, timeout, parameter transfer and saved part is logged with a
timestamp in `<Saved to>/plc_events_YYYYMMDD.log`.

## What was verified

In simulation (`tests/pc_ladder_sim.py`, a scan-level model of the sequences above, and
`tests/test_pc_interface.py`): parameter readback and target scaling; validation; failed write and
disconnect during transfer; writes blocked without verification/ownership; AT and MT parts;
both axes sharing D04101 without losing bits; requests held through step 6 and released at step 7;
home released before its sequence ends (homes once); internal completion bits; skip-scan AT; stale
position / completion bits refused; stale request bits reported, not re-sent, released by STOP;
disconnect during a stroke (part incomplete, request released after reconnect, not re-sent); STOP
followed by the PLC finishing the stroke (ignored); missing force samples (NG); one axis failing
while the other finishes (part incomplete); measurement start giving up after 5 s; exactly one
result per part. The Test Console was also driven end-to-end against the simulator.

**Not verified** (needs the real machine): every item in the unresolved list, the real Modbus
addressing and timing, the loadcell/LVDT readings during strokes, and the fixture.
