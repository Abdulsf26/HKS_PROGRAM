"""Add-on services kit status (HKS_ADDONS *.bat installers on the laptop).

The panel's Settings -> Open source tab shows this board: which optional
services (MeshCentral, ttyd, filegator, code-server, DbGate, Shinobi,
Nextcloud AIO, CasaOS, Mopidy+Iris, AnywhereInput) are installed and
running, plus a clickable LAN URL for each. Port probes are tiny local TCP
connects with no subprocess so this stays cheap on the 8 GB laptop. Add-on
installers run from the signed-in desktop agent, where their download output
and any UAC prompt are visible instead of disappearing in service session 0.
"""
import socket
import time

# Only HKS-owned container names may be stopped or assigned a Docker restart
# policy. Never kill an arbitrary process simply because it owns a port.
_DOCKER_CONTAINERS = {
    "code-server": "hks-code-server",
    "DbGate": "hks-dbgate",
    "Shinobi": "hks-shinobi",
    "Nextcloud AIO": "nextcloud-aio-mastercontainer",
}


def _docker_cli():
    import os, shutil
    found = shutil.which("docker")
    if found:
        return found
    fallback = r"C:\Program Files\Docker\Docker\resources\bin\docker.exe"
    return fallback if os.path.isfile(fallback) else None


