"""HKS Admin Panel - SQLite persistence layer (stdlib sqlite3, thread-safe)."""
import os
import sqlite3
import threading
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.abspath(os.environ.get("HKS_DATA_DIR")
                           or os.path.join(BASE_DIR, "..", "data"))
CAPTURE_DIR = os.path.join(DATA_DIR, "captures")
DEFAULT_DB = os.environ.get("HKS_DB_PATH") or os.path.join(DATA_DIR, "hks.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL,
  pw_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'admin',
  created REAL NOT NULL,
  last_login REAL
);
CREATE TABLE IF NOT EXISTS sessions(
  token TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL,
  csrf TEXT NOT NULL,
  created REAL NOT NULL,
  expires REAL NOT NULL,
  ip TEXT
);
CREATE TABLE IF NOT EXISTS bans(
  ip TEXT PRIMARY KEY,
  until REAL NOT NULL,
  reason TEXT
);
CREATE TABLE IF NOT EXISTS machines(
  machine_id TEXT PRIMARY KEY,
  name TEXT, version TEXT, ip TEXT,
  online_svc INTEGER DEFAULT 0, online_session INTEGER DEFAULT 0,
  first_seen REAL, last_seen REAL
);
CREATE TABLE IF NOT EXISTS telemetry(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  machine_id TEXT, ts REAL, cpu REAL, ram REAL,
  bat_pct REAL, bat_plugged INTEGER,
  net_sent REAL, net_recv REAL, ssid TEXT, ip TEXT
);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  machine_id TEXT, ts REAL, kind TEXT, data TEXT
);
CREATE TABLE IF NOT EXISTS errors(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  machine_id TEXT, ts REAL, module TEXT, message TEXT, detail TEXT,
  resolved INTEGER DEFAULT 0, hits INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL, user TEXT, ip TEXT, action TEXT, params TEXT, result TEXT
);
CREATE TABLE IF NOT EXISTS captures(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  machine_id TEXT, ts REAL, kind TEXT, file TEXT, meta TEXT
);
CREATE TABLE IF NOT EXISTS recovery_codes(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  code_hash TEXT NOT NULL,
  created REAL NOT NULL,
  used REAL
);

