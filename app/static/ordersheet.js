/* Order sheet and order check for takeoff answers. Plain JS, no build step.
   The sheet recomputes from the per-room figures the route returned (floor area, perimeter, height,
   each opening's width and vertical range), so the height and waste assumptions can be edited here. */
"use strict";

const OrderSheet = (() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const f2 = (x) => (x == null || !isFinite(x) ? "-" : Number(x).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
  const ico = (id) => `<svg class="ico" aria-hidden="true"><use href="#i-${id}"/></svg>`;
  let cur = null;          // the model on screen: {answer, rows, inputs, ...}

  /* ---------------------------------------------------------------- model from an answer */
  function from(a) {
    if (!a || a.value == null || a.status === "cannot" || a.unit !== "m2") return null;
    const items = (a.items || []).filter((it) => it && typeof it === "object");
    const totals = (items.find((it) => it.totals) || {}).totals || null;
    const rows = items.filter((it) => !it.totals && (it.floor_m2 != null || it.area_m2 != null || it.area != null || it.perimeter_m != null))
      .map((it, i) => ({
        n: it.n ?? i + 1,
        room: it.room || [it.number, it.name || it.label].filter(Boolean).join(" ") || `room ${i + 1}`,
        type: it.type || "",
        guid: it.guid || null,
        floor: it.floor_m2 ?? it.area_m2 ?? it.area ?? null,
        floorSrc: it.floor_source || it.source || "",
        perimeter: it.perimeter_m ?? null,
        perimeterSrc: it.perimeter_source || "",
        height: it.height_m ?? null,
        heightSrc: it.height_source || "",
        tiled: it.tiled_height_m ?? null,
        gross: it.gross_wall_m2 ?? null,
        net: it.net_wall_m2 ?? null,
        modelNet: it.model_net_wall_m2 ?? null,
        openings: (it.openings || []).map((o) => ({ kind: o.kind, id: o.id, width: o.width_m, z0: o.z0_m, z1: o.z1_m,
                                                    deducted: o.deducted_m2, src: o.source })),
      }));
    if (!rows.length) return null;
    const hasWalls = rows.some((r) => r.perimeter != null);
    const inputs = totals?.inputs || {};
    const floorIn = totals ? (totals.floor_m2 || 0) > 0 : true;
    const tk = a.query?.extra?.takeoff;
    return {
      answer: a, rows, hasWalls, totals,
      material: tk?.material || "",
      surfaces: hasWalls ? (floorIn ? "floor+walls" : "walls") : "floor",
      ceiling: inputs.ceiling_m ?? null,        // null: each room's own height
      tile: inputs.tile_m ?? null,              // null: full height
      waste: totals ? (totals.waste_pct ?? 0) : 0,
      defaults: { ceiling: inputs.ceiling_m ?? null, tile: inputs.tile_m ?? null, waste: totals ? (totals.waste_pct ?? 0) : 0 },
      assumedHeight: rows.some((r) => /ASSUMED/i.test(r.heightSrc)),
    };
  }

  function compute(m) {
    let floor = 0, walls = 0, ded = 0, approx = false;
    // with the answer's own height inputs, show the route's per-room figures as returned
    const asAnswered = m.ceiling === m.defaults.ceiling && m.tile === m.defaults.tile;
    const rows = m.rows.map((r) => {
      const out = { ...r };
      floor += r.floor || 0;
      if (r.perimeter == null) return out;
      const h = m.ceiling != null ? m.ceiling : r.height;
      const band = m.tile != null ? Math.min(m.tile, h) : h;
      out.hUsed = h; out.band = band;
      out.grossC = r.perimeter * band;
      out.dedC = 0;
      out.ops = r.openings.map((o) => {
        let d;
        if (o.z0 != null && o.z1 != null) d = Math.max(0, Math.min(o.z1, band) - Math.max(o.z0, 0)) * (o.width || 0);
        else { d = o.deducted || 0; approx = true; }       // answer cached before openings carried their z range
        out.dedC += d;
        return { ...o, d };
      });
      out.netC = Math.max(0, out.grossC - out.dedC);
      if (asAnswered && r.net != null && r.gross != null) {
        out.band = r.tiled ?? band;
        out.grossC = r.gross; out.netC = r.net;
        out.ops = out.ops.map((o) => ({ ...o, d: o.deducted ?? o.d }));
        out.dedC = out.ops.reduce((acc, o) => acc + (o.d || 0), 0);
      }
      walls += out.netC; ded += out.dedC;
      return out;
    });
    const useFloor = m.surfaces !== "walls", useWalls = m.surfaces !== "floor" && m.hasWalls;
    const net = (useFloor ? floor : 0) + (useWalls ? walls : 0);
    const waste = net * (m.waste || 0) / 100;
    const t = m.totals;
    const defaultSurf = m.hasWalls ? (t && t.floor_m2 > 0 ? "floor+walls" : "walls") : "floor";
    if (t && asAnswered && m.waste === m.defaults.waste && m.surfaces === defaultSurf && t.order_m2 != null) {
      // exactly the answer: its totals were summed before rounding, so take them as given
      return { rows, floor: t.floor_m2 ?? floor, walls: t.walls_m2 ?? walls, ded, net: t.net_m2 ?? net, waste: t.waste_m2 ?? waste,
               order: t.order_m2, useFloor, useWalls, approx, asAnswer: true };
    }
    return { rows, floor, walls, ded, net, waste, order: net + waste, useFloor, useWalls, approx };
  }

  /* ---------------------------------------------------------------- order check */
  const UNIT_RE = /(m2|m²|sqm|sq\.?\s*m|square\s*met\w*|neli\w*|m\b|pcs|kpl|pieces?|units?|x\b)?/i;
  function parseOrder(text) {
    const t = String(text || "").trim();
    const mm = t.match(/(-?\d[\d\s]*(?:[.,]\d+)?)/);
    if (!mm) return null;
    const qty = parseFloat(mm[1].replace(/\s/g, "").replace(",", "."));
    const rest = t.slice(mm.index + mm[0].length);
    const um = rest.match(new RegExp("^\\s*" + UNIT_RE.source, "i"));
    const unitRaw = (um && um[1]) || "";
    const unit = /^(m2|m²|sqm|sq|square|neli)/i.test(unitRaw) ? "m2" : /^(pcs|kpl|piece|unit|x)/i.test(unitRaw) ? "count" : unitRaw ? unitRaw.toLowerCase() : "";
    const what = (t.slice(0, mm.index) + " " + rest.slice(um ? um[0].length : 0)).replace(/\s+/g, " ").trim();
    return { qty, unit, what, text: t };
  }

  // which drawing quantity an order line should be compared with, and how that quantity is made up
  function target(order, a, m) {
    const w = order.what.toLowerCase();
    if (!m) {
      if (typeof a.value !== "number") return null;
      return { qty: a.value, unit: a.unit, label: `the answer (${a.unit === "count" ? "count" : a.unit})`, parts: [], exact: a.unit === "count" };
    }
    const c = compute(m);
    const wasteNote = m.waste ? ` + ${m.waste}% waste ${f2(c.waste)}` : "";
    if (/ceiling|katto|alakat|decke|plafond|soffit/.test(w)) {
      return { qty: c.floor, unit: "m2", label: "ceiling area of these rooms (equal to their net floor area, no waste)", parts: c.rows, kind: "floor", c };
    }
    if (/wall|seinä|seina|paint|maali|wand/.test(w) && m.hasWalls) {
      const ww = c.walls * (1 + (m.waste || 0) / 100);
      return { qty: ww, unit: "m2", label: `wall surfaces: net walls ${f2(c.walls)}${wasteNote ? ` + ${m.waste}% waste` : ""}`, parts: c.rows, kind: "walls", c };
    }
    if (/floor|lattia|parquet|parketti|screed|tasoite|carpet|matto|vinyl|laminate|boden/.test(w) && !/tile|laatta/.test(w)) {
      const ff = c.floor * (1 + (m.waste || 0) / 100);
      return { qty: ff, unit: "m2", label: `floor area ${f2(c.floor)}${m.waste ? ` + ${m.waste}% waste` : ""}`, parts: c.rows, kind: "floor", c };
    }
    const bits = [c.useFloor ? `floor ${f2(c.floor)}` : "", c.useWalls ? `walls ${f2(c.walls)}` : ""].filter(Boolean).join(" + ");
    return { qty: c.order, unit: "m2", label: `order quantity: ${bits}${wasteNote}`, parts: c.rows, kind: m.surfaces, c };
  }

  function verdict(order, tg) {
    const diff = order.qty - tg.qty;
    const rel = tg.qty ? diff / tg.qty : (diff ? Infinity : 0);
    const ok = tg.exact ? diff === 0 : Math.abs(rel) <= 0.02;
    return { diff, rel, ok, tone: ok ? "ok" : diff > 0 ? "warn" : "bad" };
  }

  function checkHTML(text, a, m) {
    const o = parseOrder(text);
    if (!o) return `<p class="meta">Type a quantity, for example "184 m2 suspended ceiling" or "120 m2 tiles".</p>`;
    const tg = target(o, a, m);
    if (!tg) return `<p class="meta">This answer is not a number, so there is nothing to compare an order with.</p>`;
    if (o.unit && tg.unit && o.unit !== tg.unit && !(o.unit === "m" && tg.unit === "m")) {
      return `<p class="oc-verdict bad">${ico("cannot")}<span>Units differ: the order is in ${esc(o.unit)}, the drawing quantity in ${esc(tg.unit === "m2" ? "m²" : tg.unit)}.</span></p>`;
    }
    const v = verdict(o, tg);
    const u = tg.unit === "m2" ? "m²" : tg.unit === "count" ? "" : tg.unit;
    const amt = tg.unit === "count" ? Math.abs(v.diff) : f2(Math.abs(v.diff));
    const pct = isFinite(v.rel) ? ` (${v.diff > 0 ? "+" : "-"}${Math.abs(v.rel * 100).toFixed(1)}%)` : "";
    const head = v.ok ? `Order matches the drawing within ${tg.exact ? "an exact count" : "2%"}`
      : v.diff > 0 ? `Order is ${amt} ${u} over the drawing${pct}` : `Order is ${amt} ${u} short of the drawing${pct}`;
    const ev = (tg.parts || []).filter((r) => (tg.kind === "walls" ? r.netC : r.floor) != null).slice(0, 6).map((r) =>
      `<li><span class="cite">#${esc(r.n)}</span><span class="l">${esc(r.room)}</span><span class="mono">${f2(tg.kind === "walls" ? r.netC : tg.kind === "floor" ? r.floor : (tg.c.useFloor ? r.floor || 0 : 0) + (tg.c.useWalls ? r.netC || 0 : 0))}</span></li>`).join("");
    return `<p class="oc-verdict ${v.tone}">${ico(v.ok ? "proven" : v.diff > 0 ? "estimate" : "cannot")}<span>${esc(head)}</span></p>
      <dl class="oc-cmp">
        <dt>On the order</dt><dd class="mono">${esc(tg.unit === "count" ? o.qty : f2(o.qty))} ${esc(u)}${o.what ? ` <span class="meta">${esc(o.what)}</span>` : ""}</dd>
        <dt>From the drawing</dt><dd class="mono">${esc(tg.unit === "count" ? tg.qty : f2(tg.qty))} ${esc(u)} <span class="meta">${esc(tg.label)}</span></dd>
        <dt>Difference</dt><dd class="mono">${v.diff > 0 ? "+" : v.diff < 0 ? "-" : ""}${esc(amt)} ${esc(u)}</dd>
      </dl>
      ${ev ? `<ul class="oc-ev" aria-label="Rooms behind the drawing quantity">${ev}${(tg.parts || []).length > 6 ? `<li class="more"><span></span><span class="l">and ${(tg.parts || []).length - 6} more rooms, each numbered on the drawing and in the order sheet</span><span></span></li>` : ""}</ul>` : ""}
      ${tg.c?.approx ? `<p class="note">Openings use the deducted areas from the answer; run the question again to edit the tiled height.</p>` : ""}`;
  }

  /* ---------------------------------------------------------------- answer-panel section */
  function mountPanel(a, sec, hooks = {}) {
    if (!sec) return;
    const m = cur && cur.answer === a ? cur : from(a);
    const numeric = a && typeof a.value === "number" && a.status !== "cannot";
    cur = m;
    if (!numeric) { sec.hidden = true; return; }
    sec.hidden = false;
    const c = m ? compute(m) : null;
    const summary = m ? `<p class="os-sum"><span class="mono os-q">${f2(c.order)} m²</span> to order:
        ${c.useFloor ? `floor <span class="mono">${f2(c.floor)}</span>` : ""}${c.useFloor && c.useWalls ? " + " : ""}${c.useWalls ? `walls <span class="mono">${f2(c.walls)}</span>` : ""}${m.waste ? ` + ${esc(m.waste)}% waste` : ", no waste"}</p>` : "";
    sec.innerHTML = `
      <div class="sec-head"><h3 class="h-panel" id="hOrder">${m ? "Order" : "Order check"}</h3>${m ? `<span class="meta">${m.rows.length} room${m.rows.length > 1 ? "s" : ""}</span>` : ""}</div>
      ${summary}
      ${m ? `<button class="btn" type="button" id="osOpen">${ico("sheet")}Open order sheet</button>` : ""}
      <form class="oc" id="ocForm" autocomplete="off">
        <label class="label" for="ocInput">Check an order against the drawing</label>
        <div class="ask-row"><input id="ocInput" type="text" placeholder="${m ? "184 m2 suspended ceiling" : "12 doors"}" maxlength="120"><button class="btn" type="submit">Check</button></div>
      </form>
      <div id="ocOut" aria-live="polite"></div>`;
    const run = () => {
      const v = sec.querySelector("#ocInput").value;
      sec.querySelector("#ocOut").innerHTML = v.trim() ? checkHTML(v, a, cur) : "";
    };
    sec.querySelector("#ocForm").addEventListener("submit", (e) => { e.preventDefault(); run(); });
    sec.querySelector("#osOpen")?.addEventListener("click", () => openSheet(a, hooks));
    sec._rerun = run;
    if (hooks.check) { sec.querySelector("#ocInput").value = hooks.check; run(); }
  }

  /* ---------------------------------------------------------------- the sheet dialog */
  function sheetBody(m, c) {
    const a = m.answer;
    const walls = m.hasWalls;
    const head = `<tr><th class="num">#</th><th>Room</th><th class="num">Floor m²</th>${walls ? `<th class="num">Perimeter m</th><th class="num">Height m</th><th class="num">Tiled to m</th><th class="num">Gross wall m²</th><th>Openings deducted</th><th class="num">Deducted m²</th><th class="num">Net wall m²</th>` : ""}<th class="num">Row m²</th></tr>`;
    const body = c.rows.map((r) => {
      const rowTotal = (c.useFloor ? r.floor || 0 : 0) + (c.useWalls ? r.netC || 0 : 0);
      const ops = (r.ops || []).map((o) => `<span class="op" title="${esc(o.src || "")}">${esc(o.kind)} ${f2(o.width)} m${o.z0 != null ? `, ${f2(o.z0)} to ${f2(o.z1)} m` : ""} <span class="mono">${f2(o.d)}</span></span>`).join("");
      return `<tr data-guid="${esc(r.guid || "")}"><td class="num">${esc(r.n)}</td><td class="wrap">${esc(r.room)}${r.type ? ` <span class="meta">${esc(r.type)}</span>` : ""}</td><td class="num">${f2(r.floor)}</td>${walls ? `
        <td class="num">${f2(r.perimeter)}</td><td class="num" title="${esc(r.heightSrc)}">${f2(r.hUsed)}${/ASSUMED/i.test(r.heightSrc) && m.ceiling == null ? "*" : ""}</td><td class="num">${f2(r.band)}</td>
        <td class="num">${f2(r.grossC)}</td><td class="wrap ops">${ops || '<span class="meta">none</span>'}</td><td class="num">${f2(r.dedC)}</td><td class="num">${f2(r.netC)}</td>` : ""}
        <td class="num">${f2(rowTotal)}</td></tr>`;
    }).join("");
    const tot = `<tr class="total"><td></td><td>Total</td><td class="num">${f2(c.floor)}</td>${walls ? `<td></td><td></td><td></td><td class="num">${f2(c.rows.reduce((s, r) => s + (r.grossC || 0), 0))}</td><td></td><td class="num">${f2(c.ded)}</td><td class="num">${f2(c.walls)}</td>` : ""}<td class="num">${f2(c.net)}</td></tr>`;
    const srcs = [...new Set(m.rows.flatMap((r) => [r.floorSrc && `Floor: ${r.floorSrc}`, r.perimeterSrc && `Perimeter: ${r.perimeterSrc}`, r.heightSrc && m.ceiling == null && `Height: ${r.heightSrc}`]).filter(Boolean))];
    const differs = m.totals && m.ceiling === m.defaults.ceiling && m.tile === m.defaults.tile && m.waste === m.defaults.waste
      && Math.abs(c.order - m.totals.order_m2) > 0.02;
    return { head, body, tot, srcs, differs, a };
  }

  function renderSheet(dlg, m, hooks) {
    const c = compute(m);
    const s = sheetBody(m, c);
    const a = m.answer;
    const date = new Date().toISOString().slice(0, 10);
    dlg.innerHTML = `
      <div class="sheet">
        <header class="sheet-head">
          <div>
            <h2 class="h-page" id="osTitle">Order sheet</h2>
            <p class="meta mono">${esc(a.drawing)}  ·  ${esc(date)}</p>
            <p class="sheet-q">${esc(a.question)}</p>
          </div>
          <button class="tool" type="button" id="osClose" aria-label="Close the order sheet"><svg class="ico"><use href="#i-close"/></svg></button>
        </header>
        <section class="sheet-inputs" aria-label="Assumptions">
          ${m.hasWalls ? `<div class="field"><label class="label" for="osSurf">Surfaces</label>
            <select id="osSurf"><option value="floor+walls">Floor and walls</option><option value="floor">Floor only</option><option value="walls">Walls only</option></select></div>
          <div class="field"><label class="label" for="osCeil">Room height, m</label><input id="osCeil" type="number" min="0.5" max="20" step="0.05" placeholder="per room" value="${m.ceiling ?? ""}"></div>
          <div class="field"><label class="label" for="osTile">Tiled to, m</label><input id="osTile" type="number" min="0.1" max="20" step="0.05" placeholder="full height" value="${m.tile ?? ""}"></div>` : ""}
          <div class="field"><label class="label" for="osWaste">Waste, %</label><input id="osWaste" type="number" min="0" max="100" step="1" value="${m.waste}"></div>
          <button class="btn btn-quiet" type="button" id="osReset">Reset to the answer</button>
        </section>
        <div class="table-wrap"><table class="tbl sheet-tbl"><thead>${s.head}</thead><tbody>${s.body}${s.tot}</tbody></table></div>
        <section class="sheet-sum" aria-label="Order quantity">
          <dl>
            ${c.useFloor ? `<dt>Floor</dt><dd class="mono">${f2(c.floor)} m²</dd>` : ""}
            ${c.useWalls ? `<dt>Walls, net of openings</dt><dd class="mono">${f2(c.walls)} m²</dd>` : ""}
            <dt>Net area</dt><dd class="mono">${f2(c.net)} m²</dd>
            <dt>Waste ${esc(m.waste)}%</dt><dd class="mono">${f2(c.waste)} m²</dd>
            <dt class="big">Order quantity</dt><dd class="mono big">${f2(c.order)} m²</dd>
          </dl>
          <div class="sheet-check">
            <form class="oc" id="osCheckForm" autocomplete="off">
              <label class="label" for="osCheck">Order check</label>
              <div class="ask-row"><input id="osCheck" type="text" placeholder="184 m2 suspended ceiling" maxlength="120" value="${esc(dlg._check || "")}"><button class="btn" type="submit">Check</button></div>
            </form>
            <div id="osCheckOut" aria-live="polite"></div>
          </div>
        </section>
        <footer class="sheet-foot">
          <ul class="sheet-src">${s.srcs.map((x) => `<li>${esc(x)}</li>`).join("")}
            ${m.assumedHeight ? `<li>* Height assumed: a plan has no heights; enter the real room height above.</li>` : ""}
            <li>Route ${esc(a.route)}, ${esc(a.status)}. Recomputed here from the per-room figures of the answer; walls = perimeter x tiled height - openings inside the tiled band.</li>
            ${s.differs ? `<li>Per-room figures are rounded to 0.01 m, so the total can differ from the answer by a few hundredths.</li>` : ""}</ul>
          <div class="sheet-actions">
            ${hooks.show3d ? `<button class="btn btn-quiet" type="button" id="os3d">Show rooms in 3D</button>` : ""}
            <button class="btn btn-quiet" type="button" id="osCsv">${ico("download")}Download CSV</button>
            <button class="btn btn-primary" type="button" id="osPrint">Print or save PDF</button>
          </div>
        </footer>
      </div>`;
    const q = (id) => dlg.querySelector(id);
    if (q("#osSurf")) q("#osSurf").value = m.surfaces;
    const upd = () => {
      if (q("#osSurf")) m.surfaces = q("#osSurf").value;
      const num = (el) => (el && el.value.trim() !== "" && isFinite(+el.value) && +el.value > 0 ? +el.value : null);
      m.ceiling = num(q("#osCeil"));
      m.tile = num(q("#osTile"));
      const w = q("#osWaste").value.trim();
      m.waste = w !== "" && isFinite(+w) && +w >= 0 ? +w : 0;
      dlg._check = q("#osCheck").value;
      const focus = document.activeElement?.id;
      renderSheet(dlg, m, hooks);
      if (focus && q("#" + focus)) { const el = q("#" + focus); el.focus(); if (el.setSelectionRange && el.type === "text") el.setSelectionRange(el.value.length, el.value.length); }
      hooks.changed?.(m);
    };
    ["#osSurf", "#osCeil", "#osTile", "#osWaste"].forEach((id) => q(id)?.addEventListener("change", upd));
    q("#osReset").addEventListener("click", () => { Object.assign(m, m.defaults, { surfaces: m.hasWalls ? (m.totals && m.totals.floor_m2 > 0 ? "floor+walls" : "walls") : "floor" }); renderSheet(dlg, m, hooks); hooks.changed?.(m); });
    q("#osClose").addEventListener("click", () => dlg.close());
    q("#osCsv").addEventListener("click", () => downloadCsv(m));
    q("#osPrint").addEventListener("click", () => printSheet(m));
    q("#os3d")?.addEventListener("click", () => { dlg.close(); hooks.show3d(m); });
    const runCheck = () => { dlg._check = q("#osCheck").value; q("#osCheckOut").innerHTML = dlg._check.trim() ? checkHTML(dlg._check, a, m) : ""; };
    q("#osCheckForm").addEventListener("submit", (e) => { e.preventDefault(); runCheck(); });
    if (dlg._check) runCheck();
  }

  function openSheet(a, hooks = {}) {
    const dlg = document.getElementById("orderDlg");
    if (!cur || cur.answer !== a) cur = from(a);
    if (!cur) return;
    renderSheet(dlg, cur, hooks);
    if (!dlg.open) dlg.showModal();
  }

  /* ---------------------------------------------------------------- exports */
  function csvText(m) {
    const c = compute(m);
    const q = (v) => { const s = String(v ?? ""); return /[",\n;]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
    const n = (x) => (x == null || !isFinite(x) ? "" : x.toFixed(2));
    const lines = [];
    lines.push(["drawing", m.answer.drawing], ["question", m.answer.question], ["route", m.answer.route], ["status", m.answer.status],
      ["surfaces", m.surfaces], ["room height m", m.ceiling ?? "per room"], ["tiled to m", m.tile ?? "full height"], ["waste %", m.waste], ...(m.assumedHeight ? [["note", "* Height assumed: a plan has no heights; enter the real room height"]] : []), []);
    lines.push(["n", "room", "type", "guid", "floor_m2", "perimeter_m", "height_m", "tiled_to_m", "gross_wall_m2", "openings", "deducted_m2", "net_wall_m2", "row_m2"]);
    for (const r of c.rows) {
      const rowTotal = (c.useFloor ? r.floor || 0 : 0) + (c.useWalls ? r.netC || 0 : 0);
      lines.push([r.n, r.room, r.type, r.guid || "", n(r.floor), n(r.perimeter), n(r.hUsed) + (/ASSUMED/i.test(r.heightSrc || "") && m.ceiling == null ? " *" : ""), n(r.band), n(r.grossC),
        (r.ops || []).map((o) => `${o.kind} ${o.id || ""} ${n(o.width)}m ${n(o.d)}m2`).join(" | "), n(r.dedC), n(r.netC), n(rowTotal)]);
    }
    lines.push([], ["floor_m2", n(c.floor)], ["walls_m2", n(c.walls)], ["net_m2", n(c.net)], ["waste_m2", n(c.waste)], ["order_m2", n(c.order)]);
    return lines.map((l) => l.map(q).join(",")).join("\n") + "\n";
  }
  function downloadCsv(m) {
    const blob = new Blob([csvText(m)], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `order-sheet-${(m.answer.drawing || "drawing").replace(/\.[^.]+$/, "").replace(/[^\w-]+/g, "_")}.csv`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  }

  // a light, self-contained page for paper: the browser's print dialog saves it as PDF
  function printHTML(m) {
    const c = compute(m);
    const s = sheetBody(m, c);
    const a = m.answer;
    return `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Order sheet - ${esc(a.drawing)}</title>
<style>
  @page { size: A4 landscape; margin: 14mm; }
  body { font: 10pt/1.4 "IBM Plex Sans", "Segoe UI", sans-serif; color: #15191f; margin: 0; }
  @media screen { body { padding: 24px; max-width: 1180px; } }
  h1 { font-size: 18pt; margin: 0 0 2pt; font-weight: 600; }
  .meta { color: #5a6270; font-size: 9pt; }
  .q { margin: 6pt 0 10pt; }
  table { width: 100%; border-collapse: collapse; font-size: 8pt; }
  th { text-align: left; font-weight: 500; color: #5a6270; border-bottom: 1px solid #8a919c; padding: 2pt 3pt; }
  td { border-bottom: 1px solid #d7dbe0; padding: 2pt 3pt; vertical-align: top; }
  th.num { white-space: normal; }
  .num { text-align: right; font-family: "IBM Plex Mono", Consolas, monospace; font-variant-numeric: tabular-nums; }
  tr.total td { font-weight: 600; border-top: 1px solid #15191f; }
  .op { display: block; } .op .mono { font-family: "IBM Plex Mono", Consolas, monospace; }
  dl { display: grid; grid-template-columns: auto auto; justify-content: start; gap: 2pt 18pt; margin: 12pt 0; }
  dd { margin: 0; text-align: right; font-family: "IBM Plex Mono", Consolas, monospace; }
  .big { font-size: 14pt; font-weight: 600; }
  ul { padding-left: 14pt; color: #5a6270; font-size: 8.5pt; }
  .tag { font-weight: 600; }
</style></head><body>
<h1>Order sheet</h1>
<p class="meta">${esc(a.drawing)} · ${new Date().toISOString().slice(0, 10)} · route ${esc(a.route)} · <span class="tag">${esc(a.status)}</span></p>
<p class="q">${esc(a.question)}</p>
<p class="meta">Surfaces: ${esc(m.surfaces)} · room height: ${m.ceiling != null ? f2(m.ceiling) + " m" : "per room"} · tiled to: ${m.tile != null ? f2(m.tile) + " m" : "full height"} · waste ${esc(m.waste)}%</p>
<table><thead>${s.head}</thead><tbody>${s.body.replace(/<span class="meta">none<\/span>/g, "none")}${s.tot}</tbody></table>
<dl>${c.useFloor ? `<dt>Floor</dt><dd>${f2(c.floor)} m²</dd>` : ""}${c.useWalls ? `<dt>Walls, net of openings</dt><dd>${f2(c.walls)} m²</dd>` : ""}
<dt>Net area</dt><dd>${f2(c.net)} m²</dd><dt>Waste ${esc(m.waste)}%</dt><dd>${f2(c.waste)} m²</dd><dt class="big">Order quantity</dt><dd class="big">${f2(c.order)} m²</dd></dl>
<ul>${s.srcs.map((x) => `<li>${esc(x)}</li>`).join("")}${m.assumedHeight ? "<li>* Height assumed: a plan has no heights.</li>" : ""}
<li>Walls = perimeter x tiled height - openings inside the tiled band. Generated by Blueprint Evidence from the drawing; every room is numbered as on the evidence image.</li></ul>
<script>window.addEventListener("load", () => setTimeout(() => window.print(), 150));<\/script>
</body></html>`;
  }
  function printSheet(m) {
    const w = window.open("", "_blank");
    if (!w) { downloadHtml(m); return; }
    w.document.open();
    w.document.write(printHTML(m));
    w.document.close();
  }
  function downloadHtml(m) {
    const blob = new Blob([printHTML(m)], { type: "text/html" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "order-sheet.html";
    link.click();
  }

  return { from, compute, parseOrder, checkHTML, mountPanel, openSheet, csvText, printHTML, current: () => cur };
})();
window.OrderSheet = OrderSheet;
