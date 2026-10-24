/* UI smoke: boot the REAL dashboard in jsdom, click through EVERY section
   with sparse/empty data — any synchronous render crash fails the test. */
const fs = require("fs");
const path = require("path");
// Resolve the declared test dependency from the workspace's 03_TESTS package.
// This works on Windows and in a zip extracted beside the workspace tests.
const { JSDOM } = require(require.resolve("jsdom", {
  paths: [path.resolve(__dirname, "../../../03_TESTS"), path.resolve(__dirname, ".."), __dirname]
}));

// round 62 (audit5 #3): derive paths - works in the repo AND inside the zip
const _path = path, _fs = require("fs");
const _cands = [
  _path.join(__dirname, "..", "server", "static", "app.js"),
  _path.join(__dirname, "..", "..", "..", "04_SOURCE", "HKS_AdminPanel", "server", "static", "app.js")];
const APP = _cands.find(p => _fs.existsSync(p)) || _cands[0];
const HTML = APP.replace(/app\.js$/, "index.html");

let html = fs.readFileSync(HTML, "utf8")
  .replace(/<script[^>]*src="[^"]*"[^>]*><\/script>/g, ""); // we eval app.js manually

const dom = new JSDOM(html, {
  runScripts: "dangerously",
  url: "http://localhost:8000/",
  pretendToBeVisual: true,
});
const w = dom.window;
let bad = 0;

/* ---- browser stubs app.js may touch ---- */
const noopCtx = new Proxy({}, {
  get: (t, p) => {
    if (p === "canvas") return { width: 300, height: 150 };
    return typeof p === "string" ? (() => noopCtx) : undefined;
  },
  set: () => true,
});
w.HTMLCanvasElement.prototype.getContext = function () { return noopCtx; };
let MACHINES = { ok: true, machines: [], default_machine: null, agent_token_hint: "x…" };
let STORAGE_SUMMARY_FAIL = false;
const COMMAND_CALLS = [];
w.fetch = async (url, opts = {}) => {
  const u = String(url);
  let body = {};
  if (u.includes("/api/me")) body = { ok: true, build: "test", user: { username: "admin", role: "admin", csrf: "t", build: "test" } };
  else if (u.includes("/api/machines")) body = MACHINES;
  else if (u.includes("/api/users")) body = { ok: true,
    users: [{ id: 1, username: "sample-admin", role: "admin", created: 1761000000, last_login: 1761100000 }],
    sessions: [{ username: "sample-admin", ip: "192.0.2.10", created: 1761100000, expires: Date.now() / 1000 + 3600, token: "test-token" }],
    bans: [{ ip: "198.51.100.22", reason: "test ban", until: 1e18 }] };
  else if (u.includes("/api/events")) body = { ok: true, events: u.includes("limit=400")
    ? Array.from({ length: 65 }, (_, i) => ({ id: i + 1, ts: 1761000000 - i, kind: "test_event", data: "{}" })) : [] };
  else if (u.includes("/api/errors")) body = { ok: true, errors: [] };
  else if (u.includes("/api/audit")) body = { ok: true, items: [] };
  else if (u.includes("/api/captures")) body = { ok: true, captures: [] };
  else if (u.includes("/api/storage/summary") && STORAGE_SUMMARY_FAIL) body = { ok: false, error: "administrator login required" };
  else if (u.includes("/api/storage/summary")) body = { ok: true, counts: { captures: 3, events: 5, errors: 2, audit: 4 }, capture_bytes: 4096, panel_log_bytes: 1024 };
  else if (u.includes("/api/cmd")) {
    const request = JSON.parse(opts.body || "{}"); COMMAND_CALLS.push(request.action);
    body = { ok: true, result: { ok: true, data: request.action === "addons.status"
      ? { addons: [{ name: "code-server", port: 8482, up: false, url: "http://127.0.0.1:8482/", open_path: "/", what: "VS Code in the browser", how: "Docker", installer_available: true }] }
      : { ok: true, note: "simulated visible installer launch" } } };
  }
  else if (u.includes("/api/export")) body = { ok: true };
  else body = { ok: true };
  return {
    ok: !(u.includes("/api/storage/summary") && STORAGE_SUMMARY_FAIL), status: u.includes("/api/storage/summary") && STORAGE_SUMMARY_FAIL ? 403 : 200,
    headers: { get: () => "application/json" },
    json: async () => body,
    text: async () => JSON.stringify(body),
    body: null,
  };
};
class FakeWS {
  constructor() { this.readyState = 0; this.handlers = {}; }
  addEventListener(k, fn) { (this.handlers[k] ||= []).push(fn); }
  removeEventListener() {}
  send() {}
  close() { this.readyState = 3; }
}
FakeWS.OPEN = 1; FakeWS.CONNECTING = 0; FakeWS.CLOSED = 3;
w.WebSocket = FakeWS;
w.AudioContext = w.webkitAudioContext = function () {
  return { sampleRate: 48000, state: "running", currentTime: 0,
    createGain: () => ({ connect: () => {}, gain: { value: 1 } }),
    createScriptProcessor: () => ({ connect: () => {}, onaudioprocess: null }),
    createMediaStreamDestination: () => ({ stream: {} }),
    destination: {}, resume: async () => {} };
};
w.Audio = function () { return { play: async () => {}, pause: () => {}, src: "" }; };
if (!w.navigator.mediaDevices) {
  Object.defineProperty(w.navigator, "mediaDevices", { value: { getUserMedia: async () => ({}) } });
}
w.matchMedia = w.matchMedia || (() => ({ matches: false, addListener() {}, removeListener() {} }));
w.requestAnimationFrame = (fn) => setTimeout(fn, 16);
w.URL.createObjectURL = () => "blob:x";
w.alert = () => {}; w.confirm = () => true; w.prompt = () => null;

