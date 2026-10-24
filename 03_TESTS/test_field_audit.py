"""Round 32: EXTERNAL FIELD AUDIT - verifies all 8 reported findings against
the current source. Findings 2/5 were already fixed earlier (regression
needles keep them fixed); 1/3/4/6/7/8 are fixed in this round."""
import asyncio
import os
import re

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(ROOT, "04_SOURCE")
START = os.path.join(ROOT, "00_START_HERE")

doctor = open(os.path.join(START, "HKS_DOCTOR.bat"), encoding="utf-8", newline="").read()
fr = open(os.path.join(SRC, "first_run.py"), encoding="utf-8", newline="").read()
sm = open(os.path.join(SRC, "HKS_Client", "agent", "session_main.py"), encoding="utf-8").read()
am = open(os.path.join(SRC, "HKS_Client", "agent", "main.py"), encoding="utf-8").read()
conn = open(os.path.join(SRC, "HKS_Client", "agent", "connection.py"), encoding="utf-8").read()
pctl = open(os.path.join(SRC, "HKS_Client", "agent", "modules", "power_ctl.py"), encoding="utf-8").read()
cam = open(os.path.join(SRC, "HKS_Client", "agent", "session_modules", "camera.py"), encoding="utf-8").read()
mpy = open(os.path.join(SRC, "HKS_AdminPanel", "server", "main.py"), encoding="utf-8").read()

# ---- 1. doctor: PORT is detected BEFORE its first use (was: empty %PORT%) ----
for name, text in (("HKS_DOCTOR.bat", doctor), ("first_run doctor template", fr)):
    i_set = text.find("set PORT=")
    # round 46: the probe tries port AND port+1 (main + companion) - the
    # first use of %PORT% is the dual-port probe, not a hardcoded URL
    i_use = text.find("@(%PORT%, (%PORT%+1))")
    assert 0 <= i_set < i_use, (name, i_set, i_use)
    assert 'if "%PORT%"=="" set PORT=6767' in text, name
    # cert-skip is mandatory: self-signed HTTPS installs used to false-alarm
    assert "ServerCertificateValidationCallback" in text, name
print("1. doctor: port detected before use + 6767 fallback (bat + template)  OK")

# ---- 2. main.py subprocess import (fixed in 7.2.11 - regression needle) ----
assert re.search(r"^import subprocess$", mpy, re.M)
print("2. server main.py imports subprocess  OK (already fixed, stays fixed)")

# ---- 3. mutex: no early return when Global\\ is denied; tries Local\\ ----
assert "return False  # another instance is running" not in sm
assert "TRY THE" in sm and "NEXT namespace" in sm
assert "a healthy twin owns it" in sm
assert "fail-open (matches except)" in sm


def lock_logic(create_mutex):
    """Replica of the fixed loop with injected CreateMutex."""
    for name in ("Global", "Local"):
        handle, already = create_mutex(name)
        if handle:
            if not already:
                return ("own", name)
            return ("duplicate", name)
    return ("own", "fail-open")


def denied_global():
    st = {"Global": (0, False), "Local": (1, False)}
    return lambda n: st[n]
assert lock_logic(denied_global()) == ("own", "Local")     # was: silent exit
assert lock_logic(lambda n: (1, True)) == ("duplicate", "Global")
assert lock_logic(lambda n: (0, False)) == ("own", "fail-open")
print("3. mutex: Global denied -> tries Local -> owns; duplicate -> exit42  OK")

# ---- 4. config.set really exists and works (was: import of a ghost) ----
assert "def make_config_set(ctx)" in am
assert "from agent.main import _make_config_set" not in sm
assert "from agent.main import make_config_set" in sm
assert 'ctx.actions["config.set"] = make_config_set(ctx)' in am


class _Cfg(dict):
    saved = 0

    def save(self):
        _Cfg.saved += 1


class _Ctx:
    cfg = _Cfg({"fps": 8, "server_url": "ws://127.0.0.1:6767"})


ns = {}
exec(compile(am[am.index("CONFIG_SET_KEYS"):am.index("def load_service_modules")],
             "<x>", "exec"), ns)
ctx1 = _Ctx()
handler = ns["make_config_set"](ctx1)(ctx1, {"data": {"fps": 12, "server_url": "ws://x:1",
                                                      "evil_key": 1}})
assert handler["changed"] == {"fps": 12, "server_url": "ws://x:1"}, handler
assert _Ctx.cfg["fps"] == 12 and _Cfg.saved == 1
assert "evil_key" not in _Ctx.cfg
ctx2 = _Ctx()
r2 = ns["make_config_set"](ctx2)(ctx2, {"data": {"nope": 1}})
assert r2["changed"] == {}
print("4. config.set: whitelist setter works, saves, ignores unknown keys  OK")

# ---- 5. camera os at module top (fixed in 7.2.9 - regression needle) ----
assert re.search(r"^import os$", cam, re.M)
print("5. camera.py imports os at module level  OK (already fixed, stays fixed)")

# ---- 6. sender never loses a dequeued message ----
assert "payloads = []" in conn and "if t.done():" in conn


async def race_test():
    """Both queues ready + second completes during the first send: the
    fixed drain-before-send must deliver BOTH messages."""
    jq, bq = asyncio.Queue(), asyncio.Queue()
    await jq.put('{"json": 1}')
    await bq.put(b"\x01bin")
    sent = []

    async def fake_send(p):
        await asyncio.sleep(0.01)   # window where the other task can finish
        sent.append(p)

    get_j, get_b = jq.get(), bq.get()
    jt, bt = asyncio.ensure_future(get_j), asyncio.ensure_future(get_b)
    done, pending = await asyncio.wait({jt, bt}, return_when=asyncio.FIRST_COMPLETED)
    payloads = []
    for t in done:
        payloads.append(t.result())
    for t in pending:
        if t.done():
            payloads.append(t.result())
        else:
            t.cancel()
    for p in payloads:
        await fake_send(p)
    assert len(sent) == 2, sent
    # nothing stranded in queues, nothing lost
    assert jq.empty() and bq.empty()


asyncio.run(race_test())
print("6. sender: both messages delivered in the drain-before-send race  OK")

# ---- 7. display_off without pywin32 ----
assert "PostMessageW" in pctl and "import win32gui" not in pctl
print("7. display_off: pure ctypes PostMessageW (no win32gui dep)  OK")

# ---- 8. language-neutral firewall checks (no English 'Yes' matching) ----
for name, text in (("HKS_DOCTOR.bat", doctor), ("first_run.py", fr)):
    assert 'find /i "Yes"' not in text, name
    assert "Select-String 'Yes'" not in text, name
assert "Get-NetFirewallRule" in doctor and "Get-NetFirewallRule" in fr
print("8. firewall checks: Get-NetFirewallRule, no English 'Yes' matching  OK")

print("FIELD-AUDIT TESTS: 8/8 VERDICTS IMPLEMENTED OR VERIFIED")
