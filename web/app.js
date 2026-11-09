const pages = ["overview", "sky", "picture", "align", "album", "plan", "settings"];
const state = {
  page: "overview",
  scopes: [],
  selected: localStorage.getItem("seestar-scope") || "",
  snap: null,
  log: [],
  targets: [],
  folders: [],
  files: [],
  folder: "",
  gallery: [],
  galleryPick: "",
  downloadDest: "",
  saveDir: null,
  dirStack: [],
  pendingCopy: null,
  galleryEngine: null,
  layout: localStorage.getItem("seestar-layout") || "cards",
  camera: "tele",
  seq: 0,
  error: "",
  query: "",
  speed: 1500,
  planItems: [],
  networks: [],
  skyPick: null,
  skyEngine: null,
  starChart: localStorage.getItem("seestar-starchart") === "gaia" ? "gaia" : "stellarium",
  starDepth: localStorage.getItem("seestar-starmag") === "12" ? 12 : 7,
  skyPictures: localStorage.getItem("seestar-skypics") === "1",
  weatherBusy: false,
  heaterPower: 50,
  place: null,
  forecast: null,
  theme: "night",
  alignTimer: 0,
};

const themes = [
  { id: "night", name: "Night", swatch: "#e0b15a" },
  { id: "slate", name: "Slate", swatch: "#7eb6e8" },
  { id: "blue", name: "Blue", swatch: "#3d9cf0" },
  { id: "pine", name: "Pine", swatch: "#7dcea0" },
  { id: "day", name: "Daylight", swatch: "#8d4e22" },
];

function applyTheme(theme) {
  state.theme = themes.some((item) => item.id === theme) ? theme : "night";
  document.documentElement.dataset.theme = state.theme;
  localStorage.setItem("seestar-theme", state.theme);
}

applyTheme(localStorage.getItem("seestar-theme") || "night");

document.body.classList.toggle("theater", state.layout === "theater");

document.querySelectorAll("nav button").forEach((button) => {
  button.addEventListener("click", () => show(button.dataset.page));
});

function show(page) {
  state.page = page;
  document.querySelectorAll("nav button").forEach((button) => {
    button.classList.toggle("active", button.dataset.page === page);
  });
  render();
  syncAlignTimer();
  if (page === "picture" && state.snap && state.snap.connected && !state.snap.parked) startLive(state.camera || "tele");
}

async function api(path, options) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const text = await response.text();
  const body = text ? JSON.parse(text) : {};
  if (!response.ok) throw new Error(body.error || response.statusText);
  return body;
}

function scopePath(suffix) {
  return `/api/scopes/${encodeURIComponent(state.selected)}${suffix}`;
}

function toast(message) {
  state.error = message;
  let node = document.querySelector(".toast");
  if (!node) {
    node = document.createElement("div");
    node.className = "toast";
    document.body.appendChild(node);
  }
  node.textContent = message;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => node.remove(), 6000);
}

async function guard(work) {
  try {
    await work();
  } catch (error) {
    toast(error.message || String(error));
  }
}

function rememberPlace(latitude, longitude) {
  const lat = Number(latitude);
  const lon = Number(longitude);
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;
  state.place = { latitude: lat, longitude: lon };
}

function placeNow(snap) {
  const liveLat = Number(snap && snap.latitude);
  const liveLon = Number(snap && snap.longitude);
  if (Number.isFinite(liveLat) && Number.isFinite(liveLon)) return { latitude: liveLat, longitude: liveLon };
  const saved = state.place;
  if (!saved) return null;
  const lat = Number(saved.latitude);
  const lon = Number(saved.longitude);
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null;
  return { latitude: lat, longitude: lon };
}

function placeText(snap) {
  const here = placeNow(snap) || {};
  const lat = Number(here.latitude);
  const lon = Number(here.longitude);
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return "—";
  const ns = lat >= 0 ? "N" : "S";
  const ew = lon >= 0 ? "E" : "W";
  return `${Math.abs(lat).toFixed(4)}° ${ns}, ${Math.abs(lon).toFixed(4)}° ${ew}`;
}

function coordValue(value) {
  if (value === null || value === undefined || value === "") return "";
  const number = Number(value);
  if (!Number.isFinite(number)) return "";
  return String(Math.round(number * 10000) / 10000);
}

function fmtNum(value, suffix) {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  if (Number.isNaN(number)) return String(value);
  return `${Math.round(number)}${suffix || ""}`;
}

function fmtRA(hours) {
  if (hours === null || hours === undefined || Number.isNaN(Number(hours))) return "—";
  let h = Number(hours);
  h = ((h % 24) + 24) % 24;
  const whole = Math.floor(h);
  const minutes = (h - whole) * 60;
  const m = Math.floor(minutes);
  const s = Math.round((minutes - m) * 60);
  return `${whole}h ${String(m).padStart(2, "0")}m ${String(s).padStart(2, "0")}s`;
}

function fmtDec(deg) {
  if (deg === null || deg === undefined || Number.isNaN(Number(deg))) return "—";
  const sign = Number(deg) < 0 ? "−" : "+";
  const abs = Math.abs(Number(deg));
  const d = Math.floor(abs);
  const minutes = (abs - d) * 60;
  const m = Math.floor(minutes);
  const s = Math.round((minutes - m) * 60);
  return `${sign}${d}° ${String(m).padStart(2, "0")}′ ${String(s).padStart(2, "0")}″`;
}

function fmtDisk(mb) {
  if (mb === null || mb === undefined) return "—";
  const value = Number(mb);
  if (value > 1024) return `${(value / 1024).toFixed(1)} GB`;
  return `${Math.round(value)} MB`;
}

function clock() {
  const now = new Date();
  document.querySelector("#clock").innerHTML =
    `<div>${now.toLocaleTimeString()}</div><div class="muted">UTC ${now.toISOString().slice(11, 19)}</div>`;
}

function scopeKind(model) {
  const text = String(model || "").toLowerCase();
  if (text.includes("s30")) return "s30";
  if (text.includes("s50") && text.includes("pro")) return "s50pro";
  if (text.includes("pro")) return "s50pro";
  if (text.includes("s50")) return "s50";
  return "seestar";
}

function scopeLabel(model) {
  return { s30: "S30", s50: "S50", s50pro: "S50 Pro", seestar: "Seestar" }[scopeKind(model)];
}

function svg(body, viewBox) {
  return `<svg viewBox="${viewBox || "0 0 24 24"}" aria-hidden="true">${body}</svg>`;
}

function scopePicture(model) {
  const kind = scopeKind(model);
  const file = { s30: "s30", s50: "s50", s50pro: "s50-pro" }[kind] || "s50";
  return `<img src="/static/scopes/${file}.jpg?v=2" alt="${escapeHtml(scopeLabel(model))}">`;
}

function batteryMark(percent, charging) {
  const known = percent !== null && percent !== undefined && percent !== "" && !Number.isNaN(Number(percent));
  const level = known ? Math.max(0, Math.min(100, Number(percent))) : 0;
  const bars = known && level > 0 ? Math.min(4, Math.ceil(level / 25)) : 0;
  const edge = charging ? "#3ddc97" : "currentColor";
  const cells = [0, 1, 2, 3].map((index) => {
    const on = index < bars;
    return `<rect x="${3.4 + index * 3.7}" y="8.7" width="2.9" height="6.6" rx="0.45" fill="currentColor" opacity="${on ? 1 : 0.2}"/>`;
  }).join("");
  return svg(`
    <rect x="1.4" y="6.6" width="18.2" height="10.8" rx="2.2" fill="none" stroke="${edge}" stroke-width="${charging ? 2.1 : 1.6}"/>
    <rect x="20.3" y="9.8" width="2.1" height="4.4" rx="0.6" fill="${edge}"/>
    ${cells}
  `, "0 5.2 24 13.4");
}

function batteryBadge(percent, charging) {
  return `${batteryMark(percent, charging)}<span>${escapeHtml(fmtNum(percent, "%"))}</span>`;
}

function iconBattery(percent) {
  const known = percent !== null && percent !== undefined && percent !== "" && !Number.isNaN(Number(percent));
  const level = known ? Math.max(0, Math.min(100, Number(percent))) : 0;
  const width = (level / 100) * 14;
  return svg(`
    <rect x="1.5" y="7" width="18" height="10" rx="2" fill="none" stroke="currentColor" stroke-width="1.6"/>
    <rect x="20.2" y="10" width="2" height="4" rx="0.6" fill="currentColor"/>
    ${known ? `<rect x="3.2" y="8.8" width="${width.toFixed(2)}" height="6.4" rx="1" fill="currentColor"/>` : ""}
  `);
}

function iconWifi(active) {
  const tone = active ? "currentColor" : "currentColor";
  return svg(`
    <path d="M4 10.5a11 11 0 0 1 16 0" fill="none" stroke="${tone}" stroke-width="1.6" stroke-linecap="round" opacity="${active ? 1 : 0.35}"/>
    <path d="M7 13.5a7 7 0 0 1 10 0" fill="none" stroke="${tone}" stroke-width="1.6" stroke-linecap="round" opacity="${active ? 1 : 0.35}"/>
    <path d="M10 16.5a3 3 0 0 1 4 0" fill="none" stroke="${tone}" stroke-width="1.6" stroke-linecap="round" opacity="${active ? 1 : 0.55}"/>
    <circle cx="12" cy="19.2" r="1.2" fill="${tone}" opacity="${active ? 1 : 0.35}"/>
  `);
}

function iconLink(mode) {
  if (mode === "control") {
    return svg(`<path d="M9 12a4 4 0 0 1 0-6l1.5-1.5a4 4 0 0 1 6 6L15 12" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/><path d="M15 12a4 4 0 0 1 0 6l-1.5 1.5a4 4 0 0 1-6-6L9 12" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>`);
  }
  if (mode === "watch") {
    return svg(`<path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12z" fill="none" stroke="currentColor" stroke-width="1.6"/><circle cx="12" cy="12" r="2.4" fill="currentColor"/>`);
  }
  return svg(`<path d="M8 12h3M13 12h3" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/><path d="M5 8l2 8M19 8l-2 8" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>`);
}

function iconDisk() {
  return svg(`<ellipse cx="12" cy="7" rx="7" ry="3" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M5 7v10c0 1.7 3.1 3 7 3s7-1.3 7-3V7" fill="none" stroke="currentColor" stroke-width="1.6"/>`);
}

function iconWifiBars(level) {
  return svg([6, 9, 12, 15].map((height, index) => {
    const y = 20 - height;
    const on = index < level;
    return `<rect x="${3 + index * 5}" y="${y}" width="3.2" height="${height}" rx="0.6" fill="currentColor" opacity="${on ? 1 : 0.28}"/>`;
  }).join(""));
}

function iconTemp() {
  return svg(`<path d="M10 13.4a3 3 0 1 0 4 0V6.4a2 2 0 1 0-4 0z" fill="none" stroke="currentColor" stroke-width="1.6"/><circle cx="12" cy="15.1" r="1.15" fill="currentColor"/>`);
}

function iconFirmware() {
  return svg(`<rect x="7" y="7" width="10" height="10" rx="2" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M9 3.5v3M15 3.5v3M9 17.5v3M15 17.5v3M3.5 9h3M3.5 15h3M17.5 9h3M17.5 15h3" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>`);
}

function wifiBars(signal) {
  const value = Number(signal);
  if (!Number.isFinite(value)) return 0;
  if (value < 0) {
    if (value >= -55) return 4;
    if (value >= -67) return 3;
    if (value >= -75) return 2;
    return value >= -85 ? 1 : 0;
  }
  if (value >= 75) return 4;
  if (value >= 50) return 3;
  if (value >= 30) return 2;
  return value > 0 ? 1 : 0;
}

function signalText(signal) {
  const value = Number(signal);
  if (!Number.isFinite(value)) return "";
  return value < 0 ? `${Math.round(value)} dBm` : `${Math.round(value)}%`;
}

function storageView(snap) {
  if (snap.disk_free === null || snap.disk_free === undefined || snap.disk_free === "") {
    return { text: "—", sub: "No storage report", pct: null, tone: "" };
  }
  const free = Number(snap.disk_free);
  const total = Number(snap.disk_total);
  if (!Number.isFinite(free)) return { text: "—", sub: "No storage report", pct: null, tone: "" };
  const used = Number.isFinite(total) ? Math.max(0, total - free) : null;
  const pct = used !== null && total > 0 ? Math.round((used / total) * 100) : null;
  const tone = pct === null ? "" : pct > 90 ? "bad" : pct > 75 ? "warn" : "good";
  return {
    text: `${fmtDisk(free)} free`,
    sub: pct === null ? "" : `${pct}% used · ${fmtDisk(total)}`,
    pct,
    tone,
  };
}

function iconMark() {
  return svg(`<rect x="3" y="9" width="14" height="6" rx="3" fill="none" stroke="currentColor" stroke-width="1.6"/><circle cx="15" cy="12" r="1.4" fill="currentColor"/><path d="M8 15v3h8" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>`);
}

function meter(icon, value, title, tone) {
  return `<span class="meter ${tone || ""}" title="${escapeHtml(title)}">${icon}<b>${value}</b></span>`;
}

function islandChip(label, value, tone) {
  return `<span class="island ${tone || ""}"><span class="island-copy"><small>${escapeHtml(label)}</small><b>${value}</b></span></span>`;
}

function scopeIsland(scope) {
  const live = Boolean(scope.connected);
  const tone = live ? (scope.master ? "good" : "warn") : "dim";
  const model = scopeLabel(scope.model);
  const title = scope.name || model;
  const line = live
    ? `${scope.master ? "In control" : "Watching"} · ${activityText(scope)}`
    : `Not connected · ${model}`;
  const current = state.selected === scope.id ? " current" : "";
  return `<button type="button" class="island scope ${tone}${current}" data-scope="${escapeHtml(scope.id)}" title="${escapeHtml(title)}">${scopePicture(scope.model)}<span class="island-copy"><b>${escapeHtml(title)}</b><small>${escapeHtml(line)}</small></span><i class="dot"></i></button>`;
}

function paintIslands() {
  const status = document.querySelector("#statusline");
  if (!status) return;
  const snap = state.snap && state.snap.connected ? state.snap : null;
  const scopes = (state.scopes || [])
    .map(scopeSnap)
    .filter((scope) => scope.linked || scope.connected || scope.demo);
  const bits = scopes.length
    ? scopes.map(scopeIsland)
    : [`<span class="island dim"><span class="island-copy"><small>Telescope</small><b>Not connected</b></span></span>`];
  if (snap) {
    const battery = Number(snap.battery);
    const batteryTone = Number.isNaN(battery) ? "dim" : battery < 20 ? "bad" : battery < 40 ? "warn" : "good";
    bits.push(islandChip("Wi-Fi", escapeHtml(signalText(snap.wifi_signal) || snap.wifi || "—"), snap.wifi ? "good" : "dim"));
    bits.push(islandChip("Activity", escapeHtml(activityText(snap)), ""));
    bits.push(islandChip("Mount", escapeHtml(snap.equatorial ? "Equatorial" : snap.equatorial === false ? "Alt-az" : "—"), ""));
    bits.push(islandChip("Battery", escapeHtml(`${fmtNum(snap.battery, "%")}${snap.charging ? " · charging" : ""}`), batteryTone));
  }
  const html = bits.join("");
  if (status.dataset.sig === html) return;
  status.dataset.sig = html;
  status.innerHTML = html;
  status.querySelectorAll("[data-scope]").forEach((button) => {
    button.addEventListener("click", () => {
      state.selected = button.dataset.scope;
      localStorage.setItem("seestar-scope", state.selected);
      show("overview");
    });
  });
}

