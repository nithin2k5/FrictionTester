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

# Allowed position range (mm), checked in Model Settings and the Test Console
POSITION_1_MIN = -30.0          # Position 1 may not be set below this
POSITION_3_MAX = 30.0           # Position 3 may not be set above this


def check_positions(name, p1, p3):
    """Raise ValueError if Position 1 / Position 3 are outside the allowed range."""
    if p1 is not None and p1 < POSITION_1_MIN:
        raise ValueError(f"{name}: Position 1 must not be below {POSITION_1_MIN:g} mm (entered {p1:g})")
    if p3 is not None and p3 > POSITION_3_MAX:
        raise ValueError(f"{name}: Position 3 must not be above {POSITION_3_MAX:g} mm (entered {p3:g})")
# The two tests: (key used in column names, display name)
TESTS = (("SELECT", "SELECT CABLE"), ("SHIFT", "SHIFT CABLE"))
TEST_KEYS = [key for key, _ in TESTS]


def cond_col(test, attr):
    """Column for one condition attribute of a test, e.g. cond_col('SHIFT', 'SPEED') -> MM_SHIFT_SPEED."""
    return f"MM_{test}_{attr}"


# PLC parameter words per test, sent to D04200-D04213 (plc.pdf profile). Raw
# 16-bit words as the PLC receives them - the ladder scales them x100 (x10 for
# the measurement start) into positioning counts; units are machine calibration.
# (attribute, label, profile key)
PLC_PARAM_FIELDS = [
    ("PLC_JOG_LOW", "Low jog speed", "jog_low"),
    ("PLC_JOG_HIGH", "High jog speed", "jog_high"),
    ("PLC_SPEED", "Operating speed", "speed"),
    ("PLC_PULL_TRAVEL", "PULL travel", "pull_travel"),
    ("PLC_LIMIT_LEFT", "Left limit", "limit_left"),
    ("PLC_LIMIT_RIGHT", "Right limit", "limit_right"),
    ("PLC_MEAS_START", "Measurement start", "meas_start"),
]

# Every data column in table order: (column, label, unit, SQL type)
FIELDS = [(c, label, "", sql) for c, label, sql in HEADER_FIELDS] + [
    (cond_col(key, attr), f"{name} {label}", unit, sql)
    for key, name in TESTS for attr, label, unit, sql in CONDITION_FIELDS
] + [
    (cond_col(key, attr), f"{name} PLC {label}", "word", "INT")
    for key, name in TESTS for attr, label, _k in PLC_PARAM_FIELDS
]
COLUMNS = [f[0] for f in FIELDS]
LABELS = {f[0]: f[1] for f in FIELDS}
NUMERIC = {cond_col(key, attr) for key in TEST_KEYS for attr, *_ in CONDITION_FIELDS}
PLC_INT = {cond_col(key, attr) for key in TEST_KEYS for attr, *_ in PLC_PARAM_FIELDS}


def plc_params_from_row(row):
    """The 14 parameter words of a model in D04200 order (int or None).

    Operating speed: the model's PLC word, else the cable's test-condition Speed.
    Low jog speed: always half the operating speed (rounded, at least 1).
    """
    from .plc import pc_profile as pp
    row = row or {}
    attr_of = {k: attr for attr, _l, k in PLC_PARAM_FIELDS}
    out = []
    for axis, key, *_r in pp.PARAMS:
        v = row.get(cond_col(axis.upper(), attr_of[key]))
        if key == "speed" and v is None:
            v = row.get(cond_col(axis.upper(), "SPEED"))
        out.append(None if v is None else int(round(float(v))))
    for axis in pp.AXES:
        speed = out[pp.param_index(axis, "speed")]
        out[pp.param_index(axis, "jog_low")] = None if speed is None else max(1, round(speed / 2))
    return out

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
        cur.execute(RESULTS_SCHEMA)
        cur.execute(ADMIN_SCHEMA)
        cur.execute(LOGIN_HISTORY_SCHEMA)


