# D04000 access: evidence, diagnosis and XG5000 verification procedure

Problem: the Test Console (mode `pc_interface`) reports the PLC disconnected and refuses ORIGIN, while
COM Setting reports the PLC connected. Nothing in this document makes the machine ready: readable
registers are necessary for the PC interface, not sufficient for ORIGIN.

## 1. What is established, and how

### From hardware (operator-pasted terminal output, 2026-11-07, PC port COM10)

| Request | Result |
|---|---|
| Serial: COM10, 57600 8N1, station 1 | PLC answers Modbus RTU requests |
| FC03 offset 4000 (0x0FA0) x21, station 1 (the app's D04000–D04020 poll) | exception 02, illegal data address (`function_code=131`) |
| FC03 / FC04 aligned 16-register blocks, 0..65535 (one sweep, now retired) | readable 0..991 and the same window again from 32768; every value 0 |
| FC03 / FC04 single-address probes | FC04 offset 1000 readable, FC03 offset 1000 not; highest readable by binary search 32768+993 (FC03) and 32768+1003 (FC04) |
| FC01 offsets 0..15 | all 0; highest readable by binary search 16223 |
| FC02 offsets 0..15 | 0,3,6,9,10,11,13,15 ON - a pattern that matches live P inputs (P0000F emergency input ON, P00000 ON), **static snapshot only: no change has been observed yet** |

Exception 02 proves the PLC answered and refused that function/address/range. It does not identify
which PLC memory any readable offset belongs to, and readable zeros prove nothing about the mapping.

Interpretation, **not verified**: the window sizes fit Cnet areas that all start inside a 1024-word P
area - bit read from P word 0 (16384 bits), bit write from P word 10 (16224 bits), word read from P
word 20 (1004 words), word write from P word 30 (994 words). That would agree with the earlier archive
finding of a Cnet configuration whose word areas are based in P memory. The binary searches assumed a
contiguous range, so these counts are approximate. Only the XG5000 read-back can confirm it.

### From source documents

| Source | Finding |
|---|---|
| `plc.pdf` - header PLC_INFAC, footer PRINTING PROGRAM OLD.xgwx (33 pages) | Contains the PC word interface: `MOV D04100 M0700` ... (p29), echo `M0700 -> D04000` (p30), status `M0800 -> D04003` (p31), parameters D04200.. (p30). Ladder only: **no CPU type and no Cnet/Modbus parameters** in the printout. |
| `PAGE 1.pdf` - NEW NLF MC KOREA MODIFY 09_06_25.xgwx | I/O parameter page: **XBC-DR32H** CPU, XBF-AD04A, XBF-PD02A, a 16-point DC-in/relay expansion. Text search finds **no D04000/D04100/M0700/M0800**: the NLF program has no PC word interface. |
| `C:\Users\HP\Developer\C#\FrictionTest.rar` - the earlier C# PC software (NModbus) | Reads M bits with FC01, coil = the M digits read as hex (`M029` -> 0x29 = 41 = M00029); reads D with FC04, offset = decimal D number; start bit M00029. That is the NLF interface (`docs/plc_shared_cycle.md`), and it implies bit-write base M00000 and word-read base D00000 *on the channel that software used, when it was used*. It is not evidence of today's settings. |
| `NEW NLF MC KOREA MODIFY 09_06_25(1).rar` | **Not on this PC** - the earlier observations (one Cnet configuration with P-based word areas, another with bit write M0 and both word areas D0) were not re-checked here, and no enum values were decoded. |

### From code (this repository)

* The app's request was FC03, raw offset 4000 decimal (0x0FA0), quantity 21, station 1 - decimal, zero
  based, no one-based display offset. That conversion was correct *for a D00000 word base*.
* Reads and writes share one `XGBPlc` object; every request now goes through one locked `_call`
  (`devices/plc_xgb.py`), so the Test Console poll, the PC-interface controller and COM Setting tools
  cannot interleave on the line. Serial I/O runs on worker threads; Tk updates go through `after()`.
* COM Setting's "connected" only means the port opened and coil 0 answered. It says nothing about
  D04000.

## 1a. Update: the client's reference program is plc kd.pdf

The client named `plc kd.pdf` (NLF, no D04000/D04100 interface) the updated reference; the app's
default mode is now `kd` (docs/plc_kd_profile.md). For that program the PC needs a word area based at
D (D00500-D00510, D05000) and a **bit-write area based at M00000** (ORIGIN = coil write M00050,
offset 80). The section-5 table below was written for the PLC_INFAC interface; for kd, the bit-write
row is required, not optional. `python tools/plc_diag.py` checks the kd addresses by default.

## 1b. The client project's communication parameters (plc kd All.pdf, p134)

| Internal Cnet | CH1 | CH2 |
|---|---|---|
| Type | RS232 | RS485 |
| Data / stop / parity | 8 / 1 / none | 8 / 1 / none |
| Station | 0 | 1 |
| Operation mode | **XGT server** (LS protocol) | **Modbus RTU server** |
| Modbus areas | - | read bit P00000, write bit M00000, read word D00000, write word D00000 |

(The speed is not legible in the text of the printout.) The app's default mapping equals the CH2
Modbus areas. The live PC port answers Modbus at **station 1**, which matches CH2, not CH1 (CH1 would
answer only the XGT protocol, at station 0) - so COM10 is probably wired to CH2 through an RS485
adapter. Check the cable: RS232 (TX/RX/SG) = CH1, RS485 (+/-) = CH2.

Still open: on the machine FC04 answered at offset 500 but refused offset 5000, and earlier probes
found word windows of about 1000 registers (FC04 0..1003, FC03 0..993). Either the live parameters
differ from the project, or the XGB Modbus server only reaches about the first 1000 D registers -
then D05000 (homing state), D05010 (cycle step), D05110, D01250 (finals) and D01500.. (results) are
out of Modbus reach whatever the base. `python tools/plc_diag.py --window` finds the exact edge, and
offsets 30 / 130 compared with D00030 / D00130 in XG5000's monitor show whether the base is D00000.

## 2. Possible causes - decide with live evidence, in this order

1. **The installed program is the NLF program.** Then D04000/D04100 do not exist in the ladder; no
   Modbus setting can make ORIGIN work through `pc_interface`. Do not download PLC_INFAC to "fix" the
   app.
2. **PLC_INFAC is installed, but the connected channel's word areas do not start in D** (the evidence
   above points this way). Then the word areas need D bases (or the app needs the correct offsets if a
   different valid base already exposes D04000-D04213).