function paintMeters() {
  const snap = state.snap;
  const name = document.querySelector("#scope-name");
  const skyline = document.querySelector("#skyline");
  const mark = document.querySelector("#brand-mark");
  if (!snap) {
    name.textContent = "No telescope";
    paintIslands();
    if (skyline) skyline.innerHTML = `<strong>Connect a telescope</strong><span>Turn it on and search this network.</span>`;
    if (mark) mark.innerHTML = iconMark();
    return;
  }
  name.textContent = `${snap.name || snap.host}${snap.model ? " · " + snap.model : ""}`;
  if (mark) mark.innerHTML = scopePicture(snap.model);
  const linkMode = snap.connected ? (snap.master ? "control" : "watch") : "off";
  const linkText = linkMode === "control" ? "In control" : linkMode === "watch" ? "Watching" : "Offline";
  const sky = (snap.plan && snap.plan.sky) || {};
  const skyTitle = sky.darkness ? sky.darkness.charAt(0).toUpperCase() + sky.darkness.slice(1) : "Seestar Desk";
  if (skyline) {
    skyline.innerHTML = `<strong>${escapeHtml(skyTitle)}</strong><span>${escapeHtml(placeText(snap) === "—" ? (snap.model || "Ready when you are") : placeText(snap))}</span>`;
  }
  paintIslands();
  document.querySelectorAll("[data-field]").forEach((node) => {
    const key = node.dataset.field;
    if (key === "ra") node.textContent = fmtRA(snap.ra);
    else if (key === "dec") node.textContent = fmtDec(snap.dec);
    else if (key === "alt") node.textContent = fmtDec(snap.alt).replace("°", "° alt ").slice(0, 12) || fmtNum(snap.alt, "°");
    else if (key === "az") node.textContent = fmtNum(snap.az, "°");
    else if (key === "focus") node.textContent = fmtNum(snap.focus, "");
    else if (key === "filter") node.textContent = filterName(snap.filter);
    else if (key === "target") node.textContent = snap.target || "—";
    else if (key === "mode") node.textContent = snap.mode || "—";
    else if (key === "stack") node.textContent = stackText(snap);
    else if (key === "goto") node.textContent = gotoText(snap);
    else if (key === "activity") node.textContent = activityText(snap);
    else if (key === "place") node.textContent = placeText(snap);
    else if (key === "link") node.textContent = `${linkText}${snap.host ? " · " + snap.host : ""}`;
    else if (key === "wifi-line") node.textContent = [snap.wifi || "—", signalText(snap.wifi_signal), snap.ip].filter(Boolean).join(" · ");
    else if (key === "storage-line") node.textContent = storageView(snap).text;
    else if (key === "align") node.textContent = alignText(snap);
    else if (key === "plan") node.textContent = planStatus(snap);
    else if (key === "tracking") node.textContent = snap.tracking ? "On" : "Off";
    else if (key === "mount") node.textContent = snap.equatorial ? "Equatorial" : snap.equatorial === false ? "Alt-az" : "—";
    else if (key === "arm") node.textContent = snap.parked ? "Folded" : snap.parked === false ? "Open" : "—";
    else if (key === "heading") node.textContent = headingText(snap.heading);
  });
  paintLevel(snap);
  paintPolar(snap);
  const liveSeq = snap.frame_seq && snap.frame_seq[state.camera];
  const stackSeq = snap.frame_seq && snap.frame_seq.stack;
  if (snap.parked) {
    clearFrame("#stage", "Parked");
    clearFrame("#stack-stage", "Parked");
  } else {
    if (liveSeq) showFrame("#stage", liveSeq, state.camera);
    else prepareLiveStage();
    if (stackSeq) showFrame("#stack-stage", stackSeq, "stack");
    else clearFrame("#stack-stage", "The stack appears here");
  }
  markCameras();
  const plot = document.querySelector("#pa-plot .dot");
  const align = snap.align || {};
  const ax = Number(align.x ?? align.eq_offset_az);
  const ay = Number(align.y ?? align.eq_offset_alt);
  if (plot && !Number.isNaN(ax) && !Number.isNaN(ay)) {
    plot.style.left = `${50 + Math.max(-1, Math.min(1, ax / 3)) * 40}%`;
    plot.style.top = `${50 + Math.max(-1, Math.min(1, -ay / 3)) * 40}%`;
  }
  const tonight = document.querySelector("#tonight-list");
  if (tonight) tonight.innerHTML = tonightHtml(snap);
  const conditions = document.querySelector("#conditions");
  if (conditions) {
    const html = conditionsHtml(snap);
    const editing = conditions.contains(document.activeElement);
    if (!editing && conditions.dataset.sig !== html) {
      conditions.innerHTML = html;
      conditions.dataset.sig = html;
      bindConditions();
    }
  }
  requestWeather(snap);
  drawRose();
  paintReadings();
  const badge = document.querySelector("[data-battery]");
  if (badge) {
    badge.classList.toggle("charging", Boolean(snap.charging));
    badge.title = `${fmtNum(snap.battery, "%")}${snap.charging ? ", charging" : ""}`;
    badge.innerHTML = batteryBadge(snap.battery, snap.charging);
  }
  const control = document.querySelector("[data-control]");
  if (control && snap.id === control.dataset.control) {
    const held = Boolean(snap.master);
    control.textContent = held ? "In control" : "Watching";
    control.classList.toggle("primary", held);
  }
  const linkButton = document.querySelector(".actions [data-connect]");
  if (linkButton && snap.id === linkButton.dataset.connect) {
    const linked = Boolean(snap.connected);
    linkButton.textContent = linked ? "Connected" : "Connect";
    linkButton.title = linked ? "Disconnect" : "Connect";
  }
  syncSkyObserver();
}

function paintReadings() {
  const root = document.querySelector("[data-readings]");
  if (!root || !state.snap || root.dataset.readings !== state.snap.id) return;
  root.innerHTML = readingsHtml(state.snap);
}

function clearFrame(selector, text) {
  const stage = document.querySelector(selector);
  const empty = document.querySelector(`${selector}-empty`);
  if (stage) {
    stage.removeAttribute("src");
    stage.dataset.token = "";
  }
  if (empty) {
    empty.hidden = false;
    if (text) empty.textContent = text;
  }
}

function showFrame(selector, seq, camera) {
  const stage = document.querySelector(selector);
  const token = `${camera}:${seq}`;
  if (!stage || !seq || stage.dataset.token === token) return;
  stage.dataset.token = token;
  stage.src = `${scopePath("/frame.jpg")}?camera=${camera}&seq=${seq}`;
  const empty = document.querySelector(`${selector}-empty`);
  if (empty) empty.hidden = true;
}

function markCameras() {
  document.querySelectorAll("[data-camera]").forEach((button) => {
    const on = button.dataset.camera === (state.camera || "tele");
    button.classList.toggle("primary", on);
    button.setAttribute("aria-pressed", on ? "true" : "false");
  });
  const label = document.querySelector("[data-live-camera]");
  if (label) label.textContent = state.camera === "wide" ? "Wide angle" : "Telephoto";
}

function prepareLiveStage() {
  const stage = document.querySelector("#stage");
  const empty = document.querySelector("#stage-empty");
  if (stage && stage.dataset.token && !stage.dataset.token.startsWith(`${state.camera}:`)) {
    stage.removeAttribute("src");
    stage.dataset.token = "";
  }
  if (empty && (!stage || !stage.getAttribute("src"))) {
    empty.hidden = false;
    empty.textContent = state.camera === "wide" ? "Waiting for the wide angle…" : "Opening the telephoto…";
  }
}

function activityText(snap) {
  const view = snap.view || {};
  const stage = String(view.stage || "");
  if (/dark/i.test(stage) && /lib/i.test(stage) && String(view.state || "").toLowerCase() === "working") {
    return "Building the dark library";
  }
  const phase = String((snap.stack || {}).state || "");
  if (["start", "working", "stacking"].includes(phase)) {
    return `Taking pictures${snap.target ? " · " + snap.target : ""}`;
  }
  if (snap.parked) return "Parked";
  if (view.state && !["complete", "cancel", "idle"].includes(String(view.state))) return gotoText(snap);
  return "Ready";
}

function tonightHtml(snap) {
  const items = (snap.plan && snap.plan.items) || [];
  if (!items.length) return `<p class="muted">No targets yet. Choose them on the Plan page.</p>`;
  return items.slice(0, 6).map((item) => `
    <div class="night-row"><b>${escapeHtml(item.name || "Target")}</b><span class="muted">${escapeHtml(item.minutes ? `${item.minutes} min` : "")}</span></div>
  `).join("");
}

function conditionsHtml(snap) {
  const linked = Boolean(snap && snap.connected);
  const report = linked ? ((snap && snap.plan) || {}) : (state.forecast || {});
  const weather = report.weather || {};
  const sky = report.sky || {};
  const hours = Array.isArray(weather.hours) ? weather.hours : [];
  if (!hours.length && !weather.error) {
    return `<p class="muted">${escapeHtml(sky.darkness ? `${sky.darkness}. The forecast is loading.` : "The forecast loads from the saved place.")}</p>`;
  }
  const bars = hours.map((hour) => {
    const cloud = Number(hour.cloud);
    const height = Number.isFinite(cloud) ? Math.max(4, Math.min(100, cloud)) : 4;
    const tone = cloud >= 70 ? "var(--bad)" : cloud >= 40 ? "var(--warn)" : "var(--good)";
    const title = `${hour.label} · ${Number.isFinite(cloud) ? Math.round(cloud) : "—"}% cloud`;
    return `<i style="height:${height}%;background:${tone}" title="${escapeHtml(title)}"></i>`;
  }).join("");
  const marks = hourMarks(hours);
  const heaterOn = Boolean((snap.settings || {}).heater_enable);
  const risk = weather.dew_risk || "—";
  const riskClass = risk === "High" ? "bad-txt" : risk === "Moderate" ? "warn-txt" : risk === "Low" ? "good-txt" : "";
  return `
    <div class="cond-head"><span class="muted">Cloud cover through the night</span><button type="button" id="wx-refresh">Refresh</button></div>
    ${weather.error ? `<p class="muted">${escapeHtml(weather.error)}</p>` : `<div class="cloud-bars">${bars}</div><div class="cloud-hours">${marks}</div>`}
    ${condRow("Place", placeText(snap))}
    ${condRow("Sky", skyLine(sky, hours))}
    ${condRow("Best observation window", weather.window || weather.note || "—", weather.window ? "good-txt" : "")}
    ${condRow("Temperature", tempLine(weather, snap))}
    ${condRow("Wind", windLine(weather))}
    ${condRow("Humidity", weather.humidity === undefined || weather.humidity === null ? "—" : `${Math.round(weather.humidity)}%`)}
    ${condRow("Risk of dew", risk, riskClass)}
    ${linked ? `<div class="cond-row"><span class="muted">Dew heater</span><span class="heater-ctl"><b class="${heaterOn ? "good-txt" : ""}">${heaterOn ? "On" : "Off"}</b><button type="button" id="dew-heater">${heaterOn ? "Turn off" : "Turn on"}</button></span></div>` : ""}
    ${linked && heaterOn ? `<label class="heater-power">Heater power<input id="dew-power" type="range" min="10" max="100" step="10" value="${state.heaterPower}"><output>${state.heaterPower}%</output></label>` : ""}
    <p class="muted cond-source">${escapeHtml(weather.source ? `Forecast: ${weather.source}` : "")}</p>`;
}

function skyLine(sky, hours) {
  const darkness = sky.darkness || "";
  if (darkness === "daylight" && hours.length) return `Daylight · dark from ${hours[0].label}`;
  return darkness || "—";
}

function condRow(label, value, klass) {
  return `<div class="cond-row"><span class="muted">${escapeHtml(label)}</span><b class="${klass || ""}">${escapeHtml(value)}</b></div>`;
}

function hourMarks(hours) {
  if (!hours.length) return "";
  const mid = hours[Math.floor((hours.length - 1) / 2)];
  const picked = [hours[0], mid, hours[hours.length - 1]];
  return picked.filter((item, index) => picked.findIndex((other) => other.label === item.label) === index)
    .map((item) => `<span>${escapeHtml(item.label)}</span>`).join("");
}

function tempLine(weather, snap) {
  const now = Number(weather.temperature);
  const low = Number(weather.low);
  const scope = Number(snap.temperature);
  const parts = [];
  if (Number.isFinite(now)) parts.push(`${Math.round(now)}° now`);
  else if (Number.isFinite(scope)) parts.push(`${Math.round(scope)}° at the telescope`);
  if (Number.isFinite(low)) parts.push(`low ${Math.round(low)}°`);
  return parts.join(" · ") || "—";
}

function windLine(weather) {
  const speed = Number(weather.wind_speed);
  if (!Number.isFinite(speed)) return "—";
  const gust = Number(weather.wind_gust);
  return `${Math.round(speed)} m/s${Number.isFinite(gust) ? ` · gusts ${Math.round(gust)} m/s` : ""}`;
}

function bindConditions() {
  bindClick("#wx-refresh", () => post("/plan", { action: "weather" }));
  bindClick("#dew-heater", () => {
    const settings = (state.snap && state.snap.settings) || {};
    const on = !settings.heater_enable;
    const power = Number(document.querySelector("#dew-power")?.value || state.heaterPower || 50);
    if (!confirm(on ? "Turn the dew heater on?" : "Turn the dew heater off?")) return;
    if (on) state.heaterPower = power;
    return post("/settings", { heater_enable: on, is_dew_on: on, dew_heater_power: on ? power : 0 });
  });
  const power = document.querySelector("#dew-power");
  if (power) power.onchange = () => {
    state.heaterPower = Number(power.value);
    const output = power.parentElement && power.parentElement.querySelector("output");
    if (output) output.textContent = `${state.heaterPower}%`;
    post("/settings", { heater_enable: true, is_dew_on: true, dew_heater_power: state.heaterPower });
  };
}

function requestWeather(snap) {
  const here = placeNow(snap || {});
  if (!here || state.weatherBusy || state.page !== "overview") return;
  const linked = Boolean(snap && snap.connected && state.selected);
  const weather = linked ? ((snap.plan && snap.plan.weather) || null) : ((state.forecast && state.forecast.weather) || null);
  if (weather && !weather.error && (weather.source || weather.note || Array.isArray(weather.hours))) return;
  if (weather && weather.error && Date.now() - (state.weatherTried || 0) < 120000) return;
  state.weatherTried = Date.now();
  state.weatherBusy = true;
  guard(async () => {
    try {
      if (linked) {
        state.snap = await api(scopePath("/plan"), { method: "POST", body: JSON.stringify({ action: "weather" }) });
      } else {
        state.forecast = await api("/api/place/weather");
      }
      paintMeters();
    } finally {
      state.weatherBusy = false;
    }
  });
}

function stackText(snap) {
  const stack = snap.stack || {};
  const kept = stack.stacked ?? "";
  const dropped = stack.dropped ?? "";
  const phase = stack.state || "";
  return [
    phase,
    kept !== "" ? `${kept} kept` : "",
    dropped !== "" ? `${dropped} rejected` : "",
    formatElapsed(stack.elapsed),
  ].filter(Boolean).join(" · ") || "Idle";
}

function formatElapsed(seconds) {
  const value = Number(seconds);
  if (!Number.isFinite(value) || value < 0) return "";
  const whole = Math.round(value);
  const minutes = Math.floor(whole / 60);
  return `${minutes}:${String(whole % 60).padStart(2, "0")}`;
}

function gotoText(snap) {
  const view = snap.view || {};
  return [view.stage, view.state, view.percent !== undefined && view.percent !== null ? `${view.percent}%` : "", view.error]
    .filter((part) => part !== undefined && part !== null && part !== "" && part !== "none")
    .join(" · ") || "Waiting for a slew";
}

function headingText(degrees) {
  const value = Number(degrees);
  if (!Number.isFinite(value)) return "—";
  const circle = ((value % 360) + 360) % 360;
  const names = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
  return `${names[Math.round(circle / 22.5) % 16]} ${circle.toFixed(0)}°`;
}

function levelVector(snap) {
  const sensor = snap.level || {};
  const x = Number(sensor.x);
  const y = Number(sensor.y);
  const z = Number(sensor.z);
  const reported = Number(sensor.angle);
  if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(z)) {
    return Number.isFinite(reported) ? { x: 0, y: 0, angle: reported, wedge: 90 - reported } : null;
  }
  const tiltX = Math.atan2(x, z) * 180 / Math.PI;
  const tiltY = Math.atan2(y, z) * 180 / Math.PI;
  const fromVector = Math.atan2(Math.hypot(x, y), Math.abs(z)) * 180 / Math.PI;
  const angle = Number.isFinite(reported) ? reported : fromVector;
  // The sensor measures from vertical. A wedge set to the latitude is the complement of that angle.
  const wedge = 90 - angle;
  return { x: tiltX, y: tiltY, angle, wedge };
}

