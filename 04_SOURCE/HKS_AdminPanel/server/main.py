"""HKS Admin Panel - FastAPI server.

Browser dashboard  <--HTTP/WS-->  this server  <--WSS-->  HKS Agent(s) on your Windows laptop(s).
Run:  python -m server.main --host 0.0.0.0 --port 8000 [--ssl cert.pem key.pem]
"""
import argparse
import asyncio
import json
import logging


class _ProactorNoiseFilter(logging.Filter):
    """errors.txt (Acer field log): 126x ConnectionResetError[WinError 10054]
    from _ProactorBasePipeTransport._call_connection_lost - benign CPython
    teardown noise when a browser drops a connection. Never a real fault;
    keep it out of the service log so real errors stay visible."""

    def filter(self, rec):
        msg = str(getattr(rec, "msg", ""))
        if "_call_connection_lost" in msg:
            return False
        ei = getattr(rec, "exc_info", None)
        if ei and ei[0] is not None and "ConnectionResetError" in ei[0].__name__:
            return False
        return True


logging.getLogger("asyncio").addFilter(_ProactorNoiseFilter())
import os
import secrets
import subprocess
import threading
import time
from urllib.parse import urlsplit

import uvicorn
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import security
from .db import DATA_DIR as _DATA_DIR
from . import autosvc  # round 68: scheduler/alerts/notify/fleet/metrics (file-driven, off by default)
from .db import CAPTURE_DIR, db

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

AGENT_TOKEN = os.environ.get("HKS_AGENT_TOKEN", "")
if not AGENT_TOKEN:
    envp = os.path.join(BASE_DIR, "..", ".env")
    if os.path.exists(envp):
        for line in open(envp):
            if line.strip().startswith("HKS_AGENT_TOKEN="):
                AGENT_TOKEN = line.strip().split("=", 1)[1].strip()
if not AGENT_TOKEN:
    AGENT_TOKEN = secrets.token_urlsafe(24)
    os.makedirs(os.path.join(BASE_DIR, ".."), exist_ok=True)
    with open(envp, "w") as f:
        f.write(f"# HKS Admin Panel config - created {time.ctime()}\n")
        f.write(f"HKS_AGENT_TOKEN={AGENT_TOKEN}\n")
    print(f"[setup] generated agent token -> {os.path.abspath(envp)}")

SESSION_TTL = 7 * 86400
app = FastAPI(title="HKS Admin Panel", docs_url=None, redoc_url=None)


# --------------------------------------------------------------------------
# Agent hub: tracks one connection per (machine_id, role). role: svc | session
# --------------------------------------------------------------------------
# Actions that need the user-session agent (desktop access). Everything else
# goes to the SYSTEM service agent.
SESSION_ACTIONS = {
    "input.apply", "input.enable", "input.status", "input.reset",
    "screen.control", "screen.snapshot", "clipboard.get", "clipboard.set",
    "camera.list", "camera.stream", "camera.snap",  # round 64 (audit6 #17): dropped camera.frame/camera.stop - never existed client-side
    "mic.record", "mic.list", "mic.stream", "talk.stream", "volume.get", "volume.set",
    "volume.mute", "notify.toast", "tts.speak", "media.play_file", "media.play_youtube",
    "media.stop", "terminal.exec_user", "power.lock_screen", "power.logoff",
    "session.status", "session.idle", "session.foreground", "window.list",
    "media.now_playing", "media.play_pause", "media.next", "media.previous",
    "volume.up", "volume.down", "terminal.stream", "terminal.write", "terminal.output",
    "camera.motion", "camera.record", "camera.record_stop",
    "addons.run",
    # Code installs and runtime checks must use the interactive user's PATH
    # and package-manager aliases (not the SYSTEM service environment).
    "code.check", "code.run", "code.install",
}


class AgentConn:
    def __init__(self, ws, machine_id, role, name, version, caps):
        self.ws = ws
        # event loop that OWNS this websocket (field fix 2026-09-21, from
        # the Windows-AI handoff): with the dual listener the companion
        # thread runs its OWN loop - a websocket accepted there must never
        # be awaited from the main loop (and vice versa). All sends go
        # through send_owned() which marshals to the owning loop.
        self.loop = asyncio.get_running_loop()
        self.machine_id = machine_id
        self.role = role
        self.name = name
        self.version = version
        self.caps = caps or []
        # test doubles (the demo mock) accept every action via a fallback and
        # CANNOT publish a closed list - they must declare themselves
        self.test_double = False
        self.pending: dict[str, asyncio.Future] = {}
        self.connected_at = time.time()
        self.last_frame = 0.0

    async def send_json(self, obj):
        await self.ws.send_text(json.dumps(obj, default=str))

    async def send_binary(self, data: bytes):
        await self.ws.send_bytes(data)


class DashboardConn:
    def __init__(self, ws, user, machine_id, tok=""):
        self.ws = ws
        self.loop = asyncio.get_running_loop()   # owner loop (see AgentConn)
        self.user = user
        self.machine_id = machine_id
        self.tok = tok  # session token bound to this socket (R04)


async def send_owned(conn, coro):
    """Await a websocket-send on whichever loop owns the socket."""
    loop = asyncio.get_running_loop()
    if getattr(conn, "loop", None) is loop:
        await coro
    else:
        fut = asyncio.run_coroutine_threadsafe(coro, conn.loop)
        try:
            await asyncio.wait_for(asyncio.wrap_future(fut), timeout=10)
        except asyncio.TimeoutError:
            raise RuntimeError("cross-loop send timed out")


class Hub:
    def __init__(self):
        self.agents: dict[str, dict[str, AgentConn]] = {}
        self.dashboards: set[DashboardConn] = set()
        self.latest_telemetry: dict[str, dict] = {}
        # latest live frame per (machine, channel) so the dashboard can
        # pull real stream frames over REST when its websocket is down
        # (field fix 2026-09: backup live mode)
        self.latest_frames: dict[tuple, tuple] = {}
        self.frame_seq = 0
        # audit4 #A2: this is a THREADING lock on purpose. The dual listener
        # (main + HTTP companion) runs TWO event loops; an asyncio.Lock is
        # bound to the first loop that awaits it and every later use from
        # the other loop raises "attached to a different event loop".
        self.lock = threading.Lock()

    def role_for(self, action: str) -> str:
        return "session" if action in SESSION_ACTIONS else "svc"

    def get_agent(self, machine_id, role) -> AgentConn | None:
        return self.agents.get(machine_id, {}).get(role)

    def machine_ids(self):
        return list(self.agents.keys())

    def first_machine(self):
        for mid, roles in self.agents.items():
            if roles:
                return mid
        return None

    async def attach(self, conn: AgentConn):
        with self.lock:  # no awaits inside (audit4 #A2)
            self.agents.setdefault(conn.machine_id, {})
            old = self.agents[conn.machine_id].get(conn.role)
            self.agents[conn.machine_id][conn.role] = conn
        if old is not None and old is not conn:
            try:  # close the OLD socket on ITS OWN loop (audit4 #A2)
                await send_owned(old, old.ws.close(code=4001, reason="superseded by new connection"))
            except Exception:
                pass
        db.upsert_machine(conn.machine_id, name=conn.name, version=conn.version,
                          online_svc=(conn.role == "svc"),
                          online_session=(conn.role == "session"))
        await self.broadcast_dash({"type": "agent_status", "machine_id": conn.machine_id,
                                   "role": conn.role, "online": True, "name": conn.name,
                                   "caps": conn.caps})
        db.add_event(conn.machine_id, "agent_connected",
                     {"role": conn.role, "version": conn.version})

    async def detach(self, conn: AgentConn):
        with self.lock:  # no awaits inside (audit4 #A2)
            roles = self.agents.get(conn.machine_id, {})
            # Reconnects replace the socket before closing the old one. The
            # old socket's finally block still calls detach; it must not mark
            # the replacement offline in SQLite or the live dashboard.
            was_current = roles.get(conn.role) is conn
            if was_current:
                roles.pop(conn.role, None)
        if not was_current:
            return
        db.upsert_machine(conn.machine_id, online_svc=False if conn.role == "svc" else None,
                          online_session=False if conn.role == "session" else None)
        await self.broadcast_dash({"type": "agent_status", "machine_id": conn.machine_id,
                                   "role": conn.role, "online": False})
        db.add_event(conn.machine_id, "agent_disconnected", {"role": conn.role})

    async def command(self, machine_id, action, params=None, timeout=30.0, user="?"):
        role = self.role_for(action)
        agent = self.get_agent(machine_id, role)
        if agent is not None and agent.caps and not agent.test_double and action not in agent.caps:
            # round 66 (Ammar's contract idea, completed): the agent PUBLISHES
            # its action list on connect (hello caps) - so an action it does
            # not handle fails HERE, in one second with the truth, instead of
            # a 30 s wait for the agent's own "unknown action".
            return {"ok": False,
                    "error": f"'{action}' is not handled by this {role} agent (it publishes {len(agent.caps)} actions)"}
        if agent is None:
            label = "no user desktop session (locked/logged out)" if role == "session" \
                else "service agent offline"
            return {"ok": False, "error": f"agent unavailable: {label}"}
        # run the whole relay ON the agent's owning loop: the agent
        # socket belongs to one loop only, and the reply future must be
        # created on the loop that resolves it (field fix 2026-09-21).
        async def _relay():
            cid = secrets.token_hex(8)
            fut = asyncio.get_running_loop().create_future()
            agent.pending[cid] = fut
            try:
                await agent.send_json({"type": "cmd", "id": cid, "action": action,
                                       "params": params or {}, "user": user})
                return await asyncio.wait_for(fut, timeout)
            except asyncio.TimeoutError:
                return {"ok": False, "error": f"agent timeout after {timeout}s"}
            except Exception as e:
                return {"ok": False, "error": f"relay error: {e}"}
            finally:
                agent.pending.pop(cid, None)

        if agent.loop is asyncio.get_running_loop():
            return await _relay()
        try:
            return await asyncio.wrap_future(
                asyncio.run_coroutine_threadsafe(_relay(), agent.loop))
        except Exception as e:
            return {"ok": False, "error": f"relay error: {e}"}

    async def broadcast_dash(self, obj):
        payload = json.dumps(obj, default=str)
        dead = []
        for d in list(self.dashboards):  # snapshot - companion thread mutates it
            try:
                await send_owned(d, d.ws.send_text(payload))
            except Exception:
                dead.append(d)
        for d in dead:
            self.dashboards.discard(d)

    async def push_frame(self, machine_id, channel: int, data: bytes):
        self.frame_seq += 1
        self.latest_frames[(machine_id, int(channel))] = (self.frame_seq, data)
        for d in list(self.dashboards):
            if d.machine_id == machine_id:
                # fire each send as its own task - a slow browser tab must
                # not delay every other viewer (audit3); failures drop it
                asyncio.ensure_future(self._send_frame(d, channel, data))

    async def _send_frame(self, d, channel: int, data: bytes):
        try:
            await send_owned(d, d.ws.send_bytes(bytes([channel]) + data))
        except Exception:
            self.dashboards.discard(d)