# ------------------------------------------------------------- test results
# One row per PLC test cycle (stage) of a completed part. An AT part gives
# two rows (SELECT, SHIFT), an MT part one row (BOTH); the rows of one part
# share TR_PART_UID, TR_LOT_VALUE and TR_LOT_RESULT.
#
# Values are the PLC's raw stored samples per channel (the channel -> cable
# and forward/return -> pull/push mapping is configuration, not stored as
# fact); TR_DETAIL keeps the full record as JSON (extra windows, limits,
# channel limit flags, PLC counters, completion evidence, scale/unit).
RESULTS_TABLE = "TBL_TEST_RESULTS"

_CH_COLS = [f"TR_CH{ch}_{d}_{i}" for ch in (1, 2) for d in ("FWD", "RET") for i in (1, 2, 3)]
RESULT_COLUMNS = (
    ["TR_PART_UID", "TR_DATETIME", "TR_MODEL", "TR_MODEL_TYPE", "TR_PART_NUMBER",
     "TR_STAGE", "TR_LABEL", "TR_STAGE_RESULT"]
    + _CH_COLS
    + ["TR_CH1_FINAL", "TR_CH2_FINAL", "TR_CH1_LIMIT_NG", "TR_CH2_LIMIT_NG",
       "TR_LOT_VALUE", "TR_LOT_RESULT", "TR_SOURCE", "TR_DETAIL"]
)

RESULTS_SCHEMA = f"""
    CREATE TABLE IF NOT EXISTS {RESULTS_TABLE} (
        ID INT AUTO_INCREMENT PRIMARY KEY,
        TR_PART_UID VARCHAR(40) NOT NULL,
        TR_DATETIME DATETIME NOT NULL,
        TR_MODEL VARCHAR(100),
        TR_MODEL_TYPE VARCHAR(10),
        TR_PART_NUMBER VARCHAR(100),
        TR_STAGE VARCHAR(10),
        TR_LABEL VARCHAR(255),
        TR_STAGE_RESULT VARCHAR(10),
        {", ".join(f"{c} DECIMAL(14,3)" for c in _CH_COLS)},
        TR_CH1_FINAL DECIMAL(14,3),
        TR_CH2_FINAL DECIMAL(14,3),
        TR_CH1_LIMIT_NG TINYINT(1),
        TR_CH2_LIMIT_NG TINYINT(1),
        TR_LOT_VALUE VARCHAR(255),
        TR_LOT_RESULT VARCHAR(10),
        TR_SOURCE VARCHAR(50),
        TR_DETAIL JSON,
        TR_CREATED TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        KEY idx_tr_part (TR_PART_UID),
        KEY idx_tr_lot (TR_LOT_VALUE),
        KEY idx_tr_datetime (TR_DATETIME)
    )
"""


