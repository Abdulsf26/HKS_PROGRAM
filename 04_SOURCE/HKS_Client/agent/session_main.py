"""HKS Agent — desktop session side (role: session).

Runs in the logged-in user's session (started by a logon scheduled task).
Handles: screen mirror + remote input, camera/mic, clipboard, volume,
toasts, TTS, media playback, lock screen, user-mode terminal, login capture.
Keeps running even when the SYSTEM service is offline; reconnects forever.
"""
import os
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.main import build_context  # noqa: E402


def load_session_modules(ctx):
    """Fault-isolated: ONE broken optional module must never take down the
    whole desktop agent — it just loses that module's features."""
    import importlib
    loaded = []
    modules = {}
    for name in ("screen", "input_ctl", "camera", "desktop", "addon_runner", "capture_watch"):
        try:
            modules[name] = importlib.import_module(f"agent.session_modules.{name}")
        except Exception as e:
            import traceback
            ctx.log.error("session module %s import failed: %s", name, e)
            ctx.send_error("session_main", f"module {name} import failed: {e}",
                           traceback.format_exc())
    for name in ("screen", "input_ctl", "camera", "desktop", "addon_runner"):
        mod = modules.get(name)
        if mod is None:
            continue
        try:
            ctx.register(name, mod)
            loaded.append(name)
        except Exception as e:
            import traceback
            ctx.log.error("session module %s failed to load: %s", name, e)
            ctx.send_error("session_main", f"module {name} failed to load: {e}",
                           traceback.format_exc())
    try:
        code = importlib.import_module("agent.modules.code")
        ctx.register("code", code)
        loaded.append("code")
    except Exception as e:
        ctx.log.error("code module failed to load: %s", e)
    camera = modules.get("camera")
    try:
        if camera is None:
            raise RuntimeError("camera module is unavailable")
        ctx.actions.update(camera.register_mic(ctx))
        loaded.append("mic")
    except Exception as e:
        ctx.log.error("mic module failed to load: %s", e)
    try:
        capture_watch = modules.get("capture_watch")
        if capture_watch is None:
            raise RuntimeError("capture_watch module is unavailable")
        capture_watch.register(ctx)
        loaded.append("capture_watch")
    except Exception as e:
        ctx.log.error("capture_watch failed to load: %s", e)
    ctx.actions["ping"] = lambda c, p: {"ok": True, "data": {"pong": time.time()}}
    try:
        from agent.main import make_config_set
        ctx.actions["config.set"] = make_config_set(ctx)
    except Exception as e:
        ctx.log.error("config.set failed to load: %s", e)
    # admin voice → laptop speakers arrives as binary channel 4
    try:
        desktop = modules.get("desktop")
        if desktop is None:
            raise RuntimeError("desktop module is unavailable")
        ctx.on_binary(4, desktop.feed_voice)
    except Exception as e:
        ctx.log.error("voice channel wiring failed: %s", e)
    ctx.log.info("session modules loaded: %s", ", ".join(loaded))


def run_session():
    ctx = build_context("session")
    from agent import local_panel
    local_panel.start(ctx)  # own status page (8590..8594; svc usually holds 8590)
    # Publish local liveness before optional module imports so the watchdog
    # can distinguish a slow/broken startup from a missing agent process.
    load_session_modules(ctx)
    ctx.send_event("session_agent_start", {"pid": os.getpid()})
    from agent.connection import Connection
    conn = Connection(ctx, ctx.actions)
    ctx.log.info("session agent starting (%d actions)", len(ctx.actions))
    conn.start_forever()


def _read_panel_endpoint():
    """(scheme, port) of the admin panel from config.json's server_url
    (wss://127.0.0.1:6767/ws/agent -> ("https", "6767"))."""
    try:
        import json
        p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "config.json")
        with open(p) as f:
            cfg = json.load(f)
        from urllib.parse import urlsplit  # audit3: manual rsplit broke on
        u = urlsplit(str(cfg.get("server_url", "")))  # portless URLs
        port = u.port or (443 if u.scheme == "wss" else 80)
        return ("https" if u.scheme == "wss" else "http"), str(port)
    except Exception:
        return None, None


def _lan_ip(timeout_s=6.0):
    """Current LAN IPv4, retrying briefly: right after logon Wi-Fi/DHCP
    is often not up yet. The IP changes with every network — the field
    complaint was exactly this (old address typed on the phone)."""
    deadline = time.time() + timeout_s
    best = ""
    while time.time() < deadline:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
            best = best or ip
        except Exception:
            pass
        finally:
            s.close()
        time.sleep(1.0)
    return best


def _logon_toast():
    """Visible proof of life + the CURRENT control address (the address
    changes whenever the network changes — always show the live one)."""
    try:
        scheme, port = _read_panel_endpoint()
        ip = _lan_ip() if port else ""
        phone = f"{scheme}://{ip}:{port}" if (scheme and port and ip
                                              and not ip.startswith("127.")) else ""
        # anywhere-tunnel address (works from mobile data / any network)
        try:
            tf = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))), "data", "tunnel_url.txt")
            if os.path.exists(tf):
                turl = open(tf).read().strip()
                if turl:
                    phone = (turl + "  <-- WORKS FROM ANYWHERE (mobile data too)"
                             + ("\nLAN only: " + phone if phone else ""))
        except Exception:
            pass
        if phone and scheme == "https":
            # the no-certificate companion listener (phone-friendly)
            try:
                phone += f"\nNo-cert option: http://{ip}:{int(port) + 1}"
            except Exception:
                pass
        msg = "This laptop is managed from your admin panel.\n" \
              "Status: http://127.0.0.1:8590"
        if phone:
            msg = ("Control from your phone (same Wi-Fi):\n"
                   f"{phone}\n"
                   "Network changed? Run CHECK_STATUS.bat\n"
                   "for the new address.\n\n") + msg
        try:
            from winotify import Notification, audio
        except ImportError:  # audit4 #S2b: still show SOMETHING
            import subprocess as _sp
            _sp.Popen(["msg", "*", "/TIME:30", msg[:250]],
                      creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0))
            return
        n = Notification(app_id="HKS",
                         title="HKS agent is running",
                         msg=msg)
        n.set_audio(audio.Silent, loop=False)
        n.show()
    except Exception as e:  # round 64 (audit6 #21): silent failure -> diagnosable
        print("[session] logon toast failed:", e, file=sys.stderr, flush=True)


