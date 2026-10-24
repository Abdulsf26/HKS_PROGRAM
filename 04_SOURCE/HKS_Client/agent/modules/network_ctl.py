"""Network control: adapters, Wi-Fi scan/connect, internet check."""
import os
import re
import socket
import time


def register(ctx):
    def adapters(c, p):
        import psutil
        stats = psutil.net_if_stats()
        addrs = psutil.net_if_addrs()
        items = []
        for name, st in stats.items():
            ip = mac = ""
            for a in addrs.get(name, []):
                if a.family.name == "AF_INET" and not a.address.startswith("127."):
                    ip = a.address
                if a.family.name == "AF_LINK":
                    mac = a.address
            items.append({"name": name, "up": bool(st.isup), "ip": ip, "mac": mac,
                          "speed": st.speed})
        return {"items": items}

    def net_enable(c, p):
        name = p["name"]
        out = os_run(f'netsh interface set interface name="{_safe(name)}" admin=enable')
        c.send_event("network_change", {"adapter": name, "enabled": True})
        return {"output": out}

    def net_disable(c, p):
        name = p["name"]
        out = os_run(f'netsh interface set interface name="{_safe(name)}" admin=disable')
        c.send_event("network_change", {"adapter": name, "disabled": True})
        return {"output": out}

    def wifi_scan(c, p):
        out = os_run("netsh wlan show networks mode=bssid")
        nets, seen = [], set()
        cur = None
        for line in out.splitlines():
            if "SSID" in line and "BSSID" not in line and ":" in line:
                ssid = line.split(":", 1)[1].strip()
                if ssid:
                    cur = {"ssid": ssid, "signal": "", "auth": ""}
                    if ssid not in seen:
                        nets.append(cur)
                        seen.add(ssid)
            elif cur is not None:
                if "Signal" in line:
                    cur["signal"] = line.split(":", 1)[1].strip()
                if "Authentication" in line:
                    cur["auth"] = line.split(":", 1)[1].strip()
        return {"items": nets}

    def wifi_profiles(c, p):
        out = os_run("netsh wlan show profiles")
        items = [{"ssid": m} for m in re.findall(r":\s*(.+)$", out, re.M) if "All User Profile" not in m and m.strip()]
        # netsh output format: "All User Profile : SSID"
        items = []
        for line in out.splitlines():
            if "All User Profile" in line or "所有用户配置文件" in line:
                items.append({"ssid": line.split(":", 1)[1].strip()})
        return {"items": items}

    def wifi_connect(c, p):
        ssid, password = p["ssid"], p.get("password") or ""
        if password:
            xml = _wlan_profile_xml(ssid, password)
            prof_dir = os.path.join(os.environ.get("PROGRAMDATA", "."), "HKS")
            os.makedirs(prof_dir, exist_ok=True)
            pf = os.path.join(prof_dir, "hks_wlan.xml")
            with open(pf, "w", encoding="utf-8") as f:
                f.write(xml)
            os_run(f'netsh wlan add profile filename="{pf}" user=all')
            try:
                os.remove(pf)
            except OSError:
                pass
        out = os_run(f'netsh wlan connect name="{ssid}"')
        c.send_event("network_change", {"wifi_connect": ssid})
        return {"output": out}

    def wifi_disconnect(c, p):
        out = os_run("netsh wlan disconnect")
        c.send_event("network_change", {"wifi_disconnect": True})
        return {"output": out}

    def net_usage(c, p):
        import psutil
        n = psutil.net_io_counters()
        return {"sent_since_boot": n.bytes_sent, "recv_since_boot": n.bytes_recv,
                "boot_time": psutil.boot_time()}

    def internet_check(c, p):
        t0 = time.time()
        try:
            s = socket.create_connection(("1.1.1.1", 443), timeout=5)
            s.close()
            return {"online": True, "latency_ms": round((time.time() - t0) * 1000)}
        except OSError:
            return {"online": False, "latency_ms": None}

    def network_addresses(c, p):
        """Usable IPv4 addresses + a stable primary LAN address.

        Skip APIPA and host-only/loopback adapters so a laptop's phone link
        does not point at an unreachable 169.254.x.x or VM address.
        """
        import psutil
        ips = []
        for name, addrs in psutil.net_if_addrs().items():
            lname = name.casefold()
            if any(marker in lname for marker in
                   ("bluetooth", "npcap", "loopback", "virtualbox", "vmware",
                    "hyper-v", "local area connection*")):
                continue
            for a in addrs:
                if (a.family == socket.AF_INET and a.address != "127.0.0.1"
                        and not a.address.startswith("169.254.")):
                    ips.append({"iface": name, "ip": a.address})
        ips.sort(key=lambda x: (
            not x["iface"].casefold().startswith(("wi", "以太")),
            not x["iface"].casefold().startswith(("eth",)),
            x["iface"].casefold()))
        primary = ips[0]["ip"] if ips else None
        prev = getattr(c, "last_primary_ip", None)
        if prev and primary and prev != primary:
            c.send_event("ip_changed", {"old": prev, "new": primary,
                                        "note": "laptop IP changed - reconnect with the new address"})
        if primary:
            c.last_primary_ip = primary
        return {"addresses": ips, "primary": primary}


    return {"net.adapters": adapters, "net.enable": net_enable, "net.disable": net_disable,
            "net.wifi_scan": wifi_scan, "net.wifi_profiles": wifi_profiles,
            "net.wifi_connect": wifi_connect, "net.wifi_disconnect": wifi_disconnect,
            "net.internet_check": internet_check, "net.usage": net_usage,
            "network.addresses": network_addresses}


def _safe(v):
    """round 61 (audit4 #7): values interpolated into netsh shell commands
    must never carry cmd metacharacters (admin-context injection guard)."""
    import re as _re
    return _re.sub(r"[^A-Za-z0-9 ._\-()]", "", str(v))


def os_run(cmd, timeout=40):
    import subprocess
    p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout,
                       errors="replace",
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return ((p.stdout or "") + (p.stderr or ""))[:20000]


def _wlan_profile_xml(ssid, password):
    # Build XML with an XML library: SSIDs/passwords may contain ampersands,
    # quotes or angle brackets, all of which must be escaped as text nodes.
    import xml.etree.ElementTree as ET
    ns = "http://www.microsoft.com/networking/WLAN/profile/v1"
    ET.register_namespace("", ns)
    def node(parent, name, value=None):
        child = ET.SubElement(parent, "{%s}%s" % (ns, name))
        if value is not None:
            child.text = str(value)
        return child

    root = ET.Element("{%s}WLANProfile" % ns)
    node(root, "name", ssid)
    ssid_config = node(root, "SSIDConfig")
    ssid_node = node(ssid_config, "SSID")
    node(ssid_node, "name", ssid)
    node(root, "connectionType", "ESS")
    node(root, "connectionMode", "auto")
    msm = node(root, "MSM")
    security = node(msm, "security")
    auth = node(security, "authEncryption")
    node(auth, "authentication", "WPA2PSK")
    node(auth, "encryption", "AES")
    node(auth, "useOneX", "false")
    shared = node(security, "sharedKey")
    node(shared, "keyType", "passPhrase")
    node(shared, "protected", "false")
    node(shared, "keyMaterial", password)
    return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding="unicode")
