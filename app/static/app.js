/* Blueprint Evidence front end. Plain JS, no build step. */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const icon = (id, cls = "ico") => `<svg class="${cls}" aria-hidden="true"><use href="#i-${id}"/></svg>`;
const pct = (x) => (x == null ? "-" : (x * 100).toFixed(1) + "%");
const num = (x) => (x == null ? "-" : Number(x).toLocaleString("en-US"));

const SOURCE_NAMES = {
  swiss_dwellings: "Swiss dwellings", cubicasa5k: "CubiCasa scans", ifc_models: "IFC models",
  ifc_bench: "IFC-Bench (free-text model questions, mostly declined)", opening_design: "OpeningDesign", upload: "Upload",
};
const EXAMPLES = [
  { lang: "en", label: "Doors", q: "How many doors are shown on this floor plan?" },
  { lang: "fi", label: "Ikkunat", q: "Kuinka monta ikkunaa pohjapiirustuksessa on?" },
  { lang: "en", label: "WC seats", q: "How many toilet bowls (WC seats) are drawn on this floor plan?" },
  { lang: "fi", label: "Makuuhuoneet", q: "Kuinka monta makuuhuonetta (MH) pohjapiirustuksessa on?" },
  { lang: "en", label: "Apartments", q: "How many apartments (residential units) are shown on this floor plan?" },
  { lang: "en", label: "Room area", q: "What is the net floor area of room 1.07 ('Vano scala') in m²?" },
  { lang: "en", label: "Tiling to order", q: "How many square metres of tiling should I order for the toilets?" },
];
const STATUS = {
  proven: { label: "Proven", icon: "proven" },
  estimate: { label: "Estimate, check highlighted items", icon: "estimate" },
  cannot: { label: "Cannot prove", icon: "cannot" },
};
const KIND = { method: "Method", query: "Reading", derivation: "Derivation", scale: "Scale", check: "Check", abstain: "Reason" };

const state = {
  source: "", query: "", offset: 0, total: 0,
  drawing: null, answer: null,
  layer: "drawing", showHeader: false,
  shown: null,                 // what the viewer shows: the answer, or one witness
  corr: { removed: new Set(), added: [] }, addMode: false,
  witnesses: null, costs: null,
  mode: "plan", has3d: false,  // viewer mode; 3D only for IFC models and plans cut from one
};

/* ================================================================ toast */
let toastT;
function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (err ? " err" : "");
  t.hidden = false;
  clearTimeout(toastT);
  toastT = setTimeout(() => (t.hidden = true), Math.max(err ? 6000 : 2600, Math.min(msg.length * 70, 14000)));
}

const UPLOAD_EXPIRED = "This upload has expired (uploads are deleted after 24 hours). Please upload it again.";
async function api(url, opts) {
  const r = await fetch(url, opts);
  /* expired upload block: uploads are named "upload:<id>/..." (deploy/web.py); a 404 on one means it was deleted */
  if (r.status === 404 && /upload(:|%3A)/i.test(String(url) + (opts && typeof opts.body === "string" ? opts.body : ""))) {
    const err = new Error(UPLOAD_EXPIRED); err.expired = true; throw err;
  }
  if (!r.ok) {
    let msg = r.statusText;
    try { const j = await r.json(); msg = j.detail || msg; } catch (_) { /* not JSON */ }
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return r.json();
}

/* ================================================================ views */
function showView(name) {
  for (const b of document.querySelectorAll(".rail-btn[data-view]")) {
    if (b.dataset.view === name) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
  }
  $("#view-ask").hidden = name !== "ask";
  backDrawLabel();                                      // results-compare: the library stays one press away
  $("#view-results").hidden = name !== "results";
  $("#view-compare").hidden = name !== "compare";      // compare-tab
  if (name === "compare") loadCompare();
  if (name === "results") loadRuns();
  if (name === "ask") requestAnimationFrame(() => viewer.fitIfNeeded());
}
document.querySelectorAll(".rail-btn[data-view]").forEach((b) => b.addEventListener("click", () => showView(b.dataset.view)));

/* results-compare (BM, 14 Nov): Results and Compare keep a way back to the library and the open drawing, with its
   answer: the Ask view is only hidden there, so showing it again restores both. */
function backDrawLabel() {
  const d = state.drawing;
  const t = d ? (d.title && d.title !== d.name ? d.title : d.name) : "";
  document.querySelectorAll("[data-back-draw]").forEach((b) => {
    b.textContent = d ? "Back to drawing" : "Back to the library";
    b.title = d ? `Back to ${t}${state.answer ? " and its answer" : ""}` : "Back to the drawing library";
  });
}
document.querySelectorAll("[data-back-draw]").forEach((b) => b.addEventListener("click", () => showView("ask")));

$("#pickerToggle").addEventListener("click", (e) => {
  const v = $("#view-ask");
  if (v.hidden) {                                       // on Results or Compare: go back with the library open
    v.classList.remove("no-picker");
    e.currentTarget.setAttribute("aria-expanded", "true");
    showView("ask");
    return;
  }
  const open = v.classList.toggle("no-picker") === false;
  e.currentTarget.setAttribute("aria-expanded", String(open));
  requestAnimationFrame(() => viewer.fit());
});

/* ================================================================ resizable side panels */
/* The library (left) and the answer panel (right) are resized by dragging their inner edge, or with the arrow
   keys on the focused handle. The width is a CSS variable on the Ask view, so the grid and the handles follow it.
   Remembered in localStorage; a double-click on a handle goes back to the default. Desktop only (above 1080px). */
const panels = (() => {
  const root = $("#view-ask");
  const WORK_MIN = 480;                     // the viewer keeps at least this much
  const cfg = {
    picker: { grip: $("#gripPicker"), panel: $("#picker"), v: "--picker-w", key: "bpe-picker-w", min: 240, max: 480, side: 1, other: "detail" },
    detail: { grip: $("#gripDetail"), panel: $("#detail"), v: "--detail-w", key: "bpe-detail-w", min: 300, max: 640, side: -1, other: "picker" },
  };
  const wide = window.matchMedia("(min-width: 1081px)");
  const store = {
    get(k) { try { const n = parseFloat(localStorage.getItem(k)); return Number.isFinite(n) ? n : null; } catch { return null; } },
    set(k, n) { try { localStorage.setItem(k, String(Math.round(n))); } catch { /* private mode */ } },
    del(k) { try { localStorage.removeItem(k); } catch { /* private mode */ } },
  };
  const saved = { picker: store.get(cfg.picker.key), detail: store.get(cfg.detail.key) };
  const dflt = (c) => parseFloat(getComputedStyle(document.documentElement).getPropertyValue(c.v)) || c.min;
  const current = (c) => c.panel.offsetParent === null ? 0 : c.panel.getBoundingClientRect().width;
  function limit(name) {                    // the allowed range right now: the panel's own bounds and room for the viewer
    const c = cfg[name], o = cfg[c.other];
    const rail = $(".rail").getBoundingClientRect().width;
    const room = window.innerWidth - rail - current(o) - WORK_MIN;
    return [c.min, Math.max(c.min, Math.min(c.max, room))];
  }
  function show(name, w) {                  // set the width and describe it to assistive tech
    const c = cfg[name], [lo, hi] = limit(name);
    w = Math.round(Math.min(Math.max(w, lo), hi));
    root.style.setProperty(c.v, w + "px");
    c.grip.setAttribute("aria-valuemin", lo);
    c.grip.setAttribute("aria-valuemax", hi);
    c.grip.setAttribute("aria-valuenow", w);
    c.grip.setAttribute("aria-valuetext", w + " pixels");
    return w;
  }
  function applyAll() {
    for (const name of Object.keys(cfg)) {
      const c = cfg[name];
      if (!wide.matches) { root.style.removeProperty(c.v); continue; }
      show(name, saved[name] ?? dflt(c));
    }
  }
  function reset(name) {
    const c = cfg[name];
    saved[name] = null;
    store.del(c.key);
    show(name, dflt(c));
  }
  for (const name of Object.keys(cfg)) {
    const c = cfg[name], g = c.grip;
    g.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      g.setPointerCapture(e.pointerId);
      g.classList.add("drag");
      document.body.classList.add("resizing");      // also stops text selection while dragging
    });
    g.addEventListener("pointermove", (e) => {
      if (!g.classList.contains("drag")) return;
      const r = root.getBoundingClientRect();
      saved[name] = show(name, c.side > 0 ? e.clientX - r.left : r.right - e.clientX);
    });
    const done = () => {
      if (!g.classList.contains("drag")) return;
      g.classList.remove("drag");
      document.body.classList.remove("resizing");
      if (saved[name] != null) store.set(c.key, saved[name]);
    };
    g.addEventListener("pointerup", done);
    g.addEventListener("pointercancel", done);
    g.addEventListener("dblclick", () => reset(name));
    g.addEventListener("keydown", (e) => {
      const [lo, hi] = limit(name);
      const now = current(c);
      const step = e.shiftKey ? 48 : 16;
      let w = null;
      if (e.key === "ArrowRight") w = now + c.side * step;
      else if (e.key === "ArrowLeft") w = now - c.side * step;
      else if (e.key === "Home") w = lo;
      else if (e.key === "End") w = hi;
      else if (e.key === "Enter" || e.key === "Escape") { e.preventDefault(); reset(name); return; }
      if (w == null) return;
      e.preventDefault();
      saved[name] = show(name, w);
      store.set(c.key, saved[name]);
    });
  }
  wide.addEventListener("change", applyAll);
  window.addEventListener("resize", applyAll);
  $("#pickerToggle").addEventListener("click", () => requestAnimationFrame(applyAll));
  applyAll();
  return { applyAll };
})();

