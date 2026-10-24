

def _hks_root():
    # dual-layout (round 62): zip root has 03_TESTS + HKS_Client + 04_SOURCE
    # as siblings; dev workspace keeps the trees under 04_SOURCE. Probe both.
    up2 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.isdir(os.path.join(up2, "HKS_Client")):
        return up2                       # zip/delivered layout
    return os.path.join(up2, "04_SOURCE")  # dev workspace layout
"""Round-4 regression suite — every defect from the user's field report:

1. volume.* "'AudioDevice' object has no attribute 'Activate'"  (panel Errors)
   + volume.set's nonexistent SetMasterVolumeScalar method
2. camera: OpenCV default backend never opens on the Acer
   ("OpenCV should be configured with libavdevice" + "Camera index out of
   range" in session_console.log)
3. session agent double-launch (logon task + Startup .bat) superseding each
   other -> streams/telemetry constantly killed; SYSTEM/session-0 instance
   can never capture a desktop
4. dashboard CPU/MEM/BATTERY cards empty: one failing psutil sub-call made
   sysinfo.snapshot() return None -> telemetry never sent; and browsers with
   a dead live websocket had no REST fallback
5. files browser "gets stuck": no client-side timeout / no loading state
6. SQL "not working / returning 200": default path silently created an
   EMPTY database -> "no tables"; no way to find real .db files
"""
import os
import sys
import types

HERE = _hks_root()
ROOT = HERE  # <root> = 04_SOURCE (repo) or zip root
sys.path.insert(0, os.path.join(ROOT, "HKS_Client"))
os.environ.setdefault("HKS_DATA_DIR", os.path.join(HERE, "_r4_tmp"))
os.makedirs(os.environ["HKS_DATA_DIR"], exist_ok=True)

OK = []


def check(name, cond, detail=""):
    if cond:
        OK.append(name)
        print(f"  PASS  {name}")
    else:
        raise AssertionError(f"{name} {detail}")


def test_volume_code():
    src = open(os.path.join(ROOT, "HKS_Client/agent/session_modules/desktop.py"), encoding="utf-8").read()
    check("volume: AudioUtilities.GetSpeakers().Activate gone",
          "AudioUtilities.GetSpeakers()\n        interface = dev.Activate" not in src)
    check("volume: SetMasterVolumeScalar typo gone (real: SetMasterVolumeLevelScalar)",
          "SetMasterVolumeScalar(" not in src and "SetMasterVolumeLevelScalar(" in src)
    check("volume: raw COM enumerator helper exists",
          "_endpoint_volume" in src and "CLSID_MMDeviceEnumerator" in src
          and "GetDefaultAudioEndpoint" in src)
    check("volume: enum .value compat shim (pycaw 2025 plain Enums)",
          'getattr(EDataFlow.eRender, "value"' in src)


