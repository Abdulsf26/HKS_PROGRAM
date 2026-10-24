"""HKS round 68 (F12): independent watchdog.

A SECOND scheduled task (HKS-Watchdog) whose only job is: every 60 s, can I
reach the panel port and the agent status ports? Three consecutive misses ->
`schtasks /Run` the matching task. If a start-loop bat ever dies, this is
what brings the stack back without a reboot. Stdlib only.
Run: pythonw watchdog.py   (installed by INSTALL_HKS.bat)
"""
import json
import os
import socket
import subprocess
import time
from urllib.request import urlopen

CREATE_NO_WINDOW = 0x08000000


def port_answers(host, port, timeout=2.0):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def read_port(cfg_path):
    try:
        with open(cfg_path, encoding="utf-8") as f:
            return int(json.load(f).get("port", 8000))
    except Exception:
        return 8000


def role_answers(host, ports, role, timeout=2.0):
    """Find this role's own status page, regardless of which agent got 8590.

    Both agents negotiate 8590..8594. A raw TCP check can mistake the other
    agent's page for this one and repeatedly relaunch an already healthy task.
    """
    for port in ports:
        try:
            with urlopen(f"http://{host}:{port}/status.json", timeout=timeout) as response:
                if response.status != 200:
                    continue
                if json.loads(response.read().decode("utf-8")).get("role") == role:
                    return True
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return False


def run_task(name):
    subprocess.run(["schtasks", "/Run", "/TN", name], capture_output=True,
                   creationflags=CREATE_NO_WINDOW)


def tick(ports, fails, now=None):
    """Check panel reachability and agent role identity; return tasks to restart."""
    now = now or time.time()
    due = []
    for role, plist in ports.items():
        healthy = (any(port_answers("127.0.0.1", p) for p in plist)
                   if role == "panel" else
                   role_answers("127.0.0.1", plist, "svc" if role == "agent" else role))
        if healthy:
            fails[role] = 0
            continue
        fails[role] = fails.get(role, 0) + 1
        if fails[role] >= 3:
            fails[role] = 0
            due.append(role)
    return due


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # HKS_Client
    port = read_port(os.path.join(root, "config.json"))
    ports = {"panel": [port], "agent": list(range(8590, 8595)),
             "session": list(range(8590, 8595))}
    tasks = {"panel": "HKS-Panel", "agent": "HKS-Agent", "session": "HKS-SessionAgent"}
    fails = {}
    while True:
        for role in tick(ports, fails):
            run_task(tasks[role])
        time.sleep(60)


if __name__ == "__main__":
    main()