/* ================================================================ status */
async function loadStatus() {
  try {
    const s = await api("/api/status");
    const btn = $("#statusBtn");
    const missing = [];
    if (!s.data_root) missing.push("dataset");
    if (!s.detector_model || !s.detector_runtime) missing.push("symbol detector");
    if (!s.ocr_runtime) missing.push("OCR");
    btn.classList.toggle("warn", missing.length > 0);
    btn.dataset.tip = missing.length ? "Status: " + missing.join(", ") + " unavailable" : "Status: all routes available";
    const row = (k, v) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`;
    $("#statusPop").innerHTML = `<h2 class="h-panel">System</h2><dl>
      ${row("Dataset", s.data_root ? "loaded" : "not found")}
      ${row("Drawings", num(s.drawings))}
      ${row("Detector", s.detector_model && s.detector_runtime ? "available (CPU)" : s.detector_model ? "model found, runtime missing: scan symbol counts abstain unless cached" : "no model: scan symbol counts abstain")}
      ${row("OCR", s.ocr_runtime ? "available" : "missing: room labels abstain")}
      ${row("Uploads", "kept for 24 hours, then deleted")}
    </dl><p class="meta">Every route runs locally. No question leaves this machine.</p>`;
  } catch (e) { /* status is optional */ }
}
$("#statusBtn").addEventListener("click", (e) => {
  const p = $("#statusPop");
  p.hidden = !p.hidden;
  e.stopPropagation();
});
document.addEventListener("click", (e) => { if (!$("#statusPop").contains(e.target)) $("#statusPop").hidden = true; });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("#statusPop").hidden = true; });

/* ================================================================ drawing library */
/* The library is grouped by what the system can do with each kind of file, not by dataset. */
/* ---- library groups: collapsible categories (block start) ---- */
const GROUPS = [
  { id: "bim", src: "ifc_models", ext: "", tone: "proven", title: "Building models (IFC)",
    note: "IFC models and their plan sheets; answers read from the model, proven" },
  { id: "cad", notSrc: "ifc_models", ext: "dxf", tone: "proven", title: "CAD drawings (DXF)",
    note: "Vector lines and layers, proven",
    none: "None listed on their own: Swiss PDFs come with a DXF twin, and you can upload a DXF." },
  { id: "pdf", notSrc: "ifc_models", ext: "pdf", tone: "proven", title: "PDF plans",
    note: "Vector PDF with a text layer; scale read from the sheet" },
  { id: "scan", notSrc: "ifc_models", ext: "png", tone: "estimate", title: "Scanned plans",
    note: "Pixels only; counts are estimates to check" },
];
const GROUP_FIRST = 8, GROUP_MORE = 30;
const GROUP_KEY = "bpe-lib-collapsed";
const TOUR_KEY = "bpe-tour";   // set by the tour block at the end of this file
const groupStore = {
  read() { try { const a = JSON.parse(localStorage.getItem(GROUP_KEY)); return new Set(Array.isArray(a) ? a : []); } catch { return new Set(); } },
  write(set) { try { localStorage.setItem(GROUP_KEY, JSON.stringify([...set])); } catch { /* storage blocked: state lasts for this visit */ } },
};
// first visit (the tour has not been answered): every group starts folded, so the tour's first step has something to open
const collapsedGroups = (() => {
  let raw = null, tourAnswered = false;
  try { raw = localStorage.getItem(GROUP_KEY); } catch (_) { /* blocked */ }
  try { tourAnswered = localStorage.getItem(TOUR_KEY) !== null; } catch (_) { /* blocked: counts as unanswered */ }
  if (raw === null && !tourAnswered && window.matchMedia("(min-width: 1081px)").matches) {
    const all = new Set(GROUPS.map((g) => g.id));
    groupStore.write(all);
    return all;
  }
  return groupStore.read();
})();

function groupHead(g, r, open, bodyId) {
  const h = document.createElement("h3");
  h.className = "h-group";
  const b = document.createElement("button");
  b.type = "button"; b.className = "dgroup-toggle";
  b.setAttribute("aria-expanded", String(open));
  b.setAttribute("aria-controls", bodyId);
  const tone = g.tone === "proven" ? "Proven" : "Estimates";
  b.innerHTML = `<svg class="ico chev" aria-hidden="true"><use href="#i-chevron"/></svg>
    <span class="dg-text"><span class="dg-title"><span class="dg-mark ${g.tone}" title="${tone}" aria-hidden="true"></span>${esc(g.title)}</span>
    <span class="dg-note">${esc(r.total ? g.note : g.none)}</span></span>
    <span class="n mono" aria-label="${num(r.total)} drawings">${num(r.total)}</span>`;
  if (!r.total) { b.disabled = true; b.removeAttribute("aria-expanded"); b.removeAttribute("aria-controls"); }  // nothing to fold
  h.appendChild(b);
  return h;
}
/* ---- library groups (block end; loadDrawings below uses groupHead) ---- */

let searchT, loadSeq = 0;
function groupQuery(g, offset, limit) {
  const p = { source: g.src || "", not_source: g.notSrc || "", q: state.query, ext: g.ext, offset, limit };   // the source chips are gone: a group is a type
  return api("/api/drawings?" + new URLSearchParams(p));
}

async function loadDrawings() {
  const list = $("#drawList");
  const seq = ++loadSeq;
  list.innerHTML = '<ul><li class="skel"></li><li class="skel"></li><li class="skel"></li></ul>';
  try {
    const res = await Promise.all(GROUPS.map((g) => groupQuery(g, 0, GROUP_FIRST)));
    if (seq !== loadSeq) return;
    const total = res.reduce((a, r) => a + r.total, 0);
    $("#drawCount").textContent = num(total);
    list.innerHTML = "";
    if (!total) {
      list.innerHTML = `<p class="list-note">${res[0].sources.length ? "No drawing matches. Try part of a file name." : "No dataset found. Set STOA_DATA, or upload a drawing above."}</p>`;
      return;
    }
    GROUPS.forEach((g, i) => {
      const r = res[i];
      const filtered = state.query;
      if (!r.total && (filtered || !g.none)) return;
      const sec = document.createElement("section");
      sec.className = "dgroup";
      const bodyId = "dg-" + g.id;
      // searching opens every group that has matches; the stored state is left alone
      const open = state.query ? r.total > 0 : !collapsedGroups.has(g.id);
      const head = groupHead(g, r, open, bodyId);
      const body = document.createElement("div");
      body.className = "dgroup-body"; body.id = bodyId; body.hidden = !open;
      sec.appendChild(head);
      sec.appendChild(body);
      head.firstChild.addEventListener("click", (e) => {
        const now = body.hidden;                       // hidden now, so this click opens it
        body.hidden = !now;
        e.currentTarget.setAttribute("aria-expanded", String(now));
        if (now) collapsedGroups.delete(g.id); else collapsedGroups.add(g.id);
        groupStore.write(collapsedGroups);
      });
      if (r.total) {
        const ul = document.createElement("ul");
        r.items.forEach((d) => ul.appendChild(drawItem(d)));
        body.appendChild(ul);
        let off = r.items.length;
        if (off < r.total) {
          const more = document.createElement("button");
          more.type = "button"; more.className = "btn btn-quiet btn-block";
          const label = () => `Show more (${num(r.total - off)} left)`;
          more.textContent = label();
          more.addEventListener("click", async () => {
            const next = await groupQuery(g, off, GROUP_MORE);
            next.items.forEach((d) => ul.appendChild(drawItem(d)));
            off += next.items.length;
            if (off >= r.total) more.remove(); else more.textContent = label();
          });
          body.appendChild(more);
        }
      }
      list.appendChild(sec);
    });
  } catch (e) {
    list.innerHTML = `<p class="list-note">Could not load the library: ${esc(e.message)}</p>`;
  }
}

/* dataset split tag: plain words and a tooltip, so "test" does not read as a test drawing */
const SPLIT_TIP = {
  test: "Dataset split: test questions were never used for tuning. They were run once, at the end.",
  dev: "Dataset split: dev questions were used while building and tuning the app.",
};
function splitTag(split) {
  return split ? `<span class="split-tag" title="${esc(SPLIT_TIP[split] || "Dataset split")}">${esc(split)} split</span>` : "";
}
/* readable name first, the file name under it (and in the tooltip); uploads have no title and keep their own name */
function nameHtml(d) {
  const title = d.title && d.title !== d.name ? d.title : "";
  const tip = esc(d.path === d.name ? d.name : d.name + "\n" + d.path);
  return title
    ? `<span class="nm" title="${tip}">${esc(title)}</span><span class="fn mono" title="${tip}">${esc(d.name)}</span>`
    : `<span class="nm nm-file mono" title="${tip}">${esc(d.name)}</span>`;
}

function drawItem(d) {
  const li = document.createElement("li");
  const b = document.createElement("button");
  b.className = "draw-item";
  b.setAttribute("aria-selected", String(state.drawing?.path === d.path));
  b.dataset.path = d.path; b.dataset.name = d.name; b.dataset.ext = d.ext;
  if (d.title && d.title !== d.name) b.dataset.title = d.title;      // ask-several: the readable name in chips and the table
  b.innerHTML = `<span class="box" aria-hidden="true"></span>${nameHtml(d)}
    <span class="sub"><span>${esc(SOURCE_NAMES[d.source] || d.source)}</span><span>${esc(d.ext.toUpperCase())}</span><span>${d.n_questions} questions</span>${splitTag(d.split)}</span>`;
  b.addEventListener("click", (e) => pickOrOpen(b, e));       // ask-several block: select, or open
  b.addEventListener("dblclick", () => { if (batch.on) selectDrawing(d.path); });
  paintPick(b);
  li.appendChild(b);
  return li;
}

$("#search").addEventListener("input", (e) => {
  clearTimeout(searchT);
  searchT = setTimeout(() => { state.query = e.target.value.trim(); loadDrawings(); }, 180);
});

/* ---------------------------------------------------------------- upload */
const drop = $("#drop");
["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, () => drop.classList.remove("over")));
drop.addEventListener("drop", (e) => { e.preventDefault(); if (e.dataTransfer.files.length) upload(e.dataTransfer.files); });
$("#fileInput").addEventListener("change", (e) => { if (e.target.files.length) upload(e.target.files); e.target.value = ""; });

/* Files that share a stem (plan.pdf + plan.dxf) are twins of one drawing and go in one request; every other
   file is its own drawing. One request per drawing keeps each under the 200 MB request limit. */
const UPLOAD_TYPES = "PDF, DXF, IFC, PNG, JPEG, TIFF or SVG";
const UPLOADS_KEY = "bpe-uploads", UPLOAD_KEEP_MS = 24 * 60 * 60 * 1000 - 60 * 1000;   // the server deletes an upload after 24 hours
const stemOf = (n) => { const i = n.lastIndexOf("."); return (i > 0 ? n.slice(0, i) : n).toLowerCase(); };
const uploadStore = {
  read() {
    try {
      const a = JSON.parse(localStorage.getItem(UPLOADS_KEY));
      return Array.isArray(a) ? a.filter((u) => Date.now() - u.t < UPLOAD_KEEP_MS) : [];
    } catch (_) { return []; }
  },
  write(a) { try { localStorage.setItem(UPLOADS_KEY, JSON.stringify(a)); } catch (_) { /* storage blocked */ } },
};
function renderUploads() {
  const box = $("#uploadList");
  const items = uploadStore.read();
  box.hidden = !items.length;
  if (!items.length) { box.innerHTML = ""; return; }
  /* compact (BM, 14 Nov): a fold with a capped, scrolling list, so at 1280x800 the search results below stay visible */
  const open = box.querySelector("details")?.open ?? true;
  box.innerHTML = `<details class="uploads-fold"${open ? " open" : ""}><summary class="h-group"><span class="up-t">Your uploads (${items.length})</span><span class="meta">kept 24 h</span></summary><ul></ul></details>`;
  const ul = box.querySelector("ul");
  for (const d of items) {
    const li = document.createElement("li");
    const b = document.createElement("button");
    b.className = "draw-item";
    b.dataset.path = d.path; b.dataset.name = d.name || d.path.split("/").pop(); b.dataset.ext = d.ext || "";
    b.setAttribute("aria-selected", String(state.drawing?.path === d.path));
    b.innerHTML = `<span class="box" aria-hidden="true"></span><span class="nm nm-file mono" title="${esc(b.dataset.name)}">${esc(b.dataset.name)}</span>
      <span class="sub"><span>${esc(b.dataset.ext.toUpperCase())}</span><span>Upload</span></span>`;
    b.addEventListener("click", (e) => pickOrOpen(b, e));       // ask-several block: select, or open
    b.addEventListener("dblclick", () => { if (batch.on) selectDrawing(d.path); });
    paintPick(b);
    li.appendChild(b);
    ul.appendChild(li);
  }
}
queueMicrotask(renderUploads);      // after the whole script has run: it needs uploadStore and the ask-several state (a call above either stopped the script, 14 Nov)

async function upload(fileList) {
  const files = [...fileList];
  const groups = new Map();
  for (const f of files) {
    const k = stemOf(f.name);
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(f);
  }
  toast(`Uploading ${files.length} file${files.length > 1 ? "s" : ""}`);
  const added = [], errors = [], notes = [];
  for (const group of groups.values()) {
    const fd = new FormData();
    for (const f of group) fd.append("files", f);
    try {
      const res = await api("/api/upload", { method: "POST", body: fd });
      added.push(...res.drawings);
      errors.push(...(res.errors || []));
      notes.push(...(res.notes || []));
    } catch (e) {
      errors.push(e.message);
    }
  }
  if (added.length) {
    const t = Date.now();
    uploadStore.write([...added.map((d) => ({ path: d.path, name: d.name, ext: d.ext, t })), ...uploadStore.read()]);
    renderUploads();
    batchAddUploaded({ drawings: added, path: added[0].path, name: added[0].name });   // ask-several: uploads join the selection
    await selectDrawing(added[0].path);
    if (added[0].precompute) watchReading(added[0].path, added[0].precompute);   // background reading progress
    renderUploads();
    const msg = added.length === 1 ? `Uploaded ${added[0].name}` : `Uploaded ${added.length} drawings. The first one is open; the others are under Your uploads.`;
    toast([msg, ...notes, ...errors].join(" "), errors.length > 0);
  } else {
    toast("Upload failed: " + errors.join(" "), true);
  }
}

/* the background reading started at upload (app/precompute.py): a status line under the drawing title */
let readT = null;
function showReading(st) {
  const el = $("#readStatus");        // always in the page, so the line coming and going never shifts the layout
  el.textContent = st ? st.message : "";
  el.title = st ? st.message : "";
}
function watchReading(path, first) {
  clearTimeout(readT);
  let misses = 0;
  const tick = (st) => {
    if (state.drawing?.path !== path) { showReading(null); return; }   // another drawing was opened
    showReading(st);
    if (st.state === "queued" || st.state === "running") {
      readT = setTimeout(async () => {
        // a failed poll keeps the last line and tries again; only repeated failures drop it
        let next = null;
        try { next = await api("/api/precompute?path=" + encodeURIComponent(path)); misses = 0; } catch { misses++; }
        if (next) tick(next); else if (misses > 5) showReading(null); else tick(st);
      }, 2000);
    } else {
      readT = setTimeout(() => showReading(null), st.state === "done" ? 6000 : 12000);
    }
  };
  tick(first);
}

/* ---------------------------------------------------------------- select a drawing */
const floatingPicker = window.matchMedia("(min-width: 761px) and (max-width: 1080px)");
async function selectDrawing(path, keepAnswer = false) {
  try {
    const d = await api("/api/drawing?path=" + encodeURIComponent(path));
    if (state.drawing?.path !== d.path) viewer.resetOrient(true);     // a new drawing opens in its own orientation
    state.drawing = d;
    document.querySelectorAll(".draw-item").forEach((b) =>
      b.setAttribute("aria-selected", String(b.dataset.path === d.path)));
    const titled = d.title && d.title !== d.name;
    $("#drawTitle").textContent = titled ? d.title : d.name;
    $("#drawTitle").classList.toggle("mono", !titled);          // a file name reads as one, in the mono face
    $("#drawTitle").title = titled ? d.name : "";
    $("#drawFile").textContent = titled ? d.name : "";
    $("#drawFile").hidden = !titled;
    const bits = [SOURCE_NAMES[d.source] || d.source, d.ext.toUpperCase()].map(esc);
    if (d.twins?.length) bits.push(esc("twins: " + d.twins.join(", ")));
    if (d.note) bits.push(esc(d.note));
    if (d.split) bits.push(splitTag(d.split));
    $("#drawMeta").innerHTML = bits.join("  ·  ");
    $("#pdfOnly").closest(".check").hidden = !(d.ext === "pdf" && d.twins?.length);
    /* between 761 and 1080 px the library floats over the question bar: once a drawing is open it steps aside (the
       rail's Drawings button brings it back). Not while picking several, where the library is the point */
    if (floatingPicker.matches && !batch.on && !$("#view-ask").classList.contains("no-picker")) {
      $("#view-ask").classList.add("no-picker");
      $("#pickerToggle").setAttribute("aria-expanded", "false");
    }
    setup3d(d);
    renderDrawingQuestions(d);
    renderExamples();
    if (!keepAnswer) {
      leaveBatchView();                              // ask-several block
      if (state.drawing && state.askTicket) {      // a question still running on the previous drawing is abandoned
        state.askTicket += 1;
        abortAsk();                                  // and its request dropped, so the server does not solve it for nobody
        state.askingQ = null;
        clearInterval(busyT);
        $("#viewerBusy").hidden = true;
        $("#askBtn").disabled = false;
        $("#askBtn").removeAttribute("aria-busy");
      }
      state.answer = null;
      renderAnswer(null);
      showDrawing();
    }
    setHash();          // after the answer reset, so the previous drawing's question is not written into this entry
  } catch (e) {
    toast("Could not open the drawing: " + e.message, true);
  }
}

function renderExamples() {
  const el = $("#exampleChips");
  el.innerHTML = EXAMPLES.map((x, i) =>
    `<button class="chip" type="button" data-i="${i}" title="${esc(x.q)}">${esc(x.label)}${x.lang !== "en" ? `<span class="lang" title="Finnish">${x.lang}</span>` : ""}</button>`).join("");
  el.querySelectorAll(".chip").forEach((c) => c.addEventListener("click", () => {
    $("#question").value = EXAMPLES[+c.dataset.i].q;
    $("#question").focus();
  }));
}

/* "Try these": one-click examples in the empty answer panel. A row is hidden when its drawing is not in this library. */
const TRY = [
  { label: "Tiling to order on a Swiss plan", note: "Floor and walls of the bathrooms, summed from the drawing's geometry.",
    path: "data/swiss_dwellings/site3387_bldg5238_floor23498.pdf", q: "How many square metres of tiling should I order for the bathrooms?" },
  { label: "Room area from an IFC model", note: "Reads the model, then opens the 3D view with the room marked.",
    path: "data/ifc_models/kit-fzk-haus_ac20-fzk-haus_plan-01-erdgeschoss.pdf", q: "What is the net floor area of the room 'K\u00fcche (6)' on 'Erdgeschoss', in m\u00b2?", mode: "3d" },
  { label: "Count windows on a scan", note: "Pixels, not vectors: comes back as an estimate with items to check.",
    path: "data/cubicasa5k/high_quality_architectural-10372.png", q: "How many windows are shown on this floor plan?" },
  { label: "Bring your own drawing", note: "PDF, DXF, IFC, an image or an SVG. Drop several files at once; a PDF and a DXF with the same name are used together.", upload: true },
];
async function renderTry() {
  const ul = $("#tryList");
  const ok = await Promise.all(TRY.map((t) => t.upload ? true : api("/api/drawing?path=" + encodeURIComponent(t.path)).then(() => true, () => false)));
  ul.innerHTML = TRY.map((t, i) => ok[i]
    ? `<li><button type="button" class="try" data-i="${i}">${esc(t.label)}</button><p class="meta">${esc(t.note)}</p></li>` : "").join("");
  ul.querySelectorAll(".try").forEach((b) => b.addEventListener("click", async () => {
    const t = TRY[+b.dataset.i];
    if (t.upload) { $("#fileInput").click(); return; }
    await selectDrawing(t.path);
    if (t.mode) setMode(t.mode);
    $("#question").value = t.q;
    ask();
  }));
}

function renderDrawingQuestions(d) {
  const box = $("#drawingQs");
  const qs = d.questions || [];
  box.hidden = qs.length === 0;
  $("#dqCount").textContent = `${qs.length}${d.n_questions > qs.length ? " of " + d.n_questions : ""} questions from the dataset for this drawing`;
  const list = $("#dqList");
  list.innerHTML = qs.map((q, i) => {
    const a = typeof q.answer === "object" ? "" : String(q.answer ?? "");
    return `<li><button type="button" class="dq-item" data-i="${i}"><span>${esc(q.question)}</span><span class="ans" title="Dataset answer">${esc(a.split("\n")[0])}${q.unit && q.unit !== "count" ? " " + esc(q.unit) : ""}</span></button></li>`;
  }).join("");
  list.querySelectorAll(".dq-item").forEach((b) => b.addEventListener("click", () => {
    $("#question").value = qs[+b.dataset.i].question;
    box.open = false;
    ask();
  }));
}

function setHash() {
  const p = new URLSearchParams();
  if (state.drawing) p.set("path", state.drawing.path);
  const q = $("#question").value.trim();
  if (q && (state.answer || state.askingQ)) p.set("q", state.askingQ || q);
  if (state.mode === "3d") p.set("mode", "3d");
  const next = "#" + p.toString();
  if (location.hash === next) return;
  /* a new drawing or a new question is a history step, so Back returns to the previous one (Mac QA 14 Nov);
     other changes (3D toggle, the question appearing while it is asked) replace the current entry */
  const cur = new URLSearchParams(location.hash.slice(1));
  const step = cur.get("path") && (cur.get("path") !== p.get("path") || (p.get("q") && cur.get("q") !== p.get("q")));
  if (step) history.pushState(null, "", next);
  else history.replaceState(null, "", next);
}

/* ================================================================ viewer */
const viewer = (() => {
  const vp = $("#viewer"), stage = $("#stage"), orient = $("#orient"), img = $("#viewImg");
  let k = 1, x = 0, y = 0, fitted = false, atFit = false, drag = null;
  let rot = 0, flip = false;               // view transform only: quarter turns clockwise, then a left-right mirror
  const orientListeners = [];

  /* image pixels -> view pixels (before zoom and pan): view = Flip(Rot(image)), shifted so the box starts at 0,0 */
  function mat() {
    const W = img.naturalWidth || 0, H = img.naturalHeight || 0;
    const co = [1, 0, -1, 0][rot], si = [0, 1, 0, -1][rot];
    let a = co, b = si, c = -si, d = co;
    if (flip) { a = -a; c = -c; }
    const X = [0, W].flatMap((px) => [0, H].map((py) => a * px + c * py));
    const Y = [0, W].flatMap((px) => [0, H].map((py) => b * px + d * py));
    const e = -Math.min(...X), f = -Math.min(...Y);
    return { a, b, c, d, e, f, w: Math.max(...X) + e, h: Math.max(...Y) + f, rot, flip };
  }
  const toView = (m, px, py) => [m.a * px + m.c * py + m.e, m.b * px + m.d * py + m.f];
  const toImage = (m, vx, vy) => { const X = vx - m.e, Y = vy - m.f; return [m.a * X + m.b * Y, m.c * X + m.d * Y]; };   // the linear part is orthogonal
  function rectToView(r) {
    const m = mat();
    const p = [toView(m, r[0], r[1]), toView(m, r[0] + r[2], r[1] + r[3])];
    return [Math.min(p[0][0], p[1][0]), Math.min(p[0][1], p[1][1]), Math.abs(p[1][0] - p[0][0]), Math.abs(p[1][1] - p[0][1])];
  }
  const pointToView = (px, py) => toView(mat(), px, py);

  function syncButtons() {
    const has = !!img.naturalWidth && !!img.getAttribute("src");
    $("#rotL").disabled = $("#rotR").disabled = $("#mirror").disabled = !has;
    $("#orientReset").disabled = !has || (rot === 0 && !flip);
    $("#mirror").setAttribute("aria-pressed", String(flip));
  }
  function apply() {
    const m = mat();
    orient.style.transform = `matrix(${m.a}, ${m.b}, ${m.c}, ${m.d}, ${m.e}, ${m.f})`;
    stage.style.width = m.w + "px";
    stage.style.height = m.h + "px";
    stage.style.transform = `translate(${x}px, ${y}px) scale(${k})`;
    stage.classList.toggle("pixel", k > 2.5);
    stage.style.setProperty("--inv", String(1 / k));
    /* number tags: constant size from 60% up, smaller when zoomed further out, so they do not cover a fitted plan */
    stage.style.setProperty("--tag", String(Math.max(0.45, Math.min(1, Math.sqrt(k / 0.6))) / k));
    $("#zoomLvl").textContent = Math.round(k * 100) + "%";
    syncButtons();
  }
  function fit() {
    if (!img.naturalWidth) return;
    const m = mat();
    const r = vp.getBoundingClientRect();
    const pad = 24;
    k = Math.min((r.width - pad * 2) / m.w, (r.height - pad * 2 - 40) / m.h);
    k = Math.max(Math.min(k, 4), 0.02);
    x = (r.width - m.w * k) / 2;
    y = Math.max(pad + 40, (r.height - m.h * k) / 2);
    fitted = true;
    atFit = true;
    apply();
  }
  function zoomAt(f, cx, cy) {
    const nk = Math.min(Math.max(k * f, 0.02), 12);
    x = cx - (cx - x) * (nk / k);
    y = cy - (cy - y) * (nk / k);
    k = nk;
    atFit = false;
    apply();
  }
  function center() { const r = vp.getBoundingClientRect(); return [r.width / 2, r.height / 2]; }
  /* a short eased move for jumps the person did not drag (selecting an item); off under reduced motion */
  let glideT = null;
  const still = window.matchMedia("(prefers-reduced-motion: reduce)");
  function glide() {
    if (still.matches) return;
    clearTimeout(glideT);
    stage.style.transition = "transform 280ms cubic-bezier(0.2, 0.7, 0.2, 1)";
    glideT = setTimeout(() => { stage.style.transition = ""; }, 320);
  }
  function focusRect(rx, ry, rw, rh, smooth = false) {
    const r = vp.getBoundingClientRect();
    [rx, ry, rw, rh] = rectToView([rx, ry, rw, rh]);
    const want = Math.min(r.width / (rw * 6), r.height / (rh * 6), 4);
    if (want > k) k = want;
    x = r.width / 2 - (rx + rw / 2) * k;
    y = r.height / 2 - (ry + rh / 2) * k;
    atFit = false;
    if (smooth) glide();
    apply();
  }

  /* zoom a rect (image pixels) to fill `fill` of a part of the viewport {l, t, w, h} and centre it there */
  function focusIn(r, box, fill = 0.7, maxK = 4, smooth = false) {
    const [rx, ry, rw, rh] = rectToView(r);
    k = Math.max(0.02, Math.min((box.w * fill) / Math.max(rw, 1), (box.h * fill) / Math.max(rh, 1), maxK));
    x = box.l + box.w / 2 - (rx + rw / 2) * k;
    y = box.t + box.h / 2 - (ry + rh / 2) * k;
    atFit = false;
    if (smooth) glide();
    apply();
  }

  /* rotate and mirror about the middle of the viewport: the point under the middle stays there, zoom is kept */
  function setOrient(nr, nf, silent = false) {
    nr = ((nr % 4) + 4) % 4;
    if (!img.naturalWidth || silent) { rot = nr; flip = nf; syncButtons(); return; }
    const [cx, cy] = center();
    const [ix, iy] = toImage(mat(), (cx - x) / k, (cy - y) / k);
    const wasFit = atFit;
    rot = nr; flip = nf;
    if (wasFit) fit();
    else {
      const [vx, vy] = toView(mat(), ix, iy);
      x = cx - vx * k; y = cy - vy * k;
      apply();
    }
    orientListeners.forEach((fn) => fn());
  }
  /* "right" is what the person sees: after a mirror a quarter turn right is one step back in the stored angle */
  const turn = (dir) => setOrient(rot + (flip ? -dir : dir), flip);
  const mirror = () => setOrient(rot, !flip);
  const resetOrient = (silent = false) => setOrient(0, false, silent);

  /* Wheel input, as map viewers do it (14 Nov: zoom felt awkward on a laptop):
     pinch on a touchpad (browsers send it as ctrl+wheel) and ctrl+wheel zoom, at a pinch-friendly rate;
     a mouse wheel notch zooms about 1.3x about the cursor (1.2x too slow, 1.5x too much, his tests); a two-finger touchpad scroll pans instead of zooming. */
  vp.addEventListener("wheel", (e) => {
    if (!img.naturalWidth) return;
    e.preventDefault();
    const r = vp.getBoundingClientRect();
    const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? r.height : 1;
    const dx = e.deltaX * unit, dy = e.deltaY * unit;
    const cx = e.clientX - r.left, cy = e.clientY - r.top;
    if (e.ctrlKey || e.metaKey) {
      zoomAt(Math.exp(-Math.max(-25, Math.min(25, dy)) * 0.02), cx, cy);   // capped per event so a fast pinch cannot jump
    } else if (e.deltaMode !== 0 || (dx === 0 && Math.abs(dy) >= 50)) {
      zoomAt(Math.exp(-Math.sign(dy) * Math.min(Math.abs(dy), 150) * 0.0027), cx, cy);   // one notch: about 1.3x
    } else {
      x -= dx; y -= dy; atFit = false; apply();
    }
  }, { passive: false });
  vp.addEventListener("pointerdown", (e) => {
    if (e.target.closest(".viewer-tools") || !img.naturalWidth) return;
    drag = { px: e.clientX, py: e.clientY, x, y, moved: false };
    vp.setPointerCapture(e.pointerId);
    vp.classList.add("dragging");
  });
  vp.addEventListener("pointermove", (e) => {
    if (!drag) return;
    if (Math.abs(e.clientX - drag.px) + Math.abs(e.clientY - drag.py) > 4) { drag.moved = true; atFit = false; }
    x = drag.x + e.clientX - drag.px;
    y = drag.y + e.clientY - drag.py;
    apply();
  });
  const end = () => { drag = null; vp.classList.remove("dragging"); };
  vp.addEventListener("pointerup", (e) => {
    if (drag && !drag.moved && state.addMode) {
      const r = vp.getBoundingClientRect();
      const [ix, iy] = toImage(mat(), (e.clientX - r.left - x) / k, (e.clientY - r.top - y) / k);
      addItemAt(ix, iy);
    }
    end();
  });
  vp.addEventListener("pointercancel", end);
  vp.addEventListener("dblclick", (e) => {
    if (e.target.closest(".viewer-tools")) return;
    const r = vp.getBoundingClientRect();
    zoomAt(2, e.clientX - r.left, e.clientY - r.top);
  });
  vp.addEventListener("keydown", (e) => {
    const step = 60;
    const map = {
      "+": () => zoomAt(1.4, ...center()), "=": () => zoomAt(1.4, ...center()), "-": () => zoomAt(1 / 1.4, ...center()),
      "0": fit, ArrowLeft: () => { x += step; atFit = false; apply(); }, ArrowRight: () => { x -= step; atFit = false; apply(); },
      ArrowUp: () => { y += step; atFit = false; apply(); }, ArrowDown: () => { y -= step; atFit = false; apply(); },
    };
    if (map[e.key]) { e.preventDefault(); map[e.key](); }
  });
  /* orientation shortcuts work anywhere on the Ask view except while typing: R, Shift+R, M, O */
  /* ... nor for 2.5 s after the question field was typed in or left, or the bar above the viewer changed size: a
     layout shift can put the click on the viewer while the person is already typing */
  const qf = $("#question");
  let qLeftAt = 0;
  qf.addEventListener("blur", () => { qLeftAt = Date.now(); });
  qf.addEventListener("input", () => { qLeftAt = Date.now(); });
  document.querySelector("#askForm")?.addEventListener("pointerdown", () => { qLeftAt = Date.now(); });
  new ResizeObserver(() => { qLeftAt = Date.now(); }).observe(document.querySelector(".workbar"));   // the field moved
  document.addEventListener("keydown", (e) => {
    if (e.ctrlKey || e.metaKey || e.altKey || e.defaultPrevented) return;
    if (e.target?.closest?.("input, textarea, select, [contenteditable]")) return;
    if (document.activeElement === qf || Date.now() - qLeftAt < 2500) return;
    if ($("#view-ask").hidden || vp.classList.contains("mode-3d") || !img.naturalWidth) return;
    const key = e.key.toLowerCase();
    if (key === "r") turn(e.shiftKey ? -1 : 1);
    else if (key === "m" && !e.shiftKey) mirror();
    else if (key === "o" && !e.shiftKey) resetOrient();
    else return;
    e.preventDefault();
  });
  $("#zoomIn").addEventListener("click", () => zoomAt(1.4, ...center()));
  $("#zoomOut").addEventListener("click", () => zoomAt(1 / 1.4, ...center()));
  $("#zoomFit").addEventListener("click", fit);
  $("#rotL").addEventListener("click", () => turn(-1));
  $("#rotR").addEventListener("click", () => turn(1));
  $("#mirror").addEventListener("click", mirror);
  $("#orientReset").addEventListener("click", () => resetOrient());
  /* the viewer takes the remaining space: any change of its size (window, panel handles) re-fits a view the person has not moved */
  let lastW = 0, lastH = 0;
  new ResizeObserver(() => {
    const r = vp.getBoundingClientRect();
    if (Math.abs(r.width - lastW) < 1 && Math.abs(r.height - lastH) < 1) return;
    lastW = r.width; lastH = r.height;
    if (fitted && atFit) fit();
  }).observe(vp);

  function load(src, refit = true) {
    return new Promise((resolve) => {
      $("#viewerEmpty").hidden = true;
      img.onload = () => { if (refit || !fitted) fit(); else apply(); resolve(true); };
      img.onerror = () => resolve(false);
      img.src = src;
    });
  }
  function clear(msgTitle, msg) {
    img.removeAttribute("src");
    fitted = false;
    syncButtons();
    $("#viewerEmpty").hidden = false;
    $("#viewerEmpty").innerHTML = `<p class="empty-title">${esc(msgTitle)}</p><p class="meta">${esc(msg)}</p>`;
  }
  return {
    load, clear, fit, focusRect, focusIn, rectToView, pointToView, resetOrient, matrix: mat,
    onOrient: (fn) => orientListeners.push(fn),
    fitIfNeeded: () => { if (img.naturalWidth && !fitted) fit(); }, img,
  };
})();

/* after a rotation or mirror: pins, reticle and the strip crops are redrawn in the new orientation */
viewer.onOrient(() => {
  if (state.layer !== "evidence" || !state.shown) return;
  drawPins(state.shown);
  renderStrip();
  if (lastFocus) focusRect(lastFocus.r, lastFocus.i, false);
});

function setLayer(layer) {
  state.layer = layer;
  const tag = $("#layerTag");
  tag.textContent = layer === "evidence" ? "Evidence" : "Drawing";
  tag.classList.toggle("ev", layer === "evidence");
  $("#headerToggle").disabled = layer !== "evidence";
}

async function showDrawing() {
  clearMarks();
  setLayer("drawing");
  state.shown = null;
  setAddMode(false);
  $("#strip").hidden = true;
  $("#openImg").hidden = true;
  const d = state.drawing;
  if (!d) return;
  if (["pdf", "png", "jpg", "jpeg", "dxf"].includes(d.ext)) {
    const src = "/api/preview?path=" + encodeURIComponent(d.path);
    const ok = await viewer.load(src);
    if (!ok) viewer.clear("No preview", "This file could not be rendered. Ask a question to draw its evidence.");
    else { $("#openImg").href = src; $("#openImg").hidden = false; }
    if (state.mode === "3d" && state.has3d) setMode("3d");
  } else if (state.has3d) {
    viewer.clear(`${d.ext.toUpperCase()} model selected`, "Opening the 3D view. Ask a question to draw its evidence on a plan.");
    setMode("3d");
  } else {
    viewer.clear(`${d.ext.toUpperCase()} file selected`, "There is no flat preview for this format. Ask a question: the evidence is drawn from the model or the DXF geometry.");
  }
}

function evidenceSrc(a) {
  return `/api/evidence/${encodeURIComponent(a.evidence_id)}.png${state.showHeader ? "" : "?body=1"}`;
}
async function showEvidence(a, refit = true) {
  clearMarks();
  setLayer("evidence");
  state.shown = a;
  if (a !== state.answer) setAddMode(false);
  $("#layerTag").textContent = a === state.answer ? "Evidence" : `${a.format} witness`;
  const src = evidenceSrc(a);
  await viewer.load(src, refit);
  $("#openImg").href = src;
  $("#openImg").hidden = false;
  drawPins(a);
  renderStrip();
  document.querySelectorAll(".wit-row").forEach((r) => r.setAttribute("aria-pressed", String(r.dataset.f === a.format && a !== state.answer)));
}
$("#headerToggle").addEventListener("click", (e) => {
  state.showHeader = !state.showHeader;
  e.currentTarget.setAttribute("aria-pressed", String(state.showHeader));
  e.currentTarget.setAttribute("aria-label", state.showHeader ? "Hide the evidence header strip" : "Show the evidence header strip");
  if (state.shown?.evidence_id) showEvidence(state.shown, true);
});

/* ---------------------------------------------------------------- pins and reticle on cited items */
/* Positions come from the evidence marks sidecar (every numbered box and room polygon the route drew, in
   evidence pixels), so DXF world coordinates, IFC geometry, PDF points and scan pixels all pin the same way.
   Raster answers without a sidecar fall back to their source-pixel boxes. */
function headerOff(a) { return state.showHeader ? (a.marks?.header ?? a.evidence_geometry?.header ?? 0) : 0; }
function markFor(it, a, i) {
  const ms = a.marks?.marks;
  if (!ms || !ms.length) return null;
  const n = (it && typeof it === "object" && it.n) || i + 1;
  const polys = ms.filter((m) => m.kind === "poly");
  const isMeasure = a.unit && a.unit !== "count";
  if ((isMeasure || typeof it !== "object") && polys[i]) return polys[i];
  return ms.find((m) => m.kind === "box" && String(m.n) === String(n)) || polys[i] || null;
}
function itemRect(it, a, i = 0) {
  const off = headerOff(a);
  const m = markFor(it, a, i);
  if (m) {
    const [x0, y0, x1, y1] = m.rect;
    return [x0, y0 + off, Math.max(x1 - x0, 4), Math.max(y1 - y0, 4)];
  }
  const g = a.evidence_geometry;
  if (!g || g.scale == null || typeof it !== "object" || it === null) return null;
  let r = null;
  if (Array.isArray(it.xyxy) && it.xyxy.length === 4) r = [it.xyxy[0], it.xyxy[1], it.xyxy[2] - it.xyxy[0], it.xyxy[3] - it.xyxy[1]];
  else if (Array.isArray(it.xywh) && it.xywh.length === 4) r = it.xywh;
  if (!r) return null;
  return [r[0] * g.scale, r[1] * g.scale + off, Math.max(r[2] * g.scale, 4), Math.max(r[3] * g.scale, 4)];
}
function answerItems(a) {
  return (a.items || []).filter((it) => !(it && typeof it === "object" && "would_answer" in it));
}
/* the items a person checks: one per counted item or measured room, numbered like the overlay */
function checkItems(a) {
  const its = answerItems(a);
  let rows = its.map((it, i) => {
    const m = markFor(it, a, i);
    let [label, extra] = itemLabel(it, a.query && a.query.obj, a.route);
    if (a.unit && a.unit !== "count" && it && typeof it === "object") {
      label = [it.number, it.name || it.label || it.room].filter(Boolean).join(" ") || `room ${i + 1}`;
    }
    const area = typeof it === "object" && it ? (it.area_m2 ?? it.area ?? (a.unit === "m2" && typeof it.value === "number" ? it.value : null)) : null;
    return { i, n: (it && typeof it === "object" && it.n) || i + 1, it, rect: itemRect(it, a, i), mark: m, label, extra, area,
             tone: m ? m.tone : a.status === "estimate" ? "amber" : "blue" };
  });
  const itemMarks = (a.marks?.marks || []).filter((m) => m.kind === "box" || m.kind === "poly");   // not context ("scale")
  if (!rows.some((r) => r.rect) && itemMarks.length) {        // items without positions: use the drawn marks
    const off = headerOff(a);
    rows = itemMarks.map((m, i) => ({ i, n: m.n ?? i + 1, it: null, mark: m, label: m.kind === "poly" ? `region ${i + 1}` : `item ${m.n}`,
      extra: "", area: null, tone: m.tone, rect: [m.rect[0], m.rect[1] + off, m.rect[2] - m.rect[0], m.rect[3] - m.rect[1]] }));
  }
  return rows;
}
function pinTone(t) { return t === "amber" ? "warn" : t === "grey" ? "bad" : "blue"; }
/* every counted item gets a pin in the app's own layer, numbered like the overlay */
function drawPins(a) {
  const own = a === state.answer;
  const off = headerOff(a);
  let outlines = "";               // measured rooms: the traced room outline, not its bounding box (QA 14 Nov: the box spilled into neighbours)
  const html = checkItems(a).map((r) => {
    if (!r.rect) return "";
    const dim = own && state.corr.removed.has(r.n) ? " off" : "";
    const v = viewer.rectToView(r.rect);
    const pts = r.mark && r.mark.kind === "poly" && r.mark.pts && r.mark.pts.length > 2 ? r.mark.pts : null;
    if (pts) {
      const vp = pts.map(([px, py]) => viewer.pointToView(px, py + off));
      outlines += `<polygon class="${pinTone(r.tone)}${dim}" data-i="${r.i}" points="${vp.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ")}"/>`;
      const [x0, y0] = vp.reduce(([ax, ay], [x, y]) => (y < ay - 0.5 || (Math.abs(y - ay) <= 0.5 && x < ax) ? [x, y] : [ax, ay]), [Infinity, Infinity]);
      return `<div class="pin room ${pinTone(r.tone)}${dim}" data-i="${r.i}" style="left:${x0}px;top:${y0}px;width:0;height:0"><span>${esc(r.n)}</span></div>`;
    }
    return `<div class="pin ${pinTone(r.tone)}${dim}" data-i="${r.i}" style="left:${v[0]}px;top:${v[1]}px;width:${v[2]}px;height:${v[3]}px"><span>${esc(r.n)}</span></div>`;
  }).join("");
  const rooms = outlines ? `<svg class="room-outlines" aria-hidden="true">${outlines}</svg>` : "";
  /* approximations: the doors / WC pans whose size set the scale, dashed amber with their size, not numbered items */
  const sobj = (a.marks?.marks || []).filter((m) => m.kind === "scale_object").map((m) => {
    const v = viewer.rectToView([m.rect[0], m.rect[1] + off, m.rect[2] - m.rect[0], m.rect[3] - m.rect[1]]);
    return `<div class="scale-obj" title="${esc(m.label || "")}: sets the approximate scale" style="left:${v[0]}px;top:${v[1]}px;width:${v[2]}px;height:${v[3]}px"><span>${esc(m.label || "")}</span></div>`;
  }).join("");
  const added = own ? state.corr.added.map((p, j) => {
    const [vx, vy] = viewer.pointToView(p.x, p.y + off);
    return `<div class="pin add" style="left:${vx - 9}px;top:${vy - 9}px;width:18px;height:18px"><span>+${j + 1}</span></div>`;
  }).join("") : "";
  /* witness views: circle the places where another format disagrees about an individual item */
  const mk = a.marks;
  const dis = !own && mk && a.disputed ? a.disputed.map((d) => {
    const R = Math.max(mk.w * 0.018, 14);
    const tip = d.kind === "only" ? `Counted here as #${d.n}, not by ${d.other}` : `Counted by ${d.other}, missed here`;
    const [vx, vy] = viewer.pointToView(d.x * mk.w, d.y * mk.h + off);
    return `<div class="dis ${d.kind}" title="${esc(tip)}" style="left:${vx - R}px;top:${vy - R}px;width:${2 * R}px;height:${2 * R}px"></div>`;
  }).join("") : "";
  $("#marks").innerHTML = rooms + sobj + html + added + dis + '<div class="reticle" hidden></div>';
  drawLayers();                                 // evidence layers of the selected item, in the new view space
}
let lastFocus = null;            // the selected item's rect and index, so a rotation can redraw its reticle
function clearMarks() { closeSteps(); $("#marks").innerHTML = ""; clearFocus(); lastFocus = null; }
function clearFocus() {
  document.querySelectorAll(".item.on, .pin.on, .thumb.on, .room-outlines polygon.on").forEach((b) => b.classList.remove("on"));
  const ret = $("#marks .reticle");
  if (ret) ret.hidden = true;
}
function focusRect(r, i, focus) {
  if (state.mode === "3d") {                    // in 3D the strip and item list select the element in the model
    const it = state.answer ? answerItems(state.answer)[+i] : null;
    if (focus && it && it.guid && window.Viewer3D) { mark3dItem(i); Viewer3D.select(it.guid); }
    return;
  }
  if (!r || state.layer !== "evidence") return;
  clearFocus();
  if (i != null) document.querySelectorAll(`#itemList .item[data-i="${i}"], #stripList .thumb[data-i="${i}"], #marks .pin[data-i="${i}"], #marks polygon[data-i="${i}"]`).forEach((el) => el.classList.add("on"));
  lastFocus = { r, i };
  const ret = $("#marks .reticle");
  const room = i != null && document.querySelector(`#marks polygon[data-i="${i}"]`);
  if (ret && !room) {               // a room is shown by its own outline turning accent; a box would cover the neighbours
    const pad = 4, v = viewer.rectToView(r);
    Object.assign(ret.style, { left: v[0] - pad + "px", top: v[1] - pad + "px", width: v[2] + pad * 2 + "px", height: v[3] + pad * 2 + "px" });
    ret.hidden = false;
  }
  if (focus) viewer.focusRect(...r, true);
  if (focus) openSteps(i);                     // evidence layers: the Steps card for the clicked item
  /* the same item in the strip and in the answer's list: bring the other one into view too (his request, 14 Nov) */
  if (focus && i != null) document.querySelectorAll(`#itemList .item[data-i="${i}"], #stripList .thumb[data-i="${i}"]`).forEach((el) => {
    const box = el.closest("#itemList, #stripList");
    if (!box) return;
    const b = box.getBoundingClientRect(), e = el.getBoundingClientRect();
    if (box.id === "stripList") { if (e.left < b.left || e.right > b.right) box.scrollBy({ left: e.left - b.left - (b.width - e.width) / 2, behavior: "smooth" }); }
    else if (e.top < b.top || e.bottom > b.bottom) box.scrollBy({ top: e.top - b.top - (b.height - e.height) / 2, behavior: "smooth" });
  });
}
/* click the selected item again, click empty drawing or press Escape: back to the plain answer (his request) */
function isSelected(i) { return layers.sel != null && String(layers.sel.i) === String(i); }
function deselect() { closeSteps(); clearFocus(); lastFocus = null; }
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape" || e.defaultPrevented || e.target?.closest?.("input, textarea, select")) return;
  if (layers.sel != null || lastFocus) deselect();
});
/* in 3D: the selected element is marked in both the strip and the Items list */
function mark3dItem(i) {
  document.querySelectorAll("#itemList .item.on, #stripList .thumb.on").forEach((b) => b.classList.remove("on"));
  document.querySelectorAll(`#itemList .item[data-i="${i}"], #stripList .thumb[data-i="${i}"]`).forEach((el) => el.classList.add("on"));
}
function markItem(btn, it, a, n, focus) {
  if (state.mode === "3d" && it && it.guid && window.Viewer3D) {
    if (focus) { mark3dItem(btn.dataset.i); Viewer3D.select(it.guid); }
    return;
  }
  if (focus && isSelected(btn.dataset.i)) { deselect(); return; }
  focusRect(itemRect(it, a, +btn.dataset.i), btn.dataset.i, focus);
}

