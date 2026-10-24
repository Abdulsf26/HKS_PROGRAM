"""USB device control — enable/disable PnP devices, mass-storage kill switch.

Input devices (keyboard/mouse) are PROTECTED by default so the admin can't
lock themselves out of the physical machine.
"""

PROTECTED_CLASSES = {"Mouse", "Keyboard", "HIDClass"}


def register(ctx):
    def usb_list(c, p):
        out = _ps("Get-PnpDevice -PresentOnly | Where-Object {$_.InstanceId -like 'USB*'} | "
                  "Select-Object Status,Class,FriendlyName,InstanceId | ConvertTo-Json -Compress")
        items = _parse_devices(out)
        return {"items": items}

    def usb_device(c, p):
        inst = p["instance_id"]
        enable = bool(p.get("enable"))
        if not enable and _class_of(inst) in PROTECTED_CLASSES:
            raise PermissionError("refusing to disable an input device (keyboard/mouse/HID)")
        verb = "enable-device" if enable else "disable-device"
        out = _ps(f"pnputil /{verb} \"{inst}\"")
        c.send_event("usb_change", {"instance_id": inst, "enabled": enable})
        return {"output": out}

    def usb_storage_disable(c, p):
        disable = bool(p.get("disable", True))
        # USBSTOR class devices = mass storage; leaves keyboards/mice alone
        out = _ps("Get-PnpDevice -PresentOnly | Where-Object {$_.InstanceId -like 'USBSTOR*'} | "
                  "ForEach-Object { pnputil /%s \"$($_.InstanceId)\" }" %
                  ("disable-device" if disable else "enable-device"))
        c.send_event("usb_change", {"usb_storage_disabled": disable})
        return {"output": out[:8000]}

    def usb_ss_off(c, p):
        """Disable USB selective suspend — ports keep power (helps some heat/idle issues)."""
        out = _ps("powercfg /setacvalueindex SCHEME_CURRENT 2a737441-1930-4402-8d77-b2bebba308a3 "
                  "48e6b7a6-50f5-4782-a5d4-53bb8f07e226 0; "
                  "powercfg /setdcvalueindex SCHEME_CURRENT 2a737441-1930-4402-8d77-b2bebba308a3 "
                  "48e6b7a6-50f5-4782-a5d4-53bb8f07e226 0; powercfg /setactive SCHEME_CURRENT")
        return {"output": out, "note": "USB selective suspend OFF (AC+DC)"}

    return {"usb.list": usb_list, "usb.device": usb_device,
            "usb.storage_disable": usb_storage_disable, "usb.ss_off": usb_ss_off}


def _class_of(instance_id):
    """Resolve the PnP class of a device instance id via PowerShell.
    Returns 'Unknown' on any failure (never raises)."""
    inst = (instance_id or "").replace('"', "").replace("'", "").replace("`", "")
    if not inst:
        return "Unknown"
    try:
        out = _ps(f'(Get-PnpDevice -InstanceId "{inst}").Class', timeout=30)
    except Exception:
        return "Unknown"
    return (out or "").strip() or "Unknown"


def _ps(cmd, timeout=60):
    import subprocess
    p = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                       capture_output=True, text=True, timeout=timeout, errors="replace",
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return (p.stdout or "")


def _parse_devices(raw):
    import json
    try:
        data = json.loads(raw)
    except Exception:
        return []
    if isinstance(data, dict):
        data = [data]
    return [{"status": d.get("Status", "?"),
             "ok": d.get("Status") == "OK",
             "class": d.get("Class") or "Unknown",
             "name": (d.get("FriendlyName") or "?")[:80],
             "id": d.get("InstanceId", "")} for d in data]


def _is_protected(instance_id):
    upper = instance_id.upper()
    return any(k in upper for k in ("MI_01&COL01", "KEYBOARD", "MOUSE")) or \
        any(instance_id.startswith(p) for p in ("HID\\",))
