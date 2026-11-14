// Verification timing study, run with the Playwright MCP `browser_run_code_unsafe` (filename) against the app
// on http://127.0.0.1:8013. For each of 10 answers it times two ways of seeing every counted item:
//   without the strip: select each item in the answer panel list (the viewer zooms to it), capture the viewer;
//   with the strip:    capture the strip, one capture per screenful of thumbnails.
// Captures go to out/app/verify_timing/. Wall times include the app's render and the capture, not a
// person's looking time; the number of captures is the number of views a person has to look at.
async (page) => {
  const base = "http://127.0.0.1:8013/";
  const out = require("path").join(__dirname, "..", "out", "app", "verify_timing") + "/";
  const cases = [
    ["data/swiss_dwellings/site1000_bldg2082_floor9385.pdf", "How many doors are shown on this floor plan?"],
    ["data/swiss_dwellings/site1000_bldg2082_floor9385.pdf", "How many windows are drawn on this floor plan?"],
    ["data/cubicasa5k/colorful-11397.png", "How many windows are shown on this floor plan?"],
    ["data/cubicasa5k/colorful-11397.png", "How many toilet bowls (WC seats) are drawn on this floor plan?"],
    ["data/cubicasa5k/colorful-1732.png", "How many doors are shown on this floor plan?"],
    ["data/cubicasa5k/colorful-2194.png", "How many doors are shown on this floor plan?"],
    ["data/cubicasa5k/colorful-2194.png", "How many windows are shown on this floor plan?"],
    ["data/ifc_models/bsc-molio_molio-with-uris.ifc", "How many doors are shown on the floor plan of 'Grundplan'?"],
    ["data/ifc_models/bsc-esplanades_1807-ep-ar-v18.ifc", "How many doors are shown on the floor plan of '1.korrus'?"],
    ["data/swiss_dwellings/site1000_bldg2082_floor9385.pdf", "What is the net floor area of apartment B in m², summing all its rooms except balconies, loggias, terraces and other outdoor spaces?"],
  ];
  const res = [];
  for (let c = 0; c < cases.length; c++) {
    const [path, q] = cases[c];
    await page.goto(base + "#path=" + encodeURIComponent(path) + "&q=" + encodeURIComponent(q));
    await page.reload();
    try { await page.waitForSelector("#detailBody .ans-head", { timeout: 90000 }); } catch (e) { res.push({ path, q, error: "no answer" }); continue; }
    await page.waitForTimeout(800);
    const info = await page.evaluate(() => ({ value: document.querySelector("#ansValue")?.textContent,
      items: document.querySelectorAll("#itemList button.item").length,
      thumbs: document.querySelectorAll("#stripList .thumb").length,
      strip: !document.querySelector("#strip").hidden }));
    // without the strip: one zoomed view per item
    await page.evaluate(() => { document.querySelector("#strip").style.visibility = "hidden"; });
    const btns = await page.$$("#itemList button.item");
    const t0 = Date.now();
    for (let i = 0; i < btns.length; i++) {
      await btns[i].click();
      await page.waitForTimeout(60);
      await page.locator("#viewer").screenshot({ path: `${out}c${c + 1}_without_${String(i + 1).padStart(2, "0")}.png` });
    }
    const without = (Date.now() - t0) / 1000;
    await page.evaluate(() => { document.querySelector("#strip").style.visibility = ""; document.querySelector("#zoomFit").click(); });
    // with the strip: one capture per screenful of thumbnails
    const t1 = Date.now();
    const pages = await page.evaluate(() => { const l = document.querySelector("#stripList"); return Math.max(1, Math.ceil(l.scrollWidth / l.clientWidth)); });
    for (let p = 0; p < pages; p++) {
      await page.evaluate((p) => { const l = document.querySelector("#stripList"); l.scrollLeft = p * l.clientWidth; }, p);
      await page.locator("#strip").screenshot({ path: `${out}c${c + 1}_with_${p + 1}.png` });
    }
    const withS = (Date.now() - t1) / 1000;
    res.push({ c: c + 1, path, q, ...info, without_views: btns.length, without_s: without, with_views: pages, with_s: withS });
  }
  return JSON.stringify(res);
}
