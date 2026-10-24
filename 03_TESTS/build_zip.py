#!/usr/bin/env python3
"""One-command release builder for HKS_PROGRAM.zip (production name, round 67).

Usage:  python3 03_TESTS/build_zip.py 7.2.9

Does the full, gated pipeline:
  1. build the zip from 04_SOURCE (root files + AdminPanel + Client + Addons)
  2. GATES - verify version, static assets, features, new fixes INSIDE the zip
  3. copy the zip into the panel's static dir (panel can serve itself)
  4. split base64 into parts (PART_CHARS) + write REBUILD_HKS.bat
     (bat verifies part count + zip size + SHA256 before saying OK)
Exit code 0 = everything passed.
"""
import base64
import glob
import hashlib
import re
import os
import shutil
import subprocess
import tempfile
import sys
import zipfile

VER = sys.argv[1] if len(sys.argv) > 1 else "7.2.69"
ASSETS_V = VER.split(".")[-1]
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Resolve all source, output, and delivery paths against the project itself,
# regardless of the shell directory from which this release script is run.
os.chdir(ROOT_DIR)
SRC = "04_SOURCE"
OUT = "HKS_PROGRAM.zip"
PARTS_DIR = "01_DELIVERY_PARTS"
PART_CHARS = 15990

ROOT_FILES = [
    ("00_START_HERE/HKS_AUTOSTART.ps1", "HKS_AUTOSTART.ps1"),
    ("00_START_HERE/HKS_REPAIR_AUTOSTART.ps1", "HKS_REPAIR_AUTOSTART.ps1"),
    ("00_START_HERE/HKS_TRACE_DESKTOP.ps1", "HKS_TRACE_DESKTOP.ps1"),
    ("00_START_HERE/CHECK_STATUS.bat", "CHECK_STATUS.bat"),
    ("00_START_HERE/HKS_DOCTOR.bat", "HKS_DOCTOR.bat"),
    ("00_START_HERE/HKS_FIX_TUNNEL.bat", "HKS_FIX_TUNNEL.bat"),
    ("00_START_HERE/INSTALL_HKS.bat", "INSTALL_HKS.bat"),
    ("00_START_HERE/UNINSTALL_HKS.bat", "UNINSTALL_HKS.bat"),
    ("00_START_HERE/RESET_PASSWORD.bat", "RESET_PASSWORD.bat"),
    ("00_START_HERE/FIX_PACKAGES.bat", "FIX_PACKAGES.bat"),
    ("00_START_HERE/REVERT_HKS.bat", "REVERT_HKS.bat"),   # round 68 (F06) rollback
    ("02_DOCS/README_START_HERE.txt", "README_START_HERE.txt"),
    ("02_DOCS/SETUP_GUIDE.md", "SETUP_GUIDE.md"),
    ("02_DOCS/CREDITS.md", "CREDITS.md"),
    ("04_SOURCE/README.txt", "DEVELOPER_NOTES.txt"),
    ("04_SOURCE/first_run.py", "first_run.py"),
]
EXCLUDE = {"HKS_AdminPanel/server/static/HKS_PROGRAM.zip", "HKS_AdminPanel/INSTALL_HKS.bat",
           "HKS_AdminPanel/CHECK_STATUS.bat", "HKS_AdminPanel/.env",
           "HKS_AdminPanel/certs/server.pem", "HKS_AdminPanel/certs/key.pem"}
EXPECTED_FILES = 158  # Includes the focused boot-task repair utility.


def add_folder(z, top, arc_prefix):
    n = 0
    for dirpath, dirnames, filenames in os.walk(top):
        dirnames[:] = [d for d in dirnames if d not in ("__pycache__", "data", ".pytest_cache")
                       and not (d.startswith("_") and d.endswith("_tmp"))]  # round 62: test litter
        for f in sorted(filenames):
            full = os.path.join(dirpath, f)
            rel = os.path.relpath(full, top)
            arc = arc_prefix + "/" + rel.replace(os.sep, "/")
            if arc in EXCLUDE:
                continue
            z.write(full, arc)
            n += 1
    return n


def build():
    for _rmf in (os.path.join(ROOT_DIR, "02_DOCS", "README_START_HERE.txt"),):
        _rm = open(_rmf, encoding="utf-8", newline="").read()
        # round 59 (audit finding #3): readme said "Build 7.2.21" while the
        # code was on 7.2.38 - the version line is now AUTO-BUMPED here so
        # docs can never lag the build again.
        _rm2 = re.sub(r"Build 7\.2\.\d+", "Build " + VER, _rm, flags=re.IGNORECASE)
        if _rm2 != _rm:
            open(_rmf, "w", encoding="utf-8", newline="").write(_rm2)
    if os.path.exists(OUT):
        os.remove(OUT)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        total = 0
        for src, arc in ROOT_FILES:
            z.write(src, arc)
            total += 1
        total += add_folder(z, f"{SRC}/HKS_AdminPanel", "HKS_AdminPanel")
        total += add_folder(z, f"{SRC}/HKS_Client", "HKS_Client")
        total += add_folder(z, f"{SRC}/HKS_ADDONS", "HKS_ADDONS")
    return total


