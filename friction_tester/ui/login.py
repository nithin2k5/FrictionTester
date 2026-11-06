"""Sign-in screen shown in the content area before a protected page opens."""

import tkinter as tk

from . import theme
from .. import auth

CARD_BG = "#12161e"
CARD_EDGE = "#262d3a"
FIELD_BG = "#0b0e14"
FIELD_EDGE = "#2a3242"
FOCUS = theme.UPDATE
LABEL_FG = "#8b95a7"


class LoginScreen(tk.Frame):
    """A centred sign-in card. Calls on_success() after a valid login,
    on_cancel() if the operator backs out."""

    def __init__(self, master, page_title, on_success, on_cancel, page=None):
        super().__init__(master, bg=theme.BG)
        self.page = page or page_title            # recorded in the login history
        self.on_success = on_success
        self.on_cancel = on_cancel

        card = tk.Frame(self, bg=CARD_BG, highlightbackground=CARD_EDGE, highlightthickness=1)
        card.place(relx=0.5, rely=0.45, anchor="center")
        tk.Frame(card, bg=theme.TITLE_FG, height=4).pack(fill="x")          # accent bar

        inner = tk.Frame(card, bg=CARD_BG)
        inner.pack(padx=44, pady=(30, 28))

        # Brand + heading
        logo = tk.Frame(inner, bg="white", padx=8, pady=3)
        logo.pack(anchor="w")
        tk.Label(logo, text="INFAC", fg="#c00000", bg="white", font=(theme.FONT, 15, "bold")).pack(side="left")
        tk.Label(logo, text=" 주식회사인팩", fg="black", bg="white", font=("Malgun Gothic", 8)).pack(side="left")
        tk.Label(inner, text=page_title, bg=CARD_BG, fg="white", font=(theme.FONT, 20, "bold")).pack(
            anchor="w", pady=(22, 2))
        tk.Label(inner, text="Sign in with your operator account to continue.", bg=CARD_BG, fg=LABEL_FG,
                 font=(theme.FONT, 10)).pack(anchor="w", pady=(0, 22))

        # Fields
        self.ent_id = self._field(inner, "USER ID")
        self.ent_pw, pw_box = self._field(inner, "PASSWORD", password=True)
        self._show = False
        self.btn_show = tk.Button(pw_box, text="Show", command=self._toggle_password, bg=FIELD_BG, fg=LABEL_FG,
                                  activebackground=FIELD_BG, activeforeground="white", bd=0,
                                  font=(theme.FONT, 9, "bold"), cursor="hand2")
        self.btn_show.pack(side="right", padx=(0, 10))

        self.error = tk.Label(inner, text="", bg=CARD_BG, fg="#ff6b6b", font=(theme.FONT, 10), anchor="w")
        self.error.pack(fill="x", pady=(2, 10))

        # Actions
        self.btn_login = tk.Button(inner, text="SIGN IN", command=self.submit, bg=theme.UPDATE, fg="white",
                                   activebackground="#1e88e5", activeforeground="white", bd=0,
                                   font=(theme.FONT, 11, "bold"), pady=9, cursor="hand2")
        self.btn_login.pack(fill="x")
        tk.Button(inner, text="Cancel", command=self.cancel, bg=CARD_BG, fg=LABEL_FG, activebackground=CARD_BG,
                  activeforeground="white", bd=0, font=(theme.FONT, 10), cursor="hand2").pack(pady=(12, 0))

        tk.Label(self, text="FRICTION TEST MACHINE  ·  Authorised personnel only", bg=theme.BG, fg="#4a5263",
                 font=(theme.FONT, 9)).place(relx=0.5, rely=0.96, anchor="center")

        self.bind_all_keys()
        self.after(50, self.ent_id.focus_set)

    # ------------------------------------------------------------ widgets
    def _field(self, parent, label, password=False):
        """Label above a tall input with a coloured edge when focused."""
        tk.Label(parent, text=label, bg=CARD_BG, fg=LABEL_FG, font=(theme.FONT, 8, "bold")).pack(anchor="w")
        box = tk.Frame(parent, bg=FIELD_BG, highlightbackground=FIELD_EDGE, highlightthickness=1)
        box.pack(fill="x", pady=(4, 14))
        e = tk.Entry(box, bg=FIELD_BG, fg="white", insertbackground="white", relief="flat", bd=0,
                     font=(theme.FONT, 12), width=30, show="●" if password else "")
        e.pack(side="left", fill="x", expand=True, padx=12, ipady=8)
        e.bind("<FocusIn>", lambda _e: box.config(highlightbackground=FOCUS, highlightthickness=2))
        e.bind("<FocusOut>", lambda _e: box.config(highlightbackground=FIELD_EDGE, highlightthickness=1))
        e.bind("<Key>", lambda _e: self.error.config(text=""), add="+")
        return (e, box) if password else e

    def _toggle_password(self):
        self._show = not self._show
        self.ent_pw.config(show="" if self._show else "●")
        self.btn_show.config(text="Hide" if self._show else "Show")

    def bind_all_keys(self):
        for e in (self.ent_id, self.ent_pw):
            e.bind("<Return>", lambda _e: self.submit())
            e.bind("<Escape>", lambda _e: self.cancel())

    # ------------------------------------------------------------ actions
    def submit(self):
        if not self.ent_id.get().strip():
            self.error.config(text="Enter your user ID.")
            self.ent_id.focus_set()
            return
        self.error.config(text="Checking…", fg=LABEL_FG)
        self.update_idletasks()
        ok, _name, message = auth.login(self.page, self.ent_id.get(), self.ent_pw.get())
        if ok:
            self.on_success()
        else:
            self.error.config(text=message, fg="#ff6b6b")
            self.ent_pw.delete(0, "end")
            self.ent_pw.focus_set()

    def cancel(self):
        self.on_cancel()
