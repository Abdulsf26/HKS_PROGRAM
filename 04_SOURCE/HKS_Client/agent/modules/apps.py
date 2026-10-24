"""Apps: launch programs, process control (kill/suspend/resume), startup items."""
import os
import subprocess

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def register(ctx):
    def launch(c, p):
        target = p["target"].strip()
        if os.path.exists(target):
            # round 61 (audit4 #9): ONE launch path - exe-like files via
            # os.startfile, everything else (folders/docs) via explorer.
            if target.lower().endswith((".exe", ".msi", ".bat", ".lnk")):
                os.startfile(target)
            else:
                subprocess.Popen(["explorer", target], creationflags=CREATE_NO_WINDOW)
            return {"launched": target}
        # bare name like "calc" / "notepad" — resolve via shell
        proc = subprocess.Popen(f'start "" "{target}"', shell=True,
                                creationflags=CREATE_NO_WINDOW)
        return {"launched": target, "pid": proc.pid}

    def processes(c, p):
        import psutil
        items = []
        for pr in psutil.process_iter(["pid", "name", "memory_info", "username"]):
            try:
                items.append({"pid": pr.info["pid"], "name": pr.info["name"],
                              "mem": pr.info["memory_info"].rss if pr.info["memory_info"] else 0,
                              "user": (pr.info["username"] or "").split("\\")[-1]})
            except Exception:
                continue
        # one extra pass for cpu% (cheap sample of top 30 by mem)
        top = sorted(items, key=lambda x: -x["mem"])[:120]
        for it in top:
            try:
                it["cpu"] = round(psutil.Process(it["pid"]).cpu_percent(interval=0.05), 1)
            except Exception:
                it["cpu"] = None
        top.sort(key=lambda x: -(x.get("cpu") or 0))
        return {"items": top}

    def kill(c, p):
        import psutil
        psutil.Process(int(p["pid"])).kill()
        return {"killed": p["pid"]}

    def suspend(c, p):
        import psutil
        psutil.Process(int(p["pid"])).suspend()
        c.send_event("process_suspended", {"pid": p["pid"]})
        return {"suspended": p["pid"]}

    def resume(c, p):
        import psutil
        psutil.Process(int(p["pid"])).resume()
        return {"resumed": p["pid"]}

    def startup_list(c, p):
        items = []
        for hive, root, label in (("HKLM", _HKLM_RUN, "HKLM"), ("HKCU", _HKCU_RUN, "HKCU")):
            try:
                import winreg
                k = winreg.OpenKey(root, hive + "\\" + _RUN_KEY)
                i = 0
                while True:
                    try:
                        name, val, _ = winreg.EnumValue(k, i)
                        i += 1
                        items.append({"name": name, "target": val, "location": label})
                    except OSError:
                        break
            except Exception:
                pass
        return {"items": items}

    def startup_add(c, p):
        import winreg
        root = _HKLM_RUN if p.get("location", "HKCU") == "HKLM" else _HKCU_RUN
        with winreg.CreateKeyEx(root, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, p["name"], 0, winreg.REG_SZ, p["target"])
        return {"added": p["name"]}

    def startup_remove(c, p):
        import winreg
        root = _HKLM_RUN if p.get("location", "HKCU") == "HKLM" else _HKCU_RUN
        with winreg.OpenKey(root, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, p["name"])
        return {"removed": p["name"]}

    def apps_uninstall_meta(c, p):
        """Real Windows uninstall entries (plan §29): registry uninstall
        keys (64+32 bit + HKCU), MSI, with UninstallString metadata."""
        if os.name != "nt":
            return {"ok": False, "error": "uninstall metadata is Windows-only"}
        import winreg
        out, seen = [], set()
        spots = [(winreg.HKEY_LOCAL_MACHINE,
                  r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", "HKLM64"),
                 (winreg.HKEY_LOCAL_MACHINE,
                  r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall", "HKLM32"),
                 (winreg.HKEY_CURRENT_USER,
                  r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", "HKCU")]
        for hive, path, label in spots:
            try:
                k = winreg.OpenKey(hive, path)
            except OSError:
                continue
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(k, i); i += 1
                except OSError:
                    break
                try:
                    sk = winreg.OpenKey(k, sub)
                    name, _ = winreg.QueryValueEx(sk, "DisplayName")
                except OSError:
                    continue
                if name in seen:
                    continue
                seen.add(name)
                def q(v):
                    try:
                        return winreg.QueryValueEx(sk, v)[0]
                    except OSError:
                        return None
                out.append({"name": name, "where": label,
                            "version": q("DisplayVersion"),
                            "publisher": q("Publisher"),
                            "install_date": q("InstallDate"),
                            "est_size_mb": round(q("EstimatedSize") / 1024, 1) if q("EstimatedSize") else None,
                            "uninstall": q("UninstallString"),
                            "quiet": q("QuietUninstallString"),
                            "key": sub})
                if len(out) >= 400:
                    break
        out.sort(key=lambda x: x["name"].lower())
        return {"items": out}

    def apps_uninstall(c, p):
        """Run a vendor-supported quiet uninstall; never strand the user in a wizard."""
        if p.get("confirm") != "CONFIRM":
            return {"ok": False, "error": 'send {"confirm":"CONFIRM"} to launch the uninstaller'}
        name = str(p.get("name", ""))
        meta = apps_uninstall_meta(c, {})
        hit = next((x for x in meta.get("items", []) if x["name"].lower() == name.lower()), None)
        if not hit or not hit.get("uninstall"):
            return {"ok": False, "error": f"no uninstall entry for: {name}"}
        import re
        import shlex
        quiet = str(hit.get("quiet") or "").strip()
        uninstall = str(hit.get("uninstall") or "").strip()
        if quiet:
            target = quiet
        elif os.name == "nt" and re.search(r"(?i)msiexec(?:\.exe)?", uninstall):
            guid = re.search(r"\{[0-9a-fA-F-]{36}\}", uninstall)
            if not guid:
                return {"ok": False, "error": "This MSI uninstall entry has no product code; open its Windows Settings page to remove it safely."}
            target = f'msiexec.exe /x {guid.group(0)} /qn /norestart'
        elif os.name == "nt" and "unins" in os.path.basename(uninstall).lower():
            target = uninstall + " /VERYSILENT /SUPPRESSMSGBOXES /NORESTART"
        elif os.name == "nt" and "uninstall" in os.path.basename(uninstall).lower():
            target = uninstall + " /S"
        else:
            return {"ok": False, "error": "This app does not advertise a supported quiet uninstall. Nothing was launched; use Windows Settings → Apps to avoid an interactive wizard."}
        try:
            if os.name == "nt":
                subprocess.Popen(["cmd.exe", "/d", "/s", "/c", target],
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            else:
                subprocess.Popen(shlex.split(target))
            return {"launched": target,
                    "note": "quiet uninstall started; refresh Installed apps to confirm it completed"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    return {"apps.launch": launch, "apps.processes": processes, "apps.kill": kill,
            "apps.suspend": suspend, "apps.resume": resume, "apps.startup_list": startup_list,
            "apps.startup_add": startup_add, "apps.startup_remove": startup_remove,
            "apps.uninstall_meta": apps_uninstall_meta, "apps.uninstall": apps_uninstall}


_RUN_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
try:
    import winreg
    _HKLM_RUN = winreg.HKEY_LOCAL_MACHINE
    _HKCU_RUN = winreg.HKEY_CURRENT_USER
except ImportError:
    _HKLM_RUN = _HKCU_RUN = None
