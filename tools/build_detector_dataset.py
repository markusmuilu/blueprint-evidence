"""Build the YOLO detector dataset from public CubiCasa5K (clean list only) plus synthetic renders.

    python tools/build_detector_dataset.py --public ../data/cubicasa5k-public/cubicasa5k --overlap out/overlap
        --stoa ../data/stoa-full/junction-dataset --synth ../data/detector/synth --out ../data/detector/ds

Rules (README, "Training data"):
  - Only plans in <overlap>/train_clean.txt are used: public plans not in Stoa's data, plus public
    plans whose image is a Stoa train-split image. A plan in public_in_stoa_test.txt is refused
    even if a list is edited by hand.
  - A fraction of the train-split-matched plans (--tune-frac, chosen by a stable hash) and every
    plan matching a preview dev image is held out of training: those Stoa images are written to
    tune_images.txt, for tuning the count thresholds on images the detector has not seen.
  - Converter counts are compared with Stoa's answers on train-split images only, per object type
    (count_check.json), to confirm the class mapping in tools/cubicasa_svg.py.

Images are F1_original.png (the image Stoa's files are; the SVG coordinates are in the F1_scaled.png
frame, so boxes are scaled by the size ratio), re-encoded as JPEG q95 with the long side capped at
--max-side. Labels follow the image (normalised coordinates).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bpqa.data import DEFAULT_DATA, split_of  # noqa: E402
from tools.cubicasa_svg import CLASSES, convert, counts, to_yolo  # noqa: E402

Image.MAX_IMAGE_PIXELS = 300_000_000
OBJ_KEY = {"toilet": "toilet", "sink": "sink", "shower_or_bath": "shower_or_bath", "door": "door", "window": "window"}


def stable_frac(key: str) -> float:
    return int(hashlib.sha1(("tune|" + key).encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


IMAGE_ORDER = ("F1_original.png", "F1_scaled.png")   # original first: it is what Stoa's images are


def plan_image(plan_dir: Path) -> Path | None:
    for n in IMAGE_ORDER:
        if (plan_dir / n).exists():
            return plan_dir / n
    return None


def convert_plan(args):
    plan, public, out_img, out_lab, max_side = args
    d = public / plan
    img_p, svg_p = plan_image(d), d / "model.svg"
    if img_p is None or not svg_p.exists():
        return plan, None, "missing image or model.svg"
    try:
        boxes, _, unknown = convert(svg_p.read_text(encoding="utf-8", errors="replace"))
        im = Image.open(img_p).convert("RGB")
        # SVG coordinates are F1_scaled.png pixels (the svg width/height attributes are only the
        # drawing extent); for F1_original.png the boxes are scaled by the size ratio
        frame = Image.open(d / "F1_scaled.png").size if (d / "F1_scaled.png").exists() else None
        if frame is None:
            return plan, None, "no F1_scaled.png: annotation frame unknown"
        lines = to_yolo(boxes, frame, im.size)
        if max(im.size) > max_side:
            s = max_side / max(im.size)
            im = im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
        name = plan.replace("/", "-")
        if out_img is not None:
            im.save(out_img / f"{name}.jpg", quality=95)
            (out_lab / f"{name}.txt").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        return plan, {"counts": counts(boxes), "unknown": dict(unknown), "n": len(lines)}, None
    except Exception as ex:
        return plan, None, f"{type(ex).__name__}: {ex}"


def stoa_answers(stoa_root: Path):
    """Stoa CubiCasa count answers per image, train split only (test answers are never read)."""
    from bpqa.query import parse
    out = defaultdict(dict)
    with open(stoa_root / "questions.jsonl", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if "/cubicasa5k/" not in r["path"] or split_of(r["path"]) != "dev":
                continue
            q = parse(r["question"])
            if q.measure == "count" and q.obj in OBJ_KEY and q.lang == "en":
                out[r["path"]][q.obj] = r["answer"]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--public", required=True)
    ap.add_argument("--overlap", required=True, help="output folder of tools/overlap.py")
    ap.add_argument("--stoa", required=True, help="Stoa dataset root (for the train-split count check)")
    ap.add_argument("--synth", action="append", default=[], help="synthetic YOLO folder(s) with images/ and labels/")
    ap.add_argument("--out", required=True)
    ap.add_argument("--tune-frac", type=float, default=0.15)
    ap.add_argument("--val-frac", type=float, default=0.05)
    ap.add_argument("--max-side", type=int, default=2048)
    ap.add_argument("--no-public", action="store_true", help="synthetic only (smoke test)")
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    public, ov, out = Path(a.public), Path(a.overlap), Path(a.out)
    (out / "images").mkdir(parents=True, exist_ok=True)
    (out / "labels").mkdir(parents=True, exist_ok=True)
    report = {}

    train_imgs, val_imgs, tune = [], [], []
    if not a.no_public:
        read = lambda n: [l.strip() for l in (ov / f"{n}.txt").read_text(encoding="utf-8").splitlines() if l.strip()]
        clean, test = read("train_clean"), set(read("public_in_stoa_test"))
        bad = [k for k in clean if k in test]
        if bad:
            raise SystemExit(f"refusing: {len(bad)} plans in train_clean.txt match Stoa test images, e.g. {bad[:3]}")
        plan_to_stoa = defaultdict(set)
        for r in csv.DictReader(open(ov / "matches.csv", encoding="utf-8")):
            plan_to_stoa[r["public_plan"]].add(r["stoa_file"])
            if r["stoa_split"] == "test" and r["public_plan"] in clean:
                raise SystemExit(f"refusing: {r['public_plan']} matches test image {r['stoa_file']}")
        preview_dev = {f"data/cubicasa5k/{p.name}" for p in (Path(DEFAULT_DATA) / "data" / "cubicasa5k").glob("*.png")
                       if split_of(f"data/cubicasa5k/{p.name}") == "dev"}
        hold = {k for k in clean if plan_to_stoa.get(k) and
                (stable_frac(k) < a.tune_frac or plan_to_stoa[k] & preview_dev)}
        use = [k for k in clean if k not in hold]
        tune = sorted({s for k in hold for s in plan_to_stoa[k]})
        (out / "tune_images.txt").write_text("".join(s + "\n" for s in tune), encoding="utf-8")

        results, errors = {}, {}
        jobs = [(k, public, out / "images", out / "labels", a.max_side) for k in use]
        jobs += [(k, public, None, None, a.max_side) for k in sorted(hold)]   # counts only, no training files
        with ProcessPoolExecutor(a.workers) as ex:
            for i, (k, res, err) in enumerate(ex.map(convert_plan, jobs, chunksize=8)):
                if err:
                    errors[k] = err
                else:
                    results[k] = res
                if (i + 1) % 500 == 0:
                    print(f"  {i + 1}/{len(jobs)}", file=sys.stderr)
        rng = random.Random(1)
        for k in use:
            if k in results:
                p = str((out / "images" / (k.replace("/", "-") + ".jpg")).resolve())
                (val_imgs if (not plan_to_stoa.get(k) and rng.random() < a.val_frac) else train_imgs).append(p)

        # converter counts vs Stoa answers, train split only
        ans = stoa_answers(Path(a.stoa))
        agree = defaultdict(lambda: [0, 0])
        diffs = defaultdict(Counter)
        for k, res in results.items():
            for s in plan_to_stoa.get(k, ()):
                for obj, gold in ans.get(s, {}).items():
                    c = res["counts"][OBJ_KEY[obj]]
                    agree[obj][0] += int(c == gold)
                    agree[obj][1] += 1
                    diffs[obj][c - gold] += 1
        unknown = Counter()
        for res in results.values():
            unknown.update(res["unknown"])
        report = {
            "plans_clean": len(clean), "plans_held_out_for_tuning": len(hold), "tune_images": len(tune),
            "train_images_public": len(train_imgs), "val_images_public": len(val_imgs), "errors": len(errors),
            "error_examples": dict(list(errors.items())[:10]),
            "count_agreement_with_stoa_train_answers": {o: {"agree": v[0], "n": v[1], "rate": round(v[0] / max(1, v[1]), 3),
                                                            "pred_minus_gold": dict(sorted(diffs[o].items()))}
                                                        for o, v in agree.items()},
            "ignored_furniture_types": dict(unknown.most_common(40)),
        }
        (out / "count_check.json").write_text(json.dumps(report, indent=1), encoding="utf-8")

    for s in a.synth:
        imgs = sorted(str(p.resolve()) for p in (Path(s) / "images").glob("*.png"))
        rng = random.Random(2)
        for p in imgs:
            (val_imgs if rng.random() < a.val_frac else train_imgs).append(p)
        report[f"synth:{s}"] = len(imgs)

    (out / "train.txt").write_text("".join(p + "\n" for p in train_imgs), encoding="utf-8")
    (out / "val.txt").write_text("".join(p + "\n" for p in val_imgs), encoding="utf-8")
    yaml = [f"path: {out.resolve().as_posix()}", "train: train.txt", "val: val.txt", "names:"]
    yaml += [f"  {i}: {c}" for i, c in enumerate(CLASSES)]
    (out / "data.yaml").write_text("\n".join(yaml) + "\n", encoding="utf-8")
    report.update({"train_images_total": len(train_imgs), "val_images_total": len(val_imgs)})
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
