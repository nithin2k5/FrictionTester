"""FrictionTester entry point."""

import tkinter as tk
from tkinter import ttk

from friction_tester.config import load_settings
from friction_tester.devices import NIDaq, PLCSerial
from friction_tester.ui import SettingsPage


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Friction Tester")
        self.minsize(900, 560)

        self.settings = load_settings()
        self.plc = PLCSerial()
        self.daq = NIDaq()

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True)
        notebook.add(SettingsPage(notebook, self.settings, self.plc, self.daq), text="Comm Settings")

        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def on_close(self):
        self.plc.disconnect()
        self.daq.disconnect()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
