"""Main window: black header with logo/title/clock, sidebar tiles, content area."""

import datetime
import threading
import time
import tkinter as tk
from tkinter import messagebox

from . import theme
from .comport_page import ComPortPage
from .contact_page import ContactPage
from .help_page import HelpPage
from .login import LoginScreen
from .model_page import ModelSettingsPage
from .report_page import ReportPage
from .admin_page import AdminPage
from .test_console import TestConsolePage

NAV_IDLE = ("#111", "white", "#444")        # bg, fg, border
NAV_ACTIVE = ("#d4d4d4", "black", "#f0f0f0")


class App:
    # (icon, caption, page key, header title)
    PAGES = [
        ("🏠", "Test Console", "test_console", "FRICTION TEST MACHINE"),
        ("⚙", "Model\nSettings", "model_settings", "Model Settings"),
        ("📋", "Report", "report", "Test Results"),
        ("👤", "Admin", "admin", "Admin"),
        ("🔧", "COM Setting", "comport_settings", "COM Settings"),
        ("❓", "Help", "help", "Help"),
        ("☎", "Contact", "contact", "Contact"),
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

        self._closing = False
        self._autoconnect_thread = None
        self.page = None                  # the page widget currently shown

        self._build_header()
        self._build_body()
        self.load_page("test_console")
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.bind("<<AppExit>>", lambda _e: self.close(confirmed=True))
        self._autoconnect_thread = threading.Thread(target=self._autoconnect, daemon=True)
        self._autoconnect_thread.start()

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
            if self._closing:
                return
            try:
                dev.connect(cfg)
            except Exception:
                pass
            if self._closing:             # closed while this one was opening
                try:
                    dev.disconnect()
                except Exception:
                    pass

    # --------------------------------------------------------------- closing
    def close(self, confirmed=False):
        """Close the app, releasing everything in order:

        1. ask first if a part is being tested (window X; EXIT has asked already)
        2. stop page background work (PLC monitor, live polling, test flow)
        3. wait briefly for worker threads to finish their current read
        4. close PLC, loadcells and NI DAQ (each on its own, with a time limit,
           so one stuck driver can't keep the others open)
        5. destroy the window

        Nothing is written to the PLC; a running machine cycle is left to the machine.
        """
        if self._closing:
            return
        page = self.page if self.page is not None and self.page.winfo_exists() else None
        busy = getattr(page, "test_in_progress", lambda: None)()
        if busy and not confirmed and not messagebox.askyesno(
                "Close", f"{busy}.\n\nClose the Friction Tester anyway? The part will not be recorded.",
                icon="warning", parent=self.root):
            return
        if not confirmed and not self.confirm_leave():
            return
        self._closing = True
        self.lbl_title.config(text="Closing…")
        self.root.update_idletasks()

        threads = list(getattr(page, "shutdown", lambda: [])() or [])
        if self._autoconnect_thread:
            threads.append(self._autoconnect_thread)
        deadline = time.monotonic() + 2.0
        for t in threads:
            t.join(max(0.0, deadline - time.monotonic()))
        # A part's result rows still going to TBL_TEST_RESULTS: give them time to land.
        saves = getattr(page, "pending_saves", lambda: [])()
        deadline = time.monotonic() + 10.0
        for t in saves:
            t.join(max(0.0, deadline - time.monotonic()))

        self.release_devices()
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def release_devices(self, limit=3.0):
        """Disconnect every device; safe to call more than once."""
        if getattr(self, "_released", False):
            return
        self._released = True
        devices = [self.plc, *self.loadcells, self.daq]
        workers = []
        for dev in devices:
            t = threading.Thread(target=self._safe_disconnect, args=(dev,), daemon=True)
            t.start()
            workers.append(t)
        deadline = time.monotonic() + limit
        for t in workers:
            t.join(max(0.0, deadline - time.monotonic()))

    @staticmethod
    def _safe_disconnect(dev):
        try:
            dev.disconnect()
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
            f = tk.Frame(sidebar, bg="#111", bd=1, relief="solid", height=88,
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
    LOGIN_PAGES = {"admin", "model_settings", "comport_settings"}

    def confirm_leave(self):
        """Before leaving a page with unsaved edits (COM Port, Model Settings): ask first."""
        page = self.page if self.page is not None and self.page.winfo_exists() else None
        if page is None or not getattr(page, "unsaved_changes", lambda: False)():
            return True
        return messagebox.askyesno(
            "Settings not saved",
            f"{getattr(page, 'UNSAVED_TEXT', 'The settings are not saved.')}\n\n"
            "Are you sure you want to leave this page? "
            "The changes will be lost.", icon="warning", default="no", parent=self.root)

    def load_page(self, page_name):
        if not self.confirm_leave():
            return
        if page_name in self.LOGIN_PAGES:
            self._show_login(page_name)
        else:
            self._open_page(page_name)

    def _show_login(self, page_name):
        """Sign-in screen in the content area; Cancel returns to the page you came from."""
        back = getattr(self, "current_page", "test_console")
        title = next(t for _i, _c, p, t in self.PAGES if p == page_name)
        self._clear_content(page_name, title)
        LoginScreen(self.content, title, page=page_name, on_success=lambda: self._open_page(page_name),
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
            self.page = ComPortPage(self.content, self.settings, self.plc, self.daq, self.loadcells)
            self.page.pack(fill="both", expand=True, padx=16, pady=12)
        elif page_name == "admin":
            self.page = AdminPage(self.content)
            self.page.pack(fill="both", expand=True, padx=16, pady=12)
        elif page_name == "report":
            self.page = ReportPage(self.content, self.settings)
            self.page.pack(fill="both", expand=True, padx=16, pady=12)
        elif page_name == "model_settings":
            self.page = ModelSettingsPage(self.content, self.settings)
            self.page.pack(fill="both", expand=True, padx=16, pady=12)
        elif page_name == "help":
            self.page = HelpPage(self.content)
            self.page.pack(fill="both", expand=True, padx=16, pady=12)
        elif page_name == "contact":
            self.page = ContactPage(self.content)
            self.page.pack(fill="both", expand=True, padx=16, pady=12)
        elif page_name == "test_console":
            self.page = TestConsolePage(self.content, self.settings, self.plc, self.loadcells, daq=self.daq)
            self.page.pack(fill="both", expand=True, padx=12, pady=10)
