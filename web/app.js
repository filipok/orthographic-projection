"use strict";

// The form for webapp.py: builds a map request in the CLI's option names, queues it and
// follows it until the map is drawn. Recipes fill the form, and the form saves them.

const $ = (id) => document.getElementById(id);
const form = $("map-form");
const STORAGE_KEY = "ortho-web-form";
const MEANS = ["temperature", "precipitation", "humidity"];
const RECIPE_NAME = /^[a-z0-9][a-z0-9_-]{0,63}$/;
let options = null;
let polling = null;
let defaults = null;      // the form as it first loads, which a recipe starts from
let recipes = [];         // [{name, description}]
let routeFiles = [];      // [{name, routes, error?}]
let loadedRecipe = "";    // the recipe the form was last loaded from or saved as

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
  $("recipe-delete").disabled = !$("recipe").value;
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
  if (view !== "hemispheres" && $("beside").value.trim()) req.beside = $("beside").value.trim();
  if (radio("orient") === "bearing") {
    const up = Number($("up").value || 0);
    if (up) req.up = up;
  } else if ($("up-toward").value.trim()) {
    req.up_toward = $("up-toward").value.trim();
  }
  if ($("circles-from").value.trim()) req.circles_from = $("circles-from").value.trim();

  const climate = $("climate").value;
  if (climate === "koppen" || climate === "trewartha") {
    const classes = list($("climate-classes").value);
    if (classes.length) req[`${climate}_class`] = classes;
    else req[climate] = true;
  } else if (MEANS.includes(climate)) {
    req[climate] = $("period").value;
  }
  if (climate) req.koppen_alpha = changed("climate-alpha", climateAlphaDefault());
  if (climate === "temperature" && $("temperature-over-sea").checked) req.temperature_over_sea = true;
  else if ($("sea-temperature").value) req.sea_temperature = $("sea-temperature").value;

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
  const biomes = $("biomes").value;
  if (biomes === "holt") {
    req.realms = "holt";
  } else if (biomes) {
    const classes = list($("biome-classes").value);
    if (classes.length) req.biome_class = classes;
    if (biomes === "ecoregions") req.ecoregions = true;
    else if (biomes === "realms") req.realms = "wwf";
    else if (!classes.length) req.biomes = true;
  }
  if (biomes) req.biome_alpha = changed("biome-alpha", options.alpha.biome);
  if ($("realm-regions").checked) req.realm_regions = true;
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
  for (const name of ["ocean-depth", "ice-age", "currents", "plates", "volcanoes", "rivers", "time-zones"]) {
    if ($(name).checked) req[name.replace("-", "_")] = true;
  }
  if ($("earthquakes").checked) {
    req.earthquakes = Number($("earthquake-magnitude").value) || options.earthquakes.default;
  }
  if ($("basins").checked) {
    req.basins = true;
    req.basins_alpha = changed("basins-alpha", options.alpha.basins);
  }
  if ($("cyclones").checked) req.cyclones = Number($("cyclones-since").value) || options.first_cyclone_year;
  if ($("permafrost").checked) {
    req.permafrost = true;
    req.permafrost_alpha = changed("permafrost-alpha", options.alpha.permafrost);
  }
  if ($("population").value) {
    req.population = Number($("population").value);
    req.population_alpha = changed("population-alpha", options.alpha.population);
  }
  if ($("livestock").value) {
    req.livestock = $("livestock").value;
    req.livestock_alpha = changed("livestock-alpha", options.alpha.livestock);
  }
  if ($("malaria").value) {
    req.malaria = $("malaria").value;
    const year = Number($("malaria-year").value);
    if (year && year !== options.malaria_years[1]) req.malaria_year = year;
    req.malaria_alpha = changed("malaria-alpha", options.alpha.malaria);
  }
  if ($("aedes").value) req.aedes = $("aedes").value;
  if ($("species").value.trim()) {
    req.species = $("species").value.trim();
    if ($("species-years").value.trim()) req.species_years = $("species-years").value.trim();
  }
  if ($("iucn").value.trim()) {
    req.iucn = $("iucn").value.trim();
    req.iucn_alpha = changed("iucn-alpha", options.alpha.iucn);
  }
  if ($("indicator").value.trim()) {
    req.indicator = $("indicator").value.trim();
    if ($("indicator-year").value) req.indicator_year = Number($("indicator-year").value);
    req.indicator_alpha = changed("indicator-alpha", options.alpha.indicator);
  }
  if ($("cities-year").value) {
    req.cities = Number($("cities-year").value);
    if ($("cities-min").value) req.cities_min = Number($("cities-min").value);
  }
  const borders = $("borders").value.trim();
  if (borders) {
    req.borders = borders;
    if ($("borders-source").value !== "auto") req.borders_source = $("borders-source").value;
    req.borders_alpha = changed("borders-alpha", options.alpha.borders);
  }
  const routes = selectedRoutes();
  if (routes.length) {
    req.route = routes;
    if ($("route-legend").checked) req.route_legend = true;
  }
  return req;
}

