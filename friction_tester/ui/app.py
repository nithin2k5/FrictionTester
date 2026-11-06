"""Main window: black header with logo/title/clock, sidebar tiles, content area."""

import datetime
import tkinter as tk

from . import theme
from .comport_page import ComPortPage

NAV_IDLE = ("#111", "white", "#444")        # bg, fg, border
NAV_ACTIVE = ("#d4d4d4", "black", "#f0f0f0")


class App:
    # (icon, caption, page key, header title)
    PAGES = [
        ("🔧", "COM Setting", "comport_settings", "COM Settings"),
    ]

    def __init__(self, root, settings, plc, daq, loadcells):
        self.root = root
        self.settings = settings
        self.plc = plc
        self.daq = daq
        self.loadcells = loadcells

        root.title("Friction Tester")
        root.geometry("1350x860")
        try:
            root.state("zoomed")
        except tk.TclError:
            pass
        root.configure(bg=theme.BG)
        theme.apply_style(root)

        self._build_header()
        self._build_body()
        self.load_page("comport_settings")

    # ---------------------------------------------------------------- header
    def _build_header(self):
        header = tk.Frame(self.root, bg=theme.BG, height=45)
        header.pack(fill="x")
        header.pack_propagate(False)

        logo = tk.Frame(header, bg="white", padx=6, pady=3)
        logo.pack(side="left", padx=10, pady=6)
        tk.Label(logo, text="INFAC", fg="#c00000", bg="white", font=(theme.FONT, 16, "bold")).pack(side="left")
        tk.Label(logo, text=" 주식회사인팩", fg="black", bg="white", font=("Malgun Gothic", 9)).pack(side="left")

        self.lbl_title = tk.Label(header, text="", fg=theme.TITLE_FG, bg=theme.BG, font=(theme.FONT, 15, "bold"))
        self.lbl_title.pack(side="left", padx=25)

        self.lbl_clock = tk.Label(header, text="", fg="white", bg=theme.BG, font=(theme.FONT, 10))
        self.lbl_clock.pack(side="right", padx=15)
        self._tick_clock()

    def _tick_clock(self):
        self.lbl_clock.config(text=datetime.datetime.now().strftime("%d/%m/%Y  %H:%M:%S"))
        self.root.after(1000, self._tick_clock)

    # ------------------------------------------------------------------ body
    def _build_body(self):
        body = tk.Frame(self.root, bg=theme.BG)
        body.pack(fill="both", expand=True)

        sidebar = tk.Frame(body, bg=theme.BG, width=90)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        self.nav_tiles = {}
        for icon, text, page, _title in self.PAGES:
            f = tk.Frame(sidebar, bg="#111", bd=1, relief="solid", height=110,
                         highlightbackground="#444", highlightthickness=1, cursor="hand2")
            f.pack(fill="x", padx=4, pady=3)
            inner = tk.Frame(f, bg="#111", cursor="hand2")
            inner.place(relx=0.5, rely=0.5, anchor="center")
            lbl_icon = tk.Label(inner, text=icon, bg="#111", fg="white", font=(theme.FONT, 18), cursor="hand2")
            lbl_icon.pack()
            lbl_text = tk.Label(inner, text=text, bg="#111", fg="white", font=(theme.FONT, 8), cursor="hand2")
            lbl_text.pack()
            for w in (f, inner, lbl_icon, lbl_text):
                w.bind("<Button-1>", lambda _e, p=page: self.load_page(p))
            self.nav_tiles[page] = (f, inner, lbl_icon, lbl_text)

        self.content = tk.Frame(body, bg=theme.BG)
        self.content.pack(side="left", fill="both", expand=True)

    def _set_active_nav(self, page_name):
        for page, (frame, inner, icon, text) in self.nav_tiles.items():
            bg, fg, border = NAV_ACTIVE if page == page_name else NAV_IDLE
            frame.config(bg=bg, highlightbackground=border)
            inner.config(bg=bg)
            icon.config(bg=bg, fg=fg)
            text.config(bg=bg, fg=fg)

    def load_page(self, page_name):
        self._set_active_nav(page_name)
        for w in self.content.winfo_children():
            w.destroy()
        title = next(t for _i, _c, p, t in self.PAGES if p == page_name)
        self.lbl_title.config(text=title)

        if page_name == "comport_settings":
            ComPortPage(self.content, self.settings, self.plc, self.daq, self.loadcells).pack(
                fill="both", expand=True, padx=20, pady=20)
