

def _hks_root():
    # dual-layout (round 62): <root> = 04_SOURCE (repo) or delivered-zip root;
    # HKS_AdminPanel/HKS_Client are siblings of it in BOTH layouts
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
"""HKS end-to-end tests (server + mock agent over real WebSockets)."""
import asyncio
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8000"
MACHINE = "test-laptop-001"


def login():
    import http.cookiejar
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    req = urllib.request.Request(
        BASE + "/api/login",
        data=json.dumps({"username": "admin",
                         "password": os.environ.get("HKS_TEST_PW", "HksAdmin#2026")}).encode(),
        headers={"Content-Type": "application/json"})
    j = json.loads(opener.open(req).read())
    cookie = "; ".join(f"{c.name}={c.value}" for c in cj)
    return j, cookie


def rest_tests(cookie, csrf):
    def call(path, data=None, method=None):
        req = urllib.request.Request(
            BASE + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers={"Content-Type": "application/json",
                     "X-HKS-CSRF": csrf, "Cookie": cookie},
            method=method or ("POST" if data is not None else "GET"))
        return json.loads(urllib.request.urlopen(req).read())

    print("[rest] chunked download...")
    r = urllib.request.urlopen(urllib.request.Request(
        BASE + f"/api/download?machine={MACHINE}&path=C%3A%5Ctest.bin",
        headers={"Cookie": cookie}))
    body = r.read()
    assert body == b"A" * 2048, body[:50]

    print("[rest] users CRUD...")
    call("/api/users", {"username": "observer1", "password": "Observer#99", "role": "observer"})
    users = call("/api/users")
    assert any(u["username"] == "observer1" for u in users["users"])
    uid = [u for u in users["users"] if u["username"] == "observer1"][0]["id"]
    call("/api/users/update", {"id": uid, "password": "Observer#100"})
    call("/api/users/delete", {"id": uid})
    users = call("/api/users")
    assert not any(u["username"] == "observer1" for u in users["users"])

    print("[rest] errors resolve...")
    errs = call("/api/errors")
    if errs["errors"]:
        call("/api/errors/resolve", {"id": errs["errors"][0]["id"]})

    print("[rest] media.scan / deploy / config / hw.status ...")
    call("/api/cmd", {"machine": MACHINE, "action": "media.scan", "params": {"kind": "img", "path": "C:\\"}})
    d = call("/api/cmd", {"machine": MACHINE, "action": "deploy.start", "params": {"path": "C:\\www", "port": 8090}})
    dep_id = d["result"]["data"]["id"]
    call("/api/cmd", {"machine": MACHINE, "action": "deploy.stop", "params": {"id": dep_id}})
    call("/api/cmd", {"machine": MACHINE, "action": "power.shutdown", "params": {"seconds": 3600}})
    call("/api/cmd", {"machine": MACHINE, "action": "power.shutdown_cancel"})
    call("/api/cmd", {"machine": MACHINE, "action": "config.set", "params": {"fps": 6, "hw_report_sec": 60}})
    d = call("/api/cmd", {"machine": MACHINE, "action": "hw.status", "params": {}})
    assert "cpu" in d["result"]["data"], d

    print("[rest] boot + hw_report events ...")
    evs = call("/api/events?kind=hw_report&limit=5")
    assert len(evs["events"]) >= 1, "no hw_report events"
    evs = call("/api/events?kind=boot&limit=5")
    assert any("HKS PROGRAM HAS BEEN STARTED" in (e["data"] or "") for e in evs["events"])
    machines = call("/api/machines")
    assert "boot_ts" in machines["machines"][0]

    print("[rest] round4 fixes: machines.telemetry + db.find + snapshots ...")
    machines = call("/api/machines")
    m0 = machines["machines"][0]
    assert "telemetry" in m0, "machines payload missing telemetry key"
    d = call("/api/cmd", {"machine": MACHINE, "action": "db.find", "params": {}})
    assert d["result"]["ok"], d
    d = call("/api/cmd", {"machine": MACHINE, "action": "screen.snapshot",
                          "params": {"monitor": 1, "max_width": 960}})
    assert d["result"]["ok"] and "b64" in d["result"]["data"], d
    d = call("/api/cmd", {"machine": MACHINE, "action": "camera.snap",
                          "params": {"index": 0}})
    assert d["result"]["ok"] and "b64" in d["result"]["data"], d

    print("[rest] round7: version stamp visible pre-auth...")
    v = json.loads(urllib.request.urlopen(BASE + "/api/version", timeout=10).read())
    assert v["ok"] and v["version"] and "main" in v, v
    print(f"    running build: v{v['version']} (main:{v['main']} companion:{v.get('http_companion')})")

    print("[rest] round6: cache-busting (upgrade freshness)...")
    r = urllib.request.urlopen(urllib.request.Request(BASE + "/static/app.js",
                                                      headers={"Cookie": cookie}))
    assert r.headers.get("Cache-Control") == "no-cache", r.headers.get("Cache-Control")
    idx = urllib.request.urlopen(urllib.request.Request(BASE + "/",
                                                        headers={"Cookie": cookie})).read().decode()
    assert "app.js?v=" in idx and "style.css?v=" in idx, "assets not versioned"
    r = urllib.request.urlopen(urllib.request.Request(BASE + "/api/machines",
                                                      headers={"Cookie": cookie}))
    assert r.headers.get("Cache-Control") == "no-store", r.headers.get("Cache-Control")

    print("[rest] round5: backup live engine (REST stream + /api/frame + REST input)...")
    d = call("/api/cmd", {"machine": MACHINE, "action": "screen.control", "params": {"on": True, "fps": 4, "quality": 50, "max_width": 960}})
    assert d["result"]["ok"], d
    d = call("/api/cmd", {"machine": MACHINE, "action": "input.enable", "params": {"on": True}})
    assert d["result"]["ok"] and d["result"]["data"].get("enabled") is True, d
    d = call("/api/cmd", {"machine": MACHINE, "action": "input.apply", "params": {"t": "kd", "key": "escape"}})
    assert d["result"]["ok"], d
    time.sleep(2.5)  # let the session agent push frames (mock: one per 1.5 s)
    r = urllib.request.urlopen(urllib.request.Request(
        BASE + "/api/frame?ch=1&machine=" + urllib.parse.quote(MACHINE),
        headers={"Cookie": cookie}))
    j = json.loads(r.read())
    assert j["ok"] and j["seq"] >= 1 and len(j["b64"]) > 100, \
        f"no cached live frame: seq={j.get('seq')} len={len(j.get('b64',''))}"
    d = call("/api/cmd", {"machine": MACHINE, "action": "screen.control", "params": {"on": False}})
    assert d["result"]["ok"], d

    print("[rest] ZIP export flow (create -> download -> auto-delete)...")
    d = call("/api/cmd", {"machine": MACHINE, "action": "files.zip_media", "params": {"kind": "img", "path": "C:\\"}})
    zpath = d["result"]["data"]["path"]
    zsize = d["result"]["data"]["size"]
    r = urllib.request.urlopen(urllib.request.Request(
        BASE + f"/api/download?machine={MACHINE}&del_after=1&path=" + urllib.parse.quote(zpath),
        headers={"Cookie": cookie}))
    zbytes = r.read()
    assert len(zbytes) == zsize and zbytes[:2] == b"PK", "zip bytes wrong"
    import time as _t
    _t.sleep(1.2)  # allow the fire-and-forget delete to reach the agent
    try:
        urllib.request.urlopen(urllib.request.Request(
            BASE + f"/api/download?machine={MACHINE}&path=" + urllib.parse.quote(zpath),
            headers={"Cookie": cookie}))
        raise AssertionError("zip should have been deleted after download")
    except urllib.error.HTTPError as e:
        assert e.code == 404, e.code

    print("[rest] CSV export...")
    r = urllib.request.urlopen(urllib.request.Request(
        BASE + "/api/export?what=events", headers={"Cookie": cookie}))
    head = r.read(400)
    assert r.status == 200 and b"," in head

    print("[rest] guards: self_destruct + terminate CONFIRM gates...")
    try:
        call("/api/self_destruct", {"confirm": "no"})
        raise AssertionError("self_destruct should reject wrong confirm")
    except urllib.error.HTTPError as e:
        assert e.code == 400, e.code
    d = call("/api/cmd", {"machine": MACHINE, "action": "self.terminate", "params": {"confirm": "nope"}})
    assert d["ok"] is False, d
    print("[rest] ALL REST TESTS PASSED")