/* ---------------------------------------------------------------- evidence layers: the Steps card */
/* Selecting an item (strip crop, item list, or the number on a pin) opens a small card over the drawing with
   the reasoning for that item, one numbered step per layer: the text that was read, how it was interpreted,
   the value, and the region it covers. Each step with a place on the drawing is drawn in the marks layer (view
   space, so rotation and mirroring keep it aligned); hovering or clicking a step shows only that layer.
   Steps come from the marks sidecar (`steps`, keyed by item number, written by the routes through
   bpqa/evidence.record_steps); routes that do not write them get steps derived from the item and its mark. */
const layers = { sel: null, solo: null, held: false, playT: null };

function layerSteps(a, row) {
  const off = headerOff(a);
  const rec = a.marks?.steps?.[String(row.n)];
  if (rec && rec.length) {
    return rec.map((st) => ({ ...st,
      r: st.rect ? [st.rect[0], st.rect[1] + off, Math.max(st.rect[2] - st.rect[0], 2), Math.max(st.rect[3] - st.rect[1], 2)] : null,
      pts: st.pts && st.pts.length > 2 ? st.pts.map(([x, y]) => [x, y + off]) : null,
      /* a step resting on several places (the doors that set an approximate scale): one rect each, captioned */
      rs: st.rects && st.rects.length ? st.rects.map((q) => [q[0], q[1] + off, Math.max(q[2] - q[0], 2), Math.max(q[3] - q[1], 2)]) : null }));
  }
  return deriveLayerSteps(a, row);
}
/* routes without recorded steps: what the item and its mark already say */
function deriveLayerSteps(a, row) {
  const it = row.it && typeof row.it === "object" ? row.it : null;
  const off = headerOff(a);
  const poly = row.mark?.kind === "poly" && row.mark.pts?.length > 2 ? row.mark.pts.map(([x, y]) => [x, y + off]) : null;
  const obj = String(a.query?.obj || "item").replace(/_/g, " ");
  const unit = fmtUnit(a.unit);
  const S = [];
  if (it && it.guid) {                                     // IFC: the element, its quantity, its outline, the model
    S.push({ kind: "label", title: "Element", text: it.name || it.label || it.room || "element", note: `GUID ${it.guid}`, cap: "element" });
    const v = it.value ?? it.area_m2 ?? it.area;
    if (typeof v === "number") S.push({ kind: "value", title: "Value read", text: `${v} ${unit}`.trim(), note: "quantity taken from the model" });
    if (poly || row.rect) S.push({ kind: "region", title: "On the plan", text: poly ? "outline" : "box", note: "drawn from the model geometry",
      cap: poly ? "outline from the model" : "element", pts: poly, r: poly ? null : row.rect });
    if (state.has3d) S.push({ kind: "model", title: "In 3D", text: "", note: "", guid: it.guid });
    return S;
  }
  if (it && it.ocr_conf != null) {                         // scan room labels: the text read, then what it counts as
    S.push({ kind: "label", title: "Text read", text: it.label || it.text || it.match || "", note: `OCR confidence ${Number(it.ocr_conf).toFixed(2)}`,
      cap: `read as ${obj}`, r: row.rect });
    S.push({ kind: "count", title: "Counted as", text: obj, note: it.match && it.match !== it.label ? `matched ${it.match}` : `item ${row.n} of the count` });
    return S;
  }
  if (it && (it.score != null || it.cls != null || it.class != null)) {   // scan symbols: the detected box, class and score
    const cls = String(it.class ?? it.cls ?? obj).replace(/_/g, " ");
    S.push({ kind: "detect", title: "Symbol found", text: cls, note: it.score != null ? `detector score ${Number(it.score).toFixed(2)}` : "",
      cap: it.score != null ? `${cls} ${Number(it.score).toFixed(2)}` : cls, r: row.rect });
    const total = answerItems(a).length;
    S.push({ kind: "count", title: "Counted", text: `#${row.n} of ${total}`, note: row.tone === "amber" ? "uncertain: check it on the drawing" : "in the answer" });
    return S;
  }
  S.push({ kind: "label", title: "Item", text: row.label || "", note: row.extra || "" });
  if (poly) S.push({ kind: "region", title: "Region", text: "outline", note: row.area != null ? `${Number(row.area).toFixed(2)} m2` : "", cap: "region", pts: poly });
  else if (row.rect) S.push({ kind: "region", title: "On the drawing", text: "box", note: "", cap: obj, r: row.rect });
  return S;
}
const m2 = (s) => String(s ?? "").replace(/(\d)\s?m2\b/g, "$1 m²");
/* text as read by OCR: only the square-metre sign is put right ("8,7 m?" and "8,7 m2" are how OCR reads "8,7 m²") */
const ocrM2 = (s) => String(s ?? "").replace(/(\d)\s?m(?:2|\?)(?![\w?])/g, "$1 m²");
function layerRow(a, i) { return checkItems(a).find((r) => String(r.i) === String(i)) || null; }

function openSteps(i, zoom = true) {
  const a = state.shown;
  if (!a || state.mode === "3d" || state.layer !== "evidence") return;
  const row = layerRow(a, i);
  if (!row) return;
  stopStepPlay();
  layers.sel = { a, i: row.i, row, steps: layerSteps(a, row) };
  layers.solo = null;
  layers.held = false;
  renderStepsCard();
  drawLayers();
  if (zoom) focusSteps();
}
/* zoom so every layer of the item is readable, in the part of the viewer the card leaves free */
function focusSteps() {
  const s = layers.sel, card = $("#stepsCard");
  if (!s || !card || card.hidden) return;
  const bb = [Infinity, Infinity, -Infinity, -Infinity];
  const add = (x, y) => { bb[0] = Math.min(bb[0], x); bb[1] = Math.min(bb[1], y); bb[2] = Math.max(bb[2], x); bb[3] = Math.max(bb[3], y); };
  for (const st of s.steps) {
    if (st.r) { add(st.r[0], st.r[1]); add(st.r[0] + st.r[2], st.r[1] + st.r[3]); }
    (st.rs || []).forEach((q) => { add(q[0], q[1]); add(q[0] + q[2], q[1] + q[3]); });
    (st.pts || []).forEach(([x, y]) => add(x, y));
  }
  if (s.row.rect) { add(s.row.rect[0], s.row.rect[1]); add(s.row.rect[0] + s.row.rect[2], s.row.rect[1] + s.row.rect[3]); }
  if (!isFinite(bb[0])) return;
  const pad = Math.max(bb[2] - bb[0], bb[3] - bb[1]) * (s.steps.some((st) => st.pts) ? 0.08 : 1.2);   // a lone symbol keeps its surroundings
  bb[0] -= pad; bb[1] -= pad; bb[2] += pad; bb[3] += pad;
  const v = $("#viewer").getBoundingClientRect(), c = card.getBoundingClientRect();
  const top = 56, gap = 16;                        // under the floating toolbar
  const right = { l: c.right - v.left + gap, t: top, w: v.right - c.right - 2 * gap, h: v.height - top - gap };
  const above = { l: gap, t: top, w: v.width - 2 * gap, h: c.top - v.top - top - gap };
  const r = [bb[0], bb[1], Math.max(bb[2] - bb[0], 8), Math.max(bb[3] - bb[1], 8)];
  const [, , vw, vh] = viewer.rectToView(r);
  const fitK = (b) => (b.w < 120 || b.h < 80 ? 0 : Math.min(b.w / vw, b.h / vh));    // the side where the item can be largest
  const box = fitK(right) >= fitK(above) ? right : above;
  /* no room beside or above the card (a short viewer): centre the item in the whole viewer instead */
  if (!fitK(box)) { viewer.focusIn(r, { l: gap, t: Math.min(top, v.height / 4), w: v.width - 2 * gap, h: v.height - Math.min(top, v.height / 4) - gap }, 0.6, 4, true); return; }
  viewer.focusIn(r, box, 0.8, 4, true);
}
function closeSteps() {
  stopStepPlay();
  layers.sel = null;
  layers.solo = null;
  layers.held = false;
  $("#marks .layers")?.remove();
  $("#marks").querySelectorAll(".lcap").forEach((c) => c.remove());
  $("#marks").removeAttribute("data-solo");
  $("#marks").classList.remove("stepping");
  $("#marks").querySelectorAll(".steps-of").forEach((el) => el.classList.remove("steps-of"));
  const card = $("#stepsCard");
  if (card) card.hidden = true;
}

/* every step with a place on the drawing, in view space; called again by drawPins after any redraw */
function drawLayers() {
  const box = $("#marks");
  box.querySelector(".layers")?.remove();
  box.querySelectorAll(".lcap").forEach((c) => c.remove());
  const s = layers.sel;
  if (!s || s.a !== state.shown || state.layer !== "evidence") { box.classList.remove("stepping"); return; }
  let svg = "", caps = "";
  const vr = s.steps.map((st) => (st.r && !st.pts ? viewer.rectToView(st.r) : null));
  const mids = vr.filter(Boolean).map((v) => v[1] + v[3] / 2).sort((p, q) => p - q);
  const split = mids.length > 1 ? (mids[Math.floor((mids.length - 1) / 2)] + mids[Math.floor(mids.length / 2)]) / 2 : Infinity;
  s.steps.forEach((st, k) => {
    const cap = esc(ocrM2(m2(st.cap || st.note || st.text || st.title)).slice(0, 48));
    if (st.rs) {
      let hole = "";
      st.rs.forEach((q, j) => {
        const v = viewer.rectToView(q), p = 3;
        svg += `<rect data-k="${k}" class="lk-${esc(st.kind)}" x="${v[0] - 2}" y="${v[1] - 2}" width="${v[2] + 4}" height="${v[3] + 4}"/>`;
        hole += `M${v[0] - p},${v[1] - p}H${v[0] + v[2] + p}V${v[1] + v[3] + p}H${v[0] - p}Z`;
        const c = esc(String((st.caps || [])[j] || cap).slice(0, 28));
        caps += `<div class="lcap mini lk-${esc(st.kind)}" data-k="${k}" style="left:${v[0] - 2}px;top:${v[1] - 2}px"><span>${j ? "" : `<b>${k + 1}</b>`}${c}</span></div>`;
      });
      st.hole = hole;
    } else if (st.pts) {
      const vp = st.pts.map(([x, y]) => viewer.pointToView(x, y));
      svg += `<polygon data-k="${k}" class="lk-${esc(st.kind)}" points="${vp.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ")}"/>`;
      st.hole = "M" + vp.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join("L") + "Z";
      const [bx, by] = vp.reduce(([ax, ay], [x, y]) => (y > ay + 0.5 || (Math.abs(y - ay) <= 0.5 && x < ax) ? [x, y] : [ax, ay]), [Infinity, -Infinity]);
      caps += `<div class="lcap below" data-k="${k}" style="left:${bx}px;top:${by}px"><span><b>${k + 1}</b>${cap}</span></div>`;
    } else if (st.r) {
      const v = vr[k];
      svg += `<rect data-k="${k}" class="lk-${esc(st.kind)}" x="${v[0] - 2}" y="${v[1] - 2}" width="${v[2] + 4}" height="${v[3] + 4}"/>`;
      const p = 4;
      st.hole = `M${v[0] - p},${v[1] - p}H${v[0] + v[2] + p}V${v[1] + v[3] + p}H${v[0] - p}Z`;
      const below = v[1] + v[3] / 2 > split;              // stacked label and value: the upper captioned above, the lower below
      caps += `<div class="lcap${below ? " below" : ""}" data-k="${k}" style="left:${v[0] - 2}px;top:${below ? v[1] + v[3] + 2 : v[1] - 2}px"><span><b>${k + 1}</b>${cap}</span></div>`;
    }
  });
  box.insertAdjacentHTML("beforeend", `<svg class="layers" aria-hidden="true"><path class="veil" d=""/>${svg}</svg>${caps}`);
  box.classList.add("stepping");
  box.querySelectorAll(`.pin[data-i="${s.i}"], .room-outlines polygon[data-i="${s.i}"]`).forEach((el) => el.classList.add("steps-of"));
  applySolo();
}
function applySolo() {
  const k = layers.solo;
  const box = $("#marks");
  if (k == null) box.removeAttribute("data-solo"); else box.setAttribute("data-solo", layers.sel?.steps[k]?.kind || "");
  box.querySelectorAll(".layers [data-k], .lcap").forEach((el) => el.classList.toggle("dim", k != null && +el.dataset.k !== k));
  const hole = k != null ? layers.sel?.steps[k]?.hole : null;          // a step shown alone: the rest of the drawing is veiled
  const veil = box.querySelector(".layers .veil");
  if (veil) veil.setAttribute("d", hole ? `M-99999,-99999H99999V99999H-99999Z${hole}` : "");
  document.querySelectorAll("#stepsCard .sc-step").forEach((b) => {
    b.setAttribute("aria-pressed", String(layers.held && +b.dataset.k === k));
    b.classList.toggle("on", +b.dataset.k === k);
  });
}
function setSolo(k, held = layers.held) {
  layers.solo = k;
  layers.held = held;
  applySolo();
}

function renderStepsCard() {
  let card = $("#stepsCard");
  if (!card) {
    card = document.createElement("section");
    card.id = "stepsCard";
    card.className = "steps-card";
    card.setAttribute("aria-label", "Steps for the selected item");
    $("#viewer").appendChild(card);
    bindStepsCard(card);
  }
  const s = layers.sel;
  if (!s) { card.hidden = true; return; }
  const geo = s.steps.filter((st) => st.r || st.pts || st.rs).length;
  const raw = (st) => st.kind === "value" || st.kind === "label" || st.kind === "detect";   // text as read: not reformatted
  card.innerHTML = `
    <div class="sc-head">
      <h3 class="h-panel"><span class="mono sc-n">#${esc(s.row.n)}</span>${esc(s.row.label || "")}</h3>
      <span class="sc-gap"></span>
      ${geo > 1 ? `<button type="button" class="btn btn-quiet sc-play" aria-pressed="false" title="Show the layers one at a time">Step through</button>` : ""}
      <button type="button" class="tool sc-close" aria-label="Close the steps">${icon("close")}</button>
    </div>
    <ol class="sc-list">
      ${s.steps.map((st, k) => `<li><button type="button" class="sc-step${st.r || st.pts || st.rs ? "" : " flat"}" data-k="${k}" aria-pressed="false">
        <span class="cite">${k + 1}</span><span class="k">${esc(st.title)}</span>
        <span class="v">${st.kind === "model" ? `<span class="sc-3d">Select in 3D</span>`
          : `${st.text ? `<span class="mono">${esc(raw(st) ? ocrM2(st.text) : m2(st.text))}</span>` : ""}${st.note ? `${st.text ? " → " : ""}<span class="note">${esc(ocrM2(m2(st.note)))}</span>` : ""}`}</span>
      </button></li>`).join("")}
    </ol>
    ${geo ? `<p class="sc-foot">Hover or click a step to show only its layer.</p>` : ""}`;
  card.hidden = false;
}
function bindStepsCard(card) {
  ["pointerdown", "pointerup", "wheel", "dblclick"].forEach((ev) => card.addEventListener(ev, (e) => e.stopPropagation()));
  card.addEventListener("click", (e) => {
    if (e.target.closest(".sc-close")) { closeSteps(); return; }
    if (e.target.closest(".sc-play")) { if (layers.playT) stopStepPlay(); else playSteps(); return; }
    const b = e.target.closest(".sc-step");
    if (!b) return;
    const k = +b.dataset.k, st = layers.sel?.steps[k];
    if (st?.kind === "model") { select3d(st.guid); return; }
    stopStepPlay();
    if (layers.held && layers.solo === k) setSolo(null, false); else setSolo(k, true);
  });
  card.addEventListener("mouseover", (e) => {
    const b = e.target.closest(".sc-step");
    if (b && !layers.held && !layers.playT) setSolo(+b.dataset.k, false);
  });
  card.addEventListener("mouseleave", () => { if (!layers.held && !layers.playT) setSolo(null, false); });
  card.addEventListener("keydown", (e) => {
    e.stopPropagation();
    const n = layers.sel?.steps.length || 0;
    if (!n) return;
    if (e.key === "Escape") { if (layers.solo != null) setSolo(null, false); else deselect(); e.preventDefault(); return; }
    const d = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 }[e.key];
    if (!d) return;
    e.preventDefault();
    stopStepPlay();
    const k = layers.solo == null ? (d > 0 ? 0 : n - 1) : layers.solo + d;
    if (k < 0 || k >= n) { setSolo(null, false); card.querySelector(".sc-close")?.focus(); return; }
    setSolo(k, true);
    card.querySelector(`.sc-step[data-k="${k}"]`)?.focus();
  });
}
/* one layer at a time, then all of them again */
function playSteps() {
  const s = layers.sel;
  if (!s) return;
  const ks = s.steps.map((st, k) => (st.r || st.pts || st.rs ? k : null)).filter((k) => k != null);
  let j = 0;
  $("#stepsCard .sc-play")?.setAttribute("aria-pressed", "true");
  const tick = () => {
    if (j >= ks.length) { stopStepPlay(); setSolo(null, false); return; }
    setSolo(ks[j++], false);
    layers.playT = setTimeout(tick, 1100);
  };
  tick();
}
function stopStepPlay() {
  clearTimeout(layers.playT);
  layers.playT = null;
  $("#stepsCard .sc-play")?.setAttribute("aria-pressed", "false");
}
function select3d(guid) {
  if (!state.has3d || !guid) return;
  closeSteps();
  setMode("3d");
  let tries = 0;                     // the model may still be loading: try until its element card shows
  const go = () => {
    if (window.Viewer3D) Viewer3D.select(guid);
    if ((!$("#v3dCard") || $("#v3dCard").hidden) && ++tries < 60 && state.mode === "3d") setTimeout(go, 500);
  };
  go();
}
/* a rotation or mirror while the card is open: bring the item back into the free part of the viewer */
viewer.onOrient(() => { if (layers.sel && state.layer === "evidence") focusSteps(); });
/* the number on a pin selects its item too (the marks layer lets pointer events through, for panning) */
(function bindPinClicks() {
  const vp = $("#viewer");
  let down = null;
  vp.addEventListener("pointerdown", (e) => {
    if (state.layer !== "evidence" || state.mode === "3d") { down = null; return; }
    const hit = (el) => { const b = el.getBoundingClientRect(); return e.clientX >= b.left && e.clientX <= b.right && e.clientY >= b.top && e.clientY <= b.bottom; };
    const tag = [...document.querySelectorAll("#marks .pin[data-i] > span")].find(hit);        // the number tag first,
    const pin = tag ? tag.parentElement : [...document.querySelectorAll("#marks .pin[data-i]:not(.room)")].filter(hit)   // then the smallest box
      .sort((p, q) => p.offsetWidth * p.offsetHeight - q.offsetWidth * q.offsetHeight)[0];
    down = { i: pin ? pin.dataset.i : null, x: e.clientX, y: e.clientY };      // i null: a click on empty drawing
  }, true);
  vp.addEventListener("pointerup", (e) => {
    const d = down;
    down = null;
    if (!d || state.addMode || Math.abs(e.clientX - d.x) + Math.abs(e.clientY - d.y) > 4) return;
    if (d.i == null) { if (e.target.closest(".viewer-tools, #stepsCard")) return; if (layers.sel != null || lastFocus) deselect(); return; }
    const a = state.shown, row = a && layerRow(a, d.i);
    if (row && row.rect && isSelected(row.i)) { deselect(); return; }
    if (row && row.rect) { focusRect(row.rect, row.i, false); openSteps(row.i); }
  });
})();

