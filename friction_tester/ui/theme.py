"""Colours, fonts and ttk styling shared by every page (matches FeederCable)."""

from tkinter import ttk

BG = "black"            # window / content background
PANEL = "#12151b"       # dialog-style panel
BORDER = "#333"
BAND = ("#161b24", "#12151b")   # alternating table rows
BAND_HOVER = "#23324a"          # row under the pointer
HEADER_FG = "#8fa3bf"           # table column headings
TITLE_FG = "#e8a000"            # page title in the app header
ENTRY_BG = "#1c2029"
MUTED = "#666"

PASS = "#4caf50"
FAIL = "#f44336"
BUSY = "#ff9800"
SAVE = "#4caf50"
UPDATE = "#2196f3"
TEST_IDLE = "#e0e0e0"

FONT = "Arial"


def apply_style(root):
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(
        "TCombobox", fieldbackground="#111", background="#111", foreground="white",
        bordercolor="#444", arrowcolor="white", lightcolor="#111", darkcolor="#111",
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", "#111"), ("disabled", "#111")],
        selectbackground=[("readonly", "#2196f3")],
        selectforeground=[("readonly", "white")],
        foreground=[("readonly", "white"), ("disabled", MUTED)],
    )
    # Dark table (Model Settings list)
    style.configure("Dark.Treeview", background="#0f131a", fieldbackground="#0f131a", foreground="white",
                    bordercolor=BORDER, rowheight=26, font=(FONT, 10))
    style.configure("Dark.Treeview.Heading", background="#1c2230", foreground=HEADER_FG,
                    bordercolor=BORDER, relief="flat", font=(FONT, 10, "bold"))
    style.map("Dark.Treeview", background=[("selected", "#2a4a7a")], foreground=[("selected", "white")])
    style.map("Dark.Treeview.Heading", background=[("active", "#252c3b")])

    # Drop-down list colours.
    root.option_add("*TCombobox*Listbox.background", "#111")
    root.option_add("*TCombobox*Listbox.foreground", "white")
    root.option_add("*TCombobox*Listbox.selectBackground", "#2196f3")
    root.option_add("*TCombobox*Listbox.selectForeground", "white")
