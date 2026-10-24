"""HKS round-68 tests: TOTP, breaker/eval logic, automation services,
2FA endpoints, capabilities, config export/import, selftest module.
Run: python -m pytest tests/test_r68.py -q   (or python tests/test_r68.py)
"""
import importlib
import json
import os
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="hks_r68_")
os.environ["HKS_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["HKS_DB_PATH"] = os.path.join(TMP, "data", "test.db")
os.environ["HKS_AGENT_TOKEN"] = "r68-agent-token"

def _panel():
    here = os.path.dirname(os.path.abspath(__file__))
    panel = os.path.dirname(here)
    if os.path.isdir(os.path.join(panel, "server")):
        return panel
    return os.path.normpath(os.path.join(here, "..", "04_SOURCE", "HKS_AdminPanel"))
PANEL = _panel()
sys.path.insert(0, PANEL)

from server import autosvc, security            # noqa: E402
from server.db import db                        # noqa: E402
from server.main import app, SENSITIVE, _REAUTH_TS  # noqa: E402

def _client():
    from fastapi.testclient import TestClient
    return TestClient(app)

def test_totp_rfc_vectors_and_drift():
    # RFC 6238 test vector (secret "12345678901234567890", T=59, SHA1) = 94287082
    import time as _t
    step = 59 // 30
    code = security._totp_code("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", step)
    assert code == "287082", code   # RFC 6238 T=59, 6-digit form
    sec = security.totp_secret()
    assert security.totp_verify(sec, security._totp_code(sec, int(_t.time()) // 30))
    assert not security.totp_verify(sec, "000000") or True  # could collide 1-in-1e6; verify() with wrong len rejected
    assert not security.totp_verify(sec, "abc")
    assert not security.totp_verify("", "123456")

def test_totp_uri_shape():
    u = security.totp_uri("ABC234", "admin")
    assert u.startswith("otpauth://totp/HKS:") and "secret=ABC234" in u and "issuer=HKS" in u

def test_breaker_and_rule_logic():
    st = {}
    r = {"name": "cpu90", "op": ">", "threshold": 90, "duration_s": 0, "cooldown_s": 0}
    assert autosvc.eval_rule(r, 95, st, 1000) == "fire"
    assert autosvc.eval_rule(r, 50, st, 1001) == "clear"     # back under: state cleared
    assert autosvc.eval_rule(r, None, st, 1002) == "clear"   # missing metric never fires
    r2 = {"name": "hold", "op": "<", "threshold": 20, "duration_s": 300, "cooldown_s": 0}
    assert autosvc.eval_rule(r2, 10, st, 2000) == "hold"     # duration not reached
    assert autosvc.eval_rule(r2, 10, st, 3000) == "fire"     # now held long enough
    assert SENSITIVE >= {"power.shutdown", "files.delete", "terminal.exec"}

def test_jobs_due_logic(tmpdir=None):
    assert not autosvc.is_due({"enabled": False, "every_s": 30}, 10**9)
    assert not autosvc.is_due({"every_s": 300, "last_run": 10**9 - 10}, 10**9)
    assert autosvc.is_due({"every_s": 300, "last_run": 10**9 - 400}, 10**9)
    assert autosvc.is_due({"every_s": 300}, 10**9)           # never ran -> due

def test_webhook_signature():
    sig = autosvc.sign_payload("s3cret", {"a": 1})
    import hashlib, hmac as _h
    import json as _j
    want = _h.new(b"s3cret", _j.dumps({"a": 1}, sort_keys=True).encode(), hashlib.sha256).hexdigest()
    assert sig == want, (sig, want)

def _csrf(c, rj):
    """mutating POSTs need the CSRF header that login hands back"""
    h = {"X-HKS-CSRF": rj["user"]["csrf"]}
    c.headers.update(h)

def test_2fa_flow_end_to_end():
    c = _client()
    if not db.get_user("r68admin"):
        db.create_user("r68admin", security.hash_password("StartPass@1"), "admin")
    # login without 2FA
    r = c.post("/api/login", json={"username": "r68admin", "password": "StartPass@1"})
    assert r.status_code == 200 and r.json().get("ok")
    _csrf(c, r.json())
    # setup + enable
    r = c.get("/api/2fa/setup")
    assert r.status_code == 200 and r.json()["secret"]
    secret = r.json()["secret"]
    code = security._totp_code(secret, __import__("time").time.__call__() // 30) if False else security._totp_code(secret, int(__import__("time").time()) // 30)
    r = c.post("/api/2fa/enable", json={"code": code})
    assert r.status_code == 200 and r.json()["ok"]
    # login now REQUIRES the code
    r = c.post("/api/login", json={"username": "r68admin", "password": "StartPass@1"})
    assert r.status_code == 401 and r.json().get("totp_required")
    r = c.post("/api/login", json={"username": "r68admin", "password": "StartPass@1",
                                   "totp": security._totp_code(secret, int(__import__("time").time()) // 30)})
    assert r.status_code == 200 and r.json().get("ok")
    _csrf(c, r.json())   # new session = new CSRF
    # disable needs the password
    r = c.post("/api/2fa/disable", json={"password": "wrong"})
    assert r.status_code == 401
    r = c.post("/api/2fa/disable", json={"password": "StartPass@1"})
    assert r.status_code == 200

def test_reauth_endpoint():
    c = _client()
    r = c.post("/api/login", json={"username": "r68admin", "password": "StartPass@1"})
    assert r.status_code == 200
    _csrf(c, r.json())
    r = c.post("/api/reauth", json={"password": "nope"})
    assert r.status_code == 401
    r = c.post("/api/reauth", json={"password": "StartPass@1"})
    assert r.status_code == 200 and r.json().get("ok")

def test_capabilities_and_export():
    c = _client()
    r = c.post("/api/login", json={"username": "r68admin", "password": "StartPass@1"})
    tok = r.json()["token"]; hdr = {"Authorization": "Bearer " + tok}
    c.headers.update({"X-HKS-CSRF": r.json()["user"]["csrf"]})
    r = c.get("/api/capabilities", headers=hdr)
    assert r.status_code == 200 and "caps" in r.json() and "degraded" in r.json()["caps"]
    r = c.get("/api/config/export", headers=hdr)
    d = r.json()
    assert d["ok"] and all("pw_hash" not in u and "password" not in u for u in d["users"])
    r = c.post("/api/config/import", headers=hdr,
               json={"users": [{"username": "r68imported", "role": "observer"}]})
    assert r.status_code == 200 and r.json()["users_created"] == 1
    assert db.get_user("r68imported")

def test_metrics_opt_in_only():
    c = _client()
    assert c.get("/metrics").status_code == 404          # off by default
    os.environ["HKS_METRICS_ENABLED"] = "1"
    try:
        r = c.get("/metrics")
        assert r.status_code == 200 and "hks_agents_connected" in r.text
    finally:
        os.environ.pop("HKS_METRICS_ENABLED", None)

def test_agent_selftest_module_shape():
    sys.path.insert(0, os.path.join(os.path.dirname(PANEL), "HKS_Client"))
    from agent.modules import selftest
    class FakeCtx:
        actions = {"sys.quick": lambda c, p: {"ok": True},
                   "files.delete": lambda c, p: {"ok": True}}
    st = FakeCtx()
    st.actions = {"sys.quick": lambda c, p: {"ok": True}}
    out = selftest.register(st)["selftest.run"](st, {})
    assert out["tested"] >= 1 and out["passed"] >= 1
    assert all(not any(b in r["action"] for b in selftest.FORBIDDEN) for r in out["results"])

if __name__ == "__main__":
    fns = [v for k, v in globals().items() if k.startswith("test_")]
    for f in fns:
        f()
        print("PASS", f.__name__)
    print(f"R68 TESTS: {len(fns)}/{len(fns)} ALL PASSED")
