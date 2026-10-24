"""Camera streaming & snapshots (OpenCV) + microphone clips (sounddevice)."""
import base64
import functools
import io
import os
import threading
import time

# Quiet OpenCV's videoio WARN spam BEFORE the first cv2 import in this
# process. Field evidence (Acer errors.txt, 2026-09-22): a missing camera
# printed ~30 WARN lines per poll - 19k lines in one console log.
os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")
os.environ.setdefault("OPENCV_VIDEOIO_DEBUG", "0")

# Cooldown per camera index: after a failed probe do not re-open that
# index for 60 s (UI frame polls used to hammer DSHOW/MSMF nonstop).
_no_cam_until = {}


def _cooldown_left(idx):
    return int(_no_cam_until.get(idx, 0) - time.time())


def _cooldown_start(idx):
    _no_cam_until[idx] = time.time() + 60


def _quiet_cv(cv2):
    """Best-effort: drop OpenCV's internal log level to ERROR."""
    try:
        cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
    except Exception:
        pass

_cam = {"on": False, "index": 0, "fps": 8, "quality": 70, "width": 960, "last_jpeg": None}
_list_cache = {"ts": 0.0, "res": None}  # avoid repeated native camera-driver probes
_thread = None
_camera_lock = threading.RLock()


def _camera_serialized(fn):
    """Keep all OpenCV handles single-owner, including concurrent API calls.

    A login capture can overlap an explicit camera list/snapshot. Some Windows
    camera drivers corrupt the native heap when two OpenCV handles open the
    same device concurrently, which otherwise takes down the whole session
    agent with a C-level access violation.
    """
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        with _camera_lock:
            return fn(*args, **kwargs)
    return wrapped


def _open_cam(index, probe=False):
    """Open a camera, trying Windows backends in reliability order.

    Field evidence (Acer, 2026-09, session_console.log): the default
    backend (CAP_ANY) fails on modern opencv-python wheels — FFMPEG
    warns about libavdevice and the obsensor plugin spams "Camera index
    out of range". DirectShow opens most laptop webcams reliably."""
    import cv2
    _quiet_cv(cv2)
    idx = int(index)
    if not probe:  # camera.list probes must not poison the 60 s cooldown
        left = _cooldown_left(idx)
        if left > 0:
            raise RuntimeError(f"camera {idx} known-missing - cooldown {left}s left "
                               "(not re-probing a missing camera every poll)")
    if os.name == "nt":
        order = [("dshow", cv2.CAP_DSHOW), ("msmf", cv2.CAP_MSMF),
                 ("default", cv2.CAP_ANY)]
    else:
        order = [("default", cv2.CAP_ANY)]
    last_err = None
    for name, be in order:
        try:
            cap = cv2.VideoCapture(idx, be)
            if cap.isOpened():
                try:  # FOURCC BEFORE resolution - DirectShow drops it after
                    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
                except Exception:
                    pass
                ok = False
                for _ in range(10):  # first read can be False: sensor wake-up
                    ok, _frame = cap.read()
                    if ok and _frame is not None and _frame.size > 0:
                        break
                    time.sleep(0.15)
                if ok:
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, _cam.get("width", 960))
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, round(_cam.get("width", 960) * 9 / 16))
                    return cap
                last_err = RuntimeError(f"camera {idx} opened via {name} but returns no frames")
            try:
                cap.release()
            except Exception:
                pass
        except Exception as e:
            last_err = e
    _cooldown_start(idx)
    raise RuntimeError(
        f"no working camera at index {idx} (tried DirectShow, MSMF, default)"
        + (f"; last error: {last_err}" if last_err else "")
        + " — connect a webcam or pick another index")


@_camera_serialized
def _capture_still_locked(index):
    """Capture one frame under the same device lock as every camera API."""
    import cv2
    import numpy as np
    if _cam["on"]:
        raw = _cam.get("last_jpeg")
        if not raw:
            raise RuntimeError("camera stream is starting; try the login capture again shortly")
        frame = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise RuntimeError("could not decode the latest camera frame")
        return frame
    if globals().get("_motion", {}).get("on") or globals().get("_rec", {}).get("on"):
        raise RuntimeError("camera is busy with motion detection or recording")
    cap = _open_cam(index)
    try:
        frame = None
        for _ in range(8):
            ok, frame = cap.read()
            if ok and frame is not None:
                return frame
            time.sleep(0.05)
        raise RuntimeError("camera opened but did not return a usable frame")
    finally:
        cap.release()


