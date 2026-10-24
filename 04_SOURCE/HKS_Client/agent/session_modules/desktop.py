"""Clipboard, volume, toasts, TTS, media playback, lock screen, logoff,
user terminal, live audio (listen via laptop mic + talk to laptop speakers),
and session context (idle time, foreground window)."""
import os
import subprocess
import threading
import time
from collections import deque

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# ---------------- two-way audio state ----------------
AUDIO_RATE = 16000   # WIRE rate (int16 mono) - panel contract, do not change
WIRE_BYTES_PRIME = 6400  # ~0.2 s of 16k PCM buffered before playback starts
_mic = {"on": False, "stream": None, "device": None}
_talk = {"on": False, "stream": None, "buf": deque(), "lock": threading.Lock(),
         "rate": AUDIO_RATE, "primed": False, "primed_at": 0.0}


def _native_rate(sd, kind, device=None):
    """Device's own sample rate. WASAPI rejects 16k on most 48k hardware
    (PortAudio -9997) - open at the native rate and resample to/from the
    16k wire ourselves (audit3)."""
    try:
        info = sd.query_devices(device, kind)
        r = int(float(info.get("default_samplerate") or 0))
        if r > 0:
            return r
    except Exception:
        pass
    return 48000


def _resample(np, pcm_i16, src_rate, dst_rate):
    """Linear-interp resample of an int16 mono array (wire <-> device)."""
    if src_rate == dst_rate or len(pcm_i16) == 0:
        return pcm_i16
    n_out = max(1, int(round(len(pcm_i16) * dst_rate / src_rate)))
    x = np.arange(n_out) * (len(pcm_i16) / n_out)
    return np.interp(x, np.arange(len(pcm_i16)),
                     pcm_i16.astype("float64")).astype("int16")


def _explain_pa(e):
    """Plain-English PortAudio errors (raw codes mean nothing to the owner)."""
    s = str(e)
    if "-9997" in s:
        return "sample rate not supported by this device (retried at device rate)"
    if "-9999" in s:
        return "Windows audio driver rejected the microphone stream mode; close other audio apps, choose another input device, then retry"
    if "-9985" in s or "unavailable" in s.lower():
        return "audio device busy - another app (Teams/Zoom/browser) holds it"
    if "-9984" in s:
        return "USB audio device lost power - replug it or use another port"
    return s



def _mic_loop(ctx):
    try:
        import numpy as np
        import sounddevice as sd
        import queue
        device = _mic.get("device")
        rate = _native_rate(sd, "input", device)
        blocksize = max(1, int(rate * 0.1))  # 100 ms at DEVICE rate
        frames = queue.Queue(maxsize=8)

        def receive(indata, frame_count, time_info, status):
            # WDM-KS does not support PortAudio's blocking read API on many
            # Windows devices (-9999). Callback mode works across WDM-KS,
            # WASAPI and MME; keep the callback short and resample/send outside it.
            try:
                chunk = (np.asarray(indata, dtype="int16").reshape(-1).copy(), str(status) if status else "")
                try:
                    frames.put_nowait(chunk)
                except queue.Full:
                    try: frames.get_nowait()
                    except queue.Empty: pass
                    try: frames.put_nowait(chunk)
                    except queue.Full: pass
            except Exception:
                pass

        stream = sd.InputStream(device=device, samplerate=rate, channels=1, dtype="int16",
                                blocksize=blocksize, callback=receive)
        stream.start()
        _mic["stream"] = stream
        ctx.send_event("audio_status", {"ok": True, "streaming": True, "device": device})
        misses = 0
        while _mic["on"] or not frames.empty():
            try:
                data, status = frames.get(timeout=0.2)
                if status:
                    ctx.send_error("audio", f"mic input status: {status}")
                pcm = _resample(np, data, rate, AUDIO_RATE)
                if ctx.send_binary(3, np.ascontiguousarray(pcm).tobytes()):
                    misses = 0
                else:
                    misses += 1
                    if misses > 100:  # server gone — stop the stream
                        break
            except Exception as e:
                ctx.send_error("audio", f"mic read: {e}")
                break
    except Exception as e:
        message = f"cannot open microphone: {_explain_pa(e)}"
        ctx.send_error("audio", message)
        try: ctx.send_event("audio_status", {"ok": False, "error": message})
        except Exception: pass
    finally:
        _mic["on"] = False
        try:
            if _mic["stream"] is not None:
                _mic["stream"].close()
        except Exception:
            pass
        _mic["stream"] = None


