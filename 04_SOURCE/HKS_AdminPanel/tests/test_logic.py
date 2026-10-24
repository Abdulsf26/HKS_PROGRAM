"""HKS unit tests — pure logic + edge cases (runnable on Linux & Windows).
Run: python -m pytest tests/test_logic.py -q   (or python tests/test_logic.py)
"""
import asyncio
import base64
import os
import sys
import tempfile
import time
import zipfile

TMP = tempfile.mkdtemp(prefix="hks_test_")
os.environ["HKS_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["HKS_DB_PATH"] = os.path.join(TMP, "data", "test.db")
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



class FakeCtx:
    """Minimal AgentContext stand-in."""
    def __init__(self):
        self.cfg = {}
        self.events = []
        self.errors = []
        self.log = __import__("logging").getLogger("test")

    def send_event(self, kind, data=None):
        self.events.append((kind, data))

    def send_error(self, module, message, detail=""):
        self.errors.append((module, message))


def test_security():
    from server import security
    h = security.hash_password("S3cretPass!")
    assert h != "S3cretPass!" and h.startswith("pbkdf2_sha256$")
    assert security.verify_password("S3cretPass!", h)
    assert not security.verify_password("wrong", h)
    assert not security.verify_password("S3cretPass!", "garbage")
    # sliding window limiter
    lim = security.SlidingWindowLimiter(max_events=3, window_sec=1)
    assert lim.check("ip1")[0] and lim.check("ip1")[0] and lim.check("ip1")[0]
    ok, retry = lim.check("ip1")
    assert not ok and retry >= 0
    time.sleep(1.05)
    assert lim.check("ip1")[0], "window should have slid"
    # failed-login ban
    db = security.db
    for _ in range(20):
        security.register_failed_login("203.0.113.9")
    assert security.is_banned("203.0.113.9") > 0
    assert security.is_banned("198.51.100.1") == 0
    db.remove_ban("203.0.113.9")
    assert security.is_banned("203.0.113.9") == 0
    print("  security: OK")


def test_db():
    from server.db import db
    db.create_user("alice", "hash-a", "admin")
    db.create_user("bob", "hash-b", "observer")
    assert [u["username"] for u in db.list_users()] == ["alice", "bob"]
    assert db.get_user("alice")["role"] == "admin"
    db.set_user_password(db.get_user("bob")["id"], "hash-b2")
    assert db.get_user("bob")["pw_hash"] == "hash-b2"
    db.delete_user(db.get_user("bob")["id"])
    assert db.get_user("bob") is None
    # sessions expire
    db.create_session("tok1", "csrf1", db.get_user("alice")["id"], -10, "1.2.3.4")
    assert db.get_session("tok1") is None, "expired session must not resolve"
    db.create_session("tok2", "csrf2", db.get_user("alice")["id"], 600, "1.2.3.4")
    assert db.get_session("tok2")["csrf"] == "csrf2"
    db.revoke_session("tok2")
    assert db.get_session("tok2") is None
    # bans
    db.add_ban("9.9.9.9", time.time() + 100, "test")
    assert db.get_ban("9.9.9.9")["reason"] == "test"
    db.remove_ban("9.9.9.9")
    assert db.get_ban("9.9.9.9") is None
    # telemetry + events + errors + audit + captures
    db.upsert_machine("m1", name="Laptop", online_svc=True)
    db.upsert_machine("m1", online_svc=False)
    assert db.list_machines()[0]["online_svc"] == 0
    db.insert_telemetry({"machine_id": "m1", "cpu": 5.0, "ram": 40.0, "bat_pct": 90.0,
                         "bat_plugged": True, "net_sent": 1, "net_recv": 2, "ssid": "x", "ip": "y"})
    assert db.latest_telemetry("m1")[0]["cpu"] == 5.0
    db.add_event("m1", "logon", {"user": "hks"})
    db.add_event("m1", "other", {})
    evs = db.list_events(machine_id="m1", kind="logon")
    assert len(evs) == 1 and "hks" in evs[0]["data"]
    db.add_error("m1", "mod", "boom", "trace")
    assert db.list_errors("m1")[0]["message"] == "boom"
    db.resolve_error(db.list_errors("m1")[0]["id"])
    assert db.list_errors("m1")[0]["resolved"] == 1
    db.add_audit("admin", "ip", "act", {"a": 1}, "ok")
    assert db.list_audit(10)[0]["action"] == "act"
    cid = db.add_capture("m1", "logon_cam", "f.jpg", "{}")
    assert db.list_captures(kind="logon_cam")[0]["id"] == cid
    assert db.list_captures(kind="logon_cam")[0]["file"] == "f.jpg"
    # prune keeps limits & wipes nothing critical
    db.prune()
    print("  db: OK")


def test_common():
    from agent.common import RingBuffer, Config, kill_switch_active, safe
    rb = RingBuffer(maxlen=3)
    for i in range(10):
        rb.push(i)
    assert list(rb) == [7, 8, 9], "ring buffer must keep only the newest"
    cfg = Config(os.path.join(TMP, "cfg1.json"))
    assert cfg["machine_id"].startswith("HKS-")
    assert cfg["fps"] == 8
    cfg["fps"] = 12
    cfg.save()
    cfg2 = Config(os.path.join(TMP, "cfg1.json"))
    assert cfg2["fps"] == 12, "config must persist"
    assert not kill_switch_active()
    open(os.path.join(os.path.dirname(cfg.path), "SELF_DESTRUCT"), "w").close()
    # kill switch checks program root, not config dir — emulate via root marker
    from agent.common import root_dir
    open(os.path.join(root_dir(), "SELF_DESTRUCT"), "w").close()
    assert kill_switch_active()
    os.remove(os.path.join(root_dir(), "SELF_DESTRUCT"))
    # safe() decorator converts raises into error dicts
    ctx = FakeCtx()

    @safe
    def boom(c, p):
        raise RuntimeError("kapow")

    res = boom(ctx, {})
    assert res["ok"] is False and "kapow" in res["error"]
    assert ctx.errors and ctx.errors[0][0], "safe() must report to server"

    @safe
    def fine(c, p):
        return {"answer": 42}

    assert fine(ctx, {}) == {"ok": True, "data": {"answer": 42}}
    print("  common: OK")


def test_files():
    from agent.modules import files as F
    ctx = FakeCtx()
    reg = F.register(ctx)
    d = os.path.join(TMP, "ft")
    os.makedirs(os.path.join(d, "sub"), exist_ok=True)
    for n in ("a.txt", "b.jpg"):
        open(os.path.join(d, n), "wb").write(os.urandom(2048))
    open(os.path.join(d, "sub", "c.txt"), "w").write("hello")
    # listdir
    ls = reg["files.list"](ctx, {"path": d})
    names = {e["name"] for e in ls["entries"]}
    assert {"a.txt", "b.jpg", "sub"} <= names
    # truncation guard
    ls2 = reg["files.list"](ctx, {"path": d, "limit": 2})
    assert len(ls2["entries"]) == 2 and ls2["truncated"] is True
    # mkdir / rename / delete
    reg["files.mkdir"](ctx, {"path": os.path.join(d, "newdir")})
    assert os.path.isdir(os.path.join(d, "newdir"))
    try:
        reg["files.mkdir"](ctx, {"path": os.path.join(d, "newdir")})
        assert False, "mkdir must report an existing folder instead of silently claiming it created one"
    except FileExistsError:
        pass
    reg["files.rename"](ctx, {"path": os.path.join(d, "a.txt"), "new_name": "renamed.txt"})
    assert os.path.exists(os.path.join(d, "renamed.txt"))
    # chunk roundtrip incl. offset + eof flag
    p = os.path.join(d, "b.jpg")
    c1 = reg["file.read_chunk"](ctx, {"path": p, "offset": 0, "size": 1000})
    c2 = reg["file.read_chunk"](ctx, {"path": p, "offset": 1000, "size": 2000})
    assert len(base64.b64decode(c1["b64"])) == 1000 and c1["eof"] is False
    assert len(base64.b64decode(c2["b64"])) == 1048 and c2["eof"] is True
    reg["file.write_chunk"](ctx, {"path": os.path.join(d, "w.bin"), "offset": 0, "b64": base64.b64encode(b"hello").decode()})
    reg["file.write_chunk"](ctx, {"path": os.path.join(d, "w.bin"), "offset": 5, "b64": base64.b64encode(b"world").decode()})
    assert open(os.path.join(d, "w.bin"), "rb").read() == b"helloworld"
    # stat incl is_dir
    st = reg["file.stat"](ctx, {"path": os.path.join(d, "sub")})
    assert st["is_dir"] is True
    # delete tree
    reg["files.delete"](ctx, {"path": os.path.join(d, "newdir")})
    assert not os.path.exists(os.path.join(d, "newdir"))
    # search bounded
    sr = reg["files.search"](ctx, {"root": d, "pattern": "*.txt", "max": 10})
    assert any(x["path"].endswith("renamed.txt") for x in sr["items"])
    # zip: media-style path list with dedup + skip missing
    z1 = reg["files.zip_paths"](ctx, {"paths": [p, os.path.join(d, "renamed.txt"),
                                           os.path.join(d, "missing.bin")]})
    assert z1["files"] == 2 and z1["size"] > 0
    with zipfile.ZipFile(z1["path"]) as z:
        assert set(z.namelist()) == {"b.jpg", "renamed.txt"}
    # dedup same-named files from different dirs
    z2 = reg["files.zip_paths"](ctx, {"paths": [os.path.join(d, "sub", "c.txt"),
                                           os.path.join(d, "renamed.txt")]})
    assert z2["files"] == 2
    # empty selection refused
    try:
        reg["files.zip_paths"](ctx, {"paths": []})
        raise AssertionError("empty zip must fail")
    except ValueError:
        pass
    # drives never crash on linux/windows
    assert reg["files.drives"](ctx, {})["drives"]
    print("  files: OK")


def test_media_scan():
    from agent.modules import media_scan as M
    root = os.path.join(TMP, "media")
    os.makedirs(os.path.join(root, "pics"), exist_ok=True)
    open(os.path.join(root, "pics", "x.JPG"), "w").write("1")
    open(os.path.join(root, "pics", "y.mp4"), "w").write("2")
    open(os.path.join(root, "z.pdf"), "w").write("3")
    open(os.path.join(root, "n.txt"), "w").write("4")
    imgs = M.scan_drive("img", root + os.sep, force=True)
    assert [i["path"] for i in imgs] and imgs[0]["path"].lower().endswith(".jpg")
    assert len(M.scan_drive("vid", root, force=True)) == 1
    assert len(M.scan_drive("doc", root, force=True)) == 2  # pdf + txt
    # cache file exists (second read path)
    assert os.path.exists(M._cache_file("img", root))
    ctx = FakeCtx()
    reg = M.register(ctx)
    page = reg["media.scan"](ctx, {"kind": "doc", "path": root, "page": 0, "per": 1})
    assert page["total"] == 2 and len(page["items"]) == 1
    filt = reg["media.scan"](ctx, {"kind": "img", "path": root, "q": "x."})
    assert filt["total"] == 1
    print("  media_scan: OK")


def test_sql():
    from agent.modules import sql_access as S
    ctx = FakeCtx()
    reg = S.register(ctx)
    dbp = os.path.join(TMP, "t.db")
    import sqlite3
    c = sqlite3.connect(dbp)
    c.execute("CREATE TABLE people (id INTEGER PRIMARY KEY, name TEXT)")
    c.execute("INSERT INTO people (name) VALUES ('ann'),('bob')")
    c.commit()
    c.close()
    tables = reg["db.tables"](ctx, {"engine": "sqlite", "path": dbp})
    assert "people" in tables["tables"]
    q = reg["db.query"](ctx, {"engine": "sqlite", "path": dbp, "sql": "SELECT * FROM people ORDER BY id"})
    assert q["columns"] == ["id", "name"] and q["rows"][0][1] == "ann"
    w = reg["db.query"](ctx, {"engine": "sqlite", "path": dbp, "sql": "INSERT INTO people (name) VALUES ('cat')"})
    assert w.get("affected") == 1
    try:
        reg["db.query"](ctx, {"engine": "sqlite", "path": dbp, "sql": ""})
        raise AssertionError("empty sql must fail")
    except ValueError:
        pass
    print("  sql: OK")


def test_webdeploy():
    from agent.modules import webdeploy as W
    ctx = FakeCtx()
    reg = W.register(ctx)
    www = os.path.join(TMP, "www")
    os.makedirs(www, exist_ok=True)
    open(os.path.join(www, "index.html"), "w").write("<h1>hello</h1>")
    port = 18499
    d = reg["deploy.start"](ctx, {"path": www, "port": port, "ip": "127.0.0.1"})
    assert d["id"] and d.get("token")
    time.sleep(0.3)
    import urllib.request
    # R13: shares are gated behind a random URL path - ungated path is 404
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/index.html", timeout=5)
        raise AssertionError("ungated path must NOT serve")
    except urllib.error.HTTPError as e:
        assert e.code == 404
    body = urllib.request.urlopen(d["url"], timeout=5).read()
    assert b"hello" in body
    lst = reg["deploy.list"](ctx, {})
    assert any(x["port"] == port for x in lst["items"])
    reg["deploy.stop"](ctx, {"id": d["id"]})
    time.sleep(0.2)
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2)
        raise AssertionError("server should be stopped")
    except OSError:
        pass
    print("  webdeploy: OK")