// --- The form's state: remembered in this browser, and the base a recipe starts from ----

function formState() {
  const state = {};
  for (const el of form.elements) {
    if (el.type === "radio") { if (el.checked) state[`radio:${el.name}`] = el.value; }
    else if (el.id && !["file", "button", "submit", "fieldset"].includes(el.type)) {
      state[el.id] = el.type === "checkbox" ? el.checked : el.value;
    }
  }
  return state;
}

function applyState(state) {
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
      else if (el.type === "file") continue;
      else if (el.tagName !== "SELECT" || [...el.options].some((o) => o.value === value)) el.value = value;
    }
  }
  renderRoutes();
}

function saveForm() {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(formState())); } catch { /* storage may be blocked */ }
}

function restoreForm() {
  let state = null;
  try { state = JSON.parse(localStorage.getItem(STORAGE_KEY)); } catch { /* ignore */ }
  if (state) applyState(state);
}

// --- Filling the form from a map request (a recipe's) ------------------------------------

// Sets a select's value if it offers it; returns whether it did
function choose(id, value) {
  const el = $(id);
  const text = value === undefined || value === null ? "" : String(value);
  if (![...el.options].some((o) => o.value === text)) return false;
  el.value = text;
  return true;
}

// The form as *req* describes it, from the form's first state; returns what it could not show
function fillForm(req) {
  applyState(defaults);
  const missing = [];
  const set = (id, value) => { if (value !== undefined && value !== null) $(id).value = value; };
  const pick = (id, value, what) => { if (!choose(id, value)) missing.push(`${what} ${value}`); };
  const check = (name, value) => { form.querySelector(`input[name="${name}"][value="${value}"]`).checked = true; };

  if (req.lat !== undefined || req.lon !== undefined) {
    check("place", "coords");
    set("lat", req.lat);
    set("lon", req.lon);
  } else if (req.city) {
    check("place", "city");
    pick("city", req.city, "city");
  }
  if (req.provider) pick("provider", req.provider, "base map");
  if (req.both_hemispheres) check("view", "hemispheres");
  else if (req.radius) { check("view", "radius"); set("radius", req.radius); }
  else check("view", "globe");
  set("beside", req.beside);
  fillZoom();
  if (req.zoom) pick("zoom", req.zoom, "zoom");
  if (req.dpi) set("dpi", Math.min(req.dpi, options.dpi.max));
  if (req.up_toward) { check("orient", "toward"); set("up-toward", req.up_toward); }
  else { check("orient", "bearing"); set("up", req.up ?? 0); }
  set("circles-from", req.circles_from);

  let climate = "";
  for (const name of ["koppen", "trewartha"]) {
    if (req[name] || req[`${name}_class`]) {
      climate = name;
      $("climate-classes").value = (req[`${name}_class`] || []).join(", ");
    }
  }
  for (const name of MEANS) {
    if (req[name]) { climate = name; pick("period", req[name], "period"); }
  }
  $("climate").value = climate;
  $("climate-alpha").value = req.koppen_alpha ?? climateAlphaDefault();
  $("temperature-over-sea").checked = Boolean(req.temperature_over_sea);
  if (req.sea_temperature) pick("sea-temperature", req.sea_temperature, "sea temperature period");

  $("elevation").checked = Boolean(req.elevation);
  set("elevation-alpha", req.elevation_alpha);
  if (req.landcover || req.landcover_class || req.landcover_year) {
    $("vegetation").value = "landcover";
    $("landcover-classes").value = (req.landcover_class || []).join(", ");
    if (req.landcover_year) pick("landcover-year", req.landcover_year, "land cover year");
  } else if (req.ndvi) {
    $("vegetation").value = "ndvi";
    $("ndvi").value = req.ndvi;
  }
  set("vegetation-alpha", req.vegetation_alpha);
  if (req.ecoregions) $("biomes").value = "ecoregions";
  else if (req.realms === "holt") $("biomes").value = "holt";
  else if (req.realms) $("biomes").value = "realms";
  else if (req.biomes || req.biome_class) $("biomes").value = "biomes";
  $("biome-classes").value = (req.biome_class || []).join(", ");
  set("biome-alpha", req.biome_alpha);
  $("realm-regions").checked = Boolean(req.realm_regions);
  if (req.soil || req.soil_class) {
    $("soil").value = "groups";
    $("soil-classes").value = (req.soil_class || []).join(", ");
  } else if (req.soil_property) {
    $("soil").value = "property";
    pick("soil-property", req.soil_property, "soil property");
    if (req.soil_depth) pick("soil-depth", req.soil_depth, "soil depth");
  }
  set("soil-alpha", req.soil_alpha);

  if (req.wind) pick("wind", req.wind, "wind period");
  $("ice").checked = Boolean(req.ice || req.ice_year);
  set("ice-year", req.ice_year);
  $("crops").value = (req.crop || []).join(", ");
  for (const name of ["ocean-depth", "ice-age", "currents", "plates", "volcanoes", "rivers", "time-zones"]) {
    $(name).checked = Boolean(req[name.replace("-", "_")]);
  }
  $("earthquakes").checked = req.earthquakes != null;
  set("earthquake-magnitude", req.earthquakes);
  $("basins").checked = Boolean(req.basins);
  set("basins-alpha", req.basins_alpha);
  $("cyclones").checked = req.cyclones != null;
  set("cyclones-since", req.cyclones);
  $("permafrost").checked = Boolean(req.permafrost);
  set("permafrost-alpha", req.permafrost_alpha);
  if (req.population != null) pick("population", String(req.population), "population year");
  set("population-alpha", req.population_alpha);
  if (req.livestock) pick("livestock", req.livestock, "livestock");
  set("livestock-alpha", req.livestock_alpha);
  if (req.malaria) pick("malaria", req.malaria, "malaria parasite");
  set("malaria-year", req.malaria_year);
  set("malaria-alpha", req.malaria_alpha);
  if (req.aedes) pick("aedes", req.aedes, "Aedes species");
  set("species", req.species);
  set("species-years", req.species_years);
  set("iucn", req.iucn);
  set("iucn-alpha", req.iucn_alpha);
  set("indicator", req.indicator);
  set("indicator-year", req.indicator_year);
  set("indicator-alpha", req.indicator_alpha);
  if (req.cities != null) pick("cities-year", String(req.cities), "cities year");
  set("cities-min", req.cities_min);
  $("borders").value = req.borders ?? "";
  if (req.borders_source) pick("borders-source", req.borders_source, "borders source");
  set("borders-alpha", req.borders_alpha);
  const routes = req.route || [];
  for (const name of routes) {
    if (!routeFiles.some((f) => f.name === name)) missing.push(`route file ${name}`);
  }
  setRoutes(routes.filter((name) => routeFiles.some((f) => f.name === name)));
  $("route-legend").checked = Boolean(req.route_legend);
  if (routes.length) $("routes-box").open = true;
  sync();
  return missing;
}