function paintLevel(snap) {
  const bubble = document.querySelector("#level-bubble .dot");
  const angle = document.querySelector('[data-field="level-angle"]');
  const note = document.querySelector('[data-field="level-note"]');
  const base = document.querySelector('[data-field="base-tilt"]');
  const target = document.querySelector('[data-field="wedge-target"]');
  const point = document.querySelector('[data-field="wedge-point"]');
  const status = document.querySelector('[data-field="eq-status"]');
  const reading = levelVector(snap);
  const here = placeNow(snap);
  const latitude = here ? Number(here.latitude) : NaN;
  const shown = reading ? (snap.equatorial ? reading.wedge : reading.angle) : null;
  const wedge = reading ? reading.wedge : null;
  const miss = shown !== null && snap.equatorial && Number.isFinite(latitude) ? Math.abs(shown - Math.abs(latitude)) : shown !== null ? shown : 99;
  if (bubble) {
    const scale = 5;
    const dx = reading ? Math.max(-1, Math.min(1, reading.x / scale)) : 0;
    const dy = reading ? Math.max(-1, Math.min(1, reading.y / scale)) : 0;
    bubble.style.left = `${50 + dx * 42}%`;
    bubble.style.top = `${50 - dy * 42}%`;
  }
  if (angle) {
    angle.textContent = shown !== null ? `${shown.toFixed(1)}°` : "—";
    angle.classList.toggle("good", Boolean(shown !== null && miss < 0.5));
    angle.classList.toggle("warn", Boolean(shown !== null && miss >= 0.5 && miss < (snap.equatorial ? 2 : 1.5)));
    angle.classList.toggle("bad", Boolean(shown !== null && miss >= (snap.equatorial ? 2 : 1.5)));
  }
  if (base) base.textContent = shown !== null ? `${shown.toFixed(1)}°` : "—";
  if (target) target.textContent = Number.isFinite(latitude) ? `${Math.abs(latitude).toFixed(1)}°` : "—";
  if (point) {
    const pole = Number.isFinite(latitude) && latitude < 0 ? "south" : "north";
    point.textContent = snap.heading === undefined || snap.heading === null ? `True ${pole}` : `${headingText(snap.heading)}, true ${pole}`;
  }
  if (note) {
    if (!reading) note.textContent = "The level appears after the telescope connects. The scale is 5° at the edge.";
    else if (snap.equatorial && Number.isFinite(latitude)) {
      note.textContent = `The wedge is ${shown.toFixed(1)}° from level. This place needs it near ${Math.abs(latitude).toFixed(1)}°.`;
    } else if (shown < 0.5) note.textContent = "Level. The dot is inside the small circle.";
    else if (shown < 1.5) note.textContent = "Close. A small tweak to a tripod leg would help. The scale is 5° at the edge.";
    else note.textContent = "Adjust the tripod legs until the dot sits in the small circle. The scale is 5° at the edge.";
  }
  if (status) {
    if (!snap.connected) status.textContent = "Connect the telescope to read the mount.";
    else if (!snap.equatorial) status.textContent = "Alt-az. Put the scope on a wedge before switching.";
    else if (shown !== null && Number.isFinite(latitude) && Math.abs(shown - Math.abs(latitude)) <= 2) status.textContent = "Equatorial. The wedge tilt matches this place.";
    else status.textContent = "Equatorial. Check the wedge angle against the latitude.";
  }
  paintEquatorial(snap, wedge, latitude);
}

function paintEquatorial(snap, wedge, latitude) {
  const now = document.querySelector('[data-field="eq-now"]');
  const need = document.querySelector('[data-field="eq-need"]');
  const guide = document.querySelector('[data-field="eq-guide"]');
  const needed = Number.isFinite(latitude) ? Math.abs(latitude) : null;
  if (need) need.textContent = needed === null ? "—" : `${needed.toFixed(1)}°`;
  const gap = wedge !== null && needed !== null ? Math.abs(wedge - needed) : null;
  if (now) {
    now.textContent = wedge === null ? "—" : `${wedge.toFixed(1)}°`;
    now.classList.toggle("good", gap !== null && gap < 0.5);
    now.classList.toggle("warn", gap !== null && gap >= 0.5 && gap < 2);
    now.classList.toggle("bad", gap !== null && gap >= 2);
  }
  const heading = Number(snap.heading);
  if (guide) guide.textContent = equatorialGuide(wedge, needed, latitude, heading);
  drawEqDome(wedge, needed, latitude, heading, gap);
}

function drawEqDome(wedge, needed, latitude, heading, gap) {
  const canvas = document.querySelector("#eq-dome");
  if (!canvas) return;
  const width = canvas.clientWidth || 520;
  const height = Math.round(Math.min(width, 520));
  canvas.style.height = `${height}px`;
  const pixel = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = Math.round(width * pixel);
  canvas.height = Math.round(height * pixel);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(pixel, 0, 0, pixel, 0, 0);
  const style = getComputedStyle(document.documentElement);
  const color = (name, fallback) => style.getPropertyValue(name).trim() || fallback;
  const accent = color("--accent", "#e0b15a");
  const text = color("--text", "#e8edf4");
  const muted = color("--muted", "#93a0b4");
  const line = color("--line", "#2a3444");
  const raise = color("--bg-raise", "#151b24");
  const sink = color("--bg-sink", "#090c11");
  const dot = gap === null ? text : gap < 0.5 ? color("--good", "#8dceab") : gap < 2 ? color("--warn", "#e0a15a") : color("--bad", "#e58989");
  const cx = width * 0.5;
  const cy = height * 0.46;
  const radius = Math.min(width, height) * 0.36;
  // Inside the dome, looking up. North is at the top and east is on the left.
  const at = (az, alt) => {
    const reach = (1 - Math.max(0, Math.min(90, alt)) / 90) * radius;
    const a = ((az % 360) + 360) % 360 * Math.PI / 180;
    return { x: cx - Math.sin(a) * reach, y: cy - Math.cos(a) * reach };
  };
  const sky = ctx.createRadialGradient(cx, cy, radius * 0.08, cx, cy, radius);
  sky.addColorStop(0, raise);
  sky.addColorStop(0.72, sink);
  sky.addColorStop(1, sink);
  ctx.beginPath();
  ctx.arc(cx, cy, radius, 0, Math.PI * 2);
  ctx.fillStyle = sky;
  ctx.fill();
  const glow = ctx.createRadialGradient(cx, cy, 0, cx, cy, radius);
  glow.addColorStop(0, accent);
  glow.addColorStop(1, "transparent");
  ctx.save();
  ctx.globalAlpha = 0.16;
  ctx.fillStyle = glow;
  ctx.beginPath();
  ctx.arc(cx, cy, radius, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
  ctx.strokeStyle = line;
  ctx.lineWidth = 1.25;
  ctx.beginPath();
  ctx.arc(cx, cy, radius, 0, Math.PI * 2);
  ctx.stroke();
  ctx.save();
  ctx.beginPath();
  ctx.arc(cx, cy, radius, 0, Math.PI * 2);
  ctx.clip();
  ctx.globalAlpha = 0.55;
  [30, 60].forEach((alt) => {
    ctx.beginPath();
    ctx.arc(cx, cy, (1 - alt / 90) * radius, 0, Math.PI * 2);
    ctx.stroke();
  });
  ctx.globalAlpha = 0.8;
  ctx.beginPath();
  ctx.moveTo(at(180, 0).x, at(180, 0).y);
  ctx.lineTo(at(0, 0).x, at(0, 0).y);
  ctx.moveTo(at(270, 0).x, at(270, 0).y);
  ctx.lineTo(at(90, 0).x, at(90, 0).y);
  ctx.stroke();
  ctx.restore();
  ctx.font = "600 14px Inter, Segoe UI, sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  [["N", 0, accent], ["E", 90, muted], ["S", 180, muted], ["W", 270, muted]].forEach(([label, az, ink]) => {
    const rim = at(az, 0);
    const dx = rim.x - cx;
    const dy = rim.y - cy;
    const length = Math.hypot(dx, dy) || 1;
    ctx.fillStyle = ink;
    ctx.fillText(label, rim.x + dx / length * 16, rim.y + dy / length * 16);
  });
  const south = Number.isFinite(latitude) && latitude < 0;
  const targetAz = south ? 180 : 0;
  const knownHeading = Number.isFinite(heading);
  if (needed !== null && wedge !== null && knownHeading) {
    const target = at(targetAz, needed);
    const actual = at(heading, wedge);
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(actual.x, actual.y);
    ctx.lineTo(target.x, target.y);
    ctx.strokeStyle = muted;
    ctx.lineWidth = 1.2;
    ctx.stroke();
    ctx.setLineDash([]);
  }
  if (needed !== null) {
    const target = at(targetAz, needed);
    ctx.beginPath();
    ctx.arc(target.x, target.y, 8, 0, Math.PI * 2);
    ctx.strokeStyle = accent;
    ctx.lineWidth = 2;
    ctx.stroke();
  }
  if (wedge !== null) {
    const actual = at(knownHeading ? heading : targetAz, wedge);
    ctx.beginPath();
    ctx.arc(actual.x, actual.y, 6.5, 0, Math.PI * 2);
    ctx.fillStyle = dot;
    ctx.fill();
  }
  ctx.font = "600 15px Inter, Segoe UI, sans-serif";
  ctx.textBaseline = "alphabetic";
  ctx.textAlign = "left";
  ctx.fillStyle = text;
  ctx.fillText(wedge === null ? "Current —" : `Current ${wedge.toFixed(1)}°`, 14, height - 14);
  ctx.textAlign = "right";
  ctx.fillStyle = muted;
  ctx.font = "14px Inter, Segoe UI, sans-serif";
  ctx.fillText(needed === null ? "Needed —" : `Needed ${needed.toFixed(1)}°`, width - 14, height - 14);
}

function equatorialGuide(wedge, needed, latitude, heading) {
  const south = Number.isFinite(latitude) && latitude < 0;
  const pole = south ? "south" : "north";
  const mark = south ? "S" : "N";
  const parts = [];
  if (wedge === null) parts.push("The current angle appears when the telescope is connected.");
  else if (needed === null) parts.push(`The wedge is ${wedge.toFixed(1)}°. Save a place in Settings for the angle this location needs.`);
  else if (Math.abs(wedge - needed) < 0.5) parts.push(`The wedge is ${wedge.toFixed(1)}°, which matches this place.`);
  else if (wedge > needed) parts.push(`The wedge is ${wedge.toFixed(1)}°. This place needs ${needed.toFixed(1)}°, so lower it ${(wedge - needed).toFixed(1)}°.`);
  else parts.push(`The wedge is ${wedge.toFixed(1)}°. This place needs ${needed.toFixed(1)}°, so raise it ${(needed - wedge).toFixed(1)}°.`);
  if (!Number.isFinite(heading)) parts.push(`You are looking up through the dome. True ${pole} is ${south ? "at the bottom" : "at the top"}, and east is on the left.`);
  else {
    const target = south ? 180 : 0;
    let delta = ((heading % 360) + 360) % 360 - target;
    if (delta > 180) delta -= 360;
    if (delta < -180) delta += 360;
    if (Math.abs(delta) < 3) parts.push(`The dot is on true ${pole}.`);
    else {
      const side = delta > 0 ? "east" : "west";
      parts.push(`The wedge points ${Math.abs(delta).toFixed(0)}° ${side} of true ${pole}. Turn it until the dot meets ${mark}. East is on the left, as in the sky.`);
    }
  }
  return parts.join(" ");
}

function paintPolar(snap) {
  const total = document.querySelector('[data-field="pa-total"]');
  if (!total) return;
  const align = snap.align || {};
  const ax = Number(align.x ?? align.eq_offset_az);
  const ay = Number(align.y ?? align.eq_offset_alt);
  total.textContent = !Number.isNaN(ax) && !Number.isNaN(ay) ? `${Math.hypot(ax, ay).toFixed(2)}°` : "—";
}

function syncAlignTimer() {
  clearInterval(state.alignTimer);
  state.alignTimer = 0;
  const repeat = document.querySelector("#align-repeat");
  if (!repeat || !repeat.checked || state.page !== "align") return;
  state.alignTimer = setInterval(() => {
    if (state.page !== "align" || !state.snap || !state.snap.connected) return;
    post("/align", { action: "recheck" });
  }, 5000);
}

function alignText(snap) {
  const align = snap.align || {};
  if (!align.Event && align.state_code === undefined && !align.state && align.x === undefined) return "Not started";
  const x = align.x ?? align.eq_offset_az;
  const y = align.y ?? align.eq_offset_alt;
  return [
    align.Event,
    align.state,
    align.state_code !== undefined ? `step ${align.state_code}` : "",
    x !== undefined ? `azimuth ${Number(x).toFixed(2)}°` : "",
    y !== undefined ? `altitude ${Number(y).toFixed(2)}°` : "",
  ].filter((part) => part !== undefined && part !== "").join(" · ");
}

function filterName(position) {
  return { 0: "Dark", 1: "IR-cut", 2: "Light pollution" }[position] || (position === null || position === undefined ? "—" : `Position ${position}`);
}

function banner() {
  const snap = state.snap;
  if (state.error) return `<div class="banner bad">${escapeHtml(state.error)}</div>`;
  if (state.page === "picture" && snap && snap.live_error) return `<div class="banner bad">${escapeHtml(snap.live_error)}</div>`;
  if (!snap || !snap.connected) return `<div class="banner">Connect a telescope on Overview. The Seestar app has to be on the same Wi-Fi, in station mode.</div>`;
  if (!snap.master) return `<div class="banner">This computer is watching. Claim control before a slew, focus move, or exposure. If the Seestar app still holds the session, release it there first.</div>`;
  if (snap.last_error) return `<div class="banner bad">${escapeHtml(snap.last_error)}</div>`;
  return "";
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
}

function render() {
  const main = document.querySelector("#main");
  const view = {
    overview: renderOverview,
    sky: renderSky,
    picture: renderPicture,
    align: renderAlign,
    album: renderAlbum,
    plan: renderPlan,
    settings: renderSettings,
  }[state.page];
  main.innerHTML = view();
  bind();
  paintMeters();
}

function scopeSnap(scope) {
  if (!state.snap || state.snap.id !== scope.id) return scope;
  return { ...scope, ...state.snap };
}

function reading(icon, label, value, sub, extra) {
  return `<div class="reading"><div class="label">${icon}<span>${escapeHtml(label)}</span></div><strong>${value}</strong>${sub ? `<div class="sub">${sub}</div>` : ""}${extra || ""}</div>`;
}

function readingsHtml(snap) {
  const battery = Number(snap.battery);
  const batteryTone = Number.isNaN(battery) ? "dim" : battery < 20 ? "bad" : battery < 40 ? "warn" : "good";
  const linkMode = snap.master ? "control" : "watch";
  const linkText = snap.master ? "In control" : "Watching";
  const activity = [snap.target, snap.mode, snap.parked ? "Parked" : "", snap.equatorial === true ? "Equatorial" : snap.equatorial === false ? "Alt-az" : ""]
    .filter(Boolean).join(" · ");
  const bars = wifiBars(snap.wifi_signal);
  const address = snap.ip || snap.host || "—";
  const storage = storageView(snap);
  const storageBar = storage.pct === null ? "" : `<div class="bar ${storage.tone}"><span style="width:${storage.pct}%"></span></div>`;
  const batteryBar = Number.isNaN(battery) ? "" : `<div class="bar ${batteryTone}"><span style="width:${Math.max(0, Math.min(100, battery))}%"></span></div>`;
  const tiles = [
    reading(iconLink(linkMode), "Status", escapeHtml(linkText), escapeHtml(activity || "Idle")),
    reading(iconBattery(snap.battery), "Battery", `${fmtNum(snap.battery, "%")}${snap.charging ? " · charging" : ""}`, "", batteryBar),
    reading(iconWifiBars(bars), "Wi-Fi", escapeHtml(snap.wifi || "—"), `${escapeHtml(signalText(snap.wifi_signal))}${signalText(snap.wifi_signal) ? " · " : ""}${escapeHtml(address)}`),
    reading(iconDisk(), "Storage", escapeHtml(storage.text), escapeHtml(storage.sub), storageBar),
    reading(iconFirmware(), "Firmware", escapeHtml(snap.firmware_label || snap.firmware || "—"), escapeHtml(scopeLabel(snap.model))),
    reading(svg(`<circle cx="12" cy="12" r="7" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>`), "Pointing", fmtRA(snap.ra), fmtDec(snap.dec)),
  ];
  if (snap.temperature !== null && snap.temperature !== undefined && snap.temperature !== "") {
    tiles.push(reading(iconTemp(), "Temperature", `${fmtNum(snap.temperature, "°")}`, "Telescope"));
  }
  return tiles.join("");
}

