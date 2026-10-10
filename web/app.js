"use strict";

// The form for webapp.py: builds a map request in the CLI's option names, queues it and
// follows it until the map is drawn.

const $ = (id) => document.getElementById(id);
const form = $("map-form");
const STORAGE_KEY = "ortho-web-form";
const MEANS = ["temperature", "precipitation", "humidity"];
let options = null;
let polling = null;

function option(value, label) {
  const el = document.createElement("option");
  el.value = value;
  el.textContent = label ?? value;
  return el;
}

function radio(name) {
  return form.querySelector(`input[name="${name}"]:checked`).value;
}

// The value a data-show condition looks at: a radio group, a select or a checkbox
function current(key) {
  const el = $(key);
  if (!el) return radio(key);
  return el.type === "checkbox" ? (el.checked ? "on" : "") : el.value;
}

function sync() {
  for (const el of form.querySelectorAll("[data-show]")) {
    const [key, values] = el.dataset.show.split(":");
    const value = current(key);
    el.hidden = values ? !values.split(",").includes(value) : !value;
  }
  $("lat").required = $("lon").required = radio("place") === "coords";
  $("radius").required = radio("view") === "radius";
  $("climate-classes").placeholder = $("climate").value === "trewartha" ? "e.g. Do, C" : "e.g. Cfb, Cs";
  fillZoom();
  for (const out of form.querySelectorAll("output")) out.value = Number($(out.htmlFor).value).toFixed(2);
}

function fillZoom() {
  const select = $("zoom");
  const keep = select.value;
  const top = radio("view") === "radius" ? options.zoom.radius_max : options.zoom.globe_max;
  const auto = radio("view") === "radius" ? "Auto (about a dozen tiles across)" : `Auto (${options.zoom.default})`;
  select.replaceChildren(option("", auto));
  for (let z = 1; z <= top; z++) select.append(option(String(z)));
  select.value = [...select.options].some((o) => o.value === keep) ? keep : "";
}

function climateAlphaDefault() {
  const layer = $("climate").value;
  return MEANS.includes(layer) ? options.alpha.climate_mean : options.alpha.classification;
}

function list(text) {
  return text.split(",").map((s) => s.trim()).filter(Boolean);
}

function changed(id, fallback) {
  const value = Number($(id).value);
  return Math.abs(value - fallback) > 1e-9 ? value : undefined;
}

// The request, in the CLI's option names; options at their default are left out
function buildRequest() {
  const req = {};
  if (radio("place") === "city") req.city = $("city").value;
  else { req.lat = Number($("lat").value); req.lon = Number($("lon").value); }
  if ($("provider").value !== "osm") req.provider = $("provider").value;
  if ($("zoom").value) req.zoom = Number($("zoom").value);
  req.dpi = Number($("dpi").value);
  const view = radio("view");
  if (view === "hemispheres") req.both_hemispheres = true;
  if (view === "radius") req.radius = Number($("radius").value);
  if (radio("orient") === "bearing") {
    const up = Number($("up").value || 0);
    if (up) req.up = up;
  } else if ($("up-toward").value.trim()) {
    req.up_toward = $("up-toward").value.trim();
  }

  const climate = $("climate").value;
  if (climate === "koppen" || climate === "trewartha") {
    const classes = list($("climate-classes").value);
    if (classes.length) req[`${climate}_class`] = classes;
    else req[climate] = true;
  } else if (MEANS.includes(climate)) {
    req[climate] = $("period").value;
  }
  if (climate) req.koppen_alpha = changed("climate-alpha", climateAlphaDefault());

  if ($("elevation").checked) {
    req.elevation = true;
    req.elevation_alpha = changed("elevation-alpha", options.alpha.elevation);
  }
  const vegetation = $("vegetation").value;
  if (vegetation === "landcover") {
    const classes = list($("landcover-classes").value);
    if (classes.length) req.landcover_class = classes;
    else req.landcover = true;
    if ($("landcover-year").value) req.landcover_year = Number($("landcover-year").value);
  } else if (vegetation === "ndvi") {
    req.ndvi = $("ndvi").value.trim() || "july";
  }
  if (vegetation) req.vegetation_alpha = changed("vegetation-alpha", options.alpha.vegetation);
  const soil = $("soil").value;
  if (soil === "groups") {
    const classes = list($("soil-classes").value);
    if (classes.length) req.soil_class = classes;
    else req.soil = true;
  } else if (soil === "property") {
    req.soil_property = $("soil-property").value;
    if ($("soil-depth").value) req.soil_depth = $("soil-depth").value;
  }
  if (soil) req.soil_alpha = changed("soil-alpha", options.alpha.soil);

  if ($("wind").value) req.wind = $("wind").value;
  if ($("ice").checked) {
    req.ice = true;
    if ($("ice-year").value) req.ice_year = Number($("ice-year").value);
  }
  const crops = list($("crops").value);
  if (crops.length) req.crop = crops;
  return req;
}

// --- Remembering the form in this browser -------------------------------------------------

function saveForm() {
  const state = {};
  for (const el of form.elements) {
    if (el.type === "radio") { if (el.checked) state[`radio:${el.name}`] = el.value; }
    else if (el.id) state[el.id] = el.type === "checkbox" ? el.checked : el.value;
  }
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(state)); } catch { /* storage may be blocked */ }
}

