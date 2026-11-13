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
    drag = { px: e.clientX, py: e.clientY, x, y };
    vp.setPointerCapture(e.pointerId);
    vp.classList.add("dragging");
  });
  vp.addEventListener("pointermove", (e) => {
    if (!drag) return;
    x = drag.x + e.clientX - drag.px;
    y = drag.y + e.clientY - drag.py;
    apply();
  });
  const end = () => { drag = null; vp.classList.remove("dragging"); };
  vp.addEventListener("pointerup", end);
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
  const src = evidenceSrc(a);
  await viewer.load(src, refit);
  $("#openImg").href = src;
  $("#openImg").hidden = false;
  drawPins(a);
}
$("#headerToggle").addEventListener("click", (e) => {
  state.showHeader = !state.showHeader;
  e.currentTarget.setAttribute("aria-pressed", String(state.showHeader));
  e.currentTarget.setAttribute("aria-label", state.showHeader ? "Hide the evidence header strip" : "Show the evidence header strip");
  if (state.answer?.evidence_id) showEvidence(state.answer, true);
});

/* ---------------------------------------------------------------- pins and reticle on cited items */
function itemRect(it, a) {
  const g = a.evidence_geometry;
  if (!g || g.scale == null || typeof it !== "object" || it === null) return null;
  let r = null;
  if (Array.isArray(it.xyxy) && it.xyxy.length === 4) r = [it.xyxy[0], it.xyxy[1], it.xyxy[2] - it.xyxy[0], it.xyxy[3] - it.xyxy[1]];
  else if (Array.isArray(it.xywh) && it.xywh.length === 4) r = it.xywh;
  if (!r) return null;
  const off = state.showHeader ? g.header : 0;
  return [r[0] * g.scale, r[1] * g.scale + off, Math.max(r[2] * g.scale, 4), Math.max(r[3] * g.scale, 4)];
}
function answerItems(a) {
  return (a.items || []).filter((it) => !(it && typeof it === "object" && "would_answer" in it));
}
/* every counted item gets a pin in the app's own layer, numbered like the overlay, so estimates
   can be checked one by one at any zoom */
