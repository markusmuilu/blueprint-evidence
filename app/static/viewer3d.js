/* 3D view of an IFC model, storeys stacked and separable. ES module; three.js from a pinned CDN build.
   The rest of the app talks to it through window.Viewer3D (open, highlight, select, close). */
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* Colors come from the CSS tokens (OKLCH), read back as sRGB through a 1px canvas. */
function cssColor(expr) {
  const c = document.createElement("canvas");
  c.width = c.height = 1;
  const g = c.getContext("2d");
  g.fillStyle = "#000";
  g.fillStyle = expr;
  g.fillRect(0, 0, 1, 1);
  const [r, gg, b] = g.getImageData(0, 0, 1, 1).data;
  return new THREE.Color(r / 255, gg / 255, b / 255).convertSRGBToLinear();
}
const tok = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

const GLASS = /Window|Plate|CurtainWall/;
function baseTone(cls) {
  // neutral slate family; slabs and roofs lighter so floors read as layers, doors and windows a step apart
  if (/Slab|Roof|Covering/.test(cls)) return "oklch(0.80 0.010 245)";
  if (/Wall/.test(cls)) return "oklch(0.70 0.012 245)";
  if (/Door/.test(cls)) return "oklch(0.60 0.020 60)";
  if (GLASS.test(cls)) return "oklch(0.62 0.040 220)";
  if (/Stair|Railing|Ramp/.test(cls)) return "oklch(0.58 0.010 245)";
  if (/Column|Beam|Member|Footing/.test(cls)) return "oklch(0.64 0.010 245)";
  if (/Site/.test(cls)) return "oklch(0.36 0.010 245)";
  return "oklch(0.52 0.012 245)";                 // furniture, MEP, proxies
}

const V = {
  path: null, meta: null, gen: 0,
  renderer: null, scene: null, camera: null, controls: null,
  floors: [],            // [{i, name, group, solid: {mesh, table, pos}, space: {...}, visible, elev}]
  byGuid: new Map(),     // guid -> {floor, kind, row}
  highlight: { cited: new Set(), deducted: new Set(), rooms: new Set() },
  hiMeshes: [], selMesh: null, selected: null,
  explode: 0.6, roomsOn: false, timings: {},
  colors: null, needs: true,
};

function colors() {
  if (V.colors) return V.colors;
  V.colors = {
    bg: cssColor(tok("--well") || "#111"),
    cited: cssColor(tok("--accent")),
    sel: cssColor(tok("--fg")),
    deducted: cssColor(tok("--warn")),
    room: cssColor(tok("--accent")),
    dim: cssColor("oklch(0.66 0.012 245)"),
    tones: {},
  };
  return V.colors;
}
function toneFor(cls) {
  const c = colors();
  if (!c.tones[cls]) c.tones[cls] = cssColor(baseTone(cls));
  return c.tones[cls];
}

/* ---------------------------------------------------------------- scene */
function ensureScene(host) {
  if (V.renderer) return;
  const canvas = $("#v3dCanvas", host);
  V.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: "high-performance" });
  V.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  V.scene = new THREE.Scene();
  V.scene.background = colors().bg;
  V.camera = new THREE.PerspectiveCamera(40, 1, 0.1, 5000);
  V.controls = new OrbitControls(V.camera, canvas);
  V.controls.enableDamping = false;
  V.controls.addEventListener("change", () => (V.needs = true));
  V.scene.add(new THREE.HemisphereLight(0xffffff, 0x3a4250, 2.2));
  const sun = new THREE.DirectionalLight(0xffffff, 1.6);
  sun.position.set(0.6, 1, 0.35);
  V.scene.add(sun);
  new ResizeObserver(() => resize()).observe(host);
  resize();
  canvas.addEventListener("pointerdown", (e) => { V.down = [e.clientX, e.clientY]; });
  canvas.addEventListener("pointerup", (e) => {
    if (!V.down || Math.hypot(e.clientX - V.down[0], e.clientY - V.down[1]) > 4) return;
    pick(e);
  });
  const loop = () => {
    requestAnimationFrame(loop);
    if (!V.needs || !V.host || V.host.hidden) return;
    V.needs = false;
    V.renderer.render(V.scene, V.camera);
    if (V.timings.firstFrameAt == null && V.floors.some((f) => f.solid?.mesh)) {
      V.timings.firstFrameAt = performance.now();
      V.timings.first_frame_ms = Math.round(V.timings.firstFrameAt - V.timings.t0);
      status();
    }
  };
  loop();
}

