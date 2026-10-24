"""Deep hardware status: CPU load + temperature, GPU, per-core, battery wear,
disk space, top processes. Periodic low-rate reports + threshold alerts.
Designed for 24/7: sampling is tiny (0.15s) and the default report period is
120s so it never meaningfully heats or loads the machine."""
import time

_alert_state = {"battery_low": 0.0, "disk_low": 0.0, "ram_high": 0.0}


_temp_cache = {"ts": 0.0, "val": None}


def _cpu_temp():
    """Best-effort CPU temperature (°C) or None if unsupported.
    Cached 5 s - the PowerShell probe costs ~250 ms and temps move slowly."""
    if time.time() - _temp_cache["ts"] < 5:
        return _temp_cache["val"]
    val = _cpu_temp_real()
    _temp_cache["ts"], _temp_cache["val"] = time.time(), val
    return val


def _cpu_temp_real():
    try:
        import psutil
        temps = psutil.sensors_temperatures() if hasattr(psutil, "sensors_temperatures") else {}
        for grp in ("coretemp", "k10temp", "cpu_thermal", "acpitz"):
            if grp in temps and temps[grp]:
                return round(temps[grp][0].current, 1)
    except Exception:
        pass
    try:
        # Windows thermal zone (tenths of Kelvin)
        out = _ps("Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature "
                  "-ErrorAction Stop | Select-Object -First 1 -ExpandProperty CurrentTemperature")
        if out.strip():
            return round(float(out.strip()) / 10.0 - 273.15, 1)
    except Exception:
        pass
    return None


def _gpu():
    gpus = []
    try:
        out = _ps("Get-CimInstance Win32_VideoController | Select-Object Name,Status,DriverVersion,"
                  "AdapterRAM | ConvertTo-Json -Compress")
        import json
        data = json.loads(out) if out.strip() else []
        if isinstance(data, dict):
            data = [data]
        for d in data:
            raw = d.get("AdapterRAM") or 0
            # round 62 (audit5 #10) + round 63 (Ammar patch review): AdapterRAM is
            # UInt32 -> >4GB cards report modulo-4GB garbage, and 0 means WMI has
            # NO idea. Both are honest-unknown; hide rather than lie (UI renders
            # a missing vram_gb as "-").
            capped = raw >= 3.9 * 2 ** 30 or raw <= 0
            gpus.append({"name": (d.get("Name") or "?")[:60],
                         "status": d.get("Status") or "?",
                         "driver": d.get("DriverVersion") or "?",
                         "vram_gb": None if capped else round(raw / 2 ** 30, 1),
                         "vram_note": "WMI 4GB cap" if capped else ""})
    except Exception:
        pass
    nv = _nvidia()
    if nv:
        gpus.append(nv)
    return gpus


_nv_primed = [False]  # round 65 (audit7 #36)