3. **The PC is wired to a different channel** from the one configured for the PC (an XGB has more than
   one built-in Cnet channel). COM10 is the PC's port name, not the PLC channel number.

## 3. Live evidence still needed

1. XG5000 read-back (upload) of the **live** program and parameters, saved as a backup file.
2. CPU model shown by XG5000 for the live PLC.
3. Program identity: project/program name in the read-back, and whether `MOV D04100 M0700` (or the
   D04000/M0800 moves) exists in it.
4. The built-in Cnet parameters for **each** channel: type (RS-232C / RS-485), speed, data bits, parity,
   stop bits, station, operating mode (e.g. Modbus RTU server), and the four Modbus area start
   addresses - photographed or exported as shown, not retyped from memory.
5. Which channel the PC cable is physically on (connector/terminal), and whether any other device
   (HMI, another PC) uses it.
6. XG5000 online device monitor values of D04000-D04005 and P00000-P0000F, with one value that you can
   change (servo ON bit, a cylinder sensor), for comparison with `tools/plc_diag.py`.

## 4. Read-only check from the PC

Close the app, then:

```
python tools/plc_diag.py
```

It prints, for D04000 x1, D04003 x3, D04100 x3, D04200 x14 and D04000 x21, the planned Cnet area,
function code, raw offset (decimal and hex), quantity and station, then the values, the exception, or
the timeout. On a failed block it tries the first and last register alone. It never writes and never
sweeps. To try candidate bases without saving them:

