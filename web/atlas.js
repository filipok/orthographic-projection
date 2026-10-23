"use strict";

// The atlas page for webapp.py: edits an atlas file's settings and pages, sends a page's map to
// the map form (/?atlas=NAME&page=N) to be changed there, and builds the atlas's PDF.

const $ = (id) => document.getElementById(id);
const ATLAS_NAME = /^[a-z0-9][a-z0-9_-]{0,63}$/;
const FALLBACK_MAP = { lat: 45.95, lon: 24.95, radius: 450 };
let options = null;
let names = [];           // [{name, title, pages, error?}]
let current = null;       // {name, atlas: {title, …, cover?, pages: [{type, title, text, questions, map, kept, recipe}]}}
let dirty = false;
let polling = null;

function option(value, label) {
  const el = document.createElement("option");
  el.value = value;
  el.textContent = label ?? value;
  return el;
}

async function getJSON(url, init) {
  const response = await fetch(url, init);
  const body = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) {
    const detail = body && body.detail;
    if (typeof detail === "string") throw new Error(detail);
    if (Array.isArray(detail)) throw new Error(detail.map((d) => `${d.loc.slice(1).join(".")}: ${d.msg}`).join("; "));
    throw new Error(`The server answered ${response.status}.`);
  }
  return body;
}

function status(message, isError = false) {
  $("status").textContent = message;
  $("status").classList.toggle("error", isError);
  $("status").hidden = !message;
}

function setDirty(value) {
  dirty = value;
  $("dirty").hidden = !dirty || !options.editable;
}

const json = (path, method, body) => getJSON(path, {
  method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
});
const atlasUrl = (name) => `/api/atlases/${encodeURIComponent(name)}`;

// --- Describing a map in a line ----------------------------------------------------------

function summary(page) {
  if (!page) return "none";
  const parts = [];
  if (page.recipe) parts.push(`recipe ${page.recipe}`);
  for (const [key, value] of Object.entries(page.map || {})) {
    if (value === true) parts.push(key.replaceAll("_", "-"));
    else parts.push(`${key.replaceAll("_", "-")} ${Array.isArray(value) ? value.join(", ") : value}`);
  }
  for (const [key, value] of page.kept || []) parts.push(`${key} ${Array.isArray(value) ? value.join(", ") : value}`);
  return parts.join(" · ") || "no options yet";
}

function lastMap(before = Infinity) {
  const pages = current.atlas.pages.slice(0, before);
  const found = pages.reverse().find((p) => p.type === "map" && p.map && Object.keys(p.map).length);
  return structuredClone(found ? found.map : FALLBACK_MAP);
}

// --- The pages -------------------------------------------------------------------------------

function renderPages() {
  const list = $("pages");
  const pages = current.atlas.pages;
  let section = 0;
  list.replaceChildren(...pages.map((page, i) => {
    const card = $("page-template").content.firstElementChild.cloneNode(true);
    const number = i + 1;
    card.id = `page-${number}`;
    if (page.type === "section") section += 1;
    card.classList.toggle("section", page.type === "section");
    card.querySelector(".page-number").textContent = page.type === "section" ? `Part ${section}` : `Page ${number}`;
    const type = card.querySelector(".page-type");
    type.value = page.type;
    const orientation = card.querySelector(".page-orientation");
    orientation.value = page.orientation || "auto";
    orientation.hidden = page.type !== "map";
    const title = card.querySelector(".page-title");
    title.value = page.title || "";
    const text = card.querySelector(".page-text");
    text.value = page.text || "";
    card.querySelector(".text-label").textContent =
      { map: "Text below the map", section: "Introduction", text: "Text" }[page.type];
    const questions = card.querySelector(".page-questions");
    questions.value = (page.questions || []).join("\n");
    card.querySelector(".page-questions-field").hidden = page.type === "section";
    card.querySelector(".map-summary").hidden = page.type !== "map";
    card.querySelector(".page-map").textContent = summary(page);
    for (const el of card.querySelectorAll(".editable")) el.hidden = !options.editable;
    for (const el of card.querySelectorAll("input, textarea, select")) el.disabled = !options.editable;

    title.addEventListener("input", () => { page.title = title.value; setDirty(true); });
    text.addEventListener("input", () => { page.text = text.value; setDirty(true); });
    questions.addEventListener("input", () => {
      page.questions = questions.value.split("\n").map((q) => q.trim()).filter(Boolean);
      setDirty(true);
    });
    orientation.addEventListener("change", () => { page.orientation = orientation.value; setDirty(true); });
    type.addEventListener("change", () => {
      page.type = type.value;
      if (page.type === "map" && !page.recipe && !(page.map && Object.keys(page.map).length)) page.map = lastMap(i);
      setDirty(true);
      renderPages();
    });
    card.addEventListener("click", (event) => {
      const act = event.target.dataset && event.target.dataset.act;
      if (!act) return;
      if (act === "up" && i > 0) [pages[i - 1], pages[i]] = [pages[i], pages[i - 1]];
      else if (act === "down" && i < pages.length - 1) [pages[i + 1], pages[i]] = [pages[i], pages[i + 1]];
      else if (act === "copy") pages.splice(i + 1, 0, structuredClone(page));
      else if (act === "remove") {
        if (!confirm(`Delete the page “${page.title || "untitled"}”?`)) return;
        pages.splice(i, 1);
      } else if (act === "map") { editMap(String(number)); return; }
      else return;
      setDirty(true);
      renderPages();
    });
    return card;
  }));
  $("empty").hidden = pages.length > 0;
}