// --- Routes: chosen from the routes folder, or uploaded into it --------------------------

function selectedRoutes() {
  try { return JSON.parse($("routes").value) || []; } catch { return []; }
}

function setRoutes(names) {
  $("routes").value = JSON.stringify(names);
  renderRoutes();
}

function plural(n, word) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

function renderRoutes() {
  const chosen = selectedRoutes();
  $("route-list").replaceChildren(...chosen.map((name) => {
    const file = routeFiles.find((f) => f.name === name);
    const li = document.createElement("li");
    const label = document.createElement("span");
    label.className = "route-name";
    label.textContent = name;
    const detail = document.createElement("span");
    detail.className = "route-detail";
    if (!file || file.error) {
      detail.classList.add("error");
      detail.textContent = file ? "unreadable" : "missing";
      detail.title = file ? file.error : "";
    } else {
      detail.textContent = plural(file.routes.length, "route");
      detail.title = file.routes.join("\n");
    }
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "link";
    remove.dataset.remove = name;
    remove.textContent = "Remove";
    remove.setAttribute("aria-label", `Remove ${name}`);
    li.append(label, detail, remove);
    return li;
  }));
  $("route-list").hidden = chosen.length === 0;
  const offered = routeFiles.filter((f) => !chosen.includes(f.name) && !f.error);
  $("route-add").replaceChildren(
    option("", offered.length ? (chosen.length ? "Add another…" : "Add a route file…") : "No other route files"),
    ...offered.map((f) => {
      const el = option(f.name, `${f.name} (${plural(f.routes.length, "route")})`);
      el.title = f.routes.join("\n");
      return el;
    }),
  );
}

