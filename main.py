"""FrictionTester entry point."""

import tkinter as tk

from friction_tester.config import load_settings
from friction_tester.devices import IAIServo, Loadcell, NIDaq, XGBPlc
from friction_tester.ui import App


def main():
    root = tk.Tk()
    plc, daq = XGBPlc(), NIDaq()
    loadcells = [Loadcell(1), Loadcell(2)]
    servos = [IAIServo(1), IAIServo(2)]
    App(root, load_settings(), plc, daq, loadcells, servos)

    def on_close():
        plc.disconnect()
        daq.disconnect()
        for dev in loadcells + servos:
            dev.disconnect()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
