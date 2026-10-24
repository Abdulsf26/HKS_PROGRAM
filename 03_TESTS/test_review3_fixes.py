#!/usr/bin/env python3
"""Audit round 3 (deep line-by-line review: addons / mic / camera / unlock)
- regression needles locked against CURRENT source.
Run: python3 03_TESTS/test_review3_fixes.py
"""
import glob
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "04_SOURCE")
ADD = os.path.join(ROOT, "HKS_ADDONS")
FAILS = []


def has(path, needle, must=True, label="", abspath=False):
    s = open(path, encoding="utf-8", errors="replace").read()
    ok = (needle in s) if must else (needle not in s)
    if not ok:
        FAILS.append(f"{os.path.basename(path)}: {'missing' if must else 'still has'}"
                     f" :: {label or needle[:56]}")
    return ok


def bat(name, needle, must=True, label=""):
    has(os.path.join(ADD, name), needle, must, label)


# ============ ADDONS ============
bat("ADDON_TAILSCALE.bat", "tailscale-setup-latest-amd64.msi", label="A1 official stable MSI")
bat("ADDON_TAILSCALE.bat", "msiexec /i tailscale.msi /qn /norestart", label="A1 silent install")
bat("ADDON_TAILSCALE.bat", "ts.zip", must=False, label="A1 dead zip flow gone")
bat("ADDON_ZEROTIER.bat", "ZeroTier%%20One.msi", label="A2 dist MSI (space escaped)")
bat("ADDON_ZEROTIER.bat", "msiexec /i zerotier.msi /qn", label="A2 silent install")
bat("ADDON_ANYWHEREINPUT.bat", "git+https://github.com/Z-Hussein/AnywhereInput.git",
    label="A3 git+ install (no PyPI pkg)")
bat("ADDON_ANYWHEREINPUT.bat", "where git", label="A3 git prereq guard")
bat("ADDON_GLANCES.bat", '"glances[web]"', label="A4 web extra")
bat("ADDON_NEXTCLOUD.bat", "SKIP_DOMAIN_VALIDATION=true", label="A5 local testing allowed")
bat("ADDON_NEXTCLOUD.bat", "-p 80:80", must=False, label="A5 port 80 clash gone")
bat("ADDON_MOPIDY.bat", 'cmd /c "python -m mopidy"', label="A6 python -m launch")
bat("ADDON_MOPIDY.bat", "import gi", label="A6 GStreamer probe")
bat("ADDON_DBGATE.bat", "LOGINS=admin:", label="A7 dbgate auth set")
bat("ADDON_SHINOBI.bat", "docker compose version >nul 2>&1", label="A8 compose v1 fallback")
bat("ADDON_AUTOHOTKEY.bat", "AutoHotkey_2.*\\.zip$", label="A9 v2 zips only")
bat("ADDON_MESHCENTRAL.bat", "if errorlevel 1 (echo [X] npm install failed",
    label="A10 explicit errorlevel")
for p in glob.glob(os.path.join(ADD, "*.bat")):
    s = open(p, encoding="utf-8", errors="replace").read()
    for ln in s.splitlines():
        if "Invoke-WebRequest " in ln and "UseBasicParsing" not in ln:
            FAILS.append(f"{os.path.basename(p)}: bare Invoke-WebRequest (no -UseBasicParsing)")
        if "for /r" in ln and "if /I not" not in ln:
            FAILS.append(f"{os.path.basename(p)}: for /r without self-copy guard")

# ============ MICROPHONE / AUDIO ============
D = os.path.join(ROOT, "HKS_Client/agent/session_modules/desktop.py")
has(D, "def _native_rate(sd, kind, device=None):", label="B1 native-rate probe")
has(D, "def _resample(np, pcm_i16, src_rate, dst_rate):", label="B1 resampler")
has(D, "def _explain_pa(e):", label="B4 plain-English PortAudio errors")
has(D, "blocksize=max(1, int(rate * 0.1))", label="B2 100ms at device rate")
has(D, "blocksize=3200", must=False, label="B2 200ms block gone")
has(D, "pcm = _resample(np, data.reshape(-1), rate, AUDIO_RATE)", label="B1 16k wire out")
has(D, "cannot open speakers for talk-back", label="B1 talk-back explained")
has(D, "_talk[\"primed\"] = True", label="B5 jitter prefill")
has(D, "WIRE_BYTES_PRIME", label="B5 prime threshold")
C = os.path.join(ROOT, "HKS_Client/agent/session_modules/camera.py")
has(C, "_desk._native_rate(sd, \"input\"", label="B1 record at device rate")
has(C, "w.setframerate(16000)", label="B1 16k WAV out")
has(C, '"default": (i == default_in)', label="B3 default mic marked")