function addPage(type) {
  const page = { type, title: { section: "New part", map: "New map", text: "New page" }[type], text: "",
                 questions: [], orientation: "auto" };
  if (type === "map") Object.assign(page, { map: lastMap(), kept: [], recipe: null });
  current.atlas.pages.push(page);
  setDirty(true);
  renderPages();
  const card = $("pages").lastElementChild;
  card.scrollIntoView({ behavior: "smooth", block: "center" });
  card.querySelector(".page-title").select();
}

for (const button of document.querySelectorAll("[data-add]")) {
  button.addEventListener("click", () => current && addPage(button.dataset.add));
}

// --- Settings and cover ----------------------------------------------------------------------

const SETTINGS = ["title", "subtitle", "author", "edition", "language", "paper", "dpi", "text_scale"];
const NUMBERS = new Set(["dpi", "text_scale"]);

function renderSettings() {
  for (const key of SETTINGS) {
    $(key).value = current.atlas[key] ?? "";
    $(key).disabled = !options.editable;
  }
  $("cover-summary").textContent = summary(current.atlas.cover);
  $("cover-edit").textContent = current.atlas.cover ? "Edit the cover map" : "Add a cover map";
  $("cover-remove").hidden = !current.atlas.cover || !options.editable;
}

for (const key of SETTINGS) {
  $(key).addEventListener("input", () => {
    current.atlas[key] = NUMBERS.has(key) ? ($(key).value ? Number($(key).value) : null) : $(key).value;
    setDirty(true);
  });
}

$("cover-edit").addEventListener("click", () => {
  if (!current.atlas.cover) {
    current.atlas.cover = { map: { ...lastMap(), no_circles: true }, kept: [], recipe: null };
    delete current.atlas.cover.map.radius;
    delete current.atlas.cover.map.rectangle;   // cut from a radius map only
    setDirty(true);
  }
  editMap("cover");
});

$("cover-remove").addEventListener("click", () => {
  delete current.atlas.cover;
  setDirty(true);
  renderSettings();
});

// --- Loading, saving, the map form --------------------------------------------------------------

async function loadNames(selected) {
  names = await getJSON("/api/atlases");
  $("atlas-name").replaceChildren(...names.map((a) => option(a.name, `${a.name}: ${a.title}${a.error ? " (cannot be read)" : ""}`)));
  if (selected && names.some((a) => a.name === selected)) $("atlas-name").value = selected;
  return $("atlas-name").value;
}

async function load(name) {
  clearTimeout(polling);
  $("pdf-actions").hidden = true;
  $("warnings").hidden = true;
  $("progress").hidden = true;
  status("");
  if (!name) {
    current = null;
    $("pages").replaceChildren();
    $("empty").hidden = false;
    return;
  }
  try {
    current = await getJSON(atlasUrl(name));
    history.replaceState(null, "", `/atlas?name=${encodeURIComponent(name)}${location.hash}`);
    renderSettings();
    renderPages();
    setDirty(false);
    if (location.hash) document.querySelector(location.hash)?.scrollIntoView({ block: "center" });
  } catch (e) {
    current = null;
    $("pages").replaceChildren();
    status(e.message, true);
  }
}

async function save(name = current.name, overwrite = true) {
  await json(atlasUrl(name), "PUT", { atlas: current.atlas, overwrite });
  current.name = name;
  setDirty(false);
}

$("atlas-name").addEventListener("change", async (event) => {
  if (dirty && !confirm("Leave this atlas without saving its changes?")) {
    event.target.value = current.name;
    return;
  }
  location.hash = "";
  await load(event.target.value);
});

$("save").addEventListener("click", async () => {
  if (!current) return;
  try {
    await save();
    status(`Saved atlases/${current.name}.toml.`);
  } catch (e) {
    status(e.message, true);
  }
});

function newName() {
  const name = $("new-name").value.trim();
  if (!ATLAS_NAME.test(name)) {
    status("Name the atlas with lower-case letters, digits, '_' and '-', e.g. my_atlas.", true);
    $("new-name").focus();
    return null;
  }
  if (names.some((a) => a.name === name) && !confirm(`Replace the atlas “${name}”?`)) return null;
  return name;
}