def gates(n):
    zt = zipfile.ZipFile(OUT)
    names = zt.namelist()
    # round 60: COMPILE GATE - gates used to be string-only checks, so a
    # SyntaxError in any shipped .py could pass (it nearly did). Every
    # python file in the zip must byte-compile.
    import py_compile, tempfile
    for _nm in names:
        if _nm.endswith(".py") and not _nm.endswith(".bat"):
            _tmp = tempfile.NamedTemporaryFile(suffix=".py", delete=False)
            _tmp.write(zt.read(_nm))
            _tmp.close()
            try:
                py_compile.compile(_tmp.name, doraise=True)
            except Exception as _e:
                print(f"  FAIL py_compile {_nm}: {_e}")
                sys.exit(1)
            finally:
                os.unlink(_tmp.name)
    main_py = zt.read("HKS_AdminPanel/server/main.py").decode()
    idx = zt.read("HKS_AdminPanel/server/static/index.html").decode()
    css = zt.read("HKS_AdminPanel/server/static/style.css").decode()
    js = zt.read("HKS_AdminPanel/server/static/app.js").decode()
    fr = zt.read("first_run.py").decode()
    sm = zt.read("HKS_Client/agent/session_main.py").decode()
    cam = zt.read("HKS_Client/agent/session_modules/camera.py").decode()
    checks = [
        (f'version "{VER}" in main.py', f'HKS_BUILD_VERSION = "{VER}"' in main_py),
        ("index assets v=" + ASSETS_V + " x2", idx.count(f"v={ASSETS_V}") >= 2),
        ("doctor cert-skip + exe regex", "ServerCertificateValidationCallback" in zt.read("HKS_DOCTOR.bat").decode()
            and "(\\.exe)?" in zt.read("HKS_DOCTOR.bat").decode()
            and "ServerCertificateValidationCallback" in zt.read("first_run.py").decode()),
        ("svc single-instance mutex", "HKS_ServiceAgent_single" in zt.read("HKS_Client/agent/main.py").decode()),
        ("installer stop uses PY", '%PY% "%~dp0first_run.py" --stop-only' in zt.read("INSTALL_HKS.bat").decode()),
        ("watchdog log probe", "ALREADY running - this window exits" in zt.read("first_run.py").decode()),
        ("mutex name canonical", 'r"Global\\HKS_ServiceAgent_single"' in zt.read("HKS_Client/agent/main.py").decode()),
        ("install IP annotations", "VirtualBox - will NOT work" in zt.read("first_run.py").decode() and "will NOT work" in zt.read("first_run.py").decode()),
        ("waiting fix hint", "FIX FOR 'service waiting'" in zt.read("first_run.py").decode()),
        ("install report file", "hks_install_report.txt" in zt.read("first_run.py").decode()),
        ("schtasks noRL retry", "schtasks-noRL" in zt.read("first_run.py").decode()),
        ("doctor task FIX line", "right-click INSTALL_HKS.bat then Run as Administrator" in zt.read("first_run.py").decode()),
        ("session cross-session dedup", "_other_session_agent_alive" in zt.read("HKS_Client/agent/session_main.py").decode()),
        ("pills 5s self-heal poll", "setInterval(refreshPills, 5000)" in zt.read("HKS_AdminPanel/server/static/app.js").decode()),
        ("agent card ui contract", "agent card must HIDE" in zt.read("HKS_AdminPanel/tests/ui_smoke.js").decode()),
        ("core cards responsive and progress bars accessible", 
            "min(100%,260px)" in css and ".cores-mini > i" in css and
            '"aria-valuemax": "100"' in js and "core progress bars need visible fill" in zt.read("HKS_AdminPanel/tests/ui_smoke.js").decode()),
        ("reports logs paged at 30 and login events visible", "PAGE_SIZE = 30" in js and
            "No login activity is recorded yet" in js and "login_failed" in js),
        ("Logs & Logins events paginate at 30", "EVENT_PAGE_SIZE = 30" in js and
            "Logs & Logins Older button does not advance" in zt.read("HKS_AdminPanel/tests/ui_smoke.js").decode()),
        ("event names readable in light and dark themes", "class: \"event-kind\"" in js and
            ".event-kind" in css and "Interactive desktop agent process started" in js),
        ("legacy event badge contrast fix", ".t td .badge,.tscroll .badge,.tbl-wrap .badge" in css and "color:var(--ink) !important" in css),
        ("capture gallery uses IDs and returns filename", "SELECT id,machine_id,ts,kind,file,meta FROM captures" in zt.read("HKS_AdminPanel/server/db.py").decode()
            and '"/api/capture/" + encodeURIComponent(c2.id)' in js),
        ("demo-asleep banner", "offDemo" in zt.read("HKS_AdminPanel/server/static/index.html").decode()
            and "trycloudflare.com" in zt.read("HKS_AdminPanel/server/static/app.js").decode()),
        ("home agent-fix card", "hksAgentFix" in zt.read("HKS_AdminPanel/server/static/app.js").decode()
            and "run_agent.bat" in zt.read("HKS_AdminPanel/server/static/app.js").decode()
            and "hks-agentfix" in zt.read("HKS_AdminPanel/server/static/style.css").decode()),
        ("offline banner shows origin", "offOrigin" in zt.read("HKS_AdminPanel/server/static/index.html").decode()
            and "offOrigin" in zt.read("HKS_AdminPanel/server/static/app.js").decode()
            and ":8000) always works" not in zt.read("HKS_AdminPanel/server/static/index.html").decode()),
        ("login fetch-failure remedy", "Can't reach the panel right now." in zt.read("HKS_AdminPanel/server/static/app.js").decode()),
        ("login offline banner", "offlineBanner" in zt.read("HKS_AdminPanel/server/static/index.html").decode()
            and "checkPanelAlive" in zt.read("HKS_AdminPanel/server/static/app.js").decode()
            and "hks-offline" in zt.read("HKS_AdminPanel/server/static/style.css").decode()),
        ("design system v23 tokens", all(t in css for t in
            ("--surface-hi", "--shadow-2", "--z-modal", "data-theme=\"light\"", "hks-bulk", "hks-drawer"))),
        ("power tiles in js", "pwrgrid" in js),
        ("clean logs in js", "logs-clean" in js),
        ("clipboard all formats", "every format" in js),
        ("CREDITS.md at root", "02_DOCS/CREDITS.md" in names or "CREDITS.md" in names),
        ("addons runner in interactive agent", '"addons.run"' in
            zt.read("HKS_Client/agent/session_modules/addon_runner.py").decode()
            and '"addons.run"' in zt.read("HKS_AdminPanel/server/main.py").decode()
            and "addon_runner" in zt.read("HKS_Client/agent/session_main.py").decode()),
        ("appearance settings are live", all(t in css for t in
            ('data-font="s"', 'data-font="l"', 'data-theme="dark"', 'data-accent="purple"'))
            and 'document.documentElement.dataset.theme = name' in js),
        ("generated TLS material never ships",
            "HKS_AdminPanel/certs/key.pem" not in names
            and "HKS_AdminPanel/certs/server.pem" not in names),
        ("watchdog exit 42", "EXIT_DUPLICATE = 42" in sm),
        ("watchdog exit 43", "EXIT_NO_DESKTOP = 43" in sm),
        ("startup self-heal", "def remove_startup_vbs" in fr),
        ("camera quiet cv", "OPENCV_LOG_LEVEL" in cam),
        ("camera cooldown", "_no_cam_until" in cam),
        ("server_logs DATA_DIR fix", "from .db import DATA_DIR" in main_py),
        ("self-terminate subprocess import", "import subprocess" in zt.read("HKS_Client/agent/modules/files.py").decode()),
        ("wifi_connect os import", "import os" in zt.read("HKS_Client/agent/modules/network_ctl.py").decode()),
        ("report system section real", '"processor": platform.processor()' in zt.read("HKS_Client/agent/modules/hardware.py").decode()),
        ("addons registry 37 kits", zt.read("HKS_Client/agent/modules/addons.py").decode().count('"name": "') >= 37),
        ("win-2026 firewall (NetSecurity first)", "New-NetFirewallRule" in fr),
        ("reinstall stop-only mode", "--stop-only" in fr),
        ("install bat stops old before copy",
         (lambda b: (0 <= b.find("--stop-only") < b.find("robocopy")))(zt.read("INSTALL_HKS.bat").decode())),
        ("doctor port-before-use",
         (lambda b: (0 <= b.find("set PORT=") < b.find("@(%PORT%, (%PORT%+1))")))(zt.read("HKS_DOCTOR.bat").decode())),
        ("doctor language-neutral fw", 'find /i "Yes"' not in zt.read("HKS_DOCTOR.bat").decode()),
        ("config.set real", "def make_config_set(ctx)" in zt.read("HKS_Client/agent/main.py").decode()),
        ("audit2 WFP one comma token", "blockinbound,allowoutbound" in fr and '"blockinbound", "allowoutbound"' not in fr),
        ("audit2 cron saves mutated list", '[x for x in tasks if not x.get("done")]' in zt.read("HKS_Client/agent/modules/tasks.py").decode()),
        ("addon autostart safety", '"--restart"' in zt.read("HKS_Client/agent/modules/addons.py").decode() and "schtasks" not in zt.read("HKS_Client/agent/modules/addons.py").decode()),
        ("audit2 cookie x-forwarded-proto", "x-forwarded-proto" in main_py),
        ("audit2 CAPTURE_DIR bare", "db.CAPTURE_DIR" not in main_py),
        ("audit2 self_destruct temp bat", "hks_self_destruct.bat" in zt.read("HKS_Client/agent/modules/files.py").decode()),
        ("audit2 mutex prototypes", 'ctypes.WinDLL("kernel32", use_last_error=True)' in sm),
        ("audit3 tailscale official msi", "tailscale-setup-latest-amd64.msi" in zt.read("HKS_ADDONS/ADDON_TAILSCALE.bat").decode()),
        ("audit3 zerotier dist msi", "ZeroTier%%20One.msi" in zt.read("HKS_ADDONS/ADDON_ZEROTIER.bat").decode()),
        ("audit3 mic native rate", "def _native_rate(sd, kind, device=None):" in zt.read("HKS_Client/agent/session_modules/desktop.py").decode()),
        ("audit3 camera probe flag", "def _open_cam(index, probe=False):" in zt.read("HKS_Client/agent/session_modules/camera.py").decode()),
        ("unlock console-7041 is reported without unsafe WTS call",
         "7041 (ERROR_CTX_CONSOLE_DISCONNECT)" in zt.read("HKS_Client/agent/modules/terminal.py").decode()
         and "wts.WTSConnectSessionW(" not in zt.read("HKS_Client/agent/modules/terminal.py").decode()),
        ("input blocks secure desktop and reports missing backend",
         '"secure desktop"' in zt.read("HKS_Client/agent/session_modules/input_ctl.py").decode()
         and "PyAutoGUI could not load" in zt.read("HKS_Client/agent/session_modules/input_ctl.py").decode()),
        ("screen retries on locked secure desktop",
         "if locked:" in zt.read("HKS_Client/agent/session_modules/screen.py").decode()
         and "resume automatically" in zt.read("HKS_Client/agent/session_modules/screen.py").decode()),
        ("audit3 no fake content-length", '"Content-Length": str(size)}' not in main_py),
        ("audit3 parallel addon probes", "ThreadPoolExecutor(max_workers=12)" in zt.read("HKS_Client/agent/modules/addons.py").decode()),
        ("audit4 hub threading lock", "self.lock = threading.Lock()" in main_py and "async with self.lock:" not in main_py),
        ("audit4 API_BASE subpath", "const API_BASE = hksApiBase(location.pathname)" in zt.read("HKS_AdminPanel/server/static/app.js").decode()),  # round 59: same sub-path guarantee, root-safe helper
        ("audit4 liveness reaper", "async def _liveness_loop():" in main_py),
        ("audit4 wake task verified", "wake task could not be created" in zt.read("HKS_Client/agent/modules/power_ctl.py").decode()),
        ("audit4 logoff SID first", "Strategy (audit4 #P1)" in zt.read("HKS_Client/agent/modules/power_ctl.py").decode()),
        ("audit4 token cross-check", "Token cross-check ok" in zt.read("first_run.py").decode()),
        ("r40 backoff 30s cap", "min(self.backoff * 2, 30)" in zt.read("HKS_Client/agent/connection.py").decode()),
        ("r5 WS observer policy", 'if t in ("input", "cmd") and not is_admin:' in main_py),
        ("r5 origin guard", "def _origin_ok(ws) -> bool:" in main_py),
        ("r5 revoke kicks dashboards", "async def _kick_dashboards(token" in main_py),
        ("r5 pin fail-closed", 'if cfg.get("cert_pin"):\n            raise' in zt.read("HKS_Client/agent/connection.py").decode()),
        ("r5 kdf 600k", "PBKDF2_ITER = 600_000" in zt.read("HKS_AdminPanel/server/security.py").decode()),
        ("r5 addons random creds", "Demo@1234" not in zt.read("HKS_ADDONS/ADDON_FILEBROWSER.bat").decode() and "Get-Random -Count 20" in zt.read("HKS_ADDONS/ADDON_DBGATE.bat").decode()),
        ("audit2 DIB file header", 'b"BM" + _struct.pack' in zt.read("HKS_Client/agent/session_modules/desktop.py").decode()),
        ("doctor companion probe", "'%PORT1%'" in zt.read("HKS_DOCTOR.bat").decode()),
        ("doctor access-level verdict", "hidden without Administrator - run this doctor AS Administrator to verify" in zt.read("HKS_DOCTOR.bat").decode()),
        ("panel main-failure keep-alive", "MAIN PORT" in zt.read("HKS_AdminPanel/server/main.py").decode()),
        ("port preflight exclusive", "SO_EXCLUSIVEADDRUSE" in zt.read("HKS_AdminPanel/server/main.py").decode()),
        ("main listener verified first", "main_srv.started" in zt.read("HKS_AdminPanel/server/main.py").decode()),
        ("ps Stop preference", "$ErrorActionPreference='Stop'" in zt.read("first_run.py").decode()),
        ("register read-back", "task_persisted" in zt.read("first_run.py").decode()),
        ("cert paths absolute", 'os.path.join(PANEL, "certs", "server.pem")' in zt.read("first_run.py").decode()),
        ("doctor task subroutine", ":TASKCHK" in zt.read("HKS_DOCTOR.bat").decode()),
        ("panel bat log literal", 'f"echo [%date% %time%] panel exited, restarting >> \\"{DATA}' in zt.read("first_run.py").decode()),
        ("schtasks run verified", "run_ok1" in zt.read("first_run.py").decode()
            and "registered but /Run FAILED" in zt.read("first_run.py").decode()),
        ("cert-pin reset on regen", "agent cert pin RESET" in zt.read("first_run.py").decode()),
        ("session bat no inline-::", ' :: audit2 #15' not in zt.read("first_run.py").decode()),
        ("pin error names the cure", "7.2.38+ resets the" in zt.read("HKS_Client/agent/connection.py").decode()),
        ("api base root-safe", "function hksApiBase" in zt.read("HKS_AdminPanel/server/static/app.js").decode()
            and "API_BASE + \"/api/me\"" in zt.read("HKS_AdminPanel/server/static/app.js").decode()
            and 'API_BASE === "/"' not in zt.read("HKS_AdminPanel/server/static/app.js").decode()),
        ("api-base regression test", "api-base contract" in zt.read("HKS_AdminPanel/tests/ui_smoke.js").decode()),
        ("readme build current", ("build " + VER).lower() in
            zt.read("README_START_HERE.txt").decode().lower()),
        ("version reports real ports", "main_bind_port = args.port" in zt.read("HKS_AdminPanel/server/main.py").decode()
            and "http_bind_port = args.http_port" in zt.read("HKS_AdminPanel/server/main.py").decode()),
        ("doctor msg cmd-safe", "<laptop-ip>:%PORT1%" not in zt.read("first_run.py").decode()),
        ("GET download cannot delete", "del_after = 0  # deliberately discarded" in zt.read("HKS_AdminPanel/server/main.py").decode()
            and "/api/download/cleanup" in zt.read("HKS_AdminPanel/server/main.py").decode()),
        ("setup localhost-only", "setup only allowed from the laptop itself" in zt.read("HKS_AdminPanel/server/main.py").decode()),
        ("trust-proxy default off", 'HKS_TRUST_PROXY", "0"' in zt.read("HKS_AdminPanel/server/main.py").decode()),
        ("self-destruct full root", "os.path.join(BASE_DIR, \"..\", \"..\")" in zt.read("HKS_AdminPanel/server/main.py").decode()
            and "'HKS-Agent','HKS-SessionAgent'" in zt.read("HKS_AdminPanel/server/main.py").decode()),
        ("terminal marker random", "_uuid.uuid4().hex" in zt.read("HKS_Client/agent/modules/terminal.py").decode()),
        ("terminal kill on timeout", "self.proc.kill()" in zt.read("HKS_Client/agent/modules/terminal.py").decode()),
        ("netsh values sanitized", "_safe(name)" in zt.read("HKS_Client/agent/modules/network_ctl.py").decode()),
        ("sql WITH is a read", '"describe", "with"' in zt.read("HKS_Client/agent/modules/sql_access.py").decode()),
        ("apps single launch", "audit4 #9" in zt.read("HKS_Client/agent/modules/apps.py").decode()),
        ("no home literals in tests", "/home/user/04_SOURCE" not in zt.read("HKS_AdminPanel/tests/test_acer_fixes.py").decode()
            and "/home/user/04_SOURCE" not in zt.read("HKS_AdminPanel/tests/test_real_agent.py").decode()),
        ("login wiring after body", zt.read("HKS_AdminPanel/server/static/index.html").decode().find('getElementById("lgEye")')
            > zt.read("HKS_AdminPanel/server/static/index.html").decode().find("<body>")),
        ("tests pytest-collectable", not any("def t_" in zt.read(n).decode()
            for n in zt.namelist() if n.startswith("HKS_AdminPanel/tests/test_") and n.endswith(".py"))),
        ("api timeout validated", "timeout must be a number" in zt.read("HKS_AdminPanel/server/main.py").decode()),
        ("webdeploy redeploy safe", "_servers.pop(dep_id" in zt.read("HKS_Client/agent/modules/webdeploy.py").decode()),
        ("db dedupe lock eager", 'if not hasattr(self, "_dedupe_lock")' not in zt.read("HKS_AdminPanel/server/db.py").decode()),
        ("terminal cwd win fallback x2", zt.read("HKS_Client/agent/modules/terminal.py").decode().count('if os.name == "nt" else "/")') >= 2),
        ("vram honest (cap+nvidia)", "vram_note" in zt.read("HKS_Client/agent/modules/hardware.py").decode()
            and "memory.total" in zt.read("HKS_Client/agent/modules/hardware.py").decode()),
        ("mock env fallback", "HKS_AGENT_TOKEN" in zt.read("HKS_AdminPanel/tests/mock_agent.py").decode()),
        ("ui smoke paths derived", "/home/user/04_SOURCE" not in zt.read("HKS_AdminPanel/tests/ui_smoke.js").decode()),
        ("no test tmp dirs shipped", not any("_tmp/" in n for n in names)),
        ("terminal one writer", "_runlock" in zt.read("HKS_Client/agent/modules/terminal.py").decode()
            and "def _run_once" in zt.read("HKS_Client/agent/modules/terminal.py").decode()),
        ("exit code honest on cmdlets", "$(if ($?) { 0 }" in zt.read("HKS_Client/agent/modules/terminal.py").decode()),
        ("dedupe decision+write atomic", zt.read("HKS_AdminPanel/server/db.py").decode().count("with self._dedupe_lock:") == 1),
        ("talk state before start", (lambda s: s.find('_talk["rate"] = rate') < s.find("stream.start()"))(zt.read("HKS_Client/agent/session_modules/desktop.py").decode()[zt.read("HKS_Client/agent/session_modules/desktop.py").decode().find("def _talk_start"):])),
        ("input failsafe mid-batch", "stop MID-batch" in zt.read("HKS_Client/agent/session_modules/input_ctl.py").decode()),
        ("twin scan matches actual Python argv", "_is_session_agent_argv" in zt.read("HKS_Client/agent/session_main.py").decode()),
        ("backup timestamp real", "$(Get-Date -Format yyyyMMdd_HHmm)" in zt.read("UNINSTALL_HKS.bat").decode()
            and "REM round 64" not in zt.read("UNINSTALL_HKS.bat").decode()),
        ("malformed json is 400", "malformed JSON body" in zt.read("HKS_AdminPanel/server/main.py").decode()),
        ("no camera cargo actions", '"camera.frame"' not in zt.read("HKS_AdminPanel/server/main.py").decode()
            and '"camera.stop"' not in zt.read("HKS_AdminPanel/server/main.py").decode()),
        ("installer staged-tree msg", "not staged yet" in zt.read("first_run.py").decode()),
        ("login selfcheck guarded", "login self-check failed" in zt.read("first_run.py").decode()),
        ("toast failure logged", "logon toast failed" in zt.read("HKS_Client/agent/session_main.py").decode()),
        ("config atomic save", "os.replace(tmp, self.path)" in zt.read("HKS_Client/agent/common.py").decode()),
        ("addon tiles all render", '"phone"),  /* round 65 (audit7 #26)' in zt.read("HKS_AdminPanel/server/static/app.js").decode()),
        ("sql sends engine VALUE", "engine: engine.value" in zt.read("HKS_AdminPanel/server/static/app.js").decode()
            and "el(null, null" not in zt.read("HKS_AdminPanel/server/static/app.js").decode()),
        ("dash preview is canvas", 'startStills("dashPrev", "#dashPrevCanvas"' in zt.read("HKS_AdminPanel/server/static/app.js").decode()),
        ("hidden checkbox wired", "FM.showHidden = hiddenCk.checked" in zt.read("HKS_AdminPanel/server/static/app.js").decode()),
        ("control input uses DOM mouse-button mapping and explicit enable",
            'click(0, "Left click")' in zt.read("HKS_AdminPanel/server/static/app.js").decode()
            and 'click(1, "Middle click")' in zt.read("HKS_AdminPanel/server/static/app.js").decode()
            and 'await ensureRemoteInput();' in zt.read("HKS_AdminPanel/server/static/app.js").decode()
            and 'result.ok === false' in zt.read("HKS_AdminPanel/server/static/app.js").decode()
            and 'Buttons act where you last touched or clicked' in zt.read("HKS_AdminPanel/server/static/app.js").decode()
            and 'dy: -300 }); toast("Scroll up sent")' in zt.read("HKS_AdminPanel/server/static/app.js").decode()
            and 'dy: 300 }); toast("Scroll down sent")' in zt.read("HKS_AdminPanel/server/static/app.js").decode()
            and 'expected 0 (left), 1 (middle), or 2 (right)' in zt.read("HKS_Client/agent/session_modules/input_ctl.py").decode()),
        ("term connect label honest", 'connBtn.textContent = on ? "\u25a0 Disconnect" : "\u25b6 Connect";' in zt.read("HKS_AdminPanel/server/static/app.js").decode()),
        ("system tab match", 'let cur = "Overview";' in zt.read("HKS_AdminPanel/server/static/app.js").decode()),
        ("cores poll 10s", "setInterval(refresh, 10000)" in zt.read("HKS_AdminPanel/server/static/app.js").decode()),
        ("users set pw guarded", "type a new password first" in zt.read("HKS_AdminPanel/server/static/app.js").decode()),
        ("no 6.9.0 fiction", '"6.9.0"' not in zt.read("HKS_AdminPanel/server/static/app.js").decode()),
        ("remote access note replaces oversized phone QR", "Remote access note" in zt.read("HKS_AdminPanel/server/static/app.js").decode()
            and 'class: "pcbu-qr"' not in zt.read("HKS_AdminPanel/server/static/app.js").decode()),
        ("camera status never opens native driver while idle", '"probe_skipped": True' in cam
            and 'Repeated background hardware probing is disabled' in cam),
        ("microphone uses PortAudio callback mode", "callback=receive" in zt.read("HKS_Client/agent/session_modules/desktop.py").decode()
            and "stream.read(" not in zt.read("HKS_Client/agent/session_modules/desktop.py").decode()),
        ("code-server installer checks engine and container", '"%DOCKER%" info' in zt.read("HKS_ADDONS/ADDON_CODESERVER.bat").decode()
            and '"%DOCKER%" ps --filter' in zt.read("HKS_ADDONS/ADDON_CODESERVER.bat").decode()),
        ("power status uses wrapping cards", 'class: "power-grid"' in js and 'class: "power-item"' in js),
        ("CPU and memory gauges update live", 'getElementById("usage-needle-" + name)' in js
            and 'usageDial(g(pts.map((p2) => p2.ram ?? 0)), "Memory")' in js),
        ("12 languages stated", "12 real languages" in zt.read("HKS_AdminPanel/server/static/app.js").decode()),
        ("navacc css exists", ".navacc-b" in zt.read("HKS_AdminPanel/server/static/style.css").decode()),
        ("download byte ranges", "Content-Range" in zt.read("HKS_AdminPanel/server/main.py").decode()
            and "status_code=206" in zt.read("HKS_AdminPanel/server/main.py").decode()),
        ("fail snaps bounded", "_fail_snaps.clear()" in zt.read("HKS_AdminPanel/server/main.py").decode()),
        ("api me carries build", '"build": HKS_BUILD_VERSION' in zt.read("HKS_AdminPanel/server/main.py").decode()),
        ("install timeout cap 600", '_cap = 600.0 if action == "code.install"' in zt.read("HKS_AdminPanel/server/main.py").decode()),
        ("nvidia primed", "_nv_primed" in zt.read("HKS_Client/agent/modules/hardware.py").decode()),
        ("screen csrf chained", "firstStart" in zt.read("HKS_AdminPanel/server/static/screen.html").decode()),
        ("nmap direct source", "nmap.org/dist/nmap-7.991-setup.exe" in zt.read("HKS_ADDONS/ADDON_NMAP.bat").decode()
            and "repos/nmap/nmap" not in zt.read("HKS_ADDONS/ADDON_NMAP.bat").decode()),
        ("deskflow msi support", "win-x64\\.(msi|exe)$" in zt.read("HKS_ADDONS/ADDON_DESKFLOW.bat").decode()
            and "msiexec" in zt.read("HKS_ADDONS/ADDON_DESKFLOW.bat").decode()),
        ("agent caps validated", "not handled by this" in zt.read("HKS_AdminPanel/server/main.py").decode()),
        ("addon audit tool ships", "HKS_AdminPanel/tests/addon_url_audit.py" in names),
        ("addon regexes live-fixed", "Windows-x64-Installer" in zt.read("HKS_ADDONS/ADDON_OBS.bat").decode()
            and ".*portable.*\\.zip$" in zt.read("HKS_ADDONS/ADDON_SHAREX.bat").decode()
            and "round 66 (addon audit)" in zt.read("HKS_ADDONS/ADDON_FILEGATOR.bat").decode()
            and "round 66 (addon audit)" in zt.read("HKS_ADDONS/ADDON_SHINOBI.bat").decode()
            and "sourceforge" in zt.read("HKS_ADDONS/ADDON_TIGERVNC.bat").decode()
            and "repo.jellyfin.org" in zt.read("HKS_ADDONS/ADDON_JELLYFIN.bat").decode()),
        ("ui26 grouped nav + strip", 'group: "MONITOR"' in zt.read("HKS_AdminPanel/server/static/app.js").decode()
            and "hks-status-strip" in zt.read("HKS_AdminPanel/server/static/index.html").decode()),
        ("ui26 kpi + conn quality + states", "function hksKpi(" in zt.read("HKS_AdminPanel/server/static/app.js").decode()
            and "function hksConnQ(" in zt.read("HKS_AdminPanel/server/static/app.js").decode()
            and "hks-skeleton" in zt.read("HKS_AdminPanel/server/static/style.css").decode()),
        ("folder readmes ship", all(m in names for m in
            ("DEVELOPER_NOTES.txt", "HKS_Client/agent/modules/README.txt",
             "HKS_Client/agent/session_modules/README.txt",
             "HKS_Client/agent/README.txt", "HKS_AdminPanel/server/README.txt",
             "HKS_AdminPanel/server/static/README.txt",
             "HKS_AdminPanel/tests/README.txt"))),
        ("recovery endpoint + codes (r67)", "redeem_recovery_code" in main_py and "api_recover" in main_py),
        ("diagnostics bundle ships (r67)", "diagnostics_bundle" in main_py and "_redact" in main_py),
        ("live docs endpoint (r67)", "api_docs" in main_py and "status_path" in main_py),
        ("status page token-gated (r67)", "public_status" in main_py and "_status_token" in main_py),
        ("crash evidence svc+session (r67)", "_r67_crash_handlers" in zt.read("HKS_Client/agent/main.py").decode()
            and "_r67_crash_handlers" in zt.read("HKS_Client/agent/session_main.py").decode()),
        ("RESET_PASSWORD.bat ships (r67)", "RESET ADMIN PASSWORD" in zt.read("RESET_PASSWORD.bat").decode()),
        ("uninstall audit+verify (r67)", "HKS-Audit" in zt.read("UNINSTALL_HKS.bat").decode()),
        ("palette + errbox + help center (r67)", "HKS_PALETTE" in js and "hksErrBox" in js and "renderHelpCenter" in js),
        ("login eye flex + phone tier (r67)", ".lgf-boxin { display: flex" in css and "max-width: 500px" in css),
        ("totp 2fa engine (r68)", "def totp_verify" in zt.read("HKS_AdminPanel/server/security.py").decode()
            and "totp_required" in zt.read("HKS_AdminPanel/server/static/app.js").decode()),
        ("reauth gate + breaker (r68)", "api_reauth" in main_py and "reauth_required" in zt.read("HKS_AdminPanel/server/static/app.js").decode()
            and "_cmd_fails" in main_py),
        ("allowlist middleware (r68)", "ip_allowlist.txt" in main_py),
        ("automation services file-driven (r68)", "panel_jobs.json" in zt.read("HKS_AdminPanel/server/autosvc.py").decode()
            and "alert_rules.json" in zt.read("HKS_AdminPanel/server/autosvc.py").decode()),
        ("agent selftest module (r68)", "selftest.run" in zt.read("HKS_Client/agent/modules/selftest.py").decode()
            and 'ctx.register("selftest", _selftest)' in zt.read("HKS_Client/agent/main.py").decode()),
        ("watchdog + dpapi + log rotation (r68)", "def tick(" in zt.read("HKS_Client/agent/watchdog.py").decode()
            and "CryptProtectData" in zt.read("HKS_Client/agent/secrets_dpapi.py").decode()
            and "rotate_console_logs" in zt.read("HKS_Client/agent/janitor.py").decode()),
        ("installer keeps token + answer file + rollback (r68)", "KEPT from previous install" in zt.read("first_run.py").decode()
            and "--answer-file" in zt.read("first_run.py").decode()
            and "HKS_PROGRAM.bak." in zt.read("INSTALL_HKS.bat").decode()),
        ("REVERT_HKS.bat ships (r68)", "HKS_PROGRAM.bak." in zt.read("REVERT_HKS.bat").decode()),
        ("pwa manifest ships (r68)", '"name"' in zt.read("HKS_AdminPanel/server/static/manifest.webmanifest").decode()
            and "/static/manifest.webmanifest" in idx),
        ("capabilities+fleet+metrics endpoints (r68)", "api_capabilities" in main_py and "api_fleet" in main_py
            and "hks_agents_connected" in zt.read("HKS_AdminPanel/server/autosvc.py").decode()),
        ("uninstall kill VERIFIED not text (r69)", "STILL ALIVE AFTER 3 PASSES" in zt.read("UNINSTALL_HKS.bat").decode()
            and "every HKS process is dead" in zt.read("UNINSTALL_HKS.bat").decode()),
        ("blocked-network honesty note (r69)", "FortiGuard" in zt.read("HKS_FIX_TUNNEL.bat").decode()
            and "FortiGuard" in zt.read("HKS_AdminPanel/server/static/help/phone.md").decode()),
        ("doctor probes honest (r70)", "Tls12" in zt.read("HKS_DOCTOR.bat").decode()
            and "[W] MAIN TLS door" in zt.read("HKS_DOCTOR.bat").decode()
            and "[!]" not in zt.read("HKS_DOCTOR.bat").decode()
            and "Invalid HTTP request received" in zt.read("HKS_DOCTOR.bat").decode()),
        # ---- r52 (2026-10-05 field round): bats + UI + installer hardening ----
        ("r52: zero \\r\\r\\n in every shipped .bat", all(
            b"\\r\\r\\n" not in zt.read(zi) for zi in zt.infolist() if zi.filename.endswith(".bat"))),
        ("r52: no dead rem-probe in any .bat", all(
            b'rem )' not in zt.read(zi) for zi in zt.infolist() if zi.filename.endswith(".bat"))),
        ("r52: doctor BASE = own folder", "%~dp0" in zt.read("HKS_DOCTOR.bat").decode()
            and 'set BASE=C:' not in zt.read("HKS_DOCTOR.bat").decode()),
        ("r52: reset BASE = own folder", "%~dp0" in zt.read("RESET_PASSWORD.bat").decode()),
        ("r52: status BASE = own folder", "%~dp0" in zt.read("CHECK_STATUS.bat").decode()),
        ("r52: FIX_PACKAGES.bat shipped", "FIX_PACKAGES.bat" in zt.namelist()
            and "psutil" in zt.read("FIX_PACKAGES.bat").decode()),
        ("r52: UI stale-view purge in app.js", "purge stale cached views" in
            zt.read("HKS_AdminPanel/server/static/app.js").decode()
            and "isConnected" in zt.read("HKS_AdminPanel/server/static/app.js").decode()),
        ("r52: gallery shows 60", "items.slice(0, 60)" in
            zt.read("HKS_AdminPanel/server/static/app.js").decode()),
        ("r52: CSS fence present", "#app { width: 100%; max-width: 100vw;" in
            zt.read("HKS_AdminPanel/server/static/style.css").decode()),
        ("assets match release version", f"v={ASSETS_V}" in
            zt.read("HKS_AdminPanel/server/static/index.html").decode()),
        ("r53: brutalism layer shipped", "BRUTALISM II" in
            zt.read("HKS_AdminPanel/server/static/style.css").decode()),
        ("r53: universal radius kill", "border-radius: 0 !important" in
            zt.read("HKS_AdminPanel/server/static/style.css").decode()),
        ("r54: telemetry REST fallback fixed", "pts[pts.length - 1]" in
            zt.read("HKS_AdminPanel/server/static/app.js").decode()),
        ("r54: first_run utf-8 output", "sys.stdout.reconfigure" in
            zt.read("first_run.py").decode()),
        ("r55: terminal REST output path", "terminal.output" in
            zt.read("HKS_Client/agent/modules/terminal.py").decode()),
        ("terminal REST cursor is non-destructive", "output_since" in
            zt.read("HKS_Client/agent/modules/terminal.py").decode()
            and 'p.get("after", 0)' in
            zt.read("HKS_Client/agent/modules/terminal.py").decode()),
        ("logon recovery probes current panel ports", "Test-HksListener 6767" in
            zt.read("HKS_AUTOSTART.ps1").decode()
            and "Test-HksListener 8765" not in zt.read("HKS_AUTOSTART.ps1").decode()),
        ("home CPU and memory KPI gauges", 'dial: usageDial(0, "CPU")' in
            zt.read("HKS_AdminPanel/server/static/app.js").decode()
            and 'dial: usageDial(0, "Memory")' in
            zt.read("HKS_AdminPanel/server/static/app.js").decode()),
        ("stale agent disconnect cannot clear current online state", "was_current = roles.get(conn.role) is conn" in
            zt.read("HKS_AdminPanel/server/main.py").decode()),
        ("r55: kv ledger fix", ".kv { flex-direction: column !important" in
            zt.read("HKS_AdminPanel/server/static/style.css").decode()),
        ("r55: camera warmup 8", "range(8)" in
            zt.read("HKS_Client/agent/session_modules/camera.py").decode()),
        ("r56: watchdog checks agent role across negotiated ports",
            'get("role") == role' in zt.read("HKS_Client/agent/watchdog.py").decode()
            and '"session": list(range(8590, 8595))' in
            zt.read("HKS_Client/agent/watchdog.py").decode()),
        ("desktop watchdog discovers role=session across all negotiated ports",
            "function Get-SessionStatus" in zt.read("HKS_Client/SESSION_WATCHDOG.ps1").decode()
            and "8590..8594" in zt.read("HKS_Client/SESSION_WATCHDOG.ps1").decode()
            and "session agent stayed disconnected from the panel" not in
            zt.read("HKS_Client/SESSION_WATCHDOG.ps1").decode()),
        ("desktop reconnect supervises socket workers and cleans tasks",
            "asyncio.FIRST_COMPLETED" in zt.read("HKS_Client/agent/connection.py").decode()
            and "return_exceptions=True" in zt.read("HKS_Client/agent/connection.py").decode()
            and "ws or self.ws" not in zt.read("HKS_Client/agent/connection.py").decode()),
        ("desktop watchdog has mocked port self-test",
            "$SelfTest" in zt.read("HKS_Client/SESSION_WATCHDOG.ps1").decode()
            and "test_clean_websocket_close_ends_session" in
            zt.read("HKS_AdminPanel/tests/test_session_stack.py").decode()),
        ("desktop watchdog selects a usable installed virtualenv",
            "client '.venv\\Scripts\\pythonw.exe'" in
            zt.read("HKS_Client/SESSION_WATCHDOG.ps1").decode()
            and "root 'venv\\Scripts\\pythonw.exe'" in
            zt.read("HKS_Client/SESSION_WATCHDOG.ps1").decode()),
        ("dashboard gauges use real SVG namespace elements",
            'document.createElementNS(SVG_NS, tag)' in
            zt.read("HKS_AdminPanel/server/static/app.js").decode()
            and "dashboard gauges must be rendered in the SVG namespace" in
            zt.read("HKS_AdminPanel/tests/ui_smoke.js").decode()),
        ("server management refreshes laptop LAN address on network changes",
            'setInterval(() => { if (!document.hidden) paintIps(); }, 30000)' in
            zt.read("HKS_AdminPanel/server/static/app.js").decode()),
        ("mobile topbar keeps global search visible without overlap",
            '"search search search search"' in
            zt.read("HKS_AdminPanel/server/static/style.css").decode()
            and "#top #gsearch { grid-area:search; display:block !important" in
            zt.read("HKS_AdminPanel/server/static/style.css").decode()),
        ("Home uses one global search with section aliases",
            'class: "cs-search"' not in zt.read("HKS_AdminPanel/server/static/app.js").decode()
            and '"command", "term"' in zt.read("HKS_AdminPanel/server/static/app.js").decode()
            and 'Home must use the single global search field' in
            zt.read("HKS_AdminPanel/tests/ui_smoke.js").decode()),
        ("CPU/RAM use distinct equal-size icons and proportional gauges",
            "function metricIcon(kind)" in zt.read("HKS_AdminPanel/server/static/app.js").decode()
            and ".hks-kpi-icon svg { display:block; inline-size:20px; block-size:20px; }" in
            zt.read("HKS_AdminPanel/server/static/style.css").decode()
            and ".usage-dial { width:min(100%,120px); aspect-ratio:5 / 3; height:auto;" in
            zt.read("HKS_AdminPanel/server/static/style.css").decode()
            and "CPU/RAM icons must be distinct, equally sized SVGs" in
            zt.read("HKS_AdminPanel/tests/ui_smoke.js").decode()),
        ("MeshCentral-inspired native device inventory and selector",
            'id: "devices"' in zt.read("HKS_AdminPanel/server/static/app.js").decode()
            and 'id="tMachine"' in zt.read("HKS_AdminPanel/server/static/index.html").decode()
            and "device cards must allow switching the managed machine" in
            zt.read("HKS_AdminPanel/tests/ui_smoke.js").decode()),
        ("old WebSocket close cannot overwrite selected device status",
            "if (STATE.ws !== ws) return;" in
            zt.read("HKS_AdminPanel/server/static/app.js").decode()),
        ("r56: localhost status ports are exclusive",
            "ThreadingHTTPServer.allow_reuse_address = False" in
            zt.read("HKS_Client/agent/local_panel.py").decode()),
        ("r56: desktop task stays alive across idle",
            "$s.IdleSettings.StopOnIdleEnd=$false" in zt.read("first_run.py").decode()),
        ("desktop trace utility ships", "HKS_TRACE_DESKTOP.ps1" in zt.namelist()
            and "HKS_TRACE_DESKTOP.ps1" in zt.read("INSTALL_HKS.bat").decode()),
        ("r54: installer silent-answer path", "answer-file" in
            zt.read("INSTALL_HKS.bat").decode()),
        ("release version is consistent", f'HKS_BUILD_VERSION = "{VER}"' in
            zt.read("HKS_AdminPanel/server/main.py").decode()),
        ("r52: session twin-family skip", "_fam" in
            zt.read("HKS_Client/agent/session_main.py").decode()),
        ("r52: installer writes ping-waits + safe probe", "ping 127.0.0.1 -n 61" in
            zt.read("first_run.py").decode()
            and '2>nul echo. >>' in zt.read("first_run.py").decode().replace('\\r\\n', '')
            and "_normalize_bat_eols" in zt.read("first_run.py").decode()),
        ("r53: version matches VER", f'HKS_BUILD_VERSION = "{VER}"' in zt.read("HKS_AdminPanel/server/main.py").decode()),
        (f"file count {EXPECTED_FILES}", n == EXPECTED_FILES),
    ]
    bad = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print(("  PASS " if ok else "  FAIL ") + name)
    return not bad


