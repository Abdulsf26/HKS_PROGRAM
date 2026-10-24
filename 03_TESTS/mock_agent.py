"""Mock HKS agent - simulates the Windows client for end-to-end testing."""
import asyncio
import base64
import re
import io
import json
import sys
import time

import websockets

TOKEN = None
import os
_ENV = None
_here = os.path.dirname(os.path.abspath(__file__))
for _cand in (os.path.join(_here, "..", ".env"),                                # zip: HKS_AdminPanel/tests/../.env
              os.path.join(_here, "..", "04_SOURCE", "HKS_AdminPanel", ".env")):  # repo: 03_TESTS -> sibling 04_SOURCE
    if os.path.exists(_cand):
        _ENV = _cand
        break
TOKEN = ""
if _ENV:
    for line in open(_ENV):
        if line.startswith("HKS_AGENT_TOKEN="):
            TOKEN = line.strip().split("=", 1)[1]
# round 62 (audit5 #5): no .env next to the panel -> take the token from the
# environment instead of dying on open(None) with a cryptic TypeError.
if not TOKEN:
    TOKEN = os.environ.get("HKS_AGENT_TOKEN", "")
if not TOKEN:
    raise SystemExit("mock_agent: no .env found - set HKS_AGENT_TOKEN or run from an installed panel folder")

URL = "ws://127.0.0.1:8000/ws/agent"
MACHINE = "test-laptop-001"


def png_red():
    from PIL import Image
    im = Image.new("RGB", (64, 64), (200, 30, 30))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=60)
    return buf.getvalue()



# ------------------------------------------------------------------
# Simulated PowerShell for the TEST laptop (mock): persistent cwd/vars,
# streams lines like the real agent. Honesty: banner says simulated.
# Backslash-free source: every path is built with B = chr(92).
# ------------------------------------------------------------------
_B = chr(92)


class _FakeShell:
    HOME = _B.join(("C:", "Users", "hks"))

    def _fs(self):
        j = _B.join
        return {
            self.HOME: [("d", "Desktop"), ("d", "Documents"), ("d", "Downloads"),
                        ("d", "Pictures"), ("f", "todo.txt", 812), ("f", "notes.md", 2333)],
            j((self.HOME, "Documents")): [("f", "report.docx", 48123), ("f", "sheet.xlsx", 20481)],
            j((self.HOME, "Downloads")): [("f", "setup.exe", 55212001), ("f", "photo.jpg", 3211122)],
            j((self.HOME, "Pictures")): [("f", "cat.jpg", 123456), ("f", "dog.png", 555001)],
            j((self.HOME, "Desktop")): [("f", "HKS.lnk", 1200)],
            j(("C:",)): [("d", "Windows"), ("d", "Program Files"), ("d", "Users"), ("d", "HKS_PROGRAM")],
            j(("C:", "Users")): [("d", "hks"), ("d", "Public")],
            j(("C:", "HKS_PROGRAM")): [("d", "panel"), ("d", "agent"), ("f", "README.txt", 934)],
            j(("C:", "Windows")): [("d", "System32"), ("f", "win.ini", 92)],
            j(("C:", "Windows", "System32")): [("f", "cmd.exe", 289792), ("f", "notepad.exe", 201216)],
            j(("C:", "Program Files")): [("d", "HKS"), ("d", "common files")],
        }

    def __init__(self):
        self.cwd = self.HOME
        self.vars = {}
        self.env = {"USERNAME": "hks", "COMPUTERNAME": "HKS-TEST-LAPTOP"}

    def _expand(self, t):
        for k, v in self.vars.items():
            t = t.replace("$" + k, v)
        for k, v in self.env.items():
            t = t.replace("$env:" + k, v)
        return t

    def _resolve(self, arg):
        arg = self._expand(arg).strip('"')
        if arg in ("~", "%userprofile%"):
            return self.HOME
        u = arg.upper()
        if u in ("BD", "ROOT") or u in (chr(92), "/"):
            return "C:" + _B
        if len(u) == 2 and u[1] == ":":
            d = u[0] + ":" + _B
            return d if d in self._fs() else None
        cur = self.cwd if arg not in ("..",) else self.cwd
        if arg == "..":
            parts = cur.rstrip(_B).split(_B)
            up = _B.join(parts[:-1])
            if up.endswith(":"):
                up += _B
            return up
        base = cur if cur.endswith(_B) else cur + _B
        cand = arg if len(arg) > 1 and arg[1] == ":" else base + arg
        for key in self._fs():
            if key.upper() == cand.upper():
                return key
        return None

    def run(self, text):
        t = text.strip()
        if not t:
            return []
        low = t.lower()
        head = t.split()[0]
        if low in ("cls", "clear", "clear-host"):
            return ["__HKS_CLEAR__"]
        if low in ("help", "get-help"):
            return ["HKS demo shell (simulated PowerShell) - try:",
                    "  dir   cd <path>   cd ..   pwd   whoami   hostname   ipconfig",
                    "  tasklist   systeminfo   Get-Date   echo hi   $x = 5   echo $x",
                    "  ping <host>   cls   exit"]
        if low in ("pwd", "get-location"):
            return [self.cwd]
        if low == "whoami":
            return [_B.join(("hks-test-laptop", "hks"))]
        if low == "hostname":
            return ["HKS-TEST-LAPTOP"]
        if low in ("date", "get-date"):
            return [time.strftime("%a %d %b %Y %H:%M:%S")]
        if low in ("ver", "$psversiontable"):
            return ["PowerShell 7.3.4 (simulated - HKS demo laptop)"]
        if low.startswith("echo "):
            return [self._expand(t[5:].strip())]
        if low in ("echo", "write-host"):
            return [""]
        m = re.match(r"^" + chr(92) + "$env:([A-Za-z_][A-Za-z0-9_]*)" + chr(92) + "s*=" + chr(92) + "s*(.*)$", t)
        if m:
            self.env[m.group(1)] = self._expand(m.group(2).strip().strip('"'))
            return [""]
        m = re.match(r"^" + chr(92) + "$([A-Za-z_][A-Za-z0-9_]*)" + chr(92) + "s*=" + chr(92) + "s*(.*)$", t)
        if m:
            self.vars[m.group(1)] = self._expand(m.group(2).strip().strip('"'))
            return [""]
        m = re.match(r"^(cd|set-location)" + chr(92) + "b" + chr(92) + "s*(.*)$", low)
        if m:
            arg = t[m.end(1):].strip().strip('"')
            if not arg:
                return [self.cwd]
            new = self._resolve(arg)
            if new is None:
                return ["cd : Cannot find path '" + arg + "' because it does not exist."]
            self.cwd = new
            return [""]
        if low in ("dir", "ls", "get-childitem", "gci"):
            fs = self._fs()
            items = fs.get(self.cwd, [])
            out = ["", "    Directory: " + self.cwd, "",
                   "Mode                 LastWriteTime         Length Name",
                   "----                 -------------         ------ ----"]
            for it in items:
                if it[0] == "d":
                    out.append("d-----        " + time.strftime("%d/%m/%Y  %H:%M") + "                " + it[1])
                else:
                    out.append("-a----        " + time.strftime("%d/%m/%Y  %H:%M") + "   %10s %s" % (f"{it[2]:,}", it[1]))
            return out
        if low == "ipconfig":
            return ["", "Windows IP Configuration", "",
                    "Ethernet adapter Ethernet0:", "",
                    "   Connection-specific DNS Suffix  . : lan",
                    "   IPv4 Address. . . . . . . . . . . : 192.168.1.42",
                    "   Subnet Mask . . . . . . . . . . . : 255.255.255.0",
                    "   Default Gateway . . . . . . . . . : 192.168.1.1", ""]
        if low == "tasklist":
            names = [("System Idle Process", 8, "0.1"), ("explorer.exe", 5120, "78.2"),
                     ("python.exe", 21400, "41.9"), ("chrome.exe", 9820, "310.5"),
                     ("svchost.exe", 1120, "18.7"), ("HKS_Client.exe", 7340, "52.1"),
                     ("dwm.exe", 1420, "96.3"), ("msmpeng.exe", 3200, "140.8")]
            out = ["", "Image Name                     PID Session Name        Mem Usage",
                   "========================= ======== ================ ============"]
            for n, pid, mem in names:
                out.append("%-25s %8d %16s %9s K" % (n, pid, "Services", mem))
            return out
        if low == "systeminfo":
            return ["", "Host Name:                 HKS-TEST-LAPTOP",
                    "OS Name:                   Microsoft Windows 11 Home",
                    "System Model:              HKS demo laptop (simulated)",
                    "Processor(s):              Intel i3-1205U",
                    "Total Physical Memory:     8,192 MB",
                    "Network Card:              Intel Wi-Fi 6 - 192.168.1.42", ""]
        if low.startswith("ping "):
            h = t[5:].strip()
            return ["", "Pinging " + h + " [93.184.216.34] with 32 bytes of data:",
                    "Reply from 93.184.216.34: bytes=32 time=24ms TTL=56",
                    "Reply from 93.184.216.34: bytes=32 time=23ms TTL=56",
                    "Reply from 93.184.216.34: bytes=32 time=25ms TTL=56",
                    "Reply from 93.184.216.34: bytes=32 time=24ms TTL=56", ""]
        if low == "exit":
            return ["[shell session closed]"]
        return ["", head + " : The term '" + head +
                "' is not recognized as a name of a cmdlet, function, script file, or executable program.",
                "Check the spelling of the name, or if a path was included, verify that the path is correct and try again."]


