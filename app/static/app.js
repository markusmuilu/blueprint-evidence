/* Blueprint Evidence front end. Plain JS, no build step. */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const icon = (id, cls = "ico") => `<svg class="${cls}" aria-hidden="true"><use href="#i-${id}"/></svg>`;
const pct = (x) => (x == null ? "-" : (x * 100).toFixed(1) + "%");
const num = (x) => (x == null ? "-" : Number(x).toLocaleString("en-US"));

const SOURCE_NAMES = {
  swiss_dwellings: "Swiss dwellings", cubicasa5k: "CubiCasa scans", ifc_models: "IFC models",
  ifc_bench: "IFC-Bench", opening_design: "OpeningDesign", upload: "Upload",
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

async function api(url, opts) {
  const r = await fetch(url, opts);
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
      ${row("Dataset", s.data_root || "not found; set STOA_DATA")}
      ${row("Drawings", num(s.drawings))}
      ${row("Detector", s.detector_model && s.detector_runtime ? "available (CPU)" : s.detector_model ? "model found, runtime missing: scan symbol counts abstain unless cached" : "no model: scan symbol counts abstain")}
      ${row("OCR", s.ocr_runtime ? "available" : "missing: room labels abstain")}
      ${row("Uploads", s.uploads)}
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
let searchT;
async function loadDrawings(append = false) {
  const list = $("#drawList");
  if (!append) {
    state.offset = 0;
    list.innerHTML = '<li class="skel"></li><li class="skel"></li><li class="skel"></li>';
  }
  const p = new URLSearchParams({ source: state.source, q: state.query, offset: state.offset, limit: 60 });
  try {
    const res = await api("/api/drawings?" + p);
    state.total = res.total;
    renderSources(res.sources);
    if (!append) list.innerHTML = "";
    if (!res.total) {
      list.innerHTML = `<li class="list-note">${res.sources.length ? "No drawing matches. Try part of a file name." : "No dataset found. Set STOA_DATA, or upload a drawing above."}</li>`;
    }
    for (const d of res.items) list.appendChild(drawItem(d));
    state.offset += res.items.length;
    $("#moreBtn").hidden = state.offset >= res.total;
    $("#drawCount").textContent = num(res.total);
  } catch (e) {
    list.innerHTML = `<li class="list-note">Could not load the library: ${esc(e.message)}</li>`;
  }
}

function drawItem(d) {
  const li = document.createElement("li");
  const b = document.createElement("button");
  b.className = "draw-item";
  b.setAttribute("aria-selected", String(state.drawing?.path === d.path));
  b.innerHTML = `<span class="nm" title="${esc(d.path)}">${esc(d.name)}</span>
    <span class="sub"><span>${esc(SOURCE_NAMES[d.source] || d.source)}</span><span class="mono">${esc(d.ext.toUpperCase())}</span><span>${d.n_questions} questions</span><span>${esc(d.split)}</span></span>`;
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
$("#moreBtn").addEventListener("click", () => loadDrawings(true));

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
    state.drawing = d;
    document.querySelectorAll(".draw-item").forEach((b) =>
      b.setAttribute("aria-selected", String(b.querySelector(".nm").title === d.path)));
    $("#drawTitle").textContent = d.name;
    const bits = [SOURCE_NAMES[d.source] || d.source, d.ext.toUpperCase()];
    if (d.twins?.length) bits.push("twins: " + d.twins.join(", "));
    if (d.split) bits.push(d.split + " split");
    $("#drawMeta").textContent = bits.join("  ·  ");
    $("#pdfOnly").closest(".check").hidden = !(d.ext === "pdf" && d.twins?.length);
    renderDrawingQuestions(d);
    renderExamples();
    setHash();
    if (!keepAnswer) {
      state.answer = null;
      renderAnswer(null);
      showDrawing();
    }
  } catch (e) {
    toast("Could not open the drawing: " + e.message, true);
  }
}

function renderExamples() {
  const el = $("#exampleChips");
  el.innerHTML = EXAMPLES.map((x, i) =>
    `<button class="chip" type="button" data-i="${i}" title="${esc(x.q)}"><span class="lang">${x.lang}</span>${esc(x.label)}</button>`).join("");
  el.querySelectorAll(".chip").forEach((c) => c.addEventListener("click", () => {
    $("#question").value = EXAMPLES[+c.dataset.i].q;
    $("#question").focus();
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
  history.replaceState(null, "", "#" + p.toString());
}

/* ================================================================ viewer */
const viewer = (() => {
  const vp = $("#viewer"), stage = $("#stage"), img = $("#viewImg");
  let k = 1, x = 0, y = 0, fitted = false, drag = null;

  function apply() {
    stage.style.transform = `translate(${x}px, ${y}px) scale(${k})`;
    stage.classList.toggle("pixel", k > 2.5);
    stage.style.setProperty("--inv", String(1 / k));
    $("#zoomLvl").textContent = Math.round(k * 100) + "%";
  }
  function fit() {
    if (!img.naturalWidth) return;
    const r = vp.getBoundingClientRect();
    const pad = 24;
    k = Math.min((r.width - pad * 2) / img.naturalWidth, (r.height - pad * 2 - 40) / img.naturalHeight);
    k = Math.max(Math.min(k, 4), 0.02);
    x = (r.width - img.naturalWidth * k) / 2;
    y = Math.max(pad + 40, (r.height - img.naturalHeight * k) / 2);
    fitted = true;
    apply();
  }
  function zoomAt(f, cx, cy) {
    const nk = Math.min(Math.max(k * f, 0.02), 12);
    x = cx - (cx - x) * (nk / k);
    y = cy - (cy - y) * (nk / k);
    k = nk;
    apply();
  }
  function center() { const r = vp.getBoundingClientRect(); return [r.width / 2, r.height / 2]; }
  function focusRect(rx, ry, rw, rh) {
    const r = vp.getBoundingClientRect();
    const want = Math.min(r.width / (rw * 6), r.height / (rh * 6), 4);
    if (want > k) k = want;
    x = r.width / 2 - (rx + rw / 2) * k;
    y = r.height / 2 - (ry + rh / 2) * k;
    apply();
  }

  vp.addEventListener("wheel", (e) => {
    if (!img.naturalWidth) return;
    e.preventDefault();
    const r = vp.getBoundingClientRect();
    zoomAt(Math.exp(-e.deltaY * 0.0015), e.clientX - r.left, e.clientY - r.top);
  }, { passive: false });
  vp.addEventListener("pointerdown", (e) => {
    if (e.target.closest(".viewer-tools") || !img.naturalWidth) return;
    drag = { px: e.clientX, py: e.clientY, x, y, moved: false };
    vp.setPointerCapture(e.pointerId);
    vp.classList.add("dragging");
  });
  vp.addEventListener("pointermove", (e) => {
    if (!drag) return;
    if (Math.abs(e.clientX - drag.px) + Math.abs(e.clientY - drag.py) > 4) drag.moved = true;
    x = drag.x + e.clientX - drag.px;
    y = drag.y + e.clientY - drag.py;
    apply();
  });
  const end = () => { drag = null; vp.classList.remove("dragging"); };
  vp.addEventListener("pointerup", (e) => {
    if (drag && !drag.moved && state.addMode) {
      const r = vp.getBoundingClientRect();
      addItemAt((e.clientX - r.left - x) / k, (e.clientY - r.top - y) / k);
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
      "+": () => zoomAt(1.25, ...center()), "=": () => zoomAt(1.25, ...center()), "-": () => zoomAt(0.8, ...center()),
      "0": fit, ArrowLeft: () => { x += step; apply(); }, ArrowRight: () => { x -= step; apply(); },
      ArrowUp: () => { y += step; apply(); }, ArrowDown: () => { y -= step; apply(); },
    };
    if (map[e.key]) { e.preventDefault(); map[e.key](); }
  });
  $("#zoomIn").addEventListener("click", () => zoomAt(1.25, ...center()));
  $("#zoomOut").addEventListener("click", () => zoomAt(0.8, ...center()));
  $("#zoomFit").addEventListener("click", fit);
  window.addEventListener("resize", () => { if (fitted) fit(); });

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
    $("#viewerEmpty").hidden = false;
    $("#viewerEmpty").innerHTML = `<p class="empty-title">${esc(msgTitle)}</p><p class="meta">${esc(msg)}</p>`;
  }
  return { load, clear, fit, focusRect, fitIfNeeded: () => { if (img.naturalWidth && !fitted) fit(); }, img };
})();

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
    const [label, extra] = itemLabel(it, a.query && a.query.obj, a.route);
    const area = typeof it === "object" && it ? (it.area_m2 ?? it.area ?? (a.unit === "m2" && typeof it.value === "number" ? it.value : null)) : null;
    return { i, n: (it && typeof it === "object" && it.n) || i + 1, it, rect: itemRect(it, a, i), mark: m, label, extra, area,
             tone: m ? m.tone : a.status === "estimate" ? "amber" : "blue" };
  });
  if (!rows.some((r) => r.rect) && a.marks?.marks?.length) {        // items without positions: use the drawn marks
    const off = headerOff(a);
    rows = a.marks.marks.map((m, i) => ({ i, n: m.n ?? i + 1, it: null, mark: m, label: m.kind === "poly" ? `region ${i + 1}` : `item ${m.n}`,
      extra: "", area: null, tone: m.tone, rect: [m.rect[0], m.rect[1] + off, m.rect[2] - m.rect[0], m.rect[3] - m.rect[1]] }));
  }
  return rows;
}
function pinTone(t) { return t === "amber" ? "warn" : t === "grey" ? "bad" : "blue"; }
/* every counted item gets a pin in the app's own layer, numbered like the overlay */
function drawPins(a) {
  const own = a === state.answer;
  const html = checkItems(a).map((r) => {
    if (!r.rect) return "";
    const off = own && state.corr.removed.has(r.n) ? " off" : "";
    return `<div class="pin ${pinTone(r.tone)}${off}" data-i="${r.i}" style="left:${r.rect[0]}px;top:${r.rect[1]}px;width:${r.rect[2]}px;height:${r.rect[3]}px"><span>${esc(r.n)}</span></div>`;
  }).join("");
  const off = headerOff(a);
  const added = own ? state.corr.added.map((p, j) =>
    `<div class="pin add" style="left:${p.x - 9}px;top:${p.y + off - 9}px;width:18px;height:18px"><span>+${j + 1}</span></div>`).join("") : "";
  /* witness views: circle the places where another format disagrees about an individual item */
  const mk = a.marks;
  const dis = !own && mk && a.disputed ? a.disputed.map((d) => {
    const R = Math.max(mk.w * 0.018, 14);
    const tip = d.kind === "only" ? `Counted here as #${d.n}, not by ${d.other}` : `Counted by ${d.other}, missed here`;
    return `<div class="dis ${d.kind}" title="${esc(tip)}" style="left:${d.x * mk.w - R}px;top:${d.y * mk.h + off - R}px;width:${2 * R}px;height:${2 * R}px"></div>`;
  }).join("") : "";
  $("#marks").innerHTML = html + added + dis + '<div class="reticle" hidden></div>';
}
function clearMarks() { $("#marks").innerHTML = ""; clearFocus(); }
function clearFocus() {
  document.querySelectorAll(".item.on, .pin.on, .thumb.on").forEach((b) => b.classList.remove("on"));
  const ret = $("#marks .reticle");
  if (ret) ret.hidden = true;
}
function focusRect(r, i, focus) {
  if (!r || state.layer !== "evidence") return;
  clearFocus();
  if (i != null) document.querySelectorAll(`#itemList .item[data-i="${i}"], #stripList .thumb[data-i="${i}"], #marks .pin[data-i="${i}"]`).forEach((el) => el.classList.add("on"));
  const ret = $("#marks .reticle");
  if (ret) {
    const pad = 4;
    Object.assign(ret.style, { left: r[0] - pad + "px", top: r[1] - pad + "px", width: r[2] + pad * 2 + "px", height: r[3] + pad * 2 + "px" });
    ret.hidden = false;
  }
  if (focus) viewer.focusRect(...r);
}
function markItem(btn, it, a, n, focus) {
  focusRect(itemRect(it, a, +btn.dataset.i), btn.dataset.i, focus);
}

