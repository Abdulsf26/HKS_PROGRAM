"""Code Runner (field request 2026-09-21): run snippets in 11 languages,
each run in its own temp workspace with timeout + output capture.

Security model (plan §19/§53):
- every run gets a fresh temp dir, wiped afterwards
- hard timeout, output caps
- NEVER installs runtimes silently: code.install requires confirm="CONFIRM"
  and uses official winget (Windows) / apt (Debian-family) only
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# lang -> (ext, argv-template, checker-argv, installer)
LANGS = {
    "python":     (".py", ["{rt}", "{file}"], ["{rt}", "--version"],
                   "winget install -e --id Python.Python.3.12"),
    "javascript": (".js", ["node", "{file}"], ["node", "--version"],
                   "winget install -e --id OpenJS.NodeJS.LTS"),
    "typescript": (".ts", ["npx", "-y", "ts-node", "{file}"], ["npx", "-y", "ts-node", "--version"],
                   "npm install -g typescript ts-node"),
    "c":          (".c", ["gcc", "{file}", "-o", "{out}", "&&", "{out}"], ["gcc", "--version"],
                   "Install MSYS2, then the Code Runner installs the UCRT GCC toolchain."),
    "cpp":        (".cpp", ["g++", "{file}", "-o", "{out}", "&&", "{out}"], ["g++", "--version"],
                   "Install MSYS2, then the Code Runner installs the UCRT GCC toolchain."),
    # Java 8 does not support the source-file launcher (java File.java).
    # Always compile first, then run the resulting class from its workspace.
    "java":       (".java", ["javac", "{file}"], ["java", "-version"],
                   "winget install -e --id EclipseAdoptium.Temurin.21.JDK"),
    "csharp":     (".cs", ["dotnet", "run", "--project", "{dir}"], ["dotnet", "--version"],
                   "winget install -e --id Microsoft.DotNet.SDK.8"),
    "go":         (".go", ["go", "run", "{file}"], ["go", "version"],
                   "winget install -e --id GoLang.Go"),
    "rust":       (".rs", ["rustc", "{file}", "-o", "{out}", "&&", "{out}"], ["rustc", "--version"],
                   "winget install -e --id Rustlang.Rustup"),
    "php":        (".php", ["php", "{file}"], ["php", "--version"],
                   "winget install -e --id PHP.PHP.8.3"),
    "ruby":       (".rb", ["ruby", "{file}"], ["ruby", "--version"],
                   "winget install -e --id RubyInstallerTeam.Ruby.3.3"),
    "perl":       (".pl", ["perl", "{file}"], ["perl", "--version"],
                   "winget install -e --id StrawberryPerl.StrawberryPerl"),
}

SHELL_CMDS = {"c", "cpp", "rust"}  # compiled with argv lists; no shell parsing


def _which(rt):
    from shutil import which
    name = rt.split()[0]
    found = which(name) or which(name + ".exe")
    if found:
        return found
    # MSYS2's UCRT compiler is not added to PATH by its installer.
    if os.name == "nt" and name in {"gcc", "g++"}:
        root = os.environ.get("MSYS2_ROOT", r"C:\msys64")
        candidate = os.path.join(root, "ucrt64", "bin", name + ".exe")
        if os.path.isfile(candidate):
            return candidate
    if os.name == "nt" and name in {"java", "javac"}:
        # A service/session process may have started before a JDK was
        # installed, so its inherited PATH can be stale. Refresh user and
        # machine PATH/JAVA_HOME from the registry before resolving Java.
        try:
            import winreg
            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                key_path = r"Environment" if hive == winreg.HKEY_CURRENT_USER else r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
                try:
                    with winreg.OpenKey(hive, key_path) as key:
                        for var in ("PATH", "JAVA_HOME"):
                            try:
                                value, _kind = winreg.QueryValueEx(key, var)
                            except OSError:
                                continue
                            if var == "PATH":
                                os.environ["PATH"] = os.environ.get("PATH", "") + os.pathsep + value
                            elif value:
                                os.environ["JAVA_HOME"] = value
                except OSError:
                    continue
        except (ImportError, OSError):
            pass
        java_home = os.environ.get("JAVA_HOME", "")
        candidates = [os.path.join(java_home, "bin", name + ".exe")] if java_home else []
        for root in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")):
            if not root or not os.path.isdir(root):
                continue
            for pattern in ("Eclipse Adoptium", "Java", "Microsoft"):
                parent = os.path.join(root, pattern)
                if os.path.isdir(parent):
                    try:
                        candidates.extend(os.path.join(parent, d, "bin", name + ".exe")
                                          for d in os.listdir(parent))
                    except OSError:
                        pass
        for candidate in candidates:
            if os.path.isfile(candidate):
                return candidate
    if os.name == "nt" and name in {"gcc", "g++"}:
        local = os.environ.get("LOCALAPPDATA", "")
        package_root = os.path.join(local, "Microsoft", "WinGet", "Packages") if local else ""
        if package_root and os.path.isdir(package_root):
            import glob
            matches = sorted(glob.glob(os.path.join(package_root, "BrechtSanders.WinLibs.POSIX.UCRT_*")), reverse=True)
            for package in matches:
                for folder, _dirs, _files in os.walk(package):
                    candidate = os.path.join(folder, name + ".exe")
                    if os.path.isfile(candidate):
                        return candidate
    return None


def _runtime(lang):
    """Returns (ok, argv-template or None, version-string)."""
    meta = LANGS.get(lang)
    if not meta:
        return False, None, "unknown language"
    ext, tmpl, chk, _inst = meta
    # find the checker binary
    base = chk[0]
    exe = _python_executable() if base == "{rt}" else (_which(base) or _which(base + ".exe"))
    if not exe:
        return False, None, "runtime not installed"
    if lang == "java" and not _which("javac"):
        return False, None, "JDK compiler (javac) not installed; a Java runtime alone cannot compile programs"
    try:
        if base == "{rt}":
            argv = [exe, "--version"]
        else:
            argv = [exe] + chk[1:]
        p = subprocess.run(argv, capture_output=True, text=True, timeout=20,
                           errors="replace", creationflags=CREATE_NO_WINDOW)
        ver = (p.stdout or p.stderr or "").strip().splitlines()
        return True, tmpl, (ver[0] if ver else "installed")
    except Exception as e:
        return False, None, f"runtime broken: {e}"


def _python_executable():
    """Resolve the exact interpreter HKS runs under before consulting PATH.

    Windows services commonly receive a reduced SYSTEM PATH; the bundled HKS
    venv is still the right runtime for Code Runner in that context.
    """
    candidates = []
    current = os.path.abspath(sys.executable) if sys.executable else ""
    if current:
        if os.name == "nt" and os.path.basename(current).lower() in {"pythonw.exe", "pythonw"}:
            candidates.append(os.path.join(os.path.dirname(current), "python.exe"))
        candidates.append(current)
        if os.name == "nt":
            candidates.append(os.path.join(os.path.dirname(current), "python.exe"))
    prefix = getattr(sys, "prefix", "")
    if prefix:
        candidates.append(os.path.join(prefix, "Scripts", "python.exe" if os.name == "nt" else "python"))
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return _which("python3") or _which("python") or _which("py")


def register(ctx):
    def code_check(c, p):
        lang = str(p.get("lang", "")).lower()
        ok, _tmpl, ver = _runtime(lang)
        meta = LANGS.get(lang)
        return {"installed": ok, "version": ver if ok else None,
                "hint": None if ok else (meta[3] if meta else "unknown language")}

    def code_run(c, p):
        lang = str(p.get("lang", "")).lower()
        code = str(p.get("code", ""))[:200000]
        try:
            timeout = max(1, min(int(p.get("timeout", 20)), 120))
        except (TypeError, ValueError):
            return {"ok": False, "error": "timeout must be an integer from 1 to 120 seconds"}
        if lang not in LANGS:
            return {"ok": False, "error": f"unsupported language: {lang}"}
        ok, tmpl, ver = _runtime(lang)
        if not ok:
            return {"ok": False, "error": f"{lang}: {ver}",
                    "install_hint": LANGS[lang][3]}
        ext, _t, _c, _i = LANGS[lang]
        workdir = tempfile.mkdtemp(prefix="hks_run_")
        result = {"ok": False}
        try:
            if lang == "csharp":
                # dotnet needs a project: minimal csproj + top-level Program.cs
                main = os.path.join(workdir, "Program.cs")
                with open(main, "w", encoding="utf-8") as f:
                    f.write(code)
                with open(os.path.join(workdir, "run.csproj"), "w", encoding="utf-8") as f:
                    f.write('<Project Sdk="Microsoft.NET.Sdk">'
                            '<PropertyGroup>'
                            '<OutputType>Exe</OutputType>'
                            '<TargetFramework>net8.0</TargetFramework>'
                            '<Nullable>disable</Nullable>'
                            '<ImplicitUsings>enable</ImplicitUsings>'
                            '</PropertyGroup></Project>')
            else:
                java_class = "Main"
                if lang == "java":
                    match = __import__("re").search(r"\bpublic\s+(?:final\s+)?class\s+([A-Za-z_$][\w$]*)", code)
                    if not match:
                        match = __import__("re").search(r"\bclass\s+([A-Za-z_$][\w$]*)", code)
                    if match:
                        java_class = match.group(1)
                main = os.path.join(workdir, java_class + ext if lang == "java" else "code" + ext)
                with open(main, "w", encoding="utf-8") as f:
                    f.write(code)
            if lang == "python":
                exe = _python_executable()
                if not exe:
                    return {"ok": False, "error": "Python runtime was not found. Install Python or repair the HKS Python runtime, then retry Check runtime."}
                run_argv = [exe, main]
            elif lang in {"c", "cpp", "rust", "java"}:
                compiler_name = {"c": "gcc", "cpp": "g++", "rust": "rustc", "java": "javac"}[lang]
                compiler = _which(compiler_name)
                if not compiler:
                    return {"ok": False,
                            "error": f"{compiler_name} is not available after runtime detection; install the runtime and restart the HKS agent"}
                built = os.path.join(workdir, "code.exe" if os.name == "nt" else "code")
                compile_argv = ([compiler, "-d", workdir, main] if lang == "java"
                                else [compiler, main, "-o", built])
                try:
                    cp = subprocess.run(compile_argv, capture_output=True, text=True,
                                        timeout=timeout, cwd=workdir, errors="replace",
                                        creationflags=CREATE_NO_WINDOW)
                except subprocess.TimeoutExpired:
                    return {"ok": False, "error": f"{compiler_name} compilation timed out after {timeout}s"}
                if cp.returncode:
                    detail = (cp.stderr or cp.stdout or "compiler returned an error")[:40000]
                    return {"ok": False, "error": "compilation failed:\n" + detail,
                            "stderr": detail,
                            "exit": cp.returncode}
                if lang == "java":
                    runtime = _which("java")
                    if not runtime:
                        return {"ok": False, "error": "Java runtime was not found after compilation."}
                    run_argv = [runtime, "-cp", workdir, java_class]
                else:
                    run_argv = [built]
            else:
                run_argv = [a.format(file=main, out=os.path.join(workdir, "code.exe"),
                                     dir=workdir) for a in tmpl]
            t0 = time.time()
            proc = subprocess.Popen(run_argv, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True,
                                    cwd=workdir, errors="replace",
                                    creationflags=CREATE_NO_WINDOW)
            try:
                out_b, err_b = proc.communicate(timeout=timeout)
                result = {"stdout": (out_b or "")[:100000],
                          "stderr": (err_b or "")[:40000],
                          "exit": proc.returncode,
                          "ms": int((time.time() - t0) * 1000)}
            except subprocess.TimeoutExpired:
                try:
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                                       capture_output=True, timeout=10,
                                       creationflags=CREATE_NO_WINDOW)
                    else:
                        proc.kill()
                except Exception:
                    pass
                try:
                    proc.communicate(timeout=5)
                except Exception:
                    pass
                result = {"ok": False, "error": f"timeout after {timeout}s (run killed)"}
        except Exception as e:
            result = {"ok": False, "error": f"run failed: {e}"}
        finally:
            try:
                shutil.rmtree(workdir, ignore_errors=True)
            except Exception:
                pass
        return result

    def code_install(c, p):
        """Install a missing runtime. Requires explicit CONFIRM (plan §53)."""
        if p.get("confirm") != "CONFIRM":
            return {"ok": False,
                    "error": 'confirmation required: send {"confirm":"CONFIRM"} to install',
                    "hint": LANGS.get(str(p.get("lang", "")).lower(), ("", "", "", "unknown"))[3]}
        lang = str(p.get("lang", "")).lower()
        meta = LANGS.get(lang)
        if not meta:
            return {"ok": False, "error": "unknown language"}
        inst = meta[3]
        install_output = ""
        try:
            if os.name == "nt":
                if lang in {"c", "cpp"}:
                    from shutil import which
                    winget = which("winget") or which("winget.exe")
                    if not winget and os.name == "nt":
                        local = os.environ.get("LOCALAPPDATA", "")
                        candidate = os.path.join(local, "Microsoft", "WindowsApps", "winget.exe") if local else ""
                        if candidate and os.path.isfile(candidate):
                            winget = candidate
                    if not winget:
                        return {"ok": False, "error": "Windows Package Manager (winget) was not found in this Windows user session. Repair or install App Installer from Microsoft Store, sign out and back in, then retry."}
                    wi = subprocess.run(
                        [winget, "install", "--exact", "--id", "BrechtSanders.WinLibs.POSIX.UCRT",
                         "--scope", "user", "--accept-source-agreements",
                         "--accept-package-agreements", "--disable-interactivity"],
                        capture_output=True, text=True, timeout=600, errors="replace",
                        creationflags=CREATE_NO_WINDOW)
                    if wi.returncode:
                        return {"ok": False, "error": "WinLibs GCC install failed: " +
                                (wi.stderr or wi.stdout or "winget returned an error")[-1200:]}
                    install_output += wi.stdout or wi.stderr or "WinLibs GCC installed.\n"
                else:
                    argv = ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
                            "-ExecutionPolicy", "Bypass", "-Command", inst]
                    p2 = subprocess.run(argv, capture_output=True, text=True, timeout=600,
                                         errors="replace", creationflags=CREATE_NO_WINDOW)
                    if p2.returncode:
                        return {"ok": False, "error": (p2.stderr or p2.stdout or "installer failed")[-1200:]}
                    install_output = p2.stdout or p2.stderr or "runtime setup completed"
            else:
                pkg = {"python": "python3", "javascript": "nodejs",
                       "c": "gcc", "cpp": "g++", "go": "golang",
                       "php": "php-cli", "ruby": "ruby", "perl": "perl"}.get(lang)
                if not pkg:
                    return {"ok": False, "error": "auto-install not mapped on this OS - install manually"}
                argv = ["/bin/bash", "-c", f"apt-get install -y {pkg}"]
                p2 = subprocess.run(argv, capture_output=True, text=True, timeout=600,
                                    errors="replace", creationflags=CREATE_NO_WINDOW)
                if p2.returncode:
                    return {"ok": False, "error": (p2.stderr or p2.stdout or "installer failed")[-1200:]}
                install_output = p2.stdout or p2.stderr or "runtime setup completed"
            ok2, _t, ver = _runtime(lang)
            if lang in {"c", "cpp"} and not ok2:
                return {"ok": False, "error": "installer completed, but GCC is still not visible to the agent. Restart HKS-Agent and click Check runtime."}
            return {"installed_now": ok2, "version": ver,
                    "output": install_output[-4000:]}
        except Exception as e:
            return {"ok": False, "error": f"installer failed: {e}"}

    return {"code.check": code_check, "code.run": code_run, "code.install": code_install}
