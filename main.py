"""FrictionTester entry point."""

import tkinter as tk

from friction_tester.config import load_settings
from friction_tester.devices import Loadcell, NIDaq, XGBPlc
from friction_tester.ui import App


def main():
    root = tk.Tk()
    plc, daq = XGBPlc(), NIDaq()
    loadcells = [Loadcell(1), Loadcell(2)]
    App(root, load_settings(), plc, daq, loadcells)

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