/* silence console noise but keep errors */
const errors = [];
w.addEventListener("error", (e) => errors.push("window.onerror: " + e.message));

/* ---- run the real app ---- */
const appjs = fs.readFileSync(APP, "utf8");
try {
  w.eval(appjs);
} catch (e) {
  console.log("FAIL: app.js threw during evaluation:", e.message);
  process.exit(1);
}

(async () => {
  await new Promise((r) => setTimeout(r, 300)); // let boot() settle

  // logged in? enterApp should have built the nav
  const navItems = w.document.querySelectorAll(".nav-item");
  if (!navItems.length) { console.log("FAIL: nav not built (login did not proceed)"); process.exit(1); }
  const homeSvgs = [...w.document.querySelectorAll(".cs-widget svg, .hks-kpis svg")];
  if (!homeSvgs.length || homeSvgs.some((svg) => svg.namespaceURI !== "http://www.w3.org/2000/svg" ||
      [...svg.querySelectorAll("circle,line,path,polyline")].some((shape) => shape.namespaceURI !== "http://www.w3.org/2000/svg"))) {
    bad++; console.log("FAIL: dashboard gauges must be rendered in the SVG namespace");
  }
  if (!w.document.querySelector("#gsearch") || w.document.querySelector(".cs-search")) {
    bad++; console.log("FAIL: Home must use the single global search field");
  }
  const metricIcons = [...w.document.querySelectorAll(".hks-kpi-icon svg")];
  if (metricIcons.length !== 2 || metricIcons.some((icon) => icon.getAttribute("width") !== "20" ||
      icon.getAttribute("height") !== "20" || icon.namespaceURI !== "http://www.w3.org/2000/svg")) {
    bad++; console.log("FAIL: CPU/RAM icons must be distinct, equally sized SVGs");
  }

  // click through EVERY section — twice (viewCache path)
  for (const pass of [1, 2]) {
    for (const item of navItems) {
      const name = item.textContent.trim();
      item.dispatchEvent(new w.Event("click", { bubbles: true }));
      const errBox = [...w.document.querySelectorAll(".view")].find((v) =>
        v.style.display !== "none" && v.textContent.includes("render error:"));
      if (errBox) {
        bad++;
        console.log(`FAIL [pass ${pass}] ${name}: ${errBox.textContent.match(/render error: [^"]*/)[0]}`);
      }
      const view = [...w.document.querySelectorAll(".view")].find((v) => v.style.display !== "none");
      if (!view || !view.children.length) {
        bad++; console.log(`FAIL [pass ${pass}] ${name}: empty view`);
      }
    }
  }
  const systemNav = [...navItems].find((n) => n.dataset.id === "system");
  if (systemNav) {
    systemNav.dispatchEvent(new w.Event("click", { bubbles: true }));
    const systemView = [...w.document.querySelectorAll(".view")].find((x) => x.style.display !== "none");
    const coresTab = [...(systemView?.querySelectorAll(".tab") || [])].find((t) => t.textContent.trim() === "Cores");
    if (!coresTab) { bad++; console.log("FAIL: System Cores tab missing"); }
    else {
      coresTab.dispatchEvent(new w.Event("click", { bubbles: true }));
      await new Promise((r) => setTimeout(r, 40));
      const coreCards = systemView.querySelectorAll(".cores-card");
      if (coreCards.length !== 9) { bad++; console.log(`FAIL: expected 9 core cards, found ${coreCards.length}`); }
      const tracks = [...systemView.querySelectorAll(".cores-mini,.cores-segbar")];
      if (!tracks.length || tracks.some((x) => x.getAttribute("role") !== "progressbar" ||
          Number(x.getAttribute("aria-valuenow")) < 0 || Number(x.getAttribute("aria-valuenow")) > 100 || !x.querySelector(":scope > i"))) {
        bad++; console.log("FAIL: core progress bars need visible fill and bounded accessible values");
      }
      const css = fs.readFileSync(HTML.replace(/index\.html$/, "style.css"), "utf8");
      if (!/\.cores-mini\s*>\s*i[^}]*display\s*:\s*block/i.test(css) || !/min\(100%,\s*260px\)/.test(css)) {
        bad++; console.log("FAIL: core cards lack the responsive grid/progress-fill CSS contract");
      }
    }
  } else { bad++; console.log("FAIL: System navigation item missing"); }
  const reportsNav = [...navItems].find((n) => n.dataset.id === "reports");
  if (reportsNav) {
    reportsNav.dispatchEvent(new w.Event("click", { bubbles: true }));
    await new Promise((r) => setTimeout(r, 40));
    const reportView = [...w.document.querySelectorAll(".view")].find((x) => x.style.display !== "none");
    const pager = reportView?.querySelector(".log-pager");
    if (!pager || !pager.textContent.includes("0 entries") || !pager.querySelectorAll("button").length) {
      bad++; console.log("FAIL: Reports log viewer needs a bounded page navigator and empty state");
    }
  } else { bad++; console.log("FAIL: Reports navigation item missing"); }
  const logsNav = [...navItems].find((n) => n.dataset.id === "logs");
  if (logsNav) {
    logsNav.dispatchEvent(new w.Event("click", { bubbles: true }));
    const logsView = [...w.document.querySelectorAll(".view")].find((x) => x.style.display !== "none");
    await new Promise((r) => setTimeout(r, 40));
    const eventPager = logsView?.querySelector(".log-pager");
    if (!eventPager || !eventPager.textContent.includes("Showing 1–30 of 65")) {
      bad++; console.log("FAIL: Logs & Logins events need 30-row pagination");
    } else {
      const older = [...eventPager.querySelectorAll("button")].find((b) => b.textContent.includes("Older"));
      older?.dispatchEvent(new w.Event("click", { bubbles: true }));
      await new Promise((r) => setTimeout(r, 40));
      const nextPage = logsView.querySelector(".log-pager");
      if (!nextPage?.textContent.includes("Showing 31–60 of 65")) {
        bad++; console.log("FAIL: Logs & Logins Older button does not advance to page 2");
      }
    }
    const loginTab = [...(logsView?.querySelectorAll("button") || [])].find((b) => b.textContent.includes("Login captures"));
    if (!loginTab) { bad++; console.log("FAIL: Login captures tab missing in Logs"); }
    else {
      loginTab.dispatchEvent(new w.Event("click", { bubbles: true }));
      await new Promise((r) => setTimeout(r, 40));
      if (!logsView.textContent.includes("No login activity is recorded yet")) {
        bad++; console.log("FAIL: empty login activity needs an explanatory state");
      }
    }
  } else { bad++; console.log("FAIL: Logs navigation item missing"); }
  const usersNav = [...navItems].find((n) => n.dataset.id === "users");
  if (usersNav) {
    usersNav.dispatchEvent(new w.Event("click", { bubbles: true }));
    await new Promise((r) => setTimeout(r, 30));
    const usersView = [...w.document.querySelectorAll(".view")].find((x) => x.style.display !== "none");
    if (!usersView?.querySelector(".user-card") || !usersView.textContent.includes("Add IP to ban")) {
      bad++; console.log("FAIL: user/session/ban cards or Add IP to ban action missing");
    }
    if (usersView?.querySelector(".tscroll, table")) {
      bad++; console.log("FAIL: user management still renders horizontally scrolling tables");
    }
  } else { bad++; console.log("FAIL: Users navigation item missing"); }
  const cleanNav = [...navItems].find((n) => n.dataset.id === "clean");
  if (cleanNav) {
    cleanNav.dispatchEvent(new w.Event("click", { bubbles: true }));
    await new Promise((r) => setTimeout(r, 35));
    const cleanView = [...w.document.querySelectorAll(".view")].find((x) => x.style.display !== "none");
    if (cleanView?.querySelectorAll(".clean-card").length !== 5 ||
        [...(cleanView?.querySelectorAll(".clean-card button") || [])].some((b) => b.disabled)) {
      bad++; console.log("FAIL: populated storage categories should show enabled cleanup actions", cleanView?.textContent.slice(0, 260), [...(cleanView?.querySelectorAll(".clean-card button") || [])].map(b => b.disabled));
    }
    STORAGE_SUMMARY_FAIL = true;
    cleanView?.querySelector(".viewhead button")?.click();
    await new Promise((r) => setTimeout(r, 35));
    const error = cleanView?.querySelector(".clean-error");
    if (!error?.textContent.includes("Storage summary could not be loaded") ||
        !error.textContent.includes("administrator login required") ||
        [...(cleanView?.querySelectorAll(".clean-card button") || [])].some((b) => !b.disabled)) {
      bad++; console.log("FAIL: unavailable storage summary must explain the failure and keep deletion safely disabled", cleanView?.textContent.slice(0, 300), error?.textContent, error?.textContent.includes("administrator login required"), [...(cleanView?.querySelectorAll(".clean-card button") || [])].map(b => b.disabled));
    }
    const retry = [...(error?.querySelectorAll("button") || [])].find((b) => b.textContent.includes("Retry"));
    if (!retry) { bad++; console.log("FAIL: unavailable storage summary must offer retry"); }
  } else { bad++; console.log("FAIL: Storage & Cleanup navigation item missing"); }
  const settingsNav = [...navItems].find((n) => n.dataset.id === "settings");
  if (settingsNav) {
    settingsNav.dispatchEvent(new w.Event("click", { bubbles: true }));
    const settingsView = [...w.document.querySelectorAll(".view")].find((x) => x.style.display !== "none");
    [...(settingsView?.querySelectorAll(".tab") || [])].find((b) => b.textContent.trim() === "Open source")?.click();
    await new Promise((r) => setTimeout(r, 50));
    const card = settingsView?.querySelector('[data-addon-name="code-server"]');
    const start = [...(card?.querySelectorAll("button") || [])].find((b) => b.textContent.includes("Install/Start"));
    if (!card || !start) { bad++; console.log("FAIL: Add-ons board must render code-server with Install/Start"); }
    else {
      start.click();
      await new Promise((r) => setTimeout(r, 50));
      if (!COMMAND_CALLS.includes("addons.run")) { bad++; console.log("FAIL: Add-ons Install/Start must send the addons.run command"); }
    }
  } else { bad++; console.log("FAIL: Settings navigation item missing"); }
  // round 59: API_BASE CONTRACT - the //api/version bug (protocol-relative
  // URL -> browser asks host "api") must NEVER come back. Test the REAL
  // shipped hksApiBase (extracted from app.js source, not a copy) against
  // REAL URL resolution via the URL constructor.
  const src = fs.readFileSync(APP, "utf8");
  const fn = src.match(/function hksApiBase\([\s\S]*?\n\}/);
  if (!fn) { bad++; console.log("FAIL: hksApiBase helper missing from app.js"); }
  else {
    const hksApiBase = eval("(" + fn[0] + ")");
    const cases = [["/", ""], ["", ""], ["/hks/", "/hks"], ["/hks/index.html", "/hks"], ["/index.html", ""]];
    for (const [pin, want] of cases) {
      const got = hksApiBase(pin);
      if (got !== want) { bad++; console.log(`FAIL: hksApiBase(${JSON.stringify(pin)}) = ${JSON.stringify(got)}, want ${JSON.stringify(want)}`); }
    }
    const joined = hksApiBase("/") + "/api/version";
    if (joined !== "/api/version") { bad++; console.log("FAIL: root api path = " + joined); }
    const resolved = new URL(joined, "https://10.58.60.224:6565/").href;
    if (resolved !== "https://10.58.60.224:6565/api/version") { bad++; console.log("FAIL: URL resolution -> " + resolved); }
    // and the LIVE page (pathname "/") must agree
    const live = w.HKS_DEBUG.apiBase() + "/api/version";
    if (live !== "/api/version") { bad++; console.log("FAIL: live page API path = " + live); }
    console.log("api-base contract: root -> " + resolved);
  }

  // round 52: agent-fix card CONTRACT - hides only when BOTH live,
  // names the missing one + the exact repair when not. Driven through
  // the REAL data path (fetch stub -> refreshPills -> paintAgentFix).
  const cardState = () => {
    const c = w.document.getElementById("hksAgentFix");
    return c ? c.style.display + "|" + c.textContent : "missing";
  };
  // showSection keeps cached views detached until selected again. Reattach
  // Home before checking its card so this contract exercises the live DOM.
  const home = [...w.document.querySelectorAll(".nav-item")].find((n) => n.dataset.id === "home");
  if (home) home.dispatchEvent(new w.Event("click", { bubbles: true }));
  MACHINES = { ok: true, machines: [{ machine_id: "m1", name: "T", live_svc: true, live_session: true }], default_machine: "m1" };
  await w.HKS_DEBUG.refreshPills();
  if (cardState().split("|")[0] !== "none") { bad++; console.log("FAIL: agent card must HIDE when both agents live"); }
  MACHINES = { ok: true, machines: [{ machine_id: "m1", name: "T", live_svc: false, live_session: true }], default_machine: "m1" };
  await w.HKS_DEBUG.refreshPills();
  if (cardState().split("|")[0] === "none" || !cardState().includes("SERVICE AGENT OFFLINE")) { bad++; console.log("FAIL: agent card must SHOW the service-offline repair"); }
  MACHINES = { ok: true, machines: [{ machine_id: "m1", name: "T", live_svc: true, live_session: false }], default_machine: "m1" };
  await w.HKS_DEBUG.refreshPills();
  if (cardState().split("|")[0] === "none" || !cardState().includes("DESKTOP AGENT OFFLINE")) { bad++; console.log("FAIL: agent card must SHOW the desktop-offline hint"); }
  MACHINES = { ok: true, machines: [], default_machine: null, agent_token_hint: "x…" };

  // Native multi-device management: inventory, topbar picker, persistence,
  // and card-based selection must all use the HKS machine registry.
  MACHINES = { ok: true, machines: [
    { machine_id: "m1", name: "Laptop One", version: "1.0", ip: "192.0.2.1", last_seen: Date.now()/1000, live_svc: true, live_session: true, telemetry: { cpu: 21, ram: 44 } },
    { machine_id: "m2", name: "Laptop Two", version: "1.1", ip: "192.0.2.2", last_seen: Date.now()/1000-3600, live_svc: true, live_session: false, telemetry: { cpu: 33, ram: 55 } }
  ], default_machine: "m1" };
  await w.HKS_DEBUG.refreshPills();
  const picker = w.document.getElementById("tMachine");
  if (!picker || picker.options.length !== 2 || picker.value !== "m1") {
    bad++; console.log("FAIL: topbar device picker must populate and select the server default");
  }
  const devicesNav = [...navItems].find((n) => n.dataset.id === "devices");
  if (!devicesNav) { bad++; console.log("FAIL: Devices navigation item missing"); }
  else {
    devicesNav.dispatchEvent(new w.Event("click", { bubbles: true }));
    await new Promise((r) => setTimeout(r, 25));
    const deviceView = w.document.querySelector(".view.on");
    if (deviceView?.querySelectorAll(".device-card").length !== 2) {
      bad++; console.log("FAIL: Devices page must render a card for each enrolled machine");
    }
    const useTwo = [...(deviceView?.querySelectorAll(".device-card button") || [])].find((b) => b.textContent.includes("Manage this device"));
    if (!useTwo) { bad++; console.log("FAIL: device cards must allow switching the managed machine"); }
    else {
      useTwo.click();
      if (w.localStorage.getItem("hks_selected_machine") !== "m2") {
        bad++; console.log("FAIL: selected machine must persist across browser reloads");
      }
      if (picker.value !== "m2") { bad++; console.log("FAIL: topbar picker must follow card-based device selection"); }
      if (w.document.getElementById("tSes")?.textContent !== "desktop offline") {
        bad++; console.log("FAIL: device switch must immediately refresh the selected desktop-agent status");
      }
    }
  }

  // login screen sanity: logout then check login form exists
  if (errors.length) { bad++; console.log("window errors:", errors); }
  if (bad) { console.log(`\nUI SMOKE: ${bad} problem(s)`); process.exit(1); }
  console.log(`UI SMOKE: ALL ${navItems.length} SECTIONS RENDER CLEAN (2 passes, sparse data)`);
  process.exit(0);
})();
