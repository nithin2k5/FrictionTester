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
        "home": (0, 9),         # M07009 [p3 rung 154]: SET M02000 when M0020 = 0, M00120, no err/busy
        "meas_start": (2, 8),   # M07028 [p14-15]: SET M02040 when M0204 = 0, M00120, M00122
        "pull": (1, 1),         # M07011 [p5-6]:  SET M02010 when M0201 = 0, M00120, M00122
        "push": (1, 0),         # M07010 [p8-9]:  SET M02020 when M0202 = 0, M00120, M00122
    },
    "select": {
        "home": (0, 13),        # M0700D [p4]
        "meas_start": (2, 9),   # M07029 [p15-16]
        "pull": (1, 3),         # M07013 [p7-8]
        "push": (1, 2),         # M07012 [p10-11]
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
        "product_raw": (0, 11, "NOT M00025 (= NOT(NOT P0000A AND P00019)) - see UNRESOLVED"),
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
        "product_raw": (0, 10, "NOT M00026 (= NOT(NOT P0000B AND P00018)) - see UNRESOLVED"),
    },
}
COMMON_STATUS = {
    "meas_start_switch": (0, 12, "P0000E (conditioned)"),
    "estop_upper": (0, 13, "P0000F"),
    "estop_lower": (2, 6, "P0000D"),
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

# Request bits the app must never leave set by accident (motion requests).
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
    "Modbus mapping of the D area (and M area, for M00051/52/61/62): table, offset and word order of "
    "the XGB Cnet port. Live writes stay off until verified.",
    "Command-word ownership: no other application may write D04100-D04102 while this app runs.",
    "M0800A/M0800B ('product detection'): the ladder drives them from NOT M00026 / NOT M00025, and "
    "M00025/M00026 = NOT P0000A AND P00019 / NOT P0000B AND P00018 (p23) are also SELECT-cycle "
    "PULL/PUSH requests (p7, p10, p14). P00018/P00019 are unlabelled. Shown raw only.",
    "Home / PULL / PUSH sequences have no PLC timeout (T0100-T0102 unused); the app times out but "
    "cannot stop the axis - a stuck sequence must be cleared at the machine.",
    "Measurement-start positioning ends after 5 s (T0103/T0203) even if the position was not reached; "
    "its sequence word M0204/M0214 is not exported, so the app checks the position bit itself.",
    "Units: speeds/travel/limits are scaled x100 and measurement start x10 into positioning counts; "
    "counts per mm and speed units are machine calibration (not in the program).",
    "D00490 (+100 counts on the PULL target while M0700F is OFF) - meaning of M0700F (D04100 bit 15).",
    "M07008 drives both lamp P0002D (p29) and, with M0700F, P00031 (p27); M0700A/M0700E appear to "
    "be alarm reset - not assigned to buttons.",
    "Servo ON from the PC (M07007/M0700B with M0700F, p27-28) - not used.",
    "Overall machine stop: no PC stop command exists in this program.",
    "Printing P00038 is triggered by M07000->M07002 / M07003->M07004 sequences (p32-33): fixture "
    "solenoid commands are therefore not written by the app.",
    "Fixture sequence, clamp polarity and the SHIFT clamp output being gated by the SELECT select "
    "bit M00205 (p28) - confirm before automatic clamping.",
    "Force source, loadcell-to-cable assignment, stroke source (LVDT), calibration and whether "
    "PULL1-3/PUSH1-3 are positions or repeated strokes.",
    "Model fields without a PLC transfer address: delay time, positions 1-3, LOAD limits (used by "
    "the app's own judgement only).",
]
