"""Where the CubiCasa scan answers go wrong, for the failure gallery (docs/failure_gallery/).

    python tools/failure_gallery.py collect --answers out/test/answers.jsonl --out <dir>
    python tools/failure_gallery.py sheets  --wrong <dir>/wrong.json --pick <dir>/sample.json --out <dir>/sheets
    python tools/failure_gallery.py crops   --cases <dir>/cases.json --out docs/failure_gallery/img

Illustration only. The public CubiCasa5K annotation of a plan is drawn next to our boxes to show what
went wrong; nothing here changes a model, a threshold or a route, and no number from this file is
ever fed back into tuning. `collect` reads an eval answers file (any split), attaches the annotation
boxes of the matching public plan (tools/overlap.py matches), and labels every counted or missed box
with the same causes as tools/det_errors.py (miss, low score, fp, merge, split ...). The by-eye mode of
each case is recorded separately by hand (inspected.json) after looking at the sheets.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

DATA_DEFAULT = REPO.parent / "data"
DET_OBJ = {"toilet": "toilet", "sink": "sink", "door": "door", "window": "window"}
BLUE, AMBER, GREEN, RED = (219, 140, 40), (0, 165, 255), (0, 170, 0), (40, 40, 230)   # BGR


def _key(s: str) -> str:
    return hashlib.sha1(s.encode()).hexdigest()


def load_matches(overlap: Path):
    m = {}
    for r in csv.DictReader(open(overlap / "matches.csv", encoding="utf-8")):
        if r["stoa_file"] not in m or r["public_file"] == "F1_original.png":
            m[r["stoa_file"]] = (r["public_plan"], r["orient"])
    return m


def collect(a):
    from bpqa.routes import detector as det
    from tools import det_errors as de
    rows = [json.loads(l) for l in open(a.answers, encoding="utf-8")]
    rows = [r for r in rows if r["path"].startswith("data/cubicasa5k/")]
    match = load_matches(Path(a.overlap))
    stoa, public = Path(a.stoa), Path(a.public)
    wrong, total = [], Counter()
    cfg = {}
    for r in rows:
        obj = r["query"]["obj"]
        total[obj] += 1
        if r.get("correct"):
            continue
        img = cv2.imread(str(stoa / r["path"]))
        if img is None:
            continue
        H, W = img.shape[:2]
        rec = {"qid": r["qid"], "path": r["path"], "stem": Path(r["path"]).stem, "obj": obj, "gold": r["gold"],
               "ours": r["value"], "route": r["route"], "tier": r.get("tier"), "w": W, "h": H,
               "question": r["question"], "explanation": r["explanation"], "tier_reason": r.get("tier_reason"),
               "abstained": r["value"] is None, "items": r.get("items") or [], "uncertain": r.get("uncertain") or []}
        mt = match.get(r["path"])
        if mt:
            rec["plan"], rec["orient"] = mt
            try:
                um = de.unit_mask(public, mt[0], mt[1], (W, H))
                dark = (cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) < 140)
                rec["dark_outside_unit"] = round(float((dark & (um == 0)).sum() / max(1, dark.sum())), 3)
            except Exception as e:                      # annotation unreadable: leave the field out
                rec["unit_error"] = str(e)
            if obj in DET_OBJ:
                gt = de.gt_boxes(public, mt[0], mt[1], (W, H), DET_OBJ[obj])
                rec["gt"] = [[round(v, 1) for v in b] for b in gt]
                if obj not in cfg:
                    cfg[obj] = det.config(obj)
                c = cfg[obj]
                try:
                    views = det.views_for(stoa / r["path"], c)
                    fused = det.combine(views)
                    n, hits, unsure, spread = det.count_views(views, fused, obj, c)
                    ids = {id(h) for h in hits}
                    low = [d for d in fused if d["cls"] in det.OBJECTS[obj] and id(d) not in ids and d["conf"] < c["conf"]]
                    causes = de.analyse(gt, hits, low, c["conf"], 0.15)
                    um = de.unit_mask(public, mt[0], mt[1], (W, H))
                    causes = [("fp outside unit" if k == "fp" and de.outside(um, b) else k, b, s) for k, b, s in causes]
                    rec["causes"] = [[k, [round(float(v), 1) for v in b], None if s is None else round(float(s), 3)] for k, b, s in causes]
                    rec["low"] = [[[round(float(v), 1) for v in d["xyxy"]], round(float(d["conf"]), 3)] for d in low if d["conf"] >= 0.1]
                    rec["threshold"] = c["conf"]
                except Exception as e:
                    rec["det_error"] = str(e)
        wrong.append(rec)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "wrong.json").write_text(json.dumps(wrong), encoding="utf-8")
    (out / "totals.json").write_text(json.dumps(dict(total)), encoding="utf-8")
    c = Counter(w["obj"] for w in wrong)
    print("wrong by object:", dict(c), "of", dict(total))


# ------------------------------------------------------------------ drawing

def draw_case(img, rec, annot=True, ours=True, crop=None, scale_to=None):
    """Our boxes (blue proven / amber estimate, numbered) and the annotation: green = annotated object we
    found, red circle = annotated object we missed, red X = box we counted that is not an annotated object."""
    vis = img.copy()
    H, W = vis.shape[:2]
    lw = max(2, round(max(H, W) / 500))
    col = AMBER if rec.get("tier") == "estimate" else BLUE
    cs = rec.get("causes") or []
    missed = [b for k, b, s in cs if k in ("miss", "low score", "near t") and rec["obj"] in DET_OBJ and _is_gt(b, rec)]
    fps = [b for k, b, s in cs if k in ("fp", "fp outside unit", "merge", "split") or (k == "near t" and not _is_gt(b, rec))]
    if ours:
        for it in rec["items"]:
            b = [int(v) for v in it["xyxy"]]
            cv2.rectangle(vis, b[:2], b[2:], col, lw)
            cv2.putText(vis, str(it["n"]), (b[0], max(10, b[1] - 3)), cv2.FONT_HERSHEY_SIMPLEX, 0.45 * lw, col, max(1, lw // 2))
    if annot:
        for b in missed:
            cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
            r = max(8, 0.6 * max(b[2] - b[0], b[3] - b[1]) + 3 * lw)
            cv2.circle(vis, (int(cx), int(cy)), int(r), RED, lw + 1)
        for b in fps:
            cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
            r = max(8, 0.6 * max(b[2] - b[0], b[3] - b[1]) + 3 * lw)
            cv2.line(vis, (int(cx - r), int(cy - r)), (int(cx + r), int(cy + r)), RED, lw + 1)
            cv2.line(vis, (int(cx - r), int(cy + r)), (int(cx + r), int(cy - r)), RED, lw + 1)
    return vis


def _is_gt(b, rec):
    return any(abs(b[i] - g[i]) < 0.6 for g in rec.get("gt", []) for i in range(4))


def sheets(a):
    wrong = {(w["qid"]): w for w in json.loads(Path(a.wrong).read_text(encoding="utf-8"))}
    pick = json.loads(Path(a.pick).read_text(encoding="utf-8"))
    stoa = Path(a.stoa)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    per = a.per
    cell = a.cell
    for s in range(0, len(pick), per):
        chunk = pick[s:s + per]
        tiles = []
        for qid in chunk:
            w = wrong[qid]
            img = cv2.imread(str(stoa / w["path"]))
            vis = draw_case(img, w)
            f = cell / max(vis.shape[:2])
            vis = cv2.resize(vis, None, fx=f, fy=f, interpolation=cv2.INTER_AREA if f < 1 else cv2.INTER_CUBIC)
            canvas = np.full((cell + 44, cell, 3), 255, np.uint8)
            canvas[44:44 + vis.shape[0], :vis.shape[1]] = vis
            cv2.putText(canvas, f"#{qid} {w['stem'][:34]} {w['w']}x{w['h']}", (4, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)
            cv2.putText(canvas, f"{w['obj']} gold {w['gold']} ours {w['ours']} ({w.get('tier')}) outside {w.get('dark_outside_unit', '?')}", (4, 36),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 160), 1)
            tiles.append(canvas)
        while len(tiles) % 2:
            tiles.append(np.full_like(tiles[0], 255))
        rowsimg = [np.hstack(tiles[i:i + 2]) for i in range(0, len(tiles), 2)]
        cv2.imwrite(str(out / f"sheet_{s // per:02d}.jpg"), np.vstack(rowsimg), [cv2.IMWRITE_JPEG_QUALITY, 82])
    print("sheets:", (len(pick) + per - 1) // per)


# ------------------------------------------------------------------ gallery crops

def _room_polys(public, rec, rtype):
    from tools.cubicasa_rooms import typed_rooms
    import PIL.Image
    d = public / rec["plan"]
    fw, fh = PIL.Image.open(d / "F1_scaled.png").size
    W, H = rec["w"], rec["h"]
    out = []
    for t, cls, poly, content in typed_rooms((d / "model.svg").read_text(encoding="utf-8", errors="replace")):
        if t != rtype:
            continue
        p = np.array(poly, float) * [W / fw, H / fh]
        if "mirror" in rec.get("orient", ""):
            p[:, 0] = W - p[:, 0]
        out.append(p)
    return out


def render_panel(rec, panel, stoa, public, width):
    """One annotated crop: amber = our counted boxes (numbered), green = annotation, red circle = annotated object we
    missed, red X = box we counted that the annotation does not have."""
    from tools import det_errors as de
    img = cv2.imread(str(stoa / rec["path"]))
    H, W = img.shape[:2]
    fx0, fy0, fx1, fy1 = panel.get("crop", [0, 0, 1, 1])
    x0, y0, x1, y1 = int(fx0 * W), int(fy0 * H), int(fx1 * W), int(fy1 * H)
    s = width / (x1 - x0)
    vis = cv2.resize(img[y0:y1, x0:x1], None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC if s > 1 else cv2.INTER_AREA)
    T = lambda b: [(b[0] - x0) * s, (b[1] - y0) * s, (b[2] - x0) * s, (b[3] - y0) * s]
    lw = 3
    cs = rec.get("causes") or []
    gtset = rec.get("gt", [])
    is_gt = lambda b: any(all(abs(b[i] - g[i]) < 0.6 for i in range(4)) for g in gtset)
    if panel.get("gt"):
        for g in gtset:
            b = [int(v) for v in T(g)]
            cv2.rectangle(vis, (b[0] - 2, b[1] - 2), (b[2] + 2, b[3] + 2), (60, 170, 60), 2)
    for poly in (_room_polys(public, rec, panel["rooms"]) if panel.get("rooms") else []):
        q = np.round(np.column_stack([(poly[:, 0] - x0) * s, (poly[:, 1] - y0) * s])).astype(np.int32)
        cv2.polylines(vis, [q], True, (60, 170, 60), 3)
    col = (0, 165, 255)
    for it in rec["items"]:
        b = [int(v) for v in T(it["xyxy"])]
        cv2.rectangle(vis, b[:2], b[2:], col, lw)
        if panel.get("numbers", True):
            cv2.putText(vis, str(it["n"]), (b[0], max(14, b[1] - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, col, 2)
    red = (40, 40, 230)
    def circ(b):
        b = T(b)
        r = max(14, 0.7 * max(b[2] - b[0], b[3] - b[1]) + 8)
        cv2.circle(vis, (int((b[0] + b[2]) / 2), int((b[1] + b[3]) / 2)), int(r), red, lw)
    def cross(b):
        b = T(b)
        cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        r = max(12, 0.6 * max(b[2] - b[0], b[3] - b[1]) + 6)
        cv2.line(vis, (int(cx - r), int(cy - r)), (int(cx + r), int(cy + r)), red, lw + 1)
        cv2.line(vis, (int(cx - r), int(cy + r)), (int(cx + r), int(cy - r)), red, lw + 1)
    if panel.get("outside"):
        um = de.unit_mask(public, rec["plan"], rec["orient"], (W, H))
        for it in rec["items"]:
            if de.outside(um, it["xyxy"]):
                cross(it["xyxy"])
    else:
        for k, b, sc in cs:
            if k in ("miss", "low score") or (k == "near t" and is_gt(b)):
                circ(b)
            elif k in ("fp", "fp outside unit") or (k == "near t" and not is_gt(b)):
                cross(b)
    for extra in panel.get("circles", []):
        circ(extra)
    return vis


def crops(a):
    from tools.failure_gallery_cases import GALLERY
    wrong = {w["qid"]: w for w in json.loads(Path(a.wrong).read_text(encoding="utf-8"))}
    stoa, public = Path(a.stoa), Path(a.public)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    meta = []
    for case in GALLERY:
        ps = case["panels"]
        width = 1000 if len(ps) == 1 else 640
        tiles = [render_panel(wrong[p["qid"]], p, stoa, public, width) for p in ps]
        hh = max(t.shape[0] for t in tiles)
        tiles = [cv2.copyMakeBorder(t, 0, hh - t.shape[0], 0, 0, cv2.BORDER_CONSTANT, value=(255, 255, 255)) for t in tiles]
        sep = np.full((hh, 8, 3), 255, np.uint8)
        row = tiles[0]
        for t in tiles[1:]:
            row = np.hstack([row, sep, t])
        cv2.imwrite(str(out / f"{case['id']}.jpg"), row, [cv2.IMWRITE_JPEG_QUALITY, 86])
        meta.append({"id": case["id"], "panels": [{"qid": p["qid"], **{k: wrong[p["qid"]][k] for k in ("stem", "obj", "gold", "ours", "w", "h", "question")}} for p in ps]})
    (out / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    print("crops:", len(meta))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("collect", "sheets", "crops"):
        p = sub.add_parser(name)
        p.add_argument("--stoa", default=str(DATA_DEFAULT / "stoa-preview" / "junction-dataset-preview"))
        p.add_argument("--public", default=str(DATA_DEFAULT / "cubicasa5k-public" / "cubicasa5k"))
        p.add_argument("--overlap", default=str(REPO.parent / "stoa" / "out" / "overlap"))
        p.add_argument("--answers", default=str(REPO / "out" / "test" / "answers.jsonl"))
        p.add_argument("--out", required=True)
        p.add_argument("--wrong", default="")
        p.add_argument("--pick", default="")
        p.add_argument("--per", type=int, default=4)
        p.add_argument("--cell", type=int, default=760)
    a = ap.parse_args()
    {"collect": collect, "sheets": sheets, "crops": crops}[a.cmd](a)


if __name__ == "__main__":
    main()