hub = Hub()


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def client_ip(request) -> str:
    # round 61 (audit4 #5/#12): default DISTRUSTS X-Forwarded-For (a client
    # can spoof it and bypass rate-limits/bans). Set HKS_TRUST_PROXY=1 ONLY
    # when the panel is reached through a trusted proxy (cloudflared tunnel).
    if os.environ.get("HKS_TRUST_PROXY", "0") == "1":
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    peer = request.client.host if request.client else ""
    return peer or "?"
    return request.client.host if request.client else "0.0.0.0"


async def body_json(request) -> dict:
    # round 64 (audit6 #22): a malformed body used to become {} and every
    # route then blamed the wrong thing ("invalid password" / "bad action").
    raw = await request.body()
    if not raw:
        return {}  # empty body stays legal
    try:
        return await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="malformed JSON body")


def get_session_user(request):
    tok = request.cookies.get("hks_session", "")
    if not tok:
        # cookie-blocked contexts (sandboxed preview iframes, some webviews):
        # accept the session token via Authorization header or ?token= param
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            tok = auth[7:].strip()
        if not tok:
            try:
                tok = request.query_params.get("token", "")
            except Exception:
                tok = ""
    if not tok:
        return None
    row = db.get_session(tok)
    if not row:
        return None
    user = db.get_user_by_id(row["user_id"])
    if not user:
        return None
    return {"username": user["username"], "role": user["role"], "token": tok,
            "csrf": row["csrf"], "ip": client_ip(request)}


def _origin_ok(ws) -> bool:
    """R02: a browser ALWAYS sends Origin on a WebSocket handshake. If it is
    present it must match OUR Host - a malicious site's cross-site WS hijack
    then dies at the handshake. Non-browser clients (tests, curl) send no
    Origin and stay welcome."""
    org = ws.headers.get("origin")
    if not org:
        return True
    try:
        return urlsplit(org).netloc.lower() == str(ws.headers.get("host", "")).lower()
    except Exception:
        return False


async def _kick_dashboards(token: str, code: int = 4008):
    """R04: revoke/logout must close the LIVE dashboard sockets too."""
    for d in list(hub.dashboards):
        if getattr(d, "tok", "") == token:
            try:
                await send_owned(d, d.ws.close(code=code))
            except Exception:
                pass
            hub.dashboards.discard(d)


def err(status, msg):
    return JSONResponse({"ok": False, "error": msg}, status_code=status)


def require_auth(request, mutating=False):
    retry = security.is_banned(client_ip(request))
    if retry:
        return None, err(403, f"IP banned ({int(retry)}s left)")
    user = get_session_user(request)
    if user is None:
        return None, err(401, "not logged in")
    if mutating:
        if request.method in ("POST", "PUT", "DELETE"):
            csrf = request.headers.get("x-hks-csrf", "")
            if not security.constant_eq(csrf, user["csrf"]):
                return None, err(403, "bad CSRF token")
        if user["role"] != "admin":
            return None, err(403, "observer role is read-only")
    return user, None


_REAUTH_TS: dict[str, float] = {}   # session token -> last password proof (F26)
_cmd_fails: dict[str, int] = {}     # machine -> consecutive unreachable count (F14)
SENSITIVE = {"power.shutdown", "power.restart", "self.terminate", "self_destruct",
             "terminal.exec", "terminal.write", "files.delete", "power.logoff",
             "power.safeboot_on", "power.hibernate"}
_REAUTH_TTL = 300

@app.middleware("http")
async def _ip_allowlist(request: Request, call_next):
    # round 68 (F25): data/ip_allowlist.txt, one CIDR per line. Empty/absent
    # file = allow all (default). Checked before anything else.
    try:
        path = os.path.join(_DATA_DIR, "ip_allowlist.txt")
        txt = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
        nets = [n.strip() for n in txt.splitlines() if n.strip() and not n.strip().startswith("#")]
        if nets:
            import ipaddress as _ipa
            addr = _ipa.ip_address(client_ip(request))
            if not any(addr in _ipa.ip_network(n, strict=False) for n in nets):
                return JSONResponse({"ok": False, "error": "IP not allowed"}, status_code=403)
    except ValueError:
        return JSONResponse({"ok": False, "error": "client IP not parseable"}, status_code=403)
    except Exception:
        pass
    return await call_next(request)

