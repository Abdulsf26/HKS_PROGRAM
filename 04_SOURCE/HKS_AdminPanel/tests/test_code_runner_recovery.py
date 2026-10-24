"""Regression checks for the Windows code runner and mobile terminal fixes."""
import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

CLIENT = Path(__file__).parents[2] / "HKS_Client"
sys.path.insert(0, str(CLIENT))
from agent.modules import code  # noqa: E402


def test_compiled_code_uses_argument_lists_not_shell_chains(monkeypatch):
    calls = []
    monkeypatch.setattr(code, "_runtime", lambda lang: (True, code.LANGS[lang][1], "fake compiler"))
    monkeypatch.setattr(code, "_which", lambda name: r"C:\msys64\ucrt64\bin\g++.exe")

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    class FakeProcess:
        returncode = 0
        def communicate(self, timeout=None):
            return "compiled and ran", ""

    monkeypatch.setattr(code.subprocess, "run", fake_run)
    monkeypatch.setattr(code.subprocess, "Popen", lambda *a, **k: FakeProcess())
    result = code.register(object())["code.run"](None, {
        "lang": "cpp", "code": "int main(){return 0;}", "timeout": 10,
    })

    assert result["stdout"] == "compiled and ran"
    assert calls and calls[0][0][0].endswith("g++.exe")
    assert "&&" not in calls[0][0] and calls[0][0][0] != "cmd"


def test_runtime_install_requires_confirmation_without_running_installer(monkeypatch):
    calls = []
    monkeypatch.setattr(code.subprocess, "run", lambda *a, **k: calls.append(a))
    result = code.register(object())["code.install"](None, {"lang": "c"})
    assert result["ok"] is False and "confirmation required" in result["error"]
    assert not calls


def test_java_8_flow_compiles_named_class_then_runs_classpath(monkeypatch):
    calls = []
    monkeypatch.setattr(code, "_runtime", lambda lang: (True, code.LANGS[lang][1], 'java version "1.8"'))
    monkeypatch.setattr(code, "_which", lambda name: rf"C:\Java\bin\{name}.exe")

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    class FakeProcess:
        returncode = 0
        def communicate(self, timeout=None):
            return "hello", ""

    monkeypatch.setattr(code.subprocess, "run", fake_run)
    monkeypatch.setattr(code.subprocess, "Popen", lambda argv, **kwargs: calls.append(argv) or FakeProcess())
    result = code.register(object())["code.run"](None, {
        "lang": "java",
        "code": 'public class Main { public static void main(String[] args) { System.out.println("hello"); } }',
        "timeout": 10,
    })
    assert result["stdout"] == "hello"
    compile_argv, run_argv = calls
    assert compile_argv[0].endswith("javac.exe")
    assert "-d" in compile_argv and compile_argv[-1].endswith("Main.java")
    assert run_argv[0].endswith("java.exe") and run_argv[-1] == "Main"
    assert "-cp" in run_argv


def test_windows_c_install_uses_one_user_scope_winlibs_package(monkeypatch):
    if os.name != "nt":
        return
    calls = []
    monkeypatch.setattr(shutil, "which", lambda name: r"C:\WindowsApps\winget.exe")
    monkeypatch.setattr(code.subprocess, "run", lambda argv, **kw: calls.append(argv) or
                        SimpleNamespace(returncode=0, stdout="ok", stderr=""))
    monkeypatch.setattr(code, "_runtime", lambda lang: (True, code.LANGS[lang][1], "gcc 14"))
    result = code.register(object())["code.install"](None, {"lang": "c", "confirm": "CONFIRM"})
    assert result["installed_now"] is True
    assert len(calls) == 1 and "BrechtSanders.WinLibs.POSIX.UCRT" in calls[0]
    assert "--scope" in calls[0] and "user" in calls[0]


def test_code_runner_actions_use_interactive_session_agent():
    server = Path(__file__).parents[1] / "server" / "main.py"
    source = server.read_text(encoding="utf-8")
    session = (CLIENT / "agent" / "session_main.py").read_text(encoding="utf-8")
    for action in ("code.check", "code.run", "code.install"):
        assert f'"{action}"' in source
    assert 'ctx.register("code", code)' in session


def test_laptop_media_playback_rejects_images_without_raising_agent_error():
    desktop = (CLIENT / "agent" / "session_modules" / "desktop.py").read_text(encoding="utf-8")
    handler = desktop[desktop.index("    def media_play_file(c, p):"):desktop.index("\n    def media_play_youtube", desktop.index("    def media_play_file(c, p):"))]
    assert "ext not in" in handler and '"ok": False' in handler
    assert "raise FileNotFoundError" not in handler


def test_login_capture_uses_serialized_camera_owner():
    camera = (CLIENT / "agent" / "session_modules" / "camera.py").read_text(encoding="utf-8")
    watcher = (CLIENT / "agent" / "session_modules" / "capture_watch.py").read_text(encoding="utf-8")
    assert "def capture_still(index=0)" in camera
    assert "@_camera_serialized\ndef _capture_still_locked" in camera
    assert "_cammod.capture_still(" in watcher
    assert "_cammod._open_cam(" not in watcher


