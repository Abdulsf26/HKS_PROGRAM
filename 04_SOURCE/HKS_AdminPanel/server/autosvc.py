"""HKS round 68 - automation services (F19/F31/F32/F33/F34/F43).

Everything here is FILE-DRIVEN and OFF by default: no jobs file, no rules
file, no channels file -> the loops idle and cost nothing. No new
dependencies: notifications use urllib, cron is a simple every-N-seconds
spec (honest about what it does, no half-parsed cron grammar).

Files (all in the panel data dir):
  panel_jobs.json     [{"name","action","params","machine","every_s","enabled"}]
  alert_rules.json    [{"name","metric","op","threshold","duration_s","cooldown_s","machine","enabled"}]
  notify_channels.json {"ntfy":     {"enabled","url"},
                        "telegram": {"enabled","bot_token","chat_id"},
                        "webhook":  {"enabled","url","secret","events":["*"]}}
  fleet.json          [{"name","url","token"}]
"""
import asyncio
import hashlib
import hmac as _hmac
import json
import os
import time
import urllib.request

_STATE = {"data_dir": "", "hub": None, "boot": time.time()}
_alert_state = {}


def init(data_dir, hub):
    _STATE["data_dir"] = data_dir
    _STATE["hub"] = hub


def _load(name, default):
    try:
        with open(os.path.join(_STATE["data_dir"], name), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


# ---------------- F34/F43: notification channels + signed webhooks ----------
def _post(url, data, headers=None, timeout=8):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status

def sign_payload(secret, obj):
    payload = json.dumps(obj, sort_keys=True).encode()
    return _hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

async def send_all(title, msg, event_kind=None, event_data=None):
    chans = _load("notify_channels.json", {}) or {}
    for name, cfg in chans.items():
        if not isinstance(cfg, dict) or not cfg.get("enabled"):
            continue
        try:
            if name == "ntfy" and cfg.get("url"):
                await asyncio.to_thread(_post, cfg["url"], msg.encode(),
                                        {"Title": title, "Priority": "default"})
            elif name == "telegram" and cfg.get("bot_token") and cfg.get("chat_id"):
                url = (f"https://api.telegram.org/bot{cfg['bot_token']}/sendMessage?"
                       f"chat_id={cfg['chat_id']}")
                body = json.dumps({"text": f"{title}\n{msg}"}).encode()
                await asyncio.to_thread(_post, url, body, {"Content-Type": "application/json"})
            elif name == "webhook" and cfg.get("url"):
                want = cfg.get("events") or ["*"]
                if event_kind and "*" not in want and event_kind not in want:
                    continue
                payload = {"kind": event_kind or "notify", "title": title,
                           "message": msg, "data": event_data or {}, "ts": time.time()}
                sig = sign_payload(cfg.get("secret", ""), payload)
                await asyncio.to_thread(_post, cfg["url"], json.dumps(payload).encode(),
                                        {"Content-Type": "application/json",
                                         "X-HKS-Signature": sig})
        except Exception:
            pass  # a dead channel must never break the caller

# ---------------- F32: panel-side scheduler (every-N jobs) ------------------
def is_due(job, now):
    if not job.get("enabled", True):
        return False
    every = max(30, int(job.get("every_s", 300)))
    return now - float(job.get("last_run", 0)) >= every

def _save_jobs(jobs):
    with open(os.path.join(_STATE["data_dir"], "panel_jobs.json"), "w", encoding="utf-8") as f:
        json.dump(jobs, f, indent=2)

async def _scheduler_loop():
    while True:
        try:
            jobs = _load("panel_jobs.json", [])
            if jobs:
                hub = _STATE["hub"]
                changed = False
                for job in jobs:
                    now = time.time()
                    if not is_due(job, now) or hub is None:
                        continue
                    job["last_run"] = now
                    changed = True
                    mid = job.get("machine") or hub.first_machine()
                    if mid:
                        try:
                            await hub.command(mid, job["action"], job.get("params") or {},
                                              timeout=60, user="scheduler")
                        except Exception:
                            pass
                if changed:
                    _save_jobs(jobs)
        except Exception:
            pass
        await asyncio.sleep(20)

# ---------------- F33: alert rules on telemetry -----------------------------
def eval_rule(rule, value, state, now):
    """Pure: returns 'fire' | 'hold' | 'clear' for this rule+sample."""
    if value is None:
        return "clear"
    op, thr = rule.get("op", ">"), rule.get("threshold", 0)
    hit = (op == ">" and value > thr) or (op == "<" and value < thr) or \
          (op == ">=" and value >= thr) or (op == "<=" and value <= thr)
    dur = max(0, int(rule.get("duration_s", 0)))
    cd = max(0, int(rule.get("cooldown_s", 1800)))
    st = state.setdefault(rule.get("name", "?"), {"since": 0, "last": 0})
    if hit:
        if not st["since"]:
            st["since"] = now
        if now - st["since"] >= dur and now - st["last"] >= cd:
            st["last"] = now
            return "fire"
        return "hold"
    st["since"] = 0
    return "clear"

async def on_telemetry(machine_id, tel):
    rules = _load("alert_rules.json", [])
    if not rules:
        return
    for r in rules:
        if not r.get("enabled", True):
            continue
        if r.get("machine") and r["machine"] != machine_id:
            continue
        val = tel.get(r.get("metric", "cpu"))
        if val is None and r.get("metric") == "ram":
            val = tel.get("mem")
        verdict = eval_rule(r, val, _alert_state, time.time())
        if verdict == "fire":
            msg = f"{r.get('name')}: {r.get('metric')} {val} {r.get('op')} {r.get('threshold')} on {machine_id}"
            from .db import db
            db.add_event(machine_id, "alert_fired", {"rule": r.get("name"), "value": val})
            await send_all("HKS alert", msg, "alert_fired", {"rule": r.get("name"), "value": val})

async def _alerts_probe_loop():
    # machines that stop streaming telemetry must not leave alerts stuck
    while True:
        await asyncio.sleep(60)
        hub = _STATE["hub"]
        if hub is not None:
            for mid, tel in list(hub.latest_telemetry.items()):
                try:
                    await on_telemetry(mid, tel)
                except Exception:
                    pass

# ---------------- F19: Prometheus text (opt-in via HKS_METRICS_ENABLED=1) ---
def metrics_text(hub, boot=None):
    lines = ["# HELP hks_agents_connected Connected agents",
             "# TYPE hks_agents_connected gauge",
             f"hks_agents_connected {len(hub.agents)}",
             "# HELP hks_dashboards_connected Active dashboard websockets",
             "# TYPE hks_dashboards_connected gauge",
             f"hks_dashboards_connected {len(hub.dashboards)}",
             "# HELP hks_uptime_seconds Panel process uptime",
             "# TYPE hks_uptime_seconds counter",
             f"hks_uptime_seconds {int(time.time() - (boot or _STATE['boot']))}"]
    for mid, tel in hub.latest_telemetry.items():
        cpu = tel.get("cpu", 0)
        mem = tel.get("mem", tel.get("ram", 0))
        safe = mid.replace('"', "")
        lines += [f'hks_agent_cpu_percent{{machine="{safe}"}} {cpu}',
                  f'hks_agent_mem_percent{{machine="{safe}"}} {mem}']
    return "\n".join(lines) + "\n"

# ---------------- F31: fleet (read-only peer aggregation, opt-in) -----------
async def fetch_fleet():
    peers = _load("fleet.json", [])
    out = []
    for p in peers:
        try:
            req = urllib.request.Request(
                p["url"].rstrip("/") + "/status/" + p.get("token", "") + "/json")
            with urllib.request.urlopen(req, timeout=8) as r:
                d = json.loads(r.read())
            d["_panel"] = p.get("name", p["url"])
            out.append(d)
        except Exception as e:
            out.append({"_panel": p.get("name", "?"), "error": str(e)})
    return out

def loops():
    return [_scheduler_loop(), _alerts_probe_loop()]
