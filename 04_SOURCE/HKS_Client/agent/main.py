"""HKS Agent — SYSTEM service core (role: svc).

Runs file/terminal/network/usb/power/app/task/log modules. Survives reboot
via the HKS Agent Service (pywin32) or a SYSTEM scheduled task (fallback).
"""
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.common import AgentContext  # noqa: E402


def build_context(role="svc"):
    ctx = AgentContext(role)
    try:
        import psutil
        p = psutil.Process()
        try:
            p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)  # stay light on the i3
        except Exception:
            pass
    except Exception:
        pass
    return ctx


CONFIG_SET_KEYS = ("fps", "quality", "max_width", "telemetry_sec", "server_url", "name")


def make_config_set(ctx):
    """Remote config.set handler (both agents). Whitelist-only, saves
    config.json, returns exactly what changed. Field audit 2026-09-23:
    session_main imported a symbol that never existed, so config.set
    silently never worked on ANY agent."""
    def config_set(c, p):
        data = p.get("data") if isinstance(p, dict) else None
        data = data or (p if isinstance(p, dict) else {})
        changed = {}
        for k in CONFIG_SET_KEYS:
            if k not in data:
                continue
            cur = c.cfg.get(k)
            try:
                new = int(data[k]) if isinstance(cur, int) else str(data[k])
            except Exception:
                continue
            if new != cur:
                c.cfg[k] = new
                changed[k] = new
        if changed:
            c.cfg.save()
        return {"ok": True, "changed": changed}
    return config_set


def load_service_modules(ctx):
    from agent.modules import (addons, apps, code, files, logs_mod, media_scan,
                               network_ctl, power_ctl, sql_access, sysinfo, tasks,
                               terminal, usb_ctl, webdeploy, xampp)
    ctx.register("sysinfo", sysinfo)
    ctx.register("files", files)
    ctx.register("terminal", terminal)
    ctx.register("media_scan", media_scan)
    ctx.register("sql_access", sql_access)
    ctx.register("webdeploy", webdeploy)
    ctx.register("network_ctl", network_ctl)
    ctx.register("usb_ctl", usb_ctl)
    ctx.register("power_ctl", power_ctl)
    ctx.register("apps", apps)
    ctx.register("code", code)
    ctx.register("xampp", xampp)
    ctx.register("tasks", tasks)
    ctx.register("logs_mod", logs_mod)
    ctx.register("addons", addons)  # HKS_ADDONS kit status board
    from agent.modules import selftest as _selftest  # round 68 (F17)
    ctx.register("selftest", _selftest)
    try:
        from agent.modules import hardware
        ctx.register("hardware", hardware)  # also starts the periodic reporter
    except Exception as e:
        import traceback as _tb
        ctx.send_error("hardware", f"module unavailable: {e}", _tb.format_exc())  # audit4
    # config echo (both roles)
    ctx.actions["config.get"] = lambda c, p: {"ok": True, "data": {k: c.cfg[k] for k in
                                              ("fps", "quality", "max_width", "telemetry_sec", "server_url", "name")}}
    ctx.actions["config.set"] = make_config_set(ctx)
    ctx.actions["ping"] = lambda c, p: {"ok": True, "data": {"pong": time.time()}}


def run_agent():
    from agent.common import kill_switch_active
    if kill_switch_active():
        print("[hks] kill-switch present — not starting")
        return
    ctx = build_context("svc")
    load_service_modules(ctx)
    from agent.modules import logon_watch
    logon_watch.register(ctx)  # starts watcher thread
    ctx.send_event("agent_start", {"role": "svc", "pid": os.getpid(),
                                   "msg": "HKS PROGRAM HAS BEEN STARTED"})
    from agent import janitor
    janitor.start(ctx)      # hourly disk cleanup (zips, logs, indexes)
    from agent import local_panel
    local_panel.start(ctx)  # localhost-only status page (127.0.0.1:8590)

    from agent.connection import Connection
    conn = Connection(ctx, ctx.actions)
    ctx.log.info("agent starting (role=svc, %d actions)", len(ctx.actions))
    conn.start_forever()


def _single_instance_lock():
    """Machine-wide mutex so the scheduled task, the Startup-folder
    fallback and a manual run_agent.bat double-click can never run two
    SERVICE agents (round 48 field: 'file in use by another process' =
    two watchdogs fighting over one log + superseded connections).
    Same battle-tested pattern as session_main (audit2 #10 prototypes)."""
    if os.name != "nt":
        return True
    try:
        import ctypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool,
                                     ctypes.c_wchar_p]
        k32.CreateMutexW.restype = ctypes.c_void_p
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        ERROR_ALREADY_EXISTS = 183
        for name in (r"Global\HKS_ServiceAgent_single",
                     r"Local\HKS_ServiceAgent_single"):
            handle = k32.CreateMutexW(None, False, name)
            if handle:
                if ctypes.get_last_error() != ERROR_ALREADY_EXISTS:
                    return True  # we own it; kernel releases on exit
                k32.CloseHandle(handle)
                return False  # a healthy twin owns it - stay out
        return True  # could not create any mutex - fail-open
    except Exception:
        return True


def supervising_main():
    """Never exits: restart the connection loop on fatal errors."""
    if not _single_instance_lock():
        print("[hks-svc] another service agent is already running - exiting")
        return
    while True:
        try:
            from agent.common import kill_switch_active
            if kill_switch_active():
                print("[hks] kill-switch present — supervising loop exiting")
                return
            run_agent()
            if kill_switch_active():
                return
        except KeyboardInterrupt:
            return
        except Exception as e:
            print(f"[hks] fatal, restarting in 15s: {e}\n{traceback.format_exc()}")
            time.sleep(15)



def _r67_crash_handlers(role):
    # round 67 (F22): leave evidence when the process dies hard. faulthandler
    # catches C-level crashes; excepthook catches Python-level ones. Files
    # land in data/crashes/ where the doctor and the user can find them.
    try:
        import faulthandler
        from agent.common import data_dir as _dd
        cdir = os.path.join(_dd(), "crashes")
        os.makedirs(cdir, exist_ok=True)
        fh = open(os.path.join(cdir, role + "_faulthandler.log"), "a")
        faulthandler.enable(file=fh, all_threads=True)

        def _hook(et, ev, tb):
            try:
                with open(os.path.join(cdir, f"{role}_{int(time.time())}.txt"), "w") as f:
                    f.write(f"role={role} pid={os.getpid()}\n")
                    traceback.print_exception(et, ev, tb, file=f)
            except Exception:
                pass
            sys.__excepthook__(et, ev, tb)
        sys.excepthook = _hook
    except Exception:
        pass

if __name__ == "__main__":
    _r67_crash_handlers("svc")
    if "--console" in sys.argv:
        supervising_main()
    else:
        from agent import win_service
        win_service.handle_command_line()
