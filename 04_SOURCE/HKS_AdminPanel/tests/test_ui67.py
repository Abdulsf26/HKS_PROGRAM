"""HKS round-67 tests: recovery codes + /api/recover, /api/docs, diagnostics
bundle, status page, UI-doc statics. Runnable on Linux & Windows.
Run: python -m pytest tests/test_ui67.py -q   (or python tests/test_ui67.py)
"""
import hashlib
import os
import sys
import tempfile
import zipfile
import io

TMP = tempfile.mkdtemp(prefix="hks_ui67_")
os.environ["HKS_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["HKS_DB_PATH"] = os.path.join(TMP, "data", "test.db")
os.environ["HKS_AGENT_TOKEN"] = "ui67-agent-token"

def _panel():
    here = os.path.dirname(os.path.abspath(__file__))
    panel = os.path.dirname(here)
    if os.path.isdir(os.path.join(panel, "server")):
        return panel
    return os.path.normpath(os.path.join(here, "..", "04_SOURCE", "HKS_AdminPanel"))
PANEL = _panel()
sys.path.insert(0, PANEL)
STATIC = os.path.join(PANEL, "server", "static")

from server import security  # noqa: E402
from server.db import db     # noqa: E402
from server.main import app, _status_token  # noqa: E402

def _client():
    from fastapi.testclient import TestClient
    return TestClient(app)

def _mkuser(name):
    if not db.get_user(name):
        db.create_user(name, security.hash_password("OldPass@123"), "admin")
    return db.get_user(name)          # create_user returns nothing - re-fetch

def test_recovery_codes_roundtrip():
    codes = db.gen_recovery_codes(_mkuser("ui67admin")["id"], 3)
    assert len(codes) == 3 and len(set(codes)) == 3
    row = db.redeem_recovery_code(codes[0])
    assert row and row["username"] == "ui67admin"
    assert db.redeem_recovery_code(codes[0]) is None      # one-time: second use refused
    assert db.redeem_recovery_code("bogus-code") is None

def test_recover_endpoint_flow():
    c = _client()
    (code,) = db.gen_recovery_codes(_mkuser("ui67flow")["id"], 1)
    r = c.post("/api/recover", json={"code": "wrong-code", "password": "NewPass@123"})
    assert r.status_code == 401 and not r.json().get("ok")
    r = c.post("/api/recover", json={"code": code, "password": "short"})
    assert r.status_code == 400                            # weak password refused
    r = c.post("/api/recover", json={"code": code, "password": "NewPass@123"})
    assert r.status_code == 200 and r.json().get("ok")
    u = db.get_user("ui67flow")
    assert security.verify_password("NewPass@123", u["pw_hash"])

def test_admin_ip_ban_blocks_that_clients_login():
    from fastapi.testclient import TestClient
    admin = _client()
    db.create_user("ui67ban", security.hash_password("BanPass@123"), "admin")
    login = admin.post("/api/login", json={"username": "ui67ban", "password": "BanPass@123"})
    assert login.status_code == 200
    csrf = login.json()["user"]["csrf"]
    target = "198.51.100.77"
    r = admin.post("/api/security/bans", json={"ip": target, "permanent": True},
                   headers={"x-hks-csrf": csrf})
    assert r.status_code == 200 and r.json().get("ok")
    banned = TestClient(app, client=(target, 54321))
    r = banned.post("/api/login", json={"username": "ui67ban", "password": "BanPass@123"})
    assert r.status_code == 429 and "banned" in r.json()["error"].lower()

def test_qr_route_returns_scannable_svg_payload():
    import importlib.util
    if importlib.util.find_spec("segno") is None:
        return  # the endpoint reports a clear 501 when its optional QR dependency is absent
    r = _client().get("/api/qr.svg", params={"text": "https://example.invalid/pair"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
    assert "<svg" in r.text and "Cache-Control" in r.headers

def test_status_token_gate():
    good = _status_token()
    c = _client()
    assert c.get(f"/status/{good}/json").status_code == 200
    assert c.get("/status/deadbeef/json").status_code == 404
    assert c.get("/status/deadbeef").status_code == 404

def test_docs_and_bundle_need_auth():
    c = _client()
    assert c.get("/api/docs").status_code == 401
    assert c.get("/api/diagnostics/bundle").status_code == 401

def test_docs_live_introspection():
    c = _client()
    db.create_user("ui67doc", security.hash_password("DocPass@123"), "admin")
    r = c.post("/api/login", json={"username": "ui67doc", "password": "DocPass@123"})
    assert r.status_code == 200, r.text
    r = c.get("/api/docs")
    assert r.status_code == 200
    d = r.json()
    paths = {x["path"] for x in d["routes"]}
    assert "/api/recover" in paths and "/api/docs" in paths and "/api/diagnostics/bundle" in paths
    assert d["version"].startswith("7.2.")
    assert d["status_path"].startswith("/status/")

def test_diagnostics_bundle_redacts_secrets():
    c = _client()
    r = c.post("/api/login", json={"username": "ui67doc", "password": "DocPass@123"})
    assert r.status_code == 200
    r = c.get("/api/diagnostics/bundle")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/zip")
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = z.namelist()
    assert "system.json" in names
    if "env.txt" in names:                                  # .env only exists on a real install
        assert "ui67-agent-token" not in z.read("env.txt").decode()
        assert "<redacted>" in z.read("env.txt").decode()

def test_ui_doc_statics_ship():
    assert os.path.isfile(os.path.join(STATIC, "CHANGELOG.md"))
    js = open(os.path.join(STATIC, "app.js"), encoding="utf-8").read()
    assert '/api/captures?kind=login&limit=100' in js
    assert '/api/captures?kind=logon_cam&limit=100' in js

def test_storage_cleanup_is_admin_csrf_gated_and_confined_to_capture_folder():
    c = _client()
    _mkuser("ui67clean")
    login = c.post("/api/login", json={"username": "ui67clean", "password": "OldPass@123"})
    assert login.status_code == 200
    csrf = login.json()["user"]["csrf"]
    from server.db import CAPTURE_DIR
    os.makedirs(CAPTURE_DIR, exist_ok=True)
    name = "cleanup_test_capture.jpg"
    path = os.path.join(CAPTURE_DIR, name)
    with open(path, "wb") as f:
        f.write(b"test-image")
    capture_id = db.add_capture("cleanup-test", "login", name, "")

    denied = c.post("/api/storage/cleanup", json={"category": "captures", "confirm": "DELETE CAPTURES"})
    assert denied.status_code in (401, 403)
    bad_phrase = c.post("/api/storage/cleanup", json={"category": "captures", "confirm": "yes"},
                        headers={"x-hks-csrf": csrf})
    assert bad_phrase.status_code == 400
    done = c.post("/api/storage/cleanup", json={"category": "captures", "confirm": "DELETE CAPTURES"},
                  headers={"x-hks-csrf": csrf})
    assert done.status_code == 200 and done.json()["ok"]
    assert not os.path.exists(path)
    assert not any(row["id"] == capture_id for row in db.list_captures(limit=1000))
    js = open(os.path.join(STATIC, "app.js"), encoding="utf-8").read()
    assert 'c.kind === "logon_cam" ? "Windows sign-in/unlock"' in js
    for p in ("install", "phone", "agent-offline", "reset", "uninstall"):
        assert os.path.isfile(os.path.join(STATIC, "help", p + ".md")), p
    css = open(os.path.join(STATIC, "style.css"), encoding="utf-8").read()
    assert ".lgf-boxin { display: flex" in css.replace("  ", " ") or "min-width: 0" in css
    assert "max-width: 500px" in css and "safe-area-inset-bottom" in css
    js = open(os.path.join(STATIC, "app.js"), encoding="utf-8").read()
    assert "hksErrBox" in js and "HKS_PALETTE" in js and "renderHelpCenter" in js
    assert 'title: "Install or repair"' in js and 'title: "Agent offline"' in js
    assert "v.append(renderSecurityTools())" in js
    assert "buildBottomNav" not in js
    css = open(os.path.join(STATIC, "style.css"), encoding="utf-8").read()
    assert ".hks-help-grid" in css and '"bnav"' not in css
    html = open(os.path.join(STATIC, "index.html"), encoding="utf-8").read()
    assert 'id="bottomnav"' not in html
    assert 'role="button"' in html and "hks-offline-checklist" in html
    import re as _re
    assert _re.search(r'v=\d+', html), html[-400:]   # asset pins bumped each round

def test_reset_bat_ships():
    roots = [os.path.dirname(PANEL)]
    roots.extend(p for p in __import__("pathlib").Path(PANEL).parents
                 if (p / "00_START_HERE").is_dir())
    candidates = [os.path.join(root, "RESET_PASSWORD.bat") for root in roots]
    candidates.extend(os.path.join(root, "00_START_HERE", "RESET_PASSWORD.bat") for root in roots)
    p = next((candidate for candidate in candidates if os.path.isfile(candidate)), candidates[0])
    assert os.path.isfile(p), p
    body = open(p, "rb").read().decode("utf-8", errors="replace")
    assert "RESET ADMIN PASSWORD" in body and "hash_password" in body

if __name__ == "__main__":
    fns = [v for k, v in globals().items() if k.startswith("test_")]
    for f in fns:
        f()
        print("PASS", f.__name__)
    print(f"UI67 TESTS: {len(fns)}/{len(fns)} ALL PASSED")
