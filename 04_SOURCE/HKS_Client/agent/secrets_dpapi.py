"""HKS round 68 (F27): DPAPI-protected secrets (Windows), optional.

Opt-in: first_run --encrypt-token converts config.json's plaintext token into
token.bin encrypted with CryptProtectData (only the SAME Windows user can
decrypt it - a stolen file is useless on another machine or account).
Reads degrade safely: no token.bin -> callers keep using config.json.
"""
import os


def dpapi_available():
    return os.name == "nt"


def protect(data):
    import ctypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_int), ("buf", ctypes.c_char_p)]

    buf = ctypes.create_string_buffer(data, len(data))
    src = BLOB(len(data), ctypes.cast(buf, ctypes.c_char_p))
    out = BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(src), None,
                                                  None, None, None, 0,
                                                  ctypes.byref(out)):
        raise OSError("CryptProtectData failed")
    try:
        return ctypes.string_at(out.buf, out.cb)
    finally:
        ctypes.windll.kernel32.LocalFree(out.buf)


def unprotect(data):
    import ctypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_int), ("buf", ctypes.c_char_p)]

    buf = ctypes.create_string_buffer(data, len(data))
    src = BLOB(len(data), ctypes.cast(buf, ctypes.c_char_p))
    out = BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(src), None,
                                                    None, None, None, 0,
                                                    ctypes.byref(out)):
        raise OSError("CryptUnprotectData failed")
    try:
        return ctypes.string_at(out.buf, out.cb)
    finally:
        ctypes.windll.kernel32.LocalFree(out.buf)


def save_secret(path, plaintext):
    if not dpapi_available():
        raise OSError("DPAPI is Windows-only")
    with open(path, "wb") as f:
        f.write(protect(plaintext.encode()))


def load_secret(path):
    with open(path, "rb") as f:
        return unprotect(f.read()).decode()


def token_from(cfg_dir):
    """Returns the agent token from token.bin (DPAPI) or None."""
    p = os.path.join(cfg_dir, "token.bin")
    if dpapi_available() and os.path.exists(p):
        try:
            return load_secret(p)
        except Exception:
            return None
    return None
