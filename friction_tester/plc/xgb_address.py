"""XG5000 (LS XGB) device addresses.

Bit devices (P, M, K, L, F): a decimal word number followed by one hex digit
for the bit. Leading zeros vary between the 4- and 5-digit forms XG5000 prints:

    M00029 -> area M, word 2, bit 9       P0002A -> area P, word 2, bit 10
    M0000D -> area M, word 0, bit 13      P00002 -> area P, word 0, bit 2

Word devices (D): a decimal word number, optionally ".<hex bit>":

    D05010 -> area D, word 5010           D00500.4 -> area D, word 500, bit 4
"""

import re
from collections import namedtuple

XgbAddress = namedtuple("XgbAddress", "area word bit")

BIT_AREAS = "PMKLF"
WORD_AREAS = "D"

_BIT = re.compile(r"^([PMKLF])(\d+)([0-9A-F])$")
_WORD = re.compile(r"^(D)(\d+)(?:\.([0-9A-F]))?$")


def parse(address):
    """Parse an XG5000 address into (area, word, bit). bit is None for a whole word."""
    text = address.strip().upper()
    m = _WORD.match(text)
    if m:
        area, word, bit = m.groups()
        return XgbAddress(area, int(word), int(bit, 16) if bit is not None else None)
    m = _BIT.match(text)
    if m:
        area, word, bit = m.groups()
        return XgbAddress(area, int(word), int(bit, 16))
    raise ValueError(f"Not an XG5000 address: {address!r}")


def bit_number(address):
    """Bit index inside its area: word * 16 + bit (M00029 -> 41, P0002A -> 42)."""
    a = parse(address)
    if a.bit is None:
        raise ValueError(f"{address} is a word address, not a bit")
    return a.word * 16 + a.bit


def word_number(address):
    """Word index of a D address (D05010 -> 5010)."""
    a = parse(address)
    if a.area not in WORD_AREAS or a.bit is not None:
        raise ValueError(f"{address} is not a D word address")
    return a.word


def to_signed16(v):
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


def to_dint(lo, hi):
    """XGB double word: the lower address holds the low word (D00030 low, D00031 high)."""
    v = ((hi & 0xFFFF) << 16) | (lo & 0xFFFF)
    return v - 0x100000000 if v & 0x80000000 else v
