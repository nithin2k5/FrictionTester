"""MySQL access for the Friction Tester (same server conventions as the
EOL and FeederCable testers: local MySQL, pure-Python driver).

Connection settings are the constants below. Call init_database() once at
start-up to create the database and tables.

TBL_MODEL_MASTER holds one row per model:

    Model, Type (MT / AT), Part Number, Customer Name, ALC Code, PRN Template,
    then the test conditions of the two tests, SELECT CABLE and SHIFT CABLE,
    with the same attributes — Speed, Delay Time, Stroke, Position 1-3,
    LOAD (min ~ max) — as MM_SELECT_<attr> and MM_SHIFT_<attr>.
"""

from contextlib import contextmanager

try:
    import mysql.connector
except ImportError:  # pragma: no cover - reported in the UI
    mysql = None

# Database connection
HOST = "localhost"
PORT = 3306
USER = "root"
PASSWORD = "root"
DATABASE = "friction"

TABLE = "TBL_MODEL_MASTER"

# Model details: (column, label, SQL type)
HEADER_FIELDS = [
    ("MM_MODEL", "Model", "VARCHAR(100) NOT NULL UNIQUE"),
    ("MM_TYPE", "Type", "VARCHAR(10)"),                 # MT / AT
    ("MM_PART_NUMBER", "Part Number", "VARCHAR(100)"),
    ("MM_CUSTOMER_NAME", "Customer Name", "VARCHAR(255)"),
    ("MM_ALC_CODE", "ALC Code", "VARCHAR(100)"),
    ("MM_PRN_TEMPLATE", "PRN Template", "VARCHAR(255)"),
]

# One test's condition: (attribute, label, unit, SQL type), stored once per
# test as MM_<TEST>_<attribute>.
CONDITION_FIELDS = [
    ("SPEED", "Speed", "mm/Sec", "DECIMAL(10,2)"),
    ("DELAY_TIME", "Delay Time", "Sec", "DECIMAL(10,2)"),
    ("STROKE", "Stroke", "mm", "DECIMAL(10,2)"),
    ("POSITION_1", "Position 1", "mm", "DECIMAL(10,2)"),
    ("POSITION_2", "Position 2", "mm", "DECIMAL(10,2)"),
    ("POSITION_3", "Position 3", "mm", "DECIMAL(10,2)"),
    ("LOAD_MIN", "LOAD min", "kgf", "DECIMAL(10,3)"),
    ("LOAD_MAX", "LOAD max", "kgf", "DECIMAL(10,3)"),
]
# The two tests: (key used in column names, display name)
TESTS = (("SELECT", "SELECT CABLE"), ("SHIFT", "SHIFT CABLE"))
TEST_KEYS = [key for key, _ in TESTS]


def cond_col(test, attr):
    """Column for one condition attribute of a test, e.g. cond_col('SHIFT', 'SPEED') -> MM_SHIFT_SPEED."""
    return f"MM_{test}_{attr}"


# Every data column in table order: (column, label, unit, SQL type)
FIELDS = [(c, label, "", sql) for c, label, sql in HEADER_FIELDS] + [
    (cond_col(key, attr), f"{name} {label}", unit, sql)
    for key, name in TESTS for attr, label, unit, sql in CONDITION_FIELDS
]
COLUMNS = [f[0] for f in FIELDS]
LABELS = {f[0]: f[1] for f in FIELDS}
NUMERIC = {cond_col(key, attr) for key in TEST_KEYS for attr, *_ in CONDITION_FIELDS}

SCHEMA = (f"CREATE TABLE IF NOT EXISTS {TABLE} (ID INT AUTO_INCREMENT PRIMARY KEY, "
          + ", ".join(f"{c} {sql}" for c, _l, _u, sql in FIELDS) + ")")

# Columns from earlier versions, renamed with their data kept:
#   MM_<attr>      (single condition)  -> MM_SELECT_<attr>
#   MM_C1_<attr>   (condition 1)       -> MM_SELECT_<attr>
#   MM_C2_<attr>   (condition 2)       -> MM_SHIFT_<attr>
RENAMED_COLUMNS = {}
for _attr, _l, _u, _sql in CONDITION_FIELDS:
    RENAMED_COLUMNS[f"MM_{_attr}"] = (cond_col("SELECT", _attr), _sql)
    RENAMED_COLUMNS[f"MM_C1_{_attr}"] = (cond_col("SELECT", _attr), _sql)
    RENAMED_COLUMNS[f"MM_C2_{_attr}"] = (cond_col("SHIFT", _attr), _sql)