$("create").addEventListener("click", async () => {
  const name = newName();
  if (!name) return;
  if (dirty && !confirm("Leave this atlas without saving its changes?")) return;
  try {
    await json(atlasUrl(name), "PUT", {
      atlas: { title: "New atlas", language: "en", paper: "a4", pages: [] },
      overwrite: names.some((a) => a.name === name),
    });
    await loadNames(name);
    await load(name);
    status(`Made atlases/${name}.toml. Add its pages below.`);
  } catch (e) {
    status(e.message, true);
  }
});

$("save-as").addEventListener("click", async () => {
  const name = current && newName();
  if (!name) return;
  try {
    await save(name, names.some((a) => a.name === name));
    await loadNames(name);
    await load(name);
    status(`Saved as atlases/${name}.toml.`);
  } catch (e) {
    status(e.message, true);
  }
});

$("delete").addEventListener("click", async () => {
  if (!current || !confirm(`Delete atlases/${current.name}.toml?`)) return;
  try {
    await getJSON(atlasUrl(current.name), { method: "DELETE" });
    await load(await loadNames(""));
    status("Deleted.");
  } catch (e) {
    status(e.message, true);
  }
});

async function editMap(page) {
  try {
    if (dirty && options.editable) await save();
    location.href = `/?atlas=${encodeURIComponent(current.name)}&page=${encodeURIComponent(page)}`;
  } catch (e) {
    status(`Save the atlas first: ${e.message}`, true);
  }
}

// --- Building the PDF --------------------------------------------------------------------------

function show(job) {
  const [done, total] = job.progress || [0, 0];
  const progress = $("progress");
  progress.hidden = job.status !== "running" || !total;
  progress.max = total || 1;
  progress.value = done;
  const text = {
    queued: job.position ? `Queued behind ${job.position} ${job.position === 1 ? "job" : "jobs"}…` : "Queued…",
    running: total ? `Drawing the maps: ${done} of ${total}… ${Math.round(job.seconds ?? 0)} s` : "Laying out the pages…",
    done: `${job.filename}: ready in ${job.seconds} s.`,
    failed: `The atlas could not be built: ${job.error}`,
  }[job.status];
  status(job.activity && job.status === "running" ? `${text} ${job.activity}` : text, job.status === "failed");
  $("warnings").replaceChildren(...job.warnings.map((w) => {
    const li = document.createElement("li");
    li.textContent = w;
    return li;
  }));
  $("warnings").hidden = job.warnings.length === 0;
  if (job.status === "done" && job.pdf) {
    $("pdf-open").href = job.pdf;
    $("pdf-download").href = `${job.pdf}?download=1`;
    $("pdf-actions").hidden = false;
  }
}

async function follow(id) {
  try {
    const job = await getJSON(`/api/maps/${id}`);
    show(job);
    if (job.status === "queued" || job.status === "running") {
      polling = setTimeout(() => follow(id), 1000);
      return;
    }
  } catch (e) {
    status(`Lost track of the atlas: ${e.message}`, true);
  }
  $("build").disabled = $("proof").disabled = false;
}

async function build(proof) {
  if (!current) return;
  clearTimeout(polling);
  $("pdf-actions").hidden = true;
  $("build").disabled = $("proof").disabled = true;
  try {
    if (dirty && options.editable) await save();
    const job = await json(`${atlasUrl(current.name)}/pdf`, "POST", { proof });
    show(job);
    follow(job.id);
  } catch (e) {
    status(e.message, true);
    $("build").disabled = $("proof").disabled = false;
  }
}

$("build").addEventListener("click", () => build(false));
$("proof").addEventListener("click", () => build(true));

window.addEventListener("beforeunload", (event) => {
  if (dirty) event.preventDefault();
});

// --- Start -------------------------------------------------------------------------------------

async function init() {
  options = await getJSON("/api/options");
  $("language").replaceChildren(...options.atlas.languages.map((l) => option(l, { en: "English", ro: "Română" }[l] ?? l)));
  $("paper").replaceChildren(...options.atlas.papers.map((p) => option(p, p.toUpperCase())));
  $("dpi").min = options.atlas.dpi.min;
  $("dpi").max = options.atlas.dpi.max;
  $("dpi").placeholder = String(options.atlas.dpi.default);
  $("text_scale").min = options.atlas.text_scale.min;
  $("text_scale").max = options.atlas.text_scale.max;
  $("text_scale").placeholder = String(options.atlas.text_scale.default);
  for (const el of document.querySelectorAll(".editable")) el.hidden = !options.editable;
  const wanted = new URLSearchParams(location.search).get("name") || "romania_school";
  await load(await loadNames(wanted));
}

init().catch((e) => status(`Could not reach the server: ${e.message}`, true));
