"""FrictionTester entry point.

    python main.py              normal start
    python main.py --simulate   Test Console runs against the shared-cycle PLC
                                simulator (no PLC needed; nothing is written to
                                a real PLC)
"""

import argparse
import tkinter as tk

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

    root = tk.Tk()
    plc, daq = XGBPlc(), NIDaq()
    loadcells = [Loadcell(1), Loadcell(2)]
    App(root, load_settings(), plc, daq, loadcells, simulator=simulator)
    if simulator is not None:
        root.title("Friction Tester — SIMULATION")

    def on_close():
        plc.disconnect()
        daq.disconnect()
        for lc in loadcells:
            lc.disconnect()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
