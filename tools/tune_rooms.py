"""Tune the raster-rooms route on the held-out tune images (train split only) and write
models/rooms_thresholds.json.

    python tools/tune_rooms.py --stoa ../data/stoa-full/junction-dataset --tune ../data/detector/ds_rooms/tune_images.txt

The tune images are Stoa train-split CubiCasa images whose public plans were held out of training
(tools/build_rooms_dataset.py). Test-split images are refused. Per room type it picks:
  thr, band      detector score threshold with the best exact-count accuracy on the tune images, and
                 a band of +-0.1 around it for "uncertain" boxes
  tta            whether the median count over the eight rotations/mirrors beats a single view
  prefer         which reading answers when detector and OCR disagree: "ocr", "det" or "ocr_proven"
  proven_rule    when an agreement is proven: "agree", "agree_ocr", "agree_clean" or "none"; the rule
                 with the most proven answers that are right on at least AGREE_MIN of the tune images
                 (at least 20 proven answers)
Accuracies are also reported with 2-fold cross-validation over images (choices fitted on one half,
scored on the other), so the choice is not only scored on the images it was fitted on.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

import cv2

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from bpqa.data import split_of  # noqa: E402
from bpqa.query import parse  # noqa: E402
from bpqa.routes import raster, raster_rooms as rr  # noqa: E402

AGREE_MIN = 0.95
GRID = [round(0.10 + 0.05 * i, 2) for i in range(16)]       # 0.10 .. 0.85


def load(stoa: Path, tune: list[str]):
    gold = defaultdict(dict)
    qtext = {}
    with open(stoa / "questions.jsonl", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if r["path"] not in tune:
                continue
            q = parse(r["question"])
            if q.measure == "count" and q.obj in rr.CLASSES and q.lang == "en":
                gold[r["path"]][q.obj] = int(r["answer"])
                qtext[(r["path"], q.obj)] = (r["question"], q)
    return gold, qtext


def ocr_reading(stoa: Path, path: str, q, question: str):
    ctx = SimpleNamespace(file=stoa / path, evidence=False, out_dir=REPO / "out" / "evidence",
                          q=SimpleNamespace(path=path, question=question))
    img = cv2.imread(str(ctx.file))
    a = raster.room_labels(ctx, q, img)
    return int(a.value), a.tier == "proven"


def choose(rows, obj, imgs):
    """Fit per-type choices on the images `imgs`. rows[img] = {obj: (gold, ocr, ocr_proven, views1, views8)}."""
    best = None
    for tta in (False, True):
        for thr in GRID:
            cfg = {"thr": thr, "band": [round(max(0.05, thr - 0.1), 2), round(thr + 0.1, 2)], "tta": tta}
            ok = sum(rr.det_count(rows[i][obj][4 if tta else 3], obj, cfg)[0] == rows[i][obj][0] for i in imgs)
            if best is None or ok > best[0]:
                best = (ok, cfg)
    cfg = dict(best[1])
    scores = {}
    for pref in ("ocr", "det", "ocr_proven"):
        c = {**cfg, "prefer": pref}
        scores[pref] = sum(value(rows[i][obj], obj, c)[0] == rows[i][obj][0] for i in imgs)
    cfg["prefer"] = max(scores, key=lambda k: (scores[k], k == "ocr_proven"))
    cfg["proven_rule"] = "none"
    best_n = 0
    for rule in ("agree", "agree_ocr", "agree_clean"):
        c = {**cfg, "proven_rule": rule}
        res = [value(rows[i][obj], obj, c) for i in imgs]
        pr = [(r[0] == rows[i][obj][0]) for r, i in zip(res, imgs) if r[1]]
        if len(pr) >= 20 and sum(pr) / len(pr) >= AGREE_MIN and len(pr) > best_n:
            cfg["proven_rule"], best_n = rule, len(pr)
    return cfg


def value(row, obj, cfg):
    gold, o, op, v1, v8 = row
    d, _, unsure = rr.det_count(v8 if cfg["tta"] else v1, obj, cfg)
    return rr.combine(obj, o, op, d, cfg, len(unsure))


def score(rows, obj, imgs, cfg):
    s = {"n": len(imgs), "ocr": 0, "det": 0, "combined": 0, "proven": 0, "proven_ok": 0}
    for i in imgs:
        gold, o, op, v1, v8 = rows[i][obj]
        d, _, unsure = rr.det_count(v8 if cfg["tta"] else v1, obj, cfg)
        val, proven, _ = rr.combine(obj, o, op, d, cfg, len(unsure))
        s["ocr"] += o == gold
        s["det"] += d == gold
        s["combined"] += val == gold
        s["proven"] += proven
        s["proven_ok"] += proven and val == gold
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stoa", required=True)
    ap.add_argument("--tune", required=True)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--out", default=str(rr.THRESHOLDS))
    ap.add_argument("--no-tta", action="store_true")
    a = ap.parse_args()
    stoa = Path(a.stoa)
    tune = [l.strip() for l in Path(a.tune).read_text(encoding="utf-8").splitlines() if l.strip()]
    bad = [p for p in tune if split_of(p) != "dev"]
    if bad:
        raise SystemExit(f"refusing: {len(bad)} tune images are in the test split, e.g. {bad[:3]}")
    gold, qtext = load(stoa, set(tune))
    rows = defaultdict(dict)
    for k, p in enumerate(sorted(gold)):
        v1 = rr.detect_views(stoa / p, a.imgsz, False)
        v8 = v1 if a.no_tta else rr.detect_views(stoa / p, a.imgsz, True)
        for obj, g in gold[p].items():
            question, q = qtext[(p, obj)]
            o, op = ocr_reading(stoa, p, q, question)
            rows[p][obj] = (g, o, op, v1, v8)
        if (k + 1) % 50 == 0:
            print(f"  {k + 1}/{len(gold)}", file=sys.stderr)
    out, report = {}, {}
    for obj in rr.CLASSES:
        imgs = sorted(p for p in rows if obj in rows[p])
        if len(imgs) < 20:
            continue
        cfg = choose(rows, obj, imgs)
        cfg["imgsz"] = a.imgsz
        out[obj] = cfg
        half = [imgs[0::2], imgs[1::2]]
        cv = defaultdict(int)
        for f in (0, 1):
            c = choose(rows, obj, half[f])
            for k, v in score(rows, obj, half[1 - f], c).items():
                cv[k] += v
        report[obj] = {"cfg": cfg, "fit": score(rows, obj, imgs, cfg), "cv2": dict(cv)}
    Path(a.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
    (REPO / "out" / "rooms").mkdir(parents=True, exist_ok=True)
    (REPO / "out" / "rooms" / "tune_report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    for obj, r in report.items():
        f, c = r["fit"], r["cv2"]
        pp = lambda s: f"ocr {s['ocr'] / s['n']:.1%} det {s['det'] / s['n']:.1%} comb {s['combined'] / s['n']:.1%} " \
                       f"proven {s['proven']} ({s['proven_ok'] / max(1, s['proven']):.1%})"
        print(f"{obj:9s} n={f['n']:4d}  fit: {pp(f)}   cv2: {pp(c)}   {r['cfg']}")


if __name__ == "__main__":
    main()
