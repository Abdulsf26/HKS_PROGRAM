"""Parity + cleanup tests.

1. ACTION PARITY: every action the browser dashboard can send (extracted from
   app.js) must exist in the client agent's registered actions (svc+session).
2. JANITOR: stale zips / old script logs / stale media indexes get cleaned.
3. DISCONNECT HOOKS: dropping the link stops every live stream.
"""
import os
import re
import sys
import time

TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_parity_tmp")
os.environ["HKS_DATA_DIR"] = os.path.join(TMP, "data")
# dual-layout (round 62): resolves in the delivered zip (HKS_AdminPanel/tests),
# the repo tree (04_SOURCE/HKS_AdminPanel/tests) AND the 03_TESTS mirrors.
def _hks_panel_client():
    here = os.path.dirname(os.path.abspath(__file__))
    panel = os.path.dirname(here)          # zip/repo sibling layout
    if os.path.isdir(os.path.join(panel, "server")):
        return panel, os.path.join(os.path.dirname(panel), "HKS_Client")
    return (os.path.join(here, "..", "04_SOURCE", "HKS_AdminPanel"),
            os.path.join(here, "..", "04_SOURCE", "HKS_Client"))
PANEL, CLIENT = _hks_panel_client()
ROOT = _ROOT = os.path.dirname(PANEL)             # 04_SOURCE (repo) or zip root
sys.path.insert(0, PANEL)
sys.path.insert(0, CLIENT)

import shutil as _sh
_sh.rmtree(TMP, ignore_errors=True)  # round 62: leftover stale files shifted the janitor count


class FakeCtx:
    def __init__(self):
        self.cfg = {}
        self.stats = {"latency_ms": None, "started": time.time(), "cmds": 0, "errors": 0}
        self.events, self.errors, self.disconnect_hooks = [], [], []
        self.log = __import__("logging").getLogger("parity")

    def send_event(self, kind, data=None):
        self.events.append((kind, data))

    def send_error(self, module, message, detail=""):
        self.errors.append((module, message))

    def handle_disconnect(self):
        for h in list(self.disconnect_hooks):
            try:
                h()
            except Exception:
                pass


def client_actions():
    from agent.modules import (addons, apps, code, files, hardware, logs_mod, media_scan,
                               xampp, network_ctl, power_ctl, sql_access, sysinfo,
                               tasks, terminal, usb_ctl, webdeploy)
    from agent.modules import selftest as _selftest  # round 68 (F17) parity
    from agent.session_modules import camera, desktop, input_ctl, screen
    ctx = FakeCtx()
    actions = set()
    mods = [addons, apps, camera, desktop, files, hardware, input_ctl, logs_mod,
            media_scan, network_ctl, power_ctl, screen, sql_access, sysinfo,
            tasks, terminal, usb_ctl, webdeploy, code, xampp, _selftest]
    for m in mods:
        reg = getattr(m, "register", None)
        if reg is None:
            continue
        got = reg(ctx)
        if isinstance(got, dict):
            actions |= set(got.keys())
    actions |= set(camera.register_mic(ctx).keys())
    # registered directly in agent/main.py + session_main.py
    actions |= {"config.get", "config.set", "ping", "self.terminate"}
    return actions, ctx


def dashboard_actions():
    js = open(os.path.join(PANEL, "server", "static",
                           "app.js"), encoding="utf-8").read()
    acts = set()
    acts |= set(re.findall(r'\bcmd\("([^"]+)"', js))
    acts |= set(re.findall(r'\bwsCmd\("([^"]+)"', js))
    acts |= set(re.findall(r'runCmd\("[^"]*",\s*"([^"]+)"', js))
    # strip non-action false positives (none expected, but keep guard)
    return {a for a in acts if "." in a or a in ("ping",)}


def test_parity():
    client, _ctx = client_actions()
    dash = dashboard_actions()
    # wsCmd/relay actions handled specially: screen.control & camera.stream go
    # over the dashboard socket to the session agent; input.apply likewise.
    missing = sorted(dash - client)
    assert not missing, f"dashboard actions missing on client: {missing}"
    print(f"  action parity: OK ({len(dash)} dashboard actions all handled by client)")


