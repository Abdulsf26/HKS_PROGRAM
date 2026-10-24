"""Task scheduler: agent-internal cron + Windows schtasks bridge +
script/code upload (py/ps1/bat/cmd) executed at a chosen time or delay."""
import base64
import json
import os
import re
import subprocess
import threading
import time

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
ALLOWED_EXTS = {"py": "script", "ps1": "script", "bat": "script", "cmd": "script", "txt": "message"}
MAX_SCRIPT_BYTES = 512 * 1024


def _tasks_file(ctx):
    from ..common import data_dir
    return os.path.join(data_dir(), "agent_tasks.json")


def _load(ctx):
    try:
        with open(_tasks_file(ctx), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save(ctx, tasks):
    os.makedirs(os.path.dirname(_tasks_file(ctx)), exist_ok=True)
    with open(_tasks_file(ctx), "w", encoding="utf-8") as f:
        json.dump(tasks, f, indent=1)


def _safe_name(name):
    return re.sub(r"[^A-Za-z0-9_\-]", "_", str(name or "script"))[:60] or "script"


def _run_script(ctx, task):
    """Run an uploaded/registered script, capture output to a log file."""
    from ..common import data_dir
    path = task.get("path")
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    if ext == "py":
        argv = ["python", path]
    elif ext == "ps1":
        argv = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path]
    else:
        argv = ["cmd", "/c", path]
    ts = time.strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(data_dir(), "script_runs", f"{_safe_name(task.get('name'))}_{ts}.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    code = None

    def work():
        nonlocal code
        try:
            p = subprocess.run(argv, capture_output=True, text=True, errors="replace",
                               timeout=3600, creationflags=CREATE_NO_WINDOW)
            code = p.returncode
            with open(log_path, "w", encoding="utf-8", errors="replace") as f:
                f.write(f"$ {task.get('name')} @ {ts}\nexit={p.returncode}\n\n"
                        f"{(p.stdout or '')[:100000]}\n{('[stderr] ' + p.stderr)[:20000]}")
        except subprocess.TimeoutExpired:
            code = -1
        except Exception as e:
            code = -2
            ctx.send_error("tasks", f"script {task.get('name')}: {e}")
        ctx.send_event("task_ran", {"name": task.get("name"), "type": "script",
                                    "exit": code, "log": log_path})

    threading.Thread(target=work, daemon=True, name="hks-script").start()
    return {"started": True, "log": log_path}


def _execute(ctx, task):
    t, v = task.get("type"), task.get("value", "")
    try:
        if t == "script":
            _run_script(ctx, task)
            return
        if t == "command":
            subprocess.Popen(["powershell", "-NoProfile", "-Command", v],
                             creationflags=CREATE_NO_WINDOW)
        elif t == "cmd":
            subprocess.Popen(["cmd", "/c", v], creationflags=CREATE_NO_WINDOW)
        elif t in ("url", "media"):
            os.startfile(v)
        elif t == "message":
            try:
                import winotify
                winotify.Notification(title="HKS scheduled task", msg=v).show()
            except ImportError:  # audit4 #T3: honest fallback toast
                subprocess.Popen(["msg", "*", "/TIME:20", "HKS: " + str(v)[:200]],
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        ctx.send_event("task_ran", {"name": task.get("name"), "type": t})
    except Exception as e:
        ctx.send_error("tasks", f"task {task.get('name')}: {e}")


def register(ctx):
    # ---------------- cron thread ----------------
    def cron_loop():
        if hasattr(ctx, "_cron_started"):
            return
        ctx._cron_started = True
        while True:
            try:
                from ..common import kill_switch_active
                if kill_switch_active():
                    return
                now = time.time()
                tasks = _load(ctx)
                changed = False
                for t in tasks:
                    if t.get("done"):
                        continue
                    w = t.get("when")
                    if w == "interval":
                        if now >= t.get("_next", 0):
                            _execute(ctx, t)
                            t["_next"] = now + max(60, int(t.get("interval_min", 30)) * 60)
                            changed = True
                    elif w == "daily":
                        hh, mm = (t.get("at") or "00:00").split(":")[:2]
                        lt = time.localtime()
                        target = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday,
                                              int(hh), int(mm), 0, 0, 0, -1))
                        if now >= t.get("_next", target) or now >= target:
                            if now - target < 3600:
                                _execute(ctx, t)
                            t["_next"] = target + 86400
                            changed = True
                    elif w == "delay":
                        if now >= t.get("_ts", 0):
                            _execute(ctx, t)
                            t["done"] = True
                            changed = True
                    elif w == "once":
                        if now >= t.get("_ts", 0):
                            _execute(ctx, t)
                            t["done"] = True
                            changed = True
                if changed:
                    _save(ctx, [x for x in tasks if not x.get("done")])  # audit2 #2: save what we mutated
            except Exception as e:
                ctx.send_error("tasks", f"cron: {e}")
            time.sleep(15)

    threading.Thread(target=cron_loop, daemon=True, name="hks-cron").start()

    # ---------------- actions ----------------
    def agent_list(c, p):
        return {"items": [{k: v for k, v in t.items() if not k.startswith("_")}
                          for t in _load(c)]}

    def agent_add(c, p):
        tasks = [x for x in _load(c) if not x.get("done")]
        when = p.get("when", "once")
        task = {"name": p.get("name") or f"task-{int(time.time())}",
                "type": p.get("type", "command"), "value": p.get("value", ""),
                "when": when, "at": p.get("at"),
                "interval_min": max(1, int(p.get("interval_min", 30))),
                "delay_min": max(0.05, float(p.get("delay_min", 1))),
                "path": p.get("path")}
        if when == "once" and task.get("at"):
            from datetime import datetime
            task["_ts"] = datetime.fromisoformat(task["at"].replace("Z", "+00:00")).timestamp()
        elif when == "delay":
            task["_ts"] = time.time() + task["delay_min"] * 60
        elif when == "once":
            task["_ts"] = time.time() + 30  # safety: run soon if no time given
        elif when not in ("daily", "interval"):
            raise ValueError(f"unsupported schedule: {when}")
        if task["type"] == "script" and not task.get("path"):
            raise ValueError("script tasks need tasks.upload_script first")
        tasks = [x for x in tasks if x["name"] != task["name"]]
        tasks.append(task)
        _save(c, tasks)
        return {"added": task["name"], "when": when}

    def agent_remove(c, p):
        tasks = [x for x in _load(c) if x["name"] != p["name"]]
        _save(c, tasks)
        return {"removed": p["name"]}

    def agent_run(c, p):
        for t in _load(c):
            if t["name"] == p["name"]:
                _execute(c, t)
                return {"ran": p["name"]}
        raise ValueError("no such task")

    def upload_script(c, p):
        name = _safe_name(p.get("name") or f"script_{int(time.time())}")
        ext = str(p.get("ext", "py")).lower().lstrip(".")
        if ext not in ALLOWED_EXTS:
            raise ValueError(f"extension .{ext} not allowed (use {', '.join(ALLOWED_EXTS)})")
        raw = base64.b64decode(p.get("content_b64", "") or "")
        if not raw:
            raise ValueError("empty script")
        if len(raw) > MAX_SCRIPT_BYTES:
            raise ValueError("script too large (512KB max)")
        from ..common import scripts_dir
        path = os.path.join(scripts_dir(), f"{name}.{ext}")
        with open(path, "wb") as f:
            f.write(raw)
        c.send_event("script_uploaded", {"name": name, "path": path, "bytes": len(raw)})
        return {"path": path, "name": name, "bytes": len(raw)}

    def run_script_now(c, p):
        return _run_script(c, {"name": p.get("name", "manual"), "path": p["path"]})

    def script_logs(c, p):
        from ..common import data_dir
        d = os.path.join(data_dir(), "script_runs")
        items = []
        if os.path.isdir(d):
            for fn in sorted(os.listdir(d))[-30:]:
                fp = os.path.join(d, fn)
                try:
                    items.append({"file": fn, "size": os.path.getsize(fp),
                                  "mtime": os.path.getmtime(fp)})
                except OSError:
                    pass
        return {"items": items}

    def script_log_read(c, p):
        from ..common import data_dir
        fn = os.path.basename(p.get("file", ""))
        fp = os.path.join(data_dir(), "script_runs", fn)
        with open(fp, "r", encoding="utf-8", errors="replace") as f:
            return {"file": fn, "content": f.read()[:100000]}

    def schtasks_list(c, p):
        p2 = subprocess.run(["schtasks", "/query", "/fo", "LIST"], capture_output=True,
                            text=True, errors="replace", timeout=60,
                            creationflags=CREATE_NO_WINDOW)
        lines = [l for l in (p2.stdout or "").splitlines() if l.strip()]
        return {"items": lines[:800]}

    def schtasks_run(c, p):
        out = subprocess.run(["schtasks", "/run", "/TN", p["name"]], capture_output=True,
                             text=True, errors="replace", creationflags=CREATE_NO_WINDOW)
        return {"output": ((out.stdout or "") + (out.stderr or ""))[:8000]}

    def schtasks_delete(c, p):
        out = subprocess.run(["schtasks", "/delete", "/TN", p["name"], "/f"],
                             capture_output=True, text=True, errors="replace",
                             creationflags=CREATE_NO_WINDOW)
        return {"output": ((out.stdout or "") + (out.stderr or ""))[:8000]}

    def startup_hks_status(c, p):
        """HKS own startup units: scheduled tasks + Startup-folder vbs
        (plan §24). Non-Windows returns a stable stub for parity."""
        if os.name != "nt":
            return {"units": [], "note": "windows-only view"}
        units = []
        for tn in ("HKS-Panel", "HKS-Agent", "HKS-SessionAgent"):
            out = subprocess.run(["schtasks", "/Query", "/TN", tn, "/FO", "LIST", "/V"],
                                 capture_output=True, text=True, timeout=20,
                                 creationflags=CREATE_NO_WINDOW)
            txt = (out.stdout or "")
            if out.returncode != 0 or "TASKNAME" not in txt.upper():
                units.append({"unit": tn, "kind": "task", "exists": False,
                              "status": "missing"})
                continue
            def field(label):
                for line in txt.splitlines():
                    if line.lower().startswith(label.lower() + ":"):
                        return line.split(":", 1)[1].strip()
                return None
            units.append({"unit": tn, "kind": "task", "exists": True,
                          "status": (field("Status") or "?"),
                          "run_task": (field("Task To Run") or "")[:120],
                          "next_run": field("Next Run Time"),
                          "last_result": field("Last Result")})
        startup_dir = os.path.join(os.environ.get("APPDATA", ""),
                                   "Microsoft", "Windows", "Start Menu", "Programs", "Startup")
        try:
            vbs = [f for f in os.listdir(startup_dir) if f.startswith("HKS_")]
        except OSError:
            vbs = []
        units.append({"unit": "Startup-folder launchers", "kind": "startup-folder",
                      "exists": bool(vbs), "status": ", ".join(vbs) if vbs else "none"})
        return {"units": units}

    def startup_hks_action(c, p):
        """enable/disable/start/stop an HKS task. Disabling the PANEL task
        cuts remote access - requires confirm."""
        if os.name != "nt":
            return {"ok": False, "error": "windows-only"}
        unit = str(p.get("unit", ""))
        op = str(p.get("op", "")).lower()
        if unit not in ("HKS-Panel", "HKS-Agent", "HKS-SessionAgent"):
            return {"ok": False, "error": "unknown HKS unit"}
        if op in ("disable",) and unit == "HKS-Panel" and p.get("confirm") != "CONFIRM":
            return {"ok": False, "error": 'disabling the panel task locks you out - send {"confirm":"CONFIRM"}'}
        flag = {"enable": "/ENABLE", "disable": "/DISABLE", "start": "/RUN", "stop": "/END"}.get(op)
        if not flag:
            return {"ok": False, "error": "op must be enable|disable|start|stop"}
        out = subprocess.run(["schtasks", "/Change", "/TN", unit, flag],
                             capture_output=True, text=True, timeout=20,
                             creationflags=CREATE_NO_WINDOW)
        if out.returncode != 0:
            return {"ok": False, "error": ((out.stdout or "") + (out.stderr or "")).strip()[:300]}
        return {"output": ((out.stdout or "") + (out.stderr or "")).strip()[:400]}

    return {"tasks.agent_list": agent_list, "tasks.agent_add": agent_add,
            "tasks.agent_remove": agent_remove, "tasks.agent_run": agent_run,
            "tasks.upload_script": upload_script, "tasks.run_script_now": run_script_now,
            "tasks.script_logs": script_logs, "tasks.script_log_read": script_log_read,
            "schtasks.list": schtasks_list, "schtasks.run": schtasks_run,
            "schtasks.delete": schtasks_delete, "startup.hks_status": startup_hks_status, "startup.hks_action": startup_hks_action}