def capture_still(index=0):
    """Use a current live frame, otherwise serialize a one-shot camera open.

    Reading the immutable JPEG reference is safe while the stream thread owns
    the device. Never open a second native OpenCV handle beside that thread.
    """
    if _cam["on"]:
        raw = _cam.get("last_jpeg")
        if raw:
            import cv2
            import numpy as np
            frame = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
            if frame is not None:
                return frame
        raise RuntimeError("camera stream is warming up; try the capture again shortly")
    return _capture_still_locked(int(index))


def _cam_loop(ctx):
    """SINGLE OWNER of the camera while live.

    Research-backed: the #1 cause of "detect works, live errors" is
    opening the device twice (preflight open+release, then the loop
    reopens before the driver fully released it). This loop opens ONCE
    with retries, owns the handle, broadcasts real state via a
    camera_live event, and keeps the latest JPEG in _cam["last_jpeg"]
    so snapshots / motion / login-capture reuse it instead of stealing
    the device."""
    import cv2
    cap = None
    try:
        last_err = None
        for _attempt in range(5):
            try:
                # Serialize native driver opens, but do not hold the global
                # lock for the whole live stream: snapshots should read the
                # cached JPEG while this stream owns its device handle.
                with _camera_lock:
                    cap = _open_cam(_cam["index"])
                break
            except Exception as e:
                last_err = e
                time.sleep(1.6)
        if cap is None:
            _cam["on"] = False
            ctx.send_event("camera_live", {"ok": False, "error": str(last_err)})
            return
        try:
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        except Exception:
            pass
        interval = 1.0 / max(1, _cam["fps"])
        misses = 0
        ctx.send_event("camera_live", {"ok": True, "index": _cam["index"],
                                       "fps": _cam["fps"], "quality": _cam["quality"]})
        while _cam["on"]:
            t0 = time.time()
            with _camera_lock:
                ok, frame = cap.read()
            if not ok:
                misses += 1
                if misses > 30:
                    ctx.send_error("camera", "camera keeps returning empty frames — stopping")
                    ctx.send_event("camera_live", {"ok": False,
                                                   "error": "camera stopped delivering frames"})
                    break
                time.sleep(0.5)
                continue
            misses = 0
            okj, enc = cv2.imencode(".jpg", frame,
                                    [int(cv2.IMWRITE_JPEG_QUALITY), _cam["quality"]])
            if not okj:
                raise RuntimeError("camera JPEG encode failed")
            _cam["last_jpeg"] = enc.tobytes()
            ctx.send_binary(2, enc.tobytes())
            wait = interval - (time.time() - t0)
            if wait > 0:
                time.sleep(wait)
    except Exception as e:
        ctx.send_error("camera", str(e))
        ctx.send_event("camera_live", {"ok": False, "error": str(e)})
    finally:
        if cap is not None:
            with _camera_lock:
                cap.release()
        _cam["on"] = False
        _cam["last_jpeg"] = None


def stop():
    _cam["on"] = False



# ---------- mini CCTV (round 14): motion detection + timed recording ----------
_motion = {"on": False, "th": None}
_rec = {"on": False, "th": None}


def _captures_dir():
    import os
    try:
        from ..common import data_dir  # HKS_DATA_DIR or <client>/data
        base = data_dir()
    except Exception:
        base = os.environ.get("HKS_DATA_DIR") or os.path.expanduser("~")
    d = os.path.join(base, "captures")
    os.makedirs(d, exist_ok=True)
    return d


def _motion_loop(ctx):
    import cv2
    import numpy as np
    cap = None
    try:
        with _camera_lock:
            cap = _open_cam(_motion["index"])
        prev = None
        interval = max(0.4, float(_motion.get("interval", 1.0)))
        sens = float(_motion.get("sensitivity", 18))  # mean-abs-diff threshold 5..60
        while _motion["on"]:
            t0 = time.time()
            with _camera_lock:
                ok, frame = cap.read()
            if not ok:
                time.sleep(0.5)
                continue
            small = cv2.resize(frame, (320, 180))
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype("int16")
            if prev is not None:
                diff = float(np.abs(gray - prev).mean())
                if diff >= sens:
                    fn = "motion_" + time.strftime("%Y%m%d_%H%M%S") + ".jpg"
                    fp = os.path.join(_captures_dir(), fn)
                    cv2.imwrite(fp, frame)
                    ctx.send_event("motion_detected",
                                   {"file": fn, "score": round(diff, 1),
                                    "ts": time.time()})
            prev = gray
            wait = interval - (time.time() - t0)
            if wait > 0:
                time.sleep(wait)
    except Exception as e:
        ctx.send_error("camera", f"motion loop: {e}")
    finally:
        if cap is not None:
            with _camera_lock:
                cap.release()
        _motion["on"] = False


