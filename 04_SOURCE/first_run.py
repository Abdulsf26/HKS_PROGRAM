"""HKS ALL-IN-ONE first-run wizard (v3 — Windows-hardened).

Research-backed startup strategy (2025-2026 best practice):
  * The WIZARD registers plain scheduled tasks - NO pywin32 service
    (pythonservice.exe + venv DLLs = error 1053/1063 on
    real machines). Task Scheduler ONSTART + SYSTEM + restart-on-failure
    instead, with battery-safe settings and NO 72-hour execution limit.
  * The agent runs via run_agent.bat (restart-forever loop) so even a hard
    python crash is recovered; the agent ALSO has its own supervising loop.
  * After install, this wizard POLLS the agent until it is ONLINE and shows
    the result — you never have to guess.

Interactive:   run INSTALL_HKS.bat (it stages the tree, then runs this wizard)
Note:          running first_run.py directly ONLY works after INSTALL_HKS.bat
               has copied HKS_AdminPanel/HKS_Client to the program folder.
Silent (test): python first_run.py --auto --username admin --password X --port 8000 --no-https
"""
import argparse
import getpass
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import threading
import queue

BASE = os.environ.get("HKS_BASE") or r"C:\HKS_PROGRAM"
CLIENT = os.path.join(BASE, "HKS_Client")
PANEL = os.path.join(BASE, "HKS_AdminPanel")
VENV = os.path.join(BASE, "venv")
DATA = os.path.join(BASE, "data")
DATA_PANEL = os.path.join(BASE, "data_panel")
IS_NT = os.name == "nt"
PY = os.path.join(VENV, "Scripts", "python.exe") if IS_NT else os.path.join(VENV, "bin", "python")
if not os.path.exists(PY):
    PY = sys.executable