def _pc_result_rows(record, part_uid, source):
    """Rows for the plc.pdf profile: one per cable. CH1 holds that cable's own
    values (FWD = PULL 1-3, RET = PUSH 1-3, FINAL = largest value judged,
    LIMIT_NG = 1 when the cable is NG); CH2 is unused."""
    import json
    rows = []
    for st in record["stages"]:
        res = st.get("result") or {}
        row = {
            "TR_PART_UID": part_uid,
            "TR_DATETIME": record["time"].replace("T", " "),
            "TR_MODEL": record.get("model"), "TR_MODEL_TYPE": record.get("type"),
            "TR_PART_NUMBER": record.get("part_number"),
            "TR_STAGE": st["stage"].upper(),
            "TR_LABEL": record.get("label_shift") if st["stage"] == "shift" else record.get("label_select"),
            "TR_STAGE_RESULT": st.get("verdict"),
            "TR_CH1_FINAL": res.get("max"), "TR_CH2_FINAL": None,
            "TR_CH1_LIMIT_NG": None if res.get("ok") is None else int(not res["ok"]),
            "TR_CH2_LIMIT_NG": None,
            "TR_LOT_VALUE": record.get("label_select") or record.get("label_shift"),
            "TR_LOT_RESULT": record.get("verdict"),
            "TR_SOURCE": source,
        }
        for ch in (1, 2):
            for d in ("FWD", "RET"):
                for i in (1, 2, 3):
                    row[f"TR_CH{ch}_{d}_{i}"] = None
        for d, key in (("FWD", "pull"), ("RET", "push")):
            for i, v in enumerate((res.get(key) or [])[:3]):
                row[f"TR_CH1_{d}_{i + 1}"] = v
        row["TR_DETAIL"] = json.dumps({
            "profile": record.get("profile"), "part_id": record.get("part_id"), "started": record.get("started"),
            "verdict_basis": record.get("verdict_basis"), "counted": record.get("counted"),
            "reasons": record.get("reasons"), "result": res, "snapshot": record.get("snapshot"),
            "evidence": (record.get("evidence") or {}).get(st["stage"]),
            "acquisition": (record.get("acquisition") or {}).get(st["stage"])}, default=str)
        rows.append(row)
    if not rows:                     # aborted before any cable ran: one row keeps the reason
        rows.append({c: None for c in RESULT_COLUMNS})
        rows[0].update({"TR_PART_UID": part_uid, "TR_DATETIME": record["time"].replace("T", " "),
                        "TR_MODEL": record.get("model"), "TR_MODEL_TYPE": record.get("type"),
                        "TR_PART_NUMBER": record.get("part_number"), "TR_STAGE": "PART",
                        "TR_LABEL": record.get("label_select") or record.get("label_shift"),
                        "TR_STAGE_RESULT": record.get("verdict"), "TR_LOT_RESULT": record.get("verdict"),
                        "TR_LOT_VALUE": record.get("label_select") or record.get("label_shift"),
                        "TR_SOURCE": source,
                        "TR_DETAIL": json.dumps({"part_id": record.get("part_id"), "reasons": record.get("reasons"),
                                                 "snapshot": record.get("snapshot")}, default=str)})
    return rows


def result_rows(record, part_uid, source):
    """TBL_TEST_RESULTS rows (dicts) for one completed part record from the part flow."""
    import json
    if record.get("profile") == "printing_program_old_pc_words":
        return _pc_result_rows(record, part_uid, source)
    rows = []
    for st in record["stages"]:
        meas = st.get("measurements") or {}
        samples = meas.get("samples") or {}
        finals = meas.get("finals") or {}
        flags = meas.get("channel_ng") or {}
        stage = st["stage"].upper()
        label = record.get("label_shift") if st["stage"] == "shift" else record.get("label_select")
        row = {
            "TR_PART_UID": part_uid,
            "TR_DATETIME": record["time"].replace("T", " "),
            "TR_MODEL": record.get("model"),
            "TR_MODEL_TYPE": record.get("type"),
            "TR_PART_NUMBER": record.get("part_number"),
            "TR_STAGE": stage,
            "TR_LABEL": label,
            "TR_STAGE_RESULT": st.get("verdict"),
            "TR_CH1_FINAL": finals.get(1), "TR_CH2_FINAL": finals.get(2),
            "TR_CH1_LIMIT_NG": None if flags.get(1) is None else int(bool(flags.get(1))),
            "TR_CH2_LIMIT_NG": None if flags.get(2) is None else int(bool(flags.get(2))),
            "TR_LOT_VALUE": record.get("label_select") or record.get("label_shift"),
            "TR_LOT_RESULT": record.get("verdict"),
            "TR_SOURCE": source,
        }
        for ch in (1, 2):
            for d, key in (("FWD", "forward"), ("RET", "return")):
                for i in (1, 2, 3):
                    row[f"TR_CH{ch}_{d}_{i}"] = samples.get(ch, {}).get(key, {}).get(f"P{i}")
        detail = {k: v for k, v in st.items() if k != "measurements"}
        detail["measurements"] = {
            "samples": {str(ch): v for ch, v in samples.items()},
            "finals": {str(k): v for k, v in finals.items()},
            "limits": {str(k): v for k, v in (meas.get("limits") or {}).items()},
            "channel_ng": {str(k): v for k, v in flags.items()},
        }
        detail["verdict_basis"] = record.get("verdict_basis")
        row["TR_DETAIL"] = json.dumps(detail, default=str)
        rows.append(row)
    return rows


