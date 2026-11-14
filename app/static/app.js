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
  toastT = setTimeout(() => (t.hidden = true), err ? 6000 : 2600);
}

const UPLOAD_EXPIRED = "This upload has expired (uploads are deleted after 15 minutes). Please upload it again.";
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
  $("#pickerToggle").hidden = name !== "ask";
  $("#view-results").hidden = name !== "results";
  if (name === "results") loadRuns();
  if (name === "ask") requestAnimationFrame(() => viewer.fitIfNeeded());
}
document.querySelectorAll(".rail-btn[data-view]").forEach((b) => b.addEventListener("click", () => showView(b.dataset.view)));

$("#pickerToggle").addEventListener("click", (e) => {
  const v = $("#view-ask");
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
      ${row("Uploads", "kept for 15 minutes, then deleted")}
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
  { id: "bim", ext: "ifc", tone: "proven", title: "Building models (IFC)",
    note: "3D model; answers read from the model, proven" },
  { id: "cad", ext: "dxf", tone: "proven", title: "CAD drawings (DXF)",
    note: "Vector lines and layers, proven",
    none: "None listed on their own: Swiss PDFs come with a DXF twin, and you can upload a DXF." },
  { id: "pdf", ext: "pdf", tone: "proven", title: "PDF plans",
    note: "Vector PDF with a text layer; scale read from the sheet" },
  { id: "scan", ext: "png", tone: "estimate", title: "Scanned plans",
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
  return api("/api/drawings?" + new URLSearchParams({ source: state.source, q: state.query, ext: g.ext, offset, limit }));
}

async function loadDrawings() {
  const list = $("#drawList");
  const seq = ++loadSeq;
  list.innerHTML = '<ul><li class="skel"></li><li class="skel"></li><li class="skel"></li></ul>';
  try {
    const res = await Promise.all(GROUPS.map((g) => groupQuery(g, 0, GROUP_FIRST)));
    if (seq !== loadSeq) return;
    renderSources(res[0].sources);
    const total = res.reduce((a, r) => a + r.total, 0);
    $("#drawCount").textContent = num(total);
    list.innerHTML = "";
    if (!total) {
      list.innerHTML = `<p class="list-note">${res[0].sources.length ? "No drawing matches. Try part of a file name." : "No dataset found. Set STOA_DATA, or upload a drawing above."}</p>`;
      return;
    }
    GROUPS.forEach((g, i) => {
      const r = res[i];
      const filtered = state.source || state.query;
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

function drawItem(d) {
  const li = document.createElement("li");
  const b = document.createElement("button");
  b.className = "draw-item";
  b.setAttribute("aria-selected", String(state.drawing?.path === d.path));
  b.innerHTML = `<span class="nm" title="${esc(d.path)}">${esc(d.name)}</span>
    <span class="sub"><span>${esc(SOURCE_NAMES[d.source] || d.source)}</span><span>${esc(d.ext.toUpperCase())}</span><span>${d.n_questions} questions</span><span>${esc(d.split)}</span></span>`;
  b.addEventListener("click", () => selectDrawing(d.path));
  li.appendChild(b);
  return li;
}

function renderSources(sources) {
  const el = $("#sources");
  const all = sources.reduce((a, s) => a + s.drawings, 0);
  const btn = (id, name, n) =>
    `<button class="src" role="radio" aria-checked="${state.source === id}" data-src="${esc(id)}">${esc(name)} <span class="n">${num(n)}</span></button>`;
  el.innerHTML = btn("", "All", all) + sources.map((s) => btn(s.id, s.name, s.drawings)).join("");
  el.querySelectorAll(".src").forEach((b) => b.addEventListener("click", () => {
    state.source = b.dataset.src;
    loadDrawings();
  }));
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

async function upload(files) {
  const fd = new FormData();
  for (const f of files) fd.append("files", f);
  toast(`Uploading ${files.length} file${files.length > 1 ? "s" : ""}`);
  try {
    const res = await api("/api/upload", { method: "POST", body: fd });
    toast(`Uploaded ${res.name}`);
    selectDrawing(res.path);
  } catch (e) {
    toast("Upload failed: " + e.message, true);
  }
}

/* ---------------------------------------------------------------- select a drawing */
async function selectDrawing(path, keepAnswer = false) {
  try {
    const d = await api("/api/drawing?path=" + encodeURIComponent(path));
    if (state.drawing?.path !== d.path) viewer.resetOrient(true);     // a new drawing opens in its own orientation
    state.drawing = d;
    document.querySelectorAll(".draw-item").forEach((b) =>
      b.setAttribute("aria-selected", String(b.querySelector(".nm").title === d.path)));
    $("#drawTitle").textContent = d.name;
    const bits = [SOURCE_NAMES[d.source] || d.source, d.ext.toUpperCase()];
    if (d.twins?.length) bits.push("twins: " + d.twins.join(", "));
    if (d.split) bits.push(d.split + " split");
    $("#drawMeta").textContent = bits.join("  ·  ");
    $("#pdfOnly").closest(".check").hidden = !(d.ext === "pdf" && d.twins?.length);
    setup3d(d);
    renderDrawingQuestions(d);
    renderExamples();
    if (!keepAnswer) {
      if (state.drawing && state.askTicket) {      // a question still running on the previous drawing is abandoned
        state.askTicket += 1;
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
  { label: "Bring your own drawing", note: "PDF, DXF, IFC or an image. Drop a PDF with its DXF twin to use both.", upload: true },
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
  if (q && state.answer) p.set("q", q);
  if (state.mode === "3d") p.set("mode", "3d");
  history.replaceState(null, "", "#" + p.toString());
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
  function focusRect(rx, ry, rw, rh) {
    const r = vp.getBoundingClientRect();
    [rx, ry, rw, rh] = rectToView([rx, ry, rw, rh]);
    const want = Math.min(r.width / (rw * 6), r.height / (rh * 6), 4);
    if (want > k) k = want;
    x = r.width / 2 - (rx + rw / 2) * k;
    y = r.height / 2 - (ry + rh / 2) * k;
    atFit = false;
    apply();
  }

  /* zoom a rect (image pixels) to fill `fill` of a part of the viewport {l, t, w, h} and centre it there */
  function focusIn(r, box, fill = 0.7, maxK = 4) {
    const [rx, ry, rw, rh] = rectToView(r);
    k = Math.max(0.02, Math.min((box.w * fill) / Math.max(rw, 1), (box.h * fill) / Math.max(rh, 1), maxK));
    x = box.l + box.w / 2 - (rx + rw / 2) * k;
    y = box.t + box.h / 2 - (ry + rh / 2) * k;
    atFit = false;
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
  document.addEventListener("keydown", (e) => {
    if (e.ctrlKey || e.metaKey || e.altKey || e.defaultPrevented) return;
    if (e.target?.closest?.("input, textarea, select, [contenteditable]")) return;
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
  if (["pdf", "png", "jpg", "jpeg"].includes(d.ext)) {
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
  $("#marks").innerHTML = rooms + html + added + dis + '<div class="reticle" hidden></div>';
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
    if (focus && it && it.guid && window.Viewer3D) Viewer3D.select(it.guid);
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
  if (focus) viewer.focusRect(...r);
  if (focus) openSteps(i);                     // evidence layers: the Steps card for the clicked item
}
/* click the selected item again, click empty drawing or press Escape: back to the plain answer (his request) */
function isSelected(i) { return layers.sel != null && String(layers.sel.i) === String(i); }
function deselect() { closeSteps(); clearFocus(); lastFocus = null; }
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape" || e.defaultPrevented || e.target?.closest?.("input, textarea, select")) return;
  if (layers.sel != null || lastFocus) deselect();
});
function markItem(btn, it, a, n, focus) {
  if (state.mode === "3d" && it && it.guid && window.Viewer3D) {
    if (focus) { document.querySelectorAll("#itemList .item.on").forEach((b) => b.classList.remove("on")); btn.classList.add("on"); Viewer3D.select(it.guid); }
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
      pts: st.pts && st.pts.length > 2 ? st.pts.map(([x, y]) => [x, y + off]) : null }));
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
  if (!fitK(box)) return;
  viewer.focusIn(r, box, 0.8);
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
    const cap = esc(m2(st.cap || st.note || st.text || st.title).slice(0, 48));
    if (st.pts) {
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
  const geo = s.steps.filter((st) => st.r || st.pts).length;
  const raw = (st) => st.kind === "value" || st.kind === "label" || st.kind === "detect";   // text as read: not reformatted
  card.innerHTML = `
    <div class="sc-head">
      <h3 class="h-panel"><span class="mono sc-n">#${esc(s.row.n)}</span>${esc(s.row.label || "")}</h3>
      <span class="sc-gap"></span>
      ${geo > 1 ? `<button type="button" class="btn btn-quiet sc-play" aria-pressed="false" title="Show the layers one at a time">Step through</button>` : ""}
      <button type="button" class="tool sc-close" aria-label="Close the steps">${icon("close")}</button>
    </div>
    <ol class="sc-list">
      ${s.steps.map((st, k) => `<li><button type="button" class="sc-step${st.r || st.pts ? "" : " flat"}" data-k="${k}" aria-pressed="false">
        <span class="cite">${k + 1}</span><span class="k">${esc(st.title)}</span>
        <span class="v">${st.kind === "model" ? `<span class="sc-3d">Select in 3D</span>`
          : `${st.text ? `<span class="mono">${esc(raw(st) ? st.text : m2(st.text))}</span>` : ""}${st.note ? `${st.text ? " → " : ""}<span class="note">${esc(m2(st.note))}</span>` : ""}`}</span>
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
  const ks = s.steps.map((st, k) => (st.r || st.pts ? k : null)).filter((k) => k != null);
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
  const w = tot && tot.waste_pct ? tot.waste_pct / 100 : 0;
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
    ${scale ? `<span class="meta scale-src" title="${esc(scale.text)}">Scale from ${esc(scale.text.split(" (")[0].replace(/^Scale found on the sheet$/, "the sheet"))}</span>` : ""}
    <span class="strip-gap"></span>
    ${own ? `<span class="corr" id="corrLine">${esc(corrLine(a))}</span>
      ${isCount(a) ? `<button class="btn btn-quiet" type="button" id="addMode" aria-pressed="${state.addMode}">${icon("plus")}Add missed item</button>` : ""}
      <button class="btn" type="button" id="saveCorr" ${corrected() ? "" : "disabled"}>Save correction</button>
      <a class="btn btn-quiet" href="/api/corrections.csv" download>${icon("download")}Export</a>`
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
        added: state.corr.added, evidence_id: a.evidence_id }),
    });
    toast(`Correction saved, ${r.n} on record. Export downloads them as CSV.`);
  } catch (e) { toast("Could not save: " + e.message, true); }
}

/* ================================================================ ask */
$("#askForm").addEventListener("submit", (e) => { e.preventDefault(); ask(); });

let busyT;
async function ask(refresh = false) {
  const q = $("#question").value.trim();
  if (!state.drawing) { toast("Pick a drawing first", true); return; }
  if (!q) { $("#question").focus(); return; }
  const btn = $("#askBtn");
  btn.disabled = true;
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
  try {
    const a = await api("/api/ask", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: asked, question: q, pdf_only: $("#pdfOnly").checked, refresh }),
    });
    // a slow answer that comes back after another drawing or question was opened is dropped, never shown on it
    if (ticket !== state.askTicket || !state.drawing || state.drawing.path !== asked) return;
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
    toast(e.expired ? e.message : "The question failed: " + e.message, true);
  } finally {
    if (ticket === state.askTicket) {         // only the latest question owns the busy state
      clearInterval(busyT);
      $("#viewerBusy").hidden = true;
      $("#detailBusy").hidden = true; $("#detailEmpty").hidden = !!state.answer; $("#detailBody").hidden = !state.answer;
      btn.disabled = false;
      btn.removeAttribute("aria-busy");
    }
  }
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
        ${hasVal ? `<span class="v${isText ? " text" : ""}" id="ansValue">${esc(valText)}</span><span class="u">${esc(fmtUnit(a.unit))}</span>`
                 : `<span class="v none">No answer</span>`}
      </div>
      <p class="note corr-note" id="corrNote" hidden></p>
      <dl class="kv facts">
        <dt>Status</dt><dd><span class="status ${esc(a.status)}">${icon(st.icon)}${esc(st.label)}</span></dd>
        ${a.status === "cannot" ? `<dd class="wide cannot-reason">${esc(shortReason(a))}${would ? ` The route would have said ${esc(would.would_answer)}.` : ""}</dd>` : ""}
        ${gold}
        <dt>Confidence</dt><dd class="num">${a.confidence != null && a.status !== "cannot" ? Number(a.confidence).toFixed(2) : "-"}</dd>
        <dt>Response</dt><dd class="num">${a.time_ms < 1000 ? Math.round(a.time_ms) + " ms" : (a.time_ms / 1000).toFixed(1) + " s"}<span class="meta">, CPU ${a.compute ? fmtSec(a.compute.cpu_s) : "-"}</span></dd>
        <dt>Model calls</dt><dd>${a.cost_usd ? "yes" : "none"}</dd>
      </dl>
      ${usedLine(a)}
      ${costMeter(a)}
      ${a.cached ? `<p class="note">Served from the answer cache. Time and cost are from the first run; Run again recomputes them.</p>` : ""}
    </div>

    <section class="sec" id="orderSec" aria-labelledby="hOrder" hidden></section>

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

    <section class="sec" aria-labelledby="hWhy">
      <h3 class="h-panel" id="hWhy">Why</h3>
      <ol class="why">
        ${why.map((w, i) => `<li><span class="cite">${i + 1}</span><span class="txt">${whyText(w)}</span></li>`).join("")}
        ${itemsCite ? `<li><span class="cite">${itemsCite}</span><span class="txt"><span class="kind">Items</span>${items.length} item${items.length > 1 ? "s" : ""} ${a.unit === "count" ? "counted" : "used"}, numbered on the drawing and in the strip under it.${checkItems(a).some((r) => r.rect) ? " Select one to find it." : ""}</span></li>` : ""}
        <li><span class="cite">${(itemsCite || nWhy) + 1}</span><span class="txt"><span class="kind">Status</span>${esc(a.status_rule || "")}</span></li>
      </ol>
      ${a.no_overlay_reason ? `<p class="note">${esc(a.no_overlay_reason)}</p>` : ""}
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

    <div class="actions">
      <button class="btn" type="button" id="copyCsv">${icon("copy")}Copy as CSV row</button>
      <button class="btn btn-quiet" type="button" id="dlCsv">${icon("download")}Download CSV</button>
      <button class="btn btn-quiet" type="button" id="reask" title="Run the routes again instead of using the cached answer">${icon("refresh")}Run again</button>
    </div>`;

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
function usedLine(a) {
  const q = a.query || {}, ex = q.extra || {}, c = a.compute || {};
  const llm = q.parser === "llm";
  const parts = [];
  for (const r of String(a.route || "").split("+")) {
    const w = USED_PART[r] || ROUTE_LABEL[r] || r;
    if (w && !parts.includes(w)) parts.push(w);
  }
  const second = (a.checks || []).some((k) => k.name === "nemotron-second-opinion" && k.status !== "error");
  if (second && !parts.includes("Nemotron image estimate")) parts.push("Nemotron second opinion");
  const imageEst = String(a.route || "").split("+").includes("nemotron-estimate") || second;
  const readCall = llm && !ex.parse_cached && (ex.parse_cost_orig_usd || ex.parse_cost_usd) ? 1 : 0;
  const calls = [];
  if (readCall) calls.push("question reading");
  if (imageEst) calls.push(second ? "Nemotron second opinion" : "Nemotron image estimate");
  const callTxt = calls.length ? `${calls.length} (${calls.join(", ")})` : (llm && ex.parse_cached ? "none (question reading cached)" : "none");
  const per = c.usd_per_1000 != null ? `${fmtUsd(c.usd_per_1000)} per 1,000 questions like this` : "";
  return `<p class="used-line" id="usedLine"><span><b>Question read by:</b> ${llm ? "language model" : "rules"}</span><span><b>Answered with:</b> ${esc(parts.join(" and ") || "-")}</span><span><b>AI calls:</b> ${callTxt}</span>${per ? `<span>${per}</span>` : ""}</p>`;
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
    const w = await api("/api/witnesses", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: a.path, question: a.question, refresh }),
    });
    if (state.answer !== a) return;
    state.witnesses = w;
    renderWitnesses(a, w);
  } catch (e) {
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
const CSV_COLS = ["drawing", "question", "value", "unit", "status", "route", "confidence", "checks_passed", "checks_failed", "cost_usd", "response_ms", "evidence"];
function csvRow(a) {
  const checks = a.checks || [];
  const vals = {
    drawing: a.path, question: a.question, value: a.status === "cannot" ? "" : a.value, unit: a.unit,
    status: a.status === "cannot" ? "cannot prove: " + (a.abstain || "") : a.status, route: a.route,
    confidence: a.confidence, checks_passed: checks.filter((c) => c.status === "pass").length,
    checks_failed: checks.filter((c) => c.status === "fail").length, cost_usd: Number(a.cost_usd || 0).toFixed(4),
    response_ms: Math.round(a.time_ms), evidence: a.evidence_id ? location.origin + "/api/evidence/" + a.evidence_id + ".png" : "",
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
function downloadCsv(a) {
  const blob = new Blob([CSV_COLS.join(",") + "\n" + csvRow(a) + "\n"], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = (a.drawing || "answer").replace(/\.[^.]+$/, "") + "_answer.csv";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

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
  const where = d.split ? `the <strong>${esc(d.dataset_name)}</strong>, <strong>${esc(d.split)}</strong> split` : `<strong>${esc(d.dataset_name)}</strong>`;
  const flags = d.flags && d.flags.length ? ` (${esc(d.flags.join("; "))})` : "";
  $("#runHeadline").innerHTML = all
    ? `On ${where}${flags}, <strong>${pct(all.accuracy)}</strong> of ${num(all.n)} questions were answered correctly. The system answered <strong>${pct(all.coverage)}</strong> of them and was right on <strong>${pct(all.precision)}</strong> of those, at <strong>${fmtUsd(all.cost_per_q || 0)}</strong> per question.`
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
}

async function loadBaseline() {
  let b;
  try { b = await api("/api/baseline"); } catch (e) { return; }      // keeps the "not measured" text
  const rows = b.by_source.map((s) => `<tr><td>${esc(SOURCE_NAMES[s.source] || s.source)}, ${esc(s.type)}</td><td class="num">${s.n}</td>
    <td class="num">${s.nemo_correct}</td><td class="num">${s.ours_correct}</td></tr>`).join("");
  const price = b.price_assumption ? `an assumed ${b.price_assumption[0]} / ${b.price_assumption[1]} USD per million input / output tokens` : "an assumed list price";
  $("#baselineBody").innerHTML = `
    <p class="value-na">Nemotron ${pct(b.nemo_accuracy)} vs router ${pct(b.ours_accuracy)}</p>
    <div class="table-wrap"><table class="tbl"><thead><tr><th>Questions</th><th class="num">n</th><th class="num">Nemotron</th><th class="num">Router</th></tr></thead>
    <tbody>${rows}<tr class="total"><td>All</td><td class="num">${b.n}</td><td class="num">${b.nemo_correct}</td><td class="num">${b.ours_correct}</td></tr></tbody></table></div>
    <p class="meta">Sample: Stoa preview set, test split, ${num(b.n)} questions (${esc(b.sample)}). Nemotron got the drawing as one image and was asked for just the number: no regions, no checks. Median latency ${esc(b.latency_median_s)} s; about ${fmtUsd(b.cost_per_q || 0)} per question at ${esc(price)} (free on NVIDIA Build). Router: €0 in model calls, ${esc(b.ours_source)}. ${FX_NOTE}</p>`;
}

function renderSrcTable(d) {
  const head = `<thead><tr><th>Source</th><th>Type</th><th class="num">n</th><th class="num">Accuracy</th><th class="num">Coverage</th><th class="num">Precision</th><th class="num">€ per 1,000</th></tr></thead>`;
  const rows = [];
  const bySrc = {};
  for (const r of d.rows) (bySrc[r.source] = bySrc[r.source] || []).push(r);
  const order = Object.keys(bySrc).filter((s) => s !== "ALL");
  const line = (r, cls, first) => `<tr class="${cls}"><td>${first}</td><td>${r.type === "ALL" ? "all" : esc(r.type)}</td><td class="num">${num(r.n)}</td>
      <td class="num">${pct(r.accuracy)}</td><td class="num${r.answered ? "" : " dim"}">${pct(r.coverage)}</td>
      <td class="num${r.answered ? "" : " dim"}">${r.answered ? pct(r.precision) : "-"}</td><td class="num">${fmtUsd((r.cost_per_q || 0) * 1000)}</td></tr>`;
  for (const s of order) {
    const rs = bySrc[s];
    const total = rs.find((r) => r.type === "ALL");
    const parts = rs.filter((r) => r.type !== "ALL");
    if (total) rows.push(line(total, "group", esc(SOURCE_NAMES[s] || s)));
    if (parts.length > 1 || !total) for (const r of parts) rows.push(line(r, "", ""));
  }
  if (bySrc.ALL) rows.push(line(bySrc.ALL[0], "total", "All sources"));
  $("#srcTable").innerHTML = head + "<tbody>" + rows.join("") + "</tbody>";
}

function renderChart(d) {
  const rows = d.rows.filter((r) => r.type === "ALL").sort((a, b) => (a.source === "ALL") - (b.source === "ALL") || b.n - a.n);
  const W = 460, rowH = 30, left = 128, right = 64, top = 8, H = top + rows.length * rowH + 22;
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
    s += `<g data-i="${i}">`;
    s += `<text class="lbl" x="${left - 10}" y="${y + rowH / 2 + 4}" text-anchor="end"${r.source === "ALL" ? ' style="font-weight:600;fill:var(--fg)"' : ""}>${esc(name)}</text>`;
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
  renderExamples();
  renderTry();
  loadStatus();
  loadCosts();
  await loadDrawings();
  const p = new URLSearchParams(location.hash.slice(1));
  if (p.get("path")) {
    await selectDrawing(p.get("path"));
    if (p.get("mode") === "3d") setMode("3d");
    if (p.get("q")) { $("#question").value = p.get("q"); ask(); }
  }
  if (p.get("view") === "results") showView("results");
})();
/* a pasted link or back/forward in the same tab changes only the hash: load what it names (QA 14 Nov). The app's
   own URL updates use history.replaceState, which fires no hashchange, so this does not loop. */
window.addEventListener("hashchange", async () => {
  const p = new URLSearchParams(location.hash.slice(1));
  if (p.get("view") === "results") { showView("results"); return; }
  if (!p.get("path")) return;
  showView("ask");
  const moved = !state.drawing || state.drawing.path !== p.get("path");
  if (moved) await selectDrawing(p.get("path"));
  if (p.get("mode") === "3d") setMode("3d");
  if (p.get("q") && (moved || p.get("q") !== $("#question").value)) { $("#question").value = p.get("q"); ask(); }
});


/* ================================================================ first-visit tour (onboarding block, v2.6)
   Replaces the static "How it works" card. On a first visit a small offer asks whether to take a tour; Start walks
   five steps, each an outline around the real element plus a bubble. "How it works" in the app bar replays it.
   The choice is kept in localStorage under bpe-tour (try/catch: without storage the offer shows on every load).
   Only at 1081px and wider: below that the panels float or stack, and CSS hides the offer and the button.
   Step 4 runs the example the old card ran (a Swiss plan, bathroom area) through selectDrawing and ask.
   Nothing above depends on this block except the first-visit default of folded library groups (TOUR_KEY). */
(() => {
  const wide = window.matchMedia("(min-width: 1081px)");
  const remember = (v) => { try { localStorage.setItem(TOUR_KEY, v); } catch (_) { /* not stored: offered again next visit */ } };
  const mk = (cls) => { const e = document.createElement("div"); e.className = cls; return e; };

  const exampleTarget = () => {
    const t = $("#detailEmpty .try-list");
    return t && t.offsetParent ? t.closest(".try") : $("#exampleChips");
  };
  const STEPS = [
    { el: () => $("#drawList"), side: ["right", "left"], text: "Drawings are grouped by type. Open a group to see its drawings." },
    { el: () => $("#drop"), side: ["right", "left"], text: "Add your own drawing here. Uploads are deleted after 15 minutes." },
    { el: () => $("#askForm"), side: ["bottom", "top"], text: "Ask anything about the drawing, in your own words and any language." },
    { el: exampleTarget, side: ["left", "bottom"], text: "Not sure what to ask? Try one of these.", next: "Try one", run: runExample },
    { el: () => $("#detail"), side: ["left", "right"], text: "The answer appears here: the number, its status and the steps behind it. Click any item to see it on the drawing." },
    /* tour step (verify strip) */
    { el: () => { const st = $("#strip"); return st && !st.hidden && st.offsetParent ? st : $("#viewer") || $("#viewerWrap"); }, side: ["top", "left"], text: "Every counted item is numbered on the drawing. Click one to see step by step how it was read." },
  ];

  let shield, spot, bubble, idx = -1, returnFocus = null, busy = false;

  async function runExample() {
    const t = { path: "data/swiss_dwellings/site3387_bldg5238_floor23498.pdf", q: "What is the floor area of the bathrooms?" };
    try {
      await selectDrawing(t.path);
      if (!state.drawing) return;
      $("#question").value = t.q;
      await ask();
    } catch (_) { /* the toast from ask already says what failed */ }
  }

  function end(markDone) {
    if (idx < 0) return;
    idx = -1;
    shield.remove(); spot.remove(); bubble.remove();
    document.removeEventListener("keydown", onKey, true);
    window.removeEventListener("resize", layout);
    if (markDone) remember("done");
    if (returnFocus && returnFocus.focus) returnFocus.focus();
  }

  function layout() {
    if (idx < 0) return;
    const step = STEPS[idx], el = step.el();
    if (!el || !el.offsetParent) { spot.hidden = true; bubble.style.left = "50%"; bubble.style.top = "40%"; return; }
    spot.hidden = false;
    const r = el.getBoundingClientRect(), pad = 4;
    Object.assign(spot.style, { left: r.left - pad + "px", top: r.top - pad + "px", width: r.width + 2 * pad + "px", height: r.height + 2 * pad + "px" });
    const W = window.innerWidth, H = window.innerHeight, bw = bubble.offsetWidth, bh = bubble.offsetHeight, m = 14;
    const clamp = (v, lo, hi) => Math.max(lo, Math.min(v, Math.max(lo, hi)));
    const spots = {
      right: () => [r.right + pad + m, clamp(r.top, 48, H - bh - 8), r.right + pad + m + bw <= W - 8],
      left: () => [r.left - pad - m - bw, clamp(r.top, 48, H - bh - 8), r.left - pad - m - bw >= 8],
      bottom: () => [clamp(r.left, 8, W - bw - 8), r.bottom + pad + m, r.bottom + pad + m + bh <= H - 8],
      top: () => [clamp(r.left, 8, W - bw - 8), r.top - pad - m - bh, r.top - pad - m - bh >= 8],
    };
    let pos = null;
    for (const s of step.side) { const p = spots[s](); if (p[2]) { pos = p; break; } }
    if (!pos) { const p = spots[step.side[0]](); pos = [clamp(p[0], 8, W - bw - 8), clamp(p[1], 8, H - bh - 8)]; }
    bubble.style.left = pos[0] + "px"; bubble.style.top = pos[1] + "px";
  }

  function render() {
    const step = STEPS[idx], last = idx === STEPS.length - 1;
    bubble.innerHTML = `<p>${esc(step.text)}</p>
      <div class="tour-act"><span class="tour-step">${idx + 1} of ${STEPS.length}</span>
        ${idx > 0 ? '<button type="button" class="btn" data-a="back">Back</button>' : ""}
        <button type="button" class="btn btn-primary" data-a="next">${esc(last ? "Done" : step.next || "Next")}</button></div>
      ${last ? "" : '<div class="tour-act"><button type="button" class="btn btn-quiet" data-a="skip">Skip all tips</button></div>'}`;
    bubble.setAttribute("aria-label", `Tour, step ${idx + 1} of ${STEPS.length}`);
    bubble.removeAttribute("aria-busy");
    const el = STEPS[idx].el();
    if (el && el.offsetParent) el.scrollIntoView({ block: "nearest" });
    layout();
    bubble.focus();
  }

  function go(to) {
    if (busy || to < 0) return;
    if (to >= STEPS.length) { end(true); return; }
    idx = to;
    render();
  }

  async function next() {
    if (busy) return;
    const step = STEPS[idx];
    if (step.run) {
      busy = true;
      const b = bubble.querySelector('[data-a="next"]'); b.disabled = true; b.textContent = "Working";
      bubble.setAttribute("aria-busy", "true");
      /* tour waiting line */
      const w = document.createElement("p"); w.className = "meta tour-wait"; w.textContent = "Answering, about 15 s...";
      bubble.querySelector("p").after(w);
      await step.run();
      busy = false;
      if (idx < 0) return;                       // skipped while it ran
    }
    go(idx + 1);
  }

  function onKey(e) {
    if (idx < 0) return;
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); end(true); return; }
    if (e.key === "Tab") {                       // keep focus inside the bubble
      const f = [...bubble.querySelectorAll("button:not([disabled])")];
      if (!f.length) { e.preventDefault(); return; }
      const first = f[0], lastB = f[f.length - 1], cur = document.activeElement;
      if (e.shiftKey && (cur === first || cur === bubble)) { e.preventDefault(); lastB.focus(); }
      else if (!e.shiftKey && cur === lastB) { e.preventDefault(); first.focus(); }
      else if (!bubble.contains(cur)) { e.preventDefault(); first.focus(); }
    }
  }

  function start() {
    if (idx >= 0 || !wide.matches) return;
    const offer = $("#tourOffer"); if (offer) offer.remove();
    returnFocus = document.activeElement && document.activeElement !== document.body ? document.activeElement : $("#guideOpen");
    showView("ask");
    shield = mk("tour-shield"); spot = mk("tour-spot"); bubble = mk("tour-bubble");
    bubble.setAttribute("role", "dialog"); bubble.tabIndex = -1;
    bubble.addEventListener("click", (e) => {
      const b = e.target.closest("[data-a]"), a = b && b.dataset.a;
      if (a === "next") next(); else if (a === "back") go(idx - 1); else if (a === "skip") end(true);
    });
    document.body.append(shield, spot, bubble);
    document.addEventListener("keydown", onKey, true);
    window.addEventListener("resize", layout);
    idx = 0; render();
  }

  function offer() {
    if (!wide.matches) return;
    let decided = false;
    try { decided = localStorage.getItem(TOUR_KEY) !== null; } catch (_) { /* no storage: offer it */ }
    if (decided) return;
    const o = mk("tour-offer");
    o.innerHTML = `<p>First time here? Take a 30-second tour</p>
      <div class="tour-act"><button type="button" class="btn btn-primary" data-a="start">Start</button>
      <button type="button" class="btn btn-quiet" data-a="no">No thanks</button></div>`;
    o.id = "tourOffer"; o.setAttribute("role", "region"); o.setAttribute("aria-label", "Tour offer");
    o.addEventListener("click", (e) => {
      const b = e.target.closest("[data-a]"), a = b && b.dataset.a;
      if (a === "start") start(); else if (a === "no") { remember("declined"); o.remove(); }
    });
    document.body.appendChild(o);
  }

  $("#guideOpen").addEventListener("click", start);
  offer();
})();
