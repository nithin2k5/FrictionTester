"""FrictionTester entry point.

    python main.py              normal start
    python main.py --simulate   Test Console runs against the shared-cycle PLC
                                simulator (no PLC needed; nothing is written to
                                a real PLC)
"""

import argparse
import tkinter as tk
from tkinter import messagebox

from friction_tester import single_instance

from friction_tester.config import load_settings
from friction_tester.devices import Loadcell, NIDaq, XGBPlc
from friction_tester.ui import App


def main():
    parser = argparse.ArgumentParser(description="Friction Tester")
    parser.add_argument("--simulate", action="store_true",
                        help="use the shared-cycle PLC simulator for the Test Console")
    args = parser.parse_args()

    simulator = None
    if args.simulate:
        from friction_tester.plc.simulator import SharedCycleSimulator
        simulator = SharedCycleSimulator()

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
    App(root, load_settings(), plc, daq, loadcells, simulator=simulator)
    if simulator is not None:
        root.title("Friction Tester — SIMULATION")

    def on_close():
        # Release every port even if one device fails to close, and always
        # end the process so no hidden copy keeps COM ports open.
        for dev in (plc, daq, *loadcells):
            try:
                dev.disconnect()
            except Exception:
                pass
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