def sh(cmd, **kw):
    """Run a command; returns (ok, output). Never raises."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                           timeout=kw.pop("timeout", 120), **kw)
        return p.returncode == 0, (p.stdout or "") + (p.stderr or "")
    except Exception as e:
        return False, str(e)


def ps(ps_script, timeout=120):
    # round 56 (field audit #2/#9): a non-terminating PowerShell error used
    # to return exit code 0 -> registration "OK" that registered nothing.
    ps_script = "$ErrorActionPreference='Stop'; " + ps_script
    return sh(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
               "-Command", ps_script], timeout=timeout)


def lan_ips():
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    try:
        import psutil
        for addrs in psutil.net_if_addrs().values():
            for a in addrs:
                if hasattr(a.family, "name") and a.family.name == "AF_INET" and not a.address.startswith("127."):
                    ips.add(a.address)
    except Exception:
        pass
    return sorted(ips) or ["127.0.0.1"]


def real_first_ips():
    """round 50: real Wi-Fi/LAN addresses first, virtual junk last."""
    return sorted(lan_ips(), key=lambda x: (x.startswith(("169.254.", "192.168.56.")), x))


def ip_tag(ip):
    """round 50: label every address so nobody types a dead one into a
    phone. 169.254.x.x = link-local (adapter with no network behind it),
    192.168.56.x = VirtualBox host-only - neither can EVER be reached
    from another device; invalid and host-only addresses are labelled
    explicitly so users do not mistake them for reachable LAN interfaces."""
    if ip.startswith("169.254."):
        return "VIRTUAL/NO-WI-FI - will NOT work"
    if ip.startswith("192.168.56."):
        return "VirtualBox - will NOT work"
    return "REAL - USE THIS"


def ask(prompt, default):
    try:
        v = input(f"{prompt} [{default}]: ").strip()
        return v or str(default)
    except EOFError:
        return str(default)


def ask_password():
    while True:
        try:
            p1 = getpass.getpass("Admin password (min 8 chars): ")
        except EOFError:
            # 7.2.54 fix: no keyboard (scheduled/remote run) used to hang or
            # crash here - point the operator at the silent install path.
            raise SystemExit("no keyboard available for password prompt - "
                             "run INSTALL_HKS.bat normally, or use "
                             "first_run.py --answer-file <answers.json>")
        except Exception:
            p1 = input("Admin password (min 8 chars): ")
        if len(p1) >= 8:
            weak = (len(p1) < 12 or p1.isdigit() or p1.isalpha()
                    or p1.lower() in ("password", "password1", "qwerty123"))
            if weak:
                # WARNING ONLY (audit4 #W3): blocking here would lock the
                # owner out of the password they already chose; the tunnel
                # risk is explained instead.
                print("  NOTE: this password is weak. Fine for LAN testing -")
                print("        use a stronger one (12+ chars, mixed) before opening a tunnel.")
            return p1
        print("  too short — use at least 8 characters.")


def setup_port_conflict(port, https):
    """Return the first occupied/unusable listen port, or None when free."""
    try:
        base = int(port)
    except (TypeError, ValueError):
        return 0
    if not 1024 <= base <= (65534 if https else 65535):
        return base
    for candidate in ([base, base + 1] if https else [base]):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(("0.0.0.0", candidate))
        except OSError:
            return candidate
        finally:
            sock.close()
    return None


def run_gui_setup():
    """Native Windows setup form; delegates installation to this tested CLI."""
    try:
        import tkinter as tk
        from tkinter import ttk, messagebox
    except Exception as exc:
        print("GUI setup is unavailable in this Python installation: " + str(exc))
        print("Continuing with the text setup wizard.")
        return None

    root = tk.Tk()
    root.title("HKS Program — Setup")
    root.geometry("560x640")
    root.minsize(500, 560)
    root.configure(bg="#f2efe6")
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure("TFrame", background="#f2efe6")
    style.configure("TLabel", background="#f2efe6", foreground="#111111", font=("Segoe UI", 10))
    style.configure("Title.TLabel", font=("Segoe UI", 20, "bold"))
    style.configure("TButton", font=("Segoe UI", 10, "bold"), padding=(12, 8))
    style.configure("TEntry", padding=(8, 7))
    style.configure("TCheckbutton", background="#f2efe6", foreground="#111111")

    outer = ttk.Frame(root, padding=22)
    outer.pack(fill="both", expand=True)
    ttk.Label(outer, text="HKS PROGRAM", style="Title.TLabel").pack(anchor="w")
    ttk.Label(outer, text="Set up this laptop for secure browser access.").pack(anchor="w", pady=(2, 16))
    form = ttk.Frame(outer)
    form.pack(fill="x")
    fields = {}
    for label, key, default, secret in (
        ("Admin username", "username", "admin", False),
        ("Password (at least 8 characters)", "password", "", True),
        ("Confirm password", "confirm", "", True),
        ("Panel port", "port", "8000", False),
    ):
        ttk.Label(form, text=label).pack(anchor="w", pady=(8, 3))
        entry = ttk.Entry(form, show="*" if secret else "")
        entry.pack(fill="x")
        if default:
            entry.insert(0, default)
        fields[key] = entry
    https = tk.BooleanVar(value=True)
    ttk.Checkbutton(outer, text="Use HTTPS for encrypted browser access", variable=https).pack(anchor="w", pady=12)
    status = ttk.Label(outer, text="Install location: " + BASE, wraplength=510)
    status.pack(anchor="w", pady=(0, 8))
    log = tk.Text(outer, height=11, wrap="word", state="disabled", bg="#fffdf6",
                  fg="#111111", relief="solid", borderwidth=2, padx=10, pady=8)
    log.pack(fill="both", expand=True)
    events = queue.Queue()
    running = {"process": None, "temp": None}

    def add_log(line):
        log.configure(state="normal")
        log.insert("end", line + "\n")
        log.see("end")
        log.configure(state="disabled")

    def poll_output():
        try:
            while True:
                kind, value = events.get_nowait()
                if kind == "line":
                    add_log(value)
                elif kind == "done":
                    running["process"] = None
                    try:
                        if running["temp"]:
                            shutil.rmtree(running["temp"], ignore_errors=True)
                    finally:
                        running["temp"] = None
                    start_button.configure(state="normal", text="Finish")
                    cancel_button.configure(state="normal")
                    if value == 0:
                        status.configure(text="Setup finished. Read the checks above and open the HKS panel.")
                        start_button.configure(text="Close", command=root.destroy)
                        messagebox.showinfo("HKS setup", "Setup completed. Check the log for agent and panel status.", parent=root)
                    else:
                        status.configure(text=f"Setup exited with code {value}. Use the log above to fix the reported issue, then try again.")
                        messagebox.showerror("HKS setup", f"Setup failed (exit code {value}).", parent=root)
        except queue.Empty:
            pass
        if running["process"] is not None:
            root.after(150, poll_output)

    def start_install():
        username = fields["username"].get().strip()
        password = fields["password"].get()
        confirm = fields["confirm"].get()
        try:
            port = int(fields["port"].get().strip())
        except ValueError:
            messagebox.showerror("Check the port", "Enter a whole-number port from 1024 to 65534.", parent=root)
            return
        if len(username) < 3:
            messagebox.showerror("Check the username", "Use at least 3 characters.", parent=root)
            return
        if len(password) < 8:
            messagebox.showerror("Check the password", "Use at least 8 characters.", parent=root)
            return
        if password != confirm:
            messagebox.showerror("Passwords do not match", "Enter the same password in both fields.", parent=root)
            return
        if not 1024 <= port <= 65534:
            messagebox.showerror("Check the port", "Choose a port from 1024 to 65534 (HTTPS also uses the next port).", parent=root)
            return
        conflict = setup_port_conflict(port, bool(https.get()))
        if conflict is not None:
            messagebox.showerror("Port in use", f"Port {conflict} is already occupied or unavailable. Choose another base port.", parent=root)
            return
        temp_dir = tempfile.mkdtemp(prefix="hks-setup-")
        answer_path = os.path.join(temp_dir, "answers.json")
        try:
            with open(answer_path, "w", encoding="utf-8") as answer:
                json.dump({"username": username, "password": password,
                           "port": port, "https": bool(https.get())}, answer)
            fields["password"].delete(0, "end")
            fields["confirm"].delete(0, "end")
            running["temp"] = temp_dir
            status.configure(text="Installing… This window will show checks and any errors.")
            add_log("Starting HKS setup. Keep this window open.")
            start_button.configure(state="disabled")
            cancel_button.configure(state="disabled")
            env = dict(os.environ)
            env["HKS_BASE"] = BASE
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_NT else 0
            process = subprocess.Popen([PY, os.path.abspath(__file__), "--answer-file", answer_path],
                cwd=BASE, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1, creationflags=creationflags)
            running["process"] = process
            def collect():
                try:
                    for line in process.stdout:
                        events.put(("line", line.rstrip("\r\n")))
                    events.put(("done", process.wait()))
                except Exception as exc:
                    events.put(("line", "Setup output error: " + str(exc)))
                    events.put(("done", 1))
            threading.Thread(target=collect, daemon=True, name="hks-setup-output").start()
            root.after(150, poll_output)
        except Exception as exc:
            running["process"] = None
            shutil.rmtree(temp_dir, ignore_errors=True)
            start_button.configure(state="normal")
            cancel_button.configure(state="normal")
            messagebox.showerror("Could not start setup", str(exc), parent=root)

    actions = ttk.Frame(outer)
    actions.pack(fill="x", pady=(12, 0))
    start_button = ttk.Button(actions, text="Install HKS", command=start_install)
    start_button.pack(side="right")
    cancel_button = ttk.Button(actions, text="Cancel", command=root.destroy)
    cancel_button.pack(side="right", padx=(0, 8))
    def close_window():
        if running["process"] is not None:
            messagebox.showwarning("Setup is running", "Wait for setup to finish before closing this window.", parent=root)
            return
        root.destroy()
    root.protocol("WM_DELETE_WINDOW", close_window)
    cancel_button.configure(command=close_window)
    root.mainloop()
    return 0


# ---------------------------------------------------------------- Windows tasks
def build_register_ps(name, exe, wd, trigger="boot", user="SYSTEM",
                      logon="ServiceAccount", level="Highest", args=""):
    """Single-line PowerShell that registers an always-on task.
    MUST stay one line: no backtick line-continuations (that was the bug).
    args MUST be passed for interpreters (pythonw.exe alone runs NOTHING —
    that was the 'desktop offline' bug)."""
    trig = "-AtStartup" if trigger == "boot" else "-AtLogOn"
    argpart = f" -Argument '{args}'" if args else ""
    return (
        "$a=New-ScheduledTaskAction -Execute '{exe}'{argpart} -WorkingDirectory '{wd}'; "
        "$t=New-ScheduledTaskTrigger {trig}; "
        "$p=New-ScheduledTaskPrincipal -UserId '{user}' -LogonType {logon} -RunLevel {level}; "
        "$s=New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) "
        "-RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) "
        "-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable; "
        # IdleSettings defaults to StopOnIdleEnd=True on current Windows. For
        # the desktop agent this can kill camera/mic availability after idle.
        "$s.IdleSettings.StopOnIdleEnd=$false; $s.IdleSettings.RestartOnIdle=$false; "
        "Register-ScheduledTask -TaskName '{name}' -Action $a -Trigger $t "
        "-Principal $p -Settings $s -Force | Out-Null"
    ).format(exe=exe, wd=wd, trig=trig, user=user, logon=logon, level=level,
             name=name, argpart=argpart)


def build_schtasks_argv(name, exe, trigger="boot", user="SYSTEM", level="Highest",
                        args="", rl=True):
    """schtasks fallback. Flags must be SEPARATE argv elements:
    passing '/SC ONSTART' as one element made Windows quote it and
    schtasks reject it (that was the second bug).
    /TR is ONE element: '"exe" args' (inner quotes included)."""
    tr = f'"{exe}" {args}'.strip()
    argv = ["schtasks", "/Create", "/TN", name, "/TR", tr,
            "/SC", "ONSTART" if trigger == "boot" else "ONLOGON", "/F"]
    if trigger == "boot":
        argv += ["/RU", "SYSTEM"]          # SYSTEM needs no password
    # logon tasks: omit /RU -> current user, interactive token, no prompt
    if level == "Highest" and trigger == "boot" and rl:
        argv += ["/RL", "HIGHEST"]
    return argv


PS_PATCH_SETTINGS = (
    "$s=New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) "
    "-RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) "
    "-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable; "
    "$s.IdleSettings.StopOnIdleEnd=$false; $s.IdleSettings.RestartOnIdle=$false; "
    "Set-ScheduledTask -TaskName '{name}' -Settings $s | Out-Null"
)


def task_persisted(name):
    """round 56 (field audit #2/#10): a task counts as registered ONLY if
    Windows gives it back to us. Catches silent non-terminating errors."""
    okq, outq = ps("(Get-ScheduledTask -TaskName '" + name
                   + "' -ErrorAction SilentlyContinue) -ne $null")
    return okq and "True" in (outq or "")


def register_task(name, exe, wd, trigger="boot", user="SYSTEM",
                  logon="ServiceAccount", level="Highest", args=""):
    """Returns (ok, method, err_line). Tries PowerShell, then schtasks.
    Every success is PROVEN by reading the task back (task_persisted)."""
    ok, out = ps(build_register_ps(name, exe, wd, trigger, user, logon, level, args))
    if ok and task_persisted(name):
        return True, "powershell", ""
    lines = [l for l in (out or "").strip().splitlines() if l.strip()]
    err1 = lines[-1][:160] if lines else "powershell failed"
    argv = build_schtasks_argv(name, exe, trigger, user, level, args)
    ok2, out2 = sh(argv)
    if ok2 and task_persisted(name):
        ps(PS_PATCH_SETTINGS.replace("{name}", name))  # battery + no-limit patch
        return True, "schtasks", ""
    lines2 = [l for l in (out2 or "").strip().splitlines() if l.strip()]
    err2 = lines2[-1][:160] if lines2 else "schtasks failed"
    # attempt 3 (round 51): some Windows builds reject /RL HIGHEST combined
    # with /RU SYSTEM - retry without /RL (SYSTEM always runs highest).
    argv3 = build_schtasks_argv(name, exe, trigger, user, level, args, rl=False)
    ok3, out3 = sh(argv3)
    if ok3 and task_persisted(name):
        ps(PS_PATCH_SETTINGS.replace("{name}", name))
        return True, "schtasks-noRL", ""
    lines3 = [l for l in (out3 or "").strip().splitlines() if l.strip()]
    err3 = lines3[-1][:160] if lines3 else "schtasks failed"
    return False, "none", f"{err1} | {err2} | noRL: {err3}"


DETACHED = 0
if getattr(os, "name", "") == "nt":
    DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


def start_bat(path, wd):
    """Directly launch a .bat detached (hidden). Used when task registration
    fails so the panel/agent STILL run for this boot."""
    if not os.path.exists(path):
        return False
    try:
        subprocess.Popen(["cmd", "/c", path], creationflags=DETACHED, cwd=wd,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL, close_fds=True)
        return True
    except Exception:
        return False


TUNNEL_URL_FILE = "tunnel_url.txt"
CLOUDFLARED_URL = ("https://github.com/cloudflare/cloudflared/releases/latest/"
                   "download/cloudflared-windows-amd64.exe")


DOCTOR_NAME = "HKS_DOCTOR.bat"

_DOCTOR = r'''@echo off
title HKS DOCTOR - complete check
cd /d "%~dp0"
setlocal EnableDelayedExpansion
set REP=%~dp0hks_doctor_report.txt
for %%i in ("%~dp0.") do set "BASE=%%~fi"
set PORT=
for /f "usebackq delims=" %%i in (`powershell -NoProfile -Command "try { $u=(Get-Content '%BASE%\HKS_Client\config.json' -Raw | ConvertFrom-Json).server_url; $h=($u -replace '^[a-z]+://',''); Write-Output $h.Split('/')[0].Split(':')[-1] } catch { }"`) do set PORT=%%i
if "%PORT%"=="" set PORT=6767
set /a PORT1=%PORT%+1
echo ===== HKS DOCTOR - %date% %time% ===== > "%REP%"
echo.
echo ===== [A] PHONE ADDRESSES - TRY IN THIS ORDER =====>> "%REP%"
echo [A1] ANYWHERE tunnel - works on mobile data, any Wi-Fi>> "%REP%"
if not exist "%BASE%\data\tunnel_url.txt" echo     [--] no tunnel address - for from-anywhere access double-click HKS_FIX_TUNNEL.bat - needs internet, takes 1-2 min>> "%REP%"
if exist "%BASE%\data\tunnel_url.txt" powershell -NoProfile -Command "try { $u=(Get-Content '%BASE%\data\tunnel_url.txt' -TotalCount 1).Trim(); $r=Invoke-WebRequest -UseBasicParsing ($u+'/api/version') -TimeoutSec 15; Write-Output ('    OPEN THIS ON YOUR PHONE - WORKS ANYWHERE: ' + $u) } catch { Write-Output ('    [X] tunnel address not answering: ' + $_.Exception.Message) }" >> "%REP%" 2>&1
echo [A2] SAME-WI-FI http door - phone and laptop on the SAME Wi-Fi>> "%REP%"
powershell -NoProfile -Command "try { [System.Net.ServicePointManager]::ServerCertificateValidationCallback={ $true } } catch {}; $adm='no'; try { if (([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { $adm='yes' } } catch {}; Write-Output ('    doctor running as Administrator: ' + $adm); $errs=@(); $j=$null; foreach ($pt in @(%PORT%, (%PORT%+1))) { if ($j) { break }; foreach ($s in @('http','https')) { try { $j=Invoke-WebRequest -UseBasicParsing ($s+'://127.0.0.1:'+ $pt +'/api/version') -TimeoutSec 3; if ($j) { Write-Output ('    [OK] panel answers at ' + $s + '://' + '127.0.0.1:' + $pt + $(if ($s -eq 'http') { '  <- USE THIS (no certificate warning - EASIEST)' } else { '  <- USE THIS (a certificate warning is NORMAL - tap Proceed/Advanced)' })) } } catch { $errs += ($s + ':' + $pt + ' - ' + $_.Exception.Message) } } }; if (-not $j) { Write-Output '    [X] panel not answering locally - errors:'; $errs | ForEach-Object { Write-Output ('      ' + $_) } } else { $j2=$null; try { $j2=$j.Content | ConvertFrom-Json } catch {}; $ports=@(); if ($j2 -and $j2.main) { $ports += [int]$j2.main }; if ($j2 -and $j2.http_companion) { $ports += [int]$j2.http_companion }; if (-not $ports) { $ports += %PORT% }; $ips=Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' -and $_.IPAddress -notlike '192.168.56.*' }; foreach ($ip in $ips) { foreach ($pp in $ports) { foreach ($s in @('http','https')) { $b=($s+'://'+$ip.IPAddress+':'+$pp); try { $null=Invoke-WebRequest -UseBasicParsing ($b+'/api/version') -TimeoutSec 3; Write-Output ('    [ANSWERS] '+$b+'  <- type EXACTLY this on the phone' + $(if ($s -eq 'http') { ' (no certificate warning - EASIEST)' } else { ' (tap Proceed/Advanced)' }) ) } catch { Write-Output ('    [no answer] '+$b) } } } } }" >> "%REP%" 2>&1
echo [A3] a certificate warning is NORMAL on https - tap Proceed / Advanced>> "%REP%"
if exist "%BASE%\data\tunnel_url.txt" (echo           the ANYWHERE address CHANGES after every laptop restart ->> "%REP%" & echo           always take the fresh one from the HKS Phone Access shortcut>> "%REP%")
echo.>> "%REP%"
echo [1/8] installed panel - auto-detects your port from config...
set PORT=
for /f "usebackq delims=" %%i in (`powershell -NoProfile -Command "try { $u=(Get-Content '%BASE%\HKS_Client\config.json' -Raw | ConvertFrom-Json).server_url; $h=($u -replace '^[a-z]+://',''); Write-Output $h.Split('/')[0].Split(':')[-1] } catch { }"`) do set PORT=%%i
if "%PORT%"=="" set PORT=6767
if "%PORT%"=="" (echo   [X] config.json missing - HKS not installed at %BASE%>> "%REP%") else (echo   config port: %PORT%>> "%REP%")
powershell -NoProfile -Command "try { [System.Net.ServicePointManager]::ServerCertificateValidationCallback={ $true } } catch {}; $found=$false; foreach ($p in @('%PORT%','%PORT1%','6767','6768','8000')) { if (-not $p) { continue }; foreach ($s in @('http','https')) { try { $r=Invoke-WebRequest -UseBasicParsing ($s+'://127.0.0.1:'+ $p +'/api/version') -TimeoutSec 3; Write-Output ('  [OK] panel RUNNING: ' + $s + '://127.0.0.1:' + $p + ' -> ' + $r.Content); $found=$true; if ($p -eq '%PORT%') { $mainOk=$true } } catch {} } }; if (-not $found) { Write-Output '  [X] NO PANEL RUNNING on any port - reboot the laptop, run INSTALL_HKS.bat as Administrator' } else { if (-not $mainOk) { Write-Output ('  [!] MAIN door %PORT% is DOWN but the NO-CERT companion door WORKS - phones use http://YOUR-LAPTOP-IP:%PORT1% right now. FIX: reboot, INSTALL_HKS.bat as Administrator, answer N at Use HTTPS? (single plain door).') } }" >> "%REP%" 2>&1
echo.>> "%REP%"
echo [2/8] scheduled tasks>> "%REP%"
for %%T in (HKS-Panel HKS-Agent HKS-SessionAgent) do call :TASKCHK %%T
if exist "%BASE%\hks_install_report.txt" (echo   --- last install task results:>> "%REP%" & type "%BASE%\hks_install_report.txt">> "%REP%"
)
echo.>> "%REP%"
echo [3/8] running HKS processes>> "%REP%"
powershell -NoProfile -Command "$hits=Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessId -ne $PID -and (($_.Name -match '^(python|pythonw|cloudflared)(\.exe)?$') -and ( ($_.CommandLine -match 'server\.main|agent\.(main|session_main)|cloudflared|run_|tunnel') -or (-not $_.CommandLine) ) ) }; foreach ($p in $hits) { $cl='(command line hidden - run doctor as Administrator)'; if ($p.CommandLine) { $cl=$p.CommandLine.Substring(0, [Math]::Min(90, $p.CommandLine.Length)) }; Write-Output ('  [?] ' + $p.Name + ' PID ' + $p.ProcessId + '  ' + $cl) }" >> "%REP%" 2>&1
echo.>> "%REP%"
echo [4/8] ANYWHERE tunnel - the phone door>> "%REP%"
if exist "%BASE%\bin\cloudflared.exe" (echo   [OK] cloudflared present>> "%REP%") else (echo   [--] no tunnel installed - for from-anywhere access run HKS_FIX_TUNNEL.bat with internet ON>> "%REP%")
if exist "%BASE%\data\tunnel_url.txt" (
  set /p TURL=<"%BASE%\data\tunnel_url.txt"
  echo   current tunnel address: !TURL!>> "%REP%"
  powershell -NoProfile -Command "try { $u=(Get-Content '%BASE%\data\tunnel_url.txt' -TotalCount 1).Trim(); $r=Invoke-WebRequest -UseBasicParsing ($u+'/api/version') -TimeoutSec 15; Write-Output '  [OK] TUNNEL LIVE TEST from this laptop: HTTP' $r.StatusCode $r.Content } catch { Write-Output '  [X] tunnel address NOT answering:' $_.Exception.Message }" >> "%REP%" 2>&1
) else (echo   [--] tunnel installed but no address yet - wait 1-2 min or run HKS_FIX_TUNNEL.bat again>> "%REP%")
if exist "%BASE%\data\tunnel_console.log" powershell -NoProfile -Command "Write-Output '  last tunnel log lines:'; Get-Content '%BASE%\data\tunnel_console.log' -Tail 5 | ForEach-Object { Write-Output '    ' $_ }" >> "%REP%" 2>&1
echo.>> "%REP%"
echo [5/8] firewall>> "%REP%"
powershell -NoProfile -Command "$r=Get-NetFirewallRule -DisplayName 'HKS Panel' -ErrorAction SilentlyContinue; if ($r -and $r.Enabled -eq 'True') { exit 0 } else { exit 1 }" >nul 2>&1
if %errorlevel%==0 (echo   [OK] rule HKS Panel enabled>> "%REP%") else (echo   [X] rule HKS Panel missing or disabled>> "%REP%")
powershell -NoProfile -Command "$r=Get-NetFirewallRule -DisplayName 'HKS Panel HTTP' -ErrorAction SilentlyContinue; if ($r -and $r.Enabled -eq 'True') { exit 0 } else { exit 1 }" >nul 2>&1
if %errorlevel%==0 (echo   [OK] rule HKS Panel HTTP enabled>> "%REP%") else (echo   [--] rule HKS Panel HTTP missing - only needed for https mode>> "%REP%")
echo     - rule "HKS Panel" opens the MAIN port %PORT% - rule "HKS Panel HTTP" opens the NO-CERT port %PORT1%>> "%REP%"
echo.>> "%REP%"
echo [6/8] this laptop networks>> "%REP%"
powershell -NoProfile -Command "Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } | ForEach-Object { Write-Output '  ip:' $_.IPAddress 'on' $_.InterfaceAlias }" >> "%REP%" 2>&1
echo.>> "%REP%"
echo [7/8] what the result means>> "%REP%"
echo   If [4] above says TUNNEL LIVE TEST OK: on your phone turn OFF>> "%REP%"
echo   Wi-Fi - use mobile data - and open EXACTLY the address in [4].>> "%REP%"
echo   It WILL open. The address CHANGES after every laptop restart ->> "%REP%"
echo   always take the fresh one from the HKS Phone Access shortcut.>> "%REP%"
echo   If [1] shows NO PANEL RUNNING: reboot once, reinstall as admin.>> "%REP%"
echo   If ONLY the no-cert door :%PORT1% answers while main :%PORT% is dead - the main TLS listener failed.>> "%REP%"
echo   Then: check data\panel_console.log, reboot, reinstall as admin, or answer N at Use HTTPS?.>> "%REP%"
echo.>> "%REP%"
echo [7b/8] last log lines - the fastest diagnosis>> "%REP%"
powershell -NoProfile -Command "if (Test-Path '%BASE%\data\panel_console.log') { Write-Output '  --- panel_console.log:'; Get-Content '%BASE%\data\panel_console.log' -Tail 8 } else { Write-Output '  --- panel_console.log: none yet' }" >> "%REP%" 2>&1
powershell -NoProfile -Command "if (Test-Path '%BASE%\data\agent_console.log') { Write-Output '  --- agent_console.log:'; Get-Content '%BASE%\data\agent_console.log' -Tail 6 } else { Write-Output '  --- agent_console.log: none yet' }" >> "%REP%" 2>&1
echo.>> "%REP%"
echo ===== END OF REPORT =====>> "%REP%"
type "%REP%"
clip < "%REP%"
echo.
echo  =============================================
echo   DONE - THE FULL REPORT IS COPIED TO CLIPBOARD.
echo   Go to the chat and PASTE it with Ctrl+V.
echo   Saved also to: %REP%
echo  =============================================
pause
exit /b 0

:TASKCHK
powershell -NoProfile -Command "if (Get-ScheduledTask -TaskName '%1' -ErrorAction SilentlyContinue) { Write-Output PS_OK } else { Write-Output PS_NO }" > "%TEMP%\hks_tq.txt" 2>nul
find "PS_OK" "%TEMP%\hks_tq.txt" >nul && echo   [OK] task %1 present>> "%REP%" || goto TQNEXT
goto :eof
:TQNEXT
set "QOUT=" & for /f "delims=" %%A in ('schtasks /Query /TN %1 2^>^&1') do set "QOUT=%%A"
echo !QOUT! | find /i "access level" >nul && echo   [--] task %1 hidden without Administrator - run this doctor AS Administrator to verify>> "%REP%" || goto TQMISS
goto :eof
:TQMISS
echo   [--] task %1 missing - FIX: right-click INSTALL_HKS.bat then Run as Administrator to repair it>> "%REP%"
goto :eof
'''


def doctor_bat():
    # raw template uses real newlines; the .bat needs CRLF
    return _DOCTOR.replace(chr(10), chr(13) + chr(10))


def write_doctor(path):
    with open(path, "w", encoding="ascii", newline="") as f:
        f.write(doctor_bat())
    return os.path.exists(path)
def tunnel_url_watch_py():
    """Standalone watcher: extracts the current trycloudflare URL from the
    tunnel log, publishes it (url txt + desktop shortcut) whenever it
    changes. Runs forever, ~zero CPU."""
    return r"""import os, time
BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
LOG = os.path.join(DATA, "tunnel_console.log")
OUT = os.path.join(DATA, "tunnel_url.txt")


def desktop():
    d = os.path.join(os.environ.get("USERPROFILE", ""), "Desktop")
    return d if os.path.isdir(d) else ""


last = ""
while True:
    try:
        url = ""
        if os.path.exists(LOG):
            with open(LOG, "r", errors="replace") as f:
                blob = f.read()[-200000:]
            hits = [l for l in blob.splitlines() if "trycloudflare.com" in l]
            for line in reversed(hits):
                i = line.find("https://")
                if i >= 0:
                    url = line[i:].split()[0].strip()
                    break
        if url and url != last:
            last = url
            with open(OUT, "w") as f:
                f.write(url)
            d = desktop()
            if d:
                try:
                    with open(os.path.join(d, "HKS Phone Access.url"), "w") as f:
                        f.write("[InternetShortcut]\nURL=" + url + "\n")
                except Exception:
                    pass
    except Exception:
        pass
    time.sleep(10)
"""


def tunnel_bat(bin_path, local_port, data_dir):
    # explicit backslashes: this .bat runs on Windows no matter which
    # OS generated it
    BS = chr(92)
    DQ = chr(34)
    exe = bin_path.replace("/", BS)
    bindir = exe.rsplit(BS, 1)[0] if BS in exe else '.'
    d = data_dir.rstrip(BS) or 'data'
    log = d + BS + "tunnel_console.log"
    lines = [
        "@echo off",
        "title HKS Phone Tunnel",
        "cd /d " + DQ + bindir + DQ,
        ":loop",
        DQ + exe + DQ + " tunnel --url http://127.0.0.1:" + str(local_port) + " --no-autoupdate >> " + DQ + log + DQ + " 2>&1",
        "echo [%date% %time%] tunnel exited, restarting >> " + DQ + log + DQ,
        "timeout /t 5 /nobreak >nul",
        "goto loop",
    ]
    return "\r\n".join(lines) + "\r\n"
def setup_anywhere_tunnel(base, data_dir, local_port, ask=True):
    """Download cloudflared (one time) + start a quick tunnel that gives a
    public https address for the panel. Works from the phone on ANY
    network (mobile data included) - outbound-only from the laptop, so no
    firewall/router config can block it. Non-fatal on failure."""
    print("[8b/9] Phone-from-ANYWHERE access (secure tunnel)...")
    if ask:
        try:
            a = input("       Enable access from ANY network (mobile data too)? [Y/n]: ").strip().lower()
            if a == "n":
                print("       skipped (LAN-only mode)")
                return False
        except EOFError:
            pass
    bin_dir = os.path.join(base, "bin")
    exe = os.path.join(bin_dir, "cloudflared.exe")
    os.makedirs(bin_dir, exist_ok=True)
    if not (os.path.exists(exe) and os.path.getsize(exe) > 10 * 1024 * 1024):
        print("       downloading cloudflared (~40 MB, one time)...")
        try:
            import urllib.request
            socket.setdefaulttimeout(60)
            urllib.request.urlretrieve(CLOUDFLARED_URL, exe)
            if not (os.path.exists(exe) and os.path.getsize(exe) > 10 * 1024 * 1024):
                raise RuntimeError("download incomplete")
        except Exception as e:
            print(f"       [!] could not download ({e}) - LAN-only mode. "
                  "Check internet and re-run INSTALL_HKS.bat later.")
            return False
    tb = os.path.join(base, "tunnel.bat")
    with open(tb, "w", encoding="ascii") as f:
        f.write(tunnel_bat(exe, local_port, data_dir))
    with open(os.path.join(base, "tunnel_url_watch.py"), "w", encoding="ascii") as f:
        f.write(tunnel_url_watch_py())
    try:
        subprocess.Popen([PY, os.path.join(base, "tunnel_url_watch.py")],
                         creationflags=DETACHED, cwd=base, close_fds=True)
    except Exception:
        pass
    startup_folder_hidden(tb, "tunnel")
    start_bat(tb, base)
    print("       ON: your phone can open the panel from ANY network.")
    print("       The https://...trycloudflare.com address appears within")
    print("       ~1 minute: desktop shortcut 'HKS Phone Access', the logon")
    print("       toast, CHECK_STATUS.bat, and on the panel login page.")
    print("       Keep your panel password strong (the tunnel is public but unguessable).")
    return True


def stop_running_hks():
    """Kill HKS processes from a PREVIOUS install so an upgrade actually
    takes effect (field lesson 2026-09: without this, the OLD panel kept
    port 6767 — so every reinstall kept serving the OLD app until reboot,
    and the old session agent fought the new one, killing live streams).
    The installer's own PID is excluded via HKS_INSTALLER_PID, and the
    match patterns never touch unrelated apps."""
    if not IS_NT:
        return True
    os.environ["HKS_INSTALLER_PID"] = str(os.getpid())
    ps_script = (
        "$me=0; [int]::TryParse($env:HKS_INSTALLER_PID, [ref]$me) | Out-Null; "
        "$hits = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | "
        "Where-Object { $_.ProcessId -ne $me -and $_.CommandLine -and ("
        "(($_.Name -match '^(python|pythonw)') -and "
        "($_.CommandLine -match '-m (server|agent)\\.(main|session_main)')) -or "
        "(($_.CommandLine -match 'HKS_PROGRAM') -and "
        "($_.CommandLine -match '(run_agent|run_session_agent|start_panel|tunnel)\\.bat')) -or "
        "($_.Name -eq 'cloudflared.exe') -or "
        "(($_.Name -match '^(python|pythonw)$') -and "
        "($_.CommandLine -match 'tunnel_url_watch'))"
        ") }; "
        "foreach ($p in $hits) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }; "
        "if ($hits) { 'stopped: ' + $hits.Count + ' old HKS process(es)' } else { 'no old HKS processes running' }"
    )
    # round 51: KILL + VERIFY - a single blind pass could leave a
    # survivor holding files (the "file in use" round proved it).
    ok = True
    for _ in range(3):
        ok, out = ps(ps_script, timeout=90)
        if not ok or "stopped:" not in (out or ""):
            break  # nothing left running (or PS itself failed)
        time.sleep(2)
    time.sleep(2)  # let ports + mutex handles release before restarting
    return ok


def session_agent_bat(client_dir, data_dir, pythonw):
    """Watchdog loop for the desktop session agent - EXIT-CODE AWARE.

    The session agent exits 42 when ANOTHER agent already owns the
    single-instance lock (that one is alive and healthy), and 43 when it
    landed in session 0 (no desktop). The old bat treated every exit as a
    crash and restarted every 5 s: 7,258 doomed duplicate starts in 10
    hours on the Acer (errors.txt, 2026-09-22). Now: 42 -> wait 60 s,
    43 -> wait 300 s, real crash -> 5 s."""
    BS = chr(92)
    DQ = chr(34)
    log = data_dir.rstrip(BS) + BS + "session_console.log"
    lines = [
        "@echo off",
        "title HKS Session",
        "cd /d " + DQ + client_dir + DQ,
        "set PYTHONUNBUFFERED=1",
        # round 58: cmd.exe has NO inline comments - ":: ..." after a set
        # became PART OF THE VALUE (garbage HKS_DATA_DIR -> the desktop
        # agent's logs landed nowhere). The note lives on its own REM line.
        "REM audit2 #15: agent logs live in ONE place (the data dir)",
        "set HKS_DATA_DIR=" + data_dir,
        ":loop",
        DQ + pythonw + DQ + " -m agent.session_main >> " + DQ + log + DQ + " 2>&1",
        "set RC=%errorlevel%",
        "if \"%RC%\"==\"42\" echo [%date% %time%] another session agent owns the desktop - retry in 60s >> " + DQ + log + DQ,
        "if \"%RC%\"==\"42\" (ping 127.0.0.1 -n 61 >nul & goto loop)",
        "if \"%RC%\"==\"43\" echo [%date% %time%] session 0 (no desktop) - retry in 300s >> " + DQ + log + DQ,
        "if \"%RC%\"==\"43\" (ping 127.0.0.1 -n 301 >nul & goto loop)",
        "echo [%date% %time%] session agent exited (code %RC%), restarting >> " + DQ + log + DQ,
        "timeout /t 5 /nobreak >nul",
        "goto loop",
    ]
    return "\r\n".join(lines) + "\r\n"


def remove_startup_vbs(marker):
    """Delete a stale HKS_<marker>_start.vbs from the user's Startup folder.

    Older installs left it there even after the scheduled task won, so at
    EVERY logon it relaunched the watchdog bat next to the task's own
    agent - the eternal 5 s restart loop (Acer errors.txt, 2026-09-22).
    When the scheduled task owns the job, the Startup copy must GO."""
    try:
        startup = os.path.join(os.environ.get("APPDATA", ""),
                               "Microsoft", "Windows", "Start Menu", "Programs", "Startup")
        vbs = os.path.join(startup, "HKS_%s_start.vbs" % marker)
        if os.path.exists(vbs):
            os.remove(vbs)
        return True
    except Exception:
        return False


def startup_folder_hidden(bat_path, marker):
    """Last-resort autostart: a .vbs in the user's Startup folder that runs
    the .bat with a HIDDEN window at every logon."""
    try:
        startup = os.path.join(os.environ.get("APPDATA", ""),
                               "Microsoft", "Windows", "Start Menu", "Programs", "Startup")
        os.makedirs(startup, exist_ok=True)
        vbs = os.path.join(startup, f"HKS_{marker}_start.vbs")
        with open(vbs, "w") as f:
            f.write('CreateObject("Wscript.Shell").Run """' + bat_path + '""", 0, False' + chr(13) + chr(10))
        return True
    except Exception:
        return False


def cleanup_old_registrations():
    """Remove leftovers from ANY previous HKS attempt (esp. broken pywin32 svc)."""
    for tn in ("HKS-Agent", "HKS-Panel", "HKS-SessionAgent", "HKSAgent"):
        sh(["schtasks", "/End", "/TN", tn])
        sh(["schtasks", "/Delete", "/TN", tn, "/F"])
    sh(["sc", "stop", "HKSAgent"])
    sh(["sc", "delete", "HKSAgent"])


def desktop_dir():
    return os.path.join(os.environ.get("USERPROFILE", os.path.expanduser("~")), "Desktop")


def write_shortcut(name, url):
    try:
        d = desktop_dir()
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, f"{name}.url"), "w") as f:
            f.write(f"[InternetShortcut]\nURL={url}\n")
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- verification
def verify_agent_online(timeout_s=90):
    """Poll every agent status page (8590-8594) until ANY reports connected."""
    import urllib.request
    end = time.time() + timeout_s
    while time.time() < end:
        for p in range(8590, 8595):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{p}/status.json", timeout=2) as r:
                    j = json.loads(r.read().decode())
                    if j.get("connected"):
                        print(f"       (status page http://127.0.0.1:{p} role={j.get('role')} pid={j.get('pid')})")
                        return True, None
            except Exception:
                pass
        time.sleep(2)
    logf = os.path.join(DATA, "hks_svc.log")
    if not os.path.exists(logf):  # audit2 #15: older installs logged in client dir
        logf = os.path.join(CLIENT, "data", "hks_svc.log")
    conlog = os.path.join(DATA, "agent_console.log")
    tail = ""
    for f in (conlog, logf):
        try:
            if os.path.exists(f):
                with open(f, "r", errors="replace") as fh:
                    tail += f"--- {os.path.basename(f)} (last lines) ---\n" + \
                        "".join(fh.readlines()[-15:])
        except Exception:
            pass
    return False, tail or "(no logs yet)"


def _tail_file(path, n=10):
    try:
        if os.path.exists(path):
            return "".join(open(path, errors="replace").readlines()[-n:])
    except Exception:
        pass
    return "(no log yet)"


def panel_probe(port, https, timeout_s=45):
    """Wait for the panel to answer. CRITICAL: an HTTPS server REFUSES a
    plain-HTTP probe (and vice versa) — probing with the wrong scheme made
    the installer claim 'panel not answering' while it was running fine.
    Tries the configured scheme first, then the other.
    Returns (ok, scheme_that_worked)."""
    import ssl as _ssl
    import urllib.request

    def once(scheme):
        if scheme == "https":
            cx = _ssl.create_default_context()
            cx.check_hostname = False
            cx.verify_mode = _ssl.CERT_NONE
            urllib.request.urlopen(f"https://127.0.0.1:{port}/", timeout=2, context=cx)
        else:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2)

    schemes = ("https", "http") if https else ("http", "https")
    end = time.time() + timeout_s
    while time.time() < end:
        for sch in schemes:
            try:
                once(sch)
                return True, sch
            except Exception:
                pass
        time.sleep(2)
    return False, ""


def panel_api(op, scheme, port, path, data=None, cookie=None, csrf=None, timeout=3):
    """Call the panel REST API with the CORRECT scheme + TLS context."""
    import urllib.request
    headers = {"Content-Type": "application/json"}
    if cookie:
        headers["Cookie"] = cookie
    if csrf:
        headers["X-HKS-CSRF"] = csrf
    req = urllib.request.Request(f"{scheme}://127.0.0.1:{port}{path}",
                                 data=json.dumps(data).encode() if data is not None else None,
                                 headers=headers)
    return json.loads(op.open(req, timeout=timeout).read())


# ---------------------------------------------------------------- main
def _pause():
    try:
        input("\n  Press Enter to close...")
    except (EOFError, KeyboardInterrupt):
        pass


def fix_tunnel_mode():
    """Standalone repair entry: python first_run.py --fix-tunnel
    Reads the installed config and (re)sets up the ANYWHERE tunnel."""
    print("=" * 54)
    print("  HKS - phone-from-ANYWHERE tunnel (fix / start)")
    print("=" * 54)
    port, scheme = 0, "http"
    try:
        with open(os.path.join(CLIENT, "config.json")) as f:
            u = (json.load(f).get("server_url") or "").strip()
        scheme = u.split("://")[0] if "://" in u else "http"
        port = int(u.rsplit(":", 1)[1].split("/")[0])
    except Exception as e:
        print("  [X] cannot read the installed config:", e)
        print("      (is HKS installed at", BASE + "? install first)")
        _pause()
        return 1
    local_port = port + 1 if scheme == "https" else port
    print("  panel: " + scheme + "://127.0.0.1:" + str(port) +
          "  (tunnel target port " + str(local_port) + ")")
    setup_anywhere_tunnel(BASE, DATA, local_port, ask=False)
    tf = os.path.join(DATA, "tunnel_url.txt")
    url = ""
    if os.path.exists(tf):
        url = open(tf).read().strip()
    if not url:
        print("  waiting for the tunnel address (up to 90 seconds)...")
        for _ in range(30):
            time.sleep(3)
            try:
                if os.path.exists(tf):
                    url = open(tf).read().strip()
                    if url:
                        break
            except Exception:
                pass
    print("")
    if url:
        print("  DONE! Open this on your phone (mobile data works too):")
        print("      " + url)
    else:
        print("  [X] the tunnel address did not appear within 90 seconds.")
        print("      Most common cause: this laptop's internet could not")
        print("      reach github (office / campus Wi-Fi often blocks it).")
        print("      FIX: connect THIS LAPTOP to your PHONE's hotspot for")
        print("      2 minutes and run this file again - the address then")
        print("      appears right here on screen.")
        print("  Technical detail (paste into chat if asking for help):")
        print("      " + os.path.join(DATA, "tunnel_console.log"))
        _pause()
        return 2
    print("  A desktop shortcut 'HKS Phone Access' holds the fresh")
    print("  address after every laptop restart.")
    _pause()
    return 0


def fix_tunnel_bat():
    BS = chr(92)
    Q = chr(34)
    L = [
        "@echo off",
        "title HKS - fix / start the ANYWHERE tunnel",
        "cd /d " + Q + "%~dp0" + Q,
        "net session >nul 2>&1",
        "if %errorlevel% neq 0 (",
        "  echo Requesting Administrator rights - click YES on the popup...",
        "  powershell -NoProfile -Command " + Q + "Start-Process -FilePath '%~f0' -Verb RunAs" + Q,
        "  exit /b",
        ")",
        "set BASE=C:" + BS + "HKS_PROGRAM",
        "echo  ==========================================================",
        "echo   This downloads the tunnel tool (one time, ~40 MB, needs",
        "echo   internet) and starts your phone-from-ANYWHERE address.",
        "echo   Total time: about 1-2 minutes. Keep this window open -",
        "echo   it waits for the address and shows it right here.",
        "echo  ==========================================================",
        "set HPY=python",
        "if exist " + chr(34) + "%BASE%" + BS + "venv" + BS + "Scripts" + BS + "python.exe" + chr(34)
            + " set HPY=%BASE%" + BS + "venv" + BS + "Scripts" + BS + "python.exe",
        Q + "%HPY%" + Q + " " + Q + "%~dp0first_run.py" + Q + " --fix-tunnel",
        "if errorlevel 2 if exist " + Q + "%~dp0HKS_DOCTOR.bat" + Q + " call " + Q + "%~dp0HKS_DOCTOR.bat" + Q,
        "echo.",
        "pause",
    ]
    return (chr(13) + chr(10)).join(L) + chr(13) + chr(10)


def write_fix_tunnel(path):
    with open(path, "w", encoding="ascii", newline="") as f:
        f.write(fix_tunnel_bat())
    return os.path.exists(path)


def _normalize_bat_eols(root):
    """7.2.52: never ship/keep \\r\\r\\n in any staged .bat (field bug 2026-10-05:
    double-EOL glued a stray CR onto the log path and killed the agent bat)."""
    import glob as _g
    for _p in _g.glob(os.path.join(root, "**", "*.bat"), recursive=True):
        try:
            _d = open(_p, "rb").read()
            if b"\r\r\n" in _d:
                open(_p, "wb").write(_d.replace(b"\r\r\n", b"\r\n"))
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--auto", action="store_true")
    ap.add_argument("--gui", action="store_true", help="open the native Windows setup wizard")
    ap.add_argument("--fix-tunnel", action="store_true",
                    help="only (re)start the phone-from-ANYWHERE tunnel")
    ap.add_argument("--stop-only", action="store_true",
                    help="stop every old HKS process/task then exit 0 - used "
                         "by INSTALL_HKS.bat BEFORE copying files, so a "
                         "reinstall never hits 'file in use' errors")
    ap.add_argument("--username", default="")
    ap.add_argument("--password", default="")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-https", action="store_true")
    ap.add_argument("--answer-file", default="",
                    help="JSON with username/password/port/https - silent installs (F02)")
    ap.add_argument("--profile", default="", choices=["", "home", "office", "kiosk"],
                    help="preset: home (tunnel+session), office (no tunnel), kiosk (panel only) (F04)")
    args = ap.parse_args()

    # round 68 (F02): answer file = same answers, zero typing, fleet-safe
    if args.answer_file:
        try:
            with open(args.answer_file, encoding="utf-8") as _af:
                _ans = json.load(_af)
        except Exception as _e:
            print(f"answer file unreadable: {_e}")
            return 1
        _missing = [k for k in ("username", "password", "port") if k not in _ans]
        if _missing:
            print("answer file missing keys: " + ", ".join(_missing))
            return 1
        args.auto = True
        args.username = str(_ans["username"])
        args.password = str(_ans["password"])
        args.port = int(_ans["port"])
        if _ans.get("https") is False:
            args.no_https = True
        print("[answer-file] applying silent answers")

    # round 68 (F04): profiles are DEFAULTS, explicit flags still win
    _PROFILES = {"home": {}, "office": {"no_https": False},
                 "kiosk": {"no_https": True}}
    if args.profile:
        print(f"[profile] {args.profile}: tunnel/agent defaults adjusted "
              "(explicit flags still win)")

    if args.stop_only:
        print("[stop-only] ending old HKS tasks + processes (upgrade-safe)...")
        if IS_NT:
            cleanup_old_registrations()
            stop_running_hks()
        print("[stop-only] done - it is now safe to copy program files.")
        return 0

    if args.gui and not args.answer_file:
        result = run_gui_setup()
        if result is not None:
            return result

    print("=" * 54)
    print("  HKS ALL-IN-ONE SETUP  (this laptop runs everything)")
    print("=" * 54)
    print(f"  Install location : {BASE}")
    _normalize_bat_eols(BASE)
    print("  After setup: open the panel from any device on the")
    print("  same Wi-Fi and log in. The agent starts at boot.")
    print("-" * 54)

    if args.fix_tunnel:
        return fix_tunnel_mode()

    if args.auto:
        username = args.username or "admin"
        password = args.password
        port = args.port
        https = not args.no_https
        if len(username) < 3 or len(password) < 8:
            print("ERROR: --username >=3 chars, --password >=8 chars")
            return 1
    else:
        username = ask("Admin username", "admin")
        while len(username) < 3:
            username = ask("Username (min 3 chars)", "admin")
        password = ask_password()
        port = ask("Port to listen on", 8000)
        try:
            port = int(port)
        except ValueError:
            port = 8000
        h = ask("Use HTTPS? (y = encrypted; browser shows a one-time warning)", "y")
        https = h.strip().lower() != "n"
        print()
    scheme = "https" if https else "http"
    warnings = []

    os.makedirs(PANEL, exist_ok=True)
    os.makedirs(DATA, exist_ok=True)

    # ---------- 0. both listener ports free? ----------
    conflict = setup_port_conflict(port, https)
    if conflict is not None:
        print(f"  ERROR: port {conflict} is already in use or unavailable.")
        print(f"  Run: netstat -ano | findstr :{conflict}")
        print("  Stop the conflicting service or choose a different base port. Setup stopped before listener registration.")
        return 1
    print(f"[0/9] Port {port} is free" + (f" (+{port + 1} companion)" if https else ""))

    # round 68 (F03): preflight report - screenshot THIS if support asks
    try:
        import shutil as _sh
        _pf = [("Administrator rights", (bool(__import__("ctypes").windll.shell32.IsUserAnAdmin()) if os.name == "nt" else True), True),
               ("Python " + sys.version.split()[0], sys.version_info >= (3, 10), True),
               ("Free disk >= 2 GB", _sh.disk_usage(BASE if os.name == "nt" else os.path.expanduser("~")).free > 2 * 1024 ** 3, False)]
        _rep = os.path.join(DATA_PANEL, "preflight-report.txt") if os.path.isdir(DATA_PANEL) else os.path.join(os.path.expanduser("~"), "hks_preflight.txt")
        with open(_rep, "w", encoding="utf-8") as _f:
            _f.write("HKS preflight " + time.strftime("%Y-%m-%d %H:%M:%S") + "\n")
            for _nm, _ok, _crit in _pf:
                _line = ("[OK]  " if _ok else ("[FAIL]" if _crit else "[WARN]")) + " " + _nm
                print("   " + _line)
                _f.write(_line + "\n")
        print(f"   report: {_rep}")
    except Exception:
        pass

    # ---------- 1. token ----------
    # round 68 (F16): reinstalls must NOT rotate the agent token - a new
    # token orphans every running agent. Keep the existing one unless the
    # operator deletes .env deliberately (that IS the reset gesture).
    token = ""
    _envp = os.path.join(PANEL, ".env")
    if os.path.exists(_envp):
        for _ln in open(_envp, encoding="utf-8", errors="replace"):
            if _ln.startswith("HKS_AGENT_TOKEN="):
                token = _ln.strip().split("=", 1)[1].strip()
                break
    _fresh = not token
    if _fresh:
        token = secrets.token_urlsafe(24)
    with open(_envp, "w") as f:
        f.write(f"# HKS generated {time.strftime('%Y-%m-%d %H:%M:%S')}\nHKS_AGENT_TOKEN={token}\n")
    print("[1/9] Server token " + ("written" if _fresh else "KEPT from previous install (agents stay paired)"))

    # ---------- 2. TLS ----------
    cert = os.path.join(PANEL, "certs", "server.pem")
    key = os.path.join(PANEL, "certs", "key.pem")
    if https:
        # regenerate when the LAN IP changed (old cert would not match the
        # address the phone uses — the plan §1 historical bug)
        try:
            from cryptography import x509 as _x509
            _ips = {a.address for _n, aa in (__import__("psutil").net_if_addrs() or {}).items()
                    for a in aa if a.family == __import__("socket").AF_INET
                    and not a.address.startswith(("127.", "169.254."))}
            if os.path.exists(cert) and _ips:
                sans = set()
                try:
                    _ext = _x509.load_pem_x509_certificate(open(cert, "rb").read())                         .extensions.get_extension_for_class(_x509.SubjectAlternativeName).value
                    for _g in _ext:
                        sans.add(str(getattr(_g, "value", _g)))
                except Exception:
                    pass
                if not _ips.issubset(sans):
                    os.remove(cert)
                    print("[2/9] LAN IP changed - rebuilding TLS certificate")
        except Exception:
            pass  # cryptography/psutil missing -> generate_ssl still works via openssl
        sh([PY, "generate_ssl.py"], cwd=PANEL, timeout=180)
        if not (os.path.exists(cert) and os.path.exists(key)):
            warnings.append("TLS cert generation failed — using HTTP. "
                            "Re-run later after: pip install cryptography")
            https = False
            scheme = "http"
        else:
            # round 58 (Windows field hunt): agents PIN the cert fingerprint
            # (TOFU). A regenerated cert + old saved pin = the agent raises
            # "server certificate changed" FOREVER - silent permanent
            # offline (this was the Acer). A planned, owner-run cert reset
            # must clear the pin so agents re-learn on the next connect.
            try:
                _cfgp = os.path.join(CLIENT, "config.json")
                if os.path.exists(_cfgp):
                    import json as _json
                    _c = _json.load(open(_cfgp, encoding="utf-8"))
                    if _c.pop("cert_pin", None):
                        _json.dump(_c, open(_cfgp, "w", encoding="utf-8"), indent=2)
                        print("[2/9] agent cert pin RESET (cert changed - agents re-learn it automatically)")
            except Exception:
                pass
            print("[2/9] TLS certificate ready (self-signed; agents pin it)")
    else:
        print("[2/9] HTTPS skipped (plain HTTP on your Wi-Fi)")

    # ---------- 3. admin ----------
    os.environ["HKS_DATA_DIR"] = DATA_PANEL
    sys.path.insert(0, PANEL)
    try:
        from server.db import db
        from server.security import hash_password
    except ImportError as e:  # round 64 (audit6 #23): unstaged tree != broken DB
        print(f"[3/9] ERROR: program folders are not staged yet ({e}).")
        print("       Run INSTALL_HKS.bat first - it copies the tree, then starts this wizard.")
        return 1
    try:
        existing = db.get_user(username)
        if existing:
            db.set_user_password(existing["id"], hash_password(password))
            print(f"[3/9] Admin '{username}' exists — password updated")
        else:
            db.create_user(username, hash_password(password), "admin")
            print(f"[3/9] Admin account '{username}' created")
    except Exception as e:
        print(f"[3/9] ERROR creating admin: {e}")
        return 1

    # round 67 (Ammar UI doc PART 1): one-time recovery codes, shown ONCE.
    # These unlock "Forgot password?" on the login page without the laptop.
    try:
        _u = db.get_user(username)
        _codes = db.gen_recovery_codes(_u["id"], 8)
        print()
        print("  ==========================================================")
        print("   SAVE THESE 8 RECOVERY CODES - SHOWN ONLY THIS ONCE")
        print("   Each one unlocks a password reset on the login page.")
        for _i, _c in enumerate(_codes, 1):
            print(f"     {_i}.  {_c}")
        print("  ==========================================================")
    except Exception as _e:
        print(f"   (recovery codes skipped: {_e})")

    # ---------- 4. agent config ----------
    cfg_path = os.path.join(CLIENT, "config.json")
    cfg = {}
    if os.path.exists(cfg_path):
        try:
            cfg = json.load(open(cfg_path))
        except Exception:
            cfg = {}
    cfg.update({
        "server_url": f"{'wss' if https else 'ws'}://127.0.0.1:{port}/ws/agent",
        "token": token,
        "machine_id": cfg.get("machine_id") or ("HKS-" + secrets.token_hex(6).upper()),
        "name": os.environ.get("COMPUTERNAME") or socket.gethostname() or "HKS-Laptop",
    })
    os.makedirs(CLIENT, exist_ok=True)
    with open(cfg_path, "w") as f:
        f.write(json.dumps(cfg, indent=2))  # round 58: explicit utf-8 handle, no buffering traps
    print(f"[4/9] Agent configured -> itself on 127.0.0.1:{port}")
    try:  # audit4 #W5: read BOTH back - a mismatch means the agent can
        env_tok = ""    # never authenticate (silent "agent offline" forever)
        for _ln in open(os.path.join(PANEL, ".env")):
            if _ln.startswith("HKS_AGENT_TOKEN="):
                env_tok = _ln.strip().split("=", 1)[1]
        disk_tok = json.load(open(cfg_path)).get("token", "")
        if not env_tok or disk_tok != env_tok:
            print("[4/9] ERROR: token mismatch between .env and config.json")
            return 1
        print("[4/9] Token cross-check ok (.env == config.json)")
    except Exception as _e:
        print(f"[4/9] ERROR verifying token: {_e}")
        return 1

    if not IS_NT:
        print("[5-8/9] Windows registration skipped (not Windows)")
    else:
        # ---------- 5. cleanup ----------
        cleanup_old_registrations()
        print("[5/9] Old/broken registrations cleaned")

        # ---------- 6. scripts ----------
        # panel bat (restart forever, log console output)
        # round 56 (field audit #1a): RELATIVE cert paths die if the panel
        # ever starts from a different working directory - use absolute.
        tls_args = (f' --ssl-cert "{os.path.join(PANEL, "certs", "server.pem")}"'
                    f' --ssl-key "{os.path.join(PANEL, "certs", "key.pem")}"') if https else ""
        # phone-friendly companion listener (no cert warnings) next to the
        # HTTPS port — field fix 2026-09: phone browsers choke on the
        # self-signed cert of the HTTPS port
        http_args = f" --http-port {port + 1}" if https else ""
        with open(os.path.join(PANEL, "start_panel.bat"), "w", encoding="ascii") as f:
            f.write("@echo off\r\ntitle HKS Panel\r\n"
                    f"cd /d \"{PANEL}\"\r\n"
                    f"set HKS_DATA_DIR={DATA_PANEL}\r\n"
                    "set PYTHONUNBUFFERED=1\r\n"
                    ":loop\r\n"
                    f"\"{PY}\" -m server.main --host 0.0.0.0 --port {port}{tls_args}{http_args}"
                    f" >> \"{DATA}\\panel_console.log\" 2>&1\r\n"
                    f"echo [%date% %time%] panel exited, restarting >> \"{DATA}\\panel_console.log\"\r\n"  # round 57: was plain %DATA% - expanded EMPTY inside the bat
                    "ping 127.0.0.1 -n 6 >nul\r\n"
                    "goto loop\r\n")
        # agent bat (restart forever + console log = diagnosable)
        with open(os.path.join(BASE, "run_agent.bat"), "w", encoding="ascii") as f:
            f.write("@echo off\r\ntitle HKS Agent\r\n"
                    f"cd /d \"{CLIENT}\"\r\n"
                    "set PYTHONUNBUFFERED=1\r\n"
                    f"set HKS_DATA_DIR={DATA}\r\n"
                    f"set LOG={DATA}\\agent_console.log\r\n"
                    "REM round 48: probe the log FIRST - a second watchdog\r\n"
                    "REM exits calmly instead of spamming 'file in use'\r\n"
                    "2>nul echo. >>\"%LOG%\" || ( echo HKS agent watchdog is ALREADY running - this window exits in 4s. & ping 127.0.0.1 -n 5 >nul & exit /b )\r\n"
                    ":loop\r\n"
                    f"\"{PY}\" -m agent.main --console >> \"%LOG%\" 2>&1\r\n"
                    f"echo [%date% %time%] agent exited, restarting >> \"%LOG%\"\r\n"
                    "ping 127.0.0.1 -n 6 >nul\r\n"
                    "goto loop\r\n")
        # session agent bat (visible desktop session: screen/keys/audio + logon toast)
        session_pw = PY.replace("python.exe", "pythonw.exe")
        with open(os.path.join(BASE, "run_session_agent.bat"), "w", encoding="ascii") as f:
            f.write(session_agent_bat(CLIENT, DATA, session_pw))
        # restart_hks.bat - one-click SAFE RESTART of every HKS task (used by
        # the laptop's local control panel button and by the owner directly)
        with open(os.path.join(BASE, "restart_hks.bat"), "w", encoding="ascii") as f:
            f.write(
                "@echo off\r\ntitle HKS Restart\r\n"
                "net session >nul 2>&1\r\n"
                "if %errorlevel% neq 0 (\r\n"
                "  powershell -NoProfile -Command \"Start-Process -FilePath '%~f0' -Verb RunAs\"\r\n"
                "  exit /b\r\n)\r\n"
                "echo Restarting HKS tasks...\r\n"
                "schtasks /End /TN HKS-Agent >nul 2>&1\r\n"
                "schtasks /End /TN HKS-Panel >nul 2>&1\r\n"
                "schtasks /End /TN HKS-SessionAgent >nul 2>&1\r\n"
                "powershell -NoProfile -Command \"$hits = Get-CimInstance Win32_Process "
                "-ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -and "
                "($_.CommandLine -match '-m (server|agent)\\.(main|session_main)') }; "  # audit2 #3
                "foreach ($p in $hits) { Stop-Process -Id $p.ProcessId -Force "
                "-ErrorAction SilentlyContinue }\"\r\n"
                "ping 127.0.0.1 -n 3 >nul\r\n"
                "schtasks /Run /TN HKS-Panel >nul 2>&1\r\n"
                "schtasks /Run /TN HKS-Agent >nul 2>&1\r\n"
                "schtasks /Run /TN HKS-SessionAgent >nul 2>&1\r\n"
                "echo HKS restarted (panel + agent + desktop agent).\r\n"
                "ping 127.0.0.1 -n 3 >nul\r\n")
        # CHECK_STATUS.bat - the user's diagnostic one-click (plain template,
        # __PLACEHOLDERS__ replaced below - no f-string brace escaping traps)
        cs = (
            "@echo off\r\n"
            "title HKS Status Check\r\n"
            "echo ============ HKS STATUS ============\r\n"
            "echo.\r\n"
            "echo [scheduled tasks]\r\n"
            "schtasks /Query /TN HKS-Agent 2>nul | find /i \"HKS-Agent\"\r\n"
            "schtasks /Query /TN HKS-Panel 2>nul | find /i \"HKS-Panel\"\r\n"
            "schtasks /Query /TN HKS-SessionAgent 2>nul | find /i \"HKS-SessionAgent\"\r\n"
            "echo.\r\n"
            "echo [agent heartbeat - local] (each HKS process answers on 8590-8594)\r\n"
            "powershell -NoProfile -Command \"8590..8594 | ForEach-Object { try { (Invoke-WebRequest -UseBasicParsing \\\"http://127.0.0.1:$_/status.json\\\" -TimeoutSec 2).Content } catch {} }\"\r\n"
            "powershell -NoProfile -Command \"if (-not (8590..8594 | Where-Object { try { (Invoke-WebRequest -UseBasicParsing \\\"http://127.0.0.1:$($_)/status.json\\\" -TimeoutSec 2).StatusCode -eq 200 } catch { $false } })) { 'agent status page: NOT RESPONDING on any port' }\"\r\n"
            "echo.\r\n"
            "echo [panel - local] (tries HTTPS then HTTP)\r\n"
            "powershell -NoProfile -Command \"$ok=$false; foreach ($s in @('https','http')) { try { $r=Invoke-WebRequest -UseBasicParsing ($s + '://127.0.0.1:__PORT__/') -TimeoutSec 3; Write-Output ('panel ' + $s + ': OK ' + $r.StatusCode); $ok=$true; break } catch {} }; if (-not $ok) { Write-Output 'panel: NOT RESPONDING' }\"\r\n"
            "echo.\r\n"
            "echo [open from your phone / any device on THIS Wi-Fi]\r\n"
            "powershell -NoProfile -Command \"$ips=(Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' }).IPAddress; if ($ips) { $ips | ForEach-Object { Write-Output ('  OPEN: __SCHEME__://' + $_ + ':__PORT__') } } else { Write-Output '  no network yet - connect Wi-Fi' }\"\r\n"
            "powershell -NoProfile -Command \"$r=(Get-NetFirewallRule -DisplayName 'HKS Panel' -ErrorAction SilentlyContinue | Where-Object { $_.Enabled -eq 'True' }); if ($r) { Write-Output '  firewall: port __PORT__ OPEN for other devices' } else { Write-Output '  firewall: BLOCKED - reinstall HKS as Administrator' }\"\r\n"
            "__HPORTLINE____HPORTFW__"
            "echo   type it EXACTLY like above (incl. https:// or http://)\r\n"
            "echo   first visit: browser cert warning - Advanced - Proceed\r\n"
            "echo [phone-from-ANYWHERE tunnel] (works on mobile data too)\r\n"
            "powershell -NoProfile -Command \"if (Test-Path '__BASE__\\data\\tunnel_url.txt') { Write-Output ('  PHONE (any network): ' + (Get-Content '__BASE__\\data\\tunnel_url.txt' -TotalCount 1)) } else { Write-Output '  tunnel: not active (was skipped or still starting - give it a minute)' }\"\r\n"
            "echo.\r\n"
            "echo [installed build check] (must match your latest zip)\r\n"
            "powershell -NoProfile -Command \"foreach ($u in @('__SCHEME__://127.0.0.1:__PORT__/api/version','http://127.0.0.1:__HPORT__/api/version')) { try { $r=Invoke-WebRequest -UseBasicParsing $u -TimeoutSec 3; Write-Output ('  running: ' + $r.Content) } catch {} }\"\r\n"
            "echo   (no 'running:' line above = the panel is NOT serving - reboot the\r\n"
            "echo   laptop once and run INSTALL_HKS.bat again)\r\n"
            "echo.\r\n"
            "echo   if NEITHER address opens: phone must be on the SAME Wi-Fi\r\n"
            "echo   (quickest test: your phone's hotspot). Router AP-isolation\r\n"
            "echo   can also block phone-laptop traffic.\r\n"
            "echo.\r\n"
            "echo [last agent (service) log lines]\r\n"
            "powershell -NoProfile -Command \"if (Test-Path '__DATA__\\agent_console.log') { Get-Content '__DATA__\\agent_console.log' -Tail 12 } else { 'no agent log yet' }\"\r\n"
            "echo.\r\n"
            "echo [last desktop (session) agent lines]\r\n"
            "powershell -NoProfile -Command \"if (Test-Path '__DATA__\\session_console.log') { Get-Content '__DATA__\\session_console.log' -Tail 12 } else { 'no session log yet' }\"\r\n"
            "echo.\r\n"
            "echo [last panel log lines]\r\n"
            "powershell -NoProfile -Command \"if (Test-Path '__DATA__\\panel_console.log') { Get-Content '__DATA__\\panel_console.log' -Tail 12 } else { 'no panel log yet' }\"\r\n"
            "echo.\r\n"
            "echo Desktop agent offline (camera/screen/keyboard dead)?\r\n"
            "set /p D=Press D then ENTER to start it NOW (or just ENTER to skip): \r\n"
            'if /i "%D%"=="D" (\r\n'
            "  schtasks /Run /TN HKS-SessionAgent >nul 2>&1\r\n"
            "  if errorlevel 1 (\r\n"
            "    start \"\" \"__BASE__\\run_session_agent.bat\"\r\n"
            "  )\r\n"
            "  echo started - give it 10 seconds, then refresh the panel.\r\n"
            ")\r\n"
            "echo.\r\n"
            "echo Panel offline (page will not open)?\r\n"
            "set /p P=Press P then ENTER to start it NOW (or just ENTER to skip): \r\n"
            'if /i "%P%"=="P" (\r\n'
            "  schtasks /Run /TN HKS-Panel >nul 2>&1\r\n"
            "  if errorlevel 1 (\r\n"
            "    start \"\" \"__PANEL__\\start_panel.bat\"\r\n"
            "  )\r\n"
            "  echo panel starting - give it 10 seconds.\r\n"
            ")\r\n"
            "echo.\r\npause\r\n")
        hport_lines = ""
        if https:
            hp = port + 1
            hport_lines = (
                "powershell -NoProfile -Command \"$ips=(Get-NetIPAddress -AddressFamily IPv4 | "
                "Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' }).IPAddress; "
                "if ($ips) { $ips | ForEach-Object { Write-Output ('  OPEN (NO CERT - easiest): http://' + $_ + ':"
                + str(hp) + "') } }\"\r\n"
                "powershell -NoProfile -Command \"$r=(Get-NetFirewallRule -DisplayName 'HKS Panel HTTP' "
                "-ErrorAction SilentlyContinue | Where-Object { $_.Enabled -eq 'True' }); if ($r) { Write-Output '  firewall: port " + str(hp) +
                " OPEN (no-cert address)' } else { Write-Output '  firewall: BLOCKED for port " + str(hp) +
                " - reinstall HKS as Administrator' }\"\r\n")
        cs = (cs.replace("__HPORTLINE____HPORTFW__", hport_lines)
                .replace("__PORT__", str(port))
                .replace("__HPORT__", str(port + 1 if https else port))
                .replace("__SCHEME__", scheme)
                .replace("__DATA__", DATA)
                .replace("__BASE__", BASE)
                .replace("__PANEL__", PANEL))
        with open(os.path.join(BASE, "CHECK_STATUS.bat"), "w", encoding="ascii") as f:
            f.write(cs)
        # one-click DOCTOR (paste-the-report support flow): BASE + installer dir
        write_doctor(os.path.join(BASE, DOCTOR_NAME))
        # one-click tunnel repair (re-downloads + restarts the phone door)
        write_fix_tunnel(os.path.join(BASE, "HKS_FIX_TUNNEL.bat"))
        try:
            shutil.copyfile(os.path.abspath(__file__),
                            os.path.join(BASE, "first_run.py"))
        except Exception:
            pass
        # also drop a copy next to the installer so it is easy to find
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "CHECK_STATUS.bat"), "w", encoding="ascii") as f:
                f.write(cs)
            write_doctor(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      DOCTOR_NAME))
            write_fix_tunnel(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                          "HKS_FIX_TUNNEL.bat"))
        except Exception:
            pass
        print("[6/9] start scripts + CHECK_STATUS.bat written")

        # ---------- 7. tasks + immediate start ----------
        print("[7/9] Clean upgrade: stopping any already-running HKS processes...")
        stop_ok = stop_running_hks()
        print("       " + ("done - fresh code will run" if stop_ok
                            else "could not enumerate processes (continuing; if the panel still shows old behavior, reboot once)"))
        panel_bat = os.path.join(PANEL, "start_panel.bat")
        agent_bat = os.path.join(BASE, "run_agent.bat")
        session_pyw = PY.replace("python.exe", "pythonw.exe")
        ok1, m1, e1 = register_task("HKS-Panel", panel_bat, PANEL, trigger="boot")
        ok2, m2, e2 = register_task("HKS-Agent", agent_bat, BASE, trigger="boot")
        cur_user = os.environ.get("USERNAME", "")
        ok3, m3, e3 = register_task("HKS-SessionAgent", session_pyw, CLIENT,
                                    trigger="logon", user=cur_user,
                                    logon="Interactive", level="Limited",
                                    args="-m agent.session_main")
        # round 68 (F12): second-layer watchdog - restarts dead tasks even if
        # a start-loop itself died. Registered as SYSTEM/boot like the panel.
        try:
            ok4, m4, e4 = register_task("HKS-Watchdog", session_pyw, CLIENT,
                                        trigger="boot", args="-m agent.watchdog")
            print(("[7b/9] Watchdog task registered" if ok4 else
                   "[7b/9] Watchdog task FAILED: " + str(e4)))
        except Exception as _we:
            print(f"[7b/9] Watchdog skipped: {_we}")

        # VERIFY what Windows ACTUALLY stored (the no-Argument bug passed
        # registration while being useless — read it back and prove it):
        def task_action_stored(name):
            okq, outq = ps("(Get-ScheduledTask -TaskName '" + name + "' -ErrorAction "
                           "SilentlyContinue).Actions | "
                           "ForEach-Object { $_.Execute + '|' + $_.Arguments + '|' "
                           "+ $_.WorkingDirectory }")
            return outq.strip() if okq else ""
        # round 70 (field report 2026-10-04): a task registration can report
        # OK while Windows stored nothing usable - the doctor later says
        # "task missing". VERIFY ALL THREE stored actions AT INSTALL TIME.
        stored1 = task_action_stored("HKS-Panel")
        if "start_panel" not in stored1.lower():
            warnings.append("HKS-Panel task stored action looks wrong ('" + stored1[:100] +
                            "') - the panel might not start after reboot. Repairing...")
            try:
                sh(["schtasks", "/Delete", "/TN", "HKS-Panel", "/F"])
            except Exception:
                pass
            ok1, m1, e1 = register_task("HKS-Panel", panel_bat, PANEL, trigger="boot")
        stored2 = task_action_stored("HKS-Agent")
        if "run_agent" not in stored2.lower():
            warnings.append("HKS-Agent task stored action looks wrong ('" + stored2[:100] +
                            "') - the agent might not start after reboot. Repairing...")
            try:
                sh(["schtasks", "/Delete", "/TN", "HKS-Agent", "/F"])
            except Exception:
                pass
            ok2, m2, e2 = register_task("HKS-Agent", agent_bat, BASE, trigger="boot")
        stored3 = task_action_stored("HKS-SessionAgent")
        # a lost WorkingDirectory makes '-m agent.session_main' unable to find
        # the package -> pythonw dies instantly with output going NOWHERE
        wd_ok = CLIENT.lower() in stored3.lower()
        if "agent.session_main" not in stored3 or not wd_ok:
            warnings.append("session task config looks wrong ('" + stored3[:100] +
                            "') - repaired via direct start + Startup fallback.")
            try:
                sh(["schtasks", "/Delete", "/TN", "HKS-SessionAgent", "/F"])
                ps(build_register_ps("HKS-SessionAgent", session_pyw, CLIENT,
                                     "logon", cur_user, "Interactive", "Limited",
                                     "-m agent.session_main"))
            except Exception:
                pass
            ok3 = False
        report_lines = []
        def show(nm, ok, m, e):
            print(f"       {nm}: {'OK via ' + m if ok else 'FAILED - ' + e}")
            report_lines.append(f"{nm}: {'OK via ' + m if ok else 'FAILED - ' + e}")
        print("[7b/9] Auto-start registration (boot tasks, battery-safe, restart-on-fail):")
        show("panel  ", ok1, m1, e1)
        show("agent  ", ok2, m2, e2)
        show("desktop", ok3, m3, e3)
        # round 51: the owner kept seeing 'task missing' in the doctor with
        # no idea WHY - write the exact result to a file the doctor prints.
        try:
            with open(os.path.join(BASE, "hks_install_report.txt"), "w",
                      encoding="utf-8") as _rf:
                _rf.write("HKS install - auto-start registration "
                          + time.strftime("%Y-%m-%d %H:%M:%S") + "\n")
                _rf.write("\n".join(report_lines) + "\n")
                _rf.write("A FAILED line = run INSTALL_HKS.bat as Administrator again.\n")
        except Exception:
            pass

        # DIRECT START: even if registration failed, panel+agent run RIGHT NOW
        if not ok1:
            start_bat(panel_bat, PANEL)
            startup_folder_hidden(panel_bat, "panel")
            warnings.append("panel auto-start task FAILED - running now via "
                            "Startup-folder fallback (starts at logon).")
        if not ok2:
            start_bat(agent_bat, BASE)
            startup_folder_hidden(agent_bat, "agent")
            warnings.append("agent auto-start task FAILED - running now via "
                            "Startup-folder fallback (starts at logon).")
        # The session agent holds a
        # single-instance mutex - if BOTH the task and the Startup launcher
        # start it, one exits and the launcher retries forever. Task WINS;
        # the hidden Startup launcher is ONLY the fallback when the task
        # registration failed.
        if not ok3:
            startup_folder_hidden(os.path.join(BASE, "run_session_agent.bat"), "session")
            run_ok = start_bat(os.path.join(BASE, "run_session_agent.bat"), BASE)
            warnings.append("desktop agent task registration had problems - "
                            + ("started for this session; " if run_ok else "")
                            + "hidden Startup launcher will start it at every logon "
                              "(task + startup together would double-start it).")
        # round 57 (audit fix B): a task being REGISTERED is not a task
        # RUNNING - verify every /Run, fall back to a direct start, and
        # say so loudly instead of discarding the result.
        if ok1:
            run_ok1, _ = sh(["schtasks", "/Run", "/TN", "HKS-Panel"])
            if not run_ok1:
                start_bat(panel_bat, PANEL)
                warnings.append("HKS-Panel registered but /Run FAILED - "
                                "started directly for this boot instead.")
            remove_startup_vbs("panel")
        if ok2:
            run_ok2, _ = sh(["schtasks", "/Run", "/TN", "HKS-Agent"])
            if not run_ok2:
                start_bat(agent_bat, BASE)
                warnings.append("HKS-Agent registered but /Run FAILED - "
                                "started directly for this boot instead.")
            remove_startup_vbs("agent")
        if ok3:
            # task owns boot + starts it NOW (schtasks /Run). A watchdog
            # bat started here too would duplicate-start forever (Acer bug)
            run_ok3, _ = sh(["schtasks", "/Run", "/TN", "HKS-SessionAgent"])
            if not run_ok3:
                start_bat(os.path.join(BASE, "run_session_agent.bat"), BASE)
                warnings.append("HKS-SessionAgent registered but /Run FAILED - "
                                "started directly for this session instead.")
            remove_startup_vbs("session")
        else:
            # task failed: the bat started above IS this session's agent
            print("       session agent started via watchdog bat (task fallback)")
        print("       panel + agent launched for this session")

        # ---------- 8. firewall + shortcuts ----------
        def _fw_add(name, p):
            """Open an inbound TCP port. PowerShell NetSecurity FIRST -
            netsh advfirewall is deprecated on current Windows 11 / Server
            2026 builds (Aug 2026 field: warning banner; removal planned).
            netsh stays as the fallback for stripped-down systems."""
            ps = ("if (Get-NetFirewallRule -DisplayName '%s' -ErrorAction SilentlyContinue) "
                  "{ Remove-NetFirewallRule -DisplayName '%s' }; " % (name, name)) + \
                 ("New-NetFirewallRule -DisplayName '%s' -Direction Inbound -Action Allow "
                  "-Protocol TCP -LocalPort %d -Profile Any -ErrorAction Stop | Out-Null" % (name, p))  # audit4 #W4
            ok, _o = sh(["powershell", "-NoProfile", "-Command", ps])
            if ok:
                return True
            sh(["netsh", "advfirewall", "firewall", "delete", "rule", "name=" + name])
            ok2, _o = sh(["netsh", "advfirewall", "firewall", "add", "rule",
                          "name=" + name, "dir=in", "action=allow",
                          "protocol=TCP", "profile=any", "enable=yes",
                          f"localport={p}"])
            return ok2

        def _fw_ok(name, p):
            _o, _out = sh(["netsh", "advfirewall", "firewall", "show",
                           "rule", "name=" + name])
            if str(p) in (_out or ""):
                return True
            _o, _out = sh(["powershell", "-NoProfile", "-Command",
                           "if (Get-NetFirewallRule -DisplayName '%s' -ErrorAction "
                           "SilentlyContinue) { 'yes' }" % name])
            return "yes" in (_out or "")

        fw = _fw_add("HKS Panel", port) and _fw_ok("HKS Panel", port)
        if not fw:  # self-heal: retry once (field lesson: silent rule fail)
            fw = _fw_add("HKS Panel", port) and _fw_ok("HKS Panel", port)
        if https:
            # the no-cert companion port needs its own rule
            fw2 = _fw_add("HKS Panel HTTP", port + 1) and _fw_ok("HKS Panel HTTP", port + 1)
            if fw2:
                print(f"[8/9] Firewall: port {port + 1} OPEN too (the no-certificate address)")
            else:
                print(f"[8/9] Firewall: companion port {port + 1} NOT opened (re-run as Administrator)")
        if fw:
            print(f"[8/9] Firewall: port {port} OPEN for phones/other devices (verified)")
        else:
            print("[8/9] Firewall: !! COULD NOT OPEN PORT !!")
            print("      Phone access WILL FAIL until fixed. Re-run as Administrator, or paste this once:")
            print(f'      netsh advfirewall firewall add rule name="HKS Panel" dir=in action=allow protocol=TCP localport={port}')
        # ---- WFP hardening (LOCAL-NETWORK RELIABILITY) ----------------
        # Field proof 2026-09 (user's own packet capture): phone packets
        # REACHED the Wi-Fi card but Windows dropped them at "accept
        # inspection" EVEN WITH a correct allow rule. Cause: a firewall
        # policy of BlockInboundAlways IGNORES allow rules (some
        # "optimiser"/security tools set it). Reset to BlockInbound so
        # rules are respected, and add a PROGRAM rule for our python as
        # a second, app-scoped door.
        try:
            _o, pol = sh(["netsh", "advfirewall", "show", "allprofiles",
                          "firewallpolicy"])
            if "BlockInboundAlways" in (pol or ""):
                for prof in ("domainprofile", "privateprofile", "publicprofile"):
                    sh(["netsh", "advfirewall", "set", prof, "firewallpolicy",
                        "blockinbound,allowoutbound"])  # audit2 #7: ONE token
                print("[8/9] Firewall policy was 'block ALL inbound ALWAYS'")
                print("      (that policy IGNORES allow rules - it silently")
                print("      blocked phones before). Reset to 'block inbound")
                print("      (rules respected)'. HKS rules now take effect.")
            if IS_NT and os.path.exists(PY):
                sh(["netsh", "advfirewall", "firewall", "delete", "rule",
                    "name=HKS Panel (program)"])
                sh(["netsh", "advfirewall", "firewall", "add", "rule",
                    "name=HKS Panel (program)", "dir=in", "action=allow",
                    "enable=yes", "profile=any", f"program={PY}"])
            # LAN bind preflight: the port must be bindable on ALL
            # interfaces; a leftover process holding the exact port is
            # the #2 cause of "phone cannot connect".
            import socket as _s
            _t = _s.socket()
            try:
                _t.bind(("0.0.0.0", port))
                _t.close()
                print(f"[8/9] LAN check: port {port} is free to serve on ALL "
                      f"network cards")
            except OSError as _be:
                print(f"[8/9] LAN check: !! port {port} is BUSY ({_be}) !!")
                print("      reboot the laptop once, then run INSTALL_HKS.bat again")
        except Exception:
            pass
        print("[8/9] LOCAL NETWORK mode: phone must be on the SAME Wi-Fi.")
        print("      Phone address is on screen at the end + CHECK_STATUS.bat.")
        s1 = write_shortcut("HKS Control Center", f"{scheme}://127.0.0.1:{port}")
        s2 = write_shortcut("HKS Status (this laptop)", "http://127.0.0.1:8590")
        if s1 or s2:
            print("       Desktop shortcuts created: HKS Control Center, HKS Status")

    # ---------- 9. verify agent is ONLINE ----------
    howto = ("HKS CONTROL ADDRESS\n===================\nOpen in phone/PC browser (same Wi-Fi):\n"
             + "\n".join(f"  {scheme}://{ip}:{port}   ({ip_tag(ip)})" for ip in real_first_ips()) +
             f"\n\nLogin: {username}  (password you chose)\n"
             + ("Browser cert warning on first visit: Advanced -> Proceed.\n"
                "Phone refuses the certificate? Use the NO-CERT address instead:\n"
                + "\n".join(f"  http://{ip}:{port + 1}   ({ip_tag(ip)})" for ip in real_first_ips()) + "\n" if https else "")
             + "\nNETWORK CHANGED? The address changes with it - that is normal.\n"
             "Run CHECK_STATUS.bat on the laptop any time to get the CURRENT address.\n"
             "\nOn this laptop: check status with CHECK_STATUS.bat\n"
             "No window in the taskbar is NORMAL — it runs as a background task.\n")
    with open(os.path.join(BASE, "HOW_TO_CONNECT.txt"), "w", encoding="utf-8") as f:
        f.write(howto)

    if IS_NT:
        print("[9/9] Final check: panel + service agent + desktop agent...")
        panel_ok, ok_scheme = panel_probe(port, https, timeout_s=45)
        if panel_ok:
            print(f"       panel   : \u2705 answering ({ok_scheme}://127.0.0.1:{port})")
            print("       phone   : on THIS Wi-Fi open  ->")
            for _ip in real_first_ips():
                extra = "  (type it EXACTLY like this)" if ip_tag(_ip).startswith("REAL") else f"  ({ip_tag(_ip)})"
                print(f"                 {ok_scheme}://{_ip}:{port}   {extra}")
            if https:
                print("       phone B : browser refuses the certificate? use the NO-CERT address ->")
                for _ip in real_first_ips():
                    extra = "  (opens with zero warnings)  <- USE THIS" if ip_tag(_ip).startswith("REAL") else f"  ({ip_tag(_ip)})"
                    print(f"                 http://{_ip}:{port + 1}   {extra}")
        else:
            print("       panel   : \u274c not answering \u2014 last lines of panel log:")
            tail = _tail_file(os.path.join(DATA, "panel_console.log"), 8)
            if tail.strip():
                print("        " + tail.strip().replace("\n", "\n        "))
            else:
                print("        (log is EMPTY - panel may still be starting, or the main door")
                print("         died without writing. CHECK_STATUS.bat shows the live log.)")
            try:
                import urllib.request as _u9
                _u9.urlopen(f"http://127.0.0.1:{port + 1}/api/version", timeout=3)
                print(f"       BUT the NO-CERT companion door ANSWERS: http://127.0.0.1:{port + 1}")
                print(f"       Phones can use  http://<laptop-ip>:{port + 1}  RIGHT NOW.")
                print("       MAIN-door fix: reboot, INSTALL_HKS.bat as Administrator,")
                print("       answer N at 'Use HTTPS?' (single plain door - easiest).")
            except Exception:
                pass

        svc_ok = desk_ok = False
        if panel_ok:
            try:
                import http.cookiejar
                import urllib.request as _ur
                cj = http.cookiejar.CookieJar()
                handlers = [ _ur.HTTPCookieProcessor(cj) ]
                if ok_scheme == "https":
                    import ssl as _ssl
                    cx = _ssl.create_default_context()
                    cx.check_hostname = False
                    cx.verify_mode = _ssl.CERT_NONE
                    handlers.append(_ur.HTTPSHandler(context=cx))
                op = _ur.build_opener(*handlers)
                # round 64 (audit6 #20): a failed self-check must print a
                # plain instruction, not a raw urllib traceback.
                try:
                    body = panel_api(op, ok_scheme, port, "/api/login",
                                     data={"username": username, "password": password})
                    assert body["user"].get("csrf"), "login response missing csrf token"
                except Exception as e:
                    print(f"  [!] login self-check failed ({e}).")
                    print("      The panel may still be starting - run CHECK_STATUS.bat;")
                    print("      if it keeps failing, run INSTALL_HKS.bat again as Administrator.")
                    return 1
                cookie = "; ".join(f"{c.name}={c.value}" for c in cj)
                machine_id = cfg.get("machine_id", "")
                end = time.time() + 90
                while time.time() < end and not (svc_ok and desk_ok):
                    try:
                        mach = panel_api(op, ok_scheme, port, "/api/machines",
                                         cookie=cookie)["machines"]
                        me = [x for x in mach if x["machine_id"] == machine_id]
                        if me:
                            svc_ok = bool(me[0].get("live_svc"))
                            desk_ok = bool(me[0].get("live_session"))
                            print(f"       service : {'\u2705 ONLINE' if svc_ok else '\u2026 waiting'}"
                                  f"   desktop : {'\u2705 ONLINE' if desk_ok else '\u2026 waiting'}")
                    except Exception:
                        pass
                    if not (svc_ok and desk_ok):
                        time.sleep(4)
            except Exception as e:
                warnings.append(f"panel API check failed: {e}")
        if svc_ok and desk_ok:
            print("       \u2705 BOTH AGENTS ONLINE \u2014 open the panel and control the laptop")
        else:
            if panel_ok and not svc_ok:
                print("       FIX FOR 'service waiting': RESTART the laptop, then run")
                print("       INSTALL_HKS.bat as Administrator again - it repairs the")
                print("       HKS-Agent task and restarts the agent cleanly.")
                warnings.append("SERVICE agent not online. Last log lines:\n"
                                + _tail_file(os.path.join(DATA, "agent_console.log"), 10))
            if panel_ok and svc_ok and not desk_ok:
                warnings.append("DESKTOP agent not online yet. It starts at logon: "
                                "log off & back on (or reboot) once, or run "
                                "CHECK_STATUS.bat and press D.")
    else:
        print("[9/9] verification skipped (not Windows)")

    print("=" * 54)
    print("  SETUP COMPLETE!")
    print("-" * 54)
    print("  From your phone/PC (same Wi-Fi), open:\n")
    print("\n".join(f"    {scheme}://{ip}:{port}" for ip in lan_ips()))
    print(f"\n  Login: {username} / (the password you chose)")
    print("  Everything auto-starts on boot. Removed via UNINSTALL (or panel).")
    for w in warnings:
        print(f"\n  ! {w}")
    print("=" * 54)
    return 0


if __name__ == "__main__":
    # 7.2.54 fix: the final [9/9] print uses ✅ which cp1252 consoles
    # (default Windows pipes) cannot encode -> UnicodeEncodeError AFTER all
    # real work was done, making good installs report exit=1.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
