#!/usr/bin/env python3
"""Audit round 5 (PDF security review R01-R20) - regression needles.
Run: python3 03_TESTS/test_review5_fixes.py
"""
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "04_SOURCE")
FAILS = []


def has(rel, needle, must=True, label=""):
    s = open(os.path.join(ROOT, rel), encoding="utf-8", errors="replace").read()
    ok = (needle in s) if must else (needle not in s)
    if not ok:
        FAILS.append(f"{rel}: {'missing' if must else 'still has'} :: {label or needle[:56]}")


M = "HKS_AdminPanel/server/main.py"
# R01 observer WS policy
has(M, 'if t in ("input", "cmd") and not is_admin:', label="R1 observer cmd/input blocked")
has(M, "observer is read-only - admin role needed", label="R1 clear error text")
has(M, 'if data and data[0] == 4 and not is_admin:', label="R1 voice channel admin-only")
# R02 origin
has(M, "def _origin_ok(ws) -> bool:", label="R2 origin guard exists")
has(M, "if not _origin_ok(ws):  # R02: cross-site WS hijack stops here", label="R2 dash guard")
has(M, "if not _origin_ok(ws):  # R02", label="R2 agent guard")
# R04 revocation closes live WS
has(M, "async def _kick_dashboards(token", label="R4 kick helper")
has(M, 'await _kick_dashboards(user["token"])  # R04', label="R4 logout kicks")
has(M, 'await _kick_dashboards(r["token"])  # R4', must=False, label="sanity")
has(M, 'await _kick_dashboards(r["token"])  # R04', label="R4 revoke kicks")
has(M, "if not getattr(d, \"tok\", \"\") or not db.get_session(d.tok):", label="R4 liveness revalidate")
has(M, "self.tok = tok  # session token bound to this socket (R04)", label="R4 conn stores token")
# R03-lite machine lock
has(M, 'lock = os.environ.get("HKS_MACHINE_LOCK", "").strip()', label="R3 machine lock opt-in")
# R15-lite proxy toggle
has(M, 'os.environ.get("HKS_TRUST_PROXY", "0") == "1"', label="R15 trust-proxy toggle")  # round 61: default flipped to DISTRUST
# R16 rehash wired
has(M, "security.maybe_rehash(password, user[\"pw_hash\"])", label="R16 rehash on login")
has(M, 'db.set_user_password(user["id"], _up)', label="R16 rehash saved")

S = "HKS_AdminPanel/server/security.py"
has(S, "PBKDF2_ITER = 600_000", label="R16 600k iterations")
has(S, "def maybe_rehash(password: str, stored: str):", label="R16 migration helper")

C = "HKS_Client/agent/connection.py"
has(C, "if cfg.get(\"cert_pin\"):\n            raise", label="R5 pin fail-closed")

W = "HKS_Client/agent/modules/webdeploy.py"
has(W, "gate = None  # R13: when set, only /<gate>/... paths are served", label="R13 gate")
has(W, "tok = _secrets.token_urlsafe(12)  # R13: random URL path gate", label="R13 random gate")
has(W, '"url": f"http://{ip if ip != \'0.0.0.0\' else _lan_ip()}:{port}/{tok}/"', label="R13 gate in URL")

FB = "HKS_ADDONS/ADDON_FILEBROWSER.bat"
has(FB, "Demo@1234", must=False, label="R8 no hard-coded fb password")
has(FB, "Get-Random -Count 20", label="R8 random fb password")
has(FB, "credentials.txt", label="R8 credentials saved")
DG = "HKS_ADDONS/ADDON_DBGATE.bat"
has(DG, "Demo@1234", must=False, label="R8 no hard-coded dbgate password")
has(DG, "-e LOGINS=admin:%HKSPW%", label="R8 dbgate random login")

# docs tell the truth now
SEC = "02_DOCS/SECURITY.md"
S2 = os.path.join(os.path.dirname(ROOT), SEC)
stext = open(S2, encoding="utf-8").read()
for needle in ("600k", "R01 observer WS bypass | **FIXED", "R05 pin fails open | **FIXED"):
    if needle not in stext:
        FAILS.append(f"SECURITY.md missing :: {needle}")

print(f"checks failed: {len(FAILS)}")
for f in FAILS:
    print("  FAIL:", f)
sys.exit(1 if FAILS else 0)