def rebuild_bat(size, sha, nparts):
    L = [
        "@echo off",
        "title HKS REBUILD - join the parts into the full zip",
        "setlocal enabledelayedexpansion",
        'cd /d "%~dp0"',
        f"set EXPECT_PARTS={nparts}",
        f"set EXPECT_SIZE={size}",
        f"set EXPECT_SHA={sha}",
        "set N=0",
        "for %%F in (HKS_part_*.txt) do set /a N+=1",
        'if not "%N%"=="%EXPECT_PARTS%" goto wrongparts',
        "if exist HKS_joined.b64 del HKS_joined.b64",
        "copy /b HKS_part_*.txt HKS_joined.b64 >nul",
        'if not exist HKS_joined.b64 goto fail',
        "certutil -decode HKS_joined.b64 HKS_PROGRAM.zip >nul",
        'if not exist HKS_PROGRAM.zip goto fail',
        'for %%A in (HKS_PROGRAM.zip) do set ZSIZE=%%~zA',
        'if not "%ZSIZE%"=="%EXPECT_SIZE%" goto badsize',
        "certutil -hashfile HKS_PROGRAM.zip SHA256 | find /i \"%EXPECT_SHA%\" >nul",
        "if errorlevel 1 goto badsha",
        "del HKS_joined.b64",
        "echo ================================================",
        "echo  REBUILD OK - HKS_PROGRAM.zip is verified.",
        "echo  Size and SHA256 both match. Extract it and run",
        "echo  INSTALL_HKS.bat (as Administrator).",
        "echo ================================================",
        "goto end",
        ":wrongparts",
        "echo ERROR: found %N% parts but expected %EXPECT_PARTS%.",
        "echo Put ALL HKS_part_XX.txt files in this folder, then retry.",
        "goto end",
        ":fail",
        "echo ERROR: joining or decoding failed - did a part get cut?",
        "goto end",
        ":badsize",
        "echo ERROR: zip size %ZSIZE% but expected %EXPECT_SIZE% - a part is bad.",
        "goto end",
        ":badsha",
        "echo ERROR: SHA256 does not match - do NOT install, ask for new parts.",
        "goto end",
        ":end",
        "pause",
    ]
    return "\r\n".join(L) + "\r\n"


