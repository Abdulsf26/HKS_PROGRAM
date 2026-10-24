

def _hks_root():
    # dual-layout (round 62): <root> = 04_SOURCE (repo) or delivered-zip root;
    # HKS_AdminPanel/HKS_Client are siblings of it in BOTH layouts
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
"""All-in-one flow test — simulates the user's exact scenario:

  1. Run first_run.py (the wizard) on the "Acer laptop"
     -> asks username/password/port (here: silent flags)
  2. Server starts on the laptop
  3. "My device" (same network) opens http://laptop-ip:port
     -> logs in with the wizard-created credentials
     -> controls the laptop through a real agent connection
"""
import base64
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = _hks_root()
ROOT = os.environ.get("HKS_TEST_ROOT")
if not ROOT:
    # dual-layout (round 62): <root> = 04_SOURCE (repo) or delivered-zip root
    ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASE = "/tmp/hks_aio_test"
PORT = 8977
USER, PASS = "hksboss", "MySecret#2026"

sys.path.insert(0, os.path.join(ROOT, "HKS_AdminPanel"))  # for server module if needed


def step(msg):
    print(f"\n=== {msg} ===")


def main():
    shutil.rmtree(BASE, ignore_errors=True)
    env = dict(os.environ, HKS_BASE=BASE)

    step("0. INSTALLER copies program folders (what INSTALL_HKS.bat does)")
    os.makedirs(os.path.join(BASE, "HKS_Client", "agent"), exist_ok=True)
    shutil.copytree(os.path.join(ROOT, "HKS_Client"), os.path.join(BASE, "HKS_Client"),
                    dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", ".venv", "data", "*.pyc"))
    shutil.copytree(os.path.join(ROOT, "HKS_AdminPanel"), os.path.join(BASE, "HKS_AdminPanel"),
                    dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", ".venv", "data", "*.pyc", ".env"))
    print("folders copied to", BASE)

    step("1. WIZARD on the laptop (username/password/port)")
    r = subprocess.run(
        [sys.executable, os.path.join(ROOT, "first_run.py"), "--auto",
         "--username", USER, "--password", PASS, "--port", str(PORT), "--no-https"],
        env=env, capture_output=True, text=True, timeout=120)
    print(r.stdout[-1400:])
    assert r.returncode == 0, r.stderr

    token = ""
    for line in open(os.path.join(BASE, "HKS_AdminPanel", ".env")):
        if line.startswith("HKS_AGENT_TOKEN="):
            token = line.strip().split("=", 1)[1]
    assert len(token) > 20
    cfg = json.load(open(os.path.join(BASE, "HKS_Client", "config.json")))
    assert cfg["server_url"] == f"ws://127.0.0.1:{PORT}/ws/agent", cfg
    assert cfg["token"] == token
    assert "HOW_TO_CONNECT.txt" in "\n".join(os.listdir(BASE))
    print("wizard artifacts OK (.env / config.json / HOW_TO_CONNECT)")

    step("2. SERVER starts on the laptop")
    srv_env = dict(env, HKS_DATA_DIR=os.path.join(BASE, "data_panel"),
                   HKS_AGENT_TOKEN=token)
    srv = subprocess.Popen(
        [sys.executable, "-m", "server.main", "--host", "127.0.0.1",
         "--port", str(PORT)],
        cwd=os.path.join(ROOT, "HKS_AdminPanel"), env=srv_env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        up = False
        for _ in range(40):
            time.sleep(0.4)
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/me", timeout=2)
                up = True
                break
            except Exception:
                pass
        assert up, "server did not start"
        print(f"server live on port {PORT}")

        step("3. MY DEVICE: login from the browser with wizard credentials")
        # wrong password must fail
        req = urllib.request.Request(
            f"http://127.0.0.1:{PORT}/api/login",
            data=json.dumps({"username": USER, "password": "wrong"}).encode(),
            headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req)
            raise AssertionError("wrong password accepted!")
        except urllib.error.HTTPError as e:
            assert e.code in (401, 429), e.code
        # right password
        import http.cookiejar
        cj = http.cookiejar.CookieJar()
        op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        j = json.loads(op.open(urllib.request.Request(
            f"http://127.0.0.1:{PORT}/api/login",
            data=json.dumps({"username": USER, "password": PASS}).encode(),
            headers={"Content-Type": "application/json"})).read())
        assert j["ok"] and j["user"]["username"] == USER
        cookie = "; ".join(f"{c.name}={c.value}" for c in cj)
        csrf = j["user"]["csrf"]
        print("login OK (wrong password rejected, right one accepted)")

        step("4+5. AGENT connects with the wizard's token; device sees it ONLINE")
        # keep the agent socket OPEN while the device checks /api/machines
        asyncio_run(cookie, token, csrf)

        print("\n=== ALL-IN-ONE FLOW: PASS ===")
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=10)
        except Exception:
            srv.kill()


def asyncio_run(cookie, token, csrf):
    import asyncio
    import websockets

    async def go():
        # agent socket stays OPEN for the whole scenario (like the real service)
        async with websockets.connect(f"ws://127.0.0.1:{PORT}/ws/agent",
                                      max_size=16 * 1024 * 1024) as agent:
            await agent.send(json.dumps({"type": "hello", "machine_id": "AIO-LAPTOP",
                                         "role": "svc", "name": "Acer", "version": "1.0",
                                         "token": token, "caps": ["x"]}))
            rep = json.loads(await agent.recv())
            assert rep["type"] == "hello_ok", rep
            await agent.send(json.dumps({"type": "telemetry", "cpu": 10, "ram": 40,
                                         "bat_pct": 90, "bat_plugged": True,
                                         "net_sent": 0, "net_recv": 0, "ssid": "Home",
                                         "ip": "192.168.1.7"}))
            print("agent authenticated with wizard token: OK")

            async def respond():
                # like the real agent: answer every cmd
                async for raw in agent:
                    try:
                        m = json.loads(raw)
                    except Exception:
                        continue
                    if m.get("type") == "cmd":
                        await agent.send(json.dumps(
                            {"type": "result", "id": m.get("id"), "ok": True,
                             "data": {"user": "hks", "note": "answered by test agent"}}))

            responder = asyncio.create_task(respond())

            # device (phone/PC) connects while the agent is live
            async with websockets.connect(f"ws://127.0.0.1:{PORT}/ws/dash",
                                          additional_headers={"Cookie": cookie}) as ws:
                await ws.send(json.dumps({"machine": "AIO-LAPTOP"}))
                hello = json.loads(await ws.recv())
                assert hello["type"] == "hello_dash"
                await ws.send(json.dumps({"type": "cmd", "id": "a1", "action": "sys.quick"}))
                while True:
                    m = json.loads(await asyncio.wait_for(ws.recv(), 20))
                    if m.get("type") == "result":
                        assert m["id"] == "a1"
                        print("device -> server -> agent relay path: OK")
                        break
                import urllib.request as _u
                req = _u.Request(f"http://127.0.0.1:{PORT}/api/machines",
                                 headers={"Cookie": cookie})
                machines = json.loads(_u.urlopen(req).read())
                me = [x for x in machines["machines"] if x["machine_id"] == "AIO-LAPTOP"]
                assert me and me[0]["live_svc"] is True, machines
                print("device sees the laptop ONLINE:", me[0]["name"])
            responder.cancel()

    asyncio.run(go())


if __name__ == "__main__":
    main()
