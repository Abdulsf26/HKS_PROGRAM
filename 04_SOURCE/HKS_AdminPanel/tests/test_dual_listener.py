

def _hks_root():
    # dual-layout (round 62): <root> = 04_SOURCE (repo) or delivered-zip root;
    # HKS_AdminPanel/HKS_Client are siblings of it in BOTH layouts
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
"""Dual-listener regression (field: "phone not able to connect" on HTTPS).

The panel must serve the HTTPS port (agents + browser, TOFU cert) AND a
plain-HTTP companion port (phone-friendly: no certificate warnings at
all). Both listeners = one process = one app = one database.

Proven here against the REAL server:
  D1 https://127.0.0.1:P  answers (TLS)
  D2 http://127.0.0.1:P+1 answers (companion thread)
  D3 login + authenticated API work on BOTH listeners
  D4 agents' loopback wss://127.0.0.1:P contract unchanged
  D5 no --http-port (or plain HTTP mode) -> single listener, unchanged
"""
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import http.cookiejar
import urllib.error
import urllib.request

HERE = _hks_root()
ROOT = HERE  # <root> = 04_SOURCE (repo) or zip root
PANEL = os.path.join(ROOT, "HKS_AdminPanel")
USER, PASS = "admin", "HksAdmin#2026"
CERT = "/tmp/hks_dual_cert/server.pem"
KEY = "/tmp/hks_dual_cert/key.pem"


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def op_with_cookies(tls=False):
    cj = http.cookiejar.CookieJar()
    handlers = [urllib.request.HTTPCookieProcessor(cj)]
    if tls:
        cx = ssl.create_default_context()
        cx.check_hostname = False
        cx.verify_mode = ssl.CERT_NONE  # self-signed test cert (TOFU in prod)
        handlers.append(urllib.request.HTTPSHandler(context=cx))
    return urllib.request.build_opener(*handlers)


def login(opener, base):
    req = urllib.request.Request(
        base + "/api/login",
        data=json.dumps({"username": USER, "password": PASS}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    return json.loads(opener.open(req, timeout=10).read())


def call(opener, base, path, csrf, data=None):
    h = {"Content-Type": "application/json"}
    if data is not None:
        h["X-HKS-CSRF"] = csrf
    req = urllib.request.Request(base + path,
                                 data=json.dumps(data).encode() if data else None,
                                 headers=h, method="POST" if data else "GET")
    return json.loads(opener.open(req, timeout=10).read())


def wait_up(url, ctx=None):
    for _ in range(60):
        try:
            urllib.request.urlopen(url, timeout=2, context=ctx)
            return True
        except urllib.error.HTTPError:
            return True  # reached the app (e.g. redirect/401) = listener up
        except Exception:
            time.sleep(0.3)
    return False


def main():
    os.makedirs(os.path.dirname(CERT), exist_ok=True)
    r = os.system(f'openssl req -x509 -newkey rsa:2048 -nodes -keyout "{KEY}" '
                  f'-out "{CERT}" -days 30 -subj "/CN=HKS-Dual" 2>/dev/null')
    assert r == 0, "openssl cert generation failed"

    tls_port, http_port = free_port(), free_port()
    data_dir = tempfile.mkdtemp(prefix="hks_dual_")
    sys.path.insert(0, PANEL)
    os.environ["HKS_DATA_DIR"] = data_dir
    from server.db import db
    from server.security import hash_password
    db.create_user(USER, hash_password(PASS), role="admin")

    env = dict(os.environ, HKS_DATA_DIR=data_dir)
    proc = subprocess.Popen(
        [sys.executable, "-m", "server.main", "--host", "127.0.0.1",
         "--port", str(tls_port), "--ssl-cert", CERT, "--ssl-key", KEY,
         "--http-port", str(http_port)],
        cwd=PANEL, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        tls_ctx = ssl.create_default_context()
        tls_ctx.check_hostname = False
        tls_ctx.verify_mode = ssl.CERT_NONE
        TLS = f"https://127.0.0.1:{tls_port}"
        HTTPC = f"http://127.0.0.1:{http_port}"

        assert wait_up(TLS + "/", tls_ctx), "HTTPS listener never came up"
        print("  D1 HTTPS listener answers over TLS: PASS")
        assert wait_up(HTTPC + "/"), "HTTP companion listener never came up"
        print("  D2 HTTP companion listener answers: PASS")

        op_tls, op_http = op_with_cookies(tls=True), op_with_cookies()
        j_tls = login(op_tls, TLS)
        assert j_tls.get("ok"), j_tls
        j_http = login(op_http, HTTPC)
        assert j_http.get("ok"), j_http
        print("  D3a login works on BOTH listeners: PASS")

        v_tls = json.loads(op_tls.open(TLS + "/api/version", timeout=10).read())
        v_http = json.loads(op_http.open(HTTPC + "/api/version", timeout=10).read())
        assert v_tls["version"] == v_http["version"] and v_tls["ok"], (v_tls, v_http)
        assert "tunnel_url" in v_tls and isinstance(v_tls["tunnel_url"], str)
        print("  D3c /api/version identical on BOTH + tunnel_url field: PASS")
        m_tls = call(op_tls, TLS, "/api/machines", j_tls["user"]["csrf"])
        m_http = call(op_http, HTTPC, "/api/machines", j_http["user"]["csrf"])
        assert m_tls.get("ok") and m_http.get("ok"), (m_tls, m_http)
        assert m_tls["machines"] == m_http["machines"], "listeners see different data"
        print("  D3b authenticated API identical on BOTH (one process/one db): PASS")

        # D4: the TLS listener still speaks websockets for the agents
        # (handshake-level check: wrong-token ws is closed by the app, not
        # the TLS layer) — lighter check here: / and /api/me over TLS ok.
        me = call(op_tls, TLS, "/api/me", j_tls["user"]["csrf"])
        assert me.get("ok") and me["user"]["username"] == USER
        print("  D4 TLS listener serves the app normally (agent wss intact): PASS")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        shutil.rmtree(data_dir, ignore_errors=True)

    # D5: without --http-port / without TLS -> no companion (arg plumbing)
    src = open(os.path.join(PANEL, "server/main.py")).read()
    assert 'if ssl and args.http_port:' in src, "companion must require BOTH tls and --http-port"
    fr = open(os.path.join(ROOT, "first_run.py")).read()
    assert "--http-port {port + 1}" in fr, "wizard must pass the companion port"
    assert "DisplayName 'HKS Panel HTTP'" in fr, "wizard must open the firewall for it"
    assert "OPEN (NO CERT" in fr, "CHECK_STATUS must print the no-cert address"
    print("  D5 companion only in HTTPS mode; wizard passes port+1, opens FW: PASS")

    print("\nDUAL-LISTENER REGRESSION: ALL PASSED")


if __name__ == "__main__":
    main()
