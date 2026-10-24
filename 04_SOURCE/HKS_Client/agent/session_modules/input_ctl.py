"""Remote mouse & keyboard injection — ENGINE REPLACED with AnywhereInput's
MouseWorker design (MIT, https://github.com/Z-Hussein/AnywhereInput).

What was ported from their engine (this is the "replace ours with theirs"
round): a queue-driven pyautogui worker (pyautogui.FAILSAFE=False, PAUSE=0),
NORMALIZED 0..1 coordinates from the browser multiplied by the real screen
size at the last moment, rate-limited moves, click batching, slow-queue for
typing/hotkeys, error classification with exponential backoff and an offline
threshold, and stuck-button auto-release. The 7.0.x HKS safety layer stays:
failsafe counters, disable on disconnect, explicit enable toggle, and the
process stays per-monitor DPI aware (screen.py import) so pyautogui.size()
returns PHYSICAL pixels matching the mss stream.

Safety: injection is only active while enabled; after too many injection
errors the engine goes offline (self-disables); when the dashboard
disconnects the connection layer flags it off; mouse buttons can never stay
stuck (tracked + auto-released).
"""
import queue
import threading
import time

from .screen import is_input_enabled, set_input_enabled

try:  # AnywhereInput optimizes pyautogui for minimum latency - same here
    import pyautogui as _pg

    _pg.FAILSAFE = False
    _pg.PAUSE = 0
except Exception:  # headless / not installed -> engine degrades to no-ops
    _pg = None

_SENTINEL = object()
_err_times = []
_disabled = threading.Event()
_MouseC = None  # legacy handles (kept for release-all compatibility)
_KbC = None

# engine state (AnywhereInput MouseWorker constants)
_q = queue.Queue(maxsize=100)
_slow = queue.Queue(maxsize=50)
_thread = None
_run = [True]
_screen = [0, 0]            # physical px, refreshed every batch
_screen_ts = [0.0]
_buttons_down = set()
_last_event = [0.0]
_min_move_interval = 0.004  # their DEFAULT_MOVE_INTERVAL spirit: max ~250 moves/s
_consec_fail = [0]
_offline_err = [""]

BASE_BACKOFF = 0.05
MAX_BACKOFF = 1.0
LONG_BACKOFF_AT = 5
OFFLINE_AT = 12


def _pgfn(name):
    """Return a real PyAutoGUI function; never silently fake input success."""
    if _pg is not None:
        return getattr(_pg, name)
    raise RuntimeError("remote input unavailable: PyAutoGUI could not load in the desktop agent")


def _input_desktop():
    """Describe the active Windows input desktop without trying to unlock it.

    Ordinary desktop input APIs are intentionally barred from Winlogon/UAC
    secure desktops. The session helper remains alive while the PC is locked,
    but it must not pretend that sends to that desktop will control Windows.
    """
    if __import__("os").name != "nt":
        return {"name": "default", "secure": False, "error": ""}
    try:
        import ctypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.OpenInputDesktop.argtypes = [ctypes.c_uint, ctypes.c_bool, ctypes.c_uint]
        user32.OpenInputDesktop.restype = ctypes.c_void_p
        user32.GetUserObjectInformationW.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                                      ctypes.c_void_p, ctypes.c_uint,
                                                      ctypes.POINTER(ctypes.c_uint)]
        user32.GetUserObjectInformationW.restype = ctypes.c_bool
        user32.CloseDesktop.argtypes = [ctypes.c_void_p]
        user32.CloseDesktop.restype = ctypes.c_bool
        handle = user32.OpenInputDesktop(0, False, 0x0001)  # DESKTOP_READOBJECTS
        if not handle:
            code = ctypes.get_last_error()
            protected = code == 5  # ACCESS_DENIED on Winlogon/UAC desktop
            return {"name": "protected" if protected else "unknown",
                    "secure": protected,
                    "error": ("Windows protects the active lock/UAC desktop from remote input (Win32 5)"
                              if protected else f"could not inspect active input desktop (Win32 {code})")}
        try:
            name = ctypes.create_unicode_buffer(256)
            needed = ctypes.c_uint(0)
            if not user32.GetUserObjectInformationW(handle, 2, name,
                                                     ctypes.sizeof(name), ctypes.byref(needed)):
                code = ctypes.get_last_error()
                protected = code == 5
                return {"name": "protected" if protected else "unknown",
                        "secure": protected,
                        "error": ("Windows protects the active lock/UAC desktop from remote input (Win32 5)"
                                  if protected else f"could not read active input desktop (Win32 {code})")}
            desktop = name.value or "unknown"
            secure = desktop.casefold() in ("winlogon", "screensaver")
            return {"name": desktop, "secure": secure,
                    "error": "Windows secure sign-in screen is active; unlock the PC locally or use a supported Remote Desktop session"
                    if secure else ""}
        finally:
            user32.CloseDesktop(handle)
    except Exception as e:
        return {"name": "unknown", "secure": False,
                "error": f"could not inspect active input desktop: {type(e).__name__}: {e}"}


