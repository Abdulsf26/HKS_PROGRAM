"""XAMPP manager (field request 2026-09-21): detect, control and inspect
Apache / MySQL / FileZilla / Mercury / Tomcat from a local XAMPP install.

Windows detection: C:\\xampp + common paths; process state via tasklist;
ports checked with a real connect test. Start/stop uses XAMPP's own
launchers (apache_start.bat / apache_stop.bat / mysql_start.bat ...) so we
never fight its service model. Logs/config paths are returned for viewing.
"""
import os
import socket
import subprocess

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

XAMPP_CANDIDATES = ["C:\\xampp", "C:\\Program Files\\XAMPP", "D:\\xampp",
                    "C:\\XAMPP", "C:\\tools\\xampp"]
SERVICES = {
    "apache":   {"start": "apache_start.bat", "stop": "apache_stop.bat",
                 "exe": "apache\\bin\\httpd.exe", "ports": [80, 443],
                 "logs": ["apache\\logs\\error.log", "apache\\logs\\access.log"]},
    "mysql":    {"start": "mysql_start.bat", "stop": "mysql_stop.bat",
                 "exe": "mysql\\bin\\mysqld.exe", "ports": [3306],
                 "logs": ["mysql\\data\\mysql_error.log"]},
    "filezilla":{"start": "filezilla_start.bat", "stop": "filezilla_stop.bat",
                 "exe": "FileZillaFTP\\filezillaadmin.exe", "ports": [21],
                 "logs": []},
    "mercury":  {"start": "mercury_start.bat", "stop": "mercury_stop.bat",
                 "exe": "MercuryMail\\mercury.exe", "ports": [25, 110, 143],
                 "logs": []},
    "tomcat":   {"start": "tomcat_start.bat", "stop": "tomcat_stop.bat",
                 "exe": "tomcat\\bin\\tomcat9.exe", "ports": [8080],
                 "logs": ["tomcat\\logs\\catalina.log"]},
}


def _find_xampp():
    for cand in XAMPP_CANDIDATES:
        if os.path.isdir(cand):
            return cand
    return None


def _port_open(port):
    s = socket.socket()
    s.settimeout(0.6)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def _exe_running(exe_rel, base):
    name = os.path.basename(exe_rel)
    try:
        argv = ["tasklist", "/FI", f"IMAGENAME eq {name}"] if os.name == "nt" else \
               ["pgrep", "-f", name]
        out = subprocess.run(argv, capture_output=True, text=True, timeout=10,
                             creationflags=CREATE_NO_WINDOW).stdout or ""
        return name.lower() in out.lower()
    except Exception:
        return None


def _tail(path, n=25):
    try:
        with open(path, "r", errors="replace") as f:
            return "".join(f.readlines()[-n:])[-8000:]
    except Exception:
        return None


def register(ctx):
    def xampp_status(c, p):
        base = _find_xampp()
        if not base:
            return {"installed": False,
                    "hint": "XAMPP not found in the usual paths (C:\\xampp ...)"}
        out = {"installed": True, "path": base, "services": {}}
        for name, meta in SERVICES.items():
            exe_rel = meta["exe"]
            exe_path = os.path.join(base, *exe_rel.replace("\\", "/").split("/"))
            entry = {"exe_present": os.path.exists(exe_path),
                     "running": None, "ports": {}}
            if entry["exe_present"]:
                entry["running"] = _exe_running(exe_rel, base)
                for port in meta["ports"]:
                    entry["ports"][str(port)] = _port_open(port)
            out["services"][name] = entry
        # versions (cheap, only if binaries exist)
        php = os.path.join(base, "php", "php.exe")
        if os.path.exists(php):
            try:
                v = subprocess.run([php, "-v"], capture_output=True, text=True,
                                   timeout=10, creationflags=CREATE_NO_WINDOW)
                out["php_version"] = (v.stdout or "").splitlines()[0] if v.stdout else None
            except Exception:
                pass
        out["htdocs"] = os.path.join(base, "htdocs")
        out["documentroot_exists"] = os.path.isdir(out["htdocs"])
        return out

    def xampp_control(c, p):
        svc = str(p.get("service", "")).lower()
        op = str(p.get("op", "")).lower()
        meta = SERVICES.get(svc)
        if not meta:
            return {"ok": False, "error": "unknown service (apache/mysql/filezilla/mercury/tomcat)"}
        base = _find_xampp()
        if not base:
            return {"ok": False, "error": "XAMPP not installed"}
        if op == "restart":  # audit2 #4: handle restart BEFORE the start/stop guard
            r1 = xampp_control(c, {"service": svc, "op": "stop"})
            import time as _t
            _t.sleep(2)
            r2 = xampp_control(c, {"service": svc, "op": "start"})
            return {"ok": r2.get("ok"), "restart": True, "stop": r1, "start": r2}
        bat = meta["start"] if op == "start" else meta["stop"] if op == "stop" else None
        if not bat:
            return {"ok": False, "error": "op must be start|stop|restart"}
        path = os.path.join(base, bat)
        if not os.path.exists(path):
            return {"ok": False, "error": f"missing launcher: {path}"}
        try:
            # XAMPP launchers open their own console windows - run detached
            subprocess.Popen(["cmd", "/c", path], cwd=base,
                             creationflags=CREATE_NO_WINDOW | 0x00000008,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             stdin=subprocess.DEVNULL)
            import time as _t
            _t.sleep(3)
            return {"action": f"{svc} {op}", "note": "gave it 3s - re-check status"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def xampp_logs(c, p):
        base = _find_xampp()
        if not base:
            return {"ok": False, "error": "XAMPP not installed"}
        svc = str(p.get("service", "apache")).lower()
        meta = SERVICES.get(svc)
        if not meta:
            return {"ok": False, "error": "unknown service"}
        return {"logs": {os.path.basename(lp): _tail(os.path.join(base, *lp.replace("\\", "/").split("/")))
                         for lp in meta["logs"]} or {"note": "no log files mapped for this service"}}

    def xampp_config(c, p):
        base = _find_xampp()
        if not base:
            return {"ok": False, "error": "XAMPP not installed"}
        paths = {"php_ini": os.path.join(base, "php", "php.ini"),
                 "httpd_conf": os.path.join(base, "apache", "conf", "httpd.conf"),
                 "my_ini": os.path.join(base, "mysql", "bin", "my.ini")}
        out = {k: (v if os.path.exists(v) else None) for k, v in paths.items()}
        out["open_urls"] = ["http://localhost", "http://localhost/phpmyadmin"]
        return out

    return {"xampp.status": xampp_status, "xampp.control": xampp_control,
            "xampp.logs": xampp_logs, "xampp.config": xampp_config}
