"""FrictionTester entry point:  python main.py"""

import atexit
import os
import signal
import sys
import tkinter as tk
from tkinter import messagebox

from friction_tester import console_log, single_instance
from friction_tester.config import load_settings
from friction_tester.devices import Loadcell, NIDaq, XGBPlc
from friction_tester.ui import App


def main():
    console_log.setup()
    if not single_instance.acquire():
        r = tk.Tk()
        r.withdraw()
        messagebox.showerror(
            "Friction Tester",
            "Friction Tester is already running.\n\n"
            "Use the open window (check the taskbar). If no window is visible, an earlier copy is "
            "stuck in the background holding the COM ports: open Task Manager → Details, end "
            "python.exe / pythonw.exe, then start the app again.")
        r.destroy()
        return

    root = tk.Tk()
    plc, daq = XGBPlc(), NIDaq()
    loadcells = [Loadcell(1), Loadcell(2)]
    app = App(root, load_settings(), plc, daq, loadcells)

    # Closing (window X) is App.close(): stop page work, then
    # release PLC, loadcells and NI DAQ, then destroy the window.
    # Backstops so the ports are freed however the process ends:
    atexit.register(app.release_devices)                   # normal interpreter exit / errors
    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):          # Ctrl+C, kill, console window closed
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), lambda *_a: root.after(0, app.close, True))
    _keep_signals_alive(root)

    try:
        root.mainloop()
    finally:
        app.release_devices()
    _end_process()


def _keep_signals_alive(root):
    """Tk's mainloop blocks Python signal handlers; wake the interpreter regularly."""
    root.after(500, _keep_signals_alive, root)


def _end_process():
    """Everything is released by now. A worker thread stuck inside a serial or
    DAQmx driver call can stop Python's normal exit and leave a window-less
    process holding the COM ports, so end the process directly."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    os._exit(0)


if __name__ == "__main__":
    main()
