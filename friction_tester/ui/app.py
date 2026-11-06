"""Main window: black header with logo/title/clock, sidebar tiles, content area."""

import datetime
import threading
import tkinter as tk

from . import theme
from .comport_page import ComPortPage
from .login import LoginScreen
from .model_page import ModelSettingsPage
from .test_console import TestConsolePage

NAV_IDLE = ("#111", "white", "#444")        # bg, fg, border
NAV_ACTIVE = ("#d4d4d4", "black", "#f0f0f0")


class App:
    # (icon, caption, page key, header title)
    PAGES = [
        ("🏠", "Test Console", "test_console", "FRICTION TEST MACHINE"),
        ("⚙", "Model\nSettings", "model_settings", "Model Settings"),
        ("🔧", "COM Setting", "comport_settings", "COM Settings"),
    ]

    def __init__(self, root, settings, plc, daq, loadcells, simulator=None):
        self.simulator = simulator       # plc.simulator.SharedCycleSimulator in --simulate mode
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
        self.load_page("test_console")
        threading.Thread(target=self._autoconnect, daemon=True).start()

    def _autoconnect(self):
        """Connect every device that has a saved COM port, in the background.

        Failures are left for the pages to show (Test Console lists anything
        not connected; COM Setting shows each row's state).
        """
        s = self.settings
        jobs = []
        if s["plc"]["port"]:
            jobs.append((self.plc, s["plc"]))
        jobs += [(dev, cfg) for dev, cfg in zip(self.loadcells, s["loadcells"]) if cfg["port"]]
        for dev, cfg in jobs:
            try:
                dev.connect(cfg)
            except Exception:
                pass

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

    # Pages that need a sign-in every time they are opened.
    LOGIN_PAGES = {"model_settings", "comport_settings"}

    def load_page(self, page_name):
        if page_name in self.LOGIN_PAGES:
            self._show_login(page_name)
        else:
            self._open_page(page_name)

    def _show_login(self, page_name):
        """Sign-in screen in the content area; Cancel returns to the page you came from."""
        back = getattr(self, "current_page", "test_console")
        title = next(t for _i, _c, p, t in self.PAGES if p == page_name)
        self._clear_content(page_name, title)
        LoginScreen(self.content, title, on_success=lambda: self._open_page(page_name),
                    on_cancel=lambda: self._open_page(back)).pack(fill="both", expand=True)

    def _clear_content(self, page_name, title):
        self._set_active_nav(page_name)
        for w in self.content.winfo_children():
            w.destroy()
        self.lbl_title.config(text=title)

    def _open_page(self, page_name):
        title = next(t for _i, _c, p, t in self.PAGES if p == page_name)
        self._clear_content(page_name, title)
        self.current_page = page_name

        if page_name == "comport_settings":
            ComPortPage(self.content, self.settings, self.plc, self.daq, self.loadcells).pack(
                fill="both", expand=True, padx=16, pady=12)
        elif page_name == "model_settings":
            ModelSettingsPage(self.content, self.settings).pack(fill="both", expand=True, padx=16, pady=12)
        elif page_name == "test_console":
            TestConsolePage(self.content, self.settings, self.plc, self.loadcells,
                            simulator=self.simulator).pack(
                fill="both", expand=True, padx=12, pady=10)