function drawPins(a) {
  const tone = a.status === "estimate" ? "warn" : a.status === "cannot" ? "bad" : "ok";
  const html = answerItems(a).map((it, i) => {
    const r = itemRect(it, a);
    if (!r) return "";
    const n = (it && it.n) || i + 1;
    return `<div class="pin ${tone}" data-i="${i}" style="left:${r[0]}px;top:${r[1]}px;width:${r[2]}px;height:${r[3]}px"><span>${esc(n)}</span></div>`;
  }).join("");
  $("#marks").innerHTML = html + '<div class="reticle" hidden></div>';
}
function clearMarks() { $("#marks").innerHTML = ""; clearFocus(); }
function clearFocus() {
  document.querySelectorAll(".item.on, .pin.on").forEach((b) => b.classList.remove("on"));
  const ret = $("#marks .reticle");
  if (ret) ret.hidden = true;
}
function markItem(btn, it, a, n, focus) {
  const r = itemRect(it, a);
  if (!r || state.layer !== "evidence") return;
  clearFocus();
  btn.classList.add("on");
  $(`#marks .pin[data-i="${btn.dataset.i}"]`)?.classList.add("on");
  const ret = $("#marks .reticle");
  if (ret) {
    const pad = 4;
    Object.assign(ret.style, { left: r[0] - pad + "px", top: r[1] - pad + "px", width: r[2] + pad * 2 + "px", height: r[3] + pad * 2 + "px" });
    ret.hidden = false;
  }
  if (focus) viewer.focusRect(...r);
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
    renderAnswer(a);
    if (a.evidence_id) await showEvidence(a);
    else await showDrawing();
    setHash();
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

function itemLabel(it, obj) {
  if (typeof it !== "object" || it === null) return [String(it), ""];
  let label = (it.number && it.name ? `${it.number} ${it.name}` : "") || it.label || it.class || it.name || it.match || (it.panel != null ? `panel ${it.panel}` : "") || it.guid || "";
  if (!label && Array.isArray(it.bbox)) {
    const [x0, y0, x1, y1] = it.bbox;
    label = `${(obj || "item").replace(/_/g, " ")} at x ${((x0 + x1) / 2).toFixed(1)}, y ${((y0 + y1) / 2).toFixed(1)} m`;
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
        ${hasVal ? `<span class="v${isText ? " text" : ""}">${esc(valText)}</span><span class="u">${esc(fmtUnit(a.unit))}</span>`
                 : `<span class="v none">No answer</span>`}
      </div>
      <span class="badge ${esc(a.status)}">${icon(st.icon)}${esc(st.label)}</span>
      ${a.status === "cannot" ? `<p class="cannot-reason">${esc(shortReason(a))}${would ? ` The route would have said ${esc(would.would_answer)}.` : ""}</p>` : ""}
      ${gold}
      <dl class="facts">
        <dt>Confidence</dt><dd>${a.confidence != null && a.status !== "cannot" ? Number(a.confidence).toFixed(2) : "-"}</dd>
        <dt>Response</dt><dd>${a.time_ms < 1000 ? Math.round(a.time_ms) + " ms" : (a.time_ms / 1000).toFixed(1) + " s"}</dd>
        <dt>Cost</dt><dd>$${Number(a.cost_usd || 0).toFixed(4)}</dd>
        <dt>Model calls</dt><dd>${a.cost_usd ? "yes" : "none"}</dd>
      </dl>
      ${a.cached ? `<p class="note">Served from the answer cache. The response time is from the first run; Run again recomputes it.</p>` : ""}
    </div>

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
        ${itemsCite ? `<li><span class="cite">${itemsCite}</span><span class="txt"><span class="kind">Items</span>${items.length} item${items.length > 1 ? "s" : ""} ${a.unit === "count" ? "counted" : "used"}, numbered on the drawing and listed below.${a.evidence_geometry?.scale != null ? " Select one to find it." : ""}</span></li>` : ""}
        <li><span class="cite">${(itemsCite || nWhy) + 1}</span><span class="txt"><span class="kind">Status</span>${esc(a.status_rule || "")}</span></li>
      </ol>
      ${a.no_overlay_reason ? `<p class="note">${esc(a.no_overlay_reason)}</p>` : ""}
    </section>

    ${items.length ? `<section class="sec" aria-labelledby="hItems">
      <div class="sec-head"><h3 class="h-panel" id="hItems">Items <span class="cite" style="margin-left:6px">${itemsCite}</span></h3><span class="meta mono">${items.length}${a.items.length >= 200 ? "+" : ""}</span></div>
      <ul class="items" id="itemList">
        ${items.map((it, i) => {
          const [l, x] = itemLabel(it, a.query && a.query.obj);
          const n = (it && it.n) || i + 1;
          const can = !!itemRect(it, a);
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
    sel.innerHTML = runs.map((r) => `<option value="${esc(r.id)}">${esc(r.id)}</option>`).join("");
    sel.onchange = () => loadRun(sel.value);
    runsLoaded = true;
    loadRun(runs[0].id);
  } catch (e) {
    $("#runHeadline").textContent = "Could not read results: " + e.message;
  }
}

async function loadRun(id) {
  try {
    runData = await api("/api/results/" + encodeURIComponent(id));
  } catch (e) { toast("Could not load run: " + e.message, true); return; }
  const d = runData;
  $("#runHeader").textContent = d.header || d.id;
  const all = d.rows.find((r) => r.source === "ALL" && r.type === "ALL");
  const split = d.id.split("-")[0];
  $("#runHeadline").innerHTML = all
    ? `On the <strong>${esc(split)}</strong> split, <strong>${pct(all.accuracy)}</strong> of ${num(all.n)} questions were answered correctly. The system answered <strong>${pct(all.coverage)}</strong> of them and was right on <strong>${pct(all.precision)}</strong> of those, at <strong>$${Number(all.cost_per_q || 0).toFixed(4)}</strong> per question.`
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
  renderExamples();
  loadStatus();
  await loadDrawings();
  const p = new URLSearchParams(location.hash.slice(1));
  if (p.get("path")) {
    await selectDrawing(p.get("path"));
    if (p.get("q")) { $("#question").value = p.get("q"); ask(); }
  }
  if (p.get("view") === "results") showView("results");
})();