function restoreForm() {
  let state = null;
  try { state = JSON.parse(localStorage.getItem(STORAGE_KEY)); } catch { /* ignore */ }
  if (!state) return;
  for (const [key, value] of Object.entries(state)) {
    if (!key.startsWith("radio:")) continue;
    const el = form.querySelector(`input[name="${key.slice(6)}"][value="${CSS.escape(value)}"]`);
    if (el) el.checked = true;
  }
  fillZoom();  // the zoom levels on offer depend on the view
  for (const [key, value] of Object.entries(state)) {
    if (!key.startsWith("radio:") && $(key)) {
      const el = $(key);
      if (el.type === "checkbox") el.checked = Boolean(value);
      else if (el.tagName !== "SELECT" || [...el.options].some((o) => o.value === value)) el.value = value;
    }
  }
}

// --- Rendering and following a map ------------------------------------------------------

function showError(message) {
  $("form-error").textContent = message;
  $("form-error").hidden = !message;
}

function errorText(body, status) {
  const detail = body && body.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((d) => `${d.loc.slice(1).join(".")}: ${d.msg}`).join("; ");
  return `The server answered ${status}.`;
}

function setBusy(busy) {
  $("render").disabled = busy;
  $("render").textContent = busy ? "Rendering…" : "Render map";
  $("stage").classList.toggle("busy", busy);
}

function show(job) {
  const status = {
    queued: job.position ? `Queued, ${job.position} ${job.position === 1 ? "map" : "maps"} ahead` : "Queued",
    running: `Rendering ${job.label}… ${Math.round(job.seconds ?? 0)} s`,
    done: `${job.label}: drawn in ${job.seconds} s`,
    failed: `${job.label}: failed`,
  }[job.status];
  $("status-text").textContent = status;
  const activity = job.status === "failed" ? job.error : job.activity;
  $("activity").textContent = activity || "";
  $("activity").hidden = !activity;
  $("activity").classList.toggle("error", job.status === "failed");

  $("command-text").textContent = job.command;
  $("command").hidden = false;
  $("warnings").replaceChildren(...job.warnings.map((w) => {
    const li = document.createElement("li");
    li.textContent = w;
    return li;
  }));
  $("warnings").hidden = job.warnings.length === 0;

  if (job.status === "done") {
    const img = $("map-image");
    img.onload = () => { img.hidden = false; $("placeholder").style.display = "none"; };
    img.alt = `Orthographic globe map centred on ${job.label}`;
    img.src = job.image;
    $("open").href = job.image;
    $("download").href = `${job.image}?download=1`;
    $("actions").hidden = false;
  }
}

async function follow(id) {
  try {
    const response = await fetch(`/api/maps/${id}`);
    const job = await response.json();
    if (!response.ok) throw new Error(errorText(job, response.status));
    show(job);
    if (job.status === "queued" || job.status === "running") {
      polling = setTimeout(() => follow(id), 1000);
      return;
    }
  } catch (e) {
    $("status-text").textContent = `Lost track of the map: ${e.message}`;
  }
  setBusy(false);
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  showError("");
  if (!form.reportValidity()) return;
  saveForm();
  clearTimeout(polling);
  setBusy(true);
  $("actions").hidden = true;
  $("status-text").textContent = "Sending…";
  try {
    const response = await fetch("/api/maps", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildRequest()),
    });
    const body = await response.json().catch(() => null);
    if (!response.ok) throw new Error(errorText(body, response.status));
    show(body);
    follow(body.id);
  } catch (e) {
    showError(e.message);
    $("status-text").textContent = "Not rendered.";
    setBusy(false);
  }
});

form.addEventListener("input", sync);
form.addEventListener("change", (event) => {
  if (event.target.id === "climate") $("climate-alpha").value = climateAlphaDefault();
  sync();
  saveForm();
});

$("copy").addEventListener("click", async () => {
  const text = $("command-text").textContent;
  try {
    await navigator.clipboard.writeText(text);
    $("copy").textContent = "Copied";
  } catch {
    getSelection().selectAllChildren($("command-text"));
    $("copy").textContent = "Selected";
  }
  setTimeout(() => { $("copy").textContent = "Copy"; }, 1500);
});

// --- Start: fill the choices from the server ----------------------------------------------

async function init() {
  options = await (await fetch("/api/options")).json();
  const names = { osm: "OpenStreetMap", google: "Google roadmap", google_satellite: "Google satellite",
                  nasa: "NASA Blue Marble" };
  $("city").append(...options.cities.map((c) => option(c)));
  $("city").value = "Paris";
  $("city-list").append(...options.cities.map((c) => option(c)));
  $("provider").append(...options.providers.map((p) => option(p, names[p] ?? p)));
  for (const select of form.querySelectorAll("select.periods")) {
    select.append(...options.periods.map(([value, label]) => option(value, label)));
  }
  const [first, latest] = options.land_cover_years;
  $("landcover-year").append(option("", `Latest (${latest})`));
  for (let y = latest - 1; y >= first; y--) $("landcover-year").append(option(String(y)));
  $("soil-property").append(...Object.entries(options.soil_properties).map(([k, title]) => option(k, title)));
  $("soil-depth").append(option("", "Default"), ...options.soil_depths.map((d) => option(d)));
  $("ice-year").min = options.first_ice_year;
  $("radius").min = options.radius.min;
  $("radius").max = options.radius.max;
  $("radius").value = options.radius.default;
  $("dpi").min = options.dpi.min;
  $("dpi").max = options.dpi.max;
  $("dpi").value = options.dpi.default;
  $("climate-alpha").value = options.alpha.classification;
  $("elevation-alpha").value = options.alpha.elevation;
  $("vegetation-alpha").value = options.alpha.vegetation;
  $("soil-alpha").value = options.alpha.soil;
  $("crops").title = `${options.crops.length} crops, e.g. ${options.crops.slice(0, 8).join(", ")}…`;
  fillZoom();
  restoreForm();
  sync();
}

init().catch((e) => {
  $("status-text").textContent = `Could not reach the server: ${e.message}`;
  $("render").disabled = true;
});