@app.middleware("http")
async def guard(request: Request, call_next):
    path = request.url.path
    # IP bans only gate unauthenticated entry points; valid sessions keep working
    # (prevents one attacker's failures from locking out a NAT'd office).
    if path == "/api/login" or path == "/setup":
        ip = client_ip(request)
        retry = security.is_banned(ip)
        if retry:
            return JSONResponse({"ok": False, "error": f"IP banned. retry in {retry}s"},
                                status_code=429)
    elif path.startswith("/api"):
        ip = client_ip(request)
        ok, retry = security.api_limiter.check(ip)
        if not ok:
            return JSONResponse({"ok": False, "error": f"rate limited, slow down ({retry}s)"},
                                status_code=429)
    resp = await call_next(request)
    # cache policy (field fix 2026-09): the panel UI must NEVER be served
    # stale from the browser after an upgrade — revalidate static assets
    # every load, never cache API answers.
    if path == "/":
        resp.headers["Cache-Control"] = "no-store, must-revalidate"  # shell: never stale
    elif path.startswith("/static"):
        resp.headers["Cache-Control"] = "no-cache"  # assets are versioned (?v=N)
    elif path.startswith("/api"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


# --------------------------------------------------------------------------
# auth routes
# --------------------------------------------------------------------------
def _prune_capture_files():
    """Keep newest 400 capture images, drop anything older than 30 days."""
    try:
        files = [os.path.join(CAPTURE_DIR, f) for f in os.listdir(CAPTURE_DIR)]
        files.sort(key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0,
                   reverse=True)
        now = time.time()
        for i, p in enumerate(files):
            try:
                mt = os.path.getmtime(p)
            except OSError:
                continue
            if i >= 400 or now - mt > 30 * 86400:
                try:
                    os.remove(p)
                except OSError:
                    pass
    except Exception:
        pass


async def _janitor_loop():
    """Hourly: DB prune + capture file prune (keeps the panel lean 24/7)."""
    while True:
        try:
            db.prune()
            _prune_capture_files()
        except Exception:
            pass
        await asyncio.sleep(3600)


async def _liveness_loop():
    """Ping every agent every 30 s; evict sockets that cannot accept a send
    (laptop slept / process killed - TCP alone can take minutes to notice)."""
    while True:
        await asyncio.sleep(30)
        for d in list(hub.dashboards):  # R04: dead sessions lose the live WS
            if not getattr(d, "tok", "") or not db.get_session(d.tok):
                try:
                    await send_owned(d, d.ws.close(code=4008))
                except Exception:
                    pass
                hub.dashboards.discard(d)
        for mid in hub.machine_ids():
            for role in list(hub.agents.get(mid, {}).keys()):
                a = hub.get_agent(mid, role)
                if a is None:
                    continue
                try:
                    await asyncio.wait_for(
                        send_owned(a, a.send_json({"type": "ping"})), timeout=6)
                except Exception:
                    try:
                        await hub.detach(a)
                    except Exception:
                        pass


@app.on_event("startup")
async def _startup():
    db.prune()
    _prune_capture_files()
    asyncio.create_task(_janitor_loop())
    asyncio.create_task(_liveness_loop())  # audit4 #A3
    autosvc.init(_DATA_DIR, hub)           # round 68: scheduler/alerts/notify
    for _c in autosvc.loops():
        asyncio.create_task(_c)


# ---------------- round 67 (Ammar UI doc + F-list) ----------------
_R67_BOOT = time.time()

def _status_token():
    import hashlib
    return hashlib.sha256(("hks-status:" + AGENT_TOKEN).encode()).hexdigest()[:16]

@app.post("/api/recover")
async def api_recover(request: Request):
    # round 67: real password-reset path. A one-time recovery code (printed
    # at install, or handed over by RESET_PASSWORD.bat on the laptop) is the
    # "something you have". Rate limited on its own key; failures feed the
    # same ban logic as login failures so this is not a brute-force side door.
    ip = client_ip(request)
    ok, retry = security.login_limiter.check("recover:" + ip)
    if not ok:
        return JSONResponse({"ok": False, "error": f"too many attempts, wait {retry}s"},
                            status_code=429)
    body = await body_json(request)
    code = str(body.get("code", "")).strip()
    new_pw = str(body.get("password", ""))
    if not code:
        return err(400, "recovery code required")
    if len(new_pw) < 8:
        return err(400, "password must be at least 8 characters")
    row = db.redeem_recovery_code(code)
    if not row:
        security.register_failed_login(ip)
        db.add_event(None, "recover_failed", {"ip": ip})
        return err(401, "invalid or already-used recovery code")
    db.set_user_password(row["user_id"], security.hash_password(new_pw))
    db.add_event(None, "recover_ok", {"user": row["username"], "ip": ip})
    security.login_limiter.reset("recover:" + ip)
    return JSONResponse({"ok": True, "user": row["username"]})

@app.get("/api/docs")
async def api_docs(request: Request):
    # round 67 (F45): live introspection - this install's real routes, agent
    # actions (from published caps), and env keys. Never goes stale.
    user, e = require_auth(request)
    if e:
        return e
    routes = sorted(({"path": r.path, "methods": ",".join(sorted(getattr(r, "methods", []) or []))}
                     for r in app.routes if hasattr(r, "path")), key=lambda x: x["path"])
    actions = set()
    for _roles in hub.agents.values():
        for _a in _roles.values():
            actions.update(_a.caps or [])
    env_keys = []
    envp = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if os.path.exists(envp):
        for line in open(envp, encoding="utf-8", errors="replace"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                env_keys.append(line.split("=", 1)[0])
    return JSONResponse({"ok": True, "version": HKS_BUILD_VERSION, "routes": routes,
                         "agent_actions": sorted(actions), "env_keys": sorted(env_keys),
                         "status_path": "/status/" + _status_token()})

@app.get("/api/diagnostics/bundle")
async def diagnostics_bundle(request: Request):
    # round 67 (F20): one redacted zip that is safe to paste into a support
    # chat. Tokens and passwords never leave this function.
    user, e = require_auth(request)
    if e:
        return e
    import io, re, zipfile, platform
    def _redact(t: str) -> str:
        t = re.sub(r"(HKS_AGENT_TOKEN=)[A-Za-z0-9_\-]+", r"\1<redacted>", t)
        t = re.sub(r"(password[\"']?\s*[:=]\s*)[^\s\",}]+", r"\1<redacted>", t, flags=re.I)
        return t
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        envp = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
        if os.path.exists(envp):
            z.writestr("env.txt", _redact(open(envp, encoding="utf-8", errors="replace").read()))
        try:
            for name in sorted(os.listdir(_DATA_DIR)):
                if name.endswith(".log"):
                    p = os.path.join(_DATA_DIR, name)
                    try:
                        with open(p, encoding="utf-8", errors="replace") as f:
                            z.writestr("logs/" + name, _redact(f.read()[-300000:]))
                    except Exception:
                        pass
        except Exception:
            pass
        z.writestr("system.json", json.dumps({
            "version": HKS_BUILD_VERSION, "os": platform.platform(),
            "python": platform.python_version(), "host": platform.node(),
            "users": [{"username": u["username"], "role": u["role"], "created": u["created"],
                       "last_login": u["last_login"]} for u in db.list_users()],
            "machines": list(hub.agents.keys())}, indent=2, default=str))
    buf.seek(0)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return StreamingResponse(buf, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="hks_diagnostics_{stamp}.zip"'})

@app.get("/status/{token}/json")
async def public_status_json(token: str):
    # round 67 (F44): unauthenticated heartbeat for monitoring tools. The
    # token is derived from the agent secret (never logged, never in UI).
    if token != _status_token():
        raise HTTPException(status_code=404, detail="not found")
    mids = list(hub.agents.keys())
    return JSONResponse({"online": bool(mids), "machines": len(mids),
                         "uptime_s": int(time.time() - _R67_BOOT)})

@app.get("/status/{token}")
async def public_status(token: str):
    if token != _status_token():
        raise HTTPException(status_code=404, detail="not found")
    return HTMLResponse("""<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">"""
                        """<title>HKS status</title></head>
<body style="background:#0b0e14;color:#e9ecf4;font:16px system-ui;text-align:center;padding-top:70px">
<div style="font-size:12px;letter-spacing:.12em;color:#8f97ac;text-transform:uppercase">HKS agent</div>
<div id="s" style="margin-top:14px">loading...</div>
<script>
async function poll(){try{const j=await fetch('json').then(r=>r.json());
document.getElementById('s').innerHTML=j.online
 ?'<span style="color:#2fbf71;font-size:46px;line-height:1">&#9679;</span><br><b>ONLINE</b><br><small style="color:#8f97ac">'+j.machines+' agent(s) connected</small>'
 :'<span style="color:#ef5466;font-size:46px;line-height:1">&#9679;</span><br><b>OFFLINE</b>';}catch(e){}}
poll();setInterval(poll,3000);
</script></body></html>""")
# ---------------- round 68: security + ops endpoints ----------------

@app.post("/api/reauth")
async def api_reauth(request: Request):
    # round 68 (F26): fresh password proof for sensitive actions
    user, e = require_auth(request, mutating=True)
    if e:
        return e
    body = await body_json(request)
    _u = db.get_user(user["username"])
    if not security.verify_password(str(body.get("password", "")), _u["pw_hash"]):
        security.register_failed_login(client_ip(request))
        return err(401, "wrong password")
    _REAUTH_TS[user["token"]] = time.time()
    return JSONResponse({"ok": True, "good_for_s": _REAUTH_TTL})

@app.get("/api/2fa/setup")
async def api_2fa_setup(request: Request):
    # round 68 (F24): generate a secret + the otpauth URI. QR apps accept the
    # URI typed or pasted; no image dependency by design.
    user, e = require_auth(request, mutating=True)
    if e:
        return e
    secret = security.totp_secret()
    _u = db.get_user(user["username"])
    db.set_totp(_u["id"], secret, False)   # stored but NOT active yet
    return JSONResponse({"ok": True, "secret": secret,
                         "uri": security.totp_uri(secret, user["username"]),
                         "note": "add to any authenticator app, then verify a code to activate"})

@app.post("/api/2fa/enable")
async def api_2fa_enable(request: Request):
    user, e = require_auth(request, mutating=True)
    if e:
        return e
    body = await body_json(request)
    u = db.get_user(user["username"])
    if not u.get("totp_secret"):
        return err(400, "run setup first")
    if not security.totp_verify(u["totp_secret"], str(body.get("code", ""))):
        return err(401, "code did not match - check the app and try again")
    db.set_totp(u["id"], u["totp_secret"], True)
    db.add_event(None, "2fa_enabled", {"user": user["username"]})
    return JSONResponse({"ok": True})

@app.post("/api/2fa/disable")
async def api_2fa_disable(request: Request):
    user, e = require_auth(request, mutating=True)
    if e:
        return e
    body = await body_json(request)
    _u = db.get_user(user["username"])
    if not security.verify_password(str(body.get("password", "")), _u["pw_hash"]):
        return err(401, "password required to turn 2FA off")
    db.set_totp(_u["id"], None, False)
    db.add_event(None, "2fa_disabled", {"user": user["username"]})
    return JSONResponse({"ok": True})

@app.get("/metrics")
async def api_metrics():
    # round 68 (F19): Prometheus text. OFF unless HKS_METRICS_ENABLED=1.
    if os.environ.get("HKS_METRICS_ENABLED") != "1":
        raise HTTPException(status_code=404, detail="not found")
    return Response(autosvc.metrics_text(hub, _R67_BOOT),
                    media_type="text/plain; version=0.0.4")

@app.get("/api/capabilities")
async def api_capabilities(request: Request):
    # round 68 (F15): what THIS machine can do RIGHT NOW (presence-based)
    user, e = require_auth(request)
    if e:
        return e
    mid = hub.first_machine() or ""
    svc = hub.get_agent(mid, "svc") if mid else None
    ses = hub.get_agent(mid, "session") if mid else None
    caps = {"machine": mid, "svc": bool(svc), "session": bool(ses),
            "screen": bool(ses), "camera": bool(svc), "mic": bool(svc),
            "terminal": bool(svc or ses), "files": bool(svc),
            "degraded": not (bool(svc) and bool(ses))}
    return JSONResponse({"ok": True, "caps": caps})

@app.get("/api/fleet")
async def api_fleet(request: Request):
    # round 68 (F31): read-only peer status via their /status tokens. Off
    # unless data/fleet.json lists peers.
    user, e = require_auth(request)
    if e:
        return e
    return JSONResponse({"ok": True, "peers": await autosvc.fetch_fleet()})

@app.get("/api/network-path")
async def api_network_path(request: Request):
    # round 68 (F23): what the LAPTOP can see about its own reachability.
    # Answers on every OS; Windows gets the extra firewall/bind detail.
    user, e = require_auth(request)
    if e:
        return e
    rep = {"port": main_bind_port if "main_bind_port" in globals() else 8000,
           "host": "0.0.0.0", "checks": []}
    import socket as _s
    ok4 = True
    try:
        with _s.create_connection(("127.0.0.1", rep["port"]), timeout=2):
            rep["checks"].append({"name": "local door answers", "ok": True})
    except OSError:
        ok4 = False
        rep["checks"].append({"name": "local door answers", "ok": False})
    bound = [str(getattr(c, "bind", "")) for c in []]  # placeholder no-op
    rep["checks"].append({"name": "agents connected", "ok": bool(hub.agents),
                          "detail": f"{len(hub.agents)} machine(s)"})
    rep["hint"] = ("Door answers locally - if your PHONE cannot open it, check: same Wi-Fi, "
                   "the address printed by CHECK_STATUS.bat, and the https certificate warning "
                   "(tap Proceed/Advanced)." if ok4 else
                   "The local door is DOWN - the panel process is not listening. Run HKS_DOCTOR.bat.")
    return JSONResponse({"ok": True, "report": rep})

@app.get("/api/config/export")
async def api_config_export(request: Request):
    # round 68 (F35): portable JSON. NEVER exports password hashes or tokens.
    user, e = require_auth(request)
    if e:
        return e
    return JSONResponse({"ok": True, "version": HKS_BUILD_VERSION,
                         "exported": time.strftime("%Y-%m-%dT%H:%M:%S"),
                         "users": [{"username": u["username"], "role": u["role"]}
                                   for u in db.list_users()],
                         "note": "passwords are not portable by design - imported users get a reset"})

@app.post("/api/config/import")
async def api_config_import(request: Request):
    user, e = require_auth(request, mutating=True)
    if e:
        return e
    body = await body_json(request)
    made = 0
    for u in body.get("users", []):
        name = str(u.get("username", ""))[:64]
        if name and not db.get_user(name):
            import secrets as _sc
            db.create_user(name, security.hash_password(_sc.token_urlsafe(12)),
                           str(u.get("role", "observer")))
            made += 1
    db.add_event(None, "config_import", {"users_created": made})
    return JSONResponse({"ok": True, "users_created": made,
                         "note": "new users start with a random password - reset it in Users"})

@app.post("/api/self_destruct/initiate")
async def self_destruct_initiate(request: Request):
    # round 68 (F08, step 1): panel shows a 6-digit code that lives 5 minutes.
    import secrets as _sc
    user, e = require_auth(request, mutating=True)
    if e:
        return e
    body = await body_json(request)
    mid = body.get("machine") or hub.first_machine()
    if not mid:
        return err(503, "no agent connected")
    code = f"{_sc.randbelow(1000000):06d}"
    _PENDING_WIPES[mid] = {"code": code, "expires": time.time() + 300,
                           "by": user["username"]}
    db.add_audit(user["username"], user["ip"], "wipe_initiated", {"machine": mid}, "pending")
    return JSONResponse({"ok": True, "code": code, "expires_in": 300})

@app.post("/api/self_destruct/confirm")
async def self_destruct_confirm(request: Request):
    # round 68 (F08, step 2): the wipe itself still goes through the
    # existing typed-CONFIRM route; this endpoint just validates the code
    # and stamps the reauth proof so the destructive call is allowed.
    user, e = require_auth(request, mutating=True)
    if e:
        return e
    body = await body_json(request)
    mid = body.get("machine") or hub.first_machine()
    ent = _PENDING_WIPES.get(mid)
    if not ent or time.time() > ent["expires"]:
        _PENDING_WIPES.pop(mid, None)
        return err(410, "no pending wipe (or code expired) - start again")
    if str(body.get("code", "")) != ent["code"]:
        security.register_failed_login(client_ip(request))
        return err(403, "wrong code")
    _PENDING_WIPES.pop(mid, None)
    _REAUTH_TS[user["token"]] = time.time()
    db.add_audit(user["username"], user["ip"], "wipe_confirmed", {"machine": mid}, "armed")
    return JSONResponse({"ok": True, "next": "POST /api/self_destruct with CONFIRM to execute"})

_PENDING_WIPES: dict[str, dict] = {}
# ---------------- end round 68 block ----------------
# ---------------- end round 67 block ----------------

@app.get("/")
async def index():
    # never cache the shell: a stale index.html referencing old asset
    # versions is a classic "my login broke after upgrade" cause
    return FileResponse(os.path.join(STATIC_DIR, "index.html"),
                        headers={"Cache-Control": "no-store, must-revalidate"})


@app.get("/api/qr.svg")
async def qr_svg(text: str = ""):
    """QR code (SVG) for pcbu-style phone pairing - encodes the panel URL."""
    if not text:
        return JSONResponse({"ok": False, "error": "text required"}, status_code=400)
    try:
        import io as _io
        import segno
    except ImportError:
        return JSONResponse({"ok": False, "error": "segno not installed on the server"},
                            status_code=501)
    buf = _io.BytesIO()
    segno.make(text[:512], error="m").save(buf, kind="svg", scale=4, border=2,
                                           dark="#0b0e14", light="#ffffff")
    return Response(buf.getvalue().decode("utf-8"), media_type="image/svg+xml",
                    headers={"Cache-Control": "no-store"})


@app.get("/api/me")
async def api_me(request: Request):
    user = get_session_user(request)
    if not user:
        return JSONResponse({"ok": True, "user": None,
                             "needs_setup": not db.list_users(),
                             "build": HKS_BUILD_VERSION})
    return JSONResponse({"ok": True, "build": HKS_BUILD_VERSION,
                         "user": {"username": user["username"],
                                              "role": user["role"], "csrf": user["csrf"],
                                              "build": HKS_BUILD_VERSION}})  # round 65 (audit7 #44)


# ---------------- login capture (field request 2026-09-21) ----------------
# photo evidence on EVERY panel login AND on failed attempts, via the
# session agent's camera. Never blocks or fails the login itself.
_fail_snaps = {}


def _fail_snap_allowed(ip: str):
    """Max 5 failed-login webcam snaps per IP per hour (audit3: a brute
    force run must not farm camera captures / hub slots)."""
    import collections
    if len(_fail_snaps) > 5000:  # round 65 (audit7 #29): bound the map - the
        _fail_snaps.clear()      # XFF spoof vector died in round 61, but real scanner IPs still accumulate
    dq = _fail_snaps.setdefault(ip, collections.deque())
    now = time.time()
    while dq and now - dq[0] > 3600:
        dq.popleft()
    if len(dq) >= 5:
        return False
    dq.append(now)
    return True


async def _login_capture(username: str, ip: str, ok: bool, action: str = "login"):
    try:
        machine = hub.first_machine()
        if not machine:
            db.add_event(None, "capture_failed", {"why": "No desktop agent is connected; camera capture was unavailable.",
                                                   "user": username, "login_ok": ok, "action": action})
            return
        res = await hub.command(machine, "camera.snap", {}, timeout=25,
                                user="login-capture")
        b64 = ((res or {}).get("data") or {}).get("b64")
        if not b64:
            reason = (res or {}).get("error") or "no image from the session agent"
            db.add_event(machine, "capture_failed",
                         {"why": reason, "user": username, "login_ok": ok, "action": action})
            return
        import base64 as _b64
        stamp = time.strftime("%Y%m%d_%H%M%S")
        prefix = {"logout": "logout_", "password_change": "password_change_"}.get(action,
                 "login_" + ("ok_" if ok else "FAIL_"))
        fn = prefix + stamp + ".jpg"
        os.makedirs(CAPTURE_DIR, exist_ok=True)
        with open(os.path.join(CAPTURE_DIR, fn), "wb") as f:
            f.write(_b64.b64decode(b64))
        db.add_capture(machine, "login", fn,
                       json.dumps({"user": username, "ip": ip, "ok": ok, "action": action}))
        event_kind = ("capture_login" if ok else "capture_login_failed") if action == "login" else "capture_" + action
        db.add_event(machine, event_kind, {"user": username, "ip": ip, "file": fn})
    except Exception as e:
        try:
            db.add_event(hub.first_machine() or "?", "capture_failed",
                         {"why": str(e)[:200], "user": username, "login_ok": ok, "action": action})
        except Exception:
            pass


@app.post("/api/login")
async def api_login(request: Request):
    ip = client_ip(request)
    # Origin check, reverse-proxy aware: the browser's Origin host must match
    # the Host we are served under (direct OR via X-Forwarded-* from a proxy
    # / tunnel / sandbox preview). A mismatch means the form was posted from
    # another site - block it, but it is NOT a credential failure, so it must
    # never count toward the ban counter (a shared proxy IP must not get
    # locked out by a header quirk).
    origin = request.headers.get("origin", "")
    if origin:
        from urllib.parse import urlparse as _up
        o_host = (_up(origin).netloc or "").lower()
        hosts = set()
        for h in (request.headers.get("host", ""), request.headers.get("x-forwarded-host", "")):
            for part in h.split(","):
                part = part.strip().lower()
                if part:
                    hosts.add(part)
                    if ":" in part:
                        hosts.add(part.rsplit(":", 1)[0])  # host w/o port
        if o_host and not any(o_host == h or o_host.endswith(":" + h) or h == o_host.split(":")[0]
                              for h in hosts):
            return err(403, "cross-origin login blocked")
    body = await body_json(request)
    username = str(body.get("username", ""))[:64]
    password = str(body.get("password", ""))[:256]
    ok, retry = security.login_limiter.check(ip)
    if not ok:
        return JSONResponse({"ok": False, "error": f"too many attempts, wait {retry}s"},
                            status_code=429)
    user = db.get_user(username)
    ok_pw = bool(user) and security.verify_password(password, user["pw_hash"])
    if ok_pw:
        _up = security.maybe_rehash(password, user["pw_hash"])  # R16: 200k -> 600k
        if _up:
            db.set_user_password(user["id"], _up)
    if ok_pw and user.get("totp_enabled") and user.get("totp_secret"):
        # round 68 (F24): password alone is not enough once 2FA is on
        if not security.totp_verify(user["totp_secret"], str(body.get("totp", ""))):
            db.add_event(None, "login_totp_failed", {"user": username, "ip": ip})
            return JSONResponse({"ok": False, "totp_required": True,
                                 "error": "enter the 6-digit code from your authenticator app"},
                                status_code=401)
    if not ok_pw:
        security.register_failed_login(ip)
        db.add_event(None, "login_failed", {"user": username, "ip": ip})
        if _fail_snap_allowed(ip):  # brute force must not farm camera snaps
            asyncio.create_task(_login_capture(username, ip, False))
        return err(401, "invalid username or password")
    security.login_limiter.reset(ip)
    try:  # round 68 (F28, offline): login from an IP this user never used -> event + notify
        known = db.login_ips(username)
        if known and ip not in known:
            db.add_event(None, "login_anomaly", {"user": username, "ip": ip})
            db.add_audit(username, ip, "login_anomaly", {"new_ip": ip}, "flagged")
            asyncio.create_task(autosvc.send_all("HKS login anomaly",
                f"Login for '{username}' from a new IP: {ip}", "login_anomaly", {"ip": ip}))
    except Exception:
        pass
    tok = security.new_session_token()
    csrf = security.new_csrf()
    # session rotation on login (OWASP session-management / fixation
    # defense): retire the session this browser already presented so
    # stale sessions don't pile up in the cookie jar. Open pages that
    # still hold the old CSRF token heal themselves via the client-side
    # 403 recovery (re-sync from /api/me, retry once) — field fix
    # 2026-09: second login used to brick every open tab with
    # "bad CSRF token" until a manual reload.
    old_tok = request.cookies.get("hks_session", "")
    if old_tok:
        db.delete_session(old_tok)
    db.create_session(tok, csrf, user["id"], SESSION_TTL, ip)
    db.touch_login(user["id"])
    db.add_event(None, "login_ok", {"user": username, "ip": ip})
    db.add_audit(username, ip, "login", {}, "ok")
    asyncio.create_task(_login_capture(username, ip, True))
    resp = JSONResponse({"ok": True, "token": tok,
                         "user": {"username": username, "role": user["role"],
                                  "csrf": csrf}})
    resp.set_cookie("hks_session", tok, max_age=SESSION_TTL, httponly=True,
                    samesite="lax",  # audit2 #16: Secure also behind tunnels/proxies
                    secure=(request.url.scheme == "https"
                            or request.headers.get("x-forwarded-proto", "http").split(",")[0].strip().lower() == "https"))  # audit4: chained proxies
    return resp


@app.post("/api/logout")
async def api_logout(request: Request):
    user = get_session_user(request)
    if user:
        asyncio.create_task(_login_capture(user["username"], user["ip"], True, "logout"))
        db.delete_session(user["token"])
        db.add_audit(user["username"], user["ip"], "logout", {}, "ok")
        await _kick_dashboards(user["token"])  # R04: logout = logged out NOW
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("hks_session")
    return resp


def _is_local(request: Request) -> bool:
    """round 61 (audit4 #3): first-run /setup must only be reachable from
    the laptop itself - an empty DB on 0.0.0.0 otherwise lets anyone on
    the LAN claim the admin account before the owner does."""
    return client_ip(request) in ("127.0.0.1", "::1")


@app.get("/setup")
async def setup_page(request: Request):
    if not _is_local(request):
        return HTMLResponse("<h3>Setup runs on the laptop itself: open http://127.0.0.1:PORT/setup there.</h3>", status_code=403)
    if db.list_users():
        return HTMLResponse("<h3>Setup already completed. <a href='/'>Login</a></h3>")
    return FileResponse(os.path.join(STATIC_DIR, "setup.html"))


@app.post("/setup")
async def setup_do(request: Request):
    if not _is_local(request):
        return err(403, "setup only allowed from the laptop itself (127.0.0.1)")
    if db.list_users():
        return err(409, "setup already completed")
    body = await body_json(request)
    u = str(body.get("username", "")).strip()
    p = str(body.get("password", ""))
    if len(u) < 3 or len(p) < 8:
        return err(400, "username >=3 chars, password >=8 chars")
    db.create_user(u, security.hash_password(p), "admin")
    db.add_event(None, "server_setup", {"admin": u, "ip": client_ip(request)})
    return JSONResponse({"ok": True})


# --------------------------------------------------------------------------
# user management
# --------------------------------------------------------------------------
@app.get("/api/users")
async def users_list(request: Request):
    user, e = require_auth(request)
    if e:
        return e
    return JSONResponse({"ok": True, "users": db.list_users(),
                         "sessions": db.list_sessions(), "bans": db.list_bans()})


@app.post("/api/users")
async def users_create(request: Request):
    user, e = require_auth(request, mutating=True)
    if e:
        return e
    body = await body_json(request)
    u = str(body.get("username", "")).strip()
    p = str(body.get("password", ""))
    role = body.get("role", "observer")
    if role not in ("admin", "observer"):
        return err(400, "role must be admin|observer")
    if len(u) < 3 or len(p) < 8:
        return err(400, "username >=3 chars, password >=8 chars")
    if db.get_user(u):
        return err(409, "user exists")
    db.create_user(u, security.hash_password(p), role)
    db.add_audit(user["username"], user["ip"], "user_create", {"username": u, "role": role}, "ok")
    return JSONResponse({"ok": True})


@app.post("/api/users/update")
async def users_update(request: Request):
    user, e = require_auth(request, mutating=True)
    if e:
        return e
    body = await body_json(request)
    try:
        uid = int(body.get("id", 0))
    except (TypeError, ValueError):
        return err(400, "id must be a number")
    target = db.get_user_by_id(uid)
    if not target:
        return err(404, "no such user")
    if body.get("password"):
        p = str(body["password"])
        if len(p) < 8:
            return err(400, "password >=8 chars")
        db.set_user_password(uid, security.hash_password(p))
        asyncio.create_task(_login_capture(target["username"], user["ip"], True, "password_change"))
    if body.get("role") in ("admin", "observer"):
        db.set_user_role(uid, body["role"])
    db.add_audit(user["username"], user["ip"], "user_update",
                 {"id": uid, "set_password": bool(body.get("password")),
                  "role": body.get("role")}, "ok")
    return JSONResponse({"ok": True})


@app.post("/api/users/delete")
async def users_delete(request: Request):
    user, e = require_auth(request, mutating=True)
    if e:
        return e
    body = await body_json(request)
    try:
        uid = int(body.get("id", 0))
    except (TypeError, ValueError):
        return err(400, "id must be a number")
    if target := db.get_user_by_id(uid):
        if target["username"] == user["username"]:
            return err(400, "cannot delete yourself")
        db.delete_user(uid)
        db.add_audit(user["username"], user["ip"], "user_delete", {"id": uid}, "ok")
        return JSONResponse({"ok": True})
    return err(404, "no such user")


@app.post("/api/bans/remove")
async def ban_remove(request: Request):
    user, e = require_auth(request, mutating=True)
    if e:
        return e
    body = await body_json(request)
    db.remove_ban(str(body.get("ip", "")))
    db.add_audit(user["username"], user["ip"], "ban_remove", body, "ok")
    return JSONResponse({"ok": True})


@app.post("/api/sessions/revoke")
async def session_revoke(request: Request):
    user, e = require_auth(request, mutating=True)
    if e:
        return e
    body = await body_json(request)
    db.revoke_session(str(body.get("token", "")))
    db.add_audit(user["username"], user["ip"], "session_revoke", body, "ok")
    return JSONResponse({"ok": True})


# --------------------------------------------------------------------------
# machine data + command relay
# --------------------------------------------------------------------------
@app.get("/api/export")
async def export_csv(request: Request, what: str = "events", machine: str = "", limit: int = 5000):
    """Download logs/telemetry as CSV from the panel."""
    user, e = require_auth(request)
    if e:
        return e
    import csv
    import io as _io
    what = what if what in ("events", "audit", "errors", "telemetry") else "events"
    rows = {"events": lambda: db.list_events(machine or None, None, min(limit, 20000)),
            "audit": lambda: db.list_audit(min(limit, 20000)),
            "errors": lambda: db.list_errors(machine or None, min(limit, 20000)),
            "telemetry": lambda: db.latest_telemetry(machine or hub.first_machine() or "",
                                                     min(limit, 20000))}[what]()
    buf = _io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()), extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition":
                                      f'attachment; filename="hks_{what}.csv"'})