function resize() {
  if (!V.renderer || !V.host) return;
  const r = V.host.getBoundingClientRect();
  if (!r.width || !r.height) return;
  V.renderer.setSize(r.width, r.height, false);
  V.camera.aspect = r.width / r.height;
  V.camera.updateProjectionMatrix();
  V.needs = true;
}

function clearScene() {
  for (const f of V.floors) {
    V.scene.remove(f.group);
    for (const k of ["solid", "space"]) if (f[k]) f[k].mesh.geometry.dispose();
  }
  clearHighlightMeshes();
  V.floors = [];
  V.byGuid.clear();
  V.selected = null;
  if (V.selMesh) { V.selMesh.parent?.remove(V.selMesh); V.selMesh = null; }
}

/* ---------------------------------------------------------------- loading */
async function fetchMeta(path, gen) {
  const t0 = performance.now();
  for (;;) {
    const r = await fetch("/api/ifc3d?path=" + encodeURIComponent(path));
    if (gen !== V.gen) return null;
    if (r.status === 200) { V.timings.meta_ms = Math.round(performance.now() - t0); return r.json(); }
    let j = {};
    try { j = await r.json(); } catch (_) { /* not JSON */ }
    if (r.status === 202) {
      V.timings.built_here = true;
      busy(`Building 3D geometry for ${j.model || "the model"} (${j.mb ?? "?"} MB): ${j.stage || "starting"}, ${j.elapsed_s ?? 0} s`);
      await new Promise((ok) => setTimeout(ok, 700));
      continue;
    }
    throw new Error(j.error || j.detail || r.statusText);
  }
}

function buildFloor(entry, kind, buf) {
  const info = entry[kind];
  const pos = new Float32Array(buf, 0, info.nv * 3);
  const idx = new Uint32Array(buf, info.nv * 12, info.nt * 3);
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  g.setIndex(new THREE.BufferAttribute(idx, 1));
  let mat;
  if (kind === "solid") {
    const col = new Float32Array(info.nv * 3);
    g.setAttribute("color", new THREE.BufferAttribute(col, 3));
    mat = new THREE.MeshLambertMaterial({ vertexColors: true, flatShading: true, side: THREE.DoubleSide,
      transparent: true, opacity: 1, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 });
  } else {
    mat = new THREE.MeshBasicMaterial({ color: colors().room, transparent: true, opacity: 0.16, depthWrite: false, side: THREE.DoubleSide });
  }
  const mesh = new THREE.Mesh(g, mat);
  mesh.matrixAutoUpdate = true;
  mesh.visible = kind === "solid" || V.roomsOn;
  mesh.userData = { kind, floor: entry.i };
  return mesh;
}

function paintBase(f) {
  if (!f.solid?.mesh) return;
  const hiOn = hasHighlight();
  // with an answer on screen: storeys holding its elements keep their tones as a see-through shell,
  // the other storeys fade to a faint outline so they do not haze the view
  const hot = hiOn && V.hotFloors?.has(f);
  const col = f.solid.mesh.geometry.getAttribute("color");
  const arr = col.array;
  const classes = V.meta.classes;
  const dim = colors().dim;
  for (const row of f.solid.table) {
    const [, ci, , v0, nv] = row;
    const c = hiOn && !hot ? dim : toneFor(classes[ci]);
    for (let v = v0; v < v0 + nv; v++) { arr[v * 3] = c.r; arr[v * 3 + 1] = c.g; arr[v * 3 + 2] = c.b; }
  }
  col.needsUpdate = true;
  const m = f.solid.mesh.material;
  m.opacity = !hiOn ? 1 : hot ? 0.22 : 0.035;
  m.depthWrite = !hiOn;
  m.needsUpdate = true;
}

