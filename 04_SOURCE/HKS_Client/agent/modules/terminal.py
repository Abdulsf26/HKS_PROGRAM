"""Terminal (PowerShell/CMD on Windows, bash on POSIX hosts; SYSTEM or
user mode) + Windows OpenSSH server control."""
import os
import platform
import subprocess
import threading
import time

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _probe_cwd(shell, cwd):
    """Ask the OS for the actual directory of a process started with `cwd`
    (a separate tiny process — the agent's own cwd is meaningless here)."""
    try:
        if os.name != "nt":
            argv = ["/bin/bash", "-c", "pwd"]
        elif shell == "cmd":
            argv = ["cmd", "/d", "/c", "cd"]
        else:
            argv = ["powershell", "-NoProfile", "-Command", "(Get-Location).Path"]
        p = subprocess.run(argv, capture_output=True, text=True, timeout=15,
                           cwd=cwd or None, errors="replace",
                           creationflags=CREATE_NO_WINDOW)
        lines = [l.strip() for l in (p.stdout or "").splitlines() if l.strip()]
        if lines:
            return lines[-1]
    except Exception:
        pass
    return cwd or ""


class _LiveShell:
    """One long-lived bash behind the panel terminal (POSIX hosts).

    Every terminal.exec feeds the SAME shell, so `cd`, environment
    variables, aliases and history persist between commands — like a
    real SSH session, not one-shot spawns. A reader thread pumps the
    shell's output into a queue (select() cannot be used on Python's
    buffered text streams — lines stall in the buffer). Output between
    command and completion marker is returned; a hard deadline guards
    against never-ending commands. Windows is untouched: it keeps
    per-command PowerShell.
    """
    MARK = "__HKS_DONE_%d__"

    def __init__(self):
        import atexit
        import queue as _queue
        import threading as _th
        import uuid as _uuid
        # round 61 (audit4 #10): a DETERMINISTIC marker can be printed by
        # the user's own command, faking completion. Random per shell.
        self.MARK = "__HKS_DONE_" + _uuid.uuid4().hex + "_%d__"
        self.q = _queue.Queue()
        # One writer (the stream drain thread) records output, while REST
        # clients may poll concurrently. Keep a sequence cursor so polling
        # neither races the queue consumer nor repeats old lines.
        self.hist = _queue.deque(maxlen=500)
        self.output_seq = 0
        self.output_lock = threading.Lock()
        env = dict(os.environ)
        env["PS1"] = ""
        env["PS2"] = ""
        env["TERM"] = "dumb"
        argv = (["powershell", "-NoProfile", "-NonInteractive", "-Command", "-"]
                if os.name == "nt" else ["/bin/bash", "--noprofile", "--norc"])
        self.proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, bufsize=1, env=env,
            errors="replace", creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0)
        self.seq = 0
        # round 64 (audit6 #11): the hub allows 3 concurrent agent actions and
        # EVERY terminal.exec lands on this ONE shell object - unsynchronized
        # stdin writes + a shared output queue let two readers eat each other's
        # marker lines. One command at a time on the shell; the hub stays free.
        self._runlock = threading.Lock()

        def _pump(proc, q):
            for line in proc.stdout:
                q.put(line.rstrip("\n"))
            q.put(None)  # EOF sentinel: shell died

        self._th = _th.Thread(target=_pump, args=(self.proc, self.q),
                              daemon=True)
        self._th.start()
        try:
            if os.name == "nt":
                self.proc.stdin.write("$ProgressPreference='SilentlyContinue'\n")
            else:
                self.proc.stdin.write("export PS1='' PS2=''\n")
            self.proc.stdin.flush()
        except Exception:
            pass
        atexit.register(self.close)

    def close(self):
        try:
            self.proc.stdin.write("exit\n")
            self.proc.stdin.flush()
        except Exception:
            pass

    def record_output(self, line):
        with self.output_lock:
            self.output_seq += 1
            self.hist.append((self.output_seq, str(line)))
            return self.output_seq

    def output_since(self, after=0, tail=200):
        try:
            after = max(0, int(after))
            tail = max(1, min(500, int(tail)))
        except (TypeError, ValueError):
            after, tail = 0, 200
        with self.output_lock:
            rows = list(self.hist)
            cursor = self.output_seq
        newer = [line for seq, line in rows if seq > after]
        return {"lines": newer[-tail:], "cursor": cursor}
        try:
            self.proc.kill()
        except Exception:
            pass

    def _read_until_marker(self, mark, deadline):
        """Drain queue lines until the marker line; returns (lines, code)."""
        buf = []
        code = None
        while time.time() < deadline:
            try:
                t = self.q.get(timeout=0.25)
            except Exception:
                continue
            if t is None:
                raise RuntimeError("shell terminated")
            if t == mark:
                code = 0
                break
            if t.startswith(mark + " "):
                try:
                    code = int(t.split(" ", 1)[1])
                except Exception:
                    code = 0
                break
            buf.append(t)
        return buf, code

    def run(self, command, timeout):
        with self._runlock:  # round 64 (audit6 #11): see __init__
            return self._run_once(command, timeout)

    def _run_once(self, command, timeout):
        self.seq += 1
        mark = self.MARK % self.seq
        # round 64 (audit6 #13): $LASTEXITCODE is ONLY set by external
        # programs - a failing cmdlet (Get-Content missing.txt) left the stale
        # value and the panel reported "exit 0". $? is honest for both.
        done = ("Write-Output \"" + mark
                + " $(if ($?) { 0 } else { if ($LASTEXITCODE) { $LASTEXITCODE } else { 1 } })\""
                if os.name == "nt" else "echo " + mark + " $?")
        try:
            self.proc.stdin.write(command + "\n")
            self.proc.stdin.write(done + "\n")
            self.proc.stdin.flush()
        except Exception:
            raise RuntimeError("shell died - will restart on next command")
        deadline = time.time() + max(3, min(int(timeout or 30), 180))
        buf, code = self._read_until_marker(mark, deadline)
        if code is None:
            # timed out - drain up to 1 s for the late marker so it cannot
            # leak into (and desync) the NEXT command's output (audit3)
            try:
                self.proc.stdin.write("\n")
                self.proc.stdin.flush()
            except Exception:
                pass
            self._read_until_marker(mark, time.time() + 1.0)
            # round 61 (audit4 #7): a timed-out command leaves the shell in
            # an UNKNOWN state - reuse would mix outputs/cwd. Kill it; the
            # next command's failed write triggers the fresh-shell restart.
            try:
                self.proc.kill()
            except Exception:
                pass
        # real cwd straight from the live shell (marker seq 0)
        cwd = ""
        pwd_cmd = ("(Get-Location).Path\n" if os.name == "nt" else "pwd\n")
        done0 = ("Write-Output \"" + (self.MARK % 0) + " $LASTEXITCODE\""
                 if os.name == "nt" else "echo " + self.MARK % 0 + " $?")
        try:
            self.proc.stdin.write(pwd_cmd)
            self.proc.stdin.write(done0 + "\n")
            self.proc.stdin.flush()
            bl, _ = self._read_until_marker(self.MARK % 0, time.time() + 5)
            if bl:
                cwd = bl[-1].strip()
        except Exception:
            pass
        return "\n".join(buf), code, cwd or ("C:\\" if os.name == "nt" else "/")  # round 62 (audit5 #9): win fallback


