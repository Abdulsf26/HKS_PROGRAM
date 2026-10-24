"""Round 59 login/profile and recovery-tool regressions."""
from pathlib import Path
HERE = Path(__file__).resolve()
PANEL = next(p for p in HERE.parents if (p / "server" / "static" / "index.html").is_file())
SOURCE_ROOT = PANEL.parent
PROJECT_ROOT = next((p for p in HERE.parents if (p / "00_START_HERE").is_dir()), SOURCE_ROOT)
STATIC = PANEL / "server" / "static"


def test_login_profile_and_clean_login_surface():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    enter_app = js[js.index("async function enterApp()") : js.index("let _verSeen")]
    assert enter_app.index("initChrome();") < enter_app.index("showSection(lastSection)")
    assert 'sessionStorage.getItem("hks_last_section")' in enter_app
    assert 'sessionStorage.setItem("hks_last_section", id)' in js
    ws_handshake = js[js.index("function connectWs()") : js.index("function drawFrame(")]
    assert "ws.onopen = () => ws.send(JSON.stringify({ machine: STATE.machine" in ws_handshake
    assert "ws.onopen = () => wsSend(" not in ws_handshake
    assert "Live channel unavailable — the dashboard is using live polling." in js
    assert "Live channel blocked by this browser — live continues in BACKUP mode" not in js
    assert "reinstall on the laptop choosing HTTP" not in js
    assert 'id="loginProfileToggle"' in html and 'id="loginProfilePanel"' in html
    assert 'id="useSavedProfile"' in html
    assert 'id="lgForgot"' in html and "hksResetModal" in html
    assert 'role="dialog" aria-modal="true"' in html
    assert 'dialog.querySelectorAll("button:not([disabled]),input:not([disabled])' in html
    assert '#login #useSavedProfile[hidden] { display:none !important; }' in css
    assert '.modal-bk { z-index:300 !important; }' in css
    assert 'fetch("/api/recover"' in html
    assert 'id="buildVer"' not in html and "installed HKS build" not in html
    assert 'aria-expanded="false" aria-controls="profileMenu"' in html
    assert 'id="profileMenu"' in html and 'id="btnLogout"' in html
    assert 'id="btnLogoutSide"' not in html
    assert 'setAttribute("aria-expanded", String(open))' in js
    assert 'e.key === "Escape"' in js
    assert 'localStorage.setItem("hks_remember", $("#lu").value)' in js
    assert 'localStorage.removeItem("hks_remember")' in js
    assert "background-image:repeating-linear-gradient" in css
    assert "box-shadow:7px 7px 0 #111" in css


def test_reset_tool_targets_live_db_and_existing_user_only():
    paths = [p for p in (SOURCE_ROOT / "RESET_PASSWORD.bat",
                         PROJECT_ROOT / "00_START_HERE" / "RESET_PASSWORD.bat") if p.is_file()]
    assert paths, "RESET_PASSWORD.bat must exist in the repo or package layout"
    for path in paths:
        body = path.read_text(encoding="utf-8")
        assert r"HKS_DATA_DIR']=r'%BASE%\data_panel'" in body
        assert "HKS_RESET_USER" in body
        assert "assert u, 'Username not found'" in body
        assert "db.create_user" not in body


def test_installers_stage_recovery_tool():
    paths = [p for p in (SOURCE_ROOT / "INSTALL_HKS.bat",
                         PROJECT_ROOT / "00_START_HERE" / "INSTALL_HKS.bat") if p.is_file()]
    assert paths, "INSTALL_HKS.bat must exist in the repo or package layout"
    for path in paths:
        body = path.read_text(encoding="utf-8")
        assert 'if not exist "%~dp0RESET_PASSWORD.bat"' in body
        assert r'copy /y "%~dp0%%F" "%BASE%\%%F"' in body
        assert ":utility_copy_failed" in body
        assert r'robocopy "%~dp0HKS_ADDONS" "%BASE%\HKS_ADDONS"' in body