async function open(path, host) {
  V.host = host;
  ensureScene(host);
  resize();
  if (V.path === path && V.meta) { applyHighlight(); return V.meta; }
  const gen = ++V.gen;
  V.path = path;
  V.meta = null;
  V.timings = { t0: performance.now() };
  clearScene();
  renderPanel();
  busy("Loading the model geometry");
  let meta;
  try {
    meta = await fetchMeta(path, gen);
  } catch (e) {
    if (gen === V.gen) fail(e.message);
    return null;
  }
  if (!meta || gen !== V.gen) return null;
  V.meta = meta;
  V.timings.geom_bytes = 0;
  const jobs = [];
  for (const entry of meta.storeys) {
    // the site and unassigned group (terrain, site objects) stays at its own height and starts hidden
    const site = entry.elevation == null;
    const f = { i: entry.i, name: entry.name, entry, group: new THREE.Group(), visible: !site, site, z: entry.z };
    V.floors.push(f);
    V.scene.add(f.group);
    for (const kind of ["solid", "space"]) {
      if (!entry[kind]) continue;
      f[kind] = { table: entry[kind].elements };
      entry[kind].elements.forEach((row) => V.byGuid.set(row[0], { floor: f, kind, row }));
      jobs.push({ f, kind, entry });
    }
  }
  renderPanel();
  layout();
  // solids first (largest floors first fill the view), then rooms; four requests in flight
  jobs.sort((a, b) => (a.kind === b.kind ? b.entry[b.kind].bytes - a.entry[a.kind].bytes : a.kind === "solid" ? -1 : 1));
  let done = 0;
  const worker = async () => {
    while (jobs.length) {
      const j = jobs.shift();
      const r = await fetch(`/api/ifc3d/geom?path=${encodeURIComponent(path)}&s=${j.entry.i}&k=${j.kind}`);
      if (gen !== V.gen) return;
      if (!r.ok) continue;
      const buf = await r.arrayBuffer();
      if (gen !== V.gen) return;
      V.timings.geom_bytes += j.entry[j.kind].bytes;
      const mesh = buildFloor(j.entry, j.kind, buf);
      j.f[j.kind].mesh = mesh;
      j.f.group.add(mesh);
      if (j.kind === "solid") {
        paintBase(j.f);
        mesh.geometry.computeBoundingBox();
        if (!V.framed && j.f.visible) frame(worldBox());
      }
      done++;
      busy(`Loading storeys ${done} / ${meta.storeys.length}`, true);
      V.needs = true;
    }
  };
  V.framed = false;
  await Promise.all([worker(), worker(), worker(), worker()]);
  if (gen !== V.gen) return null;
  V.timings.geometry_ms = Math.round(performance.now() - V.timings.t0);
  // one full frame with every storey, timed to the end of GPU work (upload of all buffers included)
  const tr = performance.now();
  V.renderer.render(V.scene, V.camera);
  V.renderer.getContext().finish();
  V.timings.full_render_ms = Math.round(performance.now() - tr);
  V.timings.ready_ms = Math.round(performance.now() - V.timings.t0);
  frame(worldBox(), true);
  applyHighlight();
  if (hasHighlight()) focusHighlight();
  busy(null);
  status();
  return meta;
}

/* Storeys are stacked by their own elevation; "explode" adds a gap of up to 1.6 storey heights between them. */
function layout() {
  const real = V.floors.filter((f) => f.z);
  const hs = real.map((f) => f.z[1] - f.z[0]).filter((h) => h > 0.5 && h < 30).sort((a, b) => a - b);
  const h = hs.length ? hs[Math.floor(hs.length / 2)] : 3;
  let k = 0;
  for (const f of V.floors) {
    f.group.visible = f.visible;
    f.group.position.y = f.visible && !f.site ? k * V.explode * h * 1.6 : 0;
    if (f.visible && !f.site) k++;
  }
  V.needs = true;
}

// the box of what is on screen, storey offsets included
function worldBox() {
  const b = new THREE.Box3();
  for (const f of V.floors) {
    if (!f.visible || !f.solid?.mesh) continue;
    const g = f.solid.mesh.geometry;
    if (!g.boundingBox) g.computeBoundingBox();
    const bb = g.boundingBox.clone();
    bb.min.y += f.group.position.y; bb.max.y += f.group.position.y;
    b.union(bb);
  }
  return b;
}

function frame(box, final = false, tight = 1.25) {
  if (box.isEmpty()) return;
  const c = box.getCenter(new THREE.Vector3());
  const s = box.getSize(new THREE.Vector3());
  const r = Math.max(s.x, s.z, s.y) * 0.5 + 1;
  const d = r / Math.tan((V.camera.fov * Math.PI) / 360) * tight;
  V.controls.target.copy(c);
  V.camera.position.set(c.x + d * 0.62, c.y + d * 0.55, c.z + d * 0.62);
  V.camera.near = Math.max(0.05, d / 500);
  V.camera.far = d * 20;
  V.camera.updateProjectionMatrix();
  V.controls.update();
  V.framed = true;
  if (final) V.home = { pos: V.camera.position.clone(), target: c.clone() };
  V.needs = true;
}