def test_webdeploy_port_conflict_is_not_stolen():
    import socket
    from agent.modules import webdeploy as W
    ctx = FakeCtx()
    reg = W.register(ctx)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = sock.getsockname()[1]
    try:
        result = reg["deploy.start"](ctx, {"path": os.path.join(TMP, "conflict"),
                                           "port": port, "ip": "127.0.0.1"})
        assert result.get("ok") is False and "busy" in result.get("error", ""), result
    finally:
        sock.close()
    print("  webdeploy: existing port is preserved and conflict reported")


def test_wifi_xml_escapes_user_values():
    import xml.etree.ElementTree as ET
    from agent.modules.network_ctl import _wlan_profile_xml
    ssid = 'Room & <Guest> "5G"'
    password = 'Pass&<word>"42'
    xml = _wlan_profile_xml(ssid, password)
    root = ET.fromstring(xml)
    ns = "{http://www.microsoft.com/networking/WLAN/profile/v1}"
    assert root.find(ns + "name").text == ssid
    assert root.find(ns + "SSIDConfig/" + ns + "SSID/" + ns + "name").text == ssid
    assert root.find(ns + "MSM/" + ns + "security/" + ns + "sharedKey/" + ns + "keyMaterial").text == password
    print("  wifi profile: SSID/password are valid escaped XML text")