# ============ CAMERA ============
has(C, "def _open_cam(index, probe=False):", label="C1 probe flag")
i2 = open(C, encoding="utf-8").read()
if i2.index("CAP_PROP_FOURCC") > i2.index("CAP_PROP_FRAME_WIDTH"):
    FAILS.append("camera: FOURCC set AFTER resolution (must be before)")
has(C, "for _ in range(10):  # first read can be False", label="C2 read warmup")
has(C, "_open_cam(i, probe=True)  # never cooldown from enumeration", label="C1 list no poison")
has(C, "_list_cache", label="C4 30s list cache")
has(C, "t_next += 1.0 / max(1, fps)", label="C6 absolute-time record pacing")
has(C, "hooks.append(stop)  # ONCE per process", label="C7 hook once")
has(C, 'getattr(ctx, "disconnect_hooks", None)', label="C7 hook defensive")
has(C, "c.disconnect_hooks.append(stop)", must=False, label="C7 per-start hook gone")
W = os.path.join(ROOT, "HKS_Client/agent/session_modules/capture_watch.py")
has(W, "_cammod._open_cam", label="C5 logon capture reuses the real opener")

# ============ REMOTE UNLOCK ============
T = os.path.join(ROOT, "HKS_Client/agent/modules/terminal.py")
has(T, "TRY DIRECT", label="D1 direct WTSConnectSession first")
has(T, '# 1) disconnect the console (makes it reconnectable)', must=False,
    label="D1 old always-disconnect gone")
has(T, "no user is logged in on this PC", label="D2 no-user guard")
has(T, 'console_user = ""  # query failed', label="D2 query-fail fallback")
has(T, "the session may now show the Windows login screen", label="D1 honest failure")

# ============ TERMINAL / HARDWARE / SYSINFO ============
has(T, "self._read_until_marker(mark, time.time() + 1.0)", label="E2 timeout drain")
has(T, "def _po(cmd, t=15):", label="E3 bounded helper")
has(T, "os.popen(", must=False, label="E3 os.popen gone in terminal.py")
H = os.path.join(ROOT, "HKS_Client/agent/modules/hardware.py")
has(H, "if d is None or n is None:", label="E4 io-counters None guard")
has(H, "_temp_cache", label="E6 temp cache 5s")
S = os.path.join(ROOT, "HKS_Client/agent/modules/sysinfo.py")
has(S, 'subprocess.run(["netsh", "wlan", "show", "interfaces"]', label="E5 bounded netsh")
has(S, "os.popen(", must=False, label="E5 os.popen gone in sysinfo.py")

# ============ SERVER ============
M = os.path.join(ROOT, "HKS_AdminPanel/server/main.py")
has(M, '"Content-Length": str(size)}', must=False, label="E8 streamed download length gone")
has(M, "async def _send_frame(self, d, channel: int, data: bytes):", label="E1 per-dash task")
has(M, "def _fail_snap_allowed(ip: str):", label="E9 capture spam cap")
DB = os.path.join(ROOT, "HKS_AdminPanel/server/db.py")
has(DB, "PRAGMA wal_checkpoint(TRUNCATE)", label="E10 WAL checkpoint in prune")
SEC = os.path.join(ROOT, "HKS_AdminPanel/server/security.py")
has(SEC, ">= 20000", label="E11 limiter guard at cap")
SM = os.path.join(ROOT, "HKS_Client/agent/session_main.py")
has(SM, "urlsplit", label="E12 URL parsed with urllib")
A = os.path.join(ROOT, "HKS_Client/agent/modules/addons.py")
has(A, "ThreadPoolExecutor(max_workers=12)", label="E13 parallel port probes")

print(f"checks failed: {len(FAILS)}")
for f in FAILS:
    print("  FAIL:", f)
sys.exit(1 if FAILS else 0)