def _win_session_id():
    """Interactive-desktop session id of this process (None if not Windows)."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        import ctypes.wintypes
        sid = ctypes.wintypes.DWORD()
        pid = ctypes.windll.kernel32.GetCurrentProcessId()
        if ctypes.windll.kernel32.ProcessIdToSessionId(pid, ctypes.byref(sid)):
            return int(sid.value)
    except Exception:
        pass
    return None


def _single_instance_lock():
    """Machine-wide mutex so the scheduled task and the Startup-folder
    launcher never run two session agents (they used to supersede each
    other on the server every few seconds, killing live streams).

    Returns True if we own the lock (or can't check on this OS)."""
    if os.name != "nt":
        return True
    try:
        import ctypes
        # full prototypes: HANDLE is 64-bit - without restype ctypes
        # truncates it to c_int (audit2 #10)
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool,
                                     ctypes.c_wchar_p]
        k32.CreateMutexW.restype = ctypes.c_void_p
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        ERROR_ALREADY_EXISTS = 183
        for name in (r"Global\HKS_SessionAgent_single",
                     r"Local\HKS_SessionAgent_single"):
            handle = k32.CreateMutexW(None, False, name)
            if handle:
                if ctypes.get_last_error() != ERROR_ALREADY_EXISTS:
                    return True  # we own it; kernel releases it when we die
                k32.CloseHandle(handle)
                return False  # a healthy twin owns it - stay out (exit 42)
            # handle == 0 (e.g. Global\ denied at low integrity): TRY THE
            # NEXT namespace. Field audit 2026-09-23: the old code returned
            # False here without ever trying Local\ - the desktop agent
            # exited silently on some unelevated sessions.
        return True  # could not create any mutex - fail-open (matches except)
    except Exception:
        return True
    return True


EXIT_DUPLICATE = 42   # another agent owns the single-instance lock
EXIT_NO_DESKTOP = 43  # session 0: no visible desktop here


def _is_session_agent_argv(process_name, argv):
    """Match only an actual Python session-agent invocation, never a shell
    whose diagnostic command text happens to mention both words."""
    name = str(process_name or "").lower()
    if name not in ("python.exe", "pythonw.exe"):
        return False
    args = [str(arg or "").replace("\\", "/").lower() for arg in (argv or [])]
    for i, arg in enumerate(args[:-1]):
        if arg == "-m" and args[i + 1] == "agent.session_main":
            return True
    return any(arg.endswith("/agent/session_main.py") for arg in args[1:])


def _other_session_agent_alive():
    """round 51: belt-and-braces BEYOND the mutex. The Local\\ mutex
    cannot see a twin in a DIFFERENT logon session (field 2026-09-26:
    doctor showed two pythonw -m agent.session_main at once). Scan every
    process cross-session; psutil already ships with the agent."""
    if os.name != "nt":
        return False
    try:
        import psutil
        me = os.getpid()
        _fam = set()
        try:
            _fam.add(psutil.Process(me).ppid())
            for _c in psutil.Process(me).children(recursive=True):
                _fam.add(_c.pid)
        except Exception:
            _fam = set()
        for p in psutil.process_iter(["pid", "cmdline", "name"]):
            try:
                if p.info["pid"] == me:
                    continue
                if p.info["pid"] in _fam:
                    continue  # 7.2.52: venv shim/child pair is ONE logical agent
                if _is_session_agent_argv(p.info.get("name"), p.info.get("cmdline")):
                    return True
            except Exception:
                continue
    except Exception:
        return False
    return False


def preflight():
    """Decide whether THIS process should run the desktop agent.

    Returns 0 to run, or a DISTINCT exit code so the run_session_agent.bat
    watchdog can react calmly instead of blind-restarting every 5 s:
      42 = another session agent already owns the lock (it is healthy)
      43 = session 0: no interactive desktop here

    Field evidence (Acer errors.txt, 2026-09-22): with the old True/False
    contract the watchdog restarted a doomed duplicate every 5 s for 10+
    hours - 7,258 "exited, restarting" lines, constant CPU and heat.
    """
    sid = _win_session_id()
    if sid == 0:
        print("[hks-session] session 0 (no interactive desktop) - exiting; "
              "the Startup launcher owns desktop features", flush=True)
        return EXIT_NO_DESKTOP
    if not _single_instance_lock():
        print("[hks-session] another session agent is already running - exiting",
              flush=True)
        return EXIT_DUPLICATE
    if _other_session_agent_alive():
        print("[hks-session] twin session agent seen in another logon "
              "session - exiting so streams do not supersede", flush=True)
        return EXIT_DUPLICATE
    return 0


def supervising_main():
    rc = preflight()
    if rc:
        sys.exit(rc)
    _logon_toast()
    while True:
        try:
            run_session()
        except KeyboardInterrupt:
            return
        except Exception as e:
            import traceback
            print(f"[hks-session] fatal, restarting in 15s: {e}\n{traceback.format_exc()}")
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
    _r67_crash_handlers("session")
    supervising_main()