/* ---------------------------------------------------------------- 3D view (IFC models) */
function has3dFor(d) { return !!d && (d.ext === "ifc" || (d.twins || []).includes("ifc model")); }
function setup3d(d) {
  state.has3d = has3dFor(d);
  $("#modeSeg").hidden = !state.has3d;
  if (!state.has3d && state.mode === "3d") setMode("plan");
}
function setMode(mode) {
  if (mode === "3d" && !state.has3d) mode = "plan";
  state.mode = mode;
  $("#viewer").classList.toggle("mode-3d", mode === "3d");
  $("#view3d").hidden = mode !== "3d";
  $("#modeSeg").querySelectorAll("button").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.mode === mode)));
  if (mode === "3d") {
    $("#v3dFail").hidden = true;
    const go = () => window.Viewer3D.open(state.drawing.path, $("#view3d")).then(() => update3d());
    if (window.Viewer3D) go(); else window.addEventListener("v3d-ready", go, { once: true });
  }
  setHash();
}
/* the IFC GlobalIds an answer rests on: cited elements, and openings deducted from wall areas */
function highlightFor(a) {
  const cited = new Set(), deducted = new Set();
  const GID = /^[0-9A-Za-z_$]{22}$/;
  const walk = (v, ded) => {
    if (typeof v === "string") { if (GID.test(v)) (ded ? deducted : cited).add(v); return; }
    if (Array.isArray(v)) { v.forEach((x) => walk(x, ded)); return; }
    if (v && typeof v === "object") for (const [k, x] of Object.entries(v)) walk(x, ded || k === "openings");
  };
  if (a && a.status !== "cannot") {
    walk(a.items || [], false);
    for (const m of String(a.explanation || "").matchAll(/(?<![\w$])[0-9A-Za-z_$]{22}(?![\w$])/g)) if (/[0-9_$]/.test(m[0])) cited.add(m[0]);
  }
  return { cited: [...cited], deducted: [...deducted] };
}
function update3d() {
  if (state.mode !== "3d" || !window.Viewer3D) return;
  Viewer3D.setHighlight(highlightFor(state.answer));
}
$("#modeSeg").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));
["pointerdown", "pointermove", "pointerup", "wheel", "dblclick", "keydown"].forEach((ev) =>
  $("#view3d").addEventListener(ev, (e) => e.stopPropagation()));
window.addEventListener("v3d-failed", () => { /* the plan stays one click away; the 3D panel explains why */ });

/* ---------------------------------------------------------------- verification strip */
function isCount(a) { return a.unit === "count"; }
function correctedValue(a) {
  const c = state.corr;
  if (a.value == null || typeof a.value !== "number") return a.value;
  if (isCount(a)) return a.value - c.removed.size + c.added.length;
  // a removed room takes out its own floor and wall area plus the waste on them (QA 14 Nov: a tiling total did
  // not change after "Not this one" because the value includes walls and waste, not just the floor area)
  const tot = (a.items || []).find((it) => it && it.totals)?.totals || null;
  const w = tot && tot.waste_pct && tot.waste_in_value !== false ? tot.waste_pct / 100 : 0;
  const floorCounts = !tot || tot.floor_m2 == null || tot.floor_m2 > 0;
  let v = a.value;
  for (const r of checkItems(a)) {
    if (!c.removed.has(r.n)) continue;
    const it = r.it && typeof r.it === "object" ? r.it : {};
    const floor = floorCounts ? (it.floor_m2 ?? it.area_m2 ?? (typeof r.area === "number" ? r.area : 0)) : 0;
    v -= (floor + (it.net_wall_m2 || 0)) * (1 + w);
  }
  return Math.round(v * 100) / 100;
}
function corrected() { return state.corr.removed.size > 0 || state.corr.added.length > 0; }

const TW = 104, TH = 78;
function renderStrip() {
  const box = $("#strip");
  const a = state.shown;
  if (!a || !a.evidence_id || state.layer !== "evidence") { box.hidden = true; return; }
  const rows = checkItems(a).filter((r) => r.rect);
  if (!rows.length) { box.hidden = true; return; }
  box.hidden = false;
  const own = a === state.answer;
  const measure = !isCount(a);
  const nBlue = rows.filter((r) => r.tone !== "amber").length, nAmber = rows.length - nBlue;
  const scale = measure ? (a.why || []).find((w) => w.kind === "scale") : null;
  const noun = measure ? (rows.length > 1 ? "rooms" : "room") : rows.length > 1 ? "items" : "item";
  $("#stripHead").innerHTML = `
    <h2 class="h-panel">Verify ${rows.length} ${noun}</h2>
    <span class="legend meta"><span class="key blue" aria-hidden="true"></span>${nBlue} proven${nAmber ? `<span class="key amber" aria-hidden="true"></span>${nAmber} to check` : ""}</span>
    ${a.approx ? `<span class="meta scale-src approx" title="${esc(a.approx.scale)}">Approximate scale from ${esc(a.approx.short || (a.approx.assume?.kind === "ratio" ? `1:${a.approx.assume.ratio} on ${a.approx.assume.sheet}` : ""))}</span>`
      : scale ? `<span class="meta scale-src" title="${esc(scale.text)}">Scale from ${esc(scale.text.split(" (")[0].replace(/^Scale found on the sheet$/, "the sheet"))}</span>` : ""}
    <span class="strip-gap"></span>
    ${own ? `<span class="corr" id="corrLine">${esc(corrLine(a))}</span>
      ${isCount(a) ? `<button class="btn btn-quiet" type="button" id="addMode" aria-pressed="${state.addMode}">${icon("plus")}Add missed item</button>` : ""}
      <button class="btn" type="button" id="saveCorr" ${corrected() ? "" : "disabled"}>Save correction</button>
      <a class="btn btn-quiet" id="exportCorr" href="/api/corrections.csv" download>${icon("download")}Export</a>`
      : `<span class="meta">${esc(a.format)} witness. Corrections apply to the answer.</span>
      <button class="btn btn-quiet" type="button" id="backToAnswer">Back to the answer</button>`}`;
  const img = viewer.img;
  const list = $("#stripList");
  list.innerHTML = "";
  const off = headerOff(a);
  const added = own ? state.corr.added.map((p, j) => ({ add: j, n: `+${j + 1}`, rect: [p.x - 30, p.y + off - 30, 60, 60], tone: "blue", label: "added by you", extra: "" })) : [];
  for (const r of [...rows, ...added]) {
    const li = document.createElement("li");
    const removed = own && r.add == null && state.corr.removed.has(r.n);
    li.className = "thumb-cell";
    const name = measure ? r.label : r.add != null ? r.label : r.extra || r.label;
    li.innerHTML = `
      <button type="button" class="thumb ${r.tone === "amber" ? "amber" : "blue"}${removed ? " removed" : ""}${r.add != null ? " added" : ""}" ${r.add == null ? `data-i="${r.i}"` : ""}
        title="${esc(r.label)}${r.extra ? " " + esc(r.extra) : ""}" aria-label="Show item ${esc(r.n)} on the drawing">
        <canvas width="${TW * 2}" height="${TH * 2}"></canvas>
        <span class="tn mono">${esc(r.n)}</span>
        ${r.tone === "amber" ? `<span class="tq" aria-label="to check">${icon("estimate")}</span>` : ""}
      </button>
      <span class="tl">${measure && r.area != null ? `<span class="mono">${Number(r.area).toFixed(2)} m²</span> ` : ""}<span class="tname">${esc(name)}</span></span>
      ${own ? (r.add != null
        ? `<button type="button" class="tx" data-add="${r.add}" aria-label="Remove added item ${esc(r.n)}">Undo</button>`
        : `<button type="button" class="tx" data-n="${esc(r.n)}" aria-pressed="${removed}" aria-label="${removed ? "Keep" : "Not this one:"} item ${esc(r.n)}">${removed ? "Keep" : "Not this one"}</button>`) : ""}`;
    list.appendChild(li);
    drawThumb(li.querySelector("canvas"), img, r.rect, removed, r.mark && r.mark.kind === "poly" ? { pts: r.mark.pts, off, tone: r.tone } : null);
    const t = li.querySelector(".thumb");
    t.addEventListener("click", () => { if (isSelected(r.i)) deselect(); else focusRect(r.rect, r.i, true); });
    t.addEventListener("mouseenter", () => focusRect(r.rect, r.i, false));
  }
  list.querySelectorAll(".tx[data-n]").forEach((b) => b.addEventListener("click", () => {
    const n = isNaN(+b.dataset.n) ? b.dataset.n : +b.dataset.n;
    if (state.corr.removed.has(n)) state.corr.removed.delete(n); else state.corr.removed.add(n);
    onCorrection();
  }));
  list.querySelectorAll(".tx[data-add]").forEach((b) => b.addEventListener("click", () => {
    state.corr.added.splice(+b.dataset.add, 1);
    onCorrection();
  }));
  $("#addMode")?.addEventListener("click", () => setAddMode(!state.addMode));
  $("#saveCorr")?.addEventListener("click", saveCorrection);
  $("#exportCorr")?.addEventListener("click", exportCorrections);
  $("#backToAnswer")?.addEventListener("click", () => showEvidence(state.answer, false));
}
function drawThumb(cv, img, r, dim, poly) {
  const ctx = cv.getContext("2d");
  ctx.fillStyle = "#fff";
  ctx.fillRect(0, 0, cv.width, cv.height);
  if (!img.naturalWidth || !r) return;
  /* the crop follows the viewer's rotation and mirror, so it looks like the item does on the drawing */
  const o = viewer.matrix();
  const cx = r[0] + r[2] / 2, cy = r[1] + r[3] / 2;
  const f = poly ? 1.35 : 2.4;
  const rv = viewer.rectToView(r);
  const w = Math.max(rv[2] * f, rv[3] * f * (TW / TH), 90);      // window size in view pixels
  const h = w * (TH / TW);
  const sw = o.rot % 2 ? h : w, sh = o.rot % 2 ? w : h;         // the same window in image pixels
  const s = cv.width / w;
  ctx.save();
  ctx.translate(cv.width / 2, cv.height / 2);
  ctx.scale(s, s);
  ctx.transform(o.a, o.b, o.c, o.d, 0, 0);
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(img, cx - sw / 2, cy - sh / 2, sw, sh, -sw / 2, -sh / 2, sw, sh);
  if (poly && poly.pts?.length > 2) {         // the measured room outlined, so it stands out from its neighbours
    ctx.beginPath();
    poly.pts.forEach(([px, py], j) => {
      const X = px - cx, Y = py + poly.off - cy;
      if (j) ctx.lineTo(X, Y); else ctx.moveTo(X, Y);
    });
    ctx.closePath();
    ctx.lineWidth = 4 / s;
    ctx.strokeStyle = poly.tone === "amber" ? "rgb(231,140,8)" : "rgb(0,106,165)";
    ctx.stroke();
  }
  ctx.restore();
  if (dim) { ctx.fillStyle = "rgba(20,26,34,0.62)"; ctx.fillRect(0, 0, cv.width, cv.height); }
}
function corrLine(a) {
  if (!corrected()) return isCount(a) ? "Count unchanged" : "Total unchanged";
  const c = state.corr;
  const bits = [];
  if (c.removed.size) bits.push(`${c.removed.size} removed`);
  if (c.added.length) bits.push(`${c.added.length} added`);
  return `${fmtValue(a.value)} → ${fmtValue(correctedValue(a))} (${bits.join(", ")})`;
}
function setAddMode(on) {
  state.addMode = on;
  $("#viewer").classList.toggle("adding", on);
  $("#addMode")?.setAttribute("aria-pressed", String(on));
  if (on) toast("Click the drawing where an item was missed. Dragging still pans.");
}
function addItemAt(x, y) {
  const a = state.answer;
  if (!a || state.shown !== a) return;
  state.corr.added.push({ x: Math.round(x), y: Math.round(y - headerOff(a)) });
  onCorrection();
}
function onCorrection() {
  const a = state.answer;
  drawPins(state.shown);
  renderStrip();
  const v = $("#ansValue");
  if (v) v.textContent = fmtValue(correctedValue(a));
  const note = $("#corrNote");
  if (note) {
    note.hidden = !corrected();
    const c = state.corr;
    note.innerHTML = corrected() ? `Corrected by you from <span class="mono">${esc(fmtValue(a.value))}</span>: ${c.removed.size} removed, ${c.added.length} added. Save correction records it.` : "";
  }
}
async function saveCorrection() {
  const a = state.answer;
  if (!a || !corrected()) return;
  try {
    const r = await api("/api/corrections", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: a.path, drawing: a.drawing, question: a.question, route: a.route, status: a.status,
        answer_value: a.value, corrected_value: correctedValue(a), unit: a.unit, removed: [...state.corr.removed],
        added: state.corr.added, evidence_id: a.evidence_id, scale_assumption: assumeLabel(a) }),
    });
    toast(`Correction saved, ${r.n} on record. Export downloads them as CSV.`);
  } catch (e) { toast("Could not save: " + e.message, true); }
}

/* Export: an unsaved correction is saved first, and an empty record says so instead of downloading a header-only file */
async function exportCorrections(e) {
  const link = e.currentTarget;
  if (link.dataset.go) { delete link.dataset.go; return; }
  e.preventDefault();
  try {
    if (corrected()) await saveCorrection();
    const r = await api("/api/corrections");
    if (!(r.corrections || []).length) { toast("No corrections saved yet. Remove or add items, then Save correction.", true); return; }
    link.dataset.go = "1";
    link.click();
  } catch (err) { toast("Could not export: " + err.message, true); }
}

/* ================================================================ ask */
$("#askForm").addEventListener("submit", (e) => { e.preventDefault(); if (batchActive()) askMany(); else ask(); });

let busyT;
async function ask(refresh = false) {
  const q = $("#question").value.trim();
  if (!state.drawing) { toast("Pick a drawing first", true); return; }
  if (!q) { $("#question").focus(); return; }
  if (state.askingQ === q && !refresh) return;   // the same question again while it runs: one request (double submit)
  leaveBatchView();                       // ask-several block: a single question shows its own answer
  window.dispatchEvent(new Event("bpe-ask"));   // the first-visit tour offer steps aside
  const btn = $("#askBtn");
  // the button stays enabled while answering, so a different question replaces the running one instead of being
  // dropped by the browser (a disabled submit button blocks Enter too; Mac QA 14 Nov)
  btn.setAttribute("aria-busy", "true");
  $("#viewerBusy").hidden = false;
  /* answer panel busy block: show the question while it runs */
  const firstOnDrawing = !(state.answeredPaths && state.answeredPaths.has(state.drawing.path));
  $("#detailQ").textContent = q;
  $("#detailWait").hidden = !firstOnDrawing;
  $("#detailBusy").hidden = false; $("#detailEmpty").hidden = true; $("#detailBody").hidden = true;
  const t0 = performance.now();
  const tick = () => { $("#busyText").textContent = `Reading the drawing  ${((performance.now() - t0) / 1000).toFixed(1)} s`; };
  tick();
  clearInterval(busyT);
  busyT = setInterval(tick, 100);
  const asked = state.drawing.path, ticket = (state.askTicket = (state.askTicket || 0) + 1);
  abortAsk();                             // a question still running is replaced: its request is dropped
  const ctl = (state.askCtl = new AbortController());
  state.askingQ = q;
  setHash();                              // the question is in the URL while it runs: a reload asks it again
  try {
    const a = await api("/api/ask", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: asked, question: q, pdf_only: $("#pdfOnly").checked, refresh }),
      signal: ctl.signal,
    });
    // a slow answer that comes back after another drawing or question was opened is dropped, never shown on it
    if (ticket !== state.askTicket || !state.drawing || state.drawing.path !== asked) return;
    state.askingQ = null;
    state.answer = a;
    (state.answeredPaths = state.answeredPaths || new Set()).add(asked);
    state.corr = { removed: new Set(), added: [] };
    state.witnesses = null;
    renderAnswer(a);
    if (a.evidence_id) await showEvidence(a);
    else await showDrawing();
    update3d();
    setHash();
    loadWitnesses(a, refresh);
  } catch (e) {
    if (e.name === "AbortError" || ticket !== state.askTicket) return;      // replaced by a newer question or drawing
    state.askingQ = null;
    toast(e.expired ? e.message : "The question failed: " + e.message, true);
  } finally {
    if (ticket === state.askTicket) {         // only the latest question owns the busy state
      state.askCtl = null;
      clearInterval(busyT);
      $("#viewerBusy").hidden = true;
      $("#detailBusy").hidden = true; $("#detailEmpty").hidden = !!state.answer; $("#detailBody").hidden = !state.answer;
      btn.disabled = false;
      btn.removeAttribute("aria-busy");
    }
  }
}

function abortAsk() {
  if (state.askCtl) { state.askCtl.abort(); state.askCtl = null; }
  if (state.witCtl) { state.witCtl.abort(); state.witCtl = null; }   // the witnesses of the answer before, too
}

function fmtValue(v) {
  if (v == null) return "";
  if (typeof v === "number") return Number.isInteger(v) ? v.toLocaleString("en-US") : v.toLocaleString("en-US", { maximumFractionDigits: 2 });
  return String(v);
}
function fmtUnit(u) { return u === "count" ? "" : u === "m2" ? "m²" : u === "m3" ? "m³" : u || ""; }

function itemLabel(it, obj, route) {
  if (typeof it !== "object" || it === null) return [String(it), ""];
  let label = (it.number && it.name ? `${it.number} ${it.name}` : "") || (it.room ? `${it.room}${it.number ? " " + it.number : ""}` : "")
    || it.label || it.class || it.name || it.match || it.text || (it.panel != null ? `panel ${it.panel}` : "") || it.guid || "";
  if (!label && Array.isArray(it.bbox_pt)) label = `${(obj || "item").replace(/_/g, " ")} #${it.n ?? ""}`;
  if (!label && Array.isArray(it.bbox)) {
    const [x0, y0, x1, y1] = it.bbox;
    label = `${(obj || "item").replace(/_/g, " ")} at x ${((x0 + x1) / 2).toFixed(1)}, y ${((y0 + y1) / 2).toFixed(1)}${route === "dxf-geometry" ? " m" : " (drawing units)"}`;
  }
  let extra = "";
  if (it.score != null) extra = "score " + Number(it.score).toFixed(2);
  else if (it.ocr_conf != null) extra = "ocr " + Number(it.ocr_conf).toFixed(2);
  else if (it.value != null) extra = typeof it.value === "number" ? it.value.toFixed(2) : String(it.value);
  else if (it.area != null) extra = Number(it.area).toFixed(2) + " m²";
  else if (it.area_m2 != null) extra = Number(it.area_m2).toFixed(2) + " m²";
  return [label || JSON.stringify(it).slice(0, 60), extra];
}

function renderAnswer(a) {
  const body = $("#detailBody");
  document.querySelector(".work")?.classList.toggle("answered", !!a);   // short screens fold the examples away (CSS)
  $("#detailEmpty").hidden = !!a;
  body.hidden = !a;
  if (!a) { body.innerHTML = ""; return; }
  const st = STATUS[a.status] || STATUS.cannot;
  const hasVal = a.value != null && a.status !== "cannot";
  const valText = fmtValue(a.value);
  const isText = hasVal && typeof a.value === "string" && valText.length > 12;

  const items = answerItems(a);
  const would = (a.items || []).find((it) => it && typeof it === "object" && "would_answer" in it);
  const why = a.why || [];
  const nWhy = why.length;
  const itemsCite = items.length ? nWhy + 1 : null;

  let gold = "";
  if (a.gold) {
    const g = a.gold.answer;
    const gv = typeof g === "number" ? fmtValue(g) : String(g ?? "").split("\n")[0];
    let tag = "";
    if (hasVal && typeof g === "number" && typeof a.value === "number") {
      const ok = a.unit === "count" || a.gold.unit === "count" ? a.value === g : Math.abs(a.value - g) <= Math.abs(g) * 0.02;
      tag = ok ? `<span class="verdict ok">${icon("proven")}matches</span>` : `<span class="verdict bad">${icon("estimate")}differs</span>`;
    }
    gold = `<dt>Dataset</dt><dd><span class="mono">${esc(gv)} ${esc(fmtUnit(a.gold.unit))}</span>${tag} <span class="meta">${esc(a.gold.split || "")} split</span></dd>`;
  }

  body.innerHTML = `
    <div class="ans-head">
      <p class="ans-q">${esc(a.question)}</p>
      <div class="ans-value">
        ${hasVal ? `${a.approx ? `<span class="about">About</span>` : ""}<span class="v${isText ? " text" : ""}" id="ansValue">${esc(valText)}</span><span class="u">${esc(fmtUnit(a.unit))}</span>`
                 : `<span class="v none">No answer</span>`}
      </div>
      ${hasVal && a.approx ? `<div class="approx-note"><p class="approx-text"><span class="approx-tag">${icon("estimate")}Approximation</span><span id="approxText">${esc(m2(a.approx.text || a.approx.sentence || ""))}</span></p>${scaleCtlHtml(a)}</div>` : ""}
      <p class="note corr-note" id="corrNote" hidden></p>
      <dl class="kv facts">
        <dt>Status</dt><dd><span class="status ${esc(a.status)}">${icon(st.icon)}${esc(st.label)}</span>${a.cached ? `<span class="cache-tag" title="Served from the answer cache: this question was asked before on this drawing. Time and cost are from the first run; Run again recomputes them.">Cached</span>` : ""}</dd>
        ${a.status === "cannot" ? `<dd class="wide cannot-reason"><p class="plain-reason">${esc(plainReason(a))}</p><details class="reason-more"><summary>Details</summary><p>${esc(shortReason(a))}${would ? ` The route would have said ${esc(would.would_answer)}.` : ""}</p></details></dd>` : ""}
        ${gold}
        <dt>Confidence</dt><dd class="num">${a.confidence != null && a.status !== "cannot" ? Number(a.confidence).toFixed(2) : "-"}</dd>
        <dt>Response</dt><dd class="num">${a.time_ms < 1000 ? Math.round(a.time_ms) + " ms" : (a.time_ms / 1000).toFixed(1) + " s"}</dd>
      </dl>
      ${usedLine(a)}
      <div class="fine-pop" id="finePrint" role="region" aria-label="Time and cost details" hidden>
        <p class="note">Model calls: ${modelCallsText(a)}. CPU ${a.compute ? fmtSec(a.compute.cpu_s) : "-"}.</p>
        ${a.cached ? `<p class="note cache-note"><span class="cache-tag">Cached</span>Served from the answer cache: this question was asked before on this drawing. Time and cost are from the first run; Run again recomputes them.</p>` : ""}
        ${costMeter(a)}
      </div>
    </div>

    <section class="sec" id="witSec" aria-labelledby="hWit" hidden>
      <div class="sec-head"><h3 class="h-panel" id="hWit">Witnesses</h3><span class="meta" id="witMeta"></span></div>
      <div id="witBody"></div>
    </section>

    <section class="sec" aria-labelledby="hWhere">
      <h3 class="h-panel" id="hWhere">Where</h3>
      <dl class="kv where">
        ${(a.where || []).map((w) => `<dt>${esc(w.k)}</dt><dd>${esc(w.v)}${w.code ? `<span class="code">${esc(w.code)}</span>` : ""}</dd>`).join("")}
        ${a.tried && a.tried.length > 1 ? `<dt>Tried</dt><dd class="mono" style="font-size:var(--fs-xs)">${esc(a.tried.join(" > "))}</dd>` : ""}
      </dl>
    </section>

    ${items.length ? `<section class="sec" aria-labelledby="hItems">
      <div class="sec-head"><h3 class="h-panel" id="hItems">Items <span class="cite" style="margin-left:6px" aria-label="step ${itemsCite}">${itemsCite}</span></h3><span class="meta mono">${items.length}${a.items.length >= 200 ? "+" : ""}</span></div>
      <ul class="items" id="itemList">
        ${items.map((it, i) => {
          const [l, x] = itemLabel(it, a.query && a.query.obj, a.route);
          const n = (it && it.n) || i + 1;
          const can = !!itemRect(it, a, i) || (state.has3d && !!(it && it.guid));
          const tag = can ? "button" : "div";
          return `<li><${tag} class="item" ${can ? `type="button" data-i="${i}"` : ""}><span class="n">#${esc(n)}</span><span class="l" title="${esc(l)}">${esc(l)}</span><span class="x">${esc(x)}</span></${tag}></li>`;
        }).join("")}
      </ul>
    </section>` : ""}

    <section class="sec" aria-labelledby="hWhy">
      <h3 class="h-panel" id="hWhy">Why</h3>
      <ol class="why">
        ${why.map((w, i) => `<li><span class="cite">${i + 1}</span><span class="txt">${whyText(w)}</span></li>`).join("")}
        ${itemsCite ? `<li><span class="cite">${itemsCite}</span><span class="txt"><span class="kind">Items</span>${items.length} item${items.length > 1 ? "s" : ""} ${a.unit === "count" ? "counted" : "used"}, numbered on the drawing and in the strip under it.${checkItems(a).some((r) => r.rect) ? " Select one to find it." : ""}</span></li>` : ""}
        <li><span class="cite">${(itemsCite || nWhy) + 1}</span><span class="txt"><span class="kind">Status</span>${esc(a.status_rule || "")}</span></li>
      </ol>
      ${a.no_overlay_reason ? `<p class="note">${esc(a.no_overlay_reason)}</p>` : ""}
    </section>


    <div class="actions">
      <button class="btn" type="button" id="copyCsv">${icon("copy")}Copy as CSV row</button>
      <button class="btn btn-quiet" type="button" id="dlCsv">${icon("download")}Download CSV</button>
      <button class="btn btn-quiet" type="button" id="reask" title="Run the routes again instead of using the cached answer">${icon("refresh")}Run again</button>
    </div>

    <details class="sec order-fold" id="orderFold" hidden><summary class="h-panel">Order check</summary>
      <section id="orderSec" aria-label="Order check" hidden></section>
    </details>`;

  body.querySelectorAll("button.item").forEach((b) => {
    const it = items[+b.dataset.i];
    const n = (it && it.n) || +b.dataset.i + 1;
    b.addEventListener("mouseenter", () => markItem(b, it, a, n, false));
    b.addEventListener("focus", () => markItem(b, it, a, n, false));
    b.addEventListener("click", () => markItem(b, it, a, n, true));
  });
  $("#itemList")?.addEventListener("mouseleave", () => { if (!document.activeElement?.classList.contains("item")) clearFocus(); });
  $("#copyCsv").addEventListener("click", () => copyCsv(a));
  $("#dlCsv").addEventListener("click", () => downloadCsv(a));
  $("#reask").addEventListener("click", () => ask(true));
  const orderHooks = {
    show3d: state.has3d ? () => { setMode("3d"); } : null,
    changed: () => OrderSheet.mountPanel(a, $("#orderSec"), orderHooks),
  };
  if (window.OrderSheet) OrderSheet.mountPanel(a, $("#orderSec"), orderHooks);
  $("#orderFold").hidden = $("#orderSec").hidden;   // folded at the bottom: it works, but is not the main path (his call, 14 Nov)
  bindFinePrint();
  bindScaleCtl(a);                        // scale-assumption block
}

