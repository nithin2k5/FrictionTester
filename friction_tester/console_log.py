"""Terminal log of what the operator does and what the PLC answers.

setup() is called once from main.py. Everything goes to stdout as
    HH:MM:SS.mmm  CHANNEL  message
CHANNEL is UI (button presses, scans), STATUS (status-bar messages) or PLC
(controller events, also written to data/plc_events_YYYYMMDD.log).
"""

import logging
import sys

ROOT = "friction_tester"


class _ShortName(logging.Formatter):
    def format(self, record):
        record.channel = record.name.rsplit(".", 1)[-1]
        return super().format(record)


def setup(level=logging.INFO):
    stream = sys.stdout
    if stream is None:                  # pythonw: no terminal
        return
    try:
        # Line-buffered so lines appear at once even when stdout is a pipe
        # (Git Bash / mintty); "replace" so ↺ ▶ ■ never raise on a cp1252 console.
        stream.reconfigure(line_buffering=True, errors="replace")
    except (AttributeError, ValueError):
        pass
    handler = logging.StreamHandler(stream)
    handler.setFormatter(_ShortName("%(asctime)s.%(msecs)03d  %(channel)-6s %(message)s", "%H:%M:%S"))
    root = logging.getLogger(ROOT)
    root.addHandler(handler)
    root.setLevel(level)
    root.propagate = False


def channel(name):
    """Logger for one channel, e.g. channel("UI")."""
    return logging.getLogger(f"{ROOT}.{name}")