/* ---------------------------------------------------------------- verification strip */
function isCount(a) { return a.unit === "count"; }
function correctedValue(a) {
  const c = state.corr;
  if (a.value == null || typeof a.value !== "number") return a.value;
  if (isCount(a)) return a.value - c.removed.size + c.added.length;
  let v = a.value;
  for (const r of checkItems(a)) if (c.removed.has(r.n) && typeof r.area === "number") v -= r.area;
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
    ${own ? `<span class="corr mono" id="corrLine">${esc(corrLine(a))}</span>
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
    drawThumb(li.querySelector("canvas"), img, r.rect, removed);
    const t = li.querySelector(".thumb");
    t.addEventListener("click", () => focusRect(r.rect, r.i, true));
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
function drawThumb(cv, img, r, dim) {
  const ctx = cv.getContext("2d");
  ctx.fillStyle = "#fff";
  ctx.fillRect(0, 0, cv.width, cv.height);
  if (!img.naturalWidth || !r) return;
  const cx = r[0] + r[2] / 2, cy = r[1] + r[3] / 2;
  const w = Math.max(r[2] * 2.4, r[3] * 2.4 * (TW / TH), 90);
  const h = w * (TH / TW);
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(img, cx - w / 2, cy - h / 2, w, h, 0, 0, cv.width, cv.height);
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
  const t0 = performance.now();
  const tick = () => { $("#busyText").textContent = `Reading the drawing  ${((performance.now() - t0) / 1000).toFixed(1)} s`; };
  tick();
  busyT = setInterval(tick, 100);
  try {
    const a = await api("/api/ask", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: state.drawing.path, question: q, pdf_only: $("#pdfOnly").checked, refresh }),
    });
    state.answer = a;
    state.corr = { removed: new Set(), added: [] };
    state.witnesses = null;
    renderAnswer(a);
    if (a.evidence_id) await showEvidence(a);
    else await showDrawing();
    setHash();
    loadWitnesses(a, refresh);
  } catch (e) {
    toast("The question failed: " + e.message, true);
  } finally {
    clearInterval(busyT);
    $("#viewerBusy").hidden = true;
    btn.disabled = false;
    btn.removeAttribute("aria-busy");
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
      tag = ok ? `<span class="tag ok">matches</span>` : `<span class="tag bad">differs</span>`;
    }
    gold = `<p class="gold">Dataset answer <span class="mono">${esc(gv)} ${esc(fmtUnit(a.gold.unit))}</span> ${tag} <span class="meta">${esc(a.gold.split || "")} split</span></p>`;
  }

  body.innerHTML = `
    <div class="ans-head">
      <p class="ans-q">${esc(a.question)}</p>
      <div class="ans-value">
        ${hasVal ? `<span class="v${isText ? " text" : ""}" id="ansValue">${esc(valText)}</span><span class="u">${esc(fmtUnit(a.unit))}</span>`
                 : `<span class="v none">No answer</span>`}
      </div>
      <p class="note corr-note" id="corrNote" hidden></p>
      <span class="badge ${esc(a.status)}">${icon(st.icon)}${esc(st.label)}</span>
      ${a.status === "cannot" ? `<p class="cannot-reason">${esc(shortReason(a))}${would ? ` The route would have said ${esc(would.would_answer)}.` : ""}</p>` : ""}
      ${gold}
      <dl class="facts">
        <dt>Confidence</dt><dd>${a.confidence != null && a.status !== "cannot" ? Number(a.confidence).toFixed(2) : "-"}</dd>
        <dt>Response</dt><dd>${a.time_ms < 1000 ? Math.round(a.time_ms) + " ms" : (a.time_ms / 1000).toFixed(1) + " s"}</dd>
        <dt>CPU</dt><dd>${a.compute ? fmtSec(a.compute.cpu_s) : "-"}</dd>
        <dt>Model calls</dt><dd>${a.cost_usd ? "yes" : "none"}</dd>
      </dl>
      ${costMeter(a)}
      ${a.cached ? `<p class="note">Served from the answer cache. Time and cost are from the first run; Run again recomputes them.</p>` : ""}
    </div>

    <section class="sec" id="witSec" aria-labelledby="hWit" hidden>
      <div class="sec-head"><h3 class="h-panel" id="hWit">Witnesses</h3><span class="meta" id="witMeta"></span></div>
      <div id="witBody"></div>
    </section>

    <section class="sec" aria-labelledby="hWhere">
      <h3 class="h-panel" id="hWhere">Where</h3>
      <dl class="where">
        ${(a.where || []).map((w) => `<dt>${esc(w.k)}</dt><dd>${esc(w.v)}${w.code ? `<span class="code">${esc(w.code)}</span>` : ""}</dd>`).join("")}
        ${a.tried && a.tried.length > 1 ? `<dt>Tried</dt><dd class="mono" style="font-size:12px">${esc(a.tried.join(" > "))}</dd>` : ""}
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
      <div class="sec-head"><h3 class="h-panel" id="hItems">Items <span class="cite" style="margin-left:6px">${itemsCite}</span></h3><span class="meta mono">${items.length}${a.items.length >= 200 ? "+" : ""}</span></div>
      <ul class="items" id="itemList">
        ${items.map((it, i) => {
          const [l, x] = itemLabel(it, a.query && a.query.obj, a.route);
          const n = (it && it.n) || i + 1;
          const can = !!itemRect(it, a, i);
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
}

function shortReason(a) {
  const r = a.abstain || "No route could answer.";
  return r.length > 320 ? r.slice(0, 317) + "..." : r;
}

function whyText(w) {
  const kind = `<span class="kind">${esc(KIND[w.kind] || w.kind)}</span>`;
  if (w.kind === "check") {
    const pass = w.status === "pass";
    return `<span class="st ${pass ? "pass" : "fail"}">${icon(pass ? "proven" : "cannot")}${pass ? "PASS" : "FAIL"}</span>${esc(w.text)}`;
  }
  return kind + esc(w.text);
}

/* ---------------------------------------------------------------- cost meter */
function fmtSec(s) { return s == null ? "-" : s < 1 ? Math.round(s * 1000) + " ms" : s.toFixed(1) + " s"; }
function fmtUsd(v) {
  if (v == null) return "-";
  if (v === 0) return "$0";
  if (v < 0.01) return "$" + v.toPrecision(2);
  return "$" + v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
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
    <p class="note">CPU ${fmtSec(c.cpu_s)}${c.gpu_s ? `, GPU ${fmtSec(c.gpu_s)}` : ""}${c.model_usd ? `, model ${fmtUsd(c.model_usd)}` : ""} at an assumed $${as.cpu_vcpu_hour_usd} per vCPU-hour${c.gpu_s ? ` and $${as.gpu_hour_usd} per GPU-hour` : ""}.${base ? ` Nemotron: ${num(Math.round(base.prompt_tokens_mean + base.completion_tokens_mean))} measured tokens per question at an assumed $${base.price_in_per_mtok} / $${base.price_out_per_mtok} per million in / out.` : ""} Log scale.</p>
  </div>`;
}

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
      <span class="wf mono">${esc(r.format)}</span>
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
    <p class="note">${rel ? esc(rel) + " " : ""}Each format is read on its own, twins hidden, no model calls. Select one to see its evidence; circled values disagree with the others.</p>`;
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
  const head = `<thead><tr><th>Route</th><th class="num">Questions</th><th class="num">Median wall</th><th class="num">p90 wall</th><th class="num">Cold median</th><th class="num">CPU s, mean</th><th class="num">$ per 1,000, CPU</th><th class="num">$ per 1,000 if on a GPU</th></tr></thead>`;
  const line = (r, cls = "") => `<tr class="${cls}"><td>${esc(r.label || ROUTE_LABEL[r.route] || r.route)}${r.route && r.route !== "all" ? ` <span class="mono dim">${esc(r.route)}</span>` : ""}</td><td class="num">${num(r.n)}</td>
    <td class="num">${fmtSec(r.wall_median_s)}</td><td class="num">${fmtSec(r.wall_p90_s)}</td><td class="num">${fmtSec(r.cold_median_s)}</td>
    <td class="num">${r.cpu_mean_s == null ? "-" : r.cpu_mean_s.toFixed(2)}</td><td class="num">${fmtUsd(r.usd_per_1000_cpu)}</td><td class="num">${fmtUsd(r.usd_per_1000_if_gpu)}</td></tr>`;
  let body = (c.routes || []).map((r) => line(r)).join("");
  if (c.all) body += line({ ...c.all, route: "all", label: "All sampled questions" }, "total");
  const b = c.baseline;
  if (b) body += `<tr class="base"><td>Nemotron, plain prompt <span class="mono dim">baseline</span></td><td class="num">${num(b.n)}</td><td class="num">${fmtSec(b.latency_median_s)}</td><td class="num">-</td><td class="num">-</td><td class="num">-</td><td class="num" colspan="2">${fmtUsd(b.usd_per_1000)} at measured tokens</td></tr>`;
  el.innerHTML = head + "<tbody>" + (body || `<tr><td colspan="8" class="dim">${esc(c.missing || "No measurements yet.")}</td></tr>`) + "</tbody>";
  $("#costNote").textContent = `Assumed prices, not quotes: $${as.cpu_vcpu_hour_usd} per vCPU-hour, $${as.gpu_hour_usd} per GPU-hour (L4 or A10G class)${b ? `, and $${b.price_in_per_mtok} / $${b.price_out_per_mtok} per million input / output tokens for Nemotron, applied to its measured ${num(Math.round(b.prompt_tokens_mean))} input and ${num(Math.round(b.completion_tokens_mean))} output tokens per question (${b.source})` : ""}. `
    + (c.questions ? `Measured on ${num(c.questions)} ${esc(c.split || "")} questions of the ${esc(datasetName(c.data))}, ${esc(c.machine?.device || "")}, evidence rendering on, Nemotron off (tools/cost_eval.py, out/cost/cost_by_route.json). CPU seconds count every thread; a cold question is the first one on its drawing.` : "");
}
const ROUTE_LABEL = {};
function datasetName(p) { return /preview/.test(p || "") ? "Stoa preview set" : /junction-dataset/.test(p || "") ? "Stoa full set" : (p || "dataset"); }

async function loadWitnessSummary() {
  let w;
  try { w = await api("/api/witness_summary"); } catch (e) { return; }
  const el = $("#witTable");
  if (!el) return;
  if (w.missing) { el.innerHTML = `<tbody><tr><td class="dim">${esc(w.missing)}</td></tr></tbody>`; return; }
  const head = `<thead><tr><th>Witnesses</th><th class="num">Questions</th><th class="num">Share</th><th class="num">Router answer correct</th><th class="num">Agreed value correct</th></tr></thead>`;
  const body = w.rows.map((r) => `<tr class="${r.level === "total" ? "total" : ""}"><td>${esc(r.label)}</td><td class="num">${num(r.n)}</td><td class="num">${pct(r.share)}</td>
    <td class="num">${r.router_correct == null ? "-" : `${pct(r.router_correct)} <span class="dim">(${r.router_correct_n})</span>`}</td>
    <td class="num">${r.majority_correct == null ? "-" : `${pct(r.majority_correct)} <span class="dim">(${r.majority_correct_n})</span>`}</td></tr>`).join("");
  el.innerHTML = head + "<tbody>" + body + "</tbody>";
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
    sel.innerHTML = runs.map((r) => `<option value="${esc(r.id)}">${esc(r.label || r.id)}  [${esc(r.where || "out")}/${esc(r.id)}]</option>`).join("");
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
  $("#runHeader").textContent = `${d.label || d.id}. Source: ${d.where || "out"}/${d.id}/summary.json. ${d.header || ""}`;
  const all = d.rows.find((r) => r.source === "ALL" && r.type === "ALL");
  const where = d.split ? `the <strong>${esc(d.dataset_name)}</strong>, <strong>${esc(d.split)}</strong> split` : `<strong>${esc(d.dataset_name)}</strong>`;
  const flags = d.flags && d.flags.length ? ` (${esc(d.flags.join("; "))})` : "";
  $("#runHeadline").innerHTML = all
    ? `On ${where}${flags}, <strong>${pct(all.accuracy)}</strong> of ${num(all.n)} questions were answered correctly. The system answered <strong>${pct(all.coverage)}</strong> of them and was right on <strong>${pct(all.precision)}</strong> of those, at <strong>$${Number(all.cost_per_q || 0).toFixed(4)}</strong> per question.`
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
  const head = `<thead><tr><th>Test set</th><th class="num">Questions</th><th class="num">Accuracy</th><th class="num">Precision when answered</th><th>Detail</th><th>Source</th></tr></thead>`;
  const body = items.map((it) => {
    if (it.missing) return `<tr><td>${esc(it.set)}</td><td colspan="4" class="dim">Not run on this machine (${esc(it.missing)}). The README states the last measured numbers.</td><td></td></tr>`;
    let detail = "";
    if (it.baseline_accuracy != null) detail = `Nemotron, plain prompt: ${pct(it.baseline_accuracy)}; router: ${esc(it.note || "")}`;
    else if (it.wrong != null) detail = `${it.correct} correct, ${it.wrong} wrong (${it.proven_wrong} of them proven), ${it.abstain} abstained, ${it.abstain_ok} correctly abstained`;
    else if (it.proven != null) detail = `proven ${num(it.proven)} at ${pct(it.proven_precision)}, estimate ${num(it.estimate)} at ${pct(it.estimate_precision)}`;
    return `<tr><td>${esc(it.set)}</td><td class="num">${num(it.n)}</td><td class="num">${pct(it.accuracy)}</td>
      <td class="num">${it.precision == null ? "-" : pct(it.precision)}</td><td class="wrap">${detail}</td><td class="mono dim srcfile">${esc(it.source || "")}</td></tr>`;
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
    <p class="value-na mono">Nemotron ${pct(b.nemo_accuracy)} vs router ${pct(b.ours_accuracy)}</p>
    <div class="table-wrap"><table class="tbl"><thead><tr><th>Questions</th><th class="num">n</th><th class="num">Nemotron</th><th class="num">Router</th></tr></thead>
    <tbody>${rows}<tr class="total"><td>All</td><td class="num">${b.n}</td><td class="num">${b.nemo_correct}</td><td class="num">${b.ours_correct}</td></tr></tbody></table></div>
    <p class="meta">Sample: Stoa preview set, test split, ${num(b.n)} questions (${esc(b.sample)}). Nemotron got the drawing as one image and was asked for just the number: no regions, no checks. Median latency ${esc(b.latency_median_s)} s; about $${Number(b.cost_per_q || 0).toFixed(3)} per question at ${esc(price)} (free on NVIDIA Build). Router: $0, ${esc(b.ours_source)}. Source: out/baseline_nemotron.json.</p>`;
}

function renderSrcTable(d) {
  const head = `<thead><tr><th>Source</th><th>Type</th><th class="num">n</th><th class="num">Accuracy</th><th class="num">Coverage</th><th class="num">Precision</th><th class="num">$ per q</th></tr></thead>`;
  const rows = [];
  const bySrc = {};
  for (const r of d.rows) (bySrc[r.source] = bySrc[r.source] || []).push(r);
  const order = Object.keys(bySrc).filter((s) => s !== "ALL");
  const line = (r, cls, first) => `<tr class="${cls}"><td>${first}</td><td>${r.type === "ALL" ? "all" : esc(r.type)}</td><td class="num">${num(r.n)}</td>
      <td class="num">${pct(r.accuracy)}</td><td class="num${r.answered ? "" : " dim"}">${pct(r.coverage)}</td>
      <td class="num${r.answered ? "" : " dim"}">${r.answered ? pct(r.precision) : "-"}</td><td class="num">${Number(r.cost_per_q || 0).toFixed(4)}</td></tr>`;
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
  loadStatus();
  loadCosts();
  await loadDrawings();
  const p = new URLSearchParams(location.hash.slice(1));
  if (p.get("path")) {
    await selectDrawing(p.get("path"));
    if (p.get("q")) { $("#question").value = p.get("q"); ask(); }
  }
  if (p.get("view") === "results") showView("results");
})();