def insert_test_results(rows):
    """Insert the rows of one part in a single transaction.

    Idempotent per part: if rows with this TR_PART_UID already exist (a retry
    after a lost acknowledgement), nothing is inserted and 0 is returned.
    """
    if not rows:
        return 0
    sql = (f"INSERT INTO {RESULTS_TABLE} ({', '.join(RESULT_COLUMNS)}) "
           f"VALUES ({', '.join(['%s'] * len(RESULT_COLUMNS))})")
    with cursor(commit=True) as cur:
        cur.execute(f"SELECT COUNT(*) FROM {RESULTS_TABLE} WHERE TR_PART_UID = %s FOR UPDATE",
                    (rows[0]["TR_PART_UID"],))
        if cur.fetchone()[0]:
            return 0
        cur.executemany(sql, [[r.get(c) for c in RESULT_COLUMNS] for r in rows])
        return cur.rowcount


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
    """Insert a new model, or update it if MM_MODEL already exists.

    Only the columns present in row are written; an existing model keeps
    the values of the others (e.g. the PLC parameter words, not on the form).
    """
    cols = [c for c in COLUMNS if c in row]
    values = [row[c] for c in cols]
    updates = ", ".join(f"{c} = VALUES({c})" for c in cols if c != "MM_MODEL")
    sql = (f"INSERT INTO {TABLE} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) "
           f"ON DUPLICATE KEY UPDATE {updates}")
    with cursor(commit=True) as cur:
        cur.execute(sql, values)


def delete_model(model):
    with cursor(commit=True) as cur:
        cur.execute(f"DELETE FROM {TABLE} WHERE MM_MODEL = %s", (model,))
        return cur.rowcount


def list_test_results(date_from=None, date_to=None, model="", text="", lot_result="", limit=2000):
    """TBL_TEST_RESULTS rows (newest first) for the Report page.

    date_from / date_to: datetime.date, inclusive. text matches the label or
    lot value. lot_result: "" (all), "OK" or "NG".
    """
    import datetime as _dt
    where, args = [], []
    if date_from:
        where.append("TR_DATETIME >= %s")
        args.append(_dt.datetime.combine(date_from, _dt.time.min))
    if date_to:
        where.append("TR_DATETIME < %s")
        args.append(_dt.datetime.combine(date_to + _dt.timedelta(days=1), _dt.time.min))
    if model:
        where.append("TR_MODEL LIKE %s")
        args.append(f"%{model}%")
    if text:
        where.append("(TR_LABEL LIKE %s OR TR_LOT_VALUE LIKE %s)")
        args += [f"%{text}%", f"%{text}%"]
    if lot_result in ("OK", "NG"):
        where.append("TR_LOT_RESULT = %s")
        args.append(lot_result)
    sql = (f"SELECT ID, {', '.join(c for c in RESULT_COLUMNS if c != 'TR_DETAIL')} FROM {RESULTS_TABLE}"
           + (" WHERE " + " AND ".join(where) if where else "")
           + " ORDER BY TR_DATETIME DESC, ID DESC LIMIT %s")
    args.append(int(limit))
    with cursor(dictionary=True) as cur:
        cur.execute(sql, args)
        return cur.fetchall()


# ------------------------------------------------------------- users / logins
# Operator accounts for the pages behind a sign-in (Admin, Model Settings,
# COM Setting), as in the FeederCable tester. Passwords are stored only as a
# salted PBKDF2-SHA256 hash (AD_SALT + AD_PWD_HASH), never as text.
ADMIN_TABLE = "TBL_ADMIN"
LOGIN_HISTORY_TABLE = "TBL_LOGIN_HISTORY"

