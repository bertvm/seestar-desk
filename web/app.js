const pages = ["overview", "sky", "picture", "align", "album", "settings"];
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
  layout: localStorage.getItem("seestar-layout") || "cards",
  camera: "tele",
  seq: 0,
  error: "",
  query: "",
};

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

function iconScope(model) {
  const kind = scopeKind(model);
  const tube = kind === "s30" ? 34 : 48;
  const wide = kind === "s50pro"
    ? `<circle cx="${28 + tube - 8}" cy="40" r="4.2" fill="#1b2430" stroke="currentColor" stroke-width="1.6"/>`
    : "";
  return svg(`
    <ellipse cx="60" cy="62" rx="28" ry="5" fill="#10161f"/>
    <path d="M42 58 V36 H54 V28" fill="none" stroke="currentColor" stroke-width="3" stroke-linejoin="round"/>
    <rect x="28" y="20" width="${tube}" height="14" rx="7" fill="#1b2430" stroke="currentColor" stroke-width="1.8"/>
    <circle cx="${28 + tube - 6}" cy="27" r="3.2" fill="#0c1016" stroke="currentColor" stroke-width="1.4"/>
    ${wide}
    <rect x="46" y="54" width="28" height="8" rx="3" fill="#1b2430" stroke="currentColor" stroke-width="1.6"/>
  `, "0 0 120 72");
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

function iconMark() {
  return svg(`<rect x="3" y="9" width="14" height="6" rx="3" fill="none" stroke="currentColor" stroke-width="1.6"/><circle cx="15" cy="12" r="1.4" fill="currentColor"/><path d="M8 15v3h8" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>`);
}

function meter(icon, value, title, tone) {
  return `<span class="meter ${tone || ""}" title="${escapeHtml(title)}">${icon}<b>${value}</b></span>`;
}

function paintMeters() {
  const snap = state.snap;
  const name = document.querySelector("#scope-name");
  const meters = document.querySelector("#meters");
  const mark = document.querySelector("#brand-mark");
  if (!snap) {
    name.textContent = "No telescope";
    meters.innerHTML = "";
    if (mark) mark.innerHTML = iconMark();
    return;
  }
  name.textContent = `${snap.name || snap.host}${snap.model ? " · " + snap.model : ""}`;
  if (mark) mark.innerHTML = iconScope(snap.model);
  const battery = Number(snap.battery);
  const batteryTone = Number.isNaN(battery) ? "dim" : battery < 20 ? "bad" : battery < 40 ? "warn" : "good";
  const linkMode = snap.connected ? (snap.master ? "control" : "watch") : "off";
  const linkText = linkMode === "control" ? "In control" : linkMode === "watch" ? "Watching" : "Offline";
  const linkTone = linkMode === "control" ? "good" : linkMode === "watch" ? "warn" : "dim";
  meters.innerHTML = [
    meter(iconLink(linkMode), linkText, "Link", linkTone),
    meter(iconBattery(snap.battery), fmtNum(snap.battery, "%"), "Battery", batteryTone),
    meter(iconDisk(), fmtDisk(snap.disk_free), "Free space", ""),
    meter(iconWifi(Boolean(snap.wifi)), escapeHtml(snap.wifi || "—"), "Wi-Fi", snap.wifi ? "good" : "dim"),
    meter(svg(`<text x="3" y="16" font-size="11" font-family="Palatino, Georgia, serif" fill="currentColor">α</text>`), fmtRA(snap.ra), "Right ascension", ""),
    meter(svg(`<text x="4" y="16" font-size="11" font-family="Palatino, Georgia, serif" fill="currentColor">δ</text>`), fmtDec(snap.dec), "Declination", ""),
  ].join("");
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
    else if (key === "align") node.textContent = alignText(snap);
    else if (key === "tracking") node.textContent = snap.tracking ? "On" : "Off";
    else if (key === "mount") node.textContent = snap.equatorial ? "Equatorial" : "Alt-az";
  });
  const stage = document.querySelector("#stage");
  const seq = snap.frame_seq && snap.frame_seq[state.camera];
  if (stage && seq && stage.dataset.seq !== String(seq)) {
    stage.dataset.seq = String(seq);
    stage.src = `${scopePath("/frame.jpg")}?camera=${state.camera}&seq=${seq}`;
  }
  drawRose();
}

