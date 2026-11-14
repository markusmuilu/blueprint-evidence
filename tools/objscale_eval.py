"""Validate the object scale (bpqa/scale_objects.py) against printed room areas, leave-one-out style.

Plans: CubiCasa scans of our dev split (bpqa.data.split_of), whose public plan is not in the CubiCasa5K test
split, that print room areas and have a printed-area scale (tools/objscale_regions.py caches the regions and that
scale). Each plan goes to the "tune" or the "val" half by a hash of its file name. The printed areas are hidden
from the object scale entirely: the scale comes from the detector boxes only, and the region set is the walls
hypothesis nearest to the object scale (not the one the printed areas would choose). Each printed room with a
usable region in that set is then measured and compared with its printed value.

    python tools/objscale_eval.py calib  --regions out/objscale/regions        size of each object vs printed scale
    python tools/objscale_eval.py loo    --regions out/objscale/regions [--half val] [--out rows.jsonl]
    python tools/objscale_eval.py tune   --regions out/objscale/regions        grid search on the tune half

Needs the detector cache for the plans (tools/objscale_detect.py).
"""
import argparse
import copy
import hashlib
import itertools
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np

from bpqa import scale_objects as OS
from bpqa.data import data_root

CUTS = (3.0, 6.0)


def half(name: str) -> str:
    return "tune" if int(hashlib.sha1(("objscale" + name).encode()).hexdigest()[:8], 16) % 2 == 0 else "val"


def load(regions: Path, root: Path):
    plans = []
    for p in sorted(regions.glob("*.json")):
        r = json.loads(p.read_text(encoding="utf-8"))
        f = root / "data" / "cubicasa5k" / r["file"]
        r["dets"] = OS.detections(f, allow_run=False)
        r["half"] = half(r["file"])
        plans.append(r)
    return plans


def ppm_true(r):
    return None if not r["scale_m2_per_px"] else 1 / np.sqrt(r["scale_m2_per_px"])


def evaluate(plans, objects, min_n, max_iqr):
    """Rows per measured printed room, and per-plan outcomes."""
    rows, out = [], []
    for r in plans:
        pt = ppm_true(r)
        comb = OS.combine(OS.candidates(r["dets"], objects), min_n, max_iqr)
        o = {"file": r["file"], "half": r["half"], "n": comb.n, "iqr": comb.iqr, "known": comb.known, "ref": pt is not None}
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
                             "classes": sorted({c["cls"] for c in comb.used}), "n_obj": comb.n})
        out.append(o)
    return rows, out


def stats(a, e):
    if not len(a):
        return {"n": 0}
    return {"n": int(len(a)), "median": round(float(np.median(a)), 3), "p80": round(float(np.percentile(a, 80)), 3),
            "within2": round(float(np.mean(a <= .02)), 3), "within5": round(float(np.mean(a <= .05)), 3),
            "within10": round(float(np.mean(a <= .10)), 3), "signed": round(float(np.median(e)), 3)}


def report(rows, out, by="pred"):
    known = [o for o in out if o["known"]]
    res = {"plans": len(out), "plans_scaled": len(known), "abstain_share": round(1 - len(known) / max(len(out), 1), 3)}
    ref = [o for o in known if "scale_err" in o]
    res["plans_with_printed_scale"] = sum(1 for o in out if o.get("ref"))
    if ref:
        res["scale_err_plans"] = len(ref)
        se = np.array([o["scale_err"] for o in ref])
        res["scale_err"] = {"median_abs": round(float(np.median(np.abs(se))), 3), "signed": round(float(np.median(se)), 3),
                            "within5": round(float(np.mean(np.abs(se) <= .05)), 3),
                            "within10": round(float(np.mean(np.abs(se) <= .10)), 3)}
    e = np.array([r["err"] for r in rows])
    a = np.abs(e)
    s = np.array([r[by] for r in rows]) if rows else np.array([])
    res["ref_plan_rooms"] = stats(np.abs([r["err"] for r in rows if r["ref"]]), np.array([r["err"] for r in rows if r["ref"]]))
    res["rooms"] = stats(a, e)
    res["rooms_plans"] = len({r["file"] for r in rows})
    if rows:
        for name, m in (("under3", s < CUTS[0]), ("3to6", (s >= CUTS[0]) & (s < CUTS[1])), ("6up", s >= CUTS[1])):
            res[name] = stats(a[m], e[m])
    return res


def calib(plans, objects):
    """Metres of each object's long side, measured with the printed-area scale."""
    res = {}
    for cls, o in objects.items():
        o2 = {cls: {**o, "metres": 1.0}}
        v = []
        for r in plans:
            pt = ppm_true(r)
            if pt is None:
                continue
            for c in OS.candidates(r["dets"], o2):
                v.append(c["px"] / pt)
        v = np.array(v)
        if len(v):
            q1, q2, q3 = np.percentile(v, [25, 50, 75])
            res[cls] = {"n": len(v), "plans": None, "median_m": round(float(q2), 3), "q1": round(float(q1), 3),
                        "q3": round(float(q3), 3), "iqr_rel": round(float((q3 - q1) / q2), 3)}
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["calib", "loo", "tune"])
    ap.add_argument("--data")
    ap.add_argument("--regions", default="out/objscale/regions")
    ap.add_argument("--half", default="all", choices=["all", "tune", "val"])
    ap.add_argument("--by", default="pred", choices=["pred", "printed"], help="room-size band by measured or printed area")
    ap.add_argument("--out")
    a = ap.parse_args()
    plans = load(Path(a.regions), data_root(a.data))
    print(f"plans {len(plans)}, with a printed-area scale {sum(ppm_true(r) is not None for r in plans)}, "
          f"tune {sum(r['half'] == 'tune' for r in plans)}, val {sum(r['half'] == 'val' for r in plans)}")
    sel = plans if a.half == "all" else [r for r in plans if r["half"] == a.half]
    if a.cmd == "calib":
        for h in ("tune", "val"):
            print(h, json.dumps(calib([r for r in plans if r["half"] == h], OS.OBJECTS)))
        return
    if a.cmd == "loo":
        rows, out = evaluate(sel, OS.OBJECTS, OS.MIN_CANDIDATES, OS.MAX_IQR)
        print(json.dumps(report(rows, out, a.by), indent=1))
        if a.out:
            Path(a.out).parent.mkdir(parents=True, exist_ok=True)
            Path(a.out).write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
        return
    tune = [r for r in plans if r["half"] == "tune"]
    results = []
    for min_n, max_iqr, door_conf, door_asp, use in itertools.product(
            (3, 4, 5, 6), (0.08, 0.12, 0.16, 0.2, 0.25, 0.35), (0.4, 0.5, 0.6, 0.7), (1.5, 2.0, 2.5, 3.0),
            ("all", "door", "door+toilet")):
        objs = copy.deepcopy(OS.OBJECTS)
        objs["door"]["min_conf"], objs["door"]["min_aspect"] = door_conf, door_asp
        if use == "door":
            objs = {"door": objs["door"]}
        elif use == "door+toilet":
            objs = {k: objs[k] for k in ("door", "toilet")}
        rows, out = evaluate(tune, objs, min_n, max_iqr)
        rep = report(rows, out)
        results.append(((min_n, max_iqr, door_conf, door_asp, use), rep))
    results.sort(key=lambda x: (x[1].get("6up", {}).get("median", 9), -x[1]["plans_scaled"]))
    for k, rep in results:
        print(k, rep["plans_scaled"], rep["abstain_share"], rep.get("scale_err"), rep.get("6up"), rep["rooms"])


if __name__ == "__main__":
    main()
