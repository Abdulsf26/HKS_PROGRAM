"""Launch the user's selected HKS add-on installer in their desktop session.

Install scripts can download software and some need UAC. They must run in the
interactive logon session so the owner sees the console output and any prompt;
launching them from the SYSTEM service hides the window in session 0.
"""
import os
import shutil
import subprocess
import threading

_launch_lock = threading.Lock()
_launches = {}


def _is_windows():
    return os.name == "nt"


def register(ctx):
    def run(c, p):
        from agent.modules.addons import ADDONS, _DOCKER_CONTAINERS, _container_info, _installer_path, _port_open

        name = str((p or {}).get("name", "")).strip()
        addon = next((a for a in ADDONS if a["name"].casefold() == name.casefold()), None)
        if addon is None:
            return {"ok": False, "error": "Unknown add-on. Choose one from the HKS add-on list."}
        bat = _installer_path(addon["installer"])
        if not bat:
            return {"ok": False, "error": f"{addon['name']} installer is missing. Restore HKS_ADDONS/{os.path.basename(addon['installer'])} and refresh."}
        if not _is_windows():
            return {"ok": False, "error": "HKS add-on installers are Windows batch files."}
        container = _DOCKER_CONTAINERS.get(addon["name"])
        if container:
            state = _container_info(container)
            if state and state.get("running"):
                return {"ok": True, "started": addon["name"], "already_running": True,
                        "note": f"{addon['name']} is already running in its HKS-managed container; its installer was not rerun."}
        if addon.get("port") and _port_open(addon["port"]):
            return {"ok": False, "error": f"Port {addon['port']} is already occupied. HKS did not rerun {addon['name']}'s installer or replace the current service."}
        if addon["how"].startswith("Docker"):
            docker = shutil.which("docker")
            if not docker and os.path.isfile(r"C:\Program Files\Docker\Docker\resources\bin\docker.exe"):
                docker = r"C:\Program Files\Docker\Docker\resources\bin\docker.exe"
            if not docker:
                return {"ok": False, "error": "Docker Desktop is missing. Install Docker Desktop, start it, then retry this add-on."}
            try:
                check = subprocess.run([docker, "info", "--format", "{{.ServerVersion}}"],
                                       capture_output=True, text=True, timeout=6,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except subprocess.TimeoutExpired:
                return {"ok": False, "error": "Docker Desktop did not respond within 6 seconds. Wait for its engine to start, then retry."}
            except OSError as exc:
                return {"ok": False, "error": f"Couldn't check Docker Desktop: {exc}"}
            if check.returncode:
                detail = (check.stderr or check.stdout or "").strip().splitlines()
                suffix = (" (" + detail[-1][:180] + ")") if detail else ""
                return {"ok": False, "error": "Docker Desktop is installed, but its engine is stopped. Start Docker Desktop and wait until it is running, then retry." + suffix}
        with _launch_lock:
            previous = _launches.get(addon["name"])
            if previous and previous.poll() is None:
                return {"ok": True, "started": addon["name"], "pid": previous.pid,
                        "already_running": True,
                        "note": f"{addon['name']} installer is already open (PID {previous.pid}); use that console instead of starting a duplicate."}
            try:
                proc = subprocess.Popen(
                    ["cmd.exe", "/d", "/c", bat],
                    cwd=os.path.dirname(bat),
                    creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
                    close_fds=True,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                return {"ok": False, "error": f"Couldn't open the {addon['name']} installer: {exc}"}
            _launches[addon["name"]] = proc
        return {"ok": True, "started": addon["name"], "pid": proc.pid,
                "note": f"Windows launched {addon['name']} in a visible console (PID {proc.pid}). Check that window for download, UAC, or error prompts; Refresh verifies whether its service actually started."}

    return {"addons.run": run}
