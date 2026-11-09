"""Machine profile: XG5000 program "PRINTING PROGRAM OLD.xgwx" (plc.pdf, 33 pages,
printed 2026-11-06) - the PC word interface.

Everything here was traced from the printout; page numbers are given so each
entry can be checked. The earlier "NEW NLF MC KOREA" assumptions (M00029 start,
D05010 step, M0000C/M0000D verdict) belong to a different program and are not
used by this profile. SH = SHIFT cable (X axis), SL = SELECT cable (Y axis).

PC -> PLC (page 29, every scan):  D04100 -> M0700,  D04101 -> M0701,  D04102 -> M0702
Echo      (page 30, every scan):  M0700 -> D04000,  M0701 -> D04001,  M0702 -> D04002
Status    (page 31, every scan):  M0800 -> D04003,  M0801 -> D04004,  M0802 -> D04005
Params    (pages 30, 25-27, 32):  D04200..D04213 -> M0750..M0763 -> D020xx/D021xx
                                  (word + upper word cleared), echo M0750..M0763 -> D04007..D04020

Bit n of a word is zero-based: D04101 bit 3 is M07013.

PLC backup 2026-11-08 (PLCBCKUP_SURESH08_11_2026.pdf, XG5000 variable/comment table
of the program read from the machine's PLC; docs/plc_backup_2026-11-08.md): the
installed program has this interface - D04000-D04020 transmit, D04100-D04102 receive
bits, D04200-D04213 receive words, M0700-M0702 / M0750-M0763 / M0800-M0802 - with
English names for every bit, and an "in use" flag per device. Used: the PC jog bits
M07014-M0701B (pages 17-19: held request + servo ON + no alarm + no axis error + no
opposite request + soft limit not reached -> K04291/K04292 jog start). Listed but
NOT used: M0701C / M0701D "Operation Stop" (still no PC stop), M00010 "PC
Communication Check" (the PLC does not watch the PC link), D04103-D04119, D04214-D04219.
"""

PROFILE_ID = "printing_program_old_pc_words"
PROFILE_VERSION = 1
PROFILE_TITLE = "PRINTING PROGRAM OLD (PC word interface)"

CMD_WORDS = ("D04100", "D04101", "D04102")      # written by the PC
ECHO_WORDS = ("D04000", "D04001", "D04002")     # M0700..M0702 copied back
STATUS_WORDS = ("D04003", "D04004", "D04005")   # M0800..M0802
PARAM_WORDS = tuple(f"D0{4200 + i}" for i in range(14))       # D04200..D04213
PARAM_ECHO_WORDS = tuple(f"D0{4007 + i}" for i in range(14))  # D04007..D04020
READ_BLOCK = ("D04000", 21)                      # D04000..D04020 in one request

AXES = ("shift", "select")
AXIS_NAME = {"shift": "SHIFT", "select": "SELECT"}

# (word index 0..2 into CMD_WORDS, bit). Pages in brackets.
COMMANDS = {
    "shift": {
        "home": (0, 9),         # M07009 [p3 rung 154]: SET M02000 when M0200 = 0, M00120, no err/busy
        "meas_start": (2, 8),   # M07028 [p14-15]: SET M02040 when M0204 = 0, M00120, M00122
        "pull": (1, 1),         # M07011 [p5-6]:  SET M02010 when M0201 = 0, M00120, M00122
        "push": (1, 0),         # M07010 [p8-9]:  SET M02020 when M0202 = 0, M00120, M00122
        "alarm_reset": (0, 10), # M0700A [p27]: P00033 = (M00105 AND M0010D) OR (NOT M00105 AND M0700A)
        # PC jog [p17-18], held requests (backup 2026-11-08: "From PC - SH Jog ...", in use)
        "jog_left_high": (1, 4),   # M07014
        "jog_left_low": (1, 5),    # M07015
        "jog_right_low": (1, 6),   # M07016
        "jog_right_high": (1, 7),  # M07017
    },
    "select": {
        "home": (0, 13),        # M0700D [p4]
        "meas_start": (2, 9),   # M07029 [p15-16]
        "pull": (1, 3),         # M07013 [p7-8]
        "push": (1, 2),         # M07012 [p10-11]
        "alarm_reset": (0, 14), # M0700E [p28]: P00037 = (M00205 AND M0020D) OR (NOT M00205 AND M0700E)
        "jog_left_high": (1, 8),   # M07018 [p18-19]
        "jog_left_low": (1, 9),    # M07019
        "jog_right_low": (1, 10),  # M0701A
        "jog_right_high": (1, 11), # M0701B
    },
}

