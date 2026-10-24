"""Single-file EXE source for HKS_PROGRAM_SETUP.exe (PyInstaller --onefile)."""
from __future__ import annotations

import ctypes
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from ctypes import wintypes
import tkinter as tk
from tkinter import messagebox, ttk


def validate_archive(source: Path) -> None:
    required = {"INSTALL_HKS.bat", "first_run.py", "HKS_AdminPanel/server/main.py",
                "HKS_Client/agent/session_main.py", "HKS_ADDONS/ADDON_MENU.bat"}
    with zipfile.ZipFile(source) as bundle:
        bad_member = bundle.testzip()
        if bad_member:
            raise OSError("Embedded package CRC failed: " + bad_member)
        missing = required.difference(bundle.namelist())
        if missing:
            raise OSError("Embedded package is incomplete: " + ", ".join(sorted(missing)))


class ShellExecuteInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR),
        ("lpParameters", wintypes.LPCWSTR), ("lpDirectory", wintypes.LPCWSTR),
        ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR),
        ("hkeyClass", wintypes.HANDLE), ("dwHotKey", wintypes.DWORD),
        ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE),
    ]


class SetupApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("HKS Program Setup")
        self.root.geometry("600x460")
        self.root.minsize(520, 400)
        self.root.configure(bg="#f2efe6")
        self.temp_dir: Path | None = None
        self.process = None
        self.log_path: Path | None = None
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background="#f2efe6")
        style.configure("TLabel", background="#f2efe6", foreground="#111111", font=("Segoe UI", 10))
        style.configure("Title.TLabel", font=("Segoe UI", 22, "bold"))
        style.configure("TButton", font=("Segoe UI", 10, "bold"), padding=(12, 9))
        box = ttk.Frame(self.root, padding=24)
        box.pack(fill="both", expand=True)
        ttk.Label(box, text="HKS PROGRAM", style="Title.TLabel").pack(anchor="w")
        ttk.Label(box, text="Guided Windows installation with visible progress and error details",
                  wraplength=520).pack(anchor="w", pady=(4, 18))
        ttk.Label(box, text="Setup installs to C:\\HKS_PROGRAM and preserves your existing data.",
                  wraplength=520).pack(anchor="w", pady=(0, 12))
        self.status = ttk.Label(box, text="Ready. Click Install to extract and launch the setup wizard.",
                                wraplength=520)
        self.status.pack(anchor="w", pady=(0, 12))
        self.progress = ttk.Progressbar(box, mode="indeterminate")
        self.progress.pack(fill="x", pady=(0, 14))
        ttk.Label(box, text="The next screen collects your admin login and port settings. Setup output and errors appear here.",
                  wraplength=520).pack(anchor="w", pady=(0, 8))
        self.details = tk.Text(box, height=8, wrap="word", state="disabled", bg="#fffdf6",
                               fg="#111111", relief="solid", borderwidth=2, padx=10, pady=8)
        self.details.pack(fill="both", expand=True, pady=(0, 12))
        actions = ttk.Frame(box)
        actions.pack(fill="x", side="bottom")
        self.install_button = ttk.Button(actions, text="Install / Repair", command=lambda: self.install("install"))
        self.install_button.grid(row=0, column=0, sticky="ew", padx=(0, 6), pady=3)
        self.status_button = ttk.Button(actions, text="Check status", command=lambda: self.install("status"))
        self.status_button.grid(row=0, column=1, sticky="ew", padx=(6, 0), pady=3)
        self.doctor_button = ttk.Button(actions, text="Run diagnostics", command=lambda: self.install("doctor"))
        self.doctor_button.grid(row=1, column=0, sticky="ew", padx=(0, 6), pady=3)
        self.uninstall_button = ttk.Button(actions, text="Uninstall…", command=lambda: self.install("uninstall"))
        self.uninstall_button.grid(row=1, column=1, sticky="ew", padx=(6, 0), pady=3)
        self.close_button = ttk.Button(actions, text="Close", command=self.close)
        self.close_button.grid(row=2, column=1, sticky="e", pady=(6, 0))
        for column in (0, 1):
            actions.columnconfigure(column, weight=1)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def payload(self) -> Path:
        source = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "HKS_PROGRAM.zip"
        if not source.is_file():
            raise FileNotFoundError("The embedded HKS_PROGRAM.zip payload is missing.")
        validate_archive(source)
        return source

    def install(self, action="install"):
        if os.name != "nt":
            messagebox.showerror("Windows required", "This installer runs on Windows.", parent=self.root)
            return
        try:
            if action in {"status", "doctor", "uninstall"}:
                target = Path(r"C:\HKS_PROGRAM")
                if not target.is_dir():
                    raise FileNotFoundError("HKS is not installed at C:\\HKS_PROGRAM yet.")
                if action == "uninstall":
                    if not messagebox.askyesno("Uninstall HKS", "Remove HKS services, tasks, and program files?", parent=self.root):
                        return
                    if not messagebox.askyesno("Confirm removal", "This will permanently remove C:\\HKS_PROGRAM. Create a Desktop backup of settings and the database first?", parent=self.root):
                        backup = "N"
                    else:
                        backup = "Y"
            archive = self.payload()
            self.temp_dir = Path(tempfile.mkdtemp(prefix="HKS_SETUP_"))
            with zipfile.ZipFile(archive) as bundle:
                base = self.temp_dir.resolve()
                for info in bundle.infolist():
                    destination = (base / info.filename).resolve()
                    if destination != base and base not in destination.parents:
                        raise ValueError("Unsafe path in embedded package.")
                bundle.extractall(base)
            names = {"install": "INSTALL_HKS.bat", "status": "CHECK_STATUS.bat",
                     "doctor": "HKS_DOCTOR.bat", "uninstall": "UNINSTALL_HKS.bat"}
            if action not in names:
                raise ValueError("Unknown setup action.")
            bat = (self.temp_dir / names[action] if action == "install" else
                   Path(r"C:\HKS_PROGRAM") / names[action])
            if not bat.is_file():
                raise FileNotFoundError(f"{bat.name} is missing. Repair the installation first.")
            self.log_path = self.temp_dir / "setup.log"
            wrapper = self.temp_dir / "HKS_GUI_INSTALL.bat"
            env_lines = ['@echo off', 'set HKS_GUI_INSTALL=1']
            if action == "uninstall":
                env_lines += ['set HKS_GUI_UNINSTALL=YES', f'set HKS_GUI_BACKUP={backup}',
                              'set HKS_GUI_CLEAN_DOWNLOADS=N']
            env_lines += [f'call "{bat}" > "{self.log_path}" 2>&1',
                          'exit /b %errorlevel%']
            wrapper.write_text('\r\n'.join(env_lines) + '\r\n', encoding="utf-8")
            params = f'/d /c call "{wrapper}"'
            info = ShellExecuteInfo()
            info.cbSize = ctypes.sizeof(info)
            info.fMask = 0x00000040  # SEE_MASK_NOCLOSEPROCESS
            info.hwnd = self.root.winfo_id()
            info.lpVerb = "runas"
            info.lpFile = os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe")
            info.lpParameters = params
            info.lpDirectory = str(self.temp_dir)
            info.nShow = 0  # hide command processor; Tk wizards remain visible
            shell32 = ctypes.WinDLL("shell32", use_last_error=True)
            shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(ShellExecuteInfo)]
            shell32.ShellExecuteExW.restype = wintypes.BOOL
            if not shell32.ShellExecuteExW(ctypes.byref(info)):
                error = ctypes.get_last_error()
                if error == 1223:
                    raise PermissionError("Administrator approval was cancelled. Click Install again to retry.")
                raise ctypes.WinError(error)
            self.process = info.hProcess
            for button in (self.install_button, self.status_button, self.doctor_button,
                           self.uninstall_button, self.close_button):
                button.configure(state="disabled")
            self.status.configure(text=f"{action.title()} is running. Approve the Windows prompt if shown.")
            self.progress.start(12)
            self.root.after(600, self.poll)
        except Exception as exc:
            if self.temp_dir:
                shutil.rmtree(self.temp_dir, ignore_errors=True)
                self.temp_dir = None
            messagebox.showerror("Could not start setup", str(exc), parent=self.root)

    def poll(self):
        if not self.process:
            return
        self.refresh_log()
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        result = kernel32.WaitForSingleObject(self.process, 0)
        if result == 0:
            exit_code = wintypes.DWORD()
            kernel32.GetExitCodeProcess(self.process, ctypes.byref(exit_code))
            kernel32.CloseHandle(self.process)
            self.process = None
            self.progress.stop()
            for button in (self.install_button, self.status_button, self.doctor_button,
                           self.uninstall_button, self.close_button):
                button.configure(state="normal")
            if exit_code.value == 0:
                self.status.configure(text="Operation finished. Review the output above for status and next steps.")
            else:
                self.refresh_log()
                self.status.configure(text=f"Setup failed (exit code {exit_code.value}). Details are shown above.")
                messagebox.showerror("HKS setup failed", self.last_log_text() or
                                     f"Installer exited with code {exit_code.value}.", parent=self.root)
            if self.temp_dir:
                self.cleanup()
            return
        self.root.after(600, self.poll)

    def last_log_text(self) -> str:
        try:
            with self.log_path.open("rb") as stream:
                stream.seek(0, os.SEEK_END)
                end = stream.tell()
                stream.seek(max(0, end - 12_000))
                tail = stream.read().decode("utf-8", errors="replace")
            return "\n".join(tail.splitlines()[-24:])[-3500:]
        except (OSError, AttributeError):
            return ""

    def refresh_log(self):
        content = self.last_log_text()
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        self.details.insert("end", content or "Waiting for installer output…")
        self.details.see("end")
        self.details.configure(state="disabled")

    def cleanup(self):
        if self.temp_dir:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
            self.temp_dir = None

    def close(self):
        if self.process:
            messagebox.showwarning("Setup is running", "Wait for the installer to finish before closing this window.", parent=self.root)
            return
        self.cleanup()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    if "--verify-package" in sys.argv:
        try:
            validate_archive(Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "HKS_PROGRAM.zip")
        except Exception as exc:
            print(str(exc))
            raise SystemExit(1)
        raise SystemExit(0)
    SetupApp().run()
