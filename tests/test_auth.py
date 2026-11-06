"""Sign-in rules (auth.login) against an in-memory user table."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from friction_tester import auth, db  # noqa: E402


@pytest.fixture
def fake_db(monkeypatch):
    state = {"users": {}, "history": [], "down": False}

    def chk():
        if state["down"]:
            raise ConnectionError("database down")

    monkeypatch.setattr(db, "init_database", lambda: chk())
    monkeypatch.setattr(db, "get_user", lambda eno: (chk(), state["users"].get(eno))[1])
    monkeypatch.setattr(db, "record_login",
                        lambda page, eno, ename, ok, reason="": (chk(), state["history"].append((page, eno, ok, reason))))
    salt, h = auth.hash_password("op1001")
    state["users"]["E1001"] = {"AD_ENO": "E1001", "AD_ENAME": "Kumar", "AD_SALT": salt, "AD_PWD_HASH": h}
    return state


def test_database_user_signs_in_and_is_recorded(fake_db):
    assert auth.login("model_settings", "E1001", "op1001") == (True, "Kumar", "")
    assert fake_db["history"][-1] == ("model_settings", "E1001", True, "")


def test_wrong_password_and_unknown_user_are_refused_and_recorded(fake_db):
    assert auth.login("admin", "E1001", "nope")[0] is False
    assert auth.login("admin", "NOBODY", "x")[2] == "Incorrect user ID or password."
    assert [h[3] for h in fake_db["history"]] == ["wrong password", "unknown user"]


def test_recovery_account_works_even_when_database_is_down(fake_db):
    fake_db["down"] = True
    assert auth.login("admin", "nice", "nice1234")[0] is True
    ok, _n, msg = auth.login("admin", "E1001", "op1001")
    assert ok is False and "not reachable" in msg


def test_passwords_are_salted_hashes():
    s1, h1 = auth.hash_password("same")
    s2, h2 = auth.hash_password("same")
    assert s1 != s2 and h1 != h2 and len(h1) == 64
    assert auth.hash_password("same", s1)[1] == h1
