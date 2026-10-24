"""Screen capture + MJPEG streaming (runs inside the desktop user session)."""
import base64
import io
import sys
import threading
import time


_DPI = {"ok": None}


def _dpi_ok():
    return _DPI["ok"] is not False


def _ensure_dpi_awareness():
    """Windows display scaling (125% / 150% on most laptops) silently breaks
    remote control: mss screenshots come back in PHYSICAL pixels while a
    non-DPI-aware process moves the mouse in virtualized LOGICAL pixels, so
    remote clicks land in the wrong place (worse the higher the scaling).
    Making this process per-monitor DPI aware puts BOTH sides on real
    pixels. Must run before the first coordinate call, so it happens at
    import time."""
    if sys.platform != "win32":
        _DPI["ok"] = True
        return
    try:
        import ctypes
        try:  # Windows 10 1703+ : per-monitor v2 (best)
            if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
                _DPI["ok"] = True
                return
        except Exception:
            pass
        try:  # Windows 8.1+ : per-monitor
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
            _DPI["ok"] = True
            return
        except Exception:
            pass
        try:  # Vista+ : system aware
            ctypes.windll.user32.SetProcessDPIAware()
            _DPI["ok"] = True
            return
        except Exception:
            pass
    except Exception:
        pass
    _DPI["ok"] = False  # audit4 #S3: register() reports this honestly


_ensure_dpi_awareness()

_state = {"on": False, "fps": 8, "quality": 60, "monitor": 1, "max_width": 1280,
          "input_enabled": False, "errors": 0, "blocked_reason": ""}
_thread = None


def _grab(monitor, max_width):
    import mss
    from PIL import Image
    with mss.mss() as sct:
        if monitor == "all":
            mon = {"left": 0, "top": 0,
                   "width": sum(m["width"] for m in sct.monitors[1:]),
                   "height": max(m["height"] for m in sct.monitors[1:])}
        else:
            idx = min(int(monitor), len(sct.monitors) - 1)
            mon = sct.monitors[idx if idx > 0 else 1]
        shot = sct.grab(mon)
        im = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    if max_width and im.width > max_width:
        ratio = max_width / im.width
        im = im.resize((max_width, int(im.height * ratio)), Image.BILINEAR)
    _draw_host_cursor(im, mon, shot.size)
    return im


