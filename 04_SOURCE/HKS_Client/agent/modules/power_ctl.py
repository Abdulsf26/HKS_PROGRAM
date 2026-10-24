"""Power: sleep / hibernate / restart / shutdown / display / wake scheduling."""
import ctypes
import os
import re
import time


def register(ctx):
    def status(c, p):
        import psutil
        bat = psutil.sensors_battery()
        d = {"boot": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(psutil.boot_time())),
             "uptime_h": round((time.time() - psutil.boot_time()) / 3600, 1)}
        if bat:
            d["battery_percent"] = bat.percent
            d["plugged"] = bat.power_plugged
        out = _run("powercfg /getactivescheme")
        d["power_scheme"] = out.strip()[:120]
        sb = _run("bcdedit /enum {current}")
        d["safeboot_pending"] = "safeboot" in sb.lower()
        return d

    def safeboot_on(c, p):
        mode = p.get("mode", "minimal")  # minimal | network
        if mode not in ("minimal", "network", "repair"):
            raise ValueError("mode must be minimal|network|repair")
        out = _run(f"bcdedit /set {{current}} safeboot {mode}")
        if "completed successfully" not in out.lower():
            raise RuntimeError("bcdedit failed: " + out[:200])
        c.send_event("safeboot_armed", {"mode": mode})
        return {"armed": mode, "note": "Next restart boots Safe Mode. Use 'Back to "
                                       "normal boot' + restart to return."}

    def safeboot_off(c, p):
        out = _run("bcdedit /deletevalue {current} safeboot")
        low = out.lower()
        ok = ("completed successfully" in low) or ("not found" in low) or (out.strip() == "")
        c.send_event("safeboot_disarmed", {"ok": ok})
        return {"disarmed": ok, "output": out[:200]}

    def logoff_console(c, p):
        """Log off the console user from the SYSTEM service (best effort).
        Strategy (audit4 #P1): use the CONCRETE session id first -
        'logoff console' succeeds silently while doing nothing on some
        Windows builds. Name-based logoff is only the fallback."""
        sid = _console_session_id()
        if sid is not None:
            res = _run(f"logoff {sid}")
            c.send_event("logoff_request", {"session": sid})
            return {"logoff": sid, "output": res[:200]}
        res = _run("logoff console")
        if "no session" not in res.lower() and "error" not in res.lower():
            c.send_event("logoff_request", {"session": "console"})
            return {"logoff": "console", "output": res[:200]}
        raise RuntimeError("No logged-on console user found (is anyone "
                           "logged into Windows right now?)")
        c.send_event("logoff_request", {"session": sid})
        return {"logoff": sid, "output": res[:200]}

    def _console_session_id():
        for cmd in ("quser", "qwinsta"):
            out = _run(cmd)
            for line in out.splitlines():
                if "console" not in line.lower():
                    continue
                m = re.search(r"console\s+>?\s*(\d+)", line, re.I)
                if m:
                    return m.group(1)
        return None

    def sleep(c, p):
        # ensure hibernate doesn't hijack sleep; then suspend
        _run("powercfg /change standby-timeout-ac 0")  # don't fight our manual request
        ctypes.windll.PowrProf.SetSuspendState(False, False, False)
        c.send_event("sleep", {"type": "sleep"})
        return {"done": True}

    def hibernate(c, p):
        _run("powercfg /hibernate on")
        ctypes.windll.PowrProf.SetSuspendState(True, False, False)
        c.send_event("sleep", {"type": "hibernate"})
        return {"done": True}

    def restart(c, p):
        c.send_event("shutdown_request", {"type": "restart"})
        import subprocess
        subprocess.Popen(["shutdown", "/r", "/t", "5", "/c", "HKS remote restart"],
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return {"scheduled": "restart in 5s"}

    def shutdown(c, p):
        sec = min(int(p.get("seconds", 10)), 3600)
        c.send_event("shutdown_request", {"type": "shutdown", "seconds": sec})
        import subprocess
        subprocess.Popen(["shutdown", "/s", "/t", str(sec), "/c", "HKS remote shutdown"],
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return {"scheduled": f"shutdown in {sec}s (cancellable)"}

    def shutdown_cancel(c, p):
        return {"output": _run("shutdown /a")}

    def display_off(c, p):
        # plain ctypes - no pywin32 needed (PostMessageW is a frozen
        # user32 export; field audit 2026-09-23 dropped the win32gui dep)
        HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER = 0xFFFF, 0x0112, 0xF170
        try:
            import ctypes
            ctypes.windll.user32.PostMessageW(HWND_BROADCAST, WM_SYSCOMMAND,
                                              SC_MONITORPOWER, 2)
        except Exception:
            pass
        return {"done": True}

    def sleep_until(c, p):
        """Sleep now; auto-wake at the given ISO time via a one-shot scheduled task
        with 'wake to run' enabled (works like an alarm-clock wake signal)."""
        wake_at = p["wake_at"].replace("Z", "+00:00")
        # convert ISO to local "HH:mm yyyy/MM/dd" for schtasks
        lt = time.localtime(_parse_iso(wake_at))
        stamp = time.strftime("%H:%M", lt)
        date = time.strftime("%Y/%m/%d", lt)
        name = "HKS-WakeSignal"
        out1 = _run(f'schtasks /Create /TN {name} /TR "cmd /c exit" /SC ONCE /ST {stamp} /SD {date} '
                    f'/RU SYSTEM /RL HIGHEST /F /V1')
        chk = _run(f'schtasks /Query /TN {name}')  # audit4 #P2: do not sleep
        if name.lower() not in chk.lower():        # forever on a dead task
            raise RuntimeError(f"wake task could not be created - PC would sleep with no alarm. "
                               f"schtasks said: {(out1 or chk)[:200]}")
        _run(f'powershell -NoProfile -Command "$t=Get-ScheduledTask -TaskName \'{name}\'; '
             f'$s=($t | Get-ScheduledTaskInfo); '
             f'(New-ScheduledTaskSettingsSet -WakeToRun -AllowStartIfOnBatteries '
             f'-DontStopIfGoingOnBatteries) | Set-ScheduledTask -TaskName \'{name}\'"')
        c.send_event("sleep", {"type": "sleep_until", "wake_at": p["wake_at"]})
        ctypes.windll.PowrProf.SetSuspendState(False, False, False)
        return {"wake_task": name, "wake_at": p["wake_at"]}

    def wake_timers(c, p):
        return {"items": _run("powercfg /waketimers").splitlines()}

    def battery_report(c, p):
        out = os.path.join(os.environ.get("PROGRAMDATA", os.path.expanduser("~")),
                           "HKS_battery_report.html")
        _run(f"powercfg /batteryreport /output \"{out}\"")
        return {"path": out}

    return {"power.status": status, "power.sleep": sleep, "power.hibernate": hibernate,
            "power.restart": restart, "power.shutdown": shutdown,
            "power.shutdown_cancel": shutdown_cancel, "power.display_off": display_off,
            "power.sleep_until": sleep_until, "power.wake_timers": wake_timers,
            "power.battery_report": battery_report,
            "power.safeboot_on": safeboot_on,
            "power.safeboot_off": safeboot_off,
            "power.logoff_console": logoff_console}


def _run(cmd, timeout=30):
    import subprocess
    p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout,
                       errors="replace",
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return (p.stdout or "") + (p.stderr or "")


def _parse_iso(s):
    import datetime
    return datetime.datetime.fromisoformat(s).timestamp()