def parts():
    data = open(OUT, "rb").read()
    size = len(data)
    sha = hashlib.sha256(data).hexdigest()
    b64 = base64.b64encode(data).decode()
    for f in glob.glob(f"{PARTS_DIR}/HKS_part_*.txt"):
        os.remove(f)
    open(f"{PARTS_DIR}/HKS_zip_base64.txt", "w").write(b64)
    chunks = [b64[i:i + PART_CHARS] for i in range(0, len(b64), PART_CHARS)]
    for i, c in enumerate(chunks, 1):
        open(f"{PARTS_DIR}/HKS_part_{i:02d}.txt", "w").write(c)
    joined = "".join(open(f"{PARTS_DIR}/HKS_part_{i:02d}.txt").read()
                     for i in range(1, len(chunks) + 1))
    rt = hashlib.sha256(base64.b64decode(joined)).hexdigest() == sha
    with open(f"{PARTS_DIR}/REBUILD_HKS.bat", "w", encoding="ascii", newline="") as f:
        f.write(rebuild_bat(size, sha, len(chunks)))
    return size, sha, len(chunks), rt


n = build()
print(f"[1/5] built {OUT}: {n} files")
ok = gates(n)
print("[2/5] gates " + ("PASS" if ok else "FAIL"))
if not ok:
    sys.exit(1)