/* ---------------------------------------------------------------- highlight */
function hasHighlight() {
  const h = V.highlight;
  return h.cited.size + h.deducted.size + h.rooms.size > 0;
}
function clearHighlightMeshes() {
  for (const m of V.hiMeshes) { m.parent?.remove(m); m.geometry.dispose(); }
  V.hiMeshes = [];
}

// a mesh that shares a floor's vertex buffer and draws only the given elements' triangles
function subsetMesh(f, kind, rows, material) {
  const src = f[kind].mesh.geometry;
  const idx = src.getIndex().array;
  let n = 0;
  for (const r of rows) n += r[6];
  const out = new Uint32Array(n);
  let o = 0;
  for (const r of rows) { out.set(idx.subarray(r[5], r[5] + r[6]), o); o += r[6]; }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", src.getAttribute("position"));
  g.setIndex(new THREE.BufferAttribute(out, 1));
  const m = new THREE.Mesh(g, material);
  m.userData = { kind: "hi", rows, floor: f.i, src: kind };
  f.group.add(m);
  V.hiMeshes.push(m);
  return m;
}

function applyHighlight() {
  if (!V.meta) return;
  clearHighlightMeshes();
  const c = colors();
  const sets = [
    ["cited", new THREE.MeshLambertMaterial({ color: c.cited, flatShading: true, side: THREE.DoubleSide })],
    ["deducted", new THREE.MeshLambertMaterial({ color: c.deducted, flatShading: true, side: THREE.DoubleSide })],
  ];
  const roomMat = new THREE.MeshBasicMaterial({ color: c.room, transparent: true, opacity: 0.5, depthWrite: false, side: THREE.DoubleSide });
  V.hotFloors = new Set();
  for (const s of ["cited", "deducted", "rooms"]) for (const g of V.highlight[s]) { const hit = V.byGuid.get(g); if (hit) V.hotFloors.add(hit.floor); }
  for (const f of V.floors) paintBase(f);
  const found = { cited: 0, deducted: 0, rooms: 0 };
  for (const f of V.floors) {
    for (const [name, mat] of sets) {
      const rows = [];
      for (const g of V.highlight[name]) {
        const hit = V.byGuid.get(g);
        if (hit && hit.floor === f && f[hit.kind]?.mesh) {
          if (hit.kind === "space") { if (!V.highlight.rooms.has(g)) V.highlight.rooms.add(g); continue; }
          rows.push(hit.row);
        }
      }
      if (rows.length) { subsetMesh(f, "solid", rows, mat); found[name] += rows.length; }
    }
    const rrows = [];
    for (const g of V.highlight.rooms) {
      const hit = V.byGuid.get(g);
      if (hit && hit.floor === f && hit.kind === "space" && f.space?.mesh) rrows.push(hit.row);
    }
    if (rrows.length) { subsetMesh(f, "space", rrows, roomMat).renderOrder = 2; found.rooms += rrows.length; }
  }
  V.found = found;
  if (V.selected) select(V.selected, false);
  renderPanel();
  V.needs = true;
}

function setHighlight(h) {
  V.highlight = { cited: new Set(h.cited || []), deducted: new Set(h.deducted || []), rooms: new Set(h.rooms || []) };
  V.selected = null;
  if (V.selMesh) { V.selMesh.parent?.remove(V.selMesh); V.selMesh = null; }
  hideCard();
  applyHighlight();
  // bring highlighted storeys to the front of attention: hide nothing, but frame them when any are known
  const fl = new Set();
  for (const s of ["cited", "deducted", "rooms"]) for (const g of V.highlight[s]) { const hit = V.byGuid.get(g); if (hit) fl.add(hit.floor); }
  V.hiFloors = fl;
  // cut the building above the answer's highest storey (as a section would); the checkboxes bring them back
  const real = V.floors.filter((x) => !x.site);
  const top = Math.max(-1, ...real.map((x, k) => (fl.has(x) ? k : -1)));
  if (top >= 0) { real.forEach((x, k) => { x.visible = k <= top; }); layout(); }
  renderPanel();
  focusHighlight();
}