def _refresh_screen(force=False):
    now = time.time()
    if force or now - _screen_ts[0] > 2.0 or not _screen[0]:
        try:
            s = _pgfn("size")()
            _screen[0], _screen[1] = int(s[0]), int(s[1])
        except Exception:
            if not _screen[0]:
                _screen[0], _screen[1] = 1920, 1080  # their fallback
        _screen_ts[0] = now


def _classify(err):
    """Their error classes: transient / degraded / failed."""
    e = str(err).lower()
    if any(k in e for k in ("access denied", "permission", "blocked", "uac", "privilege")):
        return "degraded"
    if any(k in e for k in ("display", "screen", "monitor", "rdp", "disconnect")):
        return "failed"
    return "transient"


def _backoff():
    b = BASE_BACKOFF * (2 ** max(0, _consec_fail[0] - 1))
    if _consec_fail[0] >= LONG_BACKOFF_AT:
        b = max(b, 0.5)
    return min(b, MAX_BACKOFF)


def _release_all_buttons():
    """Never leave a drag stuck (HKS 7.0.3 safety, on their down-state)."""
    if not _buttons_down:
        return
    _buttons_down.clear()
    try:
        for b in ("left", "right", "middle"):
            try:
                _pgfn("mouseUp")(b)
            except Exception:
                pass
    except Exception:
        pass


def _drain_slow():
    """Their _drain_slow_ops: typewrite/hotkey must not block the fast path."""
    while True:
        try:
            op, data = _slow.get_nowait()
        except queue.Empty:
            return
        try:
            if op == "typewrite":
                _pgfn("typewrite")(str(data)[:500], interval=0.003)  # audit4 #I2: 2000 chars = 10 s block
            elif op == "hotkey":
                _pgfn("hotkey")(*data)
            elif op == "keyDown":
                _pgfn("keyDown")(data)
            elif op == "keyUp":
                _pgfn("keyUp")(data)
        except Exception as e:
            _note_engine_error(e)


def _note_engine_error(e):
    _consec_fail[0] += 1
    _offline_err[0] = f"{type(e).__name__}: {e}"[:160]
    if _consec_fail[0] >= OFFLINE_AT and not _disabled.is_set():
        _disabled.set()
        set_input_enabled(False)
        _release_all_buttons()
    time.sleep(_backoff())


def _worker():
    _refresh_screen(True)
    last_move = 0.0
    while _run[0]:
        try:
            item = _q.get(timeout=0.5)
        except queue.Empty:
            item = None
        if item is _SENTINEL or not _run[0]:
            break
        _refresh_screen()
        if item is not None and not _disabled.is_set() and is_input_enabled():
            batch = [item]
            while len(batch) < 25:  # their MAX batch clamp
                try:
                    nxt = _q.get_nowait()
                except queue.Empty:
                    break
                if nxt is _SENTINEL:
                    _q.put(_SENTINEL)
                    break
                batch.append(nxt)
            for op, data in batch:
                if _disabled.is_set() or not is_input_enabled():
                    break  # round 64 (audit6 #18): failsafe must stop MID-batch
                try:
                    if op == "move":
                        now = time.time()
                        if now - last_move < _min_move_interval:
                            continue
                        last_move = now
                        x = max(0, min(_screen[0] - 1, int(float(data[0]) * _screen[0])))
                        y = max(0, min(_screen[1] - 1, int(float(data[1]) * _screen[1])))
                        _pgfn("moveTo")(x, y, duration=0)  # their moveTo call
                    elif op == "down":
                        _pgfn("moveTo")(max(0, min(_screen[0] - 1, int(float(data[0]) * _screen[0]))),
                                        max(0, min(_screen[1] - 1, int(float(data[1]) * _screen[1]))),
                                        duration=0)
                        _pgfn("mouseDown")(data[2])
                        _buttons_down.add(data[2])
                    elif op == "up":
                        _pgfn("mouseUp")(data[2])
                        _buttons_down.discard(data[2])
                    elif op == "scroll":
                        amt = -_sign(data[1]) * 15  # their scroll magnitude
                        if amt:
                            _pgfn("scroll")(amt)
                        if data[0]:
                            try:
                                _pgfn("hscroll")(_sign(data[0]))
                            except Exception:
                                pass
                    elif op in ("keyDown", "keyUp", "typewrite", "hotkey"):
                        _slow.put((op, data))
                    _consec_fail[0] = 0
                except Exception as e:
                    _note_engine_error(e)
            _drain_slow()
        elif item is not None:
            _drain_slow()
        # stale-press safety: events stopped for >5s with a button held
        if _buttons_down and time.time() - _last_event[0] > 5.0:
            _release_all_buttons()


def _sign(v):
    try:
        v = float(v)
    except Exception:
        return 0
    return (v > 0) - (v < 0)


def _enqueue(op, data):
    _last_event[0] = time.time()
    while True:
        try:
            _q.put_nowait((op, data))
            return True
        except queue.Full:
            try:
                _q.get_nowait()  # drop oldest - live input, newest wins
            except queue.Empty:
                pass