def _record_loop(ctx, seconds, index, fps):
    import cv2
    cap = None
    try:
        with _camera_lock:
            cap = _open_cam(index)
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 960)
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 540)
        fn = "cctv_" + time.strftime("%Y%m%d_%H%M%S") + ".mp4"
        fp = os.path.join(_captures_dir(), fn)
        wr = cv2.VideoWriter(fp, cv2.VideoWriter_fourcc(*"mp4v"),
                             max(1, fps), (w, h))
        t_end = time.time() + max(5, min(int(seconds), 3600))
        t_next = time.time()
        while time.time() < t_end and _rec["on"]:
            with _camera_lock:
                ok, frame = cap.read()
            if ok:
                wr.write(frame)
            t_next += 1.0 / max(1, fps)   # pace by absolute time - cap.read()
            d = t_next - time.time()      # itself eats 30 ms/frame otherwise
            if d > 0:
                time.sleep(d)
        wr.release()
        ctx.send_event("cctv_record_done", {"file": fn, "seconds": seconds})
    except Exception as e:
        ctx.send_error("camera", f"record: {e}")
        ctx.send_event("cctv_record_done", {"error": str(e)})
    finally:
        if cap is not None:
            with _camera_lock:
                cap.release()
        _rec["on"] = False

def register(ctx):
    @_camera_serialized
    def cam_list(c, p):
        """Report active cameras without probing native drivers repeatedly.

        Repeated VideoCapture open/read/release during enumeration triggered
        Windows heap-corruption exits on this laptop. Check a camera only when
        the user explicitly starts a stream or requests a snapshot.
        """
        # A live stream already owns the camera; don't enumerate/open a
        # second handle while it is active.
        if _cam["on"]:
            idx = int(_cam["index"])
            res = {"items": [idx], "detail": [{"index": idx, "active": True}]}
            return res
        if _motion["on"] or _rec["on"]:
            idx = int(_motion.get("index", _cam["index"]) if _motion["on"] else _cam["index"])
            mode = "recording" if _rec["on"] else "motion"
            return {"items": [idx], "detail": [{"index": idx, "active": True, "mode": mode}]}
        if _list_cache["ts"] and time.time() - _list_cache["ts"] < 300:
            return _list_cache["res"]
        res = {"items": [], "detail": [], "probe_skipped": True,
               "hint": "Camera status is checked when you start a stream or take a snapshot. Repeated background hardware probing is disabled to protect the desktop agent from this Windows camera driver's native crash."}
        _list_cache["ts"], _list_cache["res"] = time.time(), res
        return res

    @_camera_serialized
    def cam_stream(c, p):
        """Start/stop live. The LOOP opens the device (single owner) and
        reports the truth via the camera_live event; if another app
        (Zoom/Teams/antivirus webcam shield) holds the camera the user
        gets that reason, not silence."""
        if p.get("on") and (_motion["on"] or _rec["on"]):
            return {"ok": False, "error": "Motion detection or recording is using the camera - stop it first"}
        _cam["on"] = bool(p.get("on", False))
        _cam["index"] = int(p.get("index", c.cfg.get("camera_index", _cam["index"])))
        if p.get("fps"):
            _cam["fps"] = max(1, min(int(p["fps"]), 30))
        if p.get("quality"):
            _cam["quality"] = max(30, min(int(p["quality"]), 95))
        if p.get("width"):
            _cam["width"] = max(320, min(int(p["width"]), 1920))
        # (stop is registered ONCE in register() below - per-start appends
        #  grew the hook list forever. audit3)
        global _thread
        if _cam["on"] and (_thread is None or not _thread.is_alive()):
            _thread = threading.Thread(target=_cam_loop, args=(c,), daemon=True, name="hks-cam")
            _thread.start()
            return {"streaming": True, "note": "opening the camera — frames start in ~1-2 s"}
        if not _cam["on"]:
            _cam["last_jpeg"] = None
        return {"streaming": _cam["on"]}

    @_camera_serialized
    def cam_snap(c, p):
        # while LIVE is on, reuse the stream's last frame — opening a
        # second handle steals the device and kills the stream
        if _cam["on"]:
            if _cam.get("last_jpeg"):
                return {"b64": base64.b64encode(_cam["last_jpeg"]).decode(), "from_live": True}
            raise RuntimeError("camera stream is warming up; try the snapshot again shortly")
        if _motion["on"] or _rec["on"]:
            raise RuntimeError("camera is busy with motion detection or recording; stop it before taking a separate snapshot")
        cap = _open_cam(p.get("index", _cam["index"]))
        try:
            # 7.2.55: 8 warmup frames (6 was not enough on some USB webcams -
            # the first frames after open can be uninitialized buffer noise)
            ok = False
            frame = None
            for _ in range(8):
                ok, frame = cap.read()
                if not ok:
                    time.sleep(0.05)
            if not ok or frame is None:
                raise RuntimeError("camera frame failed")
            okc, enc = cv2_encode(frame)
            return {"b64": base64.b64encode(enc.tobytes()).decode()}
        finally:
            cap.release()

    @_camera_serialized
    def camera_motion(c, p):
        """Motion detection (mini CCTV). on=true starts, on=false stops."""
        if p.get("on") and _cam["on"]:
            return {"ok": False, "error": "Live camera is ON - stop it first (Live and Motion cannot share one camera)"}
        on = bool(p.get("on", False))
        if on and not _motion["on"]:
            _motion["index"] = int(p.get("index", _cam["index"]))
            _motion["interval"] = p.get("interval", 1.0)
            _motion["sensitivity"] = p.get("sensitivity", 18)
            _motion["on"] = True
            _motion["th"] = threading.Thread(target=_motion_loop, args=(c,),
                                             daemon=True, name="hks-motion")
            _motion["th"].start()
            return {"motion": True, "sensitivity": _motion["sensitivity"]}
        if not on:
            _motion["on"] = False
            return {"motion": False}
        return {"motion": True}

    @_camera_serialized
    def camera_record(c, p):
        """Record the camera for N seconds (5..3600) into an mp4 clip."""
        if _cam["on"]:
            return {"ok": False, "error": "Live camera is ON - stop it first (Live and Recording cannot share one camera)"}
        if _rec["on"]:
            return {"ok": False, "error": "already recording"}
        _rec["on"] = True
        seconds = max(5, min(int(p.get("seconds", 300)), 3600))
        _rec["th"] = threading.Thread(target=_record_loop,
                                      args=(c, seconds, int(p.get("index", _cam["index"])),
                                            max(5, min(int(p.get("fps", 15)), 30))),
                                      daemon=True, name="hks-cctv-rec")
        _rec["th"].start()
        return {"recording": True, "seconds": seconds}

    def camera_record_stop(c, p):
        _rec["on"] = False
        return {"stopped": True}

    hooks = getattr(ctx, "disconnect_hooks", None)
    if hooks is not None:
        hooks.append(stop)  # ONCE per process (audit3 - the old
    # code appended a fresh stop on every stream start, same bug as screen)
    return {"camera.list": cam_list, "camera.stream": cam_stream, "camera.snap": cam_snap,
            "camera.motion": camera_motion, "camera.record": camera_record,
            "camera.record_stop": camera_record_stop}