@app.post("/api/self_destruct")
async def self_destruct(request: Request):
    """Terminates the panel and wipes all server data. Requires typed CONFIRM."""
    user, e = require_auth(request, mutating=True)
    if e:
        return e
    body = await body_json(request)
    if str(body.get("confirm", "")) != "CONFIRM":
        return err(400, 'type CONFIRM exactly to terminate')
    db.add_audit(user["username"], user["ip"], "self_destruct", {}, "requested")
    import subprocess
    import sys as _sys
    # round 61 (audit4 #11): BASE_DIR/.. is HKS_AdminPanel - the cleaner
    # used to wipe only half the install and leave the agents running.
    here = os.path.abspath(os.path.join(BASE_DIR, ".."))            # panel dir
    root = os.path.abspath(os.path.join(BASE_DIR, "..", ".."))      # C:/HKS_PROGRAM
    # detached cleaner: waits for this process to die, then removes data,
    # both task registrations, agent config, panel .env - and with
    # HKS_WIPE_ALL=1 the entire C:/HKS_PROGRAM folder.
    cleanup = (
        "import os,shutil,time,sys,subprocess\n"
        f"panel = {here!r}\n"
        f"target = {root!r}\n"
        "for tn in ('HKS-Panel','HKS-Agent','HKS-SessionAgent'):\n"
        "    subprocess.run(['schtasks','/End','/TN',tn], capture_output=True)\n"
        "    subprocess.run(['schtasks','/Delete','/TN',tn,'/F'], capture_output=True)\n"
        "for _ in range(60):\n"
        "    time.sleep(1)\n"
        "    try:\n"
        "        shutil.rmtree(os.path.join(target,'data'), ignore_errors=True)\n"
        "        shutil.rmtree(os.path.join(target,'data_panel'), ignore_errors=True)\n"
        "        for f in (os.path.join(panel,'.env'), os.path.join(target,'HKS_Client','config.json')):\n"
        "            try: os.remove(f)\n"
        "            except OSError: pass\n"
        "        if os.environ.get('HKS_WIPE_ALL') == '1':\n"
        "            shutil.rmtree(target, ignore_errors=True)\n"
        "        break\n"
        "    except Exception:\n"
        "        pass\n"
    )
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) or getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([_sys.executable, "-c", cleanup],
                     creationflags=flags if os.name == "nt" else 0,
                     start_new_session=os.name != "nt")
    asyncio.get_running_loop().call_later(0.5, lambda: os._exit(0))
    return JSONResponse({"ok": True, "note": "panel terminating; data wiped"})