def _wait_idle(timeout=3.0):
    """Test helper: wait until the queue is drained."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        if _q.empty() and _slow.empty():
            time.sleep(0.05)
            return True
        time.sleep(0.02)
    return False


def start_engine():
    global _thread
    if _thread is None or not _thread.is_alive():
        _run[0] = True
        _thread = threading.Thread(target=_worker, daemon=True, name="hks-input-engine")
        _thread.start()


def stop_engine():
    _run[0] = False
    _release_all_buttons()


def _to_pg_button(b):
    return {1: "middle", 2: "right"}.get(b, "left")  # their button names


def register(ctx):
    def apply(c, p):
        if _disabled.is_set() or not is_input_enabled():
            return {"ok": True, "note": "input disabled (failsafe or engine offline)",
                    "suppressed": True}
        try:
            desktop = _input_desktop()
            if desktop["secure"]:
                return {"ok": False, "error": desktop["error"],
                        "desktop": desktop["name"]}
            if _pg is None:
                return {"ok": False,
                        "error": "remote input unavailable: PyAutoGUI could not load in the desktop agent"}
            t = p.get("t")
            # AnywhereInput protocol: browser sends NORMALIZED 0..1 coords
            if t == "mm":
                _enqueue("move", (float(p.get("x", 0.5)), float(p.get("y", 0.5))))
            elif t == "md":
                b = _to_pg_button(int(p.get("btn", 0)))
                _enqueue("down", (float(p.get("x", 0.5)), float(p.get("y", 0.5)), b))
            elif t == "mu":
                b = _to_pg_button(int(p.get("btn", 0)))
                _enqueue("up", (float(p.get("x", 0.5)), float(p.get("y", 0.5)), b))
            elif t == "mw":
                _enqueue("scroll", (int(p.get("dx", 0)), int(p.get("dy", 0))))
            elif t in ("kd", "ku"):
                key = _to_pg_key(p)
                if key:
                    _enqueue("keyDown" if t == "kd" else "keyUp", key)
            elif t == "type":
                text = str(p.get("text", ""))[:2000]
                if text:
                    _enqueue("typewrite", text)
            elif t == "combo":
                keys = []
                for m, pressed in (("ctrl", p.get("ctrl")), ("alt", p.get("alt")),
                                   ("shift", p.get("shift")), ("win", p.get("win"))):
                    if pressed:
                        keys.append({"ctrl": "ctrl", "alt": "alt",
                                     "shift": "shift", "win": "win"}[m])
                main = _to_pg_key(p)
                if main:
                    keys.append(main)
                if keys:
                    _enqueue("hotkey", keys)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def status(c, p):
        state = "offline" if _disabled.is_set() else ("degraded" if _consec_fail[0] else "healthy")
        desktop = _input_desktop()
        if desktop["secure"]:
            state = "secure desktop"
        elif _pg is None:
            state = "offline"
        return {"input_active": is_input_enabled(), "failsafe": _disabled.is_set(),
                "engine": state, "errors": _consec_fail[0], "last_error": _offline_err[0],
                "screen": {"w": _screen[0], "h": _screen[1]},
                "desktop": desktop["name"],
                "desktop_error": desktop["error"],
                "available": bool(_pg is not None and not desktop["secure"])}

    def reset(c, p):
        _disabled.clear()
        _consec_fail[0] = 0
        _err_times.clear()
        return {"reset": True}

    def enable(c, p):
        on = bool(p.get("on", True))
        set_input_enabled(on)
        if on:
            _disabled.clear()  # a fresh explicit enable clears the failsafe
            start_engine()
        else:
            _release_all_buttons()
        return {"enabled": on}

    # failsafe: link drop -> control off + no stuck buttons
    ctx.disconnect_hooks.append(lambda: (set_input_enabled(False), _release_all_buttons()))
    start_engine()
    return {"input.apply": apply, "input.status": status, "input.reset": reset,
            "input.enable": enable}


def _to_pg_key(p):
    """pynput-era key names -> pyautogui key names."""
    key = (p.get("key") or "").lower()
    named = {
        "enter": "enter", "return": "enter", "\n": "enter",
        "backspace": "backspace", "tab": "tab", "escape": "esc", "esc": "esc",
        " ": "space", "space": "space",
        "arrowup": "up", "up": "up", "arrowdown": "down", "down": "down",
        "arrowleft": "left", "left": "left", "arrowright": "right", "right": "right",
        "delete": "delete", "home": "home", "end": "end",
        "pageup": "pageup", "pagedown": "pagedown",
        "shift": "shift", "control": "ctrl", "ctrl": "ctrl", "alt": "alt",
        "meta": "win", "win": "win", "capslock": "capslock",
    }
    code = p.get("code") or ""
    if code.startswith("Key."):
        return named.get(code[4:].lower(), code[4:].lower())
    if code.startswith("Digit"):
        return code[5:]
    if key in named:
        return named[key]
    if key and len(key) == 1:
        return key
    if key.startswith("f") and key[1:].isdigit():
        return key
    return None
