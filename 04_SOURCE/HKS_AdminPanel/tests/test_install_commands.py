

"""Guards for the Windows task-registration command builders.

Regression: the old template used double-backtick PowerShell line
continuations (invalid syntax) and the schtasks fallback passed
'/SC ONSTART' as ONE argv element (Windows quoted it -> schtasks error).
Both made every task registration FAIL on real Windows, so the agent
never auto-started. These tests lock in the correct forms.
"""
import os
import sys

# dual-layout (round 62): resolves in the delivered zip (HKS_AdminPanel/tests),
# the repo tree (04_SOURCE/HKS_AdminPanel/tests) AND the 03_TESTS mirrors.
def _hks_panel_client():
    here = os.path.dirname(os.path.abspath(__file__))
    panel = os.path.dirname(here)          # zip/repo sibling layout
    if os.path.isdir(os.path.join(panel, "server")):
        return panel, os.path.join(os.path.dirname(panel), "HKS_Client")
    return (os.path.join(here, "..", "04_SOURCE", "HKS_AdminPanel"),
            os.path.join(here, "..", "04_SOURCE", "HKS_Client"))
PANEL, CLIENT = _hks_panel_client()
_ROOT = os.path.dirname(PANEL)             # 04_SOURCE (repo) or zip root
sys.path.insert(0, PANEL)
sys.path.insert(0, CLIENT)
sys.path.insert(0, _ROOT)  # first_run.py lives here in BOTH layouts

os.environ.setdefault("HKS_BASE", "/tmp/hks_cmdcheck")

import first_run  # noqa: E402


def _install_bat():
    base = os.path.dirname(os.path.abspath(first_run.__file__))
    candidates = (os.path.join(base, "INSTALL_HKS.bat"),
                  os.path.join(base, "00_START_HERE", "INSTALL_HKS.bat"),
                  os.path.join(os.path.dirname(base), "00_START_HERE", "INSTALL_HKS.bat"))
    return next((p for p in candidates if os.path.isfile(p)), candidates[0])


def test_ps_single_line():
    cmd = first_run.build_register_ps(
        "HKS-Agent", r"C:\HKS_PROGRAM\run_agent.bat", r"C:\HKS_PROGRAM", "boot")
    assert "`" not in cmd, "backticks are forbidden (old bug)"
    assert "\n" not in cmd, "must be a single line"
    for token in ("Register-ScheduledTask", "-AtStartup",
                  "ExecutionTimeLimit ([TimeSpan]::Zero)",
                  "-AllowStartIfOnBatteries", "-DontStopIfGoingOnBatteries",
                  "$s.IdleSettings.StopOnIdleEnd=$false",
                  "-RestartCount 999", "New-ScheduledTaskPrincipal"):
        assert token in cmd, token
    logon = first_run.build_register_ps(
        "HKS-SessionAgent", "pythonw.exe", r"C:\HKS_PROGRAM\HKS_Client",
        "logon", user="bob", logon="Interactive", level="Limited")
    assert "-AtLogOn" in logon and "bob" in logon and "Interactive" in logon
    assert "$s.IdleSettings.StopOnIdleEnd=$false" in logon
    print("  PS command: single line, no backticks, all settings present")


def test_schtasks_split():
    argv = first_run.build_schtasks_argv("HKS-Agent", r"C:\HKS\run_agent.bat", "boot")
    assert "/SC" in argv and "ONSTART" in argv, argv
    assert "/SC ONSTART" not in argv, "flags must not be merged (old bug)"
    assert argv.index("/SC") < argv.index("ONSTART")
    assert "/RU" in argv and "SYSTEM" in argv
    assert "/RL" in argv and "HIGHEST" in argv
    # no element except the /TR path may contain spaces
    for i, a in enumerate(argv):
        if argv[i - 1] if i else "" == "/TR":
            continue
        assert " " not in a or (i > 0 and argv[i - 1] == "/TR"), a
    logon = first_run.build_schtasks_argv("HKS-SessionAgent",
                                          r"C:\HKS\run_session_agent.bat", "logon")
    assert "/RU" not in logon, "logon task must omit /RU (no password prompt)"
    assert "ONLOGON" in logon
    print("  schtasks argv: correctly split, SYSTEM/logon handling right")


