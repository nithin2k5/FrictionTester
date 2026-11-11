# PLC profile: PRINTING PROGRAM OLD (PC word interface)

Source: the supplied 33-page printout headed **PLC_INFAC**, footer **PRINTING PROGRAM OLD.xgwx**
(`plc.pdf`, printed 2026-11-06). **Confirm this is the program installed on the machine before
commissioning.** Its addresses are not mixed with the older "NEW NLF MC KOREA" / PAGE 1.pdf program
(`docs/plc_shared_cycle.md`, mode `shared_cycle`).

Addresses below are native XG5000 device addresses — not connector pins and not verified Modbus
offsets. Bit numbers are zero-based (M0700A = bit 10 of M0700). Several Korean comments in the
printout are damaged by character encoding; meanings below come from the logic, and uncertain
comments are marked as such. SH = SHIFT axis (X), SL = SELECT axis (Y).

Code: `friction_tester/plc/pc_profile.py` (map), `pc_link.py` (transport, single command-word owner,
parameters), `axis_ops.py` (handshakes), `pc_flow.py` (cable routines, AT/MT coordinator, evaluation,
faults), `pc_measure.py` (acquisition), `event_log.py`, `friction_tester/result_store.py` (saving).
Mode `test.integration.mode = "pc_interface"`; settings `test.machine_profile` (profile version 2,
settings version 4). Example: [settings.example.json](settings.example.json).

## Status legend

| Tag | Meaning |
|---|---|
| **Implemented** | in the application code |
| **Simulated** | exercised against `tests/pc_ladder_sim.py` by `tests/test_pc_interface.py` |
| **Hardware-verified** | checked on the machine — **nothing is in this state yet** |
| **Unresolved** | needs a machine/wiring/project fact; kept configurable and off by default |

## 1. Transport (pages 29–32) — implemented, simulated

| PC writes | PLC word | Echo | Page |
|---|---|---|---|
| D04100 | M0700 | D04000 | 29 (`MOV D04100 M0700`), 30 (`MOV M0700 D04000`) |
| D04101 | M0701 | D04001 | 29, 30 |
| D04102 | M0702 | D04002 | 29, 30 |

Status: M0800 → D04003, M0801 → D04004, M0802 → D04005 (p31). M0850 → D04006 (p31): **no measurement
meaning established — not used.** The app reads D04000–D04020 in one request.

How these native addresses become Modbus requests is set by the PLC's Cnet Modbus areas, modelled in
`plc/modbus_map.py` (`CnetAreaMap`, settings `machine_profile.mapping.cnet_areas`). The defaults are
the *candidate* PLC_INFAC layout (bit read P00000, bit write M00000, word read/write D00000), not the
machine's confirmed parameters. On the machine (2026-11-07) FC03 offset 4000 x21 returned exception 02,
so the live channel does not expose D04000 with its current settings. Diagnosis and the XG5000
procedure: [xg5000_modbus_verification.md](xg5000_modbus_verification.md).

`PcLink` is the only writer of D04100–D04102: one lock, masked bit updates, only the changed word is
written, so SHIFT and SELECT requests in a shared word never overwrite each other. On (re)connection
the current words are adopted; request bits found ON are reported, never re-sent, and released only
by the operator (STOP). Nothing writes zero to all words at start or stop. Writes require
`mapping.verified`, `live_writes_enabled` and `command_owner_confirmed`. An echo proves only the
copy; acceptance and completion are checked separately.

## 2. Commands

| Function | SHIFT | SELECT | Pages |
|---|---|---|---|
| Home | D04100.9 / M07009 | D04100.13 / M0700D | 3–5 |
| Measurement start | D04102.8 / M07028 | D04102.9 / M07029 | 14–16 |
| PULL (tension) | D04101.1 / M07011 | D04101.3 / M07013 | 5–8 |
| PUSH (compression) | D04101.0 / M07010 | D04101.2 / M07012 | 8–11 |
| Alarm reset | D04100.10 / M0700A → P00033 | D04100.14 / M0700E → P00037 | 27–28 |
| Jog left high / left low | D04101.4 / .5 (M07014 / M07015) | D04101.8 / .9 (M07018 / M07019) | 17–19 |
| Jog right low / right high | D04101.6 / .7 (M07016 / M07017) | D04101.10 / .11 (M0701A / M0701B) | 17–19 |

