"""Colours, fonts and ttk styling shared by every page.

Light industrial theme (ISA-101 style "high-performance HMI"): a neutral grey
work area, white panels and dark text; colour is kept for state and action -
green OK, red NG / alarm, amber warning / busy, blue for the primary action.
The header and sidebar are a dark slate navigation frame around the work area.
"""

from tkinter import ttk

FONT = "Arial"

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

# Role names used by the pages (via `from .theme import light as theme`)
BG = L_BG                   # page background
PANEL = L_SURFACE           # panel / card
BORDER = L_BORDER
BAND = (L_SURFACE, L_SUNKEN)    # alternating table rows
BAND_HOVER = L_SELECT
HEADER_FG = L_TEXT_2        # table column headings
TITLE_FG = L_PRIMARY        # section captions / accent bars
ENTRY_BG = L_SURFACE
MUTED = L_TEXT_3
PASS = L_OK
FAIL = L_NG
BUSY = L_WARN
SAVE = L_OK
UPDATE = L_PRIMARY
TEST_IDLE = L_TEXT
TEXT = L_TEXT
TEXT_2 = L_TEXT_2


class light:
    """The role names above as a namespace: pages import `from .theme import light as theme`."""
    BG, PANEL, BORDER, BAND, BAND_HOVER = BG, PANEL, BORDER, BAND, BAND_HOVER
    HEADER_FG, TITLE_FG, ENTRY_BG, MUTED = HEADER_FG, TITLE_FG, ENTRY_BG, MUTED
    PASS, FAIL, BUSY, SAVE, UPDATE, TEST_IDLE = PASS, FAIL, BUSY, SAVE, UPDATE, TEST_IDLE
    FONT, TEXT, TEXT_2 = FONT, TEXT, TEXT_2


def apply_style(root):
    style = ttk.Style(root)
    style.theme_use("clam")
    # Drop-downs (the default and L.TCombobox look the same)
    for name in ("TCombobox", "L.TCombobox"):
        style.configure(name, fieldbackground=L_SURFACE, background=L_SURFACE_2, foreground=L_TEXT,
                        bordercolor=L_BORDER, arrowcolor=L_TEXT_2, lightcolor=L_SURFACE, darkcolor=L_SURFACE,
                        insertcolor=L_TEXT, padding=2)
        style.map(name,
                  fieldbackground=[("readonly", L_SURFACE), ("disabled", L_SURFACE_2)],
                  selectbackground=[("readonly", L_SURFACE)], selectforeground=[("readonly", L_TEXT)],
                  foreground=[("disabled", L_TEXT_3)], bordercolor=[("focus", L_PRIMARY)])
    # Tables
    style.configure("L.Treeview", background=L_SURFACE, fieldbackground=L_SURFACE, foreground=L_TEXT,
                    bordercolor=L_BORDER, rowheight=26, font=(FONT, 10))
    style.configure("L.Treeview.Heading", background=L_SURFACE_2, foreground=L_TEXT_2, bordercolor=L_BORDER,
                    relief="flat", font=(FONT, 10, "bold"))
    style.map("L.Treeview", background=[("selected", L_SELECT)], foreground=[("selected", L_TEXT)])
    style.map("L.Treeview.Heading", background=[("active", L_BORDER_2)])
    # Scrollbars (the defaults and the L. names look the same)
    for name in ("Vertical.TScrollbar", "Horizontal.TScrollbar", "L.Vertical.TScrollbar", "L.Horizontal.TScrollbar"):
        style.configure(name, background=L_SURFACE_2, troughcolor=L_BG, bordercolor=L_BORDER,
                        arrowcolor=L_TEXT_2, lightcolor=L_SURFACE_2, darkcolor=L_SURFACE_2)

    # Drop-down list colours
    root.option_add("*TCombobox*Listbox.background", L_SURFACE)
    root.option_add("*TCombobox*Listbox.foreground", L_TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", L_SELECT)
    root.option_add("*TCombobox*Listbox.selectForeground", L_TEXT)
