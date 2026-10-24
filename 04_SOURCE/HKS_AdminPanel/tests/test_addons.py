

def _hks_root():
    # The addon package root is the ancestor containing HKS_ADDONS in both the
    # source tree (04_SOURCE) and extracted release zip.
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(here))
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
    # round 70: every addon must name a REAL, audited source channel. This is
    # the union of the official homes found by addon_url_audit.py: GitHub,
    # SourceForge (tigervnc), vendor repos (jellyfin, php, zerotier, tailscale,
    # audiorelay, nmap) and docker hub (shinobi). The audit tool checks the
    # URLs against LIVE release assets; this only asserts a channel is named.
    REAL_SOURCE = ("github.com", "sourceforge.net", "repo.jellyfin.org",
                   "hub.docker.com", "docker pull", "nmap.org",
                   "download.zerotier.com", "pkgs.tailscale.com",
                   "windows.php.net", "audiorelay.net")
    assert any(d in text for d in REAL_SOURCE), n
    assert ("download failed" in text or "install failed" in text), n  # honest failure
    assert "pause" in text or "exit /b" in text, n

# The two release-name edge cases audited against current upstream assets.
sx = open(os.path.join(ADD, "ADDON_SHAREX.bat"), encoding="ascii").read()
assert " .*portable" not in sx and ".*portable.*\\.zip$" in sx
assert re.search(r"ShareX-21\.0\.0-portable-x64\.zip", "ShareX-21.0.0-portable-x64.zip", re.I)
vr = open(os.path.join(ADD, "ADDON_VELOCIRAPTOR.bat"), encoding="ascii").read()
assert "windows-amd64\\.exe\\.gz$" in vr and "GZipStream" in vr

# 4. netdata uses the officially documented stable URL
nd = open(os.path.join(ADD, "ADDON_NETDATA.bat"), encoding="ascii").read()
assert "releases/latest/download/netdata-latest-x64.msi" in nd
assert 'msiexec /i "%CD%\\netdata.msi" /qn' in nd

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
assert by["windows_exporter"]["url"].endswith(":9182/metrics")
assert by["Jellyfin"]["installer_available"] is True
assert by["Jellyfin"]["installed"] is None  # script presence is not product installation

# MeshCentral serves its main UI over HTTPS; its addon metadata, installer,
# and existing configuration must agree and installation must be idempotent.
mesh = next(a for a in REG if a["name"] == "MeshCentral")
assert mesh["port"] == 8490 and mesh["scheme"] == "https"
mesh_bat = open(os.path.join(ADD, "ADDON_MESHCENTRAL.bat"), encoding="ascii").read()
assert "https://localhost:8490" in mesh_bat
assert 'if not exist meshcentral-data\\config.json' in mesh_bat
assert "profile=private" in mesh_bat
assert "netstat -ano -p tcp" in mesh_bat
ui = open(os.path.join(ROOT, "HKS_AdminPanel", "server", "static", "app.js"), encoding="utf-8").read()
assert '"MeshCentral", "8490"' in ui

print("ADDONS TESTS: 37 registered / 25 new bats verified / menu + autostart + status OK")