# [3/5] ZIP SELF-CONTAINMENT (round 62, closes audit4 #14/#15 for good):
# the shipped tests must run from INSIDE the extracted zip - no 04_SOURCE.
_zc = tempfile.mkdtemp(prefix="hks_zipcheck_")
with zipfile.ZipFile(OUT) as _z:
    _z.extractall(_zc)
for _t in ("test_acer_fixes.py", "test_install_commands.py", "test_parity.py", "test_addons.py"):
    _env = dict(os.environ, HKS_BASE=_zc)
    _rc = subprocess.run([sys.executable, f"HKS_AdminPanel/tests/{_t}"],
                         cwd=_zc, env=_env, capture_output=True, text=True, timeout=300)
    if _rc.returncode != 0:
        print(f"[3/5] ZIP TEST {_t} FAILED INSIDE ZIP:\n" + _rc.stdout[-1500:] + _rc.stderr[-1500:])
        sys.exit(1)
_coll = subprocess.run([sys.executable, "-m", "pytest",
                        "HKS_AdminPanel/tests/test_logic.py",
                        "HKS_AdminPanel/tests/test_install_commands.py",
                        "HKS_AdminPanel/tests/test_parity.py",
                        "HKS_AdminPanel/tests/test_acer_fixes.py",
                        "HKS_AdminPanel/tests/test_session_stack.py",
                        "HKS_AdminPanel/tests/test_code_runner_recovery.py",
                        "HKS_AdminPanel/tests/test_file_ops.py",
                        "HKS_AdminPanel/tests/test_fixes_r4.py",
                        "HKS_AdminPanel/tests/test_integration_ports.py",
                        "--collect-only", "-q"], cwd=_zc, capture_output=True, text=True, timeout=300)
if _coll.returncode != 0 or "no tests" in (_coll.stdout or ""):
    print("[3/5] pytest collection inside zip FAILED:\n" + _coll.stdout[-800:] + _coll.stderr[-800:])
    sys.exit(1)
import re as _re
_m = _re.search(r"(\d+) tests? collected", _coll.stdout or "")
_n = int(_m.group(1)) if _m else 0
if _n < 40:  # round 66 (Ammar): THE number - if collection ever drops, the build dies
    print(f"[3/5] pytest collected only {_n} tests inside zip (floor is 40) - FAIL")
    sys.exit(1)
print(f"[3/5] pytest collected {_n} tests inside zip (floor 40)")
shutil.rmtree(_zc, ignore_errors=True)
print("[3/5] zip self-containment: acer+install+parity ran INSIDE zip, pytest collects")
shutil.copyfile(OUT, f"{SRC}/HKS_AdminPanel/server/static/HKS_PROGRAM.zip")
print("[4/5] zip copied into panel static dir")
size, sha, np, rt = parts()
print(f"[5/5] parts: {np} x {PART_CHARS} chars | zip {size} B | sha {sha[:16]}... | roundtrip {rt}")
print(f"DONE {VER}: {OUT} {size} B / sha {sha}")

