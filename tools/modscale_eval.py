"""Validate the printed door-module scale (bpqa/scale_objects.py, scale source b) against printed room areas.

Same plans, halves and region caches as tools/objscale_eval.py: CubiCasa scans of our dev split whose public
plan is not in the CubiCasa5K test split and that print 2+ room areas (out/objscale/regions). The printed areas
are hidden from the module scale entirely: it comes from door module labels ("9x21") read by OCR and the door
boxes they label. The region set is the walls hypothesis nearest to the module scale. Each printed room with a
usable region is measured and compared with its printed value.

    python tools/modscale_eval.py calib  [--half tune]      door box long side / printed module width
    python tools/modscale_eval.py loo    [--half val] [--out rows.jsonl]
    python tools/modscale_eval.py avail  --list <names>     how often a module scale is set (any plan, no areas needed)

Needs the detector and OCR caches for the plans (tools/objscale_detect.py, tools/ocr_r0.py).
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np

from bpqa import scale_objects as OS
from bpqa.data import data_root
from objscale_eval import half, ppm_true, report  # noqa: E402


def load(regions: Path, root: Path):
    plans = []
    for p in sorted(regions.glob("*.json")):
        r = json.loads(p.read_text(encoding="utf-8"))
        f = root / "data" / "cubicasa5k" / r["file"]
        r["doors"] = (OS.detections(f, allow_run=False).get("door") or [])
        r["mods"] = OS.plan_module_tokens(f, allow_run=False)
        r["half"] = half(r["file"])
        plans.append(r)
    return plans


def evaluate(plans):
    rows, out = [], []
    for r in plans:
        pt = ppm_true(r)
        cands = OS.module_candidates(r["mods"], r["doors"])
        comb = OS.combine_modules(cands)
        o = {"file": r["file"], "half": r["half"], "n": comb.n, "iqr": comb.iqr, "known": comb.known, "ref": pt is not None,
             "mods": len(r["mods"]), "matched": len(cands)}
        if comb.known:
            ppm = 1 / comb.m_per_px
            if pt is not None:
                o["scale_err"] = ppm / pt - 1
            g = min((k for k in r["hyps"] if k != "None"), key=lambda k: abs(np.log(float(k) / ppm)))
            m2 = comb.m_per_px ** 2
            for rm in r["hyps"][g]:
                if rm["printed"] is None:
                    continue
                pred = rm["pixels"] * m2
                rows.append({"file": r["file"], "label": rm["label"], "printed": rm["printed"], "pred": round(pred, 2),
                             "err": round((pred - rm["printed"]) / rm["printed"], 4), "half": r["half"], "ref": pt is not None,
                             "classes": ["door_module"], "n_obj": comb.n})
        out.append(o)
    return rows, out


def calib(plans):
    v = []
    saved = OS.BOX_PER_MODULE
    OS.BOX_PER_MODULE = 1.0
    for r in plans:
        pt = ppm_true(r)
        if pt is None:
            continue
        for c in OS.module_candidates(r["mods"], r["doors"]):
            v.append(c["px"] / (c["w_m"] * pt))
    OS.BOX_PER_MODULE = saved
    v = np.array(v)
    if not len(v):
        return {"n": 0}
    q1, q2, q3 = np.percentile(v, [25, 50, 75])
    return {"n": int(len(v)), "plans": sum(1 for r in plans if ppm_true(r) is not None and OS.module_candidates(r["mods"], r["doors"])),
            "box_per_module": round(float(q2), 3), "q1": round(float(q1), 3), "q3": round(float(q3), 3)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["calib", "loo", "avail"])
    ap.add_argument("--data")
    ap.add_argument("--regions", default="out/objscale/regions")
    ap.add_argument("--half", default="all", choices=["all", "tune", "val"])
    ap.add_argument("--list")
    ap.add_argument("--out")
    ap.add_argument("--k", type=float, help="override BOX_PER_MODULE (cross-fitting: the other half's calib value)")
    a = ap.parse_args()
    if a.k:
        OS.BOX_PER_MODULE = a.k
    root = data_root(a.data)
    if a.cmd == "avail":
        names = [n for n in Path(a.list).read_text().split() if n]
        n_mod = n_match = n_known = n_obj = n_either = 0
        for n in names:
            f = root / "data" / "cubicasa5k" / n
            det = OS.detections(f, allow_run=False)
            mods = OS.plan_module_tokens(f, allow_run=False)
            c = OS.module_candidates(mods, det.get("door") or [])
            k = OS.combine_modules(c).known
            ko = OS.combine(OS.candidates(det)).known
            n_mod += len(mods) >= 2
            n_match += len(c) >= 2
            n_known += k
            n_obj += ko
            n_either += k or ko
        print(json.dumps({"plans": len(names), "2+ module labels": n_mod, "2+ matched to doors": n_match,
                          "module scale set": n_known, "object scale set": n_obj, "either": n_either}))
        return
    plans = load(Path(a.regions), root)
    sel = plans if a.half == "all" else [r for r in plans if r["half"] == a.half]
    if a.cmd == "calib":
        for h in ("tune", "val"):
            print(h, json.dumps(calib([r for r in plans if r["half"] == h])))
        return
    rows, out = evaluate(sel)
    rep = report(rows, out)
    rep["plans_with_2+_module_labels"] = sum(o["mods"] >= 2 for o in out)
    rep["plans_with_2+_matched"] = sum(o["matched"] >= 2 for o in out)
    print(json.dumps(rep, indent=1))
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")


if __name__ == "__main__":
    main()
