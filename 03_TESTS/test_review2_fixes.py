#!/usr/bin/env python3
"""Audit round 2 (uploaded AI review of HKS_FULL_CODE.txt) - regression needles.

Every Critical/Medium/Low finding from the 17-item report is locked here as a
plain-substring check against CURRENT source, so a future edit cannot silently
reintroduce it.  Run: python3 03_TESTS/test_review2_fixes.py
"""
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "04_SOURCE")
FAILS = []


def has(rel, needle, must=True, label=""):
    p = os.path.join(ROOT, rel)
    s = open(p, encoding="utf-8", errors="replace").read()
    ok = (needle in s) if must else (needle not in s)
    if not ok:
        FAILS.append(f"{rel}: {'missing' if must else 'still has'} :: "
                     f"{label or needle[:60]}")
    return ok


def count(rel, needle, n):
    p = os.path.join(ROOT, rel)
    s = open(p, encoding="utf-8", errors="replace").read()
    if s.count(needle) != n:
        FAILS.append(f"{rel}: expected {n}x, got {s.count(needle)}x :: {needle[:50]}")


# ---- C1: bare CAPTURE_DIR (import name), never db.CAPTURE_DIR ----
count("HKS_AdminPanel/server/main.py", "db.CAPTURE_DIR", 0)
has("HKS_AdminPanel/server/main.py", "from .db import CAPTURE_DIR, db", label="C1 bare imported constant")
# C1b: api_test_capture never 500s on save failure
has("HKS_AdminPanel/server/main.py", '"save failed: {e}"', label="C1b try/except")

# ---- C2: cron_loop saves the MUTATED list ----
has("HKS_Client/agent/modules/tasks.py",
    '_save(ctx, [x for x in tasks if not x.get("done")])', label="C2")
has("HKS_Client/agent/modules/tasks.py",
    '_save(ctx, [x for x in _load(ctx)', must=False, label="C2 stale reread")

# ---- C3: restart_hks.bat regex escaping matches doctor probe (\. at runtime) ----
has("first_run.py", "\"($_.CommandLine -match '-m (server|agent)\\\\.(main|session_main)') }; \"",
    label="C3")
count("first_run.py", "agent)\\\\\\\\.", 0)  # no over-escaped form anywhere

# ---- C4: xampp restart handled BEFORE the start/stop bat guard ----
has("HKS_Client/agent/modules/xampp.py",
    'if op == "restart":  # audit2 #4', label="C4")
i = open(os.path.join(ROOT, "HKS_Client/agent/modules/xampp.py")).read()
if i.index('if op == "restart"') > i.index('if not bat:'):
    FAILS.append("xampp: restart branch still AFTER the guard")

# ---- C5: clipboard DIB gets a BITMAPFILEHEADER ----
has("HKS_Client/agent/session_modules/desktop.py", 'b"BM" + _struct.pack', label="C5")
has("HKS_Client/agent/session_modules/desktop.py", '.encode("latin1"', must=False,
    label="C5 old encode path")

# ---- C6: /TR points straight at the quoted bat (no nested quotes) ----
has("HKS_Client/agent/modules/addons.py", "f'\"{bat}\"', \"/SC\", \"ONSTART\"", label="C6")
has("HKS_Client/agent/modules/addons.py", "'\"cmd /c \"{bat}\"\"'", must=False,
    label="C6 nested-quote form gone")

# ---- C7: netsh firewallpolicy = ONE comma token ----
has("first_run.py", '"blockinbound,allowoutbound"', label="C7")
has("first_run.py", '"blockinbound", "allowoutbound"', must=False, label="C7 two tokens")

# ---- C8: self_destruct bat lives in %TEMP%, no blind pythonw kill ----
has("HKS_Client/agent/modules/files.py", "hks_self_destruct.bat", label="C8a temp bat")
has("HKS_Client/agent/modules/files.py", "taskkill /f /im pythonw.exe >nul", must=False,
    label="C8b blind kill gone (comment mention allowed)")
count("HKS_Client/agent/modules/files.py", "WINDOWTITLE eq HKS", 3)
has("HKS_Client/agent/modules/files.py", "cwd=tempfile.gettempdir()", label="C8c cwd safe")

# ---- C9: camera captures under the agent data dir (janitor-covered) ----
has("HKS_Client/agent/session_modules/camera.py", "from ..common import data_dir",
    label="C9")

# ---- M10: mutex via prototypes + use_last_error ----
has("HKS_Client/agent/session_main.py", 'ctypes.WinDLL("kernel32", use_last_error=True)',
    label="M10a")
has("HKS_Client/agent/session_main.py", "CreateMutexW.restype = ctypes.c_void_p",
    label="M10b")
has("HKS_Client/agent/session_main.py", "ctypes.get_last_error() != ERROR_ALREADY_EXISTS",
    label="M10c")
has("HKS_Client/agent/session_main.py", 'r"Local\\HKS_SessionAgent_single"', label="M10 fallback kept")

# ---- M11: disconnect hook registered once ----
has("HKS_Client/agent/session_modules/screen.py",
    "ctx.disconnect_hooks.append(stop)  # once per process", label="M11a")
count("HKS_Client/agent/session_modules/screen.py", "disconnect_hooks.append", 1)

# ---- M12: dead [:0] expression gone ----
has("HKS_Client/agent/modules/hardware.py", "[:0] or []", must=False, label="M12")

# ---- M13: semaphore slot acquired inside the task ----
has("HKS_Client/agent/connection.py", "await pool.acquire()  # audit2 #13", label="M13a")
has("HKS_Client/agent/connection.py", "await pool.acquire()\n                asyncio.ensure_future",
    must=False, label="M13b acquire-then-spawn gone")
count("HKS_Client/agent/connection.py", "pool.release()", 1)

# ---- M15: bats pin HKS_DATA_DIR; verify reads DATA then client-data fallback ----
count("first_run.py", "set HKS_DATA_DIR=", 3)  # panel bat + agent bat + session bat
has("first_run.py", 'logf = os.path.join(CLIENT, "data", "hks_svc.log")', label="M15b")

# ---- M16: Secure cookie honors x-forwarded-proto ----
has("HKS_AdminPanel/server/main.py", 'x-forwarded-proto', label="M16")

# ---- M17: dedupe dict under its own lock ----
has("HKS_AdminPanel/server/db.py", "self._dedupe_lock", label="M17")

# ---- Low: step labels, safe listing, capture id, audit masking ----
count("first_run.py", "[7/9] Auto-start", 0)
has("first_run.py", "[7b/9] Auto-start", label="L1")
has("HKS_Client/agent/modules/files.py", "_safe_iter", label="L2")
has("HKS_AdminPanel/server/db.py", "return cur.lastrowid", label="L3 add_capture lastrowid (upgraded audit4)")
has("HKS_AdminPanel/server/db.py", '"pass", "pwd", "secret", "token"', label="L4 audit mask")

print(f"checks failed: {len(FAILS)}")
for f in FAILS:
    print("  FAIL:", f)
sys.exit(1 if FAILS else 0)
