"""Deploy any folder as a web server on a chosen IP:port."""
import functools
import http.server
import os
import socketserver
import threading

_servers = {}  # id -> {"thread", "httpd", "dir", "ip", "port", "started"}
_servers_lock = threading.RLock()


class _ThreadingHTTPServer(socketserver.ThreadingTCPServer):
    # Windows SO_REUSEADDR can allow a second listener to steal a port from
    # an existing service. Report the conflict instead of serving unpredictably.
    allow_reuse_address = False
    daemon_threads = True
    block_on_close = False


def register(ctx):
    def deploy_start(c, p):
        import secrets as _secrets
        directory = os.path.abspath(p["path"])
        port = int(p.get("port", 8090))
        ip = p.get("ip", "0.0.0.0")
        if not os.path.isdir(directory):
            os.makedirs(directory, exist_ok=True)
        tok = _secrets.token_urlsafe(12)  # R13: random URL path gate
        handler = functools.partial(
            type("_GatedHandler", (_QuietHandler,), {"gate": tok}),
            directory=directory)
        dep_id = f"web_{port}"
        # round 62 (audit5 #7): redeploying a live port used to ORPHAN the old
        # server - on Windows SO_REUSEADDR double-binds silently and users saw
        # old/new content flip per connection. Stop the old, then rebind.
        with _servers_lock:
            old = _servers.pop(dep_id, None)
            if old:
                try:
                    old["httpd"].shutdown()
                    old["httpd"].server_close()
                    old["thread"].join(timeout=5)
                except Exception:
                    pass
            try:
                httpd = _ThreadingHTTPServer((ip, port), handler)
            except OSError:
                return {"ok": False, "error": f"port {port} is busy - stop what holds it or pick another"}
            t = threading.Thread(target=httpd.serve_forever, daemon=True,
                                 name=f"hks-web-{port}")
            t.start()
            _servers[dep_id] = {"thread": t, "httpd": httpd, "dir": directory,
                                "ip": ip, "port": port, "started": _now(), "gate": tok}
        c.send_event("web_deploy", {"id": dep_id, "dir": directory, "addr": f"{ip}:{port}"})
        return {"id": dep_id, "token": tok,
                "url": f"http://{ip if ip != '0.0.0.0' else _lan_ip()}:{port}/{tok}/"}

    def deploy_stop(c, p):
        with _servers_lock:
            dep = _servers.pop(p.get("id"), None)
        if not dep:
            return {"ok": True, "note": "already stopped"}
        dep["httpd"].shutdown()
        dep["httpd"].server_close()
        return {"stopped": p.get("id")}

    def deploy_list(c, p):
        with _servers_lock:
            items = [{"id": k, "dir": v["dir"], "ip": v["ip"], "port": v["port"],
                      "started": v["started"]} for k, v in _servers.items()]
        return {"items": items}

    return {"deploy.start": deploy_start, "deploy.stop": deploy_stop, "deploy.list": deploy_list}


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    gate = None  # R13: when set, only /<gate>/... paths are served

    def log_message(self, *a):  # keep quiet in service context
        pass

    def do_GET(self):
        if self.gate and not self.path.startswith("/" + self.gate + "/"):
            self.send_error(404)
            return
        # strip the gate prefix so SimpleHTTPRequestHandler sees the real path
        self.path = self.path[len(self.gate) + 1:]
        super().do_GET()

    def do_HEAD(self):
        if self.gate and not self.path.startswith("/" + self.gate + "/"):
            self.send_error(404)
            return
        self.path = self.path[len(self.gate) + 1:]
        super().do_HEAD()


def _now():
    import time
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _lan_ip():
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("1.1.1.1", 443))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"
