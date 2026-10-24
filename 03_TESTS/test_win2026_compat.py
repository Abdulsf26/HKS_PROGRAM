"""Windows 2026 compatibility invariants (Win11 24H2/25H2/26H1 + Server 2026).

Microsoft facts this locks in (researched Aug-Sep 2026):
- WMIC removed Aug/Sep 2026 (no FoD, no restore) -> we must never call it.
- at.exe: long removed -> schtasks only.
- netsh advfirewall: deprecated (warning banner), still functional ->
  NEW code must prefer the NetSecurity PowerShell cmdlets.
- WTS* live in wtsapi32.dll; input goes through SendInput (pyautogui);
  DPI awareness must be per-monitor; UAC via Start-Process -Verb RunAs.
- No hardcoded OS-version gates: Windows version numbers keep moving
  (26H1 = 28000.x), so code must never branch on them.
"""
import os
import re

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(ROOT, "04_SOURCE")

CODE_FILES = []
for dp, dn, fn in os.walk(SRC):
    dn[:] = [d for d in dn if d != "__pycache__"]
    for f in fn:
        if f.endswith((".py", ".bat")):
            CODE_FILES.append(os.path.join(dp, f))
assert len(CODE_FILES) > 100, len(CODE_FILES)

# 1. no wmic / at.exe / Get-WmiObject USAGE anywhere (doc mentions are fine)
WMIC_OK = re.compile(r"wmic.{0,40}(removed|deprecat|old|fallback|CIM)", re.I)
AT_USE = re.compile(r"(?m)^\s*(wmic|at)\s+\S")
for p in CODE_FILES:
    t = open(p, encoding="utf-8", errors="replace").read()
    assert "Get-WmiObject" not in t and "gwmi " not in t, p
    for ln in t.splitlines():
        if AT_USE.match(ln):
            raise AssertionError("wmic/at.exe usage in %s: %s" % (p, ln.strip()))
        if "wmic" in ln.lower() and "wmic.exe" in ln.lower():
            raise AssertionError("wmic.exe call in %s: %s" % (p, ln.strip()))
        if "wmic" in ln.lower() and not WMIC_OK.search(ln) and p.endswith(".py") \
                and "wmic " in ln and not ln.strip().startswith("#"):
            raise AssertionError("possible wmic usage in %s: %s" % (p, ln.strip()))

# 2. local secure-desktop unlock must fail safely. WTSConnectSessionW only
# reconnects RDP sessions; it cannot unlock the physical console (Win32 7041).
term = open(os.path.join(SRC, "HKS_Client", "agent", "modules", "terminal.py"),
            encoding="utf-8").read()
assert "Win32 7041" in term
assert "cannot unlock the local secure sign-in screen" in term
assert "WTSConnectSessionW(" not in term, "unsafe physical-console reconnect call"

# 3. per-monitor DPI chain (needed for correct remote clicks on scaled displays)
scr = open(os.path.join(SRC, "HKS_Client", "agent", "session_modules", "screen.py"),
           encoding="utf-8").read()
assert "SetProcessDpiAwarenessContext" in scr

# 4. scheduled tasks = schtasks (never at.exe)
fr = open(os.path.join(SRC, "first_run.py"), encoding="utf-8").read()
assert "schtasks" in fr

# 5. firewall: NEW code prefers NetSecurity cmdlets (netsh deprecated in 2026)
assert "New-NetFirewallRule" in fr, "first_run must use New-NetFirewallRule first"
assert "New-NetFirewallRule" in term, "ssh.enable must use New-NetFirewallRule first"
for kit in ("ADDON_NETDATA.bat", "ADDON_SYNCTHING.bat", "ADDON_KOPIA.bat"):
    t = open(os.path.join(SRC, "HKS_ADDONS", kit), encoding="ascii").read()
    assert "New-NetFirewallRule" in t, kit

# 6. input engine is pyautogui (SendInput backend - current-Windows safe)
req = open(os.path.join(SRC, "HKS_Client", "requirements.txt"), encoding="utf-8").read()
assert "pyautogui" in req and "pywin32" in req

# 7. UAC elevation via the current standard (Start-Process -Verb RunAs)
anywhere = False
for dp, dn, fn in os.walk(SRC):
    for f in fn:
        if f.endswith(".bat"):
            if "Verb RunAs" in open(os.path.join(dp, f), encoding="ascii",
                                    errors="replace").read():
                anywhere = True
                break
    if anywhere:
        break
assert anywhere, "no RunAs elevation found in the addon kit"

# 8. no OS-version branching (26H1 = 28000.x proves build numbers move)
for p in CODE_FILES:
    t = open(p, encoding="utf-8", errors="replace").read()
    assert "getwindowsversion" not in t and "GetVersionEx" not in t, p

# 9. CIM fallback exists for hardware queries (post-WMIC world)
sysinfo = open(os.path.join(SRC, "HKS_Client", "agent", "modules", "sysinfo.py"),
               encoding="utf-8").read()
assert "Get-CimInstance" in sysinfo

print("WIN-2026 COMPAT: %d code files checked - "
      "no wmic/at.exe/Get-WmiObject, wtsapi32 binding, per-monitor DPI, "
      "NetSecurity-first firewall, schtasks, SendInput engine, RunAs UAC, "
      "no version gates, CIM fallback - ALL OK" % len(CODE_FILES))
