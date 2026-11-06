"""Login check for protected pages (currently Model Settings).

TEMPORARY: one hard-coded operator account until users move into the
database. Only a salted PBKDF2 hash of the password is kept here, never the
password itself. To change it, generate a new hash:

    python -c "import hashlib,os; s=os.urandom(16); print(s.hex(), hashlib.pbkdf2_hmac('sha256', b'NEWPASS', s, 200000).hex())"
"""

import hashlib
import hmac

ITERATIONS = 200_000

# user id -> (salt hex, PBKDF2-SHA256 hash hex)
USERS = {
    "nice": ("6d400323d83a444067683249ca55f5c4",
             "e7d36c74bad0da5f5399460bdd31fe22a6a88998a962227e40fc33befe51a52d"),
}


def check(user_id, password):
    """True if the id/password pair is valid."""
    entry = USERS.get(user_id.strip())
    if entry is None:
        return False
    salt, expected = entry
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), ITERATIONS).hex()
    return hmac.compare_digest(digest, expected)