// frame the answer's elements: the box of their own vertices, storey offset included
function focusHighlight() {
  const b = new THREE.Box3(), v = new THREE.Vector3();
  for (const s of ["cited", "deducted", "rooms"]) for (const g of V.highlight[s]) {
    const hit = V.byGuid.get(g);
    const mesh = hit && hit.floor[hit.kind]?.mesh;
    if (!mesh || !hit.floor.visible) continue;
    const pos = mesh.geometry.getAttribute("position").array, [, , , v0, nv] = hit.row;
    for (let i = v0; i < v0 + nv; i++) { v.set(pos[i * 3], pos[i * 3 + 1] + hit.floor.group.position.y, pos[i * 3 + 2]); b.expandByPoint(v); }
  }
  if (b.isEmpty()) return;
  const s = b.getSize(new THREE.Vector3()), r = Math.max(s.x, s.y, s.z, 6);
  b.expandByScalar(Math.max(3, Math.min(r * 0.4, 6)));                        // keep the storey around them in view
  frame(b, false, 0.8);
}

/* ---------------------------------------------------------------- picking and the element card */
const ray = new THREE.Raycaster();
function pick(e) {
  if (!V.meta) return;
  const r = V.renderer.domElement.getBoundingClientRect();
  const p = new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
  ray.setFromCamera(p, V.camera);
  const targets = [];
  for (const f of V.floors) {
    if (!f.visible) continue;
    if (f.solid?.mesh) targets.push(f.solid.mesh);
    if (f.space?.mesh?.visible) targets.push(f.space.mesh);
  }
  for (const m of V.hiMeshes) if (m.parent?.visible) targets.push(m);
  const hits = ray.intersectObjects(targets, false);
  // highlighted elements win over the x-rayed shell in front of them
  const hit = hits.find((h) => h.object.userData.kind === "hi") || hits[0];
  if (!hit) { select(null); return; }
  const ud = hit.object.userData;
  let row;
  if (ud.kind === "hi") {
    let t = hit.faceIndex * 3, acc = 0;
    row = ud.rows.find((rw) => { acc += rw[6]; return t < acc; });
  } else {
    const f = V.floors.find((x) => x.i === ud.floor);
    const table = f[ud.kind].table, t = hit.faceIndex * 3;
    let lo = 0, hi = table.length - 1;
    while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (table[mid][5] <= t) lo = mid; else hi = mid - 1; }
    row = table[lo];
  }
  if (row) select(row[0]);
}

function select(guid, fetchCard = true) {
  if (V.selMesh) { V.selMesh.parent?.remove(V.selMesh); V.selMesh.geometry.dispose(); V.hiMeshes = V.hiMeshes.filter((m) => m !== V.selMesh); V.selMesh = null; }
  V.selected = guid;
  if (!guid) { hideCard(); V.needs = true; return; }
  const hit = V.byGuid.get(guid);
  if (!hit || !hit.floor[hit.kind]?.mesh) { hideCard(); return; }
  if (!hit.floor.visible) { hit.floor.visible = true; layout(); renderPanel(); }
  const mat = hit.kind === "space"
    ? new THREE.MeshBasicMaterial({ color: colors().sel, transparent: true, opacity: 0.7, depthWrite: false, side: THREE.DoubleSide })
    : new THREE.MeshBasicMaterial({ color: colors().sel, side: THREE.DoubleSide, depthTest: false, transparent: true, opacity: 0.85 });
  V.selMesh = subsetMesh(hit.floor, hit.kind, [hit.row], mat);
  V.selMesh.renderOrder = 5;
  V.needs = true;
  if (fetchCard) showCard(guid, hit);
}

