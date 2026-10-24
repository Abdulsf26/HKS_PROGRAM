"""Generate a self-signed TLS cert for the panel (or use a real one from
Let's Encrypt / your CA for production)."""
import datetime
import os
import socket
import subprocess
import sys

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "certs")


def _san_ext():
    parts = []
    for i, t in enumerate(_san_targets()):
        parts.append(("DNS:" if _looks_like_host(t) else "IP:") + t)
    return ",".join(parts)


def _looks_like_host(t):
    import re
    return re.match(r"^\d{1,3}(\.\d{1,3}){3}$", t) is None


def _san_targets():
    """Real SAN targets: hostname + every LAN IPv4 we can find.
    Historical bug (plan §1): certs with ONLY localhost/127.0.0.1 SANs are
    unusable from a phone at http://<lan-ip> — HTTPS mismatch on PTT."""
    targets = ["localhost", "127.0.0.1"]
    try:
        hn = os.environ.get("COMPUTERNAME") or socket.gethostname()
        if hn:
            targets.append(hn.lower())
    except Exception:
        pass
    ips = set()
    try:
        import socket as _s
        for info in _s.getaddrinfo(_s.gethostname(), None, _s.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127.") and not ip.startswith("169.254."):
                ips.add(ip)
    except Exception:
        pass
    try:
        import psutil
        for _nic, addrs in (psutil.net_if_addrs() or {}).items():
            for a in addrs:
                if a.family == socket.AF_INET and not a.address.startswith(("127.", "169.254.")):
                    ips.add(a.address)
    except Exception:
        pass
    targets.extend(sorted(ips))
    return targets


def main():
    os.makedirs(OUT, exist_ok=True)
    cert, key = os.path.join(OUT, "server.pem"), os.path.join(OUT, "key.pem")
    if os.path.exists(cert) and "--force" not in sys.argv:
        print("certs already exist:", cert, "(use --force to rebuild)")
        return
    try:
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", key, "-out", cert, "-days", "825",
            "-subj", "/CN=HKS-AdminPanel",
            "-addext", "subjectAltName=" + _san_ext(),
        ], check=True)
    except Exception:
        # no openssl binary - pure python fallback
        try:
            from cryptography import x509
        except ImportError:
            sys.exit("Install openssl or: pip install cryptography")
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        import ipaddress
        k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "HKS-AdminPanel")])
        cert_obj = (x509.CertificateBuilder()
                    .subject_name(name).issuer_name(name)
                    .public_key(k.public_key())
                    .serial_number(x509.random_serial_number())
                    .not_valid_before(datetime.datetime.utcnow())
                    .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=825))
                    .add_extension(x509.SubjectAlternativeName(
                        [x509.DNSName(t) if _looks_like_host(t)
                         else x509.IPAddress(ipaddress.ip_address(t)) for t in _san_targets()]),
                        critical=False)
                    .sign(k, hashes.SHA256()))
        with open(key, "wb") as f:
            f.write(k.private_bytes(serialization.Encoding.PEM,
                                    serialization.PrivateFormat.TraditionalOpenSSL,
                                    serialization.NoEncryption()))
        with open(cert, "wb") as f:
            f.write(cert_obj.public_bytes(serialization.Encoding.PEM))
    print("TLS ready:\n  cert:", cert, "\n  key :", key)
    print("Note: agents pin the cert fingerprint on first connect (TOFU).")


if __name__ == "__main__":
    main()