def test_requested_views_use_cards_and_new_folder_uses_child_path():
    static = Path(__file__).parents[1] / "server" / "static"
    app = (static / "app.js").read_text(encoding="utf-8")
    css = (static / "style.css").read_text(encoding="utf-8")
    for cls in ("xampp-card", "process-card", "usb-card", "app-card"):
        assert cls in app and f".{cls}" in css
    assert 'const target = parent + "\\\\" + name;' in app
    index = (static / "index.html").read_text(encoding="utf-8")
    assert 'app.js?v=' in index and 'style.css?v=' in index
    assert 'r.mem != null ? fmtB(r.mem)' in app


def test_mobile_terminal_has_visible_command_input_and_respects_manual_scroll():
    static = Path(__file__).parents[1] / "server" / "static"
    js = (static / "app.js").read_text(encoding="utf-8")
    css = (static / "style.css").read_text(encoding="utf-8")
    assert 'class: "trm-command"' in js
    assert 'aria-label": "Command to run in the laptop terminal"' in js
    assert "const follow = atBottom()" in js and "scroll(follow)" in js
    assert "grid-template-areas:\"menu brand alerts profile\"" in css
    assert ".trm-in { position:static; opacity:1;" in css


def test_remote_unlock_reports_console_7041_without_calling_session_routing_api():
    terminal = (CLIENT / "agent" / "modules" / "terminal.py").read_text(encoding="utf-8")
    unlock = terminal[terminal.index("    def power_unlock(c, p):"):terminal.index("\n    return {\"terminal.exec\"")]
    assert "wts.WTSConnectSessionW(" not in unlock
    assert "7041 (ERROR_CTX_CONSOLE_DISCONNECT)" in unlock
    assert "No session was disconnected" in unlock
    assert 'edition.startswith("core")' in unlock and "Windows Home" in unlock
    assert '"unlocked": True' not in unlock


def test_remote_keyboard_never_hides_secure_desktop_or_missing_backend():
    client = Path(__file__).parents[2] / "HKS_Client"
    input_src = (client / "agent" / "session_modules" / "input_ctl.py").read_text(encoding="utf-8")
    screen_src = (client / "agent" / "session_modules" / "screen.py").read_text(encoding="utf-8")
    app = (Path(__file__).parents[1] / "server" / "static" / "app.js").read_text(encoding="utf-8")
    assert '"secure desktop"' in input_src and '"available": bool(_pg is not None and not desktop["secure"])' in input_src
    assert "PyAutoGUI could not load" in input_src
    assert "if locked:" in screen_src and "resume automatically" in screen_src
    assert 'await sendInput({ ...ev, t: "ku" })' in app
    assert "input status unavailable" in app


def test_phone_touch_drag_uses_pixel_threshold_and_releases_long_press():
    app = (Path(__file__).parents[1] / "server" / "static" / "app.js").read_text(encoding="utf-8")
    block = app[app.index('let tStart = null'):app.index('canvas.addEventListener("keydown"', app.index('let tStart = null'))]
    assert "touch.clientX - tOrigin.x" in block
    assert 't: "mu", ...tStart, btn: 2' in block
    assert 'canvas.addEventListener("touchcancel"' in block


def test_terminal_shell_does_not_open_a_visible_windows_console():
    source = (CLIENT / "agent" / "modules" / "terminal.py").read_text(encoding="utf-8")
    assert "creationflags=CREATE_NO_WINDOW if os.name == \"nt\" else 0" in source


def test_cctv_does_not_report_fake_camera_and_shows_shinobi_status():
    app = (Path(__file__).parents[1] / "server" / "static" / "app.js").read_text(encoding="utf-8")
    cctv = app[app.index("function renderCctv(v)"):app.index("/* 2026 UI report: grouped navigation", app.index("function renderCctv(v)"))]
    assert "No laptop camera was detected" in cctv
    assert 'cmd("addons.status"' in cctv and "Open Shinobi console" in cctv
    assert 'cams.push({ name: "Camera 1"' not in cctv


def test_cleanup_is_allowlisted_admin_only_and_requires_exact_confirmation():
    source = (Path(__file__).parents[1] / "server" / "main.py").read_text(encoding="utf-8")
    route = source[source.index("async def storage_cleanup"):source.index("@app.get(\"/api/capture/{cid}\")")]
    assert 'allowed = {"events", "errors", "audit", "captures", "panel_log"}' in route
    assert 'require_auth(request, mutating=True)' in route and 'user.get("role") != "admin"' in route
    assert 'body.get("confirm") != "DELETE " + category.upper()' in route


def test_cleanup_ui_explains_summary_failures_instead_of_showing_a_false_empty_store():
    static = Path(__file__).parents[1] / "server" / "static"
    app = (static / "app.js").read_text(encoding="utf-8")
    css = (static / "style.css").read_text(encoding="utf-8")
    clean = app[app.index("function renderClean(v)"):app.index("/* ================= ERRORS", app.index("function renderClean(v)"))]
    assert 'paint(null)' in clean
    assert 'Storage summary could not be loaded.' in clean
    assert 'Retry storage check' in clean
    assert 'disabled: !available || count < 1' in clean
    assert '.clean-error' in css


def test_code_server_installer_is_resource_bounded_and_waits_for_http():
    bat = (Path(__file__).parents[2] / "HKS_ADDONS" / "ADDON_CODESERVER.bat").read_text(encoding="utf-8")
    assert "--memory=512m --memory-swap=768m" in bat
    assert "Waiting for code-server HTTP health" in bat
    assert "http://127.0.0.1:8482/" in bat
    assert 'findstr /x "hks-code-server"' in bat
