"""Contact page - who to call about the Friction Tester (details as in FeederCable)."""

import tkinter as tk

from . import theme

CARD_BG = theme.PANEL
HEAD_BG = "#171c25"
LABEL_FG = "#8a8a8a"
VALUE_FG = "#e8e8e8"

SECTIONS = [
    ("Support", "When the machine or the app needs help", [
        ("Name", "Venkaiah Alla"),
        ("Company", "Nice Computer Education & Software Solutions"),
        ("Address", "8-30-32/2, Revenue ward No.8, Vaikuntapuram, Kavali, "
                    "Sri Potti Sriramulu Nellore, Andhra Pradesh, 525201"),
        ("Phone", "+91 93467 40314"),
        ("Email", "niceavr@gmail.com"),
    ]),
    ("Developer", "Software questions and changes", [
        ("Email", "koppisettinithin.dev@gmail.com"),
        ("Phone", "+91 93982 25082"),
    ]),
    ("Application", "", [
        ("Application", "Friction Tester"),
        ("PLC program", "LS XGB - PRINTING PROGRAM OLD (PC word interface)"),
        ("Platform", "Windows / Python Tkinter"),
    ]),
]


class ContactPage(tk.Frame):
    def __init__(self, master):
        super().__init__(master, bg=theme.BG)
        tk.Label(self, text="Contact Information", bg=theme.BG, fg="white",
                 font=(theme.FONT, 17, "bold")).pack(anchor="w", pady=(0, 12))
        for title, subtitle, rows in SECTIONS:
            self._card(title, subtitle, rows)

    def _card(self, title, subtitle, rows):
        card = tk.Frame(self, bg=CARD_BG, highlightbackground=theme.BORDER, highlightthickness=1)
        card.pack(fill="x", pady=(0, 12))
        head = tk.Frame(card, bg=HEAD_BG)
        head.pack(fill="x")
        tk.Frame(head, bg=theme.TITLE_FG, width=4).pack(side="left", fill="y")
        tk.Label(head, text=title, bg=HEAD_BG, fg="white", font=(theme.FONT, 12, "bold")).pack(
            side="left", padx=10, pady=7)
        if subtitle:
            tk.Label(head, text=subtitle, bg=HEAD_BG, fg=LABEL_FG, font=(theme.FONT, 9)).pack(side="left")
        body = tk.Frame(card, bg=CARD_BG)
        body.pack(fill="x", padx=16, pady=10)
        for label, value in rows:
            row = tk.Frame(body, bg=CARD_BG)
            row.pack(fill="x", pady=3)
            tk.Label(row, text=label, bg=CARD_BG, fg=LABEL_FG, font=(theme.FONT, 10, "bold"), width=14,
                     anchor="w").pack(side="left", anchor="n")
            # Read-only entry-like text so a number or address can be selected and copied.
            val = tk.Text(row, bg=CARD_BG, fg=VALUE_FG, font=(theme.FONT, 11), bd=0, highlightthickness=0,
                          height=1, width=1, wrap="word", cursor="xterm", selectbackground="#2a4a7a")
            val.insert("1.0", value)
            val.pack(side="left", fill="x", expand=True, padx=(8, 0))
            val.bind("<Configure>", lambda e, t=val: self._fit(t))
            val.config(state="disabled")

    @staticmethod
    def _fit(text):
        """Grow a value box to its wrapped line count (the address wraps on narrow screens)."""
        try:
            lines = text.count("1.0", "end", "displaylines")[0]
            text.config(height=max(1, lines))
        except (tk.TclError, TypeError):
            pass