function scopeActions(scope) {
  const held = Boolean(scope.connected && scope.master);
  return `
    <div class="actions">
      <button class="primary" data-connect="${escapeHtml(scope.id)}" title="${scope.connected && scope.id === state.selected ? "Disconnect" : "Connect"}">${scope.connected && scope.id === state.selected ? "Connected" : "Connect"}</button>
      <button data-control="${escapeHtml(scope.id)}" class="${held ? "primary" : ""}">${held ? "In control" : "Watching"}</button>
      <button class="warn" data-power="restart" data-id="${escapeHtml(scope.id)}">Restart</button>
      <button class="danger" data-power="shutdown" data-id="${escapeHtml(scope.id)}">Shut down</button>
    </div>`;
}

function scopeBadge(scope, live) {
  const status = live
    ? `${scope.master ? "In control" : "Watching"} · ${scope.host}`
    : `Not connected · ${scope.host}`;
  const place = live ? `<div class="muted" data-field="place">${escapeHtml(placeText(scope))}</div>` : "";
  return `
    <div class="scope-hero">
      ${scopePicture(scope.model)}
      <div>
        <h2>${escapeHtml(scopeLabel(scope.model))}</h2>
        <div ${live ? 'data-field="link"' : ""}>${escapeHtml(status)}</div>
        ${place}
      </div>
      <div class="batt-badge${live && scope.charging ? " charging" : ""}" ${live ? "data-battery" : ""} title="${escapeHtml(live ? `${fmtNum(scope.battery, "%")}${scope.charging ? ", charging" : ""}` : "Not connected")}">${batteryBadge(live ? scope.battery : null, live && scope.charging)}</div>
    </div>`;
}

function renderOverview() {
  const scopes = state.scopes.map(scopeSnap);
  const current = scopes.find((scope) => scope.connected && scope.id === state.selected) || null;
  const remembered = scopes.filter((scope) => (scope.linked || scope.connected) && (!current || scope.id !== current.id));
  const fresh = scopes.filter((scope) => !scope.linked && !scope.connected);
  const past = remembered.map((scope) => `
    <div class="past-scope">
      ${scopeBadge(scope, false)}
      <div class="actions"><button data-connect="${escapeHtml(scope.id)}">Connect</button></div>
    </div>`).join("");
  const freshRows = fresh.map((scope) => `
    <div class="night-row">
      <span>${escapeHtml(scope.name || scope.host)} <span class="muted">${escapeHtml(scope.host)}</span></span>
      <button data-connect="${escapeHtml(scope.id)}">Connect</button>
    </div>`).join("");
  const hero = current ? `
    ${scopeBadge(current, true)}
    <div class="wifi-box">
      <div class="kicker">Wi-Fi</div>
      <b data-field="wifi-line">${escapeHtml([current.wifi || "—", signalText(current.wifi_signal), current.ip].filter(Boolean).join(" · "))}</b>
      <div class="muted" data-field="storage-line">${escapeHtml(storageView(current).text)}</div>
    </div>
    ${scopeActions(current)}
    ${past}` : `
    ${past || `<p class="muted">No telescope is connected. Turn it on, then search this network.</p>`}
    ${freshRows}`;
  const title = current
    ? (current.name || "My telescope")
    : (remembered.length === 1 ? (remembered[0].name || "My telescope") : remembered.length ? "Telescopes" : "My telescope");
  return `
    ${banner()}
    <section class="panel">
      <p class="kicker">Start here</p>
      <h2>Three steps to the first picture</h2>
      <div class="steps">
        <button class="step" data-find="1"><b>1. Connect the telescope</b><span>Turn it on. Desk searches this network.</span></button>
        <button class="step" id="open-sky"><b>2. Choose a target</b><span>Pick an object on the sky page.</span></button>
        <button class="step" id="open-picture"><b>3. Start the session</b><span>Focus, expose, and stack.</span></button>
      </div>
      <button class="primary wide" id="open-sky-wide">Open the sky</button>
    </section>
    <section class="board">
      <article class="panel">
        <p class="kicker">Telescope</p>
        <h2>${escapeHtml(title)}</h2>
        ${hero}
      </article>
      <article class="panel">
        <p class="kicker">Current target</p>
        <h2>Observation</h2>
        <div class="stage dash">
          <img id="stage" alt="Latest view from the telescope">
          <div class="placeholder" id="stage-empty">No target selected</div>
        </div>
        <div class="night-row"><b data-field="target">${escapeHtml((current && current.target) || "No target selected")}</b><span class="muted" data-field="goto">${escapeHtml(current ? gotoText(current) : "")}</span></div>
      </article>
      <article class="panel">
        <p class="kicker">Live stack</p>
        <h2>Stacked image</h2>
        <div class="stage dash">
          <img id="stack-stage" alt="Latest stacked frame">
          <div class="placeholder" id="stack-stage-empty">The stack appears here</div>
        </div>
        <div class="night-row"><b data-field="stack">${escapeHtml(current ? stackText(current) : "Idle")}</b></div>
        <div class="actions">
          <button class="primary" id="stack-start">Start stack</button>
          <button id="stack-stop">Stop stack</button>
        </div>
      </article>
    </section>
    <section class="board two">
      <article class="panel">
        <p class="kicker">Planning for tonight</p>
        <div class="row"><h2>Your night</h2><button id="open-plan">Open the plan</button></div>
        <div id="tonight-list">${current ? tonightHtml(current) : `<p class="muted">Connect a telescope to plan the night.</p>`}</div>
      </article>
      <article class="panel">
        <p class="kicker">Conditions</p>
        <h2>Tonight</h2>
        <div id="conditions">${placeNow(current || {}) ? conditionsHtml(current || {}) : `<p class="muted">Save a place in Settings. The desk also keeps the last place reported by a telescope.</p>`}</div>
      </article>
    </section>
    <form class="manual-add form" id="manual">
      <label>IP address<input name="host" placeholder="192.168.1.50" required></label>
      <label>Name<input name="name" placeholder="Back garden"></label>
      <div class="actions"><button type="button" id="find">Find telescopes</button><button class="primary" type="submit">Add telescope</button></div>
    </form>`;
}

function renderSky() {
  return `
    <div class="page-head">
      <h1>Sky</h1>
      <div class="chart-switch">
        <button type="button" data-chart="stellarium" class="${state.starChart === "gaia" ? "" : "primary"}">Stellarium</button>
        <button type="button" data-chart="gaia" class="${state.starChart === "gaia" ? "primary" : ""}">Gaia</button>
        <button type="button" id="star-depth" class="${state.starDepth === 12 ? "primary" : ""}">Magnitude 12</button>
        <button type="button" id="sky-pictures" class="${state.skyPictures ? "primary" : ""}">Pictures</button>
      </div>
    </div>
    <p class="muted chart-note">${chartNote()}</p>
    ${banner()}
    <div class="skymap-wrap card">
      <canvas id="skymap" class="skymap"></canvas>
      <div class="placeholder" id="skymap-status">Loading the sky map…</div>
    </div>
    <div class="card sky-pick" id="sky-pick">
      <div>
        <h2 id="sky-pick-name">Click an object</h2>
        <p class="muted" id="sky-pick-detail">The map uses the saved place and, when the telescope is connected, its clock. View now points the telescope. Add to tonight puts it on the plan.</p>
      </div>
      <div class="actions">
        <button class="primary" id="sky-view" type="button" disabled>View now</button>
        <button id="sky-plan" type="button" disabled>Add to tonight</button>
        <button id="best-tonight" type="button">Best tonight</button>
      </div>
    </div>
    <section class="card tonight" id="tonight-card" hidden>
      <h2>Best tonight</h2>
      <p class="muted" id="tonight-note"></p>
      <div class="list" id="best-list"></div>
    </section>
    <div class="work">
      <section class="card">
        <div class="row">
          <div>Right ascension <b data-field="ra"></b></div>
          <div>Declination <b data-field="dec"></b></div>
          <div>Altitude <b data-field="alt"></b></div>
          <div>Azimuth <b data-field="az"></b></div>
          <div>Tracking <b data-field="tracking"></b></div>
        </div>
        <p>GoTo <b data-field="goto"></b></p>
        <div class="actions">
          <button id="track-on">Track on</button>
          <button id="track-off">Track off</button>
          <button id="horizon">Level the arm</button>
          <button class="danger" id="park">Park</button>
          <button id="stop">Stop</button>
        </div>
      </section>
      <section class="cards">
        <div class="card">
          <h2>Nudge</h2>
          <div class="pad" id="pad"><span class="n">up</span><span class="e">right</span><span class="s">down</span><span class="w">left</span></div>
          <div class="actions">
            <button data-speed="400" class="${state.speed === 400 ? "primary" : ""}">Slow</button>
            <button data-speed="1500" class="${state.speed === 1500 ? "primary" : ""}">Medium</button>
            <button data-speed="3500" class="${state.speed === 3500 ? "primary" : ""}">Fast</button>
          </div>
          <div class="muted">Press and hold. Slow is for centering, fast is for a bigger jump.</div>
        </div>
        <form class="card" id="goto">
          <h2>Go to</h2>
          <label>Search<input id="query" value="${escapeHtml(state.query)}" placeholder="Orion, M31, Veil"></label>
          <div class="list" id="results"></div>
          <div class="form">
            <label>Name<input name="name" id="target-name"></label>
            <label>RA hours<input name="ra" id="target-ra" placeholder="5.583"></label>
            <label>Dec °<input name="dec" id="target-dec" placeholder="-5.383"></label>
            <label>Mode
              <select name="mode">
                <option value="star">Deep sky</option>
                <option value="moon">Moon</option>
                <option value="planet">Planet</option>
                <option value="scenery">Scenery</option>
              </select>
            </label>
          </div>
          <label class="row"><input type="checkbox" name="lp_filter"> Use the light-pollution filter for this GoTo</label>
          <div class="actions"><button class="primary" type="submit">Go to target</button></div>
        </form>
      </section>
    </div>`;
}

function renderPicture() {
  return `
    <div class="page-head">
      <div>
        <h1>Picture</h1>
        <div class="cam-switch" role="group" aria-label="Live view">
          <button id="live-tele" data-camera="tele" class="${state.camera === "wide" ? "" : "primary"}" aria-pressed="${state.camera === "wide" ? "false" : "true"}">Telephoto</button>
          <button id="live-wide" data-camera="wide" class="${state.camera === "wide" ? "primary" : ""}" aria-pressed="${state.camera === "wide" ? "true" : "false"}">Wide angle</button>
        </div>
      </div>
      <div class="actions">
        <button id="layout">${state.layout === "theater" ? "Show controls" : "Theater"}</button>
        <button id="live-stop">Stop live view</button>
        <button id="play-tv">Play on TV</button>
      </div>
    </div>
    ${banner()}
    <div class="stage stack-view">
      <img id="stack-stage" alt="Latest stacked frame">
      <div class="placeholder" id="stack-stage-empty">The stack appears here</div>
      <div class="overlay"><span class="card" style="padding:0.35rem 0.6rem">Stack · <span data-field="stack"></span></span></div>
    </div>
    <div class="work">
      <div>
        <div class="stage live-view">
          <img id="stage" alt="">
          <div class="placeholder" id="stage-empty">Opening the telephoto…</div>
          <div class="overlay"><span class="card" style="padding:0.35rem 0.6rem"><span data-live-camera>${state.camera === "wide" ? "Wide angle" : "Telephoto"}</span> · <span data-field="target"></span></span></div>
        </div>
        <div class="nudge card">
          <div class="pad" id="pad"><span class="n">up</span><span class="e">right</span><span class="s">down</span><span class="w">left</span></div>
          <div>
            <h2>Nudge</h2>
            <div class="actions">
              <button data-speed="400" class="${state.speed === 400 ? "primary" : ""}">Slow</button>
              <button data-speed="1500" class="${state.speed === 1500 ? "primary" : ""}">Medium</button>
              <button data-speed="3500" class="${state.speed === 3500 ? "primary" : ""}">Fast</button>
            </div>
            <div class="muted">Press and hold. Slow is for centering, fast is for a bigger jump.</div>
          </div>
        </div>
      </div>
      <div class="cards">
        <div class="card">
          <h2>Exposure</h2>
          <div>Stack <b data-field="stack"></b></div>
          <div class="muted">Mode <b data-field="mode"></b></div>
          <div class="form">
            <label>Gain<input id="gain" type="number" value="80" min="0" max="400"></label>
            <label class="row"><input id="restart" type="checkbox" checked> Restart the stack</label>
          </div>
          <div class="actions">
            <button class="primary" id="stack-start">Start stack</button>
            <button id="stack-stop">Stop stack</button>
          </div>
        </div>
        <div class="card">
          <h2>Focus</h2>
          <div>Position <b data-field="focus"></b></div>
          <div class="actions">
            <button data-focus="-50">−50</button>
            <button data-focus="-10">−10</button>
            <button data-focus="10">+10</button>
            <button data-focus="50">+50</button>
            <button class="primary" id="autofocus">Auto focus</button>
            <button id="focus-stop">Stop focus</button>
          </div>
        </div>
        <div class="card">
          <h2>Filter</h2>
          <div>Now <b data-field="filter"></b></div>
          <div class="actions">
            <button data-filter="1">IR-cut</button>
            <button data-filter="2">Light pollution</button>
            <button data-filter="0">Dark</button>
          </div>
        </div>
        <form class="card" id="mosaic">
          <h2>Mosaic</h2>
          <p class="muted">Several panels around the current coordinates, each stacked for the minutes you set.</p>
          <div class="form">
            <label>Rows<input name="rows" type="number" min="1" max="6" value="2"></label>
            <label>Columns<input name="cols" type="number" min="1" max="6" value="2"></label>
            <label>Overlap %<input name="overlap" type="number" min="0" max="60" value="20"></label>
            <label>Minutes each<input name="minutes" type="number" min="0" step="0.5" value="10"></label>
          </div>
          <div class="actions"><button class="primary" type="submit">Run mosaic</button></div>
        </form>
        <form class="card" id="video">
          <h2>Planet or scenery</h2>
          <div class="form">
            <label>Mode
              <select name="mode">
                <option value="planet">Planet</option>
                <option value="moon">Moon</option>
                <option value="scenery">Scenery</option>
              </select>
            </label>
            <label>Minutes<input name="minutes" type="number" min="0" step="0.5" value="3"></label>
          </div>
          <div class="actions">
            <button class="primary" type="submit">Start recording</button>
            <button type="button" id="video-stop">Stop</button>
          </div>
        </form>
      </div>
    </div>`;
}