def _container_info(name):
    import subprocess
    docker = _docker_cli()
    if not docker:
        return None
    try:
        result = subprocess.run(
            [docker, "inspect", "--format", "{{.State.Running}}|{{.HostConfig.RestartPolicy.Name}}", name],
            capture_output=True, text=True, timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode:
            return None
        running, restart = (result.stdout.strip().split("|", 1) + [""])[:2]
        return {"running": running.casefold() == "true", "restart": restart}
    except (OSError, subprocess.SubprocessError):
        return None

# name, LAN port, what it is, installer bat, how it is installed
ADDONS = [
    {"name": "MeshCentral", "port": 8490, "scheme": "https",
     "what": "remote-management server (HTTPS; first run may show its self-signed certificate warning)",
     "installer": "HKS_ADDONS\\ADDON_MESHCENTRAL.bat", "how": "npm (Node.js)"},
    {"name": "ttyd", "port": 7681,
     "what": "terminal in the browser (shared-machine shell)",
     "installer": "HKS_ADDONS\\ADDON_TTYD.bat", "how": "GitHub exe"},
    {"name": "filegator", "port": 8090,
     "what": "PHP web file manager (separate from the HKS panel)",
     "installer": "HKS_ADDONS\\ADDON_FILEGATOR.bat", "how": "PHP built-in server"},
    {"name": "code-server", "port": 8482,
     "what": "VS Code in the browser",
     "installer": "HKS_ADDONS\\ADDON_CODESERVER.bat", "how": "Docker"},
    {"name": "DbGate", "port": 8485,
     "what": "database GUI in the browser",
     "installer": "HKS_ADDONS\\ADDON_DBGATE.bat", "how": "Docker"},
    {"name": "Shinobi", "port": 8480,
     "what": "CCTV NVR (installer moves its web port off 8080)",
     "installer": "HKS_ADDONS\\ADDON_SHINOBI.bat", "how": "Docker compose"},
    {"name": "Nextcloud AIO", "port": 8443,
     "what": "self-hosted cloud suite (mastercontainer setup page)",
     "installer": "HKS_ADDONS\\ADDON_NEXTCLOUD.bat", "how": "Docker"},
    {"name": "CasaOS", "port": 8080,
     "what": "home-server dashboard inside WSL2 Ubuntu (port shares Shinobi)",
     "installer": "HKS_ADDONS\\ADDON_CASAOS.bat", "how": "WSL2 + script"},
    {"name": "Mopidy + Iris", "port": 6680,
     "what": "music server + web UI (sound needs GStreamer - experimental on Windows)",
     "installer": "HKS_ADDONS\\ADDON_MOPIDY.bat", "how": "pip"},
    {"name": "AnywhereInput", "port": 8008,
     "what": "the browser PC-control project (HKS already covers this natively)",
     "installer": "HKS_ADDONS\\ADDON_ANYWHEREINPUT.bat", "how": "pip"},
    {"name": "filebrowser", "port": 8487,
     "what": "single-exe web file manager over C:\\",
     "installer": "HKS_ADDONS\\ADDON_FILEBROWSER.bat", "how": "GitHub exe"},
    {"name": "AudioRelay", "port": 0,
     "what": "audio streaming to the phone (freeware; HKS also streams mic natively)",
     "installer": "HKS_ADDONS\\ADDON_AUDIORELAY.bat", "how": "freeware installer (GUI)"},
    # ---- Round 30: +25 GitHub add-ons (remote-admin / monitoring / media) ----
    {"name": "RustDesk", "port": 0,
     "what": "open-source remote desktop (own TeamViewer-like)",
     "installer": "HKS_ADDONS\\ADDON_RUSTDESK.bat", "how": "GitHub exe (GUI)"},
    {"name": "TigerVNC", "port": 0,
     "what": "classic VNC server + viewer (GPL)",
     "installer": "HKS_ADDONS\\ADDON_TIGERVNC.bat", "how": "GitHub installer (GUI)"},
    {"name": "Deskflow", "port": 24800,
     "what": "share one keyboard+mouse across PCs over LAN",
     "installer": "HKS_ADDONS\\ADDON_DESKFLOW.bat", "how": "GitHub installer (GUI)"},
    {"name": "Tailscale", "port": 0,
     "what": "WireGuard mesh VPN - reach the laptop from anywhere",
     "installer": "HKS_ADDONS\\ADDON_TAILSCALE.bat", "how": "GitHub zip (service)"},
    {"name": "ZeroTier", "port": 9993,
     "what": "mesh VPN - virtual LAN between your devices",
     "installer": "HKS_ADDONS\\ADDON_ZEROTIER.bat", "how": "GitHub MSI (service)"},
    {"name": "Netbird", "port": 0,
     "what": "open-source WireGuard mesh VPN with SSO",
     "installer": "HKS_ADDONS\\ADDON_NETBIRD.bat", "how": "GitHub MSI (service)"},
    {"name": "frp", "port": 7500,
     "what": "self-hosted reverse proxy - expose HKS via YOUR VPS",
     "installer": "HKS_ADDONS\\ADDON_FRP.bat", "how": "GitHub zip (frpc client)"},
    {"name": "Syncthing", "port": 8384,
     "what": "continuous file sync between your devices",
     "installer": "HKS_ADDONS\\ADDON_SYNCTHING.bat", "how": "GitHub zip (service-able)"},
    {"name": "rclone", "port": 5572,
     "what": "copy/sync to 70+ cloud storages (web UI)",
     "installer": "HKS_ADDONS\\ADDON_RCLONE.bat", "how": "GitHub zip (rcd web)"},
    {"name": "Kopia", "port": 51515, "scheme": "https",
     "what": "encrypted backup repository and HTTPS web UI",
     "installer": "HKS_ADDONS\\ADDON_KOPIA.bat", "how": "GitHub zip (server ui)"},
    {"name": "windows_exporter", "port": 9182,
     "what": "Prometheus metrics endpoint for Windows",
     "open_path": "/metrics",
     "installer": "HKS_ADDONS\\ADDON_WINDOWSEXPORTER.bat", "how": "GitHub exe (service-able)"},
    {"name": "Glances", "port": 61208,
     "what": "all-in-one system monitor in the browser",
     "installer": "HKS_ADDONS\\ADDON_GLANCES.bat", "how": "pip (py -m glances -w)"},
    {"name": "Netdata", "port": 19999,
     "what": "real-time per-second monitoring dashboard (native Windows)",
     "installer": "HKS_ADDONS\\ADDON_NETDATA.bat", "how": "GitHub MSI (service)"},
    {"name": "Uptime Kuma", "port": 3001,
     "what": "uptime/status monitoring with alerting (needs Node 18+)",
     "installer": "HKS_ADDONS\\ADDON_UPTIMEKUMA.bat", "how": "git + npm (guarded)"},
    {"name": "osquery", "port": 0,
     "what": "query the OS like a SQL database",
     "installer": "HKS_ADDONS\\ADDON_OSQUERY.bat", "how": "GitHub zip (CLI)"},
    {"name": "Velociraptor", "port": 8889,
     "what": "endpoint visibility / DFIR console (https)",
     "installer": "HKS_ADDONS\\ADDON_VELOCIRAPTOR.bat", "how": "GitHub exe (gui)"},
    {"name": "Nmap", "port": 0,
     "what": "network + port scanner (Npcap for full scans)",
     "installer": "HKS_ADDONS\\ADDON_NMAP.bat", "how": "GitHub installer (GUI)"},
    {"name": "LibreHardwareMonitor", "port": 8088,
     "what": "CPU/GPU/disk temperatures + fan speeds (web view optional)",
     "installer": "HKS_ADDONS\\ADDON_LIBREHWMONITOR.bat", "how": "GitHub zip (GUI)"},
    {"name": "FanControl", "port": 0,
     "what": "custom fan curves - helps the 24/7 anti-heat goal",
     "installer": "HKS_ADDONS\\ADDON_FANCONTROL.bat", "how": "GitHub zip (GUI, .NET 8)"},
    {"name": "ShareX", "port": 0,
     "what": "screenshots / screen recording with upload",
     "installer": "HKS_ADDONS\\ADDON_SHAREX.bat", "how": "GitHub zip (GUI)"},
    {"name": "yt-dlp", "port": 0,
     "what": "download videos/audio from the web (CLI)",
     "installer": "HKS_ADDONS\\ADDON_YTDLP.bat", "how": "GitHub exe (CLI)"},
    {"name": "Jellyfin", "port": 8096,
     "what": "free media server - movies/music to any device",
     "installer": "HKS_ADDONS\\ADDON_JELLYFIN.bat", "how": "GitHub installer (service)"},
    {"name": "WinSW", "port": 0,
     "what": "wrap ANY exe as a Windows service (helper)",
     "installer": "HKS_ADDONS\\ADDON_WINSW.bat", "how": "GitHub exe (CLI)"},
    {"name": "AutoHotkey", "port": 0,
     "what": "Windows automation scripting (v2)",
     "installer": "HKS_ADDONS\\ADDON_AUTOHOTKEY.bat", "how": "GitHub zip (GUI)"},
    {"name": "OBS Studio", "port": 0,
     "what": "record/stream the screen (complements HKS mirror)",
     "installer": "HKS_ADDONS\\ADDON_OBS.bat", "how": "GitHub installer (GUI)"},
]

_PROBE_TIMEOUT = 0.35


def _base():
    """Install root. Env wins so tests can redirect (C:/HKS_PROGRAM on Windows)."""
    import os
    return os.environ.get("HKS_BASE") or ("C:\\HKS_PROGRAM" if os.name == "nt" else "/opt/HKS_PROGRAM")


def _installer_path(rel):
    """Absolute path of an addon bat — old bug: relative paths broke when
    the service CWD was not the install root."""
    import os
    p = os.path.join(_base(), rel.replace("\\", os.sep))
    return p if os.path.exists(p) else None


def _lan_ip():
    try:
        from .sysinfo import _first_ip
        ip = _first_ip()
        return ip or "127.0.0.1"
    except Exception:
        return "127.0.0.1"


def _port_open(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=_PROBE_TIMEOUT):
            return True
    except OSError:
        return False


def register(ctx):
    def status(c, p):
        import concurrent.futures as _cf
        ip = _lan_ip()
        withports = [a for a in ADDONS if a.get("port")]
        # probe all ports in PARALLEL - 37 sequential x 0.35 s froze the
        # board for ~10 s (audit3)
        with _cf.ThreadPoolExecutor(max_workers=12) as ex:
            futs = {a["name"]: ex.submit(_port_open, a["port"]) for a in withports}
            ups = {n: f.result() for n, f in futs.items()}
        out = []
        for a in ADDONS:
            # port 0 = tool without a web UI (GUI/CLI) - no probe, no fake URL
            up = ups.get(a["name"]) if a.get("port") else None
            container = _DOCKER_CONTAINERS.get(a["name"])
            container_info = _container_info(container) if container else None
            open_path = a.get("open_path", "/")
            scheme = a.get("scheme", "http")
            out.append({"name": a["name"], "port": a["port"], "up": up,
                        "url": f"{scheme}://{ip}:{a['port']}{open_path}" if a.get("port") else "",
                        "open_path": open_path,
                        # Offer controls only when Docker confirms the named
                        # HKS container exists; Stop is valid only while it is
                        # running. A stopped/missing container must not show a
                        # dead Stop button or imply support on unrelated tools.
                        "can_stop": bool(container and container_info and container_info["running"]),
                        "can_autostart": bool(container and container_info is not None),
                        "container_running": container_info["running"] if container_info else False,
                        "autostart": (container_info["restart"] not in ("", "no")) if container_info else False,
                        "what": a["what"], "installer": a["installer"],
                        "how": a["how"],
                        # Presence of our .bat proves only that HKS can start
                        # an installer; it does not prove the product is installed.
                        "installer_available": _installer_path(a["installer"]) is not None,
                        "installed": True if up else None})
        return {"addons": out, "checked": round(time.time(), 1)}

    def stop(c, p):
        a = next((x for x in ADDONS if x["name"].lower() == str(p.get("name", "")).lower()), None)
        if not a:
            return {"ok": False, "error": "unknown addon"}
        container = _DOCKER_CONTAINERS.get(a["name"])
        docker = _docker_cli()
        if not container or not docker:
            return {"ok": False, "error": f"Safe Stop is not configured for {a['name']}; HKS will not terminate an arbitrary port owner."}
        import subprocess
        try:
            result = subprocess.run([docker, "stop", "--time", "10", container], capture_output=True,
                                    text=True, timeout=20,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode:
                return {"ok": False, "error": (result.stderr or result.stdout or "Docker could not stop the HKS-managed container.").strip()[-240:]}
            return {"ok": True, "stopped": container, "note": f"Stopped HKS-managed container {container}. Its data volume was kept."}
        except (OSError, subprocess.SubprocessError) as exc:
            return {"ok": False, "error": f"Could not stop the HKS-managed container: {exc}"}

    def autostart(c, p):
        a = next((x for x in ADDONS if x["name"].lower() == str(p.get("name", "")).lower()), None)
        if not a:
            return {"ok": False, "error": "unknown addon"}
        container = _DOCKER_CONTAINERS.get(a["name"])
        docker = _docker_cli()
        if not container or not docker:
            return {"ok": False, "error": f"Autoboot is not configured for {a['name']}; installers are never scheduled to rerun as SYSTEM."}
        on = bool(p.get("on", True))
        import subprocess
        policy = "unless-stopped" if on else "no"
        try:
            result = subprocess.run([docker, "update", "--restart", policy, container],
                                    capture_output=True, text=True, timeout=15,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode:
                return {"ok": False, "error": (result.stderr or result.stdout or "Docker could not update restart policy.").strip()[-240:]}
            return {"ok": True, "note": "Docker restarts this HKS-managed container at Docker Desktop startup." if on else "Docker automatic restart disabled."}
        except (OSError, subprocess.SubprocessError) as exc:
            return {"ok": False, "error": f"Could not update Docker restart policy: {exc}"}

    return {"addons.status": status,
            "addons.stop": stop, "addons.autostart": autostart}
