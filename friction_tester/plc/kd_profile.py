"""Machine profile: client reference program "plc kd.pdf" (12 pages, printed
2026-11-07, footer NEW NLF MC KOREA MODIFY 09_06_25.xgwx).

Traced from the printout's text; step numbers (left margin) and pages are given
so each entry can be checked in XG5000. The Korean comments are damaged by the
character encoding; meanings come from the English comments and the logic.
Whether this program is the one installed on the PLC is NOT confirmed.

One positioning axis (X, positioning module slot 2, axis 0). Both load cells
come from the analog module (slot 1). The automatic test cycle is the
companion block "plc Auto.pdf" (same project): start P00002 or M00029, step
word D05010 - followed by plc.shared_cycle / plc.part_flow. This module covers
the manual side: homing (M00050) and position moves (M00030-M00034).

Homing [p3-4, steps 209-236], state word D05000:
    M00050 AND D05000 = 0      -> D05000 := 10
    D05000 = 10                -> T0000 (2 s), then D05000 := 20 (driver reset P00003 meanwhile)
    D05000 = 20                -> homing instruction on slot 2 axis 0
    homing status D00500.4 ... -> D05000 := 0, RESET M00050 (and P00022)
  so the PLC clears M00050 itself when homing is finished. The exact contacts
  of steps 212 and 228 are only partly legible in the text extraction (a T0030
  / P00003 branch may also start homing) - verify in XG5000.

Moves [p5, steps 283-327]: NOT P00000 (EMG) AND M0003x AND NOT D00500.0 (busy)
AND NOT P00004 (external emergency) -> DST slot 2 axis 0 to the position value
at the given speed. The PLC never resets M0003x.
"""

PROFILE_ID = "plc_kd_nlf_x_axis"
PROFILE_VERSION = 1
PROFILE_TITLE = "plc kd.pdf (NLF, X axis)"

AXIS = "X"

# Positioning-module status copied every scan by SRD slot 2 axis 0 [p2, step 63]
STATUS_BLOCK = ("D00500", 11)          # D00500..D00510

STATUS_BITS = {        # name -> (word index into STATUS_BLOCK, bit)
    "busy": (0, 0),                    # D00500.0 X-axis operating status
    "error": (0, 1),                   # D00500.1 X-axis error status
    "pos_complete": (0, 2),            # D00500.2 positioning complete signal
    "mcode": (0, 3),                   # D00500.3
    "homed": (0, 4),                   # D00500.4 X-axis homing status (origin determined)
    "stopped": (0, 6),                 # D00500.6 stop status
    "upper_limit": (0, 8),             # D00500.8 upper limit detection
    "lower_limit": (0, 9),             # D00500.9 lower limit detection
    "emergency_stop": (0, 10),         # D00500.A emergency stop
    "direction": (0, 11),              # D00500.B forward / reverse
    "accel": (0, 12), "constant": (0, 13), "decel": (0, 14), "dwell": (0, 15),
    "position_control": (1, 0),        # D00501.0
    "speed_control": (1, 1),           # D00501.1
    "homing_active": (1, 5),           # D00501.5 homing operation
    "jog_low": (1, 8), "jog_high": (1, 9), "inching": (1, 10),
    "ext_upper_limit": (3, 4),         # D00503.4 external upper limit signal
    "ext_lower_limit": (3, 5),         # D00503.5
    "home_signal": (3, 6),             # D00503.6
    "near_home_signal": (3, 7),        # D00503.7
    "in_position": (3, 12),            # D00503.C in-position signal
}
POSITION_WORDS = (4, 5)                # D00504 low, D00505 high: current position (DINT)
SPEED_WORDS = (6, 7)                   # D00506 low, D00507 high (text partly legible)
STEP_WORD = 8                          # D00508 step number
MCODE_WORD = 9                         # D00509
ERROR_CODE_WORD = 10                   # D00510 error code

HOME_STATE = "D05000"                  # 0 idle, 10 driver reset/wait, 20 homing [p4]
HOME_REQUEST = "M00050"