def test_session_task_has_script_arg():
    """THE 'desktop offline' regression: the session task ran pythonw.exe
    with NO -Argument, so it executed nothing at logon."""
    ps_cmd = first_run.build_register_ps(
        "HKS-SessionAgent", r"C:\HKS_PROGRAM\venv\Scripts\pythonw.exe",
        r"C:\HKS_PROGRAM\HKS_Client", "logon", user="acer",
        logon="Interactive", level="Limited", args="-m agent.session_main")
    assert "-Argument '-m agent.session_main'" in ps_cmd, ps_cmd
    assert "pythonw.exe" in ps_cmd and "-AtLogOn" in ps_cmd
    # schtasks fallback carries the args inside /TR too
    argv = first_run.build_schtasks_argv(
        "HKS-SessionAgent", r"C:\HKS_PROGRAM\venv\Scripts\pythonw.exe",
        "logon", args="-m agent.session_main")
    tr = argv[argv.index("/TR") + 1]
    assert "-m agent.session_main" in tr and "pythonw.exe" in tr, tr
    # boot tasks (plain .bat) legitimately need no args
    ps_bat = first_run.build_register_ps("HKS-Agent", r"C:\HKS\run_agent.bat", r"C:\HKS")
    assert "-Argument" not in ps_bat
    # WorkingDirectory MUST be part of the task action (a lost wd kills -m)
    assert "-WorkingDirectory" in ps_cmd
    print("  session task: pythonw + '-m agent.session_main' + WorkingDirectory in BOTH forms")


def test_register_task_graceful_off_windows():
    ok, method, err = first_run.register_task("X", "/tmp/nope.bat", "/tmp")
    assert ok is False and method == "none" and err, (ok, method, err)
    print("  register_task: fails gracefully off-Windows with error text")


def test_vbs_hidden_start():
    os.environ["APPDATA"] = "/tmp/hks_cmdcheck/appdata"
    os.makedirs("/tmp/hks_cmdcheck/appdata", exist_ok=True)
    p = "/tmp/hks_cmdcheck/fake.bat"
    open(p, "w").write("@echo off\r\n")
    assert first_run.startup_folder_hidden(p, "agent")
    out = open("/tmp/hks_cmdcheck/appdata/Microsoft/Windows/Start Menu/"
               "Programs/Startup/HKS_agent_start.vbs").read()
    assert p in out and "Wscript.Shell" in out and ", 0, False" in out
    print("  startup-folder vbs: hidden-window launcher correct")


def test_phone_reachability():
    src = open(first_run.__file__).read()
    # CHECK_STATUS.bat must print LIVE phone URLs (IP changes with network)
    assert "OPEN: __SCHEME__://" in src, "CHECK_STATUS missing live phone URL line"
    assert '.replace("__SCHEME__", scheme)' in src, "__SCHEME__ never substituted"
    assert "Get-NetIPAddress" in src, "CHECK_STATUS must query the CURRENT IPs"
    # firewall rule must be verified after add, and self-heal on failure
    assert '"profile=any", "enable=yes"' in src, "firewall rule not explicit"
    assert "_fw_ok" in src and "COULD NOT OPEN PORT" in src, \
        "firewall rule not verified / no self-heal"
    # step [9/9] shows the phone address at install time
    assert "phone   : on THIS Wi-Fi open" in src
    # HOW_TO_CONNECT must explain that the address FOLLOWS the network
    assert "NETWORK CHANGED?" in src and "CURRENT address" in src
    print("  phone reachability: live URLs in CHECK_STATUS, verified firewall,")
    print("  step-9 phone URL, network-change guidance")


def test_doctor():
    import tempfile
    tmp = os.path.join(tempfile.mkdtemp(), first_run.DOCTOR_NAME)
    assert first_run.write_doctor(tmp)
    raw = open(tmp, "rb").read()
    assert raw.count(b"\r\n") > 40, "doctor must be CRLF"
    txt = raw.decode("ascii")
    for must in ("ConvertFrom-Json",          # port auto-detect from config
                 "/api/version",              # RUNNING build probe
                 "TUNNEL LIVE TEST",          # live test of the phone door
                 "tunnel_url.txt",
                 "clip <",                    # report auto-copy
                 "HKS-SessionAgent",
                 "Get-NetIPAddress"):
        assert must in txt, f"doctor missing: {must}"
    # [A] PHONE ADDRESSES summary first; PS uses + concatenation inside parens
    assert "PHONE ADDRESSES - TRY IN THIS ORDER" in txt
    assert "OPEN THIS ON YOUR PHONE" in txt
    assert "type EXACTLY this on the phone" in txt
    assert "Write-Output ('  [OK] panel RUNNING: ' + $s" in txt
    import re as _re
    assert not _re.findall(r"Write-Output \('[^']*' \$", txt), \
        "adjacent-args inside parens is INVALID PowerShell - must use + concatenation"
    # [A2] derives ports from the LIVE /api/version (main + companion),
    # not port+1 blindly; probes report real errors + elevation status;
    # VirtualBox/loopback/link-local are excluded; the process probe
    # cannot match its own powershell and tolerates hidden cmdlines
    assert "Invoke-WebRequest" in txt and "$j2.main" in txt
    assert "$j2.http_companion" in txt
    assert "IsInRole" in txt, "elevation status missing"
    assert "panel not answering locally - errors" in txt
    assert "[no answer] " in txt
    assert "192.168.56." in txt
    assert "ProcessId -ne $PID" in txt
    assert "command line hidden - run doctor as Administrator" in txt
    assert "for from-anywhere access double-click HKS_FIX_TUNNEL.bat" in txt  # round 56/61 wording
    # firewall checks use errorlevel (no pipe/&& precedence trap)
    assert "if %errorlevel%==0 (echo   [OK] rule HKS Panel enabled" in txt
    print("  HKS_DOCTOR.bat: generated, CRLF, all 8 probes + clipboard copy")