RETIRED_COLUMNS = ["MM_TEST_CABLE", "MM_SAVED_TO", "MM_DATE", "MM_TEST_RESULT"]


def available():
    return mysql is not None


def _connect(with_database=True):
    if mysql is None:
        raise RuntimeError("mysql-connector-python is not installed (pip install mysql-connector-python)")
    args = {"host": HOST, "port": PORT, "user": USER, "password": PASSWORD,
            "use_pure": True, "connection_timeout": 5}
    if with_database:
        args["database"] = DATABASE
    return mysql.connector.connect(**args)


@contextmanager
def cursor(commit=False, dictionary=False):
    conn = _connect()
    try:
        cur = conn.cursor(dictionary=dictionary)
        try:
            yield cur
            if commit:
                conn.commit()
        finally:
            cur.close()
    finally:
        conn.close()


def init_database():
    """Create the database and TBL_MODEL_MASTER if they don't exist."""
    conn = _connect(with_database=False)
    try:
        cur = conn.cursor()
        cur.execute(f"CREATE DATABASE IF NOT EXISTS `{DATABASE}`")
        cur.close()
    finally:
        conn.close()
    with cursor(commit=True) as cur:
        cur.execute(SCHEMA)
        _migrate(cur)


def _migrate(cur):
    """Bring a TBL_MODEL_MASTER made by an earlier version up to date."""
    cur.execute("SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s", (DATABASE, TABLE))
    existing = {r[0].upper() for r in cur.fetchall()}
    # Old single condition -> condition 1, keeping the data
    for old, (new, sql) in RENAMED_COLUMNS.items():
        if old in existing and new not in existing:
            cur.execute(f"ALTER TABLE {TABLE} CHANGE COLUMN {old} {new} {sql}")
            existing.discard(old)
            existing.add(new)
    # Any other missing column, placed after its neighbour in table order
    previous = "ID"
    for column, _label, _unit, sql in FIELDS:
        if column not in existing:
            sql = sql.replace(" NOT NULL UNIQUE", "")   # MM_MODEL always exists already
            cur.execute(f"ALTER TABLE {TABLE} ADD COLUMN {column} {sql} AFTER {previous}")
        previous = column
    for column in RETIRED_COLUMNS:
        if column in existing:
            cur.execute(f"ALTER TABLE {TABLE} DROP COLUMN {column}")


# ------------------------------------------------------------------ models
SEARCH_COLUMNS = ["MM_MODEL", "MM_PART_NUMBER", "MM_CUSTOMER_NAME", "MM_ALC_CODE"]


def list_models(search=""):
    sql = f"SELECT ID, {', '.join(COLUMNS)} FROM {TABLE}"
    args = ()
    if search:
        sql += " WHERE " + " OR ".join(f"{c} LIKE %s" for c in SEARCH_COLUMNS)
        args = tuple(f"%{search}%" for _ in SEARCH_COLUMNS)
    sql += " ORDER BY MM_MODEL"
    with cursor(dictionary=True) as cur:
        cur.execute(sql, args)
        return cur.fetchall()


def get_model(model):
    with cursor(dictionary=True) as cur:
        cur.execute(f"SELECT ID, {', '.join(COLUMNS)} FROM {TABLE} WHERE MM_MODEL = %s", (model,))
        return cur.fetchone()


def save_model(row):
    """Insert a new model, or update it if MM_MODEL already exists."""
    values = [row.get(c) for c in COLUMNS]
    updates = ", ".join(f"{c} = VALUES({c})" for c in COLUMNS if c != "MM_MODEL")
    sql = (f"INSERT INTO {TABLE} ({', '.join(COLUMNS)}) VALUES ({', '.join(['%s'] * len(COLUMNS))}) "
           f"ON DUPLICATE KEY UPDATE {updates}")
    with cursor(commit=True) as cur:
        cur.execute(sql, values)


def delete_model(model):
    with cursor(commit=True) as cur:
        cur.execute(f"DELETE FROM {TABLE} WHERE MM_MODEL = %s", (model,))
        return cur.rowcount
