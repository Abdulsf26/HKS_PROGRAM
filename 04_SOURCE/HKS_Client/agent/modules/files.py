"""Full-disk file operations — includes hidden & system files.
Bounded everywhere so a huge folder can never exhaust RAM."""
import base64
import os
import shutil
import stat
import subprocess
import time

ZIP_MAX_FILES = 1000
ZIP_MAX_BYTES = 1024 * 1024 * 1024  # 1 GB hard cap


def _attr_flags(p):
    hidden = system = False
    try:
        import ctypes
        attrs = ctypes.windll.kernel32.GetFileAttributesW(str(p))
        if attrs != -1:
            hidden = bool(attrs & 0x2)
            system = bool(attrs & 0x4)
    except Exception:
        pass
    return hidden, system


def register(ctx):
    def drives(c, p):
        import string
        out = []
        if os.name == "nt":
            import ctypes
            bitmask = ctypes.windll.kernel32.GetLogicalDrives()
            for i, letter in enumerate(string.ascii_uppercase):
                if bitmask & (1 << i):
                    out.append(letter + ":\\")
        else:
            out = ["/"]
        return {"drives": out}

    def listdir(c, p):
        path = os.path.abspath(p.get("path", "C:\\"))
        cap = min(int(p.get("limit", 500)), 2000)
        entries = []
        truncated = False
        try:
            it = os.scandir(path)
        except (PermissionError, OSError):
            # 'C:\Documents and Settings' etc: Windows protects these -
            # say so plainly instead of a raw traceback (field: Acer 9/22)
            raise PermissionError(
                f"Windows protects '{path}' (access denied). "
                "Pick another folder - e.g. C:\\Users") from None
        def _safe_iter():  # audit2 LOW: a file can vanish (or deny access)
            while True:    # between scandir() and next() - skip, don't abort
                try:
                    e = next(it)
                except StopIteration:
                    return
                except (PermissionError, OSError):
                    continue
                yield e
        with it:
            for e in _safe_iter():
                if len(entries) >= cap:
                    truncated = True
                    break
                try:
                    hidden, system = _attr_flags(e.path)
                    st = e.stat(follow_symlinks=False)
                    entries.append({"name": e.name, "dir": e.is_dir(follow_symlinks=False),
                                    "size": 0 if e.is_dir() else st.st_size,
                                    "mtime": st.st_mtime, "hidden": hidden, "system": system})
                except (PermissionError, OSError):
                    entries.append({"name": e.name, "dir": e.is_dir(), "size": 0,
                                    "mtime": 0, "hidden": True, "system": True, "denied": True})
        entries.sort(key=lambda x: (not x["dir"], x["name"].lower()))
        return {"path": path, "entries": entries, "truncated": truncated}

    def mkdir(c, p):
        # Do not report success when the requested folder already exists.
        os.mkdir(p["path"])
        return {"created": p["path"]}

    def delete(c, p):
        target = p["path"]
        if os.path.isdir(target) and not os.path.islink(target):
            shutil.rmtree(target, onerror=_force_remove)
        else:
            _force_remove(None, target, None)
        return {"deleted": target}

    def rename(c, p):
        src = p["path"]
        dst = os.path.join(os.path.dirname(src), p["new_name"])
        os.rename(src, dst)
        return {"renamed": dst}

    def stat_file(c, p):
        st = os.stat(p["path"])
        hidden, system = _attr_flags(p["path"])
        return {"size": st.st_size, "mtime": st.st_mtime, "hidden": hidden, "system": system,
                "is_dir": os.path.isdir(p["path"])}

    def read_chunk(c, p):
        path, off = p["path"], int(p.get("offset", 0))
        size = min(int(p.get("size", 512 * 1024)), 8 * 1024 * 1024)
        name = os.path.basename(path).lower()
        if name in ("swapfile.sys", "pagefile.sys", "hiberfil.sys",
                    "dumpstack.log", "dumpstack.log.tmp"):
            raise PermissionError(
                f"'{os.path.basename(path)}' is a Windows system file - "
                "the OS keeps it locked and it can never be read. "
                "It is safe to ignore.") from None
        try:
            with open(path, "rb") as f:
                f.seek(off)
                data = f.read(size)
        except PermissionError:
            raise PermissionError(
                f"Access denied: '{path}'. Windows (or the file's owner) "
                "protects it - try a file in your own folders.") from None
        return {"b64": base64.b64encode(data).decode(), "offset": off,
                "eof": len(data) < size}

    def write_chunk(c, p):
        path, off = p["path"], int(p.get("offset", 0))
        mode = "wb" if p.get("truncate") or off == 0 else "r+b"
        with open(path, mode) as f:
            f.seek(off)
            if p.get("b64"):
                f.write(base64.b64decode(p["b64"]))
        return {"written": off, "path": path}

    def search(c, p):
        root = p.get("root", "C:\\")
        pattern = (p.get("pattern") or "*").lower()
        maxhits = min(int(p.get("max", 300)), 2000)
        hits = []
        skip_dirs = {"$recycle.bin", "system volume information", "windows.old",
                     "node_modules", "__pycache__", "windows\\winsxs"}
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d.lower() not in skip_dirs][:60]
            if len(hits) >= maxhits:
                break
            for fn in filenames:
                if fn.lower().startswith(pattern.replace("*", "")) or _glob(fn.lower(), pattern):
                    fp = os.path.join(dirpath, fn)
                    try:
                        st = os.stat(fp)
                        hits.append({"path": fp, "size": st.st_size, "mtime": st.st_mtime})
                    except OSError:
                        hits.append({"path": fp, "size": 0, "mtime": 0})
                    if len(hits) >= maxhits:
                        break
        return {"items": hits[:maxhits], "total": len(hits)}

    def zip_media(c, p):
        """ZIP photos/videos/docs (by media index) for one-click download."""
        from . import media_scan
        kind = p.get("kind", "img")
        root = (p.get("path") or "C:\\").rstrip("\\") + "\\"
        q = (p.get("q") or "").lower()
        max_files = min(int(p.get("max_files", 300)), ZIP_MAX_FILES)
        items = media_scan.scan_drive(kind, root)
        if q:
            items = [i for i in items if q in i["path"].lower()]
        paths = [i["path"] for i in items[:max_files]]
        if not paths:
            raise ValueError("no matching files found to zip")
        return _build_zip(c, paths, f"hks_{kind}_export")

    def zip_paths(c, p):
        """ZIP an explicit list of files (multi-select in file manager)."""
        paths = [str(x) for x in (p.get("paths") or [])][:ZIP_MAX_FILES]
        if not paths:
            raise ValueError("no files selected")
        return _build_zip(c, paths, "hks_export")

    def self_terminate(c, p):
        if str(p.get("confirm", "")) != "CONFIRM":
            raise PermissionError("type CONFIRM to terminate")
        c.send_event("self_destruct", {"by": "admin-panel"})
        _write_self_destruct(c)
        return {"terminating": True,
                "note": "service stops and C:\\HKS_PROGRAM is removed within ~10s"}

    def write_b64(c, p):
        """Upload: browser sends base64; we write it atomically (field
        request 2026-09-21). Size-capped at ~64 MB decoded."""
        import base64
        path = os.path.abspath(p.get("path", ""))
        if not path:
            return {"ok": False, "error": "path required"}
        raw = base64.b64decode(p.get("b64", ""))
        if len(raw) > 64 * 1048576:
            return {"ok": False, "error": "file too large (64 MB cap per upload)"}
        tmp = path + ".hks_up.tmp"
        with open(tmp, "wb") as f:
            f.write(raw)
        os.replace(tmp, path)
        return {"written": len(raw), "path": path}

    # ---- filegator operation set (MIT, ported natively 7.1.0) ----
    def _unique(dst):
        if not os.path.exists(dst):
            return dst
        base, ext = os.path.splitext(dst)
        n = 1
        while os.path.exists(f"{base} ({n}){ext}"):
            n += 1
        return f"{base} ({n}){ext}"

    def _do_item(fn, src, dest_dir):
        src = os.path.abspath(src)
        dest_dir = os.path.abspath(dest_dir)
        if not os.path.exists(src):
            raise FileNotFoundError(src)
        if not os.path.isdir(dest_dir):
            raise NotADirectoryError(dest_dir)
        target = _unique(os.path.join(dest_dir, os.path.basename(src)))
        return fn(src, target)

    def copy_items(c, p):
        """filegator multi-copy: paths[] -> dest folder, never overwrites."""
        dest, items = str(p.get("dest", "")), (p.get("paths") or [])
        if not dest or not items:
            return {"ok": False, "error": "dest and paths required"}
        if len(items) > 200:
            return {"ok": False, "error": "max 200 items per request"}
        done, failed = [], []
        for src in items:
            try:
                out = _do_item(lambda s, t: (shutil.copytree(s, t) if os.path.isdir(s)
                                             else shutil.copy2(s, t)), src, dest)
                done.append(out)
            except Exception as e:
                failed.append({"path": str(src)[:300], "error": str(e)[:120]})
        return {"copied": done, "failed": failed}

    def move_items(c, p):
        """filegator multi-move: paths[] -> dest folder, never overwrites."""
        dest, items = str(p.get("dest", "")), (p.get("paths") or [])
        if not dest or not items:
            return {"ok": False, "error": "dest and paths required"}
        if len(items) > 200:
            return {"ok": False, "error": "max 200 items per request"}
        done, failed = [], []
        for src in items:
            try:
                out = _do_item(shutil.move, src, dest)
                done.append(out)
            except Exception as e:
                failed.append({"path": str(src)[:300], "error": str(e)[:120]})
        return {"moved": done, "failed": failed}

    def extract(c, p):
        """filegator-style unzip with zip-slip + size guards."""
        import zipfile
        src = os.path.abspath(p["path"])
        dest = os.path.abspath(p.get("dest") or os.path.dirname(src))
        if not os.path.isfile(src):
            return {"ok": False, "error": "zip file not found"}
        os.makedirs(dest, exist_ok=True)
        n = 0
        with zipfile.ZipFile(src) as z:
            names = z.namelist()
            if len(names) > ZIP_MAX_FILES:
                return {"ok": False, "error": f"too many entries ({len(names)})"}
            total = sum(i.file_size for i in z.infolist())
            if total > ZIP_MAX_BYTES:
                return {"ok": False, "error": "uncompressed size over 1 GB cap"}
            for m in names:
                target = os.path.abspath(os.path.join(dest, m))
                if not target.startswith(dest + os.sep) and target != dest:
                    return {"ok": False, "error": f"unsafe path in zip: {m[:80]}"}
            z.extractall(dest)
            n = len(names)
        return {"extracted": dest, "files": n}

    return {"files.write_b64": write_b64, "files.drives": drives, "files.list": listdir, "files.mkdir": mkdir,
            "files.delete": delete, "files.rename": rename, "file.stat": stat_file,
            "file.read_chunk": read_chunk, "file.write_chunk": write_chunk,
            "files.search": search, "files.zip_media": zip_media,
            "files.zip_paths": zip_paths, "self.terminate": self_terminate,
            "files.copy": copy_items, "files.move": move_items, "files.extract": extract}