def _run_shell(shell, command, cwd, timeout, prev_cwd="", live=None):
    timeout = min(int(timeout or 30), 180)
    if live is not None:
        try:
            out, code, new_cwd = live.run(command, timeout)
            return {"output": out[:120000], "error": "", "code": 0 if code is None else code,
                    "cwd": new_cwd, "live": True}
        except RuntimeError:
            try:
                live.close()
            except Exception:
                pass
            if live.proc and live.proc.poll() is None:
                raise
            # fall through to one-shot below
    cwd = cwd or prev_cwd or None   # persistent terminal: remember last directory
    if os.name != "nt":
        # POSIX host (Linux support/demo boxes): bash runs unix commands
        # AND coreutils `dir`. Windows keeps powershell/cmd unchanged.
        argv = ["/bin/bash", "-c", command]
    elif shell == "cmd":
        argv = ["cmd", "/d", "/c", command]
    else:
        argv = ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
    p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                       cwd=cwd, errors="replace",
                       creationflags=CREATE_NO_WINDOW)
    out = (p.stdout or "")
    err = (p.stderr or "")
    new_cwd = _probe_cwd(shell, cwd)
    return {"output": out[:120000], "error": err[:20000], "code": p.returncode,
            "cwd": new_cwd or ("C:\\" if os.name == "nt" else "/")}


# ---------- real-time streaming terminal (round 14) ----------
_STREAM = {"on": False, "th": None}

def _stream_drain(ctx, sh):
    while _STREAM["on"]:
        try:
            t = sh.q.get(timeout=0.3)
        except Exception:
            continue
        if t is None:
            sh.record_output("[shell exited]")
            ctx.send_event("term_out", {"data": "[shell exited]"})
            _STREAM["on"] = False
            break
        sh.record_output(t)
        ctx.send_event("term_out", {"data": t})