async def dash_test(cookie):
    import websockets
    async with websockets.connect("ws://127.0.0.1:8000/ws/dash",
                                  additional_headers={"Cookie": cookie}) as ws:
        await ws.send(json.dumps({"machine": MACHINE}))
        hello = json.loads(await ws.recv())
        assert hello["type"] == "hello_dash", hello
        got_frame = False
        for _ in range(60):
            msg = await asyncio.wait_for(ws.recv(), timeout=15)
            if isinstance(msg, bytes) and msg[0] == 1:
                print(f"[dash] screen frame received ({len(msg)} bytes)")
                got_frame = True
                break
        assert got_frame, "no screen frame arrived"
        await ws.send(json.dumps({"type": "input", "ev": {"t": "mm", "x": 100, "y": 50}}))
        await ws.send(json.dumps({"type": "cmd", "id": "t1", "action": "sys.info"}))
        while True:
            m = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
            if m.get("type") == "result" and m.get("id") == "t1":
                assert m["ok"], m
                print("[dash] ws cmd relay OK")
                break
        print("[dash] WS TEST PASSED")


if __name__ == "__main__":
    j, cookie = login()
    rest_tests(cookie, j["user"]["csrf"])
    asyncio.run(dash_test(cookie))
    print("=== E2E ALL GREEN ===")