def test_lan_only():
    """User directive 2026-09-21: NO tunnel in the default install -
    LOCAL NETWORK only - and the LAN door must survive Windows firewall
    quirks (WFP BlockInboundAlways ignores allow rules)."""
    src = open(first_run.__file__).read()
    # the installer flow must NOT set up any tunnel (the fixer keeps it
    # as an optional standalone tool only)
    # exactly one definition + one call site (inside fix_tunnel_mode,
    # the optional standalone tool). The install flow must not call it.
    assert src.count("def setup_anywhere_tunnel(") == 1
    assert src.count("setup_anywhere_tunnel(BASE") == 1, \
        "tunnel must only be startable via the standalone fixer"
    assert 'print("[8/9] Phone-from-ANYWHERE' not in src
    # WFP hardening present: policy reset + program rule + bind preflight
    assert "BlockInboundAlways" in src, "firewall-policy reset missing"
    assert '"name=HKS Panel (program)"' in src, "program rule missing"
    assert '_t.bind(("0.0.0.0", port))' in src, "LAN bind preflight missing"
    assert "LOCAL NETWORK mode" in src
    # firewall reset commands are the correct netsh argv
    for frag in ('"set", prof, "firewallpolicy"', '"blockinbound,allowoutbound"'):
        assert frag in src, frag
    print("  LAN-only install: no tunnel in flow, WFP policy reset +")
    print("  program rule + bind preflight verified in source")


def test_fix_tunnel():
    src = open(first_run.__file__).read()
    assert '"--fix-tunnel"' in src, "argparse flag missing"
    assert "fix_tunnel_mode" in src
    assert "setup_anywhere_tunnel(BASE, DATA, local_port, ask=False)" in src
    assert "shutil" in src.split("def ")[0], "shutil import missing"
    # v2: waits for the URL itself, then either prints it or explains +
    # exits 2 so the .bat chains into the doctor; download has a timeout
    assert "waiting for the tunnel address (up to 90 seconds)" in src
    assert "PHONE's hotspot" in src
    assert "return 2" in src
    assert "socket.setdefaulttimeout(60)" in src
    bat = first_run.fix_tunnel_bat()
    assert "net session >nul 2>&1" in bat, "fixer must self-elevate"
    assert "Start-Process -FilePath '%~f0' -Verb RunAs" in bat
    assert 'call "%~dp0HKS_DOCTOR.bat"' in bat, "doctor on failure"
    import tempfile
    tmpd = tempfile.mkdtemp()
    batp = os.path.join(tmpd, "HKS_FIX_TUNNEL.bat")
    assert first_run.write_fix_tunnel(batp)
    raw = open(batp, "rb").read()
    BS = bytes([92])
    assert (b'set BASE=C:' + BS + b'HKS_PROGRAM') in raw
    assert (b'venv' + BS + b'Scripts' + BS + b'python.exe') in raw
    assert b'"%~dp0first_run.py" --fix-tunnel' in raw
    assert raw.endswith(b"\r\n")
    # install wiring: fixer bat + first_run.py land in BASE for later repair
    assert 'write_fix_tunnel(os.path.join(BASE, "HKS_FIX_TUNNEL.bat"))' in src
    assert 'shutil.copyfile(os.path.abspath(__file__),' in src
    print("  --fix-tunnel mode + HKS_FIX_TUNNEL.bat: repair path verified")