```
python tools/plc_diag.py --base word_read=D00000 --base word_write=D00000
python tools/plc_diag.py --raw 4 4000 1        (one raw request: FC, offset, quantity)
```

## 5. XG5000 procedure - only if the read-back shows the mapping is wrong

Menu names differ between XG5000 versions; check them against the installed XG5000 and the CPU's
built-in Cnet manual (XBC/XEC user manual, built-in communication chapter) before changing anything.

1. **Back up.** Connect XG5000 to the PLC, read (upload) program and parameters, save the project
   under a new name with the date. Stop the friction test flow; have the machine put into its
   established safe state by the person responsible for it.
2. **Find the channel.** Open the CPU's built-in Cnet / internal communication parameters. Identify
   the channel the PC is physically connected to (section 3, item 5). Record its serial settings and
   station - they must stay exactly as they are (57600 8N1, station 1 if that is what is shown).
3. **Record before / after.** Write down the four Modbus area start addresses as found, the proposed
   values, the reason for each change, and every other client of that channel. Change only what the
   verified PLC_INFAC interface needs:

   | Area | Function | Needed for | Proposed (if not already so) |
   |---|---|---|---|
   | Word read | FC04 | status D04000-D04020 | a D base that covers D04000-D04020, e.g. D00000 |
   | Word write | FC03 read / FC16 write | command words D04100-D04102, parameters D04200-D04213 | a D base covering D04100-D04213, e.g. D00000 |
   | Bit read | FC02 | sensor lamps P00000-P0000F | keep as found if it already reads P00000 (the FC02 snapshot suggests it does) |
   | Bit write | FC01 / FC05 | only native M reads (`internal_bits_enabled`: M00051/52/61/62 ...) | leave unchanged unless native M reads are wanted; a D-register problem alone does not justify remapping coils |

4. **Download parameters only.** Write only the changed communication parameters from the backed-up
   live project - not the ladder, and not either archived program. Apply whatever restart that CPU
   requires for Cnet parameter changes, as stated by XG5000 / the manual.
5. **Read back and compare.** Read the parameters back from the PLC and confirm the four bases and the
   serial settings. Run `python tools/plc_diag.py` and compare every value with XG5000's device
   monitor, including one changing status.
6. **Rollback.** If anything is wrong, write the recorded original parameters back from the backup and
   read them back again.

## 6. App settings after the read-back

In `settings.json` -> `test.machine_profile.mapping`:

```json
"mapping": {
  "schema": "xgb_cnet_areas",
  "verified": false,
  "verified_note": "",
  "cnet_areas": {
    "bit_read":   {"plc_base": "<as read back>", "accessible_count": null},
    "bit_write":  {"plc_base": "<as read back>", "accessible_count": null},
    "word_read":  {"plc_base": "<as read back>", "accessible_count": null},
    "word_write": {"plc_base": "<as read back>", "accessible_count": null}
  },
  "word_read_order": ["word_read", "word_write"]
}
```

Word-area bases are written in word context (`D00000`, `P0020` = P word 20); bit-area bases are bit
addresses (`M00000`, `P00100` = P word 10 bit 0). Set `verified` to `true`, with a note of who checked
what and when, only after section 5 step 5 matches. `tools/enable_machine_test.py` no longer sets it.

## 7. Still required before ORIGIN can work

Readable D04000-D04020 only gives the app status. Homing additionally needs: `live_writes_enabled`,
`command_owner_confirmed` (no other writer of D04100-D04102), servo-ON / alarm-normal / motion
permission ON in the status words, no stale PC request bits, and on the machine the full PLC home
sequence to finish with motion permission restored - origin feedback alone is not completion. The
app sends homing as a masked bit in D04100 (M07009 SHIFT, M0700D SELECT) and never resends a request
after a lost connection.