@app.get("/api/machines")
async def machines(request: Request):
    user, e = require_auth(request)
    if e:
        return e
    rows = db.list_machines()
    for r in rows:
        b = db.one("SELECT ts FROM events WHERE machine_id=? AND kind='boot' "
                   "ORDER BY id DESC LIMIT 1", (r["machine_id"],))
        a = db.one("SELECT ts FROM events WHERE machine_id=? AND kind='agent_start' "
                   "ORDER BY id DESC LIMIT 1", (r["machine_id"],))
        r["boot_ts"] = b["ts"] if b else None
        r["agent_start_ts"] = a["ts"] if a else None
    live = {}
    for mid, roles in hub.agents.items():
        live[mid] = {"svc": "svc" in roles, "session": "session" in roles}
    for r in rows:
        l = live.get(r["machine_id"], {})
        r["live_svc"] = bool(l.get("svc"))
        r["live_session"] = bool(l.get("session"))
        # latest telemetry snapshot so the dashboard cards can paint even
        # while the browser's live websocket is down (field fix 2026-09)
        r["telemetry"] = hub.latest_telemetry.get(r["machine_id"])
    return JSONResponse({"ok": True, "machines": rows,
                         "default_machine": hub.first_machine(),
                         "agent_token_hint": AGENT_TOKEN[:6] + "…" if user["role"] == "admin" else ""})


@app.get("/api/frame")
async def api_frame(request: Request, machine: str = "", ch: int = 1):
    user, e = require_auth(request)
    if e:
        return e
    machine = machine or hub.first_machine()
    import base64 as _b64
    seq, data = hub.latest_frames.get((machine, int(ch)), (0, None))
    if data is None:
        return JSONResponse({"ok": True, "seq": 0, "b64": ""})
    return JSONResponse({"ok": True, "seq": seq,
                         "b64": _b64.b64encode(data).decode()})


@app.get("/api/telemetry")
async def telemetry(request: Request, machine: str = "", limit: int = 240):
    user, e = require_auth(request)
    if e:
        return e
    machine = machine or hub.first_machine()
    if not machine:
        return JSONResponse({"ok": True, "points": []})
    return JSONResponse({"ok": True, "machine": machine,
                         "points": db.latest_telemetry(machine, min(limit, 1000))})


@app.post("/api/cmd")
async def api_cmd(request: Request):
    user, e = require_auth(request, mutating=True)
    if e:
        return e
    body = await body_json(request)
    machine = body.get("machine") or hub.first_machine()
    action = str(body.get("action", ""))
    # round 68 (F26): destructive/terminal actions need a fresh password proof
    if action in SENSITIVE and time.time() - _REAUTH_TS.get(user["token"], 0) > _REAUTH_TTL:
        return JSONResponse({"ok": False, "reauth_required": True,
                             "error": "enter your password again for this action"},
                            status_code=401)
    # round 68 (F14): circuit breaker - a dead agent fails FAST after 5
    # straight unreachable/timeout results instead of hanging every click
    global _cmd_fails
    _f = _cmd_fails.get(machine, 0)
    if _f >= 5:
        return JSONResponse({"ok": False,
                             "error": "circuit open: agent unreachable 5x - pausing 30s (Refresh retries)",
                             "circuit": "open"}, status_code=503)
    if not machine:
        return err(503, "no agent connected")
    if not action or any(c in action for c in "\r\n\x00"):
        return err(400, "bad action")
    # round 62 (audit5 #6): validate AND clamp both ends - "abc" was a 500,
    # -1 was a silent always-timeout
    try:
        _cap = 600.0 if action == "code.install" else 120.0  # round 65 (audit7 #45): installs outlive 120s; admin-only action
        timeout = max(1.0, min(float(body.get("timeout", 30)), _cap))
    except (TypeError, ValueError):
        return err(400, "timeout must be a number")
    result = await hub.command(machine, action, body.get("params") or {},
                               timeout=timeout, user=user["username"])
    db.add_audit(user["username"], user["ip"], "cmd:" + action,
                 body.get("params"), "ok" if result.get("ok") else result.get("error", "fail"))
    if result.get("ok"):
        _cmd_fails[machine] = 0
    else:
        _e = str(result.get("error", ""))
        if "unavailable" in _e or "timed out" in _e or "timeout" in _e:
            _cmd_fails[machine] = _cmd_fails.get(machine, 0) + 1
            if _cmd_fails[machine] >= 5:  # auto half-open after 30 s
                async def _half_open(_m=machine):
                    await asyncio.sleep(30)
                    _cmd_fails[_m] = 0
                asyncio.create_task(_half_open())
    return JSONResponse({"ok": result.get("ok", False),
                         "error": result.get("error"),
                         "result": result})


@app.post("/api/upload")
async def api_upload(request: Request):
    """Browser → laptop file upload (plan §14). JSON b64 capped at 64MB."""
    user, e = require_auth(request, mutating=True)
    if e:
        return e
    body = await body_json(request)
    machine = body.get("machine") or hub.first_machine()
    name = os.path.basename(str(body.get("name", "upload.bin")))[:120]
    directory = str(body.get("path", ""))
    if not machine or not directory or not body.get("b64"):
        return err(400, "machine, path and b64 are required")
    result = await hub.command(machine, "files.write_b64",
                               {"path": os.path.join(directory, name),
                                "b64": body["b64"]},
                               timeout=90, user=user["username"])
    ok = result.get("ok")
    db.add_audit(user["username"], user["ip"], "upload:" + name,
                 {"path": directory}, "ok" if ok else result.get("error", "fail"))
    return JSONResponse({"ok": ok, "result": result})