def test_anywhere_tunnel():
    src = open(first_run.__file__).read()
    assert "CLOUDFLARED_URL" in src and "cloudflared-windows-amd64.exe" in src
    assert "setup_anywhere_tunnel" in src and "tunnel_url_watch_py" in src
    assert 'startup_folder_hidden(tb, "tunnel")' in src, "tunnel must auto-start at logon"
    bat = first_run.tunnel_bat("C:" + chr(92) + "x" + chr(92) + "cloudflared.exe", 6768,
                               "C:" + chr(92) + "data")
    assert "tunnel --url http://127.0.0.1:6768 --no-autoupdate" in bat
    assert bat.count(chr(13) + chr(10)) >= 6, "CRLF required"
    watch = first_run.tunnel_url_watch_py()
    compile(watch, "watch", "exec")  # the generated watcher must be valid python
    assert "trycloudflare.com" in watch and "tunnel_url.txt" in watch
    assert "HKS Phone Access.url" in watch
    # URL extraction logic against a realistic log slice
    import re as _re
    sample = ("2026-09-17T12:00:00Z INF +-------------------------------------------\n"
              "https://signals-comparison-scanned-whether.trycloudflare.com\n"
              "2026-09-17T12:00:01Z INF |  Your quick Tunnel has been created! \n")
    urls = []
    for line in sample.splitlines():
        i = line.find("https://")
        if i >= 0 and "trycloudflare.com" in line:
            urls.append(line[i:].split()[0].strip())
    assert urls and urls[-1].endswith("trycloudflare.com"), urls
    panel = open(os.path.join(os.path.dirname(first_run.__file__),
                              "HKS_AdminPanel/server/main.py")).read()
    assert "tunnel_url" in panel
    import re as _rev
    assert _rev.search(r'HKS_BUILD_VERSION = "\d+\.\d+\.\d+"', panel), "build stamp missing"
    sm = open(os.path.join(os.path.dirname(first_run.__file__),
                           "HKS_Client/agent/session_main.py")).read()
    assert "WORKS FROM ANYWHERE" in sm
    print("  anywhere-tunnel: downloader + tunnel.bat + watcher + toast +")
    print("  CHECK_STATUS + /api/version passthrough all verified")


def test_elevation_and_version():
    bat = open(_install_bat(), encoding="utf-8").read()
    assert "net session" in bat and "-Verb RunAs" in bat, "installer must self-elevate"
    src = open(first_run.__file__).read()
    assert "__HPORT__" in src and "/api/version" in src, \
        "CHECK_STATUS must show the running build + both ports"
    print("  installer self-elevates (UAC) + CHECK_STATUS shows running build")


def test_clean_upgrade_kill():
    assert hasattr(first_run, "stop_running_hks"), "stop_running_hks missing"
    src = open(first_run.__file__).read()
    assert "Get-CimInstance Win32_Process" in src, "kill must enumerate processes"
    assert "HKS_INSTALLER_PID" in src, "installer must exclude its own PID"
    assert "stop_running_hks()" in src, "step 7 must call the kill"
    # patterns are targeted: python -m server/agent + HKS_PROGRAM bats only
    assert "-m (server|agent)" in src and "run_session_agent" in src
    ok = first_run.stop_running_hks()  # off-Windows: graceful True
    assert ok is True
    print("  clean upgrade: old HKS processes killed (installer PID excluded),")
    print("  graceful off-Windows")


def test_gui_setup_entrypoint_and_password_validation():
    src = open(first_run.__file__, encoding="utf-8").read()
    bat = open(_install_bat(), encoding="utf-8").read()
    assert "def run_gui_setup():" in src and 'ap.add_argument("--gui"' in src
    assert 'first_run.py" --gui' in bat
    assert "Passwords do not match" in src and "Port in use" in src
    assert "--answer-file" in src and "shutil.rmtree(running[\"temp\"]" in src
    import socket as _socket
    held = _socket.socket()
    held.bind(("0.0.0.0", 0))
    held.listen(1)
    port = held.getsockname()[1]
    try:
        assert first_run.setup_port_conflict(port, False) == port
    finally:
        held.close()


if __name__ == "__main__":
    test_doctor()
    test_lan_only()
    test_fix_tunnel()
    test_anywhere_tunnel()
    test_elevation_and_version()
    test_clean_upgrade_kill()
    test_ps_single_line()
    test_schtasks_split()
    test_register_task_graceful_off_windows()
    test_vbs_hidden_start()
    test_session_task_has_script_arg()
    test_phone_reachability()
    print("ALL INSTALL-COMMAND TESTS PASSED")