function routeStatus(message, isError = false) {
  $("route-status").textContent = message;
  $("route-status").classList.toggle("error", isError);
  $("route-status").hidden = !message;
}

async function loadRouteFiles() {
  routeFiles = await getJSON("/api/routes");
  renderRoutes();
}

$("route-add").addEventListener("change", () => {
  const name = $("route-add").value;
  if (name) setRoutes([...selectedRoutes(), name]);
  saveForm();
});

$("route-list").addEventListener("click", (event) => {
  const name = event.target.dataset && event.target.dataset.remove;
  if (!name) return;
  setRoutes(selectedRoutes().filter((n) => n !== name));
  saveForm();
});

$("route-upload").addEventListener("change", async () => {
  const file = $("route-upload").files[0];
  if (!file) return;
  routeStatus(`Uploading ${file.name}…`);
  try {
    const added = await getJSON("/api/routes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename: file.name, content: await file.text() }),
    });
    await loadRouteFiles();
    setRoutes([...selectedRoutes(), added.name]);
    saveForm();
    routeStatus(`Saved routes/${added.name} (${plural(added.routes.length, "route")}) and added it to the map.`);
  } catch (e) {
    routeStatus(e.message, true);
  }
  $("route-upload").value = "";
});

// --- Recipes: load one into the form, save the form as one, delete one -------------------

async function getJSON(url, init) {
  const response = await fetch(url, init);
  const body = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) throw new Error(errorText(body, response.status));
  return body;
}

function recipeStatus(message, isError = false) {
  $("recipe-status").textContent = message;
  $("recipe-status").classList.toggle("error", isError);
  $("recipe-status").hidden = !message;
}

async function loadRecipes(selected = "") {
  recipes = await getJSON("/api/recipes");
  $("recipe").replaceChildren(option("", "None: start from the form"), ...recipes.map((r) => {
    const el = option(r.name);
    el.title = r.description;
    return el;
  }));
  choose("recipe", selected);
  sync();
}

$("recipe").addEventListener("change", async () => {
  const name = $("recipe").value;
  recipeStatus("");
  if (!name) { loadedRecipe = ""; return; }
  try {
    const recipe = await getJSON(`/api/recipes/${encodeURIComponent(name)}`);
    const missing = fillForm(recipe.request);
    $("recipe").value = recipe.name;
    $("recipe-name").value = recipe.name;
    $("recipe-description").value = recipe.description;
    loadedRecipe = recipe.name;
    const notes = [];
    if (missing.length) notes.push(`Not offered here, so left out: ${missing.join(", ")}.`);
    if (recipe.kept.length) notes.push(`Kept as they are when saved: ${recipe.kept.join("; ")}.`);
    recipeStatus(notes.join(" "), missing.length > 0);
    sync();
    saveForm();
  } catch (e) {
    recipeStatus(e.message, true);
  }
});

