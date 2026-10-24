"""SQL access: local SQLite files + MySQL (incl. AWS RDS)."""
import json
import os
import sqlite3


def _connect(p):
    engine = p.get("engine", "sqlite")
    if engine == "sqlite":
        path = p.get("path") or "C:\\HKS_PROGRAM\\data\\app.db"
        # sqlite3.connect would silently CREATE an empty file here — the
        # user then saw "no tables" and thought SQL was broken. Fail with
        # a clear message instead (field fix 2026-09).
        if not os.path.isfile(path):
            raise FileNotFoundError(
                f"SQLite file not found: {path} — use 'Find DB files on laptop' "
                "in the panel to locate real .db/.sqlite files")
        conn = sqlite3.connect(path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn, engine
    else:
        try:
            import pymysql  # optional dependency (pre-installed by setup)
        except ImportError:
            raise RuntimeError("MySQL support needs the pymysql package. "
                               r"Install once: C:\HKS_PROGRAM\venv\Scripts\pip.exe install pymysql  (SQLite needs nothing extra)")
        conn = pymysql.connect(host=p.get("host"), port=int(p.get("port", 3306)),
                               user=p.get("user"), password=p.get("password", ""),
                               database=p.get("database") or None,
                               connect_timeout=10, cursorclass=pymysql.cursors.Cursor)
        return conn, engine


def _apply_defaults(ctx, p):
    """Fill host/user/etc from saved defaults when the panel omits them."""
    if p.get("engine") == "mysql":
        d = ctx.cfg.get("mysql_defaults", {})
        for k in ("host", "port", "user", "password", "database"):
            p.setdefault(k, d.get(k))
    return p


def register(ctx):
    def db_tables(c, p):
        p = _apply_defaults(c, p)
        conn, engine = _connect(p)
        try:
            cur = conn.cursor()
            if engine == "sqlite":
                cur.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view') ORDER BY name")
            else:
                cur.execute("SHOW TABLES")
            return {"tables": [r[0] for r in cur.fetchall()][:500]}
        finally:
            conn.close()

    def db_query(c, p):
        p = _apply_defaults(c, p)
        sql = (p.get("sql") or "").strip()
        if not sql:
            raise ValueError("Type a SQL query first (example: SELECT * FROM users)")
        conn, engine = _connect(p)
        try:
            cur = conn.cursor()
            cur.execute(sql)
            if sql.lower().lstrip("(").startswith(("select", "show", "pragma", "explain", "describe", "with")):  # round 61: CTEs are reads
                cols = [d[0] for d in cur.description]
                rows = cur.fetchmany(500)
                out = {"columns": cols, "rows": [list(map(_jsonable, r)) for r in rows],
                       "count": len(rows)}
            else:
                conn.commit()
                out = {"affected": cur.rowcount}
            return out
        finally:
            conn.close()

    def db_save_defaults(c, p):
        if p.get("mysql_defaults"):
            c.cfg["mysql_defaults"] = p["mysql_defaults"]
        if p.get("sqlite_defaults"):
            c.cfg["sqlite_defaults"] = p["sqlite_defaults"]
        c.cfg.save()
        return {"saved": True}

    def db_find(c, p):
        """Find SQLite database files on this laptop (for the SQL panel's
        picker). Walks a few user-facing roots, depth-capped, never walks
        into system/namespace junk, and caps results at 100."""
        exts = (".db", ".sqlite", ".sqlite3", ".db3")
        seeds = [p.get("path"), os.path.expanduser("~"), "C:\\HKS_PROGRAM"]
        roots = []
        for s in seeds:
            if s and os.path.isdir(s) and s.lower() not in [r.lower() for r in roots]:
                roots.append(s)
        skip_dirs = {"node_modules", "__pycache__", ".git", "windows", "winsxs",
                     "system32", "syswow64", "recycler", "$recycle.bin",
                     "appdata\\local\\temp", "site-packages"}
        found, errors = [], 0
        for root in roots[:3]:
            base_depth = root.rstrip("\\").count("\\")
            for dirpath, dirnames, filenames in os.walk(root, onerror=lambda e: None):
                try:
                    if dirpath.count("\\") - base_depth >= 4:
                        dirnames[:] = []
                    dirnames[:] = [d for d in dirnames
                                   if d.lower() not in skip_dirs
                                   and not d.startswith(("$", "."))]
                    for fn in filenames:
                        if fn.lower().endswith(exts):
                            fp = os.path.join(dirpath, fn)
                            try:
                                found.append({"path": fp,
                                              "size": os.path.getsize(fp)})
                            except OSError:
                                pass
                            if len(found) >= 100:
                                return {"items": found, "truncated": True}
                except Exception:
                    errors += 1
        return {"items": found, "errors": errors}

    return {"db.tables": db_tables, "db.query": db_query, "db.find": db_find,
            "db.save_defaults": db_save_defaults}


def _jsonable(v):
    try:
        json.dumps(v)
        return v
    except Exception:
        return str(v)