CREATE INDEX IF NOT EXISTS idx_tel ON telemetry(machine_id, ts);
CREATE INDEX IF NOT EXISTS idx_evt ON events(machine_id, ts);
"""

PRUNE_LIMITS = {"telemetry": 60000, "events": 30000, "audit": 30000, "errors": 8000}


class DB:
    def __init__(self, path=DEFAULT_DB):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        os.makedirs(CAPTURE_DIR, exist_ok=True)
        self._local = threading.local()
        self._path = path
        self._lock = threading.Lock()
        # round 62 (audit5 #8): eager init - the lazy hasattr version could let
        # two startup threads build two different locks and both "win"
        self._err_dedupe = {}
        self._dedupe_lock = threading.Lock()  # audit2 #17: own lock (main _lock is NOT reentrant)
        with self._conn() as c:
            c.executescript(SCHEMA)
        # round 68 (F24): 2FA columns - additive migration, safe on existing DBs
        try:
            c.execute("ALTER TABLE users ADD COLUMN totp_secret TEXT")
        except Exception:
            pass
        try:
            c.execute("ALTER TABLE users ADD COLUMN totp_enabled INTEGER DEFAULT 0")
        except Exception:
            pass
            # migration: older DBs lack the errors.hits dedupe column
            try:
                c.execute("ALTER TABLE errors ADD COLUMN hits INTEGER DEFAULT 1")
            except Exception:
                pass  # column already exists

    def _conn(self):
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._path, timeout=15)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=8000")
            self._local.conn = conn
        return conn

    def exec(self, sql, params=()):
        with self._lock:
            with self._conn() as c:
                cur = c.execute(sql, params)
                return cur

    def query(self, sql, params=()):
        with self._lock:
            with self._conn() as c:
                return [dict(r) for r in c.execute(sql, params).fetchall()]

    def one(self, sql, params=()):
        rows = self.query(sql, params)
        return rows[0] if rows else None

    # ---------- users ----------
    def create_user(self, username, pw_hash, role="admin"):
        self.exec("INSERT INTO users(username,pw_hash,role,created) VALUES(?,?,?,?)",
                  (username, pw_hash, role, time.time()))

    def get_user(self, username):
        return self.one("SELECT * FROM users WHERE username=?", (username,))

    def get_user_by_id(self, uid):
        return self.one("SELECT * FROM users WHERE id=?", (uid,))

    def set_totp(self, uid, secret, enabled):
        self.exec("UPDATE users SET totp_secret=?, totp_enabled=? WHERE id=?",
                  (secret, 1 if enabled else 0, uid))

    def login_ips(self, username, limit=10):
        # round 68 (F28): recent successful login sources for anomaly checks
        return [r["ip"] for r in self.query(
            "SELECT DISTINCT ip FROM audit WHERE user=? AND action='login' "
            "ORDER BY ts DESC LIMIT ?", (username, limit))]

    def list_users(self):
        return self.query("SELECT id,username,role,created,last_login FROM users ORDER BY id")

    def set_user_password(self, uid, pw_hash):
        self.exec("UPDATE users SET pw_hash=? WHERE id=?", (pw_hash, uid))

    def set_user_role(self, uid, role):
        self.exec("UPDATE users SET role=? WHERE id=?", (role, uid))

    def delete_user(self, uid):
        self.exec("DELETE FROM users WHERE id=?", (uid,))
        self.exec("DELETE FROM sessions WHERE user_id=?", (uid,))

    def touch_login(self, uid):
        self.exec("UPDATE users SET last_login=? WHERE id=?", (time.time(), uid))

    # ---------- sessions ----------
    def create_session(self, token, csrf, user_id, ttl, ip):
        now = time.time()
        self.exec("INSERT INTO sessions(token,csrf,user_id,created,expires,ip) VALUES(?,?,?,?,?,?)",
                  (token, csrf, user_id, now, now + ttl, ip))

    def get_session(self, token):
        row = self.one("SELECT * FROM sessions WHERE token=?", (token,))
        if row and row["expires"] > time.time():
            return row
        if row:
            self.exec("DELETE FROM sessions WHERE token=?", (token,))
        return None

    def delete_session(self, token):
        self.exec("DELETE FROM sessions WHERE token=?", (token,))

    def list_sessions(self):
        now = time.time()
        return self.query(
            "SELECT s.token,s.csrf,s.created,s.expires,s.ip,u.username FROM sessions s "
            "JOIN users u ON u.id=s.user_id WHERE s.expires>? ORDER BY s.created DESC", (now,))

    def revoke_session(self, token):
        self.delete_session(token)

    # ---------- bans ----------
    def add_ban(self, ip, until, reason=""):
        self.exec("INSERT OR REPLACE INTO bans(ip,until,reason) VALUES(?,?,?)", (ip, until, reason))

    def get_ban(self, ip):
        row = self.one("SELECT * FROM bans WHERE ip=?", (ip,))
        if row and row["until"] > time.time():
            return row
        if row:
            self.exec("DELETE FROM bans WHERE ip=?", (ip,))
        return None

    def list_bans(self):
        return self.query("SELECT * FROM bans WHERE until>? ORDER BY until DESC", (time.time(),))

    def remove_ban(self, ip):
        self.exec("DELETE FROM bans WHERE ip=?", (ip,))

    # ---------- machines / telemetry ----------
    def upsert_machine(self, machine_id, name=None, version=None, ip=None,
                       online_svc=None, online_session=None):
        now = time.time()
        cur = self.one("SELECT * FROM machines WHERE machine_id=?", (machine_id,))
        if cur is None:
            self.exec("INSERT INTO machines(machine_id,name,version,ip,online_svc,online_session,first_seen,last_seen)"
                      " VALUES(?,?,?,?,?,?,?,?)",
                      (machine_id, name, version, ip,
                       int(bool(online_svc)) if online_svc is not None else 0,
                       int(bool(online_session)) if online_session is not None else 0,
                       now, now))
        else:
            self.exec("UPDATE machines SET name=COALESCE(?,name), version=COALESCE(?,version),"
                      " ip=COALESCE(?,ip),"
                      " online_svc=COALESCE(?,online_svc), online_session=COALESCE(?,online_session),"
                      " last_seen=? WHERE machine_id=?",
                      (name, version, ip,
                       None if online_svc is None else int(bool(online_svc)),
                       None if online_session is None else int(bool(online_session)),
                       now, machine_id))

    def list_machines(self):
        return self.query("SELECT * FROM machines ORDER BY last_seen DESC")

    def insert_telemetry(self, m):
        self.exec("INSERT INTO telemetry(machine_id,ts,cpu,ram,bat_pct,bat_plugged,net_sent,net_recv,ssid,ip)"
                  " VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (m.get("machine_id"), time.time(), m.get("cpu"), m.get("ram"),
                   m.get("bat_pct"), int(bool(m.get("bat_plugged"))),
                   m.get("net_sent"), m.get("net_recv"), m.get("ssid"), m.get("ip")))

    def latest_telemetry(self, machine_id, limit=240):
        rows = self.query("SELECT * FROM telemetry WHERE machine_id=? ORDER BY ts DESC LIMIT ?",
                          (machine_id, limit))
        return list(reversed(rows))

    # ---------- events / errors / audit / captures ----------
    # round 67 (Ammar UI doc PART 1): one-time recovery codes. Stored
    # hashed; each code unlocks exactly one password reset from the login
    # page. Generated by first_run and printed to the console ONCE.
    def gen_recovery_codes(self, user_id, count=8):
        import hashlib, secrets as _sc
        out = []
        for _ in range(count):
            c = _sc.token_urlsafe(9)
            self.exec("INSERT INTO recovery_codes(user_id, code_hash, created) VALUES (?,?,?)",
                      (user_id, hashlib.sha256(c.encode()).hexdigest(), time.time()))
            out.append(c)
        return out

    def redeem_recovery_code(self, code):
        import hashlib
        h = hashlib.sha256(code.strip().encode()).hexdigest()
        row = self.one("SELECT rc.id AS rcid, rc.user_id AS user_id, u.username AS username "
                       "FROM recovery_codes rc JOIN users u ON u.id = rc.user_id "
                       "WHERE rc.code_hash = ? AND rc.used IS NULL", (h,))
        if not row:
            return None
        self.exec("UPDATE recovery_codes SET used = ? WHERE id = ?", (time.time(), row["rcid"]))
        return row

    def add_event(self, machine_id, kind, data=None):
        self.exec("INSERT INTO events(machine_id,ts,kind,data) VALUES(?,?,?,?)",
                  (machine_id, time.time(), kind, data if isinstance(data, str) else __import__("json").dumps(data or {})))

    def list_events(self, machine_id=None, kind=None, limit=300):
        sql = "SELECT * FROM events WHERE 1=1"
        p = []
        if machine_id:
            sql += " AND machine_id=?"; p.append(machine_id)
        if kind:
            sql += " AND kind=?"; p.append(kind)
        sql += " ORDER BY id DESC LIMIT ?"; p.append(limit)
        return self.query(sql, tuple(p))

    def add_error(self, machine_id, module, message, detail=""):
        # Dedupe storms (Acer 2026-09-22: 15 identical BitBlt errors in
        # 30 s): the same machine+module+message within 90 s UPDATES the
        # existing row (fresh ts + hits counter) instead of adding rows.
        # (_err_dedupe/_dedupe_lock are created eagerly in __init__ - round 62)
        key = (machine_id, module, message[:120])
        # round 64 (audit6 #15): the dedupe DECISION and the DB write now sit
        # in ONE critical section - the old get-then-act let two identical
        # startup errors both INSERT (2 rows instead of hits=2). The lock
        # order (_dedupe_lock -> _lock inside exec) has no inverse anywhere.
        with self._dedupe_lock:
            last_id, last_ts = self._err_dedupe.get(key, (None, 0.0))
            if last_id is not None and time.time() - last_ts < 90:
                try:
                    self.exec("UPDATE errors SET ts=?, detail=?, "
                              "hits=COALESCE(hits,1)+1 WHERE id=?",
                              (time.time(), detail[:4000], last_id))
                    self._err_dedupe[key] = (last_id, time.time())
                    return
                except Exception:
                    pass  # column missing on very old DBs - fall through to insert
            self.exec("INSERT INTO errors(machine_id,ts,module,message,detail) VALUES(?,?,?,?,?)",
                      (machine_id, time.time(), module, message[:500], detail[:4000]))
            try:
                row = self.query("SELECT id FROM errors ORDER BY id DESC LIMIT 1")
                if row:
                    self._err_dedupe[key] = (row[0]["id"], time.time())
            except Exception:
                pass

    def list_errors(self, machine_id=None, limit=300):
        sql = "SELECT * FROM errors"
        p = []
        if machine_id:
            sql += " WHERE machine_id=?"; p.append(machine_id)
        sql += " ORDER BY id DESC LIMIT ?"; p.append(limit)
        return self.query(sql, tuple(p))

    def resolve_error(self, eid):
        self.exec("UPDATE errors SET resolved=1 WHERE id=?", (eid,))

    def clear_errors(self, machine_id=None):
        if machine_id:
            self.exec("DELETE FROM errors WHERE machine_id=?", (machine_id,))
        else:
            self.exec("DELETE FROM errors")

    def add_audit(self, user, ip, action, params, result):
        # audit2 posture: mask secret-looking values BEFORE they hit disk
        if isinstance(params, dict):
            params = {k: ("***" if any(s in k.lower() for s in
                                      ("pass", "pwd", "secret", "token")) else v)
                      for k, v in params.items()}
        self.exec("INSERT INTO audit(ts,user,ip,action,params,result) VALUES(?,?,?,?,?,?)",
                  (time.time(), user, ip, action,
                   (params if isinstance(params, str) else __import__("json").dumps(params or {}))[:2000],
                   (str(result))[:500]))

    def list_audit(self, limit=300):
        return self.query("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))

    def add_capture(self, machine_id, kind, filename, meta=""):
        # cursor.lastrowid straight from the INSERT - the old round-trip
        # through one() could return None and crash on .get (audit4 #D2)
        with self._lock:
            with self._conn() as c:
                cur = c.execute(
                    "INSERT INTO captures(machine_id,ts,kind,file,meta) VALUES(?,?,?,?,?)",
                    (machine_id, time.time(), kind, filename, meta))
                return cur.lastrowid

    def list_captures(self, machine_id=None, kind=None, limit=200):
        sql = "SELECT id,machine_id,ts,kind,file,meta FROM captures WHERE 1=1"
        p = []
        if machine_id:
            sql += " AND machine_id=?"; p.append(machine_id)
        if kind:
            sql += " AND kind=?"; p.append(kind)
        sql += " ORDER BY id DESC LIMIT ?"; p.append(limit)
        return self.query(sql, tuple(p))

    # ---------- maintenance ----------
    def prune(self):
        try:  # keep the WAL from growing on always-on installs (audit3)
            self.exec("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            pass
        for table, lim in PRUNE_LIMITS.items():
            try:
                if table == "errors":
                    # one row can carry millions of hits - keep the top 500
                    # by hits AND the newest 500 (audit4 #D4)
                    self.exec("DELETE FROM errors WHERE id NOT IN "
                              "(SELECT id FROM errors ORDER BY COALESCE(hits,1) DESC LIMIT 500) "
                              "AND id NOT IN (SELECT id FROM errors ORDER BY id DESC LIMIT 500)")
                    continue
                self.exec(f"DELETE FROM {table} WHERE id NOT IN "
                          f"(SELECT id FROM {table} ORDER BY id DESC LIMIT {lim})")
            except Exception:
                pass
        self.exec("DELETE FROM sessions WHERE expires<?", (time.time(),))
        self.exec("DELETE FROM telemetry WHERE ts<?", (time.time() - 14 * 86400,))


db = DB()
