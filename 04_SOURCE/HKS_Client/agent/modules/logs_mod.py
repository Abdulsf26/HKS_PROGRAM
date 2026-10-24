"""Windows event queries: logons, shutdowns, boots + local sessions."""
import subprocess

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _ps(cmd, timeout=90):
    p = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                       capture_output=True, text=True, errors="replace", timeout=timeout,
                       creationflags=CREATE_NO_WINDOW)
    return p.stdout or ""


def register(ctx):
    def windows_log(c, p):
        kinds = p.get("kinds") or ["logon", "logoff", "shutdown", "start"]
        lines = []
        if "logon" in kinds or "logoff" in kinds:
            ids = "4624,4625,4634,4647,4800,4801"
            lines.append("=== Logon/Logoff/Lock-Unlock (Security) ===")
            lines.append(_ps(
                f"Get-WinEvent -FilterHashtable @{{LogName='Security';Id={ids}}} -MaxEvents 60 "
                "-ErrorAction SilentlyContinue | "
                "ForEach-Object { \"{0} id={1} {2}\" -f $_.TimeCreated,$_.Id,($_.Message -split \"`n\")[0..4] -join ' ' } "
                "| Select-Object -First 60"))
        if "shutdown" in kinds or "start" in kinds:
            lines.append("=== Shutdown / Boot events (System) ===")
            lines.append(_ps(
                "Get-WinEvent -FilterHashtable @{LogName='System';Id=1074,6006,6008,107,42,1} "
                "-MaxEvents 60 -ErrorAction SilentlyContinue | "
                "ForEach-Object { \"{0} id={1} {2}\" -f $_.TimeCreated,$_.Id,($_.Message -split \"`n\")[0] } "
                "| Select-Object -First 60"))
        return {"lines": [l for l in lines if l.strip()]}

    def local_sessions(c, p):
        out = subprocess.run(["quser"], capture_output=True, text=True, errors="replace",
                             creationflags=CREATE_NO_WINDOW).stdout or ""
        return {"raw": out}

    return {"logs.windows": windows_log, "logs.sessions": local_sessions}
