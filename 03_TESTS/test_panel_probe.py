"""panel_probe must detect BOTH https and http panels.

Field bug: the installer probed an HTTPS panel with plain HTTP -> always
reported "panel not answering" -> skipped agent verification entirely,
while the panel was actually running fine on https.
"""
import http.server
import os
import ssl
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "04_SOURCE")
sys.path.insert(0, ROOT)
from first_run import panel_probe  # noqa: E402

CERT = "/tmp/hks_probe_cert/server.pem"
KEY = "/tmp/hks_probe_cert/key.pem"


def _make_cert():
    os.makedirs(os.path.dirname(CERT), exist_ok=True)
    r = os.system(f'openssl req -x509 -newkey rsa:2048 -nodes -keyout "{KEY}" '
                  f'-out "{CERT}" -days 30 -subj "/CN=HKS-Test" 2>/dev/null')
    return r == 0 and os.path.exists(CERT)


def _serve(port, tls=None):
    class OK(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *a):
            pass
    srv = http.server.HTTPServer(("127.0.0.1", port), OK)
    if tls:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(tls[0], tls[1])
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main():
    have_cert = os.path.exists(CERT) or _make_cert()
    if have_cert:
        s1 = _serve(8601, tls=(CERT, KEY))
        ok, sch = panel_probe(8601, https=True, timeout_s=6)
        assert ok and sch == "https", (ok, sch)
        ok2, sch2 = panel_probe(8601, https=False, timeout_s=6)
        assert ok2 and sch2 == "https", (ok2, sch2)
        s1.shutdown()
        print("  https panel: probed correctly + scheme auto-detect")
    s2 = _serve(8602)
    ok3, sch3 = panel_probe(8602, https=False, timeout_s=6)
    assert ok3 and sch3 == "http", (ok3, sch3)
    s2.shutdown()
    ok4, sch4 = panel_probe(8603, https=True, timeout_s=3)
    assert ok4 is False and sch4 == ""
    print("  http panel + dead port: correct")
    print("PANEL-PROBE TESTS PASSED")


if __name__ == "__main__":
    main()