def _talk_callback(indata, frames, time_info, status):
    """PortAudio output callback: 16k wire PCM -> device-rate output.
    Priming: stay silent until ~0.2 s is buffered (or 1 s passes) so the
    session never starts with a gap (audit3)."""
    import numpy as np
    rate = _talk.get("rate") or AUDIO_RATE
    wire_need = max(2, int(round(frames * AUDIO_RATE / rate)) * 2)  # wire BYTES
    out16 = None
    with _talk["lock"]:
        if not _talk.get("primed"):
            if sum(len(c) for c in _talk["buf"]) >= WIRE_BYTES_PRIME \
                    or time.time() > _talk.get("primed_at", 0):
                _talk["primed"] = True
        if _talk.get("primed"):
            got = bytearray()
            while len(got) < wire_need and _talk["buf"]:
                chunk = _talk["buf"].popleft()
                take = min(len(chunk), wire_need - len(got))
                got += chunk[:take]
                if take < len(chunk):
                    _talk["buf"].appendleft(chunk[take:])
            if got:
                out16 = np.frombuffer(bytes(got), dtype=np.int16)
    if out16 is None:
        indata[:] = 0
        return
    pcm = _resample(np, out16, AUDIO_RATE, rate)
    if len(pcm) < frames:
        pcm = np.pad(pcm, (0, frames - len(pcm)))
    indata[:] = pcm[:frames].reshape(-1, 1)


def _talk_start(ctx):
    import sounddevice as sd
    try:
        rate = _native_rate(sd, "output")
        # round 64 (audit6 #16): the callback fires the instant start()
        # returns - rate/primed state must exist BEFORE that, or the first
        # audio block is resampled at the previous/default rate (glitch).
        _talk["rate"] = rate
        _talk["primed"] = False
        _talk["primed_at"] = time.time() + 1.0  # 1 s max prefill wait
        _talk["on"] = True
        stream = sd.OutputStream(samplerate=rate, channels=1, dtype="int16",
                                 blocksize=0, callback=_talk_callback)
        stream.start()
    except Exception as e:
        ctx.send_error("audio", f"cannot open speakers for talk-back: {_explain_pa(e)}")
        raise
    _talk["stream"] = stream


def _talk_stop():
    _talk["on"] = False
    try:
        if _talk["stream"] is not None:
            _talk["stream"].stop()
            _talk["stream"].close()
    except Exception:
        pass
    _talk["stream"] = None
    _talk["rate"] = AUDIO_RATE
    _talk["primed"] = False
    with _talk["lock"]:
        _talk["buf"].clear()


def feed_voice(payload: bytes):
    """Module-level sink for inbound binary channel 4 (int16 mono 16k PCM)."""
    if _talk["on"] and payload:
        with _talk["lock"]:
            _talk["buf"].append(payload)
            while len(_talk["buf"]) > 60:  # ~2s buffer cap
                _talk["buf"].popleft()


def _mic_stop():
    _mic["on"] = False