function renderAlign() {
  const ready = localStorage.getItem("seestar-eq-ready") === "1";
  const repeat = localStorage.getItem("seestar-align-repeat") === "1";
  return `
    <div class="page-head"><h1>Level, mount and alignment</h1></div>
    ${banner()}
    <div class="align-board">
      <section class="card eq-setup">
        <h2>Equatorial setup</h2>
        <div class="eq-screen">
          <canvas id="eq-dome" width="640" height="440" aria-label="Dome with true north and the current wedge angle"></canvas>
          <div class="eq-angles">
            <div>
              <span class="muted">Current angle</span>
              <p class="level-readout" data-field="eq-now">—</p>
            </div>
            <div>
              <span class="muted">Needed here</span>
              <p class="level-readout needed" data-field="eq-need">—</p>
            </div>
            <p class="muted eq-guide" data-field="eq-guide">Look up through the dome. Set the wedge to the angle for this place, then turn it until the dot meets true north. East is on the left.</p>
          </div>
        </div>
        <label class="row"><input id="eq-ready" type="checkbox"${ready ? " checked" : ""}> The scope is on a wedge, set to this latitude, facing the pole, and the arm can swing freely</label>
        <div class="actions">
          <button id="to-eq"${ready ? "" : " disabled"}>Switch to equatorial</button>
          <button id="to-az">Switch to alt-az</button>
        </div>
        <p class="muted">Switching mode levels the arm and then parks. That is how the telescope changes between alt-az and equatorial.</p>
      </section>
      <section class="card level-card">
        <h2>Tripod level</h2>
        <div class="bubble" id="level-bubble" title="The scale is 5 degrees at the edge"><span class="dot"></span></div>
        <p class="level-readout" data-field="level-angle">—</p>
        <p class="muted" data-field="level-note">The level appears after the telescope connects.</p>
      </section>
      <div class="align-side">
        <section class="card">
          <h2>Current setup</h2>
          <div class="facts">
            <div class="fact"><span>Mode</span><b data-field="mount">—</b></div>
            <div class="fact"><span>Arm</span><b data-field="arm">—</b></div>
            <div class="fact"><span>Tracking</span><b data-field="tracking">—</b></div>
            <div class="fact"><span>Compass</span><b data-field="heading">—</b></div>
            <div class="fact"><span>Location</span><b data-field="place">—</b></div>
          </div>
          <div class="actions">
            <button id="open-arm">Open the arm</button>
            <button class="danger" id="park">Park and fold</button>
          </div>
        </section>
        <section class="card">
          <h2>Align the wedge to the pole</h2>
          <p data-field="align">Not started</p>
          <p class="level-readout" data-field="pa-total">—</p>
          <label>Measure in this part of the sky
            <select id="dec-band">
              <option value="2">Lower in the sky</option>
              <option value="3" selected>Overhead</option>
              <option value="4">Higher, nearer the pole</option>
            </select>
          </label>
          <div class="actions">
            <button class="primary" id="align-start">Measure polar error</button>
            <button id="align-recheck">Measure here</button>
            <button id="align-live"${repeat ? ' class="primary"' : ""}>Live adjust</button>
            <button id="align-stop">Stop</button>
          </div>
          <label class="row"><input id="align-repeat" type="checkbox"${repeat ? " checked" : ""}> Repeat readings automatically, every 5 seconds</label>
          <div class="crosshair" id="pa-plot" title="Bring the dot to the center with the wedge knobs"><span class="dot"></span></div>
          <p class="muted">Azimuth is left and right, altitude is up and down. Turn the wedge knobs until the dot sits in the center. Live adjust waits until you let go, then reads again.</p>
        </section>
      </div>
    </div>`;
}

function renderAlbum() {
  const placed = state.gallery.filter((item) => item.ra !== null && item.ra !== undefined && item.dec !== null && item.dec !== undefined);
  const loose = state.gallery.filter((item) => item.ra === null || item.ra === undefined || item.dec === null || item.dec === undefined);
  const pick = galleryItem(state.galleryPick) || placed[0] || state.gallery[0] || null;
  if (pick) state.galleryPick = pick.name;
  const looseRow = loose.map((item) => `
    <button type="button" class="gallery-chip${item.name === state.galleryPick ? " primary" : ""}" data-gallery="${escapeHtml(item.name)}">
      ${item.thumb ? `<img alt="" src="${gallerySrc(item, false)}">` : ""}
      <span>${escapeHtml(item.name)}</span>
    </button>`).join("");
  return `
    <div class="page-head"><h1>Album</h1><button id="reload-album">Refresh</button></div>
    ${banner()}
    <section class="card gallery-section">
      <h2>Gallery</h2>
      <p class="muted">Each picture sits on the sky at the place it was taken. Click one to open it large.</p>
      <div class="gallery-layout">
        <div class="skymap-wrap gallery-map">
          <canvas id="gallery-map" class="skymap gallery-map"></canvas>
          <div class="placeholder" id="gallery-status">${state.selected ? "Loading the sky…" : "Connect a telescope to place its pictures."}</div>
        </div>
        <figure class="gallery-view">
          ${pick && pick.thumb ? `<img id="gallery-photo" alt="${escapeHtml(pick.name)}" src="${gallerySrc(pick, true)}">` : `<div class="placeholder" id="gallery-photo-empty">${state.gallery.length ? "This session has no preview." : "No pictures yet."}</div>`}
          <figcaption>
            <h2 id="gallery-name">${escapeHtml(pick ? pick.name : "No picture selected")}</h2>
            <p class="muted" id="gallery-place">${escapeHtml(galleryCaption(pick))}</p>
          </figcaption>
        </figure>
      </div>
      ${looseRow ? `<div class="gallery-loose"><p class="muted">These sessions have no sky position yet.</p>${looseRow}</div>` : ""}
      <p class="muted gallery-count">${placed.length} on the sky${loose.length ? ` · ${loose.length} without a position` : ""}</p>
    </section>
    <section class="card transfer-window">
      <div class="transfer-head">
        <div>
          <h2>File transfer</h2>
          <p class="muted" id="download-dest">${escapeHtml(downloadDestText())}</p>
        </div>
        <div class="actions">
          <button type="button" id="choose-folder">Choose folder</button>
          <button type="button" id="select-all">Select all files</button>
          <button class="primary" id="copy">Copy selected to this computer</button>
        </div>
      </div>
      <div class="transfer-grid">
        <div>
          <h3>Sessions</h3>
          <div class="list transfer-list" id="session-list">${sessionButtons() || '<div class="muted">No sessions reported yet.</div>'}</div>
        </div>
        <div>
          <h3 id="file-heading">${escapeHtml(state.folder || "Files")}</h3>
          <div class="list transfer-list" id="file-list">${fileRows()}</div>
          <label class="row"><input id="delete-after" type="checkbox"> Delete the selected files from the telescope after the copy</label>
        </div>
      </div>
    </section>`;
}

function sessionButtons() {
  return state.folders.map((folder) => `
    <button data-folder="${escapeHtml(folder.name)}" class="${folder.name === state.folder ? "primary" : ""}">
      <span>${escapeHtml(folder.name)}</span>
      <span class="muted">${escapeHtml(folder.group || "")} · ${folder.count ?? ""}</span>
    </button>`).join("");
}

function fileRows() {
  if (!state.files.length) return '<div class="muted">Open a session to see its files.</div>';
  return state.files.map((file) => file.is_dir ? `
    <div class="file"><span>${escapeHtml(file.name)}</span><span class="muted">Folder</span></div>` : `
    <label class="file"><span>${fileThumb(file)}<input type="checkbox" name="file" value="${escapeHtml(file.name)}"> ${escapeHtml(file.name)}</span>
      <span class="muted">${file.size_k ? file.size_k + " KB" : ""}</span></label>`).join("");
}

function galleryItem(name) {
  return state.gallery.find((item) => item.name === name) || null;
}

function gallerySrc(item, large) {
  let path = String(item.thumb || "");
  if (large) path = path.replace(/_thn(?=\.)/i, "");
  return `${scopePath("/media")}?path=${encodeURIComponent("MyWorks/" + path)}`;
}

function galleryCaption(item) {
  if (!item) return "Pictures appear here after the telescope reports a session.";
  if (item.ra === null || item.ra === undefined || item.dec === null || item.dec === undefined) {
    return "No sky position for this session.";
  }
  const kind = item.kind ? `${item.kind} · ` : "";
  return `${kind}${fmtRA(item.ra)} · ${fmtDec(item.dec)}`;
}

function selectGallery(name) {
  const item = galleryItem(name);
  if (!item) return;
  state.galleryPick = item.name;
  const photo = document.querySelector("#gallery-photo");
  const caption = document.querySelector("#gallery-place");
  const title = document.querySelector("#gallery-name");
  if (title) title.textContent = item.name;
  if (caption) caption.textContent = galleryCaption(item);
  if (photo && item.thumb) {
    photo.alt = item.name;
    photo.dataset.fallback = "";
    photo.src = gallerySrc(item, true);
  }
  document.querySelectorAll("[data-gallery]").forEach((button) => {
    button.classList.toggle("primary", button.dataset.gallery === item.name);
  });
  const engine = state.galleryEngine;
  if (engine && item.ra !== null && item.ra !== undefined) lookAtHours(engine, item.ra, item.dec, 0.28);
}

function lookAtHours(engine, raHours, decDeg, fov) {
  if (!engine || !engine.lookAt) return;
  const ra = raHours * Math.PI / 12;
  const dec = decDeg * Math.PI / 180;
  const scale = Math.cos(dec);
  engine.lookAt([Math.cos(ra) * scale, Math.sin(ra) * scale, Math.sin(dec)], fov);
}

function paintTransfer() {
  const files = document.querySelector("#file-list");
  const heading = document.querySelector("#file-heading");
  if (!files) return;
  if (heading) heading.textContent = state.folder || "Files";
  files.innerHTML = fileRows();
  document.querySelectorAll("[data-folder]").forEach((button) => {
    button.classList.toggle("primary", button.dataset.folder === state.folder);
  });
}

function fileThumb(file) {
  const name = String(file.name || "");
  let preview = "";
  if (/\.(jpg|jpeg|png)$/i.test(name)) preview = name;
  else if (name.toLowerCase().endsWith(".fit")) {
    const thumb = `${name.slice(0, -4)}_thn.jpg`;
    if (state.files.some((item) => item.name === thumb)) preview = thumb;
  }
  if (!preview || !state.folder) return "";
  const path = `MyWorks/${state.folder}/${preview}`;
  return `<img class="mini" alt="" src="${scopePath("/media")}?path=${encodeURIComponent(path)}">`;
}

function planStatus(snap) {
  const plan = (snap && snap.plan) || {};
  const status = plan.status || {};
  const sky = plan.sky || {};
  return [status.step, status.detail, sky.darkness].filter(Boolean).join(" · ") || "Idle";
}

function renderPlan() {
  const plan = (state.snap && state.snap.plan) || {};
  const safety = plan.safety || { min_battery: 20, min_altitude: 25, stop_before_dawn: true, max_cloud: 80 };
  const weather = plan.weather || {};
  const sky = plan.sky || {};
  const rows = state.planItems.map((item, index) => `
    <div class="file">
      <span>${escapeHtml(item.name)} · ${escapeHtml(item.mode)} · ${item.minutes} min${item.rows > 1 || item.cols > 1 ? ` · ${item.rows}×${item.cols}` : ""}</span>
      <span class="muted">${item.altitude === undefined || item.altitude === null ? "" : item.altitude + "°"}${item.reasons && item.reasons.length ? " · " + escapeHtml(item.reasons[0]) : ""}</span>
      <button type="button" data-drop="${index}">Remove</button>
    </div>`).join("");
  const clouds = weather.cloud_cover === undefined || weather.cloud_cover === null ? "—" : `${weather.cloud_cover}% cloud`;
  const windowText = weather.window ? ` · clearest ${weather.window}` : "";
  const dewText = weather.dew_risk ? ` · dew ${weather.dew_risk.toLowerCase()}` : "";
  return `
    <div class="page-head"><h1>Plan</h1><div class="muted" data-field="plan"></div></div>
    ${banner()}
    <section class="card">
      <h2>Tonight</h2>
      <p>${escapeHtml(sky.note || sky.darkness || "Sky not checked yet.")}${sky.sun_altitude !== undefined && sky.sun_altitude !== null ? ` · sun ${sky.sun_altitude}°` : ""}</p>
      <p class="muted">${escapeHtml(weather.error || `${clouds}${windowText}${dewText}${weather.wind_speed !== undefined && weather.wind_speed !== null ? ` · wind ${weather.wind_speed} m/s` : ""}${weather.precipitation ? ` · rain ${weather.precipitation} mm` : ""}`)}</p>
      <div class="actions"><button id="weather" type="button">Refresh sky and weather</button></div>
    </section>
    <form class="card" id="plan-add" style="margin-top:0.8rem">
      <h2>Add a target</h2>
      <label>Search<input id="plan-query" placeholder="M31, Orion"></label>
      <div class="list" id="plan-results"></div>
      <div class="form">
        <label>Name<input name="name" id="plan-name" required></label>
        <label>RA hours<input name="ra" id="plan-ra" required></label>
        <label>Dec °<input name="dec" id="plan-dec" required></label>
        <label>Mode
          <select name="mode">
            <option value="star">Deep sky</option>
            <option value="planet">Planet</option>
            <option value="moon">Moon</option>
            <option value="scenery">Scenery</option>
          </select>
        </label>
        <label>Minutes<input name="minutes" type="number" min="0" step="0.5" value="15"></label>
        <label>Rows<input name="rows" type="number" min="1" max="6" value="1"></label>
        <label>Columns<input name="cols" type="number" min="1" max="6" value="1"></label>
        <label>Overlap %<input name="overlap" type="number" min="0" max="60" value="20"></label>
        <label>Gain<input name="gain" type="number" value="80"></label>
      </div>
      <label class="row"><input name="autofocus" type="checkbox" checked> Autofocus before each panel</label>
      <div class="actions"><button class="primary" type="submit">Add to the list</button></div>
    </form>
    <section class="card" style="margin-top:0.8rem">
      <h2>List</h2>
      <div class="list">${rows || '<div class="muted">No targets yet.</div>'}</div>
      <label class="row"><input id="park-end" type="checkbox"> Park when the list finishes. This powers the telescope down.</label>
      <div class="form">
        <label>Stop below battery %<input id="min-battery" type="number" min="0" max="100" value="${safety.min_battery ?? 20}"></label>
        <label>Stop below altitude °<input id="min-altitude" type="number" value="${safety.min_altitude ?? 25}"></label>
        <label>Skip above cloud %<input id="max-cloud" type="number" min="0" max="100" value="${safety.max_cloud ?? 80}"></label>
      </div>
      <label class="row"><input id="stop-dawn" type="checkbox" ${safety.stop_before_dawn === false ? "" : "checked"}> Stop when the sky is no longer dark</label>
      <div class="actions">
        <button class="primary" id="plan-start" type="button">Run the list</button>
        <button id="plan-stop" type="button">Stop</button>
      </div>
    </section>`;
}

function renderSettings() {
  const snap = state.snap || {};
  const settings = snap.settings || {};
  const exp = (settings.exp_ms && settings.exp_ms.stack_l) || 10000;
  const dither = settings.stack_dither || {};
  const lines = (state.log.length ? state.log : snap.log || []).slice(-40).map((entry) =>
    `${entry.time || ""}  ${entry.event || ""}  ${entry.state || ""}  ${entry.detail || ""}`).join("\n");
  return `
    <div class="page-head"><h1>Settings</h1></div>
    ${banner()}
    <section class="card">
      <h2>Appearance</h2>
      <p class="muted">The choice stays on this browser.</p>
      <div class="theme-switch">
        ${themes.map((item) => `<button type="button" data-theme-choice="${item.id}" class="${state.theme === item.id ? "primary" : ""}"><span class="swatch" style="background:${item.swatch}"></span>${item.name}</button>`).join("")}
      </div>
    </section>
    <form class="card" id="site" style="margin-top:0.8rem">
      <h2>Place and clock</h2>
      <p class="muted">Saved place <b>${escapeHtml(placeText(snap))}</b>. It stays on this computer when the telescope is off.</p>
      <p class="muted">Telescope clock <b>${escapeHtml(snap.clock || "—")}</b></p>
      <div class="form">
        <label>Latitude<input name="lat" value="${coordValue(placeNow(snap) && placeNow(snap).latitude)}" placeholder="51.5"></label>
        <label>Longitude<input name="lon" value="${coordValue(placeNow(snap) && placeNow(snap).longitude)}" placeholder="-0.12"></label>
      </div>
      <div class="actions">
        <button class="primary" type="submit">Save location</button>
        <button type="button" id="set-clock">Set the telescope clock from this computer</button>
      </div>
    </form>
    <section class="card" style="margin-top:0.8rem">
      <h2>Wi-Fi</h2>
      <p class="muted">Current network <b>${escapeHtml((snap.station && snap.station.ssid) || snap.wifi || "—")}</b>. Joining a network makes the telescope leave the one it is on now.</p>
      <div class="actions"><button id="wifi-scan" type="button">Scan networks</button></div>
      <div class="list" id="wifi-list">${state.networks.map((item) => `<button type="button" data-ssid="${escapeHtml(item.ssid)}">${escapeHtml(item.ssid)} <span class="muted">${item.rssi ?? ""}</span></button>`).join("")}</div>
      <form class="form" id="wifi-join">
        <label>Network<input name="ssid" id="wifi-ssid" required></label>
        <label>Password<input name="password" type="password"></label>
        <div class="actions"><button class="primary" type="submit">Join</button></div>
      </form>
    </section>
    <section class="card" style="margin-top:0.8rem">
      <h2>Power</h2>
      <p class="muted">Restart drops the link and the telescope comes back by itself. Shut down powers it off until you switch it on.</p>
      <div class="actions">
        <button class="warn" id="restart-scope" type="button">Restart telescope</button>
        <button class="danger" id="shutdown-scope" type="button">Shut down telescope</button>
      </div>
    </section>
    <form class="card" id="imaging" style="margin-top:0.8rem">
      <h2>Stack and dew</h2>
      <div class="form">
        <label>Stack exposure
          <select name="exp">
            ${[10000, 20000, 30000, 60000].map((value) => `<option value="${value}" ${Number(exp) === value ? "selected" : ""}>${value / 1000} s</option>`).join("")}
          </select>
        </label>
        <label>Dither pixels<input name="pix" type="number" value="${dither.pix ?? 50}"></label>
        <label>Dither interval<input name="interval" type="number" value="${dither.interval ?? 5}"></label>
        <label>Heater power<input name="power" type="number" min="0" max="100" value="50"></label>
      </div>
      <label class="row"><input name="dither" type="checkbox" ${dither.enable ? "checked" : ""}> Dither between frames</label>
      <label class="row"><input name="heater" type="checkbox" ${settings.heater_enable ? "checked" : ""}> Dew heater</label>
      <div class="actions"><button class="primary" type="submit">Save imaging settings</button></div>
    </form>
    <section class="card" style="margin-top:0.8rem">
      <h2>Firmware ${escapeHtml(snap.firmware || "—")}</h2>
      <p class="muted">Solar imaging and firmware updates stay in the official Seestar app until a solar-filter flow has been checked on hardware.</p>
      <div class="log">${escapeHtml(lines || "No events yet.")}</div>
    </section>`;
}