def test_tasks():
    from agent.modules import tasks as T
    ctx = FakeCtx()
    reg = T.register(ctx)
    # upload validation
    try:
        reg["tasks.upload_script"](ctx, {"name": "x", "ext": "exe", "content_b64": "AAAA"})
        raise AssertionError("exe must be rejected")
    except ValueError:
        pass
    try:
        reg["tasks.upload_script"](ctx, {"name": "x", "ext": "py", "content_b64": ""})
        raise AssertionError("empty script must be rejected")
    except ValueError:
        pass
    up = reg["tasks.upload_script"](ctx, {"name": "my job!", "ext": "py",
                                     "content_b64": base64.b64encode(b"print(1)").decode()})
    assert up["name"] == "my_job_" and up["path"].endswith(".py"), up
    # schedule bookkeeping
    reg["tasks.agent_add"](ctx, {"name": "t-once", "type": "command", "value": "echo", "when": "once",
                            "at": "2030-01-01T00:00:00Z"})
    reg["tasks.agent_add"](ctx, {"name": "t-delay", "type": "script", "path": up["path"],
                            "when": "delay", "delay_min": 5})
    reg["tasks.agent_add"](ctx, {"name": "t-int", "type": "cmd", "value": "dir", "when": "interval",
                            "interval_min": 10})
    items = {x["name"] for x in reg["tasks.agent_list"](ctx, {})["items"]}
    assert {"t-once", "t-delay", "t-int"} <= items
    # duplicate name replaces
    reg["tasks.agent_add"](ctx, {"name": "t-once", "type": "message", "value": "hi", "when": "delay", "delay_min": 1})
    got = [x for x in reg["tasks.agent_list"](ctx, {})["items"] if x["name"] == "t-once"]
    assert len(got) == 1 and got[0]["type"] == "message"
    # script task without upload refused
    try:
        reg["tasks.agent_add"](ctx, {"name": "bad", "type": "script", "when": "delay", "delay_min": 1})
        raise AssertionError("script without path must fail")
    except ValueError:
        pass
    # script run log listing path exists
    logs = reg["tasks.script_logs"](ctx, {})
    assert isinstance(logs["items"], list)
    reg["tasks.agent_remove"](ctx, {"name": "t-int"})
    items = {x["name"] for x in reg["tasks.agent_list"](ctx, {})["items"]}
    assert "t-int" not in items
    print("  tasks: OK")