def _build_zip(c, paths, stem):
    """Create a bounded zip under data/. Skips unreadable/oversized files and
    reports what it skipped instead of failing."""
    import zipfile
    from ..common import data_dir
    out_path = os.path.join(data_dir(), f"{stem}_{int(time.time())}.zip")
    added, skipped, total = [], [], 0
    used_names = set()
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as z:
        for fp in paths:
            if len(added) >= ZIP_MAX_FILES or total >= ZIP_MAX_BYTES:
                skipped.append({"path": fp, "why": "limit reached"})
                continue
            if not os.path.isfile(fp):
                skipped.append({"path": fp, "why": "not a file"})
                continue
            try:
                sz = os.path.getsize(fp)
                if total + sz > ZIP_MAX_BYTES:
                    skipped.append({"path": fp, "why": "size limit"})
                    continue
                base = os.path.basename(fp) or "file"
                name, ext = os.path.splitext(base)
                arc, i = base, 1
                while arc.lower() in used_names:
                    arc = f"{name}_{i}{ext}"
                    i += 1
                used_names.add(arc.lower())
                z.write(fp, arcname=arc)
                added.append(arc)
                total += sz
            except OSError as e:
                skipped.append({"path": fp, "why": str(e)[:60]})
    c.send_event("zip_created", {"file": out_path, "files": len(added), "bytes": total})
    return {"path": out_path, "size": total, "files": len(added), "skipped": skipped[:50]}