def register(ctx):
    def terminal_exec(c, p):
        if getattr(c, "live_shell", None) is None:
            c.live_shell = _LiveShell()   # one bash/PowerShell for the session
        res = _run_shell(p.get("shell", "powershell"), p.get("command", ""),
                         p.get("cwd"), p.get("timeout", 30),
                         prev_cwd=getattr(c, "terminal_cwd", ""),
                         live=getattr(c, "live_shell", None))
        if not res.get("live"):
            c.live_shell = None   # shell died - fresh one next command
        c.terminal_cwd = res.get("cwd") or getattr(c, "terminal_cwd", "")
        return res

    def _po(cmd, t=15):
        # os.popen has NO timeout - a stuck WLAN service hung telemetry once;
        # every helper call is bounded now (audit3)
        try:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                               timeout=t, errors="replace")
            return r.stdout or ""
        except Exception:
            return ""

    def ssh_status(c, p):
        out = _po("powershell -NoProfile -Command "
                  "(Get-WindowsCapability -Online -Name 'OpenSSH.Server*').State; "
                  "(Get-Service sshd -ErrorAction SilentlyContinue).Status")
        ip = _po("ipconfig")
        return {"state": out.strip(), "hint_ip": [l for l in ip.splitlines() if "IPv4" in l]}

    def ssh_enable(c, p):
        lines = []
        lines.append(_po("powershell -NoProfile -Command "
                         "Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0"))
        _po("powershell -NoProfile -Command "
            "Set-Service sshd -StartupType Automatic; Start-Service sshd")
        # firewall: NetSecurity cmdlet first (netsh advfirewall is deprecated
        # on current Win11 / Server 2026 builds), netsh as fallback
        fw = ("if (-not (Get-NetFirewallRule -DisplayName 'HKS sshd' "
              "-ErrorAction SilentlyContinue)) { New-NetFirewallRule "
              "-DisplayName 'HKS sshd' -Direction Inbound -Action Allow "
              "-Protocol TCP -LocalPort 22 | Out-Null }")
        _po("powershell -NoProfile -Command " + fw)
        return {"ok": True, "output": lines, "note": "SSH on port 22 - firewall rule "
                "'HKS sshd' created (New-NetFirewallRule)"}

    def ssh_disable(c, p):
        _po("powershell -NoProfile -Command "
            "Stop-Service sshd -ErrorAction SilentlyContinue; "
            "Set-Service sshd -StartupType Disabled")
        return {"ok": True}

    def terminal_stream(c, p):
        """Stream the live shell's output to the panel in real time.

        While on, the panel gets every line as a term_out event the moment
        the shell prints it (like a real SSH window). terminal.write feeds
        the shell's stdin. Turning stream off returns the shell to
        one-shot exec mode."""
        on = bool(p.get("on", False))
        if on and not _STREAM["on"]:
            c.live_shell = getattr(c, "live_shell", None) or _LiveShell()
            _STREAM["on"] = True
            _STREAM["th"] = threading.Thread(target=_stream_drain, args=(c, c.live_shell),
                                             daemon=True, name="hks-term-stream")
            _STREAM["th"].start()
            return {"streaming": True, "shell": "powershell" if os.name == "nt" else "bash"}
        if not on:
            _STREAM["on"] = False
            return {"streaming": False}
        return {"streaming": True}

    def terminal_output(c, p):
        """7.2.55: pull terminal output over plain REST - the panel uses this
        when the browser blocks the live websocket channel (self-signed cert
        browsers). Returns the buffered tail since the last poll."""
        sh = getattr(c, "live_shell", None)
        if sh is None:
            return {"ok": True, "lines": [], "streaming": bool(_STREAM.get("on"))}
        result = sh.output_since(p.get("after", 0), p.get("tail", 200))
        return {"ok": True, **result, "streaming": bool(_STREAM.get("on"))}

    def terminal_write(c, p):
        if getattr(c, "live_shell", None) is None:
            c.live_shell = _LiveShell()
        text = str(p.get("text", ""))
        if not text:
            return {"written": 0}
        if not text.endswith("\n"):
            text += "\n"
        try:
            c.live_shell.proc.stdin.write(text)
            c.live_shell.proc.stdin.flush()
            return {"written": len(text)}
        except Exception as e:
            c.live_shell = None
            return {"ok": False, "error": f"shell died: {e}"}

    def power_unlock(c, p):
        """Fail safely: Windows does not expose local secure-screen unlock here."""
        if os.name != "nt":
            return {"ok": False, "error": "Windows-only action"}
        try:
            edition_name = platform.win32_edition() or ""
            edition = edition_name.lower()
        except Exception:
            edition_name = edition = ""
        if "home" in edition or edition.startswith("core"):
            return {"ok": False,
                    "error": f"Remote Desktop hosting is unavailable on this Windows edition ({edition_name or 'Home'}). HKS cannot unlock the local secure screen on Windows Home; use a supported Remote Desktop host or sign in on the PC."}
        import ctypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.WTSGetActiveConsoleSessionId.restype = ctypes.c_uint32
        sid = ctypes.c_uint32(k32.WTSGetActiveConsoleSessionId())
        if sid.value == 0xFFFFFFFF:
            return {"ok": False, "error": "no active console session (nobody logged in?)"}
        return {"ok": False,
                "error": "This is the physical Windows console. Win32 7041 (ERROR_CTX_CONSOLE_DISCONNECT) means Windows does not support disconnecting that console session. WTSConnectSessionW reconnects Remote Desktop Services sessions; it cannot unlock the local secure sign-in screen. No session was disconnected. Unlock locally, or connect with a supported Remote Desktop client/host."}

    return {"terminal.exec": terminal_exec, "ssh.status": ssh_status,
            "ssh.enable": ssh_enable, "ssh.disable": ssh_disable,
            "terminal.stream": terminal_stream, "terminal.write": terminal_write,
            "terminal.output": terminal_output,
            "power.unlock": power_unlock}