# (status word index 0..2 into STATUS_WORDS, bit, source in the ladder)
STATUS = {
    "shift": {
        "servo_on": (0, 14, "P00010"),
        "pos_complete": (0, 15, "P00011"),
        "origin_complete": (1, 0, "P00012"),
        "alarm_normal": (1, 1, "P00013"),      # comment: "no alarm" - NO contact in M00120 [p3]
        "pull_active": (1, 6, "M00127 = (M0201 <> 0)"),
        "push_active": (1, 7, "M00128 = (M0202 <> 0)"),
        "permitted": (1, 10, "M00120 AND M00122"),
        "limit_left": (1, 12, "M00123 = CurPos >= D02014"),
        "limit_right": (1, 13, "M00124 = CurPos <= D02016"),
        "axis_error": (2, 0, "K04201 _POS_X_Err"),
        "at_pull": (2, 2, "CurPos = D02004"),
        "at_push": (2, 3, "CurPos = D02018"),
        "at_meas_start": (2, 14, "CurPos = D02018"),
        "clamp_off": (0, 2, "P00002"),
        "clamp_on": (0, 3, "P00003"),
        "expansion_home": (0, 0, "P00000"),
        "expansion_end": (0, 1, "P00001"),
        "discharge2_home": (0, 4, "P00004"),
        "discharge2_end": (0, 5, "P00005"),
    },
    "select": {
        "servo_on": (1, 2, "P00014"),
        "pos_complete": (1, 3, "P00015"),
        "origin_complete": (1, 4, "P00016"),
        "alarm_normal": (1, 5, "P00017"),
        "pull_active": (1, 8, "M00227 = (M0211 <> 0)"),
        "push_active": (1, 9, "M00228 = (M0212 <> 0)"),
        "permitted": (1, 11, "M00220 AND M00222"),
        "limit_left": (1, 14, "M00223"),
        "limit_right": (1, 15, "M00224"),
        "axis_error": (2, 1, "K04301 _POS_Y_Err"),
        "at_pull": (2, 4, "CurPos = D02104"),
        "at_push": (2, 5, "CurPos = D02118"),
        "at_meas_start": (2, 15, "CurPos = D02118"),
        "clamp_off": (0, 8, "P00008"),
        "clamp_on": (0, 9, "P00009"),
        "expansion_home": (0, 6, "P00006"),
        "expansion_end": (0, 7, "P00007"),
    },
}
# Names from the PLC backup's comment table (2026-11-08); keys kept for compatibility.
COMMON_STATUS = {
    "m0800a_cond": (0, 10, "M0800A 'To PC - SL Product Detection' (NOT M00026; P0000A / P00018) [p23-24]"),
    "m0800b_cond": (0, 11, "M0800B 'To PC - SH Product Detection' (NOT M00025; P0000B / P00019) [p23-24]"),
    "meas_start_switch": (0, 12, "M0800C 'To PC - Measurement Start Switch' (P0000E) [p24]"),
    "p0000f_input": (0, 13, "M0800D 'To PC - Emergency Stop Switch' = P0000F 'Emergency Stop Top', ON = healthy"),
    "p0000d_input": (2, 6, "M08026 'To PC - Emergency Stop Bottom' = P0000D, ON = healthy"),
}

# Local/PC selector per axis (touch-operation select): ON = local branch, the PC
# fixture / alarm-reset branches are inactive [p27-28]. Not exported; readable
# only through a verified native M mapping.
SELECTOR = {"shift": "M00105", "select": "M00205"}