/* the fine print (CPU time, model calls, cost breakdown and price assumptions) sits behind a small (i) after the used
   line: hover or focus shows it, a click keeps it open (his request, 14 Nov: a lighter answer panel) */
function bindFinePrint() {
  const btn = $("#fineBtn"), pop = $("#finePrint");
  if (!btn || !pop) return;
  let pinned = false, hideT = null;
  const set = (on) => { clearTimeout(hideT); pop.hidden = !on; btn.setAttribute("aria-expanded", String(on)); };
  const later = () => { clearTimeout(hideT); if (!pinned) hideT = setTimeout(() => set(false), 150); };
  btn.addEventListener("click", () => { pinned = !pinned || pop.hidden; set(pinned); });
  btn.addEventListener("mouseenter", () => set(true));
  btn.addEventListener("mouseleave", later);
  btn.addEventListener("focus", () => set(true));
  btn.addEventListener("blur", later);
  pop.addEventListener("mouseenter", () => clearTimeout(hideT));
  pop.addEventListener("mouseleave", later);
  btn.addEventListener("keydown", (e) => { if (e.key === "Escape" && !pop.hidden) { e.preventDefault(); e.stopPropagation(); pinned = false; set(false); } });
}

/* a cannot answer leads with one plain sentence; the technical reason stays behind Details (his request, 14 Nov).
   Known reasons are put in plain words; anything else is the first part of the reason, without the route name. */
function plainReason(a) {
  const r = String(a.abstain || "");
  const raw = String(a.query?.obj || "").replace(/_or_/g, " or ").replace(/_/g, " ");
  const obj = raw ? (/s$/.test(raw) ? raw : raw.replace(/ or (\w+)$/, " or $1s").replace(/^(\w+)( or|$)/, "$1s$2").replace(/ss$/, "s")) : "items";
  const P = [
    [/question not understood|no measurable question/i, () => "Could not read this as one thing to count or measure on the drawing. Try asking for one thing, for example: How many doors are there?"],
    [/compound question|unresolved scope/i, () => "The question asks for several things at once. Ask for one thing at a time."],
    [/no raster method for|no method for/i, () => `Scanned plans cannot answer questions about ${obj} yet: there is no reliable way to find them on a scan.`],
    [/could not be measured|not measurable|no closed (?:room|outline)/i, () => `Could not measure the ${obj}: their outlines could not be traced on this plan, and no areas are printed for them.`],
    [/detector switched off for/i, () => `Could not count the ${obj}: the symbol finder is not reliable enough for them on scanned plans.`],
    [/(\d+) \w+ detected at score[^;]*?but (\d+) box/i, (m) => `Could not prove the count: ${m[1]} ${+m[1] === 1 ? raw : obj} found for sure, but ${m[2]} more ${+m[2] === 1 ? "is" : "are"} borderline and could go either way.`],
    [/only \d+ room labels? readable/i, () => "Could not read enough room names on this scan to prove the count."],
    [/label\(s\) read.*not reliable/i, () => `Could not prove the count: room names for ${obj} are read too unreliably on scans.`],
    [/not closed|open outline|outline is open/i, () => `Could not measure the ${obj}: their outline is not closed on this plan.`],
    [/no scale|scale not found|without a scale/i, () => "Could not measure: no scale was found on this drawing."],
    [/no text-layer rule|no schedule table|measure text not supported/i, () => "Nothing in this drawing's text or model answers this question."],
    [/not (?:in|found in) (?:the|this) (?:drawing|model|plan)|no \w+ found/i, () => `Could not find any ${obj} on this drawing to prove an answer.`],
  ];
  for (const [re, fn] of P) { const m = r.match(re); if (m) return fn(m); }
  let first = r.split(";")[0].replace(/^[\w+ -]{2,40}:\s+/, "").trim();
  if (!first) return "No route could prove an answer from this drawing.";
  if (first.length > 140) first = first.slice(0, 137).replace(/\s+\S*$/, "") + "...";
  return "Could not prove an answer: " + first.replace(/[.\s]+$/, "") + ".";
}

function shortReason(a) {
  const r = a.abstain || "No route could answer.";
  return r.length > 320 ? r.slice(0, 317) + "..." : r;
}

function whyText(w) {
  const kind = `<span class="kind">${esc(KIND[w.kind] || w.kind)}</span>`;
  if (w.kind === "check") {
    const pass = w.status === "pass";
    return `<span class="st ${pass ? "pass" : "fail"}">${icon(pass ? "proven" : "cannot")}${pass ? "Pass" : "Fail"}</span>${esc(w.text)}`;
  }
  return kind + esc(w.text);
}

/* ---------------------------------------------------------------- cost meter */
function fmtSec(s) { return s == null ? "-" : s < 1 ? Math.round(s * 1000) + " ms" : s.toFixed(1) + " s"; }
/* costs are computed from USD list prices and shown in euros at one stated rate (his request, 14 Nov) */
const USD_PER_EUR = 1.1052, FX_NOTE = "Prices converted from USD at the ECB reference rate of 13 Nov 2026 (1 EUR = 1.1052 USD).";
function fmtUsd(v) {
  if (v == null) return "-";
  const e = v / USD_PER_EUR;
  if (e === 0) return "€0";
  if (e < 0.01) return "€" + e.toPrecision(2);
  return "€" + e.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
/* log scale from $0.0001 to $1,000 per 1,000 questions */
function meterW(v) { return v <= 0 ? 0 : Math.max(2, Math.min(100, ((Math.log10(v) + 4) / 7) * 100)); }
function costMeter(a) {
  const c = a.compute;
  if (!c) return "";
  const base = state.costs?.baseline;
  const as = c.assumed || {};
  const row = (label, v, cls) => `<div class="meter-row ${cls}"><span class="ml">${label}</span><span class="mb"><i style="width:${meterW(v).toFixed(1)}%"></i></span><span class="mv mono">${fmtUsd(v)}</span></div>`;
  return `<div class="meter" role="group" aria-label="Cost per 1,000 questions">
    <span class="label">Cost per 1,000 questions like this</span>
    ${row("This answer", c.usd_per_1000, "ours")}
    ${base ? row("Nemotron, plain prompt", base.usd_per_1000, "base") : ""}
    ${c.parse_usd || c.parse_orig_usd ? `<p class="note parse-cost">Language model reading: ${fmtUsd(c.parse_orig_usd * 1000)} per 1,000${c.parse_cached ? " (cached now, counted as €0 for this repeat)" : " (included above)"}.</p>` : ""}
    <p class="note">CPU ${fmtSec(c.cpu_s)}${c.gpu_s ? `, GPU ${fmtSec(c.gpu_s)}` : ""}${c.model_usd ? `, model ${fmtUsd(c.model_usd)}` : ""} at an assumed $${as.cpu_vcpu_hour_usd} per vCPU-hour${c.gpu_s ? ` and $${as.gpu_hour_usd} per GPU-hour` : ""}.${base ? ` Nemotron: ${num(Math.round(base.prompt_tokens_mean + base.completion_tokens_mean))} measured tokens per question at an assumed $${base.price_in_per_mtok} / $${base.price_out_per_mtok} per million in / out.` : ""} ${FX_NOTE} Log scale.</p>
  </div>`;
}

/* BEGIN used-line: a plain "what was used" line on every answer, with the AI calls counted (his request, 14 Nov) */
const USED_PART = { "dxf-geometry": "CAD geometry", "dxf-generic": "CAD geometry", ifc: "IFC model", "ifc-props": "IFC model", "pdf-text": "PDF text", "pdf-vector": "PDF linework", "pdf-generic": "PDF linework", "raster-detector": "object detector", "raster-ocr-labels": "OCR (room labels)", "raster-area": "OCR (room labels)", "raster-cv": "object detector", "raster-cv-fixtures": "object detector", "raster-rooms": "room detector", "sheet-scale": "sheet scale", "nemotron-estimate": "Nemotron image estimate", nemotron: "Nemotron image estimate", "raster-prior": "baseline prior" };
/* the AI calls this answer made, one list for the used line and the Model calls row: a question reading by the language
   model counts whenever it was made and paid for (also when its reading was dropped and the answer is a cannot); a cached
   reading costs nothing and is not a call */
function aiCalls(a) {
  const ex = (a.query || {}).extra || {};
  const second = (a.checks || []).some((k) => k.name === "nemotron-second-opinion" && k.status !== "error");
  const imageEst = String(a.route || "").split("+").includes("nemotron-estimate") || second;
  const calls = [];
  if (!ex.parse_cached && (ex.parse_cost_orig_usd || ex.parse_cost_usd)) calls.push("question reading");
  if (imageEst) calls.push(second ? "Nemotron second opinion" : "Nemotron image estimate");
  return calls;
}
/* the Model calls row: "none" only when no language model was involved at all. A question read by the language model
   from its cache made no new call, but the reading came from a model, so the row says so (judge review 14 Nov) */
function modelCallsText(a) {
  const calls = aiCalls(a);
  if (calls.length) return `yes (${calls.length}: ${calls.join(", ")})`;
  const ex = (a.query || {}).extra || {};
  if (ex.parse_cached && (a.query?.parser === "llm" || ex.parse_cost_orig_usd || ex.parse_cost_usd)) return "none new (question reading by the language model, cached)";
  return "none";
}
function usedLine(a) {
  const q = a.query || {}, ex = q.extra || {}, c = a.compute || {};
  const llm = q.parser === "llm";
  const parts = [];
  const answered = a.value != null && !a.abstain;
  /* a cannot answer used nothing to answer with: list what was tried instead */
  const routes = answered ? String(a.route || "").split("+") : (a.tried && a.tried.length ? a.tried : String(a.route || "").split("+"));
  for (const r of routes) {
    const w = USED_PART[r] || ROUTE_LABEL[r] || r;
    if (w && !parts.includes(w)) parts.push(w);
  }
  const second = (a.checks || []).some((k) => k.name === "nemotron-second-opinion" && k.status !== "error");
  if (second && !parts.includes("Nemotron image estimate")) parts.push("Nemotron second opinion");
  const calls = aiCalls(a);
  const asked = !!(ex.parse_cost_orig_usd || ex.parse_cost_usd);     /* the reader was asked, also when its reading was not used */
  const callTxt = calls.length ? `${calls.length} (${calls.join(", ")})` : (asked && ex.parse_cached ? "none (question reading cached)" : "none");
  const per = c.usd_per_1000 != null ? `${fmtUsd(c.usd_per_1000)} per 1,000 questions like this` : "";
  const readBy = llm ? "language model" : (asked ? "rules (the language model was also asked; the rule reading was kept)" : "rules");
  return `<p class="used-line" id="usedLine"><span><b>Question read by:</b> ${readBy}</span><span><b>${answered ? "Answered with" : "Tried"}:</b> ${esc(answered ? (parts.join(" and ") || "-") : (parts.join(", ") || "-") + "; none could prove it")}</span><span><b>AI calls:</b> ${callTxt}</span>${per ? `<span>${per}</span>` : ""}<button type="button" class="info-btn" id="fineBtn" aria-expanded="false" aria-controls="finePrint" aria-label="Time and cost details" title="Time and cost details">i</button></p>`;
}
/* END used-line */

/* ---------------------------------------------------------------- witnesses */
const FMT_NAME = { DXF: "DXF layers", IFC: "IFC model", PDF: "PDF alone", PNG: "PNG image" };
let witT;
async function loadWitnesses(a, refresh = false) {
  const sec = $("#witSec");
  if (!sec || !state.drawing || !(state.drawing.twins || []).length || a.status === "cannot" && !a.query?.obj) return;
  sec.hidden = false;
  const t0 = performance.now();
  const meta = $("#witMeta");
  $("#witBody").innerHTML = `<p class="meta">Asking the same question of every format of this drawing, each read by its own route.</p>`;
  clearInterval(witT);
  witT = setInterval(() => { meta.textContent = `${((performance.now() - t0) / 1000).toFixed(1)} s`; }, 100);
  try {
    const ctl = (state.witCtl = new AbortController());
    const w = await api("/api/witnesses", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: a.path, question: a.question, refresh }),
      signal: ctl.signal,
    });
    if (state.answer !== a) return;
    state.witnesses = w;
    renderWitnesses(a, w);
  } catch (e) {
    if (e.name === "AbortError" || state.answer !== a) return;
    $("#witBody").innerHTML = `<p class="meta">Could not ask the other formats: ${esc(e.message)}</p>`;
  } finally {
    clearInterval(witT);
    meta.textContent = state.witnesses ? `${fmtSec(state.witnesses.time_ms / 1000)}${state.witnesses.cached ? ", cached" : ""}` : "";
  }
}
function witnessVerdict(w) {
  const v = w.majority_value != null ? `${fmtValue(w.majority_value)} ${fmtUnit(w.unit)}`.trim() : "";
  const odd = w.witnesses.filter((r) => r.agrees === false).map((r) => r.format);
  if (w.level === "all") return { tone: "ok", icon: "proven", text: `${w.n_answered} of ${w.n_formats} formats agree on ${v}` };
  if (w.level === "majority") return { tone: "warn", icon: "estimate", text: `${w.n_agree} of ${w.n_formats} formats agree on ${v}; ${odd.join(" and ")} ${odd.length > 1 ? "differ" : "differs"}` };
  if (w.level === "split") return { tone: "bad", icon: "cannot", text: `The formats disagree: check the circled values` };
  if (w.level === "single") return { tone: "warn", icon: "estimate", text: `Only one format could answer, so nothing corroborates it` };
  return { tone: "bad", icon: "cannot", text: "No format could answer" };
}
function renderWitnesses(a, w) {
  if (w.n_formats < 2) { $("#witSec").hidden = true; return; }
  const vd = witnessVerdict(w);
  let rel = "";
  if (w.majority_value != null && a.value != null && a.status !== "cannot" && w.n_agree > 1) {
    const same = a.unit === "count" ? a.value === w.majority_value : Math.abs(a.value - w.majority_value) <= Math.abs(w.majority_value) * 0.02;
    rel = same ? "The answer above is the agreed value." : "The answer above differs from the agreed value: check it.";
  }
  const rows = w.witnesses.map((r) => {
    const st = r.value == null ? "none" : r.tier === "proven" ? "proven" : "estimate";
    const vtxt = r.value == null ? "no answer" : `${fmtValue(r.value)}${r.unit && r.unit !== "count" ? " " + fmtUnit(r.unit) : ""}`;
    const ring = r.agrees === false || (w.level === "split" && r.value != null) ? " ring" : "";
    const tip = r.value == null ? (r.abstain || "") : `${r.route_name || r.route}, ${r.n_items} items, ${fmtSec(r.wall_s)}`;
    const can = !!r.evidence_id;
    return `<li><button type="button" class="wit-row" data-f="${esc(r.format)}" ${can ? "" : "disabled"} aria-pressed="false" title="${esc(tip)}">
      <span class="wf">${esc(r.format)}</span>
      <span class="wr"><span>${esc(FMT_NAME[r.format] || r.format)}</span><span class="meta">${esc(r.route_name || r.route || "")}</span></span>
      <span class="wv mono${ring}${r.value == null ? " na" : ""}">${esc(vtxt)}</span>
      <span class="wt ${st}" aria-label="${st === "none" ? "abstained" : st}">${st === "none" ? "" : icon(st === "proven" ? "proven" : "estimate")}</span>
    </button></li>`;
  }).join("");
  const paged = w.witnesses.filter((r) => (r.disputed || []).length).map((r) => r.format);
  const disLine = w.n_disputed ? `<p class="note dis-note"><span class="dis-key" aria-hidden="true"></span>${w.n_disputed} item${w.n_disputed > 1 ? "s" : ""} where ${esc(paged.join(" and "))} disagree on the same page ${w.n_disputed > 1 ? "are" : "is"} circled in their views. Select ${esc(paged.join(" or "))}.</p>` : "";
  $("#witBody").innerHTML = `
    <p class="wit-verdict ${vd.tone}">${icon(vd.icon)}<span>${esc(vd.text)}</span></p>
    <ul class="wit-list">${rows}</ul>
    ${disLine}
    <p class="note">${rel ? esc(rel) + " " : ""}Each format is read on its own, twins hidden, no model calls. Select one to see its evidence; a value boxed in amber disagrees with the others.</p>`;
  $("#witBody").querySelectorAll(".wit-row:not([disabled])").forEach((b) => b.addEventListener("click", () => {
    const r = w.witnesses.find((x) => x.format === b.dataset.f);
    if (state.shown && state.shown.format === r.format && state.shown !== state.answer) { showEvidence(state.answer, true); return; }
    showEvidence({ ...r, status: r.tier === "proven" ? "proven" : "estimate", query: a.query, why: [] }, true);
  }));
}

/* ---------------------------------------------------------------- results: cost and witnesses */
async function loadCosts() {
  let c;
  try { c = await api("/api/costs"); } catch (e) { return; }
  state.costs = c;
  const el = $("#costTable");
  if (!el) return;
  /* no measured routes in this build (out/cost/cost_by_route.json missing): hide the panel, as the witness panel does,
     instead of a table with only the baseline row and a note about measurements that are not there */
  const panel = el.closest(".panel");
  if (panel) panel.hidden = !(c.routes || []).length;
  if (!(c.routes || []).length) return;
  const as = c.assumed || {};
  const head = `<thead><tr><th>Route</th><th class="num">Questions</th><th class="num">Answered</th><th class="num">Median wall</th><th class="num">p90 wall</th><th class="num">Cold median</th><th class="num">CPU s, mean</th><th class="num">€ per 1,000, CPU</th><th class="num">€ per 1,000, detector on a GPU</th></tr></thead>`;
  const line = (r, cls = "") => `<tr class="${cls}"><td>${esc(r.label || ROUTE_LABEL[r.route] || r.route)}${r.route && r.route !== "all" ? ` <span class="mono dim">${esc(r.route)}</span>` : ""}</td><td class="num">${num(r.n)}</td><td class="num">${num(r.answered)}</td>
    <td class="num">${fmtSec(r.wall_median_s)}</td><td class="num">${fmtSec(r.wall_p90_s)}</td><td class="num">${fmtSec(r.cold_median_s)}</td>
    <td class="num">${r.cpu_mean_s == null ? "-" : r.cpu_mean_s.toFixed(2)}</td><td class="num">${fmtUsd(r.usd_per_1000_cpu)}</td><td class="num">${r.route === "raster-detector" ? fmtUsd(r.usd_per_1000_if_gpu) : "-"}</td></tr>`;
  let body = (c.routes || []).map((r) => line(r)).join("");
  if (c.all) body += line({ ...c.all, route: "all", label: "All sampled questions" }, "total");
  const b = c.baseline;
  if (b) body += `<tr class="base"><td>Nemotron, plain prompt <span class="dim">baseline</span></td><td class="num">${num(b.n)}</td><td class="num">-</td><td class="num">${fmtSec(b.latency_median_s)}</td><td class="num">-</td><td class="num">-</td><td class="num">-</td><td class="num" colspan="2">${fmtUsd(b.usd_per_1000)} at measured tokens</td></tr>`;
  el.innerHTML = head + "<tbody>" + (body || `<tr><td colspan="9" class="dim">${esc(c.missing || "No measurements yet.")}</td></tr>`) + "</tbody>";
  $("#costNote").textContent = `Assumed prices, not quotes: $${as.cpu_vcpu_hour_usd} per vCPU-hour, $${as.gpu_hour_usd} per GPU-hour (L4 or A10G class)${b ? `, and $${b.price_in_per_mtok} / $${b.price_out_per_mtok} per million input / output tokens for Nemotron, applied to its measured ${num(Math.round(b.prompt_tokens_mean))} input and ${num(Math.round(b.completion_tokens_mean))} output tokens per question (${b.source})` : ""}. `
    + (c.questions ? `Measured on ${num(c.questions)} ${esc(c.split || "")} questions of the ${esc(datasetName(c.data))}, ${esc(c.machine?.device || "")}, evidence rendering on, Nemotron off . CPU seconds count every thread; a cold question is the first one on its drawing; a question every route abstained on is booked under the first route tried.` : "") + " " + FX_NOTE;
}
const ROUTE_LABEL = { "dxf-geometry": "DXF geometry", "dxf-generic": "DXF layers, generic", ifc: "IFC model", "ifc-props": "IFC properties", "pdf-text": "PDF text layer", "pdf-vector": "PDF linework", "pdf-generic": "PDF, convention-free", "raster-detector": "Scan, symbol detector", "raster-ocr-labels": "Scan, room labels", "raster-cv": "Scan, panel analysis", "raster-cv-fixtures": "Scan, outlines", "raster-area": "Scan, printed areas" };
function datasetName(p) { return /preview/.test(p || "") ? "Stoa preview set" : /junction-dataset/.test(p || "") ? "Stoa full set" : (p || "dataset"); }

async function loadWitnessSummary() {
  let w;
  try { w = await api("/api/witness_summary"); } catch (e) { return; }
  const el = $("#witTable");
  if (!el) return;
  if (w.missing) { const panel = el.closest(".panel"); if (panel) panel.hidden = true; return; }   // no run yet: hide, no dev paths
  const head = `<thead><tr><th>Witnesses</th><th class="num">Questions</th><th class="num">Share</th><th class="num">Router answer correct</th><th class="num">Agreed value correct</th></tr></thead>`;
  const body = w.rows.map((r) => `<tr class="${r.level === "total" ? "total" : ""}"><td>${esc(r.label)}</td><td class="num">${num(r.n)}</td><td class="num">${pct(r.share)}</td>
    <td class="num">${r.router_correct == null ? "-" : `${pct(r.router_correct)} <span class="dim">(${r.router_correct_n})</span>`}</td>
    <td class="num">${r.majority_correct == null ? "-" : `${pct(r.majority_correct)} <span class="dim">(${r.majority_correct_n})</span>`}</td></tr>`).join("");
  const wr = (w.witness_rows || []).map((r) => `<tr><td>${esc(r.label)}</td><td class="num">${num(r.n)}</td><td class="num">${pct(r.correct)} <span class="dim">(${r.correct_n})</span></td>
    <td class="wrap dim">${Object.entries(r.by_format).map(([f, v]) => `${f} ${v.correct} of ${v.n}`).join(", ")}</td></tr>`).join("");
  el.innerHTML = head + "<tbody>" + body + "</tbody>";
  $("#witTable2").innerHTML = wr ? `<thead><tr><th>Each witness answer</th><th class="num">Answers</th><th class="num">Correct</th><th>By format</th></tr></thead><tbody>${wr}</tbody>` : "";
  $("#witNote").textContent = w.note || "";
}

