"""Admin page — user management and login history, as in the FeederCable tester.

Top: the user form (Employee ID, Name, Password, Designation, Department)
with Save / Update, Delete and Clear. Middle: the users. Bottom: the most
recent sign-in attempts on the protected pages.

Passwords are hashed before they reach the database; the stored hash is
never shown. When editing an existing user, leave Password empty to keep
the current one. Database calls run on a worker thread.
"""

import datetime
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from . import theme as T
from .theme import light as theme      # light industrial theme
from .. import auth, db

CARD_BG = theme.PANEL
HEAD_BG = T.L_SURFACE_2
HISTORY_LIMIT = 200
PAGE_NAMES = {"admin": "Admin", "model_settings": "Model Settings", "comport_settings": "COM Setting"}


class AdminPage(tk.Frame):
    def __init__(self, master):
        super().__init__(master, bg=theme.BG)
        self._alive = True
        self.bind("<Destroy>", lambda e: setattr(self, "_alive", False) if e.widget is self else None)
        self._editing = None          # employee ID loaded from the list

        bar = tk.Frame(self, bg=theme.BG)
        bar.pack(fill="x")
        self._label(bar, "Admin User Management", size=15).pack(side="left")
        self._label(bar, db.ADMIN_TABLE, fg=theme.MUTED, size=10).pack(side="left", padx=(12, 0), pady=(4, 0))
        self._button(bar, "CLEAR", self.clear, T.L_NEUTRAL_BTN).pack(side="right", padx=(8, 0))
        self.btn_delete = self._button(bar, "DELETE", self.delete_user, theme.FAIL)
        self.btn_delete.pack(side="right", padx=(8, 0))
        self._button(bar, "SAVE / UPDATE", self.save_user, theme.SAVE).pack(side="right", padx=(8, 0))

        self._build_form()
        body = tk.Frame(self, bg=theme.BG)
        body.pack(fill="both", expand=True, pady=(10, 0))
        body.columnconfigure(0, weight=2, uniform="b")
        body.columnconfigure(1, weight=3, uniform="b")
        body.rowconfigure(0, weight=1)
        self._build_users(body).grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self._build_history(body).grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        self.status = tk.Label(self, text="", bg=theme.BG, fg=theme.MUTED, font=(theme.FONT, 10), anchor="w")
        self.status.pack(fill="x", pady=(6, 0))
        self.clear()
        self.refresh_all()

    # ------------------------------------------------------------ layout
    def _label(self, parent, text, fg=T.L_TEXT, size=10, bold=False, **kw):
        return tk.Label(parent, text=text, bg=parent["bg"], fg=fg,
                        font=(theme.FONT, size, "bold") if bold else (theme.FONT, size), **kw)

    def _button(self, parent, text, command, bg):
        return tk.Button(parent, text=text, command=command, bg=bg, fg="white", activebackground=bg,
                         activeforeground="white", font=(theme.FONT, 10, "bold"), bd=0, padx=16, pady=5,
                         cursor="hand2", disabledforeground="#dfe4ea")

    def _card(self, parent, title):
        card = tk.Frame(parent, bg=CARD_BG, highlightbackground=theme.BORDER, highlightthickness=1)
        head = tk.Frame(card, bg=HEAD_BG)
        head.pack(fill="x")
        tk.Frame(head, bg=theme.TITLE_FG, width=4).pack(side="left", fill="y")
        self._label(head, title, size=12, bold=True).pack(side="left", padx=(10, 8), pady=6)
        return card, head

    def _entry(self, parent, show=""):
        return tk.Entry(parent, bg=theme.ENTRY_BG, fg=T.L_TEXT, insertbackground=T.L_TEXT, font=(theme.FONT, 11),
                        bd=0, relief="flat", highlightbackground=T.L_BORDER, highlightthickness=1,
                        highlightcolor=T.L_PRIMARY, show=show, disabledbackground=T.L_SUNKEN,
                        disabledforeground=T.L_TEXT_2)

    def _build_form(self):
        card, _ = self._card(self, "User")
        card.pack(fill="x", pady=(10, 0))
        form = tk.Frame(card, bg=CARD_BG)
        form.pack(fill="x", padx=14, pady=10)
        self.fields = {}
        layout = [("eno", "Employee ID", 0, 0, ""), ("ename", "Employee Name", 0, 2, ""),
                  ("pwd", "Password", 1, 0, "●"), ("desig", "Designation", 1, 2, ""),
                  ("dept", "Department", 2, 0, "")]
        for key, label, r, c, show in layout:
            self._label(form, label, fg=theme.MUTED).grid(row=r, column=c, sticky="w", pady=4, padx=(0 if c == 0 else 24, 10))
            e = self._entry(form, show)
            e.grid(row=r, column=c + 1, sticky="ew", pady=4, ipady=2)
            self.fields[key] = e
        for c in (1, 3):
            form.columnconfigure(c, weight=1)
        self.pwd_hint = self._label(form, "", fg=theme.MUTED, size=9)
        self.pwd_hint.grid(row=2, column=2, columnspan=2, sticky="w", padx=(24, 0))

    def _tree(self, parent, cols, style="L.Treeview"):
        box = tk.Frame(parent, bg=CARD_BG)
        box.pack(fill="both", expand=True, padx=8, pady=8)
        tree = ttk.Treeview(box, columns=[c for c, _, _ in cols], show="headings", selectmode="browse", style=style)
        for col, heading, width in cols:
            tree.heading(col, text=heading)
            tree.column(col, width=width, minwidth=40, anchor="center")
        sb = ttk.Scrollbar(box, orient="vertical", command=tree.yview, style="L.Vertical.TScrollbar")
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        tree.tag_configure("odd", background=T.L_SUNKEN)
        return tree

    def _build_users(self, parent):
        card, head = self._card(parent, "Users")
        self.lbl_users = self._label(head, "", fg=theme.MUTED, size=9)
        self.lbl_users.pack(side="right", padx=10)
        self.users = self._tree(card, [("AD_ENO", "Employee ID", 110), ("AD_ENAME", "Name", 150),
                                       ("AD_DESIG", "Designation", 110), ("AD_DEPT", "Department", 110)])
        self.users.bind("<<TreeviewSelect>>", lambda _e: self._on_select())
        return card

    def _build_history(self, parent):
        card, head = self._card(parent, "Login History")
        tk.Button(head, text="REFRESH", command=self.load_history, bg=T.L_NEUTRAL_BTN, fg="white", activebackground=T.L_NEUTRAL_BTN,
                  activeforeground="white", bd=0, padx=12, pady=2, font=(theme.FONT, 9, "bold"),
                  cursor="hand2").pack(side="right", padx=8)
        self.lbl_hist = self._label(head, "", fg=theme.MUTED, size=9)
        self.lbl_hist.pack(side="right")
        self.history = self._tree(card, [("date", "Date", 95), ("time", "Time", 80), ("page", "Page", 120),
                                         ("eno", "Employee ID", 100), ("ename", "Name", 120),
                                         ("result", "Result", 80), ("reason", "Reason", 140)])
        self.history.tag_configure("ok", foreground=theme.PASS)
        self.history.tag_configure("bad", foreground=T.L_NG)
        return card

    # ------------------------------------------------------------ helpers
    def _run(self, work, busy, done):
        self.status.config(text=busy, fg=theme.BUSY)
        result = {}

        def worker():
            try:
                db.init_database()
                result["value"], result["error"] = work(), None
            except Exception as exc:
                result["value"], result["error"] = None, exc

        def collect():
            if not self._alive:
                return
            if "value" not in result:
                self.after(100, collect)
            elif result["error"] is not None:
                self.status.config(text=f"Database error: {result['error']}", fg=theme.FAIL)
            else:
                done(result["value"])

        threading.Thread(target=worker, daemon=True).start()
        self.after(100, collect)

    def _form(self):
        return {k: e.get().strip() for k, e in self.fields.items()}

    # ------------------------------------------------------------ actions
    def refresh_all(self):
        self.load_users()
        self.load_history()

    def load_users(self):
        def done(rows):
            self.users.delete(*self.users.get_children())
            for i, r in enumerate(rows):
                self.users.insert("", "end", values=[r.get("AD_ENO"), r.get("AD_ENAME") or "—",
                                                     r.get("AD_DESIG") or "—", r.get("AD_DEPT") or "—"],
                                  tags=("odd",) if i % 2 else ())
            self.lbl_users.config(text=f"{len(rows)} user(s)")
            if not rows:
                self.status.config(text="No users yet - add one above (the recovery account still works)",
                                   fg=theme.PASS)
            elif self.status.cget("text").endswith("…"):
                self.status.config(text="Users loaded", fg=theme.PASS)
        self._run(db.list_users, "Loading users…", done)

    def load_history(self):
        def done(rows):
            self.history.delete(*self.history.get_children())
            for i, r in enumerate(rows):
                dt = r.get("LH_DATETIME")
                ok = r.get("LH_RESULT") == "SUCCESS"
                self.history.insert("", "end", values=[
                    dt.strftime("%Y-%m-%d") if isinstance(dt, datetime.datetime) else dt,
                    dt.strftime("%H:%M:%S") if isinstance(dt, datetime.datetime) else "",
                    PAGE_NAMES.get(r.get("LH_PAGE"), r.get("LH_PAGE") or "—"),
                    r.get("LH_ENO") or "—", r.get("LH_ENAME") or "—",
                    r.get("LH_RESULT"), r.get("LH_REASON") or "—"],
                    tags=("ok" if ok else "bad",))
            self.lbl_hist.config(text=f"[ {len(rows)} most recent ]" if rows else "[ no logins recorded yet ]")
        self._run(lambda: db.list_login_history(HISTORY_LIMIT), "Loading login history…", done)

    def _on_select(self):
        sel = self.users.selection()
        if not sel:
            return
        eno = str(self.users.item(sel[0], "values")[0])
        values = self.users.item(sel[0], "values")
        self.clear()
        self._editing = eno
        e = self.fields
        e["eno"].insert(0, eno)
        e["eno"].config(state="disabled")          # the ID is the key; delete and re-add to change it
        for key, v in zip(("ename", "desig", "dept"), values[1:]):
            if v and v != "—":
                e[key].insert(0, v)
        self.pwd_hint.config(text="Leave Password empty to keep the current one")
        self.btn_delete.config(state="normal")

    def clear(self):
        self._editing = None
        self.fields["eno"].config(state="normal")
        for e in self.fields.values():
            e.delete(0, "end")
        self.pwd_hint.config(text="Required for a new user")
        self.btn_delete.config(state="disabled")
        self.users.selection_remove(self.users.selection())
        self.fields["eno"].focus_set()

    def save_user(self):
        f = self._form()
        eno = self._editing or f["eno"]
        if not eno:
            messagebox.showwarning("Save user", "Employee ID is required.", parent=self)
            return
        if not self._editing and not f["pwd"]:
            messagebox.showwarning("Save user", "A password is required for a new user.", parent=self)
            return
        if f["pwd"] and len(f["pwd"]) < 4:
            messagebox.showwarning("Save user", "The password must have at least 4 characters.", parent=self)
            return
        salt = pwd_hash = None
        if f["pwd"]:
            salt, pwd_hash = auth.hash_password(f["pwd"])

        def work():
            if not self._editing and db.get_user(eno):
                return "exists"
            return db.save_user(eno, f["ename"], f["desig"], f["dept"], salt, pwd_hash)

        def done(result):
            if result == "exists":
                messagebox.showwarning("Save user", f"Employee ID {eno} already exists.\n"
                                       "Select it in the list to update it.", parent=self)
                self.status.config(text=f"{eno} not saved", fg=theme.MUTED)
                return
            self.clear()
            # Refresh without selecting the saved row: selecting it would load
            # it back into the form for editing, and the next entry typed would
            # overwrite this user instead of creating a new one.
            self.load_users()
            self.status.config(text=f"User {eno} {result}", fg=theme.PASS)

        self._run(work, f"Saving {eno}…", done)

    def delete_user(self):
        eno = self._editing
        if not eno:
            return
        if not messagebox.askyesno("Delete user", f"Delete user {eno}?", parent=self):
            return

        def done(count):
            self.status.config(text=f"User {eno} deleted" if count else f"{eno} was not found",
                               fg=theme.PASS if count else theme.MUTED)
            self.clear()
            self.load_users()

        self._run(lambda: db.delete_user(eno), f"Deleting {eno}…", done)
