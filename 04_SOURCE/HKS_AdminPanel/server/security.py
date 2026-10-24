import os
import time
"""HKS Admin Panel - password hashing, tokens, rate limiting, bans."""
import hashlib
import hmac
import secrets
import time

from .db import db

PBKDF2_ITER = 600_000   # OWASP 2026 guidance (was 200k; R16)
PBKDF2_MIN = 600_000


def maybe_rehash(password: str, stored: str):
    """After a SUCCESSFUL verify: if the stored hash still uses the old
    200k work factor, return an upgraded hash (caller saves it). None =
    nothing to do. Login cost only, zero user-visible change."""
    try:
        _, iters, _salt, _want = stored.split("$")
        if int(iters) >= PBKDF2_MIN:
            return None
        return hash_password(password)
    except Exception:
        return None


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), PBKDF2_ITER)
    return f"pbkdf2_sha256${PBKDF2_ITER}${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iters, salt, want = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iters))
        return hmac.compare_digest(dk.hex(), want)
    except Exception:
        return False


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def new_csrf() -> str:
    return secrets.token_urlsafe(24)


def constant_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


class SlidingWindowLimiter:
    """Per-key sliding-window counter (thread-safe, bounded memory)."""

    def __init__(self, max_events: int, window_sec: float):
        self.max = max_events
        self.window = window_sec
        self._hits: dict[str, list] = {}

    def check(self, key: str):
        """Return (allowed, retry_after). Records the hit when allowed."""
        now = time.time()
        lst = [t for t in self._hits.get(key, []) if t > now - self.window]
        if len(lst) >= self.max:
            self._hits[key] = lst
            return False, int(lst[0] + self.window - now) + 1
        lst.append(now)
        self._hits[key] = lst
        # audit4 #S1: full-dict rescan at most every 100th call
        self._gc = getattr(self, "_gc", 0) + 1
        if self._gc % 100 == 0 and len(self._hits) >= 20000:
            cutoff = now - self.window
            self._hits = {k: v for k, v in self._hits.items() if v and v[-1] > cutoff}
        return True, 0

    def reset(self, key: str):
        self._hits.pop(key, None)


login_limiter = SlidingWindowLimiter(max_events=6, window_sec=300)       # 6 tries / 5 min
api_limiter = SlidingWindowLimiter(max_events=600, window_sec=60)        # 600 req / min (shared proxy IPs poll too)
ws_limiter = SlidingWindowLimiter(max_events=20, window_sec=60)


def register_failed_login(ip: str):
    """After repeated failures, ban the IP. Public IPs: 15 minutes.
    Private/proxy IPs (shared NAT, sandbox previews, LAN): 2 minutes - one
    misbehaving browser behind a shared egress must not lock everybody out
    for a quarter of an hour."""
    ok, _ = login_limiter.check("fail:" + ip)
    if not ok:
        secs = 900
        try:  # RFC1918/loopback -> shared NAT or a tunnel/preview proxy
            a, b, c, d = (ip.split(".") + ["0", "0", "0", "0"])[:4]
            a, b = int(a), int(b)
            if a == 10 or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168) or a == 127:
                secs = 120
        except Exception:
            pass
        db.add_ban(ip, time.time() + secs, "Too many failed logins")
        login_limiter.reset("fail:" + ip)


def is_banned(ip: str):
    row = db.get_ban(ip)
    if row:
        return int(row["until"] - time.time()) + 1
    return 0


# ---------------- round 68 (F24): TOTP 2FA, pure stdlib, no new deps --------
def totp_secret() -> str:
    import base64
    return base64.b32encode(os.urandom(10)).decode().rstrip("=")

def _totp_code(secret: str, step: int) -> str:
    import base64, hashlib, hmac, struct
    pad = "=" * ((8 - len(secret) % 8) % 8)
    key = base64.b32decode(secret.upper() + pad)
    msg = struct.pack(">Q", step)
    dig = hmac.new(key, msg, hashlib.sha1).digest()
    o = dig[-1] & 0x0F
    return f"{((struct.unpack('>I', dig[o:o+4])[0] & 0x7FFFFFFF) % 1000000):06d}"

def totp_verify(secret: str, code: str, window: int = 1) -> bool:
    # RFC 6238, 30 s step, +/-1 step drift. Constant-time compare.
    if not secret or not code:
        return False
    code = str(code).strip().replace(" ", "")
    if not code.isdigit() or len(code) != 6:
        return False
    step = int(time.time()) // 30
    for off in range(-window, window + 1):
        import hmac as _h
        if _h.compare_digest(_totp_code(secret, step + off), code):
            return True
    return False

def totp_uri(secret: str, username: str) -> str:
    from urllib.parse import quote
    return f"otpauth://totp/HKS:{quote(username)}?secret={secret}&issuer=HKS&period=30&digits=6"
