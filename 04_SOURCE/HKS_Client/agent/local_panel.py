"""Client-side local control panel — http://127.0.0.1:8590 on the laptop.

Binds to loopback ONLY (never exposed to the network). Gives the laptop's
owner a clean status page: connection state, latency, hardware snapshot,
and elevated uninstall/start actions that trigger UAC on Windows."""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_state = {"ctx": None, "server": None, "port": None}


def _phone_url(ctx):
    """Live control URL for phones. The LAN IP changes whenever the
    network changes — always compute it fresh, never cache."""
    try:
        url = str(ctx.cfg.get("server_url", ""))
        scheme, rest = url.split("://", 1)
        port = rest.split("/", 1)[0].rsplit(":", 1)[1]
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
        finally:
            s.close()
        if ip and not ip.startswith("127."):
            return ("https" if scheme == "wss" else "http") + f"://{ip}:{port}"
    except Exception:
        pass
    return ""


def _page(ctx):
    conn = ctx.conn
    connected = bool(conn is not None and getattr(conn, "alive", False))
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>HKS Agent — Local Status</title><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{{font:15px/1.5 'Segoe UI',system-ui,sans-serif;margin:0;background:#f5f6f8;color:#1a2230}}
.wrap{{max-width:720px;margin:0 auto;padding:24px 16px}}
.card{{background:#fff;border:1px solid #e3e7ee;border-radius:12px;padding:18px;margin-bottom:14px}}
h1{{font-size:19px;margin:0 0 4px}} .dot{{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px}}
.ok{{background:#1db970}}.bad{{background:#e5484d}}
.kv{{display:grid;grid-template-columns:170px 1fr;gap:5px 12px;font-size:14px}}
.kv b{{color:#5a6a7e;font-weight:600}}
button{{border:1px solid #c9d2de;background:#fff;border-radius:9px;padding:10px 16px;font:inherit;font-weight:600;cursor:pointer;margin:4px 6px 0 0}}
button.warn{{background:#e5484d;border-color:#e5484d;color:#fff}}
small{{color:#7b8794}}
</style></head><body><div class="wrap">
<h1><span class="dot {'ok' if connected else 'bad'}"></span>HKS Agent — {'connected' if connected else 'offline'}</h1>
<div class="card"><div class="kv">
<b>Machine ID</b><span>{ctx.cfg.get('machine_id')}</span>
<b>Server</b><span>{ctx.cfg.get('server_url')}</span>
<b>Latency</b><span>{ctx.stats.get('latency_ms') or '—'} ms</span>
<b>Uptime</b><span>{int((__import__('time').time()-ctx.stats['started'])//60)} min</span>
<b>Commands handled</b><span>{ctx.stats['cmds']}</span>
<b>Errors reported</b><span>{ctx.stats['errors']}</span>
<b>Local panel</b><span>http://127.0.0.1:8590 (this page — loopback only)</span>
<b>Open from phone</b><span>{_phone_url(ctx) or 'no network yet — run CHECK_STATUS.bat on this laptop'}</span>
</div></div>
<div class="card"><b>Actions</b><br>
<button onclick="fetch('/open_data')">📁 Open data folder</button>
<button onclick="fetch('/restart').then(()=>alert('Confirm the UAC prompt - HKS panel + agents restart in ~5 seconds'))">🔄 Restart HKS (UAC)</button>
<button onclick="fetch('/uninstall').then(()=>alert('Confirm the UAC prompt to remove HKS completely'))">🗑 Uninstall (UAC)</button>
<br><small>Remote control happens only via your admin panel (authenticated).</small>
</div>
<small>HKS Agent service (HKSAgent) — removed anytime with C:\\HKS_PROGRAM\\HKS_Client\\uninstall.bat</small>
</div></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def handle_error(self, request, client_address):
        # Refresh/navigation can cancel a local status poll. Avoid treating
        # routine Windows client disconnects (10053/10054) as agent errors.
        import errno
        import sys
        exc = sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionAbortedError, ConnectionResetError)):
            return
        if isinstance(exc, OSError) and exc.errno in (errno.EPIPE, errno.ECONNRESET, errno.ECONNABORTED):
            return
        super().handle_error(request, client_address)

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        ctx = _state["ctx"]
        if ctx is None:
            return self._send(503, "starting")
        if self.path == "/":
            try:
                return self._send(200, _page(ctx))
            except Exception as e:
                return self._send(500, f"render error: {e}")
        if self.path == "/status.json":
            try:
                conn = ctx.conn
                payload = {"connected": bool(conn is not None and getattr(conn, "alive", False)),
                           "machine_id": ctx.cfg.get("machine_id"),
                           "role": ctx.role, "pid": os.getpid(),
                           "latency_ms": ctx.stats.get("latency_ms"),
                           "cmds": ctx.stats["cmds"], "errors": ctx.stats["errors"],
                           "status_port": _state.get("port"),
                           "process_started_at": getattr(conn, "started_at", None),
                           "last_connected_at": getattr(conn, "last_connected_at", None),
                           "connection_stage": getattr(conn, "connection_stage", "initializing"),
                           "connection_attempts": getattr(conn, "connection_attempts", 0),
                           "reconnect_delay": getattr(conn, "backoff", None),
                           "last_error": str(getattr(conn, "last_error", ""))[:300],
                           "panel_for_phone": _phone_url(ctx)}
                return self._send(200, json.dumps(payload), "application/json")
            except Exception as e:
                return self._send(500, json.dumps({"error": str(e)}), "application/json")
        if self.path == "/open_data":
            try:
                import subprocess
                subprocess.Popen(["explorer", os.path.join(
                    os.path.dirname(ctx.cfg.path))])
                return self._send(200, "ok")
            except Exception:
                return self._send(500, "failed")
        if self.path == "/uninstall":
            try:
                import subprocess
                bat = os.path.join(os.path.dirname(ctx.cfg.path), "uninstall.bat")
                subprocess.Popen(["powershell", "-NoProfile", "-Command",
                                  f"Start-Process -FilePath '{bat}' -Verb RunAs"])
                return self._send(200, "ok")
            except Exception:
                return self._send(500, "failed")
        if self.path == "/restart":
            # one-click safe restart: ends the HKS tasks, kills leftover
            # HKS processes, starts all three tasks again (UAC if needed)
            try:
                import subprocess
                bat = os.path.join(os.path.dirname(ctx.cfg.path), "restart_hks.bat")
                subprocess.Popen(["powershell", "-NoProfile", "-Command",
                                  f"Start-Process -FilePath '{bat}' -Verb RunAs"])
                return self._send(200, "ok")
            except Exception:
                return self._send(500, "failed")
        self._send(404, "not found")


# Windows may let multiple localhost listeners bind the same endpoint when
# SO_REUSEADDR is enabled. The service and interactive agent then race to
# answer status requests on 8590, masking one role from the watchdog. Claim
# each status port exclusively so the second agent advances to the next port.
ThreadingHTTPServer.allow_reuse_address = False


def start(ctx, port=8590):
    def run():
        # port negotiation: several HKS processes may live on one laptop
        # (svc service + desktop session). Each gets its own status page.
        srv = None
        bound = None
        for p in range(port, port + 5):
            try:
                srv = ThreadingHTTPServer(("127.0.0.1", p), Handler)
                bound = p
                break
            except OSError:
                continue
        if srv is None:
            ctx.log.info("local panel: no free port in %d-%d — skipping", port, port + 4)
            return
        _state["ctx"] = ctx
        _state["server"] = srv
        _state["port"] = bound
        ctx.log.info("local status page on http://127.0.0.1:%d (role=%s)", bound, ctx.role)
        try:
            srv.serve_forever(poll_interval=0.5)
        except Exception as e:
            ctx.log.error("local panel: %s", e)

    threading.Thread(target=run, daemon=True, name="hks-local-panel").start()