# Sequence words (bit k = step k of a BSFT chain) [p3-16]. Only the
# measurement-start chain is read (when the M mapping is verified): its word is
# not exported and its step 5 can advance on T0103/T0203 without the target.
SEQ_WORDS = {
    "shift": {"home": "M0200", "pull": "M0201", "push": "M0202", "repeat": "M0203", "meas_start": "M0204"},
    "select": {"home": "M0210", "pull": "M0211", "push": "M0212", "repeat": "M0213", "meas_start": "M0214"},
}
MEAS_STEP_BITS = {axis: [f"{SEQ_WORDS[axis]['meas_start']}{k:X}" for k in range(10)] for axis in ("shift", "select")}
MEAS_RELEASE_STEP = 7          # step 7 waits for the request to be released

# Bits that drive more than one thing - never exposed as a simple control.
COUPLED_BITS = {
    "M07008": "SHIFT torque-limit P00031 (with M0700F) AND green lamp P0002D [p27, p29] - backup comment "
              "'Indicator Lamp - Green' - not alarm reset",
    "M0700C": "SELECT torque-limit P00035 (with M0700F) [p28]",
    "M0700F": "PC manual/automatic branch: servo-enable paths, torque limits, D00490 (+100 PULL target) [p20, p27-28]",
    "M07007": "SHIFT servo enable in the manual PC branch only (M0700F ON) [p27]",
    "M0700B": "SELECT servo enable in the manual PC branch only (M0700F ON) [p28]",
    "M07006": "SET buzzer P0002A; reset by NG box P0001A [p29]",
}

# Internal completion bits (set at step 6, reset at steps 1 and 9 of the
# sequence) [p6-7, p8, p9-10, p11]. Not in the status words; read only when the
# M-area Modbus mapping is verified and settings enable it.
INTERNAL_COMPLETE = {
    "shift": {"pull": "M00051", "push": "M00052"},
    "select": {"pull": "M00061", "push": "M00062"},
}

# Parameters, in D04200.. order: (axis, key, label, ladder destination, scaling found on p20-22)
PARAMS = [
    ("shift", "jog_low", "Low jog speed", "D02022", "x100 -> K0481 X jog low speed"),
    ("shift", "jog_high", "High jog speed", "D02024", "x100 -> K0479 X jog high speed"),
    ("shift", "speed", "Operating speed", "D02026", "x100 -> D02002 -> D01002"),
    ("shift", "pull_travel", "PULL travel", "D02028", "x100 + D02018 + D00490 -> D02004 PULL target"),
    ("shift", "limit_left", "Left limit", "D02030", "x100 + D02000 -> D02014"),
    ("shift", "limit_right", "Right limit", "D02032", "D02000 - x100 -> D02016"),
    ("select", "jog_low", "Low jog speed", "D02122", "x100"),
    ("select", "jog_high", "High jog speed", "D02124", "x100"),
    ("select", "speed", "Operating speed", "D02126", "x100 -> D02102 -> D01102"),
    ("select", "pull_travel", "PULL travel", "D02128", "x100 + D02118 + D00490 -> D02104 PULL target"),
    ("select", "limit_left", "Left limit", "D02130", "x100 + D02100"),
    ("select", "limit_right", "Right limit", "D02132", "D02100 - x100"),
    ("shift", "meas_start", "Measurement start", "D02034", "D02000 - MUL x10 (16-bit signed) -> D02018"),
    ("select", "meas_start", "Measurement start", "D02134", "D02100 - MUL x10 (16-bit signed) -> D02118"),
]
PARAM_KEYS = ("jog_low", "jog_high", "speed", "pull_travel", "limit_left", "limit_right", "meas_start")
PARAM_LABELS = {k: lbl for _a, k, lbl, _d, _s in PARAMS}
WORD_MAX = 65535          # MOV of one word into a cleared double word: 0..65535


def param_max(key):
    """Largest value the ladder handles correctly: DMUL params use the cleared
    double word (0..65535); measurement start goes through 16-bit signed MUL."""
    return 32767 if key == "meas_start" else WORD_MAX


def param_index(axis, key):
    for i, (a, k, *_r) in enumerate(PARAMS):
        if a == axis and k == key:
            return i
    raise KeyError((axis, key))


