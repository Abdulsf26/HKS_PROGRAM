"""Logon/logoff/power watcher (service side).

Polls `quser` for console session changes and the System event log for
sleep/resume/shutdown events. Writes state to data/state.json which the
session agent reads at startup to decide whether a fresh logon capture
(webcam photo + screenshot) is needed.
"""
import json
import os
import re
import subprocess
import threading
import time

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _state_path():
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
    from agent.common import data_dir
    return os.path.join(data_dir(), "state.json")


def read_state():
    try:
        with open(_state_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def write_state(d):
    tmp = None
    try:
        path = _state_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + f".{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass


def _quser():
    try:
        p = subprocess.run(["quser"], capture_output=True, text=True, errors="replace",
                           timeout=15, creationflags=CREATE_NO_WINDOW)
        return p.stdout or ""
    except Exception:
        return ""


def _active_user():
    out = _quser()
    for line in out.splitlines():
        parts = re.split(r"\s{2,}", line.strip())
        if len(parts) >= 4 and "disc" not in parts[3].lower() and parts[0] != "USERNAME":
            if ">" in parts[0]:
                parts[0] = parts[0].replace(">", "")
            return {"user": parts[0], "session": parts[-2] if len(parts) >= 5 else parts[2],
                    "raw": line.strip()}
    return None


def _new_system_events(last_id):
    """Incremental read of interesting System events (sleep/resume/boot/shutdown)."""
    out = ""
    try:
        p = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             f"$e=Get-WinEvent -FilterHashtable @{{LogName='System';Id=1,42,107,1074,6005,6006,6008}} "
             f"-MaxEvents 30 -ErrorAction SilentlyContinue | Sort-Object RecordId; "
             f"$e | Where-Object {{$_.RecordId -gt {last_id}}} | "
             "ForEach-Object { \"{0}|{1}|{2}\" -f $_.RecordId,$_.Id,($_.Message -split \"`n\")[0] }"],
            capture_output=True, text=True, errors="replace", timeout=60,
            creationflags=CREATE_NO_WINDOW)
        out = p.stdout or ""
    except Exception:
        pass
    events = []
    for line in out.splitlines():
        parts = line.split("|", 2)
        if len(parts) == 3:
            events.append({"record": int(parts[0]), "id": int(parts[1]), "msg": parts[2][:120]})
    return events


def _event_kind(event_id):
    """Map only unambiguous Windows System event IDs.

    Kernel-General event 1 means the system clock changed; treating it as a
    resume repeatedly triggered webcam login captures on machines receiving
    periodic time corrections. Kernel-Power event 107 is the resume event.
    """
    return {42: "sleep", 107: "resume", 1074: "shutdown_request",
            6005: "boot", 6006: "shutdown", 6008: "dirty_shutdown"}.get(int(event_id))


def register(ctx):
    def start():
        threading.Thread(target=_watch, args=(ctx,), daemon=True,
                         name="hks-logon-watch").start()
        return {}

    return start()


def _watch(ctx):
    state = read_state()
    last_record = state.get("last_event_record", 0)
    last_user = state.get("console_user")
    ctx.send_event("boot", {"msg": "HKS PROGRAM HAS BEEN STARTED",
                        "detail": "starting up — fully ready in ~1 minute",
                        "last_boot_seen": state.get("last_boot"),
                        "boot_time": time.time()})

    while True:
        try:
            user_info = _active_user()
            now_user = user_info["user"] if user_info else None
            if now_user and now_user != last_user:
                state = read_state()
                state["console_user"] = now_user
                state["last_logon_ts"] = time.time()
                state["last_logon_user"] = now_user
                state["capture_needed"] = True
                state["capture_reason"] = "login"
                write_state(state)
                ctx.send_event("logon", {"user": now_user, "session": user_info.get("session"),
                                         "detail": user_info.get("raw", "")})
                last_user = now_user
            elif not now_user and last_user:
                ctx.send_event("logoff", {"user": last_user})
                state = read_state()
                state["console_user"] = None
                write_state(state)
                last_user = None

            for ev in _new_system_events(last_record):
                last_record = max(last_record, ev["record"])
                kind = _event_kind(ev["id"])
                if kind:
                    ctx.send_event(kind, {"win_event": ev["id"], "msg": ev["msg"]})
                    state = read_state()
                    if kind in ("resume", "boot"):
                        state["capture_needed"] = True
                        state["last_wake"] = time.time()
                        state["capture_reason"] = "wake/resume" if kind == "resume" else "boot"
                        state["last_wake_event_id"] = ev["id"]
                    state["last_event_record"] = last_record
                    write_state(state)
        except Exception as e:
            ctx.send_error("logon_watch", str(e))
        time.sleep(10)