function loadScript(url) {
  return new Promise((resolve, reject) => {
    if (document.querySelector(`script[src="${url}"]`)) {
      resolve();
      return;
    }
    const script = document.createElement("script");
    script.src = url;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error("The sky map did not load."));
    document.body.appendChild(script);
  });
}

function skyDate(clock) {
  if (!clock) return new Date();
  const parsed = new Date(String(clock).replace(" ", "T"));
  return Number.isNaN(parsed.getTime()) ? new Date() : parsed;
}

function skyMode(name) {
  const planet = { Moon: "moon", Mercury: "planet", Venus: "planet", Mars: "planet", Jupiter: "planet", Saturn: "planet", Uranus: "planet", Neptune: "planet" };
  return planet[name] || "star";
}

function skyObjectName(obj) {
  let names = [];
  try { names = obj.designations() || []; } catch (error) { names = []; }
  const named = names.find((item) => String(item).startsWith("NAME ")) || names[0] || "";
  return String(named).replace(/^NAME\s+/, "").replace(/^M\s+/, "M") || "Sky position";
}

function readSkyPick(engine) {
  const obj = engine.core && engine.core.selection;
  const title = document.querySelector("#sky-pick-name");
  const detail = document.querySelector("#sky-pick-detail");
  const view = document.querySelector("#sky-view");
  const plan = document.querySelector("#sky-plan");
  if (!obj || !obj.getInfo) {
    state.skyPick = null;
    if (title) title.textContent = "Click an object";
    if (detail) detail.textContent = "Nothing was selected. Click a star, planet, or deep-sky object.";
    if (view) view.disabled = true;
    if (plan) plan.disabled = true;
    return;
  }
  const vector = obj.getInfo("radec");
  if (!vector || vector.length < 3) return;
  let ra = Math.atan2(vector[1], vector[0]);
  if (ra < 0) ra += Math.PI * 2;
  const dec = Math.atan2(vector[2], Math.hypot(vector[0], vector[1])) * 180 / Math.PI;
  const name = skyObjectName(obj);
  const pick = { name, ra: ra * 12 / Math.PI, dec, mode: skyMode(name) };
  state.skyPick = pick;
  if (title) title.textContent = name;
  if (detail) detail.textContent = `${fmtRA(pick.ra)} · ${fmtDec(pick.dec)} · ${pick.mode === "star" ? "Deep sky" : pick.mode}`;
  if (view) view.disabled = false;
  if (plan) plan.disabled = false;
}

function syncSkyObserver() {
  const engine = state.skyEngine;
  if (!engine || !engine.observer) return;
  const snap = state.snap;
  const here = placeNow(snap || {});
  if (!here) return;
  engine.observer.latitude = here.latitude * Math.PI / 180;
  engine.observer.longitude = here.longitude * Math.PI / 180;
  if (engine.date2MJD) engine.observer.utc = engine.date2MJD(skyDate(snap && snap.clock));
}

function prepareSky(engine) {
  const base = "/static/sky/data/";
  const sources = [
    [engine.core.stars, base + "stars"],
    [engine.core.dsos, base + "dso"],
    [engine.core.milkyway, base + "surveys/milkyway"],
  ];
  sources.forEach(([module, url]) => {
    try { module.addDataSource({ url }); } catch (error) { /* a missing survey leaves the rest of the map usable */ }
  });
  if (state.starDepth === 12) {
    try { engine.core.stars.addDataSource({ url: "/api/sky/stars", key: "extended" }); } catch (error) { /* the bright chart remains */ }
  }
  if (state.starChart === "gaia") {
    try { engine.core.stars.addDataSource({ url: "/api/sky/gaia", key: "gaia" }); } catch (error) { /* the bright chart remains */ }
  }
  if (engine.core.dss) engine.core.dss.visible = false;
  if (engine.core.atmosphere) engine.core.atmosphere.visible = false;
  if (state.skyPictures) loadNebulae(engine);
  try {
    engine.core.skycultures.addDataSource({ url: base + "skycultures/western", key: "western" });
    engine.core.constellations.lines_visible = true;
    engine.core.constellations.labels_visible = true;
  } catch (error) { /* constellation art is optional */ }
  engine.on("click", () => {
    requestAnimationFrame(() => readSkyPick(engine));
    return 0;
  });
  state.skyEngine = engine;
}

function loadNebulae(engine) {
  fetch("/static/sky/data/nebulae.json")
    .then((response) => response.json())
    .then((items) => {
      if (state.skyEngine !== engine) return;
      state.skyPhotos = items.map((item) => {
        const photo = engine.createObj("photo", {
          visible: true,
          url: `/api/sky/nebula/${item.file}`,
        });
        if (!photo) return null;
        photo.calibration = [{
          ra: item.ra,
          dec: item.dec,
          orientation: item.orientation,
          pixscale: item.widthDeg * 3600 / item.pixels,
        }];
        engine.core.add(photo);
        photo.retain();
        return photo;
      }).filter(Boolean);
    })
    .catch(() => { /* the star chart remains */ });
}

function chooseTonight(item) {
  const generic = !item.name || String(item.name).startsWith("Messier");
  const name = generic ? item.id : `${item.id} ${item.name}`;
  state.skyPick = { name, ra: item.ra, dec: item.dec, mode: "star" };
  const title = document.querySelector("#sky-pick-name");
  const detail = document.querySelector("#sky-pick-detail");
  const view = document.querySelector("#sky-view");
  const plan = document.querySelector("#sky-plan");
  if (title) title.textContent = name;
  if (detail) detail.textContent = tonightDetail(item);
  if (view) view.disabled = false;
  if (plan) plan.disabled = false;
  document.querySelectorAll("[data-tonight]").forEach((row) => {
    row.classList.toggle("selected", Number(row.dataset.tonight) === item.index);
  });
  const engine = state.skyEngine;
  if (!engine || !engine.lookAt) return;
  const ra = item.ra * Math.PI / 12;
  const dec = item.dec * Math.PI / 180;
  const scale = Math.cos(dec);
  engine.lookAt([Math.cos(ra) * scale, Math.sin(ra) * scale, Math.sin(dec)], 1);
}

function tonightDetail(item) {
  const high = item.minutes_above_40 >= 60
    ? `${(item.minutes_above_40 / 60).toFixed(1)} h above 40°`
    : `${item.minutes_above_40} min above 40°`;
  const when = new Date(item.peak_at);
  const clock = Number.isNaN(when.getTime()) ? "" : when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const moon = item.moon_up && item.moon_distance < 40 ? ` · Moon is ${item.moon_distance}° away` : "";
  const magnitude = item.mag === null || item.mag === undefined ? "" : ` · mag ${item.mag}`;
  return `${item.kind || "Deep sky"}${magnitude} · highest ${item.peak_altitude}° at ${clock} · ${high}${moon}`;
}

