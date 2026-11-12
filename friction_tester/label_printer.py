"""Print a model's .prn label template on a Windows printer (raw, no driver rendering).

The .prn file is sent byte for byte through the spooler as a RAW job, so it must
already be in the printer's language (ZPL, TSPL, EPL, ...). Before sending, these
placeholders are replaced if the template contains them (others are left as is):

    {MODEL} {TYPE} {PART_NUMBER} {CUSTOMER} {ALC_CODE}
    {LABEL} {LABEL_SELECT} {LABEL_SHIFT} {VERDICT}
    {DATE} (YYYY-MM-DD) {TIME} (HH:MM:SS) {DATETIME} {YYYYMMDD} {HHMMSS}
"""

import ctypes
import datetime
import os
import sys
from ctypes import wintypes

DEFAULT_PRINTER = "FTM_PRINTER"
PRN_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prn_templates")


def fields(record, model_row=None):
    """Placeholder values for one finished part."""
    row = model_row or {}
    now = datetime.datetime.now()
    s = lambda v: "" if v is None else str(v).strip()
    label_select, label_shift = s(record.get("label_select")), s(record.get("label_shift"))
    return {
        "MODEL": s(record.get("model") or row.get("MM_MODEL")),
        "TYPE": s(record.get("type") or row.get("MM_TYPE")),
        "PART_NUMBER": s(record.get("part_number") or row.get("MM_PART_NUMBER")),
        "CUSTOMER": s(row.get("MM_CUSTOMER_NAME")),
        "ALC_CODE": s(row.get("MM_ALC_CODE")),
        "LABEL": label_select or label_shift,
        "LABEL_SELECT": label_select, "LABEL_SHIFT": label_shift,
        "VERDICT": s(record.get("verdict")),
        "DATE": f"{now:%Y-%m-%d}", "TIME": f"{now:%H:%M:%S}", "DATETIME": f"{now:%Y-%m-%d %H:%M:%S}",
        "YYYYMMDD": f"{now:%Y%m%d}", "HHMMSS": f"{now:%H%M%S}",
    }


def render(data, values):
    """Replace {KEY} placeholders in the template bytes."""
    for key, value in values.items():
        data = data.replace(b"{" + key.encode("ascii") + b"}", value.encode("utf-8"))
    return data


def template_path(name):
    if not name:
        raise ValueError("no PRN template is set for this model (Model Settings)")
    path = name if os.path.isabs(name) else os.path.join(PRN_DIR, name)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"PRN template not found: {path}")
    return path


def print_label(template, record, model_row=None, printer=DEFAULT_PRINTER):
    """Fill the template for this part and send it to the printer. Raises on failure."""
    with open(template_path(template), "rb") as f:
        data = render(f.read(), fields(record, model_row))
    send_raw(printer or DEFAULT_PRINTER, data, f"FTM label {record.get('model') or ''}".strip())


# ------------------------------------------------------------------ Windows spooler (RAW)
class _DOC_INFO_1(ctypes.Structure):
    _fields_ = [("pDocName", wintypes.LPWSTR), ("pOutputFile", wintypes.LPWSTR),
                ("pDatatype", wintypes.LPWSTR)]


def send_raw(printer, data, doc_name="FTM label"):
    if sys.platform != "win32":
        raise OSError("label printing needs the Windows print spooler")
    ws = ctypes.WinDLL("winspool.drv", use_last_error=True)
    ws.OpenPrinterW.argtypes = [wintypes.LPWSTR, ctypes.POINTER(wintypes.HANDLE), ctypes.c_void_p]
    ws.StartDocPrinterW.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(_DOC_INFO_1)]
    ws.StartDocPrinterW.restype = wintypes.DWORD
    ws.WritePrinter.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    for fn in ("StartPagePrinter", "EndPagePrinter", "EndDocPrinter", "ClosePrinter"):
        getattr(ws, fn).argtypes = [wintypes.HANDLE]

    def fail(what):
        raise OSError(f"{what} failed for printer {printer!r}: {ctypes.FormatError(ctypes.get_last_error())}")

    handle = wintypes.HANDLE()
    if not ws.OpenPrinterW(printer, ctypes.byref(handle), None):
        fail("OpenPrinter")
    try:
        doc = _DOC_INFO_1(doc_name, None, "RAW")
        if not ws.StartDocPrinterW(handle, 1, ctypes.byref(doc)):
            fail("StartDocPrinter")
        try:
            if not ws.StartPagePrinter(handle):
                fail("StartPagePrinter")
            buf = ctypes.create_string_buffer(data, len(data))
            written = wintypes.DWORD()
            ok = ws.WritePrinter(handle, buf, len(data), ctypes.byref(written))
            ws.EndPagePrinter(handle)
            if not ok:
                fail("WritePrinter")
            if written.value != len(data):
                raise OSError(f"printer {printer!r} took {written.value} of {len(data)} bytes")
        finally:
            ws.EndDocPrinter(handle)
    finally:
        ws.ClosePrinter(handle)