@app.get("/api/report")
async def api_report(request: Request, format: str = "html"):
    """One-click system report download (plan §6): html | json | csv."""
    user, e = require_auth(request)
    if e:
        return e
    machine = hub.first_machine()
    if not machine:
        return err(503, "no agent connected")
    res = await hub.command(machine, "report.generate", {}, timeout=90,
                            user=user["username"])
    rep = (res.get("data") or {}).get("report") if res.get("ok") else None
    if not rep:
        return err(500, (res.get("error") or "report failed"))
    import html as _html
    stamp = time.strftime("%Y%m%d_%H%M%S")
    fmt = format.lower()
    if fmt == "json":
        body = json.dumps(rep, indent=2, default=str)
        return Response(body, media_type="application/json",
                        headers={"Content-Disposition":
                                 f'attachment; filename="HKS_report_{stamp}.json"'})
    if fmt == "csv":
        import io as _io
        buf = _io.StringIO()
        buf.write("section,key,value\n")
        hw = rep.get("hardware") or {}
        buf.write(f"system,host,{_csv(rep.get('host'))}\n")
        buf.write(f"system,os,{_csv(rep.get('os'))}\n")
        for k, v in (hw.get("cpu") or {}).items():
            if k != "per_core":
                buf.write(f"cpu,{_csv(k)},{_csv(v)}\n")
        for k, v in (hw.get("memory") or {}).items():
            buf.write(f"ram,{_csv(k)},{_csv(v)}\n")
        for dsk in hw.get("disks") or []:
            for k, v in dsk.items():
                buf.write(f"disk,{_csv(k)},{_csv(v)}\n")
        bat = hw.get("battery") or {}
        for k, v in bat.items():
            buf.write(f"battery,{_csv(k)},{_csv(v)}\n")
        for pr in rep.get("processes_top") or []:
            buf.write(f"process,{_csv(pr.get('name'))},{pr.get('pid')} mem={pr.get('mem_mb')}MB\n")
        return Response(buf.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition":
                                 f'attachment; filename="HKS_report_{stamp}.csv"'})
    # default: a clean dark HTML report
    def row(k, v):
        return f"<tr><td>{_html.escape(str(k))}</td><td><b>{_html.escape(str(v))}</b></td></tr>"
    cpu = (rep.get("hardware") or {}).get("cpu") or {}
    mem = (rep.get("hardware") or {}).get("memory") or {}
    bat = (rep.get("hardware") or {}).get("battery") or {}
    rows = "".join(row(k, v) for k, v in [("Host", rep.get("host")),
                                          ("OS", rep.get("os")),
                                          ("Generated", rep.get("generated"))])
    rows += "".join(row("CPU " + k, v) for k, v in cpu.items() if k != "per_core")
    rows += "".join(row("RAM " + k, v) for k, v in mem.items())
    rows += "".join(row("Battery " + k, v) for k, v in bat.items() if v is not None)
    procs = "".join(f"<tr><td>{p.get('pid')}</td><td>{_html.escape(str(p.get('name')))}"
                    f"</td><td>{p.get('mem_mb')} MB</td></tr>"
                    for p in (rep.get("processes_top") or [])[:20])
    html_doc = f"""<!doctype html><html><head><meta charset="utf-8"><title>HKS System Report</title>
<style>body{{background:#0a0d13;color:#e8ebf2;font:14px/1.5 'Segoe UI',sans-serif;padding:30px}}
table{{border-collapse:collapse;width:100%;max-width:900px}}td{{border-bottom:1px solid #232b3d;padding:8px 12px}}
h1{{font-size:20px}}h2{{font-size:15px;margin-top:28px;color:#4f8cff}}</style></head>
<body><h1>HKS System Report</h1><table>{rows}</table>
<h2>Top processes (RAM)</h2><table><tr><td><b>PID</b></td><td><b>Name</b></td><td><b>Memory</b></td></tr>{procs}</table>
</body></html>"""
    return Response(html_doc, media_type="text/html",
                    headers={"Content-Disposition":
                             f'attachment; filename="HKS_report_{stamp}.html"'})


def _csv(v):
    s = str(v if v is not None else "")
    return '"' + s.replace('"', '""') + '"'


@app.get("/api/security/bans")
async def api_bans_list(request: Request):
    user, e = require_auth(request)
    if e:
        return e
    return JSONResponse({"ok": True, "bans": [dict(b) for b in db.list_bans()]})


@app.post("/api/security/bans")
async def api_bans_add(request: Request):
    user, e = require_auth(request, mutating=True)
    if e:
        return e
    body = await body_json(request)
    ip = str(body.get("ip", "")).strip()
    import ipaddress as _ipa
    try:
        _ipa.ip_address(ip)
    except Exception:
        return err(400, "not a valid IP address")
    if ip == user["ip"]:
        return err(400, "that is YOUR current IP - banning it would lock you out")
    permanent = bool(body.get("permanent", True))
    try:
        minutes = int(body.get("minutes", 0))
    except (TypeError, ValueError):
        return err(400, "minutes must be a number")
    until = (time.time() + max(60, minutes * 60)) if not permanent else 1e18
    db.add_ban(ip, until, str(body.get("reason", "banned by admin")))
    db.add_audit(user["username"], user["ip"], "ban_ip", {"ip": ip}, "ok")
    return JSONResponse({"ok": True})


@app.post("/api/security/bans/delete")
async def api_bans_del(request: Request):
    user, e = require_auth(request, mutating=True)
    if e:
        return e
    body = await body_json(request)
    db.remove_ban(str(body.get("ip", "")).strip())
    db.add_audit(user["username"], user["ip"], "unban_ip", body, "ok")
    return JSONResponse({"ok": True})