# Fixture commands (pages 27-28): driven only while the axis' touch-operation
# select (M00105 SH / M00205 SL) is OFF. NOT written automatically by the app:
# M07000->M07002 and M07003->M07004 also feed the print trigger P00038 (p32-33).
FIXTURE_COMMANDS = {
    "shift_expand": (0, 0, "P00024"), "shift_discharge1": (0, 1, "P00025"),
    "shift_discharge2": (0, 2, "P00026"), "select_expand": (0, 3, "P00027"),
    "select_discharge1": (0, 4, "P00028"), "select_discharge2": (0, 5, "P00029"),
    "shift_clamp": (2, 0, "P0002E, gated by NOT M00205 (the SELECT select bit) as printed"),
    "select_clamp": (2, 1, "P0002F"),
}

JOG_OPS = ("jog_left_high", "jog_left_low", "jog_right_low", "jog_right_high")

# Request bits the app must never leave set by accident (motion requests, jog and
# alarm reset).
MOTION_BITS = {(axis, op): COMMANDS[axis][op] for axis in AXES for op in COMMANDS[axis]}


def bit(words, index, n):
    """Bit n (0-based) of words[index], or None if the word is unknown."""
    if words is None or words[index] is None:
        return None
    return bool((int(words[index]) >> n) & 1)


def m_address(word_base, index, n):
    """XG5000 bit name, e.g. m_address(700, 1, 3) -> 'M07013'."""
    return f"M{word_base + index:04d}{n:X}"


def command_name(axis, op):
    idx, n = COMMANDS[axis][op]
    return f"{CMD_WORDS[idx]} bit {n} / {m_address(700, idx, n)}"


UNRESOLVED = [
    "Program identity: the PLC backup of 2026-11-08 (comment table) matches this interface; the ladder "
    "itself is still from plc.pdf (2026-11-06) - compare when a ladder printout of the backup is available.",
    "Communication: PLC model, protocol, station, serial settings and the Modbus mapping of the D area "
    "(table, offset, register width, word order). Live writes stay off until verified.",
    "Native M reads (M00051/52/61/62, selectors M00105/M00205, measurement-start steps M0204x/M0214x): "
    "needed for PULL/PUSH completion and verified measurement-start acceptance.",
    "Command-word ownership: no other program may write D04100-D04102 while this app runs.",
    "Stop: no PC stop command - M0701C / M0701D 'Operation Stop' are named but not used (backup 2026-11-08).",
    "PC link watchdog: M00010 'PC Communication Check' is not used - the PLC does not notice a lost PC link. "
    "A held jog bit stops only at a soft limit; the app releases it on button release, after "
    "timeouts.jog_hold without a refresh, and on reconnection.",
    "M0800A/M0800B: SL / SH product detection per the backup; they come from NOT M00026 / NOT M00025 "
    "(P0000A/P0000B and P00018/P00019) [p23-24] - polarity on the machine still to be seen.",
    "Home / PULL / PUSH sequences have no PLC timeout (T0100-T0102 unused); a stuck sequence must be "
    "cleared at the machine. Timer units (presets 1, 5, 10, 50) depend on timer configuration.",
    "Units: speeds/travel/limits are scaled x100 and measurement start x10 into positioning counts; "
    "counts per mm and speed units are machine calibration.",
    "Alarm reset: required pulse length (drive requirement) and whether one pulse clears each fault class.",
    "M0700F, M07007, M0700B, M07008, M0700C: coupled mode/servo/torque-limit/lamp outputs - not written.",
    "Fixture order, sensor confirmations and clamp polarity; both clamp PC branches use M00205 [p28].",
    "Printing P00038 is triggered by M07000->M07002 / M07003->M07004 [p32-33]; fixture bits not written.",
    "MT: simultaneous SHIFT/SELECT motion (clearance, fixtures, drives, acquisition) not verified.",
    "Measurement: force source per cable, stroke/LVDT source and alignment, calibration, sign, "
    "sampling windows, limits rule, positions vs repeated strokes.",
    "K04200/K04300 (busy), K0422/K0432 (positions), K04204/K04304 (origin) - native access/width not verified.",
    "D04006 (copy of M0850): no measurement meaning established.",
]