**Jog** (names and "in use" from the PLC backup of 2026-11-08, logic from p17–19): the jog start
K04291 / K04292 (SL K04391 / K04392) is ON while a jog request (touch M0010x or PC M0701x) is held
AND servo-ON status P00010 / P00014 AND no-alarm P00013 / P00017 AND NOT axis error K04201 / K04301
AND NOT the opposite request AND NOT the soft limit of that direction (M00123 / M00124, SL M00223 /
M00224). The "high" bits select high jog speed (K04293). No M0700F condition. The PLC does **not**
watch the PC link (M00010 "PC Communication Check" is unused), so a jog bit left ON keeps the axis
moving until a soft limit. App (Manual view, `pc_flow._cmd_jog`): hold-to-jog; the button repeats the
request every 0.2 s, release / leaving the button stops it, the controller drops the bit after
`timeouts.jog_hold` (0.6 s) without a repeat, on STOP and on closing; after a lost link the bit is
released when the link returns. Refused (nothing written) without the write gate, with a part or
operation active, a fault latched, stale status, servo OFF, a drive alarm, an axis error or the soft
limit of that direction reached. — implemented, simulated.

**No PC stop:** M0701C / M0701D "SH / SL Operation Stop" are named in the comment table but not used.

Alarm reset (p27–28): `P00033 = (M00105 AND M0010D) OR (NOT M00105 AND M0700A)`, `P00037 = (M00205 AND
M0020D) OR (NOT M00205 AND M0700E)` — the PC branch works only while the local/PC selector M00105 /
M00205 is OFF. App: one pulse of `timeouts.alarm_reset_pulse` seconds per operator press (**None by
default → disabled until the drive requirement is known**); refused if the selector is read ON or the
alarm-normal input is already ON; if alarm-normal does not return it is reported and **not retried**.
— implemented, simulated.

**M07008 is not alarm reset**: it drives SHIFT torque-limit P00031 (with M0700F, p27) and green lamp
P0002D (p29). M0700C drives SELECT torque-limit P00035 (with M0700F, p28). M0700F selects the manual/
automatic PC branches (servo enable via M07007 / M0700B only in the manual branch; with M0700F OFF the
automatic branch keeps servo enable asserted; D00490 = 100 while OFF). None of these is written.

### Handshakes (axis_ops.py)

**PULL / PUSH** — sequence word M0201 / M0202 (SL M0211 / M0212), steps as BSFT (p5–11): 0 SET by
request AND word = 0 AND M00120 AND M00122 · 1 reset completion · 2 DMOV target → D01000 (T 2) ·
3 (T 10) · 5 NOT busy AND CurPos = target (**no timeout**) · 6 **SET M00051 / M00052** (SL M00061 /
M00062) · 7 **waits for the request OFF** · 9 word = 0, completion reset. Exported active M08016 /
M08017 (SL M08018 / M08019) stays ON through step 7.

App: precheck (fresh data, servo, alarm-normal, no error, no active sequence, permission ON, no
stale completion flag, target compare OFF) → arm acquisition → request → acceptance = echo AND active
ON AND permission OFF (fresh sequence) → completion = the flag of this execution (window closes here)
→ clear the request → wait active OFF, flag OFF, permission ON before the next stroke.
Completion source `stroke_completion`:
* `internal_flags` (default) needs **native M reads** (`internal_bits_enabled`); if they are not
  readable the routine stops with "completion flag not readable" — no status bit is substituted.
