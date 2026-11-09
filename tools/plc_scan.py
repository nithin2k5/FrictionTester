"""READ-ONLY scan of the four Modbus tables of an XGB Cnet channel (used by plc_diag.py --scan).

Answers, without XG5000, "where do the channel's Modbus areas start?":

1. Extent: the highest readable offset of each table (FC01 coils, FC02 discrete inputs,
   FC03 holding registers, FC04 input registers), by binary search over 0..32767 with
   single reads. (This XGB answers again from 32768 - the offsets wrap - so 32767 is the top.)
2. Content: every reachable bit / word, read in blocks; the non-zero places are listed.
3. Alignment: FC02 is known to start at P00000 (the start / emergency inputs move the bits
   at P0000E / P0000F). Its bits packed into words are the P area's image. Each other table
   is compared with that image at every shift: an exact match of the non-zero words at shift
   k means that table starts at P word k (bit tables: P bit k*16).
4. Size arithmetic: if an area runs to the end of its device, base = device size - count.
   Device sizes are assumptions (printed with the result) - check the CPU manual.

Nothing is ever written. Live memory changes while the scan runs, so the alignment
reports how many non-zero words agree, not only perfect matches.
"""

import json
import time

TABLES = [  # (fc, table, kind)
    (2, "discrete_inputs", "bit"),
    (1, "coils", "bit"),
    (4, "input_registers", "word"),
    (3, "holding_registers", "word"),
]
TOP = 32767
BLOCK = {"bit": 1000, "word": 100}
# Assumed XGB device sizes in words (check against the CPU's manual): P and M 1024 words,
# D 10240 words on XBC H-type CPUs.
DEVICE_WORDS = {"P": 1024, "M": 1024, "D": 10240}


def find_extent(read1):
    """read1(offset) -> True if a single read there succeeds. Returns the highest readable
    offset (assuming the readable range is contiguous from 0), or None if 0 is not readable."""
    if not read1(0):
        return None
    lo, hi = 0, TOP + 1          # lo readable, hi not (or past the top)
    if read1(TOP):
        return TOP
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if read1(mid):
            lo = mid
        else:
            hi = mid
    return lo


def read_all(read_block, count, kind):
    """read_block(offset, qty) -> list or raises. Returns (values, failures)."""
    values, failures = [], []
    step = BLOCK[kind]
    for off in range(0, count, step):
        qty = min(step, count - off)
        try:
            values.extend(read_block(off, qty))
        except Exception as exc:          # keep going: report the gap
            failures.append((off, qty, str(exc)))
            values.extend([None] * qty)
    return values, failures


def pack_bits(bits):
    """Bits (offset 0 = bit 0 of word 0) -> 16-bit words; None if any bit of the word is None."""
    words = []
    for w in range(len(bits) // 16):
        chunk = bits[w * 16:(w + 1) * 16]
        if any(b is None for b in chunk):
            words.append(None)
        else:
            words.append(sum(1 << i for i, b in enumerate(chunk) if b))
    return words


def align(reference, other, min_nonzero=2):
    """Find shifts k where other[i] == reference[i + k] (both known). Only shifts whose overlap
    holds at least `min_nonzero` non-zero words count. Returns a list of
    (k, agreeing_nonzero, disagreeing) sorted best first."""
    out = []
    for k in range(len(reference)):
        agree = disagree = 0
        for i, v in enumerate(other):
            j = i + k
            if j >= len(reference):
                break
            r = reference[j]
            if v is None or r is None:
                continue
            if v == r:
                if v:
                    agree += 1
            else:
                disagree += 1
        if agree >= min_nonzero:
            out.append((k, agree, disagree))
    out.sort(key=lambda t: (t[2], -t[1]))
    return out


def nonzero_summary(values, kind, limit=60):
    if kind == "bit":
        on = [i for i, b in enumerate(values) if b]
        text = ", ".join(map(str, on[:limit])) + (" ..." if len(on) > limit else "")
        return len(on), text
    nz = [(i, v) for i, v in enumerate(values) if v]
    text = " ".join(f"{i}={v}(0x{v:04X})" for i, v in nz[:limit]) + (" ..." if len(nz) > limit else "")
    return len(nz), text


def size_candidates(count, kind):
    words = count // 16 if kind == "bit" else count
    out = []
    for dev, size in DEVICE_WORDS.items():
        if kind == "bit" and dev == "D":
            continue                       # bit areas are P / M here
        base = size - words
        if 0 <= base < size:
            if kind == "bit":
                out.append(f"{dev}{base:04d}0")
            else:
                out.append(f"D{base:05d}" if dev == "D" else f"{dev}{base:04d}")
    return out


def p_name(word, kind):
    return f"P{word:04d}" if kind == "word" else f"P{word:04d}0 (P word {word})"


def run(read_table, out_path=None, log=print):
    """read_table(table, offset, qty) -> list (raises on a Modbus exception / timeout)."""
    result = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "tables": {}}

    def ok(table):
        def read1(off):
            try:
                read_table(table, off, 1)
                return True
            except Exception:
                return False
        return read1

    log("Step 1/3 - extent of each table (single reads, binary search)")
    for fc, table, kind in TABLES:
        top = find_extent(ok(table))
        count = 0 if top is None else top + 1
        result["tables"][table] = {"fc": fc, "kind": kind, "count": count}
        log(f"   FC{fc:02d} {table:<17}: " + ("offset 0 refused" if top is None else
            f"offsets 0..{top} ({count} {kind}s)"))

    log("Step 2/3 - content (block reads)")
    for fc, table, kind in TABLES:
        t = result["tables"][table]
        if not t["count"]:
            continue
        vals, fails = read_all(lambda off, qty, tb=table: read_table(tb, off, qty), t["count"], kind)
        t["values"] = vals
        t["failures"] = fails
        n, text = nonzero_summary(vals, kind)
        log(f"   FC{fc:02d}: {n} non-zero {kind}s" + (f": {text}" if n else "")
            + (f"  [{len(fails)} block(s) failed]" if fails else ""))

    log("Step 3/3 - where each table starts")
    di = result["tables"]["discrete_inputs"]
    p_image = pack_bits(di.get("values") or [])
    result["analysis"] = {}
    for fc, table, kind in TABLES:
        t = result["tables"][table]
        if not t["count"]:
            continue
        lines = []
        cands = size_candidates(t["count"], kind)
        if cands:
            lines.append("if the area runs to the end of its device: base " + " or ".join(cands)
                         + " (assumed sizes " + ", ".join(f"{d} {n} words" for d, n in DEVICE_WORDS.items()) + ")")
        if table != "discrete_inputs" and p_image:
            other = pack_bits(t.get("values") or []) if kind == "bit" else (t.get("values") or [])
            if not any(other):
                lines.append("all zero - cannot be aligned with the P image")
            else:
                hits = align(p_image, other)
                if hits:
                    k, agree, dis = hits[0]
                    lines.append(f"best match with the P image: starts at {p_name(k, kind)} "
                                 f"({agree} non-zero words agree, {dis} differ)")
                    t["p_shift"] = k
                else:
                    lines.append("no match with the P image: its non-zero values are not P memory "
                                 "(another device, e.g. M or D)")
        elif table == "discrete_inputs":
            lines.append("known: starts at P00000 (inputs P0000E / P0000F respond)")
        result["analysis"][table] = lines
        for line in lines:
            log(f"   FC{fc:02d}: {line}")

    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f)
        log(f"Full dump: {out_path}")
    return result