ADMIN_SCHEMA = f"""
    CREATE TABLE IF NOT EXISTS {ADMIN_TABLE} (
        AD_ENO VARCHAR(50) PRIMARY KEY,
        AD_ENAME VARCHAR(100),
        AD_SALT CHAR(32) NOT NULL,
        AD_PWD_HASH CHAR(64) NOT NULL,
        AD_DESIG VARCHAR(100),
        AD_DEPT VARCHAR(100),
        AD_CREATED TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        AD_MODIFIED TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    )
"""

LOGIN_HISTORY_SCHEMA = f"""
    CREATE TABLE IF NOT EXISTS {LOGIN_HISTORY_TABLE} (
        ID INT AUTO_INCREMENT PRIMARY KEY,
        LH_DATETIME DATETIME NOT NULL,
        LH_PAGE VARCHAR(50),
        LH_ENO VARCHAR(50),
        LH_ENAME VARCHAR(100),
        LH_RESULT VARCHAR(10),
        LH_REASON VARCHAR(200),
        KEY idx_lh_datetime (LH_DATETIME)
    )
"""


def list_users():
    with cursor(dictionary=True) as cur:
        cur.execute(f"SELECT AD_ENO, AD_ENAME, AD_DESIG, AD_DEPT FROM {ADMIN_TABLE} ORDER BY AD_ENO")
        return cur.fetchall()


def get_user(eno):
    with cursor(dictionary=True) as cur:
        cur.execute(f"SELECT * FROM {ADMIN_TABLE} WHERE AD_ENO = %s", (eno,))
        return cur.fetchone()


def save_user(eno, ename, desig, dept, salt=None, pwd_hash=None):
    """Insert or update a user. salt/pwd_hash None on update = keep the current password."""
    with cursor(commit=True) as cur:
        cur.execute(f"SELECT AD_ENO FROM {ADMIN_TABLE} WHERE AD_ENO = %s", (eno,))
        if cur.fetchone():
            if pwd_hash:
                cur.execute(f"UPDATE {ADMIN_TABLE} SET AD_ENAME=%s, AD_DESIG=%s, AD_DEPT=%s, AD_SALT=%s, "
                            f"AD_PWD_HASH=%s WHERE AD_ENO=%s", (ename, desig, dept, salt, pwd_hash, eno))
            else:
                cur.execute(f"UPDATE {ADMIN_TABLE} SET AD_ENAME=%s, AD_DESIG=%s, AD_DEPT=%s WHERE AD_ENO=%s",
                            (ename, desig, dept, eno))
            return "updated"
        if not pwd_hash:
            raise ValueError("A new user needs a password")
        cur.execute(f"INSERT INTO {ADMIN_TABLE} (AD_ENO, AD_ENAME, AD_SALT, AD_PWD_HASH, AD_DESIG, AD_DEPT) "
                    f"VALUES (%s, %s, %s, %s, %s, %s)", (eno, ename, salt, pwd_hash, desig, dept))
        return "added"


def delete_user(eno):
    with cursor(commit=True) as cur:
        cur.execute(f"DELETE FROM {ADMIN_TABLE} WHERE AD_ENO = %s", (eno,))
        return cur.rowcount


def record_login(page, eno, ename, ok, reason=""):
    import datetime as _dt
    with cursor(commit=True) as cur:
        cur.execute(f"INSERT INTO {LOGIN_HISTORY_TABLE} (LH_DATETIME, LH_PAGE, LH_ENO, LH_ENAME, LH_RESULT, LH_REASON) "
                    f"VALUES (%s, %s, %s, %s, %s, %s)",
                    (_dt.datetime.now().replace(microsecond=0), page, eno, ename,
                     "SUCCESS" if ok else "FAILED", reason[:200]))


def list_login_history(limit=200):
    with cursor(dictionary=True) as cur:
        cur.execute(f"SELECT LH_DATETIME, LH_PAGE, LH_ENO, LH_ENAME, LH_RESULT, LH_REASON FROM {LOGIN_HISTORY_TABLE} "
                    f"ORDER BY LH_DATETIME DESC, ID DESC LIMIT %s", (int(limit),))
        return cur.fetchall()
