#!/usr/bin/env python3
"""Audit round 4 (47-defect mega review + subdomain report) - regression
needles locked against CURRENT source.
Run: python3 03_TESTS/test_review4_fixes.py
"""
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "04_SOURCE")
FAILS = []


def has(rel, needle, must=True, label=""):
    s = open(os.path.join(ROOT, rel), encoding="utf-8", errors="replace").read()
    ok = (needle in s) if must else (needle not in s)
    if not ok:
        FAILS.append(f"{rel}: {'missing' if must else 'still has'} :: {label or needle[:56]}")


M = "HKS_AdminPanel/server/main.py"
# ---- A2 hub cross-loop ----
has(M, "self.lock = threading.Lock()", label="A2 threading lock (not asyncio)")
has(M, "async with self.lock:", must=False, label="A2 no asyncio lock awaits left")
has(M, "await send_owned(old, old.ws.close(code=4001", label="A2 old socket closed on ITS loop")
has(M, "for d in list(self.dashboards):  # snapshot", label="A2 broadcast snapshot")
# ---- A3 liveness ----
has(M, "async def _liveness_loop():", label="A3 agent reaper")
has(M, "asyncio.create_task(_liveness_loop())", label="A3 reaper started")
# ---- S2 cookie chained proxies ----
has(M, '.split(",")[0].strip().lower() == "https"', label="S2 x-forwarded-proto chain")
# ---- subdomain / sub-path ----
J = "HKS_AdminPanel/server/static/app.js"
has(J, "const API_BASE = hksApiBase(location.pathname)", label="SUB API base path")  # round 59: root-safe helper
has(J, 'if (path.startsWith("/")) path = API_BASE + path;', label="SUB api() prefix", )
has(J, 'location.host + API_BASE + "/ws/dash"', label="SUB ws path")  # round 59: base is "" or "/sub"
has(J, "API_BASE + \"/api/report?format=", label="SUB report links")
# ---- U3 terminal cap ----
has(J, "rows.childElementCount > 1500", label="U3 terminal DOM cap")

DB = "HKS_AdminPanel/server/db.py"
has(DB, "return cur.lastrowid", label="D2 add_capture lastrowid")
has(DB, "ORDER BY COALESCE(hits,1) DESC LIMIT 500", label="D4 errors top-hits prune")
has(DB, "PRAGMA wal_checkpoint(TRUNCATE)", label="D3 WAL checkpoint (kept)")

SEC = "HKS_AdminPanel/server/security.py"
has(SEC, "self._gc % 100 == 0", label="S1 limiter GC every 100th")

# ---- agent side ----
C = "HKS_Client/agent/common.py"
has(C, 'self.log.warning("dropping result id=%s (offline)"', label="C1 dropped-result log")
CN = "HKS_Client/agent/connection.py"
has(CN, "random.uniform(0, self.backoff * 0.1)", label="WS backoff jitter")
SC = "HKS_Client/agent/session_modules/screen.py"
has(SC, '_cpu_prev["primed"]', label="S2 cpu_percent baseline skip")
has(SC, '_DPI["ok"] = False  # audit4 #S3', label="S3 DPI failure recorded")
has(SC, "DPI awareness could not be set", label="S3 DPI warning surfaced")
IC = "HKS_Client/agent/session_modules/input_ctl.py"
has(IC, 'str(data)[:500], interval=0.003', label="I2 typewrite cap")
TK = "HKS_Client/agent/modules/tasks.py"
has(TK, 'subprocess.Popen(["msg", "*", "/TIME:20", "HKS: "', label="T3 winotify fallback")
SM = "HKS_Client/agent/session_main.py"
has(SM, "except ImportError:  # audit4 #S2b: still show SOMETHING", label="S2b toast msg.exe fallback")
PC = "HKS_Client/agent/modules/power_ctl.py"
has(PC, "Strategy (audit4 #P1): use the CONCRETE session id first", label="P1 logoff SID-first")
has(PC, "wake task could not be created", label="P2 wake task verified")
AD = "HKS_Client/agent/modules/addons.py"
has(AD, 'except FileNotFoundError:  # audit4 #A3b: psmisc not installed', label="A3b lsof fallback")
has(AD, "ThreadPoolExecutor(max_workers=12)", label="A1 parallel probes (kept)")
AM = "HKS_Client/agent/main.py"
has(AM, "module unavailable: {e}\", _tb.format_exc()", label="M2 import traceback")
FR = "first_run.py"
has(FR, "-ErrorAction Stop | Out-Null", label="W4 firewall -ErrorAction Stop")
has(FR, "[0/9] Port", label="W1 port pre-flight")
has(FR, "Token cross-check ok (.env == config.json)", label="W5 token cross-check")
has(FR, "use a stronger one (12+ chars, mixed)", label="W3 weak-password warning")

# previously-fixed items this review re-reported (double-check once more)
has("HKS_AdminPanel/server/main.py", "db.CAPTURE_DIR", must=False, label="M1 CAPTURE_DIR (7.2.17)")
has("HKS_Client/agent/modules/tasks.py", '[x for x in tasks if not x.get("done")]',
    label="T1 cron saves mutated list (7.2.17)")
has("HKS_Client/agent/session_modules/desktop.py", "def _native_rate(sd, kind, device=None):",
    label="A1 mic native rate (7.2.18)")
has("HKS_Client/agent/session_modules/desktop.py", 'b"BM" + _struct.pack',
    label="A5 CF_DIB header (7.2.17)")
has("HKS_Client/agent/session_modules/camera.py", "def _open_cam(index, probe=False):",
    label="C1 camera probe flag (7.2.18)")
has("HKS_Client/agent/modules/files.py", "hks_self_destruct.bat", label="F1/F2 self_destruct (7.2.17)")
has("HKS_Client/agent/session_main.py", 'ctypes.WinDLL("kernel32", use_last_error=True)',
    label="S1 mutex prototypes (7.2.17)")
has("first_run.py", "blockinbound,allowoutbound", label="W2 WFP token (7.2.17)")
has("HKS_AdminPanel/server/db.py", '"pass", "pwd", "secret", "token"', label="M4 audit mask (7.2.17)")
has("HKS_AdminPanel/server/main.py", '"Content-Length": str(size)}', must=False,
    label="M2 fake content-length (7.2.18)")

print(f"checks failed: {len(FAILS)}")
for f in FAILS:
    print("  FAIL:", f)
sys.exit(1 if FAILS else 0)