def _nvidia():
    try:
        import shutil
        import subprocess
        exe = shutil.which("nvidia-smi")
        if not exe:
            return None
        p = subprocess.run([exe, "--query-gpu=name,temperature.gpu,utilization.gpu,memory.used,memory.total",
                            "--format=csv,noheader,nounits"], capture_output=True, text=True,
                           timeout=10,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        name, temp, util, mem, tot = (p.stdout.strip().split(",") + ["?"] * 5)[:5]
        try:
            vram = round(float(tot) / 1024, 1)  # audit5 #10: nvidia-smi is UInt64-honest
        except Exception:
            vram = None
        if not _nv_primed[0]:
            # first nvidia-smi sample after driver idle reports 0% util -
            # discard it so the panel never shows a fake zero (audit7 #36)
            _nv_primed[0] = True
            return None
        return {"name": name.strip()[:60] + " (live)", "status": "OK",
                "temp_c": float(temp), "util_pct": float(util), "mem_used_mb": float(mem),
                "vram_gb": vram}
    except Exception:
        return None


def _battery_wear():
    try:
        out = _ps("(Get-CimInstance -Namespace root/wmi -ClassName BatteryStaticData | "
                  "Select-Object -First 1 -ExpandProperty DesignedCapacity);"
                  "(Get-CimInstance -Namespace root/wmi -ClassName BatteryFullChargedCapacity | "
                  "Select-Object -First 1 -ExpandProperty FullChargedCapacity)")
        nums = [int(x) for x in out.split() if x.strip().isdigit()]
        if len(nums) == 2 and nums[0] > 0:
            design, full = nums
            return {"design_mwh": design, "full_mwh": full,
                    "wear_pct": round(100.0 * (1 - full / design), 1)}
    except Exception:
        pass
    return None


def _ps(cmd, timeout=25):
    import subprocess
    p = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                       capture_output=True, text=True, timeout=timeout, errors="replace",
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return p.stdout or ""


def _board_bios():
    """Motherboard + BIOS identity (Cores-inspired hardware detail)."""
    out = {}
    try:
        import json as _json
        b = _ps("Get-CimInstance Win32_BaseBoard | Select-Object Manufacturer,Product "
                "| ConvertTo-Json -Compress")
        if b.strip():
            bd = _json.loads(b)
            out["board"] = f"{(bd.get('Manufacturer') or '?').strip()} {(bd.get('Product') or '').strip()}".strip() or None
        s = _ps("Get-CimInstance Win32_BIOS | Select-Object Manufacturer,SMBIOSBIOSVersion "
                "| ConvertTo-Json -Compress")
        if s.strip():
            sd = _json.loads(s)
            out["bios"] = f"{(sd.get('Manufacturer') or '?').strip()} {(sd.get('SMBIOSBIOSVersion') or '').strip()}".strip() or None
    except Exception:
        pass
    return out


_io_prev = {"ts": 0.0, "disk": None, "net": None}


def _rates():
    """Real disk/network throughput (MB/s) from psutil io-counter deltas.
    First call after start returns None values (no previous sample yet)."""
    import psutil
    now = time.time()
    d = psutil.disk_io_counters()
    n = psutil.net_io_counters()
    out = {"disk_read_mb_s": None, "disk_write_mb_s": None,
           "net_down_mb_s": None, "net_up_mb_s": None}
    prev = _io_prev
    if d is None or n is None:
        return out  # psutil returns None on some VMs/containers - no crash
    if prev["disk"] is not None and now > prev["ts"]:
        dt = now - prev["ts"]
        out["disk_read_mb_s"] = round(max(0.0, (d.read_bytes - prev["disk"].read_bytes) / dt / 2 ** 20), 2)
        out["disk_write_mb_s"] = round(max(0.0, (d.write_bytes - prev["disk"].write_bytes) / dt / 2 ** 20), 2)
    if prev["net"] is not None and now > prev["ts"]:
        dt = now - prev["ts"]
        out["net_down_mb_s"] = round(max(0.0, (n.bytes_recv - prev["net"].bytes_recv) / dt / 2 ** 20), 2)
        out["net_up_mb_s"] = round(max(0.0, (n.bytes_sent - prev["net"].bytes_sent) / dt / 2 ** 20), 2)
    prev.update(ts=now, disk=d, net=n)
    return out


def _fans():
    """Real fan sensors when the platform exposes them (psutil on Linux,
    Win32_Fan on Windows). Empty list = machine reports no fan sensors."""
    import psutil
    try:
        f = psutil.sensors_fans() if hasattr(psutil, "sensors_fans") else {}
        out = []
        for grp, arr in (f or {}).items():
            for x in arr:
                out.append({"name": x.label or grp, "rpm": x.current})
        if out:
            return out
    except Exception:
        pass
    try:
        out2 = _ps("Get-CimInstance Win32_Fan | Select-Object -First 2 Name,DesiredSpeed "
                   "| ConvertTo-Json -Compress")
        import json as _json
        data = _json.loads(out2) if out2.strip() else []
        if isinstance(data, dict):
            data = [data]
        return [{"name": d.get("Name") or "Fan", "rpm": d.get("DesiredSpeed")} for d in data]
    except Exception:
        return []


def _gpu_live():
    """GPU utilisation + temperature via nvidia-smi when present; None = honest
    'no utilisation sensor' (Windows generic GPUs expose neither)."""
    try:
        out = _ps("nvidia-smi --query-gpu=utilization.gpu,temperature.gpu "
                  "--format=csv,noheader,nounits")
        u, t = [x.strip() for x in out.strip().split(",")][:2]
        return {"util_pct": float(u), "temp_c": float(t)}
    except Exception:
        return None


def deep_status(ctx=None):
    import psutil
    cpu_per_core = psutil.cpu_percent(interval=0.15, percpu=True)
    freq = None
    try:
        f = psutil.cpu_freq()
        freq = {"current_mhz": round(f.current), "max_mhz": round(f.max)} if f else None
    except Exception:
        pass
    vm = psutil.virtual_memory()
    disks = []
    for part in psutil.disk_partitions():
        if "cdrom" in part.opts:
            continue
        try:
            u = psutil.disk_usage(part.mountpoint)
            disks.append({"mount": part.mountpoint, "total_gb": round(u.total / 2 ** 30, 1),
                          "free_gb": round(u.free / 2 ** 30, 1),
                          "used_pct": round(u.percent, 1)})
        except (PermissionError, OSError):
            continue
    procs = []
    for p in psutil.process_iter(["name", "memory_info"]):
        try:
            procs.append({"name": p.info["name"], "mem": p.info["memory_info"].rss})
        except Exception:
            continue
    procs.sort(key=lambda x: -x["mem"])
    bat = psutil.sensors_battery()
    bb = _board_bios()
    hw = {
        "ts": time.time(),
        # total from the SAME interval sample as per-core: a bare
        # cpu_percent(interval=None) reports 0.0 on the first call
        # Prime the CPU sampler before the first dashboard reading.
        "cpu": {"total_pct": round(sum(cpu_per_core) / len(cpu_per_core), 1) if cpu_per_core else 0.0,
                "per_core": [round(x, 1) for x in cpu_per_core],
                "temp_c": _cpu_temp(), "freq": freq,
                "cores": psutil.cpu_count(logical=False),
                "threads": psutil.cpu_count()},
        "ram": {"used_pct": vm.percent,
                "used_gb": round((vm.total - vm.available) / 2 ** 30, 2),
                "total_gb": round(vm.total / 2 ** 30, 2),
                "swap_pct": psutil.swap_memory().percent},
        "gpu": _gpu(),
        "board": bb.get("board"),
        "bios": bb.get("bios"),
        "disks": disks,
        "battery": (None if bat is None else
                    {"percent": bat.percent, "plugged": bat.power_plugged,
                     "wear": _battery_wear()}),
        "processes": len(procs),
        "top_mem": [{"name": p["name"], "mem_mb": round(p["mem"] / 2 ** 20, 1)}
                    for p in procs[:8]],
        "latency_ms": (ctx.stats.get("latency_ms") if ctx else None),
        "uptime_h": round((time.time() - psutil.boot_time()) / 3600, 2),
    }
    try:
        hw["fans"] = _fans()
    except Exception:
        hw["fans"] = []
    try:
        hw["rates"] = _rates()
    except Exception:
        hw["rates"] = {}
    try:
        g = hw.get("gpu")
        if isinstance(g, list) and g:
            g[0].update({k: v for k, v in (_gpu_live() or {}).items() if v is not None})
    except Exception:
        pass
    return hw


def lightweight(ctx):
    """Small periodic snapshot for the hardware timeline (cheap to build)."""
    import psutil
    vm = psutil.virtual_memory()
    disk_free = None
    try:
        disk_free = round(psutil.disk_usage("C:\\" if psutil.WINDOWS else "/").free / 2 ** 30, 2)
    except Exception:
        pass
    return {"ts": time.time(), "cpu": psutil.cpu_percent(interval=None),
            "ram": vm.percent, "temp_c": _cpu_temp(), "disk_free_gb": disk_free,
            "processes": len(psutil.pids()),
            "latency_ms": ctx.stats.get("latency_ms")}


def check_alerts(ctx):
    """Threshold alerts — rate-limited so they never spam (battery 10 min,
    disk 1 h, RAM 5 min between repeats)."""
    import psutil
    now = time.time()
    bat = psutil.sensors_battery()
    if bat and not bat.power_plugged and bat.percent < 20 and now - _alert_state["battery_low"] > 600:
        _alert_state["battery_low"] = now
        ctx.send_event("alert", {"kind": "battery_low", "percent": bat.percent})
    try:
        free = psutil.disk_usage("C:\\" if psutil.WINDOWS else "/").free / 2 ** 30
        if free < 5 and now - _alert_state["disk_low"] > 3600:
            _alert_state["disk_low"] = now
            ctx.send_event("alert", {"kind": "disk_low", "free_gb": round(free, 2)})
    except Exception:
        pass
    if psutil.virtual_memory().percent > 90 and now - _alert_state["ram_high"] > 300:
        _alert_state["ram_high"] = now
        ctx.send_event("alert", {"kind": "ram_high", "percent": psutil.virtual_memory().percent})


def _loop(ctx):
    period = max(30, int(ctx.cfg.get("hw_report_sec", 120)))
    while True:
        try:
            ctx.send_event("hw_report", lightweight(ctx))
            check_alerts(ctx)
        except Exception as e:
            ctx.send_error("hardware", f"report: {e}")
        time.sleep(period)


def register(ctx):
    def hw_status(c, p):
        return deep_status(c)

    import threading
    import psutil
    threading.Thread(target=_loop, args=(ctx,), daemon=True, name="hks-hw").start()

    def _grade(ok, warn, val, crit_at):
        """-> (label, reason) with UNKNOWN for unavailable values (plan §5)."""
        try:
            v = float(val)
        except (TypeError, ValueError):
            return "UNKNOWN", "value unavailable on this machine"
        if v >= crit_at:
            return "CRITICAL", f"{v:g}"
        if v >= warn:
            return "WARNING", f"{v:g}"
        return "GOOD", f"{v:g}"

    def health_report(c, p):
        d = deep_status(c)
        cpu = (d.get("cpu") or {}).get("total_pct")
        mem = d.get("ram") or {}
        ram_pct = mem.get("used_pct")
        checks = []
        checks.append({"name": "CPU load", "state": _grade(0, 80, cpu, 92)[0],
                       "reason": _grade(0, 80, cpu, 92)[1] + "%"})
        checks.append({"name": "RAM pressure", "state": _grade(0, 85, ram_pct, 95)[0],
                       "reason": (_grade(0, 85, ram_pct, 95)[1] + "%") if ram_pct is not None else "unavailable"})
        for dsk in (d.get("disks") or []):
            free_pct = dsk.get("free_pct")
            if free_pct is None:
                continue
            st = "CRITICAL" if free_pct < 5 else "WARNING" if free_pct < 15 else "GOOD"
            checks.append({"name": f"Disk {dsk.get('mount', dsk.get('device', '?'))}",
                           "state": st, "reason": f"{free_pct:g}% free"})
        bat = d.get("battery") or {}
        if bat.get("wear") is not None:
            w = bat["wear"]
            checks.append({"name": "Battery wear", "state": _grade(0, 30, w, 45)[0],
                           "reason": _grade(0, 30, w, 45)[1] + "% worn"})
        tmp = (d.get("cpu") or {}).get("temp_c")
        if tmp is not None:
            checks.append({"name": "CPU temperature", "state": _grade(0, 80, tmp, 92)[0],
                           "reason": _grade(0, 80, tmp, 92)[1] + " C"})
        if not checks:
            checks.append({"name": "System", "state": "UNKNOWN", "reason": "no data yet"})
        worst = "GOOD"
        order = {"GOOD": 0, "UNKNOWN": 1, "WARNING": 2, "CRITICAL": 3}
        for c2 in checks:
            if order[c2["state"]] > order[worst]:
                worst = c2["state"]
        return {"overall": worst, "checks": checks, "snapshot": d}

    def report_generate(c, p):
        """Full system report (plan §6) - sections assembled on the agent."""
        import datetime
        import platform
        rep = {"generated": datetime.datetime.now().isoformat(timespec="seconds"),
               "host": platform.node(), "os": f"{platform.system()} {platform.release()} ({platform.version()})"}
        rep["system"] = {
            "python": platform.python_version(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "boot": datetime.datetime.fromtimestamp(psutil.boot_time()).isoformat(timespec="seconds"),
        }
        rep["hardware"] = deep_status(c)
        # (audit2 #12: dead list(...)[:0] block removed - the REAL top
        #  list is built right below as processes_top)
        try:
            procs = []
            for pr in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info"]):
                try:
                    mi = pr.info.get("memory_info")
                    procs.append({"pid": pr.info["pid"], "name": pr.info["name"],
                                  "cpu": pr.info.get("cpu_percent"),
                                  "mem_mb": round((mi.rss if mi else 0) / 1048576, 1)})
                except Exception:
                    continue
            procs.sort(key=lambda x: (x.get("mem_mb") or 0), reverse=True)
            rep["processes_top"] = procs[:25]
        except Exception:
            rep["processes_top"] = []
        try:
            rep["network"] = [{"iface": name, "addrs": [a.address for a in addrs]}
                              for name, addrs in psutil.net_if_addrs().items()]
        except Exception:
            rep["network"] = []
        rep["battery"] = (rep.get("hardware") or {}).get("battery")
        rep["disks"] = (rep.get("hardware") or {}).get("disks")
        return {"report": rep}

    return {"hw.status": hw_status, "health.report": health_report,
            "report.generate": report_generate,
            "hw.deep": lambda c, p2: deep_status(c)}