async function loadBestTonight() {
  const here = placeNow(state.snap || {});
  if (!here) {
    toast("Save a place in Settings. The desk also keeps the last place reported by a telescope.");
    return;
  }
  const lat = here.latitude;
  const lon = here.longitude;
  const data = await api(`/api/sky/tonight?lat=${encodeURIComponent(lat)}&lon=${encodeURIComponent(lon)}`);
  const card = document.querySelector("#tonight-card");
  const note = document.querySelector("#tonight-note");
  const list = document.querySelector("#best-list");
  if (!card || !list) return;
  card.hidden = false;
  const picks = data.picks || [];
  if (!picks.length) {
    if (note) note.textContent = data.note || "Nothing in the catalog stays high enough tonight.";
    list.innerHTML = "";
    return;
  }
  const window = data.window || {};
  const from = new Date(window.start);
  const until = new Date(window.end);
  const span = Number.isNaN(from.getTime()) ? "" : `${from.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}–${until.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
  if (note) note.textContent = `For this place during ${window.darkness || "the dark hours"}${span ? `, ${span}` : ""}. The first row is the best pick.`;
  list.innerHTML = picks.map((item, index) => `
    <button type="button" class="file tonight-row${index === 0 ? " selected" : ""}" data-tonight="${index}">
      <span>${index === 0 ? "Best pick · " : ""}${escapeHtml(item.id)} ${escapeHtml(item.name || "")}</span>
      <span class="muted">${escapeHtml(tonightDetail(item))}</span>
    </button>`).join("");
  list.querySelectorAll("[data-tonight]").forEach((row) => {
    row.onclick = () => chooseTonight({ ...picks[Number(row.dataset.tonight)], index: Number(row.dataset.tonight) });
  });
  chooseTonight({ ...picks[0], index: 0 });
}

function chartNote() {
  const stored = state.starDepth === 12
    ? " Stars from magnitude 8 to 12 are kept on this computer after the first view."
    : "";
  const pictures = state.skyPictures
    ? " Nebula and cluster photographs sit on the dark sky. Zoom in to see them. They are kept on this computer after the first view."
    : "";
  if (state.starChart === "gaia") {
    return "Gaia adds fainter stars and needs the internet. Bright stars stay from the Stellarium chart." + stored + pictures;
  }
  return "Stellarium is stored on this computer, down to magnitude 7." + stored + pictures;
}

function reloadSky(message) {
  const canvas = document.querySelector("#skymap");
  if (!canvas) return;
  const fresh = document.createElement("canvas");
  fresh.id = "skymap";
  fresh.className = "skymap";
  canvas.replaceWith(fresh);
  const status = document.querySelector("#skymap-status");
  if (status) {
    status.hidden = false;
    status.textContent = message;
  }
  mountSky();
}

function chooseStarDepth() {
  state.starDepth = state.starDepth === 12 ? 7 : 12;
  localStorage.setItem("seestar-starmag", String(state.starDepth));
  const button = document.querySelector("#star-depth");
  if (button) button.classList.toggle("primary", state.starDepth === 12);
  const note = document.querySelector(".chart-note");
  if (note) note.textContent = chartNote();
  reloadSky(state.starDepth === 12 ? "Storing stars to magnitude 12…" : "Loading the sky map…");
}

function chooseSkyPictures() {
  state.skyPictures = !state.skyPictures;
  localStorage.setItem("seestar-skypics", state.skyPictures ? "1" : "0");
  const button = document.querySelector("#sky-pictures");
  if (button) button.classList.toggle("primary", state.skyPictures);
  const note = document.querySelector(".chart-note");
  if (note) note.textContent = chartNote();
  reloadSky(state.skyPictures ? "Loading sky pictures…" : "Loading the sky map…");
}

function chooseStarChart(chart) {
  chart = chart === "gaia" ? "gaia" : "stellarium";
  if (chart === state.starChart) return;
  state.starChart = chart;
  localStorage.setItem("seestar-starchart", chart);
  document.querySelectorAll("[data-chart]").forEach((button) => {
    button.classList.toggle("primary", button.dataset.chart === chart);
  });
  const note = document.querySelector(".chart-note");
  if (note) note.textContent = chartNote();
  reloadSky(chart === "gaia" ? "Loading Gaia…" : "Loading the sky map…");
}

function mountSky() {
  const canvas = document.querySelector("#skymap");
  if (!canvas || canvas.dataset.booting) return;
  canvas.dataset.booting = "1";
  const status = document.querySelector("#skymap-status");
  loadScript("/static/sky/stellarium-web-engine.js?v=2").then(() => {
    const created = window.StelWebEngine({
      canvas,
      wasmFile: "/static/sky/stellarium-web-engine.wasm?v=2",
      onReady(engine) {
        prepareSky(engine);
        syncSkyObserver();
        if (status) status.hidden = true;
      },
    });
    if (created && created.then) return created;
    return null;
  }).catch(() => {
    canvas.dataset.booting = "";
    if (status) status.textContent = "The sky map did not start.";
  });
}

function bind() {
  document.querySelectorAll("#find, [data-find]").forEach((find) => {
    find.onclick = () => guard(async () => { state.scopes = (await api("/api/discover", { method: "POST" })).scopes; render(); });
  });
  bindClick("#open-sky", () => show("sky"));
  bindClick("#open-sky-wide", () => show("sky"));
  bindClick("#open-picture", () => show("picture"));
  bindClick("#open-plan", () => show("plan"));
  const manual = document.querySelector("#manual");
  if (manual) manual.onsubmit = (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(manual));
    guard(async () => {
      await api("/api/scopes", { method: "POST", body: JSON.stringify(data) });
      state.scopes = (await api("/api/scopes")).scopes;
      render();
    });
  };
  document.querySelectorAll("[data-connect]").forEach((button) => {
    button.onclick = () => guard(async () => {
      const id = button.dataset.connect;
      const linked = Boolean(state.snap && state.snap.connected && state.snap.id === id && state.selected === id);
      if (linked) {
        await api(`/api/scopes/${encodeURIComponent(id)}/disconnect`, { method: "POST" });
        state.snap = null;
        state.scopes = (await api("/api/scopes")).scopes;
        render();
        return;
      }
      state.selected = id;
      localStorage.setItem("seestar-scope", state.selected);
      state.error = "";
      state.snap = await api(scopePath("/connect"), { method: "POST" });
      state.scopes = (await api("/api/scopes")).scopes;
      render();
    });
  });
  document.querySelectorAll("[data-control]").forEach((button) => {
    button.onclick = () => guard(async () => {
      state.selected = button.dataset.control;
      const held = Boolean(state.snap && state.snap.id === state.selected && state.snap.master);
      state.snap = await api(scopePath(held ? "/release" : "/claim"), { method: "POST" });
      render();
    });
  });
  document.querySelectorAll("[data-power]").forEach((button) => {
    button.onclick = () => {
      state.selected = button.dataset.id;
      localStorage.setItem("seestar-scope", state.selected);
      askPower(button.dataset.power);
    };
  });
  bindClick("#restart-scope", () => askPower("restart"));
  bindClick("#shutdown-scope", () => askPower("shutdown"));
  const query = document.querySelector("#query");
  if (query) {
    query.oninput = () => {
      state.query = query.value;
      guard(fillTargets);
    };
    fillTargets();
  }
  const form = document.querySelector("#goto");
  if (form) form.onsubmit = (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(form));
    data.lp_filter = form.lp_filter.checked;
    guard(async () => { state.snap = await api(scopePath("/goto"), { method: "POST", body: JSON.stringify(data) }); paintMeters(); });
  };
  bindClick("#track-on", () => post("/track", { on: true }));
  bindClick("#track-off", () => post("/track", { on: false }));
  bindClick("#horizon", () => post("/horizon", {}));
  bindClick("#stop", () => post("/stop", {}));
  bindClick("#park", () => {
    if (confirm("Park closes the arm and can power the telescope down.")) post("/park", {});
  });
  mountSky();
  document.querySelectorAll("[data-chart]").forEach((button) => {
    button.onclick = () => chooseStarChart(button.dataset.chart);
  });
  bindClick("#star-depth", () => chooseStarDepth());
  bindClick("#sky-pictures", () => chooseSkyPictures());
  bindClick("#best-tonight", () => guard(loadBestTonight));
  bindClick("#sky-view", () => {
    const pick = state.skyPick;
    if (!pick) return;
    if (!state.selected) {
      toast("Connect a telescope first.");
      return;
    }
    post("/goto", { name: pick.name, ra: pick.ra, dec: pick.dec, mode: pick.mode });
  });
  bindClick("#sky-plan", () => {
    const pick = state.skyPick;
    if (!pick) return;
    state.planItems.push({
      name: pick.name,
      ra: pick.ra,
      dec: pick.dec,
      mode: pick.mode === "scenery" ? "star" : pick.mode,
      minutes: 15,
      rows: 1,
      cols: 1,
      overlap: 20,
      gain: 80,
      autofocus: true,
    });
    if (!state.selected) {
      toast(`${pick.name} is on the list. Connect a telescope before running it.`);
      return;
    }
    guard(async () => {
      await runPlan(false);
      toast(`${pick.name} added to tonight.`);
    });
  });
  const pad = document.querySelector("#pad");
  if (pad) bindPad(pad);
  document.querySelectorAll("[data-speed]").forEach((button) => {
    button.onclick = () => {
      state.speed = Number(button.dataset.speed);
      document.querySelectorAll("[data-speed]").forEach((item) => item.classList.toggle("primary", Number(item.dataset.speed) === state.speed));
    };
  });
  bindClick("#layout", () => {
    state.layout = state.layout === "theater" ? "cards" : "theater";
    localStorage.setItem("seestar-layout", state.layout);
    document.body.classList.toggle("theater", state.layout === "theater");
    render();
  });
  bindClick("#live-tele", () => startLive("tele"));
  bindClick("#live-wide", () => startLive("wide"));
  bindClick("#live-stop", () => guard(async () => { await api(scopePath("/live"), { method: "POST", body: JSON.stringify({ enabled: false, camera: state.camera }) }); }));
  bindClick("#play-tv", () => playOnTv());
  bindClick("#stack-start", () => post("/stack", { action: "start", restart: document.querySelector("#restart")?.checked ?? true, gain: document.querySelector("#gain")?.value }));
  bindClick("#stack-stop", () => post("/stack", { action: "stop" }));
  const mosaic = document.querySelector("#mosaic");
  if (mosaic) mosaic.onsubmit = (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(mosaic));
    const snap = state.snap || {};
    if (snap.ra === null || snap.ra === undefined || snap.dec === null || snap.dec === undefined) {
      toast("The telescope has not reported coordinates yet.");
      return;
    }
    state.planItems = [{
      name: snap.target || "Mosaic",
      ra: snap.ra,
      dec: snap.dec,
      mode: "star",
      minutes: Number(data.minutes),
      rows: Number(data.rows),
      cols: Number(data.cols),
      overlap: Number(data.overlap),
      gain: document.querySelector("#gain")?.value,
      autofocus: true,
    }];
    guard(() => runPlan(false));
  };
  const video = document.querySelector("#video");
  if (video) video.onsubmit = (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(video));
    post("/video", { mode: data.mode, minutes: Number(data.minutes) });
  };
  bindClick("#video-stop", () => post("/video", { action: "stop" }));
  bindClick("#autofocus", () => post("/focus", { action: "auto" }));
  bindClick("#focus-stop", () => post("/focus", { action: "stop" }));
  document.querySelectorAll("[data-focus]").forEach((button) => {
    button.onclick = () => post("/focus", { action: "move", delta: Number(button.dataset.focus) });
  });
  document.querySelectorAll("[data-filter]").forEach((button) => {
    button.onclick = () => post("/filter", { position: Number(button.dataset.filter) });
  });
  bindClick("#open-arm", () => {
    if (confirm("This opens the arm out to the horizon.")) post("/horizon", {});
  });
  const ready = document.querySelector("#eq-ready");
  if (ready) ready.onchange = () => {
    localStorage.setItem("seestar-eq-ready", ready.checked ? "1" : "0");
    const button = document.querySelector("#to-eq");
    if (button) button.disabled = !ready.checked;
  };
  bindClick("#to-eq", () => {
    if (confirm("This levels the arm, then parks into equatorial mode.")) post("/mount", { equatorial: true });
  });
  bindClick("#to-az", () => {
    if (confirm("This levels the arm, then parks into alt-az mode.")) post("/mount", { equatorial: false });
  });
  bindClick("#align-start", () => post("/align", { action: "start", dec_pos_index: document.querySelector("#dec-band")?.value }));
  bindClick("#align-recheck", () => post("/align", { action: "recheck" }));
  bindClick("#align-live", () => {
    const box = document.querySelector("#align-repeat");
    if (!box) return;
    box.checked = !box.checked;
    box.dispatchEvent(new Event("change"));
  });
  const repeat = document.querySelector("#align-repeat");
  if (repeat) repeat.onchange = () => {
    localStorage.setItem("seestar-align-repeat", repeat.checked ? "1" : "0");
    document.querySelector("#align-live")?.classList.toggle("primary", repeat.checked);
    syncAlignTimer();
  };
  bindClick("#align-stop", () => post("/align", { action: "stop" }));
  bindClick("#reload-album", loadAlbum);
  document.querySelectorAll("[data-folder]").forEach((button) => {
    button.onclick = () => guard(async () => {
      state.folder = button.dataset.folder;
      state.files = (await api(`${scopePath("/files")}?folder=${encodeURIComponent(state.folder)}`)).files;
      const shot = galleryItem(state.folder);
      if (shot) selectGallery(shot.name);
      paintTransfer();
    });
  });
  document.querySelectorAll("[data-gallery]").forEach((button) => {
    button.onclick = () => selectGallery(button.dataset.gallery);
  });
  const galleryPhoto = document.querySelector("#gallery-photo");
  if (galleryPhoto) galleryPhoto.onerror = () => {
    const item = galleryItem(state.galleryPick);
    if (!item || galleryPhoto.dataset.fallback) return;
    galleryPhoto.dataset.fallback = "1";
    galleryPhoto.src = gallerySrc(item, false);
  };
  if (document.querySelector("#gallery-map")) mountGallery();
  bindClick("#select-all", () => {
    document.querySelectorAll("input[name=file]").forEach((node) => { node.checked = true; });
  });
  bindClick("#choose-folder", () => openFolderWindow(false));
  bindClick("#copy", () => copySelected());
  bindClick("#folder-up", () => climbFolder());
  bindClick("#folder-browse", () => browseFolder());
  bindClick("#folder-new", () => makeFolder());
  bindClick("#folder-use", () => useCurrentFolder());
  bindClick("#folder-cancel", () => closeFolderWindow());
  document.querySelectorAll("[data-theme-choice]").forEach((button) => {
    button.onclick = () => {
      applyTheme(button.dataset.themeChoice);
      document.querySelectorAll("[data-theme-choice]").forEach((item) => {
        item.classList.toggle("primary", item.dataset.themeChoice === state.theme);
      });
    };
  });
  const site = document.querySelector("#site");
  if (site) site.onsubmit = (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(site));
    guard(async () => {
      state.place = await api("/api/place", { method: "POST", body: JSON.stringify({ lat: data.lat, lon: data.lon }) });
      state.forecast = null;
      if (state.snap && state.snap.connected) await post("/settings", data);
      else {
        toast("Place saved. The sky and tonight use it while the telescope is off.");
        render();
      }
    });
  };
  bindClick("#set-clock", () => post("/settings", { set_clock: true }));
  bindClick("#wifi-scan", () => guard(async () => {
    const data = await api(scopePath("/wifi"), { method: "POST", body: JSON.stringify({ action: "scan" }) });
    state.networks = data.networks || [];
    render();
  }));
  document.querySelectorAll("[data-ssid]").forEach((button) => {
    button.onclick = () => {
      const input = document.querySelector("#wifi-ssid");
      if (input) input.value = button.dataset.ssid;
    };
  });
  const wifi = document.querySelector("#wifi-join");
  if (wifi) wifi.onsubmit = (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(wifi));
    if (!confirm(`Join ${data.ssid}? The telescope will leave its current network.`)) return;
    post("/wifi", { action: "join", ssid: data.ssid, password: data.password || "" });
  };
  bindPlan();
  const imaging = document.querySelector("#imaging");
  if (imaging) imaging.onsubmit = (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(imaging));
    post("/settings", {
      exp_ms: { stack_l: Number(data.exp), continuous: Number(data.exp) },
      stack_dither: { pix: Number(data.pix), interval: Number(data.interval), enable: imaging.dither.checked },
      heater_enable: imaging.heater.checked,
      is_dew_on: imaging.heater.checked,
      dew_heater_power: Number(data.power || 0),
    });
  };
  if (state.page === "album" && state.selected && !state.albumLoaded) {
    state.albumLoaded = true;
    guard(loadAlbum);
  }
}

function planPayload(action) {
  return {
    action,
    items: state.planItems,
    options: { park_when_finished: Boolean(document.querySelector("#park-end")?.checked) },
    safety: {
      min_battery: Number(document.querySelector("#min-battery")?.value ?? 20),
      min_altitude: Number(document.querySelector("#min-altitude")?.value ?? 25),
      max_cloud: Number(document.querySelector("#max-cloud")?.value ?? 80),
      stop_before_dawn: document.querySelector("#stop-dawn")?.checked !== false,
    },
  };
}

async function runPlan(start) {
  state.snap = await api(scopePath("/plan"), { method: "POST", body: JSON.stringify(planPayload(start ? "start" : "preview")) });
  if (state.snap.plan && state.snap.plan.items) state.planItems = state.snap.plan.items;
  if (state.page === "plan") render();
  else paintMeters();
}

function bindPlan() {
  const add = document.querySelector("#plan-add");
  if (add) add.onsubmit = (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(add));
    state.planItems.push({
      name: data.name,
      ra: Number(data.ra),
      dec: Number(data.dec),
      mode: data.mode,
      minutes: Number(data.minutes),
      rows: Number(data.rows),
      cols: Number(data.cols),
      overlap: Number(data.overlap),
      gain: Number(data.gain),
      autofocus: add.autofocus.checked,
    });
    render();
  };
  document.querySelectorAll("[data-drop]").forEach((button) => {
    button.onclick = () => {
      state.planItems.splice(Number(button.dataset.drop), 1);
      render();
    };
  });
  bindClick("#plan-start", () => runPlan(true));
  bindClick("#plan-stop", () => guard(async () => {
    state.snap = await api(scopePath("/plan"), { method: "POST", body: JSON.stringify({ action: "stop" }) });
    paintMeters();
  }));
  bindClick("#weather", () => guard(async () => {
    state.snap = await api(scopePath("/plan"), { method: "POST", body: JSON.stringify(planPayload("weather")) });
    if (state.snap.plan && state.snap.plan.items) state.planItems = state.snap.plan.items;
    render();
  }));
  const query = document.querySelector("#plan-query");
  if (query) query.oninput = () => guard(async () => {
    const data = await api(`/api/catalog?q=${encodeURIComponent(query.value)}`);
    const box = document.querySelector("#plan-results");
    if (!box) return;
    const targets = data.targets.slice(0, 6);
    box.innerHTML = targets.map((target, index) =>
      `<button type="button" data-plan-target="${index}">${escapeHtml(target.id)} ${escapeHtml(target.name)}</button>`
    ).join("");
    box.querySelectorAll("[data-plan-target]").forEach((button) => {
      button.onclick = () => {
        const target = targets[Number(button.dataset.planTarget)];
        document.querySelector("#plan-name").value = target.id;
        document.querySelector("#plan-ra").value = target.ra;
        document.querySelector("#plan-dec").value = target.dec;
      };
    });
  });
}

function bindClick(selector, action) {
  const node = document.querySelector(selector);
  if (node) node.onclick = () => guard(action);
}

function askPower(action) {
  const restart = action === "restart";
  const message = restart
    ? "Restart the telescope? The connection will drop, and it should come back on its own."
    : "Shut the telescope down? It will power off and stay off until you switch it on.";
  if (!confirm(message)) return;
  return guard(async () => {
    state.snap = await api(scopePath("/power"), { method: "POST", body: JSON.stringify({ action }) });
    state.scopes = (await api("/api/scopes")).scopes;
    render();
  });
}

function post(path, body) {
  return guard(async () => {
    state.snap = await api(scopePath(path), { method: "POST", body: JSON.stringify(body) });
    paintMeters();
  });
}

function castElements() {
  let plate = document.querySelector("#cast-plate");
  if (!plate) {
    plate = document.createElement("canvas");
    plate.id = "cast-plate";
    plate.hidden = true;
    document.body.appendChild(plate);
  }
  let video = document.querySelector("#cast-video");
  if (!video) {
    video = document.createElement("video");
    video.id = "cast-video";
    video.muted = true;
    video.playsInline = true;
    video.setAttribute("playsinline", "");
    video.setAttribute("x-webkit-airplay", "allow");
    video.setAttribute("airplay", "allow");
    video.style.cssText = "position:fixed;width:1px;height:1px;opacity:0;pointer-events:none";
    document.body.appendChild(video);
  }
  return { plate, video };
}

function castStackImage() {
  let image = document.querySelector("#cast-stack");
  if (!image) {
    image = document.createElement("img");
    image.id = "cast-stack";
    image.alt = "";
    image.hidden = true;
    document.body.appendChild(image);
  }
  const seq = state.snap && state.snap.frame_seq && state.snap.frame_seq.stack;
  if (!seq || image.dataset.seq === String(seq)) return image;
  image.dataset.seq = String(seq);
  image.src = `${scopePath("/frame.jpg")}?profile=tv&camera=stack&seq=${encodeURIComponent(seq)}`;
  return image;
}

function paintCastPlate(plate) {
  const source = castStackImage();
  const ready = source && source.complete && source.naturalWidth;
  const width = ready ? source.naturalWidth : 1920;
  const height = ready ? source.naturalHeight : 1080;
  if (plate.width !== width || plate.height !== height) {
    plate.width = width;
    plate.height = height;
  }
  const context = plate.getContext("2d");
  context.fillStyle = "#000";
  context.fillRect(0, 0, width, height);
  if (ready) context.drawImage(source, 0, 0, width, height);
}

function castPageUrl() {
  const origin = state.lanOrigin || location.origin;
  const url = new URL("/tv", `${origin}/`);
  url.searchParams.set("scope", state.selected);
  url.searchParams.set("camera", "stack");
  return url.href;
}

function playOnTv() {
  if (!state.selected) {
    toast("Connect a telescope first.");
    return;
  }
  const { plate, video } = castElements();
  paintCastPlate(plate);
  if (!state.castTimer) state.castTimer = setInterval(() => paintCastPlate(plate), 100);
  try {
    if (!video.srcObject) video.srcObject = plate.captureStream(10);
  } catch (error) {
    presentOnTv();
    return;
  }
  const started = video.play();
  if (started && started.catch) started.catch(() => {});
  if (typeof video.webkitShowPlaybackTargetPicker === "function") {
    video.webkitShowPlaybackTargetPicker();
    return;
  }
  if (video.remote && typeof video.remote.prompt === "function") {
    video.remote.prompt().catch(() => presentOnTv());
    return;
  }
  presentOnTv();
}

function presentOnTv() {
  const page = castPageUrl();
  if (window.PresentationRequest) {
    const request = new PresentationRequest([page]);
    request.start().catch((error) => {
      if (error && error.name === "AbortError") return;
      window.open(page, "seestar-tv");
    });
    return;
  }
  window.open(page, "seestar-tv");
}

function startLive(camera) {
  const next = camera === "wide" ? "wide" : "tele";
  const changed = state.camera !== next;
  state.camera = next;
  markCameras();
  if (changed) prepareLiveStage();
  if (state.snap && state.snap.parked) return;
  return guard(async () => {
    await api(scopePath("/live"), { method: "POST", body: JSON.stringify({ camera: next, enabled: true }) });
    paintMeters();
  });
}

function bindPad(pad) {
  let timer = 0;
  const send = (event) => {
    const rect = pad.getBoundingClientRect();
    const dx = event.clientX - (rect.left + rect.width / 2);
    const dy = event.clientY - (rect.top + rect.height / 2);
    if (Math.hypot(dx, dy) < 24) return;
    let angle = Math.atan2(-dy, dx) * 180 / Math.PI;
    if (angle < 0) angle += 360;
    api(scopePath("/move"), { method: "POST", body: JSON.stringify({ angle, speed: state.speed, dur: 1 }) }).catch((error) => toast(error.message));
  };
  pad.onpointerdown = (event) => {
    send(event);
    try { pad.setPointerCapture(event.pointerId); } catch (error) { /* synthetic events */ }
    timer = setInterval(() => send(event), 900);
  };
  const end = () => clearInterval(timer);
  pad.onpointerup = end;
  pad.onpointercancel = end;
}

async function fillTargets() {
  const box = document.querySelector("#results");
  if (!box) return;
  const data = await api(`/api/catalog?q=${encodeURIComponent(state.query)}`);
  state.targets = data.targets.slice(0, 8);
  box.innerHTML = state.targets.map((target, index) =>
    `<button type="button" data-target="${index}"><span>${escapeHtml(target.id)} ${escapeHtml(target.name)}</span><span class="muted">${escapeHtml(target.kind)}</span></button>`
  ).join("");
  box.querySelectorAll("[data-target]").forEach((button) => {
    button.onclick = () => {
      const target = state.targets[Number(button.dataset.target)];
      document.querySelector("#target-name").value = target.id;
      document.querySelector("#target-ra").value = target.ra;
      document.querySelector("#target-dec").value = target.dec;
    };
  });
}

async function loadAlbum() {
  if (!state.selected) return;
  const data = await api(scopePath("/albums"));
  state.folders = data.folders;
  state.downloadDest = data.dest || "";
  try {
    state.gallery = (await api(scopePath("/gallery"))).items || [];
  } catch (error) {
    state.gallery = [];
  }
  state.albumLoaded = true;
  if (state.page === "album") render();
}

function mountGallery() {
  const canvas = document.querySelector("#gallery-map");
  if (!canvas || canvas.dataset.booting) return;
  canvas.dataset.booting = "1";
  const status = document.querySelector("#gallery-status");
  loadScript("/static/sky/stellarium-web-engine.js?v=2").then(() => {
    const created = window.StelWebEngine({
      canvas,
      wasmFile: "/static/sky/stellarium-web-engine.wasm?v=2",
      onReady(engine) {
        try {
          prepareGallery(engine);
          if (status) status.hidden = true;
          const pick = galleryItem(state.galleryPick);
          if (pick && pick.ra !== null && pick.ra !== undefined) lookAtHours(engine, pick.ra, pick.dec, 0.9);
        } catch (error) {
          if (status) status.textContent = error && error.message ? error.message : "The sky map did not start.";
        }
      },
    });
    if (created && created.then) return created;
    return null;
  }).catch(() => {
    canvas.dataset.booting = "";
    if (status) status.textContent = "The sky map did not start.";
  });
}

function prepareGallery(engine) {
  const base = "/static/sky/data/";
  [[engine.core.stars, base + "stars"], [engine.core.dsos, base + "dso"], [engine.core.milkyway, base + "surveys/milkyway"]].forEach(([module, url]) => {
    try { module.addDataSource({ url }); } catch (error) { /* a missing survey leaves the chart usable */ }
  });
  if (engine.core.dss) engine.core.dss.visible = false;
  if (engine.core.atmosphere) engine.core.atmosphere.visible = false;
  try {
    engine.core.skycultures.addDataSource({ url: base + "skycultures/western", key: "western" });
    engine.core.constellations.lines_visible = true;
    engine.core.constellations.labels_visible = true;
  } catch (error) { /* constellation lines are optional */ }
  const here = placeNow(state.snap || {});
  if (here && engine.observer) {
    engine.observer.latitude = here.latitude * Math.PI / 180;
    engine.observer.longitude = here.longitude * Math.PI / 180;
  }
  state.galleryEngine = engine;
  state.gallery.filter((item) => item.ra !== null && item.ra !== undefined && item.thumb).forEach((item) => {
    try {
      const photo = engine.createObj("photo", { visible: true, url: gallerySrc(item, false) });
      if (!photo) return;
      photo.calibration = [{
        ra: item.ra * 15,
        dec: item.dec,
        orientation: 0,
        pixscale: 6 * 3600 / 640,
      }];
      engine.core.add(photo);
      photo.retain();
    } catch (error) { /* the chart remains if one picture cannot be placed */ }
  });
  engine.on("click", () => {
    requestAnimationFrame(() => openGalleryNear(engine));
    return 0;
  });
}

function openGalleryNear(engine) {
  const obj = engine.core && engine.core.selection;
  if (!obj || !obj.getInfo) return;
  const vector = obj.getInfo("radec");
  if (!vector || vector.length < 3) return;
  let ra = Math.atan2(vector[1], vector[0]);
  if (ra < 0) ra += Math.PI * 2;
  ra = ra * 12 / Math.PI;
  const dec = Math.atan2(vector[2], Math.hypot(vector[0], vector[1])) * 180 / Math.PI;
  let best = null;
  let gap = 12;
  state.gallery.forEach((item) => {
    if (item.ra === null || item.ra === undefined) return;
    const distance = angularDistance(item.ra, item.dec, ra, dec);
    if (distance < gap) {
      best = item;
      gap = distance;
    }
  });
  if (best) selectGallery(best.name);
}

function angularDistance(raHours, decDeg, otherRa, otherDec) {
  const ra1 = raHours * Math.PI / 12;
  const ra2 = otherRa * Math.PI / 12;
  const dec1 = decDeg * Math.PI / 180;
  const dec2 = otherDec * Math.PI / 180;
  const step = Math.sin(dec1) * Math.sin(dec2) + Math.cos(dec1) * Math.cos(dec2) * Math.cos(ra1 - ra2);
  return Math.acos(Math.max(-1, Math.min(1, step))) * 180 / Math.PI;
}

function drawRose() {
  const canvas = document.querySelector("#rose");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const width = canvas.width;
  const height = canvas.height;
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#090c11";
  ctx.fillRect(0, 0, width, height);
  const cx = width / 2;
  const cy = height / 2 + 10;
  ctx.strokeStyle = "#2a3444";
  ctx.beginPath();
  ctx.arc(cx, cy, 90, 0, Math.PI * 2);
  ctx.stroke();
  ctx.fillStyle = "#93a0b4";
  ctx.font = "14px Inter, Segoe UI, sans-serif";
  ctx.fillText("N", cx - 5, cy - 98);
  ctx.fillText("E", cx + 98, cy + 4);
  ctx.fillText("S", cx - 4, cy + 112);
  ctx.fillText("W", cx - 112, cy + 4);
  const az = Number(state.snap && state.snap.az);
  const alt = Number(state.snap && state.snap.alt);
  if (Number.isNaN(az) || Number.isNaN(alt)) return;
  const radius = 90 * (1 - Math.max(-10, Math.min(90, alt)) / 90);
  const theta = (az - 90) * Math.PI / 180;
  const x = cx + Math.cos(theta) * radius;
  const y = cy + Math.sin(theta) * radius;
  ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim() || "#e0b15a";
  ctx.beginPath();
  ctx.arc(x, y, 6, 0, Math.PI * 2);
  ctx.fill();
}

function applyIncoming(message) {
  if (message.type === "hello") {
    state.scopes = message.scopes || state.scopes;
    if (message.place && message.place.latitude !== undefined) state.place = message.place;
  }
  const snap = message.state || (message.type === "state" ? message : null);
  if (snap && snap.host && (!state.selected || snap.id === state.selected)) {
    state.snap = snap;
    rememberPlace(snap.latitude, snap.longitude);
  }
  syncSkyObserver();
  if (message.event) state.log = state.log.concat([{ time: "", event: message.event.Event, state: message.event.state, detail: "" }]).slice(-80);
  if (message.type === "frame" && message.seq) {
    state.seq = message.seq;
    if (state.snap) {
      state.snap.frame_seq = state.snap.frame_seq || {};
      state.snap.frame_seq[message.camera] = message.seq;
    }
  }
  paintMeters();
  if (state.page === "overview" && message.type === "hello") render();
}

function connectSocket() {
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${protocol}://${location.host}/api/ws`);
  socket.onmessage = (event) => applyIncoming(JSON.parse(event.data));
  socket.onclose = () => setTimeout(connectSocket, 1500);
}

