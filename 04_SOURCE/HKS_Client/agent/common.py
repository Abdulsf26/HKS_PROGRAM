"""HKS Agent - shared context: config, logging, error reporting, telemetry."""
import base64
import json
import os
import sys
import threading
import time
import traceback
import uuid


def app_dir():
    return os.path.dirname(os.path.abspath(__file__))


def root_dir():
    """Program root (…/HKS_Client) — where config.json, data/ and the
    SELF_DESTRUCT kill-switch marker live."""
    return os.path.dirname(app_dir())


def data_dir():
    d = os.environ.get("HKS_DATA_DIR") or os.path.join(root_dir(), "data")
    os.makedirs(d, exist_ok=True)
    return d


def scripts_dir():
    d = os.path.join(root_dir(), "scripts")
    os.makedirs(d, exist_ok=True)
    return d


def kill_switch_active():
    """If SELF_DESTRUCT marker exists (created by remote terminate), the agent
    must exit and stay out."""
    return os.path.exists(os.path.join(root_dir(), "SELF_DESTRUCT"))


DEFAULT_CONFIG = {
    "server_url": "wss://YOUR-SERVER-IP:8000/ws/agent",
    "token": "PASTE_AGENT_TOKEN_HERE",
    "machine_id": "",
    "name": "HKS-Laptop",
    "fps": 8,
    "quality": 60,
    "max_width": 1280,
    "telemetry_sec": 5,
    "camera_index": 0,
    "cert_pin": "",
    "sqlite_defaults": {"path": "C:\\HKS_PROGRAM\\data\\app.db"},
    "mysql_defaults": {"host": "", "port": 3306, "user": "", "password": "", "database": ""},
}


class Config(dict):
    def __init__(self, path=None):
        super().__init__(DEFAULT_CONFIG)
        self.path = path or os.path.join(os.path.dirname(app_dir()), "config.json")
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self.update(json.load(f))
        except Exception:
            pass
        if not self.get("machine_id"):
            self["machine_id"] = "HKS-" + uuid.uuid4().hex[:12].upper()
            self.save()

    def save(self):
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self, f, indent=2)
            os.replace(tmp, self.path)  # round 64 (audit6 #25): atomic write
        except Exception as e:
            print("[config] save failed:", e, file=sys.stderr)


class RingBuffer(list):
    """Bounded offline queue — events made while disconnected, flushed on reconnect."""

    def __init__(self, maxlen=300):
        super().__init__()
        self.maxlen = maxlen

    def push(self, item):
        self.append(item)
        if len(self) > self.maxlen:
            del self[: len(self) - self.maxlen]


def rotating_log(path, max_bytes=2_000_000, backups=2):
    import logging
    from logging.handlers import RotatingFileHandler
    logger = logging.getLogger("hks")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        try:
            h = RotatingFileHandler(path, maxBytes=max_bytes, backupCount=backups,
                                    encoding="utf-8")
        except Exception:
            h = logging.StreamHandler()
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(h)
    return logger


class AgentContext:
    """Everything a module could need. One per running agent process."""

    def __init__(self, role, config_path=None):
        self.role = role  # "svc" | "session"
        self.cfg = Config(config_path)
        self.log = rotating_log(os.path.join(data_dir(), f"hks_{role}.log"))
        self.conn = None                 # set by connection.py
        self.actions = {}                # action -> callable(ctx, params) -> dict
        self.offline = RingBuffer()      # queued json messages while offline
        self.pending_stream_stop = threading.Event()
        self.stats = {"started": time.time(), "cmds": 0, "errors": 0, "latency_ms": None}
        self.binary_handlers = {}   # channel -> callable(bytes) for live streams
        self.disconnect_hooks = []  # callables run when the link drops (stop streams)
        self._send_lock = threading.Lock()

    def on_binary(self, channel, handler):
        """Register a handler for inbound binary frames: handler(bytes).
        Channel 4 = admin voice -> laptop speakers."""
        self.binary_handlers[int(channel)] = handler

    def handle_disconnect(self):
        """Connection lost: modules stop their streams so nothing runs 24/7."""
        for h in list(self.disconnect_hooks):
            try:
                h()
            except Exception:
                pass

    # ---------------- outbound ----------------
    def send_json(self, obj: dict):
        """Queue-safe send; buffers when disconnected."""
        with self._send_lock:
            if self.conn is None or not getattr(self.conn, "alive", False):
                if obj.get("type") in ("event", "error", "capture"):
                    self.offline.push(obj)
                elif obj.get("type") == "result":
                    # the server already gave up on this command - buffering
                    # it would replay a STALE answer after reconnect (audit4)
                    self.log.warning("dropping result id=%s (offline)", obj.get("id"))
                return False
        self.conn.enqueue(obj)
        return True

    def send_event(self, kind, data=None):
        self.send_json({"type": "event", "kind": kind, "data": data or {}})
        self.log.info("event %s %s", kind, json.dumps(data or {}, default=str)[:300])

    def send_error(self, module, message, detail=""):
        self.stats["errors"] += 1
        self.log.error("[%s] %s | %s", module, message, detail[:300])
        self.send_json({"type": "error", "module": module, "message": str(message)[:500],
                        "detail": str(detail)[:4000]})

    def send_capture(self, kind, jpeg_bytes: bytes, meta=None):
        self.send_json({"type": "capture", "kind": kind, "meta": meta or {},
                        "b64": base64.b64encode(jpeg_bytes).decode()})

    def send_binary(self, channel: int, payload: bytes):
        if self.conn is not None and getattr(self.conn, "alive", False):
            self.conn.enqueue_binary(bytes([channel]) + payload)
            return True
        return False

    def send_telemetry(self, snap: dict):
        self.send_json({"type": "telemetry", **snap})

    # ---------------- module registry ----------------
    def register(self, name, module):
        """module must expose register(ctx) -> dict[action, handler]."""
        try:
            handlers = module.register(self) or {}
            self.actions.update(handlers)
            self.log.info("module loaded: %s (%d actions)", name, len(handlers))
            return True
        except Exception as e:
            self.send_error(f"module:{name}", f"failed to load module: {e}",
                            traceback.format_exc())
            return False


def safe(fn):
    """Decorator for action handlers: never raises; reports errors to the server."""
    def wrapper(ctx, params):
        try:
            return {"ok": True, "data": fn(ctx, params)}
        except Exception as e:
            ctx.send_error(fn.__module__.rsplit(".", 1)[-1],
                           f"{fn.__name__}: {e}", traceback.format_exc())
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    wrapper.__name__ = fn.__name__
    return wrapper


def run_ps(command, timeout=30):
    """Run a PowerShell snippet, return stdout+stderr text."""
    import subprocess
    p = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                       capture_output=True, text=True, timeout=timeout,
                       creationflags=getattr(__import__("subprocess"), "CREATE_NO_WINDOW", 0))
    return (p.stdout or "") + (("\n[stderr] " + p.stderr) if p.stderr.strip() else "")


def run_cmd_line(command, timeout=30):
    import subprocess
    p = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=timeout,
                       creationflags=getattr(__import__("subprocess"), "CREATE_NO_WINDOW", 0))
    return (p.stdout or "") + (("\n[stderr] " + p.stderr) if p.stderr.strip() else "")