function hideCard() { const c = $("#v3dCard"); if (c) c.hidden = true; }
async function showCard(guid, hit) {
  const card = $("#v3dCard");
  const cls = V.meta.classes[hit.row[1]];
  const role = V.highlight.deducted.has(guid) ? "Deducted opening" : V.highlight.cited.has(guid) ? "Used by the answer"
    : V.highlight.rooms.has(guid) ? "Room used by the answer" : "Not used by the answer";
  card.innerHTML = `<div class="v3d-card-head"><span class="mono v3d-cls">${esc(cls)}</span>
      <button class="tool" type="button" id="v3dCardClose" aria-label="Close the element details"><svg class="ico"><use href="#i-close"/></svg></button></div>
    <p class="v3d-name">${esc(hit.row[2] || "(no name)")}</p>
    <dl class="v3d-dl"><dt>GlobalId</dt><dd class="mono">${esc(guid)}</dd><dt>Storey</dt><dd>${esc(storeyLabel(hit.floor.name))}</dd><dt>Role</dt><dd>${esc(role)}</dd></dl>
    <div id="v3dProps" class="meta">Reading properties</div>`;
  card.hidden = false;
  $("#v3dCardClose").addEventListener("click", () => select(null));
  try {
    const r = await fetch(`/api/ifc3d/element?path=${encodeURIComponent(V.path)}&guid=${encodeURIComponent(guid)}`);
    if (V.selected !== guid) return;
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    const d = await r.json();
    const head = [["Type", d.type], ["Tag", d.tag], ["Object type", d.object_type], ["STEP id", "#" + d.step_id]].filter(([, v]) => v);
    const ps = Object.entries(d.psets || {});
    $("#v3dProps").outerHTML = `<dl class="v3d-dl">${head.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl>
      ${ps.map(([n, rows]) => `<details class="v3d-pset"><summary>${esc(n)} <span class="meta mono">${Object.keys(rows).length}</span></summary>
        <dl class="v3d-dl">${Object.entries(rows).map(([k, v]) => `<dt>${esc(k)}</dt><dd class="mono">${esc(v)}</dd>`).join("")}</dl></details>`).join("")
        || `<p class="meta">No property sets on this element.</p>`}`;
  } catch (e) {
    const p = $("#v3dProps");
    if (p) p.textContent = "Properties could not be read: " + e.message;
  }
}

