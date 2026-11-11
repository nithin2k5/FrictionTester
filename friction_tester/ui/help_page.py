"""Help page - the operator manual for the Friction Tester.

Laid out like FeederCable's manual: a contents list with a search box on the
left, a scrolling chapter on the right, Previous / Next underneath. Each
chapter is data - a list of typed blocks - so adding to the manual means
adding entries to CHAPTERS, not writing widget code.

Block types:
    ("h",     text)                 heading inside a chapter
    ("p",     text)                 paragraph
    ("steps", [text, ...])          numbered procedure
    ("bul",   [text, ...])          bullet list
    ("kv",    [(term, text), ...])  definition list
    ("tbl",   [headers], [rows])    table
    ("pre",   text)                 fixed-width diagram
    ("note",  text)                 informational callout
    ("warn",  text)                 warning callout
"""

import tkinter as tk
from tkinter import ttk

from . import theme as T
from .theme import light as theme      # light industrial theme

BG, PANEL, HEADER = theme.BG, theme.PANEL, T.L_SURFACE_2
LINE, TXT, DIM, BODY = theme.BORDER, T.L_TEXT, T.L_TEXT_3, T.L_TEXT_2
ACCENT, BLUE, WARN = theme.TITLE_FG, T.L_PRIMARY, "#e08e00"
ROW_BG = (T.L_SURFACE, T.L_SUNKEN)
CALLOUT_BG = T.L_SUNKEN
CHIP_BG = T.L_SELECT     # step numbers / selected chapter
PLACEHOLDER = "Search the manual…"


