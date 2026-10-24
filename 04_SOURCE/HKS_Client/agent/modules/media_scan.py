"""Media indexer — images / videos / documents across a drive, cached."""
import os
import time

EXTS = {
    "img": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".heic", ".tif", ".tiff", ".ico"},
    "vid": {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".mpg", ".mpeg", ".3gp"},
    "doc": {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".rtf",
            ".odt", ".csv", ".md", ".one", ".epub"},
    "aud": {".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg", ".wma", ".aiff", ".opus"},
}
CACHE_TTL = 3600  # rescan hourly at most


def _cache_file(kind, root):
    from ..common import data_dir
    safe_root = root.replace(":", "").replace("\\", "_").replace("/", "_").strip("_") or "drive"
    return os.path.join(data_dir(), f"media_index_{safe_root}_{kind}.json")


def scan_drive(kind, root, force=False):
    import json
    cf = _cache_file(kind, root)
    if not force and os.path.exists(cf) and time.time() - os.path.getmtime(cf) < CACHE_TTL:
        try:
            with open(cf, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    exts = EXTS.get(kind)
    if exts is None:
        raise ValueError("unsupported media kind; use img, vid, doc or aud")
    items = []
    skip = {"$recycle.bin", "system volume information", "windows.old", "node_modules",
            "__pycache__", "winsxs", "servicing", "windowsapps"}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d.lower() not in skip][:40]
        for fn in filenames:
            if os.path.splitext(fn)[1].lower() in exts:
                fp = os.path.join(dirpath, fn)
                try:
                    st = os.stat(fp)
                    items.append({"path": fp, "size": st.st_size, "mtime": st.st_mtime})
                except OSError:
                    pass
    items.sort(key=lambda x: -x["mtime"])
    try:
        os.makedirs(os.path.dirname(cf), exist_ok=True)
        with open(cf, "w", encoding="utf-8") as f:
            json.dump(items, f)
    except Exception:
        pass
    return items


def register(ctx):
    def media_scan(c, p):
        kind = p.get("kind", "img")
        root = (p.get("path") or "C:\\").rstrip("\\") + "\\"
        page = max(0, int(p.get("page", 0)))
        per = min(int(p.get("per", 120)), 500)
        q = (p.get("q") or "").lower()
        items = scan_drive(kind, root)
        if q:
            items = [i for i in items if q in i["path"].lower()]
        start = page * per
        return {"total": len(items), "page": page, "items": items[start:start + per]}

    def media_thumb(c, p):
        path = str(p.get("path") or "")
        if not path:
            return {"ok": False, "error": "Select a media file first."}
        if not os.path.isfile(path):
            return {"ok": False, "error": "This file was moved or deleted. Refresh the media list and try again."}
        from PIL import Image
        import base64
        import io
        try:
            with Image.open(path) as source:
                source.thumbnail((320, 320))
                buf = io.BytesIO()
                source.convert("RGB").save(buf, "JPEG", quality=60)
                return {"b64": base64.b64encode(buf.getvalue()).decode()}
        except FileNotFoundError:
            return {"ok": False, "error": "This file was moved or deleted. Refresh the media list and try again."}
        except PermissionError:
            return {"ok": False, "error": "Windows denied access to this media file."}
        except OSError as exc:
            return {"ok": False, "error": f"This file cannot be previewed: {exc}"[:220]}

    return {"media.scan": media_scan, "media.thumb": media_thumb}
