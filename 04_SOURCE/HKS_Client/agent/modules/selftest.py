"""HKS round 68 (F17): one-action self-test of every SAFE agent capability.

Read-only actions only, minimal params, failures collected - never raised.
The panel Settings tab and HKS_DOCTOR can call selftest.run and show the
user exactly which subsystems answer and which do not.
"""

SAFE = {
    "sys.quick": {}, "sys.info": {}, "hw.status": {}, "files.drives": {},
    "apps.processes": {}, "net.adapters": {}, "net.internet_check": {},
    "session.status": {}, "volume.get": {}, "camera.list": {}, "mic.list": {},
}

# never run these even if someone edits SAFE later
FORBIDDEN = ("delete", "shutdown", "restart", "terminate", "write", "extract",
             "uninstall", "kill", "format", "wipe", "logoff", "hibernate")


def register(ctx):
    def run(c, p):
        results = []
        acts = getattr(c, "actions", {}) or {}
        for action, params in SAFE.items():
            if any(b in action for b in FORBIDDEN):
                continue
            fn = acts.get(action)
            if not fn:
                results.append({"action": action, "ok": None, "note": "not registered"})
                continue
            try:
                r = fn(c, params)
                ok = not (isinstance(r, dict) and r.get("ok") is False)
                results.append({"action": action, "ok": ok, "sample": str(r)[:80]})
            except Exception as e:
                results.append({"action": action, "ok": False,
                                "error": type(e).__name__ + ": " + str(e)})
        return {"tested": len(results),
                "passed": sum(1 for r in results if r["ok"] is True),
                "failed": sum(1 for r in results if r["ok"] is False),
                "absent": sum(1 for r in results if r["ok"] is None),
                "results": results}
    return {"selftest.run": run}