def main():
    print("== round-4 field-fix regression ==")

    # 1) volume -----------------------------------------------------------
    print("[volume]")
    test_volume_code()

    # 2) camera backend order ---------------------------------------------
    print("[camera]")
    calls = []

    class FakeCap:
        def __init__(self, backend):
            self.backend = backend
            self.released = False
            self.reads = 0

        def isOpened(self):
            return self.backend != "CAP_MSMF"  # msmf "opens" but is dead

        def read(self):
            self.reads += 1
            import types as _t
            return (self.backend != "CAP_MSMF",
                    _t.SimpleNamespace(size=3))  # proof-frame check needs .size

        def set(self, *a):
            pass

        def release(self):
            self.released = True

    fake_cv2 = types.SimpleNamespace(
        CAP_DSHOW="CAP_DSHOW", CAP_MSMF="CAP_MSMF", CAP_ANY="CAP_ANY",
        CAP_PROP_FRAME_WIDTH=3, CAP_PROP_FRAME_HEIGHT=4,
        IMWRITE_JPEG_QUALITY=1,
        VideoCapture=lambda idx, be=None: (calls.append(be),
                                           FakeCap(be or "CAP_ANY"))[1],
        imencode=lambda *a, **k: (True, b"jpg"))

    real_cv2 = sys.modules.get("cv2")
    real_osname = os.name
    os.name = "nt"  # simulate the Acer (Windows backend ordering)
    sys.modules["cv2"] = fake_cv2
    try:
        from agent.session_modules import camera as cam_mod
        cap = cam_mod._open_cam(0)
        check("camera: DirectShow tried FIRST on Windows",
              calls[0] == "CAP_DSHOW", f"got {calls}")
        check("camera: proof-frame read before accepting the backend",
              cap.reads >= 1 and cap.backend == "CAP_DSHOW")
        # all backends dead -> clear error, not silent
        fake_cv2.VideoCapture = lambda idx, be=None: (calls.append(be),
                                                      FakeCap("CAP_MSMF"))[1]
        calls.clear()
        try:
            cam_mod._open_cam(0)
            raise AssertionError("camera: all-fail must raise")
        except RuntimeError as e:
            check("camera: total failure raises a CLEAR error",
                  "DirectShow" in str(e) and "no working camera" in str(e), str(e))
        check("camera: all 3 backends attempted on failure", len(calls) == 3, str(calls))
    finally:
        if real_cv2 is not None:
            sys.modules["cv2"] = real_cv2
        else:
            sys.modules.pop("cv2", None)
        os.name = real_osname

    # Login capture and explicit camera actions can arrive together. Keep the
    # native OpenCV camera handle single-owner to avoid Windows driver heap
    # corruption when two threads open the same webcam.
    import threading as _threading
    import time as _time
    state = {"active": 0, "peak": 0}
    gate = _threading.Barrier(3)
    def _overlap_probe():
        state["active"] += 1
        state["peak"] = max(state["peak"], state["active"])
        _time.sleep(0.03)
        state["active"] -= 1
    serialized = cam_mod._camera_serialized(_overlap_probe)
    def _start_serialized():
        gate.wait()
        serialized()
    workers = [_threading.Thread(target=_start_serialized) for _ in range(2)]
    for worker in workers:
        worker.start()
    gate.wait()
    for worker in workers:
        worker.join(timeout=2)
    check("camera: concurrent OpenCV sessions are serialized",
          state["peak"] == 1 and all(not w.is_alive() for w in workers), str(state))

    # 3) session_main single-instance + session-0 guard ---------------------
    print("[session guard]")
    src = open(os.path.join(ROOT, "HKS_Client/agent/session_main.py"), encoding="utf-8").read()
    check("guard: session-0 detection present", "ProcessIdToSessionId" in src)
    check("guard: machine-wide mutex present", "CreateMutexW" in src
          and "HKS_SessionAgent_single" in src)
    check("guard: supervising_main honors preflight (exit-code contract)",
          "rc = preflight()" in src)
    from agent import session_main as sm
    check("guard: imports cleanly on non-Windows", hasattr(sm, "preflight"))
    # Keep this regression isolated from a real Windows session agent running
    # on the developer/test laptop.
    _preflight_orig = (sm._win_session_id, sm._single_instance_lock,
                       sm._other_session_agent_alive)
    sm._win_session_id = lambda: None
    sm._single_instance_lock = lambda: True
    sm._other_session_agent_alive = lambda: False
    try:
        _preflight_ok = sm.preflight() == 0
    finally:
        (sm._win_session_id, sm._single_instance_lock,
         sm._other_session_agent_alive) = _preflight_orig
    check("guard: preflight passes with isolated non-Windows probes", _preflight_ok)

    # 4) telemetry hardening ------------------------------------------------
    print("[telemetry]")
    real_psutil = sys.modules.get("psutil")

    class BoomBattery:
        def cpu_percent(self, interval=0):
            return 7.0

        def virtual_memory(self):
            return types.SimpleNamespace(percent=55.0)

        def sensors_battery(self):
            raise RuntimeError("battery probe exploded (field case)")

        def net_io_counters(self):
            return types.SimpleNamespace(bytes_sent=1, bytes_recv=2)

        def net_if_addrs(self):
            return {}

    sys.modules["psutil"] = BoomBattery()
    try:
        from agent.modules import sysinfo
        snap = sysinfo.snapshot()
        check("telemetry: battery failure no longer nukes the payload",
              isinstance(snap, dict) and snap.get("cpu") == 7.0
              and snap.get("ram") == 55.0, str(snap))
        check("telemetry: battery fields degrade to None",
              snap.get("bat_pct") is None and snap.get("bat_plugged") is None)
    finally:
        if real_psutil is not None:
            sys.modules["psutil"] = real_psutil
        else:
            sys.modules.pop("psutil", None)

    appjs = open(os.path.join(ROOT, "HKS_AdminPanel/server/static/app.js"), encoding="utf-8").read()
    idxhtml = open(os.path.join(ROOT, "HKS_AdminPanel/server/static/index.html"), encoding="utf-8").read()
    check("telemetry: REST fallback poll exists in the UI",
          "/api/machines" in appjs and "lastTel" in appjs)
    check("live channel: wsPill visible in the header", "wsPill" in idxhtml
          and "setPill" in appjs)

    # 5) files/REST timeout --------------------------------------------------
    print("[files + live-channel fallbacks]")
    check("files: hard client-side fetch timeout", "AbortController" in appjs
          and "server timeout" in appjs)
    check("files: loading status text", 'status.textContent = "loading "' in appjs)
    check("live: 3-tier engine (ws -> REST frames -> stills) for screen & camera",
          'startStills(key, sel, channel === 1 ? "screen.snapshot" : "camera.snap"' in appjs
          and 'function startLive' in appjs and 'function stopLive' in appjs)
    check("wsCmd: waits for the live channel instead of instant reject",
          "live channel offline" in appjs and "attempt" in appjs)
    check("streams: auto-resume after live channel reconnects",
          "scrParams" in appjs and "camParams" in appjs)

    # 6) SQL -----------------------------------------------------------------
    print("[sql]")
    from agent.modules import sql_access
    import tempfile
    import sqlite3
    tmpdb = os.path.join(tempfile.mkdtemp(), "real.db")
    c = sqlite3.connect(tmpdb)
    c.execute("CREATE TABLE stuff (id INTEGER, name TEXT)")
    c.execute("INSERT INTO stuff VALUES (1, 'hks')")
    c.commit()
    c.close()
    try:
        sql_access._connect({"engine": "sqlite", "path": tmpdb + ".missing"})
        raise AssertionError("missing sqlite file must raise")
    except FileNotFoundError as e:
        check("sql: missing file -> clear error (no silent empty db)",
              "not found" in str(e) and "Find DB" in str(e))
    conn, _ = sql_access._connect({"engine": "sqlite", "path": tmpdb})
    conn.close()
    check("sql: real file still opens", True)

    ctx_stub = types.SimpleNamespace(cfg={"sqlite_defaults": {}})
    res = sql_access.register(ctx_stub)["db.find"](ctx_stub, {"path": os.path.dirname(tmpdb)})
    paths = [it["path"] for it in res["items"]]
    check("sql: db.find locates real .db files", tmpdb in paths, str(paths))

    mainjs = open(os.path.join(ROOT, "HKS_AdminPanel/server/main.py"), encoding="utf-8").read()
    check("panel: /api/machines carries latest telemetry",
          'r["telemetry"] = hub.latest_telemetry.get' in mainjs)

    # 7) phone reachability (network may change at any time) -----------------
    print("[reachability]")
    sm_src = open(os.path.join(ROOT, "HKS_Client/agent/session_main.py"), encoding="utf-8").read()
    check("toast: reads the panel endpoint from config.json",
          "_read_panel_endpoint" in sm_src and "server_url" in sm_src)
    check("toast: computes the LIVE LAN ip (retries after logon)",
          "_lan_ip" in sm_src and "10.255.255.255" in sm_src)
    check("toast: shows the phone URL + CHECK_STATUS advice",
          "Control from your phone" in sm_src and "CHECK_STATUS" in sm_src)
    from agent import session_main as sm2
    sch, prt = sm2._read_panel_endpoint()
    check("endpoint parse: tolerates missing config", sch is None and prt is None,
          f"got {sch},{prt}")
    lp_src = open(os.path.join(ROOT, "HKS_Client/agent/local_panel.py"), encoding="utf-8").read()
    check("status page: shows the current phone URL",
          "_phone_url" in lp_src and "Open from phone" in lp_src
          and "panel_for_phone" in lp_src)
    lp_stub = types.SimpleNamespace(cfg={"server_url": "wss://127.0.0.1:6767/ws/agent"})
    sys.path.insert(0, os.path.join(ROOT, "HKS_Client"))
    from agent import local_panel as lpm
    got = lpm._phone_url(lp_stub)
    check("status page: _phone_url returns a usable url-or-empty string",
          isinstance(got, str), got)
    fr_src = open(os.path.join(ROOT, "first_run.py"), encoding="utf-8").read()
    check("installer: firewall rule verified + self-heal",
          "_fw_ok" in fr_src and "COULD NOT OPEN PORT" in fr_src)
    check("installer: CHECK_STATUS prints live phone URLs",
          "OPEN: __SCHEME__://" in fr_src and "Get-NetIPAddress" in fr_src)

    # 8) watchdog must identify whichever role owns the negotiated port ---
    print("[watchdog role probe]")
    wd_src = open(os.path.join(ROOT, "HKS_Client/agent/watchdog.py"), encoding="utf-8").read()
    check("watchdog: probes status.json role across the shared 8590-8594 range",
          "def role_answers" in wd_src and 'get("role") == role' in wd_src
          and '"agent": list(range(8590, 8595))' in wd_src
          and '"session": list(range(8590, 8595))' in wd_src)
    lp_src_wd = open(os.path.join(ROOT, "HKS_Client/agent/local_panel.py"), encoding="utf-8").read()
    check("local status: Windows listeners claim exclusive ports",
          "ThreadingHTTPServer.allow_reuse_address = False" in lp_src_wd)
    from agent import watchdog as wd
    old_probe = wd.role_answers
    wd.role_answers = lambda host, ports, role: role == "session" and 8590 in ports
    fails = {"agent": 2, "session": 2}
    try:
        due = wd.tick({"agent": [8590, 8591], "session": [8590, 8591]}, fails)
    finally:
        wd.role_answers = old_probe
    check("watchdog: session on 8590 resets session failures without relaunch",
          due == ["agent"] and fails["session"] == 0, f"due={due}, fails={fails}")

    # 8) live control + live camera without the browser websocket ----------
    print("[live engine r5]")
    scr_src = open(os.path.join(ROOT, "HKS_Client/agent/session_modules/screen.py"), encoding="utf-8").read()
    inp_src = open(os.path.join(ROOT, "HKS_Client/agent/session_modules/input_ctl.py"), encoding="utf-8").read()
    cam_src = open(os.path.join(ROOT, "HKS_Client/agent/session_modules/camera.py"), encoding="utf-8").read()
    sm_src2 = open(os.path.join(ROOT, "HKS_Client/agent/session_main.py"), encoding="utf-8").read()
    main_src2 = open(os.path.join(ROOT, "HKS_AdminPanel/server/main.py"), encoding="utf-8").read()
    app_src = open(os.path.join(ROOT, "HKS_AdminPanel/server/static/app.js"), encoding="utf-8").read()
    check("input: gate decoupled from the mirror (set_input_enabled)",
          "def set_input_enabled" in scr_src and "input_enabled" in scr_src)
    check("input: mirror-on implies control, mirror-off leaves the toggle",
          'mirror off must NOT kill an explicit control toggle' in scr_src)
    check("input: input.enable action exists",
          '"input.enable": enable' in inp_src)
    check("input: failsafe hook clears control on disconnect",
          "disconnect_hooks.append" in inp_src and "set_input_enabled(False)" in inp_src)
    # functional: control works with the mirror OFF
    from agent.session_modules import screen as scr_mod
    scr_mod._state["on"] = False
    scr_mod._state["input_enabled"] = True
    check("input: functional — control ON while mirror OFF",
          scr_mod.is_input_enabled() is True)
    scr_mod.set_input_enabled(False)
    check("input: functional — set_input_enabled(False) gates it",
          scr_mod.is_input_enabled() is False)
    # input_ctl enable/suppress path (no pynput needed for suppressed path)
    from agent.session_modules import input_ctl as inp_mod
    ctx_stub2 = types.SimpleNamespace(disconnect_hooks=[], log=None)
    acts = inp_mod.register(ctx_stub2)
    r = acts["input.enable"](ctx_stub2, {"on": True})
    check("input: enable action turns it on + clears failsafe",
          r.get("enabled") is True and scr_mod.is_input_enabled() is True)
    r = acts["input.apply"](ctx_stub2, {"t": "mm", "x": 1, "y": 1})
    check("input: apply accepted (not suppressed) when enabled",
          not r.get("suppressed", False), str(r))
    r = acts["input.enable"](ctx_stub2, {"on": False})
    check("input: enable(off) gates it",
          r.get("enabled") is False and scr_mod.is_input_enabled() is False)
    r = acts["input.apply"](ctx_stub2, {"t": "mm", "x": 1, "y": 1})
    check("input: apply suppressed when disabled",
          r.get("suppressed") is True and r.get("ok") is True, str(r))
    check("camera: stream-thread open retries (5 attempts)",
          "for _attempt in range(5)" in cam_src)
    check("visibility: module load failures reach the panel Errors",
          'send_error("session_main"' in sm_src2)
    check("server: /api/frame endpoint + latest_frames cache",
          '"/api/frame"' in main_src2 and "latest_frames" in main_src2)
    check("ui: live engine present (startLive/stopLive/frame poll/input REST)",
          "function startLive" in app_src and "/api/frame?ch=" in app_src
          and '"input.enable"' in app_src and 'cmd("input.apply", ev, 15)' in app_src)
    check("ui: backup-mode messaging on audio buttons",
          app_src.count("reinstall on the laptop choosing HTTP") >= 2)

    print(f"\nROUND-4 FIX REGRESSION: {len(OK)} checks PASSED")


if __name__ == "__main__":
    main()
