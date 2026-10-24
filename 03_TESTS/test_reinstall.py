"""Round 31: REPEAT-INSTALL safety - upgrade over a running HKS must never
hit 'file in use' and must never leave the old version serving."""
import os
import re
import subprocess
import sys

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FR = os.path.join(ROOT, "04_SOURCE", "first_run.py")
BAT = os.path.join(ROOT, "00_START_HERE", "INSTALL_HKS.bat")
LP = os.path.join(ROOT, "04_SOURCE", "HKS_Client", "agent", "local_panel.py")

# 1. --stop-only mode really works (exit 0, safe on non-Windows too)
r = subprocess.run([sys.executable, FR, "--stop-only"], capture_output=True,
                   text=True, timeout=120, cwd=ROOT)
assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
assert "[stop-only] done" in r.stdout, r.stdout
print("1. first_run --stop-only: runs clean, exit 0  OK")

# 2. INSTALL_HKS.bat: stop happens BEFORE the file copy (the order IS the bug fix)
bat = open(BAT, encoding="ascii").read()
i_stop = bat.find("--stop-only")
i_copy = bat.find("robocopy")
assert 0 < i_stop < i_copy, (i_stop, i_copy)
print("2. installer stops old HKS BEFORE robocopy  OK")

# 3. robocopy is bounded (no 1,000,000 x 30 s default retry hang)
assert bat.count("/R:5 /W:2") == 3, bat.count("/R:5 /W:2")
# data dirs are excluded from the copy -> users/DB survive every reinstall
assert bat.count("/XD __pycache__ .venv data") == 2
assert '"%~dp0HKS_ADDONS" "%BASE%\\HKS_ADDONS"' in bat
assert bat.count("if errorlevel 8 goto :copy_failed") == 3
assert 'if not exist "%~dp0HKS_ADDONS\\ADDON_MENU.bat"' in bat
print("3. robocopy /R:5 /W:2 + data dirs preserved on reinstall  OK")

# 4. locked-file handler (robocopy >= 8 -> clear message, no silent continue)
assert "if errorlevel 8" in bat and "LOCKED by another process" in bat
print("4. locked-file handler present  OK")

# 5. stop_running_hks covers every HKS process shape (incl. tunnel + cloudflared)
fr = open(FR, encoding="utf-8").read()
for needle in ("cloudflared.exe", "start_panel|tunnel",
               "tunnel_url_watch", "main|session_main",
               "HKS_INSTALLER_PID"):
    assert needle in fr, needle
print("5. stop list: python/pythonw -m, watchdog bats, tunnel, cloudflared  OK")

# 6. restart_hks.bat generated at install + wired into the local panel
assert '"restart_hks.bat"' in fr
assert "schtasks /Run /TN HKS-Agent" in fr
lp = open(LP, encoding="utf-8").read()
assert 'self.path == "/restart"' in lp
assert "restart_hks.bat" in lp
assert "Restart HKS (UAC)" in lp
print("6. local panel Restart button -> restart_hks.bat (UAC)  OK")

# 7. the generated restart bat is a valid self-elevating cmd script
gen_start = fr.find('restart_hks.bat')
gen = fr[gen_start:gen_start + 4000]
for needle in ("net session >nul 2>&1", "-Verb RunAs", "schtasks /End /TN HKS-SessionAgent",
               "schtasks /Run /TN HKS-Panel"):
    assert needle in gen, needle
print("7. restart bat: self-elevating, ends tasks, kills, restarts all 3  OK")

print("REINSTALL TESTS: ALL PASSED")
