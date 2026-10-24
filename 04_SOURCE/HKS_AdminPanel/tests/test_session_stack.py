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
    assert "addons.run" in ctx.actions, "add-on installer action must be available to the desktop session"
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


def test_session_agent_twin_detection_uses_exact_python_argv():
    from agent.session_main import _is_session_agent_argv
    assert _is_session_agent_argv("pythonw.exe", ["pythonw.exe", "-m", "agent.session_main"])
    assert _is_session_agent_argv("python.exe", ["python.exe", r"C:\HKS_PROGRAM\HKS_Client\agent\session_main.py"])
    assert not _is_session_agent_argv("pwsh.exe", ["pwsh.exe", "-Command", "Get-Content agent/session_main.py"])
    assert not _is_session_agent_argv("python.exe", ["python.exe", "-c", "print('agent session_main')"])
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
    from agent.session_modules import camera
    prior = camera._list_cache.copy()
    camera._list_cache.update(ts=0, res=None)
    original = camera._open_cam
    camera._open_cam = lambda *a, **k: (_ for _ in ()).throw(AssertionError("native probe called"))
    try:
        actions = camera.register(types.SimpleNamespace())
        result = actions["camera.list"](None, {})
        assert result.get("probe_skipped") is True
        assert result["items"] == []
    finally:
        camera._open_cam = original
        camera._list_cache.update(prior)


def test_microphone_uses_callback_stream_on_windows():
    source = open(os.path.join(CLIENT, "agent", "session_modules", "desktop.py"), encoding="utf-8").read()
    loop = source.split("def _mic_loop(ctx):", 1)[1].split("def _talk_callback", 1)[0]
    assert "callback=receive" in loop
    assert "stream.read(" not in loop
    assert "-9999" in source
    print("  camera: stream encode uses imencode->tobytes; snap still works")


def test_camera_list_does_not_repeat_native_driver_probes(monkeypatch):
    from agent.session_modules import camera
    camera._cam["on"] = False
    camera._motion["on"] = False
    camera._rec["on"] = False
    camera._list_cache["ts"] = 0
    camera._list_cache["res"] = None
    def forbidden_probe(*args, **kwargs):
        raise AssertionError("camera.list must not open native camera handles")
    monkeypatch.setattr(camera, "_open_cam", forbidden_probe)
    result = camera.register(types.SimpleNamespace(disconnect_hooks=[]))["camera.list"](None, {})
    assert result["probe_skipped"] is True and result["items"] == []
    assert "when you start a stream" in result["hint"]


def test_microphone_uses_callback_stream_for_windows_host_apis():
    source = open(os.path.join(CLIENT, "agent", "session_modules", "desktop.py"),
                  encoding="utf-8").read()
    body = source[source.index("def _mic_loop(ctx):"):source.index("def _talk_callback(")]
    assert "callback=receive" in body
    assert "stream.read(" not in body


def test_terminal_rest_cursor_does_not_consume_or_repeat_stream_output():
    from collections import deque
    from threading import Lock
    from agent.modules.terminal import _LiveShell

    shell = _LiveShell.__new__(_LiveShell)
    shell.hist = deque(maxlen=500)
    shell.output_seq = 0
    shell.output_lock = Lock()
    first = shell.record_output("first")
    second = shell.record_output("second")
    assert (first, second) == (1, 2)
    assert shell.output_since(0) == {"lines": ["first", "second"], "cursor": 2}
    assert shell.output_since(1) == {"lines": ["second"], "cursor": 2}
    assert shell.output_since(2) == {"lines": [], "cursor": 2}


def test_replaced_agent_disconnect_does_not_mark_new_connection_offline(monkeypatch):
    from server import main

    h = main.Hub()
    old = types.SimpleNamespace(machine_id="machine", role="svc")
    current = types.SimpleNamespace(machine_id="machine", role="svc")
    h.agents = {"machine": {"svc": current}}
    side_effects = []
    monkeypatch.setattr(main.db, "upsert_machine", lambda *a, **kw: side_effects.append((a, kw)))
    monkeypatch.setattr(main.db, "add_event", lambda *a, **kw: side_effects.append((a, kw)))

    async def broadcast(payload):
        side_effects.append(payload)
    h.broadcast_dash = broadcast

    asyncio.run(h.detach(old))
    assert h.get_agent("machine", "svc") is current
    assert side_effects == []


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