function stackText(snap) {
  const stack = snap.stack || {};
  const count = stack.stack_count ?? stack.stacked_frame ?? "";
  const phase = stack.state || "";
  return [phase, count !== "" ? `${count} frames` : ""].filter(Boolean).join(" · ") || "Idle";
}

function alignText(snap) {
  const align = snap.align || {};
  if (!align.Event && align.state_code === undefined && !align.state) return "Not started";
  return [align.Event, align.state, align.state_code !== undefined ? `step ${align.state_code}` : ""]
    .filter((part) => part !== undefined && part !== "")
    .join(" · ");
}

function filterName(position) {
  return { 0: "Dark", 1: "IR-cut", 2: "Light pollution" }[position] || (position === null || position === undefined ? "—" : `Position ${position}`);
}

function banner() {
  const snap = state.snap;
  if (state.error) return `<div class="banner bad">${escapeHtml(state.error)}</div>`;
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
    settings: renderSettings,
  }[state.page];
  main.innerHTML = view();
  bind();
  paintMeters();
}

function renderOverview() {
  const cards = state.scopes.map((scope) => `
    <article class="scope-card">
      <div class="scope-art ${scopeKind(scope.model)}" title="${escapeHtml(scopeLabel(scope.model))}">${iconScope(scope.model)}</div>
      <header>
        <div>
          <h2>${escapeHtml(scope.name || scope.host)}</h2>
          <div class="muted">${escapeHtml(scope.model || scopeLabel(scope.model))} · ${escapeHtml(scope.host)}</div>
        </div>
        <span class="pill ${scope.connected ? "good" : ""}">${iconLink(scope.connected ? "control" : "off")} ${scope.connected ? "Linked" : "Available"}</span>
      </header>
      <div class="actions">
        <button class="primary" data-connect="${escapeHtml(scope.id)}">${scope.connected && scope.id === state.selected ? "Connected" : "Connect"}</button>
        <button data-claim="${escapeHtml(scope.id)}">Claim control</button>
        <button data-release="${escapeHtml(scope.id)}">Release</button>
        <button class="warn" data-power="restart" data-id="${escapeHtml(scope.id)}">Restart</button>
        <button class="danger" data-power="shutdown" data-id="${escapeHtml(scope.id)}">Shut down</button>
      </div>
    </article>`).join("");
  return `
    <div class="page-head"><h1>Telescopes</h1>
      <button class="primary" id="find">Find Seestars</button></div>
    ${banner()}
    <div class="scope-list">${cards || `<div class="card">None found yet. Search the network, or type an address.</div>`}</div>
    <form class="card form" id="manual" style="margin-top:0.8rem">
      <label>IP address<input name="host" placeholder="192.168.1.50" required></label>
      <label>Name<input name="name" placeholder="Back garden"></label>
      <div class="actions"><button class="primary" type="submit">Add telescope</button></div>
    </form>`;
}

function renderSky() {
  return `
    <div class="page-head"><h1>Sky</h1><div class="muted">Pointing <span data-field="target"></span></div></div>
    ${banner()}
    <div class="work">
      <section class="card">
        <canvas class="rose" id="rose" width="640" height="280"></canvas>
        <div class="row">
          <div>Right ascension <b data-field="ra"></b></div>
          <div>Declination <b data-field="dec"></b></div>
          <div>Altitude <b data-field="alt"></b></div>
          <div>Azimuth <b data-field="az"></b></div>
          <div>Tracking <b data-field="tracking"></b></div>
        </div>
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
          <div class="muted">Press and hold. Speed is a short sidereal nudge.</div>
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
      <h1>Picture</h1>
      <div class="actions">
        <button id="layout">${state.layout === "theater" ? "Show controls" : "Theater"}</button>
        <button id="live-tele">Telephoto</button>
        <button id="live-wide">Wide camera</button>
        <button id="live-stop">Stop live view</button>
      </div>
    </div>
    ${banner()}
    <div class="work">
      <div class="stage">
        <img id="stage" alt="Latest frame from the telescope">
        <div class="overlay">
          <span class="card" style="padding:0.35rem 0.6rem"><span data-field="target"></span> · <span data-field="stack"></span></span>
        </div>
      </div>
      <div class="cards">
        <div class="card">
          <h2>Exposure</h2>
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
      </div>
    </div>`;
}

