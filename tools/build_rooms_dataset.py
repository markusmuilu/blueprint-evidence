"""Build the YOLO room-type dataset from public CubiCasa5K room polygons (clean list only).

    python tools/build_rooms_dataset.py --public ../data/cubicasa5k-public/cubicasa5k --overlap out/overlap
        --stoa ../data/stoa-full/junction-dataset --out ../data/detector/ds_rooms

Same plan rules as tools/build_detector_dataset.py (README "Detector"), and the same held-out plans:
  - only plans in <overlap>/train_clean.txt (public plans not in Stoa, or matching only Stoa
    train-split images); a plan matching a Stoa test-split image is refused;
  - plans held out by build_detector_dataset.stable_frac (< --tune-frac) or matching a preview dev
    image are not trained on; their Stoa images go to tune_images.txt (identical to the fixture
    detector's list), the only images the room route is tuned on.
Rooms (tools/cubicasa_rooms.py) become boxes of six classes: kitchen, bathroom, wc_room, sauna,
bedroom, balcony. The room counts are compared with Stoa's train-split answers (count_check.json)
to confirm the mapping. Images are F1_original.png re-encoded as JPEG, long side capped at --max-side.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bpqa.data import DEFAULT_DATA, split_of  # noqa: E402
from tools.build_detector_dataset import plan_image, stable_frac  # noqa: E402
from tools.cubicasa_rooms import ROOM_TYPES, counts, typed_rooms  # noqa: E402

Image.MAX_IMAGE_PIXELS = 300_000_000


def convert_plan(args):
    plan, public, out_img, out_lab, max_side = args
    d = public / plan
    img_p, svg_p = plan_image(d), d / "model.svg"
    if img_p is None or not svg_p.exists() or not (d / "F1_scaled.png").exists():
        return plan, None, "missing image, model.svg or F1_scaled.png"
    try:
        typed = typed_rooms(svg_p.read_text(encoding="utf-8", errors="replace"))
        res = {"counts": counts(typed)}
        if out_img is None:
            return plan, res, None
        im = Image.open(img_p).convert("RGB")
        fw, fh = Image.open(d / "F1_scaled.png").size        # SVG coordinates are in this frame
        W, H = im.size
        lines = []
        for typ, _, poly, _ in typed:
            if typ is None:
                continue
            x0, y0 = poly.min(0) * (W / fw, H / fh)
            x1, y1 = poly.max(0) * (W / fw, H / fh)
            x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
            if x1 - x0 < 4 or y1 - y0 < 4:
                continue
            lines.append(f"{ROOM_TYPES.index(typ)} {(x0 + x1) / 2 / W:.6f} {(y0 + y1) / 2 / H:.6f} "
                         f"{(x1 - x0) / W:.6f} {(y1 - y0) / H:.6f}")
        if max(im.size) > max_side:
            s = max_side / max(im.size)
            im = im.resize((round(W * s), round(H * s)), Image.LANCZOS)
        name = plan.replace("/", "-")
        im.save(out_img / f"{name}.jpg", quality=92)
        (out_lab / f"{name}.txt").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        res["n"] = len(lines)
        return plan, res, None
    except Exception as ex:
        return plan, None, f"{type(ex).__name__}: {ex}"


def stoa_answers(stoa_root: Path):
    """Stoa CubiCasa room-count answers per image, train split only (test answers are never read)."""
    from bpqa.query import parse
    out = defaultdict(dict)
    with open(stoa_root / "questions.jsonl", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if "/cubicasa5k/" not in r["path"] or split_of(r["path"]) != "dev":
                continue
            q = parse(r["question"])
            if q.measure == "count" and q.obj in ROOM_TYPES and q.lang == "en":
                out[r["path"]][q.obj] = r["answer"]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--public", required=True)
    ap.add_argument("--overlap", required=True, help="output folder of tools/overlap.py")
    ap.add_argument("--stoa", required=True, help="Stoa dataset root (for the train-split count check)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--tune-frac", type=float, default=0.15)
    ap.add_argument("--val-frac", type=float, default=0.05)
    ap.add_argument("--max-side", type=int, default=1600)
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    public, ov, out = Path(a.public), Path(a.overlap), Path(a.out)
    (out / "images").mkdir(parents=True, exist_ok=True)
    (out / "labels").mkdir(parents=True, exist_ok=True)

    def read(n):
        return [l.strip() for l in (ov / f"{n}.txt").read_text(encoding="utf-8").splitlines() if l.strip()]

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
    hold = {k for k in clean if plan_to_stoa.get(k) and (stable_frac(k) < a.tune_frac or plan_to_stoa[k] & preview_dev)}
    use = [k for k in clean if k not in hold]
    tune = sorted({s for k in hold for s in plan_to_stoa[k]})
    (out / "tune_images.txt").write_text("".join(s + "\n" for s in tune), encoding="utf-8")

    results, errors = {}, {}
    jobs = [(k, public, out / "images", out / "labels", a.max_side) for k in use]
    jobs += [(k, public, None, None, a.max_side) for k in sorted(hold)]      # counts only, no training files
    with ProcessPoolExecutor(a.workers) as ex:
        for i, (k, res, err) in enumerate(ex.map(convert_plan, jobs, chunksize=8)):
            if err:
                errors[k] = err
            else:
                results[k] = res
            if (i + 1) % 500 == 0:
                print(f"  {i + 1}/{len(jobs)}", file=sys.stderr)
    train_imgs, val_imgs = [], []
    rng = random.Random(1)
    for k in use:
        if k in results:
            p = f"images/{k.replace('/', '-')}.jpg"
            (val_imgs if rng.random() < a.val_frac else train_imgs).append(p)

    ans = stoa_answers(Path(a.stoa))
    agree, diffs = defaultdict(lambda: [0, 0]), defaultdict(Counter)
    for k, res in results.items():
        for s in plan_to_stoa.get(k, ()):
            for obj, gold in ans.get(s, {}).items():
                c = res["counts"][obj]
                agree[obj][0] += int(c == gold)
                agree[obj][1] += 1
                diffs[obj][c - gold] += 1
    inst = Counter()
    for k in use:
        if k in results:
            inst.update(results[k]["counts"])
    report = {"plans_clean": len(clean), "plans_held_out_for_tuning": len(hold), "tune_images": len(tune),
              "train_images": len(train_imgs), "val_images": len(val_imgs), "errors": len(errors),
              "error_examples": dict(list(errors.items())[:10]), "train_room_instances": dict(inst),
              "count_agreement_with_stoa_train_answers": {
                  o: {"agree": v[0], "n": v[1], "rate": round(v[0] / max(1, v[1]), 4),
                      "pred_minus_gold": dict(sorted(diffs[o].items()))} for o, v in agree.items()}}
    (out / "count_check.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    (out / "train.txt").write_text("".join(f"./{p}\n" for p in train_imgs), encoding="utf-8")
    (out / "val.txt").write_text("".join(f"./{p}\n" for p in val_imgs), encoding="utf-8")
    yaml = ["path: .", "train: train.txt", "val: val.txt", "names:"] + [f"  {i}: {c}" for i, c in enumerate(ROOM_TYPES)]
    (out / "data.yaml").write_text("\n".join(yaml) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