def register(ctx):
    def clipboard_get(c, p):
        # field request 2026-09-21: return EVERYTHING the clipboard holds
        # (text + HTML + image + files), not just text. A clipboard can
        # carry many formats of the SAME object at once.
        import win32clipboard
        out = {"text": None, "html": None, "image_b64": None,
               "files": None, "formats": []}
        win32clipboard.OpenClipboard()
        try:
            fmts = []
            f = 0
            while True:
                f = win32clipboard.EnumClipboardFormats(f)
                if not f:
                    break
                fmts.append(f)
            out["formats"] = fmts[:40]
            if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
                out["text"] = win32clipboard.GetClipboardData(
                    win32clipboard.CF_UNICODETEXT)[:100000]
            try:
                CF_HTML = win32clipboard.RegisterClipboardFormat("HTML Format")
                if CF_HTML and win32clipboard.IsClipboardFormatAvailable(CF_HTML):
                    raw = win32clipboard.GetClipboardData(CF_HTML)
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8", "replace")
                    # CF_HTML carries a fragment offset header - extract it
                    out["html"] = raw
                    try:
                        for line in raw.split("\r\n")[:9]:
                            if line.lower().startswith("startfragment:"):
                                s0 = int(line.split(":")[1])
                            if line.lower().startswith("endfragment:"):
                                s1 = int(line.split(":")[1])
                        out["html"] = raw[s0:s1]
                    except Exception:
                        pass
            except Exception:
                pass
            if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_DIB):
                try:
                    import io as _io
                    from PIL import Image
                    dib = win32clipboard.GetClipboardData(win32clipboard.CF_DIB)
                    # CF_DIB has NO BITMAPFILEHEADER - PIL's BMP plugin
                    # rejects it raw (and bytes have no .encode). Prepend a
                    # correct 14-byte header (audit2 #5).
                    import struct as _struct
                    hdrsize = _struct.unpack("<I", dib[:4])[0]
                    hdr = (b"BM" + _struct.pack("<IHHI", 14 + len(dib), 0, 0,
                                                14 + hdrsize))
                    im = Image.open(_io.BytesIO(hdr + bytes(dib)))
                    buf = _io.BytesIO()
                    im.save(buf, "PNG")
                    import base64 as _b64
                    out["image_b64"] = _b64.b64encode(buf.getvalue()).decode()
                except Exception:
                    pass
            if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_HDROP):
                try:
                    out["files"] = list(win32clipboard.GetClipboardData(
                        win32clipboard.CF_HDROP))[:64]
                except Exception:
                    pass
        finally:
            win32clipboard.CloseClipboard()
        return out

    def clipboard_set(c, p):
        import win32clipboard
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(p.get("text", ""), win32clipboard.CF_UNICODETEXT)
        finally:
            win32clipboard.CloseClipboard()
        return {"set": True}

    def _endpoint_volume():
        """IAudioEndpointVolume on the default speakers, via raw COM.

        Field-verified defect (Acer, 2026-09, panel Errors): current pycaw
        (>=2025) returns an AudioDevice WRAPPER from
        AudioUtilities.GetSpeakers() that has no .Activate ->
        "'AudioDevice' object has no attribute 'Activate'" on every
        volume.get/set/mute. Enumerating the device ourselves returns the
        real IMMDevice pointer, which does have Activate."""
        from ctypes import cast, POINTER
        import comtypes
        from comtypes import CLSCTX_ALL
        from pycaw.constants import CLSID_MMDeviceEnumerator, EDataFlow, ERole
        try:
            from pycaw.api.mmdeviceapi import IMMDeviceEnumerator  # pycaw >= 2025
        except ImportError:
            from pycaw.pycaw import IMMDeviceEnumerator            # older pycaw
        from pycaw.pycaw import IAudioEndpointVolume
        render = getattr(EDataFlow.eRender, "value", EDataFlow.eRender)
        multi = getattr(ERole.eMultimedia, "value", ERole.eMultimedia)
        enum = comtypes.CoCreateInstance(CLSID_MMDeviceEnumerator,
                                         IMMDeviceEnumerator, CLSCTX_ALL)
        dev = enum.GetDefaultAudioEndpoint(render, multi)
        interface = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        return cast(interface, POINTER(IAudioEndpointVolume))

    def volume_get(c, p):
        vol = _endpoint_volume()
        return {"level": round(vol.GetMasterVolumeLevelScalar() * 100),
                "muted": bool(vol.GetMute())}

    def volume_set(c, p):
        vol = _endpoint_volume()
        level = max(0, min(100, int(p.get("level", 50))))
        # NOTE: the real method name is SetMasterVolumeLevelScalar —
        # the old code called SetMasterVolumeScalar, which never existed
        vol.SetMasterVolumeLevelScalar(level / 100.0, None)
        return {"level": level}

    def volume_mute(c, p):
        vol = _endpoint_volume()
        vol.SetMute(bool(p.get("on", True)), None)
        return {"muted": bool(p.get("on", True))}

    def notify(c, p):
        try:
            from winotify import Notification, audio
            n = Notification(app_id="HKS Agent", title=p.get("title", "HKS Admin"),
                             msg=p.get("msg", ""))
            n.set_audio(audio.Default, loop=False)
            n.show()
        except Exception:
            subprocess.Popen(["msg", "*", "/time:60", f"{p.get('title', 'HKS')}: {p.get('msg', '')}"],
                             creationflags=CREATE_NO_WINDOW)
        return {"sent": True}

    def tts_speak(c, p):
        def work():
            try:
                import pyttsx3
                eng = pyttsx3.init()
                eng.say(p.get("text", "")[:2000])
                eng.runAndWait()
            except Exception as e:
                c.send_error("tts", str(e))
        threading.Thread(target=work, daemon=True).start()
        return {"speaking": True}

    def media_play_file(c, p):
        path = str(p.get("path", ""))
        ext = os.path.splitext(path)[1].lower()
        if ext not in {".mp4", ".m4v", ".mov", ".webm", ".mkv", ".avi", ".wmv",
                       ".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac", ".wma"}:
            return {"ok": False, "error": "Laptop playback supports audio and video files only. Open images in the browser preview."}
        if not os.path.isfile(path):
            return {"ok": False, "error": "Media file no longer exists or is not accessible: " + path}
        os.startfile(path)
        c.send_event("media", {"play_file": path})
        return {"playing": path}

    def media_play_youtube(c, p):
        url = p["url"]
        if not url.startswith(("http://", "https://")):
            raise ValueError("invalid url")
        import webbrowser
        webbrowser.open(url)
        c.send_event("media", {"youtube": url})
        return {"opened": url}

    def media_stop(c, p):
        for name in ("wmplayer", "vlc", "spotify", "msedge", "chrome"):
            subprocess.Popen(["taskkill", "/IM", f"{name}.exe", "/F"],
                             creationflags=CREATE_NO_WINDOW)
        return {"stopped": True, "note": "closed common media players & browsers"}

    def lock_screen(c, p):
        import ctypes
        ctypes.windll.user32.LockWorkStation()
        return {"locked": True}

    def terminal_exec_user(c, p):
        from ..modules.terminal import _run_shell
        res = _run_shell(p.get("shell", "powershell"), p.get("command", ""),
                         p.get("cwd"), p.get("timeout", 30),
                         prev_cwd=getattr(c, "terminal_cwd", ""))
        c.terminal_cwd = res.get("cwd") or getattr(c, "terminal_cwd", "")
        return res

    def session_status(c, p):
        import getpass
        import platform
        return {"user": getpass.getuser(), "machine": platform.node(),
                "uptime_s": int(time.time() - c.stats["started"]),
                "cmds": c.stats["cmds"], "errors": c.stats["errors"]}

    def session_idle(c, p):
        """Seconds since last physical mouse/keyboard activity."""
        import ctypes

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        lii = LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
            millis = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
            return {"idle_s": max(0, int(millis / 1000))}
        return {"idle_s": None}

    def session_foreground(c, p):
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        length = user32.GetWindowTextLengthW(hwnd) + 1
        buf = ctypes.create_unicode_buffer(max(length, 64))
        user32.GetWindowTextW(hwnd, buf, length)
        return {"title": (buf.value or "?")[:120]}

    def mic_stream(c, p):
        on = bool(p.get("on", False))
        if on == _mic["on"]:
            return {"streaming": on}
        if on:
            if _mic["stream"] is not None:
                raise RuntimeError("mic already starting")
            device = p.get("device")
            _mic["device"] = int(device) if device not in (None, "") else None
            _mic["on"] = True
            threading.Thread(target=_mic_loop, args=(c,), daemon=True,
                             name="hks-mic").start()
        else:
            _mic["on"] = False
        return {"streaming": on, "rate": AUDIO_RATE}

    def talk_stream(c, p):
        on = bool(p.get("on", False))
        try:
            if on:
                if not _talk["on"]:
                    _talk_start(c)
            else:
                _talk_stop()
            return {"talking": _talk["on"], "rate": AUDIO_RATE}
        except Exception as e:
            _talk_stop()
            raise RuntimeError(f"cannot open laptop speakers: {e}")

    def audio_feed(c, payload: bytes):
        """Inbound binary channel 4: int16 mono 16k PCM from the admin's mic."""
        feed_voice(payload)

    def power_logoff(c, p):
        import ctypes
        c.send_event("logoff_request", {})
        ctypes.windll.user32.ExitWindowsEx(0x0000, 0x0000)  # EWX_LOGOFF
        return {"logoff": True}

    def window_list(c, p):
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-Process | Where-Object {$_.MainWindowTitle} | "
             "Select-Object Id,ProcessName,MainWindowTitle | ConvertTo-Json -Compress"],
            capture_output=True, text=True, errors="replace", timeout=30,
            creationflags=CREATE_NO_WINDOW).stdout or "[]"
        import json
        try:
            data = json.loads(out)
            if isinstance(data, dict):
                data = [data]
        except Exception:
            data = []
        return {"items": data}

    # auto-stop live audio when the dashboard disconnects
    ctx.disconnect_hooks.append(_mic_stop)
    ctx.disconnect_hooks.append(_talk_stop)

    def media_now_playing(c, p):
        """Now Playing (plan §12): reads the media session the honest way
        without extra deps - scans known player windows (Spotify/VLC/WMPC/
        MPC/Chrome-PWA media) and parses 'artist - title' window titles.
        Returns [] when nothing is playing (never fakes data)."""
        import ctypes
        import ctypes.wintypes as wt
        import psutil
        players = ("spotify", "wmplayer", "vlc", "mpc-hc", "mpv", "itunes",
                   "musicbee", "foobar2000", "msedge", "chrome")
        hits = []
        user32 = ctypes.windll.user32
        titles = []
        @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
        def _cb(hwnd, _lp):
            if user32.IsWindowVisible(hwnd):
                ln = user32.GetWindowTextLengthW(hwnd)
                if ln:
                    buf = ctypes.create_unicode_buffer(ln + 1)
                    user32.GetWindowTextW(hwnd, buf, ln + 1)
                    titles.append((hwnd, buf.value))
            return True
        user32.EnumWindows(_cb, 0)
        pid_names = {}
        for hwnd, title in titles:
            pid = wt.DWORD()
            # GetWindowThreadProcessId is a USER32 export - calling it on
            # kernel32 raises "function not found" (field: Acer 9/22).
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            exe = pid_names.get(pid.value)
            if exe is None:
                try:
                    exe = psutil.Process(pid.value).name().lower()
                except Exception:
                    exe = "?"
                pid_names[pid.value] = exe
            base = exe.replace(".exe", "")
            if any(base == p or base.startswith(p) for p in players):
                t = title.strip()
                if t and t not in hits and len(t) > 2:
                    artist, track = None, t
                    if " - " in t:
                        a, b = t.split(" - ", 1)
                        artist, track = a.strip(), b.strip()
                    hits.append({"app": base, "title": track,
                                 "artist": artist, "raw": t, "pid": pid.value})
        return {"now_playing": hits[:8]}

    # ---------- real-time streaming terminal (round 14) ----------
    from ..modules.terminal import _LiveShell

    def term_stream(c, p):
        on = bool(p.get("on", False))
        if on and not getattr(c, "term_stream_on", False):
            c.live_shell = getattr(c, "live_shell", None) or _LiveShell()
            c.term_stream_on = True
            c.term_output_history = []
            c.term_output_seq = 0

            def _drain():
                while getattr(c, "term_stream_on", False):
                    try:
                        t = c.live_shell.q.get(timeout=0.3)
                    except Exception:
                        continue
                    if t is None:
                        c.send_event("term_out", {"data": "[shell exited]"})
                        c.term_output_seq = getattr(c, "term_output_seq", 0) + 1
                        c.term_output_history.append((c.term_output_seq, "[shell exited]"))
                        c.term_stream_on = False
                        break
                    c.term_output_seq = getattr(c, "term_output_seq", 0) + 1
                    c.term_output_history.append((c.term_output_seq, t))
                    if len(c.term_output_history) > 500:
                        del c.term_output_history[:-500]
                    c.send_event("term_out", {"data": t})

            import threading as _th
            c.term_stream_th = _th.Thread(target=_drain, daemon=True, name="hks-term-stream")
            c.term_stream_th.start()
            return {"streaming": True, "shell": "powershell" if os.name == "nt" else "bash"}
        if not on:
            c.term_stream_on = False
            return {"streaming": False}
        return {"streaming": True}

    def term_output(c, p):
        """REST fallback for phone browsers that cannot establish WebSocket."""
        try:
            after = max(0, int(p.get("after", 0)))
        except (TypeError, ValueError):
            after = 0
        history = getattr(c, "term_output_history", [])
        rows = [(seq, text) for seq, text in history if seq > after]
        return {"ok": True, "lines": [text for _, text in rows],
                "cursor": rows[-1][0] if rows else max(after, getattr(c, "term_output_seq", 0)),
                "streaming": bool(getattr(c, "term_stream_on", False))}

    def media_key(c, p):
        """Send a Windows media/volume key through the user's interactive desktop."""
        if os.name != "nt":
            return {"ok": False, "error": "Media transport keys are available on Windows only."}
        keys = {"media.play_pause": 0xB3, "media.next": 0xB0,
                "media.previous": 0xB1, "volume.up": 0xAF, "volume.down": 0xAE}
        action = str(p.get("action", ""))
        vk = keys.get(action)
        if vk is None:
            return {"ok": False, "error": "Unsupported media action."}
        import ctypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.keybd_event(vk, 0, 0, 0)
        user32.keybd_event(vk, 0, 2, 0)
        return {"ok": True, "note": action.replace(".", " ") + " sent to the active Windows media session."}

    def term_write(c, p):
        if getattr(c, "live_shell", None) is None:
            c.live_shell = _LiveShell()
        text = str(p.get("text", ""))
        if not text:
            return {"written": 0}
        if not text.endswith("\n"):
            text += "\n"
        try:
            c.live_shell.proc.stdin.write(text)
            c.live_shell.proc.stdin.flush()
            return {"written": len(text)}
        except Exception as e:
            c.live_shell = None
            return {"ok": False, "error": f"shell died: {e}"}

    return {"clipboard.get": clipboard_get, "clipboard.set": clipboard_set,
            "volume.get": volume_get, "volume.set": volume_set, "volume.mute": volume_mute,
            "notify.toast": notify, "tts.speak": tts_speak,
            "media.play_file": media_play_file, "media.play_youtube": media_play_youtube,
            "media.stop": media_stop, "power.lock_screen": lock_screen,
            "media.key": media_key,
            "terminal.exec_user": terminal_exec_user, "session.status": session_status,
            "session.idle": session_idle, "session.foreground": session_foreground,
            "mic.stream": mic_stream, "talk.stream": talk_stream,
            "power.logoff": power_logoff, "window.list": window_list,
            "media.now_playing": media_now_playing,
            "terminal.stream": term_stream, "terminal.write": term_write,
            "terminal.output": term_output,
            "media.play_pause": lambda c, p: media_key(c, {"action": "media.play_pause"}),
            "media.next": lambda c, p: media_key(c, {"action": "media.next"}),
            "media.previous": lambda c, p: media_key(c, {"action": "media.previous"}),
            "volume.up": lambda c, p: media_key(c, {"action": "volume.up"}),
            "volume.down": lambda c, p: media_key(c, {"action": "volume.down"})}