function downloadDestText() {
  if (state.saveDir) return `Saving into ${folderPath()} on this computer.`;
  if (window.showDirectoryPicker) return "Choose a folder on this computer. The files stay here, not in the desk.";
  return "This browser saves into its download folder on this computer.";
}

function folderPath() {
  const names = state.dirStack.map((item) => item.name).filter(Boolean);
  if (names.length) return names.join(" / ");
  return (state.saveDir && state.saveDir.name) || "the chosen folder";
}

function handleDb() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open("seestar-desk", 1);
    request.onupgradeneeded = () => request.result.createObjectStore("handles");
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function storeHandle(handle) {
  const db = await handleDb();
  await new Promise((resolve, reject) => {
    const tx = db.transaction("handles", "readwrite");
    tx.objectStore("handles").put(handle, "saveDir");
    tx.oncomplete = resolve;
    tx.onerror = () => reject(tx.error);
  });
  db.close();
}

async function loadHandle() {
  const db = await handleDb();
  const handle = await new Promise((resolve, reject) => {
    const request = db.transaction("handles", "readonly").objectStore("handles").get("saveDir");
    request.onsuccess = () => resolve(request.result || null);
    request.onerror = () => reject(request.error);
  });
  db.close();
  return handle;
}

async function restoreSaveFolder() {
  try {
    const handle = await loadHandle();
    if (!handle || !handle.queryPermission) return;
    if (await handle.queryPermission({ mode: "readwrite" }) !== "granted") return;
    state.saveDir = handle;
    state.dirStack = [handle];
  } catch (error) { /* the folder can be chosen again */ }
}

async function pickDirectory() {
  try {
    return await showDirectoryPicker({ mode: "readwrite", startIn: "pictures" });
  } catch (error) {
    if (error && error.name === "AbortError") return null;
    throw error;
  }
}

async function openFolderWindow() {
  const dialog = document.querySelector("#folder-window");
  if (!window.showDirectoryPicker) {
    toast("This browser saves into its download folder on this computer.");
    return;
  }
  if (!state.dirStack.length) {
    const picked = await pickDirectory();
    if (!picked) {
      state.pendingCopy = null;
      return;
    }
    state.dirStack = [picked];
  }
  if (dialog) dialog.hidden = false;
  await paintFolderWindow();
}

async function browseFolder() {
  const picked = await pickDirectory();
  if (!picked) return;
  state.dirStack = [picked];
  await paintFolderWindow();
}

async function climbFolder() {
  if (state.dirStack.length < 2) return;
  state.dirStack.pop();
  await paintFolderWindow();
}

async function makeFolder() {
  const current = state.dirStack[state.dirStack.length - 1];
  if (!current) return;
  const name = prompt("Name the new folder");
  if (!name || !name.trim()) return;
  const created = await current.getDirectoryHandle(name.trim(), { create: true });
  state.dirStack.push(created);
  await paintFolderWindow();
}

async function paintFolderWindow() {
  const current = state.dirStack[state.dirStack.length - 1];
  const path = document.querySelector("#folder-path");
  const list = document.querySelector("#folder-entries");
  const up = document.querySelector("#folder-up");
  if (path) path.textContent = folderPath();
  if (up) up.disabled = state.dirStack.length < 2;
  if (!list || !current || !current.entries) return;
  const rows = [];
  for await (const [name, handle] of current.entries()) rows.push({ name, handle });
  rows.sort((a, b) => {
    if (a.handle.kind !== b.handle.kind) return a.handle.kind === "directory" ? -1 : 1;
    return a.name.localeCompare(b.name);
  });
  list.innerHTML = rows.map((row, index) => `
    <button type="button" class="folder-entry" data-index="${index}">${row.handle.kind === "directory" ? "Folder" : "File"} · ${escapeHtml(row.name)}</button>
  `).join("") || '<div class="muted">This folder is empty.</div>';
  list.querySelectorAll("[data-index]").forEach((button) => {
    const row = rows[Number(button.dataset.index)];
    if (!row || row.handle.kind !== "directory") {
      button.disabled = true;
      return;
    }
    button.onclick = () => {
      state.dirStack.push(row.handle);
      paintFolderWindow();
    };
  });
}

function closeFolderWindow() {
  const dialog = document.querySelector("#folder-window");
  if (dialog) dialog.hidden = true;
  state.pendingCopy = null;
}

async function useCurrentFolder() {
  const current = state.dirStack[state.dirStack.length - 1];
  if (!current) return;
  state.saveDir = current;
  const names = state.pendingCopy;
  state.pendingCopy = null;
  try { await storeHandle(current); } catch (error) { /* the choice still lasts for this visit */ }
  const dialog = document.querySelector("#folder-window");
  if (dialog) dialog.hidden = true;
  const label = document.querySelector("#download-dest");
  if (label) label.textContent = downloadDestText();
  if (names && names.length) await saveNames(names);
}

async function copySelected() {
  const names = [...document.querySelectorAll("input[name=file]:checked")].map((node) => node.value);
  if (!names.length) {
    toast("Choose at least one file.");
    return;
  }
  if (!state.folder) {
    toast("Open a session first.");
    return;
  }
  if (!window.showDirectoryPicker) {
    await downloadInBrowser(names);
    return;
  }
  if (!state.saveDir) {
    state.pendingCopy = names;
    await openFolderWindow();
    return;
  }
  const permission = await state.saveDir.requestPermission({ mode: "readwrite" });
  if (permission !== "granted") {
    state.saveDir = null;
    state.dirStack = [];
    state.pendingCopy = names;
    await openFolderWindow();
    return;
  }
  await saveNames(names);
}

async function saveNames(names) {
  const dir = state.saveDir;
  for (const name of names) {
    const path = `MyWorks/${state.folder}/${name}`;
    const response = await fetch(`${scopePath("/media")}?path=${encodeURIComponent(path)}`);
    if (!response.ok) throw new Error(`${name} was not copied.`);
    const handle = await dir.getFileHandle(name, { create: true });
    const writable = await handle.createWritable();
    if (response.body && response.body.pipeTo) await response.body.pipeTo(writable);
    else {
      await writable.write(await response.blob());
      await writable.close();
    }
  }
  await finishCopy(names, `${folderPath()} on this computer`);
}

async function downloadInBrowser(names) {
  for (const name of names) {
    const path = `MyWorks/${state.folder}/${name}`;
    const response = await fetch(`${scopePath("/media")}?path=${encodeURIComponent(path)}`);
    if (!response.ok) throw new Error(`${name} was not copied.`);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = name;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }
  await finishCopy(names, "your download folder on this computer");
}

async function finishCopy(names, where) {
  const remove = document.querySelector("#delete-after")?.checked;
  const count = `${names.length} file${names.length === 1 ? "" : "s"}`;
  if (remove) {
    if (!confirm(`Delete ${count} from the telescope? This cannot be undone.`)) {
      toast(`Saved ${count} in ${where}.`);
      return;
    }
    const removed = await api(scopePath("/files/delete"), { method: "POST", body: JSON.stringify({ folder: state.folder, names }) });
    toast(`Saved ${count} and removed ${removed.removed.length} from the telescope.`);
    state.files = (await api(`${scopePath("/files")}?folder=${encodeURIComponent(state.folder)}`)).files;
    render();
    return;
  }
  toast(`Saved ${count} in ${where}.`);
}

async function boot() {
  clock();
  setInterval(clock, 1000);
  try {
    state.scopes = (await api("/api/scopes")).scopes;
    if (!state.selected && state.scopes[0]) state.selected = state.scopes[0].id;
    if (state.selected) {
      try { state.snap = (await api(scopePath("/state"))).state; } catch (error) { /* not connected yet */ }
      if (state.snap) rememberPlace(state.snap.latitude, state.snap.longitude);
    }
    const place = await api("/api/place");
    if (place && place.latitude !== undefined) state.place = place;
    try {
      const reach = await api("/api/reach");
      if (reach.origin) state.lanOrigin = reach.origin;
    } catch (error) { /* an older desk has no network address */ }
  } catch (error) {
    toast(error.message);
  }
  connectSocket();
  await restoreSaveFolder();
  render();
}

boot();
