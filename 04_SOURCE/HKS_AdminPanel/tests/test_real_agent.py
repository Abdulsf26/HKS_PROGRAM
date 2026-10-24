

"""REAL agent E2E: the actual agent code against the actual server,
exactly like first_run.py + run_agent.bat do it on the Acer."""
import http.client
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

PORT = 8991
BASE = "/tmp/realagent"


def main():
    shutil.rmtree(BASE, ignore_errors=True)
    os.makedirs(BASE)
    _root = _ROOT  # dual-layout (round 62)
    subprocess.run(["cp", "-r", os.path.join(_root, "HKS_Client"), BASE + "/"], check=True)
    subprocess.run(["cp", "-r", os.path.join(_root, "HKS_AdminPanel"), BASE + "/"], check=True)
    shutil.rmtree(BASE + "/HKS_Client/data", ignore_errors=True)

    import secrets
    token = secrets.token_urlsafe(24)
    open(BASE + "/HKS_AdminPanel/.env", "w").write(f"HKS_AGENT_TOKEN={token}\n")
    json.dump({"server_url": f"ws://127.0.0.1:{PORT}/ws/agent", "token": token,
               "machine_id": "HKS-REALTEST1", "name": "RealAgentTest"},
              open(BASE + "/HKS_Client/config.json", "w"), indent=2)
    os.makedirs(BASE + "/data_panel", exist_ok=True)
    os.makedirs(BASE + "/HKS_Client/data", exist_ok=True)

    env = dict(os.environ, HKS_DATA_DIR=BASE + "/data_panel")
    srv = subprocess.Popen([sys.executable, "-m", "server.main", "--host", "127.0.0.1",
                            "--port", str(PORT)], cwd=BASE + "/HKS_AdminPanel", env=env,
                           stdout=open(BASE + "/server.log", "w"), stderr=subprocess.STDOUT)
    time.sleep(3)
    assert srv.poll() is None, "server died on startup"

    ag = subprocess.Popen([sys.executable, "-m", "agent.main", "--console"],
                          cwd=BASE + "/HKS_Client",
                          stdout=open(BASE + "/agent.log", "w"), stderr=subprocess.STDOUT)
    time.sleep(10)

    st = json.loads(urllib.request.urlopen("http://127.0.0.1:8590/status.json",
                                           timeout=3).read())
    print("agent status page:", st)

    os.environ["HKS_DATA_DIR"] = BASE + "/data_panel"
    sys.path.insert(0, BASE + "/HKS_AdminPanel")
    from server.db import db
    from server.security import hash_password
    if not db.get_user("realtest"):
        db.create_user("realtest", hash_password("TestPass#123"), "admin")
    conn = http.client.HTTPConnection("127.0.0.1", PORT)
    conn.request("POST", "/api/login",
                 json.dumps({"username": "realtest", "password": "TestPass#123"}),
                 {"Content-Type": "application/json"})
    r = conn.getresponse()
    ck = (r.getheader("Set-Cookie") or "").split(";")[0]
    r.read()
    conn.request("GET", "/api/me", headers={"Cookie": ck})
    me = json.loads(conn.getresponse().read())
    csrf = me["user"]["csrf"]
    AUTH = {"Cookie": ck, "X-HKS-CSRF": csrf}
    conn.request("GET", "/api/machines", headers={"Cookie": ck})
    m = json.loads(conn.getresponse().read())["machines"]
    if not (m and m[0]["live_svc"] is True):
        print("MACHINE NOT ONLINE")
        print(open(BASE + "/HKS_Client/data/hks_svc.log").read()[-1500:])
        srv.kill(); ag.kill()
        sys.exit(1)
    print("REAL AGENT ONLINE:", m[0]["machine_id"], "| live_svc:", m[0]["live_svc"])

    conn.request("POST", "/api/cmd",
                 json.dumps({"machine": "HKS-REALTEST1", "action": "sys.quick"}),
                 {"Content-Type": "application/json", **AUTH})
    res = json.loads(conn.getresponse().read())
    inner = res.get("result") or {}
    assert inner.get("ok") is True, res
    d = inner.get("data") or {}
    assert d.get("user") and "ram" in d, d
    print("real command round trip: ok =", inner.get("ok"), "| data:", d)

    conn.request("GET", "/api/events?limit=10", headers={"Cookie": ck})
    evs = json.loads(conn.getresponse().read())
    kinds = [e.get("kind") for e in evs.get("events", [])]
    print("events:", kinds[:6])
    assert "agent_start" in kinds or "boot" in kinds, kinds

    srv.kill(); ag.kill()
    print("REAL AGENT E2E: PASS")


if __name__ == "__main__":
    main()
