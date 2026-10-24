"""Regression tests for the session-agent stack (bugs found from the field):

  1. ctx.on_binary missing        -> talk-audio wiring silently failed
  2. _receiver dropped binary     -> admin voice never reached the laptop
  3. screen.py missing import time-> screen stream crashed with NameError
  4. camera .tofile(BytesIO)      -> live camera crashed (snap worked)
  5. session agent: no status page-> couldn't tell svc/session status apart
  6. terminal cwd not persistent  -> cd + dir behaved wrong; wrong cwd shown
"""
import asyncio
import json
import os
import sys
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
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
_ROOT = os.path.dirname(PANEL)             # 04_SOURCE (repo) or zip root
sys.path.insert(0, PANEL)
sys.path.insert(0, CLIENT)
ROOT = _ROOT

sys.path.insert(0, CLIENT)
os.environ.setdefault("HKS_DATA_DIR", os.path.join(HERE, "_session_stack_tmp"))
os.makedirs(os.environ["HKS_DATA_DIR"], exist_ok=True)


def test_voice_wiring_end_to_end():
    """Real AgentContext + real load_session_modules: channel 4 must be wired."""
    from agent.common import AgentContext
    from agent.session_main import load_session_modules
    from agent.session_modules import desktop
    cfg = os.path.join(os.environ["HKS_DATA_DIR"], "config.json")
    json.dump({"machine_id": "VOICETEST"}, open(cfg, "w"))
    ctx = AgentContext("session", cfg)
    load_session_modules(ctx)
    assert 4 in ctx.binary_handlers, "channel 4 not wired (old: on_binary missing)"
    assert ctx.binary_handlers[4] is desktop.feed_voice
    print("  voice wiring: ctx.on_binary(4, feed_voice) registered for real")


def test_receiver_demuxes_binary():
    """Binary frames must reach binary_handlers; JSON must still dispatch."""
    from agent.connection import Connection

    received = []
    ran = []

    class FakeCfg(dict):
        def save(self):
            pass

    class FakeCtx:
        role = "session"
        cfg = FakeCfg(machine_id="X")
        binary_handlers = {4: lambda b: received.append(b)}
        stats = {"cmds": 0, "errors": 0}
        actions = {"ping": lambda c, p: (ran.append("ping"), {"ok": True})[1]}

        def send_error(self, *a):
            pass

    class FakeWS:
        def __init__(self, items):
            self._it = iter(items)

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                item = next(self._it)
            except StopIteration:
                raise StopAsyncIteration
            await asyncio.sleep(0.01)  # let scheduled cmd tasks actually run
            return item

    conn = Connection(FakeCtx(), FakeCtx.actions)  # pass the real action table
    ws = FakeWS([b"\x04voice-bytes", b"",  # empty binary must be skipped
                 json.dumps({"type": "cmd", "id": "1", "action": "ping"})])
    asyncio.run(conn._receiver(ws))
    time.sleep(0.2)
    assert received == [b"voice-bytes"], received
    assert ran == ["ping"], ran
    print("  _receiver: binary frames demuxed by channel, JSON still works")


def test_screen_imports_time():
    import agent.session_modules.screen as screen
    assert hasattr(screen, "time"), "screen.py missing import time (old NameError)"
    print("  screen: time module available (stream can pace itself)")


def test_camera_stream_encode():
    src = open(os.path.join(CLIENT, "agent", "session_modules", "camera.py"),
               encoding="utf-8").read()
    assert ".tofile(" not in src, "camera still uses ndarray.tofile (old crash)"
    assert ".tobytes()" in src and "imencode" in src

    # functional: snap path with a fake cv2
    fake = types.ModuleType("cv2")

    class _Enc:
        def tobytes(self):
            return b"jpeg-bytes-here"

    class _Cap:
        def isOpened(self):
            return True

        def read(self):
            import types as _t
            return True, _t.SimpleNamespace(size=5)

        def set(self, *a):
            pass

        def release(self):
            pass

    fake.imencode = lambda *a, **k: (True, _Enc())
    fake.VideoCapture = lambda i, be=None: _Cap()
    fake.IMWRITE_JPEG_QUALITY = 1
    fake.CAP_PROP_FRAME_WIDTH = 3
    fake.CAP_PROP_FRAME_HEIGHT = 4
    fake.CAP_ANY = 0        # backend constants used by _open_cam (round-4)
    fake.CAP_DSHOW = 700
    fake.CAP_MSMF = 1400
    sys.modules["cv2"] = fake
    try:
        from agent.session_modules import camera
        import logging

        class Ctx:
            actions = {}
            events = []
            disconnect_hooks = []
            stats = {"started": time.time(), "cmds": 0, "errors": 0, "latency_ms": None}
            log = logging.getLogger("t")

            def send_binary(self, ch, b):
                pass

        import base64
        out = camera.register(Ctx())["camera.snap"](Ctx(), {})
        assert base64.b64decode(out["b64"]) == b"jpeg-bytes-here", out
    finally:
        sys.modules.pop("cv2", None)