/* ---------------------------------------------------------------- CSV */
const CSV_COLS = ["drawing", "question", "value", "unit", "status", "route", "confidence", "checks_passed", "checks_failed", "cost_eur", "response_ms", "evidence", "scale_assumption"];
function csvRow(a) {
  const checks = a.checks || [];
  const vals = {
    drawing: a.path, question: a.question, value: a.status === "cannot" ? "" : a.value, unit: a.unit,
    status: a.status === "cannot" ? "cannot prove: " + (a.abstain || "") : a.status, route: a.route,
    confidence: a.confidence, checks_passed: checks.filter((c) => c.status === "pass").length,
    checks_failed: checks.filter((c) => c.status === "fail").length, cost_eur: (Number(a.cost_usd || 0) / USD_PER_EUR).toFixed(4),
    response_ms: Math.round(a.time_ms), evidence: a.evidence_id ? location.origin + "/api/evidence/" + a.evidence_id + ".png" : "",
    scale_assumption: assumeLabel(a),     // scale-assumption block: the size an approximation used, if not the default
  };
  const cell = (v) => { const s = String(v ?? ""); return /[",\n;]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
  return CSV_COLS.map((c) => cell(vals[c])).join(",");
}
async function copyCsv(a) {
  const text = csvRow(a);
  try { await navigator.clipboard.writeText(text); toast("Copied one CSV row"); }
  catch (_) {
    const ta = document.createElement("textarea");
    ta.value = text; document.body.appendChild(ta); ta.select();
    const ok = document.execCommand("copy"); ta.remove();
    toast(ok ? "Copied one CSV row" : "Copy is blocked here; use Download CSV", !ok);
  }
}
function saveCsv(text, filename) {
  const blob = new Blob([text], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function downloadCsv(a) {
  saveCsv(CSV_COLS.join(",") + "\n" + csvRow(a) + "\n", (a.drawing || "answer").replace(/\.[^.]+$/, "") + "_answer.csv");
}

/* ================================================================ BEGIN scale-assumption (app.js)
   The size behind an approximation, editable after the answer (an idea from Taavi, Stoa's CPO): "Scale from 7 doors,
   assumed [0.84] m wide" with a slider, or "1:[200] on [A2]" on a big sheet. A change rescales the answer at once in
   the browser (areas x k^2, lengths x k, k = new metres per pixel / used), then, 600 ms after the last change, the
   server measures again with the assumption ({assume} on /api/ask) and its answer replaces the preview. With doors
   and a WC pan the scale is the median of all candidates, each with its own size, so the preview recomputes that
   median from the per-object candidates in the answer instead of scaling by the door width alone. The preview can
   still differ from the recompute: the server may pick another wall-gap setting for the new scale. */
const SCALE_UI = {
  door_m: { one: "door", many: "doors", dim: "wide", what: "door width", lo: 0.70, hi: 1.20 },
  wc_m: { one: "WC pan", many: "WC pans", dim: "long", what: "WC pan length", lo: 0.55, hi: 0.95 },
  bath_m: { one: "bathtub", many: "bathtubs", dim: "long", what: "bathtub length", lo: 1.40, hi: 2.00 },
};
const scaleCtl = { base: null, vals: null, ticket: 0, timer: null, k: 1, req: 0 };
const AREA_KEYS = ["area_m2", "floor_m2", "gross_m2", "net_named_rooms_m2", "area"];
const LEN_KEYS = ["perimeter_m", "net_m", "doors_m", "baseboard_m", "waste_m", "order_m", "width_m",
  "gross_wall_m2", "net_wall_m2", "openings_m2", "walls_m2"];     // wall areas: perimeter x height, so x k

function scaleKind(a) {
  const s = a && a.approx && a.approx.assume;
  if (!s) return null;
  if (s.kind === "objects" && s.candidates && s.candidates.length) return "objects";
  return s.kind === "ratio" ? "ratio" : null;
}
function median(v) { const s = [...v].sort((x, y) => x - y), n = s.length; return n % 2 ? s[(n - 1) / 2] : (s[n / 2 - 1] + s[n / 2]) / 2; }
const R2 = (x) => Math.round(x * 100) / 100;
const sameVal = (x, y) => Math.abs(x - y) <= 1e-9;

/* the starting values: the sizes this answer used (default or the person's) */
function scaleVals(a) {
  const s = a.approx.assume;
  if (scaleKind(a) === "ratio") return { ratio: s.ratio, sheet: s.sheet };
  return { ...s.using };
}
function scaleIsDefault(base, vals) {
  const s = base.approx.assume;
  if (scaleKind(base) === "ratio") return vals.ratio === s.read_ratio && vals.sheet === s.read_sheet;
  return Object.keys(vals).every((k) => sameVal(vals[k], s.defaults[k]));
}
/* length factor k for the values in the box against the answer they were set on */
function scaleFactor(base, vals) {
  const s = base.approx.assume;
  if (scaleKind(base) === "ratio") {
    const ppm = s.long_px / s.sheets[vals.sheet] * 1000 / vals.ratio;
    return s.px_per_m / ppm;
  }
  const cls = { door: "door_m", toilet: "wc_m", bathtub: "bath_m" };
  const m = median(s.candidates.map((c) => (vals[cls[c.cls]] ?? c.metres) / c.px));
  return m / s.m_per_px;
}
/* a copy of the answer with every area x k^2 and every length x k; totals with waste are recomputed */
function rescaleAnswer(a, k) {
  const p = JSON.parse(JSON.stringify(a));
  p.preview = true;
  const scaleObj = (o) => {
    if (!o || typeof o !== "object") return;
    for (const key of AREA_KEYS) if (typeof o[key] === "number") o[key] = R2(o[key] * k * k);
    for (const key of LEN_KEYS) if (typeof o[key] === "number") o[key] = R2(o[key] * k);
  };
  (p.items || []).forEach((it) => { scaleObj(it); if (it && it.totals) scaleObj(it.totals); });
  (p.approx.rooms || []).forEach(scaleObj);
  const t = (p.items || []).find((it) => it && it.totals)?.totals;
  if (typeof p.value === "number") {
    if (t && t.walls_m2 != null) {                 // tiling: floor x k^2 + walls x k, then the waste on both
      t.net_m2 = R2((t.floor_m2 || 0) + (t.walls_m2 || 0));
      t.waste_m2 = R2(t.net_m2 * (t.waste_pct || 0) / 100);
      t.order_m2 = R2(t.net_m2 + t.waste_m2);
      p.value = /order/.test(t.value_is || "") ? t.order_m2 : t.net_m2;
    } else if (t && t.unit === "m") {             // baseboard: a length
      t.waste_m = R2((t.net_m || 0) * (t.waste_pct || 0) / 100);
      t.order_m = R2((t.net_m || 0) + t.waste_m);
      p.value = R2(a.value * k);
    } else p.value = R2(a.value * (a.unit === "m" ? k : k * k));
  }
  return p;
}
/* "door width 0.90 m (default 0.84 m)" for the CSV and the correction record; "" when nothing is assumed */
function assumeLabel(a) {
  if (!a || !a.approx || !a.approx.assume) return "";
  const s = a.approx.assume;
  const live = a === state.answer && scaleCtl.vals && scaleCtl.base && scaleCtl.base.path === a.path;
  const tail = a.preview ? " (preview)" : "";
  if (s.kind === "ratio") {
    const r = live ? scaleCtl.vals.ratio : s.ratio, sh = live ? scaleCtl.vals.sheet : s.sheet;
    return r !== s.read_ratio || sh !== s.read_sheet ? `ratio 1:${r} on ${sh} (read 1:${s.read_ratio} on ${s.read_sheet})${tail}` : "";
  }
  const use = (live ? scaleCtl.vals : s.using) || {};
  const mine = Object.keys(SCALE_UI).filter((k) => use[k] != null && !sameVal(use[k], s.defaults[k]));
  return mine.length ? mine.map((k) => `${SCALE_UI[k].what} ${use[k].toFixed(2)} m (default ${s.defaults[k].toFixed(2)} m)`).join("; ") + tail : "";
}

function scaleCtlHtml(a) {
  const kind = scaleKind(a);
  if (!kind) return "";
  const s = a.approx.assume, v = scaleVals(a);
  let rows;
  if (kind === "ratio") {
    rows = `<div class="sc-row"><label for="sc-ratio">Scale from the printed ratio 1:</label>
      <input class="sc-num sc-den" id="sc-ratio" type="number" inputmode="numeric" min="10" max="5000" step="1" value="${v.ratio}" data-k="ratio">
      <label for="sc-sheet">on</label>
      <select class="sc-sheet" id="sc-sheet" data-k="sheet">${Object.keys(s.sheets).map((n) => `<option${n === v.sheet ? " selected" : ""}>${n}</option>`).join("")}</select></div>`;
  } else {
    const keys = Object.keys(SCALE_UI).filter((k) => (s.counts || {})[k]);
    rows = keys.map((k, i) => {
      const u = SCALE_UI[k], n = s.counts[k];
      const lead = `${i === 0 ? "Scale from" : "and"} ${n} ${n > 1 ? u.many : u.one}, assumed`;
      return `<div class="sc-row"><label for="sc-${k}">${lead}</label>
        <input class="sc-num" id="sc-${k}" type="number" inputmode="decimal" min="${s.ranges[k][0]}" max="${s.ranges[k][1]}" step="0.01" value="${v[k].toFixed(2)}" data-k="${k}">
        <span>m ${u.dim}</span>
        <input class="sc-range" type="range" min="${u.lo.toFixed(2)}" max="${u.hi.toFixed(2)}" step="0.01" value="${v[k].toFixed(2)}" data-k="${k}" aria-label="${u.what[0].toUpperCase() + u.what.slice(1)} in metres"></div>`;
    }).join("");
  }
  return `<div class="scale-ctl" role="group" aria-label="Scale assumption">${rows}
    <p class="sc-status" id="scStatus" aria-live="polite"></p></div>`;
}

function scaleStatus(base, vals, phase) {
  const s = base.approx.assume, said = (base.query && base.query.extra && base.query.extra.assume_said) || {};
  let what;
  if (scaleKind(base) === "ratio") {
    what = scaleIsDefault(base, vals) ? `Using the scale read on the sheet, 1:${s.read_ratio} on ${s.read_sheet}`
      : `Using your scale 1:${vals.ratio} on ${vals.sheet} (read on the sheet 1:${s.read_ratio} on ${s.read_sheet})`;
  } else {
    const mine = Object.keys(vals).filter((k) => !sameVal(vals[k], s.defaults[k]));
    what = mine.length ? "Using " + mine.map((k) => `your ${SCALE_UI[k].what} ${vals[k].toFixed(2)} m${said[k] === vals[k] ? " from the question" : ""} (default ${s.defaults[k].toFixed(2)} m)`).join("; ")
      : "Using the default sizes";
  }
  const still = scaleKind(base) === "objects" ? notInMedian(base, vals) : [];
  if (still.length) what += `. The ${still.join(" and ")} ${still.length > 1 ? "are" : "is"} not one of the middle values of the median, so the scale does not move`;
  let tail = phase === "preview" ? `. Preview: areas x${(scaleCtl.k ** 2).toFixed(2)}, lengths x${scaleCtl.k.toFixed(2)}; measuring again on the drawing.`
    : phase === "busy" ? ". Measuring again on the drawing." : ".";
  if (!phase && !scaleIsDefault(base, vals)) {
    // against the default sizes (the rooms stay as traced, so every area moves by exactly this factor)
    const def = scaleKind(base) === "ratio" ? { ratio: s.read_ratio, sheet: s.read_sheet }
      : Object.fromEntries(Object.keys(vals).map((k) => [k, s.defaults[k]]));
    const kd = 1 / scaleFactor(base, def);
    // one size changed and the scale moved less than that size: the other objects hold the median back
    const changed = scaleKind(base) === "objects" ? Object.keys(vals).filter((k) => !sameVal(vals[k], s.defaults[k])) : [];
    const mixed = changed.length === 1 && Math.abs(kd - vals[changed[0]] / s.defaults[changed[0]]) > 0.002;
    tail = `: areas x${(kd * kd).toFixed(2)}, lengths x${kd.toFixed(2)} against the default` +
      (mixed ? ` (the scale is the median of all ${s.candidates.length} objects, so it moves less than one size alone)` : "") + ".";
  }
  return `${esc(what + tail)}${scaleIsDefault(base, vals) ? "" : ` <button class="btn btn-quiet sc-reset" type="button" id="scReset">Reset</button>`}`;
}

/* the classes set away from the default whose size does not move the median at all ("WC pan" with 7 doors) */
function notInMedian(base, vals) {
  const s = base.approx.assume, k = scaleFactor(base, vals);
  return Object.keys(vals).filter((key) => !sameVal(vals[key], s.defaults[key])
    && Math.abs(scaleFactor(base, { ...vals, [key]: s.defaults[key] }) - k) < 1e-9).map((key) => SCALE_UI[key].one);
}

/* only the numbers change while the person drags: the panel is not rebuilt, so the slider keeps the pointer */
function paintScaled(p, base) {
  const v = $("#ansValue");
  if (v) v.textContent = fmtValue(p.value);
  const items = answerItems(p);
  document.querySelectorAll("#itemList li").forEach((li, i) => {
    const x = li.querySelector(".x");
    if (x && items[i]) x.textContent = itemLabel(items[i], p.query && p.query.obj, p.route)[1];
  });
  const t = $("#approxText");
  if (t) t.textContent = p === base ? m2(base.approx.text || base.approx.sentence || "")
    : `About ${fmtValue(p.value)} ${fmtUnit(p.unit)} with the size below. The sentence, the drawing and the Steps card update when the new measurement arrives.`;
  if (window.OrderSheet && $("#orderSec")) {
    const hooks = { show3d: null, changed: () => OrderSheet.mountPanel(state.answer, $("#orderSec"), hooks) };
    OrderSheet.mountPanel(p, $("#orderSec"), hooks);
  }
}

function showScaled(base) {
  scaleCtl.k = scaleFactor(base, scaleCtl.vals);
  const p = Math.abs(scaleCtl.k - 1) < 1e-9 ? base : rescaleAnswer(base, scaleCtl.k);
  state.answer = p;
  paintScaled(p, base);
  $("#scStatus").innerHTML = scaleStatus(base, scaleCtl.vals, p === base ? "" : "preview");
  bindReset();
}

function onScaleInput(e) {
  const el = e.target, key = el.dataset.k, base = scaleCtl.base;
  if (!key || !base) return;
  let val = key === "sheet" ? el.value : Number(el.value);
  if (key !== "sheet") {
    if (el.value === "" || !Number.isFinite(val)) return;
    const [lo, hi] = key === "ratio" ? [10, 5000] : base.approx.assume.ranges[key];
    if (val < lo || val > hi) { el.setAttribute("aria-invalid", "true"); return; }
    el.removeAttribute("aria-invalid");
    val = key === "ratio" ? Math.round(val) : R2(val);
    document.querySelectorAll(`.scale-ctl [data-k="${key}"]`).forEach((o) => { if (o !== el) o.value = key === "ratio" ? val : val.toFixed(2); });
  }
  scaleCtl.vals = { ...scaleCtl.vals, [key]: val };
  showScaled(base);
  clearTimeout(scaleCtl.timer);
  scaleCtl.timer = setTimeout(recomputeScale, 600);
}

/* the request's assumption: null asks the plain (cached) answer when nothing is assumed anywhere */
function assumeBody(base, vals) {
  const said = base.query && base.query.extra && base.query.extra.assume_said;
  return scaleIsDefault(base, vals) && !said ? null : vals;
}

async function recomputeScale() {
  const base = scaleCtl.base, vals = scaleCtl.vals;
  if (!base || !vals) return;
  const t = ++scaleCtl.ticket, req = ++scaleCtl.req;
  const st = $("#scStatus");
  if (st) { st.innerHTML = scaleStatus(base, vals, Math.abs(scaleCtl.k - 1) < 1e-9 ? "busy" : "preview"); bindReset(); }
  $("#busyText").textContent = "Measuring again with your size";
  $("#viewerBusy").hidden = false;
  try {
    const a2 = await api("/api/ask", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: base.path, question: base.question, pdf_only: $("#pdfOnly").checked, assume: assumeBody(base, vals) }),
    });
    if (t !== scaleCtl.ticket || !state.drawing || state.drawing.path !== base.path || scaleCtl.base !== base) return;
    if (a2.status === "cannot" || !scaleKind(a2)) {
      toast("No answer with that size: " + shortReason(a2) + " Back to the size before.", true);
      state.answer = base;
      renderAnswer(base);
      return;
    }
    const f = document.activeElement && document.activeElement.closest && document.activeElement.closest(".scale-ctl") ? document.activeElement : null;
    const focusSel = f ? (f.classList.contains("sc-range") ? `.sc-range[data-k="${f.dataset.k}"]` : f.id ? "#" + f.id : null) : null;
    state.answer = a2;
    state.corr = { removed: new Set(), added: [] };
    renderAnswer(a2);
    if (a2.evidence_id) await showEvidence(a2, false);
    if (focusSel) $(focusSel)?.focus();
  } catch (e) {
    if (t === scaleCtl.ticket) toast("Could not measure again: " + e.message, true);
  } finally {
    if (req === scaleCtl.req) $("#viewerBusy").hidden = true;      // the latest request ends the busy state
  }
}

function bindReset() {
  $("#scReset")?.addEventListener("click", () => {
    const base = scaleCtl.base, s = base.approx.assume;
    const def = scaleKind(base) === "ratio" ? { ratio: s.read_ratio, sheet: s.read_sheet }
      : Object.fromEntries(Object.keys(scaleCtl.vals).map((k) => [k, s.defaults[k]]));
    for (const [k, v] of Object.entries(def)) {
      document.querySelectorAll(`.scale-ctl [data-k="${k}"]`).forEach((o) => { o.value = typeof v === "number" && k !== "ratio" ? v.toFixed(2) : v; });
    }
    scaleCtl.vals = def;
    showScaled(base);
    clearTimeout(scaleCtl.timer);
    recomputeScale();
  });
}

/* called at the end of renderAnswer: the answer just drawn is the base the next change scales from */
function bindScaleCtl(a) {
  clearTimeout(scaleCtl.timer);
  scaleCtl.ticket++;                    // a recompute still on its way belongs to the answer before
  if (!scaleKind(a)) { scaleCtl.base = null; return; }
  scaleCtl.base = a;
  scaleCtl.vals = scaleVals(a);
  scaleCtl.k = 1;
  const box = document.querySelector(".scale-ctl");
  if (!box) return;
  $("#scStatus").innerHTML = scaleStatus(a, scaleCtl.vals, "");
  bindReset();
  box.querySelectorAll("input, select").forEach((el) => el.addEventListener("input", onScaleInput));
  box.querySelectorAll(".sc-num").forEach((el) => el.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); clearTimeout(scaleCtl.timer); recomputeScale(); }
  }));
  // the CSV buttons follow the value on screen (preview included), not the answer the panel was drawn with
  for (const [id, fn] of [["copyCsv", copyCsv], ["dlCsv", downloadCsv]]) {
    const b = $("#" + id);
    if (!b) continue;
    const nb = b.cloneNode(true);
    b.replaceWith(nb);
    nb.addEventListener("click", () => fn(state.answer));
  }
}
/* END scale-assumption (app.js) ================================================================ */

/* ================================================================ BEGIN ask-several (app.js)
   One question across many drawings. Nothing here touches the single-drawing path except five one-line hooks
   (the submit handler, ask(), selectDrawing(), drawItem(), upload()). The answers come from the ordinary /api/ask,
   one drawing at a time: the server solves one question at a time anyway (SOLVE_LOCK), and one at a time is gentle.
   An answer already in the server's cache returns in about a second, so opening a row afterwards costs nothing.
   Cannot-prove rows and failed requests are listed but never added to a total. */
const BATCH_MAX = 40;
const batch = {
  on: false,                 // the "Ask several" toggle
  sel: new Map(),            // path -> { name, ext }, in the order picked
  anchor: null,              // last picked path, for Shift-click ranges
  results: null,             // { question, pdf_only, rows: [{ path, name, st, a, err }], stop, seq }
};
let batchSeq = 0;
const batchActive = () => batch.on && batch.sel.size >= 2;
const plural = (n, w) => !w || n === 1 ? w : /[^aeiou]y$/.test(w) ? w.slice(0, -1) + "ies" : /(s|x|ch|sh)$/.test(w) ? w + "es" : w + "s";
const nDrawings = (n) => `${n} drawing${n === 1 ? "" : "s"}`;

/* the selection keeps the file name, its readable title if it has one, and never an empty name (upload chips were blank) */
const baseName = (p) => String(p || "").split(/[\/]/).pop();
function pickInfo(x) { return { name: x.dataset.name || baseName(x.dataset.path), ext: x.dataset.ext || "", title: x.dataset.title || "" }; }
const pickLabel = (path, d) => (d && (d.title || d.name)) || baseName(path);
function paintPick(b) {
  const on = batch.on && batch.sel.has(b.dataset.path);
  if (batch.on) { b.setAttribute("role", "checkbox"); b.setAttribute("aria-checked", String(on)); }
  else { b.removeAttribute("role"); b.removeAttribute("aria-checked"); }
  b.classList.toggle("picked", on);
}
function batchChanged() {
  $("#picker").classList.toggle("multi", batch.on);
  document.querySelectorAll(".draw-item").forEach(paintPick);
  $("#multiToggle").setAttribute("aria-pressed", String(batch.on));
  $("#multiBar").hidden = !batch.on;
  const n = batch.sel.size;
  $("#multiCount").textContent = n ? `${n} selected` : "None selected";
  $("#multiList").innerHTML = [...batch.sel].map(([p, d]) =>
    `<li><span class="nm${d.title ? " titled" : ""}" title="${esc(d.name || baseName(p))}${d.title ? "" : "\n" + esc(p)}">${esc(pickLabel(p, d))}</span><button type="button" class="x" data-path="${esc(p)}" aria-label="Remove ${esc(pickLabel(p, d))} from the selection">×</button></li>`).join("");
  const note = $("#multiNote");
  note.hidden = !batch.on;
  note.textContent = n >= 2 ? `Asks all ${n} selected drawings, one after another.` : n === 1 ? "One drawing selected. Select another to ask across several." : "No drawings selected. Tick two or more in the library.";
  $("#askBtn").textContent = batchActive() ? `Ask ${n} drawings` : "Ask";
}
function batchPick(path, info, on) {
  if (on) {
    if (!batch.sel.has(path) && batch.sel.size >= BATCH_MAX) { toast(`Up to ${BATCH_MAX} drawings at a time, to go easy on the server`, true); return false; }
    batch.sel.set(path, info);
  } else batch.sel.delete(path);
  return true;
}
function setMulti(on) {
  if (on === batch.on) return;
  batch.on = on;
  if (on) {
    if (state.drawing) batchPick(state.drawing.path, { name: state.drawing.name || baseName(state.drawing.path), ext: state.drawing.ext,
      title: state.drawing.title && state.drawing.title !== state.drawing.name ? state.drawing.title : "" }, true);
    batch.anchor = state.drawing ? state.drawing.path : null;
  } else {
    if (batch.results) batch.results.stop = true;
    batch.sel.clear(); batch.results = null; batch.anchor = null;
    leaveBatchView();
    $("#askBtn").disabled = false;
    $("#askBtn").removeAttribute("aria-busy");
  }
  batchChanged();
}
function pickOrOpen(b, e) {
  const path = b.dataset.path, info = pickInfo(b);
  const withKey = e.ctrlKey || e.metaKey || e.shiftKey;
  if (!batch.on && !withKey) { selectDrawing(path); return; }
  if (!batch.on) setMulti(true);
  const items = [...document.querySelectorAll(".draw-item")].filter((x) => x.offsetParent !== null);
  const from = items.findIndex((x) => x.dataset.path === batch.anchor), to = items.indexOf(b);
  if (e.shiftKey && from >= 0 && to >= 0) {
    for (const x of items.slice(Math.min(from, to), Math.max(from, to) + 1)) if (!batchPick(x.dataset.path, pickInfo(x), true)) break;
  } else {
    batchPick(path, info, !batch.sel.has(path));
  }
  batch.anchor = path;
  batchChanged();
}
function batchAddUploaded(res) {
  if (!batch.on) return;
  const list = Array.isArray(res.drawings) && res.drawings.length ? res.drawings : [{ path: res.path, name: res.name, ext: res.ext }];
  for (const d of list) if (!batchPick(d.path, { name: d.name || baseName(d.path), ext: d.ext, title: "" }, true)) break;
  batchChanged();
}
$("#multiToggle").addEventListener("click", () => setMulti(!batch.on));
$("#multiClear").addEventListener("click", () => { batch.sel.clear(); batch.anchor = null; batchChanged(); });
$("#multiShown").addEventListener("click", () => {
  const shown = [...document.querySelectorAll(".draw-item")].filter((x) => x.offsetParent !== null);
  /* every group folded (the first visit starts so): say what to do instead of silently selecting nothing (QA 14 Nov) */
  if (!shown.length) { toast("No drawings are listed yet. Open a group or search, then Select shown picks the listed ones.", true); return; }
  for (const x of shown) if (!batchPick(x.dataset.path, pickInfo(x), true)) break;
  batchChanged();
});
$("#multiList").addEventListener("click", (e) => {
  const x = e.target.closest("button.x");
  if (x) { batch.sel.delete(x.dataset.path); batchChanged(); }
});

