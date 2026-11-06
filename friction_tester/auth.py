"""Sign-in for protected pages (Admin, Model Settings, COM Setting).

Accounts come from TBL_ADMIN, managed on the Admin page (as in the FeederCable
tester). Passwords are stored only as a salted PBKDF2-SHA256 hash.

One built-in recovery account is kept so the machine is never locked out
(no users yet, or the database is down). Only its hash is stored here. To
change it, generate a new pair and replace RECOVERY_USERS:

    python -c "import hashlib,os; s=os.urandom(16); print(s.hex(), hashlib.pbkdf2_hmac('sha256', b'NEWPASS', s, 200000).hex())"

Every attempt, successful or not, is written to TBL_LOGIN_HISTORY (best
effort: a database problem never blocks the recovery account).
"""

import hashlib
import hmac
import os

ITERATIONS = 200_000

# user id -> (salt hex, PBKDF2-SHA256 hash hex)
RECOVERY_USERS = {
    "nice": ("6d400323d83a444067683249ca55f5c4",
             "e7d36c74bad0da5f5399460bdd31fe22a6a88998a962227e40fc33befe51a52d"),
}


def hash_password(password, salt_hex=None):
    """(salt hex, hash hex) for a password; a new random salt unless one is given."""
    salt = bytes.fromhex(salt_hex) if salt_hex else os.urandom(16)
    return salt.hex(), hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS).hex()


def _matches(password, salt_hex, expected_hex):
    return hmac.compare_digest(hash_password(password, salt_hex)[1], expected_hex)


def check(user_id, password):
    """True if the pair is valid (database users, then the recovery account). No logging."""
    return login(None, user_id, password, record=False)[0]


def login(page, user_id, password, record=True):
    """Check a sign-in for `page`. Returns (ok, display name, message for the operator)."""
    from . import db
    eno = (user_id or "").strip()
    ok, name, reason = False, "", "unknown user"
    db_error = None
    try:
        db.init_database()
        user = db.get_user(eno) if eno else None
        if user:
            name = user.get("AD_ENAME") or eno
            if _matches(password, user["AD_SALT"], user["AD_PWD_HASH"]):
                ok, reason = True, ""
            else:
                reason = "wrong password"
    except Exception as exc:
        db_error = exc
        reason = "database unavailable"
    if not ok and eno in RECOVERY_USERS:
        salt, expected = RECOVERY_USERS[eno]
        if _matches(password, salt, expected):
            ok, name, reason = True, name or eno, "recovery account"
        elif reason == "unknown user":
            reason = "wrong password"
    if record:
        try:
            db.record_login(page, eno, name, ok, reason)
        except Exception:
            pass
    if ok:
        return True, name, ""
    if db_error is not None and eno not in RECOVERY_USERS:
        return False, "", "The user database is not reachable. Try again, or use the recovery account."
    return False, "", "Incorrect user ID or password."