CHAPTERS = [
("About This Machine", [
    ("p", "The Friction Tester measures how hard it is to pull and push the inner wire of a gear "
          "cable. A part has two cables - SELECT and SHIFT - and each one has its own servo axis "
          "and its own loadcell. The axis moves the cable end back and forth while the force is "
          "recorded, and the force at three points of the stroke is judged against the model's LOAD limits."),
    ("h", "What is connected"),
    ("kv", [
        ("PLC", "LS XGB (PRINTING PROGRAM OLD). Runs both servo axes; the PC sends it the test "
                "parameters and the PULL / PUSH / home requests."),
        ("Loadcells", "Two KOMEIN AM-50LW indicators on RS232 - Loadcell 1 = SELECT, "
                      "Loadcell 2 = SHIFT. Their readings are the test results."),
        ("NI card", "NI USB-6210. Loadcell and LVDT signals for the graph "
                    "(load against stroke)."),
        ("Database", "MySQL: the models (TBL_MODEL_MASTER) and every test result."),
    ]),
    ("h", "Pages"),
    ("kv", [
        ("Test Console", "Load a model, run tests, see results, the graph and the counters."),
        ("Model Settings", "Create and edit models (sign-in needed)."),
        ("Report", "Look up and export saved test results."),
        ("Admin", "User accounts (sign-in needed)."),
        ("COM Setting", "PLC, loadcell and NI card connections (sign-in needed)."),
        ("Help / Contact", "This manual and who to call."),
    ]),
]),

("Before You Start", [
    ("steps", [
        "Switch on the machine and the PLC, then start the Friction Tester on the PC.",
        "The app connects every device that has a saved COM port. Anything not connected is "
        "listed at the bottom of the Test Console as \"Not connected: …\".",
        "If something is missing, open COM Setting, fix the connection and press Save.",
        "Open the Test Console. Both axes go to origin by themselves once the PLC link is up "
        "and the M-area reads are verified.",
    ]),
    ("note", "Close the app before editing settings.json by hand - the running app writes it "
             "and would overwrite the edit."),
]),

("COM Setting", [
    ("p", "Every field on this page is saved only when you press Save. Leaving the page with "
          "unsaved changes asks first; No keeps you on the page."),
    ("h", "PLC"),
    ("kv", [
        ("COM port", "The PLC's RS-485 adapter (COM13 on this machine)."),
        ("Baud / station", "115200, station 1."),
        ("CONNECT / TEST / READ", "CONNECT opens the port; TEST reads the listed test bits; READ "
                                  "reads the given registers."),
    ]),
    ("h", "Loadcells"),
    ("kv", [
        ("COM port / baud", "The indicator's RS232 port; 9600 unless the indicator was changed (F-41)."),
        ("ID", "Required. Type the ID set in the indicator (F-40): 00 = stream mode, "
               "01-99 = command mode. Loadcell 2 on this machine is 01. CONNECT refuses a blank ID."),
        ("TEST / ZERO", "TEST reads one value; ZERO zeroes the indicator."),
    ]),
    ("h", "NI card"),
    ("kv", [
        ("Device", "The NI USB-6210 (Rescan Ports lists it). Without a device the graph stays empty."),
        ("Wiring", "Differential: channel n uses AIn+ and AI(n+8)-."),
        ("Sensor rows", "Loadcell 1 ai0, Loadcell 2 ai1, LVDT 1 ai2, LVDT 2 ai3. "
                        "Value = (volts - offset_v) x scale, in the row's unit."),
        ("TEST", "Reads the row's pin once and shows volts and the scaled value."),
    ]),
    ("warn", "Set scale and offset_v of all four NI rows before trusting the graph. With scale "
             "1.0 the graph shows volts, not kgf and mm."),
]),

("Model Settings", [
    ("p", "A model holds the part details and one test condition per cable (SELECT CABLE SLOT "
          "and SHIFT CABLE SLOT). Click a model in the list to edit it, NEW for an empty form, "
          "SAVE to store it, DELETE to remove it."),
    ("h", "Condition fields"),
    ("tbl", ["Field", "Used for"], [
        ("Speed (mm/s)", "Test speed and high jog speed; low jog speed is half of it."),
        ("Stroke (mm)", "How far each PULL moves. The start is centred: -Stroke/2."),
        ("Position 1 (mm)", "Where PULL1 / PUSH1 are read, on the - side of the stroke."),
        ("Position 2 (mm)", "Where PULL2 / PUSH2 are read, normally 0 (the middle)."),
        ("Position 3 (mm)", "Where PULL3 / PUSH3 are read, on the + side of the stroke."),
        ("Delay Time (s)", "Not used."),
        ("LOAD min / max (kgf)", "The OK range for every measured value."),
    ]),
    ("h", "Rules a model must follow"),
    ("bul", [
        "Position 1 >= -30 and Position 3 <= +30 (checked on SAVE, with a pop-up).",
        "Position 1-3 must lie inside the stroke, between -Stroke/2 and +Stroke/2 - otherwise "
        "that value cannot be read and the result is INCMP.",
        "Left empty, the positions are -(Stroke/2 - 2), 0 and +(Stroke/2 - 2).",
        "LOAD min must not be above LOAD max.",
        "Type must be AT or MT.",
    ]),
    ("p", "Example: Stroke 22 -> the stroke runs from -11 to +11 mm, Position 1 / 2 / 3 = "
          "-8 / 0 / +8. The axis travel limits are set automatically 2 mm outside the stroke "
          "(-13 / +13)."),
    ("note", "Unsaved edits are protected: leaving the page, pressing NEW or clicking another "
             "model asks first."),
]),

("Running a Test", [
    ("steps", [
        "Test Console: pick the model in the list and press MODEL CHANGE (or double-click it). "
        "The cable panels fill with its conditions and the PLC parameters are sent.",
        "Set the production target if it is empty.",
        "Press RUN. If an axis is not at origin it is homed first - the message says "
        "\"Waiting for … to reach origin\" and the test starts by itself when both are home.",
        "AT: the SELECT cable is tested. The step strip then says \"press RUN for the SHIFT "
        "routine\" - press RUN again for the SHIFT cable.",
        "When both cables are done the part is judged OK or NG, saved and counted.",
    ]),
    ("kv", [
        ("STOP", "Cancels the running test or a waiting RUN and releases the PC requests."),
        ("ORIGIN", "Homes the axes."),
        ("Manual", "Opens the manual operation view (see Manual Operation)."),
        ("Save Conditions", "Keeps edits made to the conditions on the console."),
        ("Clear Results", "Clears the result boxes."),
    ]),
    ("note", "A SELECT result of INCOMPLETE ends the part - SHIFT is not tested."),
    ("warn", "MT models (both cables together) are refused until running both axes at the same "
             "time has been verified on this machine."),
]),

("The Test Cycle", [
    ("p", "For each cable the PLC moves the axis like this (Stroke 10; the travel limits are set "
          "automatically, 2 mm outside the stroke):"),
    ("pre", "  - limit   start          home            PULL end   + limit\n"
            "    -7       -5              0                +6          +7\n"
            "             |<============= PULL ============>|\n"
            "             |<============= PUSH =============|\n"
            "             3 x PULL + PUSH   ->  PULL1-3 / PUSH1-3"),
    ("steps", [
        "Home: both axes at origin.",
        "Measurement start: the axis moves to -Stroke/2.",
        "PULL: from the start to +Stroke/2 + 1 mm while the force is recorded.",
        "PUSH: back to the start while the force is recorded.",
        "PULL and PUSH are repeated three times.",
    ]),
    ("p", "A stroke ends when the PLC sets its completion flag. Every step is checked: the PLC "
          "must accept the request, start the sequence and finish it in time, or the cable "
          "result is INCOMPLETE with the reason."),
]),

("Results and Judgement", [
    ("kv", [
        ("PULL1-3 / PUSH1-3", "The force (sign ignored) of the last stroke at the model's Position "
                              "1, 2 and 3, from the serial loadcell. The positions are read on the "
                              "graph's stroke axis (-Stroke/2 to +Stroke/2), e.g. -8 / 0 / +8 for "
                              "Stroke 22."),
        ("Mean", "The average of the three PULL / three PUSH values."),
        ("Max", "The largest of all six."),
        ("OK", "All six values inside LOAD min-max (limits included)."),
        ("NG", "At least one value outside the limits."),
        ("INCMP", "INCOMPLETE - no judgement: too few valid readings, a failed step, or a missing "
                  "limit. The reason is shown under the panel. Not counted."),
    ]),
    ("p", "Every part is written to the database (and kept locally until it is). RUN is refused "
          "while a finished part is not saved yet; saving is retried every 15 s."),
]),

("The Graph", [
    ("p", "Each cable panel draws Load against Stroke live while the strokes run: PULL and PUSH "
          "share a graph, and each new stroke starts a fresh one. The graph uses the NI card "
          "(loadcell channel and LVDT); the results always use the serial loadcells."),
    ("kv", [
        ("+ / -", "Zoom in / out."),
        ("Fit", "Zoom to the drawn data."),
        ("◄ ► ▲ ▼", "Pan."),
    ]),
    ("note", "If the NI card cannot be read the graph shows no points for that stroke and the "
             "status line says why. The test result is not affected."),
]),

("Manual Operation", [
    ("p", "Manual on the Test Console opens the manual view for both cables; Return goes back."),
    ("kv", [
        ("POSITION SET", "Left / right limit. An edited value is sent with the model's other "
                         "PLC parameters (not saved to the model)."),
        ("JOG CONTROL", "Jog speeds and hold-to-jog buttons: the axis moves while the button is "
                        "held and stops when it is released."),
        ("POSITION MOVE", "PULL / PUSH run only inside a test."),
        ("Status lamps", "Servo, alarm and the other actuator inputs."),
        ("Cylinders", "Driven from the machine's touch panel, not from the PC."),
    ]),
]),

("Report", [
    ("p", "Filter the saved results, see the part summary, select a row for its readings, and "
          "export to CSV."),
]),

("Messages and What to Do", [
    ("tbl", ["Message", "What to do"], [
        ("Not connected: …", "Connect the listed device on COM Setting."),
        ("M-area reads not verified …", "The PLC's internal bits cannot be read, so nothing will "
                                        "move. Check the PLC link; call support if it stays."),
        ("Parameters not confirmed for …", "Press MODEL CHANGE again; check the database is "
                                           "reachable."),
        ("PLC parameters for … not valid: targets outside the limits",
         "The travel limits are worked out from the Stroke, so this should not appear - call "
         "support."),
        ("Position 1 must not be below -30 / Position 3 must not be above 30",
         "Enter a value inside the range."),
        ("Waiting for … to reach origin", "Normal - the test starts when both axes are home."),
        ("loadcell L2 is not connected", "Connect Loadcell 2 on COM Setting (with its ID)."),
        ("Enter ID 00-99", "Type the indicator ID (F-40) before CONNECT."),
        ("… graph: NI card … points not drawn", "Select the NI device on COM Setting; check the "
                                                "sensor rows."),
        ("PC request bits are ON in the PLC", "Inspect the machine, then press STOP."),
        ("FAULT latched: …", "Inspect the machine, then press STOP to acknowledge."),
        ("RUN blocked: … not saved yet", "Wait for the database; the part is retried every 15 s."),
    ]),
]),
]


