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

# ---------------------------------------------------------------------------
# Light industrial theme (ISA-101 style "high-performance HMI"): a neutral grey
# work area, white panels and dark text; colour is kept for state and action -
# green OK, red NG / alarm, amber warning / busy, blue for the primary action.
# Pages move to these names one by one; the dark names above stay until all do.
L_BG = "#e3e7eb"            # work area behind the panels
L_SURFACE = "#ffffff"       # panels, cards, dialogs
L_SURFACE_2 = "#f2f4f6"     # card title strips, table headings, idle tiles
L_SUNKEN = "#f7f8fa"        # read-only value boxes
L_BORDER = "#bfc7d0"        # panel and field edges
L_BORDER_2 = "#d8dde3"      # dividers inside a panel
L_TEXT = "#1d2733"          # main text and values
L_TEXT_2 = "#4b5866"        # labels
L_TEXT_3 = "#7d8995"        # hints, units, disabled
L_PRIMARY = "#1565c0"       # primary action / focus / selection
L_PRIMARY_DK = "#0d47a1"
L_SELECT = "#d6e6f8"        # selected row
L_OK = "#2e7d32"
L_NG = "#c62828"
L_WARN = "#b26a00"          # amber text on light ground
L_WARN_BG = "#fff4e0"
L_NEUTRAL_BTN = "#5f6b78"   # secondary buttons
# Navigation frame (header + sidebar): dark slate so the work area stands out
NAV_BG = "#24313f"
NAV_TILE = "#2e3d4e"
NAV_FG = "#d3dbe3"
NAV_DIM = "#93a1b0"


class light:
    """The dark theme's names with light values: a page on the light theme imports
    `from .theme import light as theme`, so theme.PANEL, theme.PASS ... are light."""
    BG = L_BG
    PANEL = L_SURFACE
    BORDER = L_BORDER
    BAND = (L_SURFACE, L_SUNKEN)
    BAND_HOVER = L_SELECT
    HEADER_FG = L_TEXT_2
    TITLE_FG = L_PRIMARY        # section captions / accent bars
    ENTRY_BG = L_SURFACE
    MUTED = L_TEXT_3
    PASS = L_OK
    FAIL = L_NG
    BUSY = L_WARN
    SAVE = L_OK
    UPDATE = L_PRIMARY
    TEST_IDLE = L_TEXT
    FONT = FONT
    TEXT = L_TEXT
    TEXT_2 = L_TEXT_2


def apply_style(root):
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(
        "TCombobox", fieldbackground="#111", background="#111", foreground="white",
        bordercolor="#444", arrowcolor="white", lightcolor="#111", darkcolor="#111", insertcolor="white",
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

    # Light theme counterparts (pages on the light theme use these style names)
    style.configure("L.TCombobox", fieldbackground=L_SURFACE, background=L_SURFACE_2, foreground=L_TEXT,
                    bordercolor=L_BORDER, arrowcolor=L_TEXT_2, lightcolor=L_SURFACE, darkcolor=L_SURFACE,
                    insertcolor=L_TEXT, padding=2)
    style.map("L.TCombobox",
              fieldbackground=[("readonly", L_SURFACE), ("disabled", L_SURFACE_2)],
              selectbackground=[("readonly", L_SURFACE)], selectforeground=[("readonly", L_TEXT)],
              foreground=[("disabled", L_TEXT_3)], bordercolor=[("focus", L_PRIMARY)])
    style.configure("L.Treeview", background=L_SURFACE, fieldbackground=L_SURFACE, foreground=L_TEXT,
                    bordercolor=L_BORDER, rowheight=26, font=(FONT, 10))
    style.configure("L.Treeview.Heading", background=L_SURFACE_2, foreground=L_TEXT_2, bordercolor=L_BORDER,
                    relief="flat", font=(FONT, 10, "bold"))
    style.map("L.Treeview", background=[("selected", L_SELECT)], foreground=[("selected", L_TEXT)])
    style.map("L.Treeview.Heading", background=[("active", L_BORDER_2)])
    style.configure("L.Vertical.TScrollbar", background=L_SURFACE_2, troughcolor=L_BG, bordercolor=L_BORDER,
                    arrowcolor=L_TEXT_2, lightcolor=L_SURFACE_2, darkcolor=L_SURFACE_2)

    # Drop-down list colours.
    root.option_add("*TCombobox*Listbox.background", "#111")
    root.option_add("*TCombobox*Listbox.foreground", "white")
    root.option_add("*TCombobox*Listbox.selectBackground", "#2196f3")
    root.option_add("*TCombobox*Listbox.selectForeground", "white")
