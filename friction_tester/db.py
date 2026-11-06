"""MySQL access for the Friction Tester (same server conventions as the
EOL and FeederCable testers: local MySQL, pure-Python driver).

Connection settings are the constants below. Call init_database() once at
start-up to create the database and tables.

TBL_MODEL_MASTER holds one row per model, with the fields of the LabVIEW
model sheet:

    Model, Test Cable, Saved to, Date, Test Result,
    Speed, Delay Time, Stroke, Position 1, Position 2, Position 3, LOAD (min ~ max)
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

SCHEMA = f"""
    CREATE TABLE IF NOT EXISTS {TABLE} (
        ID INT AUTO_INCREMENT PRIMARY KEY,
        MM_MODEL VARCHAR(100) NOT NULL UNIQUE,
        MM_TEST_CABLE VARCHAR(50),
        MM_SAVED_TO VARCHAR(255),
        MM_DATE DATETIME,
        MM_TEST_RESULT VARCHAR(10),
        MM_SPEED DECIMAL(10,2),
        MM_DELAY_TIME DECIMAL(10,2),
        MM_STROKE DECIMAL(10,2),
        MM_POSITION_1 DECIMAL(10,2),
        MM_POSITION_2 DECIMAL(10,2),
        MM_POSITION_3 DECIMAL(10,2),
        MM_LOAD_MIN DECIMAL(10,3),
        MM_LOAD_MAX DECIMAL(10,3)
    )
"""

# Column, label, unit — in sheet order. Used by the Model Settings page.
FIELDS = [
    ("MM_MODEL", "Model", ""),
    ("MM_TEST_CABLE", "Test Cable", ""),
    ("MM_SAVED_TO", "Saved to", ""),
    ("MM_DATE", "Date", ""),
    ("MM_TEST_RESULT", "Test Result", ""),
    ("MM_SPEED", "Speed", "mm/Sec"),
    ("MM_DELAY_TIME", "Delay Time", "Sec"),
    ("MM_STROKE", "Stroke", "mm"),
    ("MM_POSITION_1", "Position 1", "mm"),
    ("MM_POSITION_2", "Position 2", "mm"),
    ("MM_POSITION_3", "Position 3", "mm"),
    ("MM_LOAD_MIN", "LOAD min", "kgf"),
    ("MM_LOAD_MAX", "LOAD max", "kgf"),
]
COLUMNS = [c for c, _, _ in FIELDS]
NUMERIC = {"MM_SPEED", "MM_DELAY_TIME", "MM_STROKE", "MM_POSITION_1", "MM_POSITION_2",
           "MM_POSITION_3", "MM_LOAD_MIN", "MM_LOAD_MAX"}

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


# ------------------------------------------------------------------ models
def list_models(search=""):
    sql = f"SELECT ID, {', '.join(COLUMNS)} FROM {TABLE}"
    args = ()
    if search:
        sql += " WHERE MM_MODEL LIKE %s OR MM_TEST_CABLE LIKE %s"
        args = (f"%{search}%", f"%{search}%")
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