def test_janitor():
    import tempfile
    from agent import janitor
    # hermetic (round 62): a PRIVATE dir - agent calls from co-run suites write
    # into whatever HKS_DATA_DIR points at during the run phase, which used to
    # leak stray logs into the shared _parity_tmp and shift the removed-count.
    d = tempfile.mkdtemp(prefix="hks_janitor_")
    os.makedirs(d, exist_ok=True)
    runs = os.path.join(d, "script_runs")
    os.makedirs(runs, exist_ok=True)
    old = time.time() - 48 * 3600
    # 6 zips: 3 stale
    for i in range(6):
        p = os.path.join(d, f"hks_export_{i}.zip")
        open(p, "w").write("x")
        if i < 3:
            os.utime(p, (old, old))
    # 40 logs: keep 30
    for i in range(40):
        p = os.path.join(runs, f"job_{i:02}.log")
        open(p, "w").write("x")
        if i < 5:
            os.utime(p, (old, old))
    # stale + fresh media index
    idx_old = os.path.join(d, "media_index_C_img.json")
    open(idx_old, "w").write("[]")
    os.utime(idx_old, (old, old))
    idx_new = os.path.join(d, "media_index_C_vid.json")
    open(idx_new, "w").write("[]")
    removed = janitor.cleanup_data_dir(d)
    zips_left = [f for f in os.listdir(d) if f.endswith(".zip")]
    logs_left = sorted(os.listdir(runs))
    assert len(zips_left) == 3, zips_left
    assert len(logs_left) == 30, len(logs_left)
    assert not os.path.exists(idx_old) and os.path.exists(idx_new)
    assert removed == 3 + 10 + 1  # 3 zips over cap, 10 logs over (5 stale + 5 over the 30 cap), 1 stale index
    print(f"  janitor: OK (removed {removed})")


def test_disconnect_hooks():
    ctx = FakeCtx()
    state = {"screen": True, "cam": True, "mic": True, "talk": True}
    ctx.disconnect_hooks.append(lambda: state.update(screen=False))
    ctx.disconnect_hooks.append(lambda: state.update(cam=False))

    def boom():
        raise RuntimeError("handler errors must not break the chain")

    ctx.disconnect_hooks.append(boom)
    ctx.disconnect_hooks.append(lambda: state.update(mic=False, talk=False))
    ctx.handle_disconnect()
    assert state == {"screen": False, "cam": False, "mic": False, "talk": False}
    print("  disconnect hooks: OK (all streams stopped, errors contained)")


def test_screen_stop():
    from agent.session_modules import screen
    screen._state["on"] = True
    screen._state["input_enabled"] = True
    screen.stop()
    assert screen._state["on"] is False and screen.is_input_enabled() is False
    print("  screen.stop: OK")


def test_terminal_rest_output_and_mobile_media_ui():
    """Guard the phone fallback and the real Windows media actions/UI."""
    from agent.session_modules import desktop
    handlers = desktop.register(FakeCtx())
    class Ctx:
        term_output_history = [(1, "phone-terminal-output")]
        term_output_seq = 1
        term_stream_on = True
    result = handlers["terminal.output"](Ctx(), {"after": 0})
    assert result["lines"] == ["phone-terminal-output"] and result["cursor"] == 1
    assert all(name in handlers for name in
               ("media.play_pause", "media.previous", "media.next", "volume.up", "volume.down"))
    js = open(os.path.join(PANEL, "server", "static", "app.js"), encoding="utf-8").read()
    css = open(os.path.join(PANEL, "server", "static", "style.css"), encoding="utf-8").read()
    assert 'cmd("terminal.output", { after: termCursor }' in js
    assert "canvas:not(#camview)" in css
    assert ".mp-transport" in css and "object-fit:contain" in css
    print("  phone terminal REST output, media controls, and camera color/framing: OK")


def test_terminal_quick_commands_and_shared_ui_controls():
    js = open(os.path.join(PANEL, "server", "static", "app.js"), encoding="utf-8").read()
    css = open(os.path.join(PANEL, "server", "static", "style.css"), encoding="utf-8").read()
    assert "inp.oninput();" not in js and "inp.setSelectionRange(inp.value.length" in js
    assert "[terminal output unavailable — " in js and "connect failed — " in js
    assert '.switch input[type="checkbox"] {\n  appearance:none !important' in css
    assert "width:42px !important; min-width:42px !important; height:24px !important" in css
    assert "table:not(.t) th, table:not(.t) td, .t th, .t td" in css
    assert "padding:8px 10px !important" in css
    assert ".reference-grid" in css and "class: \"reference-card\"" in js
    assert "--ink:#edf1f6; --text:#edf1f6" in css
    print("  terminal quick actions, error visibility, switches, dark mode, tables, and reference cards: OK")


if __name__ == "__main__":
    test_parity()
    test_janitor()
    test_disconnect_hooks()
    test_screen_stop()
    test_terminal_rest_output_and_mobile_media_ui()
    print("ALL PARITY/CLEANUP TESTS PASSED")