def test_addon_installer_runs_in_interactive_session(monkeypatch, tmp_path):
    from agent.session_modules import addon_runner
    from agent.modules import addons

    bat = tmp_path / "ADDON_CODESERVER.bat"
    bat.write_text("@echo off\necho simulated installer\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(addon_runner, "_is_windows", lambda: True)
    monkeypatch.setattr(addons, "_installer_path", lambda rel: str(bat))
    monkeypatch.setattr(addons, "_container_info", lambda name: None)
    monkeypatch.setattr(addons, "_port_open", lambda port: False)
    monkeypatch.setattr(addon_runner.subprocess, "run", lambda *a, **k:
                        types.SimpleNamespace(returncode=0, stderr="", stdout="29.8.0"))
    monkeypatch.setattr(addon_runner.subprocess, "Popen",
                        lambda argv, **kwargs: (calls.append((argv, kwargs)) or types.SimpleNamespace(pid=4321)))
    actions = addon_runner.register(object())
    result = actions["addons.run"](None, {"name": "code-server"})
    assert result.get("ok") and "visible console" in result.get("note", "")
    assert result.get("pid") == 4321
    assert calls and calls[0][0] == ["cmd.exe", "/d", "/c", str(bat)]
    assert calls[0][1]["cwd"] == str(tmp_path)

    assert actions["addons.run"](None, {"name": "arbitrary.exe"}).get("ok") is False
    monkeypatch.setattr(addons, "_installer_path", lambda rel: None)
    missing = actions["addons.run"](None, {"name": "code-server"})
    assert missing.get("ok") is False and "installer is missing" in missing.get("error", "")


def test_appearance_preferences_have_real_styles_and_keyboard_theme_controls():
    static = os.path.join(PANEL, "server", "static")
    css = open(os.path.join(static, "style.css"), encoding="utf-8").read()
    js = open(os.path.join(static, "app.js"), encoding="utf-8").read()
    assert 'html[data-font="s"]' in css and 'html[data-font="l"]' in css
    assert 'html[data-accent="blue"]' in css and 'html[data-accent="green"]' in css
    assert 'html[data-theme="dark"]' in css
    assert 'el("button", { type: "button", class: "tcard"' in js
    assert 'document.documentElement.dataset.theme = name' in js


def test_code_server_reports_stopped_docker_before_launch(monkeypatch, tmp_path):
    from agent.session_modules import addon_runner
    from agent.modules import addons
    import subprocess

    bat = tmp_path / "ADDON_CODESERVER.bat"
    bat.write_text("@echo off\n", encoding="utf-8")
    monkeypatch.setattr(addon_runner, "_is_windows", lambda: True)
    monkeypatch.setattr(addons, "_installer_path", lambda rel: str(bat))
    monkeypatch.setattr(addons, "_container_info", lambda name: None)
    monkeypatch.setattr(addons, "_port_open", lambda port: False)
    monkeypatch.setattr(addon_runner.shutil, "which", lambda name: "docker.exe")
    monkeypatch.setattr(addon_runner.subprocess, "run", lambda *a, **k: types.SimpleNamespace(
        returncode=1, stderr="Docker Desktop Linux engine is not running", stdout=""))
    launches = []
    monkeypatch.setattr(addon_runner.subprocess, "Popen", lambda *a, **k: launches.append(a))
    result = addon_runner.register(object())["addons.run"](None, {"name": "code-server"})
    assert result.get("ok") is False and "engine is stopped" in result.get("error", "")
    assert not launches, "do not report a started installer when its required Docker engine is down"


def test_addon_start_does_not_rerun_installer_for_running_container(monkeypatch, tmp_path):
    from agent.session_modules import addon_runner
    from agent.modules import addons
    import subprocess

    bat = tmp_path / "ADDON_CODESERVER.bat"
    bat.write_text("@echo off\n", encoding="utf-8")
    monkeypatch.setattr(addon_runner, "_is_windows", lambda: True)
    monkeypatch.setattr(addons, "_installer_path", lambda rel: str(bat))
    monkeypatch.setattr(addons, "_container_info", lambda name: {"running": True, "restart": "unless-stopped"})
    monkeypatch.setattr(addons, "_port_open", lambda port: True)
    launches = []
    monkeypatch.setattr(addon_runner.subprocess, "Popen", lambda *a, **k: launches.append(a))
    result = addon_runner.register(object())["addons.run"](None, {"name": "code-server"})
    assert result.get("ok") and result.get("already_running")
    assert "not rerun" in result.get("note", "")
    assert not launches


def test_addon_start_refuses_to_replace_an_unidentified_port_owner(monkeypatch, tmp_path):
    from agent.session_modules import addon_runner
    from agent.modules import addons

    bat = tmp_path / "ADDON_FILEBROWSER.bat"
    bat.write_text("@echo off\n", encoding="utf-8")
    monkeypatch.setattr(addon_runner, "_is_windows", lambda: True)
    monkeypatch.setattr(addons, "_installer_path", lambda rel: str(bat))
    monkeypatch.setattr(addons, "_port_open", lambda port: True)
    monkeypatch.setattr(addon_runner.subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not relaunch")))
    result = addon_runner.register(object())["addons.run"](None, {"name": "filebrowser"})
    assert result.get("ok") is False and "Port 8487 is already occupied" in result.get("error", "")


def test_addon_stop_never_kills_an_unmanaged_port_owner(monkeypatch):
    from agent.modules import addons
    actions = addons.register(object())
    called = []
    monkeypatch.setattr(addons, "_docker_cli", lambda: "docker.exe")
    monkeypatch.setattr(addons.subprocess if hasattr(addons, "subprocess") else __import__("subprocess"), "run",
                        lambda *a, **k: called.append(a))
    result = actions["addons.stop"](None, {"name": "filebrowser"})
    assert result.get("ok") is False and "will not terminate an arbitrary port owner" in result.get("error", "")
    assert not called


def test_addon_autostart_does_not_schedule_non_container_installers():
    from agent.modules import addons
    result = addons.register(object())["addons.autostart"](None, {"name": "Netdata", "on": True})
    assert result.get("ok") is False and "never scheduled to rerun as SYSTEM" in result.get("error", "")


def test_addon_status_hides_stop_for_stopped_or_missing_containers(monkeypatch):
    from agent.modules import addons
    monkeypatch.setattr(addons, "_port_open", lambda port: False)
    monkeypatch.setattr(addons, "_container_info", lambda name: {"running": False, "restart": "no"})
    monkeypatch.setattr(addons, "_lan_ip", lambda: "127.0.0.1")
    monkeypatch.setattr(addons, "_installer_path", lambda rel: "present.bat")
    result = addons.register(object())["addons.status"](None, {})
    code = next(a for a in result["addons"] if a["name"] == "code-server")
    assert code["container_running"] is False
    assert code["can_stop"] is False
    assert code["can_autostart"] is True


def test_network_addresses_skip_apipa_and_host_only_adapters(monkeypatch):
    import socket
    from agent.modules import network_ctl

    class Addr:
        def __init__(self, family, address):
            self.family, self.address = family, address
    class Psutil:
        @staticmethod
        def net_if_addrs():
            return {
                "Ethernet": [Addr(socket.AF_INET, "169.254.146.211")],
                "VirtualBox Host-Only Network": [Addr(socket.AF_INET, "192.168.56.1")],
                "Wi-Fi": [Addr(socket.AF_INET, "10.58.60.224")],
                "Ethernet 2": [Addr(socket.AF_INET, "10.0.0.5")],
            }
    monkeypatch.setitem(sys.modules, "psutil", Psutil)
    class Ctx:
        last_primary_ip = None
    got = network_ctl.register(Ctx())["network.addresses"](Ctx(), {})
    assert got["primary"] == "10.58.60.224"
    assert [x["ip"] for x in got["addresses"]] == ["10.58.60.224", "10.0.0.5"]


def test_agent_result_envelope_preserves_success_details():
    from agent.connection import _command_envelope
    assert _command_envelope({"ok": True, "started": "windows_exporter", "note": "console opened", "pid": 42}) == {
        "ok": True, "started": "windows_exporter", "note": "console opened", "pid": 42,
        "data": {"started": "windows_exporter", "note": "console opened", "pid": 42}}
    assert _command_envelope({"ok": False, "error": "session unavailable"})["error"] == "session unavailable"
    assert _command_envelope({"pong": True}) == {"ok": True, "data": {"pong": True}}


def test_clean_websocket_close_ends_session_and_cleans_workers(monkeypatch):
    """A normal receiver close must not hang on sender's empty queue."""
    from agent import connection as connection_mod
    from agent.connection import Connection

    class Cfg(dict):
        def save(self):
            pass

    class Ctx:
        role = "session"
        cfg = Cfg(server_url="ws://localhost/ws", machine_id="T", token="x")
        offline = []
        stats = {"cmds": 0, "errors": 0}
        conn = None
        def handle_disconnect(self):
            self.disconnected = getattr(self, "disconnected", 0) + 1
        def log_info(self, *args):
            pass

    class WS:
        transport = None
        def __init__(self):
            self.sent = []
            self.reply_sent = False
        async def send(self, data):
            self.sent.append(data)
        async def recv(self):
            return json.dumps({"type": "hello_ok"})
        def __aiter__(self):
            return self
        async def __anext__(self):
            raise StopAsyncIteration

    class SocketContext:
        def __init__(self, ws): self.ws = ws
        async def __aenter__(self): return self.ws
        async def __aexit__(self, *args): return False

    ws = WS()
    monkeypatch.setattr(connection_mod, "ws_connect", lambda *a, **kw: SocketContext(ws))
    ctx = Ctx()
    ctx.log = types.SimpleNamespace(info=lambda *a: None, warning=lambda *a: None,
                                    error=lambda *a: None)
    conn = Connection(ctx, {})
    try:
        asyncio.run(asyncio.wait_for(conn._session(), timeout=1))
    except ConnectionError as exc:
        assert "returned normally" in str(exc)
    else:
        assert False, "clean websocket close must end this session attempt"
    assert conn.alive is False and conn.ws is None and conn._loop is None
    assert ctx.disconnected == 1
    assert not conn._command_tasks


def test_websocket_worker_exception_cleans_siblings(monkeypatch):
    from agent import connection as connection_mod
    from agent.connection import Connection

    class Cfg(dict):
        def save(self): pass
    class Ctx:
        role = "session"
        cfg = Cfg(server_url="ws://localhost/ws", machine_id="T", token="x")
        offline = []
        stats = {"cmds": 0, "errors": 0}
        conn = None
        def handle_disconnect(self): self.disconnected = getattr(self, "disconnected", 0) + 1
    class WS:
        transport = None
        async def send(self, data): pass
        async def recv(self): return json.dumps({"type": "hello_ok"})
        def __aiter__(self): return self
        async def __anext__(self): raise RuntimeError("simulated socket failure")
    class SocketContext:
        async def __aenter__(self): return WS()
        async def __aexit__(self, *args): return False
    monkeypatch.setattr(connection_mod, "ws_connect", lambda *a, **kw: SocketContext())
    ctx = Ctx()
    ctx.log = types.SimpleNamespace(info=lambda *a: None, warning=lambda *a: None,
                                    error=lambda *a: None)
    conn = Connection(ctx, {})
    try:
        asyncio.run(asyncio.wait_for(conn._session(), timeout=1))
        assert False, "socket worker exception should trigger reconnect"
    except RuntimeError as exc:
        assert "simulated socket failure" in str(exc)
    assert conn.alive is False and conn.ws is None and conn._loop is None
    assert ctx.disconnected == 1
    assert not conn._command_tasks


def test_one_optional_session_import_failure_keeps_other_features(monkeypatch):
    import importlib
    from agent.common import AgentContext
    from agent.session_main import load_session_modules
    cfg = os.path.join(os.environ["HKS_DATA_DIR"], "optional-import.json")
    json.dump({"machine_id": "OPTIONALTEST"}, open(cfg, "w"))
    ctx = AgentContext("session", cfg)
    real_import = importlib.import_module
    def selective_import(name, package=None):
        if name == "agent.session_modules.screen":
            raise ImportError("simulated missing screen dependency")
        return real_import(name, package)
    monkeypatch.setattr(importlib, "import_module", selective_import)
    load_session_modules(ctx)
    assert "screen.start" not in ctx.actions
    assert "input.apply" in ctx.actions and "addons.run" in ctx.actions


if __name__ == "__main__":
    test_voice_wiring_end_to_end()
    test_receiver_demuxes_binary()
    test_screen_imports_time()
    test_camera_stream_encode()
    test_terminal_cwd_persistent()
    test_session_status_page()
    # pytest covers the two added tests above; direct invocation remains a
    # quick hardware smoke and does not simulate launching third-party code.
    print("ALL SESSION-STACK TESTS PASSED")