# Move requests [p5] -> (request bit, position register (DINT), description)
MOVES = {
    "pos1": ("M00030", "D00735", "Position 1"),
    "pos2": ("M00031", "D00745", "Position 2"),
    "pos3": ("M00032", "D00755", "Position 3"),
    "total": ("M00033", "D00730", "Total distance"),
    "home_pos": ("M00034", "D00740", "Home position"),
}
SPEED_REGISTER = "D00750"

# Jog [p4, steps 262-276] - not driven by the app (needs a held request)
JOG_BITS = {"ccw": "M00020", "cw": "M00022", "speed": ("M00021", "M00023")}

# Recipe [p10-12, steps 579-845]: D00080 = model 1..20; M00062 copies
# D02000+100*(n-1) (99 words) to D00700, M00063 copies D00700 back. Not driven yet.
RECIPE_NUMBER = "D00080"
RECIPE_LOAD, RECIPE_SAVE = "M00062", "M00063"
RECIPE_WORK = ("D00700", 99)

# Command bits read back every poll (one FC01 block M00020..M00063)
COMMAND_BITS = ["M00020", "M00021", "M00022", "M00023", "M00030", "M00031", "M00032", "M00033",
                "M00034", "M00050", "M00062", "M00063"]

# Project cross-check (plc kd All.pdf): variable/comment table p1-5 confirms D00500-D00503 bit names,
# D00030 "Select Load Cell Value", D00130 "Shift Load Cell Value", positions D00735/D00745/D00755,
# total D00730, home position D00740, speed D00750, measurement count D00781.
# Inputs used as interlocks by the ladder; contact type / polarity NOT verified - shown raw.
INTERLOCK_INPUTS = {"P00000": "Emergency stop (EMG) - in move/jog interlocks",
                    "P00004": "External emergency stop - in move/jog interlocks"}

# Load cells [p1, steps 1-60]: (value, value+100) - DINT, scaling unverified
LOADS = {"select": "D00030", "shift": "D00130"}

# Bits the app may set. Everything else (jog, recipe, servo P00020 = always ON) is not written.
WRITABLE_BITS = {HOME_REQUEST} | {m[0] for m in MOVES.values()}

UNRESOLVED = [
    "Installed program: confirm by XG5000 Online -> Read that the PLC runs this program (search M00050, D05000).",
    "Cnet: the project (plc kd All.pdf p134) has CH1 RS232 = XGT server, station 0 and CH2 RS485 = "
    "Modbus RTU server, station 1, areas P00000 / M00000 / D00000 / D00000. Which channel the PC is "
    "wired to and whether the live PLC holds these parameters is not confirmed; on the machine FC04 "
    "offset 500 answers but 5000 returns exception 02 (a word window of about 1000 registers).",
    "Homing start/finish contacts (steps 212, 228): T0030 / P00003 branch, '= 10' vs '= 20' compare.",
    "Whether DST runs on the rising edge of M0003x (the app releases the bit once the axis is busy).",
    "P00000 / P00004 contact type (NO/NC) and wiring.",
    "Load-cell scaling (D00010/D00110 factors) and the meaning of value+100 (D00040/D00140).",
    "Who writes D00900-D00950 (position load values copied to D01200..): PC or HMI?",
    "Auto cycle: ageing M02000; P0000A / P0000B are 'Driver Homing' / 'Driver Alarm Status' in the "
    "project (P0001A 'Driver Home', P00009 'Completion Signal') but 'part presence' in the client's "
    "I/O list - the sensor lamps still follow the I/O list.",
    "Recipe numbering D00080 1..20 versus the app's model list.",
]


def bit(words, idx, n):
    if words is None or idx >= len(words) or words[idx] is None:
        return None
    return bool((int(words[idx]) >> n) & 1)


def dint(lo, hi):
    v = ((int(hi) & 0xFFFF) << 16) | (int(lo) & 0xFFFF)
    return v - 0x100000000 if v & 0x80000000 else v
