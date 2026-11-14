"""Error analysis for detector counts on held-out train-split images (never the test split).

    python tools/det_errors.py --stoa ../data/stoa-full/junction-dataset --public ../data/cubicasa5k-public/cubicasa5k
        --overlap out/overlap --images ../data/detector/ds_cubi/tune_images.txt --objects door,window
        [--thresholds models/detector_thresholds.json] [--sheets out/det_errors] [--sample 60]

For every image whose count (the route's policy from the thresholds file) differs from Stoa's answer,
the counted boxes are matched to the public CubiCasa5K annotation boxes of the same plan (mapped into
the Stoa image frame), and every unmatched box is given a cause:

  miss        an annotated object with no detector box at all (score >= 0.05)       -> (a)
  low score   an annotated object whose box scored below the threshold              -> (d)
  fp          a counted box that overlaps no annotated object                       -> (b)
  fp outside unit  the same, but off the annotated walls and rooms: a neighbouring unit,
              a stairwell or a cut-off part of the scan, which the annotation (and Stoa) leave out -> (c)
  merge       one counted box spans two or more annotated objects                   -> (c)
  split       an annotated object covered by two or more counted boxes              -> (c)
  near t      a fp or low-score box scored within --near of the threshold           -> (d)
  gold        the annotation count itself differs from Stoa's answer                -> (c)

The image's cause is the category with the most offending boxes (ties: the order above). With --sheets
a crop sheet per sampled image is written: annotation boxes green, counted boxes blue, below-threshold
boxes amber, so each category can be checked by eye. Test-split answers are never read.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import cv2
import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from bpqa.data import split_of  # noqa: E402
from bpqa.routes import detector as det  # noqa: E402
from tools.cubicasa_svg import convert  # noqa: E402
from tools.tune_detector import gold_counts  # noqa: E402

CATS = ["miss", "low score", "fp outside unit", "fp", "merge", "split", "near t", "gold"]
LETTER = {"miss": "a", "fp": "b", "fp outside unit": "c", "merge": "c", "split": "c", "gold": "c", "low score": "d", "near t": "d"}


def _inter(a, b):
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def _area(b):
    return max(1e-6, (b[2] - b[0]) * (b[3] - b[1]))


def overlaps(a, b) -> bool:
    """Same object: IoU >= 0.3, or the smaller box lies 60% inside the larger (thin door/window boxes)."""
    i = _inter(a, b)
    if i <= 0:
        return False
    return i / (_area(a) + _area(b) - i) >= 0.3 or i / min(_area(a), _area(b)) >= 0.6


def gt_boxes(public: Path, plan: str, orient: str, img_wh, cls: str):
    d = public / plan
    boxes, _, _ = convert((d / "model.svg").read_text(encoding="utf-8", errors="replace"))
    import PIL.Image
    fw, fh = PIL.Image.open(d / "F1_scaled.png").size
    W, H = img_wh
    sx, sy = W / fw, H / fh
    out = []
    for k, x0, y0, x1, y1 in boxes:
        if k != cls:
            continue
        b = [x0 * sx, y0 * sy, x1 * sx, y1 * sy]
        if "mirror" in orient:
            b = [W - b[2], b[1], W - b[0], b[3]]
        out.append(b)
    return out


def unit_mask(public: Path, plan: str, orient: str, img_wh):
    """Filled annotated walls and rooms (the unit CubiCasa annotated), in the Stoa image frame."""
    import xml.etree.ElementTree as ET
    import PIL.Image
    from tools.cubicasa_svg import _apply, _local, _points, parse_transform
    d = public / plan
    root = ET.fromstring((d / "model.svg").read_text(encoding="utf-8", errors="replace"))
    fw, fh = PIL.Image.open(d / "F1_scaled.png").size
    W, H = img_wh
    S = np.diag([W / fw, H / fh, 1.0])
    if "mirror" in orient:
        S = np.array([[-1, 0, W], [0, 1, 0], [0, 0, 1.0]]) @ S
    mask = np.zeros((H, W), np.uint8)

    def walk(e, M):
        M = M @ parse_transform(e.get("transform"))
        cls = (e.get("class") or "").split()
        if _local(e.tag) == "g" and cls and cls[0] in ("Wall", "Space"):
            first = next((c for c in e if _local(c.tag) == "polygon"), None)
            if first is not None:
                pts = _apply(S @ M @ parse_transform(first.get("transform")), _points(first))
                if len(pts) >= 3:
                    cv2.fillPoly(mask, [np.round(pts).astype(np.int32)], 255)
            return
        for c in e:
            walk(c, M)

    walk(root, np.eye(3))
    k = max(3, round(max(W, H) * 0.01))
    return cv2.dilate(mask, np.ones((k, k), np.uint8))


def outside(mask, b) -> bool:
    x0, y0, x1, y1 = [int(round(v)) for v in b]
    sub = mask[max(0, y0):max(y0 + 1, y1), max(0, x0):max(x0 + 1, x1)]
    return sub.size == 0 or (sub > 0).mean() < 0.2


def analyse(gt, hits, low, t, near):
    """Per-box causes for one image: list of (cause, box, score or None)."""
    causes = []
    gt_hit = defaultdict(list)          # gt index -> hit indices
    hit_gt = defaultdict(list)
    for hi, h in enumerate(hits):
        for gi, g in enumerate(gt):
            if overlaps(h["xyxy"], g):
                gt_hit[gi].append(hi)
                hit_gt[hi].append(gi)
    # merges: a hit that covers 2+ gt boxes (each mostly inside it)
    merged_gt = set()
    for hi, gis in hit_gt.items():
        inside = [gi for gi in gis if _inter(hits[hi]["xyxy"], gt[gi]) / _area(gt[gi]) >= 0.5]
        if len(inside) >= 2:
            causes.append(("merge", hits[hi]["xyxy"], hits[hi]["conf"]))
            merged_gt.update(inside)
    for gi, his in gt_hit.items():
        if len(his) >= 2 and gi not in merged_gt:
            for hi in his[1:]:
                if len(hit_gt[hi]) == 1:
                    causes.append(("split", hits[hi]["xyxy"], hits[hi]["conf"]))
    for hi, h in enumerate(hits):
        if not hit_gt[hi]:
            causes.append(("near t" if h["conf"] < t + near else "fp", h["xyxy"], h["conf"]))
    for gi, g in enumerate(gt):
        if gi in gt_hit or gi in merged_gt:
            continue
        cand = [d for d in low if overlaps(d["xyxy"], g)]
        if cand:
            s = max(d["conf"] for d in cand)
            causes.append(("near t" if s >= t - near else "low score", g, s))
        else:
            causes.append(("miss", g, None))
    return causes


def sheet(img, gt, hits, low, causes, title, out: Path):
    vis = img.copy()
    lw = max(2, round(max(img.shape[:2]) / 600))
    for d in low:
        if d["conf"] >= 0.1:
            b = [int(v) for v in d["xyxy"]]
            cv2.rectangle(vis, b[:2], b[2:], (0, 170, 255), lw)
    for g in gt:
        b = [int(v) for v in g]
        cv2.rectangle(vis, (b[0] - lw, b[1] - lw), (b[2] + lw, b[3] + lw), (0, 180, 0), lw)
    for h in hits:
        b = [int(v) for v in h["xyxy"]]
        cv2.rectangle(vis, b[:2], b[2:], (255, 80, 0), lw)
    for c, b, s in causes:
        b = [int(v) for v in b]
        cv2.putText(vis, f"{c} {'' if s is None else f'{s:.2f}'}", (b[0], max(12, b[1] - 4)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5 * lw, (0, 0, 220), max(1, lw // 2))
    s = 1600 / max(vis.shape[:2])
    if s < 1:
        vis = cv2.resize(vis, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    vis = cv2.copyMakeBorder(vis, 36, 0, 0, 0, cv2.BORDER_CONSTANT, value=(255, 255, 255))
    cv2.putText(vis, title, (6, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 2)
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), vis)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stoa", required=True)
    ap.add_argument("--public", required=True)
    ap.add_argument("--overlap", default=str(REPO / "out" / "overlap"))
    ap.add_argument("--images", required=True)
    ap.add_argument("--thresholds", default=str(det.THRESHOLDS))
    ap.add_argument("--objects", default="door,window")
    ap.add_argument("--near", type=float, default=0.15, help="a box this close to the threshold is calibration (d)")
    ap.add_argument("--sample", type=int, default=60)
    ap.add_argument("--sheets", default="")
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    root, public = Path(a.stoa), Path(a.public)
    th = json.loads(Path(a.thresholds).read_text(encoding="utf-8"))
    paths = {l.strip() for l in Path(a.images).read_text(encoding="utf-8").splitlines() if l.strip()}
    paths = {p for p in paths if split_of(p) == "dev"}
    gold = gold_counts(root, paths)
    match = {}
    for r in csv.DictReader(open(Path(a.overlap) / "matches.csv", encoding="utf-8")):
        if r["stoa_file"] in paths and r["stoa_split"] != "test":
            if r["stoa_file"] not in match or r["public_file"] == "F1_original.png":
                match[r["stoa_file"]] = (r["public_plan"], r["orient"])
    report, boxes_out = {}, {}
    for obj in a.objects.split(","):
        c = {"model": "detector.pt", "imgsz": th.get("_imgsz", 1280), "tile": None, "tta": False} | th[obj]
        model = REPO / "models" / c["model"]
        cls = det.OBJECTS[obj][0]
        rows = []
        for p in sorted(gold):
            if obj not in gold[p] or p not in match:
                continue
            views = det.detect_views(root / p, model=model, imgsz=c["imgsz"], tile=c["tile"], tta=c["tta"])
            fused = det.combine(views)
            n, hits, _, _ = det.count_views(views, fused, obj, c)
            g = gold[p][obj]
            if n == g:
                rows.append({"path": p, "gold": g, "n": n, "ok": True})
                continue
            img = cv2.imread(str(root / p))
            gt = gt_boxes(public, *match[p], (img.shape[1], img.shape[0]), cls)
            ids = {id(h) for h in hits}
            low = [d for d in fused if d["cls"] in det.OBJECTS[obj] and id(d) not in ids and d["conf"] < c["conf"]]
            causes = analyse(gt, hits, low, c["conf"], a.near)
            um = unit_mask(public, *match[p], (img.shape[1], img.shape[0]))
            causes = [("fp outside unit" if k == "fp" and outside(um, b) else k, b, s) for k, b, s in causes]
            if len(gt) != g:
                causes.append(("gold", [0, 0, 1, 1], None))
            cnt = Counter(k for k, _, _ in causes)
            main_cause = max(CATS, key=lambda k: (cnt.get(k, 0), -CATS.index(k))) if cnt else "unexplained"
            rows.append({"path": p, "gold": g, "n": n, "ok": False, "gt": len(gt), "cause": main_cause,
                         "causes": dict(cnt), "boxes": [(k, [round(v) for v in b], s) for k, b, s in causes]})
            key = int(hashlib.sha1(p.encode()).hexdigest()[:6], 16)
            rows[-1]["key"] = key
            rows[-1]["_viz"] = (img, gt, hits, low, causes)
        wrong = sorted((r for r in rows if not r["ok"]), key=lambda r: r["key"])
        sample = wrong[: a.sample]
        if a.sheets:
            for r in sample:
                img, gt, hits, low, causes = r["_viz"]
                sheet(img, gt, hits, low, causes,
                      f"{obj} {Path(r['path']).stem} gold {r['gold']} counted {r['n']} annotation {r['gt']}: {r['cause']}",
                      Path(a.sheets) / obj / f"{Path(r['path']).stem}.jpg")
        for r in rows:
            r.pop("_viz", None)

        def table(rs):
            img_c = Counter(r["cause"] for r in rs)
            box_c = Counter()
            for r in rs:
                box_c.update(r["causes"])
            return {"images": len(rs), "image_cause": dict(img_c), "box_causes": dict(box_c),
                    "over": sum(r["n"] > r["gold"] for r in rs), "under": sum(r["n"] < r["gold"] for r in rs),
                    "off_by_1": sum(abs(r["n"] - r["gold"]) == 1 for r in rs)}
        boxes_out[obj] = [{"path": r["path"], "boxes": r["boxes"]} for r in sample]
        report[obj] = {"n": len(rows), "exact": round(sum(r["ok"] for r in rows) / max(1, len(rows)), 4),
                       "all_wrong": table(wrong), "sample": table(sample),
                       "sample_rows": [{k: v for k, v in r.items() if k != "boxes"} for r in sample]}
        print(obj, json.dumps({k: v for k, v in report[obj].items() if k != "sample_rows"}, indent=1))
    if a.json:
        Path(a.json).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json).write_text(json.dumps(report, indent=1), encoding="utf-8")
        Path(a.json).with_name("boxes.json").write_text(json.dumps(boxes_out), encoding="utf-8")


if __name__ == "__main__":
    main()
