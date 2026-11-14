"""Report for tools/model_bakeoff.py: reads out/bakeoff/*.jsonl and ours.json, writes
out/model_bakeoff.json and docs/model_bakeoff.md."""
from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUTD = REPO / "out" / "bakeoff"

CATS = [("scan_door", "Scan doors"), ("scan_window", "Scan windows"), ("scan_room_type", "Scan room types"),
        ("scan_other", "Scan other counts"), ("swiss_count", "Swiss counts"), ("swiss_area", "Swiss areas"),
        ("ifc_area", "IFC areas")]
GROUPS = [("areas", "Areas (Swiss + IFC)", ["swiss_area", "ifc_area"])]


class _A:
    def __init__(self, v):
        self.value, self.answered = v, v is not None


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def load_runs():
    runs = {}
    for f in sorted(OUTD.glob("*.jsonl")):
        model_mode = f.stem
        recs = {}
        for line in f.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            recs[r["qid"]] = r
        cut = (OUTD / f"{model_mode}.cut").exists()
        runs[model_mode] = {"recs": recs, "cut": cut}
    return runs


def report(root, qs, s):
    from model_bakeoff import category
    from eval import score
    ours = json.loads((OUTD / "ours.json").read_text(encoding="utf-8"))
    ours_full = {int(k): v for k, v in ours["full"].items()}
    ours_pdf = {int(k): v for k, v in ours["pdf_only"].items()}
    cat_of = {i: category(qs[i], s) for i in qs if i in set(s["base150"]) | set(s["weak110"]) | set(s["text60"])}
    runs = load_runs()
    sets = {"base150": set(s["base150"]), "weak110": set(s["weak110"]), "text60": set(s["text60"])}
    PRICE = json.loads((Path(__file__).parent / "bakeoff_prices.json").read_text(encoding="utf-8")) \
        if (Path(__file__).parent / "bakeoff_prices.json").exists() else {}

    def stats(recs, ids, ref):
        ids = [i for i in ids if i in recs]
        n = len(ids)
        ok = [i for i in ids if score(qs[i], _A(recs[i]["pred"]))]
        ans = [i for i in ids if recs[i]["pred"] is not None]
        lat = [recs[i]["latency_s"] for i in ids if recs[i].get("latency_s")]
        pin = [recs[i].get("prompt_tokens") or 0 for i in ids]
        pout = [recs[i].get("completion_tokens") or 0 for i in ids]
        o_ok = [i for i in ids if ref.get(i, {}).get("ok")]
        both_wrong_model_right = [i for i in ids if i in ok and not ref.get(i, {}).get("ok")]
        ours_right_model_wrong = [i for i in ids if i not in ok and ref.get(i, {}).get("ok")]
        lo, hi = wilson(len(ok), n)
        return {"n": n, "correct": len(ok), "accuracy": len(ok) / n if n else None, "ci95": [lo, hi],
                "answered": len(ans), "abstain_rate": 1 - len(ans) / n if n else None,
                "acc_when_answered": len([i for i in ans if i in ok]) / len(ans) if ans else None,
                "latency_median_s": statistics.median(lat) if lat else None,
                "tokens_in_mean": sum(pin) / n if n else None, "tokens_out_mean": sum(pout) / n if n else None,
                "ours_correct": len(o_ok), "ours_accuracy": len(o_ok) / n if n else None,
                "ours_answered": len([i for i in ids if ref.get(i, {}).get("pred") is not None]),
                "ours_wrong_model_right": len(both_wrong_model_right),
                "ours_right_model_wrong": len(ours_right_model_wrong)}

    def cat_ids(setname, cats):
        return [i for i in sorted(sets[setname]) if cat_of.get(i) in cats]

    result = {"models": {}, "ours": {}, "notes": {}}
    # ours on each set
    for nm, ref in (("full", ours_full), ("pdf_only", ours_pdf)):
        d = {}
        for sn in sets:
            ids = [i for i in sets[sn] if i in ref]
            ok = sum(1 for i in ids if ref[i]["ok"])
            d[sn] = {"n": len(ids), "correct": ok, "accuracy": ok / len(ids) if ids else None}
            for c, _ in CATS:
                cid = [i for i in ids if cat_of.get(i) == c]
                if cid:
                    d[f"{sn}/{c}"] = {"n": len(cid), "correct": sum(1 for i in cid if ref[i]["ok"]),
                                       "answered": sum(1 for i in cid if ref[i]["pred"] is not None)}
        result["ours"][nm] = d
    for key, run in runs.items():
        model, mode = key.rsplit("__", 1)
        ref = ours_pdf if mode == "text" else ours_full
        entry = {"mode": mode, "cut": run["cut"], "n_evaluated": len(run["recs"]),
                 "errors": sum(1 for r in run["recs"].values() if r.get("error")), "sets": {}, "cats": {}}
        for sn in sets:
            ids = sorted(sets[sn])
            entry["sets"][sn] = stats(run["recs"], ids, ref)
        pooled = sorted(set(sets["base150"]) | set(sets["weak110"]) | set(sets["text60"]))
        for c, _ in CATS:
            st = stats(run["recs"], [i for i in pooled if cat_of.get(i) == c], ref)
            if st["n"]:
                entry["cats"][c] = st
        for g, _, cs in GROUPS:
            st = stats(run["recs"], [i for i in pooled if cat_of.get(i) in cs], ref)
            if st["n"]:
                entry["cats"][g] = st
        result["models"][key] = entry
    result["price_note"] = ("NVIDIA build.nvidia.com free tier: no per-token price is published for these hosted "
                            "endpoints, so cost is unknown and tokens per question are reported instead.")
    (REPO / "out").mkdir(exist_ok=True)
    (REPO / "out" / "model_bakeoff.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    write_md(result, cat_of, sets, ours_full, ours_pdf, qs)
    print("wrote out/model_bakeoff.json and docs/model_bakeoff.md")


def pct(x, d=0):
    return "-" if x is None else f"{100 * x:.{d}f}%"


def cell(st):
    return "-" if not st or not st["n"] else f"{pct(st['accuracy'])} ({st['correct']}/{st['n']})"


def write_md(result, cat_of, sets, ours_full, ours_pdf, qs):
    L = []
    models = result["models"]
    img = {k: v for k, v in models.items() if v["mode"] == "image"}
    txt = {k: v for k, v in models.items() if v["mode"] == "text"}
    L.append("# Model bake-off\n")
    L.append(Path(__file__).with_name("bakeoff_intro.md").read_text(encoding="utf-8") if
             Path(__file__).with_name("bakeoff_intro.md").exists() else "")
    (REPO / "docs").mkdir(exist_ok=True)
    # filled by the author after the numbers are in: tables follow
    L.append("\n## Results: vision models, accuracy per category (pooled unique questions)\n")
    hdr = "| Model | " + " | ".join(n for _, n in CATS) + " | " + " | ".join(n for _, n, _ in GROUPS) + " |"
    L.append(hdr)
    L.append("|" + "---|" * (len(CATS) + len(GROUPS) + 1))
    # ours row: on the pooled questions
    pooled_ids = sorted(set().union(*sets.values()))
    ours_row = []
    for c, _ in CATS:
        ids = [i for i in pooled_ids if cat_of.get(i) == c and i in ours_full]
        ours_row.append("-" if not ids else f"{pct(sum(ours_full[i]['ok'] for i in ids) / len(ids))} ({sum(ours_full[i]['ok'] for i in ids)}/{len(ids)})")
    for g, _, cs in GROUPS:
        ids = [i for i in pooled_ids if cat_of.get(i) in cs and i in ours_full]
        ours_row.append(f"{pct(sum(ours_full[i]['ok'] for i in ids) / len(ids))} ({sum(ours_full[i]['ok'] for i in ids)}/{len(ids)})")
    L.append("| **Our router (Nemotron off)** | " + " | ".join(ours_row) + " |")
    for k, e in sorted(img.items()):
        cells = [cell(e["cats"].get(c)) for c, _ in CATS] + [cell(e["cats"].get(g)) for g, _, _ in GROUPS]
        L.append(f"| {k.rsplit('__', 1)[0]}{' (stopped early)' if e['cut'] else ''} | " + " | ".join(cells) + " |")
    L.append("\n## Our router on the pooled questions, by tier\n")
    L.append("| Category | n | Answered | Correct | Proven right/answered | Estimate right/answered |")
    L.append("|---|---|---|---|---|---|")
    for c, name in CATS:
        ids = [i for i in pooled_ids if cat_of.get(i) == c and i in ours_full]
        if not ids:
            continue
        R = [ours_full[i] for i in ids]

        def tr(t, R=R):
            x = [r for r in R if r["tier"] == t and r["pred"] is not None]
            return f"{sum(r['ok'] for r in x)}/{len(x)}"
        L.append(f"| {name} | {len(ids)} | {sum(r['pred'] is not None for r in R)} | {sum(r['ok'] for r in R)} | {tr('proven')} | {tr('estimate')} |")
    L.append("\nThe router never abstained on these questions; scan doors, windows and room types are all 'estimate' tier.\n")
    L.append("\nCells are accuracy with correct/total; an abstention or unparseable reply counts as wrong. "
             "Models were not all run on every question (cut models and rate-limit failures), so compare a model "
             "with the 'Our router' row only where the n is the same; the per-model tables below pair "
             "our answers question by question.\n")
    for sn, title in (("base150", "Naive-baseline sample, 150 questions"), ("weak110", "Scan weak spots, 110 questions")):
        L.append(f"\n## {title}\n")
        L.append("| Model | n | Accuracy (95% CI) | Abstain | Acc. when answered | Median latency | Tokens in/out per q | Ours on the same questions | Ours wrong, model right | Ours right, model wrong |")
        L.append("|---|---|---|---|---|---|---|---|---|---|")
        for k, e in sorted(img.items()):
            st = e["sets"][sn]
            if not st["n"]:
                continue
            L.append(f"| {k.rsplit('__', 1)[0]} | {st['n']} | {pct(st['accuracy'])} ({pct(st['ci95'][0])} to {pct(st['ci95'][1])}) | "
                     f"{pct(st['abstain_rate'])} | {pct(st['acc_when_answered'])} | "
                     f"{'-' if st['latency_median_s'] is None else format(st['latency_median_s'], '.1f') + ' s'} | "
                     f"{st['tokens_in_mean']:.0f} / {st['tokens_out_mean']:.0f} | {pct(st['ours_accuracy'])} | "
                     f"{st['ours_wrong_model_right']} | {st['ours_right_model_wrong']} |")
    L.append("\n## Per-category detail, vision models\n")
    for c, name in CATS + [(g, n) for g, n, _ in GROUPS]:
        rows = [(k, e["cats"][c]) for k, e in sorted(img.items()) if c in e["cats"] and e["cats"][c]["n"] >= 5]
        if not rows:
            continue
        L.append(f"\n### {name}\n")
        L.append("| Model | n | Accuracy | Abstain | Median latency | Ours (same qs) | Ours wrong, model right | Ours right, model wrong |")
        L.append("|---|---|---|---|---|---|---|---|")
        for k, st in rows:
            L.append(f"| {k.rsplit('__', 1)[0]} | {st['n']} | {pct(st['accuracy'])} | {pct(st['abstain_rate'])} | "
                     f"{'-' if st['latency_median_s'] is None else format(st['latency_median_s'], '.1f') + ' s'} | "
                     f"{pct(st['ours_accuracy'])} | {st['ours_wrong_model_right']} | {st['ours_right_model_wrong']} |")
    if txt:
        L.append("\n## Text-only models over the PDF text layer (60 Swiss and IFC-plan questions)\n")
        L.append("| Model | Swiss counts | Swiss areas | IFC areas | All 60 | Abstain | Median latency | Tokens in/out per q |")
        L.append("|---|---|---|---|---|---|---|---|")
        t60 = sorted(sets["text60"])
        orow = []
        for c in ("swiss_count", "swiss_area", "ifc_area"):
            ids = [i for i in t60 if cat_of.get(i) == c and i in ours_pdf]
            orow.append(f"{pct(sum(ours_pdf[i]['ok'] for i in ids) / len(ids))} ({sum(ours_pdf[i]['ok'] for i in ids)}/{len(ids)})")
        ok = sum(ours_pdf[i]["ok"] for i in t60 if i in ours_pdf)
        L.append(f"| **Our router, PDF only (no DXF/IFC twin)** | {' | '.join(orow)} | {pct(ok / 60)} ({ok}/60) | - | - | - |")
        for k, e in sorted(txt.items()):
            st = e["sets"]["text60"]
            L.append(f"| {k.rsplit('__', 1)[0]} | " + " | ".join(cell(e["cats"].get(c)) for c in ("swiss_count", "swiss_area", "ifc_area"))
                     + f" | {cell(st)} | {pct(st['abstain_rate'])} | "
                     f"{'-' if st['latency_median_s'] is None else format(st['latency_median_s'], '.1f') + ' s'} | "
                     f"{st['tokens_in_mean']:.0f} / {st['tokens_out_mean']:.0f} |")
    (REPO / "docs" / "model_bakeoff.md").write_text("\n".join(L) + "\n", encoding="utf-8")