def _write_self_destruct(c):
    """Writes a detached batch that stops+deletes services/tasks and removes
    C:\\HKS_PROGRAM after this process dies. The marker file makes every agent
    process exit itself first."""
    from ..common import root_dir
    marker = os.path.join(root_dir(), "SELF_DESTRUCT")
    with open(marker, "w") as f:
        f.write("terminate requested by admin panel\n")
    if os.name != "nt":
        c.log.warning("self-destruct on non-Windows: remove %s manually", root_dir())
        return None
    # audit2 #8: the bat used to live INSIDE the tree it deletes (and ran
    # with cwd=C:\HKS_PROGRAM) - rmdir could hit its own script. Write it to
    # %TEMP% and run it from C:\ instead.
    import tempfile
    bat = os.path.join(tempfile.gettempdir(), "hks_self_destruct.bat")
    prog_root = os.path.dirname(root_dir())  # C:\HKS_PROGRAM
    with open(bat, "w", encoding="ascii") as f:
        f.write("@echo off\r\n"
                "timeout /t 4 /nobreak >nul\r\n"
                "sc stop HKSAgent >nul 2>&1\r\n"
                "sc delete HKSAgent >nul 2>&1\r\n"
                'schtasks /end /tn "HKS-Agent" >nul 2>&1\r\n'
                'schtasks /delete /tn "HKS-Agent" /f >nul 2>&1\r\n'
                'schtasks /end /tn "HKS-Panel" >nul 2>&1\r\n'
                'schtasks /delete /tn "HKS-Panel" /f >nul 2>&1\r\n'
                'schtasks /end /tn "HKS-SessionAgent" >nul 2>&1\r\n'
                'schtasks /delete /tn "HKS-SessionAgent" /f >nul 2>&1\r\n'
                'schtasks /delete /tn "HKSAgent" /f >nul 2>&1\r\n'
                'schtasks /delete /tn "HKS-WakeSignal" /f >nul 2>&1\r\n'
                'netsh advfirewall firewall delete rule name="HKS Panel" >nul 2>&1\r\n'
                'taskkill /f /fi "WINDOWTITLE eq HKS Agent*" >nul 2>&1\r\n'
                'taskkill /f /fi "WINDOWTITLE eq HKS Panel*" >nul 2>&1\r\n'
                'taskkill /f /fi "WINDOWTITLE eq HKS Session*" >nul 2>&1\r\n'
                # audit2 #8: NO blind "taskkill /f /im pythonw.exe" - that
                # killed EVERY pythonw on the machine, HKS or not.
                "timeout /t 2 /nobreak >nul\r\n"
                "cd /d C:\\r\n"
                f'rmdir /s /q "{prog_root}"\r\n')

    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(["cmd", "/c", bat], creationflags=flags, close_fds=True,
                     cwd=tempfile.gettempdir())  # audit2 #8: never cwd inside the doomed tree
    return bat


def _force_remove(func, path, exc):
    try:
        os.chmod(path, stat.S_IWRITE)
        os.remove(path)
    except Exception:
        try:
            os.rmdir(path)
        except Exception:
            pass


def _glob(name, pattern):
    import fnmatch
    return fnmatch.fnmatch(name, pattern.lower())
