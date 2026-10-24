"""Resource janitor — keeps the laptop clean for 24/7 operation.

Hourly sweep of the data dir:
  * exported ZIPs: keep newest 3, delete anything older than 24 h
  * script run logs: keep newest 30, delete anything older than 7 days
  * media index caches: delete older than 6 h (they rebuild on demand)
Also exposes disconnect hooks so live streams never outlive the dashboard.
"""
import os
import time

KEEP_ZIPS = 3
ZIP_TTL = 24 * 3600
LOG_KEEP = 30
LOG_TTL = 7 * 86400
INDEX_TTL = 6 * 3600


def _mtime(p):
    try:
        return os.path.getmtime(p)
    except OSError:
        return 0.0


def rotate_console_logs(d, caps=None, keep=3):
    """round 68 (F18): console logs written by bat redirects had NO cap.
    Rotate anything over its size cap: current -> .1 -> .2 -> gone."""
    caps = caps or {"agent_console.log": 50 * 1024 * 1024,
                    "session_console.log": 50 * 1024 * 1024,
                    "panel_console.log": 50 * 1024 * 1024,
                    "tunnel_console.log": 20 * 1024 * 1024}
    for name, cap in caps.items():
        path = os.path.join(d, name)
        try:
            if not os.path.exists(path) or os.path.getsize(path) < cap:
                continue
            for i in range(keep - 1, 0, -1):
                src, dst = f"{path}.{i}", f"{path}.{i + 1}"
                if os.path.exists(dst):
                    os.remove(dst)
                if os.path.exists(src):
                    os.rename(src, dst)
            os.rename(path, f"{path}.1")
        except Exception:
            pass


def cleanup_data_dir(root=None):
    from .common import data_dir
    d = root or data_dir()
    now = time.time()
    removed = 0
    try:
        rotate_console_logs(d)  # round 68 (F18)
    except Exception:
        pass
    if not os.path.isdir(d):
        return 0
    # 1) exported zips
    zips = sorted([os.path.join(d, f) for f in os.listdir(d) if f.endswith(".zip")],
                  key=_mtime, reverse=True)
    for i, p in enumerate(zips):
        if i >= KEEP_ZIPS or now - _mtime(p) > ZIP_TTL:
            try:
                os.remove(p)
                removed += 1
            except OSError:
                pass
    # 2) script run logs
    runs = os.path.join(d, "script_runs")
    if os.path.isdir(runs):
        logs = sorted([os.path.join(runs, f) for f in os.listdir(runs)],
                      key=_mtime, reverse=True)
        for i, p in enumerate(logs):
            if i >= LOG_KEEP or now - _mtime(p) > LOG_TTL:
                try:
                    os.remove(p)
                    removed += 1
                except OSError:
                    pass
    # 3) stale media indexes
    for f in os.listdir(d):
        if f.startswith("media_index_"):
            p = os.path.join(d, f)
            if now - _mtime(p) > INDEX_TTL:
                try:
                    os.remove(p)
                    removed += 1
                except OSError:
                    pass
    return removed


def start(ctx):
    def loop():
        while True:
            try:
                from .common import kill_switch_active
                if kill_switch_active():
                    return
                n = cleanup_data_dir()
                if n:
                    ctx.log.info("janitor removed %d stale files", n)
            except Exception as e:
                ctx.log.error("janitor: %s", e)
            time.sleep(3600)

    threading = __import__("threading")
    threading.Thread(target=loop, daemon=True, name="hks-janitor").start()
