"""Live telemetry + system info (psutil; Windows-aware)."""
import os
import subprocess
import time


def _cim(wmi_class, wmi_prop):
    """Read one property via PowerShell CIM (works on new Win 11 builds where
    the old `wmic` tool was removed). Returns the first non-empty value or ''."""
    if os.name != "nt":
        return ""
    try:
        cmd = ("powershell -NoProfile -NonInteractive -Command "
               f"(Get-CimInstance {wmi_class} | "
               f"ForEach-Object {{ $_.{wmi_prop} }} | Where-Object {{ $_ }} | "
               "Select-Object -First 1)")
        try:  # bounded instead of os.popen (audit3)
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                               timeout=10, errors="replace")
            return (r.stdout or "").strip()
        except Exception:
            return ""
    except Exception:
        return ""


def snapshot():
    """Telemetry snapshot for the dashboard.

    Field lesson (Acer, 2026-09): any single failing sub-call (e.g.
    psutil.sensors_battery) used to nuke the WHOLE payload via the outer
    except -> return None -> the panel never received telemetry and the
    CPU/MEM/BATTERY cards stayed empty. Every field is now independent
    and we always return a dict when at least one field worked."""
    out = {}
    try:
        import psutil
    except Exception:
        return None
    try:
        out["cpu"] = psutil.cpu_percent(interval=0.4)
    except Exception:
        pass
    try:
        out["ram"] = psutil.virtual_memory().percent
    except Exception:
        pass
    try:
        bat = psutil.sensors_battery()
        out["bat_pct"] = None if bat is None else bat.percent
        out["bat_plugged"] = None if bat is None else bool(bat.power_plugged)
    except Exception:
        out["bat_pct"] = None
        out["bat_plugged"] = None
    try:
        net = psutil.net_io_counters()
        out["net_sent"] = net.bytes_sent
        out["net_recv"] = net.bytes_recv
    except Exception:
        pass
    try:
        out2 = dict(out)
        ssid = ""
        try:  # bounded - netsh hung once on a stuck WLAN service (audit3)
            r = subprocess.run(["netsh", "wlan", "show", "interfaces"],
                               capture_output=True, text=True, timeout=5,
                               errors="replace")
            out3 = r.stdout or ""
        except Exception:
            out3 = ""
        for line in out3.splitlines():
            if "SSID" in line and "BSSID" not in line:
                ssid = line.split(":", 1)[1].strip()
                break
        out2["ssid"] = ssid
        out = out2
    except Exception:
        pass
    try:
        for addrs in psutil.net_if_addrs().values():
            for a in addrs:
                if a.family.name == "AF_INET" and not a.address.startswith("127."):
                    out["ip"] = a.address
                    break
            if out.get("ip"):
                break
    except Exception:
        pass
    return out or None


def _register(ctx):
    def quick(c, p):
        import psutil
        user = os.environ.get("USERNAME", "SYSTEM")
        uptime = time.time() - psutil.boot_time()
        h, m = int(uptime // 3600), int((uptime % 3600) // 60)
        return {"user": user, "ip": _first_ip(), "uptime": f"{h}h {m}m",
                "processes": str(len(psutil.pids())), "ram": f"{psutil.virtual_memory().percent}%"}

    def info(c, p):
        import platform
        import psutil
        d = {}
        d["hostname"] = platform.node()
        d["os"] = f"{platform.system()} {platform.release()} ({platform.version()})"
        cpu = platform.processor()
        if not cpu:  # some systems return '' from platform - ask CIM instead
            cpu = _cim("Win32_Processor", "Name") or "unknown CPU"
        d["cpu"] = cpu
        d["cpu_cores"] = psutil.cpu_count(logical=False)
        d["cpu_threads"] = psutil.cpu_count()
        vm = psutil.virtual_memory()
        d["ram_total_gb"] = round(vm.total / 2 ** 30, 2)
        d["ram_used_percent"] = vm.percent
        d["swap_percent"] = psutil.swap_memory().percent
        d["disks"] = [{"dev": x.device, "mount": x.mountpoint, "total_gb": round((psutil.disk_usage(x.mountpoint).total or 0) / 2 ** 30, 1),
                       "free_gb": round(psutil.disk_usage(x.mountpoint).free / 2 ** 30, 1)}
                      for x in psutil.disk_partitions() if "cdrom" not in x.opts]
        bat = psutil.sensors_battery()
        if bat:
            d["battery"] = {"percent": bat.percent, "plugged": bat.power_plugged,
                            "secs_left": bat.secsleft if isinstance(bat.secsleft, int) else "calc"}
        d["boot_time"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(psutil.boot_time()))
        d["network"] = _net_summary()
        d["python"] = platform.python_version()
        d["agent_pid"] = os.getpid()
        try:
            d["gpu"] = _cim("Win32_VideoController", "Name") or None
            if not d["gpu"]:
                del d["gpu"]
        except Exception:
            pass
        return d

    return {"sys.quick": quick, "sys.info": info}


def _first_ip():
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("1.1.1.1", 443))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _net_summary():
    try:
        import psutil
        ifs = []
        stats = psutil.net_if_stats()
        addrs = psutil.net_if_addrs()
        for name, st in stats.items():
            ip = ""
            for a in addrs.get(name, []):
                if a.family.name == "AF_INET":
                    ip = a.address
            ifs.append({"name": name, "up": bool(st.isup), "speed": st.speed, "ip": ip})
        return ifs
    except Exception:
        return []


def register(ctx):
    return _register(ctx)