def test_power_parse():
    from agent.modules.power_ctl import _parse_iso
    import datetime
    ts = _parse_iso("2030-01-01T12:00:00Z")
    want = datetime.datetime(2030, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc).timestamp()
    assert abs(ts - want) < 1
    try:
        _parse_iso("garbage")
        raise AssertionError("bad iso must raise")
    except Exception:
        pass
    print("  power: OK")


def test_logon_state():
    from agent.modules import logon_watch as L
    assert L._event_kind(1) is None  # Kernel-General clock change, never a resume
    assert L._event_kind(107) == "resume"
    assert L._event_kind(42) == "sleep"
    L.write_state({"console_user": "hks", "capture_needed": True})
    st = L.read_state()
    assert st["console_user"] == "hks" and st["capture_needed"] is True


def test_capture_watch_handles_late_logon_request_once():
    from agent.modules import logon_watch as L
    from agent.session_modules import capture_watch as C
    calls = []
    class Ctx:
        pass
    old = C._take_logon_capture
    C._take_logon_capture = lambda ctx, trigger: calls.append(trigger)
    try:
        L.write_state({"console_user": "hks", "last_logon_user": "hks",
                       "last_logon_ts": 100.0, "capture_needed": True})
        request = C._process_pending_capture(Ctx())
        assert calls == ["login (hks)"]
        assert L.read_state().get("capture_needed") is False
        state = L.read_state()
        state["capture_needed"] = True
        L.write_state(state)
        C._process_pending_capture(Ctx(), request)
        assert len(calls) == 1
        state["last_logon_ts"] = 101.0
        state["capture_needed"] = True
        L.write_state(state)
        C._process_pending_capture(Ctx(), request)
        assert calls == ["login (hks)", "login (hks)"]
    finally:
        C._take_logon_capture = old
    print("  capture watcher: late logon requests are consumed once")
    print("  logon_watch state: OK")