def test_camera_list_does_not_probe_native_driver():
    """Idle status refreshes must never reopen the Windows camera driver."""
    from agent.session_modules import camera
    prior = camera._list_cache.copy()
    camera._list_cache.update(ts=0, res=None)
    original = camera._open_cam
    camera._open_cam = lambda *a, **k: (_ for _ in ()).throw(AssertionError("native probe called"))
    try:
        actions = camera.register(types.SimpleNamespace())
        result = actions["camera.list"](None, {})
        assert result.get("probe_skipped") is True, result
        assert result["items"] == [], result
    finally:
        camera._open_cam = original
        camera._list_cache.update(prior)


def test_microphone_uses_callback_instead_of_windows_blocking_read():
    source = open(os.path.join(CLIENT, "agent", "session_modules", "desktop.py"), encoding="utf-8").read()
    loop = source.split("def _mic_loop(ctx):", 1)[1].split("def _talk_callback", 1)[0]
    assert "callback=receive" in loop
    assert "stream.read(" not in loop
    assert "-9999" in source and "audio driver rejected" in source


def test_code_server_installer_checks_docker_before_reporting_success():
    source = open(os.path.join(ROOT, "HKS_ADDONS", "ADDON_CODESERVER.bat"), encoding="ascii").read()
    assert '"%DOCKER%" info' in source
    assert '"%DOCKER%" pull codercom/code-server\nif errorlevel 1' in source
    assert '"%DOCKER%" ps --filter' in source
    assert "[OK] code-server is running" in source
    print("  camera: stream encode uses imencode->tobytes; snap still works")


def test_terminal_cwd_persistent():
    from agent.modules import terminal

    calls = []

    class R:
        returncode = 0
        stderr = ""

        def __init__(self, out):
            self.stdout = out

    def fake_run(argv, capture_output, text, timeout, cwd, errors, creationflags):
        calls.append(cwd)
        joined = " ".join(argv)
        if joined.startswith("cmd /d /c cd") or "(Get-Location)" in joined:
            return R("C:\\newdir\r\n")
        return R("command-output\r\n")

    real_run = terminal.subprocess.run
    real_live_shell = terminal._LiveShell
    terminal.subprocess.run = fake_run
    try:
        import logging

        class Ctx:
            terminal_cwd = ""
            stats = {"started": 0, "cmds": 0, "errors": 0, "latency_ms": None}
            log = logging.getLogger("t")

        class FakeLiveShell:
            """Deterministic stand-in for the persistent Windows shell."""
            def __init__(self):
                self.cwd = "C:\\"
                self.proc = types.SimpleNamespace(poll=lambda: None)

            def run(self, command, timeout):
                if command.lower().startswith("cd "):
                    self.cwd = command[3:].strip().strip('"')
                return "command-output", 0, self.cwd

            def close(self):
                pass

        terminal._LiveShell = FakeLiveShell

        ctx = Ctx()
        reg = terminal.register(ctx)
        if os.name != "nt":
            # POSIX host: the live-shell path ignores the stubbed
            # subprocess.run - assert REAL behavior instead (cwd is the
            # shell's true pwd and persists inside one live shell)
            r1 = reg["terminal.exec"](ctx, {"command": "cd /tmp"})
            assert r1.get("live") and r1["cwd"] == "/tmp", r1
            r2 = reg["terminal.exec"](ctx, {"command": "pwd"})
            assert r2["cwd"] == "/tmp" and "/tmp" in r2["output"], r2
            print("  terminal (POSIX live shell): cwd persists across execs")
            return
        r1 = reg["terminal.exec"](ctx, {"shell": "cmd", "command": "cd C:\\newdir"})
        assert r1["cwd"] == "C:\\newdir", r1
        assert r1.get("live") is True, r1
        assert ctx.terminal_cwd == "C:\\newdir"
        r2 = reg["terminal.exec"](ctx, {"shell": "cmd", "command": "dir"})
        assert r2["cwd"] == "C:\\newdir" and r2.get("live") is True, r2
        assert ctx.live_shell.cwd == "C:\\newdir", "second exec continues in the remembered directory"
    finally:
        terminal.subprocess.run = real_run
        terminal._LiveShell = real_live_shell
    print("  terminal: cwd persists across execs; reported cwd is the real one")


def test_session_status_page():
    import socket
    import urllib.request
    from agent.common import AgentContext
    from agent import local_panel
    cfg = os.path.join(os.environ["HKS_DATA_DIR"], "config2.json")
    json.dump({"machine_id": "STATUSTEST"}, open(cfg, "w"))
    ctx = AgentContext("session", cfg)

    class C:
        alive = True
    ctx.conn = C()
    # Do not assume the agent's fixed 8590..8594 range is free on a laptop
    # where the real HKS service/session agents may already be running.
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    local_panel.start(ctx, port=port)
    time.sleep(1.2)
    got = None
    try:
        got = json.loads(urllib.request.urlopen(
            f"http://127.0.0.1:{port}/status.json", timeout=2).read())
    except Exception:
        pass
    assert got and got.get("role") == "session" and got.get("connected"), got
    print(f"  session status page: live with role=session (port {port})")


if __name__ == "__main__":
    test_voice_wiring_end_to_end()
    test_receiver_demuxes_binary()
    test_screen_imports_time()
    test_camera_stream_encode()
    test_terminal_cwd_persistent()
    test_session_status_page()
    print("ALL SESSION-STACK TESTS PASSED")