def cv2_encode(frame):
    import cv2
    ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
    if not ok:
        raise RuntimeError("encode failed")
    return ok, buf


def register_mic(ctx):
    def mic_list(c, p):
        import sounddevice as sd
        try:
            default_in = sd.default.device[0]
        except Exception:
            default_in = None
        items = []
        for i, d in enumerate(sd.query_devices()):
            if d.get("max_input_channels", 0) > 0:
                items.append({"i": i, "name": d["name"][:60],
                              "rate": int(float(d.get("default_samplerate") or 0)),
                              "default": (i == default_in)})  # Stereo Mix can be
        return {"items": items[:20], "default": default_in}    # the OS default!

    def mic_record(c, p):
        import wave
        import numpy as np
        import sounddevice as sd
        from . import desktop as _desk
        secs = min(float(p.get("seconds", 6)), 60)
        dev = p.get("device")
        # record at the DEVICE rate (16k request fails on 48k hardware with
        # PortAudio -9997), then resample to the 16k WAV (audit3)
        sr = _desk._native_rate(sd, "input", int(dev) if dev is not None else None)
        audio = sd.rec(int(secs * sr), samplerate=sr, channels=1, dtype="int16",
                       device=int(dev) if dev is not None else None)
        sd.wait()
        pcm = _desk._resample(np, audio.reshape(-1), sr, 16000)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(pcm.tobytes())
        c.send_event("mic_clip", {"seconds": secs})
        return {"b64": base64.b64encode(buf.getvalue()).decode(), "seconds": secs,
                "rate": 16000}

    return {"mic.list": mic_list, "mic.record": mic_record}