* `position_fallback` (opt-in, validate on the machine first): target compare + in-position while
  active, request held ≥ `stroke_min_hold` (ladder step timers; timer units unverified).

**Home** — word **M0200** / M0210 (p3–5): 0 → 1 → 2 (home output P00032 / P00036) → 3 (origin P00012 /
P00016) → 5 (origin determined, PRS D02000 / D02100 = 1 000 000 — home is **not** coordinate 0) → 8
word = 0. Step 0 is SET by request AND word = 0, so a held request re-homes after the reset.
App: request until permission (M0801A / M0801B, which requires M0200 = 0) drops → release at once →
done only when permission is restored, origin feedback ON and no axis error. Origin feedback alone
(arrives at step 3) is not completion. — implemented, simulated (origin feedback before the end).
The home rung needs M00120 but **not M00122**, and M00122 includes origin feedback, so an axis never
homed since power-up shows permission OFF. The app then does not require permission: it holds the
request until origin feedback turns ON (step 3, sequence already past step 0) and releases it there;
completion is unchanged. Permission OFF with origin feedback ON (module busy / a sequence running) is
refused. — simulated (`test_home_from_unhomed_power_up`).
**Home on open** (`home_on_open`, on by default): when the Test Console opens, both axes are homed
once on the first good status read, through the same checks as the ORIGIN button. Not repeated after
a reconnect; skipped with a message when writes are off or PC request bits are found ON. — simulated.

**Measurement start** — word M0204 / M0214, targets D02018 / D02118 (p14–16): step 5 advances on
`CurPos = target` **OR T0103 / T0203**; step 7 waits for release. The word is not exported and not in
M00122 (permission does not drop). With native reads of the step bits M02040–M02049 / M02140–M02149:
acceptance = chain active; at step 7 the start compare (M0802E / M0802F) and in-position must be ON,
otherwise **the timeout branch was taken → failure**; then release and wait for the chain to reset.
Without them: request only if the compare is OFF; accept a fresh OFF→ON + in-position after
`meas_dwell`; an axis already on target is recorded as such and no motion is requested.
M08023 = M0802E and M08025 = M0802F are the **same compares** and never count as two pieces of evidence.
— implemented, simulated (timeout branch without arrival).

Per axis, one operation at a time. A lost connection fails the operation as not confirmed; its
request is released after reconnection and never replayed.

## 3. Status (pages 24–25) — implemented (display + handshakes)

| Meaning | SHIFT | SELECT |
|---|---|---|
| Servo ON feedback | D04003.14 / M0800E / P00010 | D04004.2 / M08012 / P00014 |
| Drive in-position | D04003.15 / M0800F / P00011 | D04004.3 / M08013 / P00015 |
| Drive origin feedback | D04004.0 / M08010 / P00012 | D04004.4 / M08014 / P00016 |
| Drive alarm-normal input (ON = no alarm, permission in M00120) | D04004.1 / M08011 / P00013 | D04004.5 / M08015 / P00017 |
| PULL / PUSH active | D04004.6 / .7 | D04004.8 / .9 |
| Motion permission (M00120 AND M00122) | D04004.10 / M0801A | D04004.11 / M0801B |
| Soft limits M00123/124, M00223/224 | D04004.12 / .13 | D04004.14 / .15 |
| PLC positioning error | D04005.0 / M08020 / K04201 | D04005.1 / M08021 / K04301 |
| At PULL target | D04005.2 / M08022 | D04005.4 / M08024 |
| At PUSH/start target | D04005.3 / M08023 | D04005.5 / M08025 |
| At measurement start (same compare) | D04005.14 / M0802E | D04005.15 / M0802F |
| Clamp OFF / ON | D04003.2 / .3 (P00002/3) | D04003.8 / .9 (P00008/9) |
| Expansion home / end | D04003.0 / .1 (P00000/1) | D04003.6 / .7 (P00006/7) |
| Discharge-2 home / end | D04003.4 / .5 (P00004/5) | — |

