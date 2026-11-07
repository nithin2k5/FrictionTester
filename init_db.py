"""Initialise the FrictionTester database.

Creates the MySQL database and every table the app uses, using the connection
set at the top of friction_tester/db.py (HOST, PORT, USER, PASSWORD, DATABASE).
Safe to run again: existing tables and data are kept; tables from an older
version are migrated the same way the app does on startup.

    python init_db.py                        create database + tables, show them
    python init_db.py --admin E1001 "Name"   also add (or reset) a sign-in user;
                                             the password is asked for, not typed
                                             on the command line
    python init_db.py --check                only test the connection and list
                                             the tables, change nothing
"""

import argparse
import getpass
import sys

from friction_tester import auth, db

TABLES = [db.TABLE, db.RESULTS_TABLE, db.ADMIN_TABLE, db.LOGIN_HISTORY_TABLE]


def server_text():
    return f"{db.USER}@{db.HOST}:{db.PORT}, database `{db.DATABASE}`"


def show_tables():
    with db.cursor() as cur:
        cur.execute("SHOW TABLES")
        existing = {row[0].upper() for row in cur.fetchall()}
        print(f"\n  {'Table':<22} {'Rows':>8}")
        print(f"  {'-' * 22} {'-' * 8}")
        missing = []
        for name in TABLES:
            if name.upper() in existing:
                cur.execute(f"SELECT COUNT(*) FROM {name}")
                print(f"  {name:<22} {cur.fetchone()[0]:>8}")
            else:
                print(f"  {name:<22} {'missing':>8}")
                missing.append(name)
    return missing


def add_admin(eno, ename, desig, dept):
    eno = eno.strip()
    if not eno or not ename.strip():
        raise ValueError("Employee ID and name are required")
    while True:
        pw = getpass.getpass(f"Password for {eno}: ")
        if len(pw) < 4:
            print("  Use at least 4 characters.")
            continue
        if getpass.getpass("Repeat password: ") == pw:
            break
        print("  Passwords do not match, try again.")
    salt, pwd_hash = auth.hash_password(pw)
    existed = db.get_user(eno) is not None
    db.save_user(eno, ename.strip(), desig, dept, salt, pwd_hash)
    print(f"\n{'Updated' if existed else 'Added'} user {eno} ({ename.strip()}) in {db.ADMIN_TABLE}.")


def main():
    p = argparse.ArgumentParser(description="Create the FrictionTester MySQL database and tables.")
    p.add_argument("--check", action="store_true", help="only test the connection and list the tables")
    p.add_argument("--admin", nargs=2, metavar=("EMPLOYEE_ID", "NAME"),
                   help="add or reset a sign-in user (password is prompted)")
    p.add_argument("--desig", default="Admin", help="designation for --admin (default: Admin)")
    p.add_argument("--dept", default="", help="department for --admin")
    args = p.parse_args()

    if not db.available():
        print("mysql-connector-python is not installed:  pip install mysql-connector-python")
        return 1

    print(f"MySQL server: {server_text()}")
    try:
        if args.check:
            with db.cursor() as cur:
                cur.execute("SELECT VERSION()")
                print(f"Connected (MySQL {cur.fetchone()[0]}).")
        else:
            db.init_database()
            print("Database and tables are ready.")
        missing = show_tables()
    except Exception as exc:
        print(f"\nFAILED: {exc}")
        print("Check that the MySQL service is running and that HOST / PORT / USER / PASSWORD at the "
              "top of friction_tester/db.py are right.")
        return 1

    if args.admin:
        if missing:
            print("\nTables are missing; run without --check first.")
            return 1
        try:
            add_admin(args.admin[0], args.admin[1], args.desig, args.dept)
        except (ValueError, KeyboardInterrupt) as exc:
            print(f"\nUser not saved: {exc or 'cancelled'}")
            return 1
        except Exception as exc:
            print(f"\nFAILED to save the user: {exc}")
            return 1
    return 1 if missing and args.check else 0


if __name__ == "__main__":
    sys.exit(main())
