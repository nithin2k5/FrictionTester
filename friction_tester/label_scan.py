"""Scanned cable labels: find the traceability code and check the label against the model.

A label holds the model's ALC code and/or model number, plus a traceability code.
Its layout is written with placeholders (settings test.scan.formats), tried in order:

    {ALC}    the loaded model's ALC Code (Model Settings) - must match
    {MODEL}  the loaded model's model number             - must match
    {PART}   the loaded model's part number              - must match
    {TRACE}  the traceability code (the rest)             - must be unique

Any other text in a format is matched literally (e.g. "{ALC}-{TRACE}"). Between two
placeholders with nothing written between them, a separator (space - _ ; : , | /) is
allowed but not needed. Letters are compared without regard to case. Put the longer
formats first: "{ALC}{TRACE}" would also take an ALC+model label, with the model
number counted into the traceability code.
"""

import re

PLACEHOLDERS = ("ALC", "MODEL", "PART", "TRACE")
DEFAULT_FORMATS = ["{ALC}{MODEL}{TRACE}", "{ALC}{TRACE}", "{MODEL}{TRACE}"]
SEPARATORS = r"[\s\-_;:,|/]*"
_TOKEN = re.compile(r"\{(" + "|".join(PLACEHOLDERS) + r")\}")


class ScanError(ValueError):
    pass


def _pattern(fmt, values):
    """The regex for one format, or None when it needs a model field that is not set."""
    parts, pos, prev_token = [], 0, False
    if "{TRACE}" not in fmt:
        raise ScanError(f"label format {fmt!r} has no {{TRACE}}")
    for m in _TOKEN.finditer(fmt):
        literal = fmt[pos:m.start()]
        if literal:
            parts.append(re.escape(literal))
        elif prev_token:
            parts.append(SEPARATORS)
        name = m.group(1)
        if name == "TRACE":
            parts.append(r"(?P<TRACE>\S.*?)")
        else:
            value = (values.get(name) or "").strip()
            if not value:
                return None
            parts.append(re.escape(value))
        prev_token, pos = True, m.end()
    if fmt[pos:]:
        parts.append(re.escape(fmt[pos:]))
    return re.compile("".join(parts), re.IGNORECASE)


def parse(label, formats=None, alc="", model="", part=""):
    """Split a scanned label. Returns {"trace", "format", "alc", "model", "part"}
    (the matched model fields; "" when the format has none). Raises ScanError."""
    label = (label or "").strip()
    if not label:
        raise ScanError("empty label")
    values = {"ALC": alc, "MODEL": model, "PART": part}
    usable = []
    for fmt in formats or DEFAULT_FORMATS:
        rx = _pattern(fmt, values)
        if rx is None:
            continue
        usable.append(fmt)
        m = rx.fullmatch(label)
        if m and m.group("TRACE").strip():
            return {"trace": m.group("TRACE").strip(), "format": fmt,
                    **{k.lower(): (values[k].strip() if "{" + k + "}" in fmt else "")
                       for k in ("ALC", "MODEL", "PART")}}
    if not usable:
        raise ScanError("the loaded model has no ALC Code or model number to check the label "
                        "against (Model Settings)")
    expect = ", ".join(f"{n} {values[k].strip()}" for k, n in (("ALC", "ALC"), ("MODEL", "model"))
                       if values[k].strip())
    raise ScanError(f"the label does not match the loaded model ({expect}) in any label format "
                    f"({' / '.join(usable)})")