Neutral, raw only: D04003.10 / M0800A = NOT M00026, D04003.11 / M0800B = NOT M00025, with M00025 =
NOT P0000A AND P00019, M00026 = NOT P0000B AND P00018 (p23–24) — conditioned, inverted signals whose
comments disagree with the inputs; M00025/M00026 are also SELECT-cycle PULL/PUSH requests (p7, p10,
p14). D04003.12 = P0000E (start switch, conditioned), D04003.13 = P0000F (upper emergency input),
D04005.6 = P0000D (lower emergency input): ON is a permission in the shown logic; shown as raw values,
never as "emergency pressed". K04200/K04300 (busy), K0422/K0432 (positions used in compares),
K04204/K04304 (origin reference) are not read — native access and width unverified; command position
is never presented as measured stroke.

## 4. Parameters (pages 20–27, 30, 32) — implemented, simulated

| Parameter | SHIFT write → PLC | SELECT write → PLC |
|---|---|---|
| Jog low speed | D04200 → D02022 | D04206 → D02122 |
| Jog high speed | D04201 → D02024 | D04207 → D02124 |
| Test operating speed | D04202 → D02026 | D04208 → D02126 |
| PULL travel | D04203 → D02028 | D04209 → D02128 |
| Left limit | D04204 → D02030 | D04210 → D02130 |
| Right limit | D04205 → D02032 | D04211 → D02132 |
| Measurement start | D04212 → D02034 | D04213 → D02134 |

Through M0750–M0763 (p30), upper word cleared (p25–27), echo D04007–D04020 (p32), working copies
D03002…D03016 / D03102…D03116 (p23). Formulas (p20–22): start D02018 = D02000 − D03016 × 10 (16-bit
signed MUL → 0..32767), D02118 likewise; PULL D02004 = D02018 + D03008 × 100 + D00490 (D02104 likewise);
D00490 = 100 when M0700F is OFF, 0 when ON; speeds × 100; limits D02014 = D02000 + L × 100,
D02016 = D02000 − R × 100. These are counts, not mm or mm/s.

Source (`db.plc_params_from_row`, 2026-11-10): the cable's test condition — operating speed = Speed,
high jog = Speed, low jog = Speed / 2, PULL travel = Stroke, left limit = Position 3, right limit =
−Position 1 (positions are signed distances from home), measurement start = Stroke ÷ 2 × 10 — centred
automatically, so the test runs from −Stroke/2 to +Stroke/2 (+1 mm D00490) across home; Position 2 is
not sent. A stored PLC word is used only where the condition field is empty.