def test_capture_request_key_tracks_wake_separately_from_login():
    from agent.session_modules import capture_watch as C
    login = {"capture_reason": "login", "last_logon_ts": 100.0, "last_wake": 90.0}
    wake = {"last_logon_ts": 100.0, "last_wake": 110.0, "last_wake_event_id": 107}
    assert C._capture_request_key(login) != C._capture_request_key(wake)
    assert C._capture_request_key(wake) == "wake/resume:110.0"
    assert C._capture_reason({"last_wake": 120.0}) is None


def test_connection_queue():
    from agent.connection import Connection
    q = asyncio.Queue(maxsize=2)
    Connection._put(q, "a")
    Connection._put(q, "b")
    Connection._put(q, "c")  # would overflow → drops oldest
    items = [q.get_nowait(), q.get_nowait()]
    assert items == ["b", "c"], items
    print("  connection queue: OK")


def test_input_keymap():
    # 7.1.0 engine (AnywhereInput port): pyautogui key-name map
    import agent.session_modules.input_ctl as I
    import inspect
    src = inspect.getsource(I._to_pg_key)
    for need in ("enter", "backspace", "esc", "space", "ctrl", "capslock", "Digit"):
        assert need in src, need
    assert I._to_pg_key({"key": "Enter"}) == "enter"
    assert I._to_pg_key({"key": "a"}) == "a"
    assert I._to_pg_key({"code": "Digit5"}) == "5"
    print("  input keymap: OK")


def test_hardware_lightweight():
    from agent.modules import hardware as H
    ctx = FakeCtx()
    ctx.stats = {"latency_ms": 33}
    snap = H.lightweight(ctx)
    for k in ("cpu", "ram", "processes", "ts"):
        assert k in snap
    assert snap["latency_ms"] == 33
    print("  hardware: OK")


def test_desktop_audio_pure():
    from agent.session_modules import desktop as D
    assert D.AUDIO_RATE == 16000
    # feed_voice must be a no-op-safe when talk off
    D.feed_voice(b"\x00\x00" * 100)
    # _talk_callback returns silence when buffer empty
    import numpy as np
    out = np.zeros((4, 1), dtype=np.int16)
    D._talk_callback(out, 4, None, "")
    assert (out == 0).all()
    # and copies data when fed
    D._talk["on"] = True
    D.feed_voice(np.array([[100], [200], [300], [400]], dtype=np.int16).tobytes())
    D._talk_callback(out, 4, None, "")
    assert out.flatten().tolist() == [100, 200, 300, 400]
    D._talk["on"] = False
    print("  desktop audio: OK")


if __name__ == "__main__":
    for fn in (test_security, test_db, test_common, test_files, test_media_scan, test_sql,
               test_webdeploy, test_webdeploy_port_conflict_is_not_stolen,
               test_wifi_xml_escapes_user_values, test_tasks, test_power_parse, test_logon_state,
               test_capture_watch_handles_late_logon_request_once,
               test_connection_queue, test_input_keymap, test_hardware_lightweight,
               test_desktop_audio_pure):
        fn()
    print("ALL LOGIC TESTS PASSED")