@app.get("/api/sessions")
async def api_sessions(request: Request):
    """Who is connected to THIS panel right now (plan: see who connected)."""
    user, e = require_auth(request)
    if e:
        return e
    rows = []
    now = time.time()
    for r in db.list_sessions():
        rows.append({"token": r["token"][:8] + "…", "user": r["username"],
                     "ip": r["ip"], "age_min": int((now - r["created"]) // 60)})
    return JSONResponse({"ok": True, "sessions": rows})


@app.post("/api/sessions/revoke")
async def api_sessions_revoke(request: Request):
    user, e = require_auth(request, mutating=True)
    if e:
        return e
    body = await body_json(request)
    token = str(body.get("token", ""))
    for r in db.list_sessions():
        if r["token"].startswith(token.rstrip("…")):
            db.revoke_session(r["token"])
            db.add_audit(user["username"], user["ip"], "revoke_session", {"token": token}, "ok")
            await _kick_dashboards(r["token"])  # R04: revoked = disconnected NOW
            return JSONResponse({"ok": True})
    return err(404, "session not found")


@app.get("/api/agents_live")
async def api_agents_live(request: Request):
    """Every agent socket connected to the server (machine, role, since)."""
    user, e = require_auth(request)
    if e:
        return e
    out = []
    for mid, roles in hub.agents.items():
        for role, a in roles.items():
            out.append({"machine": mid, "name": a.name, "role": role,
                        "version": a.version, "connected_min":
                        int((time.time() - a.connected_at) // 60)})
    return JSONResponse({"ok": True, "agents": out})


@app.get("/api/server_logs")
async def api_server_logs(request: Request, limit: int = 120):
    """Panel server log tail (Security/Server Management -> logs)."""
    user, e = require_auth(request)
    if e:
        return e
    logf = os.environ.get("HKS_LOG_FILE") or os.path.join(_DATA_DIR, "panel.log")
    lines = []
    if os.path.exists(logf):
        with open(logf, "r", errors="replace") as f:
            lines = f.readlines()[-max(10, min(int(limit), 500)):]
    else:
        for h in logging.getLogger().handlers:
            try:
                h.flush()
            except Exception:
                pass
        lines = ["(no log file on disk - logs go to the console/service output)"]
    return JSONResponse({"ok": True, "lines": [l.rstrip("\n") for l in lines]})


@app.post("/api/server/restart")
async def api_server_restart(request: Request):
    """Restart the panel service. Requires confirm=CONFIRM (it drops every
    session incl. yours - the service comes back in ~10 s)."""
    user, e = require_auth(request, mutating=True)
    if e:
        return e
    body = await body_json(request)
    if body.get("confirm") != "CONFIRM":
        return err(400, 'send {"confirm":"CONFIRM"} to restart the panel')
    db.add_audit(user["username"], user["ip"], "server_restart", {}, "ok")

    async def _die():
        await asyncio.sleep(0.6)
        os._exit(75)  # the service manager / run loop restarts us

    asyncio.get_running_loop().create_task(_die())
    return JSONResponse({"ok": True, "note": "panel restarting in ~1 s - reload after ~10 s"})


@app.post("/api/security/test_capture")
async def api_test_capture(request: Request):
    """Fire the login-capture path RIGHT NOW so the user can verify it."""
    user, e = require_auth(request, mutating=True)
    if e:
        return e
    machine = hub.first_machine()
    if not machine:
        return JSONResponse({"ok": False, "error": "no agent connected"})
    res = await hub.command(machine, "camera.snap", {}, timeout=25, user="test-capture")
    b64 = ((res or {}).get("data") or {}).get("b64")
    if not b64:
        return JSONResponse({"ok": False,
                             "error": (res or {}).get("error") or "no image (session agent offline?)"})
    import base64 as _b64
    fn = "login_test_" + time.strftime("%Y%m%d_%H%M%S") + ".jpg"
    try:  # audit2 #1b: disk full / AV lock -> clean error, not HTTP 500
        os.makedirs(CAPTURE_DIR, exist_ok=True)
        with open(os.path.join(CAPTURE_DIR, fn), "wb") as f:
            f.write(_b64.b64decode(b64))
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"save failed: {e}"})
    db.add_capture(machine, "login", fn, json.dumps({"user": user["username"], "test": True}))
    return JSONResponse({"ok": True, "file": fn})


@app.get("/api/events")
async def events(request: Request, machine: str = "", kind: str = "", limit: int = 300):
    user, e = require_auth(request)
    if e:
        return e
    return JSONResponse({"ok": True, "events": db.list_events(machine or None, kind or None,
                                                              min(limit, 1000))})


@app.get("/api/errors")
async def errors(request: Request, machine: str = "", limit: int = 300):
    user, e = require_auth(request)
    if e:
        return e
    return JSONResponse({"ok": True, "errors": db.list_errors(machine or None, min(limit, 1000))})


@app.post("/api/errors/resolve")
async def errors_resolve(request: Request):
    user, e = require_auth(request, mutating=True)
    if e:
        return e
    body = await body_json(request)
    if body.get("all"):
        db.clear_errors(body.get("machine"))
    else:
        try:
            db.resolve_error(int(body.get("id", 0)))
        except (TypeError, ValueError):
            return err(400, "id must be a number")
    return JSONResponse({"ok": True})


@app.get("/api/audit")
async def audit(request: Request, limit: int = 300):
    user, e = require_auth(request)
    if e:
        return e
    return JSONResponse({"ok": True, "audit": db.list_audit(min(limit, 1000))})


@app.get("/api/captures")
async def captures(request: Request, machine: str = "", kind: str = "", limit: int = 200):
    user, e = require_auth(request)
    if e:
        return e
    return JSONResponse({"ok": True, "captures": db.list_captures(machine or None, kind or None,
                                                                  min(limit, 500))})


@app.get("/api/storage/summary")
async def storage_summary(request: Request):
    user, e = require_auth(request)
    if e:
        return e
    if user.get("role") != "admin":
        return err(403, "admin only")
    counts = {"events": db.one("SELECT COUNT(*) AS n FROM events")["n"],
              "errors": db.one("SELECT COUNT(*) AS n FROM errors")["n"],
              "audit": db.one("SELECT COUNT(*) AS n FROM audit")["n"],
              "captures": db.one("SELECT COUNT(*) AS n FROM captures")["n"]}
    capture_bytes = 0
    for row in db.query("SELECT file FROM captures"):
        name = os.path.basename(str(row.get("file") or ""))
        path = os.path.join(CAPTURE_DIR, name)
        try:
            if os.path.commonpath([os.path.realpath(CAPTURE_DIR), os.path.realpath(path)]) == os.path.realpath(CAPTURE_DIR):
                capture_bytes += os.path.getsize(path)
        except (OSError, ValueError):
            pass
    log_path = os.path.join(_DATA_DIR, "panel.log")
    try:
        log_bytes = os.path.getsize(log_path)
    except OSError:
        log_bytes = 0
    return JSONResponse({"ok": True, "counts": counts,
                         "capture_bytes": capture_bytes, "panel_log_bytes": log_bytes})


@app.post("/api/storage/cleanup")
async def storage_cleanup(request: Request):
    """Explicit, admin-only cleanup of selected HKS panel data categories."""
    user, e = require_auth(request, mutating=True)
    if e:
        return e
    if user.get("role") != "admin":
        return err(403, "admin only")
    body = await body_json(request)
    category = str(body.get("category") or "")
    allowed = {"events", "errors", "audit", "captures", "panel_log"}
    if category not in allowed or body.get("confirm") != "DELETE " + category.upper():
        return err(400, "select a listed category and enter its exact confirmation phrase")
    removed_files = 0
    failed_files = 0
    if category == "captures":
        base = os.path.realpath(CAPTURE_DIR)
        for row in db.query("SELECT file FROM captures"):
            path = os.path.join(CAPTURE_DIR, os.path.basename(str(row.get("file") or "")))
            try:
                real = os.path.realpath(path)
                if os.path.commonpath([base, real]) != base:
                    failed_files += 1
                    continue
                if os.path.isfile(real):
                    os.remove(real)
                    removed_files += 1
            except (OSError, ValueError):
                failed_files += 1
        db.exec("DELETE FROM captures")
    elif category == "panel_log":
        path = os.path.realpath(os.path.join(_DATA_DIR, "panel.log"))
        if os.path.commonpath([os.path.realpath(_DATA_DIR), path]) != os.path.realpath(_DATA_DIR):
            return err(400, "invalid log path")
        if os.path.isfile(path):
            try:
                with open(path, "w", encoding="utf-8"):
                    pass
                removed_files = 1
            except OSError as ex:
                return err(409, "could not clear the active panel log: " + str(ex))
    else:
        db.exec("DELETE FROM " + category)
    db.add_audit(user["username"], user.get("ip", ""), "storage_cleanup",
                 {"category": category, "files_removed": removed_files}, "ok")
    return JSONResponse({"ok": True, "category": category,
                         "files_removed": removed_files, "files_failed": failed_files})


@app.get("/api/capture/{cid}")
async def capture_file(request: Request, cid: int):
    user, e = require_auth(request)
    if e:
        return e
    rows = [r for r in db.list_captures(limit=1000) if r["id"] == cid]
    if not rows:
        return err(404, "not found")
    fname = db.one("SELECT file FROM captures WHERE id=?", (cid,))
    if not fname:
        return err(404, "not found")
    path = os.path.join(CAPTURE_DIR, os.path.basename(fname["file"]))
    if not os.path.exists(path):
        return err(404, "file missing")
    return FileResponse(path, media_type="image/jpeg")


@app.get("/api/token")
async def token(request: Request):
    user, e = require_auth(request)
    if e:
        return e
    if user["role"] != "admin":
        return err(403, "admin only")
    return JSONResponse({"ok": True, "agent_token": AGENT_TOKEN})


@app.post("/api/download/cleanup")
async def download_cleanup(request: Request):
    """round 61 (audit4 #2): the destructive half of the ZIP flow, now its
    own CSRF-protected POST - a GET can no longer delete anything."""
    user, e = require_auth(request, mutating=True)
    if e:
        return e
    body = await body_json(request)
    machine = str(body.get("machine", "") or hub.first_machine())
    path = str(body.get("path", ""))
    if not machine or not path:
        return err(400, "machine and path required")
    r = await hub.command(machine, "files.delete", {"path": path},
                          timeout=30, user=user["username"])
    return JSONResponse(r if r.get("ok") else {"ok": False, "error": r.get("error", "delete failed")})


@app.get("/api/download")
async def download(request: Request, machine: str = "", path: str = "", del_after: int = 0):
    """Streams a remote file through the agent in base64 chunks.
    round 61 (audit4 #2): GET is READ-ONLY - del_after is IGNORED here (a
    plain link/navigation must never be able to delete a remote file).
    Deletion is an explicit POST /api/download/cleanup with CSRF."""
    del_after = 0  # deliberately discarded - see docstring
    user, e = require_auth(request)
    if e:
        return e
    machine = machine or hub.first_machine()
    if not machine or not path:
        return err(400, "machine and path required")
    st = await hub.command(machine, "file.stat", {"path": path}, timeout=20, user=user["username"])
    if not st.get("ok"):
        return err(404, st.get("error", "stat failed"))
    size = (st.get("data") or {}).get("size", 0)

    # round 65 (audit7 #37): browsers need byte ranges for <video>/<audio>
    # seek + playback past the first buffer. Serve 206 + Content-Range when
    # the client asks; the agent already reads from any offset.
    rng = request.headers.get("range", "")
    start, end = 0, size - 1
    partial = False
    if rng.startswith("bytes=") and size > 0:
        try:
            spec = rng[6:].split(",")[0].strip()
            if spec.startswith("-"):
                n = int(spec[1:])
                start = max(0, size - n)
            else:
                a, _, b = spec.partition("-")
                start = int(a)
                end = int(b) if b else size - 1
            if start >= size or start > end:
                return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
            end = min(end, size - 1)
            partial = True
        except ValueError:
            start, end, partial = 0, size - 1, False

    async def gen():
        off = start
        chunk = 768 * 1024
        import base64
        try:
            while off <= end:
                r = await hub.command(machine, "file.read_chunk",
                                      {"path": path, "offset": off,
                                       "size": min(chunk, end - off + 1)},
                                      timeout=30, user=user["username"])
                if not r.get("ok"):
                    break
                b = base64.b64decode((r.get("data") or {}).get("b64", ""))
                if not b:
                    break
                if off + len(b) > end + 1:
                    b = b[:end + 1 - off]
                yield b
                off += len(b)
            if del_after and off >= size:
                # transfer complete — schedule the remote cleanup right away
                asyncio.ensure_future(hub.command(machine, "files.delete",
                                                  {"path": path}, timeout=30,
                                                  user=user["username"]))
        except GeneratorExit:
            raise

    name = os.path.basename(path) or "download.bin"
    # NO Content-Length: the bytes come through a live agent pipe - if the
    # agent dies mid-file fewer bytes arrive and a fixed length would hang
    # the browser (audit3). Chunked streaming handles it.
    headers = {"Content-Disposition": f'attachment; filename="{name}"',
               "Accept-Ranges": "bytes"}
    if partial:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(gen(), status_code=206 if partial else 200,
                             media_type="application/octet-stream", headers=headers)


# --------------------------------------------------------------------------
# WebSocket: agents
# --------------------------------------------------------------------------
@app.websocket("/ws/agent")
async def ws_agent(ws: WebSocket):
    await ws.accept()
    if not _origin_ok(ws):  # R02
        await ws.close(code=4003)
        return
    hello = None
    try:
        hello = json.loads(await asyncio.wait_for(ws.receive_text(), 15))
    except Exception:
        await ws.close(code=4000)
        return
    if hello.get("type") != "hello" or not security.constant_eq(
            str(hello.get("token", "")), AGENT_TOKEN):
        await ws.send_text(json.dumps({"type": "error", "code": "auth"}))
        await ws.close(code=4002)
        return
    ip = ws.client.host if ws.client else "?"
    ok, _ = security.ws_limiter.check(ip)
    if not ok:
        await ws.close(code=4029)
        return
    role = hello.get("role", "svc")
    if role not in ("svc", "session"):
        role = "svc"
    mid = str(hello.get("machine_id", "unknown"))[:64]
    lock = os.environ.get("HKS_MACHINE_LOCK", "").strip()
    if lock and mid != lock:
        # R03-lite (opt-in): with HKS_MACHINE_LOCK set to your machine id,
        # a stolen token cannot present a DIFFERENT identity. Default off
        # so fresh installs / test rigs keep working hassle-free.
        await ws.send_text(json.dumps({"type": "error", "code": "machine_lock"}))
        await ws.close(code=4003)
        return
    conn = AgentConn(ws, mid, role,
                     str(hello.get("name", "laptop"))[:64],
                     str(hello.get("version", "?"))[:16], hello.get("caps", []))
    conn.test_double = bool(hello.get("test_double"))  # round 66: declared test doubles skip caps validation
    await hub.attach(conn)
    try:
        await conn.send_json({"type": "hello_ok", "server_time": time.time()})
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            if (data := msg.get("bytes")) is not None:
                if not data:
                    continue
                ch = data[0]
                conn.last_frame = time.time()
                await hub.push_frame(conn.machine_id, ch, data[1:])
            elif (text := msg.get("text")) is not None:
                try:
                    obj = json.loads(text)
                except Exception:
                    continue
                t = obj.get("type")
                if t == "result":
                    fut = conn.pending.pop(str(obj.get("id")), None)
                    if fut and not fut.done():
                        fut.set_result({"ok": obj.get("ok", False),
                                        "data": obj.get("data"),
                                        "error": obj.get("error")})
                elif t == "telemetry":
                    obj["machine_id"] = conn.machine_id
                    hub.latest_telemetry[conn.machine_id] = obj
                    asyncio.create_task(autosvc.on_telemetry(conn.machine_id, obj))  # round 68 (F33)
                    db.insert_telemetry(obj)
                    await hub.broadcast_dash({"type": "telemetry", **obj})
                elif t == "event":
                    kind = str(obj.get("kind", "misc"))[:40]
                    db.add_event(conn.machine_id, kind, obj.get("data"))
                    await hub.broadcast_dash({"type": "event", "machine_id": conn.machine_id,
                                              "kind": kind, "data": obj.get("data"),
                                              "ts": time.time()})
                elif t == "capture":
                    await save_capture(conn, obj)
                elif t == "error":
                    db.add_error(conn.machine_id, str(obj.get("module", "?"))[:60],
                                 str(obj.get("message", ""))[:500],
                                 str(obj.get("detail", ""))[:4000])
                    await hub.broadcast_dash({"type": "agent_error", "machine_id": conn.machine_id,
                                              "module": obj.get("module"),
                                              "message": obj.get("message")})
                elif t == "pong":
                    pass
    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            db.add_error(conn.machine_id, "ws", f"agent conn error: {e}")
        except Exception:
            pass
    finally:
        await hub.detach(conn)


async def save_capture(conn: AgentConn, obj: dict):
    import base64
    try:
        kind = str(obj.get("kind", "cam"))[:32]
        raw = base64.b64decode(obj.get("b64", ""))
        if not raw or len(raw) > 12 * 1024 * 1024:
            return
        cid = secrets.token_hex(8)
        fname = f"{conn.machine_id}_{cid}_{kind}.jpg"
        with open(os.path.join(CAPTURE_DIR, fname), "wb") as f:
            f.write(raw)
        db.add_capture(conn.machine_id, kind, fname, json.dumps(obj.get("meta", {}), default=str))
        await hub.broadcast_dash({"type": "capture", "machine_id": conn.machine_id, "kind": kind,
                                  "meta": obj.get("meta", {})})
        db.add_event(conn.machine_id, "capture_" + kind, obj.get("meta", {}))
    except Exception as e:
        db.add_error(conn.machine_id, "capture", str(e))


# --------------------------------------------------------------------------
# WebSocket: dashboard live channel (screen frames + events + input relay)
# --------------------------------------------------------------------------
@app.websocket("/ws/dash")
async def ws_dash(ws: WebSocket):
    await ws.accept()
    if not _origin_ok(ws):  # R02: cross-site WS hijack stops here
        await ws.close(code=4003)
        return
    user = get_session_user(ws)
    # first text message selects machine (and may carry the session token
    # when the browser blocks cookies in this context)
    machine = None
    try:
        sel = json.loads(await asyncio.wait_for(ws.receive_text(), 10))
        if not user and sel.get("token"):
            tok = str(sel.get("token"))
            row = db.get_session(tok)
            if row:
                u = db.get_user_by_id(row["user_id"])
                if u:
                    user = {"username": u["username"], "role": u["role"], "token": tok,
                            "csrf": row["csrf"], "ip": client_ip(ws)}
        machine = sel.get("machine") or hub.first_machine()
    except Exception:
        try:  # the socket may ALREADY be gone (refresh mid-select, Acer field
            await ws.close()  # log 2026-09) - never let the cleanup raise
        except Exception:
            pass
        return
    if not user:
        try:
            await ws.close(code=4401)
        except Exception:
            pass
        return
    is_admin = (user.get("role") == "admin")  # R01: SAME policy as REST
    d = DashboardConn(ws, user["username"], machine, tok=user.get("token", ""))
    hub.dashboards.add(d)
    try:
        await ws.send_text(json.dumps({"type": "hello_dash", "machine": machine,
                                       "telemetry": hub.latest_telemetry.get(machine)}))
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            if (text := msg.get("text")) is not None:
                try:
                    obj = json.loads(text)
                except Exception:
                    continue
                t = obj.get("type")
                if t in ("input", "cmd") and not is_admin:
                    # R01: an observer is read-only on EVERY transport -
                    # this is the same rule require_auth(mutating=True) makes
                    # for REST, now enforced on the WebSocket too.
                    try:
                        await ws.send_text(json.dumps(
                            {"type": "error", "module": "auth",
                             "message": "observer is read-only - admin role needed for control"}))
                    except Exception:
                        pass
                    continue
                if t == "input":
                    agent = hub.get_agent(machine, "session")
                    if agent:
                        try:
                            await send_owned(agent, agent.send_json(
                                {"type": "cmd", "id": secrets.token_hex(6),
                                 "action": "input.apply", "params": obj.get("ev", {}),
                                 "user": user["username"]}))
                        except Exception:
                            pass
                elif t == "cmd":
                    res = await hub.command(machine, str(obj.get("action", "")),
                                            obj.get("params") or {}, timeout=60,
                                            user=user["username"])
                    try:
                        await ws.send_text(json.dumps({"type": "result", "id": obj.get("id"),
                                                       **res}, default=str))
                    except Exception:
                        pass
                elif t == "sub":
                    machine = str(obj.get("machine", machine))[:64]
                    d.machine_id = machine
            elif (data := msg.get("bytes")) is not None:
                # channel 4 = admin voice → laptop speakers (admins only, R01)
                if data and data[0] == 4 and not is_admin:
                    continue
                if data and data[0] == 4:
                    agent = hub.get_agent(machine, "session")
                    if agent:
                        try:
                            await send_owned(agent, agent.send_binary(data))
                        except Exception:
                            pass
    finally:
        hub.dashboards.discard(d)


# static assets
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# Bump on EVERY rebuild — the login page and CHECK_STATUS display it so a
# stale install is instantly visible in the field ("still error" usually
# meant the old build was still running).
HKS_BUILD_VERSION = "7.2.99"


@app.get("/api/version")
async def api_version():
    ports = {"main": main_bind_port, "http_companion": http_bind_port}
    # live tunnel address (phone-from-anywhere door), published by
    # tunnel_url_watch.py next to the data dir
    tunnel = ""
    try:
        tf = os.path.join(os.path.dirname(os.path.dirname(BASE_DIR)),
                          "data", "tunnel_url.txt")
        if os.path.exists(tf):
            tunnel = open(tf).read().strip()
    except Exception:
        pass
    return JSONResponse({"ok": True, "version": HKS_BUILD_VERSION,
                         "tunnel_url": tunnel, **ports})


main_bind_port = 0
http_bind_port = 0


def _port_owner(pid_hint=True, port=0):
    """Best-effort 'who owns this port' for the field message."""
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"],
                             capture_output=True, text=True, timeout=10).stdout
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[3] == "LISTENING" and parts[1].endswith(f":{port}"):
                return parts[4]
    except Exception:
        pass
    return "?"


def _wait_ports_free(host, ports, timeout_s=20):
    """Block until all ports are bindable; return the list still stuck."""
    import socket as _s
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        stuck = []
        for pt in ports:
            if not pt:
                continue
            try:
                t = _s.socket(_s.AF_INET, _s.SOCK_STREAM)
                if os.name == "nt":
                    # round 56 (field audit #3/#7): SO_REUSEADDR on Windows
                    # binds EVEN WHEN a live listener owns the port - the
                    # old preflight said "free" while 5757 was occupied.
                    # EXCLUSIVEADDRUSE makes "bindable" mean "really free".
                    t.setsockopt(_s.SOL_SOCKET, _s.SO_EXCLUSIVEADDRUSE, 1)
                else:
                    t.setsockopt(_s.SOL_SOCKET, _s.SO_REUSEADDR, 1)
                t.bind((host, pt))
                t.close()
            except OSError:
                stuck.append(pt)
        if not stuck:
            return []
        time.sleep(1)
    return stuck


def main():
    global main_bind_port, http_bind_port
    ap = argparse.ArgumentParser(description="HKS Admin Panel server")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--ssl-cert", default=os.environ.get("HKS_SSL_CERT", ""))
    ap.add_argument("--ssl-key", default=os.environ.get("HKS_SSL_KEY", ""))
    ap.add_argument("--http-port", type=int, default=0,
                    help="when serving HTTPS, ALSO listen plain-HTTP on this "
                         "port (phone-friendly: no certificate warnings). "
                         "0 = disabled.")
    args = ap.parse_args()
    ssl = None
    if args.ssl_cert and args.ssl_key:
        ssl = {"cert": args.ssl_cert, "key": args.ssl_key}
        print(f"[HKS] HTTPS enabled: {args.ssl_cert}")
    else:
        print("[HKS] WARNING: running WITHOUT TLS. Use --ssl-cert/--ssl-key for production.")
    # round 60 fix (owner's Acer doctor exposed it): the round-56 listener
    # rewrite DROPPED these assignments - /api/version reported main:0 /
    # http_companion:0, silencing the doctor's LAN [ANSWERS] port list and
    # the phone-door hint in the UI.
    main_bind_port = args.port
    http_bind_port = args.http_port if (ssl and args.http_port) else 0
    print(f"[HKS] Admin panel:  http{'s' if ssl else ''}://{args.host}:{args.port}/")
    print(f"[HKS] Build version: {HKS_BUILD_VERSION}")

    # ---- bind preflight with self-defense -------------------------------
    # Field: an old panel that survived an upgrade keeps the port; uvicorn
    # then dies and NOTHING serves (not even the companion). Diagnose and
    # auto-heal BEFORE starting listeners.
    want = [args.port, args.http_port if (ssl and args.http_port) else 0]
    stuck = _wait_ports_free(args.host, want, timeout_s=8)
    if stuck:
        print(f"[HKS] ports busy: {stuck} — old HKS processes still running, stopping them...")
        try:
            hits = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "$me=$PID; Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $me "
                 "-and $_.CommandLine -match 'server\\.main|agent\\.(main|session_main)' } | "
                 "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; "
                 "'killed old'"],
                capture_output=True, text=True, timeout=60)
            print("[HKS]", (hits.stdout or "").strip() or "cleanup attempted")
        except Exception as e:
            print("[HKS] cleanup attempt failed:", e)
        stuck = _wait_ports_free(args.host, want, timeout_s=20)
        if stuck:
            owner = _port_owner(port=stuck[0])
            print(f"[HKS] !! PORT {stuck[0]} STILL BUSY (PID {owner}) !!")
            print("[HKS] !! REBOOT THE LAPTOP once, then run INSTALL_HKS.bat again !!")

    import threading
    import uvicorn as _uv
    # round 56 (field audit #1/#8): the MAIN listener starts FIRST and its
    # startup is VERIFIED before the no-cert companion is allowed to make
    # the process "look alive". A dead main door can never hide again.
    main_cfg = _uv.Config(app, host=args.host, port=args.port,
                          ssl_certfile=ssl and ssl["cert"],
                          ssl_keyfile=ssl and ssl["key"],
                          ws_max_size=16 * 1024 * 1024, log_level="info")
    main_srv = _uv.Server(main_cfg)
    main_thr = threading.Thread(target=main_srv.run, daemon=True,
                                name="hks-main")
    main_thr.start()
    _deadline = time.time() + 25
    while time.time() < _deadline and not main_srv.started and main_thr.is_alive():
        time.sleep(0.2)
    if not main_srv.started:
        print(f"[HKS] !! MAIN PORT {args.port} FAILED to start - see the error above !!", flush=True)
        if ssl and args.http_port:
            print(f"[HKS] The NO-CERT door http://<laptop-ip>:{args.http_port} will still open - use it from phones.", flush=True)
        print("[HKS] FIX: reboot, run INSTALL_HKS.bat as Administrator,", flush=True)
        print("[HKS] answer N at 'Use HTTPS?' for one plain door (easiest).", flush=True)
        print("[HKS] Agents need this MAIN port - control stays offline until repaired.", flush=True)
    else:
        print(f"[HKS] MAIN door verified on port {args.port}", flush=True)
    if ssl and args.http_port:
        http_cfg = _uv.Config(app, host=args.host, port=args.http_port,
                              ws_max_size=16 * 1024 * 1024, log_level="warning")
        http_server = _uv.Server(http_cfg)
        threading.Thread(target=http_server.run, daemon=True,
                         name="hks-http-companion").start()
        print(f"[HKS] Phone-friendly: http://{args.host}:{args.http_port}/  (no cert warnings)")
    print(f"[HKS] Agents connect to: wss://YOUR-SERVER:{args.port}/ws/agent")
    # process stays alive while the main listener lives; if it dies mid-run
    # the process exits and start_panel.bat restarts everything loudly.
    while main_thr.is_alive():
        main_thr.join(timeout=5)
    if not main_srv.started:
        # main never came up: keep the phone door (companion) serving
        print("[HKS] main listener is down - keeping the phone door alive;", flush=True)
        print("[HKS] run INSTALL_HKS.bat as Administrator to repair the main port.", flush=True)
        while True:
            time.sleep(3600)


if __name__ == "__main__":
    main()