async function saveRecipe() {
  const name = $("recipe-name").value.trim();
  if (!RECIPE_NAME.test(name)) {
    recipeStatus("Name the recipe with lower-case letters, digits, '_' and '-', e.g. my_monsoon.", true);
    $("recipe-name").focus();
    return;
  }
  if (!form.reportValidity()) return;
  const exists = recipes.some((r) => r.name === name);
  if (exists && name !== loadedRecipe && !confirm(`Replace the recipe "${name}"?`)) return;
  try {
    const saved = await getJSON(`/api/recipes/${encodeURIComponent(name)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ description: $("recipe-description").value, request: buildRequest(), overwrite: exists }),
    });
    loadedRecipe = name;
    await loadRecipes(name);
    saveForm();
    recipeStatus(`Saved ${saved.file}.`);
  } catch (e) {
    recipeStatus(e.message, true);
  }
}

$("recipe-save").addEventListener("click", saveRecipe);
$("recipe-name").addEventListener("keydown", (event) => {
  if (event.key === "Enter") { event.preventDefault(); saveRecipe(); }
});

$("recipe-delete").addEventListener("click", async () => {
  const name = $("recipe").value;
  if (!name || !confirm(`Delete recipes/${name}.toml?`)) return;
  try {
    await getJSON(`/api/recipes/${encodeURIComponent(name)}`, { method: "DELETE" });
    if (loadedRecipe === name) loadedRecipe = "";
    await loadRecipes("");
    saveForm();
    recipeStatus(`Deleted recipes/${name}.toml.`);
  } catch (e) {
    recipeStatus(e.message, true);
  }
});

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
  const names = { osm: "OpenStreetMap", osm_plain: "OpenStreetMap, plain land", google: "Google roadmap",
                  google_satellite: "Google satellite", nasa: "NASA Blue Marble" };
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
  $("biome-alpha").value = options.alpha.biome;
  $("borders-alpha").value = options.alpha.borders;
  for (const name of ["basins", "permafrost", "population", "livestock", "malaria", "iucn", "indicator"]) {
    $(`${name}-alpha`).value = options.alpha[name];
  }
  [$("malaria-year").min, $("malaria-year").max] = options.malaria_years;
  $("malaria-year").value = options.malaria_years[1];
  $("earthquake-magnitude").min = options.earthquakes.min;
  $("earthquake-magnitude").max = options.earthquakes.max;
  $("earthquake-magnitude").value = options.earthquakes.default;
  $("cyclones-since").min = options.first_cyclone_year;
  $("cyclones-since").value = options.first_cyclone_year;
  $("population").append(...options.population_years.slice().reverse().map((y) => option(String(y))));
  $("indicator-list").append(...Object.entries(options.indicators).map(([name, title]) => option(name, title)));
  $("cities-year").append(...options.cities_years.slice().reverse().map((y) => option(String(y),
    y === 2030 ? "2030 (projected)" : String(y))));
  $("livestock").append(...options.livestock.map((name) => option(name, name[0].toUpperCase() + name.slice(1))));
  $("aedes").append(...options.aedes.map((name) => option(name, name === "both" ? "Both species" : `Aedes ${name}`)));
  $("soil-alpha").value = options.alpha.soil;
  $("crops").title = `${options.crops.length} crops, e.g. ${options.crops.slice(0, 8).join(", ")}…`;
  for (const el of document.querySelectorAll(".editable")) el.hidden = !options.editable;
  await Promise.all([loadRouteFiles(), loadRecipes()]);
  fillZoom();
  defaults = formState();
  restoreForm();
  loadedRecipe = $("recipe").value;
  sync();
}

init().catch((e) => {
  $("status-text").textContent = `Could not reach the server: ${e.message}`;
  $("render").disabled = true;
});