function renderAlign() {
  return `
    <div class="page-head"><h1>Align</h1></div>
    ${banner()}
    <div class="grid">
      <section class="card">
        <h2>Mount</h2>
        <p>Current mode: <b data-field="mount"></b></p>
        <p class="muted">Switching mode levels the arm and then parks, which is how the Seestar changes between alt-az and equatorial.</p>
        <div class="actions">
          <button id="to-eq">Use equatorial</button>
          <button id="to-az">Use alt-az</button>
        </div>
      </section>
      <section class="card">
        <h2>Three-point polar alignment</h2>
        <p data-field="align"></p>
        <label>Declination band
          <select id="dec-band">
            <option value="2">Lower</option>
            <option value="3" selected>Overhead</option>
            <option value="4">Higher</option>
          </select>
        </label>
        <div class="actions">
          <button class="primary" id="align-start">Start alignment</button>
          <button id="align-recheck">Recheck</button>
          <button id="align-stop">Stop</button>
        </div>
      </section>
    </div>`;
}

function renderAlbum() {
  const folders = state.folders.map((folder) => `
    <button data-folder="${escapeHtml(folder.name)}">
      <span>${escapeHtml(folder.name)}</span>
      <span class="muted">${escapeHtml(folder.group || "")} · ${folder.count ?? ""}</span>
    </button>`).join("");
  const files = state.files.map((file) => file.is_dir ? `
    <div class="file"><span>${escapeHtml(file.name)}</span><span class="muted">Folder</span></div>` : `
    <label class="file"><span><input type="checkbox" name="file" value="${escapeHtml(file.name)}"> ${escapeHtml(file.name)}</span>
      <span class="muted">${file.size_k ? file.size_k + " KB" : ""}</span></label>`).join("");
  const thumbs = state.folders.map((folder) => folder.thumb ? `
    <figure>
      <img src="${scopePath("/media")}?path=${encodeURIComponent("MyWorks/" + folder.thumb)}" alt="">
      <figcaption>${escapeHtml(folder.name)}</figcaption>
    </figure>` : "").join("");
  return `
    <div class="page-head"><h1>Album</h1><button id="reload-album">Refresh</button></div>
    ${banner()}
    <div class="thumbs">${thumbs}</div>
    <div class="work" style="margin-top:0.8rem">
      <section class="card"><h2>Sessions</h2><div class="list">${folders || '<div class="muted">No sessions reported yet.</div>'}</div></section>
      <section class="card">
        <h2>${escapeHtml(state.folder || "Files")}</h2>
        <div class="list">${files}</div>
        <div class="actions"><button class="primary" id="copy">Copy selected to this computer</button></div>
      </section>
    </div>`;
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
    <form class="card" id="site">
      <h2>Place and clock</h2>
      <div class="form">
        <label>Latitude<input name="lat" placeholder="51.5"></label>
        <label>Longitude<input name="lon" placeholder="-0.12"></label>
      </div>
      <div class="actions">
        <button class="primary" type="submit">Save location</button>
        <button type="button" id="set-clock">Set the telescope clock from this computer</button>
      </div>
    </form>
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

function bind() {
  const find = document.querySelector("#find");
  if (find) find.onclick = () => guard(async () => { state.scopes = (await api("/api/discover", { method: "POST" })).scopes; render(); });
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
      state.selected = button.dataset.connect;
      localStorage.setItem("seestar-scope", state.selected);
      state.error = "";
      state.snap = await api(scopePath("/connect"), { method: "POST" });
      state.scopes = (await api("/api/scopes")).scopes;
      render();
    });
  });
  document.querySelectorAll("[data-claim]").forEach((button) => {
    button.onclick = () => guard(async () => {
      state.selected = button.dataset.claim;
      state.snap = await api(scopePath("/claim"), { method: "POST" });
      render();
    });
  });
  document.querySelectorAll("[data-release]").forEach((button) => {
    button.onclick = () => guard(async () => {
      state.selected = button.dataset.release;
      state.snap = await api(scopePath("/release"), { method: "POST" });
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
  const pad = document.querySelector("#pad");
  if (pad) bindPad(pad);
  bindClick("#layout", () => {
    state.layout = state.layout === "theater" ? "cards" : "theater";
    localStorage.setItem("seestar-layout", state.layout);
    document.body.classList.toggle("theater", state.layout === "theater");
    render();
  });
  bindClick("#live-tele", () => startLive("tele"));
  bindClick("#live-wide", () => startLive("wide"));
  bindClick("#live-stop", () => guard(async () => { await api(scopePath("/live"), { method: "POST", body: JSON.stringify({ enabled: false, camera: state.camera }) }); }));
  bindClick("#stack-start", () => post("/stack", { action: "start", restart: document.querySelector("#restart")?.checked ?? true, gain: document.querySelector("#gain")?.value }));
  bindClick("#stack-stop", () => post("/stack", { action: "stop" }));
  bindClick("#autofocus", () => post("/focus", { action: "auto" }));
  bindClick("#focus-stop", () => post("/focus", { action: "stop" }));
  document.querySelectorAll("[data-focus]").forEach((button) => {
    button.onclick = () => post("/focus", { action: "move", delta: Number(button.dataset.focus) });
  });
  document.querySelectorAll("[data-filter]").forEach((button) => {
    button.onclick = () => post("/filter", { position: Number(button.dataset.filter) });
  });
  bindClick("#to-eq", () => {
    if (confirm("This levels the arm, then parks into equatorial mode.")) post("/mount", { equatorial: true });
  });
  bindClick("#to-az", () => {
    if (confirm("This levels the arm, then parks into alt-az mode.")) post("/mount", { equatorial: false });
  });
  bindClick("#align-start", () => post("/align", { action: "start", dec_pos_index: document.querySelector("#dec-band")?.value }));
  bindClick("#align-recheck", () => post("/align", { action: "recheck" }));
  bindClick("#align-stop", () => post("/align", { action: "stop" }));
  bindClick("#reload-album", loadAlbum);
  document.querySelectorAll("[data-folder]").forEach((button) => {
    button.onclick = () => guard(async () => {
      state.folder = button.dataset.folder;
      state.files = (await api(`${scopePath("/files")}?folder=${encodeURIComponent(state.folder)}`)).files;
      render();
    });
  });
  bindClick("#copy", () => guard(async () => {
    const names = [...document.querySelectorAll("input[name=file]:checked")].map((node) => node.value);
    const result = await api(scopePath("/download"), { method: "POST", body: JSON.stringify({ folder: state.folder, names }) });
    toast(`Copied ${result.saved.length} file${result.saved.length === 1 ? "" : "s"} to ${result.dest}`);
  }));
  const site = document.querySelector("#site");
  if (site) site.onsubmit = (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(site));
    post("/settings", data);
  };
  bindClick("#set-clock", () => post("/settings", { set_clock: true }));
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

function startLive(camera) {
  state.camera = camera;
  return guard(async () => {
    await api(scopePath("/live"), { method: "POST", body: JSON.stringify({ camera, enabled: true }) });
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
    api(scopePath("/move"), { method: "POST", body: JSON.stringify({ angle, speed: 1500, dur: 1 }) }).catch((error) => toast(error.message));
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
  state.albumLoaded = true;
  if (state.page === "album") render();
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
  ctx.font = "14px Palatino, Georgia, serif";
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
  ctx.fillStyle = "#e0b15a";
  ctx.beginPath();
  ctx.arc(x, y, 6, 0, Math.PI * 2);
  ctx.fill();
}

function applyIncoming(message) {
  if (message.type === "hello") state.scopes = message.scopes || state.scopes;
  const snap = message.state || (message.type === "state" ? message : null);
  if (snap && snap.host && (!state.selected || snap.id === state.selected)) {
    state.snap = snap;
  }
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

async function boot() {
  clock();
  setInterval(clock, 1000);
  try {
    state.scopes = (await api("/api/scopes")).scopes;
    if (!state.selected && state.scopes[0]) state.selected = state.scopes[0].id;
    if (state.selected) {
      try { state.snap = (await api(scopePath("/state"))).state; } catch (error) { /* not connected yet */ }
    }
  } catch (error) {
    toast(error.message);
  }
  connectSocket();
  render();
}

boot();
