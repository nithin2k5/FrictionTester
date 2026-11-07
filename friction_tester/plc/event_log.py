"""Timestamped log of PLC command requests, echoes, state transitions,
timeouts and result persistence (data/plc_events_YYYYMMDD.log).

Thread-safe; never raises into the caller (a full disk must not stop a test).
The last lines are also kept in memory for the screen.
"""

import collections
import datetime
import os
import threading


class EventLog:
    def __init__(self, folder="data", keep=300, clock=datetime.datetime.now):
        self.folder = folder
        self.clock = clock
        self.lines = collections.deque(maxlen=keep)
        self._lock = threading.Lock()

    def log(self, kind, message="", **fields):
        now = self.clock()
        extra = " ".join(f"{k}={v}" for k, v in fields.items())
        line = f"{now.isoformat(timespec='milliseconds')} {kind:<14} {message}{(' ' + extra) if extra else ''}"
        with self._lock:
            self.lines.append(line)
            if self.folder:
                try:
                    os.makedirs(self.folder, exist_ok=True)
                    path = os.path.join(self.folder, f"plc_events_{now:%Y%m%d}.log")
                    with open(path, "a", encoding="utf-8") as f:
                        f.write(line + "\n")
                except OSError:
                    pass
        return line

    def tail(self, n=20):
        with self._lock:
            return list(self.lines)[-n:]
