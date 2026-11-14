"""Drawing scale route: "What is the scale of this drawing?"

The scale is read the same way the pdf-vector route reads it for areas (bpqa/pdfvec.py):

  printed ratio  "1:100", "Massstab 1:200", "Echelle 1:100" in the PDF text layer -> tier proven
                 (the sheet states it); the measured methods below are reported as agreeing or not
  measured       scale bar, room-area labels or dimension strings, when no ratio is printed -> tier
                 estimate: the ratio is derived as (metres per PDF point) / (metres per point on paper),
                 which assumes the PDF page is printed at its own size
  scans          a printed ratio read by the local OCR (bpqa/routes/ocr.py) -> proven when the token also
                 carries a scale word ("Scale 1:50", "M 1:100") and was read with high confidence, else
                 estimate; a scan has no paper size, so no ratio is derived from measurements

A DXF or IFC file has no sheet scale (model space is in real units), so the route answers from the PDF
or image the question was asked on and abstains for .dxf and .ifc files.
"""
from __future__ import annotations

import re

from ..answer import Answer, abstain
from ..evidence import BLUE, ImageCanvas, evidence_path, finish
from ..pdfvec import PT_M, SCALE_RE, SCALE_WORDS, load_page

ROUTE = "sheet-scale"
RASTER_EXT = {"png", "jpg", "jpeg", "tif", "tiff"}
COMMON = [1, 2, 5, 10, 20, 25, 50, 75, 100, 125, 200, 250, 500, 1000, 1250, 2000, 2500, 5000]


def _ratio(m_per_pt: float) -> float:
    return m_per_pt / PT_M


def _nearest_common(d: float) -> int | None:
    best = min(COMMON, key=lambda c: abs(c - d) / c)
    return best if abs(best - d) / best <= 0.03 else None


def answer(ctx, q) -> Answer:
    ext = ctx.q.ext
    if ext == "pdf":
        return _pdf(ctx, q)
    if ext in RASTER_EXT:
        return _raster(ctx, q)
    return abstain(ROUTE, f"a .{ext} file has no sheet scale (model space is in real units)")


def _pdf(ctx, q) -> Answer:
    vp = load_page(str(ctx.file))
    if vp.kind == "scanned":
        return _raster(ctx, q)
    sc = vp.scale()
    cands = sc.candidates
    text = cands.get("text")
    measured = {k: v for k, v in cands.items() if k != "text" and v and v.get("m_per_pt")}
    meas_txt = "; ".join(f"{k}: {v['detail']} -> 1:{_ratio(v['m_per_pt']):.0f}" for k, v in measured.items())
    boxes = []
    if text and text.get("denom"):
        d = text["denom"]
        boxes = [s.bbox for s in text.get("spans", [])]
        agree = [k for k, v in measured.items() if abs(_ratio(v["m_per_pt"]) - d) <= 0.015 * d]
        clash = [k for k, v in measured.items() if abs(_ratio(v["m_per_pt"]) - d) > 0.02 * d]
        expl = (f"1:{d} = scale printed on the sheet ('" + "', '".join(s.text for s in text.get("spans", [])[:3]) + "'); "
                + text["detail"])
        if agree:
            expl += f"; agrees with the drawing's own {', '.join(agree)} ({meas_txt})"
        ans = Answer(value=f"1:{d}", unit="ratio", route=ROUTE, confidence=0.97 if agree else 0.9,
                     explanation=expl, items=[{"printed": f"1:{d}", "spans": [s.text for s in text.get("spans", [])]}]
                     + [{"method": k, "ratio": round(_ratio(v["m_per_pt"]), 1), "detail": v["detail"]} for k, v in measured.items()])
        if clash:
            ans.tier = "estimate"
            ans.tier_reason = (f"the printed 1:{d} disagrees with the drawing's {', '.join(clash)} ({meas_txt}); "
                               f"the sheet may have been printed or exported at another size")
    elif text and not text.get("denom") and text.get("detail"):
        return abstain(ROUTE, f"{text['detail']} (several views at different scales); ask about one view")
    elif measured:
        k, v = next(iter(sorted(measured.items(), key=lambda kv: ["bar", "area", "dimension"].index(kv[0])
                                    if kv[0] in ("bar", "area", "dimension") else 9)))
        r = _ratio(v["m_per_pt"])
        near = _nearest_common(r)
        val = f"1:{near}" if near else f"1:{r:.0f}"
        boxes = [s.bbox for s in v.get("spans", [])]
        ans = Answer(value=val, unit="ratio", route=ROUTE, confidence=sc.confidence,
                     explanation=(f"{val} = derived, no scale is printed on the sheet: {meas_txt}; "
                                  f"ratio = metres per PDF point / {PT_M * 1000:.4f} mm per point on paper"
                                  + (f", 1:{r:.1f} rounded to the common scale 1:{near}" if near else "")),
                     tier="estimate",
                     tier_reason="no printed scale; derived from the drawing's own measurements, which assumes the "
                                 "PDF page is printed at its own size",
                     items=[{"method": kk, "ratio": round(_ratio(vv["m_per_pt"]), 1), "detail": vv["detail"]}
                            for kk, vv in measured.items()])
    else:
        return abstain(ROUTE, f"no printed scale, scale bar, area labels or dimension strings found ({sc.detail})")
    if ctx.evidence:
        img, mat = vp.render()
        cv = ImageCanvas(img)
        import pymupdf
        for i, b in enumerate(boxes):
            a, c = pymupdf.Point(b[0], b[1]) * mat, pymupdf.Point(b[2], b[3]) * mat
            cv.box((min(a.x, c.x) - 4, min(a.y, c.y) - 4, max(a.x, c.x) + 4, max(a.y, c.y) + 4), n=i + 1, color=BLUE, width=3)
        ans.evidence = finish(cv.img, evidence_path(ctx.out_dir, ctx.q.path, ctx.q.question), ctx.q.question,
                              f"Answer: {ans.value}{'   ESTIMATE' if ans.tier == 'estimate' else ''}   route: {ROUTE} (cost $0)",
                              note="boxed: the text that sets the scale. " + ans.explanation[:300])
    return ans