/* ---------------------------------------------------------------- side panel */
function renderPanel() {
  const el = $("#v3dPanel");
  if (!el) return;
  if (!V.meta) { el.innerHTML = ""; return; }
  const f = V.found || { cited: 0, deducted: 0, rooms: 0 };
  const any = hasHighlight();
  const legend = any ? `<ul class="v3d-legend">
      ${f.cited ? `<li><i class="sw cited"></i>Used by the answer <span class="mono">${f.cited}</span></li>` : ""}
      ${f.rooms ? `<li><i class="sw room"></i>Rooms in the sum <span class="mono">${f.rooms}</span></li>` : ""}
      ${f.deducted ? `<li><i class="sw ded"></i>Openings deducted <span class="mono">${f.deducted}</span></li>` : ""}
      ${!f.cited && !f.rooms && !f.deducted ? `<li class="meta">The answer's elements have no geometry in this model.</li>` : ""}
    </ul>` : `<p class="meta">Ask a question: the elements it uses are drawn in blue, deducted openings in amber.</p>`;
  el.innerHTML = `
    <div class="v3d-row"><label class="label" for="v3dExplode">Separate storeys</label>
      <input type="range" id="v3dExplode" min="0" max="1" step="0.05" value="${V.explode}" aria-describedby="v3dExplodeV"><output class="mono meta" id="v3dExplodeV">${Math.round(V.explode * 100)}%</output></div>
    <label class="check"><input type="checkbox" id="v3dRooms" ${V.roomsOn ? "checked" : ""}> <span>Show all rooms</span></label>
    ${legend}
    <p class="meta v3d-note">In brackets: the storey name in the model file.</p>
    <ul class="v3d-floors" aria-label="Storeys">${V.floors.slice().reverse().map((fl) => {
      const n = (fl.entry.solid?.elements.length || 0);
      const hot = V.hiFloors?.has(fl);
      return `<li><label class="check${hot ? " hot" : ""}"><input type="checkbox" data-f="${fl.i}" ${fl.visible ? "checked" : ""}> <span>${esc(storeyLabel(fl.name))}</span></label>
        <button class="v3d-only" type="button" data-only="${fl.i}" title="Show only this storey">only</button><span class="mono meta">${n}</span></li>`;
    }).join("")}</ul>
    <button class="btn btn-quiet" type="button" id="v3dHome">Reset view</button>`;
  $("#v3dExplode").addEventListener("input", (e) => { V.explode = +e.target.value; $("#v3dExplodeV").textContent = Math.round(V.explode * 100) + "%"; layout(); });
  $("#v3dRooms").addEventListener("change", (e) => {
    V.roomsOn = e.target.checked;
    for (const fl of V.floors) if (fl.space?.mesh) fl.space.mesh.visible = V.roomsOn;
    V.needs = true;
  });
  el.querySelectorAll("input[data-f]").forEach((cb) => cb.addEventListener("change", () => {
    V.floors.find((x) => x.i === +cb.dataset.f).visible = cb.checked; layout();
  }));
  el.querySelectorAll("button[data-only]").forEach((b) => b.addEventListener("click", () => {
    const only = +b.dataset.only;
    const solo = V.floors.every((x) => x.visible === (x.i === only));
    for (const x of V.floors) x.visible = solo ? true : x.i === only;
    layout(); renderPanel();
  }));
  $("#v3dHome").addEventListener("click", () => frame(worldBox(), true));
}

function busy(msg, soft = false) {
  const b = $("#v3dBusy");
  if (!b) return;
  b.hidden = !msg;
  b.classList.toggle("soft", soft);
  if (msg) $("#v3dBusyText").textContent = msg;
}
function fail(msg) {
  busy(null);
  const b = $("#v3dFail");
  b.hidden = false;
  b.innerHTML = `<p class="empty-title">No 3D view for this model</p><p class="meta">${esc(msg)}. The plan view and the answer still work.</p>`;
  window.dispatchEvent(new CustomEvent("v3d-failed", { detail: msg }));
}
function status() {
  const s = $("#v3dStatus");
  if (!s || !V.meta) return;
  const t = V.timings, m = V.meta;
  const mb = (x) => (x / 1048576).toFixed(1) + " MB";
  s.textContent = `${m.elements.toLocaleString("en-US")} elements, ${(m.triangles / 1000).toFixed(0)}k triangles, ${mb(t.geom_bytes || m.bytes)}`
    + (t.ready_ms != null ? `  ·  ready in ${(t.ready_ms / 1000).toFixed(1)} s` : "")
    + (t.built_here ? `  ·  built on the server in ${m.build_s.total} s` : "");
  s.title = `IfcOpenShell geometry: open ${m.build_s.open} s, tessellation ${m.build_s.geometry} s on ${m.threads} threads (cached per model). `
    + `Browser: meta ${t.meta_ms} ms, first frame ${t.first_frame_ms} ms, all storeys ${t.geometry_ms} ms, first full frame ${t.full_render_ms} ms.`;
}

window.Viewer3D = {
  open, setHighlight, select: (g) => select(g),
  close() { busy(null); },
  timings: () => ({ ...V.timings, build_s: V.meta?.build_s, triangles: V.meta?.triangles, bytes: V.meta?.bytes, elements: V.meta?.elements }),
  has: (g) => V.byGuid.has(g),
  resize,
};
window.dispatchEvent(new Event("v3d-ready"));

/* Storey names in English with the model's own name kept: "5th floor (5.korrus)", "Basement (Kælder)" (his request, 14 Nov) */
function ordinal(n) { const s = ["th", "st", "nd", "rd"], v = n % 100; return n + (s[(v - 20) % 10] || s[v] || s[0]); }
function storeyLabel(name) {
  const raw = String(name || "").trim();
  const low = raw.toLowerCase().normalize("NFKD").replace(/[̀-ͯ]/g, "");
  let en = null;
  const num = low.match(/^(-?\d+)\s*[.]?\s*(korrus|kerros|sal|etage|etg|floor|og|stock|plan|vaning|krs)\b/) || low.match(/^(korrus|kerros|floor|level|etage)\s*(-?\d+)/);
  if (num) { const n = parseInt(num[1].match(/-?\d+/) ? num[1] : num[2], 10); en = n === 0 ? "Ground floor" : n < 0 ? `Basement ${-n}` : `${ordinal(n)} floor`; }
  else if (/kaelder|kælder|kelder|keller|kellari|basement|untergeschoss|souterrain|kjeller/.test(low)) en = "Basement";
  else if (/vundament|fundament|foundation|perustus/.test(low)) en = "Foundation";
  else if (/^tag\b|katus|katto|roof|dach(?!geschoss)|vesikatto/.test(low)) en = "Roof";
  else if (/erdgeschoss|grundplan|stueplan|maantasokerros|ground|^eg\b|bottenvaning/.test(low)) en = "Ground floor";
  else if (/dachgeschoss|ullakko|attic|loft/.test(low)) en = "Attic";
  else if (/obergeschoss|^og\b/.test(low)) en = "Upper floor";
  if (!en || en.toLowerCase() === low) return raw;
  return `${en} (${raw})`;
}