/* ---- running the question ---- */
async function askMany() {
  const q = $("#question").value.trim();
  if (!q) { $("#question").focus(); return; }
  const run = { question: q, pdf_only: $("#pdfOnly").checked, stop: false, seq: ++batchSeq,
    rows: [...batch.sel].map(([path, d]) => ({ path, name: d.name || baseName(path), title: d.title || "", st: "wait" })) };
  batch.results = run;
  showBatchView();
  const btn = $("#askBtn");
  btn.disabled = true; btn.setAttribute("aria-busy", "true");
  renderBatch();
  for (const row of run.rows) {
    if (run.stop || batch.results !== run) break;
    row.st = "run"; renderBatch();
    try {
      row.a = await api("/api/ask", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: row.path, question: q, pdf_only: run.pdf_only, refresh: false }),
      });
      row.st = "done";
    } catch (e) { row.st = "fail"; row.err = e.message; }
    if (batch.results === run) renderBatch();
  }
  if (batch.results !== run) return;           // cleared or left while it ran
  for (const row of run.rows) if (row.st === "wait" || row.st === "run") row.st = "skip";
  run.finished = true;
  btn.disabled = false; btn.removeAttribute("aria-busy");
  renderBatch();
}

function showBatchView() { $("#detail").classList.add("batch-on"); $("#detailBatch").hidden = false; $("#batchBack").hidden = true; }
function leaveBatchView() {
  $("#detail").classList.remove("batch-on");
  $("#detailBatch").hidden = true;
  const r = batch.results;
  $("#batchBack").hidden = !r;
  if (r) $("#batchBackBtn").textContent = `Back to the table for ${nDrawings(r.rows.length)}`;
}
$("#batchBackBtn").addEventListener("click", () => { if (batch.results) { showBatchView(); renderBatch(); } });

/* ---- what the table says ---- */
const ROW_STATUS = {
  proven: ["Proven", "proven"], estimate: ["Estimate", "estimate"], cannot: ["Cannot prove", "cannot"],
};
const SUMMABLE = { count: "", m2: "m²", m3: "m³", m: "m" };
function batchSummary(run) {
  const done = run.rows.filter((r) => r.st === "done" && r.a);
  const used = done.filter((r) => r.a.status !== "cannot" && r.a.value != null);
  const proven = used.filter((r) => r.a.status === "proven").length, est = used.length - proven;
  const left = run.rows.filter((r) => !used.includes(r) && (r.st === "done" || r.st === "fail")).length;
  const pending = run.rows.filter((r) => r.st === "wait" || r.st === "run").length;
  const out = { used, proven, est, left, pending, total: null, why: "" };
  if (!used.length) { out.why = run.finished ? "No total: no drawing could be answered." : ""; return out; }
  const units = new Set(used.map((r) => r.a.unit || ""));
  if (used.some((r) => typeof r.a.value !== "number" || !isFinite(r.a.value))) out.why = "No total: the answers are not numbers.";
  else if (units.size > 1) out.why = "No total: the drawings answered in different units.";
  else if (!(([...units][0]) in SUMMABLE)) out.why = `No total: ${[...units][0] || "this unit"} cannot be added up.`;
  else {
    out.unit = [...units][0];
    out.total = Math.round(used.reduce((s, r) => s + r.a.value, 0) * 100) / 100;
    const objs = new Set(used.map((r) => (r.a.query && r.a.query.obj) || ""));
    const obj = objs.size === 1 ? [...objs][0] : "";
    out.noun = out.unit === "count" && /^[a-z][a-z_ ]*$/.test(obj) ? plural(out.total, obj.replace(/_/g, " ")) : fmtUnit(out.unit);
    out.about = used.some((r) => r.a.approx);
  }
  return out;
}
function batchSentence(run, s) {
  if (s.total == null) return s.why;
  const parts = [];
  if (s.proven) parts.push(`${s.proven} proven`);
  if (s.est) parts.push(`${s.est} estimate`);
  let t = `${run.finished ? "Total" : "Total so far"} ${s.about ? "about " : ""}${fmtValue(s.total)}${s.noun ? " " + s.noun : ""}: ${parts.join(", ")}`;
  if (s.left) t += `; ${nDrawings(s.left)} could not be answered (not included)`;
  return t + ".";
}
function reasonText(r) {
  const t = r.st === "fail" ? r.err || "The request failed." : shortReason(r.a);
  return t.length > 180 ? t.slice(0, 177) + "..." : t;
}
function renderBatch() {
  const run = batch.results, box = $("#detailBatch");
  if (!run) { box.innerHTML = ""; return; }
  const n = run.rows.length, finished = run.rows.filter((r) => r.st === "done" || r.st === "fail").length;
  const cur = run.rows.find((r) => r.st === "run");
  const s = batchSummary(run);
  const rows = run.rows.map((r) => {
    const link = `<button type="button" class="rowlink${r.title ? "" : " mono"}" data-path="${esc(r.path)}" title="${esc(r.name)}\nOpen with its evidence">${esc(r.title || r.name)}</button>`;
    if (r.st === "wait") return `<tr class="dimrow"><td>${link}</td><td class="num">-</td><td></td><td class="dim">Waiting</td></tr>`;
    if (r.st === "run") return `<tr><td>${link}</td><td class="num">-</td><td></td><td class="dim">Reading...</td></tr>`;
    if (r.st === "skip") return `<tr class="dimrow"><td>${link}</td><td class="num">-</td><td></td><td class="dim">Not run</td></tr>`;
    if (r.st === "fail") return `<tr class="has-reason"><td>${link}</td><td class="num">-</td><td></td><td class="fail">Failed</td></tr><tr class="reason"><td colspan="4">${esc(reasonText(r))}</td></tr>`;
    const a = r.a, [label, cls] = ROW_STATUS[a.status] || ROW_STATUS.cannot;
    const hasVal = a.value != null && a.status !== "cannot";
    const val = hasVal ? `${a.approx ? '<span class="about">about </span>' : ""}${esc(fmtValue(a.value))}` : "-";
    const unit = hasVal ? esc(a.unit === "count" ? "count" : fmtUnit(a.unit)) : "";
    const main = `<tr${a.status === "cannot" ? ' class="has-reason"' : ""}><td>${link}</td><td class="num">${val}</td><td class="unit">${unit}</td><td class="status ${cls}">${label}</td></tr>`;
    return main + (a.status === "cannot" ? `<tr class="reason"><td colspan="4">${esc(reasonText(r))}</td></tr>` : "");
  }).join("");
  const totalRow = s.total == null ? "" :
    `<tr class="total"><td>${run.finished ? "Total" : "Total so far"}, ${s.used.length} of ${n} drawings</td><td class="num">${s.about ? '<span class="about">about </span>' : ""}${esc(fmtValue(s.total))}</td><td class="unit">${esc(s.unit === "count" ? "count" : fmtUnit(s.unit))}</td><td class="status ${s.est ? "estimate" : "proven"}">${s.est ? "Includes estimates" : "All proven"}</td></tr>`;
  const sentence = s.pending && !run.finished ? "" : batchSentence(run, s);
  const prog = `${finished} of ${n} done` + (run.finished ? (run.stop ? ", stopped" : "") : cur ? `, reading ${esc(cur.title || cur.name)}` : "");
  box.innerHTML = `
    <div class="ans-head">
      <h2 class="h-panel">Answers across ${esc(nDrawings(n))}</h2>
      <p class="detail-q">${esc(run.question)}</p>
      <div class="batch-prog" role="status">
        <p class="meta">${prog}</p>
        ${run.finished ? "" : `<div class="bar" aria-hidden="true"><span style="width:${Math.round((finished / n) * 100)}%"></span></div>
        <p class="meta">The first question on a new scan can take up to a minute or two while it is read; after that, about a second. CAD, PDF and IFC answer in about a second.</p>`}
      </div>
    </div>
    <section class="sec">
      <div class="table-wrap"><table class="tbl batch-tbl">
        <thead><tr><th>Drawing</th><th class="num">Value</th><th>Unit</th><th>Status</th></tr></thead>
        <tbody>${rows}${totalRow}</tbody>
      </table></div>
      ${sentence ? `<p class="batch-sum" id="batchSum">${esc(sentence)}</p>` : ""}
      <p class="meta">Click a drawing to open its own answer with the evidence and the steps. A drawing that cannot be proven is listed with its reason and is never added to the total.</p>
    </section>
    <div class="actions">
      ${run.finished ? `<button class="btn" type="button" id="batchCsv">${icon("download")}Download CSV</button>`
                     : `<button class="btn" type="button" id="batchStop">Stop</button>`}
      <button class="btn btn-quiet" type="button" id="batchLeave">Back to one drawing</button>
    </div>`;
}
$("#detailBatch").addEventListener("click", (e) => {
  const run = batch.results;
  if (!run) return;
  const link = e.target.closest(".rowlink");
  if (link) { openBatchRow(link.dataset.path); return; }
  if (e.target.closest("#batchStop")) { run.stop = true; e.target.closest("#batchStop").disabled = true; }
  else if (e.target.closest("#batchCsv")) downloadBatchCsv(run);
  else if (e.target.closest("#batchLeave")) setMulti(false);
});
async function openBatchRow(path) {
  const run = batch.results;
  if (!run.finished) { toast("Wait for the run to finish, or press Stop"); return; }
  leaveBatchView();
  await selectDrawing(path);                    // opens the drawing (and resets the panel to its empty state)
  $("#question").value = run.question;
  $("#pdfOnly").checked = run.pdf_only;
  ask();                                        // served from the server's answer cache, with evidence and steps
}

/* ---- CSV: the same columns and the same row writer as the single answer, one row per drawing, then the total ---- */
function downloadBatchCsv(run) {
  const s = batchSummary(run);
  const lines = [CSV_COLS.join(",")];
  for (const r of run.rows) {
    if (r.st === "done" && r.a) lines.push(csvRow(r.a));
    else lines.push(csvRow({ path: r.path, question: run.question, status: "cannot", abstain: r.st === "fail" ? "request failed: " + (r.err || "") : "not run", unit: "", time_ms: 0 }));
  }
  if (s.total != null) {
    lines.push(csvRow({ path: `total of ${s.used.length} of ${run.rows.length} drawings`, question: run.question, value: s.total, unit: s.unit,
      status: s.est ? `sum of ${s.proven} proven and ${s.est} estimate rows` : `sum of ${s.proven} proven rows`, time_ms: 0 }));
  }
  saveCsv(lines.join("\n") + "\n", `answers_${run.rows.length}_drawings.csv`);
}
/* END ask-several (app.js) ================================================================ */

/* ================================================================ results */
let runsLoaded = false, runData = null, measure = "accuracy";
async function loadRuns() {
  if (runsLoaded) return;
  try {
    const { runs } = await api("/api/results");
    const sel = $("#runSel");
    if (!runs.length) {
      $("#runHeadline").textContent = "No eval outputs found. Run eval.py; its out/<run>/summary.json files appear here.";
      return;
    }
    sel.innerHTML = runs.map((r) => `<option value="${esc(r.id)}">${esc(r.label || r.id)}</option>`).join("");
    sel.onchange = () => loadRun(sel.value);
    runsLoaded = true;
    loadRun(runs[0].id);
    loadOverview();
    loadBaseline();
    loadCosts();
    loadWitnessSummary();
  } catch (e) {
    $("#runHeadline").textContent = "Could not read results: " + e.message;
  }
}

async function loadRun(id) {
  try {
    runData = await api("/api/results/" + encodeURIComponent(id));
  } catch (e) { toast("Could not load run: " + e.message, true); return; }
  const d = runData;
  $("#runHeader").textContent = `${d.label || d.id}.`;
  if (d.header) $("#runHeader").title = d.header;
  $("#runHeader").title = `${d.where || "out"}/${d.id}/summary.json`;
  const all = d.rows.find((r) => r.source === "ALL" && r.type === "ALL");
  /* a run that did not record its dataset: "On the test split (dataset not recorded)", not "On the dataset not recorded" */
  const unknown = !d.dataset_name || /not recorded/i.test(d.dataset_name);
  const where = unknown ? (d.split ? `the <strong>${esc(d.split)}</strong> split (dataset not recorded)` : "an unrecorded dataset")
    : d.split ? `the <strong>${esc(d.dataset_name)}</strong>, <strong>${esc(d.split)}</strong> split` : `<strong>${esc(d.dataset_name)}</strong>`;
  const flags = d.flags && d.flags.length ? ` (${esc(d.flags.join("; "))})` : "";
  $("#runHeadline").innerHTML = all
    ? `On ${where}${flags}, <strong>${pct(all.accuracy)}</strong> of ${num(all.n)} questions were answered correctly. The system answered <strong>${pct(all.coverage)}</strong> of them and was right on <strong>${pct(all.precision)}</strong> of those${all.cost_per_q ? `, at <strong>${fmtUsd(all.cost_per_q)}</strong> per question` : ""}.`
    : "";
  renderSrcTable(d);
  renderChart(d);
  const srcs = [...new Set(d.objects.map((o) => o.source))];
  const os = $("#objSource");
  const prev = os.value;
  os.innerHTML = srcs.map((s) => `<option value="${esc(s)}">${esc(SOURCE_NAMES[s] || s)}</option>`).join("");
  if (srcs.includes(prev)) os.value = prev; else if (srcs.includes("cubicasa5k")) os.value = "cubicasa5k";
  os.onchange = () => renderObjTable(d);
  renderObjTable(d);
}

async function loadOverview() {
  let items = [];
  try { ({ items } = await api("/api/overview")); } catch (e) {
    $("#overviewTable").innerHTML = `<tbody><tr><td class="dim">Could not read: ${esc(e.message)}</td></tr></tbody>`;
    return;
  }
  const head = `<thead><tr><th>Test set</th><th class="num">Questions</th><th class="num">Accuracy</th><th class="num">Precision when answered</th><th>Detail</th></tr></thead>`;
  const body = items.map((it) => {
    if (it.missing) return `<tr><td>${esc(it.set)}</td><td colspan="4" class="dim">Not run in this build. The README states the last measured numbers.</td></tr>`;
    let detail = "";
    if (it.baseline_accuracy != null) detail = `Nemotron, plain prompt: ${pct(it.baseline_accuracy)}; router: ${esc(it.note || "")}`;
    else if (it.wrong != null) detail = `${it.correct} correct, ${it.wrong} wrong (${it.proven_wrong} of them proven), ${it.abstain} abstained, ${it.abstain_ok} correctly abstained`;
    else if (it.proven != null) detail = `proven ${num(it.proven)} at ${pct(it.proven_precision)}, estimate ${num(it.estimate)} at ${pct(it.estimate_precision)}`;
    return `<tr><td>${esc(it.set)}</td><td class="num">${num(it.n)}</td><td class="num">${pct(it.accuracy)}</td>
      <td class="num">${it.precision == null ? "-" : pct(it.precision)}</td><td class="wrap">${detail}</td></tr>`;
  }).join("");
  $("#overviewTable").innerHTML = head + "<tbody>" + body + "</tbody>";
  tileData.ov = items; renderTiles();
}

/* ---- headline tiles (results-totals, BM 14 Nov): the totals first, read from /api/overview and /api/baseline.
   The cost per 1,000 questions is the one number no endpoint serves: it is the blended figure measured in the README
   (route mix of the full test split plus the question reader at its 0.74% share), kept here with its source. */
const COST_BLENDED_USD_PER_1000 = 0.168;
const COST_SOURCE = "README, cost section: CPU time per route at an assumed $0.05 per vCPU-hour, weighted by the full test split's route mix, plus the question reader on the 0.74% of questions the rules flag";
const tileData = { ov: null, base: null };
function tileBar(v, cls = "") { return `<span class="tile-bar ${cls}" aria-hidden="true"><i style="width:${Math.max(0, Math.min(1, v || 0)) * 100}%"></i></span>`; }
function renderTiles() {
  const box = $("#resTiles");
  const items = tileData.ov || [], b = tileData.base;
  const run = items.find((it) => /^Stoa full set/.test(it.set) && !it.missing) || items.find((it) => /^Stoa preview set/.test(it.set) && !it.missing);
  const tiles = [];
  if (run) {
    tiles.push(`<div class="tile" title="${esc(run.source || "")}"><p class="tile-k">Correct, ${esc(run.set.replace(/^Stoa /, ""))}</p>
      <p class="tile-v">${pct(run.accuracy)}</p>${tileBar(run.accuracy)}<p class="tile-s">${num(run.n)} questions, abstentions counted as wrong</p></div>`);
    if (run.proven_precision != null) tiles.push(`<div class="tile" title="${esc(run.source || "")}"><p class="tile-k">Proven answers right</p>
      <p class="tile-v">${pct(run.proven_precision)}</p>${tileBar(run.proven_precision)}<p class="tile-s">${num(run.proven)} answers marked Proven on the same split</p></div>`);
  }
  tiles.push(`<div class="tile" title="${esc(COST_SOURCE)}"><p class="tile-k">Cost per 1,000 questions (estimate)</p>
    <p class="tile-v">about ${fmtUsd(COST_BLENDED_USD_PER_1000)}</p>
    <p class="tile-s">${b && b.cost_per_q ? `Nemotron alone: about ${fmtUsd(b.cost_per_q * 1000)} at its measured tokens` : "CPU time at an assumed cloud price"}</p></div>`);
  if (b) tiles.push(`<div class="tile tile-vs" title="${esc(b.ours_source || "")}"><p class="tile-k">Against a vision model alone</p>
    <div class="vs"><span class="vs-n">Ours</span>${tileBar(b.ours_accuracy)}<span class="vs-v">${pct(b.ours_accuracy)}</span>
      <span class="vs-n">Nemotron</span>${tileBar(b.nemo_accuracy, "dim")}<span class="vs-v dim">${pct(b.nemo_accuracy)}</span></div>
    <p class="tile-s">The same ${num(b.n)} test questions, scored the same way</p></div>`);
  box.innerHTML = tiles.join("");
}

async function loadBaseline() {
  let b;
  try { b = await api("/api/baseline"); } catch (e) { renderTiles(); return; }      // keeps the "not measured" text
  tileData.base = b; renderTiles();
  const rows = b.by_source.map((s) => `<tr><td>${esc(SOURCE_NAMES[s.source] || s.source)}, ${esc(s.type)}</td><td class="num">${s.n}</td>
    <td class="num">${s.nemo_correct}</td><td class="num">${s.ours_correct}</td></tr>`).join("");
  const price = b.price_assumption ? `an assumed ${b.price_assumption[0]} / ${b.price_assumption[1]} USD per million input / output tokens` : "an assumed list price";
  $("#baselineBody").innerHTML = `
    <p class="value-na">Nemotron ${pct(b.nemo_accuracy)} vs ours ${pct(b.ours_accuracy)}</p>
    <div class="table-wrap"><table class="tbl"><thead><tr><th>Questions</th><th class="num">n</th><th class="num">Nemotron</th><th class="num">Ours</th></tr></thead>
    <tbody>${rows}<tr class="total"><td>All</td><td class="num">${b.n}</td><td class="num">${b.nemo_correct}</td><td class="num">${b.ours_correct}</td></tr></tbody></table></div>
    <details class="fine-note"><summary>How this was measured</summary>
    <p class="meta">Sample: Stoa preview set, test split, ${num(b.n)} questions (${esc(b.sample)}). Nemotron got the drawing as one image and was asked for just the number: no regions, no checks. Median latency ${esc(b.latency_median_s)} s; about ${fmtUsd(b.cost_per_q || 0)} per question at ${esc(price)} (free on NVIDIA Build). Router: €0 in model calls, ${esc(b.ours_source)}. ${FX_NOTE}</p></details>`;
}

function renderSrcTable(d) {
  const costed = d.rows.some((r) => r.cost_per_q);       // a run with no model cost recorded: no column of €0 (the tiles carry the cost)
  const head = `<thead><tr><th>Source</th><th>Type</th><th class="num">n</th><th class="num">Accuracy</th><th class="num">Coverage</th><th class="num">Precision</th>${costed ? '<th class="num">€ per 1,000</th>' : ""}</tr></thead>`;
  const rows = [];
  const bySrc = {};
  for (const r of d.rows) (bySrc[r.source] = bySrc[r.source] || []).push(r);
  const order = Object.keys(bySrc).filter((s) => s !== "ALL");
  const line = (r, cls, first) => `<tr class="${cls}"><td>${first}</td><td>${r.type === "ALL" ? "all" : esc(r.type)}</td><td class="num">${num(r.n)}</td>
      <td class="num">${pct(r.accuracy)}</td><td class="num${r.answered ? "" : " dim"}">${pct(r.coverage)}</td>
      <td class="num${r.answered ? "" : " dim"}">${r.answered ? pct(r.precision) : "-"}</td>${costed ? `<td class="num">${fmtUsd((r.cost_per_q || 0) * 1000)}</td>` : ""}</tr>`;
  for (const s of order) {
    const rs = bySrc[s];
    const total = rs.find((r) => r.type === "ALL");
    const parts = rs.filter((r) => r.type !== "ALL");
    if (total) rows.push(line(total, "group", `<span class="src-nm">${esc(SOURCE_NAMES[s] || s)}</span>`));
    if (parts.length > 1 || !total) for (const r of parts) rows.push(line(r, "", ""));
  }
  if (bySrc.ALL) rows.push(line(bySrc.ALL[0], "total", "All sources"));
  $("#srcTable").innerHTML = head + "<tbody>" + rows.join("") + "</tbody>";
}

function renderChart(d) {
  const rows = d.rows.filter((r) => r.type === "ALL").sort((a, b) => (a.source === "ALL") - (b.source === "ALL") || b.n - a.n);
  const W = 460, rowH = 30, left = 116, right = 92, top = 8, H = top + rows.length * rowH + 22;
  const xw = W - left - right;
  const sx = (v) => left + v * xw;
  let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${measure} by source">`;
  for (const t of [0, 0.25, 0.5, 0.75, 1]) {
    s += `<line class="grid" x1="${sx(t)}" x2="${sx(t)}" y1="${top}" y2="${H - 20}"/>`;
    s += `<text class="tick" x="${sx(t)}" y="${H - 6}" text-anchor="middle">${t * 100}%</text>`;
  }
  rows.forEach((r, i) => {
    const y = top + i * rowH;
    const v = measure === "precision" ? (r.answered ? r.precision : null) : r[measure];
    const w = v == null ? 0 : Math.max(v * xw, v > 0 ? 2 : 0);
    const name = r.source === "ALL" ? "All sources" : SOURCE_NAMES[r.source] || r.source;
    const short = name.replace(/\s*\(.*\)\s*$/, "");       // "IFC-Bench (free-text ...)" ran over the next row at 1280 px
    s += `<g data-i="${i}">`;
    s += `<text class="lbl" x="${left - 10}" y="${y + rowH / 2 + 4}" text-anchor="end"${r.source === "ALL" ? ' style="font-weight:600;fill:var(--fg)"' : ""}><title>${esc(name)}</title>${esc(short)}</text>`;
    s += `<rect class="hit" x="0" y="${y}" width="${W}" height="${rowH}"/>`;
    s += `<rect class="bar" x="${left}" y="${y + 8}" width="${w}" height="${rowH - 16}" rx="2"/>`;
    s += `<text class="val" x="${left + w + 6}" y="${y + rowH / 2 + 4}">${v == null ? "none answered" : pct(v)}</text>`;
    s += `</g>`;
  });
  s += `<line class="axis" x1="${left}" x2="${left}" y1="${top}" y2="${H - 20}"/></svg>`;
  const el = $("#chart");
  el.innerHTML = s;
  $("#hChart").textContent = measure[0].toUpperCase() + measure.slice(1) + " by source";
  const tip = $("#chartTip");
  el.querySelectorAll("g[data-i]").forEach((g) => {
    const r = rows[+g.dataset.i];
    g.addEventListener("mousemove", (e) => {
      const box = el.closest(".panel").getBoundingClientRect();
      tip.hidden = false;
      tip.textContent = `${r.source === "ALL" ? "All sources" : SOURCE_NAMES[r.source] || r.source}\nn ${num(r.n)}   answered ${num(r.answered)}   correct ${num(r.correct)}\naccuracy ${pct(r.accuracy)}   coverage ${pct(r.coverage)}   precision ${r.answered ? pct(r.precision) : "-"}`;
      tip.style.left = Math.min(e.clientX - box.left + 12, box.width - 290) + "px";
      tip.style.top = e.clientY - box.top + 14 + "px";
      g.classList.add("on");
    });
    g.addEventListener("mouseleave", () => { tip.hidden = true; g.classList.remove("on"); });
  });
}
$("#measureSeg").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
  measure = b.dataset.m;
  $("#measureSeg").querySelectorAll("button").forEach((x) => x.setAttribute("aria-checked", String(x === b)));
  if (runData) renderChart(runData);
}));

function renderObjTable(d) {
  const src = $("#objSource").value;
  const rows = d.objects.filter((o) => o.source === src).sort((a, b) => b.accuracy - a.accuracy || b.n - a.n);
  const head = `<thead><tr><th>Object</th><th class="num">n</th><th>Accuracy</th><th class="num">Coverage</th><th class="num">Precision</th></tr></thead>`;
  const body = rows.map((o) => `<tr><td>${esc(o.object.replace(/_/g, " "))}</td><td class="num">${num(o.n)}</td>
    <td><span class="cellbar"><i><b style="width:${(o.accuracy * 100).toFixed(1)}%"></b></i><span class="mono">${pct(o.accuracy)}</span></span></td>
    <td class="num">${pct(o.coverage)}</td><td class="num${o.answered ? "" : " dim"}">${o.precision == null ? "-" : pct(o.precision)}</td></tr>`).join("");
  $("#objTable").innerHTML = head + "<tbody>" + (body || `<tr><td colspan="5" class="dim">No per-question file (answers.jsonl) for this run.</td></tr>`) + "</tbody>";
}