def _term_out(ws, line):
    return ws.send(json.dumps({"type": "event", "kind": "term_out", "data": {"data": line}}))


async def _term_banner(ws):
    for ln in ["PowerShell 7.3.4 - HKS demo laptop (SIMULATED shell, real on your Windows)",
               "Type a command and press Enter. Try: help"]:
        await _term_out(ws, ln)
    await _term_out(ws, "__HKS_PROMPT__" + ws._shell.cwd)


async def handle(ws, msg):
    action = msg.get("action")
    p = msg.get("params", {})
    data, ok = {}, True
    try:
        if action == "sys.quick":
            data = {"user": "hks", "ip": "192.168.1.42", "uptime": "3h 12m", "processes": "182", "ram": "55%"}
        elif action == "sys.info":
            data = {"hostname": "LAPTOP-HKS", "os": "Windows 11 Home", "cpu": "Intel i3-1205U", "ram_gb": 8}
        elif action == "terminal.exec":
            data = {"output": f"[mock] ran: {p.get('command','')}", "cwd": "C:\\HKS_PROGRAM"}
        elif action == "files.list":
            data = {"path": p.get("path", "C:\\"), "entries": [
                {"name": "Windows", "dir": True, "size": 0, "mtime": time.time() - 86400, "hidden": False},
                {"name": "pagefile.sys", "dir": False, "size": 2 ** 31, "mtime": time.time(),
                 "hidden": True, "system": True}]}
        elif action == "file.stat":
            target = p.get("path", "")
            if target.lower() == "c:\\test.bin":
                data = {"size": 2048, "mtime": time.time(), "is_dir": False}
            else:
                import os
                st = os.stat(target)  # raises for missing files -> server 404
                data = {"size": st.st_size, "mtime": st.st_mtime, "is_dir": os.path.isdir(target)}
        elif action == "file.read_chunk":
            off = int(p.get("offset", 0))
            size = min(int(p.get("size", 1024)), 1024)
            try:
                with open(p.get("path"), "rb") as f:
                    f.seek(off)
                    chunk = f.read(size)
            except OSError:
                chunk = b"A" * min(1024, max(0, 2048 - off))
            data = {"b64": base64.b64encode(chunk).decode(), "offset": off,
                    "eof": len(chunk) < size}
        elif action == "camera.stream":
            if p.get("on"):
                async def _cam_frames():
                    import time as _t
                    for _ in range(10):
                        await asyncio.sleep(0.35)
                        try:
                            await ws.send(bytes([2]) + png_red())
                        except Exception:
                            return
                asyncio.get_running_loop().create_task(_cam_frames())
            data = {"streaming": bool(p.get("on"))}
        elif action == "camera.list":
            data = {"cameras": [{"index": 0, "name": "Demo Webcam (built-in)"},
                                 {"index": 1, "name": "Demo USB Camera"}]}
        elif action in ("screen.snapshot", "camera.snap"):
            data = {"b64": base64.b64encode(png_red()).decode()}
        elif action == "files.delete":
            import os
            try:
                os.remove(p.get("path"))
            except OSError:
                pass
            data = {"deleted": p.get("path")}
        elif action == "deploy.start":
            data = {"id": "web_8090", "url": "http://192.168.1.42:8090/"}
        elif action == "media.zip" or action == "files.zip_media":
            import zipfile, random
            zp = f"/tmp/hks_export_{random.randint(1000,9999)}.zip"
            with zipfile.ZipFile(zp, "w") as z:
                z.writestr("cat.jpg", b"\xff\xd8\xff\xe0FAKEJPGDATA" * 100)
                z.writestr("dog.png", b"\x89PNG\r\n\x1a\nFAKEPNG" * 50)
            import os
            data = {"path": zp, "size": os.path.getsize(zp), "files": 2, "skipped": []}
        elif action == "files.zip_paths":
            import zipfile, random
            zp = f"/tmp/hks_sel_{random.randint(1000,9999)}.zip"
            with zipfile.ZipFile(zp, "w") as z:
                for i, f in enumerate((p.get("paths") or [])[:50]):
                    z.writestr(f.split("\\")[-1], f"content-{i}".encode())
            import os
            data = {"path": zp, "size": os.path.getsize(zp),
                    "files": len(p.get("paths") or []), "skipped": []}
        elif action == "hw.status":
            b = chr(92)
            data = {"cpu": {"total_pct": 21.0, "per_core": [28.0, 11.0, 7.0, 0.0, 14.0, 3.0, 9.0, 2.0],
                            "temp_c": 55.0, "core_temps": [52.0, 55.0, 53.0, 54.0, 56.0, 51.0, 54.0, 53.0],
                            "cores": 4, "threads": 8,
                            "freq": {"current_mhz": 2900, "max_mhz": 4100}},
                    "ram": {"used_pct": 55.0, "used_gb": 4.4, "total_gb": 8.0, "swap_pct": 34.0},
                    "gpu": [{"name": "Intel UHD Graphics (demo - simulated sensors)",
                             "status": "OK", "driver": "31.0", "vram_gb": 1.0,
                             "util_pct": 2.0, "temp_c": 43.0}],
                    "fans": [{"name": "CPU Fan (simulated)", "pct": 48.0},
                             {"name": "System Fan #1 (simulated)", "pct": 42.0}],
                    "rates": {"disk_read_mb_s": 0.11, "disk_write_mb_s": 0.53,
                              "net_down_mb_s": 0.4, "net_up_mb_s": 0.05,
                              "disk_model": "HKS Demo SSD 512GB (simulated)"},
                    "disks": [{"mount": "C:" + b, "total_gb": 476.0, "free_gb": 210.0, "used_pct": 55.9}],
                    "battery": {"percent": 77.0, "plugged": True,
                                "wear": {"design_mwh": 50000, "full_mwh": 47000, "wear_pct": 6.0}},
                    "processes": 182, "top_mem": [{"name": "chrome.exe", "mem_mb": 512}],
                    "latency_ms": 24, "uptime_h": 3.2}
        elif action == "input.enable":
            data = {"enabled": bool(p.get("on", True))}
        elif action == "db.find":
            data = {"items": [{"path": "C:\\Users\\hks\\test.db", "size": 8192}],
                    "errors": 0}
        elif action == "ping":
            data = {"pong": time.time()}
        elif action == "self.terminate":
            if str(p.get("confirm", "")) != "CONFIRM":
                ok = False
                data = None
                await ws.send(json.dumps({"type": "result", "id": msg["id"], "ok": False,
                                          "error": "type CONFIRM to terminate"}))
                return
            data = {"terminating": True}
        elif action == "media.scan":
            data = {"total": 2, "items": [
                {"path": "C:\\Users\\hks\\Pictures\\cat.jpg", "size": 123456, "mtime": time.time()},
                {"path": "C:\\pics\\dog.png", "size": 555, "mtime": time.time()}]}
        elif action == "terminal.stream":
            if p.get("on"):
                ws._shell = getattr(ws, "_shell", None) or _FakeShell()
                ws._stream = True
                data = {"ok": True, "streaming": True, "shell": "powershell (simulated)"}
                asyncio.create_task(_term_banner(ws))
            else:
                ws._stream = False
                data = {"ok": True, "streaming": False}
        elif action == "terminal.write":
            if getattr(ws, "_shell", None) is None:
                ws._shell = _FakeShell()
                ws._stream = True
                await _term_banner(ws)
            text = str(p.get("text", ""))
            for ln in ws._shell.run(text):
                await _term_out(ws, ln)
                await asyncio.sleep(0.03)
            await _term_out(ws, "__HKS_PROMPT__" + ws._shell.cwd)
            data = {"written": len(text) + 1}
        elif action == "addons.status":
            data = {"addons": [
                {"name": "MeshCentral", "port": 8490, "up": True, "url": "http://192.168.1.42:8490", "what": "(demo) kit installs on the real laptop", "installer": "HKS_ADDONS", "how": "(demo)", "installed": True, "autostart": False},
                {"name": "ttyd", "port": 7681, "up": False, "url": "http://192.168.1.42:7681", "what": "(demo) kit installs on the real laptop", "installer": "HKS_ADDONS", "how": "(demo)", "installed": True, "autostart": False},
                {"name": "filegator", "port": 8090, "up": False, "url": "http://192.168.1.42:8090", "what": "(demo) kit installs on the real laptop", "installer": "HKS_ADDONS", "how": "(demo)", "installed": True, "autostart": False},
                {"name": "code-server", "port": 8482, "up": True, "url": "http://192.168.1.42:8482", "what": "(demo) kit installs on the real laptop", "installer": "HKS_ADDONS", "how": "(demo)", "installed": True, "autostart": False},
                {"name": "DbGate", "port": 8485, "up": False, "url": "http://192.168.1.42:8485", "what": "(demo) kit installs on the real laptop", "installer": "HKS_ADDONS", "how": "(demo)", "installed": True, "autostart": False},
                {"name": "Shinobi", "port": 8480, "up": False, "url": "http://192.168.1.42:8480", "what": "(demo) kit installs on the real laptop", "installer": "HKS_ADDONS", "how": "(demo)", "installed": True, "autostart": False},
                {"name": "Nextcloud AIO", "port": 8443, "up": True, "url": "http://192.168.1.42:8443", "what": "(demo) kit installs on the real laptop", "installer": "HKS_ADDONS", "how": "(demo)", "installed": True, "autostart": False},
                {"name": "CasaOS", "port": 8080, "up": False, "url": "http://192.168.1.42:8080", "what": "(demo) kit installs on the real laptop", "installer": "HKS_ADDONS", "how": "(demo)", "installed": True, "autostart": False},
                {"name": "Mopidy + Iris", "port": 6680, "up": False, "url": "http://192.168.1.42:6680", "what": "(demo) kit installs on the real laptop", "installer": "HKS_ADDONS", "how": "(demo)", "installed": True, "autostart": False},
                {"name": "AnywhereInput", "port": 8008, "up": True, "url": "http://192.168.1.42:8008", "what": "(demo) kit installs on the real laptop", "installer": "HKS_ADDONS", "how": "(demo)", "installed": True, "autostart": False},
                {"name": "filebrowser", "port": 8487, "up": False, "url": "http://192.168.1.42:8487", "what": "(demo) kit installs on the real laptop", "installer": "HKS_ADDONS", "how": "(demo)", "installed": True, "autostart": False},
                {"name": "AudioRelay", "port": 0, "up": None, "url": "", "what": "(demo) kit installs on the real laptop", "installer": "HKS_ADDONS", "how": "(demo)", "installed": True, "autostart": True},
                {"name": "RustDesk", "port": 0, "up": None, "url": "", "what": "(demo) kit installs on the real laptop", "installer": "HKS_ADDONS", "how": "(demo)", "installed": True, "autostart": False},
                {"name": "TigerVNC", "port": 0, "up": None, "url": "", "what": "(demo) kit installs on the real laptop", "installer": "HKS_ADDONS", "how": "(demo)", "installed": True, "autostart": False},
                {"name": "Deskflow", "port": 24800, "up": False, "url": "http://192.168.1.42:24800", "what": "(demo) kit installs on the real laptop", "installer": "HKS_ADDONS", "how": "(demo)", "installed": True, "autostart": False},
                {"name": "Tailscale", "port": 0, "up": None, "url": "", "what": "(demo) kit installs on the real laptop", "installer": "HKS_ADDONS", "how": "(demo)", "installed": True, "autostart": False},
                {"name": "ZeroTier", "port": 9993, "up": False, "url": "http://192.168.1.42:9993", "what": "(demo) kit installs on the real laptop", "installer": "HKS_ADDONS", "how": "(demo)", "installed": True, "autostart": False},
                {"name": "Netbird", "port": 0, "up": None, "url": "", "what": "(demo) kit installs on the real laptop", "installer": "HKS_ADDONS", "how": "(demo)", "installed": True, "autostart": False},
                {"name": "frp", "port": 7500, "up": True, "url": "http://192.168.1.42:7500", "what": "(demo) kit installs on the real laptop", "installer": "HKS_ADDONS", "how": "(demo)", "installed": True, "autostart": False},
                {"name": "Syncthing", "port": 8384, "up": False, "url": "http://192.168.1.42:8384", "what": "(demo) kit installs on the real laptop", "installer": "HKS_ADDONS", "how": "(demo)", "installed": True, "autostart": True},
                {"name": "rclone", "port": 5572, "up": False, "url": "http://192.168.1.42:5572", "what": "(demo) kit installs on the real laptop", "installer": "HKS_ADDONS", "how": "(demo)", "installed": True, "autostart": False},
                {"name": "Kopia", "port": 51515, "up": True, "url": "http://192.168.1.42:51515", "what": "(demo) kit installs on the real laptop", "installer": "HKS_ADDONS", "how": "(demo)", "installed": True, "autostart": False},
                {"name": "windows_exporter", "port": 9182, "up": False, "url": "http://192.168.1.42:9182", "what": "(demo) kit installs on the real laptop", "installer": "HKS_ADDONS", "how": "(demo)", "installed": True, "autostart": False},
                {"name": "Glances", "port": 61208, "up": False, "url": "http://192.168.1.42:61208", "what": "(demo) kit installs on the real laptop", "installer": "HKS_ADDONS", "how": "(demo)", "installed": True, "autostart": False},
                {"name": "Netdata", "port": 19999, "up": True, "url": "http://192.168.1.42:19999", "what": "(demo) kit installs on the real laptop", "installer": "HKS_ADDONS", "how": "(demo)", "installed": True, "autostart": False},
                {"name": "Uptime Kuma", "port": 3001, "up": False, "url": "http://192.168.1.42:3001", "what": "(demo) kit installs on the real laptop", "installer": "HKS_ADDONS", "how": "(demo)", "installed": True, "autostart": False},
                {"name": "osquery", "port": 0, "up": None, "url": "", "what": "(demo) kit installs on the real laptop", "installer": "HKS_ADDONS", "how": "(demo)", "installed": True, "autostart": False},
                {"name": "Velociraptor", "port": 8889, "up": True, "url": "http://192.168.1.42:8889", "what": "(demo) kit installs on the real laptop", "installer": "HKS_ADDONS", "how": "(demo)", "installed": True, "autostart": False},
                {"name": "Nmap", "port": 0, "up": None, "url": "", "what": "(demo) kit installs on the real laptop", "installer": "HKS_ADDONS", "how": "(demo)", "installed": True, "autostart": False},
                {"name": "LibreHardwareMonitor", "port": 8088, "up": False, "url": "http://192.168.1.42:8088", "what": "(demo) kit installs on the real laptop", "installer": "HKS_ADDONS", "how": "(demo)", "installed": True, "autostart": False},
                {"name": "FanControl", "port": 0, "up": None, "url": "", "what": "(demo) kit installs on the real laptop", "installer": "HKS_ADDONS", "how": "(demo)", "installed": True, "autostart": False},
                {"name": "ShareX", "port": 0, "up": None, "url": "", "what": "(demo) kit installs on the real laptop", "installer": "HKS_ADDONS", "how": "(demo)", "installed": True, "autostart": False},
                {"name": "yt-dlp", "port": 0, "up": None, "url": "", "what": "(demo) kit installs on the real laptop", "installer": "HKS_ADDONS", "how": "(demo)", "installed": True, "autostart": False},
                {"name": "Jellyfin", "port": 8096, "up": True, "url": "http://192.168.1.42:8096", "what": "(demo) kit installs on the real laptop", "installer": "HKS_ADDONS", "how": "(demo)", "installed": True, "autostart": False},
                {"name": "WinSW", "port": 0, "up": None, "url": "", "what": "(demo) kit installs on the real laptop", "installer": "HKS_ADDONS", "how": "(demo)", "installed": True, "autostart": False},
                {"name": "AutoHotkey", "port": 0, "up": None, "url": "", "what": "(demo) kit installs on the real laptop", "installer": "HKS_ADDONS", "how": "(demo)", "installed": True, "autostart": False},
                {"name": "OBS Studio", "port": 0, "up": None, "url": "", "what": "(demo) kit installs on the real laptop", "installer": "HKS_ADDONS", "how": "(demo)", "installed": True, "autostart": False}], "checked": time.time()}
        elif action == "addons.run":
            data = {"ok": True, "started": p.get("name"),
                    "note": "console window opened (demo)"}
        elif action == "addons.stop":
            data = {"ok": True, "killed": 1, "note": "demo stop"}
        elif action == "addons.autostart":
            data = {"ok": True, "note": "demo autostart " + ("ON" if p.get("on") else "OFF")}
        elif action == "power.status":
            data = {"safeboot_pending": ws.__dict__.get("safeboot", False),
                    "battery": "77% (charging)", "power_source": "AC",
                    "sleep_after": "30 min", "hibernate": "available",
                    "last_boot": time.strftime("%Y-%m-%d %H:%M")}
        elif action == "power.sleep":
            data = {"ok": True, "note": "(demo) real sleep on your Windows"}
        elif action == "power.hibernate":
            data = {"ok": True, "note": "(demo) real hibernate on your Windows"}
        elif action == "power.display_off":
            data = {"ok": True, "note": "(demo) display off is real on Windows"}
        elif action == "power.lock_screen":
            data = {"ok": True, "note": "(demo) LockWorkStation is real on Windows"}
        elif action in ("power.logoff", "power.logoff_console"):
            data = {"ok": True, "note": "(demo) logoff is real on Windows"}
        elif action == "power.restart":
            data = {"ok": True, "note": "(demo) restart scheduled on Windows"}
        elif action == "power.shutdown":
            sec = int((p or {}).get("seconds", 10))
            data = {"ok": True, "note": f"(demo) shutdown in {sec}s - cancel works"}
        elif action == "power.shutdown_cancel":
            data = {"ok": True, "note": "(demo) shutdown cancelled"}
        elif action == "power.safeboot_on":
            ws._safeboot = True
            data = {"ok": True, "note": "(demo) safe mode armed"}
        elif action == "power.safeboot_off":
            ws._safeboot = False
            data = {"ok": True, "note": "(demo) safe mode disarmed"}
        elif action == "power.sleep_until":
            data = {"ok": True, "note": "(demo) wake timer set"}
        elif action == "power.unlock":
            data = {"ok": True, "note": "(demo) WTSConnectSessionW runs on real Windows"}
        elif action == "power.wake_timers":
            data = {"items": []}
        elif action == "selftest.run":   # round 68 (F17): demo answers the Help-tab self-test button
            _st_res = [{"action": a, "ok": True, "sample": "(demo)"} for a in (
                "sys.quick", "sys.info", "hw.status", "files.drives", "apps.processes",
                "net.adapters", "net.internet_check", "session.status", "volume.get",
                "camera.list", "mic.list")]
            out = {"tested": len(_st_res), "passed": len(_st_res), "failed": 0,
                   "absent": 0, "results": _st_res}

        elif action == "health.report":
            data = {"overall": "GOOD", "checks": [
                {"name": "CPU", "state": "GOOD", "reason": "21% load"},
                {"name": "Memory", "state": "GOOD", "reason": "55% used"},
                {"name": "Storage", "state": "GOOD", "reason": "210 GB free"},
                {"name": "Battery", "state": "GOOD", "reason": "77% charging"},
                {"name": "Network", "state": "GOOD", "reason": "192.168.1.42 up"}]}
        elif action == "input.status":
            data = {"engine": "healthy", "input_active": False, "errors": 0,
                    "screen": {"w": 1920, "h": 1080}}
        elif action == "input.apply":
            data = {"ok": True, "engine": "healthy", "note": "(demo) real injection on Windows"}
        elif action == "hw.deep":
            data = {"cpu": {"name": "Intel i3-1205U (demo)", "cores": 4, "threads": 8,
                            "total_pct": 21.0, "freq": {"current_mhz": 2900}},
                    "ram": {"total_gb": 8.0, "used_pct": 55.0},
                    "board": "HKS Demo Board UEFI", "bios": "DemoBIOS 1.04",
                    "processes": 182, "uptime_h": 3.2}
        elif action == "network.addresses":
            data = {"addresses": [{"iface": "Ethernet (demo)", "ip": "192.168.1.42"},
                                  {"iface": "loopback", "ip": "127.0.0.1"}]}
        elif action == "net.adapters":
            data = {"items": [
                {"name": "Ethernet (demo)", "up": True, "ip": "192.168.1.42",
                 "mac": "AA:BB:CC:DD:EE:01"},
                {"name": "Wi-Fi (demo)", "up": False, "ip": None,
                 "mac": "AA:BB:CC:DD:EE:02"},
                {"name": "Bluetooth (demo)", "up": True, "ip": None,
                 "mac": "AA:BB:CC:DD:EE:03"}]}
        elif action == "net.usage":
            data = {"recv_since_boot": 9900000000, "sent_since_boot": 1200000000}
        elif action == "net.internet_check":
            data = {"online": True, "latency_ms": 24, "via": "demo"}
        elif action == "net.wifi_profiles":
            data = {"items": [{"name": "HomeWiFi"}, {"name": "Office-5G"}]}
        elif action == "net.wifi_scan":
            data = {"items": [{"ssid": "HomeWiFi", "signal": "88%", "secure": True},
                              {"ssid": "CoffeeShop", "signal": "41%", "secure": False}]}
        elif action in ("net.wifi_connect", "net.wifi_disconnect", "net.enable", "net.disable"):
            data = {"ok": True, "note": "(demo) adapter change is real on Windows"}
        elif action == "usb.list":
            data = {"items": [
                {"name": "Demo USB Keyboard", "id": "USB\\VID_046D&PID_C31C\\demo", "class": "HID",
                 "ok": True, "status": "enabled"},
                {"name": "Demo Optical Mouse", "id": "USB\\VID_046D&PID_C077\\demo", "class": "HID",
                 "ok": True, "status": "enabled"},
                {"name": "Demo USB Camera", "id": "USB\\VID_18CF&PID_2C09\\demo", "class": "Camera",
                 "ok": True, "status": "enabled"},
                {"name": "Demo USB Storage", "id": "USB\\VID_0781&PID_5591\\demo", "class": "Storage",
                 "ok": False, "status": "disabled (demo)"}]}
        elif action == "usb.storage_disable":
            data = {"ok": True, "note": "(demo) storage lockdown is real on Windows"}
        elif action == "usb.device":
            data = {"ok": True, "note": "(demo) device enable/disable is real on Windows"}
        elif action == "apps.processes":
            data = {"processes": [
                {"name": "chrome.exe", "pid": 8420, "cpu": 4.2, "mem": 312},
                {"name": "Code.exe", "pid": 11248, "cpu": 2.1, "mem": 264},
                {"name": "explorer.exe", "pid": 8240, "cpu": 1.8, "mem": 156},
                {"name": "python.exe", "pid": 5200, "cpu": 1.2, "mem": 96},
                {"name": "dwm.exe", "pid": 1420, "cpu": 0.9, "mem": 72}]}
        elif action == "apps.kill":
            data = {"ok": True, "note": "(demo) kill is real on Windows"}
        elif action in ("apps.suspend", "apps.resume", "apps.launch"):
            data = {"ok": True, "note": "(demo) real on Windows"}
        elif action == "apps.uninstall_meta":
            data = {"items": [
                {"name": "Google Chrome (demo)", "version": "126", "publisher": "Google LLC"},
                {"name": "VS Code (demo)", "version": "1.91", "publisher": "Microsoft"},
                {"name": "Python 3.11 (demo)", "version": "3.11.4", "publisher": "Python"}]}
        elif action == "apps.uninstall":
            data = {"launched": True, "note": "(demo) real uninstaller on Windows"}
        elif action == "apps.startup_list":
            data = {"items": [
                {"name": "HKS Agent (demo)", "target": "schtasks HKS-Agent",
                 "location": "Task Scheduler"},
                {"name": "OneDrive (demo)", "target": "%LocalAppData%\\OneDrive",
                 "location": "HKCU Run"}]}
        elif action == "apps.startup_remove":
            data = {"ok": True, "note": "(demo) removed"}
        elif action == "clipboard.get":
            data = {"text": None, "html": None, "files": None,
                    "image_b64": None, "formats": ["(demo clipboard is empty)"]}
        elif action == "clipboard.set":
            data = {"ok": True}
        elif action == "code.check":
            vmap = {"python": "3.11.4", "javascript": "V8 20.x", "php": "8.2 (XAMPP)",
                    "go": "1.22", "rust": "1.78"}
            data = {"installed": True, "version": vmap.get(str((p or {}).get("lang", "")), "runtime (demo)")}
        elif action == "code.run":
            data = {"output": "(demo laptop) Hello, HKS! - real run on your Windows\nCount: 0..4 done",
                    "error": "", "code": 0, "time_ms": 11}
        elif action == "code.install":
            data = {"ok": True, "note": "(demo) winget install runs on Windows"}
        elif action == "mic.list":
            data = {"items": ["Demo Microphone (built-in)"]}
        elif action == "mic.record":
            import io as _io, wave as _wave, math as _math
            secs = min(10, max(1, int((p or {}).get("seconds", 2))))
            rate = 16000
            buf = _io.BytesIO()
            with _wave.open(buf, "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
                frames = bytearray()
                for i in range(rate * secs):
                    v = int(6000 * _math.sin(2 * _math.pi * 440 * (i / rate)) * (1 - i / (rate * secs)))
                    frames += int(v).to_bytes(2, "little", signed=True)
                w.writeframes(bytes(frames))
            import base64 as _b64
            data = {"b64": _b64.b64encode(buf.getvalue()).decode(), "seconds": secs,
                    "note": "(demo tone - real mic on Windows)"}
        elif action == "camera.motion":
            data = {"ok": True, "on": bool((p or {}).get("on", False)),
                    "note": "(demo) motion engine is real on Windows"}
        elif action == "camera.record":
            data = {"ok": True, "path": "C:\\Users\\hks\\Videos\\demo_recording.avi",
                    "note": "(demo) records the real webcam on Windows"}
        elif action == "camera.record_stop":
            data = {"ok": True}
        elif action == "media.now_playing":
            data = {"now_playing": []}
        elif action == "media.play_file":
            data = {"ok": True, "note": "(demo) would play on the laptop"}
        elif action == "media.play_youtube":
            data = {"ok": True, "note": "(demo) would open on the laptop"}
        elif action == "media.stop":
            data = {"ok": True}
        elif action == "media.thumb":
            try:
                from PIL import Image
                im = Image.new("RGB", (96, 54), (24, 34, 56))
                bu = _io.BytesIO() if False else None
                import io as _io2
                bu = _io2.BytesIO(); im.save(bu, "JPEG", quality=70)
                import base64 as _b2
                data = {"b64": _b2.b64encode(bu.getvalue()).decode()}
            except Exception:
                data = {"b64": None}
        elif action == "notify.toast":
            data = {"ok": True, "note": "(demo) toast on Windows"}
        elif action == "tts.speak":
            data = {"ok": True, "note": "(demo) speech on Windows"}
        elif action in ("volume.set", "volume.mute"):
            data = {"ok": True}
        elif action == "volume.get":
            data = {"level": 50, "muted": False}
        elif action == "ssh.status":
            data = {"state": "NotPresent (demo - real OpenSSH query on Windows)",
                    "hint_ip": ["192.168.1.42"]}
        elif action in ("ssh.enable", "ssh.disable"):
            data = {"ok": True, "note": "(demo) sshd control is real on Windows"}
        elif action == "startup.hks_status":
            data = {"registered": True, "method": "schtasks HKS-Agent/Panel/SessionAgent (demo)"}
        elif action == "startup.hks_action":
            data = {"ok": True, "note": "(demo) task control is real on Windows"}
        elif action == "schtasks.list":
            data = {"items": ["HKS-Panel   Ready   At system start",
                              "HKS-Agent   Ready   At system start",
                              "HKS-SessionAgent   Ready   At logon"]}
        elif action == "tasks.upload_script":
            data = {"ok": True, "note": "(demo) saved"}
        elif action == "tasks.agent_list":
            data = {"items": []}
        elif action in ("tasks.agent_add", "tasks.agent_remove", "tasks.agent_run"):
            data = {"ok": True, "note": "(demo) scheduled-task control is real on Windows"}
        elif action == "deploy.list":
            data = {"list": []}
        elif action == "deploy.stop":
            data = {"ok": True}
        elif action == "file.write_chunk":
            data = {"ok": True}
        elif action == "files.drives":
            data = {"drives": ["C:\\"]}
        elif action == "files.mkdir":
            data = {"ok": True, "note": "(demo) created"}
        elif action == "files.rename":
            data = {"ok": True, "note": "(demo) renamed"}
        elif action == "files.extract":
            data = {"files": 3, "note": "(demo)"}
        elif action == "files.search":
            data = {"items": [{"path": "C:\\Users\\hks\\Documents\\report.docx", "size": 48123},
                              {"path": "C:\\Users\\hks\\Desktop\\todo.txt", "size": 812}]}
        elif action == "logs.windows":
            data = {"lines": ["(demo) 2026-09-23 09:41  logon    hks (console)",
                              "(demo) 2026-09-22 21:04  shutdown  clean",
                              "(demo) 2026-09-22 20:58  start    HKS boot",
                              "(demo) real Windows Event Log on your laptop"]}
        elif action == "session.idle":
            data = {"idle_s": 47}
        elif action == "session.foreground":
            data = {"title": "Demo Explorer - Documents"}
        elif action == "session.status":
            data = {"user": "hks", "state": "active (demo)"}
        elif action == "xampp.status":
            x = getattr(ws, "_xamps", None)
            if x is None:
                ws._xamps = x = {"Apache": False, "MySQL": False, "FileZilla": False,
                                 "Mercury": False, "Tomcat": False}
            data = {"services": {k: {"running": v} for k, v in x.items()},
                    "note": "demo laptop has no XAMPP - toggles show the flow; real on your PC"}
        elif action == "xampp.control":
            x = getattr(ws, "_xamps", None)
            if x is None:
                ws._xamps = x = {"Apache": False, "MySQL": False, "FileZilla": False,
                                 "Mercury": False, "Tomcat": False}
            svc2 = str((p or {}).get("service", "")).title()
            op = str((p or {}).get("op", ""))
            if svc2 in x:
                x[svc2] = op == "start" if op in ("start", "stop") else x[svc2]
                data = {"ok": True, "note": f"(demo) {svc2} {op}"}
            else:
                data = {"ok": False, "error": "unknown service (demo)"}
        elif action == "xampp.logs":
            data = {"lines": ["(demo) no XAMPP installed on the demo laptop"]}
        elif action == "xampp.config":
            data = {"lines": ["(demo) Apache httpd.conf would appear here"]}

        else:
            data = {"note": f"mock ack for {action}"}
    except Exception as e:
        ok, data = False, None
        await ws.send(json.dumps({"type": "error", "module": "mock", "message": str(e)}))
    await ws.send(json.dumps({"type": "result", "id": msg["id"], "ok": ok, "data": data}))


async def session(ws):
    async def frames():
        while True:
            await ws.send(bytes([1]) + png_red())
            await asyncio.sleep(1.5)

    f1 = asyncio.create_task(frames())
    try:
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("type") == "cmd":
                await handle(ws, msg)
    finally:
        f1.cancel()


async def svc(ws):
    async def tel():
        i = 0
        while True:
            await ws.send(json.dumps({"type": "telemetry", "cpu": 12 + i % 30, "ram": 55,
                                      "bat_pct": 77, "bat_plugged": i % 2,
                                      "net_sent": 1.2e6, "net_recv": 9.9e6,
                                      "ssid": "HomeWiFi", "ip": "192.168.1.42"}))
            i += 1
            await asyncio.sleep(2)

    async def hw():
        while True:
            await ws.send(json.dumps({"type": "event", "kind": "hw_report",
                                      "data": {"ts": time.time(), "cpu": 18.0, "ram": 55.0,
                                               "temp_c": 52.0, "disk_free_gb": 210.0,
                                               "processes": 182, "latency_ms": 24}}))
            await asyncio.sleep(5)
    t1 = asyncio.create_task(tel())
    t2 = asyncio.create_task(hw())
    await asyncio.sleep(0.4)
    await ws.send(json.dumps({"type": "event", "kind": "boot",
                              "data": {"msg": "HKS PROGRAM HAS BEEN STARTED",
                                       "detail": "starting up — fully ready in ~1 minute"}}))
    await ws.send(json.dumps({"type": "event", "kind": "logon", "data": {"user": "hks", "type": "console"}}))
    await ws.send(json.dumps({"type": "capture", "kind": "logon_cam",
                              "b64": base64.b64encode(png_red()).decode(),
                              "meta": {"user": "hks", "trigger": "logon"}}))
    await ws.send(json.dumps({"type": "error", "module": "usb_ctl",
                              "message": "demo error: device busy", "detail": "trace..."}))
    try:
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("type") == "cmd":
                await handle(ws, msg)
    finally:
        t1.cancel()
        t2.cancel()


# round 66 (Ammar's contract idea): the mock publishes its REAL handled
# actions AND declares test_double=True (it accepts everything via a
# fallback, so it cannot publish a closed list) - the panel then exempts it.
MOCK_ACTIONS = ['addons.autostart', 'addons.run', 'addons.status', 'addons.stop', 'apps.kill', 'apps.launch', 'apps.processes', 'apps.resume', 'apps.startup_list', 'apps.startup_remove', 'apps.suspend', 'apps.uninstall', 'apps.uninstall_meta', 'camera.list', 'camera.motion', 'camera.record', 'camera.record_stop', 'camera.snap', 'camera.stream', 'clipboard.get', 'clipboard.set', 'code.check', 'code.install', 'code.run', 'db.find', 'deploy.list', 'deploy.start', 'deploy.stop', 'file.read_chunk', 'file.stat', 'file.write_chunk', 'files.delete', 'files.drives', 'files.extract', 'files.list', 'files.mkdir', 'files.rename', 'files.search', 'files.zip_media', 'files.zip_paths', 'health.report', 'hw.deep', 'hw.status', 'input.apply', 'input.enable', 'input.status', 'logs.windows', 'media.now_playing', 'media.play_file', 'media.play_youtube', 'media.scan', 'media.stop', 'media.thumb', 'media.zip', 'mic.list', 'mic.record', 'net.adapters', 'net.disable', 'net.enable', 'net.internet_check', 'net.usage', 'net.wifi_connect', 'net.wifi_disconnect', 'net.wifi_profiles', 'net.wifi_scan', 'network.addresses', 'notify.toast', 'ping', 'power.display_off', 'power.hibernate', 'power.lock_screen', 'power.logoff', 'power.logoff_console', 'power.restart', 'power.safeboot_off', 'power.safeboot_on', 'power.shutdown', 'power.shutdown_cancel', 'power.sleep', 'power.sleep_until', 'power.status', 'power.unlock', 'power.wake_timers', 'schtasks.list', 'screen.snapshot', 'self.terminate', 'session.foreground', 'session.idle', 'session.status', 'selftest.run', 'ssh.disable', 'ssh.enable', 'ssh.status', 'startup.hks_action', 'startup.hks_status', 'sys.info', 'sys.quick', 'tasks.agent_add', 'tasks.agent_list', 'tasks.agent_remove', 'tasks.agent_run', 'tasks.upload_script', 'terminal.exec', 'terminal.stream', 'terminal.write', 'tts.speak', 'usb.device', 'usb.list', 'usb.storage_disable', 'volume.get', 'volume.mute', 'volume.set', 'xampp.config', 'xampp.control', 'xampp.logs', 'xampp.status']

async def main(role="svc"):
    while True:
        try:
            async with websockets.connect(URL, max_size=16 * 1024 * 1024) as ws:
                await ws.send(json.dumps({"type": "hello", "machine_id": MACHINE, "role": role,
                                          "name": "HKS Test Laptop", "version": "1.0.0",
                                          "token": TOKEN, "caps": MOCK_ACTIONS, "test_double": True}))
                rep = json.loads(await ws.recv())
                assert rep["type"] == "hello_ok", rep
                print(f"[mock:{role}] connected+authed", flush=True)
                if role == "svc":
                    await svc(ws)
                else:
                    await session(ws)
        except Exception as e:
            print(f"[mock:{role}] err: {e}", flush=True)
        print(f"[mock:{role}] reconnecting in 3s", flush=True)
        await asyncio.sleep(3)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "svc"))