def _host_cursor_pos():
    """Physical-pixel cursor position (process is DPI aware since import)."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        pt = POINT()
        if ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
            return pt.x, pt.y
    except Exception:
        pass
    return None


def _draw_host_cursor(im, mon, grab_size):
    """Draw a visible cursor marker into the streamed frame.

    Ported from AnywhereInput: screen captures do NOT include the real
    cursor, so a remote viewer sees nothing move when they click - the
    #1 'screen control feels dead' complaint. A crosshair + dot at the
    host cursor position makes every remote click visibly land."""
    pos = _host_cursor_pos()
    if pos is None:
        return
    try:
        from PIL import ImageDraw
        gw = grab_size[0]
        f = (im.width / gw) if gw else 1.0  # grab vs streamed size ratio
        x = int((pos[0] - mon["left"]) * f)
        y = int((pos[1] - mon["top"]) * f)
        if not (0 <= x < im.width and 0 <= y < im.height):
            return
        d = ImageDraw.Draw(im)
        w, h = max(8, im.width // 90), max(12, im.width // 64)
        d.line([(x - w, y), (x + w, y)], fill=(255, 255, 255), width=2)
        d.line([(x, y - h), (x, y + h)], fill=(255, 255, 255), width=2)
        d.line([(x - w, y), (x + w, y)], fill=(230, 50, 60), width=1)
        d.line([(x, y - h), (x, y + h)], fill=(230, 50, 60), width=1)
        d.ellipse([(x - 3, y - 3), (x + 3, y + 3)], fill=(255, 220, 60),
                  outline=(200, 40, 50), width=1)
    except Exception:
        pass


def _jpeg(im, quality):
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def _explain(e):
    """Translate raw GDI/mss errors into honest one-line reasons.

    Field evidence (Acer, 2026-09-22): every poll while a UAC prompt or
    the secure desktop was up produced 'BitBlt: Access is denied' with a
    full traceback, spamming the Errors page 15+ times in 30 s. The
    condition is normal Windows protection, not a crash - say so."""
    s = str(e)
    low = s.lower()
    if "access is denied" in low or "access denied" in low or "winerror 5" in low:
        return ("Windows blocked desktop capture (BitBlt denied). A UAC prompt or the "
                "lock screen is up, or the agent is not in the logged-on session. "
                "Close the UAC prompt / unlock the screen, then retry.")
    if "gdi" in low or "getdcrend" in low or "getdc" in low:
        return ("No visible desktop to capture right now (session starting, locked, "
                "or disconnected). Retry after logon.")
    return s


def _secure_desktop_active():
    """Detect lock/UAC desktop so a normal privacy lock does not kill streaming."""
    try:
        # Shared with the input engine so both features use the same Win32
        # desktop test; import lazily to keep screen/input module loading acyclic.
        from .input_ctl import _input_desktop
        info = _input_desktop()
        if info.get("secure"):
            return True, info.get("error") or "Windows secure sign-in screen is active"
    except Exception:
        pass
    return False, ""


_cpu_prev = {"primed": False}


def _cpu_ok():
    try:
        import psutil
        v = psutil.cpu_percent(interval=None)
        if not _cpu_prev["primed"]:
            _cpu_prev["primed"] = True  # first call ALWAYS returns 0.0 (audit4)
            return True
        return v < 92
    except Exception:
        return True


def _stream_loop(ctx):
    min_interval = 1.0 / max(1, _state["fps"])
    while _state["on"]:
        t0 = time.time()
        try:
            if not _cpu_ok():
                time.sleep(0.2)  # adaptive throttling on the i3
                continue
            im = _grab(_state["monitor"], _state["max_width"])
            ctx.send_binary(1, _jpeg(im, _state["quality"]))
            _state["errors"] = 0
            _state["blocked_reason"] = ""
        except Exception as e:
            locked, reason = _secure_desktop_active()
            if locked:
                # Locking the workstation/UAC switches Windows to Winlogon.
                # Keep the logged-on helper and stream task alive, pause frame
                # delivery, and retry at a low rate; resume automatically as
                # soon as Windows returns to the user's Default desktop.
                _state["blocked_reason"] = reason
                _state["errors"] = 0
                time.sleep(min(2.0, max(0.5, min_interval)))
                continue
            _state["blocked_reason"] = ""
            _state["errors"] += 1
            if _state["errors"] > 10:
                ctx.send_error("screen", f"stream stopping after errors: {_explain(e)}")
                _state["on"] = False
                break
            if _state["errors"] <= 2:  # first misses only - no spam
                ctx.send_error("screen", f"frame skipped: {_explain(e)}")
            time.sleep(1)
        wait = min_interval - (time.time() - t0)
        if wait > 0:
            time.sleep(wait)
    _state["input_enabled"] = False


def stop():
    """Idempotent stop — used by the control action and the disconnect hook."""
    _state["on"] = False
    _state["input_enabled"] = False


def register(ctx):
    def control(c, p):
        _state["on"] = bool(p.get("on", False))
        for k in ("fps", "quality", "max_width"):
            if p.get(k):
                _state[k] = int(p[k])
        if p.get("monitor"):
            _state["monitor"] = p["monitor"] if p["monitor"] == "all" else int(p["monitor"])
        if _state["on"]:
            _state["input_enabled"] = True  # mirror on implies control on;
            # mirror off must NOT kill an explicit control toggle
        # (stop is registered ONCE in register() below - per-toggle
        #  appends grew the hook list forever. audit2 #11)
        global _thread
        if _state["on"] and (_thread is None or not _thread.is_alive()):
            _thread = threading.Thread(target=_stream_loop, args=(c,), daemon=True,
                                       name="hks-screen")
            _thread.start()
        return {"streaming": _state["on"], "fps": _state["fps"]}

    def snapshot(c, p):
        try:
            im = _grab(p.get("monitor", 1), int(p.get("max_width", 1600)))
        except Exception as e:
            raise RuntimeError(_explain(e)) from None  # honest, no raw dump
        return {"b64": base64.b64encode(_jpeg(im, 70)).decode()}

    ctx.disconnect_hooks.append(stop)  # once per process (audit2 #11)
    if not _dpi_ok():
        ctx.send_error("screen", "DPI awareness could not be set - remote clicks may land off-target "
                                 "(check Windows display scaling)", "")
    return {"screen.control": control, "screen.snapshot": snapshot}


def set_input_enabled(on):
    """Remote-control toggle, INDEPENDENT of the mirror stream.

    Field fix 2026-09: injection was gated on the screen STREAM running,
    so when the browser's live websocket was down (backup live mode) the
    control toggle silently did nothing. Now control is its own flag;
    the failsafes remain: agent disconnect clears it, and 5 injection
    errors in 30 s self-disable it."""
    _state["input_enabled"] = bool(on)


def is_input_enabled():
    return _state["input_enabled"]