/* ================================================================ boot */
(async function boot() {
  if (window.matchMedia("(max-width: 760px)").matches) {
    $("#view-ask").classList.add("no-picker");      // phones start on the question, library one tap away
    $("#pickerToggle").setAttribute("aria-expanded", "false");
  }
  /* the question field shows the URL's question, never one the browser restored from the page before (QA 14 Nov) */
  const p = new URLSearchParams(location.hash.slice(1));
  $("#question").value = p.get("q") || "";
  renderExamples();
  renderTry();
  loadStatus();
  loadCosts();
  await loadDrawings();
  if (p.get("path")) {
    await selectDrawing(p.get("path"));
    if (p.get("mode") === "3d") setMode("3d");
    if (p.get("q")) { $("#question").value = p.get("q"); ask(); }
  }
  if (p.get("view") === "results") showView("results");
})();
/* a pasted link or back/forward in the same tab changes only the hash: load what it names (QA 14 Nov). The app's
   own URL updates use pushState / replaceState, which fire no hashchange, so this does not loop. */
window.addEventListener("hashchange", async () => {
  const p = new URLSearchParams(location.hash.slice(1));
  if (p.get("view") === "results") { showView("results"); return; }
  if (!p.get("path")) return;
  showView("ask");
  const moved = !state.drawing || state.drawing.path !== p.get("path");
  if (moved) await selectDrawing(p.get("path"));
  if (p.get("mode") === "3d") setMode("3d");
  if (p.get("q") && (moved || p.get("q") !== $("#question").value)) { $("#question").value = p.get("q"); ask(); }
  else if (!p.get("q") && !moved && state.askingQ) {
    // Back pressed while the question is still answering: drop it, as a drawing switch does (Mac QA 14 Nov)
    state.askTicket += 1;
    abortAsk();
    state.askingQ = null;
    clearInterval(busyT);
    $("#viewerBusy").hidden = true;
    $("#askBtn").disabled = false;
    $("#askBtn").removeAttribute("aria-busy");
    state.answer = null;
    renderAnswer(null);
    showDrawing();
    $("#question").value = "";
  }
  else if (!p.get("q") && !moved && state.answer && !state.askingQ) {
    // Back to the drawing before a question was asked: show it without that answer (Mac QA 14 Nov)
    state.answer = null;
    renderAnswer(null);
    showDrawing();
    $("#question").value = "";
  }
});



/* ================================================================ compare tab (block start)
   Prepared side-by-side examples and totals, read from compare.json (tools/build_compare.py). No live model calls.
   Left: the recorded Nemotron baseline answer. Right: our answer from a local run, with its evidence image and a button
   that opens the drawing and question in Ask (the full evidence is computed there, as for any question). */
let compareData = null;
const cmpNum = (v, unit) => v == null ? null : `${Number(v).toLocaleString("en-US", { maximumFractionDigits: 2 })}${unit && unit !== "count" ? " " + unit.replace("m2", "m\u00b2") : ""}`;
const cmpSec = (v) => v == null ? "-" : v < 1 ? `${Math.round(v * 1000)} ms` : v < 10 ? `${v.toFixed(1)} s` : `${Math.round(v)} s`;
const CMP_STATUS = { proven: "Proven", estimate: "Estimate, check the highlighted items", cannot: "Cannot prove" };

async function loadCompare() {
  const box = $("#compareBody");
  if (compareData) return;
  try {
    compareData = await api("compare.json");
  } catch (e) {
    box.innerHTML = `<p class="meta">The comparison data is missing (app/static/compare.json, made by tools/build_compare.py).</p>`;
    return;
  }
  /* results-compare (BM, 14 Nov): the page renders at once. It used to wait for an /api/drawing check per example
     first, which on a busy server (right after the tour's question) held the whole page on "Loading". The check now
     runs afterwards and only takes away "Open in Ask" for a drawing this library does not have. */
  box.innerHTML = compareTiles(compareData.totals) + compareData.examples.map((x, i) => compareExample(x, i, true)).join("") + compareTotals(compareData.totals);
  box.querySelectorAll("[data-open]").forEach((b) => b.addEventListener("click", () => compareOpen(compareData.examples[+b.dataset.open])));
  compareData.examples.forEach((x, i) => api("/api/drawing?path=" + encodeURIComponent(x.path)).catch(() => {
    const b = box.querySelector(`[data-open="${i}"]`);
    if (b) b.outerHTML = `<p class="meta">This drawing is not in this library.</p>`;
  }));
}

function compareTiles(t) {
  const b = t.bakeoff, c = t.cost;
  const vs = (k, ours, theirs, them, sub) => `<div class="tile tile-vs"><p class="tile-k">${k}</p>
    <div class="vs"><span class="vs-n">Ours</span>${tileBar(ours)}<span class="vs-v">${Math.round(ours * 100)}%</span>
      <span class="vs-n">${them}</span>${tileBar(theirs, "dim")}<span class="vs-v dim">${Math.round(theirs * 100)}%</span></div>
    <p class="tile-s">${sub}</p></div>`;
  return `<section class="tiles cmp-tiles" aria-label="Totals">
    ${vs("Accuracy", t.ours_accuracy, t.nemotron_accuracy, "Nemotron", `${num(t.questions)} test questions, both scored the same way`)}
    ${b ? vs("Against the best of other vision models", b.ours_accuracy, b.best_accuracy, "Best other", `${b.models} hosted models on ${b.n} of our weakest scan questions; small sample`) : ""}
    ${c ? `<div class="tile" title="${esc(COST_SOURCE)}"><p class="tile-k">Cost per 1,000 questions (estimate)</p>
      <p class="tile-v">about ${fmtUsd(COST_BLENDED_USD_PER_1000)}</p>
      <p class="tile-s">Nemotron alone: about ${fmtUsd(c.nemotron_usd_per_1000)}, on the same ${num(t.questions)} questions</p></div>` : ""}
  </section>`;
}

function compareExample(x, i, here) {
  const n = x.nemotron, o = x.ours;
  const nAns = n.answer == null ? "No answer" : cmpNum(n.answer, x.unit);
  const nWhy = n.answer == null ? `It used its whole output budget (${num(n.completion_tokens)} tokens) without giving a number.` : n.correct ? "Right" : "Wrong";
  const oAns = cmpNum(o.answer, x.unit);
  return `<section class="cmp" aria-labelledby="cmp-h${i}">
    <h2 class="cmp-q" id="cmp-h${i}"><span class="cmp-kind">${esc(x.kind)}</span> ${esc(x.question)}</h2>
    <p class="meta cmp-dw"><span class="mono">${esc(x.path.split("/").pop())}</span>  ·  ${esc(SOURCE_NAMES[x.source] || x.source)}  ·  dataset answer ${esc(cmpNum(x.gold, x.unit))}</p>
    <div class="cmp-cols">
      <div class="cmp-col cmp-left">
        <h3 class="h-group">Nemotron 3.5 Super VL alone</h3>
        <p class="cmp-ans ${n.correct ? "ok" : "bad"}">${esc(nAns)}</p>
        <p class="cmp-verdict ${n.correct ? "ok" : "bad"}">${esc(nWhy)}</p>
        <p class="meta">${esc(cmpSec(n.latency_s))}  ·  no evidence</p>
      </div>
      <div class="cmp-col cmp-right">
        <h3 class="h-group">Blueprint Evidence</h3>
        <p class="cmp-ans ${o.correct ? "ok" : "bad"}">${esc(oAns)}</p>
        <p class="cmp-verdict ${o.correct ? "ok" : "bad"}">${o.correct ? "Right" : "Wrong"}  ·  ${esc(CMP_STATUS[o.status] || o.status)}</p>
        <p class="meta" title="First question on the drawing, not cached">${esc(cmpSec(o.time_s))}  ·  read from ${esc(o.route)}</p>
        ${here ? `<button type="button" class="btn" data-open="${i}">Open in Ask</button>` : `<p class="meta">This drawing is not in this library.</p>`}
      </div>
      <div class="cmp-col cmp-evi">${o.evidence ? `<img class="cmp-thumb" src="${esc(o.evidence)}" alt="Evidence image for ${esc(x.kind)}: the drawing with the items our answer is built from" loading="lazy" decoding="async">` : ""}</div>
    </div></section>`;
}

function compareTotals(t) {
  const b = t.bakeoff, c = t.cost;
  return `<details class="fine cmp-totals"><summary id="cmp-tot">How the totals were measured</summary>
    <table class="tbl"><tbody>
      <tr><th scope="row">Accuracy</th><td>${num(t.questions)} test questions: Nemotron alone ${pct(t.nemotron_accuracy)}, Blueprint Evidence ${pct(t.ours_accuracy)}</td>
        <td class="dim">${t.nemotron_correct} and ${t.ours_correct} of ${t.questions} correct. Preview test split, seeded sample; both scored the same way.</td></tr>
      <tr><th scope="row">Other vision models</th><td>${b.models} hosted vision models tried; on our weakest scan questions the best got ${Math.round(b.best_accuracy * 100)}%, ours ${Math.round(b.ours_accuracy * 100)}%</td>
        <td class="dim">${b.best_correct} of ${b.n} for the best one (mistral-large-4, which finished ${b.n} of the 110 weak-spot questions); ours on the same ${b.n}: ${b.ours_correct}. Scan doors, windows and room types; small sample, read it as direction. Details in docs/model_bakeoff.md.</td></tr>
      <tr><th scope="row">Cost</th><td>€${Math.round(c.nemotron_usd_per_1000 / c.usd_per_eur)} against under ${fmtUsd(0.1 * c.usd_per_eur)} per 1,000 questions</td>
        <td class="dim">Nemotron at its measured tokens and an assumed list price of $${c.price_in_out_usd_per_mtok[0]} and $${c.price_in_out_usd_per_mtok[1]} per million tokens in and out (NVIDIA Build itself was free); ours at measured CPU time on the same ${num(t.questions)} questions, ${fmtUsd(c.ours_usd_per_1000)} per 1,000. ${esc(FX_NOTE)}</td></tr>
    </tbody></table></details>`;
}

async function compareOpen(x) {
  showView("ask");
  await selectDrawing(x.path);
  if (!state.drawing || state.drawing.path !== x.path) return;
  $("#question").value = x.question;
  await ask();
}
/* ---- compare tab (block end) ---- */

/* ================================================================ first-visit tour (onboarding block, v3)
   On a first visit a small offer asks whether to take a tour; "How it works" in the app bar replays it.
   v3 (14 Nov, his call): no Next button. Each step outlines the real element and says what to press, and the tour
   moves on when the person does it: open a group, open a drawing, ask (the step waits, "answering", until the answer
   shows), press an item in the answer, press a numbered item in the strip, open Compare. Only "Skip tour" and Escape
   end it early. The current step is worked out from the page itself on every event and on a short poll, so a step
   done early jumps forward, and going off-script (another drawing, an upload, the answer cleared) moves the tour back
   to the step that now applies instead of breaking it. A step that cannot be done here (an answer with no items, no
   strip) is passed over.
   The choice is kept in localStorage under bpe-tour (try/catch: without storage the offer shows on every load).
   Only at 1081px and wider: below that the panels float or stack, and CSS hides the offer and the button.
   Nothing above depends on this block except the first-visit default of folded library groups (TOUR_KEY). */
(() => {
  const wide = window.matchMedia("(min-width: 1081px)");
  const remember = (v) => { try { localStorage.setItem(TOUR_KEY, v); } catch (_) { /* not stored: offered again next visit */ } };
  const mk = (cls) => { const e = document.createElement("div"); e.className = cls; return e; };
  const shown = (el) => !!(el && el.offsetParent && el.getBoundingClientRect().width > 0);

  /* what the person has done since the tour started (clicks seen in the capture phase, before the page acts) */
  let did = {};
  const anyGroupOpen = () => !!document.querySelector('#drawList .dgroup-toggle[aria-expanded="true"]');
  const answerItems = () => document.querySelectorAll("#itemList button.item");
  const stripItems = () => document.querySelectorAll("#stripList .thumb[data-i]");
  const tryBox = () => { const t = $("#detailEmpty .try-list"); return shown(t) ? t.closest(".try") : null; };

  /* each step: the elements it outlines (the first places the bubble), what to press, when it is done, and whether it
     can be done at all right now. `done` reads the page, so a later step done first also completes the earlier ones */
  const STEPS = [
    { id: "group", el: () => [$("#drawList")], side: ["right", "left"],
      text: () => "Drawings are grouped by type. Press a group heading, for example Building models (IFC), to open it.",
      done: () => did.group || anyGroupOpen() || !!state.drawing },
    { id: "pick", el: () => [$("#drawList"), $("#drop"), $("#search"), $("#uploadList")].filter((e) => e && e.offsetParent !== null), side: ["right", "left"],
      text: () => "Press a drawing to open it, or search by name. Building models also open in a 3D view. Your own drawing goes in Upload a drawing above; uploads are deleted after 24 hours.",
      done: () => !!state.drawing },
    { id: "ask", el: () => [$("#askForm"), $(".chips-block"), tryBox()], anchor: 2, side: ["bottom", "left"],
      text: () => state.askingQ
        ? "Reading the drawing for your question. The answer appears on the right."
        : "Ask about this drawing in your own words, in any language: type a question and press Ask. Or press one of the Examples, which fills in the question, then press Ask; or press one of the Try these questions on the right, which asks at once.",
      wait: () => !!state.askingQ,
      done: () => !!state.answer && !state.askingQ },
    /* the strip under the drawing is always on screen; the answer's own Items list can sit below the fold of the panel */
    { id: "item", el: () => (shown($("#strip")) && stripItems().length ? [$("#strip"), $("#detail")] : [$("#detail")]), side: ["top", "left"],
      scrollTo: () => (shown($("#strip")) && stripItems().length ? null : $("#itemList")),
      text: () => "This is the answer on the right: the number, its status and the steps behind it. A question asked again comes from the answer cache and is marked Cached. "
        + (shown($("#strip")) && stripItems().length ? "Press a numbered item in the strip under the drawing, or in the answer's Items list: the drawing zooms to it." : "Press any item in its Items list to see it on the drawing."),
      can: () => answerItems().length > 0,
      done: () => did.item || did.strip },
    { id: "strip", el: () => [$("#strip")], side: ["top", "left"],
      text: () => "Every counted item is numbered on the drawing and in this strip. Press one to zoom to it and see step by step how it was read.",
      can: () => shown($("#strip")) && stripItems().length > 0,
      done: () => did.strip },
    { id: "compare", el: () => [$('.rail-btn[data-view="compare"]')], side: ["right", "bottom"],
      text: () => (state.answer && !answerItems().length ? "Your answer is on the right, with its status and the steps behind it. " : "")
        + "Last step: press Compare. It shows prepared questions answered by a large vision model alone and by this app, with totals.",
      done: () => did.compare },
  ];

  let shield, bubble, spots = [], idx = -1, returnFocus = null, pollT = null, watchRO = null, painted = "";
  const watch = (el) => {
    if (!watchRO) watchRO = new ResizeObserver(() => layout());
    watchRO.disconnect();
    if (el) watchRO.observe(el);
  };

  /* the step that applies now: the first one not done, passing over steps that cannot be done here */
  function current() {
    for (let k = 0; k < STEPS.length; k++) {
      const s = STEPS[k];
      if (s.done()) continue;
      if (s.can && !s.can()) continue;      // every step before it is done or passed over, or the loop stopped there
      return k;
    }
    return STEPS.length;
  }

  function targets() {
    if (idx < 0 || idx >= STEPS.length) return [];
    return STEPS[idx].el().filter(shown);
  }

  /* bring the step's element on screen: the library back if it was folded away, the Ask view if another is open */
  function reveal() {
    const id = STEPS[idx].id;
    if (id !== "compare" && $("#view-ask").hidden) showView("ask");
    if ((id === "group" || id === "pick") && $("#view-ask").classList.contains("no-picker")) $("#pickerToggle").click();
    const [first] = targets(), inner = STEPS[idx].scrollTo?.();
    if (first) first.scrollIntoView({ block: "nearest", inline: "nearest" });
    if (shown(inner)) inner.scrollIntoView({ block: "center", inline: "nearest" });   // the part to press, inside its panel
  }

  function sync() {
    if (idx < 0) return;
    const k = current();
    if (k >= STEPS.length) { finish(); return; }
    const key = k + "|" + STEPS[k].text() + "|" + (STEPS[k].wait?.() ? 1 : 0);
    if (k !== idx) { idx = k; reveal(); }
    if (key !== painted) { painted = key; render(); }
    layout();
  }

  function finish() {
    end(true);
    toast("Tour done. How it works, top right, plays it again.");
  }

  function end(markDone) {
    if (idx < 0) return;
    idx = -1; painted = "";
    clearInterval(pollT);
    shield.remove(); bubble.remove(); spots.forEach((s) => s.remove()); spots = [];
    watch(null);
    document.removeEventListener("keydown", onKey, true);
    document.removeEventListener("click", onClick, true);
    window.removeEventListener("resize", layout);
    window.removeEventListener("scroll", layout, true);
    window.removeEventListener("bpe-ask", sync);
    if (markDone) remember("done");
    if (returnFocus && returnFocus.focus && document.contains(returnFocus)) returnFocus.focus();
  }

  function layout() {
    if (idx < 0 || idx >= STEPS.length) return;
    const step = STEPS[idx], els = targets(), pad = 4;
    while (spots.length < els.length) { const s = mk("tour-spot"); document.body.append(s); spots.push(s); }
    spots.forEach((s, i) => { s.hidden = i >= els.length; });
    if (!els.length) {                 // nothing to outline: the bubble sits in the middle and Skip still works
      shield.style.clipPath = ""; bubble.style.left = "50%"; bubble.style.top = "40%"; return;
    }
    /* the shield has a hole over each outlined element, so it can be used while everything else stays blocked */
    const rects = els.map((el) => el.getBoundingClientRect());
    let path = "0 0, 100% 0, 100% 100%, 0 100%, 0 0";
    rects.forEach((r, i) => {
      Object.assign(spots[i].style, { left: r.left - pad + "px", top: r.top - pad + "px", width: r.width + 2 * pad + "px", height: r.height + 2 * pad + "px" });
      const [x0, y0, x1, y1] = [r.left - pad, r.top - pad, r.right + pad, r.bottom + pad].map((v) => Math.round(v) + "px");
      path += `, ${x0} ${y0}, ${x1} ${y0}, ${x1} ${y1}, ${x0} ${y1}, ${x0} ${y0}, 0 0`;
    });
    shield.style.clipPath = `polygon(evenodd, ${path})`;
    /* the bubble sits beside the first outlined element, or beside the first `anchor` of them taken together */
    const lead = rects.slice(0, Math.min(step.anchor || 1, rects.length));
    const r = { left: Math.min(...lead.map((x) => x.left)), top: Math.min(...lead.map((x) => x.top)),
      right: Math.max(...lead.map((x) => x.right)), bottom: Math.max(...lead.map((x) => x.bottom)) };
    const W = window.innerWidth, H = window.innerHeight, bw = bubble.offsetWidth, bh = bubble.offsetHeight, m = 14;
    const clamp = (v, lo, hi) => Math.max(lo, Math.min(v, Math.max(lo, hi)));
    const at = {
      right: () => [r.right + pad + m, clamp(r.top, 48, H - bh - 8), r.right + pad + m + bw <= W - 8],
      left: () => [r.left - pad - m - bw, clamp(r.top, 48, H - bh - 8), r.left - pad - m - bw >= 8],
      bottom: () => [clamp(r.left, 8, W - bw - 8), r.bottom + pad + m, r.bottom + pad + m + bh <= H - 8],
      top: () => [clamp(r.left, 8, W - bw - 8), r.top - pad - m - bh, r.top - pad - m - bh >= 8],
    };
    let pos = null;
    for (const s of step.side) { const p = at[s](); if (p[2]) { pos = p; break; } }
    if (!pos) { const p = at[step.side[0]](); pos = [clamp(p[0], 8, W - bw - 8), clamp(p[1], 8, H - bh - 8)]; }
    bubble.style.left = pos[0] + "px"; bubble.style.top = pos[1] + "px";
  }

  function render() {
    const step = STEPS[idx], waiting = !!step.wait?.();
    const hadFocus = bubble.contains(document.activeElement) || document.activeElement === document.body;
    bubble.innerHTML = `<p>${esc(step.text())}</p>
      ${waiting ? '<p class="meta tour-wait">Answering... the first question on a new scan can take up to a minute or two while it is read; after that, about a second. CAD, PDF and IFC answer in about a second.</p>' : ""}
      <div class="tour-act"><span class="tour-step">Step ${idx + 1} of ${STEPS.length}</span>
        <button type="button" class="btn btn-quiet" data-a="skip">Skip tour</button></div>`;
    bubble.setAttribute("aria-label", `Tour, step ${idx + 1} of ${STEPS.length}`);
    if (waiting) bubble.setAttribute("aria-busy", "true"); else bubble.removeAttribute("aria-busy");
    const [first] = targets();
    watch(first || null);
    layout();
    if (hadFocus) bubble.focus();        // never pull focus out of the question box while the person types
  }

  /* clicks are only noted here; the page handles them as usual, and the next sync reads the result */
  function onClick(e) {
    const t = e.target;
    if (!(t instanceof Element)) return;
    if (t.closest("#drawList .dgroup-toggle")) did.group = true;
    if (t.closest("#itemList .item")) did.item = true;
    if (t.closest("#stripList .thumb[data-i]")) did.strip = true;
    if (t.closest('.rail-btn[data-view="compare"]')) did.compare = true;
    setTimeout(sync, 0);
  }

  function onKey(e) {
    if (idx < 0) return;
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); end(true); return; }
    if (e.key === "Tab") {             // focus stays within the bubble and the outlined elements
      const inHole = targets().some((el) => el.contains(document.activeElement));
      if (inHole) return;
      const f = [...bubble.querySelectorAll("button:not([disabled])")];
      if (!f.length) { e.preventDefault(); return; }
      if (!bubble.contains(document.activeElement)) { e.preventDefault(); f[0].focus(); }
    }
  }

  function start() {
    if (idx >= 0 || !wide.matches) return;
    const offer = $("#tourOffer"); if (offer) offer.remove(); document.querySelectorAll(".tour-offer-dim").forEach((d) => d.remove());
    returnFocus = document.activeElement && document.activeElement !== document.body ? document.activeElement : $("#guideOpen");
    showView("ask");
    did = {};
    shield = mk("tour-shield"); bubble = mk("tour-bubble");
    bubble.setAttribute("role", "dialog"); bubble.tabIndex = -1;
    bubble.addEventListener("click", (e) => { if (e.target.closest('[data-a="skip"]')) end(true); });
    document.body.append(shield, bubble);
    document.addEventListener("keydown", onKey, true);
    document.addEventListener("click", onClick, true);
    window.addEventListener("resize", layout);
    window.addEventListener("scroll", layout, true);       // the highlighted list scrolled inside its panel
    window.addEventListener("bpe-ask", sync);
    idx = STEPS.length + 1; painted = "";                   // no step yet, so the first sync reveals the one that applies
    sync();
    bubble.focus();
    pollT = setInterval(sync, 300);      // answers arrive, drawings change, panels open: read the page, cheaply
  }

  function offer() {
    if (!wide.matches) return;
    let decided = false;
    try { decided = localStorage.getItem(TOUR_KEY) !== null; } catch (_) { /* no storage: offer it */ }
    if (decided) return;
    /* BEGIN tour-offer-v2 */
    const o = mk("tour-offer"), dim = mk("tour-offer-dim");
    const close = () => {
      o.remove(); dim.remove();
      document.removeEventListener("keydown", onEsc);
      document.removeEventListener("pointerdown", onAway, true);
      window.removeEventListener("bpe-ask", onAsk);
    };
    /* the offer gets out of the way once the person starts on their own: a click elsewhere or a question asked
       (judge review 14 Nov: it stayed over the question bar after a "Try these" example had answered) */
    const onAway = (e) => { if (!o.contains(e.target)) { remember("declined"); close(); } };
    const onAsk = () => close();
    const onEsc = (e) => { if (e.key === "Escape" && idx < 0) { remember("declined"); close(); } };
    o.innerHTML = `<h2>First time here?</h2>
      <p>A 30-second tour shows how to open a drawing, ask a question in your own words, and check the answer against the drawing.</p>
      <div class="tour-act"><button type="button" class="btn btn-primary" data-a="start">Start the tour</button>
      <button type="button" class="btn btn-quiet" data-a="no">No thanks</button></div>`;
    o.id = "tourOffer"; o.setAttribute("role", "region"); o.setAttribute("aria-label", "Tour offer");
    o.addEventListener("click", (e) => {
      const b = e.target.closest("[data-a]"), a = b && b.dataset.a;
      if (a === "start") { close(); start(); }
      else if (a === "no") { remember("declined"); close(); }
    });
    document.addEventListener("keydown", onEsc);
    document.addEventListener("pointerdown", onAway, true);
    window.addEventListener("bpe-ask", onAsk);
    document.body.append(dim, o);
    /* END tour-offer-v2 */
  }

  $("#guideOpen").addEventListener("click", start);
  offer();
})();
