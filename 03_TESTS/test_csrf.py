

def _hks_root():
    # dual-layout (round 62): walk up to 04_SOURCE (repo, any depth) or stop at
    # the delivered-zip root (the dir that contains HKS_AdminPanel)
    d = os.path.dirname(os.path.abspath(__file__))
    while not os.path.isdir(os.path.join(d, "04_SOURCE")):
        if os.path.isdir(os.path.join(d, "HKS_AdminPanel")):
            return d  # delivered zip root
        pd = os.path.dirname(d)
        if pd == d:
            raise RuntimeError("HKS project root not found")
        d = pd
    return d
"""CSRF regression — reproduces the field error "bad CSRF token" and locks
in the researched fix (OWASP synchronizer token + SPA 403 recovery +
session rotation on login):

Field mechanics: every login mints a NEW session+cookie. A browser keeps
ONE cookie jar per host, so after ANY second login the already-open page
still holds the OLD csrf while the jar sends the NEW cookie ->
403 "bad CSRF token" and the page was stuck until a manual reload.

Fix under test:
  S1 login never requires CSRF (credentials ARE the auth)
  S2 mutating calls without/with-wrong csrf -> 403; with right one -> pass
  S3 GET /api/me returns the session's LIVE csrf without rotating it
     (GetOrCreate semantics — rotation on read is the classic multi-tab bug)
  S4 login ROTATES the presented session (old row deleted — fixation defense)
  S5 the recovery path: re-sync csrf via /api/me, retry once -> passes
  S6 logout invalidates the csrf immediately
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import http.cookiejar
import urllib.error
import urllib.request

HERE = _hks_root()
if os.path.isdir(os.path.join(HERE, "HKS_AdminPanel", "server")):
    ROOT = HERE  # zip root or 04_SOURCE - HKS_AdminPanel is a child
else:            # 03_TESTS twin: 04_SOURCE sits next to this folder
    ROOT = os.path.join(HERE, "04_SOURCE")
PANEL = os.path.join(ROOT, "HKS_AdminPanel")
PORT = 8978
BASE = f"http://127.0.0.1:{PORT}"
USER, PASS = "admin", "HksAdmin#2026"


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def new_opener():
    cj = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj)), cj


def post(op, path, data=None, csrf=None):
    h = {"Content-Type": "application/json"}
    if csrf is not None:
        h["X-HKS-CSRF"] = csrf
    req = urllib.request.Request(BASE + path,
                                 data=json.dumps(data or {}).encode(),
                                 headers=h, method="POST")
    try:
        return json.loads(op.open(req).read())
    except urllib.error.HTTPError as e:
        return {"status": e.code, **json.loads(e.read())}


def get(op, path):
    try:
        return json.loads(op.open(BASE + path).read())
    except urllib.error.HTTPError as e:
        return {"status": e.code, **json.loads(e.read())}


def cookie_of(cj):
    return next((c.value for c in cj if c.name == "hks_session"), None)


def auth_passed(r):
    """503 'no agent connected' arrives AFTER auth -> auth+CSRF layer
    passed (this suite runs without agents on purpose)."""
    return r.get("status") not in (401, 403)


def main():
    port = free_port()
    global BASE
    BASE = f"http://127.0.0.1:{port}"
    data_dir = tempfile.mkdtemp(prefix="hks_csrf_")
    # pre-create the admin user BEFORE the server starts (runbook pattern)
    sys.path.insert(0, PANEL)
    os.environ["HKS_DATA_DIR"] = data_dir
    from server.db import db
    from server.security import hash_password
    db.create_user(USER, hash_password(PASS), role="admin")

    env = dict(os.environ, HKS_DATA_DIR=data_dir)
    proc = subprocess.Popen([sys.executable, "-m", "server.main",
                             "--host", "127.0.0.1", "--port", str(port)],
                            cwd=PANEL, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(BASE + "/", timeout=1)
                break
            except Exception:
                time.sleep(0.3)
        else:
            raise RuntimeError("panel did not start")

        op, cj = new_opener()

        # S1: login works, and works even when already "logged in"
        j1 = post(op, "/api/login", {"username": USER, "password": PASS})
        assert j1.get("ok") and j1["user"]["csrf"], j1
        csrf1, cookie1 = j1["user"]["csrf"], cookie_of(cj)
        # login again WITH the old cookie present (login must never demand CSRF)
        j1b = post(op, "/api/login", {"username": USER, "password": PASS})
        assert j1b.get("ok"), f"login demanded CSRF: {j1b}"
        csrf1, cookie1 = j1b["user"]["csrf"], cookie_of(cj)
        print("  S1 login never requires CSRF (even mid-session): PASS")

        # S2: mutations are csrf-guarded
        r = post(op, "/api/cmd", {"action": "ping", "params": {}}, csrf=None)
        assert r.get("status") == 403 and "CSRF" in r.get("error", ""), r
        r = post(op, "/api/cmd", {"action": "ping", "params": {}}, csrf="WRONG")
        assert r.get("status") == 403 and "CSRF" in r.get("error", ""), r
        r = post(op, "/api/cmd", {"action": "ping", "params": {}}, csrf=csrf1)
        assert auth_passed(r), r
        print("  S2 mutating calls guarded (none/wrong -> 403, right -> pass): PASS")

        # S3: /api/me returns the LIVE csrf without rotating it
        me = get(op, "/api/me")
        assert me["user"]["csrf"] == csrf1, (me, csrf1)
        me2 = get(op, "/api/me")
        assert me2["user"]["csrf"] == csrf1, "csrf rotated on read!"
        print("  S3 /api/me returns live csrf, never rotates on read: PASS")

        # FIELD CASE: second login in the same browser -> old page holds
        # csrf_old but the jar now carries the new cookie
        j2 = post(op, "/api/login", {"username": USER, "password": PASS})
        csrf2, cookie2 = j2["user"]["csrf"], cookie_of(cj)
        assert cookie2 != cookie1, "session was not rotated"
        r = post(op, "/api/cmd", {"action": "ping", "params": {}}, csrf=csrf1)
        assert r.get("status") == 403 and "CSRF" in r.get("error", ""), r
        print("  S4 second login rotates the session (old cookie row dead): PASS")
        print("  FIELD CASE reproduced: stale page + new cookie -> 403 bad CSRF")

        # S5: THE RECOVERY (what the fixed app.js now does automatically):
        # re-sync csrf from /api/me with whatever cookie the jar holds,
        # then retry once -> passes
        me = get(op, "/api/me")
        assert me.get("ok") and me["user"]["csrf"] == csrf2, me
        r = post(op, "/api/cmd", {"action": "ping", "params": {}},
                 csrf=me["user"]["csrf"])
        assert auth_passed(r), r
        print("  S5 recovery: re-sync csrf -> retry once -> PASSES: PASS")

        # the rotated (old) cookie itself must be dead everywhere
        op3, cj3 = new_opener()
        req = urllib.request.Request(BASE + "/api/me")
        req.add_header("Cookie", "hks_session=" + cookie1)
        try:
            j3 = json.loads(op3.open(req).read())
            assert j3.get("user") is None, f"rotated cookie still valid: {j3}"
        except urllib.error.HTTPError as e:
            assert e.code == 401
        r = post(op3, "/api/cmd", {"action": "ping", "params": {}}, csrf=csrf1)
        assert r.get("status") in (401, 403), r
        print("  S5b rotated session rejected (fixation defense): PASS")

        # S6: logout kills the csrf immediately
        r = post(op, "/api/logout", {}, csrf=csrf2)
        assert r.get("ok"), r
        r = post(op, "/api/cmd", {"action": "ping", "params": {}}, csrf=csrf2)
        assert r.get("status") == 401, r
        print("  S6 logout invalidates the session csrf: PASS")

        # client implementation must contain the recovery + reset
        app = open(os.path.join(PANEL, "server/static/app.js")).read()
        assert "syncCsrf" in app and "_noretry" in app and "/csrf/i.test" in app
        assert 'CSRF = "";' in app  # showLogin resets stale token
        main_src = open(os.path.join(PANEL, "server/main.py")).read()
        assert "db.delete_session(old_tok)" in main_src  # rotation on login
        print("  S7 client recovery + server rotation present in code: PASS")

        print("\nCSRF REGRESSION: ALL PASSED")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
