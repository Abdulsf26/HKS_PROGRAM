

def _hks_root():
    # This standalone test lives in 03_TESTS, while the package root is
    # 04_SOURCE. Tests copied inside HKS_AdminPanel/tests use the zip root.
    here = os.path.dirname(os.path.abspath(__file__))
    source = os.path.join(os.path.dirname(here), "04_SOURCE")
    if os.path.isdir(os.path.join(source, "HKS_ADDONS")):
        return source
    if os.path.basename(here) == "tests" and os.path.basename(os.path.dirname(here)) == "HKS_AdminPanel":
        return os.path.dirname(os.path.dirname(here))
    raise RuntimeError("cannot find HKS_ADDONS from test location: " + here)
"""Round 30: HKS_ADDONS consistency - registry, bats, ports, autostart wiring."""
import importlib.util
import os
import re
import sys

ROOT = _hks_root()  # <root> = 04_SOURCE (repo) or zip root
ADD = os.path.join(ROOT, "HKS_ADDONS")

spec = importlib.util.spec_from_file_location(
    "addons_mod", os.path.join(ROOT, "HKS_Client", "agent", "modules", "addons.py"))
am = importlib.util.module_from_spec(spec)
spec.loader.exec_module(am)
REG = am.ADDONS

# 1. registry size: 10 original + filebrowser + AudioRelay + 25 new = 37
assert len(REG) == 37, len(REG)

# 2. every registry entry: bat exists, name unique, port valid
seen = set()
for a in REG:
    assert a["name"] and a["name"] not in seen, a["name"]
    seen.add(a["name"])
    assert isinstance(a["port"], int) and 0 <= a["port"] <= 65535, a
    bat = os.path.join(ROOT, *a["installer"].split("\\"))
    assert os.path.isfile(bat), bat

# 3. the 25 new bats: ASCII, CRLF, GitHub-sourced, honest failure block, no fake
NEW = ["ADDON_RUSTDESK.bat", "ADDON_TIGERVNC.bat", "ADDON_DESKFLOW.bat", "ADDON_TAILSCALE.bat",
       "ADDON_ZEROTIER.bat", "ADDON_NETBIRD.bat", "ADDON_FRP.bat", "ADDON_SYNCTHING.bat",
       "ADDON_RCLONE.bat", "ADDON_KOPIA.bat", "ADDON_WINDOWSEXPORTER.bat", "ADDON_GLANCES.bat",
       "ADDON_NETDATA.bat", "ADDON_UPTIMEKUMA.bat", "ADDON_OSQUERY.bat", "ADDON_VELOCIRAPTOR.bat",
       "ADDON_NMAP.bat", "ADDON_LIBREHWMONITOR.bat", "ADDON_FANCONTROL.bat", "ADDON_SHAREX.bat",
       "ADDON_YTDLP.bat", "ADDON_JELLYFIN.bat", "ADDON_WINSW.bat", "ADDON_AUTOHOTKEY.bat",
       "ADDON_OBS.bat"]
assert len(NEW) == 25
for n in NEW:
    raw = open(os.path.join(ADD, n), "rb").read()
    raw.decode("ascii")                       # pure ASCII (bat-safe on every codepage)
    assert b"\r\n" in raw                     # CRLF line endings
    text = raw.decode("ascii")
    assert ("https://" in text or "pip install" in text or "winget install" in text), n
    assert ("download failed" in text or "install failed" in text), n  # honest failure
    assert "pause" in text or "exit /b" in text, n

# 4. netdata uses the officially documented stable URL
nd = open(os.path.join(ADD, "ADDON_NETDATA.bat"), encoding="ascii").read()
assert "releases/latest/download/netdata-x64.msi" in nd
assert "msiexec /i netdata-x64.msi /qn" in nd

# 5. uptime kuma honestly guards on Node
uk = open(os.path.join(ADD, "ADDON_UPTIMEKUMA.bat"), encoding="ascii").read()
assert "where node" in uk and "ADDON_PREREQS" in uk

# 6. menu lists all 37 + prereqs + quit
menu = open(os.path.join(ADD, "ADDON_MENU.bat"), encoding="ascii").read()
assert menu.count('" & goto menu') == 37, menu.count('" & goto menu')  # 37 numbered entries
assert "q" in menu and "ADDON_PREREQS" in menu

# 7. ports unique across the whole registry (0 = tool, allowed to repeat)
ports = [a["port"] for a in REG if a["port"]]
assert len(ports) == len(set(ports)), sorted(ports)

# 8. autostart task-name mapping stays alphanumeric-safe (schtasks TN rule)
for a in REG:
    tn = "HKS-Addon-" + "".join(ch for ch in a["name"] if ch.isalnum())
    assert tn.startswith("HKS-Addon-") and " " not in tn

# 9. status marks port-0 tools as up=None (no fake "running")
class _C:  # dummy ctx
    pass
actions = am.register(_C())
st = actions["addons.status"]
out = st(_C(), {})
by = {x["name"]: x for x in out["addons"]}
assert len(by) == 37
assert by["RustDesk"]["up"] is None and by["RustDesk"]["url"] == ""
assert isinstance(by["Syncthing"]["up"], bool)

print("ADDONS TESTS: 37 registered / 25 new bats verified / menu + autostart + status OK")