def _raster(ctx, q) -> Answer:
    from .labels import quad_box
    from .ocr import ocr_image
    toks = ocr_image(ctx.file) or []
    hits = []
    for t in toks:
        for m in SCALE_RE.finditer(t["text"]):
            d = int(m.group(1))
            if 1 <= d <= 5000:
                hits.append((d, bool(re.search(SCALE_WORDS, t["text"].lower())), t))
    if not hits:
        return abstain(ROUTE, "no printed scale ratio read on the scan (a scan has no paper size, so no ratio "
                              "can be derived from measurements)")
    denoms = {d for d, _, _ in hits}
    kw = {d for d, k, _ in hits if k}
    if len(denoms) > 1 and len(kw) != 1:
        return abstain(ROUTE, f"several ratios read on the scan {sorted(denoms)}; ask about one view")
    d = (kw or denoms).pop()
    used = [t for dd, _, t in hits if dd == d]
    sure = any(k and t["conf"] >= 0.9 for dd, k, t in hits if dd == d)
    ans = Answer(value=f"1:{d}", unit="ratio", route=ROUTE, confidence=0.9 if sure else 0.5,
                 explanation=f"1:{d} = scale printed on the drawing, read by OCR: "
                             + ", ".join(f"'{t['text']}' ({t['conf']:.2f})" for t in used[:3])
                             + "; the scan's pixel size on paper is unknown, so the ratio applies to the original sheet",
                 items=[{"text": t["text"], "ocr_conf": t["conf"]} for t in used])
    if not sure:
        ans.tier = "estimate"
        ans.tier_reason = "the ratio was read by OCR without a scale word next to it, or with low confidence"
    if ctx.evidence:
        import cv2
        from PIL import Image
        img = cv2.imread(str(ctx.file))
        if img is not None and all(t.get("rot", 0) == 0 and not t.get("mirror") for t in used):
            cv = ImageCanvas(Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)))
            for i, t in enumerate(used):
                b = quad_box(t["quad"])
                cv.box((b[0] - 3, b[1] - 3, b[2] + 3, b[3] + 3), n=i + 1, color=BLUE, width=3)
            ans.evidence = finish(cv.img, evidence_path(ctx.out_dir, ctx.q.path, ctx.q.question), ctx.q.question,
                                  f"Answer: {ans.value}{'   ESTIMATE' if ans.tier == 'estimate' else ''}   route: {ROUTE} (cost $0)",
                                  note="boxed: the scale text read by OCR")
    return ans
