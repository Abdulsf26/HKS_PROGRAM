from pathlib import Path


def _project_root():
    here = Path(__file__).resolve()
    for candidate in (here.parents[3], here.parents[2]):
        if (candidate / "00_START_HERE" / "HKS_SETUP.py").is_file():
            return candidate
    # Packaged layout: installer launcher and package root are siblings.
    for candidate in here.parents:
        if (candidate / "HKS_SETUP.py").is_file():
            return candidate.parent
    raise AssertionError("Could not locate GUI setup launcher")


def test_gui_setup_hides_command_processor_and_surfaces_output():
    root = _project_root()
    launcher = (root / "00_START_HERE" / "HKS_SETUP.py").read_text(encoding="utf-8")
    installer = (root / "00_START_HERE" / "INSTALL_HKS.bat").read_text(encoding="utf-8")
    assert "info.nShow = 0" in launcher
    assert "for column in (0, 1):" in launcher, "Tk grid configures one column at a time"
    assert "HKS_GUI_INSTALL.bat" in launcher and "setup.log" in launcher
    assert "self.refresh_log()" in launcher and "showerror(\"HKS setup failed\"" in launcher
    assert "end - 12_000" in launcher, "progress polling should read a bounded log tail"
    assert "set HKS_GUI_INSTALL=1" in launcher
    assert "if not defined HKS_GUI_INSTALL pause" in installer
    assert 'python "%~dp0first_run.py" --gui' in installer
    for action in ("Check status", "Run diagnostics", "Uninstall…"):
        assert action in launcher
    uninstall = (root / "00_START_HERE" / "UNINSTALL_HKS.bat").read_text(encoding="utf-8")
    assert "HKS_GUI_UNINSTALL" in uninstall and "HKS_GUI_BACKUP" in uninstall
    assert "cloudflared.*trycloudflare';" in uninstall
    assert "|HKS_PROGRAM" not in uninstall, "uninstaller must not match/kill its own GUI launcher"