App: whole numbers 0..65535 (start 0..32767); derived targets checked (right ≤ start ≤ PULL ≤ left,
using the PLC's own D00490 from the M0700F echo — the app never adds D00490 itself); written only
when both axes are idle and no part is active; confirmed after the full echo matches **and**
`params_settle` has passed (the echo does not prove the derived targets were recalculated). A failed
write, mismatch or disconnect leaves "Parameters not confirmed". Parameters are frozen per part; a
model change during a part is refused in the UI and new parameters are deferred.

## 5. Fixture, mode, printing — not written by the app

| Request | Output | PC branch |
|---|---|---|
| D04100.0 / M07000 | P00024 SH expansion | M00105 OFF |
| D04100.1 / M07001 | P00025 SH discharge 1 | M00105 OFF |
| D04100.2 / M07002 | P00026 SH discharge 2 | M00105 OFF |
| D04100.3 / M07003 | P00027 SL expansion | M00205 OFF |
| D04100.4 / M07004 | P00028 SL discharge 1 | M00205 OFF |
| D04100.5 / M07005 | P00029 SL discharge 2 | M00205 OFF |
| D04102.0 / M07020 | P0002E SH clamp | **M00205** OFF, as drawn (p28) |
| D04102.1 / M07021 | P0002F SL clamp | M00205 OFF |

Printing/NG (p32–33): M07000 → M07002 and M07003 → M07004 sequences feed M00011–M00015 → **P00038**;
M07006 SETs buzzer P0002A; NG box P0001A resets it. Fixture commands therefore affect printing/NG;
P00038 is not a force verdict and is not combined with the app's PRN printing. Fixture order and
sensor confirmations are commissioning items; `fixture_checks` can require status bits (e.g.
`clamp_on`) before a routine; empty = "fixture checks not configured" recorded in each result.

## 6. Application process — implemented, simulated

Cable routine: fixture checks → home if `home_before_test` requires it (`if_not_referenced` by
default: origin feedback OFF) → measurement-start positioning → `strokes_per_test` × (PULL, PUSH)
with acquisition → evaluation. No extra return-to-home and no fixture release are added.

* AT: RUN → SELECT label (SELECT field only) → SELECT routine → SHIFT label → SHIFT routine → one part.
  A SELECT INCOMPLETE ends the part; OK/NG continue to SHIFT.
* MT: RUN → one label (copied to both fields) → both routines together, each tracked on its own →
  one part. **Refused unless `mt_parallel_verified`** (never silently made sequential).
* Refused: wrong field, scans while testing or before RUN, invalid type, RUN while active, model change
  during a part. Label check `test.label_rule`: `contains_part_number` (existing), `regex` per field, or
  `none` (status says no rule is defined).

## 7. Measurement and results — implemented, simulated with deterministic data

`measurement`: force source per cable (serial loadcell number or NI DAQ sensor row), stroke source (DAQ
LVDT row, or none), calibration (scale, offset, unit, verified flag), and the evaluation rules, **all
unset by default**: `judge` (`peak` | `strokes` | `positions`: the load of the last stroke at −(Stroke/2 − 2), 0 and
+(Stroke/2 − 2) on the centred stroke axis of the graph, linear interpolation; the stroke is the LVDT
re-zeroed so the PULL start is −Stroke/2, or speed × time when no LVDT is read; the model's
Position 1–3 are not used for this), `force_sign` (`absolute` | `signed`), `limit_bounds` (`inclusive` | `exclusive`),
`min_samples`. Samples carry timestamp, calibrated and raw force, stroke and raw stroke; a failed read
is recorded as invalid (no substitute sensor). Positions and repetition count are separate settings.

NI card: the Sampler opens the NI DAQ task itself (device, wiring and sensor rows from the COM Port
page) for the rows named by `stroke` / `daq_force`, with a ~10 ms averaging block; load and stroke
come from the same acquisition. If the card cannot be opened the stroke is missing but a serial force
sample still counts (retried each stroke). This machine (settings.json): results use the serial loadcells (`force_source` = `serial`); the
graph uses the NI card (`graph_force` = `daq`) — SELECT `Loadcell 1` (ai0) + `LVDT 1` (ai2), SHIFT
`Loadcell 2` (ai1) + `LVDT 2` (ai3). NI load = (V − offset_v) × scale of the sensor row. Each sample
stores the result tuple and a separate graph point (`Capture.graph`), so a failed serial read still
plots. A failed NI open is retried every 5 s.

Graph (Test Console): each measuring stroke is drawn live from `Capture.graph`, PULL and PUSH on a fresh graph per stroke
number, Load against the LVDT stroke in mm — or against time since the stroke began when no stroke
reading is available. The view grows to fit the data; **Fit** zooms to it.

Verdicts: OK / NG (a value outside the limits) per cable; **INCOMPLETE** for unset rules, missing
limits, too few or invalid samples, uncovered positions; **ERROR** for a failed machine operation.
Part: OK / NG (counted) or INCOMPLETE / ABORTED (saved with reasons, not counted). Records include
labels, model/condition/parameter/rule snapshot, evidence per operation, acquisition metadata, graph
data (≤ 200 points per direction), timestamps.

Saving (`result_store.py`): each finalized part goes to `<Saved to>/unsaved_results.json`, then to
`TBL_TEST_RESULTS` on a worker thread; `insert_test_results` skips a part UID already present, so a
retry after a lost acknowledgement cannot duplicate it. Counters change only on the confirmed save
(once per part). While a part is unsaved, RUN is refused and saving is retried every 15 s.

## 8. STOP, faults, threads — implemented, simulated

No verified stop address or abort procedure exists in this program. STOP cancels the application's
sequencing, invalidates the run token (late events are ignored), clears the app's request bits, ends
the part as ABORTED and shows **"Application sequence cancelled — machine stop NOT confirmed"**.
Clearing a PULL/PUSH request does not abort motion: a started stroke finishes by itself.

Timeouts (acceptance, motion, release, part), disconnects during an operation, drive faults and
failed operations **latch a fault**: RUN is refused until the operator inspects the machine and presses
STOP while the PLC is connected and no sequence is active or in error. A disconnected machine is not
assumed stopped; nothing is replayed after reconnection. Communication and acquisition run off the
Tk thread; UI updates go through `after()` polling of the controller and store queues.

## 9. Unresolved — machine verification needed

1. The installed program is PRINTING PROGRAM OLD.xgwx (the NLF program has no D04000/D04100 interface).
2. PLC model, connected Cnet channel, station, serial settings and the four Cnet Modbus area bases
   (read back in XG5000 - see xg5000_modbus_verification.md); register width, word order.
3. Native M reads: M00051/52/61/62, M00105/M00205, M0204x/M0214x (needed for `internal_flags` and the
   observed measurement-start handshake).
4. Command-word ownership (no other writer of D04100–D04102).
5. Stop behaviour / abort procedure (none in this program).
6. Alarm-reset pulse length and behaviour per fault class.
7. Product-detection meaning of M0800A/M0800B, P00018/P00019 functions, SELECT/SHIFT assignment.
8. Emergency input polarity and wiring (P0000F, P0000D).
9. Timer units (presets 1, 2, 5, 10, 50); PLC sequences without timeouts (home, PULL, PUSH).
10. Engineering units: counts per mm, speed units, force calibration.
11. Fixture order, sensor confirmations, clamp polarity; SHIFT clamp gated by M00205.
12. MT simultaneous motion (clearance, fixtures, drives, acquisition).
13. Force source and stroke/LVDT source per cable, alignment, sign, sampling windows, limits rule, and
    whether PULL1–3/PUSH1–3 are positions or repeated strokes.
14. K04200/K04300, K0422/K0432, K04204/K04304 access; D04006 / M0850 meaning.
15. Ladder quirks as printed: SH PUSH release step checks M0010A (p9); SH repeat cycle waits on M00061 (p12).

## 10. Commissioning settings (`test.machine_profile`)

| Item | Setting |
|---|---|
| Program confirmed, mapping, native reads, stop, sensors, units, fixture, rules, MT | `commissioning.*` = `"verified"` (recorded in the log at start) |
| Cnet Modbus areas read back and checked | `mapping.cnet_areas.*.plc_base` (+ optional `accessible_count`), `mapping.verified`, `mapping.verified_note` |
| Status read function | `mapping.word_read_order` (`word_read` = FC04 first, `word_write` = FC03) |
| Allow writes / sole writer | `live_writes_enabled`, `command_owner_confirmed` |
| Native M reads | `mapping.cnet_areas.bit_write` (FC01), `internal_bits_enabled` |
| Stroke completion | `stroke_completion` |
| MT parallel | `mt_parallel_verified` |
| Homing policy, fixture checks | `home_before_test`, `home_on_open`, `fixture_checks` |
| Measurement | `measurement.*` |
| Timeouts (s) | `timeouts.*` (incl. `alarm_reset_pulse`) |

Log: `<Saved to>/plc_events_YYYYMMDD.log` — requests, echoes, state changes, timeouts, faults,
parameter transfers, finalized and saved parts.
