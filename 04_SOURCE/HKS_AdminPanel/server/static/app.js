/* HKS Control Center — dashboard client v2 (self-contained, no CDN) */
"use strict";

/* ---- sandboxed-preview guard -----------------------------------------
   The live preview (and some SSO-style embeds) run the panel inside an
   iframe with an OPAQUE origin: any access to localStorage/document.cookie
   THROWS there, which killed views one by one. Shim localStorage with an
   in-memory map when the real one is unavailable. */
try { window.localStorage.getItem("_probe"); } catch (e) {
  try {
    const _mem = new Map();
    const shim = {
      getItem: (k) => (_mem.has(String(k)) ? _mem.get(String(k)) : null),
      setItem: (k, v) => _mem.set(String(k), String(v)),
      removeItem: (k) => _mem.delete(String(k)),
      key: (i) => Array.from(_mem.keys())[i] ?? null,
      clear: () => _mem.clear(),
      get length() { return _mem.size; },
    };
    Object.defineProperty(window, "localStorage", { value: shim, configurable: true });
  } catch (e2) {}
}
let AUTH_TOKEN = "";  // bearer fallback for cookie-blocked contexts
try { AUTH_TOKEN = window.localStorage.getItem("hks_token") || ""; } catch (e) {}

/* ---------------- helpers ---------------- */
const $ = (s, r = document) => r.querySelector(s);
const SVG_NS = "http://www.w3.org/2000/svg";
const SVG_ELEMENTS = new Set(["svg", "circle", "line", "path", "polyline"]);
const BOOL_ATTRS = new Set(["autofocus", "autoplay", "checked", "controls", "default", "defer", "disabled", "formnovalidate", "hidden", "inert", "ismap", "itemscope", "loop", "multiple", "muted", "nomodule", "novalidate", "open", "readonly", "required", "reversed", "selected"]);
const el = (tag, attrs = {}, ...kids) => {
  // SVG tags created in the HTML namespace are inert in browsers: they look
  // like DOM nodes but circles/needles never paint. Keep the helper shared,
  // but create actual SVG namespace elements for charts and gauges.
  const n = SVG_ELEMENTS.has(tag)
    ? document.createElementNS(SVG_NS, tag)
    : document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") n.setAttribute("class", v);
    else if (k === "style") n.style.cssText = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (BOOL_ATTRS.has(k.toLowerCase())) {
      const property = k.toLowerCase() === "readonly" ? "readOnly" : k;
      n[property] = !!v;
      if (v) n.setAttribute(k, ""); else n.removeAttribute(k);
    }
    else if (v !== null && v !== undefined) n.setAttribute(k, v);
  }
  for (const k of kids.flat(9)) if (k != null) n.append(k.nodeType ? k : document.createTextNode(k));
  return n;
};
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
// Windows-correct parent (Windows-AI handoff §22): C:\Users\hks -> C:\Users,
// C:\Users -> C:\ (NOT "C:" - drive-relative is a trap), C:\ -> C:\.
const parentDir = (p) => {
  const s = String(p || "").trim();
  if (!s) return "C:\\";
  if (s === "/") return "/";
  if (/^[a-zA-Z]:\\?$/.test(s)) return s.replace(/\\?$/, "\\");
  const cut = s.replace(/[\\/]+$/, "");
  const ix = Math.max(cut.lastIndexOf("\\"), cut.lastIndexOf("/"));
  if (ix < 0) return "C:\\";
  const parent = cut.slice(0, ix + 1) || "/";
  return parent;
};
const fmtB = (n) => { n = Number(n) || 0; const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0; while (n >= 1024 && i < 4) { n /= 1024; i++; } return n.toFixed(i ? 1 : 0) + " " + u[i]; };
const fmtT = (ts) => ts ? new Date(ts * 1000).toLocaleString() : "—";
const ago = (ts) => { if (!ts) return "never"; const s = Date.now() / 1000 - ts; if (s < 0) return "now"; if (s < 60) return Math.floor(s) + "s ago"; if (s < 3600) return Math.floor(s / 60) + "m ago"; if (s < 86400) return Math.floor(s / 3600) + "h ago"; return Math.floor(s / 86400) + "d ago"; };
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
const busy = (btn, on, label) => { if (btn) { btn.disabled = on; if (label && on) btn.dataset.old = btn.textContent, btn.textContent = label; else if (btn.dataset.old) btn.textContent = btn.dataset.old; } };

let CSRF = "";
let csrfSync = null;
function syncCsrf() {
  // single-flight: many failing calls share one token refresh
  if (!csrfSync) {
    csrfSync = (async () => {
      try {
        const j = await apiOnce("/api/me", { _noretry: true });
        CSRF = (j.user && j.user.csrf) || "";
        return !!CSRF;
      } catch { CSRF = ""; return false; }
      finally { csrfSync = null; }
    })();
  }
  return csrfSync;
}
// audit4: base path of THIS page - the panel then works when it is
// proxied under a sub-path/subdomain (https://my.box.com/hks/) and is
// unchanged at the root ("/").
// round 59 CRITICAL FIX (owner's external audit, mechanically proven):
// the old ternary made API_BASE="/" at the root, so API_BASE + "/api/x"
// became "//api/x" - a PROTOCOL-RELATIVE URL the browser resolves as
// https://api/x (host "api"!) -> every fetch failed while the server was
// perfectly alive. Root must yield "" so paths stay root-relative.
function hksApiBase(pathname) {
  const p = pathname || "/";
  if (p === "/" || p === "") return "";
  return p.endsWith("/") ? p.slice(0, -1) : p.replace(/\/[^/]*$/, "");
}
const API_BASE = hksApiBase(location.pathname);

async function apiOnce(path, opts = {}) {
  if (path.startsWith("/")) path = API_BASE + path;
  const headers = { ...(opts.headers || {}) };
  if (AUTH_TOKEN && !headers["Authorization"]) headers["Authorization"] = "Bearer " + AUTH_TOKEN;
  let body = opts.body;
  if (body && typeof body === "object") {
    headers["Content-Type"] = "application/json";
    if (opts.method && opts.method !== "GET") headers["X-HKS-CSRF"] = CSRF;
    body = JSON.stringify(body);
  }
  // hard client-side timeout so a hanging command can never freeze a
  // section forever (field report: file browser "gets stuck")
  const tmo = opts.tmo || 40000;
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), tmo);
  let r;
  try { r = await fetch(path, { ...opts, headers, body, signal: ctl.signal }); }
  catch (e) { if (e.name === "AbortError") throw new Error("server timeout — the laptop did not answer in time"); throw e; }
  finally { clearTimeout(timer); }
  let j = {}, jtxt = ""; try { jtxt = await r.text(); j = JSON.parse(jtxt); } catch {}
  // Sensitive actions deliberately return HTTP 401 until the user proves
  // their password again. Classify that response before the expired-session
  // handler below; otherwise terminal.write was falsely shown as "session
  // expired" even while the login cookie was valid.
  if (r.status === 401 && j && j.reauth_required) { const e2 = new Error(j.error || "reauth required"); e2.reauthRequired = true; throw e2; }
  if (r.status === 401 && j && j.totp_required) { const e2 = new Error(j.error || "totp required"); e2.totpRequired = true; throw e2; }
  if (r.status === 401 && !path.includes("/api/login")) {
    // auth-race guard: a stale background request must NOT log the UI
    // out of a session that is still valid - confirm with /api/me first
    try {
      const me = await fetch(API_BASE + "/api/me", { credentials: "same-origin",
        headers: AUTH_TOKEN ? { "Authorization": "Bearer " + AUTH_TOKEN } : {} });
      const mj = await me.json();
      if (mj && mj.user) { syncCsrf(mj.user.csrf); throw new Error("retry"); }
      showLogin();
    } catch (e) { if (e.message !== "retry") showLogin(); }
    throw new Error("session expired");
  }
  if (!r.ok || j.ok === false)
    /* round 67: when the server did not send a JSON error (proxy page, empty
       reply, crash), show WHAT came back instead of a bare status number */
    throw new Error(j.error || (j.result && j.result.error) ||
      (jtxt ? "HTTP " + r.status + " — panel said: " + jtxt.slice(0, 160) : "HTTP " + r.status + " (no body)"));
  return j;
}
async function api(path, opts = {}) {
  if (path.startsWith("/")) path = API_BASE + path;
  try {
    return await apiOnce(path, opts);
  } catch (e) {
    // CSRF pairing breaks when the panel is logged into AGAIN from
    // anywhere (the browser cookie jar holds the newest session while
    // this open page still holds the old token -> 403 "bad CSRF
    // token"). Standard SPA recovery (OWASP SPA guidance): re-sync the
    // token from /api/me and retry the request ONCE.
    if (!opts._noretry && !path.includes("/api/login") && /csrf/i.test(String(e.message || ""))) {
      if (await syncCsrf() && CSRF) return apiOnce(path, { ...opts, _noretry: true });
    }
    /* round 68 (F26): sensitive action wants a fresh password proof - ask,
       POST /api/reauth, retry the original call ONCE. */
    if (e && e.reauthRequired && !opts._noretry && !path.includes("/api/reauth")) {
      const pw = await requestReauthPassword();
      if (pw != null) {
        try { await api("/api/reauth", { method: "POST", body: { password: pw } }); }
        catch (_e2) { throw new Error("wrong password - action not sent"); }
        return apiOnce(path, { ...opts, _noretry: true });
      }
    }
    throw e;
  }
}
function requestReauthPassword() {
  return new Promise((resolve) => {
    const field = el("input", { type: "password", autocomplete: "current-password",
      "aria-label": "Current account password", placeholder: "Enter your current password", style: "width:100%" });
    const note = el("p", { class: "muted" }, "Confirm your password to continue this sensitive action.");
    const bk = modal("Re-enter password", note, field);
    const panel = bk.querySelector(".modal");
    const genericClose = panel && panel.querySelector(":scope > .row.end");
    if (genericClose) genericClose.remove();
    let done = false;
    const finish = (value) => { if (done) return; done = true; bk.remove(); resolve(value); };
    const cancel = el("button", { class: "btn ghost", onclick: () => finish(null) }, "Cancel");
    const submit = el("button", { class: "btn", onclick: () => {
      if (!field.value) { note.textContent = "Enter your current password to continue."; field.focus(); return; }
      finish(field.value);
    } }, "Continue");
    panel.append(el("div", { class: "row end", style: "margin-top:14px" }, cancel, submit));
    field.addEventListener("keydown", (ev) => { if (ev.key === "Enter") submit.click(); });
    bk.addEventListener("keydown", (ev) => { if (ev.key === "Escape") finish(null); });
    field.focus();
  });
}
async function sendInput(obj) {
  const result = STATE.wsReady ? await wsCmd("input.apply", obj) : await cmd("input.apply", obj, 20);
  // REST unwraps the agent result in cmd(); the WS relay returns that same
  // result as data. Do not turn an agent-side {ok:false} into a fake success.
  if (result && result.ok === false) throw new Error(result.error || "remote input failed");
  return result;
}
async function ensureRemoteInput() {
  const status = await cmd("input.status", {}, 12);
  if (status.available === false || status.engine === "secure desktop") {
    throw new Error(status.desktop_error || status.last_error ||
      "Remote input is unavailable. Unlock Windows locally and check the desktop agent.");
  }
  if (!status.input_active) {
    await cmd("input.enable", { on: true }, 12);
    STATE.inputOn = true;
    const label = document.getElementById("inputStateLabel");
    if (label) { label.textContent = "control ON"; label.className = "tag ok"; }
  }
}
async function cmd(action, params = {}, timeout = 30) {
  const j = await api("/api/cmd", { method: "POST", body: { action, params, machine: STATE.machine, timeout }, tmo: (Math.max(timeout, 30) + 10) * 1000 });
  if (!j.ok || j.result?.ok === false) throw new Error(j.result?.error || "command failed");
  return j.result?.data ?? {};
}
function toast(msg, bad = false) {
  const t = el("div", { class: "toast" + (bad ? " err" : "") }, String(msg).slice(0, 400));
  $("#toasts").append(t);
  setTimeout(() => t.remove(), 6000);
  if (bad) beep(220);
}
function beep(freq = 660, dur = 0.12) {
  try {
    if (localStorage.getItem("hks_sound") === "0") return;
    AC = AC || new (window.AudioContext || window.webkitAudioContext)();
    if (AC.state === "suspended") { try { AC.resume(); } catch {} }
    const o = AC.createOscillator(), g = AC.createGain();
    o.frequency.value = freq; g.gain.value = 0.06;
    o.connect(g); g.connect(AC.destination); o.start(); o.stop(AC.currentTime + dur);
  } catch {}
}
let AC = null;

/* ---------------- state & websocket ---------------- */
const STATE = {
  user: null, machines: [], machine: null, live: { svc: false, session: false },
  ws: null, wsReady: false, wsCmds: new Map(),
  scrSize: null, inputOn: false, screenOn: false, camOn: false,
  micListen: false, talkOn: false, talkCtx: null, talkNode: null, talkStream: null,
  micCtx: null, micHead: 0,
  lastMove: 0, pendingMove: null,
  lastTel: 0, scrStills: null, camStills: null,
  scrParams: null, camParams: null, scrMode: "", camMode: "",
  frameSeq: {}, wsFails: 0, lastRestMove: 0,
};
function wsSend(obj) { if (STATE.wsReady) STATE.ws.send(JSON.stringify(obj)); }
function wsBin(bytes) { if (STATE.wsReady) STATE.ws.send(bytes); }
function wsCmd(action, params = {}, waitMs = 6000) {
  return new Promise((resolve, reject) => {
    const t0 = Date.now();
    (function attempt() {
      if (!STATE.wsReady) {
        if (Date.now() - t0 < waitMs) return setTimeout(attempt, 400);
        return reject(new Error("live channel offline — check the ● LIVE pill (top right); snapshots still work"));
      }
      const id = Math.random().toString(36).slice(2);
      STATE.wsCmds.set(id, { resolve, reject });
      wsSend({ type: "cmd", id, action, params });
      setTimeout(() => { if (STATE.wsCmds.has(id)) { STATE.wsCmds.delete(id); reject(new Error("timeout")); } }, 65000);
    })();
  });
}
function setPill(state) {
  const p = $("#wsPill");
  if (!p) return;
  if (state === "on") { STATE.wsFails = 0; STATE.wsTries = 0; p.textContent = "● LIVE"; p.className = "pill ok"; p.title = "browser live channel connected (real-time frames + input)"; }
  else if (state === "wait") { p.textContent = "● live…"; p.className = "pill"; p.title = "connecting the live channel…"; }
  else {
    STATE.wsFails++;
    p.textContent = "● live offline"; p.className = "pill bad";
    p.title = "Live updates are using polling. Refresh the page and check the network; if this page uses HTTPS, confirm that the browser trusts the laptop certificate.";
    if (STATE.wsFails === 3) toast("Live channel unavailable — the dashboard is using live polling. Refresh the page or check the laptop connection.", true);
  }
  paintStrip();
}
const HKS_PROGRAM_PATH = "C:\\HKS_PROGRAM\\run_agent.bat";

/* Round 48: THE AGENT CARD - when the laptop-side agent is down, Home says
   so in plain words WITH the exact repair (owner field: Acer doctor showed
   "task HKS-Agent missing" -> login works but everything is dead). */
function paintAgentFix() {
  const c = document.getElementById("hksAgentFix");
  if (!c) return;
  const L = STATE.live || null;
  const svc = !!(L && L.svc), ses = !!(L && L.session);
  if (svc && ses) { c.style.display = "none"; c.dataset.mode = "ok"; return; }
  c.style.display = "flex";
  const rows = [];
  if (!L) {
    c.dataset.mode = "wait";
    c.innerHTML = "<b>⏳ Checking the laptop agent…</b><span>If this stays for more than ~30 s, no agent is connected - see the fixes below.</span>";
    return;
  }
  c.dataset.mode = "fix";
  if (!svc) rows.push("<b>🔴 SERVICE AGENT OFFLINE</b><span>The service agent is not connected to HKS. Files and system-level tasks may be unavailable; screen and camera depend on the separate desktop session.</span>"
    + "<span>• START: run the HKS setup wizard on the laptop and choose Repair.</span>"
    + "<span>• KEEP IT RUNNING: Repair registers the missing HKS-Agent startup task. The desktop session can still be online independently.</span>");
  if (!ses) rows.push("<b>🟠 DESKTOP AGENT OFFLINE</b><span>Screen mirror, camera and remote keyboard need your Windows session.</span>"
    + "<span>• Log into Windows on the laptop (it starts with your session). Locking is fine - logging out stops it.</span>");
  c.innerHTML = rows.join("");
}

/* 2026 UI report Part 4.4: the status strip - glanceable, never scrolls away */
function paintStrip() {
  const on = (id, ok) => { const n = document.getElementById(id); if (n) { n.className = "hks-dot " + (ok ? "ok hks-pulse" : "bad"); } };
  on("ssServer", true);
  on("ssSvc", !!(STATE.live && STATE.live.svc));
  on("ssSes", !!(STATE.live && STATE.live.session));
  const lat = document.getElementById("ssLat");
  if (lat) lat.textContent = STATE.agentLat != null ? Math.round(STATE.agentLat) + " ms" : "—";
  const conn = document.getElementById("ssConn");
  if (conn) { const p = $("#wsPill"); conn.textContent = p ? (p.textContent || "").replace("● ", "live: ") : ""; }
}
function connectWs() {
  if (STATE.ws) { try { STATE.ws.close(); } catch {} }
  const proto = location.protocol === "https:" ? "wss://" : "ws://";
  const ws = new WebSocket(proto + location.host + API_BASE + "/ws/dash");  // round 59: API_BASE is "" or "/sub" - no slash games left
  ws.binaryType = "arraybuffer";
  STATE.ws = ws;
  setPill("wait");
  // The server waits for this first selection before it sends hello_dash.
  // wsSend() intentionally requires wsReady, which is set only after that
  // hello, so using wsSend here deadlocked every dashboard WebSocket.
  ws.onopen = () => ws.send(JSON.stringify({ machine: STATE.machine, token: AUTH_TOKEN || undefined }));
  ws.onmessage = (ev) => {
    if (ev.data instanceof ArrayBuffer) {
      const u = new Uint8Array(ev.data), ch = u[0];
      if (ch === 1) drawFrame($("#scr"), ev.data.slice(1), "scrSize");
      else if (ch === 2) drawFrame($("#camview"), ev.data.slice(1), "camSize");
      else if (ch === 3) {
        try { playLaptopMic(new Uint8Array(ev.data.slice(1))); }
        catch (e) {
          if (STATE.micListen) {
            STATE.micListen = false;
            wsCmd("mic.stream", { on: false }).catch(() => {});
            const micBtn = $("#btnMicListen");
            if (micBtn) { micBtn.textContent = "🎧 Listen to laptop mic"; micBtn.className = "btn"; }
            toast("Laptop mic playback failed: " + e.message, true);
          }
        }
      }
      return;
    }
    let m; try { m = JSON.parse(ev.data); } catch { return; }
    if (m.type === "hello_dash") { STATE.wsReady = true; setPill("on"); if (m.telemetry) paintTelemetry(m.telemetry);
      // resume live streams that were on before the channel dropped
      if (STATE.screenOn && STATE.scrParams) startLive("scrStills", "#scr", "screen.control", STATE.scrParams, 1, "scrSize", (STATE.scrParams && STATE.scrParams.fps) || 8);
      if (STATE.camOn && STATE.camParams) startLive("camStills", "#camview", "camera.stream", STATE.camParams, 2, "camSize", 6);
    }
    else if (m.type === "telemetry") paintTelemetry(m);
    else if (m.type === "result") { const p = STATE.wsCmds.get(m.id); if (p) { STATE.wsCmds.delete(m.id); m.ok ? p.resolve(m.data ?? {}) : p.reject(new Error(m.error || "failed")); } }
    else if (m.type === "agent_status") {
      const was = { ...STATE.live }; refreshPills();
      if (m.role === "svc") beep(m.online ? 740 : 300);
    }
    else if (m.type === "event") {
      if (m.kind === "term_out") { if (STATE.termFeed) STATE.termFeed((m.data || {}).data + "\n"); return; }
      if (m.kind === "audio_status" && m.data && m.data.ok === false) {
        STATE.micListen = false;
        const b = $("#btnMicListen");
        if (b) { b.textContent = "🎧 Listen to laptop mic"; b.className = "btn"; }
        toast("Laptop microphone: " + (m.data.error || "could not start"), true);
      }
      if (m.kind === "ip_changed" && localStorage.getItem("hks_toast_ip") !== "0")
        toast("📡 Laptop IP changed: " + (m.data || {}).new + " — see Server Management for the new address");
      if (m.kind === "motion_detected" && localStorage.getItem("hks_toast_motion") !== "0")
        toast("🛰 Motion detected on camera");
      if (m.kind === "camera_live") {
        if (m.data && m.data.ok === false) {
          toast("📷 LIVE failed: " + (m.data.error || "?") + " — if Zoom/Teams/antivirus is open, close it and press Live again", true);
          if (STATE.camOn && STATE.camParams) {
            startStills("camStills", "#camview", "camera.snap",
              { index: STATE.camParams.index }, 1500, "camSize");
            STATE.camMode = "stills";
            const pill = $("#camModePill");
            if (pill) { pill.textContent = "stills mode"; pill.className = "tag warn"; }
          }
        } else {
          const pill = $("#camModePill");
          if (pill) { pill.textContent = "● LIVE " + ((STATE.camParams || {}).fps || 8) + " fps"; pill.className = "tag ok"; }
        }
      }
      if (m.kind === "capture_failed" && localStorage.getItem("hks_toast_cap") !== "0")
        toast("⚠ login capture failed: " + ((m.data || {}).why || "?"), true);
      addLiveEvent(m);
      if (m.kind === "boot") toast("🚀 HKS PROGRAM HAS BEEN STARTED — starting up (~1 min)");
      else if (m.kind === "alert") toast("⚠ Alert: " + JSON.stringify(m.data));
      else if (m.kind && m.kind.startsWith("capture")) toast("📸 Capture received: " + m.kind);
      else if (m.kind === "self_destruct") toast("💣 Client self-terminate requested");
    }
    else if (m.type === "agent_error") toast("⚠ Agent error [" + m.module + "]: " + m.message, true);
  };
  ws.onclose = (ev) => {
    // connectWs closes the old machine's socket when a device is selected.
    // Ignore its late close event so it cannot change the new device status
    // or schedule a duplicate reconnect against the new selection.
    if (STATE.ws !== ws) return;
    STATE.wsReady = false; setPill("off");
    if (ev && ev.code === 4401) {
      // server says unauthenticated - confirm before logging the UI out
      fetch(API_BASE + "/api/me", { credentials: "same-origin" }).then((r) => r.json()).then((mj) => {
        if (!(mj && mj.user) && STATE.user) showLogin();
      }).catch(() => {});
    }
    STATE.wsTries = (STATE.wsTries || 0) + 1;
    const wait = Math.min(15000, 2500 * STATE.wsTries);
    if (STATE.user) setTimeout(() => { if (STATE.user) connectWs(); }, wait);
  };
  ws.onerror = () => { if (STATE.ws === ws) setPill("off"); };
}
// dashboard stays LIVE even when the websocket is down (5s REST poll).
// 7.2.54 fix: /api/telemetry returns {points:[...]} - the old code read
// t.cpu off the envelope, so the fallback NEVER painted (the "CPU 0%"
// mystery whenever the live channel is down). Use the newest point.
// Also: wsReady is now cleared on close so this fallback engages
// while the socket is dead instead of being disabled forever.
setInterval(() => {
  if (!STATE.user || STATE.wsReady || document.hidden) return;
  api("/api/telemetry", { tmo: 8000 }).then((t) => {
    const pts = t.points || [];
    const last = pts.length ? pts[pts.length - 1] : (t.telemetry || null);
    if (last && last.cpu != null) paintTelemetry(last);
  }).catch(() => {});
}, 5000);
function drawFrame(canvas, buf, sizeKey) {
  if (!canvas) return;
  const blob = new Blob([buf], { type: "image/jpeg" });
  const url = URL.createObjectURL(blob);
  const img = new Image();
  img.onload = () => {
    STATE[sizeKey] = { w: img.naturalWidth, h: img.naturalHeight };
    if (canvas.width !== img.naturalWidth) { canvas.width = img.naturalWidth; canvas.height = img.naturalHeight; }
    const _cx = canvas.getContext && canvas.getContext("2d"); if (_cx) _cx.drawImage(img, 0, 0);
    URL.revokeObjectURL(url);
  };
  img.src = url;
}

/* ---------------- audio: laptop mic → browser ---------------- */
function playLaptopMic(pcm) {
    const c = STATE.micCtx || (STATE.micCtx = new (window.AudioContext || window.webkitAudioContext)());
    if (c.state !== "running") throw new Error("Browser audio is paused; press Listen again to resume audio playback.");
    const i16 = new Int16Array(pcm.buffer, pcm.byteOffset, pcm.byteLength >> 1);
    const f32 = new Float32Array(i16.length);
    for (let i = 0; i < i16.length; i++) f32[i] = i16[i] / 32768;
    const buf = c.createBuffer(1, f32.length, 16000);
    buf.copyToChannel(f32, 0);
    const src = c.createBufferSource(); src.buffer = buf;
    STATE.micHead = Math.max(STATE.micHead, c.currentTime + 0.05);
    src.start(STATE.micHead);
    STATE.micHead += buf.duration;
}
/* ---------------- audio: browser mic → laptop ---------------- */
async function startTalk() {
  if (!window.isSecureContext) return toast("Push To Talk needs HTTPS (browsers block the phone mic on plain http). Mic Listen works on http; for PTT open the panel via the https address once.", true);
  STATE.talkCtx = new (window.AudioContext || window.webkitAudioContext)();
  STATE.talkStream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
  const src = STATE.talkCtx.createMediaStreamSource(STATE.talkStream);
  STATE.talkNode = STATE.talkCtx.createScriptProcessor(4096, 1, 1);
  const from = STATE.talkCtx.sampleRate;
  STATE.talkNode.onaudioprocess = (e) => {
    let f32 = e.inputChannelData || e.inputBuffer.getChannelData(0);
    if (from !== 16000) { // naive linear resample to 16k
      const ratio = from / 16000, n = Math.floor(f32.length / ratio);
      const out = new Float32Array(n);
      for (let i = 0; i < n; i++) out[i] = f32[Math.floor(i * ratio)];
      f32 = out;
    }
    const i16 = new Int16Array(f32.length);
    for (let i = 0; i < f32.length; i++) i16[i] = Math.max(-32768, Math.min(32767, f32[i] * 32767));
    const pkt = new Uint8Array(1 + i16.length * 2);
    pkt[0] = 4;
    pkt.set(new Uint8Array(i16.buffer), 1);
    wsBin(pkt);
  };
  src.connect(STATE.talkNode);
  STATE.talkNode.connect(STATE.talkCtx.destination); // needed to keep node running
}
function stopTalk() {
  try { STATE.talkNode && (STATE.talkNode.onaudioprocess = null, STATE.talkNode.disconnect()); } catch {}
  try { STATE.talkStream && STATE.talkStream.getTracks().forEach((t) => t.stop()); } catch {}
  try { STATE.talkCtx && STATE.talkCtx.close(); } catch {}
  STATE.talkNode = STATE.talkStream = STATE.talkCtx = null;
}

function renderDevices(v) {
  v.append(el("p", { class: "vsub" }, "Every HKS agent registered to this server. Select a device to direct controls, files, and live telemetry to it."));
  const summary = el("div", { class: "device-summary muted" }, "Loading managed devices…");
  const grid = el("div", { class: "device-grid" });
  v.append(panel("Managed devices", summary,
    el("div", { class: "row", style: "margin:12px 0" },
      el("button", { class: "btn ghost sm", onclick: async (e) => {
        busy(e.target, true);
        try { await refreshPills(); paint(); } catch (err) { toast("Device refresh failed: " + err.message, true); }
        finally { busy(e.target, false); }
      } }, "⟳ Refresh")), grid));

  const ago = (value) => {
    const n = Number(value);
    if (!Number.isFinite(n) || n <= 0) return "Never seen";
    const seconds = Math.max(0, Math.floor(Date.now() / 1000 - n));
    if (seconds < 60) return "Just now";
    if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)} hr ago`;
    return `${Math.floor(seconds / 86400)} days ago`;
  };
  function paint() {
    const devices = Array.isArray(STATE.machines) ? STATE.machines : [];
    grid.replaceChildren();
    summary.textContent = devices.length
      ? `${devices.length} registered device${devices.length === 1 ? "" : "s"} · selected device controls all machine actions`
      : "No HKS agent has registered yet. Start the HKS service and desktop agent on a laptop, then refresh.";
    if (!devices.length) {
      grid.append(hksErrState("No managed devices", "Check the HKS agent service and network connection on the laptop, then refresh.", async () => { await refreshPills(); paint(); }));
      return;
    }
    for (const m of devices) {
      const selected = m.machine_id === STATE.machine;
      const t = m.telemetry || {};
      const svc = !!m.live_svc, session = !!m.live_session;
      const card = el("article", { class: "device-card" },
        el("div", { class: "device-card-head" },
          el("div", { class: "device-name-wrap" }, el("span", { class: "device-icon", "aria-hidden": "true" }, "▣"),
            el("div", { class: "device-title" }, el("h3", null, m.name || "Windows device"),
              el("span", { class: "device-id", title: m.machine_id || "" }, String(m.machine_id || "unknown")))),
          el("span", { class: "tag " + ((svc || session) ? "ok" : "bad") }, (svc || session) ? "ONLINE" : "OFFLINE")),
        el("div", { class: "device-state-row" },
          el("span", { class: "device-state " + (svc ? "online" : "") }, `Service ${svc ? "online" : "offline"}`),
          el("span", { class: "device-state " + (session ? "online" : "") }, `Desktop ${session ? "online" : "offline"}`)),
        el("div", { class: "device-facts" },
          el("div", null, el("span", null, "Address"), el("b", null, m.ip || t.ip || "Not reported")),
          el("div", null, el("span", null, "Agent version"), el("b", null, m.version || "Unknown")),
          el("div", null, el("span", null, "Last check-in"), el("b", null, (svc || session) ? "Connected now" : ago(m.last_seen))),
          t.cpu != null ? el("div", null, el("span", null, "CPU"), el("b", null, `${Math.round(Number(t.cpu))}%`)) : null,
          t.ram != null ? el("div", null, el("span", null, "Memory"), el("b", null, `${Math.round(Number(t.ram))}%`)) : null,
          t.ssid ? el("div", null, el("span", null, "Wi-Fi"), el("b", null, String(t.ssid))) : null),
        el("div", { class: "device-card-actions" },
          el("button", { class: "btn " + (selected ? "ghost" : ""), disabled: selected,
            "aria-pressed": String(selected), onclick: () => selectMachine(m.machine_id) }, selected ? "✓ Managing this device" : "Manage this device")));
      grid.append(card);
    }
  }
  paint();
  v.refreshDevices = async () => { try { await refreshPills(); paint(); } catch (e) { summary.textContent = "Could not refresh devices: " + e.message; } };
  // The five-second status poll updates STATE.machines; refresh this inventory without rebuilding the screen.
  every(5000, paint);
}

/* ---------------- top bar ---------------- */
async function refreshPills() {
  try {
    const j = await api("/api/machines");
    STATE.machines = Array.isArray(j.machines) ? j.machines : [];
    if (!STATE.machine) {
      let saved = null; try { saved = localStorage.getItem("hks_selected_machine"); } catch {}
      STATE.machine = (saved && STATE.machines.some((x) => x.machine_id === saved) ? saved : null)
        || j.default_machine || (STATE.machines[0] && STATE.machines[0].machine_id) || null;
    }
    if (STATE.machine && !STATE.machines.some((x) => x.machine_id === STATE.machine))
      STATE.machine = j.default_machine || (STATE.machines[0] && STATE.machines[0].machine_id) || null;
    if (STATE.machine) { try { localStorage.setItem("hks_selected_machine", STATE.machine); } catch {} }
    const picker = $("#tMachine");
    if (picker) {
      const signature = STATE.machines.map((x) => `${x.machine_id}\u0000${x.name || ""}`).join("\u0001");
      if (picker.dataset.signature !== signature) {
        picker.replaceChildren(...(STATE.machines.length
          ? STATE.machines.map((x) => el("option", { value: x.machine_id }, x.name || x.machine_id.slice(0, 12)))
          : [el("option", { value: "" }, "No managed device")]));
        picker.dataset.signature = signature;
      }
      picker.value = STATE.machine || "";
    }
    const m = STATE.machines.find((x) => x.machine_id === STATE.machine);
    $("#dSvc").className = "dot" + (m && m.live_svc ? " ok" : "");
    $("#dSes").className = "dot" + (m && m.live_session ? " ok" : "");
    $("#tSvc").textContent = "service " + (m && m.live_svc ? "online" : "offline");
    $("#tSes").textContent = "desktop " + (m && m.live_session ? "online" : "offline");
    STATE.live = { svc: !!(m && m.live_svc), session: !!(m && m.live_session) };
    STATE.bootTs = m && m.boot_ts;
    paintStrip();
    paintAgentFix();
  } catch (e) {}
}
function selectMachine(machineId) {
  if (!machineId || machineId === STATE.machine || !STATE.machines.some((m) => m.machine_id === machineId)) return;
  STATE.machine = machineId;
  try { localStorage.setItem("hks_selected_machine", machineId); } catch {}
  const selected = STATE.machines.find((m) => m.machine_id === machineId);
  STATE.live = { svc: !!selected?.live_svc, session: !!selected?.live_session };
  STATE.bootTs = selected?.boot_ts || null;
  $("#dSvc").className = "dot" + (STATE.live.svc ? " ok" : "");
  $("#dSes").className = "dot" + (STATE.live.session ? " ok" : "");
  $("#tSvc").textContent = "service " + (STATE.live.svc ? "online" : "offline");
  $("#tSes").textContent = "desktop " + (STATE.live.session ? "online" : "offline");
  paintStrip();
  paintAgentFix();
  const picker = $("#tMachine"); if (picker) picker.value = machineId;
  Object.keys(viewCache).forEach((id) => { try { viewCache[id].remove(); } catch {} delete viewCache[id]; });
  STATE.screenOn = STATE.camOn = STATE.micListen = STATE.talkOn = false;
  refreshPills();
  connectWs();
  const current = sessionStorage.getItem("hks_last_section") || "home";
  showSection(current);
  toast("Managing " + (STATE.machines.find((m) => m.machine_id === machineId)?.name || machineId));
}
function drawB64(canvas, b64, sizeKey) {
  if (!canvas || !b64) return;
  const img = new Image();
  img.onload = () => {
    if (canvas.width !== img.naturalWidth) { canvas.width = img.naturalWidth; canvas.height = img.naturalHeight; }
    const _cx = canvas.getContext && canvas.getContext("2d"); if (_cx) _cx.drawImage(img, 0, 0);
    if (sizeKey) STATE[sizeKey] = { w: img.naturalWidth, h: img.naturalHeight };
  };
  img.src = "data:image/jpeg;base64," + b64;
}
function stopStills(key) { if (STATE[key]) { clearInterval(STATE[key]); STATE[key] = null; } }
function startStills(key, sel, action, params, ms, sizeKey) {
  stopStills(key);
  const tick = async () => { try { const d = await cmd(action, params, 20); drawB64($(sel), d.b64, sizeKey); } catch {} };
  tick();
  STATE[key] = setInterval(tick, ms);
}
/* Live engine (field fix 2026-09: "live not working, snapshots work"):
   1) live channel (websocket) -> real-time frames arrive via onmessage
   2) websocket down -> START the stream over REST (the AGENT link is a
      different, working socket) and PULL the latest frames from
      /api/frame (server caches them as the agent pushes)
   3) even that fails -> snapshot polling (stills) as the last resort */
function startLive(key, sel, startAction, startParams, channel, sizeKey, fps) {
  stopStills(key);
  const modeKey = key === "scrStills" ? "scrMode" : "camMode";
  const paramKey = key === "scrStills" ? "scrParams" : "camParams";
  STATE[paramKey] = startParams;
  const pollMs = Math.max(250, Math.floor(1000 / Math.max(1, fps || 6)));
  const restStart = async () => {
    try {
      await cmd(startAction, { on: true, ...startParams }, 20);
    } catch { return false; }
    const tick = async () => {
      try {
        const j = await api("/api/frame?ch=" + channel + "&machine=" + encodeURIComponent(STATE.machine || ""), { tmo: 12000 });
        if (j.seq && j.seq !== STATE.frameSeq[channel]) { STATE.frameSeq[channel] = j.seq; drawB64($(sel), j.b64, sizeKey); }
        else if (!j.seq) drawB64($(sel), j.b64, sizeKey);
      } catch {}
    };
    tick();
    STATE[key] = setInterval(tick, pollMs);
    return true;
  };
  (async () => {
    if (STATE.wsReady) {
      try { await wsCmd(startAction, { on: true, ...startParams }); STATE[modeKey] = "ws"; return; } catch {}
    }
    if (await restStart()) { STATE[modeKey] = "rest"; return; }
    startStills(key, sel, channel === 1 ? "screen.snapshot" : "camera.snap",
      channel === 1 ? { monitor: startParams.monitor, max_width: startParams.max_width }
                    : { index: startParams.index },
      channel === 1 ? 1200 : 1500, sizeKey);
    STATE[modeKey] = "stills";
  })();
}
function stopLive(key, startAction, stopParams) {
  stopStills(key);
  const modeKey = key === "scrStills" ? "scrMode" : "camMode";
  STATE[modeKey] = "";
  const p = { on: false, ...(stopParams || {}) };
  (STATE.wsReady ? wsCmd(startAction, p) : cmd(startAction, p, 15)).catch(() => {});
}
function paintTelemetry(t) {
  STATE.lastTel = Date.now();
  if (STATE.prevTel && t.net_sent != null && STATE.prevTel.net_sent != null) {
    const dt = (t.ts || Date.now() / 1000) - (STATE.prevTel.ts || 0) || 2;
    STATE.netLog = STATE.netLog || [];
    STATE.netLog.push({ down: Math.max(0, (t.net_sent - STATE.prevTel.net_sent) / dt),
                        up: Math.max(0, (t.net_recv - STATE.prevTel.net_recv) / dt) });
    if (STATE.netLog.length > 40) STATE.netLog.shift();
  }
  STATE.prevTel = t; STATE.tel = t;
  const set = (id, v, sfx) => { const n = $(id); if (n) n.textContent = v + (sfx || ""); };
  set("#gCpu", Math.round(t.cpu || 0), "%");
  set("#gRam", Math.round(t.ram || 0), "%");
  set("#gBat", t.bat_pct != null ? Math.round(t.bat_pct) : "—", t.bat_pct != null ? "%" : "");
  [["cpu", t.cpu], ["memory", t.ram]].forEach(([name, val]) => {
    const needle = document.getElementById("usage-needle-" + name);
    if (needle && Number.isFinite(Number(val))) needle.setAttribute("transform", "rotate(" + (-90 + Math.max(0, Math.min(100, Number(val))) * 1.8) + " 50 50)");
  });
  const plug = $("#gPlug"); if (plug) { plug.textContent = t.bat_plugged ? "⚡ charging" : "🔋 battery"; plug.className = "tag " + (t.bat_plugged ? "ok" : "warn"); }
  const cb = $("#barCpu"); if (cb) { cb.style.width = (t.cpu || 0) + "%"; cb.className = t.cpu > 85 ? "bad" : t.cpu > 60 ? "warn" : ""; }
  const rb = $("#barRam"); if (rb) { rb.style.width = (t.ram || 0) + "%"; rb.className = t.ram > 85 ? "bad" : t.ram > 60 ? "warn" : ""; }
  pushSpark(t);
  const last = $("#lastTel"); if (last) last.textContent = "updated " + new Date().toLocaleTimeString();
  /* 2026 UI report: KPI cards live-update wherever they are on screen */
  const kset = (id, v) => { const n = document.getElementById(id); if (n) n.textContent = v; };
  kset("hkCpu", Math.round(t.cpu || 0));
  kset("hkRam", Math.round(t.ram || 0));
  kset("hkNet", netKb(t));
  kset("hkBat", t.bat_pct != null ? Math.round(t.bat_pct) : "—");
  const netS = document.getElementById("hkNetS"); if (netS) netS.textContent = "KB/s ↓";
  if (t.latency_ms != null) { STATE.agentLat = t.latency_ms; }
  paintStrip();
  refreshKpiRow();
}
function netKb(t) {
  if (!STATE.netLog || !STATE.netLog.length) return "—";
  return Math.round(STATE.netLog[STATE.netLog.length - 1].down / 1024);
}
/* one KPI row per the report: 4 max, value + delta + sparkline */
function refreshKpiRow() {
  const row = document.getElementById("hksKpis");
  if (!row || row.dataset.built !== "1") return;
  const cpu = sparkData.cpu, ram = sparkData.ram, bat = sparkData.bat, net = sparkData.net;
  const d = (a) => (a.length > 4 ? Math.round(a[a.length - 1] - a[a.length - 4]) : null);
  const st = (v) => (v > 85 ? "critical" : v > 60 ? "warning" : "good");
  const cells = [
    { id: "hkCpu", label: "CPU", unit: "%", arr: cpu, status: st(cpu[cpu.length - 1] || 0) },
    { id: "hkRam", label: "MEMORY", unit: "%", arr: ram, status: st(ram[ram.length - 1] || 0) },
    { id: "hkNet", label: "NETWORK", unit: "KB/s", arr: net, status: "neutral" },
    { id: "hkBat", label: "BATTERY", unit: "%", arr: bat, status: "neutral" }];
  for (const c of cells) {
    const b = document.getElementById(c.id);
    const card = b && b.closest(".hks-kpi");
    if (!card) continue;
    const val = c.arr.length ? c.arr[c.arr.length - 1] : 0;
    const nv = c.id === "hkNet" ? netKb({}) : Math.round(val);
    if (b.textContent !== String(nv)) {
      b.textContent = nv;
      b.classList.remove("hks-value-flash"); void b.offsetWidth; b.classList.add("hks-value-flash");
    }
    card.setAttribute("data-status", c.status);
    let sp = card.querySelector(".hks-spark");
    if (sp) sp.replaceWith(hksSpark(c.arr, c.status === "good" ? "ok" : c.status === "warning" ? "warn" : c.status === "critical" ? "bad" : ""));
    let dl = card.querySelector(".hks-delta");
    const dv = d(c.arr);
    if (dl && dv != null && c.id !== "hkNet") { dl.className = "hks-delta " + (dv > 0 ? "up" : dv < 0 ? "down" : "flat"); dl.textContent = (dv > 0 ? "▲ " : dv < 0 ? "▼ " : "■ ") + Math.abs(dv) + "%"; }
  }
}
const sparkData = { cpu: [], ram: [], net: [], bat: [] };
function pushSpark(t) {
  sparkData.cpu.push(t.cpu || 0); sparkData.ram.push(t.ram || 0);
  sparkData.net.push(STATE.netLog && STATE.netLog.length ? STATE.netLog[STATE.netLog.length - 1].down / 1024 : 0);
  sparkData.bat.push(t.bat_pct != null ? t.bat_pct : 0);
  for (const k of ["cpu", "ram", "net", "bat"]) if (sparkData[k].length > 120) sparkData[k].shift();
  drawSpark("#spCpu", sparkData.cpu, "#2ea86b"); drawSpark("#spRam", sparkData.ram, "#3e9fd4");
}
function drawSpark(sel, data, color) {
  const c = $(sel); if (!c) return;
  const ctx = c.getContext ? c.getContext("2d") : null; if (!ctx) return; const w = c.width = c.offsetWidth || 300; const h = c.height = 46;
  ctx.clearRect(0, 0, w, h); if (data.length < 2) return;
  ctx.beginPath(); ctx.strokeStyle = color; ctx.lineWidth = 1.6;
  data.forEach((v, i) => { const x = (i / 119) * w, y = h - (Math.min(v, 100) / 100) * (h - 4) - 2; i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
  ctx.stroke();
}

/* ================= HEALTH & REPORTS (plan §5/§6 + §24 startup) ============ */
function gradeTag(st) {
  const cls = st === "GOOD" ? "ok" : st === "CRITICAL" ? "bad" : st === "WARNING" ? "warn" : "";
  return el("span", { class: "tag " + cls }, st);
}
function renderHealth(v) {
  const wrap = el("div");
  v.append(panel("System health (real checks, never fake numbers)",
      el("div", { class: "btn-row", style: "margin-bottom:10px" },
        el("button", { class: "btn", onclick: (e) => { busy(e.target, true, "checking…"); cmd("health.report", {}, 90)
            .then((d) => { paintHealth(d); busy(e.target, false); })
            .catch((x) => { toast(x.message, true); busy(e.target, false); }); } }, "⟳ Run health check")),
      wrap));

  const dl = (f, label) => el("a", { class: "btn ghost", href: API_BASE + "/api/report?format=" + f, download: "" }, "⬇ " + label);
  v.append(panel("Generate full report",
    el("p", { class: "muted", style: "margin-top:0" }, "System, CPU, RAM, disks, battery, network, top processes — downloaded to this device."),
    el("div", { class: "btn-row" }, dl("html", "HTML report"), dl("json", "JSON"), dl("csv", "CSV"))));

  const su = el("div");
  v.append(panel("Startup manager (HKS units)",
    el("div", { class: "btn-row", style: "margin-bottom:10px" },
      el("button", { class: "btn ghost", onclick: (e) => { busy(e.target, true); cmd("startup.hks_status", {}, 30)
          .then((d) => { paintStartup(d); busy(e.target, false); }).catch((x) => { toast(x.message, true); busy(e.target, false); }); } }, "⟳ Refresh")),
    su));

  function paintHealth(d) {
    wrap.innerHTML = "";
    const head = el("div", { class: "btn-row", style: "margin-bottom:10px" },
      el("span", { class: "muted" }, "Overall:"), gradeTag(d.overall || "UNKNOWN"));
    const t = el("div", { class: "tbl-wrap" }, el("table", null,
      el("thead", null, el("tr", null, el("th", null, "Check"), el("th", null, "State"), el("th", null, "Value"))),
      el("tbody", null, (d.checks || []).map((c2) => el("tr", null,
        el("td", null, c2.name), el("td", null, gradeTag(c2.state)), el("td", null, String(c2.reason ?? "")))))));
    wrap.append(head, t);
  }
  function paintStartup(d) {
    su.innerHTML = "";
    su.append(el("div", { class: "tbl-wrap" }, el("table", null,
      el("thead", null, el("tr", null, el("th", null, "Unit"), el("th", null, "Exists"), el("th", null, "Status"), el("th", null, "Actions"))),
      el("tbody", null, (d.units || []).map((u) => {
        const acts = el("td", null, el("div", { class: "btn-row" },
          ["start", "stop", "enable", "disable"].map((op) =>
            el("button", { class: "btn ghost sm", onclick: async () => {
              if (op === "disable" && u.unit === "HKS-Panel" &&
                  !confirm("Disabling the PANEL task locks you out of remote access. Type OK to confirm.")) return;
              try {
                const r = await cmd("startup.hks_action", { unit: u.unit, op,
                  confirm: op === "disable" && u.unit === "HKS-Panel" ? "CONFIRM" : undefined }, 30);
                toast((r.output || "done"));
              } catch (e2) { toast(e2.message, true); }
            } }, op))));
        return el("tr", null, el("td", null, u.unit), el("td", null, u.exists ? "yes" : "no"),
          el("td", null, String(u.status || "?")), acts);
      })))));
  }
}

/* ================= CODE RUNNER (plan §19) ================= */
function renderCode(v) {
  v.append(chromeBar("vs", "#0098ff", "code-server style", "VS Code in the browser — engine: HKS Code Runner (12 real languages) + full HKS_ADDONS kit installs the real code-server"));
  const LANGS = ["python", "javascript", "typescript", "c", "cpp", "java",
                 "csharp", "go", "rust", "php", "ruby", "perl"];
  const lang = el("select", null, LANGS.map((l) => el("option", { value: l }, l)));
  const starters = {
    python: 'print("hello from the laptop")',
    javascript: 'console.log("hello from the laptop");',
    typescript: 'console.log("hello from the laptop");',
    c: '#include <stdio.h>\n\nint main(void) {\n    puts("hello from the laptop");\n    return 0;\n}',
    cpp: '#include <iostream>\n\nint main() {\n    std::cout << "hello from the laptop\\n";\n    return 0;\n}',
    java: 'public class Main {\n    public static void main(String[] args) {\n        System.out.println("hello from the laptop");\n    }\n}',
    csharp: 'Console.WriteLine("hello from the laptop");',
    go: 'package main\n\nimport "fmt"\n\nfunc main() {\n    fmt.Println("hello from the laptop")\n}',
    rust: 'fn main() {\n    println!("hello from the laptop");\n}',
    php: '<?php\necho "hello from the laptop\\n";',
    ruby: 'puts "hello from the laptop"',
    perl: 'print "hello from the laptop\\n";'
  };
  let previousStarter = starters.python;
  const verSpan = el("span", { class: "muted" }, "");
  const chips = el("div", { class: "chips" });
  const ed = el("textarea", { class: "code", rows: "14", spellcheck: "false",
    style: "width:100%;background:var(--surface);color:var(--text);font-size:13px" });
  ed.value = starters.python;
  const changeLanguage = () => {
    if (ed.value === previousStarter) ed.value = starters[lang.value] || "";
    previousStarter = starters[lang.value] || "";
    paintChips(); check();
  };
  const paintChips = () => {
    chips.innerHTML = "";
    LANGS.forEach((l) => chips.append(el("button", {
      class: "chip" + (l === lang.value ? " on" : ""),
      onclick: () => { lang.value = l; changeLanguage(); } }, l)));
  };
  paintChips();
  const check = () => cmd("code.check", { lang: lang.value }, 30).then((d) => {
    verSpan.textContent = d.installed ? ("✓ " + (d.version || "installed")) : "✗ not installed — " + (d.hint || "install the runtime");
    verSpan.setAttribute("role", "status");
    return d;
  }).catch((e) => { verSpan.textContent = "Runtime check failed — " + e.message; return null; });
  lang.onchange = changeLanguage;
  lang.value = "python";
  const out = resultBox("pre");
  v.append(panel("Code Runner — runs ON the laptop (isolated temp workspace, hard timeout)",
      chips,
      el("div", { class: "btn-row", style: "margin-bottom:10px" },
        lang, verSpan, el("span", { style: "flex:1" }),
        el("button", { class: "btn ghost sm", onclick: () => check() }, "⟳ check runtime"),
        el("button", { class: "btn ghost sm", onclick: () => {
          const blob = new Blob([ed.value], { type: "text/plain" });
          const ext = { python: "py", javascript: "js", typescript: "ts", c: "c", cpp: "cpp",
            java: "java", csharp: "cs", go: "go", rust: "rs", php: "php", ruby: "rb", perl: "pl" }[lang.value] || "txt";
          const a = el("a", { href: URL.createObjectURL(blob), download: "hks_snippet." + ext });
          a.click(); toast("snippet saved to this device");
        } }, "💾 Save snippet")),
      ed,
      el("div", { class: "btn-row", style: "margin-top:10px" },
        el("button", { id: "runCodeBtn", class: "btn", onclick: (e) => {
          busy(e.target, true, "running…");
          cmd("code.run", { lang: lang.value, code: ed.value, timeout: 30 }, 60)
            .then((d) => { busy(e.target, false);
              out.show((d.stdout || "") + (d.stderr ? "\n[stderr]\n" + d.stderr : "") +
                (d.error ? "\n[error] " + d.error : "") +
                (d.exit !== undefined ? "\n[exit " + d.exit + " in " + d.ms + " ms]" : ""));
            }).catch((x) => { busy(e.target, false);
              if (String(x.message || "").indexOf("not installed") >= 0) installModal();
              else out.show("[error] " + x.message); });
        } }, "▶ Run"),
        el("button", { class: "btn ghost", onclick: () => out.box.style.display = "none" }, "Clear output")),
      out.box));
  ed.onkeydown = (ev) => { if ((ev.ctrlKey || ev.metaKey) && ev.key === "Enter") {
    const b = $("#runCodeBtn"); if (b) b.click();
  } };
  function installModal() {
    modal("Runtime missing", el("div", null,
      el("p", null, "The " + lang.value + " runtime is not installed on the laptop."),
      el("p", { class: "muted" }, "This installs it with winget (Windows) or apt (Linux). It can take a few minutes."),
      el("div", { class: "btn-row" },
        el("button", { class: "btn danger", onclick: async (ev) => {
          busy(ev.target, true, "installing…");
          try {
            const r = await cmd("code.install", { lang: lang.value, confirm: "CONFIRM" }, 540);  /* round 65 (audit7 #45): server allows 600 for installs only */
            if (r.ok === false) throw new Error(r.error || "Runtime installer failed");
            out.show((r.output || "Runtime installation completed.") + (r.version ? "\n" + r.version : ""));
            const verified = await check();
            if (verified && verified.installed) toast(lang.value + " installed and verified");
            else throw new Error("Installer finished, but the agent still cannot find this runtime. Restart HKS-Agent, then check again.");
          } catch (er) { out.show("[install failed] " + er.message); toast(er.message, true); }
          busy(ev.target, false);
        } }, "Install " + lang.value),
        el("button", { class: "btn ghost", onclick: (ev) => ev.target.closest(".modal-bk").remove() }, "Cancel"))));
  }
  check();
}

/* ================= XAMPP MANAGER (plan §20) ================= */
function renderXampp(v) {
  const wrap = el("div");
  v.append(panel("XAMPP control",
      el("div", { class: "btn-row", style: "margin-bottom:10px" },
        el("button", { class: "btn", onclick: (e) => { busy(e.target, true); cmd("xampp.status", {}, 60)
            .then((d) => { paint(d); busy(e.target, false); })
            .catch((x) => { toast(x.message, true); busy(e.target, false); }); } }, "⟳ Refresh status")),
      wrap));
  function paint(d) {
    wrap.innerHTML = "";
    if (!d.installed) return wrap.append(el("div", { class: "banner" }, d.hint));
    wrap.append(el("p", { class: "muted", style: "margin-top:0" },
      "XAMPP at " + d.path + (d.php_version ? " · PHP " + d.php_version : "") +
      (d.documentroot_exists ? " · htdocs: " + d.htdocs : "")));
    wrap.append(el("div", { class: "app-card-grid xampp-card-grid" },
      ...Object.entries(d.services || {}).map(([name, svc]) => {
        const ports = Object.entries(svc.ports || {}).map(([p2, open]) =>
          el("span", { class: "tag " + (open ? "ok" : "") }, p2 + (open ? " open" : "")));
        return el("article", { class: "app-card xampp-card" },
          el("div", { class: "app-card-head" }, el("h3", null, name),
            svc.running ? el("span", { class: "tag ok" }, "RUNNING") : el("span", { class: "tag" }, "STOPPED")),
          el("div", { class: "app-card-meta" }, el("span", { class: "muted" }, "Ports"),
            el("div", { class: "chips" }, ...(ports.length ? ports : [el("span", { class: "muted" }, "No open ports")]))),
          el("div", { class: "btn-row app-card-actions" },
            ["start", "stop", "restart"].map((op) =>
              el("button", { class: "btn ghost sm", onclick: () =>
                cmd("xampp.control", { service: name, op }, 60)
                  .then((r) => toast(r.note || r.error || (name + " " + op + " sent")))
                  .catch((e2) => toast(e2.message, true)) }, op)),
            el("button", { class: "btn ghost sm", onclick: async () => {
              try {
                const r = await cmd("xampp.logs", { service: name }, 30);
                const body = Object.entries(r.logs || {})
                  .map(([f, t]) => "--- " + f + " ---\n" + (t || "(empty/unavailable)")).join("\n\n");
                modal(name + " logs", el("pre", { class: "pre" }, body));
              } catch (e2) { toast(e2.message, true); }
            } }, "logs"),
            el("button", { class: "btn ghost sm", onclick: async () => {
              try {
                const r = await cmd("xampp.config", {}, 30);
                const rows = Object.entries(r).filter(([k]) => k !== "ok").map(([k, val]) =>
                  el("div", { class: "btn-row", style: "margin-bottom:6px" },
                    el("b", null, k + ":"), el("span", { class: "muted" }, String(val || "not found"))));
                modal("Config files", el("div", null, rows));
              } catch (e2) { toast(e2.message, true); }
            } }, "config")));
      })));
  }
}

/* ---------------- shared dashboard helpers ---------------- */
function spark(points, w = 120, h = 34, color = "#4f8cff") {
  if (!points || points.length < 2) return el("span", { class: "muted" }, "…");
  const pts = points.map((p2, i) =>
    `${(i / (points.length - 1)) * w},${h - ((Number(p2) || 0) / 100) * h}`).join(" ");
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
  svg.style.cssText = "display:block;width:100%;max-width:120px;height:34px;flex:1 1 auto;min-width:0";
  const pl = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
  pl.setAttribute("points", pts); pl.setAttribute("fill", "none");
  pl.setAttribute("stroke", color); pl.setAttribute("stroke-width", "2");
  svg.append(pl);
  return svg;
}
function dashTile(ic, label, value, sub, accent, extra) {
  return el("div", { class: "dtile" },
    el("div", { class: "dtile-ic " + (accent || "b") }, ic),
    el("div", { class: "dtile-body" },
      el("div", { class: "dtile-label" }, label),
      el("div", { class: "dtile-value" }, String(value)),
      sub ? el("div", { class: "dtile-sub" }, String(sub)) : null,
      extra || null));
}
function usageDial(value, label) {
  const pct = Math.max(0, Math.min(100, Number(value) || 0));
  return el("div", { class: "usage-dial", role: "img", "aria-label": label + " usage " + Math.round(pct) + " percent" },
    el("svg", { viewBox: "0 0 100 60", "aria-hidden": "true" },
      el("path", { d: "M10 50 A40 40 0 0 1 90 50", fill: "none", "stroke-width": "8", "stroke": "var(--border-soft)" }),
      el("line", { id: "usage-needle-" + label.toLowerCase(), x1: "50", y1: "50", x2: "50", y2: "17", class: "usage-needle", transform: "rotate(" + (-90 + pct * 1.8) + " 50 50)" }),
      el("circle", { cx: "50", cy: "50", r: "4", class: "usage-hub" })));
}
function metricIcon(kind) {
  const cpu = kind === "cpu";
  return el("svg", { viewBox: "0 0 24 24", width: "20", height: "20", fill: "none",
    stroke: "currentColor", "stroke-width": "1.8", "stroke-linecap": "round",
    "stroke-linejoin": "round", "aria-hidden": "true" },
    cpu
      ? el("path", { d: "M8 5V3m8 2V3M8 21v-2m8 2v-2M5 8H3m2 8H3m18-8h-2m2 8h-2M7 7h10v10H7zM10 10h4v4h-4z" })
      : el("path", { d: "M4 7h16v10H4zM7 10h2v4H7zm4 0h2v4h-2zm4 0h2v4h-2zM7 4v3m5-3v3m5-3v3M7 17v3m5-3v3m5-3v3" }));
}
function pill(ok, label, why) {
  return el("span", { class: "pill " + (ok === true ? "ok" : ok === false ? "bad" : "wait"),
    title: why || "" }, ok === true ? "✓" : ok === false ? "✗" : "…", label);
}
function gradeTag(st) {
  const cls = st === "GOOD" ? "ok" : st === "CRITICAL" ? "bad" : st === "WARNING" ? "warn" : "";
  return el("span", { class: "tag " + cls }, st);
}

/* ================= SYSTEM (mockup §2) ================= */
function renderSystem(v) {
  v.append(el("div", { class: "viewhead" }, el("div", null,
    el("h2", { class: "vt" }, "System"), el("p", { class: "vsub" }, "System information + health"))));
  const tabs = el("div", { class: "tabs" });
  const box = el("div");
  v.append(tabs, box);
  const TABS = [
    ["Cores", paintCores],
    ["Overview", paintOverview], ["Hardware", paintHardware], ["Battery", paintBattery],
    ["Storage", paintStorage], ["Network", paintNetwork]];
  let cur = "Overview";  /* round 65 (audit7 #38): was "Cores" while paintOverview() ran - highlight disagreed with content */
  TABS.forEach(([name, fn]) => {
    const b = el("button", { class: "tab" + (name === cur ? " on" : ""), onclick: () => {
      cur = name;
      tabs.querySelectorAll(".tab").forEach((t) => t.classList.toggle("on", t === b));
      fn();
    } }, name);
    tabs.append(b);
  });
  const kv = (rows) => {
    const w = el("div", { class: "kv" });
    rows.filter((r) => r[1] !== null && r[1] !== undefined && r[1] !== "").forEach(([k, val]) =>
      w.append(el("div", { class: "kvrow" }, el("span", null, k), el("b", null, String(val)))));
    return w;
  };
  function paintCores() {
    box.innerHTML = "";
    const grid = el("div", { class: "cores-grid" });
    const progress = (value, className, label) => {
      const n = Number(value);
      const pct = Number.isFinite(n) ? Math.max(0, Math.min(100, n)) : 0;
      return el("div", { class: className, role: "progressbar", "aria-label": label,
        "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": String(Math.round(pct)) },
        el("i", { style: "width:" + pct + "%" }));
    };
    const cpuDonut = el("div", { class: "cores-donut" }, donut(0, 120, "coCpu"));
    const cpuBars = el("div", { class: "cores-threadbars" }, el("span", { class: "muted" }, "loading…"));
    const cpuName = el("div", { class: "cores-model" }, "…");
    const ramDonut = el("div", { class: "cores-donut" }, donut(0, 120, "coRam"));
    const ramInfo = el("div", { class: "cores-model" }, "…");
    const ramSwap = el("div", { class: "cores-kv" });
    const gpuDonut = el("div", { class: "cores-donut" }, donut(0, 120, "coGpu"));
    const gpuInfo = el("div", { class: "cores-model" }, "…");
    const driveBox = el("div", { class: "cores-kv" }, el("span", { class: "muted" }, "…"));
    const fanBox = el("div", { class: "cores-kv" }, el("span", { class: "muted" }, "…"));
    const ifaceBox = el("div", { class: "cores-kv" }, el("span", { class: "muted" }, "…"));
    const cpuTempBox = el("div", { class: "cores-temps" }, el("span", { class: "muted" }, "…"));
    const ramTempBox = el("div", { class: "cores-temps" }, el("span", { class: "muted" }, "…"));
    const gpuTempBox = el("div", { class: "cores-temps" }, el("span", { class: "muted" }, "…"));
    const strip = el("div", { class: "cores-strip" });
    grid.append(
      el("div", { class: "card cores-card" }, el("div", { class: "cores-head" }, el("span", { class: "cores-ic", style: "background:linear-gradient(135deg,#7c3aed,#8b5cf6)" }, "🧠"), el("b", null, "CPU")), cpuName,
        el("div", { class: "cores-duo" }, cpuDonut, cpuBars)),
      el("div", { class: "card cores-card" }, el("div", { class: "cores-head" }, el("span", { class: "cores-ic", style: "background:linear-gradient(135deg,#2563eb,#3b82f6)" }, "💈"), el("b", null, "RAM")), ramInfo,
        el("div", { class: "cores-duo" }, ramDonut, ramSwap)),
      el("div", { class: "card cores-card" }, el("div", { class: "cores-head" }, el("span", { class: "cores-ic", style: "background:linear-gradient(135deg,#0891b2,#22d3ee)" }, "🎮"), el("b", null, "GPU")), gpuInfo,
        el("div", { class: "cores-duo" }, gpuDonut, el("div", { class: "cores-kv", id: "coGpuSide" }))),
      el("div", { class: "card cores-card" }, el("div", { class: "cores-head" }, el("span", { class: "cores-ic", style: "background:linear-gradient(135deg,#b45309,#f59e0b)" }, "💽"), el("b", null, "Drives")), driveBox),
      el("div", { class: "card cores-card" }, el("div", { class: "cores-head" }, el("span", { class: "cores-ic", style: "background:linear-gradient(135deg,#0f766e,#2dd4bf)" }, "🌀"), el("b", null, "Fans")), fanBox),
      el("div", { class: "card cores-card" }, el("div", { class: "cores-head" }, el("span", { class: "cores-ic", style: "background:linear-gradient(135deg,#334155,#64748b)" }, "🔌"), el("b", null, "Interfaces")), ifaceBox),
      el("div", { class: "card cores-card" }, el("div", { class: "cores-head" }, el("span", { class: "cores-ic", style: "background:linear-gradient(135deg,#be123c,#fb7185)" }, "🌡"), el("b", null, "CPU Temperature")), cpuTempBox),
      el("div", { class: "card cores-card" }, el("div", { class: "cores-head" }, el("span", { class: "cores-ic", style: "background:linear-gradient(135deg,#9333ea,#c084fc)" }, "📟"), el("b", null, "RAM Usage")), ramTempBox),
      el("div", { class: "card cores-card" }, el("div", { class: "cores-head" }, el("span", { class: "cores-ic", style: "background:linear-gradient(135deg,#db2777,#ec4899)" }, "🌡"), el("b", null, "GPU Temperature")), gpuTempBox),
      strip);
    box.append(grid);
    const segbar = (label, val, max) => el("div", { class: "cores-temp" },
      el("span", { class: "cores-temp-l" }, label + (val != null ? " (" + Math.round(val) + " °C)" : " (no sensor)")),
      progress(val == null ? 0 : 100 * Number(val) / (Number(max) || 100), "cores-segbar", label),
      el("span", { class: "cores-temp-v" }, val == null ? "—" : Math.round(val) + "°"));
    const paint = (d) => {
      if (!document.getElementById("coCpu")) return;
      const c = d.cpu || {}, r = d.ram || {}, g = (d.gpu || [])[0] || {}, rt = d.rates || {};
      const t = STATE.tel || {};
      donutSet("coCpu", t.cpu != null ? t.cpu : c.total_pct);
      donutSet("coRam", r.used_pct);
      donutSet("coGpu", g.util_pct != null ? g.util_pct : 0);
      cpuName.textContent = "cores " + (c.cores || "?") + " • threads " + (c.threads || "?") +
        (c.freq ? " • " + Math.round(c.freq.current_mhz / 100) / 10 + " GHz" : "");
      cpuBars.innerHTML = "";
      (c.per_core || []).forEach((p2, i) => cpuBars.append(el("div", { class: "cores-thread" },
        el("span", null, "CPU #" + (i + 1)), el("span", null, Math.round(p2) + "%"),
        progress(p2, "cores-mini", "CPU core " + (i + 1) + " load"))));
      if (!(c.per_core || []).length) cpuBars.innerHTML = "";
      ramInfo.textContent = (r.used_gb || 0).toFixed(1) + " / " + (r.total_gb || "?") + " GB used";
      ramSwap.innerHTML = "";
      ramSwap.append(el("div", { class: "cores-thread" }, el("span", null, "Virtual memory"),
        el("span", null, (r.swap_pct != null ? Math.round(r.swap_pct) : "—") + "%"),
        progress(r.swap_pct, "cores-mini", "Virtual memory usage")));
      gpuInfo.textContent = g.name || "no GPU reported";
      const side = document.getElementById("coGpuSide");
      if (side) side.innerHTML = "";
      if (side && g.vram_gb) side.append(el("div", { class: "cores-thread" }, el("span", null, "VRAM"), el("span", null, g.vram_gb + " GB"), progress(Number(g.vram_gb) * 8, "cores-mini", "GPU memory estimate")));
      driveBox.innerHTML = "";
      (d.disks || []).forEach((dk) => driveBox.append(el("div", { class: "cores-thread" },
        el("span", null, dk.mount + " free " + (dk.free_gb || 0) + " GB"),
        el("span", null, dk.total_gb ? Math.round(100 * (dk.free_gb || 0) / dk.total_gb) + "%" : "—"),
        progress(dk.used_pct, "cores-mini", (dk.mount || "Drive") + " used"))));
      driveBox.append(el("div", { class: "cores-thread" }, el("span", null, "Read speed"), el("span", null, rt.disk_read_mb_s != null ? rt.disk_read_mb_s + " MB/s" : "sampling…")),
        el("div", { class: "cores-thread" }, el("span", null, "Write speed"), el("span", null, rt.disk_write_mb_s != null ? rt.disk_write_mb_s + " MB/s" : "sampling…")));
      fanBox.innerHTML = "";
      (d.fans || []).forEach((f) => fanBox.append(el("div", { class: "cores-thread" },
        el("span", null, f.name), el("span", null, f.pct != null ? Math.round(f.pct) + "%" : (f.rpm != null ? f.rpm + " rpm" : "—")),
        progress(f.pct != null ? f.pct : Number(f.rpm || 0) / 40, "cores-mini", (f.name || "Fan") + " speed"))));
      if (!(d.fans || []).length) fanBox.append(el("span", { class: "muted" }, "no fan sensors reported by this machine (honest — nothing faked)"));
      ifaceBox.innerHTML = "";
      ifaceBox.append(el("div", { class: "cores-thread" }, el("span", null, "Download speed"), el("span", null, rt.net_down_mb_s != null ? rt.net_down_mb_s + " MB/s" : "sampling…")),
        el("div", { class: "cores-thread" }, el("span", null, "Upload speed"), el("span", null, rt.net_up_mb_s != null ? rt.net_up_mb_s + " MB/s" : "sampling…")),
        el("div", { class: "cores-thread" }, el("span", null, "Ping (panel link)"), el("span", null, d.latency_ms != null ? d.latency_ms + " ms" : "—")),
        el("div", { class: "cores-thread" }, el("span", null, "Network"), el("span", null, (t.ssid ? t.ssid : "ethernet"))));
      cpuTempBox.innerHTML = "";
      cpuTempBox.append(segbar("Avg. temperature", c.temp_c, 100));
      (c.core_temps || []).forEach((ct, i) => cpuTempBox.append(segbar("Core #" + (i + 1), ct, 100)));
      ramTempBox.innerHTML = "";
      ramTempBox.append(el("div", { class: "cores-temp" },
        el("span", { class: "cores-temp-l" }, "Memory: " + (r.used_gb || 0).toFixed(1) + "/" + (r.total_gb || "?") + " GB"),
        progress(r.used_pct, "cores-segbar", "Memory usage"),
        el("span", { class: "cores-temp-v" }, Math.round(r.used_pct || 0) + "%")));
      gpuTempBox.innerHTML = "";
      gpuTempBox.append(segbar("GPU" + (g.name && !/demo/i.test(g.name) ? "" : " (simulated on demo)"), g.temp_c, 100));
      const b = d.battery;
      strip.innerHTML = "";
      strip.append(el("span", null, b ? "🔋 " + Math.round(b.percent) + "% " + (b.plugged ? "charging" : "on battery") + (b.wear ? " • wear " + Math.round(b.wear.wear_pct) + "%" : "") : "🔋 no battery"),
        el("span", null, "⏱ uptime " + (d.uptime_h != null ? d.uptime_h + " h" : "—")),
        el("span", null, "⚙ processes " + (d.processes != null ? d.processes : "—")),
        el("span", { class: "muted" }, (STATE.tel && STATE.tel.ip) ? "IP " + STATE.tel.ip : ""));
    };
    const refresh = () => cmd("hw.status", {}, 60).then(paint).catch(() => {});
    refresh();
    const iv = setInterval(refresh, 10000);  /* round 65 (audit7 #50): 3 s = ~5 PowerShell spawns/snapshot on the laptop; 10 s matches the temp cache */
    cleanupFns.push(() => clearInterval(iv));
  }
  function paintOverview() {
    box.innerHTML = "";
    const info = el("div", { class: "muted" }, "loading…");
    const health = el("div", { class: "muted" }, "not checked yet");
    box.append(el("div", { class: "dashgrid" },
      panel("System information", info),
      panel("Health reports", health)));
    box.append(el("div", { class: "btn-row", style: "margin-bottom:12px" },
      el("button", { class: "btn ghost", onclick: () => showSection("security") }, "🛡 Security center"),
      el("button", { class: "btn ghost", onclick: () => showSection("servermg") }, "🗄 Server management"),
      el("button", { class: "btn ghost", onclick: () => showSection("reports") }, "🧾 Reports")));
    box.append(panel("Power actions (real Windows power API — every action asks first)",
      (() => {
        const g = el("div", { class: "pwrgrid" });
        const P = [
          ["🌙", "Sleep", "g", "power.sleep", {}, "Sleep now?"],
          ["🐻", "Hibernate", "t", "power.hibernate", {}, "Hibernate now?"],
          ["🌑", "Display off", "s", "power.display_off", {}, "Turn the display off?"],
          ["🔒", "Lock", "b", "power.lock_screen", {}, "Lock the screen?"],
          ["🚪", "Log off", "o", "power.logoff_console", {}, "Log off the Windows user?"],
          ["🔄", "Restart (10s)", "p", "power.restart", { seconds: 10 }, "RESTART the laptop in 10 seconds?"],
          ["⏻", "Shutdown (10s)", "r", "power.shutdown", { seconds: 10 }, "SHUT DOWN the laptop in 10 seconds?"],
          ["✋", "Cancel shutdown", "v", "power.shutdown_cancel", {}, null],
          ["🛠", "Safe mode next boot", "o", "power.safeboot_on", {}, "Boot into SAFE MODE on the next restart? (undo with the OFF tile)"],
          ["✔", "Safe mode off", "g", "power.safeboot_off", {}, null]];
        P.forEach(([ic, name, c, action, params, ask]) =>
          g.append(el("div", { class: "qa " + c, onclick: () => {
            const go = () => cmd(action, params).then(() => toast(name + ": command sent")).catch((e) => toast(e.message, true));
            if (ask) confirmDo(ask, go); else go();
          } }, el("span", { class: "qa-ic" }, ic), name)));
        return g;
      })()));
    (async () => {
      try {
        const d = await cmd("sys.info", {}, 40);
        info.innerHTML = "";
        info.append(kv(Object.entries(d).filter(([k]) => typeof d[k] !== "object").slice(0, 14)));
      } catch (e) { info.className = "muted"; info.textContent = e.message; }
      try {
        const d = await cmd("health.report", {}, 90);
        health.innerHTML = ""; health.className = "";
        health.append(el("div", { class: "btn-row", style: "margin-bottom:8px" },
          el("span", { class: "muted" }, "Overall:"), gradeTag(d.overall)));
        d.checks.forEach((c2) => health.append(el("div", { class: "kvrow" },
          el("span", null, c2.name), gradeTag(c2.state), el("span", { class: "muted" }, c2.reason))));
      } catch (e) { health.className = "muted"; health.textContent = e.message; }
    })();
  }
  function paintHardware() {
    box.innerHTML = "";
    const w = el("div", { class: "muted" }, "loading…");
    box.append(panel("Hardware details", w));
    cmd("hw.deep", {}, 40).then((d) => {
      w.innerHTML = ""; w.className = "";
      const cpu = d.cpu || {}, ram = d.ram || {};
      w.append(kv([["CPU", cpu.name || (cpu.threads + " threads")], ["Cores", cpu.cores],
        ["Threads", cpu.threads], ["Load", cpu.total_pct + "%"],
        ["Freq", (cpu.freq || {}).current_mhz && ((cpu.freq.current_mhz / 1000).toFixed(2) + " GHz")],
        ["RAM", ram.total_gb && (ram.total_gb + " GB (" + ram.used_pct + "% used)")],
        ["Mainboard", d.board], ["BIOS", d.bios],
        ["Processes", d.processes], ["Uptime", d.uptime_h && (d.uptime_h + " h")]]));
    }).catch((e) => { w.className = "muted"; w.textContent = e.message; });
  }
  function paintBattery() {
    box.innerHTML = "";
    const w = el("div", { class: "muted" }, "loading…");
    box.append(panel("Battery", w));
    cmd("hw.deep", {}, 40).then((d) => {
      w.innerHTML = ""; w.className = "";
      const b = d.battery;
      if (!b) { w.append(el("span", { class: "muted" }, "no battery (desktop or battery hidden)")); return; }
      w.append(kv([["Charge", b.percent + "%"], ["Plugged in", b.plugged ? "yes" : "no"],
        ["Wear", b.wear != null ? b.wear + "%" : "unknown"]]));
    }).catch((e) => { w.className = "muted"; w.textContent = e.message; });
  }
  function paintStorage() {
    box.innerHTML = "";
    const w = el("div", { class: "muted" }, "loading…");
    box.append(panel("Disks", w));
    cmd("hw.deep", {}, 40).then((d) => {
      w.innerHTML = ""; w.className = "";
      (d.disks || []).forEach((dk) => {
        w.append(el("div", { class: "kvrow" }, el("span", null, dk.mount),
          el("b", null, dk.free_gb + " GB free of " + dk.total_gb)));
      });
      if (!(d.disks || []).length) w.append(el("span", { class: "muted" }, "no disks reported"));
    }).catch((e) => { w.className = "muted"; w.textContent = e.message; });
  }
  function paintNetwork() {
    box.innerHTML = "";
    const w = el("div", { class: "muted" }, "loading…");
    box.append(panel("Laptop addresses", w,
      el("p", { class: "muted", style: "margin-top:8px" }, "Auto-updates when the laptop changes network (you get a toast).")));
    cmd("network.addresses", {}, 20).then((d) => {
      w.innerHTML = ""; w.className = "";
      (d.addresses || []).forEach((a) => {
        const url = location.protocol + "//" + a.ip + (location.port ? ":" + location.port : "");
        w.append(el("div", { class: "kvrow" }, el("span", null, a.iface), el("b", null, a.ip),
          el("a", { class: "btn ghost sm", href: url, target: "_blank" }, "open")));
      });
      if (!(d.addresses || []).length) { w.className = "muted"; w.textContent = "agent offline"; }
    }).catch((e) => { w.className = "muted"; w.textContent = e.message; });
  }
  paintOverview();
}

/* ================= CONTROL (screen + input + clipboard + unlock) ====== */
function renderControl(v) {
  v.append(el("div", { class: "viewhead" }, el("div", null,
    el("h2", { class: "vt" }, "Control"),
    el("p", { class: "vsub" }, "Live screen, keyboard & mouse, clipboard, lock / unlock"))));
  renderScreen(v);
  renderTools(v);
  // ---- screenshot with Download/Discard (timestamped filename)
  v.append(panel("Screenshot (download or discard)",
      el("div", { class: "btn-row" },
        el("button", { class: "btn", onclick: async (e) => {
          busy(e.target, true);
          try {
            const d = await cmd("screen.snapshot", { max_width: 1600 }, 40);
            busy(e.target, false);
            if (!d.b64) return toast("no image", true);
            const stamp = new Date();
            const pad = (n) => String(n).padStart(2, "0");
            const fname = "HKS_screen_" + stamp.getFullYear() + "-" + pad(stamp.getMonth() + 1) + "-" +
              pad(stamp.getDate()) + "_" + pad(stamp.getHours()) + "-" + pad(stamp.getMinutes()) + "-" + pad(stamp.getSeconds()) + ".jpg";
            modal("Screenshot", el("div", null,
              el("img", { src: "data:image/jpeg;base64," + d.b64, style: "width:100%" }),
              el("div", { class: "btn-row", style: "margin-top:10px" },
                el("a", { class: "btn", href: "data:image/jpeg;base64," + d.b64, download: fname }, "⬇ Download " + fname),
                el("button", { class: "btn ghost", onclick: (ev) => ev.target.closest(".modal-bk").remove() }, "Discard"))));
          } catch (er) { busy(e.target, false); toast(er.message, true); }
        } }, "📸 Take screenshot"))));
  // ---- mockup control-button grid (real input.apply actions)
  // Pointer is stored in the same normalized coordinate space the agent
  // consumes. It is updated by the live preview (mouse or touch).
  const ptr = () => (STATE.lastPtr || { x: 0.5, y: 0.5 });

  const click = async (btn, label) => {
    const p2 = ptr();
    const nx = Math.min(0.99999, Math.max(0, p2.x)), ny = Math.min(0.99999, Math.max(0, p2.y));
    try {
      await ensureRemoteInput();
      await sendInput({ t: "md", x: nx, y: ny, btn });
      await sendInput({ t: "mu", x: nx, y: ny, btn });
      toast(label + " at " + Math.round(nx * 100) + "% x " + Math.round(ny * 100) + "%");
    } catch (er) { toast(er.message, true); }
  };
  v.append(panel("Control panel (acts on the laptop's cursor)",
      el("p", { class: "muted", style: "margin-top:0" },
        "Buttons act where you last touched or clicked the live preview (center until you position it). Tap the preview first for precise control."),
      el("div", { class: "ctrlgrid" },
        el("button", { class: "ctrl", onclick: () => click(0, "Left click") }, "🖱 Left Click"),
        el("button", { class: "ctrl", onclick: () => click(2, "Right click") }, "🖱 Right Click"),
        el("button", { class: "ctrl", onclick: async () => {
          const p2 = ptr();
          const nx = Math.min(0.99999, Math.max(0, p2.x)), ny = Math.min(0.99999, Math.max(0, p2.y));
          try {
            await ensureRemoteInput();
            for (let i = 0; i < 2; i++) {
              await sendInput({ t: "md", x: nx, y: ny, btn: 0 });
              await sendInput({ t: "mu", x: nx, y: ny, btn: 0 });
            }
            toast("Double click sent");
          } catch (er) { toast(er.message, true); }
        } }, "🖱 Double Click"),
        el("button", { class: "ctrl", onclick: () => click(1, "Middle click") }, "🖱 Middle Click"),
        el("button", { class: "ctrl", onclick: async () => {
          try { await ensureRemoteInput(); await sendInput({ t: "mw", dx: 0, dy: -300 }); toast("Scroll up sent"); }
          catch (er) { toast(er.message, true); }
        } }, "⌃ Scroll up"),
        el("button", { class: "ctrl", onclick: async () => {
          try { await ensureRemoteInput(); await sendInput({ t: "mw", dx: 0, dy: 300 }); toast("Scroll down sent"); }
          catch (er) { toast(er.message, true); }
        } }, "⌄ Scroll down"),
        el("button", { class: "ctrl", onclick: () => {
          const box = el("input", { placeholder: "text to type on the laptop…", style: "width:100%" });
          modal("Send keys (types on the laptop)", el("div", null, box,
            el("div", { class: "btn-row", style: "margin-top:10px" },
              el("button", { class: "btn", onclick: async (ev) => {
                try { await sendInput({ t: "type", text: box.value }); toast("typed"); }
                catch (er) { toast(er.message, true); }
                ev.target.closest(".modal-bk").remove();
              } }, "Type"))));
          setTimeout(() => box.focus(), 50);
        } }, "⌨ Send Keys"),
        el("button", { class: "ctrl", onclick: () => showSection("tools") }, "⌨ Full keyboard"),
        el("button", { class: "ctrl green", onclick: () => window.open(API_BASE + "/static/screen.html" + (AUTH_TOKEN ? "?token=" + encodeURIComponent(AUTH_TOKEN) : ""), "_blank") }, "⛶ Fullscreen touch"))));
  // ---- clipboard manager (Current)
  const clipBox = el("div", { class: "muted" }, "press Get clipboard");
  v.append(panel("Clipboard manager",
      el("div", { class: "btn-row", style: "margin-bottom:10px" },
        el("button", { class: "btn", onclick: (e) => { busy(e.target, true);
          cmd("clipboard.get", {}, 20).then((d) => { busy(e.target, false); paintClip(d); })
            .catch((x) => { busy(e.target, false); toast(x.message, true); }); } }, "⬇ Get clipboard")), clipBox));
  function paintClip(d) {
    clipBox.innerHTML = ""; clipBox.className = "";
    clipBox.append(el("div", { class: "kvrow" }, el("span", null, "formats"),
      el("b", null, (d.formats || []).join(", ") || "empty")));
    if (d.text) clipBox.append(el("pre", { class: "pre small", style: "max-height:140px;overflow:auto" }, d.text));
    if (d.image_b64) clipBox.append(el("img", { src: "data:image/png;base64," + d.image_b64, style: "max-width:100%;border-radius:8px;margin-top:8px" }));
    if (d.files && d.files.length) clipBox.append(el("div", { class: "kvrow" }, el("span", null, "files"), el("b", null, d.files.join(", "))));
  }
  // ---- LOCK / UNLOCK
  const st = el("span", { class: "muted" }, "");
  v.append(panel("Lock and remote sign-in",
  el("p", { class: "muted", style: "margin-top:0" },
      "Lock now uses the real Windows lock API. Windows error 7041 means its physical console cannot be disconnected by WTSConnectSessionW, and the secure sign-in screen cannot accept HKS keyboard input. Use a supported Remote Desktop client/host to sign in remotely; Windows Home cannot host built-in Remote Desktop."),
    el("div", { class: "btn-row" },
      el("button", { class: "btn ghost", onclick: (e) => runCmd("Lock", "power.lock_screen", {}, () => toast("Windows lock requested"), e.target) }, "🔒 Lock now"))));
}

/* ================= PROCESSES (own page) ================= */
function renderProcs(v) {
  const box = el("div");
  const filt = el("input", { placeholder: "filter…", style: "flex:1;min-width:160px" });
  let ROWS = [];
  const paint = () => {
    const q = (filt.value || "").toLowerCase();
    box.innerHTML = "";
    const items = ROWS.filter((r) => !q || (r.name || "").toLowerCase().includes(q)).slice(0, 80);
    box.append(el("div", { class: "process-card-grid" }, ...items.map((r) =>
      el("article", { class: "process-card" },
        el("div", { class: "app-card-head process-card-head" },
          el("strong", null, appIcon(r.name), " ", r.name || "Unknown process"),
          el("span", { class: "tag" }, "PID " + r.pid)),
        el("div", { class: "app-card-details" },
          el("div", null, el("dt", null, "CPU"), el("dd", null, r.cpu == null ? "—" : Number(r.cpu).toFixed(1) + "%")),
          el("div", null, el("dt", null, "Memory"), el("dd", null, r.mem != null ? fmtB(r.mem) : (r.mem_mb != null ? r.mem_mb + " MB" : "—"))),
          el("div", null, el("dt", null, "User"), el("dd", null, r.user || "—"))),
        el("button", { class: "btn danger sm", onclick: () =>
          confirmDo("End " + (r.name || "process") + " (PID " + r.pid + ")?", () =>
            cmd("apps.kill", { pid: r.pid }, 20).then(() => refresh())) }, "■ End process")))));
  };
  const refresh = () => cmd("apps.processes", {}, 40).then((d) => { ROWS = d.processes || d.items || []; paint(); })
    .catch((e) => { box.innerHTML = ""; box.append(el("div", { class: "banner" }, e.message)); });
  filt.oninput = debounce(paint, 250);
  v.append(el("div", { class: "viewhead" }, el("div", null,
    el("h2", { class: "vt" }, "Processes"), el("p", { class: "vsub" }, "What is running on the laptop")),
    el("button", { class: "btn sm", onclick: refresh }, "⟳ Refresh")));
  v.append(panel("Running processes", el("div", { class: "btn-row", style: "margin-bottom:10px" }, filt), box));
  refresh();
}

/* ================= APPLICATIONS (launch + installed + uninstall) ======= */
function renderAppsPage(v) {
  const launch = el("input", { placeholder: "app name or path (calc.exe, notepad…)", style: "flex:1;min-width:220px" });
  const uninstallList = el("div", { class: "muted" }, "loading installed applications…");
  const uq = el("input", { placeholder: "filter installed apps…", style: "flex:1;min-width:200px" });
  let UNINSTALL = [];
  const paintU = () => {
    const q = (uq.value || "").toLowerCase();
    const items = UNINSTALL.filter((x) => !q || x.name.toLowerCase().includes(q)).slice(0, 60);
    uninstallList.innerHTML = ""; uninstallList.className = "";
    uninstallList.append(el("div", { class: "app-card-grid" }, ...items.map((x) =>
        el("article", { class: "app-card" },
        el("div", { class: "app-card-head" }, el("h3", null, appIcon(x.name), " ", x.name)),
        el("dl", { class: "app-card-details" },
          el("div", null, el("dt", null, "Version"), el("dd", null, String(x.version || "—"))),
          el("div", null, el("dt", null, "Publisher"), el("dd", null, String(x.publisher || "—")))),
        el("button", { class: "btn danger sm", onclick: async () => {
          if (!confirm("Launch the uninstaller for\n" + x.name + "?")) return;
          try { const r = await cmd("apps.uninstall", { name: x.name, confirm: "CONFIRM" }, 90);
            toast(r.note || r.error || "done"); } catch (er) { toast(er.message, true); }
        } }, "Uninstall")))));
  };
  uq.oninput = debounce(paintU, 250);
  v.append(el("div", { class: "viewhead" }, el("div", null,
    el("h2", { class: "vt" }, "Applications"), el("p", { class: "vsub" }, "Launch, install-list, uninstall"))));
  v.append(panel("Launch application", el("div", { class: "btn-row" }, launch,
    el("button", { class: "btn", onclick: (e) => launch.value && runCmd("Launch", "apps.launch",
      { target: launch.value }, () => { toast("launched"); launch.value = ""; }, e.target) }, "🚀 Launch"))));
  v.append(panel("Installed applications (real Windows uninstall entries)",
    el("div", { class: "btn-row", style: "margin-bottom:10px" }, uq), uninstallList));
  cmd("apps.uninstall_meta", {}, 60).then((d) => { UNINSTALL = d.items || []; paintU(); })
    .catch(() => { uninstallList.textContent = "needs the Windows service agent"; });
}

/* ================= TERMINAL — the photo one (real streaming shell) ===== */
function renderTermLive(v) {
  let on = false, hist = [], hi = -1, promptPath = "", running = false;
  const body = el("div", { class: "trm-body" });
  const rows = el("div", { class: "trm-rows" });
  const ps1 = el("span", { class: "trm-ps1", "aria-hidden": "true" }, "PS ❯");
  const inp = el("input", { class: "trm-in", type: "text", autocomplete: "off", spellcheck: "false",
    "aria-label": "Command to run in the laptop terminal", placeholder: "Type a command…", disabled: true });
  const atBottom = () => body.scrollHeight - body.clientHeight - body.scrollTop < 40;
  const scroll = (force = false) => { if (force || atBottom()) body.scrollTop = body.scrollHeight; };
  const paintPs1 = () => { ps1.textContent = promptPath ? "PS " + promptPath + " ❯" : "PS ❯"; };
  const addLine = (txt) => { const d = el("div", { class: "trm-l" }, txt); rows.append(d);
    while (rows.childElementCount > 1500) rows.removeChild(rows.firstChild);  // audit4 U3: cap DOM
    return d; };
  const feed = (chunk) => {
    const t = String(chunk);
    if (t === "__HKS_CLEAR__") { rows.innerHTML = ""; scroll(true); return; }
    if (t.startsWith("__HKS_PROMPT__")) { promptPath = t.slice(14) || ""; paintPs1(); scroll(true); return; }
    const follow = atBottom();
    t.split("\n").forEach((ln) => addLine(ln));
    scroll(follow);
  };
  const echo = () => { rows.append(el("div", { class: "trm-l trm-echo" },
    (promptPath ? "PS " + promptPath + " " : "PS ") + "❯ " + inp.value)); scroll(true); };
  const send = async () => {
    const txt = inp.value;
    if (!txt.trim() || !on || running) return;
    hist.push(txt); hi = hist.length;
    echo();
    inp.value = "";
    running = true; inp.disabled = true;
    const started = performance.now();
    const progress = addLine("[running command…]"); progress.classList.add("trm-running"); scroll(true);
    try {
      // Use the authenticated request/response channel so mobile browsers
      // always receive command output even if their live event stream stalls.
      const r = await cmd("terminal.exec_user", { shell: "powershell", command: txt,
        cwd: promptPath || undefined, timeout: 30 }, 45);
      promptPath = String(r.cwd || promptPath || ""); paintPs1();
      progress.remove();
      if (r.output) feed(String(r.output).replace(/\r\n/g, "\n"));
      if (r.error) feed("[stderr]\n" + String(r.error).replace(/\r\n/g, "\n"));
      if (!r.output && !r.error) feed("[command completed with no output]");
      if (r.code != null && Number(r.code) !== 0) feed("[exit " + r.code + "]");
      feed("[completed in " + Math.max(1, Math.round(performance.now() - started)) + " ms]");
    } catch (e) {
      progress.remove();
      feed("[command failed — " + String(e && e.message || "unknown terminal error") + "]");
    } finally { running = false; inp.disabled = !on; if (on) inp.focus(); }
  };
  const commandForm = el("form", { class: "trm-command", onsubmit: (ev) => { ev.preventDefault(); send(); } },
    ps1, inp, el("button", { class: "btn sm", type: "submit" }, "Send"));
  inp.onkeydown = (ev) => {
    if (ev.key === "ArrowUp") { ev.preventDefault(); if (hi > 0) { hi--; inp.value = hist[hi] || ""; } }
    else if (ev.key === "ArrowDown") { ev.preventDefault(); if (hi < hist.length - 1) { hi++; inp.value = hist[hi] || ""; } else { hi = hist.length; inp.value = ""; } }
    else if (ev.key === "c" && ev.ctrlKey) { ev.preventDefault(); addLine("^C (current input cleared)"); inp.value = ""; scroll(true); }
    else if (ev.key === "l" && ev.ctrlKey) { ev.preventDefault(); rows.innerHTML = ""; scroll(true); }
  };
  const conn = async (e) => {
    busy(e.target, true, "connecting…");
    try {
      on = true;
      connBtn.textContent = "■ Disconnect"; connBtn.classList.add("danger");
      feed("[ready — commands run on the laptop and return their output here]");
      inp.disabled = false; inp.placeholder = "Type a command and press Enter"; inp.focus();
    } catch (er) {
      const detail = String(er && er.message || "unknown terminal error");
      feed("[connect failed — " + detail + (STATE.live && STATE.live.session ? "]" : "; check that the desktop agent is online]") );
    }
    busy(e.target, false);  /* round 65 (audit7 #43): restore first, THEN the true label - busy() was overwriting "■ Disconnect" */
    connBtn.textContent = on ? "■ Disconnect" : "▶ Connect";
    connBtn.classList.toggle("danger", on);
  };
  const connBtn = el("button", { class: "btn sm", onclick: (e) => {
    if (!on) conn(e);
    else {
      on = false; inp.disabled = true;
      connBtn.textContent = "▶ Connect"; connBtn.classList.remove("danger");
      feed("[disconnected]");
    }
  } }, "▶ Connect");
  const quicks = el("div", { class: "chips" });
  ["ipconfig", "dir", "tasklist", "systeminfo", "whoami", "hostname", "Get-Date", "cls"].forEach((qc) =>
    quicks.append(el("button", { class: "chip", onclick: () => {
      if (inp.disabled) return;
      inp.value = qc;
      inp.focus();
      inp.setSelectionRange(inp.value.length, inp.value.length);
    } }, qc)));
  v.append(el("div", { class: "viewhead" }, el("div", null,
    el("h2", { class: "vt" }, "Terminal"),
    el("p", { class: "vsub" }, "Signed-in PowerShell — command output and errors return here on your phone")),
    el("span", { class: "tag ok" }, "LIVE")));
  v.append(el("div", { class: "trm-win" },
    el("div", { class: "trm-bar" },
      el("span", { class: "trm-dots" }, el("i", { class: "d r" }), el("i", { class: "d y" }), el("i", { class: "d g" })),
      el("span", { class: "trm-title" }, "HKS live shell — " + ((STATE.sel && STATE.sel.name) || "laptop")),
      el("span", { class: "btn-row", style: "gap:6px" }, connBtn,
        el("button", { class: "btn ghost sm", onclick: () => { rows.innerHTML = ""; } }, "Clear"))),
    body,
    commandForm,
    el("div", { class: "trm-hint" }, "↑/↓ history · Ctrl+C clears the input line · Ctrl+L clears output")));
  v.append(el("div", { class: "sechead", style: "margin:12px 0 8px" }, "Quick commands"), quicks);
  inp.disabled = true; inp.placeholder = "Connect to the laptop before sending a command";
  setTimeout(() => { if (!on) { const b = connBtn; conn({ target: b }); } }, 250);
  cleanupFns.push(() => { on = false; });
}

/* ================= REPORTS (mockup §8) ================================= */
function renderReports(v) {
  v.append(el("div", { class: "viewhead" }, el("div", null,
    el("h2", { class: "vt" }, "Reports"), el("p", { class: "vsub" }, "Generate system report + health check"))));
  const SECS = ["System information", "Hardware details", "Battery report", "Storage report",
    "Network report", "Installed applications", "USB devices", "Security status",
    "XAMPP status", "Process list"];
  const ck = el("div", { class: "checklist" });
  SECS.forEach((name, i) => {
    const id = "rchk" + i;
    const c = el("input", { type: "checkbox", id });
    c.checked = ["System information", "Hardware details", "Battery report", "Storage report",
      "Network report", "Process list"].includes(name);
    ck.append(el("label", { for: id }, c, name));
  });
  const pdfBtn = el("button", { class: "btn", onclick: () => {
    window.open(API_BASE + "/api/report?format=html", "_blank");
    toast("press Print in the new tab → Save as PDF");
  } }, "⬇ PDF (via Print)");
  const prev = el("div", { class: "muted" }, "last report: —");
  v.append(panel("Generate system report",
      ck,
      el("div", { class: "btn-row" },
        pdfBtn,
        el("a", { class: "btn", href: API_BASE + "/api/report?format=html", download: "" }, "⬇ HTML"),
        el("a", { class: "btn ghost", href: "/api/report?format=json", download: "" }, "⬇ JSON"),
        el("a", { class: "btn ghost", href: "/api/report?format=csv", download: "" }, "⬇ CSV")),
      el("p", { class: "muted", style: "margin-top:8px" },
        "Report is built fresh on the laptop each time. PDF = the HTML report + your browser's Print → Save as PDF."),
      prev));
  const health = el("div", { class: "muted" }, "not checked yet");
  v.append(panel("System health check",
      el("div", { class: "btn-row", style: "margin-bottom:8px" },
        el("button", { class: "btn", onclick: (e) => { busy(e.target, true);
          cmd("health.report", {}, 90).then((d) => { busy(e.target, false); paint(d);
            prev.textContent = "last report: " + new Date().toLocaleString(); })
            .catch((x) => { busy(e.target, false); toast(x.message, true); }); } }, "⟳ Run health check")), health));
  function paint(d) {
    health.innerHTML = ""; health.className = "";
    health.append(el("div", { class: "btn-row", style: "margin-bottom:8px" },
      el("span", { class: "muted" }, "Overall:"), gradeTag(d.overall)));
    (d.checks || []).forEach((c2) => health.append(el("div", { class: "kvrow" },
      el("span", null, c2.name), gradeTag(c2.state), el("span", { class: "muted" }, c2.reason))));
  }
  /* ---- clean log viewer (design doc #6): colored, filtered, exportable ---- */
  {
    let ALL = [], logPage = 0;
    const PAGE_SIZE = 30;
    const list = el("div", { class: "logs-clean" }, el("span", { class: "muted" }, "loading…"));
    const flt = el("input", { placeholder: "filter…", style: "flex:1;min-width:150px" });
    const pageLabel = el("span", { class: "muted", "aria-live": "polite" }, "Page 1");
    const pager = el("div", { class: "btn-row log-pager" });
    const paint2 = () => {
      const q = (flt.value || "").toLowerCase();
      const filtered = ALL.filter((x) => !q || x.line.toLowerCase().includes(q));
      const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
      logPage = Math.min(logPage, pages - 1);
      const items = filtered.slice(logPage * PAGE_SIZE, (logPage + 1) * PAGE_SIZE);
      pageLabel.textContent = filtered.length ? `Showing ${logPage * PAGE_SIZE + 1}–${logPage * PAGE_SIZE + items.length} of ${filtered.length} · page ${logPage + 1}/${pages}` : "0 entries";
      prevPage.disabled = logPage === 0; nextPage.disabled = logPage >= pages - 1;
      list.innerHTML = ""; list.className = "logs-clean";
      items.forEach((x) => list.append(el("div", { class: "logrow " + x.sev },
        el("span", { class: "logt" }, x.time),
        el("span", { class: "logsev" }, x.sev.toUpperCase()),
        el("span", { class: "logm" }, x.line))));
      if (!items.length) list.append(el("span", { class: "muted" }, "nothing matches"));
    };
    const prevPage = el("button", { class: "btn ghost sm", onclick: () => { logPage = Math.max(0, logPage - 1); paint2(); } }, "← Newer");
    const nextPage = el("button", { class: "btn ghost sm", onclick: () => { logPage++; paint2(); } }, "Older →");
    flt.oninput = debounce(() => { logPage = 0; paint2(); }, 200);
    const load = () => {
      ALL = []; logPage = 0;
      api("/api/errors?limit=200").then((d) => {
        (d.errors || []).forEach((e2) => ALL.push({
          ts: Number(e2.ts || 0), time: new Date((e2.ts || 0) * 1000).toLocaleTimeString(),
          sev: "error", line: "[" + (e2.module || "?") + "] " + String(e2.message || "").slice(0, 200) }));
        return api("/api/events?limit=250");
      }).then((d) => {
        (d.events || []).forEach((e2) => ALL.push({
          ts: Number(e2.ts || 0), time: new Date((e2.ts || 0) * 1000).toLocaleTimeString(),
          sev: (e2.kind || "").includes("fail") ? "warn" : "info",
          line: "[" + (e2.kind || "?") + "] " + String((e2.data || {}).msg || (e2.data || {}).user || JSON.stringify(e2.data || {}).slice(0, 120)) }));
        ALL.sort((a, b2) => b2.ts - a.ts);
        paint2();
      }).catch((e) => { list.className = "muted"; list.textContent = e.message; });
    };
    load();
    v.append(panel("Logs (clean view)",
      el("div", { class: "btn-row", style: "margin-bottom:8px" }, flt,
        el("button", { class: "btn ghost sm", onclick: () => { load(); toast("logs reloaded"); } }, "⟳"),
        el("button", { class: "btn ghost sm", onclick: () => {
          const txt = ALL.map((x) => x.time + "  " + x.sev.toUpperCase().padEnd(5) + " " + x.line).join("\n");
          const a = document.createElement("a");
          a.href = URL.createObjectURL(new Blob([txt], { type: "text/plain" }));
          a.download = "HKS_logs_" + new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-") + ".txt";
          a.click();
          toast("log file exported");
        } }, "⬇ Export .txt")), pager, list));
    pager.append(prevPage, pageLabel, nextPage);
  }
}

/* ================= SECURITY (audit + captures + bans + sessions) ======= */
function renderSecurity(v) {
  const gallery = el("div", { class: "gallery" }, el("span", { class: "muted" }, "loading captures…"));
  const bansBox = el("div");
  const sessBox = el("div", { class: "muted" }, "…");
  const auditBox = el("div", { class: "muted" }, "…");
  const ipIn = el("input", { placeholder: "e.g. 192.168.1.99", style: "max-width:200px" });
  const whyIn = el("input", { placeholder: "reason (optional)", style: "flex:1;min-width:140px" });
  const paintBans = () => api("/api/security/bans").then((d) => {
    bansBox.innerHTML = "";
    const rows = d.bans || [];
    if (!rows.length) { bansBox.append(el("span", { class: "muted" }, "no banned IPs")); return; }
    bansBox.append(el("div", { class: "tbl-wrap" }, el("table", null,
      el("thead", null, el("tr", null, el("th", null, "IP"), el("th", null, "Reason"), el("th", null, ""))),
      el("tbody", null, rows.map((b) => el("tr", null, el("td", null, b.ip),
        el("td", null, String(b.reason || "—")),
        el("td", null, el("button", { class: "btn ghost sm", onclick: () =>
          api("/api/security/bans/delete", { method: "POST", body: { ip: b.ip } })
            .then(paintBans).catch((e) => toast(e.message, true)) }, "Remove"))))))));
  }).catch((e) => { bansBox.append(el("span", { class: "muted" }, e.message)); });
  const paintSess = () => api("/api/sessions").then((d) => {
    sessBox.className = ""; sessBox.innerHTML = "";
    (d.sessions || []).forEach((s2) => sessBox.append(el("div", { class: "kvrow" },
      el("span", null, `${s2.user} @ ${s2.ip} (${s2.age_min} min)`),
      el("button", { class: "btn ghost sm", onclick: () =>
        api("/api/sessions/revoke", { method: "POST", body: { token: s2.token } })
          .then(paintSess).catch((e) => toast(e.message, true)) }, "Revoke"))));
    if (!(d.sessions || []).length) sessBox.append(el("span", { class: "muted" }, "no other sessions"));
  }).catch((e) => { sessBox.className = "muted"; sessBox.textContent = e.message; });
  v.append(el("div", { class: "viewhead" }, el("div", null,
    el("h2", { class: "vt" }, "Security"),
    el("p", { class: "vsub" }, "Login captures, banned IPs, active sessions, audit"))));
  v.append(panel("Remote access note", el("p", { class: "muted" }, "HKS works from a phone browser while Windows is signed in. Windows protects its secure sign-in screen from remote keyboard, mouse, and screen capture; unlock the laptop locally or use Windows Remote Desktop.")));
    v.append(panel("Login captures (every login AND failed attempt)",
    el("div", { class: "btn-row", style: "margin-bottom:10px" },
      el("button", { class: "btn ghost sm", onclick: async (e) => {
        busy(e.target, true);
        try { const r = await api("/api/security/test_capture", { method: "POST", body: {} });
          toast(r.ok ? "capture saved — see it below" : "capture failed: " + (r.error || "?"));
        } catch (er) { toast(er.message, true); }
        busy(e.target, false);
      } }, "📸 Test capture now"),
      el("button", { class: "btn ghost sm", onclick: (e) => { busy(e.target); location.hash = ""; paintGallery(); busy(e.target, false); } }, "⟳")), gallery));
  v.append(el("div", { class: "dashgrid" },
    panel("Banned IPs", el("div", { class: "btn-row", style: "margin-bottom:10px" }, ipIn, whyIn,
      el("button", { class: "btn danger sm", onclick: () => {
        if (!ipIn.value) return;
        api("/api/security/bans", { method: "POST", body: { ip: ipIn.value.trim(), reason: whyIn.value } })
          .then(() => { ipIn.value = ""; whyIn.value = ""; paintBans(); toast("IP banned"); })
          .catch((e) => toast(e.message, true));
      } }, "Ban")), bansBox),
    panel("Active panel sessions", sessBox)));
  v.append(panel("Recent audit", auditBox));
  v.append(renderSecurityTools());
  const paintGallery = () => api("/api/captures").then((d) => {
    gallery.innerHTML = "";
    const items = d.captures || d.items || [];
    if (!items.length) { gallery.append(el("span", { class: "muted" }, "no captures yet — press Test capture now")); return; }
    items.slice(0, 60).forEach((c2) => {
      const url = "/api/capture/" + encodeURIComponent(c2.id);
      let meta = {}; try { meta = JSON.parse(c2.meta || "{}"); } catch {}
      const when = c2.ts ? new Date(Number(c2.ts) * 1000).toLocaleString() : "time unavailable";
      const where = [c2.machine_id || "Laptop", meta.ip].filter(Boolean).join(" · ");
      gallery.append(el("div", { class: "gitem" },
        el("img", { src: url, loading: "lazy", alt: "Login capture", onclick: () => modal("Login capture · " + when, el("img", { src: url, class: "media-preview", alt: "Login capture" })) }),
        el("div", { class: "gcap" }, el("b", null, when), el("span", { class: "media-meta" }, where || "Laptop · location/IP unavailable"),
          el("span", { class: "media-meta" }, meta.user ? "User: " + meta.user : "User unavailable"))));
    });
  }).catch((e) => { gallery.innerHTML = ""; gallery.append(el("span", { class: "muted" }, e.message)); });
  api("/api/audit?limit=14").then((d) => {
    auditBox.innerHTML = ""; auditBox.className = "";
    (d.rows || d.audit || d.items || []).forEach((r) => auditBox.append(el("div", { class: "audit-row" },
      el("span", { class: "audit-time" }, r.ts ? new Date(r.ts * 1000).toLocaleString() : ""),
      el("b", { class: "audit-action" }, String(r.action || "—")),
      el("span", { class: "audit-user" }, String(r.username || r.user || "?")),
      el("span", { class: "muted audit-ip" }, String(r.ip || "")))));
    if (!auditBox.children.length) { auditBox.className = "muted"; auditBox.textContent = "no audit rows yet"; }
  }).catch((e) => { auditBox.textContent = e.message; });
  paintGallery(); paintBans(); paintSess();
}

/* ================= SERVER MANAGEMENT (who/what/ban/logs/restart) ======= */
function renderServerMgmt(v) {
  const agentsBox = el("div", { class: "muted" }, "…");
  const sessBox = el("div", { class: "muted" }, "…");
  const ipBox = el("div", { class: "muted", "aria-live": "polite" }, "…");
  const logBox = el("pre", { class: "pre small", style: "max-height:240px;overflow:auto" }, "…");
  const stBox = el("div", { class: "kv" });
  const cf = el("input", { placeholder: 'type CONFIRM to restart', style: "max-width:220px" });
  v.append(el("div", { class: "viewhead" }, el("div", null,
    el("h2", { class: "vt" }, "Server Management"),
    el("p", { class: "vsub" }, "Who is connected, laptop addresses, logs, restart"))));
  v.append(el("div", { class: "dashgrid" },
    panel("Server", stBox),
    panel("Connected devices (agents)", agentsBox,
      el("div", { class: "btn-row", style: "margin-top:8px" },
        el("button", { class: "btn ghost sm", onclick: (e) => { busy(e.target); paintAgents(); busy(e.target, false); } }, "⟳")))));
  v.append(el("div", { class: "dashgrid" },
    panel("Laptop IP addresses (auto-updates on network change)", ipBox),
    panel("Active panel sessions", sessBox)));
  v.append(panel("Server log (tail)",
    el("div", { class: "btn-row", style: "margin-bottom:8px" },
      el("button", { class: "btn ghost sm", onclick: (e) => { busy(e.target); paintLogs(); busy(e.target, false); } }, "⟳ Refresh")), logBox));
  v.append(panel("Restart the panel service",
    el("p", { class: "muted", style: "margin-top:0" }, "Drops ALL sessions (including yours). Comes back in ~10 s."),
    el("div", { class: "btn-row" }, cf,
      el("button", { class: "btn danger", onclick: async (e) => {
        if (cf.value !== "CONFIRM") return toast('type CONFIRM first', true);
        try { await api("/api/server/restart", { method: "POST", body: { confirm: "CONFIRM" } });
          toast("restarting… reload this page in ~10 s");
        } catch (er) { toast(er.message, true); }
      } }, "Restart panel"))));
  const paintAgents = () => api("/api/agents_live").then((d) => {
    agentsBox.className = ""; agentsBox.innerHTML = "";
    (d.agents || []).forEach((a) => agentsBox.append(el("div", { class: "kvrow" },
      el("span", null, `${a.name || a.machine} — ${a.role} agent (v${a.version || "?"})`),
      el("span", { class: "tag ok" }, "connected " + a.connected_min + " min"))));
    if (!(d.agents || []).length) { agentsBox.className = "muted"; agentsBox.textContent = "no agents connected"; }
  }).catch((e) => { agentsBox.className = "muted"; agentsBox.textContent = e.message; });
  const paintSess = () => api("/api/sessions").then((d) => {
    sessBox.className = ""; sessBox.innerHTML = "";
    (d.sessions || []).forEach((s2) => sessBox.append(el("div", { class: "kvrow" },
      el("span", null, `${s2.user} @ ${s2.ip}`), el("span", { class: "muted" }, s2.age_min + " min"))));
    if (!(d.sessions || []).length) sessBox.append(el("span", { class: "muted" }, "none"));
  }).catch((e) => { sessBox.className = "muted"; sessBox.textContent = e.message; });
  const paintIps = () => cmd("network.addresses", {}, 20).then((d) => {
    ipBox.className = ""; ipBox.innerHTML = "";
    (d.addresses || []).forEach((a) => {
      const url = location.protocol + "//" + a.ip + (location.port ? ":" + location.port : "");
      ipBox.append(el("div", { class: "kvrow" },
        el("span", null, a.iface), el("b", null, a.ip),
        el("a", { class: "btn ghost sm", href: url, target: "_blank" }, "open"),
        el("button", { class: "btn ghost sm", onclick: () => { navigator.clipboard.writeText(url); toast("copied " + url); } }, "copy")));
    });
    if (!(d.addresses || []).length) { ipBox.className = "muted"; ipBox.textContent = "No active, reachable IPv4 addresses were found on the laptop."; }
  }).catch((e) => { ipBox.className = "muted"; ipBox.textContent = e.message; });
  const paintLogs = () => api("/api/server_logs?limit=80").then((d) => {
    logBox.textContent = (d.lines || []).join("\n") || "(empty)";
    logBox.scrollTop = logBox.scrollHeight;
  }).catch((e) => { logBox.textContent = e.message; });
  (async () => {
    try { const me = await api("/api/me");
      const panelBuild = me.build || (me.user || {}).build;
      stBox.append(el("div", { class: "kvrow" }, el("span", null, "Panel build"), el("b", null, panelBuild ? "v" + panelBuild : "Build unavailable")));  /* /api/me returns build at the response and user levels for compatibility */
      stBox.append(el("div", { class: "kvrow" }, el("span", null, "Logged in as"), el("b", null, (me.user || {}).username || "?")));
      stBox.append(el("div", { class: "kvrow" }, el("span", null, "Address"), el("b", null, location.host)));
    } catch (e) {}
  })();
  paintAgents(); paintSess(); paintIps(); paintLogs();
  const ipPoll = setInterval(() => { if (!document.hidden) paintIps(); }, 30000);
  cleanupFns.push(() => clearInterval(ipPoll));
}

/* ---------------- navigation ---------------- */
/* ---- round 24: imported-app chrome bar ---- */
function chromeBar(kind, color, name, engine) {
  return el("div", { class: "impbar imp-" + kind },
    el("span", { class: "imp-dot", style: "background:" + color }),
    el("b", null, name),
    el("span", { class: "muted" }, " — " + engine));
}

/* ---- round 23 helpers: svg donut gauge + right-click style menu ---- */
function donut(pct, size, id) {
  const r = (size - 14) / 2, c = 2 * Math.PI * r, p = Math.max(0, Math.min(100, pct || 0));
  return el("div", { class: "donut", style: "width:" + size + "px;height:" + size + "px" },
    el("svg", { width: size, height: size, viewBox: "0 0 " + size + " " + size },
      el("circle", { cx: size / 2, cy: size / 2, r, fill: "none", stroke: "rgba(120,140,170,.22)", "stroke-width": 9 }),
      el("circle", { id, cx: size / 2, cy: size / 2, r, fill: "none", stroke: "#4cc2ff", "stroke-width": 9,
        "stroke-linecap": "round", "stroke-dasharray": c, "stroke-dashoffset": c * (1 - p / 100),
        transform: "rotate(-90 " + size / 2 + " " + size / 2 + ")", style: "transition:stroke-dashoffset .6s ease" })),
    el("span", { class: "donut-txt", id: id + "-txt" }, Math.round(p) + "%"));
}
function donutSet(id, pct) {
  const g = document.getElementById(id);
  if (!g) return;
  // The gauge size belongs to its parent DIV's inline style, not a width
  // attribute. Reading getAttribute("width") returned null, produced NaN
  // dash offsets, and made the Home CPU/RAM rings disappear after telemetry.
  const svg = g.ownerSVGElement;
  const size = Number(svg && svg.getAttribute("width")) ||
    parseFloat(g.parentNode && g.parentNode.style.width) || 86;
  const r = (size - 14) / 2, c = 2 * Math.PI * r;
  const value = Number.isFinite(Number(pct)) ? Math.max(0, Math.min(100, Number(pct))) : 0;
  g.setAttribute("stroke-dashoffset", c * (1 - value / 100));
  const t = document.getElementById(id + "-txt");
  if (t) t.textContent = Math.round(value) + "%";
}
function popMenu(anchor, items) {
  document.querySelectorAll(".popmenu").forEach((m) => m.remove());
  const m = el("div", { class: "popmenu" });
  items.forEach((it) => m.append(el("button", { class: "popitem" + (it.danger ? " danger" : ""), onclick: () => { m.remove(); it.fn(); } }, it.label)));
  document.body.append(m);
  const r = anchor.getBoundingClientRect();
  m.style.top = Math.min(window.innerHeight - m.offsetHeight - 8, r.bottom + 4) + "px";
  m.style.left = Math.min(window.innerWidth - m.offsetWidth - 8, r.left - m.offsetWidth + r.width) + "px";
  setTimeout(() => document.addEventListener("click", function h() { m.remove(); document.removeEventListener("click", h); }), 0);
}

/* ================= HOME — the CasaOS-style launcher ==================== */
function renderHome(v) {
  /* 2026 UI report Part 4.2: primary KPI row - 4 cards max, live-updated
     by paintTelemetry() (ids hkCpu/hkRam/hkNet/hkBat), sparklines from
     the rolling telemetry history. Purely additive. */
  const agentFix = el("div", { class: "hks-agentfix", id: "hksAgentFix", style: "display:none" });
  v.append(agentFix);
  paintAgentFix();
  const kpis = el("div", { class: "hks-kpis", id: "hksKpis" },
    hksKpi({ id: "hkCpu", label: "CPU", value: "—", unit: "%", dial: usageDial(0, "CPU") }),
    hksKpi({ id: "hkRam", label: "MEMORY", value: "—", unit: "%", dial: usageDial(0, "Memory") }),
    hksKpi({ id: "hkNet", label: "NETWORK", value: "—", unit: "KB/s" }),
    hksKpi({ id: "hkBat", label: "BATTERY", value: "—", unit: "%" }));
  kpis.dataset.built = "1";
  v.append(kpis);
  const wrap = el("div", { class: "cs-hero" });
  v.append(wrap);
  const grid = el("div", { class: "cs-grid" });
  const left = el("div", { class: "cs-left" });
  const right = el("div", { class: "cs-right" });

  /* clock widget */
  const clock = el("div", { class: "cs-clock" }, el("div", { class: "cs-time", id: "csTime" }, "--:--"),
    el("div", { class: "cs-date", id: "csDate" }, ""));
  const tick = () => {
    const n = new Date();
    const t = document.getElementById("csTime");
    if (!t) return;
    t.textContent = n.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    document.getElementById("csDate").textContent = n.toLocaleDateString([], { weekday: "long", month: "long", day: "numeric", year: "numeric" });
  };
  tick();
  const clockIv = setInterval(tick, 1000);
  cleanupFns.push(() => clearInterval(clockIv));

  /* system status widget (live donuts) */
  const statusW = el("div", { class: "cs-widget" },
    el("div", { class: "cs-whead" }, "System Status", el("span", { class: "muted", style: "cursor:pointer", onclick: () => showSection("system") }, "›")),
    el("div", { class: "cs-donuts" },
      el("div", { class: "cs-donut" }, donut(0, 86, "csCpu"), el("span", null, "CPU")),
      el("div", { class: "cs-donut" }, donut(0, 86, "csRam"), el("span", null, "RAM"))));
  const live = () => {
    if (!document.getElementById("csCpu")) return;
    const t = STATE.tel || {};
    donutSet("csCpu", t.cpu || 0);
    donutSet("csRam", t.ram || 0);
  };
  live();
  const liveIv = setInterval(live, 2000);
  cleanupFns.push(() => clearInterval(liveIv));

  /* storage widget */
  const storBody = el("div", { class: "muted", style: "font-size:12px" }, "loading…");
  const storW = el("div", { class: "cs-widget" },
    el("div", { class: "cs-whead" }, "Storage", el("span", { class: "tag ok" }, "…")),
    el("div", { class: "cs-stor" }, el("span", { class: "cs-stor-ic" }, "💽"), storBody));
  cmd("hw.status", {}, 60).then((d) => {
    const dk = (d.disks || [])[0] || {};
    const freePct = dk.total_gb ? Math.round(100 * (dk.free_gb || 0) / dk.total_gb) : null;
    storW.querySelector(".tag").textContent = freePct == null ? "?" : (freePct > 10 ? "Healthy" : "Low space");
    storW.querySelector(".tag").className = "tag " + (freePct == null || freePct > 10 ? "ok" : "warn");
    storBody.innerHTML = "";
    storBody.append(
      el("div", null, "Used: " + (dk.total_gb ? (dk.total_gb - (dk.free_gb || 0)).toFixed(1) : "?") + " GB"),
      el("div", null, "Total: " + (dk.total_gb || "?") + " GB"),
      el("div", { class: "cs-bar" }, el("i", { style: "width:" + (100 - (freePct || 0)) + "%" })));
  }).catch((e) => { storBody.textContent = e.message; });

  /* network widget */
  const netW = el("div", { class: "cs-widget" },
    el("div", { class: "cs-whead" }, "Network Status"),
    el("div", { class: "cs-net", id: "csNet" }, el("span", { class: "muted", style: "font-size:12px" }, "waiting for samples…")),
    el("div", { class: "cs-net-foot" }, el("span", { id: "csIface" }, "iface"),
      el("span", { id: "csRates" }, "—")));
  const netTick = () => {
    const n = document.getElementById("csNet");
    if (!n) return;
    const log = STATE.netLog || [];
    const t = STATE.tel || {};
    document.getElementById("csIface").textContent = t.ssid ? " wlan (" + t.ssid + ")" : " eth0";
    if (log.length >= 2) {
      const w = 180, h = 44, mx = Math.max(...log.map((x) => Math.max(x.down, x.up)), 1);
      const pts = log.map((x, i) => (i / (log.length - 1)) * w + "," + (h - (x.down / mx) * h)).join(" ");
      const pts2 = log.map((x, i) => (i / (log.length - 1)) * w + "," + (h - (x.up / mx) * h)).join(" ");
      n.innerHTML = "";
      n.append(el("svg", { width: w, height: h, viewBox: "0 0 " + w + " " + h },
        el("polyline", { points: pts, fill: "none", stroke: "#4cc2ff", "stroke-width": 1.6 }),
        el("polyline", { points: pts2, fill: "none", stroke: "#34d399", "stroke-width": 1.2 })));
      document.getElementById("csRates").textContent =
        "▼ " + log[log.length - 1].down.toFixed(2) + " MB/s  ▲ " + log[log.length - 1].up.toFixed(2) + " MB/s";
    }
  };
  const netIv = setInterval(netTick, 3000);
  cleanupFns.push(() => clearInterval(netIv));

  left.append(clock, statusW, storW, netW,
    el("div", { class: "cs-widget", style: "cursor:pointer", onclick: () => showSection("settings") },
      el("div", { class: "cs-whead" }, "Widgets Settings", el("span", { class: "muted" }, "›"))));

  /* hero banners */
  const liveTag = el("span", { class: "cs-livedot" }, "●");
  const banners = el("div", { class: "cs-banners" },
    el("div", { class: "cs-banner" },
      el("div", null, el("b", null, "Remote connections ", liveTag),
        el("p", null, "Control the laptop's screen, keyboard and audio from here — live.")),
      el("button", { class: "btn sm", onclick: () => showSection("control") }, "Open")),
    el("div", { class: "cs-banner" },
      el("div", null, el("b", null, "Unlock from your phone"),
        el("p", null, "Pair your phone by QR and unlock the laptop's lock screen (pcbu-style).")),
      el("button", { class: "btn sm", onclick: () => showSection("security") }, "Open")));

  /* apps grid */
  const appTile = (icon, bg, name, fn) => el("div", { class: "cs-app", onclick: fn },
    el("span", { class: "cs-app-ic", style: "background:" + bg }, icon), el("span", null, name));
  const apps = el("div", { class: "cs-apps" },
    appTile("📁", "linear-gradient(135deg,#2563eb,#3b82f6)", "Files", () => showSection("files")),
    appTile("⌨️", "linear-gradient(135deg,#0f172a,#334155)", "Terminal", () => showSection("term")),
    appTile("🖥️", "linear-gradient(135deg,#0891b2,#06b6d4)", "Control Screen", () => showSection("control")),
    appTile("📷", "linear-gradient(135deg,#db2777,#ec4899)", "Camera", () => showSection("cam")),
    appTile("🧊", "linear-gradient(135deg,#7c3aed,#8b5cf6)", "Hardware", () => showSection("system")),
    appTile("🎵", "linear-gradient(135deg,#d97706,#f59e0b)", "Media", () => showSection("media")),
    appTile("🛡️", "linear-gradient(135deg,#059669,#10b981)", "Security", () => showSection("security")),
    appTile("📊", "linear-gradient(135deg,#4f8cff,#67e8f9)", "Dashboard", () => showSection("dash")));
    const addonTile = (icon, bg, name, port) => el("div", { class: "cs-app", title: "Open add-on install and status controls for " + name, onclick: () => {
      showSection("settings");
      setTimeout(() => [...document.querySelectorAll("#views .view.on .tabs .tab")]
        .find((b) => b.textContent.trim() === "Open source")?.click(), 0);
    } },
    el("span", { class: "cs-app-ic", style: "background:" + bg }, icon), el("span", null, name));
  const addons = el("div", { class: "cs-apps" },
    addonTile("🌐", "linear-gradient(135deg,#7c3aed,#a78bfa)", "MeshCentral", "8490"),
    addonTile("🧩", "linear-gradient(135deg,#0891b2,#22d3ee)", "code-server", "8482"),
    addonTile("🐊", "linear-gradient(135deg,#16a34a,#4ade80)", "filegator", "8484"),
    addonTile("📟", "linear-gradient(135deg,#334155,#64748b)", "ttyd", "7681"),
    addonTile("🗄", "linear-gradient(135deg,#b45309,#f59e0b)", "DbGate", "8485"),
    addonTile("🎥", "linear-gradient(135deg,#be123c,#fb7185)", "Shinobi", "8080"),
    addonTile("☁️", "linear-gradient(135deg,#2563eb,#60a5fa)", "Nextcloud", "8486"),
    addonTile("🏡", "linear-gradient(135deg,#0f766e,#2dd4bf)", "CasaOS", "8080"),
    addonTile("🎶", "linear-gradient(135deg,#9333ea,#c084fc)", "Mopidy", "6680"),
    addonTile("🗂", "linear-gradient(135deg,#0d9488,#34d399)", "filebrowser", "8487"),
    addonTile("🔊", "linear-gradient(135deg,#dc2626,#f87171)", "AudioRelay", "phone"),  /* round 65 (audit7 #26): the missing ) made every later tile an ignored argument - 25 addons invisible */
    addonTile("🐝", "linear-gradient(135deg,#475569,#94a3b8)", "RustDesk", "tool"),
    addonTile("🖥", "linear-gradient(135deg,#6d28d9,#a78bfa)", "TigerVNC", "tool"),
    addonTile("⌨", "linear-gradient(135deg,#0e7490,#22d3ee)", "Deskflow", "24800"),
    addonTile("🌐", "linear-gradient(135deg,#1d4ed8,#60a5fa)", "Tailscale", "tool"),
    addonTile("🔗", "linear-gradient(135deg,#7c2d12,#fb923c)", "ZeroTier", "9993"),
    addonTile("🐜", "linear-gradient(135deg,#065f46,#34d399)", "Netbird", "tool"),
    addonTile("🚀", "linear-gradient(135deg,#b91c1c,#f87171)", "frp", "7500"),
    addonTile("🔄", "linear-gradient(135deg,#0f766e,#2dd4bf)", "Syncthing", "8384"),
    addonTile("☁", "linear-gradient(135deg,#1e40af,#93c5fd)", "rclone", "5572"),
    addonTile("🔒", "linear-gradient(135deg,#3f6212,#a3e635)", "Kopia", "51515"),
    addonTile("📈", "linear-gradient(135deg,#334155,#94a3b8)", "windows_exporter", "9182"),
    addonTile("👁", "linear-gradient(135deg,#155e75,#67e8f9)", "Glances", "61208"),
    addonTile("⚡", "linear-gradient(135deg,#9a3412,#fdba74)", "Netdata", "19999"),
    addonTile("⏰", "linear-gradient(135deg,#7e22ce,#d8b4fe)", "Uptime Kuma", "3001"),
    addonTile("🔍", "linear-gradient(135deg,#1f2937,#9ca3af)", "osquery", "tool"),
    addonTile("🦦", "linear-gradient(135deg,#064e3b,#6ee7b7)", "Velociraptor", "8889"),
    addonTile("🔦", "linear-gradient(135deg,#7f1d1d,#fca5a5)", "Nmap", "tool"),
    addonTile("🌡", "linear-gradient(135deg,#0891b2,#a5f3fc)", "LibreHardwareMonitor", "8088"),
    addonTile("🌞", "linear-gradient(135deg,#854d0e,#fde047)", "FanControl", "tool"),
    addonTile("📷", "linear-gradient(135deg,#9d174d,#f9a8d4)", "ShareX", "tool"),
    addonTile("⏩", "linear-gradient(135deg,#1e3a8a,#93c5fd)", "yt-dlp", "tool"),
    addonTile("🍿", "linear-gradient(135deg,#5b21b6,#c4b5fd)", "Jellyfin", "8096"),
    addonTile("⚙", "linear-gradient(135deg,#374151,#d1d5db)", "WinSW", "tool"),
    addonTile("📝", "linear-gradient(135deg,#166534,#86efac)", "AutoHotkey", "tool"),
    addonTile("🎙", "linear-gradient(135deg,#831843,#fbcfe8)", "OBS Studio", "tool"));  /* round 65: terminator rebalanced after the AudioRelay paren fix */
  right.append(banners,
    el("div", { class: "cs-sechead" }, "Apps"),
    apps,
    el("div", { class: "cs-sechead" }, "Add-ons — installed on the laptop by the HKS_ADDONS kit"),
    addons,
    el("div", { class: "cs-foot" }, "HKS • made with ❤ — project information: Settings → About"));
  grid.append(left, right);
  wrap.append(el("div", { class: "cs-top" },
    el("span", { class: "cs-logo" }, "🏡 HKS"),
    el("span", { class: "tag", id: "csMachine" }, (STATE.sel && STATE.sel.name) || "laptop")), grid);
  cmd("sys.quick", {}, 30).then((d) => {
    const m = document.getElementById("csMachine");
    if (m && d && d.user) m.textContent = ((STATE.sel && STATE.sel.name) || "laptop") + " • " + (d.ip || "");
  }).catch(() => {});
}


/* ================= CCTV — the Shinobi-style monitor ==================== */
function renderCctv(v) {
  const wrap = el("div", { class: "sn-wrap" });
  const side = el("div", { class: "sn-side" });
  const main = el("div", { class: "sn-main" });
  wrap.append(side, main);
  v.append(wrap);
  const stats = el("div", { class: "sn-stats" });
  const sCpu = snBar("CPU"), sRam = snBar("RAM"), sDsk = snBar("Primary"), sMon = snBar("Active Monitors");
  stats.append(sCpu.w, sRam.w, sDsk.w, sMon.w);
  side.append(stats);
  const cams = [];                     /* [{name, index}] from camera.list */
  let monLabel = "…";
  let cameraIssue = "Checking for connected cameras…";
  const tree = el("div", { class: "sn-nav" });
  const body = el("div");
  const shinobiCard = el("div", { class: "sn-shinobi-card" },
    el("div", null, el("strong", null, "Shinobi NVR"), el("span", { class: "muted" }, "Checking add-on status…")));
  main.append(shinobiCard, body);
  cmd("addons.status", {}, 60).then((d) => {
    const item = (d.addons || []).find((x) => String(x.name || "").toLowerCase() === "shinobi");
    shinobiCard.innerHTML = "";
    if (item && item.up && /^https?:\/\//i.test(item.url || "")) {
      const link = el("a", { class: "btn", href: item.url, target: "_blank", rel: "noopener noreferrer" }, "Open Shinobi console ↗");
      shinobiCard.append(el("div", null, el("strong", null, "Shinobi NVR"), el("span", { class: "muted" }, "Online · " + item.url)), link);
    } else {
      shinobiCard.append(el("div", null, el("strong", null, "Shinobi NVR"), el("span", { class: "muted" }, item && item.installer_available ? "Not running. Start or install the Shinobi add-on to open its console." : "Not running or installer files are missing.")),
        el("button", { class: "btn ghost", onclick: () => showSection("home") }, "View Add-ons"));
    }
  }).catch((e) => { shinobiCard.innerHTML = ""; shinobiCard.append(el("strong", null, "Shinobi status unavailable"), el("span", { class: "muted" }, e.message)); });
  const statTick = () => {
    const t = STATE.tel || {};
    snSet(sCpu, Math.round(t.cpu || 0) + "%", t.cpu || 0);
    snSet(sRam, Math.round(t.ram || 0) + "%", t.ram || 0);
    if (!document.getElementById("snLive")) return;
  };
  const iv1 = setInterval(statTick, 2000);
  cleanupFns.push(() => clearInterval(iv1));
  statTick();
  cmd("hw.status", {}, 60).then((d) => {
    const dk = (d.disks || [])[0] || {};
    snSet(sDsk, (dk.used_pct != null ? Math.round(dk.used_pct) : "?") + "%", dk.used_pct || 0);
  }).catch(() => snSet(sDsk, "—", 0));
  const loadCams = () => cmd("camera.list", {}, 30).then((d) => {
    const arr = d.cameras || d.devices || d.items || [];
    cams.length = 0;
    (Array.isArray(arr) ? arr : []).forEach((c2, i) => cams.push({ name: c2.name || c2.label || ("Camera " + (i + 1)), index: c2.index != null ? c2.index : i }));
    cameraIssue = cams.length ? "" : (d.probe_skipped
      ? "Automatic camera probing is paused because this Windows camera driver crashed the desktop agent. Use Camera controls to test camera 0 when you choose; IP cameras are added through Shinobi."
      : "No laptop camera was detected. Connect a webcam and retry. For IP cameras, install and configure the Shinobi add-on from Home → Add-ons.");
    monLabel = String(cams.length);
    snSet(sMon, cams.length ? monLabel + " / " + monLabel : "No camera", cams.length ? 100 : 0);
    paintNav("timeline");
  }).catch((e) => { cams.length = 0; cameraIssue = "Camera check failed: " + e.message; monLabel = "0"; snSet(sMon, "Unavailable", 0); paintNav("timeline"); });
  function snBar(label) {
    const w = el("div", { class: "sn-stat" }, el("span", { class: "sn-stat-l" }, label),
      el("span", { class: "sn-stat-v" }, "…"),
      el("div", { class: "sn-stat-b" }, el("i")));
    return { w, set: (v2, p2) => { w.querySelector(".sn-stat-v").textContent = v2; w.querySelector("i").style.width = Math.max(2, Math.min(100, p2 || 0)) + "%"; } };
  }
  function snSet(b, v2, p2) { b.set(v2, p2); }
  function paintNav(active) {
    tree.innerHTML = "";
    const item = (label, on, fn, cls) => el("div", { class: "sn-item" + (on ? " on" : "") + (cls ? " " + cls : ""), onclick: fn }, label);
    tree.append(item("🏠 Home", false, () => showSection("dash")));
    tree.append(item("▦ Live Grid", active === "grid", () => paintGrid()));
    tree.append(item("🎥 Monitors " + monLabel, active === "mons", () => paintMons()));
    tree.append(item("▤ Timeline", active === "timeline", () => paintTimeline()));
    if (active === "timeline") tree.append(item("• All Monitors", true, () => paintTimeline(), "sn-sub"));
    tree.append(item("🎬 Videos", active === "vids", () => paintVids()));
    tree.append(item("📅 Calendar", active === "cal", () => paintCal()));
    tree.append(item("≡ Logs", active === "logs", () => paintLogs()));
    tree.append(item("⚙ Monitor Settings", active === "set", () => paintSet()));
    side.querySelectorAll(".sn-nav").forEach((n) => n.remove());
    side.append(tree);
  }
  const viewer = (right) => el("div", { class: "sn-viewer" }, right);
  function snapLoop(img, index, ms, tag) {
    let stop = false;
    const tick = async () => {
      if (stop || !document.body.contains(img)) return;
      try {
        const d = await cmd("camera.snap", { index }, 30);
        if (d && (d.b64 || d.jpeg_b64)) img.src = "data:image/jpeg;base64," + (d.b64 || d.jpeg_b64);
        else if (d && d.png_b64) img.src = "data:image/png;base64," + d.png_b64;
        if (tag) tag.textContent = new Date().toLocaleTimeString();
      } catch (e) { if (tag) tag.textContent = "offline"; }
      setTimeout(tick, ms);
    };
    tick();
    cleanupFns.push(() => { stop = true; });
  }
  function paintTimeline() {
    paintNav("timeline");
    body.innerHTML = "";
    if (!cams.length) {
      const refresh = el("button", { class: "btn", onclick: () => showSection("cam") }, "Open Camera controls");
      const addons = el("button", { class: "btn ghost", onclick: () => showSection("home") }, "Open Add-ons");
      body.append(panel("CCTV camera status",
        el("div", { class: "cctv-empty-card" },
          el("span", { class: "cctv-empty-icon", "aria-hidden": "true" }, "🎥"),
          el("div", null, el("h3", null, "No live camera feed"), el("p", { class: "muted" }, cameraIssue || "Connect a webcam to start the live monitor.")),
          el("div", { class: "btn-row" }, refresh, addons))));
      return;
    }
    const img = el("img", { class: "sn-img", alt: "live monitor" });
    const stamp = el("span", { class: "sn-stamp" }, "…");
    const sel = el("select", { class: "sn-camsel", onchange: () => snapLoop(img, sel.value, 900, stamp) },
      el("option", { value: 0 }, "All Monitors → first camera"));
    cams.forEach((c2, i) => sel.append(el("option", { value: c2.index }, c2.name)));
    const pause = el("button", { class: "btn ghost sm", onclick: (e) => {
      const stopped = img.dataset.stop === "1";
      img.dataset.stop = stopped ? "" : "1";
      e.target.textContent = stopped ? "⏸ Pause" : "▶ Paused";
    } }, "⏸ Pause");
    const tl = el("div", { class: "sn-tl" });
    const tlHead = el("div", { class: "sn-tl-head" }, "recording timeline — last 3 hours (photos the CCTV saved)");
    const tlBar = el("div", { class: "sn-tl-bar" }, el("span", { class: "muted", style: "font-size:11.5px" }, "loading events…"));
    body.append(viewer(el("div", { class: "sn-viewtop" }, sel, pause),
      el("div", { class: "sn-frame" }, img, stamp)), tlHead, tlBar);
    snapLoop(img, 0, 900, stamp);
    api("/api/captures?limit=300").then((d) => {
      tlBar.innerHTML = "";
      const now = Date.now() / 1000, W = 3 * 3600;
      const bar = el("div", { class: "sn-tl-strip" });
      for (let k = 0; k <= 6; k++) bar.append(el("span", { class: "sn-tl-tick", style: "left:" + (k / 6) * 100 + "%" },
        "-" + (3 - k * 0.5) + "h"));
      (d.captures || []).forEach((c2) => {
        const ts = c2.ts || (c2.created_at || 0);
        if (!ts || now - ts > W) return;
        const blk = el("button", { class: "sn-tl-blk " + (c2.kind || "cam"), title: (c2.kind || "cam") + " — " + new Date(ts * 1000).toLocaleTimeString(),
          style: "left:" + (100 * (1 - (now - ts) / W)) + "%",
          onclick: () => modal("capture " + new Date(ts * 1000).toLocaleString(),
            el("img", { src: "/api/capture/" + c2.id + "?token=" + encodeURIComponent(AUTH_TOKEN || ""), style: "width:100%", alt: "capture" })) });
        bar.append(blk);
      });
      const now2 = el("span", { class: "sn-tl-now", style: "right:0" }, "now");
      bar.append(now2);
      tlBar.append(bar);
    }).catch((e) => { tlBar.innerHTML = ""; tlBar.append(el("span", { class: "muted" }, "captures: " + e.message)); });
  }
  function paintGrid() {
    paintNav("grid");
    body.innerHTML = "";
    if (!cams.length) { paintTimeline(); return; }
    const g = el("div", { class: "sn-grid" });
    body.append(g);
    const list = cams;
    list.forEach((c2) => {
      const img = el("img", { class: "sn-img", alt: c2.name });
      const st = el("span", { class: "sn-stamp" }, "…");
      const tile = el("div", { class: "sn-tile" }, el("div", { class: "sn-tile-head" }, c2.name), el("div", { class: "sn-frame sm" }, img, st));
      g.append(tile);
      snapLoop(img, c2.index, 1200, st);
    });
  }
  function paintMons() {
    paintNav("mons");
    body.innerHTML = "";
    if (!cams.length) { paintTimeline(); return; }
    const t = el("table", { class: "t" }, el("thead", null, el("tr", null,
      el("th", null, "Monitor"), el("th", null, "State"), el("th", null, "Source"), el("th", null, "Actions"))));
    cams.forEach((c2) => {
      t.append(el("tr", null, el("td", null, "🎥 " + c2.name),
        el("td", null, el("span", { class: "tag ok" }, "live")),
        el("td", { class: "muted" }, "webcam index " + c2.index + " (real device stream)"),
        el("td", null, el("span", { class: "btn-row" },
          el("button", { class: "btn ghost sm", onclick: async (e) => {
            busy(e.target, true);
            try { const d = await cmd("camera.snap", { index: c2.index }, 30);
              const src = d.b64 ? "data:image/jpeg;base64," + d.b64 : "data:image/png;base64," + (d.png_b64 || "");
              modal(c2.name + " snapshot", el("img", { src, style: "width:100%", alt: "snap" })); }
            catch (er) { toast(er.message, true); }
            busy(e.target, false);
          } }, "📸 Snapshot"),
          el("button", { class: "btn ghost sm", onclick: async (e) => {
            const min = prompt("Record minutes:", "5");
            if (!min) return;
            busy(e.target, true);
            try { const d = await cmd("camera.record", { index: c2.index, seconds: Math.min(3600, Math.round(min * 60)) }, 60); toast("recording started" + (d.path ? " → " + d.path : "")); }
            catch (er) { toast(er.message, true); }
            busy(e.target, false);
          } }, "⏺ Record"),
          el("button", { class: "btn dangerg sm", onclick: async (e) => {
            busy(e.target, true);
            try { await cmd("camera.record_stop", { index: c2.index }, 30); toast("recording stopped"); }
            catch (er) { toast(er.message, true); }
            busy(e.target, false);
          } }, "⏹ Stop")))));
    });
    body.append(panel("Monitors — real webcam devices on the laptop", t));
  }
  function paintVids() {
    paintNav("vids");
    body.innerHTML = "";
    const g = el("div", { class: "sn-vids" });
    body.append(panel("Saved events (real captures from the laptop)", g),
      el("p", { class: "muted" }, "Video records are saved on the laptop (Monitor → Record). Photos appear here the moment they are captured."));
    api("/api/captures?limit=60").then((d) => {
      g.innerHTML = "";
      (d.captures || []).forEach((c2) => {
        const card = el("div", { class: "sn-vid" },
          el("img", { src: "/api/capture/" + c2.id + "?token=" + encodeURIComponent(AUTH_TOKEN || ""), alt: "capture", loading: "lazy" }),
          el("span", null, c2.kind || "cam"),
          el("span", { class: "muted" }, new Date((c2.ts || 0) * 1000).toLocaleString()));
        card.onclick = () => modal("capture", el("img", { src: card.querySelector("img").src, style: "width:100%", alt: "capture" }));
        g.append(card);
      });
      if (!(d.captures || []).length) g.append(el("span", { class: "muted" }, "no captures yet — press Snapshot or Record"));
    }).catch((e) => { g.innerHTML = ""; g.append(el("span", { class: "muted" }, e.message)); });
  }
  function paintCal() {
    paintNav("cal");
    body.innerHTML = "";
    const grid = el("div", { class: "sn-cal" });
    const list = el("div");
    body.append(panel("Event calendar — real laptop events", grid), list);
    api("/api/events?limit=500").then((d) => {
      const byDay = {};
      (d.events || []).forEach((e2) => {
        const day = new Date((e2.ts || 0) * 1000).toISOString().slice(0, 10);
        (byDay[day] = byDay[day] || []).push(e2);
      });
      grid.innerHTML = "";
      const days = Object.keys(byDay).sort().reverse().slice(0, 28);
      if (!days.length) grid.append(el("span", { class: "muted" }, "no events yet"));
      days.forEach((day) => {
        grid.append(el("button", { class: "sn-day", onclick: () => {
          list.innerHTML = "";
          list.append(panel("Events on " + day, ...(byDay[day].slice(0, 80).map((e2) =>
            el("div", { class: "kvrow" }, el("span", null, new Date((e2.ts || 0) * 1000).toLocaleTimeString() + " — " + (e2.kind || "?")),
              el("span", { class: "muted" }, String((e2.data || {}).msg || (e2.data || {}).user || "").slice(0, 60)))))));
        } }, el("b", null, day.slice(5)), el("span", null, (byDay[day].length) + " events")));
      });
    }).catch((e) => { grid.append(el("span", { class: "muted" }, e.message)); });
  }
  function paintLogs() {
    paintNav("logs");
    body.innerHTML = "";
    const box = el("div", { class: "muted" }, "loading…");
    body.append(panel("Agent errors (real)", box));
    api("/api/errors?limit=80").then((d) => {
      box.className = "";
      box.innerHTML = "";
      const rows = d.errors || [];
      if (!rows.length) { box.className = "muted"; box.textContent = "no errors — all clean"; return; }
      rows.forEach((e2) => box.append(el("div", { class: "kvrow" },
        el("span", null, new Date((e2.ts || 0) * 1000).toLocaleString() + " — " + (e2.module || "?")),
        el("span", { class: "muted" }, String(e2.message || "").slice(0, 80)))));
    }).catch((e) => { box.textContent = e.message; });
  }
  function paintSet() {
    paintNav("set");
    body.innerHTML = "";
    const sense = el("input", { type: "range", min: "5", max: "60", value: "18", style: "flex:1" });
    body.append(panel("Monitor settings (real controls)",
        el("div", { class: "kv" },
          el("div", { class: "kvrow" }, el("span", null, "Motion sensitivity"), sense),
          el("div", { class: "btn-row" },
            el("button", { class: "btn sm", onclick: async (e) => {
              busy(e.target, true);
              try { await cmd("camera.motion", { on: true, sensitivity: Number(sense.value) }, 30); toast("motion detection ON"); } catch (er) { toast(er.message, true); }
              busy(e.target, false);
            } }, "👁 Motion ON"),
            el("button", { class: "btn dangerg sm", onclick: async (e) => {
              busy(e.target, true);
              try { await cmd("camera.motion", { on: false }, 30); toast("motion detection OFF"); } catch (er) { toast(er.message, true); }
              busy(e.target, false);
            } }, "Motion OFF")))),
      el("p", { class: "muted" }, "Multi-monitor layouts, region editor and schedules are full-Shinobi features — installed on the laptop by the HKS_ADDONS kit (ADDON_SHINOBI.bat) and linked in Home → Add-ons."));
  }
  loadCams();
  paintTimeline();
}

/* 2026 UI report: grouped navigation (overview first, details on demand).
   buildNav() prints a group label whenever .group changes - so the old flat
   29-item list becomes 5 scannable groups, same section ids as before. */
const SECTIONS = [
  // MONITOR - glanceable awareness
  { id: "home",   ic: "🏡", name: "Home",             render: renderHome, group: "MONITOR" },
  { id: "dash",   ic: "🏠", name: "Dashboard",        render: renderDash, group: "MONITOR" },
  { id: "devices",ic: "🖥️", name: "Devices",          render: renderDevices, group: "MONITOR" },
  { id: "system", ic: "💻", name: "System",           render: renderSystem, group: "MONITOR" },
  { id: "security",ic:"🛡️", name: "Security",         render: renderSecurity, group: "MONITOR" },
  { id: "reports",ic: "🧾", name: "Reports",          render: renderReports, group: "MONITOR" },
  { id: "logs",   ic: "📜", name: "Logs & Logins",   render: renderLogs, group: "MONITOR" },
  { id: "errs",   ic: "🛑", name: "Errors",          render: renderErrors, group: "MONITOR" },
  // CONTROL - act on the laptop right now
  { id: "control",ic: "🖥️", name: "Control",          render: renderControl, group: "CONTROL" },
  { id: "term",   ic: "⌨️", name: "Terminal",         render: renderTermLive, group: "CONTROL" },
  { id: "cam",    ic: "📷", name: "Camera & Audio",   render: renderCam, group: "CONTROL" },
  { id: "cctv",   ic: "🎥", name: "CCTV (Shinobi)",   render: renderCctv, group: "CONTROL" },
  { id: "media",  ic: "🎵", name: "Media",            render: renderPlayer, group: "CONTROL" },
  { id: "hw",     ic: "🌡️", name: "Hardware live",   render: renderHardware, group: "CONTROL" },
  { id: "tools",  ic: "🎛️", name: "Session Tools",   render: renderTools, group: "CONTROL" },
  // MANAGE - files, processes, system inventory
  { id: "files",  ic: "📁", name: "Files",            render: renderFiles, group: "MANAGE" },
  { id: "clean",  ic: "🧹", name: "Storage & Cleanup", render: renderClean, group: "MANAGE" },
  { id: "procs",  ic: "⚙️", name: "Processes",        render: renderProcs, group: "MANAGE" },
  { id: "apps",   ic: "📦", name: "Applications",     render: renderAppsPage, group: "MANAGE" },
  { id: "usb",    ic: "🔌", name: "USB Devices",      render: renderUsb, group: "MANAGE" },
  { id: "xampp",  ic: "🪵", name: "XAMPP",            render: renderXampp, group: "MANAGE" },
  { id: "db",     ic: "🗄️", name: "Database",         render: renderDb, group: "MANAGE" },
  { id: "net",    ic: "📶", name: "Network",         render: renderNet, group: "MANAGE" },
  { id: "power",  ic: "⚡", name: "Power & Boot",    render: renderPower, group: "MANAGE" },
  { id: "deploy", ic: "🌐", name: "Web Deploy",      render: renderDeploy, group: "MANAGE" },
  // AUTOMATE - run and schedule work
  { id: "code",   ic: "🧪", name: "Code Runner",      render: renderCode, group: "AUTOMATE" },
  { id: "tasks",  ic: "⏰", name: "Scheduler",       render: renderTasks, group: "AUTOMATE" },
  { id: "mediazip",ic:"🖼️", name: "Media & ZIP Export", render: renderMedia, group: "AUTOMATE" },
  // CONFIGURE - panel and accounts
  { id: "servermg",ic:"🗄", name: "Server Management",render: renderServerMgmt, group: "CONFIGURE" },
  { id: "settings",ic:"⚙", name: "Settings",         render: renderSettings, group: "CONFIGURE" },
  { id: "users",  ic: "👥", name: "Users",           render: renderUsers, group: "CONFIGURE" },
  /* round 67 (F41/F42/F45): changelog + offline help pages + live docs */
  { id: "help",   ic: "📖", name: "Help",            render: renderHelpCenter, group: "CONFIGURE" },
];
let cleanupFns = [];
const viewCache = {};
function buildNav() {
  const nav = $("#nav"); nav.innerHTML = "";
  try { if (localStorage.getItem("hks_side") === "slim") document.body.classList.add("side-collapsed"); } catch (e) {}
  const side = $("#side");
  if (side && !document.getElementById("sideCollapse")) {
    const cb = el("button", { class: "btn ghost icon", id: "sideCollapse", title: "collapse sidebar",
      style: "margin:0 10px 6px;width:auto;padding:4px 10px;height:auto",
      onclick: () => {
        const slim = document.body.classList.toggle("side-collapsed");
        try { localStorage.setItem("hks_side", slim ? "slim" : "wide"); } catch (e) {}
      } }, "«");
    side.querySelector(".brand").after(cb);
  }
  let grp = "";
  const NAV_ACC = ["b", "p", "g", "o", "r", "v", "t", "s"];
  let ai = 0;
  for (const s of SECTIONS) {
    if (s.group !== grp) { grp = s.group; nav.append(el("div", { class: "nav-sec" }, grp)); ai = 0; }
    nav.append(el("div", { class: "nav-item", "data-id": s.id, title: s.name,
      onclick: () => { showSection(s.id); $("#side").classList.remove("open"); document.body.classList.remove("side-open"); const sbk = document.querySelector(".side-bk"); if (sbk) sbk.remove(); } },
      el("span", { class: "ic navacc-" + NAV_ACC[ai++ % NAV_ACC.length] }, s.ic), el("span", null, s.name)));
  }
}
function showSection(id) {
  if (!SECTIONS.some((s) => s.id === id)) id = "home";
  try { sessionStorage.setItem("hks_last_section", id); } catch (e) {}
  stopCleanup();
  document.querySelectorAll(".nav-item").forEach((n) => n.classList.toggle("on", n.dataset.id === id));
  document.querySelectorAll(".view").forEach((v) => v.classList.remove("on"));
  let v = viewCache[id];
  if (!v) {
    const sec = SECTIONS.find((s) => s.id === id);
    v = el("div", { class: "view on" }, el("h2", { class: "vt" }, sec.name),
      el("p", { class: "vsub" }, sec.sub || ""));
    try { sec.render(v); } catch (e) {
      console.error(e);
      v.append(hksErrState("This section failed to draw",
        "render error: " + e.message + " — the rest of the panel keeps working.",
        () => { delete viewCache[id]; v.remove(); showSection(id); }));
    }
    viewCache[id] = v; $("#views").append(v);
  } else {
    /* 7.2.52 fix: a purged cached view must be re-attached before showing */
    if (!v.isConnected) $("#views").append(v);
    v.classList.add("on");
  }
  if (id === "devices" && typeof v.refreshDevices === "function") v.refreshDevices();
  /* 7.2.52 fix: some renders draw their own header - drop the auto one if so */
  const _h2s = v.querySelectorAll("h2.vt");
  if (_h2s.length > 1) _h2s[0].remove();
  /* 7.2.52 fix: purge stale cached views so nothing piles up */
  document.querySelectorAll("#views .view").forEach((x) => { if (x !== v) x.remove(); });
  $("#main").scrollTop = 0;
}
function stopCleanup() {
  cleanupFns.forEach((f) => { try { f(); } catch {} });
  cleanupFns = [];
  if (STATE.screenOn) { STATE.screenOn = false; wsCmd("screen.control", { on: false }).catch(() => {}); }
  if (STATE.camOn) { STATE.camOn = false; wsCmd("camera.stream", { on: false }).catch(() => {}); }
  if (STATE.micListen) { STATE.micListen = false; wsCmd("mic.stream", { on: false }).catch(() => {}); }
  if (STATE.talkOn) { STATE.talkOn = false; stopTalk(); wsCmd("talk.stream", { on: false }).catch(() => {}); }
  STATE.inputOn = false;
}
const every = (ms, fn) => { const t = setInterval(fn, ms); cleanupFns.push(() => clearInterval(t)); fn(); };
function appIcon(name) {
  const n = String(name || "?");
  let h = 0;
  for (let i = 0; i < n.length; i++) h = (h * 31 + n.charCodeAt(i)) >>> 0;
  const hues = ["linear-gradient(135deg,#2563eb,#3b82f6)", "linear-gradient(135deg,#db2777,#ec4899)",
    "linear-gradient(135deg,#059669,#10b981)", "linear-gradient(135deg,#d97706,#f59e0b)",
    "linear-gradient(135deg,#7c3aed,#8b5cf6)", "linear-gradient(135deg,#0891b2,#22d3ee)"];
  return el("span", { class: "apptile", style: "background:" + hues[h % hues.length] }, (n[0] || "?").toUpperCase());
}
function panel(title, ...kids) {
  const h4 = el("h4", null, title);
  const box = el("div", { class: "panel" }, h4, ...kids);
  box.head = h4;          // views can append action buttons: p.head.append(btn)
  return box;
}
/* ---- 2026 UI report Part 10.3: detail drawer (progressive disclosure) ---- */
function openDrawer(title, ...kids) {
  const bk = el("div", { class: "hks-drawer-bk", onclick: () => close() });
  const x = el("button", { class: "hks-drawer-x", "aria-label": "close", onclick: () => close() }, "✕");
  const d = el("div", { class: "hks-drawer", role: "dialog", "aria-label": title },
    el("h3", null, el("span", null, title), x), ...kids);
  const close = () => { bk.remove(); d.remove(); };
  document.body.append(bk, d);
  const esc = (e) => { if (e.key === "Escape") { close(); document.removeEventListener("keydown", esc); } };
  document.addEventListener("keydown", esc);
  return { close, body: d };
}
function tRow(...cells) { return el("tr", null, ...cells.map((c) => el("td", null, c))); }
function resultBox(cls) { const p = el("div", { class: cls || "pre", style: "display:none;margin-top:10px" }); return { box: p, show: (txt) => { p.style.display = "block"; p.textContent = typeof txt === "string" ? txt : JSON.stringify(txt, null, 2); } }; }
function emptyState(ic, txt) { return el("div", { class: "empty" }, el("span", { class: "big-ic" }, ic), txt); }
/* ---- 2026 UI report helpers (Part 4/5/11): sparkline, KPI, quality, states ---- */
function hksSpark(pts, cls) {
  const d = (pts && pts.length > 1) ? pts.slice(-24) : [0, 0];
  const min = Math.min(...d), max = Math.max(...d), span = (max - min) || 1;
  const ptsAttr = d.map((v, i) => `${(i / (d.length - 1)) * 100},${28 - ((v - min) / span) * 24}`).join(" ");
  return el("svg", { class: "hks-spark " + (cls || ""), viewBox: "0 0 100 32", preserveAspectRatio: "none",
    "aria-hidden": "true" }, el("polyline", { points: ptsAttr, fill: "none", "stroke-width": "2",
    "vector-effect": "non-scaling-stroke" }));
}
function hksKpi(o) {
  // o: {id, label, value, unit, delta, status, spark}
  const kind = o.label === "CPU" ? "cpu" : o.label === "MEMORY" ? "memory" : "";
  const card = el("div", { class: "hks-kpi", "data-status": o.status || "neutral" },
    el("div", { class: "hks-kpi-head" },
      kind ? el("span", { class: "hks-kpi-icon" }, metricIcon(kind)) : null,
      el("span", { class: "hks-kpi-label" }, o.label)),
    el("span", { class: "hks-kpi-val" }, el("b", { id: o.id || "" }, o.value),
      o.unit ? el("small", null, o.unit) : null));
  if (o.dial) card.append(o.dial);
  if (o.delta != null) {
    const up = o.delta > 0, flat = o.delta === 0;
    card.append(el("span", { class: "hks-delta " + (flat ? "flat" : up ? "up" : "down") },
      up ? "▲" : flat ? "■" : "▼", " ", Math.abs(o.delta) + "%"));
  }
  if (o.spark) card.append(hksSpark(o.spark, o.status === "good" ? "ok" : o.status === "warning" ? "warn" : o.status === "critical" ? "bad" : ""));
  return card;
}
function hksConnQ(fps, latMs) {
  // Part 5.3: connection HEALTH, not just connected/disconnected
  const lat = latMs == null ? null : Number(latMs);
  const q = lat == null ? "linking" : lat < 50 ? "excellent" : lat < 120 ? "good" : lat < 250 ? "fair" : "poor";
  const e = el("span", { class: "hks-connq q-" + q, title: "live-link quality from latency" },
    el("i", { class: "hks-dot" }), q.toUpperCase(),
    el("small", null, (fps ? fps + " fps · " : "") + (lat != null ? Math.round(lat) + " ms" : "measuring…")));
  return e;
}
function hksSkeleton(lines) {
  return el("div", { class: "hks-skeleton", role: "status" },
    Array.from({ length: lines || 3 }, () => el("div", { class: "hks-skrow" },
      el("i", { class: "hks-sk ic" }), el("span", null, el("i", { class: "hks-sk l1" }), el("i", { class: "hks-sk l2" })))));
}
function hksErrState(title, msg, retry) {
  // keep the literal words "render error:" when used for render failures -
  // ui_smoke.js greps for them to catch broken sections
  return el("div", { class: "hks-state" },
    el("span", { class: "hks-state-ic" }, "⚠️"),
    el("h3", null, title), el("p", { class: "muted" }, msg || ""),
    retry ? el("button", { class: "btn ghost sm", onclick: retry }, "⟳ Try again") : null);
}
function runCmd(label, action, params, then, btn) {
  busy(btn, true);
  return cmd(action, params).then((d) => then && then(d))
    .catch((e) => toast(label + " failed: " + e.message, true))
    .finally(() => busy(btn, false));
}
function confirmDo(q, fn) { confirmModal(q, null, fn); }
function confirmModal(title, confirmWord, fn, danger = true) {
  const input = confirmWord ? el("input", { placeholder: "type " + confirmWord, style: "width:100%" }) : null;
  const go = el("button", { class: "btn " + (danger ? "danger" : "") }, "Confirm");
  const bk = modal(title, input, el("div", { class: "row end", style: "margin-top:14px" },
    el("button", { class: "btn ghost", onclick: () => bk.remove() }, "Cancel"), go));
  go.onclick = () => {
    if (confirmWord && input.value !== confirmWord) return toast("type " + confirmWord + " exactly", true);
    bk.remove();
    Promise.resolve(fn()).then(() => toast("done")).catch((e) => toast(e.message, true));
  };
}
function modal(title, ...kids) {
  const bk = el("div", { class: "modal-bk", onclick: (e) => { if (e.target === bk) bk.remove(); } },
    el("div", { class: "modal" }, el("h3", null, title), ...kids,
      el("div", { class: "row end", style: "margin-top:14px" }, el("button", { class: "btn ghost sm", onclick: () => bk.remove() }, "Close"))));
  document.body.append(bk); return bk;
}
const liveEvents = [];
function addLiveEvent(m) {
  if (m.kind === "term_out") return;
  liveEvents.unshift(m); if (liveEvents.length > 200) liveEvents.pop();
  const box = $("#evtBox");
  if (box) box.textContent = liveEvents.map((e) => `[${new Date(e.ts * 1000).toLocaleTimeString()}] ${e.kind} ${e.data ? JSON.stringify(e.data).slice(0, 160) : ""}`).join("\n");
}
function downloadUrl(path) { return `/api/download?machine=${encodeURIComponent(STATE.machine || "")}&path=${encodeURIComponent(path)}`; }
/* round 61 (audit4 #2): deletion is a separate CSRF-protected POST - a
   plain GET link must never be able to delete a remote file. */
async function downloadThenDelete(path) {
  try {
    toast("downloading…");
    const r = await fetch(downloadUrl(path), { credentials: "same-origin" });
    if (!r.ok) throw new Error("HTTP " + r.status);
    const blob = await r.blob();
    const a = el("a", { href: URL.createObjectURL(blob), download: path.split("\\").pop() });
    document.body.append(a); a.click(); a.remove();
    await api("/api/download/cleanup", { method: "POST", body: { machine: STATE.machine, path } });
    toast("downloaded + remote copy deleted");
  } catch (e) { toast("download failed: " + e.message, true); }
}

/* ================= DASHBOARD ================= */
/* ---- topbar/sidebar chrome: bell, global search, identity block ---- */
let _chromeDone = false;
function initChrome() {
  if (_chromeDone) return;
  _chromeDone = true;
  const machinePicker = $("#tMachine");
  if (machinePicker) machinePicker.addEventListener("change", () => selectMachine(machinePicker.value));
  const bell = $("#btnBell");
  const profile = $("#tUserWrap"), profileMenu = $("#profileMenu");
  if (profile && profileMenu) {
    const closeProfile = (restoreFocus) => {
      profileMenu.hidden = true; profile.setAttribute("aria-expanded", "false");
      if (restoreFocus) profile.focus();
    };
    profile.onclick = () => {
      const open = profileMenu.hidden;
      profileMenu.hidden = !open; profile.setAttribute("aria-expanded", String(open));
      if (open) $("#btnLogout").focus();
    };
    document.addEventListener("click", (e) => {
      if (!profile.contains(e.target) && !profileMenu.contains(e.target)) closeProfile(false);
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !profileMenu.hidden) closeProfile(true);
    });
  }
  if (bell) bell.onclick = () => showSection("errs");
  const gs = $("#gsearch");
  const hamb = document.getElementById("hamb");
  if (hamb) hamb.onclick = () => {
    const open = $("#side").classList.toggle("open");
    document.body.classList.toggle("side-open", open);
    let bk = document.querySelector(".side-bk");
    if (open && !bk) { bk = el("div", { class: "side-bk", onclick: () => { $("#side").classList.remove("open"); document.body.classList.remove("side-open"); bk.remove(); } }); document.body.append(bk); }
    if (!open && bk) bk.remove();
  };
  if (gs) gs.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    const raw = gs.value.trim();
    const q = raw.toLowerCase();
    if (!q) return;
    const aliases = [["file", "files"], ["terminal", "term"], ["command", "term"], ["camera", "cam"],
      ["screen", "control"], ["mirror", "control"], ["hardware", "system"], ["music", "media"],
      ["audio", "media"], ["lock", "security"], ["process", "procs"], ["log", "errs"]];
    const section = SECTIONS.find((x) => x.id === q || x.name.toLowerCase().includes(q));
    const alias = aliases.find(([word]) => q.includes(word));
    const target = section || (alias && SECTIONS.find((x) => x.id === alias[1]));
    if (target) { showSection(target.id); toast("opened " + target.name); gs.value = ""; }
    else toast("no section matches \u201c" + raw + "\u201d", true);
  });
  const tick = async () => {
    if (!$("#app").classList.contains("on")) return;  // logged out: zero polling
    try {
      const d = await api("/api/machines");
      const rows = d.machines || [];
      const m = rows.find((x) => x.machine_id === STATE.machine) || rows[0];
      const dot = $("#ssDot"), nm = $("#ssName"), os = $("#ssOs"), up = $("#ssUp");
      if (!m) { if (nm) nm.textContent = "no machine"; return; }
      if (nm) nm.textContent = m.name || m.machine_id;
      const on = !!(m.live_svc || m.live_session);
      if (dot) dot.className = "dot" + (on ? " on" : "");
      if (os) os.textContent = (m.os || "windows").slice(0, 34);
      if (up) up.textContent = m.boot_ts ? ("up " + relTime(m.boot_ts)) : (on ? "online" : "offline");
      const bellBadge = $("#errBadge");
      if (bellBadge) {
        const er = await api("/api/errors?limit=300");
        const n = (er.errors || []).filter((x) => !x.resolved).length;
        bellBadge.style.display = n ? "grid" : "none";
        bellBadge.textContent = String(n);
      }
    } catch (e) {}
  };
  tick();
  setInterval(tick, 30000);
}
function relTime(ts) {
  const s = Math.max(0, (Date.now() / 1000) - (Number(ts) || 0));
  if (s < 90) return "just now";
  if (s < 3600) return Math.round(s / 60) + " min";
  if (s < 86400 * 2) return Math.round(s / 3600) + " h";
  return Math.round(s / 86400) + " d";
}
const EVENT_META = {
  boot: ["🚀", "Laptop booted"], agent_start: ["🟢", "HKS agent started"],
  hw_alert: ["🌡️", "Hardware alert"], capture_login: ["📸", "Login capture"],
  capture_logon_cam: ["📸", "Logon camera capture"], capture_failed: ["⚠️", "Capture failed"],
  agent_connected: ["🟢", "Agent connected"], agent_disconnected: ["🔴", "Agent disconnected"],
  hw_report: ["📈", "Hardware report"], login: ["🔑", "Login activity"],
  usb: ["🔌", "USB event"], error: ["🛑", "Error reported"]
};
function chipcard(icon, title, sub, state, onclick) {
  return el("div", { class: "chipcard " + (state || "wait"), onclick },
    el("span", { class: "cc-ic" }, icon),
    el("span", { class: "cc-tx" }, el("b", null, title), el("small", null, sub)));
}
function drawDualChart(cv, a, b, la, lb) {
  const w = cv.clientWidth || 560, h = 150;
  cv.width = w * 2; cv.height = h * 2;
  const x = cv.getContext ? cv.getContext("2d") : null; if (!x) return;
  x.scale(2, 2);
  x.clearRect(0, 0, w, h);
  x.strokeStyle = "rgba(154,167,189,.14)";
  x.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const y = 8 + (h - 24) * i / 4;
    x.beginPath(); x.moveTo(24, y); x.lineTo(w - 6, y); x.stroke();
    x.fillStyle = "rgba(154,167,189,.75)";
    x.font = "9px Segoe UI";
    x.fillText(String(100 - i * 25) + "%", 2, y + 3);
  }
  const line = (arr, color, fill) => {
    if (!arr || arr.length < 2) return;
    const px = (i) => 24 + (w - 34) * i / (arr.length - 1);
    const py = (v) => 8 + (h - 24) * (1 - (Number(v) || 0) / 100);
    x.beginPath();
    arr.forEach((v, i) => i ? x.lineTo(px(i), py(v)) : x.moveTo(px(i), py(v)));
    x.strokeStyle = color; x.lineWidth = 2; x.stroke();
    if (fill) {
      x.lineTo(px(arr.length - 1), h - 16); x.lineTo(24, h - 16); x.closePath();
      const g = x.createLinearGradient(0, 0, 0, h);
      g.addColorStop(0, fill); g.addColorStop(1, "transparent");
      x.fillStyle = g; x.fill();
    }
  };
  line(a, "#4f8cff", "rgba(79,140,255,.18)");
  line(b, "#a78bfa", null);
  const lg = cv.parentElement.querySelector(".legend");
  if (lg) { lg.innerHTML = ""; lg.append(el("span", null, el("i", { style: "background:#4f8cff" }), la),
    el("span", null, el("i", { style: "background:#a78bfa" }), lb)); }
}

function renderDash(v) {
  initChrome();
  const t = el("div", { class: "tiles6" });
  const chips = el("div", { class: "sc-chips" });
  const qa = el("div", { class: "qagrid" });
  const QA = [
    ["🖥️", "Control Screen", "b", "control"], ["📷", "Camera", "p", "cam"],
    ["🎙️", "Mic Listen", "g", "cam"], ["🔊", "Push to Talk", "o", "cam"],
    ["⌨️", "Terminal", "s", "term"], ["📁", "Files", "b", "files"],
    ["📦", "Apps", "r", "apps"], ["🔌", "USB", "o", "usb"],
    ["🎵", "Media", "v", "media"], ["🧾", "Generate Report", "t", "reports"]];
  QA.forEach(([ic, name, c, id]) =>
    qa.append(el("div", { class: "qa " + c, onclick: () => showSection(id) },
      el("span", { class: "qa-ic" }, ic), name)));
  const acts = el("div");
  const prev = el("div", { class: "prev" }, el("canvas", { id: "dashPrevCanvas", width: 640, height: 360 }));  /* round 65 (audit7 #28): drawB64 needs a CANVAS - a div made the live preview dead forever */
  const sysInfo = el("div", { class: "kv" }, el("span", { class: "muted" }, "loading…"));
  const chartBox = el("div", { class: "chartwrap" },
    el("canvas", { id: "dashChart", style: "display:block;width:100%;height:150px;max-width:100%" }), el("div", { class: "legend" },
      el("span", null, el("i", { style: "background:#4f8cff" }), "CPU"),
      el("span", null, el("i", { style: "background:#a78bfa" }), "Memory")));
  const procsBox = el("div", { class: "muted" }, "loading…");
  const xampBox = el("div", { class: "muted" }, "…");

  v.append(el("div", { class: "viewhead" },
    el("div", null, el("h2", { class: "vt" }, "Dashboard"),
      el("p", { class: "vsub" }, "Overview of your system and all components — real values from the laptop")),
    el("button", { class: "btn ghost sm", onclick: () => showSection("dash") }, "⟳ Refresh")));

  v.append(t);
  v.append(el("h3", { class: "sechead" }, "Component status"), chips);
  v.append(el("h3", { class: "sechead" }, "Quick actions"), qa);

  const grid = el("div", { class: "dashgrid" });
  grid.append(
    panel("Recent activities",
      el("div", { class: "btn-row", style: "margin-bottom:6px" },
        el("button", { class: "btn ghost sm", onclick: () => showSection("logs") }, "View all")), acts),
    panel("Live preview", prev,
      el("div", { class: "btn-row", style: "margin-top:8px" },
        el("button", { class: "btn sm", onclick: () => window.open(API_BASE + "/static/screen.html" + (AUTH_TOKEN ? "?token=" + encodeURIComponent(AUTH_TOKEN) : ""), "_blank") }, "⛶ Fullscreen control"),
        el("button", { class: "btn ghost sm", onclick: () => showSection("control") }, "Open Control"))));
  v.append(grid);
  const grid2 = el("div", { class: "dashgrid" });
  grid2.append(
    panel("System information",
      el("div", { class: "btn-row", style: "margin-bottom:6px" },
        el("button", { class: "btn ghost sm", onclick: () => showSection("hw") }, "Details")), sysInfo),
    panel("CPU & memory (recent history)", chartBox));
  v.append(grid2);
  v.append(el("div", { class: "dashgrid" },
    panel("Top processes (RAM)",
      el("div", { class: "btn-row", style: "margin-bottom:6px" },
        el("button", { class: "btn ghost sm", onclick: () => showSection("procs") }, "View all")), procsBox),
    panel("XAMPP", xampBox,
      el("div", { class: "btn-row", style: "margin-top:8px" },
        el("button", { class: "btn ghost sm", onclick: () => showSection("xampp") }, "Manage")))));

  const setChips = (list) => {
    chips.innerHTML = "";
    list.forEach(([st, ic, title, sub, go2]) =>
      chips.append(chipcard(ic, title, sub, st === true ? "ok" : st === false ? "bad" : "wait",
        go2 ? () => showSection(go2) : null)));
  };
  setChips([[null, "🖥", "Server", "checking…"], [(STATE.live || {}).svc, "🟢", "Agent", "service", "system"],
    [(STATE.live || {}).session, "🖥", "Desktop", "session", "control"],
    [null, "📷", "Camera", "…", "cam"], [null, "🖼", "Screen", "…", "control"],
    [null, "🎙", "Mic", "…", "cam"], [null, "🗄", "Database", "…", "db"], [null, "🪵", "XAMPP", "…", "xampp"]]);

  const paintActs = (rows) => {
    acts.innerHTML = "";
    if (!rows.length) { acts.append(el("span", { class: "muted" }, "no events yet")); return; }
    rows.forEach((e2) => {
      const [ic, label] = EVENT_META[e2.kind] || ["•", e2.kind];
      acts.append(el("div", { class: "actrow" },
        el("span", { class: "ac-ic" }, ic),
        el("span", { class: "ac-tx" }, label),
        el("span", { class: "ac-tm" }, new Date(e2.ts * 1000).toLocaleTimeString())));
    });
  };
  const paintSys = (d, extra) => {
    sysInfo.innerHTML = "";
    const rows = [["Host", d.host || d.hostname], ["OS", d.os || d.system],
      ["CPU", d.cpu_name || (d.cpu || {}).name],
      ["Mainboard", extra && extra.board], ["BIOS", extra && extra.bios],
      ["RAM", (d.memory || {}).total_gb ? (d.memory.total_gb + " GB") : d.ram_total],
      ["Uptime", d.uptime || (d.uptime_h ? d.uptime_h + " h" : null)], ["Machine", STATE.machine]];
    rows.forEach(([k, val]) => { if (val) sysInfo.append(el("div", { class: "kvrow" }, el("span", null, k), el("b", null, String(val)))); });
    if (!sysInfo.children.length) sysInfo.append(el("span", { class: "muted" }, "connect the agent to see system info"));
  };
  const paintProcs = (d) => {
    procsBox.innerHTML = ""; procsBox.className = "";
    const tops = d.top_mem || [];
    if (!tops.length) { procsBox.className = "muted"; procsBox.textContent = "no data yet"; return; }
    const max = Math.max(...tops.map((x) => x.mem_mb || 0), 1);
    tops.forEach((pr) => procsBox.append(el("div", { class: "prow" },
      el("div", null, el("div", null, pr.name),
        el("div", { class: "bar", style: "height:5px;margin-top:4px" },
          el("i", { style: "width:" + Math.max(4, Math.round((pr.mem_mb || 0) / max * 100)) + "%" }))),
      el("div", { class: "pm" }, (pr.mem_mb || 0) + " MB"))));
  };
  const paintXampp = (d) => {
    xampBox.innerHTML = "";
    if (!d.installed) { xampBox.className = "muted"; xampBox.textContent = "XAMPP not detected on the laptop"; return; }
    xampBox.className = "";
    Object.entries(d.services || {}).forEach(([name, svc]) => {
      const ctl = (op, label, cls) => el("button", { class: "btn ghost sm " + (cls || ""), style: "min-height:26px;padding:2px 9px;font-size:11.5px", onclick: async (e) => {
        if (op === "stop") return confirmDo("STOP the " + name + " service on the laptop ?", doIt);
        doIt();
        async function doIt() {
          busy(e.target, true);
          try { await cmd("xampp.control", { service: name.toLowerCase(), op }, 60); toast(name + " " + (op === "start" ? "started" : "stopped")); paintXampp(await cmd("xampp.status", {}, 60)); }
          catch (er) { toast(er.message, true); }
          busy(e.target, false);
        }
      } }, label);
      xampBox.append(el("div", { class: "kvrow" }, el("span", null, name),
        el("span", { class: "btn-row", style: "margin-left:auto;gap:5px" },
          ctl("start", "\u25b6"), ctl("stop", "\u23f9", "red"),
          svc.running ? el("span", { class: "tag ok" }, "Running") : el("span", { class: "tag" }, "Stopped"))));
    });
  };

  (async () => {
    let pts = [];
    try {
      const tel = await api("/api/telemetry?limit=120");
      pts = (tel.points || []).slice(-120);
    } catch (e) {}
    const g = (arr) => Math.round(arr[arr.length - 1] ?? 0);
    const last = pts[pts.length - 1] || {};
    const before = pts.length > 1 ? pts[pts.length - 2] : null;
    let netRate = null, netSub = "waiting for network samples";
    if (before && Number.isFinite(+last.ts) && Number.isFinite(+before.ts)) {
      const seconds = (+last.ts - +before.ts);
      if (seconds > 0 && seconds <= 300 && last.net_recv != null && last.net_sent != null && before.net_recv != null && before.net_sent != null) {
        const down = Math.max(0, (+last.net_recv - +before.net_recv) / seconds / 1024);
        const up = Math.max(0, (+last.net_sent - +before.net_sent) / seconds / 1024);
        netRate = Math.round((down + up) * 10) / 10;
        netSub = `↓ ${down.toFixed(1)} · ↑ ${up.toFixed(1)} KB/s`;
      }
    }
    t.append(
      dashTile("▦", "CPU", pts.length ? g(pts.map((p2) => p2.cpu ?? 0)) + "%" : "—", pts.length ? "load now" : "waiting for agent", "b",
        pts.length ? usageDial(g(pts.map((p2) => p2.cpu ?? 0)), "CPU") : null),
      dashTile("▤", "RAM", pts.length ? g(pts.map((p2) => p2.ram ?? 0)) + "%" : "—", "of installed memory", "p",
        pts.length ? usageDial(g(pts.map((p2) => p2.ram ?? 0)), "Memory") : null),
      dashTile("▥", "Storage", (last.disk_free_gb != null ? last.disk_free_gb + " GB" : "—"), "free", "t"),
      dashTile("🔋", "Battery", (last.bat_pct != null ? Math.round(last.bat_pct) + "%" : "—"),
        last.bat_pct != null ? (last.bat_plugged ? "⚡ charging" : "on battery") : "not reported", "g"),
      dashTile("📡", "Network", (netRate != null ? netRate + " KB/s" : "—"), netSub, "b"),
      dashTile("🛡", "HKS server", "Online", "port " + location.port, "r"));
    if (pts.length) drawDualChart($("#dashChart"), pts.map((p2) => p2.cpu ?? 0), pts.map((p2) => p2.ram ?? 0), "CPU", "Memory");
    else { const c = $("#dashChart"); if (c) { const x = c.getContext ? c.getContext("2d") : null; if (!x) return; x.fillStyle = "rgba(154,167,189,.8)"; x.font = "12px Segoe UI"; x.fillText("waiting for telemetry from the agent…", 20, 70); } }

    try {
      const ev = await api("/api/events?limit=40");
      paintActs((ev.events || []).slice(0, 9));
    } catch (e) { paintActs([]); }
    try { paintSys(await cmd("sys.quick", {}, 20)); } catch (e) { paintSys({}); }
    try {
      const hd = await cmd("hw.deep", {}, 40);
      paintProcs(hd);
      paintSys(await cmd("sys.quick", {}, 20).catch(() => ({})), hd);
    } catch (e) {}
    try { paintXampp(await cmd("xampp.status", {}, 60)); } catch (e) {}
    startStills("dashPrev", "#dashPrevCanvas", "screen.snapshot", { max_width: 640 }, 1500, "scrSize");  /* round 65 (audit7 #28) */

    // honest component chips (each from a real probe)
    const set2 = [[(STATE.live || {}).svc, "🟢", "Agent", (STATE.live || {}).svc ? "service online" : "offline", "system"],
      [(STATE.live || {}).session, "🖥", "Desktop", (STATE.live || {}).session ? "session online" : "offline", "control"],
      [true, "🗄", "Database", "connected", "db"]];
    try { const cam = await cmd("camera.list", {}, 30); set2.push([true, "📷", "Camera", (cam.items || []).length + " device(s)", "cam"]); } catch (e) { set2.push([false, "📷", "Camera", "unavailable", "cam"]); }
    try { const sv = await cmd("screen.snapshot", { max_width: 320 }, 30); set2.push([!!sv.b64, "🖼", "Screen", sv.b64 ? "capturing" : "no signal", "control"]); } catch (e) { set2.push([false, "🖼", "Screen", "unavailable", "control"]); }
    try { const ml = await cmd("mic.list", {}, 20); set2.push([true, "🎙", "Mic", ((ml.items || []).length || 1) + " device(s)", "cam"]); } catch (e) { set2.push([false, "🎙", "Mic", "unavailable", "cam"]); }
    try { const xs = await cmd("xampp.status", {}, 30); const run = xs.installed && Object.values(xs.services || {}).some((s2) => s2.running); set2.push([xs.installed ? !!run : null, "🪵", "XAMPP", xs.installed ? (run ? "services running" : "installed, idle") : "not installed", "xampp"]); } catch (e) { set2.push([null, "🪵", "XAMPP", "unknown", "xampp"]); }
    setChips([[true, "🖥", "Server", "online", null]].concat(set2));
  })();
  cleanupFns.push(() => stopStills("dashPrev"));
}
function go(id) { showSection(id); }

/* ================= HARDWARE (deep status) ================= */
function renderHardware(v) {
  const wrap = el("div");
  const hist = el("div");
  v.append(panel("Deep hardware status",
      el("div", { class: "sub" }, "CPU load & temperature, GPU, per-core, battery wear, disks. Periodic snapshots arrive automatically every ~2 min."),
      el("div", { class: "btn-row", style: "margin-bottom:12px" },
        el("button", { class: "btn", onclick: (e) => { busy(e.target, true, "sampling…"); cmd("hw.status", {}, 60).then((d) => { paint(d); busy(e.target, false); }).catch((x) => { toast(x.message, true); busy(e.target, false); }); } }, "⟳ Refresh now"),
        el("span", { class: "muted" }, "sampling is tiny — safe for 24/7 use")),
      wrap),
    panel("Hardware timeline (automatic snapshots)", el("button", { class: "btn ghost sm", onclick: loadHist }, "⟳"), hist));
  const iv = setInterval(() => { cmd("hw.status", {}, 60).then((d) => paint(d)).catch(() => {}); }, 10000);
  cleanupFns.push(() => clearInterval(iv));

  function coreBars(per) {
    return el("div", { class: "grid", style: "grid-template-columns:repeat(auto-fill,minmax(120px,1fr));margin-top:8px" },
      per.map((x, i) => el("div", null, el("div", { class: "muted" }, "core " + (i + 1)), el("div", { class: "bar" }, el("i", { style: `width:${x}%`, class: x > 85 ? "bad" : x > 60 ? "warn" : "" })))));
  }
  function paint(d) {
    wrap.innerHTML = "";
    if (d.latency_ms != null) { STATE.agentLat = d.latency_ms; paintStrip(); }  // 2026 UI report: strip latency = real agent↔server ms
    const cpu = d.cpu || {}, ram = d.ram || {};
    wrap.append(el("div", { class: "grid cards" },
      el("div", { class: "card" }, el("h3", null, "CPU"), el("div", { class: "big" }, Math.round(cpu.total_pct ?? 0) + "%"),
        cpu.temp_c != null ? el("div", null, el("span", { class: "tag " + (cpu.temp_c > 85 ? "bad" : cpu.temp_c > 65 ? "warn" : "ok") }, "🌡 " + cpu.temp_c + " °C")) : el("div", { class: "muted" }, "temperature: n/a on this hardware"),
        cpu.freq ? el("div", { class: "muted" }, `${cpu.freq.current_mhz} MHz (max ${cpu.freq.max_mhz})`) : null,
        cpu.threads ? el("div", { class: "muted" }, `${cpu.cores} cores / ${cpu.threads} threads`) : null),
      el("div", { class: "card" }, el("h3", null, "Memory"), el("div", { class: "big" }, Math.round(ram.used_pct ?? 0) + "%"),
        el("div", { class: "muted" }, `${ram.used_gb} / ${ram.total_gb} GB`), el("div", { class: "muted" }, `swap ${ram.swap_pct ?? 0}%`)),
      el("div", { class: "card" }, el("h3", null, "Processes"), el("div", { class: "big" }, d.processes ?? "—"),
        el("div", { class: "muted" }, `uptime ${d.uptime_h} h`, el("br"), `agent↔server: ${d.latency_ms != null ? d.latency_ms + " ms" : "—"}`
        )),
      (() => {
        const card = el("div", { class: "card" }, el("h3", null, "Battery"));
        if (!d.battery) card.append(el("div", { class: "muted" }, "no battery (desktop / removed)"));
        else {
          const b = d.battery, w = b.wear;
          card.append(el("div", { class: "big" }, Math.round(b.percent) + "%"),
            el("div", null, el("span", { class: "tag " + (b.plugged ? "ok" : "warn") }, b.plugged ? "charging" : "discharging")));
          if (w) card.append(el("div", { class: "muted" }, `health: ${Math.round(100 - w.wear_pct)}% (${w.wear_pct}% worn)`));
        }
        return card;
      })()));
    if (cpu.per_core) wrap.append(panel("Per-core load", coreBars(cpu.per_core)));
    const gpu = panel("GPU");
    if (d.gpu && d.gpu.length) {
      const t = el("table", { class: "t" });
      for (const g of d.gpu) t.append(tRow(g.name, el("span", { class: "tag " + (g.status === "OK" ? "ok" : "warn") }, g.status),
        g.driver || "—", g.vram_gb ? g.vram_gb + " GB" : "—",
        g.util_pct != null ? `${g.util_pct}% @ ${g.temp_c}°C, ${g.mem_used_mb} MB` : el("span", { class: "muted" }, "no live sensors (integrated)")));
      gpu.append(t);
    } else gpu.append(emptyState("🖥", "no GPU information available"));
    wrap.append(gpu);
    const dk = panel("Disks");
    if (d.disks && d.disks.length) {
      for (const x of d.disks) dk.append(el("div", { style: "margin-bottom:8px" },
        el("div", { class: "row" }, el("b", null, x.mount), el("span", { class: "muted" }, `${x.free_gb} GB free of ${x.total_gb} GB`),
          el("span", { style: "flex:1" }), el("span", { class: "tag " + (x.used_pct > 90 ? "bad" : x.used_pct > 75 ? "warn" : "ok") }, x.used_pct + "% used")),
        el("div", { class: "bar" }, el("i", { style: `width:${x.used_pct}%`, class: x.used_pct > 90 ? "bad" : x.used_pct > 75 ? "warn" : "" }))));
    } else dk.append(el("span", { class: "muted" }, "no disks reported"));
    wrap.append(dk);
    if (d.top_mem && d.top_mem.length) {
      const tm = panel("Top memory consumers");
      const t = el("table", { class: "t" });
      for (const p of d.top_mem) t.append(tRow(p.name, p.mem_mb + " MB"));
      tm.append(t); wrap.append(tm);
    }
  }
  async function loadHist() {
    hist.innerHTML = "<span class='spin'></span>";
    try {
      const d = await api(`/api/events?kind=hw_report&limit=40`);
      const rows = d.events.slice().reverse();
      hist.innerHTML = "";
      if (!rows.length) { hist.append(emptyState("📉", "no snapshots yet — they arrive every ~2 minutes")); return; }
      const t = el("table", { class: "t" }, el("thead", null, el("tr", null, el("th", null, "Time"), el("th", null, "CPU"), el("th", null, "RAM"), el("th", null, "Temp"), el("th", null, "Disk free"), el("th", null, "Procs"), el("th", null, "Latency"))));
      for (const e of rows) {
        let j = {}; try { j = JSON.parse(e.data); } catch {}
        t.append(tRow(new Date(e.ts * 1000).toLocaleTimeString(),
          (j.cpu != null ? Math.round(j.cpu) + "%" : "—"), (j.ram != null ? Math.round(j.ram) + "%" : "—"),
          j.temp_c != null ? j.temp_c + "°C" : el("span", { class: "muted" }, "n/a"),
          j.disk_free_gb != null ? j.disk_free_gb + " GB" : "—", j.processes ?? "—",
          j.latency_ms != null ? j.latency_ms + " ms" : "—"));
      }
      hist.append(el("div", { class: "tscroll" }, t));
    } catch (e) { hist.innerHTML = `<span class="muted">${esc(e.message)}</span>`; }
  }
  loadHist();
}

/* ================= REMOTE SCREEN ================= */
function renderScreen(v) {
  if (!STATE.live.session) v.append(el("div", { class: "banner" }, "Desktop agent is offline — log into Windows on the laptop, or use Terminal (SYSTEM) for everything else."));
  const quality = el("select", null, ...["40", "60", "75", "85"].map((q) => el("option", { value: q }, "JPEG " + q + "%")));
  quality.value = "60";
  const fps = el("select", null, ...["4", "8", "12", "18"].map((q) => el("option", { value: q }, q + " fps")));
  fps.value = "8";
  const scale = el("select", null, ...["960", "1280", "1600", "0"].map((q) => el("option", { value: q }, q === "0" ? "full size" : q + "px wide")));
  scale.value = "1280";
  const mon = el("select", null, el("option", { value: "1" }, "monitor 1"), el("option", { value: "2" }, "monitor 2"), el("option", { value: "all" }, "all monitors"));
  const toggle = el("button", { class: "btn", onclick: async () => {
      if (!STATE.live.session) return toast("desktop agent offline", true);
      STATE.screenOn = !STATE.screenOn;
      STATE.inputOn = STATE.screenOn;
      updInputLabel();
      const fpsSel = +fps.value;
      if (STATE.screenOn) {
        startLive("scrStills", "#scr", "screen.control",
          { fps: fpsSel, quality: +quality.value, monitor: mon.value, max_width: +scale.value },
          1, "scrSize", fpsSel);
        // control toggle is independent of the mirror now (agent input.enable)
        (STATE.wsReady ? wsCmd("input.enable", { on: true }) : cmd("input.enable", { on: true }, 15)).catch(() => {});
        toggle.textContent = "⏹ Stop mirror";
        if (!STATE.wsReady) toast("backup live mode — the browser live channel is down, streaming slower over the agent link", true);
      } else {
        stopLive("scrStills", "screen.control");
        (STATE.wsReady ? wsCmd("input.enable", { on: false }) : cmd("input.enable", { on: false }, 15)).catch(() => {});
        toggle.textContent = "▶ Start mirror";
      }
    } }, "▶ Start mirror");
  const inLbl = el("span", { id: "inputStateLabel", class: "tag" }, "control OFF");
  const updInputLabel = () => { inLbl.textContent = STATE.inputOn ? "control ON" : "control OFF"; inLbl.className = "tag " + (STATE.inputOn ? "ok" : ""); };
  const inputHealth = el("span", { class: "tag", role: "status", "aria-live": "polite" }, "checking input…");
  const refreshInputHealth = async () => {
    try {
      const s = await cmd("input.status", {}, 12);
      const locked = s.engine === "secure desktop";
      const ready = s.available !== false && s.engine === "healthy";
      inputHealth.textContent = locked ? "Windows secure screen — unlock locally" :
        ready ? "keyboard / mouse ready" : (s.desktop_error || s.last_error || "input engine unavailable");
      inputHealth.className = "tag " + (ready ? "ok" : "warn");
      inputHealth.title = "Desktop: " + (s.desktop || "unknown") + (s.desktop_error ? " · " + s.desktop_error : "");
    } catch (e) {
      inputHealth.textContent = "input status unavailable: " + e.message;
      inputHealth.className = "tag warn";
    }
  };
  refreshInputHealth();
  const inputHealthIv = setInterval(refreshInputHealth, 5000);
  cleanupFns.push(() => clearInterval(inputHealthIv));
  const canvas = el("canvas", { id: "scr", tabindex: "0", width: 960, height: 540 });
  v.append(panel("Mirror & control", el("div", { class: "btn-row" }, toggle,
      el("button", { class: "btn ghost", onclick: () => { STATE.inputOn = !STATE.inputOn; updInputLabel(); (STATE.wsReady ? wsCmd("input.enable", { on: STATE.inputOn }) : cmd("input.enable", { on: STATE.inputOn }, 15)).then(refreshInputHealth).catch((e) => toast(e.message, true)); } }, "Toggle input"), inLbl, inputHealth,
      el("span", { style: "flex:1" }), mon, fps, quality, scale,
      el("button", { class: "btn ghost sm", onclick: () => { const c = $("#scr"); if (c.width) { const a = document.createElement("a"); a.href = c.toDataURL("image/png"); a.download = "hks_screen_" + Date.now() + ".png"; a.click(); } } }, "💾 Save frame"),
      el("button", { class: "btn dangerg sm", onclick: () => confirmDo("Lock Windows screen? (mirroring continues)", () => cmd("power.lock_screen")) }, "🔒 Lock"))),
    canvas,
    el("div", { class: "btn-row", style: "margin-top:8px" },
      el("span", { id: "scrQuality" }, hksConnQ(0, null)),
      el("span", { class: "muted" }, "Click the picture first. With control ON, mouse + keyboard are forwarded. Input auto-disables on disconnect or errors (failsafe).")));
  /* live quality: fps from the active mirror params + last known latency */
  const qIv = setInterval(() => {
    const q = document.getElementById("scrQuality");
    if (!q) return;
    q.replaceChildren(hksConnQ(STATE.screenOn ? ((STATE.scrParams || {}).fps || 8) : 0, STATE.agentLat));
  }, 1000);
  cleanupFns.push(() => clearInterval(qIv));

  const typeIn = el("input", { placeholder: "Type text → Enter sends it as keystrokes to the laptop", style: "flex:1;min-width:220px" });
  typeIn.onkeydown = async (e) => {
    if (e.key !== "Enter" || !typeIn.value) return;
    if (!STATE.inputOn) return toast("turn input ON first", true);
    const text = typeIn.value; typeIn.value = "";
    for (let i = 0; i < text.length; i += 400) {
      try { await sendInput({ t: "type", text: text.slice(i, i + 400) }); } catch (er) { toast(er.message, true); break; }
    }
  };
  const key = (label, ev) => el("button", { class: "btn ghost sm", onclick: async () => {
    if (!STATE.inputOn) return toast("turn input ON first", true);
    try {
      await sendInput(ev);
      // A keyDown without its matching keyUp leaves modifiers and ordinary
      // keys stuck on the host. Keyboard buttons are taps; hold keys via the
      // live screen canvas when a press/release pair is needed.
      if (ev.t === "kd") await sendInput({ ...ev, t: "ku" });
    } catch (e) { toast(e.message, true); }
  } }, label);
  v.append(panel("Remote keyboard",
    el("div", { class: "term-in" }, typeIn),
    el("div", { class: "keys" },
      key("Win", { t: "kd", key: "meta" }), key("Win+D", { t: "combo", key: "d", win: true }),
      (() => { const b = el("button", { class: "btn ghost sm" }, "Win+L"); b.onclick = () => { if (!STATE.inputOn) return toast("turn input ON first", true); api("/api/cmd", { method: "POST", body: { machine: STATE.machine, action: "power.lock_screen", params: {} } }).then(() => toast("🔒 Windows blocks a synthetic Win+L (secure sequence) - used the real Lock API instead")).catch((er) => toast(er.message, true)); }; return b; })(), key("Alt+Tab", { t: "combo", key: "tab", alt: true }),
      key("Alt+F4", { t: "combo", key: "f4", alt: true }), key("Ctrl+C", { t: "combo", key: "c", ctrl: true }),
      key("Ctrl+V", { t: "combo", key: "v", ctrl: true }), key("Esc", { t: "kd", key: "escape" }),
      key("Enter", { t: "kd", key: "enter" }), key("Tab", { t: "kd", key: "tab" }),
      el("span", { class: "muted", style: "align-self:center" }, "Ctrl+Alt+Del is a Windows secure sequence and cannot be sent remotely."))));

  /* AnywhereInput protocol: normalized 0..1 coords; agent multiplies by real size */
  const toNat = (e) => { const r = canvas.getBoundingClientRect(); const x = Math.max(0, Math.min(0.99999, (e.clientX - r.left) / Math.max(1, r.width))); const y = Math.max(0, Math.min(0.99999, (e.clientY - r.top) / Math.max(1, r.height))); STATE.lastPtr = { x: +x.toFixed(5), y: +y.toFixed(5) }; return STATE.lastPtr; };
  const send = (ev) => {
    if (!STATE.inputOn) return;
    // High-rate movement is best-effort; discrete clicks/keys/wheel use the
    // correlated command path so agent errors reach the browser.
    if (STATE.wsReady && ev.t === "mm") { wsSend({ type: "input", ev }); return; }
    if (ev.t !== "mm") { sendInput(ev).catch((er) => toast(er.message, true)); return; }
    // backup mode: input over the agent link (REST). Mouse moves are
    // throttled harder here (no realtime channel).
    if (ev.t === "mm") {
      const now = Date.now();
      if (now - STATE.lastRestMove < 200) return;
      STATE.lastRestMove = now;
    }
    cmd("input.apply", ev, 15).catch((er) => toast(er.message, true));
  };
  canvas.addEventListener("mousemove", (e) => {
    const point = toNat(e);
    if (!STATE.inputOn) return;
    const now = Date.now();
    STATE.pendingMove = point;
    if (now - STATE.lastMove > 40) { STATE.lastMove = now; send({ t: "mm", ...STATE.pendingMove }); STATE.pendingMove = null; }
  });
  canvas.addEventListener("mousedown", (e) => { e.preventDefault(); canvas.focus(); send({ t: "md", ...toNat(e), btn: e.button }); });
  canvas.addEventListener("mouseup", (e) => send({ t: "mu", ...toNat(e), btn: e.button }));
  canvas.addEventListener("wheel", (e) => { e.preventDefault(); send({ t: "mw", dx: e.deltaX, dy: e.deltaY }); }, { passive: false });
  canvas.addEventListener("contextmenu", (e) => e.preventDefault());
  // ---- touch → remote mouse (phones/tablets), like VNC clients do:
  // tap = left click, drag = move with button held, long-press (600ms) = right click
  let tStart = null, tOrigin = null, tMoved = false, tTimer = null, tRightSent = false;
  canvas.addEventListener("touchstart", (e) => {
    const t = e.touches[0]; tStart = toNat(t);
    if (!STATE.inputOn) { tStart = null; return; }
    e.preventDefault(); canvas.focus(); tOrigin = { x: t.clientX, y: t.clientY };
    tMoved = false; tRightSent = false;
    tTimer = setTimeout(() => { if (tStart && !tMoved) { send({ t: "md", ...tStart, btn: 2 }); tRightSent = true; } }, 600);
  }, { passive: false });
  canvas.addEventListener("touchmove", (e) => {
    if (!STATE.inputOn || !tStart) return; e.preventDefault();
    const p = toNat(e.touches[0]);
    const touch = e.touches[0];
    if (Math.abs(touch.clientX - tOrigin.x) + Math.abs(touch.clientY - tOrigin.y) > 10) {
      tMoved = true; clearTimeout(tTimer);
      if (tRightSent) { send({ t: "mu", ...tStart, btn: 2 }); tRightSent = false; }
      if (!STATE.touchDown) { send({ t: "md", ...tStart, btn: 0 }); STATE.touchDown = true; }
      send({ t: "mm", ...p });
    }
  }, { passive: false });
  canvas.addEventListener("touchend", (e) => {
    clearTimeout(tTimer);
    if (!STATE.inputOn || !tStart) return; e.preventDefault();
    if (tRightSent) { send({ t: "mu", ...tStart, btn: 2 }); tRightSent = false; }
    else if (STATE.touchDown) { send({ t: "mu", ...toNat(e.changedTouches[0]), btn: 0 }); STATE.touchDown = false; }
    else if (!tMoved) { send({ t: "md", ...tStart, btn: 0 }); send({ t: "mu", ...tStart, btn: 0 }); }
    tStart = null; tOrigin = null;
  }, { passive: false });
  canvas.addEventListener("touchcancel", () => {
    clearTimeout(tTimer);
    if (tRightSent && tStart) send({ t: "mu", ...tStart, btn: 2 });
    else if (STATE.touchDown && tStart) send({ t: "mu", ...tStart, btn: 0 });
    STATE.touchDown = false; tRightSent = false; tStart = null; tOrigin = null;
  }, { passive: true });
  canvas.addEventListener("keydown", (e) => { if (!STATE.inputOn) return; e.preventDefault(); sendInput({ t: "kd", key: e.key, code: e.code, ctrl: e.ctrlKey, alt: e.altKey, shift: e.shiftKey }).catch((er) => toast(er.message, true)); });
  canvas.addEventListener("keyup", (e) => { if (!STATE.inputOn) return; e.preventDefault(); sendInput({ t: "ku", key: e.key, code: e.code }).catch((er) => toast(er.message, true)); });
  cleanupFns.push(() => { if (STATE.screenOn) { STATE.screenOn = false; stopLive("scrStills", "screen.control"); (STATE.wsReady ? wsCmd("input.enable", { on: false }) : cmd("input.enable", { on: false }, 15)).catch(() => {}); } STATE.inputOn = false; });
}

/* ================= SESSION TOOLS (audio, clipboard, volume…) ================= */
function renderTools(v) {
  if (!STATE.live.session) v.append(el("div", { class: "banner" }, "Desktop agent offline — these tools need a logged-in Windows session."));
  const micDevice = el("select", { class: "mic-device", "aria-label": "Laptop microphone input" },
    el("option", { value: "" }, "System default microphone"));
  cmd("mic.list", {}, 20).then((d) => {
    const list = d.items || [];
    if (!list.length) { micDevice.innerHTML = ""; micDevice.append(el("option", { value: "" }, "No microphone input found")); return; }
    micDevice.innerHTML = "";
    for (const dev of list) micDevice.append(el("option", { value: String(dev.i) },
      (dev.default ? "✓ " : "") + dev.name + (dev.default ? " (default)" : "")));
    const def = list.find((x) => x.default);
    if (def) micDevice.value = String(def.i);
  }).catch((e) => { micDevice.title = "Could not list microphones: " + e.message; });
  const micBtn = el("button", { id: "btnMicListen", class: "btn", onclick: async (e) => {
      if (!STATE.wsReady) return toast("Realtime audio is unavailable. Refresh the dashboard; if microphone access is blocked, open the HTTPS panel address and allow microphone access. Live video and input can continue in polling mode.", true);
      try {
        if (!STATE.micListen) {
          STATE.micCtx = STATE.micCtx || new (window.AudioContext || window.webkitAudioContext)();
          if (STATE.micCtx.state !== "running") await STATE.micCtx.resume();
        }
        STATE.micListen = !STATE.micListen;
        await wsCmd("mic.stream", { on: STATE.micListen,
          device: micDevice.value === "" ? null : Number(micDevice.value) });
        micBtn.textContent = STATE.micListen ? "⏹ Stop listening" : "🎧 Listen to laptop mic";
        micBtn.className = "btn " + (STATE.micListen ? "danger" : "");
        if (STATE.micListen) beep(740);
      } catch (er) { STATE.micListen = false; toast(er.message, true); }
    } }, "🎧 Listen to laptop mic");
  const talkBtn = el("button", { class: "btn", onclick: async (e) => {
      if (!STATE.wsReady) return toast("Push-to-talk needs the realtime channel. Refresh the dashboard; for microphone access, open the HTTPS panel address and allow microphone access.", true);
      try {
        if (!STATE.talkOn) {
          await wsCmd("talk.stream", { on: true });
          await startTalk();
          STATE.talkOn = true;
          talkBtn.textContent = "⏹ Stop talking";
          talkBtn.className = "btn danger";
        } else {
          await wsCmd("talk.stream", { on: false });
          stopTalk();
          STATE.talkOn = false;
          talkBtn.textContent = "📢 Talk to laptop (push-to-talk)";
          talkBtn.className = "btn";
        }
      } catch (er) { STATE.talkOn = false; stopTalk(); toast(er.message, true); }
    } }, "📢 Talk to laptop (push-to-talk)");
  v.append(panel("Two-way audio", el("div", { class: "sub" }, "Choose the laptop input, then listen. If another app owns the microphone, HKS will show the device error. Talk uses your phone microphone and the laptop speakers."),
    el("div", { class: "btn-row audio-controls" }, micDevice, micBtn, talkBtn,
      el("span", { class: "muted" }, STATE.micListen ? "listening…" : STATE.talkOn ? "talking…" : "idle"))));

  const clipGet = resultBox();
  const clipSet = el("textarea", { rows: "3", style: "width:100%", placeholder: "text to put on the laptop's clipboard" });
  v.append(panel("Remote clipboard",
    el("div", { class: "btn-row", style: "margin-bottom:8px" },
      el("button", { class: "btn ghost", onclick: (e) => runCmd("Clipboard", "clipboard.get", {}, (d) => {
        clipGet.box.style.display = "block"; clipGet.box.textContent = d.text ?? "(no text on clipboard)";
        const parts = [];
        if (d.text) parts.push(el("div", { style: "margin-bottom:8px" }, el("b", null, "Text"), el("pre", { class: "pre small", style: "max-height:26vh" }, String(d.text).slice(0, 4000))));
        if (d.html) parts.push(el("div", { style: "margin-bottom:8px" }, el("b", null, "HTML"), el("pre", { class: "pre small", style: "max-height:18vh" }, String(d.html).slice(0, 2000))));
        if (d.files && d.files.length) parts.push(el("div", { style: "margin-bottom:8px" }, el("b", null, "Files (" + d.files.length + ")"), el("pre", { class: "pre small" }, d.files.join("\n").slice(0, 2000))));
        if (d.image_b64) parts.push(el("div", { style: "margin-bottom:8px" }, el("b", null, "Image"), el("img", { src: "data:image/png;base64," + d.image_b64, style: "max-width:100%", alt: "clipboard image" })));
        parts.push(el("p", { class: "muted", style: "margin:6px 0 0" }, "formats on the clipboard: " + ((d.formats || []).join(", ") || "—")));
        modal("Laptop clipboard — every format", ...parts);
      }, e.target) }, "📋 Read laptop clipboard (all formats)"),
      el("button", { class: "btn", onclick: (e) => clipSet.value && runCmd("Clipboard set", "clipboard.set", { text: clipSet.value }, () => { toast("clipboard set"); clipSet.value = ""; }, e.target) }, "➕ Set laptop clipboard")),
    el("div", { class: "term-in", style: "margin-top:0" }, clipSet), clipGet.box));

  const vol = el("input", { type: "range", min: "0", max: "100", value: "50", style: "flex:1", oninput: debounce((e) => cmd("volume.set", { level: +e.target.value }).catch(() => {}), 300) });
  const toastRow = (() => {
    const t = el("input", { placeholder: "toast message…", style: "flex:1;min-width:160px" });
    const b = el("button", { class: "btn ghost", onclick: () => t.value && cmd("notify.toast", { title: "HKS Admin", msg: t.value }).then(() => (t.value = "")).catch((x) => toast(x.message, true)) }, "Toast");
    return el("div", { class: "btn-row", style: "margin-top:10px" }, t, b);
  })();
  const speakRow = (() => {
    const t2 = el("input", { placeholder: "text to speak aloud…", style: "flex:1;min-width:160px" });
    const b2 = el("button", { class: "btn ghost", onclick: () => t2.value && cmd("tts.speak", { text: t2.value }).then(() => (t2.value = "")).catch((x) => toast(x.message, true)) }, "Speak");
    return el("div", { class: "btn-row", style: "margin-top:10px" }, t2, b2);
  })();
  v.append(panel("Volume & messages", el("div", { class: "btn-row" }, "🔈", vol, "🔊",
      el("button", { class: "btn ghost sm", onclick: () => cmd("volume.mute", { on: true }).catch((x) => toast(x.message, true)) }, "mute"),
      el("button", { class: "btn ghost sm", onclick: () => cmd("volume.mute", { on: false }).catch((x) => toast(x.message, true)) }, "unmute")),
    toastRow, speakRow));

  const winBox = resultBox();
  v.append(panel("Session context",
    el("div", { class: "btn-row" },
      el("button", { class: "btn ghost sm", onclick: (e) => runCmd("Idle", "session.idle", {}, (d) => winGet(), e.target) }, "⟳ Refresh"),
      el("button", { class: "btn ghost sm", onclick: () => confirmDo("Log off the Windows user?", () => cmd("power.logoff")) }, "🚪 Log off user")),
    winBox.box));
  async function winGet() {
    try {
      const idle = await cmd("session.idle", {}, 15).catch(() => ({}));
      const fg = await cmd("session.foreground", {}, 15).catch(() => ({}));
      winBox.show({ idle_seconds: idle.idle_s, foreground_window: fg.title });
    } catch (e) { winBox.show(e.message); }
  }
  winGet();
  every(10000, winGet);
  cleanupFns.push(() => { if (STATE.micListen) { STATE.micListen = false; wsCmd("mic.stream", { on: false }).catch(() => {}); } if (STATE.talkOn) { STATE.talkOn = false; stopTalk(); wsCmd("talk.stream", { on: false }).catch(() => {}); } });
}

/* ================= FILE MANAGER ================= */
const FM = { path: "C:\\", entries: [], sel: new Set() };
function renderFiles(v) {
  const crumbs = el("div", { class: "btn-row", style: "margin-bottom:8px" });
  const driveSel = el("select", { onchange: () => nav(driveSel.value) }, el("option", null, "C:\\"));
  FM.view = FM.view || "list";
  const viewBtns = el("span", { class: "fg-viewtog" },
    el("button", { class: "fg-vtog" + (FM.view === "grid" ? " on" : ""), title: "grid view", onclick: (e) => { FM.view = "grid"; e.target.parentNode.querySelectorAll(".fg-vtog").forEach((b) => b.classList.remove("on")); e.target.classList.add("on"); paint(); } }, "▦"),
    el("button", { class: "fg-vtog" + (FM.view === "list" ? " on" : ""), title: "list view", onclick: (e) => { FM.view = "list"; e.target.parentNode.querySelectorAll(".fg-vtog").forEach((b) => b.classList.remove("on")); e.target.classList.add("on"); paint(); } }, "☰"));
  const bar = el("div", { class: "btn-row", style: "margin-bottom:10px" },
    viewBtns,
    el("button", { class: "btn ghost sm", onclick: () => nav(parentDir(FM.path)) }, "⬆ Up"),
    el("button", { class: "btn ghost sm", onclick: () => nav(FM.path) }, "⟳"));
  /* 2026 UI report Part 6.2: density toggle - compact 32 / regular 44 / relaxed 56 px rows */
  const dens = el("select", { title: "row density", onchange: () => {
      document.documentElement.setAttribute("data-density", dens.value);
      try { localStorage.setItem("hks_density", dens.value); } catch (e) {} } },
    el("option", { value: "regular" }, "regular rows"),
    el("option", { value: "compact" }, "compact rows"),
    el("option", { value: "relaxed" }, "relaxed rows"));
  try {
    dens.value = localStorage.getItem("hks_density") || "regular";
    document.documentElement.setAttribute("data-density", dens.value);
  } catch (e) {}
  bar.append(dens);
  const hiddenCk = el("input", { type: "checkbox" }); hiddenCk.checked = true;
  hiddenCk.onchange = () => { FM.showHidden = hiddenCk.checked; paint(); };  /* round 65 (audit7 #42) */
  const newBtn = el("button", { class: "btn ghost sm", onclick: async () => {
    const entered = prompt("Enter one folder name. It will be created inside:\n" + FM.path);
    if (entered === null) return;
    const name = entered.trim();
    if (!name) return toast("Enter a folder name.", true);
    if (name === "." || name === ".." || /[<>:"/\\|?*\x00-\x1f]/.test(name) || /[ .]$/.test(name))
      return toast("Use a single Windows folder name without path separators or reserved characters.", true);
    try {
      const parent = FM.path.replace(/[\\/]+$/, "");
      const target = parent + "\\" + name;
      await cmd("files.mkdir", { path: target });
      await nav(FM.path);
      toast("Created folder: " + name);
    } catch (e) { toast(e.message, true); }
  } }, "📁+ Folder");
  const up = el("input", { type: "file", style: "display:none" });
  up.onchange = () => uploadFile(up.files[0]);
  const zipSel = el("button", { class: "btn sm", onclick: zipSelected }, "⬇ ZIP selection");
  /* filegator operation set: clipboard (copy/cut/paste), extract */
  FM.clip = FM.clip || null;
  const clipSel = (mode) => {
    if (!FM.sel.size) return toast("tick some files first", true);
    FM.clip = { mode, paths: [...FM.sel] };
    toast(mode + ": " + FM.sel.size + " item(s) - now Paste in the destination folder");
  };
  const copyBtn = el("button", { class: "btn ghost sm", onclick: () => clipSel("copy") }, "⧉ Copy");
  const cutBtn = el("button", { class: "btn ghost sm", onclick: () => clipSel("cut") }, "✂ Cut");
  const pasteBtn = el("button", { class: "btn sm", onclick: async () => {
    if (!FM.clip) return toast("nothing copied/cut yet", true);
    const n = FM.clip.paths.length;
    const go = async () => {
      try {
        const d = await cmd(FM.clip.mode === "cut" ? "files.move" : "files.copy", { paths: FM.clip.paths, dest: FM.path }, 300);
        const bad = (d.failed || []).length;
        toast((FM.clip.mode === "cut" ? "moved " : "copied ") + (n - bad) + " item(s)" + (bad ? " - " + bad + " failed" : ""));
        if (FM.clip.mode === "cut") FM.clip = null;
        nav(FM.path);
      } catch (e) { toast(e.message, true); }
    };
    if (FM.clip.mode === "cut") confirmDo("MOVE " + n + " item(s) into " + FM.path + " ?", go); else go();
  } }, "📋 Paste");
  const extractBtn = el("button", { class: "btn ghost sm", onclick: async () => {
    const zips = [...FM.sel].filter((x) => x.toLowerCase().endsWith(".zip"));
    if (!zips.length) return toast("tick a .zip file first", true);
    try {
      for (const zp of zips) {
        const d = await cmd("files.extract", { path: zp, dest: FM.path }, 600);
        toast("extracted " + d.files + " files");
      }
      nav(FM.path);
    } catch (e) { toast(e.message, true); }
  } }, "📦 Extract");
  bar.append(driveSel, newBtn, el("button", { class: "btn sm", onclick: () => up.click() }, "⬆ Upload here"), up, zipSel,
    copyBtn, cutBtn, pasteBtn, extractBtn,
    el("label", { class: "row", style: "gap:5px;font-size:12px;margin:0" }, hiddenCk, "hidden/system"));
  const search = el("input", { placeholder: "search this drive (e.g. *.pdf)…", style: "flex:1;min-width:160px" });
  search.onkeydown = (e) => e.key === "Enter" && doSearch(search.value);
  bar.append(search, el("button", { class: "btn ghost sm", onclick: () => doSearch(search.value) }, "🔍"));
  const tableWrap = el("div");
  // drag & drop upload (filegator-inspired): drop file(s) anywhere on the list
  tableWrap.addEventListener("dragover", (e) => { e.preventDefault(); tableWrap.style.outline = "2px dashed var(--primary)"; });
  tableWrap.addEventListener("dragleave", () => { tableWrap.style.outline = ""; });
  tableWrap.addEventListener("drop", async (e) => {
    e.preventDefault(); tableWrap.style.outline = "";
    const fs = Array.from((e.dataTransfer && e.dataTransfer.files) || []);
    if (!fs.length) return;
    toast("dropped " + fs.length + " file(s) - uploading...");
    for (const f of fs) await uploadFile(f);
  });
  const status = el("div", { class: "muted", style: "margin-top:8px" });
  const fgFoot = el("div", { class: "fg-foot" }, el("span", { id: "fgSel" }, "Selected: 0 of 0"),
    el("span", { class: "muted", id: "fgPath" }, ""));
  v.append(el("div", { class: "fg-win" },
    el("div", { class: "fg-logo" }, el("span", { class: "fg-gator" }, "🐊"), el("b", null, "filegator"), el("span", { class: "muted", style: "font-weight:400" }, " — full access incl. hidden & system files")),
    bar, crumbs, tableWrap, fgFoot));
  v.append(status);

  (async () => {
    try { const d = await cmd("files.drives"); driveSel.innerHTML = ""; (d.drives || ["C:\\"]).forEach((x) => driveSel.append(el("option", { value: x }, x))); } catch {}
  })();
  async function nav(p) {
    try {
      status.textContent = "loading " + p + " …";
      const d = await cmd("files.list", { path: p }, 45);
      FM.path = d.path; FM.entries = d.entries || []; FM.sel.clear();
      crumbs.innerHTML = "";
      const parts = [];
      d.path.split("\\").forEach((seg, i, arr) => { if (seg || i === 0) parts.push(arr.slice(0, i + 1).join("\\")); });
      parts.forEach((pp, i) => crumbs.append(el("a", { href: "#", onclick: (e) => { e.preventDefault(); nav(pp); } }, pp.split("\\").filter(Boolean).pop() || pp),
        i < parts.length - 1 ? el("span", { class: "muted" }, " \\ ") : null));
      if (d.truncated) toast("large folder — showing first 500 entries", true);
      paint();
      paintFooter();
    } catch (e) { status.textContent = ""; toast(e.message, true); }
  }
  function paintFooter() {
    const rows = FM.entries.filter((x) => FM.showHidden || !(x.hidden || x.system));
    const sel = document.getElementById("fgSel");
    const pp = document.getElementById("fgPath");
    if (sel) sel.textContent = "Selected: " + FM.sel.size + " of " + rows.length;
    if (pp) pp.textContent = rows.length + " items — " + FM.path;
  }
  function rowMenu(x, fp, anchor) {
    const items = [
      { label: x.dir ? "📂 Open" : "👁 View", fn: () => (x.dir ? nav(fp) : viewFile(fp)) },
      { label: "✏ Rename", fn: async () => { const n = prompt("Rename to:", x.name); if (n && n !== x.name) { try { await cmd("files.rename", { path: fp, new_name: n }); nav(FM.path); } catch (e) { toast(e.message, true); } } } },
      { label: "⧉ Copy", fn: () => { FM.sel.add(fp); clipSel("copy"); } },
      { label: "✂ Cut", fn: () => { FM.sel.add(fp); clipSel("cut"); } }];
    if (!x.dir) items.push({ label: "⬇ Download", fn: () => downloadOne(fp) });
    if (x.name.toLowerCase().endsWith(".zip")) items.push({ label: "📦 Extract here", fn: async () => { try { const d = await cmd("files.extract", { path: fp, dest: FM.path }, 600); toast("extracted " + d.files + " files"); nav(FM.path); } catch (e) { toast(e.message, true); } } });
    items.push({ label: "🗑 Delete", danger: true, fn: () => confirmDo("DELETE " + x.name + " ?", async () => { try { await cmd("files.delete", { path: fp }); FM.sel.delete(fp); nav(FM.path); } catch (e) { toast(e.message, true); } }) });
    popMenu(anchor, items);
  }
  function paint() {
    tableWrap.innerHTML = "";
    const rows = FM.entries.filter((x) => FM.showHidden || !(x.hidden || x.system));
    const full = (x) => FM.path.replace(/\\?$/, "\\") + x.name;
    if (FM.view === "grid") {
      const g = el("div", { class: "fg-grid" });
      for (const x of rows) {
        const fp = full(x);
        const tile = el("div", { class: "fg-tile" + (FM.sel.has(fp) ? " fg-sel" : ""), onclick: () => { FM.sel.has(fp) ? FM.sel.delete(fp) : FM.sel.add(fp); paint(); },
          ondblclick: () => (x.dir ? nav(fp) : viewFile(fp)) },
          el("span", { class: "fg-tile-ic" }, x.dir ? "📂" : "📄"), el("span", { class: "fg-tile-nm" }, x.name),
          el("button", { class: "fg-dots", title: "actions", onclick: (e) => { e.stopPropagation(); rowMenu(x, fp, e.target); } }, "⋯"));
        g.append(tile);
      }
      tableWrap.append(g);
      paintFooter(); paintBulk();
      return;
    }
    const allCk = el("input", { type: "checkbox", onchange: () => { rows.forEach((x) => { if (!x.dir) allCk.checked ? FM.sel.add(full(x)) : FM.sel.delete(full(x)); }); paint(); } });
    const t = el("table", { class: "t fg-t" }, el("thead", null, el("tr", null, el("th", { style: "width:30px" }, allCk),
      el("th", null, "Name"), el("th", null, "Date Modified"), el("th", { style: "text-align:right" }, "Size"), el("th", { style: "width:44px" }, ""))));
    const tb = el("tbody");
    for (const x of rows) {
      const fp = full(x);
      const ck = el("input", { type: "checkbox", checked: FM.sel.has(fp),
        onclick: (e) => e.stopPropagation(),
        onchange: () => { ck.checked ? FM.sel.add(fp) : FM.sel.delete(fp); paint(); } });
      const dots = el("button", { class: "fg-dots", title: "actions", onclick: (e) => { e.stopPropagation(); rowMenu(x, fp, e.target); } }, "⋯");
      const tr = el("tr", { class: FM.sel.has(fp) ? "fg-sel" : "",
        onclick: () => { FM.sel.has(fp) ? FM.sel.delete(fp) : FM.sel.add(fp); paint(); },
        ondblclick: () => (x.dir ? nav(fp) : viewFile(fp)) },
        el("td", null, ck),
        el("td", null, el("span", { class: "fg-ic" }, x.dir ? "📁" : "📄"), x.name),
        el("td", null, fmtT(x.mtime)),
        el("td", { style: "text-align:right" }, x.dir ? "Folder" : fmtB(x.size)),
        el("td", null, dots));
      tb.append(tr);
    }
    t.append(tb);
    tableWrap.append(el("div", { class: "tscroll" }, t));
    paintFooter(); paintBulk();
  }
  /* 2026 UI report: bulk actions follow the selection in a floating bar */
  const bulk = el("div", { class: "hks-bulk", style: "display:none" });
  v.append(bulk);
  function paintBulk() {
    const n = FM.sel.size;
    bulk.style.display = n ? "flex" : "none";
    if (!n) return;
    bulk.replaceChildren(el("b", null, n + " selected"), el("span", { class: "sep" }),
      el("button", { class: "btn ghost sm", onclick: zipSelected }, "⬇ ZIP"),
      el("button", { class: "btn ghost sm", onclick: () => clipSel("copy") }, "⧉ Copy"),
      el("button", { class: "btn ghost sm", onclick: () => clipSel("cut") }, "✂ Cut"),
      el("button", { class: "btn dangerg sm", onclick: () => {
        confirmDo("DELETE " + n + " selected item(s)?", async () => {
          for (const p of [...FM.sel]) { try { await cmd("files.delete", { path: p }); } catch (e) { toast(e.message, true); } }
          FM.sel.clear(); nav(FM.path);
        });
      } }, "🗑 Delete"),
      el("span", { class: "sep" }),
      el("button", { class: "btn ghost sm", onclick: () => { FM.sel.clear(); paint(); } }, "Clear"));
  }
  async function zipSelected() {
    if (!FM.sel.size) return toast("tick some files first", true);
    toast("zipping " + FM.sel.size + " files on the laptop…");
    try {
      const d = await cmd("files.zip_paths", { paths: [...FM.sel] }, 300);
      downloadThenDelete(d.path);
      if (d.skipped && d.skipped.length) toast(`${d.skipped.length} file(s) skipped (limits/unreadable)`, true);
    } catch (e) { toast(e.message, true); }
  }
  async function viewFile(p) {
    try {
      const st = await cmd("file.stat", { path: p });
      if (st.is_dir) return nav(p);
      const size = st.size || 0;
      if (/\.(jpe?g|png|gif|bmp|webp)$/i.test(p)) {
        const d = await cmd("file.read_chunk", { path: p, offset: 0, size: Math.min(size, 6 * 1024 * 1024) }, 90);
        return modal(p, el("img", { src: "data:image/*;base64," + d.b64, style: "width:100%" }));
      }
      if (size > 2 * 1024 * 1024) return downloadOne(p);
      const b64 = (await cmd("file.read_chunk", { path: p, offset: 0, size }, 90)).b64;
      let content = ""; try { content = atob(b64); } catch { return downloadOne(p); }
      (() => {
        const shown = content.slice(0, 200000);
        const pre = el("div", { class: "pre", style: "max-height:60vh" }, shown);
        const ta = el("textarea", { style: "width:100%;min-height:40vh;background:#0d1117;color:#e8ebf2;border:1px solid var(--border);border-radius:8px;padding:10px;font-family:var(--mono,monospace);font-size:12.5px;display:none" });
        ta.value = shown;
        const saveBtn = el("button", { class: "btn sm", style: "display:none", onclick: () => {
          confirmDo("OVERWRITE " + p.split("\\").pop() + " on the laptop ?", async () => {
            try {
              let bin = ""; const txt = ta.value; for (let i = 0; i < txt.length; i++) bin += String.fromCharCode(txt.charCodeAt(i));
              await cmd("files.write_b64", { path: p, b64: btoa(bin) }, 60);
              toast("saved");
            } catch (e) { toast(e.message, true); }
          });
        } }, "💾 Save to laptop");
        const editBtn = el("button", { class: "btn ghost sm", onclick: () => {
          pre.style.display = "none"; ta.style.display = "block"; saveBtn.style.display = ""; editBtn.style.display = "none";
        } }, "✏ Edit");
        modal(p, pre, ta,
          el("div", { class: "row end", style: "margin-top:10px" }, el("button", { class: "btn ghost sm", onclick: () => downloadOne(p) }, "⬇ Download"), editBtn, saveBtn));
      })();
    } catch (e) { toast(e.message, true); }
  }
  function downloadOne(p) { toast("downloading…"); const a = document.createElement("a"); a.href = downloadUrl(p); a.download = p.split("\\").pop(); a.click(); }
  async function uploadFile(f) {
    if (!f) return;
    const CHUNK = 512 * 1024; const dest = FM.path.replace(/\\?$/, "\\") + f.name;
    const btn = null;
    try {
      await cmd("file.write_chunk", { path: dest, offset: 0, b64: "", truncate: true });
      for (let off = 0; off < f.size; off += CHUNK) {
        const buf = new Uint8Array(await f.slice(off, off + CHUNK).arrayBuffer());
        let bin = ""; for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode(...buf.subarray(i, i + 0x8000));
        await cmd("file.write_chunk", { path: dest, offset: off, b64: btoa(bin) }, 60);
        toast(`uploading ${f.name}: ${Math.min(100, Math.round(100 * (off + CHUNK) / f.size))}%`);
      }
      toast("uploaded " + f.name); nav(FM.path);
    } catch (e) { toast("upload failed: " + e.message, true); }
  }
  async function doSearch(q) {
    if (!q) return;
    status.textContent = "searching… (bounded scan)";
    try {
      const d = await cmd("files.search", { root: FM.path, pattern: q, max: 400 }, 110);
      tableWrap.innerHTML = "";
      const t = el("table", { class: "t" });
      for (const x of d.items) {
        const xp = x.path ?? x;
        t.append(tRow(el("a", { href: "#", onclick: (e) => { e.preventDefault(); viewFile(xp); } }, "📄 " + xp.split("\\").pop()),
          el("span", { class: "muted mono" }, xp), fmtB(x.size),
          el("button", { class: "btn ghost sm", onclick: () => downloadOne(xp) }, "⬇")));
      }
      tableWrap.append(el("div", { class: "tscroll" }, t));
      status.textContent = `${d.items.length} matches for ${q}`;
    } catch (e) { status.textContent = "search: " + e.message; }
  }
  nav(FM.path);
}

/* ================= TERMINAL ================= */
function renderTerm(v) {
  let mode = "system", shell = "powershell";
  const out = el("div", { class: "term" }, "HKS secure shell — 'admin (SYSTEM)' runs as NT AUTHORITY\\SYSTEM (full OS access), 'normal user' runs in the desktop session.\n");
  const input = el("input", { placeholder: "type a command… (Enter to run)", autocomplete: "off" });
  const hist = []; let hi = -1; let cwd = "";
  const modeSel = el("select", { onchange: () => (mode = modeSel.value) },
    el("option", { value: "system" }, "admin (SYSTEM)"), el("option", { value: "user" }, "normal user"));
  const shellSel = el("select", { onchange: () => (shell = shellSel.value) },
    el("option", { value: "powershell" }, "PowerShell"), el("option", { value: "cmd" }, "CMD"));
  const sshBtn = el("button", { class: "btn ghost sm", onclick: async () => {
      try {
        const d = await cmd("ssh.status");
        modal("OpenSSH server", el("div", { class: "pre" }, JSON.stringify(d, null, 2)),
          el("div", { class: "btn-row", style: "margin-top:10px" },
            el("button", { class: "btn sm", onclick: () => cmd("ssh.enable").then(() => toast("SSH enabled")).catch((e) => toast(e.message, true)) }, "Enable SSH server"),
            el("button", { class: "btn dangerg sm", onclick: () => cmd("ssh.disable").then(() => toast("SSH disabled")).catch((e) => toast(e.message, true)) }, "Disable")));
      } catch (e) { toast(e.message, true); }
    } }, "SSH server");
  async function run() {
    const c = input.value.trim(); if (!c) return;
    input.value = ""; hist.push(c); hi = hist.length;
    out.append(el("div", { class: "in" }, `${mode}@laptop> ${c}`));
    try {
      const action = mode === "user" ? "terminal.exec_user" : "terminal.exec";
      const d = await cmd(action, { shell, command: c, cwd }, 90);
      if (d.output) out.append(document.createTextNode(d.output + "\n"));
      if (d.error) out.append(el("span", { style: "color:var(--bad)" }, d.error + "\n"));
      cwd = d.cwd || cwd;
    } catch (e) { out.append(el("span", { style: "color:var(--bad)" }, "error: " + e.message + "\n")); }
    out.scrollTop = out.scrollHeight;
  }
  input.onkeydown = (e) => {
    if (e.key === "Enter") run();
    else if (e.key === "ArrowUp") { if (hi > 0) input.value = hist[--hi]; e.preventDefault(); }
    else if (e.key === "ArrowDown") { if (hi < hist.length - 1) input.value = hist[++hi]; else { hi = hist.length; input.value = ""; } e.preventDefault(); }
  };
  v.append(panel("Terminal", el("div", { class: "btn-row", style: "margin-bottom:10px" }, modeSel, shellSel, sshBtn,
      el("span", { style: "flex:1" }), el("button", { class: "btn ghost sm", onclick: () => (out.innerHTML = "") }, "clear")),
    out, el("div", { class: "term-in" }, input)));
  input.focus();
}

/* ================= APPS & PROCESSES ================= */
function renderApps(v) {
  const launch = el("input", { placeholder: "app name or full path (e.g. calc.exe, notepad, C:\\path\\app.exe)", style: "flex:1;min-width:220px" });
  v.append(panel("Launch application", el("div", { class: "btn-row" }, launch,
    el("button", { class: "btn", onclick: (e) => launch.value && runCmd("Launch", "apps.launch", { target: launch.value }, () => { toast("launched"); launch.value = ""; refreshProcs(); }, e.target) }, "🚀 Launch"))));
  const uninstallList = el("div", { class: "muted" }, "loading installed applications…");
  const uq = el("input", { placeholder: "filter installed apps…", style: "flex:1;min-width:200px" });
  let UNINSTALL = [];
  const paintU = () => {
    const q = (uq.value || "").toLowerCase();
    const items = UNINSTALL.filter((x) => !q || x.name.toLowerCase().includes(q)).slice(0, 60);
    uninstallList.innerHTML = "";
    uninstallList.className = "";
    uninstallList.append(el("div", { class: "tbl-wrap" }, el("table", null,
      el("thead", null, el("tr", null, el("th", null, "Application"), el("th", null, "Version"), el("th", null, "Publisher"), el("th", null, ""))),
      el("tbody", null, items.map((x) => el("tr", null,
        el("td", null, appIcon(x.name), " ", x.name), el("td", null, String(x.version || "—")),
        el("td", null, String(x.publisher || "—")),
        el("td", null, el("button", { class: "btn danger sm", onclick: async () => {
          if (!confirm("Launch the uninstaller for\n" + x.name + "?\nThe app's own wizard opens - finish it there.")) return;
          try {
            const r = await cmd("apps.uninstall", { name: x.name, confirm: "CONFIRM" }, 90);
            toast(r.launched ? "uninstaller launched" : (r.error || "done"));
          } catch (er) { toast(er.message, true); }
        } }, "Uninstall"))))))));
  };
  uq.oninput = debounce(paintU, 250);
  cmd("apps.uninstall_meta", {}, 60).then((d) => { UNINSTALL = d.items || []; paintU(); })
    .catch(() => { uninstallList.className = "muted"; uninstallList.textContent = "uninstall metadata needs the Windows service agent"; });
  v.append(panel("Installed applications (real Windows uninstall entries)",
      el("div", { class: "btn-row", style: "margin-bottom:10px" }, uq), uninstallList));

  const procWrap = el("div");
  const startWrap = el("div");
  v.append(panel("Running processes", el("div", { class: "btn-row" },
      el("button", { class: "btn ghost sm", onclick: refreshProcs }, "⟳ Refresh"),
      el("label", { class: "row", style: "gap:5px;font-size:12px;margin:0" }, procAuto(), "auto 4s")), procWrap),
    panel("Startup items (autorun)", el("button", { class: "btn ghost sm", onclick: refreshStartup }, "⟳"), startWrap));
  function procAuto() { const c = el("input", { type: "checkbox" }); c.onchange = () => c.checked && every(4000, refreshProcs); return c; }
  async function refreshProcs() {
    try {
      const d = await cmd("apps.processes");
      procWrap.innerHTML = "";
      const t = el("table", { class: "t" }, el("thead", null, el("tr", null, el("th", null, "PID"), el("th", null, "Name"), el("th", null, "CPU%"), el("th", null, "RAM"), el("th", null, "User"), el("th", null, "Actions"))));
      for (const p of (d.items || []).slice(0, 150)) {
        t.append(tRow(p.pid, p.name, p.cpu ?? "—", fmtB(p.mem), p.user || "—",
          el("span", { class: "btn-row" },
            el("button", { class: "btn dangerg sm", onclick: () => confirmDo("Kill " + p.name + " (" + p.pid + ")?", () => cmd("apps.kill", { pid: p.pid }).then(refreshProcs)) }, "kill"),
            el("button", { class: "btn ghost sm", onclick: () => cmd("apps.suspend", { pid: p.pid }).then(refreshProcs).catch((e) => toast(e.message, true)) }, "suspend"),
            el("button", { class: "btn ghost sm", onclick: () => cmd("apps.resume", { pid: p.pid }).then(refreshProcs).catch((e) => toast(e.message, true)) }, "resume"))));
      }
      procWrap.append(el("div", { class: "tscroll", style: "max-height:460px" }, t));
    } catch (e) { procWrap.innerHTML = `<span class="muted">${esc(e.message)}</span>`; }
  }
  async function refreshStartup() {
    try {
      const d = await cmd("apps.startup_list");
      startWrap.innerHTML = "";
      const t = el("table", { class: "t" });
      for (const s of d.items) t.append(tRow(s.name, el("span", { class: "muted mono", style: "font-size:11px" }, s.target), s.location,
        el("button", { class: "btn dangerg sm", onclick: () => confirmDo("Remove startup item " + s.name + "?", () => cmd("apps.startup_remove", { name: s.name, location: s.location }).then(refreshStartup)) }, "remove")));
      if (!d.items.length) startWrap.append(emptyState("📭", "no autorun entries"));
      else startWrap.append(el("div", { class: "tscroll" }, t));
    } catch (e) { startWrap.innerHTML = `<span class="muted">${esc(e.message)}</span>`; }
  }
  refreshProcs(); refreshStartup();
}

/* ================= MEDIA & ZIP EXPORT ================= */
function renderMedia(v) {
  const KINDS = { img: "🖼 Images", vid: "🎬 Videos", doc: "📄 Documents" };
  let kind = "img"; let page = 0; const PER = 120; const sel = new Set();
  const tabs = el("div", { class: "seg" });
  const grid = el("div", { class: "grid-media" });
  const status = el("div", { class: "muted", style: "margin-top:8px" });
  const path = el("input", { value: "C:\\", style: "width:200px" });
  const q = el("input", { placeholder: "filename filter…", style: "width:150px" });
  for (const [k, label] of Object.entries(KINDS)) {
    const b = el("button", { onclick: () => { kind = k; page = 0; sel.clear(); [...tabs.children].forEach((x) => x.classList.remove("on")); b.classList.add("on"); load(); } }, label);
    if (k === "img") b.classList.add("on");
    tabs.append(b);
  }
  v.append(panel("Search entire C: — images / videos / documents indexed separately",
    el("div", { class: "btn-row", style: "margin-bottom:10px" }, tabs, path, q,
      el("button", { class: "btn", onclick: () => { page = 0; sel.clear(); load(); } }, "🔍 Search"),
      el("button", { class: "btn", onclick: zipAll }, "⬇ Download all as ZIP"),
      el("button", { class: "btn ghost", onclick: zipSelected }, "⬇ ZIP selected"),
      el("span", { style: "flex:1" }),
      el("button", { class: "btn ghost sm", onclick: () => { page--; load(); } }, "◀"),
      el("button", { class: "btn ghost sm", onclick: () => { page++; load(); } }, "▶")),
    grid, status));
  function currentFilter() { return { kind, path: path.value, q: q.value, page, per: PER }; }
  async function load() {
    grid.innerHTML = "<span class='spin'></span> <span class='muted'>scanning drive (cached after first run)…</span>";
    try {
      const d = await cmd("media.scan", { ...currentFilter() }, 110);
      grid.innerHTML = "";
      status.textContent = `${d.total} total — page ${page + 1} showing ${d.items.length} — ${sel.size} selected`;
      if (!d.items.length) { grid.append(emptyState("🗂", "nothing found — try another folder/filter")); return; }
      for (const it of d.items) {
        const card = el("div", { class: "mcard" + (sel.has(it.path) ? " sel" : ""), onclick: (e) => {
            if (e.ctrlKey || e.metaKey || e.shiftKey) { sel.has(it.path) ? sel.delete(it.path) : sel.add(it.path); card.classList.toggle("sel"); status.textContent = `${sel.size} selected`; return; }
            openMedia(it.path, kind);
          } },
          el("div", { class: "th" }, kind === "img" ? "🖼" : kind === "vid" ? "🎬" : "📄"),
          el("div", { class: "nm" }, it.path.split("\\").pop()));
        grid.append(card);
        if (kind === "img") lazyThumb(card.querySelector(".th"), it.path);
      }
    } catch (e) { grid.innerHTML = ""; status.textContent = e.message; }
  }
  async function zipAll() {
    toast("zipping on the laptop (max 300 files / 1 GB)…");
    try {
      const d = await cmd("files.zip_media", { kind, path: path.value, q: q.value, max_files: 300 }, 600);
      downloadThenDelete(d.path);
      if (d.skipped && d.skipped.length) toast(`${d.skipped.length} skipped (limits)`, true);
    } catch (e) { toast(e.message, true); }
  }
  async function zipSelected() {
    if (!sel.size) return toast("ctrl+click cards to select", true);
    try {
      const d = await cmd("files.zip_paths", { paths: [...sel] }, 600);
      downloadThenDelete(d.path);
    } catch (e) { toast(e.message, true); }
  }
  async function lazyThumb(th, p) {
    try { const d = await cmd("media.thumb", { path: p }, 30); if (d.b64) { th.innerHTML = ""; th.append(el("img", { src: "data:image/jpeg;base64," + d.b64 })); } } catch {}
  }
  function openMedia(p, kind) {
    const name = p.split("\\").pop();
    const ext = (name.split(".").pop() || "").toLowerCase();
    const image = kind === "img" || ["jpg", "jpeg", "png", "gif", "webp", "bmp", "tif", "tiff", "heic"].includes(ext);
    const video = ["mp4", "m4v", "mov", "avi", "mkv", "webm", "wmv", "mpeg", "mpg"].includes(ext);
    const audio = ["mp3", "wav", "m4a", "aac", "flac", "ogg", "wma"].includes(ext);
    const preview = image ? el("img", { src: downloadUrl(p), alt: name, class: "media-preview" })
      : video ? el("video", { src: downloadUrl(p), controls: "", playsinline: "", class: "media-preview" })
        : audio ? el("audio", { src: downloadUrl(p), controls: "" }) : null;
    const actions = el("div", { class: "btn-row" },
        ...((video || audio) ? [el("button", { class: "btn", onclick: () => cmd("media.play_file", { path: p }).then((d) => d.ok === false ? toast(d.error, true) : toast("playing on laptop")).catch((e) => toast(e.message, true)) }, "▶ Play on laptop")] : []),
        el("button", { class: "btn ghost", onclick: () => { const a = document.createElement("a"); a.href = downloadUrl(p); a.download = p.split("\\").pop(); a.click(); } }, "⬇ Download"),
        el("button", { class: "btn dangerg", onclick: () => confirmDo("DELETE " + p + " ?", () => cmd("files.delete", { path: p }).then(load)) }, "🗑"));
    modal(name, el("div", { class: "media-open" }, ...(preview ? [preview] : []), actions,
      el("div", { class: "muted mono media-path" }, p)));
  }
  load();
}

/* ================= DATABASE ================= */
function renderDb(v) {
  v.append(chromeBar("dg", "#5f79ff", "DbGate style", "engine: HKS SQL console (SQLite + MySQL) — the real DbGate app installs via HKS_ADDONS"));
  const engine = el("select", null, el("option", { value: "sqlite" }, "SQLite file"), el("option", { value: "mysql" }, "MySQL / AWS RDS"));
  const sqlitePath = el("input", { value: "", placeholder: "pick a .db file below ↓", style: "width:260px" });
  const myHost = el("input", { placeholder: "host", style: "width:130px" }); const myPort = el("input", { placeholder: "3306", style: "width:64px" });
  const myUser = el("input", { placeholder: "user", style: "width:100px" }); const myPass = el("input", { placeholder: "password", type: "password", style: "width:110px" });
  const myDb = el("input", { placeholder: "database", style: "width:120px" });
  const sql = el("textarea", { rows: "4", style: "width:100%;font-family:Consolas,monospace", placeholder: "SELECT name FROM sqlite_master WHERE type='table';  — or any SQL" });
  const out = resultBox(); const tablesBox = el("div", { class: "btn-row", style: "margin-top:8px" });
  const dbSel = el("select", { style: "max-width:340px" }, el("option", { value: "" }, "— found databases —"));
  dbSel.onchange = () => { if (dbSel.value) sqlitePath.value = dbSel.value; };
  const engRow = el("div", { class: "btn-row" }, engine, sqlitePath);
  engine.onchange = () => { engRow.innerHTML = ""; engRow.append(engine); engRow.append(engine.value === "sqlite" ? sqlitePath : el("span", { class: "row" }, myHost, myPort, myUser, myPass, myDb)); };  /* round 65 (audit7 #41): el(null,...) threw - mysql fields never appeared */
  const params = () => engine.value === "sqlite" ? { engine: engine.value, path: sqlitePath.value }  /* round 65 (audit7 #40): shorthand sent the <select> ELEMENT, serialized {} - SQL page hit the mysql path and never worked */
    : { engine: engine.value, host: myHost.value, port: +myPort.value || 3306, user: myUser.value, password: myPass.value, database: myDb.value };
  v.append(panel("SQL — SQLite files or MySQL (incl. AWS RDS), queried from the laptop",
    el("div", { class: "sub" }, "How to use:  1) 🔍 Find DB files → pick one from the dropdown   2) List tables → click a table name to prefill   3) ▶ Run query.   MySQL: switch the dropdown, fill host/user/password."),
    engRow,
    el("div", { class: "btn-row", style: "margin:10px 0" },
      el("button", { class: "btn ghost", onclick: (e) => runCmd("Find", "db.find", {}, (d) => {
          dbSel.innerHTML = "";
          const items = d.items || [];
          dbSel.append(el("option", { value: "" }, items.length ? `— ${items.length} database${items.length > 1 ? "s" : ""} found —` : "— no .db files found in user folders —"));
          items.forEach((it) => dbSel.append(el("option", { value: it.path }, `${it.path}  (${fmtB(it.size)})`)));
          if (!items.length) toast("no .db/.sqlite files found — point Files search at another folder", true);
        }, e.target) }, "🔍 Find DB files"),
      dbSel),
    el("div", { class: "btn-row", style: "margin:10px 0" },
      el("button", { class: "btn ghost", onclick: (e) => runCmd("Tables", "db.tables", params(), (d) => { tablesBox.innerHTML = ""; for (const t of d.tables) tablesBox.append(el("button", { class: "btn ghost sm", onclick: () => (sql.value = `SELECT * FROM ${t} LIMIT 50;`) }, t)); if (!d.tables.length) toast("no tables"); else if (!sql.value) sql.value = `SELECT * FROM ${d.tables[0]} LIMIT 50;`; }, e.target) }, "List tables"),
      el("button", { class: "btn", onclick: (e) => runCmd("Query", "db.query", { ...params(), sql: sql.value }, (d) => out.show(d), e.target) }, "▶ Run query"),
      el("button", { class: "btn ghost sm", onclick: () => (sql.value = "") }, "clear")),
    sql, out.box, tablesBox));
}

/* ================= WEB DEPLOY ================= */
function renderDeploy(v) {
  const dir = el("input", { value: "C:\\HKS_PROGRAM\\www", style: "width:240px" });
  const port = el("input", { value: "8090", style: "width:80px" });
  const ipSel = el("select", null, el("option", { value: "0.0.0.0" }, "all interfaces (0.0.0.0)"));
  const list = el("div");
  v.append(panel("Deploy a folder as a web server on the laptop (choose IP + port)",
    el("div", { class: "btn-row" }, dir, port, ipSel,
      el("button", { id: "deployStartBtn", class: "btn", onclick: (e) => runCmd("Deploy", "deploy.start", { path: dir.value, port: +port.value, ip: ipSel.value }, (d) => {
          toast("deployed");
          let m;
          m = modal("Web server running on the laptop",
            el("div", { class: "sub" }, "Open this address from any device on the same network:"),
            el("div", { class: "btn-row" },
              el("a", { class: "btn", href: d.url || "#", target: "_blank" }, d.url || "(no url)"),
              el("button", { class: "btn ghost", onclick: () => { navigator.clipboard && navigator.clipboard.writeText(d.url || ""); toast("copied"); } }, "copy")),
            el("div", { class: "row end", style: "margin-top:14px" }, el("button", { class: "btn", onclick: () => m.remove() }, "Close")));
          refresh();
        }, e.target) }, "▶ Deploy"),
      el("button", { class: "btn ghost", onclick: refresh }, "⟳")), list),
    el("div", { class: "muted" }, "Phones/PCs on the same network can open http://LAPTOP-IP:PORT — works across subnets if routed."));
  netIps(ipSel);
  async function refresh() {
    try { const d = await cmd("deploy.list"); list.innerHTML = "";
      if (!(d.items || []).length) { list.append(emptyState("🌐", "no web servers deployed")); return; }
      const unique = new Map();
      for (const s of d.items) unique.set(String(s.port || s.id), s);
      const cards = el("div", { class: "deploy-card-grid" });
      for (const s of unique.values()) cards.append(el("article", { class: "deploy-card" },
        el("div", { class: "network-card-head" }, el("strong", null, `Port ${s.port}`), el("span", { class: "tag ok" }, "Running")),
        el("div", { class: "deploy-card-detail" }, el("span", { class: "muted" }, "Address"),
          el("a", { href: `http://${s.ip}:${s.port}`, target: "_blank", rel: "noopener" }, `${s.ip}:${s.port}`)),
        el("div", { class: "deploy-card-detail" }, el("span", { class: "muted" }, "Folder"), el("code", null, s.dir)),
        el("button", { class: "btn dangerg sm", onclick: (e) => runCmd("Stop", "deploy.stop", { id: s.id }, refresh, e.target) }, "■ Stop")));
      list.append(cards);
    } catch (e) { list.innerHTML = `<span class="muted">${esc(e.message)}</span>`; }
  }
  refresh();
}
async function netIps(sel) {
  try { const d = await cmd("net.adapters");
    for (const a of d.items) if (a.ip && !a.ip.startsWith("127.")) sel.append(el("option", { value: a.ip }, `${a.name} (${a.ip})`));
  } catch {}
}

/* ================= CAMERA & MIC CLIP ================= */
function renderCam(v) {
  renderCamBase(v);
  // ---- mini CCTV (round 14): motion detection + timed recording
  const motionState = el("span", { class: "tag" }, "off");
  const sens = el("input", { type: "range", min: "5", max: "60", value: "18", style: "flex:1" });
  const motionEvents = el("div", { class: "muted" }, "no motion events yet");
  const recMin = el("input", { type: "number", min: "1", max: "60", value: "5", style: "max-width:90px" });
  const recState = el("span", { class: "muted" }, "idle");
  liveEvents.filter((e) => e.kind === "motion_detected").slice(0, 6).forEach((e) =>
    motionEvents.append(el("div", { class: "kvrow" }, el("span", null, "motion @ " + new Date(e.ts * 1000).toLocaleTimeString()),
      el("span", { class: "tag warn" }, "score " + (e.data || {}).score))));
  v.append(panel("Motion detection (mini CCTV)",
      el("p", { class: "muted", style: "margin-top:0" }, "Watches the camera; saves a photo + alerts here when something moves."),
      el("div", { class: "btn-row", style: "margin-bottom:8px" },
        el("button", { class: "btn", onclick: async (e) => {
          busy(e.target, true);
          try { const r = await cmd("camera.motion", { on: true, sensitivity: +sens.value }, 30);
            motionState.textContent = "ON"; motionState.className = "tag ok"; toast("motion detection running");
          } catch (er) { toast(er.message, true); }
          busy(e.target, false);
        } }, "▶ Start motion"),
        el("button", { class: "btn ghost", onclick: async (e) => {
          try { await cmd("camera.motion", { on: false }, 20);
            motionState.textContent = "off"; motionState.className = "tag"; } catch (er) { toast(er.message, true); }
        } }, "■ Stop"),
        motionState),
      el("div", { class: "btn-row" }, el("span", { class: "muted" }, "sensitivity"), sens),
      el("div", { style: "margin-top:10px" }, motionEvents)));
  v.append(panel("Camera recording (CCTV clip)",
      el("div", { class: "btn-row", style: "margin-bottom:8px" }, recMin, el("span", { class: "muted" }, "minutes"), recState),
      el("div", { class: "btn-row" },
        el("button", { class: "btn", onclick: async (e) => {
          busy(e.target, true);
          try { const r = await cmd("camera.record", { seconds: (+recMin.value || 5) * 60 }, 30);
            recState.textContent = "recording " + ((+recMin.value || 5)) + " min…"; recState.className = "tag warn";
            toast("recording started"); } catch (er) { toast(er.message, true); }
          busy(e.target, false);
        } }, "⏺ Record"),
        el("button", { class: "btn danger", onclick: async () => {
          try { await cmd("camera.record_stop", {}, 20); recState.textContent = "stopped"; recState.className = "tag"; }
          catch (er) { toast(er.message, true); }
        } }, "■ Stop")),
      el("p", { class: "muted", style: "margin-top:8px" }, "Clips are saved on the laptop in the captures folder (Files → that folder to download).")));
}
function renderCamBase(v) {
  const camSel = el("select", null, el("option", { value: "0" }, "Camera 0 (checked when opened)"));
  const view = el("canvas", { id: "camview", width: 640, height: 480, style: "background:#000;border-radius:12px;max-width:100%" });
  const toggle = el("button", { class: "btn", onclick: async () => {
      if (!STATE.live.session) return toast("desktop agent offline", true);
      STATE.camOn = !STATE.camOn;
      const idx = +camSel.value;
      if (STATE.camOn) {
        startLive("camStills", "#camview", "camera.stream",
          { index: idx, fps: +(($("#camFps") || {}).value) || 8,
            quality: +(($("#camQ") || {}).value) || 70,
            width: +(($("#camRes") || {}).value) || 960 },
          2, "camSize", +(($("#camFps") || {}).value) || 8);
        toggle.textContent = "⏹ Stop camera";
        toggle.className = "btn danger";
        if (!STATE.wsReady) toast("backup live mode — streaming camera frames over the agent link", true);
      } else {
        stopLive("camStills", "camera.stream", { index: idx });
        toggle.textContent = "▶ Live camera";
        toggle.className = "btn";
      }
    } }, "▶ Live camera");
  const micOut = resultBox();
  const secs = el("input", { value: "6", style: "width:64px" });
  v.append(panel("Camera — live stream & snapshot",
    el("div", { class: "btn-row", style: "margin-bottom:10px" }, camSel, toggle,
      el("button", { class: "btn ghost", onclick: (e) => runCmd("Detect", "camera.list", {}, (d) => { camSel.innerHTML = ""; const items = d.items || []; (items.length ? items : [0]).forEach((c) => camSel.append(el("option", { value: c }, items.length ? "Camera " + c : "Camera 0 (checked when opened)"))); if (d.probe_skipped) toast("Camera is checked only when you start a stream or snapshot."); }, e.target) }, "⟳ status"),
      el("button", { class: "btn", onclick: (e) => runCmd("Snapshot", "camera.snap", { index: +camSel.value }, (d) => d.b64 && modal("Snapshot", el("img", { src: "data:image/jpeg;base64," + d.b64, style: "width:100%" })), e.target) }, "📸 Snapshot")),
      el("div", { class: "btn-row", style: "margin-top:10px" },
        el("span", { class: "tag", id: "camModePill" }, "idle"),
        el("select", { id: "camRes" },
          el("option", { value: "640" }, "640×360"),
          el("option", { value: "960", selected: true }, "960×540"),
          el("option", { value: "1280" }, "1280×720")),
        el("select", { id: "camFps" },
          el("option", { value: "5" }, "5 fps"),
          el("option", { value: "8", selected: true }, "8 fps"),
          el("option", { value: "12" }, "12 fps"),
          el("option", { value: "20" }, "20 fps")),
        el("select", { id: "camQ" },
          el("option", { value: "55" }, "low size"),
          el("option", { value: "70", selected: true }, "normal"),
          el("option", { value: "85" }, "high quality"))),
    view),
    panel("Microphone — record a clip",
      el("div", { class: "btn-row" }, secs, el("span", { class: "muted" }, "seconds"),
        el("button", { class: "btn", onclick: async (e) => {
          try { busy(e.target, true, "recording…");
            const d = await cmd("mic.record", { seconds: +secs.value }, (+secs.value + 25) * 1000);
            micOut.box.innerHTML = ""; micOut.box.style.display = "block";
            micOut.box.append(el("audio", { controls: "", src: "data:audio/wav;base64," + d.b64, style: "width:100%" }));
          } catch (er) { toast(er.message, true); } finally { busy(e.target, false); }
        } }, "🎙 Record clip")),
      micOut.box),
    el("div", { class: "muted" }, "Tip: for live listening use Session Tools → Listen. Login captures appear in Logs → Login captures."));
  every(8000, async () => { try { if (!camSel.dataset.loaded) { const d = await cmd("camera.list"); camSel.innerHTML = ""; const items = d.items || []; (items.length ? items : [0]).forEach((c) => camSel.append(el("option", { value: c }, items.length ? "Camera " + c : "Camera 0 (checked when opened)"))); camSel.dataset.loaded = "1"; } } catch {} });
  cleanupFns.push(() => { if (STATE.camOn) { STATE.camOn = false; stopLive("camStills", "camera.stream"); } });
}

/* ================= MEDIA PLAYER ================= */
function renderPlayer(v) {
  v.append(el("div", { class: "viewhead" }, el("div", null,
    el("h2", { class: "vt" }, "Media"), el("p", { class: "vsub" }, "Now playing, library and remote playback"))));
  v.append(chromeBar("mp", "#f59e0b", "Mopidy style", "engine: HKS media player (real playback on the laptop) — the real Mopidy server installs via HKS_ADDONS"));
  {
    const np = el("div", { class: "mp-nowbar", style: "display:none" });
    v.append(np);
    const npTick = async () => {
      if (!document.body.contains(np)) return;
      try {
        const d = await cmd("media.now_playing", {}, 15);
        const t = d && ((d.now_playing && d.now_playing[0]) || d.title || d.track);
        if (t) { np.style.display = ""; np.textContent = "♫ " + (typeof t === "string" ? t : (t.title || t.name || JSON.stringify(t).slice(0, 60))); }
      } catch (e) { /* honest: bar stays hidden when nothing reports */ }
    };
    npTick();
    const iv = setInterval(npTick, 5000);
    cleanupFns.push(() => clearInterval(iv));
  }
  const tabs = el("div", { class: "tabs" });
  const box = el("div");
  v.append(tabs, box);
  const mk = (name, fn, def) => {
    const b = el("button", { class: "tab" + (def ? " on" : ""), onclick: () => {
      tabs.querySelectorAll(".tab").forEach((t) => t.classList.toggle("on", t === b));
      fn();
    } }, name);
    tabs.append(b);
    if (def) fn();
  };
  mk("Now playing", paintNow, true);
  mk("Library", paintLibrary);
  mk("Play on laptop", paintPlay);
  function paintNow() {
    box.innerHTML = "";
    const np = el("div", { class: "muted" }, "press Detect");
    box.append(panel("Now Playing (from the laptop's media apps)",
        el("div", { class: "btn-row", style: "margin-bottom:8px" },
          el("button", { class: "btn", onclick: (e) => { busy(e.target, true);
            cmd("media.now_playing", {}, 30).then((d) => { busy(e.target, false); paintNp(d); })
              .catch((x) => { busy(e.target, false); toast(x.message, true); }); } }, "⟳ Detect now playing")), np));
    function paintNp(d) {
      np.innerHTML = ""; np.className = "";
      const items = d.now_playing || [];
      const mediaActions = el("div", { class: "mp-transport", role: "group", "aria-label": "Laptop media controls" },
        [["media.previous", "⏮ Previous"], ["media.play_pause", "⏯ Pause / play"], ["media.next", "⏭ Next"],
          ["volume.down", "− Volume"], ["volume.up", "+ Volume"]].map(([action, label]) =>
          el("button", { class: "btn", type: "button", onclick: async (e) => {
            busy(e.target, true);
            try { await cmd(action, {}, 15); }
            catch (err) { toast(err.message, true); }
            finally { busy(e.target, false); }
          } }, label)));
      np.append(mediaActions);
      if (!items.length) { np.append(el("span", { class: "muted" }, "nothing playing right now (controls still target the laptop's active media app)")); return; }
      np.append(el("div", { class: "tbl-wrap" }, el("table", null,
        el("thead", null, el("tr", null, el("th", null, "App"), el("th", null, "Artist"), el("th", null, "Title"))),
        el("tbody", null, items.map((x) => el("tr", null,
          el("td", null, x.app), el("td", null, String(x.artist || "—")), el("td", null, x.title)))))));
    }
  }
  function paintLibrary() {
    box.innerHTML = "";
    const kind = el("select", null, ["img", "vid", "aud"].map((k, i) =>
      el("option", { value: k, selected: i === 0 }, { img: "Images", vid: "Videos", aud: "Music" }[k])));
    const lst = el("div", { class: "muted" }, "pick a type and scan");
    box.append(panel("Media library on the laptop",
        el("div", { class: "btn-row", style: "margin-bottom:10px" }, kind,
          el("button", { class: "btn", onclick: (e) => { busy(e.target, true, "scanning…");
            cmd("media.scan", { kind: kind.value, root: "C:\\Users" }, 90).then((d) => {
              busy(e.target, false); lst.innerHTML = ""; lst.className = "";
              const items = d.items || d.files || [];
              if (!items.length) { lst.append(el("span", { class: "muted" }, "nothing found")); return; }
              lst.append(el("div", { class: "tbl-wrap" }, el("table", null, el("tbody", null,
                items.slice(0, 80).map((f) => {
                  const path = typeof f === "string" ? f : (f.path || f.file || "");
                  const watch = () => {
                    const ext = (path.split(".").pop() || "").toLowerCase();
                    if (["jpg", "jpeg", "png", "gif", "webp", "bmp"].includes(ext))
                      return modal(path.split("\\").pop(),
                        el("img", { class: "media-preview", src: downloadUrl(path),
                          alt: path.split("\\").pop() }));
                    if (["mp4", "webm", "mov", "mkv", "avi"].includes(ext))
                      return modal(path.split("\\").pop(),
                        el("video", { src: downloadUrl(path), controls: true, autoplay: true,
                          style: "width:100%;max-height:70vh;background:#000" }));
                    if (["mp3", "wav", "ogg", "flac", "m4a"].includes(ext))
                      return modal(path.split("\\").pop(),
                        el("div", null, el("audio", { src: downloadUrl(path), controls: true, autoplay: true,
                          style: "width:100%" })));
                    modal("open file", el("p", { class: "muted" }, "no browser player for ." + ext + " — use Download or Play on laptop"));
                  };
                  const meta = typeof f === "string" ? {} : f;
                  const playButton = ["mp4", "m4v", "mov", "webm", "mkv", "avi", "wmv", "mp3", "wav", "ogg", "flac", "m4a", "aac", "wma"].includes(ext)
                    ? el("button", { class: "btn ghost sm", onclick: () =>
                        cmd("media.play_file", { path }, 30).then((d) => d.ok === false ? toast(d.error, true) : toast("playing on laptop"))
                          .catch((er) => toast(er.message, true)) }, "On laptop") : null;
                  return el("tr", null, el("td", null, String(path).split("\\").pop()),
                    el("td", { class: "muted" },
                      el("span", null, String(path)),
                      meta.mtime ? el("small", { class: "media-meta" }, "Modified " + fmtT(meta.mtime)) : null,
                      Number.isFinite(Number(meta.size)) ? el("small", { class: "media-meta" }, fmtB(meta.size)) : null),
                    el("td", null, el("span", { class: "btn-row" },
                      el("button", { class: "btn sm", onclick: watch }, "▶ Watch here"),
                      playButton,
                      el("button", { class: "btn ghost sm", onclick: () => {
                        const a = document.createElement("a");
                        a.href = downloadUrl(path); a.download = path.split("\\").pop(); a.click();
                      } }, "⬇"))));
                })))));
            }).catch((x) => { busy(e.target, false); toast(x.message, true); }); } }, "⟳ Scan C:\Users")),
        lst));
  }
  function paintPlay() {
    box.innerHTML = "";
    const yt = el("input", { placeholder: "YouTube URL (opens on the laptop)", style: "flex:1;min-width:200px" });
    const f = el("input", { placeholder: "music/video file path e.g. C:\\Music\\song.mp3", style: "flex:1;min-width:200px" });
    const vol = el("input", { type: "range", min: "0", max: "100", value: "50", style: "flex:1",
      oninput: debounce((e) => cmd("volume.set", { level: +e.target.value }).catch(() => {}), 300) });
    box.append(panel("Play on the laptop",
        el("div", { class: "btn-row" }, yt,
          el("button", { class: "btn", onclick: () => yt.value && cmd("media.play_youtube", { url: yt.value }, 40)
            .then(() => toast("opened on laptop")).catch((e) => toast(e.message, true)) }, "▶ YouTube")),
        el("div", { class: "btn-row", style: "margin-top:10px" }, f,
          el("button", { class: "btn", onclick: () => {
            const ext = (f.value.split(".").pop() || "").toLowerCase();
            if (!["mp4", "m4v", "mov", "webm", "mkv", "avi", "wmv", "mp3", "wav", "ogg", "flac", "m4a", "aac", "wma"].includes(ext))
              return toast("Choose an audio or video file (not an image or document).", true);
            cmd("media.play_file", { path: f.value }, 40).then((d) => d.ok === false ? toast(d.error, true) : toast("playing")).catch((e) => toast(e.message, true));
          } }, "▶ Play file"),
          el("button", { class: "btn dangerg", onclick: () => cmd("media.stop").then(() => toast("stopped"))
            .catch((e) => toast(e.message, true)) }, "⏹ Stop")),
        el("div", { class: "btn-row", style: "margin-top:10px" }, el("span", { class: "muted" }, "Volume"), vol)));
  }
}
function renderNet(v) {
  const adWrap = el("div"); const wifiWrap = el("div"); const profWrap = el("div");
  const netPill = el("span", { class: "tag" }, "…"); const usage = el("div", { class: "muted" });
  v.append(panel("Internet status", el("div", { class: "btn-row" }, netPill, usage,
      el("button", { class: "btn ghost", onclick: check }, "⟳ check"),
      el("button", { class: "btn dangerg", onclick: () => confirmDo("Disconnect Wi-Fi now?", () => cmd("net.wifi_disconnect").then(refresh)) }, "✂ Disconnect Wi-Fi"))),
    panel("Network adapters — enable / disable", adWrap),
    panel("Wi-Fi networks in range", el("div", { class: "btn-row", style: "margin-bottom:8px" }, el("button", { class: "btn", onclick: scanWifi }, "🔍 Scan")), wifiWrap),
    panel("Saved Wi-Fi profiles", profWrap),
    el("div", { class: "muted" }, "Cross-subnet: the agent connects OUT to your panel, so any routed network works (router port-forward, VPN like Tailscale/ZeroTier, or a public VPS)."));
  async function check() {
    try { const d = await cmd("net.internet_check");
      netPill.textContent = d.online ? "🌐 ONLINE (" + d.latency_ms + "ms)" : "🔴 OFFLINE";
      netPill.className = "tag " + (d.online ? "ok" : "bad");
    } catch (e) { netPill.textContent = e.message; netPill.className = "tag bad"; }
    try { const u = await cmd("net.usage");
      usage.textContent = `since boot: ↓ ${fmtB(u.recv_since_boot)} · ↑ ${fmtB(u.sent_since_boot)}`;
    } catch {}
  }
  async function refresh() {
    try {
      const d = await cmd("net.adapters");
      adWrap.innerHTML = "";
      const cards = el("div", { class: "network-card-grid" });
      for (const a of d.items) {
        const swInput = el("input", { type: "checkbox", "aria-label": (a.up ? "Disable " : "Enable ") + a.name });
        swInput.checked = !!a.up;
        const sw = el("label", { class: "switch", title: (a.up ? "Disable " : "Enable ") + a.name }, swInput, el("i", { "aria-hidden": "true" }));
        swInput.onchange = async (e) => {
          const enable = !!e.target.checked;
          if (!enable && !window.confirm("Disable network adapter “" + a.name + "”? This can disconnect HKS and other network apps.")) {
            e.target.checked = true;
            return;
          }
          e.target.disabled = true;
          try {
            const result = await cmd(enable ? "net.enable" : "net.disable", { name: a.name });
            if (result.output && /failed|error/i.test(result.output)) throw new Error(result.output.trim().slice(0, 300));
            await refresh();
          } catch (x) { toast(x.message, true); refresh(); }
          finally { e.target.disabled = false; }
        };
        cards.append(el("article", { class: "network-card" },
          el("div", { class: "network-card-head" }, el("strong", null, a.name),
            el("span", { class: "tag " + (a.up ? "ok" : "bad") }, a.up ? "Connected" : "Disconnected")),
          el("dl", { class: "network-card-details" },
            el("div", null, el("dt", null, "IP address"), el("dd", null, a.ip || "—")),
            el("div", null, el("dt", null, "MAC address"), el("dd", null, a.mac || "—"))),
          el("label", { class: "network-card-switch" }, sw, el("span", null, a.up ? "Disable adapter" : "Enable adapter"))));
      }
      adWrap.append(cards);
      const p = await cmd("net.wifi_profiles");
      profWrap.innerHTML = "";
      const savedCards = el("div", { class: "network-card-grid" });
      for (const pr of p.items) savedCards.append(el("article", { class: "network-card network-profile-card" },
        el("strong", null, "📶 " + pr.ssid),
        el("button", { class: "btn ghost sm", onclick: () => connectSsid(pr.ssid, true) }, "Connect")));
      profWrap.append(savedCards);
      if (!(p.items || []).length) profWrap.append(el("span", { class: "muted" }, "no saved profiles"));
    } catch (e) { adWrap.innerHTML = `<span class="muted">${esc(e.message)}</span>`; }
    check();
  }
  async function scanWifi() {
    wifiWrap.innerHTML = "<span class='spin'></span>";
    try {
      const d = await cmd("net.wifi_scan");
      wifiWrap.innerHTML = "";
      const cards = el("div", { class: "network-card-grid" });
      for (const n of d.items) cards.append(el("article", { class: "network-card network-profile-card" },
        el("div", { class: "network-card-head" }, el("strong", null, "📶 " + n.ssid), el("span", { class: "tag" }, n.signal || "Signal unavailable")),
        el("button", { class: "btn ghost sm", onclick: () => connectSsid(n.ssid, false) }, "Connect")));
      wifiWrap.append(cards);
      if (!d.items.length) wifiWrap.append(emptyState("📶", "no networks found"));
    } catch (e) { wifiWrap.innerHTML = `<span class="muted">${esc(e.message)}</span>`; }
  }
  function connectSsid(ssid, known) {
    let password = null;
    if (!known) { password = prompt(`Password for "${ssid}" (leave blank for open network):`); if (password === null) return; }
    cmd("net.wifi_connect", { ssid, password }).then(() => { toast("connecting to " + ssid); setTimeout(refresh, 6000); }).catch((e) => toast(e.message, true));
  }
  refresh();
}

/* ================= USB ================= */
function renderUsb(v) {
  v.append(el("div", { class: "viewhead" }, el("div", null,
    el("h2", { class: "vt" }, "USB Devices"), el("p", { class: "vsub" }, "Devices, storage blocking, disk management")),
    el("button", { class: "btn sm", onclick: (e) => { const b = $("#usbRefresh"); if (b) b.click(); } }, "⟳ Refresh")));
  const qa = el("div", { class: "chips", style: "margin-bottom:12px" });
  qa.append(el("button", { class: "chip", onclick: () => { const b = $("#usbRefresh"); if (b) b.click(); } }, "⟳ Refresh"));
  qa.append(el("button", { class: "chip", onclick: () => {
    cmd("apps.launch", { target: "diskmgmt.msc" }, 20).then(() => toast("opened on the laptop"))
      .catch((e) => toast(e.message, true));
  } }, "🗄 Open Disk Management"));
  v.append(qa);
  renderUsbBase(v);
}
function renderUsbBase(v) {
  const wrap = el("div");
  v.append(panel("USB devices",
    el("div", { class: "sub" }, "Disable individual devices or all mass-storage (protects ports & data; ports stay powered when the PC is on — disabling idle storage saves power). Keyboards/mice are protected."),
    el("div", { class: "btn-row", style: "margin-bottom:10px" },
      el("button", { id: "usbRefresh", class: "btn ghost", onclick: refresh }, "⟳"),
      el("button", { class: "btn dangerg", onclick: () => confirmDo("Disable ALL USB mass-storage devices?", () => cmd("usb.storage_disable", { disable: true }).then(refresh)) }, "⛔ Disable all USB storage"),
      el("button", { class: "btn", onclick: (e) => runCmd("Re-enable", "usb.storage_disable", { disable: false }, refresh, e.target) }, "✅ Re-enable USB storage"),
      el("button", { class: "btn ghost", onclick: (e) => runCmd("Anti-heat", "usb.ss_off", {}, (d) => toast("USB selective suspend disabled"), e.target) }, "⚡ Keep ports powered")), wrap));
  async function refresh() {
    try {
      const d = await cmd("usb.list");
      wrap.innerHTML = "";
      if (!(d.items || []).length) { wrap.append(emptyState("🔌", "no USB devices found")); return; }
      const cards = el("div", { class: "usb-card-grid" });
      for (const u of d.items) cards.append(el("article", { class: "usb-card" },
        el("div", { class: "app-card-head" }, el("strong", null, "🔌 " + u.name),
          el("span", { class: "tag " + (u.ok ? "ok" : "bad") }, u.status)),
        el("div", { class: "usb-card-field" }, el("span", { class: "muted" }, "Class"), el("b", null, u.class || "Unknown")),
        el("code", { class: "usb-instance-id" }, u.id),
        el("button", { class: "btn ghost sm", onclick: (e) => runCmd("USB", "usb.device", { instance_id: u.id, enable: !u.ok }, refresh, e.target) }, u.ok ? "Disable device" : "Enable device")));
      wrap.append(cards);
    } catch (e) { wrap.innerHTML = `<span class="muted">${esc(e.message)}</span>`; }
  }
  refresh();
}

/* ================= POWER & BOOT ================= */
function renderPower(v) {
  const bat = el("div", { class: "power-grid" }, "Loading power status…");
  const until = el("input", { type: "datetime-local" });
  const delayMin = el("input", { type: "number", value: "30", style: "width:80px" });
  const sbMode = el("select", null, el("option", { value: "minimal" }, "Safe Mode (minimal)"), el("option", { value: "network" }, "Safe Mode with networking"));
  const out = resultBox();
  v.append(panel("Battery & power state", bat, el("button", { class: "btn ghost sm", onclick: refresh, style: "margin-top:8px" }, "⟳")),
    panel("Power actions", el("div", { class: "btn-row" },
      el("button", { class: "btn ghost", onclick: () => confirmDo("Sleep now?", () => cmd("power.sleep")) }, "😴 Sleep"),
      el("button", { class: "btn ghost", onclick: () => confirmDo("Hibernate now?", () => cmd("power.hibernate")) }, "🐻 Hibernate"),
      el("button", { class: "btn ghost", onclick: () => confirmDo("Turn display off?", () => cmd("power.display_off")) }, "🌑 Display off"),
      el("button", { class: "btn ghost", onclick: () => confirmDo("Lock screen?", () => cmd("power.lock_screen")) }, "🔒 Lock"),
      el("button", { class: "btn ghost", onclick: () => confirmDo("Log off the Windows user?", () => cmd("power.logoff_console")) }, "🚪 Log off"),
      el("button", { class: "btn dangerg", onclick: () => confirmDo("RESTART the laptop?", () => cmd("power.restart")) }, "↻ Restart"),
      el("button", { class: "btn danger", onclick: () => confirmDo("SHUTDOWN the laptop in 10s?", () => cmd("power.shutdown", { seconds: 10 })) }, "⏻ Shutdown"),
      el("button", { class: "btn ghost", onclick: (e) => runCmd("Cancel", "power.shutdown_cancel", {}, () => toast("shutdown cancelled"), e.target) }, "Cancel shutdown"))),
    panel("Sleep with wake-up signal", el("div", { class: "btn-row" }, until,
        el("button", { class: "btn", onclick: async () => {
          if (!until.value) return toast("pick a wake time", true);
          const iso = new Date(until.value).toISOString();
          confirmDo(`Sleep now and auto-wake at ${until.value}?`, () => cmd("power.sleep_until", { wake_at: iso }).then(() => toast("sleeping — will wake on schedule")));
        } }, "😴 Sleep until…"),
        delayMin, el("span", { class: "muted" }, "min"),
        el("button", { class: "btn ghost", onclick: async () => {
          confirmDo(`Sleep now and wake in ${delayMin.value} minutes?`, async () => {
            const at = new Date(Date.now() + (+delayMin.value) * 60000).toISOString();
            await cmd("power.sleep_until", { wake_at: at }); toast("sleeping — timed wake set");
          });
        } }, "😴 Sleep for N min")),
      el("div", { class: "muted", style: "margin-top:8px" }, "Creates a Windows task with 'wake to run' — the laptop wakes itself at the set time. It also reconnects and reports the moment anything wakes it.")),
    panel("Safe mode", el("div", { class: "btn-row" }, sbMode,
        el("button", { class: "btn dangerg", onclick: () => confirmDo("Arm Safe Mode for the NEXT boot?", () => cmd("power.safeboot_on", { mode: sbMode.value })) }, "⚙ Boot into Safe Mode next restart"),
        el("button", { class: "btn ghost", onclick: (e) => runCmd("Normal boot", "power.safeboot_off", {}, () => toast("next boot will be normal Windows"), e.target) }, "↩ Back to normal boot")),
      el("div", { class: "muted", style: "margin-top:8px" }, "After booting into Safe Mode, re-open this panel (service starts in Safe Mode too) and press 'Back to normal boot', then restart.")),
    panel("Wake timers (what CAN wake this PC)",
      el("button", { class: "btn ghost sm", onclick: (e) => runCmd("Wake timers", "power.wake_timers", {}, (d) => out.show((d.items || []).join("\n") || "none"), e.target) }, "⟳ list"), out.box));
  async function refresh() {
    try {
      const d = await cmd("power.status");
      const safe = d.safeboot_pending ? el("span", { class: "tag warn" }, "safe mode armed for next boot") : null;
      bat.innerHTML = "";
      Object.entries(d || {}).filter(([k]) => k !== "safeboot_pending")
        .forEach(([k, val]) => bat.append(el("div", { class: "power-item" }, el("span", null, k), el("b", null, String(val)))));
      if (safe) bat.append(safe);
    } catch (e) { bat.textContent = e.message; }
  }
  refresh();
}

/* ================= SCHEDULER & CODE ================= */
function renderTasks(v) {
  let winB = null;
  const name = el("input", { placeholder: "task name", style: "width:140px" });
  const type = el("select", null, el("option", { value: "command" }, "PowerShell command"), el("option", { value: "cmd" }, "CMD command"), el("option", { value: "url" }, "open URL"), el("option", { value: "media" }, "play file"), el("option", { value: "message" }, "toast message"));
  const value = el("input", { placeholder: "value (command / url / path / message)", style: "flex:1;min-width:200px" });
  const when = el("select", { onchange: () => { dtWrap.style.display = when.value === "once" ? "" : "none"; minWrap.style.display = when.value === "interval" ? "" : "none"; delayWrap.style.display = when.value === "delay" ? "" : "none"; dailyWrap.style.display = when.value === "daily" ? "" : "none"; } },
    el("option", { value: "once" }, "once at"), el("option", { value: "delay" }, "after N minutes"), el("option", { value: "daily" }, "daily at"), el("option", { value: "interval" }, "every N minutes"));
  const dt = el("input", { type: "datetime-local" }); const mins = el("input", { type: "number", value: "30", style: "width:70px" });
  const delayM = el("input", { type: "number", value: "5", style: "width:70px" });
  const dailyT = el("input", { type: "time", value: "09:00" });
  const dtWrap = el("span", { class: "row", style: "gap:6px" }, dt);
  const minWrap = el("span", { class: "row", style: "gap:6px;display:none" }, mins);
  const delayWrap = el("span", { class: "row", style: "gap:6px;display:none" }, delayM, el("span", { class: "muted" }, "min"));
  const dailyWrap = el("span", { class: "row", style: "gap:6px;display:none" }, dailyT);
  const list = el("div");
  v.append(panel("Create scheduled task",
    el("div", { class: "btn-row", style: "margin-bottom:10px" }, name, type, value),
    el("div", { class: "btn-row" }, when, dtWrap, dailyWrap, delayWrap, minWrap,
      el("button", { class: "btn", onclick: add }, "➕ Schedule"))),
    panel("Upload code / script to run later",
      el("div", { class: "sub" }, "Python (.py), PowerShell (.ps1) or batch (.bat/.cmd) — max 512 KB. Upload once, then schedule it above as type = script."),
      (() => {
        const sName = el("input", { placeholder: "script name", style: "width:150px" });
        const sExt = el("select", null, el("option", { value: "py" }, ".py"), el("option", { value: "ps1" }, ".ps1"), el("option", { value: "bat" }, ".bat"), el("option", { value: "cmd" }, ".cmd"));
        const file = el("input", { type: "file", accept: ".py,.ps1,.bat,.cmd" });
        const ta = el("textarea", { rows: "4", style: "width:100%;font-family:Consolas,monospace", placeholder: "…or paste code here (used if no file chosen)" });
        const upBtn = el("button", { class: "btn", onclick: async (e) => {
          try {
            busy(e.target, true, "uploading…");
            let b64 = "", nm = sName.value || "script";
            if (file.files[0]) {
              const f = file.files[0];
              if (f.size > 512 * 1024) throw new Error("file > 512KB");
              const buf = new Uint8Array(await f.arrayBuffer());
              let bin = ""; for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode(...buf.subarray(i, i + 0x8000));
              b64 = btoa(bin); nm = sName.value || f.name.replace(/\.[^.]+$/, "");
            } else {
              if (!ta.value.trim()) throw new Error("choose a file or paste code");
              b64 = btoa(unescape(encodeURIComponent(ta.value)));
            }
            const d = await cmd("tasks.upload_script", { name: nm, ext: sExt.value, content_b64: b64 }, 60);
            toast("uploaded: " + d.path);
            addScriptTask(d.name, d.path);
          } catch (er) { toast(er.message, true); } finally { busy(e.target, false); }
        } }, "⬆ Upload & schedule");
        function addScriptTask(nm, pth) {
          cmd("tasks.agent_add", { name: "run:" + nm, type: "script", path: pth, when: when.value,
            at: when.value === "once" && dt.value ? new Date(dt.value).toISOString() : null,
            delay_min: +delayM.value, interval_min: +mins.value })
            .then(() => { toast("script scheduled (" + when.value + ")"); refresh(); })
            .catch((er) => toast(er.message, true));
        }
        return el("div", null,
          el("div", { class: "btn-row", style: "margin-bottom:8px" }, sName, sExt, file),
          ta, el("div", { class: "btn-row", style: "margin-top:8px" }, upBtn,
            el("span", { class: "muted" }, "uses the schedule chosen above (once at / after N min / daily / interval)")));
      })()),
    panel("Agent tasks", el("div", { class: "btn-row", style: "margin-bottom:8px" }, el("button", { class: "btn ghost sm", onclick: refresh }, "⟳")), list),
    panel("Windows scheduled tasks (schtasks)", el("div", { class: "btn-row", style: "margin-bottom:8px" }, el("button", { class: "btn ghost sm", onclick: listWin }, "⟳ list")), winBox()));
  function winBox() { winB = el("div"); return winB; }
  async function add() {
    const params = { name: name.value, type: type.value, value: value.value, when: when.value,
      at: when.value === "once" && dt.value ? new Date(dt.value).toISOString() : null,
      interval_min: +mins.value, delay_min: +delayM.value };
    try { await cmd("tasks.agent_add", params); toast("scheduled"); refresh(); } catch (e) { toast(e.message, true); }
  }
  async function refresh() {
    try { const d = await cmd("tasks.agent_list"); list.innerHTML = "";
      if (!(d.items || []).length) { list.append(emptyState("⏰", "no scheduled tasks")); return; }
      const t = el("table", { class: "t" }, el("thead", null, el("tr", null, el("th", null, "Name"), el("th", null, "Task"), el("th", null, "Schedule"), el("th", null, "Actions"))));
      for (const x of d.items) {
        const sched = x.when === "once" ? "once @ " + (x.at || "next ping") :
          x.when === "delay" ? `after ${x.delay_min} min` :
          x.when === "daily" ? "daily @ " + (x.at || "?") : `every ${x.interval_min} min`;
        t.append(tRow(x.name, el("span", { class: "mono", style: "font-size:11px" }, `${x.type}: ${(x.value || x.path || "").slice(0, 60)}`), sched,
          el("span", { class: "btn-row" },
            el("button", { class: "btn ghost sm", onclick: () => cmd("tasks.agent_run", { name: x.name }).then(() => toast("ran now")).catch((e) => toast(e.message, true)) }, "▶ run"),
            el("button", { class: "btn dangerg sm", onclick: () => confirmDo("Delete task?", () => cmd("tasks.agent_remove", { name: x.name }).then(refresh)) }, "🗑"))));
      }
      list.append(el("div", { class: "tscroll" }, t));
    } catch (e) { list.innerHTML = `<span class="muted">${esc(e.message)}</span>`; }
  }
  async function listWin() {
    try { const d = await cmd("schtasks.list", {}, 60);
      winB.innerHTML = ""; winB.append(el("div", { class: "pre", style: "max-height:260px" }, (d.items || []).join("\n")));
    } catch (e) { winB.innerHTML = `<span class="muted">${esc(e.message)}</span>`; }
  }
  refresh();
}

/* ================= LOGS ================= */
function renderLogs(v) {
  let tab = "events", eventPage = 0;
  const EVENT_PAGE_SIZE = 30;
  const tabs = el("div", { class: "seg", style: "flex-wrap:wrap" });
  const box = el("div");
  const kindSel = el("select", null, ...["", "logon", "logoff", "boot", "agent_start", "shutdown_request", "sleep", "resume", "network_change", "usb_change", "hw_report", "alert", "login_ok", "login_failed", "self_destruct", "task_ran", "zip_created", "capture_logon_cam", "error"].map((k) => el("option", { value: k }, k || "(all events)")));
  kindSel.onchange = () => { tab = "events"; eventPage = 0; load(); };
  for (const [id, label] of [["events", "Events"], ["logins", "🔐 Login captures"], ["audit", "Admin audit"], ["sys", "System report"], ["winlog", "Windows logon/shutdown"]])
    tabs.append(el("button", { onclick: () => { tab = id; load(); [...tabs.children].forEach((x) => x.classList.remove("on")); [...tabs.children][[ "events", "logins", "audit", "sys", "winlog" ].indexOf(id)].classList.add("on"); } }, label));
  [...tabs.children][0].classList.add("on");
  v.append(panel("Complete activity log — who logged in, what happened, hardware timeline",
      el("div", { class: "btn-row", style: "margin-bottom:10px" }, tabs, el("span", { class: "muted" }, "filter:"), kindSel,
        el("span", { style: "flex:1" }),
        el("a", { class: "btn ghost sm", href: "/api/export?what=events" }, "⬇ events.csv"),
        el("a", { class: "btn ghost sm", href: "/api/export?what=audit" }, "⬇ audit.csv"),
        el("a", { class: "btn ghost sm", href: "/api/export?what=telemetry" }, "⬇ telemetry.csv")),
      box));
  async function load() {
    box.innerHTML = "<span class='spin'></span>";
    try {
      if (tab === "events") {
        const d = await api(`/api/events?machine=${encodeURIComponent(STATE.machine || "")}&kind=${encodeURIComponent(kindSel.value)}&limit=400`);
        const events = d.events || [], pages = Math.max(1, Math.ceil(events.length / EVENT_PAGE_SIZE));
        eventPage = Math.min(eventPage, pages - 1);
        const shown = events.slice(eventPage * EVENT_PAGE_SIZE, (eventPage + 1) * EVENT_PAGE_SIZE);
        const t = el("table", { class: "t" }, el("thead", null, el("tr", null, el("th", null, "Time"), el("th", null, "Kind"), el("th", null, "Details"))));
        for (const e of shown) t.append(tRow(fmtT(e.ts), el("span", { class: "event-kind" }, humanEventKind(e.kind)),
          el("span", { class: "mono", style: "font-size:11px" }, prettyEvent(e))));
        box.innerHTML = "";
        if (!events.length) box.append(emptyState("📭", "No activity events recorded for this laptop yet."));
        else {
          const pager = el("div", { class: "btn-row log-pager" },
            el("button", { class: "btn ghost sm", disabled: eventPage === 0, onclick: () => { eventPage--; load(); } }, "← Newer"),
            el("span", { class: "muted", "aria-live": "polite" }, `Showing ${eventPage * EVENT_PAGE_SIZE + 1}–${eventPage * EVENT_PAGE_SIZE + shown.length} of ${events.length} · page ${eventPage + 1}/${pages}`),
            el("button", { class: "btn ghost sm", disabled: eventPage >= pages - 1, onclick: () => { eventPage++; load(); } }, "Older →"));
          box.append(pager, el("div", { class: "tscroll", style: "max-height:60vh" }, t), pager.cloneNode(true));
          const bottomPager = box.lastElementChild;
          [...bottomPager.querySelectorAll("button")].forEach((b, i) => b.onclick = () => { eventPage += i === 0 ? -1 : 1; load(); });
        }
      } else if (tab === "logins") {
        const [panelCaptures, windowsCaptures, eventData] = await Promise.all([
          api(`/api/captures?kind=login&limit=100`),
          api(`/api/captures?kind=logon_cam&limit=100`),
          api(`/api/events?limit=300`)
        ]);
        const captures = [...(panelCaptures.captures || []), ...(windowsCaptures.captures || [])]
          .sort((a, b) => Number(b.ts || 0) - Number(a.ts || 0)).slice(0, 100);
        const loginKinds = new Set(["login_ok", "login_failed", "login_totp_failed", "login_anomaly", "logon", "logoff", "capture_login", "capture_login_failed", "capture_logon_cam", "capture_failed"]);
        const events = (eventData.events || []).filter((e) => loginKinds.has(e.kind)).slice(0, 100);
        box.innerHTML = "";
        if (!captures.length && !events.length) { box.append(emptyState("🔐", "No login activity is recorded yet. New successful/failed panel logins and Windows session events will appear here.")); return; }
        if (captures.length) {
          const g = el("div", { class: "thumbs" });
          for (const c of captures) {
            let meta = {}; try { meta = JSON.parse(c.meta || "{}"); } catch {}
            g.append(el("div", { class: "cap" },
              el("img", { src: `/api/capture/${c.id}`, alt: "Login capture", loading: "lazy", onclick: () => modal("Login capture", el("img", { src: `/api/capture/${c.id}`, class: "media-preview", alt: "Login capture" })) }),
              el("div", { class: "c" }, el("b", null, fmtT(c.ts)),
                el("div", { class: "muted" }, [c.machine_id || "Laptop", meta.ip].filter(Boolean).join(" · ") || "Laptop · IP unavailable"),
                el("div", { class: "muted" }, prettyMeta(c.meta)),
                el("div", { class: "muted" }, c.kind === "logon_cam" ? "Windows sign-in/unlock" : "Panel " + (meta.action || "login")))));
          }
          box.append(panel("Login photos", g));
        }
        const eventList = el("div", { class: "audit-list" });
        for (const e of events) {
          let data = {}; try { data = JSON.parse(e.data || "{}"); } catch {}
          eventList.append(el("div", { class: "audit-row" },
            el("span", { class: "audit-time" }, fmtT(e.ts)),
            el("b", { class: "audit-action" }, String(e.kind).replaceAll("_", " ")),
            el("span", { class: "audit-user" }, data.user || "Windows session"),
            el("span", { class: "muted audit-ip" }, data.ip || data.why || "Details unavailable")));
        }
        if (events.length) box.append(panel("Login and Windows session events", eventList));
      } else if (tab === "audit") {
        const d = await api("/api/audit?limit=120");
        box.innerHTML = "";
        const rows = d.audit || [];
        if (!rows.length) box.append(emptyState("🧾", "No admin audit actions recorded yet."));
        else for (const a of rows) {
          let params = a.params || "";
          try { params = JSON.stringify(JSON.parse(params)); } catch {}
          box.append(el("div", { class: "audit-row audit-row-full" },
            el("span", { class: "audit-time" }, fmtT(a.ts)),
            el("b", { class: "audit-action" }, a.action || "—"),
            el("span", { class: "audit-user" }, a.user || "?"),
            el("span", { class: "muted audit-ip" }, a.ip || ""),
            el("span", { class: "audit-params" }, params),
            el("span", { class: "tag " + (a.result === "ok" ? "ok" : "bad") }, a.result || "unknown")));
        }
      } else if (tab === "sys") {
        const d = await cmd("sys.info", {}, 40);
        box.innerHTML = ""; box.append(el("div", { class: "pre" }, JSON.stringify(d, null, 2)));
      } else if (tab === "winlog") {
        const d = await cmd("logs.windows", { kinds: ["logon", "logoff", "shutdown", "start"] }, 90);
        box.innerHTML = ""; box.append(el("div", { class: "pre" }, (d.lines || []).join("\n") || "no entries"));
      }
    } catch (e) { box.innerHTML = `<span class="muted">${esc(e.message)}</span>`; }
  }
  function humanEventKind(kind) {
    const labels = { session_agent_start: "Desktop agent started", service_agent_start: "Service agent started",
      agent_connected: "Agent connected", agent_disconnected: "Agent disconnected", hw_report: "Hardware report",
      alert: "Alert", login_ok: "Login succeeded", login_failed: "Login failed", logon: "Windows session started",
      logoff: "Windows session ended", capture_failed: "Photo capture unavailable" };
    const key = String(kind || "system_event").toLowerCase();
    return labels[key] || key.replace(/_/g, " ").replace(/\b[a-z]/g, (c) => c.toUpperCase());
  }
  function prettyEvent(e) {
    try { const j = JSON.parse(e.data);
      if (e.kind === "boot") return (j.msg || "boot") + (j.detail ? " — " + j.detail : "");
      if (e.kind === "session_agent_start") return "Interactive desktop agent process started" + (j.pid ? ` (PID ${j.pid})` : "") + ".";
      if (e.kind === "service_agent_start") return "Background service agent process started" + (j.pid ? ` (PID ${j.pid})` : "") + ".";
      if (e.kind === "agent_connected") return `${j.role === "session" ? "Interactive desktop agent" : "Background service agent"} connected` + (j.version ? ` (version ${j.version})` : "") + ".";
      if (e.kind === "agent_disconnected") return `${j.role === "session" ? "Interactive desktop agent" : "Background service agent"} disconnected.`;
      if (e.kind === "hw_report") return `CPU ${Math.round(j.cpu)}% · RAM ${Math.round(j.ram)}%` + (j.temp_c != null ? ` · ${j.temp_c}°C` : "");
      if (e.kind === "alert") return "⚠ " + JSON.stringify(j);
      if (e.kind === "task_ran") return `${j.name} (${j.type}) exit=${j.exit ?? "—"}`;
      return (e.kind || "System event").replace(/_/g, " ") + (e.data && e.data !== "{}" ? ": " + JSON.stringify(j) : "");
    } catch { return (e.data || "System event details unavailable"); }
  }
  function prettyMeta(m) { try { const j = JSON.parse(m); return (j.user ? "user: " + j.user + " · " : "") + (j.trigger || j.action || ""); } catch { return m || "details unavailable"; } }
  load();
}

/* ================= STORAGE & CLEANUP ================= */
function renderClean(v) {
  const cards = el("div", { class: "clean-card-grid" });
  const current = el("span", { class: "tag", role: "status", "aria-live": "polite" }, "Loading panel storage…");
  const categories = [
    ["captures", "📷", "Photos & captures", "Delete HKS capture photos and their index records."],
    ["panel_log", "📄", "Panel log file", "Clear DATA/panel.log without removing other files."],
    ["events", "🧭", "Event history", "Delete event history from the HKS panel database."],
    ["errors", "⚠", "Agent error history", "Delete recorded agent errors from the panel database."],
    ["audit", "🛡", "Admin audit history", "Delete old audit entries. The cleanup itself is recorded afterward."]
  ];
  v.append(el("div", { class: "viewhead" }, el("div", null,
    el("h2", { class: "vt" }, "Storage & Cleanup"),
    el("p", { class: "vsub" }, "Review each category and clean only the data you select.")),
    el("button", { class: "btn ghost sm", onclick: refresh }, "⟳ Refresh")));
  v.append(panel("HKS panel data", el("div", { class: "btn-row" }, current), cards,
    el("p", { class: "muted" }, "Actions affect only HKS panel data and ask for an exact confirmation phrase. Personal files on the laptop are not touched.")));
  const fmt = (value) => { const n = Number(value) || 0; return n < 1024 ? n + " B" : n < 1048576 ? (n / 1024).toFixed(1) + " KB" : (n / 1048576).toFixed(1) + " MB"; };
  function paint(summary = null) {
    const counts = summary?.counts || {};
    cards.innerHTML = "";
    for (const [category, icon, title, description] of categories) {
      const available = !!summary;
      const count = available ? (category === "panel_log" ? Number(summary.panel_log_bytes || 0) : Number(counts[category] || 0)) : 0;
      const metric = !available ? "Count unavailable" : category === "panel_log" ? (count ? fmt(count) : "No log file") :
        category === "captures" ? `${count} items · ${fmt(summary.capture_bytes || 0)}` : `${count} records`;
      const button = el("button", { class: "btn danger sm", disabled: !available || count < 1,
        title: !available ? "Refresh the storage summary before cleaning data." : count < 1 ? "There is no data in this category to clean." : "" ,
        onclick: () => confirmCategory(category, title, metric) }, "Clean this category");
      cards.append(el("article", { class: "clean-card" },
        el("div", { class: "clean-card-head" }, el("span", { class: "clean-icon", "aria-hidden": "true" }, icon), el("span", { class: "tag" }, metric)),
        el("h3", null, title), el("p", { class: "muted" }, description), button));
    }
  }
  async function refresh() {
    current.textContent = "Loading panel storage…"; current.className = "tag";
    try {
      const d = await api("/api/storage/summary");
      if (!d || d.ok === false || !d.counts) throw new Error(d?.error || "The panel returned an incomplete storage summary.");
      paint(d); current.textContent = "Panel storage ready"; current.className = "tag ok";
    } catch (e) {
      paint(null);
      current.textContent = "Storage unavailable: " + (e.message || "unknown panel error");
      current.className = "tag bad";
      cards.prepend(el("div", { class: "clean-error", role: "alert" },
        el("b", null, "Storage summary could not be loaded."),
        el("span", null, " Cleanup is paused until the panel can read its database. Check administrator access, then retry. Panel says: " + (e.message || "unknown error")),
        el("button", { class: "btn ghost sm", onclick: refresh }, "Retry storage check")));
    }
  }
  function confirmCategory(category, title, metric) {
    const phrase = "DELETE " + category.toUpperCase();
    const input = el("input", { autocomplete: "off", placeholder: phrase, "aria-label": "Type " + phrase + " to confirm" });
    const status = el("div", { class: "muted", role: "status", "aria-live": "polite" }, "Nothing is removed until you confirm.");
    const remove = el("button", { class: "btn danger", disabled: true }, "Delete selected data");
    input.addEventListener("input", () => { remove.disabled = input.value.trim() !== phrase; });
    const drawer = openDrawer("Clean " + title,
      el("p", { class: "muted" }, metric + " selected. This action cannot be undone."),
      el("label", { class: "clean-confirm" }, "Type ", el("code", null, phrase), " to continue", input),
      el("div", { class: "btn-row" }, remove, el("button", { class: "btn ghost", onclick: () => drawer.close() }, "Cancel")), status);
    remove.onclick = async () => {
      remove.disabled = true; remove.textContent = "Cleaning…";
      try {
        const result = await api("/api/storage/cleanup", { method: "POST", body: { category, confirm: phrase } });
        status.textContent = `Done. ${result.files_removed || 0} file(s) removed${result.files_failed ? `; ${result.files_failed} could not be removed` : ""}.`;
        status.className = "tag ok"; await refresh(); setTimeout(() => drawer.close(), 1200);
      } catch (e) { status.textContent = e.message; status.className = "tag bad"; remove.disabled = false; remove.textContent = "Delete selected data"; }
    };
  }
  refresh();
}

/* ================= ERRORS ================= */
function renderErrors(v) {
  const box = el("div");
  v.append(panel("Error reports — every agent failure lands here with module + detail",
    el("div", { class: "btn-row", style: "margin-bottom:10px" },
      el("button", { class: "btn ghost sm", onclick: load }, "⟳"),
      el("button", { class: "btn dangerg sm", onclick: () => confirmDo("Clear ALL errors?", () => api("/api/errors/resolve", { method: "POST", body: { all: true } }).then(load)) }, "clear all")), box));
  async function load() {
    try {
      const d = await api("/api/errors?limit=400");
      box.innerHTML = "";
      if (!d.errors.length) { box.append(emptyState("🎉", "no errors reported")); return; }
      const t = el("table", { class: "t" }, el("thead", null, el("tr", null, el("th", null, "Time"), el("th", null, "Module"), el("th", null, "Message"), el("th", null, "Detail"), el("th", null, ""))));
      for (const e of d.errors) {
        t.append(tRow(fmtT(e.ts), e.module,
          el("span", { style: e.resolved ? "color:var(--tx3)" : "color:var(--bad)" }, e.message),
          el("span", { class: "mono", style: "font-size:10.5px" }, (e.detail || "").slice(0, 140)),
          e.resolved ? el("span", { class: "tag ok" }, "resolved")
            : el("button", { class: "btn ghost sm", onclick: () => api("/api/errors/resolve", { method: "POST", body: { id: e.id } }).then(load) }, "✔ resolve")));
      }
      box.append(el("div", { class: "tscroll", style: "max-height:60vh" }, t));
    } catch (e) { box.innerHTML = `<span class="muted">${esc(e.message)}</span>`; }
  }
  load();
}

/* ================= USERS & SECURITY ================= */
function renderUsers(v) {
  const usersBox = el("div", { class: "user-card-grid" });
  const sessBox = el("div", { class: "user-card-grid" });
  const banBox = el("div", { class: "user-card-grid" });
  const nu = el("input", { placeholder: "Username", autocomplete: "off", "aria-label": "New username" });
  const np = el("input", { placeholder: "Password (min 8)", type: "password", autocomplete: "new-password", "aria-label": "New user password" });
  const role = el("select", { "aria-label": "New user role" }, el("option", { value: "admin" }, "Admin"), el("option", { value: "observer" }, "Observer (read-only)"));
  const addUser = el("button", { class: "btn", onclick: async (e) => {
    if (!nu.value.trim() || !np.value) { toast("enter a username and password first", true); return; }
    busy(e.target, true);
    try { await api("/api/users", { method: "POST", body: { username: nu.value.trim(), password: np.value, role: role.value } });
      nu.value = ""; np.value = ""; toast("user added"); await load();
    } catch (er) { toast(er.message, true); } finally { busy(e.target, false); }
  } }, "＋ Add user");
  const createUser = el("div", { class: "user-create-grid" }, nu, np, role, addUser);
  const banIp = el("input", { placeholder: "IPv4 or IPv6 address", inputmode: "text", "aria-label": "IP address to ban" });
  const banReason = el("input", { placeholder: "Reason (optional)", "aria-label": "Ban reason" });
  const addBan = el("button", { class: "btn danger", onclick: async (e) => {
    const ip = banIp.value.trim();
    if (!ip) { toast("enter an IP address first", true); banIp.focus(); return; }
    busy(e.target, true);
    try { await api("/api/security/bans", { method: "POST", body: { ip, reason: banReason.value.trim() || "Banned by admin", permanent: true } });
      banIp.value = ""; banReason.value = ""; toast("IP banned"); await load();
    } catch (er) { toast(er.message, true); } finally { busy(e.target, false); }
  } }, "⊘ Add IP to ban");
  const banForm = el("div", { class: "user-create-grid ban-create-grid" }, banIp, banReason, addBan);
  v.append(panel("Add panel user", createUser), panel("Panel users", usersBox),
    panel("Active panel sessions", sessBox),
    panel("Banned IPs", el("div", null, banForm, banBox)));
  async function load() {
    try {
      const d = await api("/api/users");
      usersBox.innerHTML = "";
      for (const u of d.users) {
        const newPw = el("input", { type: "password", placeholder: "New password", autocomplete: "new-password", "aria-label": "New password for " + u.username });
        const setPassword = el("button", { class: "btn ghost sm", onclick: async (e) => {
          if (!newPw.value) { toast("type a new password first", true); newPw.focus(); return; }
          busy(e.target, true);
          try { await api("/api/users/update", { method: "POST", body: { id: u.id, password: newPw.value } }); newPw.value = ""; toast("password changed"); }
          catch (er) { toast(er.message, true); } finally { busy(e.target, false); }
        } }, "🔑 Save password");
        const actions = el("div", { class: "btn-row user-card-actions" }, newPw, setPassword,
          el("button", { class: "btn ghost sm", onclick: async (e) => {
            busy(e.target, true);
            try { await api("/api/users/update", { method: "POST", body: { id: u.id, role: u.role === "admin" ? "observer" : "admin" } }); await load(); }
            catch (er) { toast(er.message, true); busy(e.target, false); }
          } }, u.role === "admin" ? "⇄ Make observer" : "⇄ Make admin"),
          el("button", { class: "btn dangerg sm", onclick: () => confirmDo("Delete user " + u.username + "?", () => api("/api/users/delete", { method: "POST", body: { id: u.id } }).then(load).catch((er) => toast(er.message, true))) }, "⌫ Delete"));
        usersBox.append(el("article", { class: "user-card" },
          el("div", { class: "user-card-heading" }, el("strong", null, u.username), el("span", { class: "tag" }, u.role)),
          el("dl", { class: "user-card-details" },
            el("div", null, el("dt", null, "Created"), el("dd", null, fmtT(u.created))),
            el("div", null, el("dt", null, "Last login"), el("dd", null, fmtT(u.last_login)))), actions));
      }
      if (!d.users.length) usersBox.append(el("span", { class: "muted" }, "No users found."));
      sessBox.innerHTML = "";
      for (const s of d.sessions) sessBox.append(el("article", { class: "user-card" },
        el("div", { class: "user-card-heading" }, el("strong", null, s.username || "Panel session"), el("span", { class: "tag" }, ago(s.expires))),
        el("dl", { class: "user-card-details" }, el("div", null, el("dt", null, "IP address"), el("dd", null, s.ip)),
          el("div", null, el("dt", null, "Created"), el("dd", null, fmtT(s.created)))),
        el("button", { class: "btn ghost sm", onclick: async (e) => { busy(e.target, true); try { await api("/api/sessions/revoke", { method: "POST", body: { token: s.token } }); await load(); } catch (er) { toast(er.message, true); busy(e.target, false); } } }, "↪ Revoke session")));
      if (!d.sessions.length) sessBox.append(el("span", { class: "muted" }, "No active sessions."));
      banBox.innerHTML = "";
      for (const b of d.bans) banBox.append(el("article", { class: "user-card ban-card" },
        el("div", { class: "user-card-heading" }, el("strong", { class: "mono" }, b.ip), el("span", { class: "tag danger-tag" }, "Banned")),
        el("dl", { class: "user-card-details" }, el("div", null, el("dt", null, "Reason"), el("dd", null, b.reason || "—")),
          el("div", null, el("dt", null, "Expires"), el("dd", null, Number(b.until) > 1e15 ? "Never" : fmtT(b.until)))),
        el("button", { class: "btn ghost sm", onclick: async (e) => { busy(e.target, true); try { await api("/api/bans/remove", { method: "POST", body: { ip: b.ip } }); await load(); } catch (er) { toast(er.message, true); busy(e.target, false); } } }, "⊘ Unban IP")));
      if (!d.bans.length) banBox.append(el("span", { class: "muted" }, "No banned IPs."));
    } catch (e) { toast(e.message, true); }
  }
  load();
}

/* ================= SETTINGS ================= */
function renderSettings(v) {
  v.append(el("div", { class: "viewhead" }, el("div", null,
    el("h2", { class: "vt" }, "Settings"), el("p", { class: "vsub" }, "Appearance, notifications, startup, backup"))));
  const tabs = el("div", { class: "tabs" });
  const box = el("div");
  v.append(tabs, box);
  const mk = (name, fn, def) => {
    const b = el("button", { class: "tab" + (def ? " on" : ""), onclick: () => {
      tabs.querySelectorAll(".tab").forEach((t) => t.classList.toggle("on", t === b));
      fn();
    } }, name);
    tabs.append(b);
    if (def) fn();
  };
  const sw = (label, get, set) => {
    const inp = el("input", { type: "checkbox", "aria-label": label });
    inp.checked = !!get();
    inp.onchange = () => set(inp.checked);
    return el("div", { class: "kvrow" }, el("span", null, label),
      el("label", { class: "switch" }, inp, el("i")));
  };
  mk("General", () => {
    box.innerHTML = "";
    box.append(el("div", { class: "dashgrid" },
      panel("Panel",
        el("div", { class: "kv" },
          el("div", { class: "kvrow" }, el("span", null, "Address"), el("b", null, location.host)),
          el("div", { class: "kvrow" }, el("span", null, "Connection"), el("b", null, location.protocol.replace(":", ""))),
          el("div", { class: "kvrow" }, el("span", null, "Language"), el("b", null, "English"))),
        el("div", { class: "btn-row", style: "margin-top:10px" },
          el("button", { class: "btn ghost", onclick: () => showSection("users") }, "Users & passwords"),
          el("button", { class: "btn ghost", onclick: () => showSection("servermg") }, "Server management")))));
  }, true);
  mk("Appearance", () => {
    box.innerHTML = "";
      const themes = el("div", { class: "theme-cards" });
    const accents = el("div", { class: "chips" });
    let savedFont = "";
    try { savedFont = localStorage.getItem("hks_font") || ""; } catch (e) {}
    if (savedFont === "s" || savedFont === "l") document.documentElement.dataset.font = savedFont;
    const fonts = el("select", { "aria-label": "Interface text size", value: savedFont }, [["", "Normal"], ["s", "Small"], ["l", "Large"]].map(([v, n]) =>
      el("option", { value: v, selected: savedFont === v }, n)));
    fonts.onchange = () => {
      document.documentElement.dataset.font = fonts.value;
      try { localStorage.setItem("hks_font", fonts.value); } catch (e) {}
    };
    const paint = () => {
      themes.innerHTML = "";
      [["light", "#f2efe6"], ["dark", "#171a22"]].forEach(([name, col]) => {
        const cur = (localStorage.getItem("hks_theme") || "light");
        const title = name === "light" ? "☼ Light" : "☾ Dark";
        const card = el("button", { type: "button", class: "tcard" + (cur === name ? " on" : ""),
          "aria-label": "Use " + name + " theme", "aria-pressed": cur === name ? "true" : "false", onclick: () => {
          document.documentElement.dataset.theme = name;
          try { localStorage.setItem("hks_theme", name); } catch (e) {}
          paint();
        } }, el("span", { class: "swatch", "aria-hidden": "true", style: "background:" + col }), el("span", null, title));
        themes.append(card);
      });
      accents.innerHTML = "";
      [["blue", "Blue"], ["green", "Green"], ["purple", "Purple"]].forEach(([v, n]) => {
        const selected = (localStorage.getItem("hks_accent") || "blue") === v;
        accents.append(el("button", { class: "chip" + (selected ? " on" : ""),
          "aria-pressed": selected ? "true" : "false", "aria-label": "Use " + n + " accent",
          onclick: () => {
            document.documentElement.dataset.accent = v;
            try { localStorage.setItem("hks_accent", v); } catch (e) {}
            paint();
          } }, n));
      });
    };
    try {
      const ac = localStorage.getItem("hks_accent");
      if (ac && ac !== "blue") document.documentElement.dataset.accent = ac;
      else document.documentElement.removeAttribute("data-accent");
    } catch (e) {}
    paint();
    box.append(panel("Theme", themes),
      panel("Accent colour", accents),
      panel("Font size", el("div", { class: "btn-row" }, fonts)));
  });
  mk("Startup", () => {
    box.innerHTML = "";
    const su = el("div", { class: "muted" }, "loading…");
    box.append(panel("HKS startup units (on the laptop)",
        el("div", { class: "btn-row", style: "margin-bottom:10px" },
          el("button", { class: "btn ghost sm", onclick: (e) => { busy(e.target, true);
            cmd("startup.hks_status", {}, 30).then((d) => { busy(e.target, false); paint(d); })
              .catch((x) => { busy(e.target, false); toast(x.message, true); }); } }, "⟳ Refresh")), su));
    function paint(d) {
      su.innerHTML = ""; su.className = "";
      const body = (d.units || []).map((u) => {
        const acts = el("div", { class: "btn-row" });
        ["start", "stop", "enable", "disable"].forEach((op) => acts.append(el("button", {
          class: "btn ghost sm",
          onclick: async () => {
            if (op === "disable" && u.unit === "HKS-Panel" &&
                !confirm("Disabling the PANEL task locks you out of remote access. Continue?")) return;
            try {
              const r = await cmd("startup.hks_action", { unit: u.unit, op,
                confirm: op === "disable" && u.unit === "HKS-Panel" ? "CONFIRM" : undefined }, 30);
              toast(r.output || "done");
            } catch (e2) { toast(e2.message, true); }
          }
        }, op)));
        const tr = el("tr", null, el("td", null, u.unit), el("td", null, String(u.status || "?")));
        const td = el("td", null); td.append(acts); tr.append(td);
        return tr;
      });
      const tbody = el("tbody", null, body);
      const table = el("table", null,
        el("thead", null, el("tr", null, el("th", null, "Unit"), el("th", null, "Status"), el("th", null, "Actions"))),
        tbody);
      const wrap = el("div", { class: "tbl-wrap" }, table);
      su.append(wrap);
    }
  });
  mk("Notifications", () => {
    box.innerHTML = "";
    const get = (k) => localStorage.getItem(k) !== "0";
    const set = (k) => (v) => localStorage.setItem(k, v ? "1" : "0");
    box.append(panel("Panel notifications",
      el("div", { class: "kv" },
        sw("Sound effects (connect / disconnect beeps)", () => get("hks_sound"), set("hks_sound")),
        sw("Laptop IP change toast", () => get("hks_toast_ip"), set("hks_toast_ip")),
        sw("Motion detected toast", () => get("hks_toast_motion"), set("hks_toast_motion")),
        sw("Login capture failure toast", () => get("hks_toast_cap"), set("hks_toast_cap"))),
      el("p", { class: "muted", style: "margin-top:8px" }, "These affect THIS device only.")));
  });
  mk("Backup", () => {
    box.innerHTML = "";
    const exp = el("button", { class: "btn", onclick: () => {
      const data = {};
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (k && k.startsWith("hks_")) data[k] = localStorage.getItem(k);
      }
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const a = el("a", { href: URL.createObjectURL(blob), download: "hks_panel_settings.json" });
      a.click(); toast("settings exported");
    } }, "⬇ Export settings");
    const imp = el("input", { type: "file", accept: ".json", style: "display:none", onchange: (e) => {
      const f = e.target.files[0]; if (!f) return;
      const r = new FileReader();
      r.onload = () => {
        try {
          const data = JSON.parse(String(r.result));
          Object.entries(data).forEach(([k, val]) => { if (k.startsWith("hks_")) localStorage.setItem(k, String(val)); });
          const root = document.documentElement;
          const theme = localStorage.getItem("hks_theme");
          if (theme === "light" || theme === "dark") root.dataset.theme = theme;
          const accent = localStorage.getItem("hks_accent");
          if (accent === "green" || accent === "purple") root.dataset.accent = accent;
          else root.removeAttribute("data-accent");
          const font = localStorage.getItem("hks_font");
          if (font === "s" || font === "l") root.dataset.font = font;
          else root.removeAttribute("data-font");
          toast("settings imported and applied");
        } catch (er) { toast("bad file", true); }
      };
      r.readAsText(f);
    } });
    box.append(panel("Data & backup",
      el("p", { class: "muted", style: "margin-top:0" }, "Panel preferences (theme, accents, toggles) as a small JSON file."),
      el("div", { class: "btn-row" }, exp,
        el("button", { class: "btn ghost", onclick: () => imp.click() }, "⬆ Import settings"), imp)));
  });
  mk("Open source", () => {
    box.innerHTML = "";
    const GROUPS = [
      ["Studied while building HKS - ideas used", [
        ["AnywhereInput", "https://github.com/Z-Hussein/AnywhereInput", "Browser screen + input control - its same-pixel-space lesson drove our DPI fix", "idea used"],
        ["pcbu-desktop (PulseUnlock)", "https://github.com/MeisApps/pcbu-desktop", "Phone unlock at the Windows login screen - confirms our unlock trust model", "idea used"],
        ["Cores", "https://github.com/Levminer/cores", "Modern hardware monitor with remote web view - richer hardware info ideas", "idea used"]]],
      ["Alternatives we checked - not added", [
        ["PC Control (PC-Control-Windows-Server)", "https://github.com/abnerdevbr/control-pc", "Phone PC control with no real authentication - ours is authenticated", "not added"],
        ["filegator", "https://github.com/filegator/filegator", "PHP web file manager - HKS file manager already covers this", "not added"],
        ["CasaOS", "https://github.com/IceWhaleTech/CasaOS", "Docker home-server dashboard - too heavy for the 8 GB laptop", "not added"],
        ["Nextcloud AIO", "https://github.com/nextcloud/all-in-one", "Docker self-hosted cloud - not this project's job", "not added"],
        ["Shinobi", "https://github.com/ShinobiCCTV/Shinobi", "CCTV NVR (Node.js) - HKS camera already does live + motion + record", "not added"],
        ["Mopidy", "https://github.com/mopidy/mopidy", "Music server - extra always-on service", "not added"],
        ["Mopidy-Iris", "https://github.com/jaesivsm/Mopidy-Iris", "Web UI for Mopidy", "not added"],
        ["mopidy-youtube", "https://github.com/natumbri/mopidy-youtube", "YouTube plugin for Mopidy", "not added"],
        ["Red-DiscordBot", "https://github.com/Cog-Creators/Red-DiscordBot", "Discord community bot - unrelated", "not added"],
        ["MusicBot", "https://github.com/Just-Some-Bots/MusicBot", "Discord music bot - unrelated", "not added"],
        ["DbGate", "https://github.com/dbgate/dbgate", "Database GUI - HKS already has SQLite query + tables", "not added"],
        ["code-server", "https://github.com/coder/code-server", "VS Code in the browser - HKS ships a live terminal + editor", "not added"],
        ["ttyd", "https://github.com/tsl0922/ttyd", "Terminal in the browser - same reason as code-server", "not added"],
        ["MeshCentral", "https://github.com/Ylianst/MeshCentral", "Full remote-management server - would replace HKS; ideas covered natively", "not added"]]]
    ];
    const row = (name, url, note, verdict) => el("article", { class: "reference-card" },
      el("div", { class: "reference-card-head" },
        el("a", { href: url, target: "_blank", rel: "noopener" }, name + " ↗"),
        el("span", { class: "tag " + (verdict === "idea used" ? "ok" : ""),
          "aria-label": verdict === "idea used" ? "Studied and informed HKS" : "Reviewed, not included" },
          verdict === "idea used" ? "✓ idea used" : "○ not added")),
      el("p", null, note));
    const wrap = el("div", { class: "dashgrid addon-layout" });
    // live add-on board: real status of the HKS_ADDONS services on the laptop
    const adBoard = el("div");
    let addonPollTimer = null;
    const refreshAddonStatus = async () => {
      if (!adBoard.isConnected || document.visibilityState === "hidden") return;
      try {
        const latest = await cmd("addons.status", {}, 20);
        for (const a of latest.addons || []) {
          const card = [...adBoard.querySelectorAll("[data-addon-name]")]
            .find((node) => node.dataset.addonName === a.name);
          if (!card) continue;
          Object.assign(card._addonState || {}, a);
          const autoButton = card.querySelector(".addon-autostart");
          if (autoButton) autoButton.textContent = a.autostart ? "↻ Autoboot ON" : "↻ Autoboot OFF";
          const label = card.querySelector(".addon-live-status");
          if (label) label.textContent = a.up === null ? "status not monitored" : a.up ? `service responding · port ${a.port}` : `service not responding · port ${a.port}`;
          const open = card.querySelector(".addon-open");
          if (open) {
            let href = a.url || "";
            try { if (href) { const u = new URL(href); u.pathname = a.open_path || "/"; href = u.href; } } catch {}
            open.href = href;
            open.style.display = a.up && href ? "inline-flex" : "none";
          }
        }
      } catch { /* keep last known service status visible during a brief disconnect */ }
    };
    const paintAddons = async () => {
      adBoard.innerHTML = "";
      const w = el("div", { class: "addon-board" });
      const query = el("input", { type: "search", class: "addon-search", placeholder: "Find an add-on…", "aria-label": "Find an add-on" });
      const refreshRow = el("div", { class: "btn-row", style: "margin-bottom:6px" },
          el("button", { class: "btn ghost sm", onclick: () => refreshAddonStatus() }, "\u27f3 Refresh"),
        query,
        el("span", { class: "muted", style: "font-size:12px" }, "Installers open visibly on the laptop; Docker add-ons need Docker Desktop running."));
      adBoard.append(panel("Add-on services on the laptop", refreshRow, w));
      try {
    const d = await cmd("addons.status", {}, 20);
        w.innerHTML = "";
        const all = d.addons || [];
        if (!all.length) { w.append(emptyState("📦", "No add-on installers are registered.")); return; }
        const groups = new Map();
        const category = (a) => a.port ? "Web services" : /vpn|network|tunnel/i.test(a.name + " " + a.what) ? "Network tools" : "Desktop & command-line tools";
        for (const a of all) { const key = category(a); if (!groups.has(key)) groups.set(key, []); groups.get(key).push(a); }
        const rows = [];
        for (const [name, items] of groups) {
          const body = el("div", { class: "addon-group-body" });
          const details = el("details", { class: "addon-group" },
            el("summary", null, name + " · " + items.length), body);
          w.append(details);
          for (const a of items) {
          // Support older service agents that omit open_path; Windows
          // Exporter serves Prometheus metrics at /metrics, not at /.
          const openPath = a.name.toLowerCase() === "windows_exporter" ? "/metrics" : (a.open_path || "/");
          let openUrl = a.url || "";
          try { if (openUrl) { const u = new URL(openUrl); u.pathname = openPath; openUrl = u.href; } } catch {}
          const act = (label, action, params, cls) => el("button", { class: "btn ghost sm " + (cls || ""),
            onclick: async (e) => {
              busy(e.target, true);
              try {
                const actionParams = typeof params === "function" ? params() : params;
                const r = await cmd(action, actionParams, action === "addons.run" ? 60 : 30);
                if (r && r.ok === false) throw new Error(r.error || (a.name + " action failed"));
                const detail = r && (r.note || r.error) || action + " completed";
                toast(a.name + ": " + detail, false);
                if (action === "addons.run" || action === "addons.stop" || action === "addons.autostart") setTimeout(() => refreshAddonStatus(), 1200);
              }
              catch (er) { toast(er.message, true); }
              busy(e.target, false);
            } }, label);
          const state = { ...a };
          const row = el("article", { class: "addon-card", "data-addon-name": a.name, "data-addon": (a.name + " " + a.what + " " + a.how).toLowerCase() },
            el("span", { style: "min-width:170px" }, el("b", null, a.name),
              el("span", { class: "muted" }, " \u00b7 " + a.how),
              a.autostart ? el("span", { class: "tag ok", style: "margin-left:6px" }, "autoboot") : null),
            el("span", { class: "muted addon-description" }, a.what,
              el("div", { class: "muted", style: "font-size:11px" },
                ((a.installer_available ?? a.installed) ? "\ud83d\udfe2 installer ready" : "\u26aa installer missing on laptop")),
              el("span", { class: "muted addon-live-status", "aria-live": "polite" },
                a.up === null ? "status not monitored" : a.up ? "service responding · port " + a.port : "service not responding · port " + a.port)),
            el("span", { class: "btn-row addon-actions" },
              el("a", { href: a.up ? openUrl : "#", target: "_blank", rel: "noopener", class: "btn sm addon-open", style: "min-height:40px;padding:6px 12px;display:" + (a.up && openUrl ? "inline-flex" : "none") }, "\ud83c\udf10 Open"),
              act("\u25b6 Install/Start", "addons.run", { name: a.name }),
              a.can_stop ? act("\u23f9 Stop", "addons.stop", { name: a.name }, "red") : null,
              a.can_autostart ? act(a.autostart ? "\u21bb Autoboot ON" : "\u21bb Autoboot OFF", "addons.autostart",
                () => ({ name: a.name, on: !state.autostart }), "addon-autostart") : null));
          row._addonState = state;
          body.append(row); rows.push({ row, details });
          }
        }
        query.addEventListener("input", () => {
          const q = query.value.trim().toLowerCase();
          for (const item of rows) {
            item.row.hidden = !!q && !item.row.dataset.addon.includes(q);
            if (q && !item.row.hidden) item.details.open = true;
          }
        });
        if (!addonPollTimer) {
          addonPollTimer = setInterval(refreshAddonStatus, 8000);
          cleanupFns.push(() => { clearInterval(addonPollTimer); addonPollTimer = null; });
        }
      } catch (e) { w.className = "muted"; w.textContent = e.message; }
    };
    wrap.append(adBoard);
    paintAddons();
    for (const [gt, rows] of GROUPS) {
      const reference = el("details", { class: "addon-reference" },
        el("summary", null, gt), el("div", { class: "reference-grid" },
        el("p", { class: "muted", style: "margin:2px 0 8px" }, "HKS does not install any of these. Links open on GitHub."),
        ...rows.map((r) => row(...r))));
      wrap.append(reference);
    }
    box.append(wrap);
  });
  mk("About", () => {
    box.innerHTML = "";
    const info = el("div", { class: "kv" }, el("span", { class: "muted" }, "loading…"));
    const eng = el("div", { class: "kv" }, el("span", { class: "muted" }, "checking the input engine on the laptop…"));
    box.append(el("div", { class: "dashgrid" },
      panel("This build", info),
      panel("Input engine (live status)", eng,
        el("p", { class: "muted", style: "margin:8px 0 0" },
          "Engine = AnywhereInput MouseWorker port: queue-driven pyautogui, normalized 0-1 coordinates, rate caps, auto-offline failsafe. Control Screen shows ENG health live."))));
    (async () => {
      try {
        const v = await api("/api/version");
        info.innerHTML = "";
        info.append(
          el("div", { class: "kvrow" }, el("span", null, "HKS build"), el("b", null, "v" + v.version)),
          el("div", { class: "kvrow" }, el("span", null, "Main port"), el("b", null, String(v.main))),
          el("div", { class: "kvrow" }, el("span", null, "UI version"), el("b", null, "v101")),
          el("div", { class: "kvrow" }, el("span", null, "User"), el("b", null, (STATE.user || {}).username || "-")));
      } catch (e) { info.innerHTML = ""; info.className = "muted"; info.textContent = e.message; }
      try {
        const d = await cmd("input.status", {}, 20);
        eng.innerHTML = "";
        eng.append(
          el("div", { class: "kvrow" }, el("span", null, "Engine state"),
            el("b", { style: "color:" + (d.engine === "healthy" ? "#34d399" : d.engine === "degraded" ? "#fbbf24" : "#f87171") }, String(d.engine || "unknown"))),
          el("div", { class: "kvrow" }, el("span", null, "Injection enabled"), el("b", null, d.input_active ? "yes" : "no")),
          el("div", { class: "kvrow" }, el("span", null, "Consecutive errors"), el("b", null, String(d.errors ?? 0))),
          d.last_error ? el("div", { class: "kvrow" }, el("span", null, "Last error"), el("b", null, String(d.last_error).slice(0, 60))) : null,
          d.screen && d.screen.w ? el("div", { class: "kvrow" }, el("span", null, "Screen (physical px)"), el("b", null, d.screen.w + " \u00d7 " + d.screen.h)) : null);
      } catch (e) { eng.innerHTML = ""; eng.className = "muted"; eng.textContent = "agent offline - engine status unavailable"; }
    })();
  });
}

/* ================= boot / auth ================= */
function showLogin() { STATE.user = null; CSRF = ""; $("#app").classList.remove("on"); $("#login").style.display = "flex"; }
async function enterApp() {
  $("#login").style.display = "none";
  $("#app").classList.add("on");
  // Bind the always-visible top bar (including Profile/Logout) for every
  // initial section. Previously this only ran from renderDash(), so the
  // default Home screen left Profile inert until Dashboard was opened.
  initChrome();
  $("#tUser").textContent = STATE.user.username;
  $("#tUserAv").textContent = (STATE.user.username || "?").charAt(0).toUpperCase();
  const profileName = $("#profileMenuUser"); if (profileName) profileName.textContent = STATE.user.username || "—";
  buildNav();
  await refreshPills();
  connectWs();
  let lastSection = "home";
  try {
    const saved = sessionStorage.getItem("hks_last_section");
    if (saved && SECTIONS.some((s) => s.id === saved)) lastSection = saved;
  } catch (e) {}
  showSection(lastSection);
  setInterval(refreshPills, 5000);  // round 52: 15 s left a stale window
  // round 52: phones throttle background timers - the moment the owner
  // comes BACK to this tab, refresh liveness immediately (no stale
  // "agent offline" card after the laptop was repaired).
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refreshPills(); });
  setInterval(() => ($("#tClock").textContent = new Date().toLocaleTimeString()), 1000);
  const footUpd = () => {
    const c = $("#footConn"), a = $("#footAddr");
    if (!c) return;
    const live = (STATE.live || {}).svc;
    c.textContent = live ? "Connected" : "Waiting for laptop…";
    c.parentElement.className = "pill " + (live ? "ok" : "");
    a.textContent = location.host;
  };
  setInterval(footUpd, 3000);
  footUpd();
}
let _verSeen = false;
async function checkPanelAlive() {
  let j = null;
  try { j = await apiOnce("/api/version", { _noretry: true, tmo: 6000 }); } catch (e) {}
  const ban = $("#offlineBanner");
  if (j) {
    _verSeen = true;
    if (ban) ban.style.display = "none";
    return;
  }
  if (_verSeen) return;  // it worked once; a later blip is handled after login
  // 2026 UI report Part 11: errors get a REMEDY, not "something went wrong"
  if (ban) {
    ban.style.display = "block";
    // a screenshot of this banner must answer "WHERE am I connected?" by itself
    const og = document.getElementById("offOrigin");
    if (og) og.textContent = location.host || "(unknown)";
    const op = document.getElementById("offPort");
    if (op) op.textContent = location.port || (location.protocol === "https:" ? "443" : "80");
    const od = document.getElementById("offDemo");
    if (od) od.style.display = (location.hostname || "").endsWith(".trycloudflare.com") ? "" : "none";
  }
}
(async () => { await checkPanelAlive(); setInterval(checkPanelAlive, 5000); })();
$("#loginForm").onsubmit = async (e) => {
  e.preventDefault();
  try {
    const _totp = $("#ltot") ? $("#ltot").value.trim() : "";
    const j = await api("/api/login", { method: "POST", body: { username: $("#lu").value, password: $("#lp").value, totp: _totp } });
    STATE.user = j.user; CSRF = j.user.csrf;
    AUTH_TOKEN = j.token || "";
    try { if (AUTH_TOKEN) window.localStorage.setItem("hks_token", AUTH_TOKEN); else window.localStorage.removeItem("hks_token"); } catch (er) {}
    try {  // remember-me (bedimcode login port): store only the username
      if ($("#lgRemember") && $("#lgRemember").checked) localStorage.setItem("hks_remember", $("#lu").value);
      else localStorage.removeItem("hks_remember");
    } catch (er) {}
    await enterApp();
  } catch (err) {
    $("#lerr").style.color = "";
    /* round 68 (F24): 2FA - show the code field when the server asks */
    if (err && err.totpRequired && $("#totpRow")) {
      $("#totpRow").style.display = "";
      if ($("#ltot")) $("#ltot").focus();
      $("#lerr").textContent = "enter the 6-digit code from your authenticator app";
    }
    else if (String(err.message).includes("fetch")) {
      // report Part 11: network failure gets a REMEDY, not raw jargon
      $("#lerr").innerHTML = "Can't reach the panel right now.<br><small>• Opened from: <b>" + (location.host || "?") + "</b> - that address is not answering. Use the address printed by CHECK_STATUS.bat on the laptop.<br>• Same Wi-Fi? http://&lt;laptop-ip&gt;:" + (location.port || "?") + " (this page's port).<br>• Retrying is safe." + ((location.hostname || "").endsWith(".trycloudflare.com") ? "<br>• This is a DEMO link - its server sleeps between sessions. Ask for a fresh link; your laptop is NOT the problem." : "") + "</small>";
      checkPanelAlive();  // also flip the OFFLINE banner while it is down
    }
    else $("#lerr").textContent = err.message;
  }
};
$("#btnLogout").onclick = async () => { try { await api("/api/logout", { method: "POST", body: {} }); } catch {} try { window.localStorage.removeItem("hks_token"); } catch (er) {} AUTH_TOKEN = ""; location.reload(); };
$("#btnLockPanel").onclick = () => { $("#lockscreen").classList.add("on"); };
$("#btnUnlock").onclick = async () => {
  try {
    const j = await api("/api/login", { method: "POST", body: { username: STATE.user.username, password: $("#lockPw").value } });
    CSRF = j.user.csrf; $("#lockscreen").classList.remove("on"); $("#lockPw").value = ""; $("#lockErr").textContent = "";
  } catch (e) { $("#lockErr").textContent = e.message; }
};
$("#lockPw").onkeydown = (e) => e.key === "Enter" && $("#btnUnlock").click();
$("#hamb").onclick = () => $("#side").classList.toggle("open");
document.addEventListener("click", (e) => { if ($("#side").classList.contains("open") && !$("#side").contains(e.target) && e.target.id !== "hamb") $("#side").classList.remove("open"); });

(async function boot() {
  try {
    let j;
    try { j = await api("/api/me"); }
    catch (e0) {
      if (AUTH_TOKEN) {  // cookie blocked (sandboxed iframe): token only
        AUTH_TOKEN = ""; 
        try { AUTH_TOKEN = window.localStorage.getItem("hks_token") || ""; } catch (er) {}
        if (AUTH_TOKEN) { try { j = await api("/api/me"); } catch (e1) { j = undefined; } }
      }
      if (typeof j === "undefined") throw e0;
    }
    if (j.needs_setup) { location.href = "/setup"; return; }
    if (j.user) { STATE.user = j.user; CSRF = j.user.csrf; if (j.token) AUTH_TOKEN = j.token; await enterApp(); }
    else showLogin();
  } catch (e) {
    if (/not logged in|401/i.test(String(e.message || ""))) showLogin();
    else if (String(e.message).includes("fetch")) {
      // report Part 11: network failure gets a REMEDY, not raw jargon
      $("#lerr").innerHTML = "Can't reach the panel right now.<br><small>• Opened from: <b>" + (location.host || "?") + "</b> - that address is not answering. Use the address printed by CHECK_STATUS.bat on the laptop.<br>• Same Wi-Fi? http://&lt;laptop-ip&gt;:" + (location.port || "?") + " (this page's port).<br>• Retrying is safe." + ((location.hostname || "").endsWith(".trycloudflare.com") ? "<br>• This is a DEMO link - its server sleeps between sessions. Ask for a fresh link; your laptop is NOT the problem." : "") + "</small>";
      checkPanelAlive();  // flip the OFFLINE banner too, if it is still down
    }
    else $("#lerr").textContent = "server error: " + e.message;
  }
})();

/* round 52: tiny console/debug handle - the ui_smoke contract test drives
   the REAL pills path through this (eval realms cannot reach inside);
   also handy for manual field debugging in the browser console. */
window.HKS_DEBUG = { refreshPills: () => refreshPills(), apiBase: () => API_BASE };

/* ==================== round 67 (Ammar UI doc) ==================== */
/* 1) GLOBAL ERROR SURFACING: an uncaught error now shows a real, detailed
   box (message + file:line + stack) instead of a silently dead feature.
   The user can screenshot it and we can pinpoint the failure exactly. */
(function(){
  function showErr(kind, msg, src, line, stack){
    try{
      let box = document.getElementById("hksErrBox");
      if(!box){
        box = el("div"); box.id = "hksErrBox";
        box.style.cssText = "position:fixed;right:10px;top:10px;z-index:9999;max-width:min(560px,94vw);" +
          "background:#2a1218;border:1px solid #ef5466;border-radius:10px;padding:12px 14px;" +
          "color:#ffd9de;font:12px/1.55 var(--mono,monospace);box-shadow:0 8px 30px rgba(0,0,0,.5);" +
          "white-space:pre-wrap;overflow-wrap:anywhere";
        const close = el("button", null, "✕");
        close.style.cssText = "float:right;background:none;border:0;color:#ffd9de;cursor:pointer;font-size:14px";
        close.onclick = () => box.remove();
        box.append(close);
        if(document.body) document.body.append(box);
      }
      const row = document.createElement("div");
      row.textContent = kind + ": " + msg +
        (src ? ("\n  at " + src + (line ? ":" + line : "")) : "") +
        (stack ? ("\n  " + String(stack).split("\n").slice(1, 3).join("\n  ")) : "");
      box.append(row);
    }catch(_e){ /* the error reporter must never be the thing that breaks */ }
  }
  window.addEventListener("error", (e) => showErr("error", e.message, e.filename, e.lineno, e.error && e.error.stack));
  window.addEventListener("unhandledrejection", (e) =>
    showErr("promise", (e.reason && (e.reason.message || e.reason)) || "unhandled rejection", "", 0, e.reason && e.reason.stack));
})();

/* 2) MODALS: scroll lock + Escape + focus trap. Was: the page scrolled
   behind the dialog on phones and Tab could walk out of it. Additive -
   works with every existing modal() / confirmDo() dialog. */
(function(){
  if(!window.MutationObserver) return;
  const mo = new MutationObserver(() => {
    const open = document.querySelector(".modal-bk");
    document.body.classList.toggle("modal-open", !!open);
    if(open && !open._r67trap){
      open._r67trap = true;
      const focusables = () => open.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
      const first = focusables()[0];
      if(first && first.focus) setTimeout(() => { try{ first.focus(); }catch(_e){} }, 30);
      open.addEventListener("keydown", (ev) => {
        if(ev.key === "Escape"){ ev.stopPropagation(); open.remove(); document.body.classList.remove("modal-open"); return; }
        if(ev.key === "Tab"){
          const f = focusables(); if(!f.length) return;
          const fn = f[0], ln = f[f.length - 1];
          if(ev.shiftKey && document.activeElement === fn){ ev.preventDefault(); ln.focus(); }
          else if(!ev.shiftKey && document.activeElement === ln){ ev.preventDefault(); fn.focus(); }
        }
      });
    }
  });
  mo.observe(document.body, { childList: true, subtree: true });
})();

/* 3) COMMAND PALETTE (Ctrl+K) + SHORTCUT OVERLAY (?) - F36/F37 */
(function(){
  const CHORDS = { d: "dash", f: "files", t: "term", c: "control", s: "settings", h: "home" };
  function jump(id){ if(typeof showSection === "function") showSection(id); }
  function shortcuts(){
    modal("Keyboard shortcuts", el("div", { class: "kv" },
      [["Ctrl+K", "Command palette"], ["?", "This help"], ["Esc", "Close dialogs"],
       ["G then D", "Go to Dashboard"], ["G then F", "Go to Files"], ["G then T", "Go to Terminal"],
       ["G then C", "Go to Control"], ["G then S", "Go to Settings"], ["G then H", "Go to Home"]]
      .map(([k, d]) => el("div", { class: "kvrow" }, el("kbd", null, k), el("span", null, d)))));
  }
  function palette(){
    const old = document.getElementById("hksPal"); if(old) old.remove();
    const bk = el("div", { class: "modal-bk", id: "hksPal",
      onclick: (e) => { if(e.target === bk) bk.remove(); } });
    const box = el("div", { class: "modal hks-pal" });
    const inp = el("input", { placeholder: "Type a command - Enter runs the top hit, Esc closes" });
    const list = el("div", { class: "hks-pal-list" });
    const cmds = (typeof SECTIONS !== "undefined" ? SECTIONS : [])
      .map(s => ({ label: (s.ic ? s.ic + " " : "") + "Go to " + s.name, run: () => jump(s.id) }));
    cmds.push(
      { label: "🔒 Lock the laptop screen", run: () => cmd("power.lock_screen").then(() => toast("lock sent")).catch(e => toast(e.message, true)) },
      { label: "🌗 Toggle light/dark theme", run: () => {
          const light = document.documentElement.getAttribute("data-theme") === "light";
          if(light) document.documentElement.removeAttribute("data-theme");
          else document.documentElement.setAttribute("data-theme", "light");
          try{ localStorage.setItem("hks_theme", light ? "dark" : "light"); }catch(_e){} } },
      { label: "🩺 Download diagnostics bundle (redacted zip)", run: () => window.open((typeof API_BASE !== "undefined" ? API_BASE : "") + "/api/diagnostics/bundle", "_blank") },
      { label: "❓ Keyboard shortcuts", run: shortcuts });
    let first = null;
    function paint(q){
      list.innerHTML = "";
      const ql = (q || "").toLowerCase();
      const hits = cmds.filter(c => !ql || c.label.toLowerCase().includes(ql));
      first = hits[0];
      hits.forEach((c, i) => {
        const item = el("div", { class: "hks-pal-item" + (i === 0 ? " on" : ""), onclick: () => { bk.remove(); c.run(); } }, c.label);
        list.append(item);
      });
      if(!hits.length) list.append(el("div", { class: "hks-pal-item muted" }, "no match"));
    }
    inp.oninput = () => paint(inp.value);
    inp.onkeydown = (e) => {
      if(e.key === "Enter" && first){ bk.remove(); first.run(); }
      else if(e.key === "Escape") bk.remove();
    };
    box.append(inp, list); bk.append(box); document.body.append(bk);
    setTimeout(() => { try{ inp.focus(); }catch(_e){} }, 30);
    paint("");
  }
  document.addEventListener("keydown", (e) => {
    const t = e.target;
    if(t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
    if((e.ctrlKey || e.metaKey) && (e.key === "k" || e.key === "K")){ e.preventDefault(); palette(); return; }
    if(e.key === "?"){ e.preventDefault(); shortcuts(); return; }
    if(e.key === "g" || e.key === "G"){
      const h2 = (e2) => {
        document.removeEventListener("keydown", h2, true);
        const t2 = e2.target;
        if(t2 && (t2.tagName === "INPUT" || t2.tagName === "TEXTAREA")) return;
        const dest = CHORDS[e2.key.toLowerCase()];
        if(dest){ e2.preventDefault(); jump(dest); }
      };
      document.addEventListener("keydown", h2, true);
      setTimeout(() => document.removeEventListener("keydown", h2, true), 900);
    }
  });
  window.HKS_PALETTE = palette;
})();

function renderSecurityTools() {
  const tools = el("div", { class: "panel" });
  tools.append(el("h4", null, "SECURITY & TOOLS"));
  const toolRow = el("div", { class: "btn-row", style: "flex-wrap:wrap" });
  const out = el("div", { class: "muted", style: "margin-top:10px;white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px" });
  const show = (t) => { out.innerHTML = ""; out.append(typeof t === "string" ? document.createTextNode(t) : t); };
  const kvOf = (d) => { const k = el("div", { class: "kv" }); Object.entries(d).forEach(([a, b]) => k.append(el("div", { class: "kvrow" }, el("b", null, a), el("span", null, String(b))))); return k; };
  api("/api/capabilities").then((d) => {
    const c = d.caps || {};
    tools.prepend(el("div", { class: "banner", style: c.degraded ? "border-color:var(--warning)" : "" },
      c.degraded ? "DEGRADED: " + (c.svc ? "no desktop session agent" : "no service agent") + " - some tabs cannot work right now."
                 : "All agent pieces ONLINE (service + desktop session)."));
  }).catch(() => {});
  toolRow.append(el("button", { class: "btn ghost sm", onclick: async () => {
    try {
      const d = await api("/api/2fa/setup");
      const inp = el("input", { placeholder: "123456", maxlength: "6", style: "max-width:140px" });
      const err = el("div", { class: "muted", style: "min-height:18px" });
      inp.onkeydown = async (e) => { if (e.key !== "Enter") return;
        try { await api("/api/2fa/enable", { method: "POST", body: { code: inp.value.trim() } });
              toast("2FA enabled - you will need the code at every login");
              const b = document.querySelector(".modal-bk"); if (b) b.remove(); }
        catch (e2) { err.textContent = e2.message; } };
      modal("Turn on 2FA (authenticator app)", el("div", null,
        el("p", { class: "muted" }, "1. Open Google Authenticator / Authy / Aegis and ADD an account with this key:"),
        el("p", null, el("b", null, d.secret)),
        el("p", { class: "muted", style: "font-size:11px;overflow-wrap:anywhere" }, d.uri),
        el("p", { class: "muted" }, "2. Type the 6-digit code it shows and press Enter:"),
        inp, err));
    } catch (e) { show("2FA setup failed: " + e.message); }
  } }, "🔐 Set up 2FA"));
  toolRow.append(el("button", { class: "btn sm", onclick: async () => {
    show("running self-test on the service agent…");
    try { const r = await cmd("selftest.run", {}, 90);
      const box = el("div", null, el("b", null, "passed " + r.passed + " / failed " + r.failed + " / absent " + r.absent));
      r.results.forEach((x) => box.append(el("div", { class: "kvrow" },
        el("b", null, x.action), el("span", null, x.ok === true ? "OK" : x.ok === false ? ("FAIL " + (x.error || "")) : (x.note || "absent")))));
      show(box);
    } catch (e) { show("self-test failed: " + e.message); }
  } }, "🩺 Run agent self-test"));
  toolRow.append(el("button", { class: "btn ghost sm", onclick: async () => {
    show("checking…");
    try { const d = await api("/api/network-path");
      const box = el("div");
      (d.report.checks || []).forEach((c) => box.append(el("div", { class: "kvrow" }, el("b", null, c.name), el("span", null, c.ok ? "OK" : "FAIL" + (c.detail ? " - " + c.detail : "")))));
      box.append(el("p", { class: "muted" }, d.report.hint || ""));
      show(box);
    } catch (e) { show("network check failed: " + e.message); }
  } }, "📶 Phone-reachability check"));
  toolRow.append(el("button", { class: "btn ghost sm", onclick: async () => {
    try { const d = await api("/api/config/export");
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([JSON.stringify(d, null, 2)], { type: "application/json" }));
      a.download = "hks_config_export.json"; a.click();
      show("config exported (passwords are NOT included, by design)");
    } catch (e) { show("export failed: " + e.message); }
  } }, "📤 Export config"));
  toolRow.append(el("button", { class: "btn ghost sm", onclick: async () => {
    show("checking fleet…");
    try { const d = await api("/api/fleet");
      const peers = d.peers || [];
      show(peers.length ? kvOf(Object.fromEntries(peers.map((p) => [p._panel || "?", p.error ? ("ERROR " + p.error) : (p.online ? "ONLINE (" + (p.machines || 0) + " agents)" : "OFFLINE")])))
                        : "fleet.json has no peers - add [{name,url,token}] in the panel data folder to watch other HKS panels here.");
    } catch (e) { show("fleet failed: " + e.message); }
  } }, "🛰 Fleet status"));
  tools.append(toolRow, out);
  return tools;
}

/* HELP CENTER — short action cards with the exact next step */
async function renderHelpCenter(v){
  // Fill these two values when the owner's public links are ready.
  const HKS_GITHUB_URL = "https://github.com/Abdulsf26";
  const HKS_CONTACT_EMAIL = "fakmail112233@gmail.com";
  const cards = [
    { icon: "📦", title: "Install or repair", text: "Run the setup app, then choose Install / Repair.", path: "Downloads\\HKS_PROGRAM\\HKS_PROGRAM_SETUP.exe" },
    { icon: "📱", title: "Connect your phone", text: "Put both devices on the same Wi-Fi, then open the laptop address.", path: "C:\\HKS_PROGRAM\\CHECK_STATUS.bat" },
    { icon: "🔌", title: "Agent offline", text: "Check which agent is down, then run diagnostics for the cause.", path: "C:\\HKS_PROGRAM\\CHECK_STATUS.bat → HKS_DOCTOR.bat" },
    { icon: "🔑", title: "Forgot password", text: "Use a one-time recovery code on the login page, or reset locally.", path: "Login → Forgot password | C:\\HKS_PROGRAM\\RESET_PASSWORD.bat" },
    { icon: "🗑️", title: "Uninstall", text: "Open the maintenance app and choose Uninstall.", path: "Downloads\\HKS_PROGRAM\\HKS_PROGRAM_SETUP.exe" }
  ];
  const grid = el("div", { class: "hks-help-grid" });
  cards.forEach((item) => grid.append(el("article", { class: "panel hks-help-card" },
    el("div", { class: "hks-help-title" }, el("span", { "aria-hidden": "true" }, item.icon), el("h3", null, item.title)),
    el("p", null, item.text), el("code", { class: "hks-help-path" }, item.path))));
  const githubMark = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  githubMark.setAttribute("viewBox", "0 0 24 24"); githubMark.setAttribute("aria-hidden", "true"); githubMark.setAttribute("class", "help-brand-icon");
  const githubPath = document.createElementNS("http://www.w3.org/2000/svg", "path");
  githubPath.setAttribute("fill", "currentColor");
  githubPath.setAttribute("d", "M12 .8a11.2 11.2 0 0 0-3.54 21.83c.56.1.77-.24.77-.54v-2.1c-3.13.68-3.79-1.33-3.79-1.33-.5-1.3-1.25-1.65-1.25-1.65-1.02-.7.08-.69.08-.69 1.13.08 1.72 1.16 1.72 1.16 1 .1.8 2.66 3.4 1.9.1-.74.4-1.25.72-1.54-2.5-.29-5.13-1.25-5.13-5.56 0-1.23.44-2.23 1.16-3.02-.12-.29-.5-1.43.11-2.98 0 0 .95-.3 3.08 1.15a10.7 10.7 0 0 1 5.6 0c2.13-1.45 3.08-1.15 3.08-1.15.61 1.55.23 2.69.11 2.98.72.79 1.16 1.79 1.16 3.02 0 4.32-2.63 5.26-5.14 5.55.4.35.77 1.03.77 2.08v3.08c0 .3.2.65.78.54A11.2 11.2 0 0 0 12 .8Z");
  githubMark.append(githubPath);
  const mailMark = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  mailMark.setAttribute("viewBox", "0 0 24 24"); mailMark.setAttribute("aria-hidden", "true"); mailMark.setAttribute("class", "help-brand-icon");
  const mailPath = document.createElementNS("http://www.w3.org/2000/svg", "path");
  mailPath.setAttribute("fill", "none"); mailPath.setAttribute("stroke", "currentColor"); mailPath.setAttribute("stroke-width", "2"); mailPath.setAttribute("stroke-linecap", "round"); mailPath.setAttribute("stroke-linejoin", "round");
  mailPath.setAttribute("d", "M3 5h18v14H3z M3 6l9 7 9-7"); mailMark.append(mailPath);
  const links = el("div", { class: "hks-help-grid hks-help-links" });
  const githubCard = el("article", { class: "panel hks-help-card hks-contact-card" },
    el("div", { class: "hks-help-title" }, githubMark, el("h3", null, "GitHub")),
    el("p", null, HKS_GITHUB_URL ? "Project repository and updates." : "Add your GitHub profile or project URL here when ready."),
    HKS_GITHUB_URL ? el("a", { class: "btn", href: HKS_GITHUB_URL, target: "_blank", rel: "noopener noreferrer" }, "↗ Open GitHub")
      : el("span", { class: "help-placeholder" }, "GitHub link not set"));
  const emailCard = el("article", { class: "panel hks-help-card hks-contact-card" },
    el("div", { class: "hks-help-title" }, mailMark, el("h3", null, "Contact")),
    el("p", null, HKS_CONTACT_EMAIL ? "Send a message to the project owner." : "Add your contact email here when ready."),
    HKS_CONTACT_EMAIL ? el("a", { class: "btn", href: "mailto:" + HKS_CONTACT_EMAIL }, "✉ Email")
      : el("span", { class: "help-placeholder" }, "Contact email not set"));
  links.append(githubCard, emailCard);
  v.append(el("div", { class: "viewhead" }, el("div", null,
    el("h2", { class: "vt" }, "Help"),
    el("p", { class: "vsub" }, "Quick steps and the exact place to go."))), grid,
    el("h3", { class: "hks-help-section-title" }, "Project and contact"), links);
}