def _chapter_text(name, blocks):
    """Everything in a chapter as one lowercase string, for searching."""
    parts = [name]

    def add(x):
        if isinstance(x, str):
            parts.append(x)
        elif isinstance(x, (list, tuple)):
            for y in x:
                add(y)

    for b in blocks:
        add(list(b[1:]))
    return " ".join(parts).lower()


class HelpPage(tk.Frame):
    """Two-pane manual: contents + search on the left, the chapter on the right."""

    def __init__(self, master):
        super().__init__(master, bg=BG)
        self.current = 0
        self.toc_items = []
        self.wrapped = []        # [(label, indent)] re-wrapped when the pane width changes
        self._build()
        self._build_toc()
        self.select(0)

    # ------------------------------------------------------------------ layout
    def _build(self):
        head = tk.Frame(self, bg=BG)
        head.pack(fill="x", pady=(0, 8))
        tk.Label(head, text="Operator Manual", bg=BG, fg=TXT, font=(theme.FONT, 17, "bold")).pack(side="left")
        tk.Label(head, text="Friction Tester", bg=BG, fg=DIM, font=(theme.FONT, 10)).pack(
            side="left", padx=(12, 0), pady=(8, 0))

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True)

        toc_outer = tk.Frame(body, bg=LINE, padx=1, pady=1, width=290)
        toc_outer.pack(side="left", fill="y")
        toc_outer.pack_propagate(False)
        toc_head = tk.Frame(toc_outer, bg=HEADER)
        toc_head.pack(fill="x")
        tk.Label(toc_head, text="Contents", bg=HEADER, fg=TXT, font=(theme.FONT, 12, "bold"),
                 padx=12, pady=8).pack(side="left")
        toc_body = tk.Frame(toc_outer, bg=PANEL)
        toc_body.pack(fill="both", expand=True)
        wrap = tk.Frame(toc_body, bg=PANEL, padx=8, pady=8)
        wrap.pack(fill="x")
        self.search = tk.Entry(wrap, bg=T.L_SURFACE, fg=DIM, font=(theme.FONT, 11), bd=0, relief="flat",
                               insertbackground=T.L_TEXT, highlightbackground=LINE, highlightcolor=ACCENT,
                               highlightthickness=1)
        self.search.pack(fill="x", ipady=4)
        self.search.insert(0, PLACEHOLDER)
        self.search.bind("<FocusIn>", self._focus_search)
        self.search.bind("<FocusOut>", self._blur_search)
        self.search.bind("<KeyRelease>", lambda _e: self._build_toc(self.search_term()))
        self.toc_list = tk.Frame(toc_body, bg=PANEL)
        self.toc_list.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        page = tk.Frame(body, bg=LINE, padx=1, pady=1)
        page.pack(side="left", fill="both", expand=True, padx=(6, 0))
        page_head = tk.Frame(page, bg=HEADER)
        page_head.pack(fill="x")
        self.title_lbl = tk.Label(page_head, text="", bg=HEADER, fg=TXT, font=(theme.FONT, 13, "bold"),
                                  padx=12, pady=8)
        self.title_lbl.pack(side="left")
        self.pos_lbl = tk.Label(page_head, text="", bg=HEADER, fg=DIM, font=(theme.FONT, 10))
        self.pos_lbl.pack(side="right", padx=12)

        foot = tk.Frame(page, bg=HEADER)
        foot.pack(side="bottom", fill="x")
        for text, delta, side in (("‹  Previous", -1, "left"), ("Next  ›", 1, "right")):
            tk.Button(foot, text=text, bg=T.L_NEUTRAL_BTN, fg="white", font=(theme.FONT, 11, "bold"), bd=0, padx=14,
                      pady=6, cursor="hand2", activebackground="#4b5560", activeforeground="white",
                      command=lambda d=delta: self.step(d)).pack(side=side, padx=10, pady=6)

        host = tk.Frame(page, bg=PANEL)
        host.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(host, bg=PANEL, highlightthickness=0, bd=0)
        sb = ttk.Scrollbar(host, orient="vertical", command=self.canvas.yview, style="L.Vertical.TScrollbar")
        self.canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=PANEL)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self._rewrap(e.width))
        # Wheel scrolls this page only while the pointer is over it.
        self.canvas.bind("<Enter>", lambda _e: self.canvas.bind_all("<MouseWheel>", self._wheel))
        self.canvas.bind("<Leave>", lambda _e: self.canvas.unbind_all("<MouseWheel>"))
        self.bind("<Destroy>", lambda e: e.widget is self and self._unbind_wheel())

    def _unbind_wheel(self):
        try:
            self.canvas.unbind_all("<MouseWheel>")
        except tk.TclError:
            pass

    def _wheel(self, e):
        self.canvas.yview_scroll(-1 * int(e.delta / 120), "units")

    def _rewrap(self, width):
        self.canvas.itemconfig(self._win, width=width)
        for lbl, indent in self.wrapped:
            try:
                lbl.config(wraplength=max(200, width - 48 - indent))
            except tk.TclError:
                pass

    # ------------------------------------------------------------------ blocks
    def _text(self, parent, text, fg=BODY, font=None, indent=0, pady=(0, 0)):
        lbl = tk.Label(parent, text=text, bg=parent.cget("bg"), fg=fg, font=font or (theme.FONT, 12),
                       justify="left", anchor="nw")
        lbl.pack(fill="x", padx=(indent, 0), pady=pady)
        self.wrapped.append((lbl, indent))
        return lbl

    def _row_label(self, parent, marker, text, marker_kw, indent):
        row = tk.Frame(parent, bg=PANEL)
        row.pack(fill="x", pady=2)
        tk.Label(row, text=marker, **marker_kw).pack(side="left", anchor="n")
        lbl = tk.Label(row, text=text, bg=PANEL, fg=BODY, font=(theme.FONT, 12), justify="left", anchor="nw")
        lbl.pack(side="left", fill="x", expand=True, padx=(10, 0))
        self.wrapped.append((lbl, indent))

    def _callout(self, parent, text, accent, tag):
        wrap = tk.Frame(parent, bg=accent)
        wrap.pack(fill="x", pady=(10, 4))
        body = tk.Frame(wrap, bg=CALLOUT_BG)
        body.pack(fill="x", padx=(3, 0))
        tk.Label(body, text=tag, bg=CALLOUT_BG, fg=accent, font=(theme.FONT, 9, "bold")).pack(
            anchor="w", padx=12, pady=(8, 0))
        lbl = tk.Label(body, text=text, bg=CALLOUT_BG, fg=TXT, font=(theme.FONT, 12), justify="left", anchor="nw")
        lbl.pack(fill="x", padx=12, pady=(2, 10))
        self.wrapped.append((lbl, 30))

    def _table(self, parent, headers, rows):
        outer = tk.Frame(parent, bg=LINE)
        outer.pack(fill="x", pady=(8, 6))
        for c in range(len(headers)):
            outer.columnconfigure(c, weight=1 if c else 0)
        for c, h in enumerate(headers):
            tk.Label(outer, text=h, bg=HEADER, fg=TXT, font=(theme.FONT, 11, "bold"), anchor="w", padx=10,
                     pady=6).grid(row=0, column=c, sticky="nsew", padx=1, pady=1)
        for r, row in enumerate(rows, start=1):
            for c, cell in enumerate(row):
                lbl = tk.Label(outer, text=cell, bg=ROW_BG[r % 2], fg=TXT if c == 0 else DIM,
                               font=(theme.FONT, 11, "bold") if c == 0 else (theme.FONT, 11),
                               anchor="nw", justify="left", padx=10, pady=6,
                               wraplength=300 if c == 0 else 0)
                lbl.grid(row=r, column=c, sticky="nsew", padx=1, pady=1)
                if c:
                    self.wrapped.append((lbl, 340))

    def _render(self, idx):
        for w in self.inner.winfo_children():
            w.destroy()
        self.wrapped.clear()
        name, blocks = CHAPTERS[idx]
        self.title_lbl.config(text=name)
        self.pos_lbl.config(text=f"Chapter {idx + 1} of {len(CHAPTERS)}")
        pad = tk.Frame(self.inner, bg=PANEL, padx=20, pady=16)
        pad.pack(fill="both", expand=True)
        for block in blocks:
            kind = block[0]
            if kind == "h":
                self._text(pad, block[1], fg=ACCENT, font=(theme.FONT, 13, "bold"), pady=(16, 5))
            elif kind == "p":
                self._text(pad, block[1], pady=(0, 6))
            elif kind == "steps":
                for i, s in enumerate(block[1], start=1):
                    self._row_label(pad, str(i), s, dict(bg=CHIP_BG, fg=ACCENT, font=(theme.FONT, 11, "bold"),
                                                         width=3, pady=2), 70)
            elif kind == "bul":
                for s in block[1]:
                    self._row_label(pad, "•", s, dict(bg=PANEL, fg=ACCENT, font=(theme.FONT, 13, "bold")), 60)
            elif kind == "kv":
                for term, desc in block[1]:
                    row = tk.Frame(pad, bg=PANEL)
                    row.pack(fill="x", pady=3)
                    tk.Label(row, text=term, bg=PANEL, fg=BLUE, font=(theme.FONT, 11, "bold"), width=19,
                             anchor="nw", justify="left", wraplength=175).pack(side="left", anchor="n")
                    lbl = tk.Label(row, text=desc, bg=PANEL, fg=BODY, font=(theme.FONT, 12), justify="left",
                                   anchor="nw")
                    lbl.pack(side="left", fill="x", expand=True, padx=(10, 0))
                    self.wrapped.append((lbl, 230))
            elif kind == "tbl":
                self._table(pad, block[1], block[2])
            elif kind == "pre":
                tk.Label(pad, text=block[1], bg=T.L_SUNKEN, fg=TXT, font=("Consolas", 11), justify="left",
                         anchor="w", padx=12, pady=10).pack(fill="x", pady=(6, 8))
            elif kind == "note":
                self._callout(pad, block[1], BLUE, "NOTE")
            elif kind == "warn":
                self._callout(pad, block[1], WARN, "WARNING")
        self.canvas.yview_moveto(0)
        self.canvas.update_idletasks()
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self._rewrap(self.canvas.winfo_width())

    # ---------------------------------------------------------------- contents
    def search_term(self):
        term = self.search.get()
        return "" if term == PLACEHOLDER else term

    def _focus_search(self, _e=None):
        if self.search.get() == PLACEHOLDER:
            self.search.delete(0, "end")
            self.search.config(fg=TXT)

    def _blur_search(self, _e=None):
        if not self.search.get().strip():
            self.search.delete(0, "end")
            self.search.insert(0, PLACEHOLDER)
            self.search.config(fg=DIM)

    def _build_toc(self, match=""):
        for w in self.toc_list.winfo_children():
            w.destroy()
        self.toc_items.clear()
        needle = match.strip().lower()
        for i, (name, blocks) in enumerate(CHAPTERS):
            if needle and needle not in _chapter_text(name, blocks):
                continue
            lbl = tk.Label(self.toc_list, text=f"{i + 1}.  {name}", bg=PANEL, fg=DIM, font=(theme.FONT, 11),
                           anchor="w", padx=10, pady=7, cursor="hand2")
            lbl.pack(fill="x", pady=1)
            lbl.bind("<Button-1>", lambda _e, k=i: self.select(k))
            self.toc_items.append((i, lbl))
        if needle and not self.toc_items:
            tk.Label(self.toc_list, text="Nothing found", bg=PANEL, fg=T.L_TEXT_3, font=(theme.FONT, 11, "italic"),
                     pady=10).pack(fill="x")
        self._highlight()

    def _highlight(self):
        for i, lbl in self.toc_items:
            on = i == self.current
            lbl.config(bg=CHIP_BG if on else PANEL, fg=ACCENT if on else DIM,
                       font=(theme.FONT, 11, "bold") if on else (theme.FONT, 11))

    def select(self, idx):
        self.current = idx
        self._highlight()
        self._render(idx)

    def step(self, delta):
        nxt = self.current + delta
        if 0 <= nxt < len(CHAPTERS):
            self.select(nxt)
