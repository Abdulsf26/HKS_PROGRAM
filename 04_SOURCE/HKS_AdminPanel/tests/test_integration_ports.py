

def _hks_root():
    # dual-layout (round 62): <root> = 04_SOURCE (repo) or delivered-zip root;
    # HKS_AdminPanel/HKS_Client are siblings of it in BOTH layouts
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
"""Round-18c integration-port regression tests (code ported from the shared repos).

Covers:
- screen.py cursor overlay (AnywhereInput port): marker actually drawn at the
  host cursor position, scaled + monitor-offset aware
- input_ctl.py MouseWorker safety (AnywhereInput port): button tracking,
  stale release, move rate cap
- hardware.py board/BIOS detail (Cores port): CIM parse into hw.deep fields
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(_hks_root(),
                                "HKS_Client"))
sys.path.insert(0, os.path.join(_hks_root(),
                                "HKS_AdminPanel"))

from agent.session_modules import screen as scr          # noqa: E402
from agent.session_modules import input_ctl as ict       # noqa: E402
from agent.modules import hardware as hw                 # noqa: E402


def test_cursor_overlay_drawn():
    from PIL import Image
    im = Image.new("RGB", (200, 100), (10, 10, 10))
    center_before = im.getpixel((100, 50))
    scr._host_cursor_pos = lambda: (100, 50)  # fake a Windows cursor
    scr._draw_host_cursor(im, {"left": 0, "top": 0}, (200, 100))
    assert im.getpixel((100, 50)) != center_before, "cursor marker not drawn at host pos"
    # marker must respect monitor offset + downscale factor
    im2 = Image.new("RGB", (100, 50), (10, 10, 10))  # half scale
    scr._host_cursor_pos = lambda: (100, 25)
    scr._draw_host_cursor(im2, {"left": 100, "top": 0}, (200, 100))  # phys (100,25) -> img (0,12)
    assert im2.getpixel((0, 12)) != (10, 10, 10), "offset/scale mapping wrong"
    # cursor outside the grabbed monitor -> nothing drawn, no crash
    im3 = Image.new("RGB", (100, 50), (10, 10, 10))
    scr._draw_host_cursor(im3, {"left": 500, "top": 500}, (200, 100))
    assert im3.getpixel((50, 25)) == (10, 10, 10)
    print("  screen: cursor overlay drawn + mapped + clipped OK")


def test_input_safety_ports():
    # Replace the pyautogui module with a recorder (real pyautogui needs a display)
    import types

    calls = []
    fake = types.ModuleType("pyautogui")
    fake.FAILSAFE = True
    fake.PAUSE = 1
    for fn in ("moveTo", "mouseDown", "mouseUp", "scroll", "hscroll",
               "keyDown", "keyUp", "typewrite", "hotkey"):
        setattr(fake, fn, (lambda f: lambda *a, **k: calls.append((f, a)))(fn))
    fake.size = lambda: (2000, 1000)
    ict._pg = fake
    ict._screen[0], ict._screen[1] = 2000, 1000
    ict._screen_ts[0] = time.time()
    ict._buttons_down.clear()
    ict._consec_fail[0] = 0
    ict._disabled.clear()

    class FakeCtx:
        def __init__(self):
            self.disconnect_hooks = []

        def send_error(self, *a, **k):
            pass

    scr.set_input_enabled(True)
    reg = ict.register(FakeCtx())
    # AnywhereInput protocol: normalized 0..1 in -> physical px out
    reg["input.apply"](FakeCtx(), {"t": "mm", "x": 0.5, "y": 0.25})
    reg["input.apply"](FakeCtx(), {"t": "md", "x": 0.1, "y": 0.1, "btn": 0})
    assert ict._wait_idle(3), "engine queue did not drain"
    time.sleep(0.05)
    assert ict._buttons_down, "press not tracked"
    reg["input.apply"](FakeCtx(), {"t": "mu", "x": 0.1, "y": 0.1, "btn": 0})
    reg["input.apply"](FakeCtx(), {"t": "mw", "dx": 0, "dy": -300})
    reg["input.apply"](FakeCtx(), {"t": "type", "text": "hi"})
    reg["input.apply"](FakeCtx(), {"t": "combo", "ctrl": True, "key": "a"})
    assert ict._wait_idle(3), "engine queue did not drain"
    time.sleep(0.05)
    assert any(c[0] == "moveTo" and c[1][0] == 1000 and c[1][1] == 250 for c in calls), \
        f"fraction->px mapping wrong: {calls[:4]}"
    assert any(c[0] == "mouseDown" and c[1] == ("left",) for c in calls)
    assert any(c[0] == "mouseUp" and c[1] == ("left",) for c in calls)
    assert any(c[0] == "scroll" and c[1] == (15,) for c in calls), "scroll sign wrong"
    assert any(c[0] == "typewrite" and c[1][0] == "hi" for c in calls)
    assert any(c[0] == "hotkey" and tuple(c[1]) == ("ctrl", "a") for c in calls)
    st = reg["input.status"](FakeCtx(), {})
    assert st["screen"]["w"] == 2000 and st["engine"] in ("healthy", "degraded")
    # engine disable releases buttons
    reg["input.enable"](FakeCtx(), {"on": False})
    assert not ict._buttons_down
    print("  input: fractions->px + buttons + scroll sign + hotkey + status OK")


def test_missing_input_backend_is_not_reported_as_success(monkeypatch):
    """A missing GUI backend and the Winlogon desktop must be explicit errors."""
    class FakeCtx:
        def __init__(self):
            self.disconnect_hooks = []

        def send_error(self, *a, **k):
            pass

    old_pg = ict._pg
    old_desktop = ict._input_desktop
    old_disabled = ict._disabled.is_set()
    try:
        monkeypatch.setattr(ict, "_pg", None)
        monkeypatch.setattr(ict, "_input_desktop", lambda: {
            "name": "Default", "secure": False, "error": ""})
        ict._disabled.clear()
        scr.set_input_enabled(True)
        actions = ict.register(FakeCtx())
        result = actions["input.apply"](FakeCtx(), {"t": "kd", "key": "enter"})
        assert result["ok"] is False
        assert "PyAutoGUI could not load" in result["error"]
        status = actions["input.status"](FakeCtx(), {})
        assert status["available"] is False and status["engine"] == "offline"
        monkeypatch.setattr(ict, "_input_desktop", lambda: {
            "name": "Winlogon", "secure": True,
            "error": "Windows secure sign-in screen is active"})
        monkeypatch.setattr(ict, "_pg", object())
        result = actions["input.apply"](FakeCtx(), {"t": "kd", "key": "enter"})
        assert result["ok"] is False and result["desktop"] == "Winlogon"
        assert "secure sign-in" in result["error"]
    finally:
        ict._pg = old_pg
        ict._input_desktop = old_desktop
        if old_disabled:
            ict._disabled.set()
        else:
            ict._disabled.clear()
        scr.set_input_enabled(False)
    print("  input: backend failures + secure desktop are explicit, never fake success")


def test_screen_stream_survives_lock_and_resumes(monkeypatch):
    """A locked desktop pauses frames; it must not kill the stream thread."""
    import threading

    class FakeCtx:
        def __init__(self):
            self.errors = []

        def send_error(self, *args):
            self.errors.append(args)

        def send_binary(self, data):
            pass

    ctx = FakeCtx()
    old_state = dict(scr._state)
    monkeypatch.setattr(scr, "_cpu_ok", lambda: True)
    monkeypatch.setattr(scr, "_grab", lambda *a: (_ for _ in ()).throw(PermissionError("access denied")))
    monkeypatch.setattr(scr, "_secure_desktop_active", lambda: (True, "secure sign-in screen"))
    scr._state.update(on=True, fps=8, quality=60, monitor=1, max_width=640,
                      errors=0, blocked_reason="")
    th = threading.Thread(target=scr._stream_loop, args=(ctx,), daemon=True)
    th.start()
    try:
        time.sleep(0.1)
        assert th.is_alive() and scr._state["on"] is True
        assert scr._state["blocked_reason"] == "secure sign-in screen"
        assert not ctx.errors, "expected locked desktop to be a quiet, recoverable pause"
    finally:
        scr._state["on"] = False
        th.join(timeout=2)
        scr._state.update(old_state)
    assert not th.is_alive()
    print("  screen: secure desktop pauses capture without stopping its worker")


def test_board_bios_fields():
    import json as _json

    def fake_ps(cmd, timeout=25):
        if "BaseBoard" in cmd:
            return _json.dumps({"Manufacturer": " Acer ", "Product": "Aspire 5"})
        if "BIOS" in cmd:
            return _json.dumps({"Manufacturer": "Insyde", "SMBIOSBIOSVersion": "V1.24"})
        return ""

    hw._ps = fake_ps
    out = hw._board_bios()
    assert out.get("board") == "Acer Aspire 5", out
    assert out.get("bios") == "Insyde V1.24", out
    # graceful when CIM returns junk
    hw._ps = lambda cmd, timeout=25: "not json"
    out2 = hw._board_bios()
    assert out2 == {}, out2
    print("  hardware: board/BIOS CIM parse + junk tolerance OK")


if __name__ == "__main__":
    test_cursor_overlay_drawn()
    test_input_safety_ports()
    test_board_bios_fields()
    print("INTEGRATION-PORT TESTS PASSED")
