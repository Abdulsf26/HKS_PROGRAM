

"""Regression tests for bugs found from REAL Acer error reports:
  usb.device   -> "name '_class_of' is not defined"
  db.tables    -> "No module named 'pymysql'" (raw traceback)
  db.query     -> "empty SQL" (unfriendly)
  power.logoff_console -> "no console session found to log off"
"""
import logging
import os
import sys
import types

# dual-layout (round 62): resolves in the delivered zip (HKS_AdminPanel/tests),
# the repo tree (04_SOURCE/HKS_AdminPanel/tests) AND the 03_TESTS mirrors.
def _hks_panel_client():
    here = os.path.dirname(os.path.abspath(__file__))
    panel = os.path.dirname(here)          # zip/repo sibling layout
    if os.path.isdir(os.path.join(panel, "server")):
        return panel, os.path.join(os.path.dirname(panel), "HKS_Client")
    return (os.path.join(here, "..", "04_SOURCE", "HKS_AdminPanel"),
            os.path.join(here, "..", "04_SOURCE", "HKS_Client"))
PANEL, CLIENT = _hks_panel_client()
_ROOT = os.path.dirname(PANEL)             # 04_SOURCE (repo) or zip root
sys.path.insert(0, PANEL)
sys.path.insert(0, CLIENT)

os.environ.setdefault("HKS_DATA_DIR", os.path.join(PANEL, "_acerfix_tmp"))
os.makedirs(os.environ["HKS_DATA_DIR"], exist_ok=True)


class FakeCtx:
    def __init__(self):
        self.actions, self.events, self.cfg = {}, [], {}
        self.stats = {"started": 0, "cmds": 0, "errors": 0, "latency_ms": None}
        self.log = logging.getLogger("t")

    def send_event(self, k, d=None): self.events.append((k, d))
    def on_binary(self, ch, fn): pass


def test_usb():
    from agent.modules import usb_ctl
    assert hasattr(usb_ctl, "_class_of")
    ctx = FakeCtx(); reg = usb_ctl.register(ctx)
    usb_ctl._ps = lambda cmd, timeout=60: "Mouse"
    try:
        reg["usb.device"](ctx, {"instance_id": r"USB\VID_1&PID_2", "enable": False})
        raise AssertionError("no refusal")
    except PermissionError:
        pass
    print("  usb.device: protected-class refusal OK")


def test_sql():
    from agent.modules import sql_access
    ctx = FakeCtx()
    try:
        sql_access.register(ctx)["db.query"](ctx, {"sql": "  "})
        raise AssertionError("no raise")
    except ValueError as e:
        assert "Type a SQL" in str(e)
    sys.modules["pymysql"] = None  # simulate not installed
    try:
        sql_access.register(ctx)["db.tables"](ctx, {"engine": "mysql", "host": "1.2.3.4", "user": "u"})
        raise AssertionError("no raise")
    except RuntimeError as e:
        assert "pymysql" in str(e) and "pip" in str(e)
    finally:
        sys.modules.pop("pymysql", None)
    import sqlite3 as _sq
    # r53 isolation: other suites overwrite HKS_DATA_DIR mid-run (and rmtree
    # their tmps) — own the dir this test writes to instead of trusting it
    _dbdir = os.path.join(PANEL, "_acerfix_tmp")
    os.makedirs(_dbdir, exist_ok=True)
    os.environ["HKS_DATA_DIR"] = _dbdir
    _dbpath = os.path.join(_dbdir, "t.db")
    _c = _sq.connect(_dbpath); _c.execute("CREATE TABLE IF NOT EXISTS t (x)"); _c.close()
    out = sql_access.register(ctx)["db.query"](ctx, {"sql": "SELECT 1 AS one",
                                                     "path": _dbpath})
    assert out["rows"] == [[1]], out
    print("  db: friendly errors + sqlite round trip OK")


def test_logoff():
    from agent.modules import power_ctl
    ctx = FakeCtx(); reg = power_ctl.register(ctx); calls = []
    # audit4: quser/qwinsta are queried FIRST; 'logoff console' is the
    # fallback when no concrete console session id is found
    power_ctl._run = lambda cmd: calls.append(cmd) or ""
    reg["power.logoff_console"](ctx, {})
    assert "logoff console" in calls
    calls.clear()

    def fake(cmd):
        calls.append(cmd)
        if cmd == "logoff console":
            return "error: session console not found"
        if cmd == "quser":
            return " USERNAME  SESSIONNAME ID STATE\n>boss console 3 Active\n"
        return "ok"
    power_ctl._run = fake
    assert reg["power.logoff_console"](ctx, {})["logoff"] == "3"

    power_ctl._run = lambda cmd: "error" if cmd.startswith("logoff") else "none"
    try:
        reg["power.logoff_console"](ctx, {})
        raise AssertionError("no raise")
    except RuntimeError as e:
        assert "No logged-on console user" in str(e)
    print("  power.logoff_console: console-first + fallback + clear error OK")


def test_ui_and_field_round():
    static = os.path.join(PANEL, "server", "static")  # dual-layout (round 62)
    js = open(static + "/app.js", encoding="utf-8").read()
    css = open(static + "/style.css", encoding="utf-8").read()
    scr = open(static + "/screen.html", encoding="utf-8").read()
    mm = open(os.path.join(PANEL, "server", "main.py"), encoding="utf-8").read()
    desk = open(os.path.join(CLIENT, "agent", "session_modules", "desktop.py"), encoding="utf-8").read()
    assert "auth-race guard" in js and '"/api/me"' in js
    assert "wsTries" in js and "ev.code === 4401" in js
    assert "/api/telemetry" in js and "document.hidden" in js
    assert "HKS_screen_" in js and "Discard" in js
    assert "power.lock_screen" in js and "secure sequence" in js
    assert "touchstart" in js and "touchmove" in js
    assert "toNat" in scr and "touchstart" in scr and "requestFullscreen" in scr
    assert 'id="inputOn" checked' not in scr and 'api("input.enable"' in scr
    assert "inputToggle.checked = false" in scr and "input_active" in scr
    assert "function liveText()" in scr and "if (!bootstrapReady) return" in scr
    assert 'role="alert"' in scr and "releaseHeld" in scr and "touchcancel" in scr
    assert "flex-wrap:wrap" in scr and "wsDead = true" in scr
    assert "screen.html" in js
    assert "isSecureContext" in js
    for tok in ("--bg", "--primary", "--success", "--danger", "--sidebar"):
        assert tok in css
    assert "CF_DIB" in desk and "CF_HDROP" in desk and "HTML Format" in desk
    assert "_login_capture" in mm and "capture_login_failed" in mm
    assert '"Cache-Control"] = "no-cache"' in mm  # middleware owns it
    print("  UI/field round: auth-race, ws backoff, telemetry poll, shot save,")
    print("  Win+L Lock API, touch, screen.html, dark tokens, clipboard, login-capture")


if __name__ == "__main__":
    test_usb(); test_sql(); test_logoff(); test_ui_and_field_round()
    print("ALL ACER-FIX TESTS PASSED")
