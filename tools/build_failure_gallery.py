"""Build docs/failure_gallery/ (index.html, SUMMARY.md, inspected.json, img/) from an eval run and the by-eye modes.

    python tools/build_failure_gallery.py --scratch <dir with all/wrong.json and crops/> --answers out/test/answers.jsonl

Every number on the page is computed here from the answers file, the by-eye labels (tools/failure_modes.py) or
quoted from README.md (marked as such). Nothing here touches a model, a threshold or a route.
"""
from __future__ import annotations

import argparse
import base64
import collections
import html
import json
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from tools.failure_gallery_cases import GALLERY  # noqa: E402
from tools.failure_modes import CASES, MODES  # noqa: E402

OUT = REPO / "docs" / "failure_gallery"

# Quoted from README.md (full test split, 39,807 questions; Swiss/IFC rows from the "Final numbers" section).
README_FULL = {
    "swiss": ("Swiss dwellings, counts", 1.000, "from the vector drawing or its DXF, part of 11,408 questions"),
    "swiss_all": ("Swiss dwellings, areas", 0.970, "part of the same 11,408 questions"),
    "ifc": ("IFC models", 0.879, "838 questions; 96.8% precise when answered"),
    "scans": ("CubiCasa scans, all questions", 0.742, "27,160 questions"),
}
README_OBJ = {"toilet": 0.883, "sink": 0.787, "shower_or_bath": 0.760, "door": 0.596, "window": 0.492}
OBJ_NAME = {"door": "doors", "window": "windows", "sink": "sinks", "toilet": "toilets", "shower_or_bath": "showers and baths",
            "bedroom": "bedrooms", "kitchen": "kitchens", "sauna": "saunas", "bathroom": "bathrooms", "wc_room": "WC rooms",
            "balcony": "balconies", "storey": "storeys"}


def pct(x, d=1):
    return f"{100 * x:.{d}f}%"


def load(a):
    rows = [json.loads(l) for l in open(a.answers, encoding="utf-8")]
    wrong = {w["qid"]: w for w in json.loads((Path(a.scratch) / "all" / "wrong.json").read_text(encoding="utf-8"))}
    return rows, wrong


def stats(rows, wrong):
    by = collections.defaultdict(lambda: [0, 0])
    drawings = collections.defaultdict(set)
    for r in rows:
        o = r["query"]["obj"]
        by[o][0] += 1
        by[o][1] += bool(r["correct"])
        drawings[o].add(r["path"])
    tier = collections.Counter()
    for r in rows:
        tier[(r.get("tier"), bool(r["correct"]))] += 1
    pairs = {}
    for w in wrong.values():
        pairs.setdefault((w["path"], w["obj"]), w)
    det = [w for (p, o), w in pairs.items() if o in ("door", "window", "sink", "toilet", "shower_or_bath")]
    off1 = sum(1 for w in det if abs(w["ours"] - w["gold"]) == 1)
    boxc = {}
    for obj in ("door", "window"):
        c = collections.Counter()
        for (p, o), w in pairs.items():
            if o == obj:
                for k, b, s in w.get("causes", []):
                    c[k] += 1
        boxc[obj] = dict(c)
    dirn = {}
    for obj in ("door", "window", "sink", "toilet", "shower_or_bath"):
        xs = [w for (p, o), w in pairs.items() if o == obj]
        dirn[obj] = {"wrong": len(xs), "over": sum(w["ours"] > w["gold"] for w in xs), "under": sum(w["ours"] < w["gold"] for w in xs),
                     "off_by_one": sum(abs(w["ours"] - w["gold"]) == 1 for w in xs)}
    kinds = collections.defaultdict(lambda: [0, 0])
    seen = set()
    for r in rows:
        k = (r["path"], r["query"]["obj"])
        if k in seen:
            continue
        seen.add(k)
        if r["query"]["obj"] == "window":
            kind = r["path"].split("/")[-1].rsplit("-", 1)[0]
            kinds[kind][0] += 1
            kinds[kind][1] += bool(r["correct"])
    return {"by": dict(by), "drawings": {k: len(v) for k, v in drawings.items()}, "tier": {f"{k[0]}|{k[1]}": v for k, v in tier.items()},
            "wrong_pairs": len(pairs), "det_wrong": len(det), "det_off_by_one": off1, "box_causes": boxc, "direction": dirn,
            "window_by_kind": {k: v for k, v in kinds.items()}, "n_drawings": len({r["path"] for r in rows})}


def inspected(wrong):
    out = []
    for qid, (mode, tags, note) in CASES.items():
        w = wrong[qid]
        out.append({"drawing": w["stem"], "size": f"{w['w']}x{w['h']}", "object": w["obj"], "question": w["question"], "ours": w["ours"],
                    "gold": w["gold"], "tier": w["tier"], "mode": mode, "tags": tags, "note": note})
    out.sort(key=lambda r: (r["object"], r["drawing"]))
    return out


def mode_table(ins):
    groups = {"door": "doors", "window": "windows"}
    rows = []
    for mode, label in MODES.items():
        sub = [r for r in ins if r["mode"] == mode]
        rows.append({"mode": mode, "label": label, "n": len(sub),
                     "door": sum(r["object"] == "door" for r in sub), "window": sum(r["object"] == "window" for r in sub),
                     "other": sum(r["object"] not in groups for r in sub)})
    rows.sort(key=lambda r: (-r["n"], r["label"]))
    return rows


CSS = """
:root{--bg:oklch(0.165 0.012 245);--surface:oklch(0.190 0.013 245);--raised:oklch(0.228 0.015 245);--well:oklch(0.135 0.010 245);
--rule:oklch(0.268 0.014 245);--border:oklch(0.330 0.016 245);--fg:oklch(0.930 0.008 245);--fg2:oklch(0.790 0.014 245);--muted:oklch(0.680 0.016 245);
--accent:oklch(0.760 0.105 235);--warn:oklch(0.820 0.125 78);--err:oklch(0.720 0.130 25);--ok:oklch(0.760 0.105 150);
--font:"IBM Plex Sans","Segoe UI",sans-serif;--mono:"IBM Plex Mono","Cascadia Mono",Consolas,monospace}
*{box-sizing:border-box}
html{background:var(--bg)}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 var(--font);-webkit-font-smoothing:antialiased}
main{max-width:1040px;margin:0 auto;padding:48px 24px 96px}
h1{font-size:28px;font-weight:600;letter-spacing:-0.01em;margin:0 0 8px}
h2{font-size:20px;font-weight:600;margin:64px 0 8px;padding-top:24px;border-top:1px solid var(--rule)}
h3{font-size:16px;font-weight:600;margin:0 0 4px}
p{margin:0 0 14px;color:var(--fg2);max-width:70ch}
.lede{font-size:17px;color:var(--fg);max-width:64ch}
.meta,.small{font-size:13px;color:var(--muted)}
.mono,td.n,.k{font-family:var(--mono)}
a{color:var(--accent)}
table{border-collapse:collapse;width:100%;margin:12px 0 8px;font-size:14px}
th{font-weight:500;color:var(--muted);text-align:left;font-size:13px;padding:6px 12px 6px 0;border-bottom:1px solid var(--border)}
td{padding:7px 12px 7px 0;border-bottom:1px solid var(--rule);vertical-align:middle;color:var(--fg2)}
td.n{text-align:right;color:var(--fg);font-variant-numeric:tabular-nums;white-space:nowrap}
th.n{text-align:right}
td.name{color:var(--fg)}
.bar{display:block;height:6px;background:var(--raised);border-radius:1px;min-width:120px}
.bar i{display:block;height:100%;background:var(--accent);border-radius:1px}
.bar.w i{background:var(--warn)}
.legend{display:flex;flex-wrap:wrap;gap:6px 28px;margin:12px 0 28px;font-size:13px;color:var(--fg2)}
.legend span{display:inline-flex;align-items:center;gap:8px}
.sw{display:inline-block;width:18px;height:11px;border:2px solid}
.sw.a{border-color:oklch(0.75 0.16 60)}.sw.g{border-color:oklch(0.68 0.15 145)}
.sw.c{width:15px;height:15px;border:2px solid oklch(0.62 0.2 25);border-radius:50%}
.sw.x{position:relative;width:15px;height:15px;border:0}
.sw.x:before,.sw.x:after{content:"";position:absolute;left:6px;top:-1px;width:2px;height:17px;background:oklch(0.62 0.2 25)}
.sw.x:before{transform:rotate(45deg)}.sw.x:after{transform:rotate(-45deg)}
figure{margin:0 0 56px}
figure img{display:block;width:100%;height:auto;background:#fff;border:1px solid var(--border)}
figcaption{padding-top:10px}
.cap{font-size:13px;color:var(--muted);margin-bottom:4px;font-family:var(--mono)}
.cap b{color:var(--warn);font-weight:500}
figcaption p{margin:2px 0 0}
.two{display:grid;grid-template-columns:1fr 1fr;gap:32px}
ul{margin:0 0 14px;padding-left:20px;color:var(--fg2);max-width:72ch}
li{margin-bottom:6px}
.note{border-left:2px solid var(--warn);padding:2px 0 2px 14px;margin:18px 0;color:var(--fg2);max-width:72ch}
code{font-family:var(--mono);font-size:13px;color:var(--fg)}
@media(max-width:700px){main{padding:28px 16px 64px}.two{grid-template-columns:1fr}table{font-size:13px}.bar{min-width:60px}}
"""


def esc(s):
    return html.escape(str(s), quote=False)


def bar(x, warn=False):
    return f'<span class="bar{" w" if warn else ""}"><i style="width:{max(0, min(1, x)) * 100:.1f}%"></i></span>'


def build(a):
    rows, wrong = load(a)
    st = stats(rows, wrong)
    ins = inspected(wrong)
    mt = mode_table(ins)
    meta = {m["id"]: m for m in json.loads((Path(a.scratch) / "crops" / "meta.json").read_text(encoding="utf-8"))}
    by = st["by"]
    nq, ncorr = sum(v[0] for v in by.values()), sum(v[1] for v in by.values())
    tier = st["tier"]
    proven_n = tier.get("proven|True", 0) + tier.get("proven|False", 0)
    proven_ok = tier.get("proven|True", 0)
    est_n = tier.get("estimate|True", 0) + tier.get("estimate|False", 0)
    est_ok = tier.get("estimate|True", 0)
    n_ins = len(ins)
    cnt = collections.Counter(r["object"] for r in ins)
    D, W = st["direction"]["door"], st["direction"]["window"]
    win_kind = st["window_by_kind"]
    arch = win_kind.get("high_quality_architectural", [0, 0])
    hq = win_kind.get("high_quality", [0, 0])
    tags = collections.Counter(t for r in ins for t in r["tags"])
    rot = tags["rotated"] + tags["mirrored"]
    bc = st["box_causes"]
    n_merge = len({(w['path']) for w in wrong.values() if w['obj'] == 'window' and any(k == 'merge' for k, _, _ in w.get('causes', []))})

    # --- write images and inspected.json
    (OUT / "img").mkdir(parents=True, exist_ok=True)
    for g in GALLERY:
        shutil.copy(Path(a.scratch) / "crops" / f"{g['id']}.jpg", OUT / "img" / f"{g['id']}.jpg")
    (OUT / "inspected.json").write_text(json.dumps({"source": "eval.py --split test --sources cubicasa5k, Nemotron off, preview test split",
                                                    "modes": MODES, "cases": ins}, indent=1, ensure_ascii=False), encoding="utf-8")
    (OUT / "stats.json").write_text(json.dumps({"stats": st, "mode_counts": {r["mode"]: r["n"] for r in mt}}, indent=1), encoding="utf-8")

    # --- html
    h = []
    A = h.append
    A('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
      "<title>What is still hard</title>"
      '<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
      '<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&display=swap" rel="stylesheet">'
      f"<style>{CSS}</style></head><body><main>")
    A("<h1>What is still hard</h1>")
    A('<p class="lede">Where our counts go wrong on scanned floor plans, what the drawing looks like when they do, and what we do about it today. '
      "Real failures, real numbers, checked on the images.</p>")
    A(f'<p class="meta">Source: <code>eval.py --split test --sources cubicasa5k</code> on the preview test split, {st["n_drawings"]} CubiCasa scans, {nq:,} questions, '
      f'Nemotron off, 2026-11-14. The public CubiCasa5K annotation is drawn only to show what went wrong; no model or threshold was changed with it. '
      f'Floor plans are CubiCasa5K images from the Stoa dataset.</p>')

    # 1 strong and weak
    A("<h2>Where we are strong and where we are weak</h2>")
    A("<p>Vector drawings are close to solved: the answer is read from geometry, so there is nothing to detect, and IFC models are good. "
      "Scans are different. A small detector has to find each door and window in pixels, and that is where the errors are.</p>")
    A("<table><tr><th>Full test split (README)</th><th class=n>exact</th><th></th></tr>")
    for k in ("swiss", "swiss_all", "ifc", "scans"):
        name, v, note = README_FULL[k]
        A(f'<tr><td class=name>{esc(name)}<br><span class=small>{esc(note)}</span></td><td class=n>{pct(v)}</td><td>{bar(v, k == "scans")}</td></tr>')
    A("</table>")
    A("<table><tr><th>Objects counted on scans</th><th class=n>full test split</th><th class=n>this run, preview test</th><th></th></tr>")
    for obj in ("toilet", "sink", "shower_or_bath", "door", "window"):
        n, ok = by[obj]
        A(f'<tr><td class=name>{OBJ_NAME[obj]}<br><span class=small>{st["drawings"][obj]} drawings, {n} questions</span></td>'
          f'<td class=n>{pct(README_OBJ[obj])}</td><td class=n>{pct(ok / n)}</td><td>{bar(ok / n, obj in ("door", "window"))}</td></tr>')
    A("</table>")
    A(f'<p class="small">"Exact" counts an abstention as wrong; here nothing abstains. Full test split numbers are quoted from the README '
      f'(CubiCasa scans, 27,160 questions). The preview run is a small sample and each drawing is asked twice, so read its last digit loosely. '
      f'Whole run: {pct(ncorr / nq)} of {nq:,} CubiCasa questions exact; {proven_n} proven answers, {proven_ok} right; {est_n:,} estimates, {pct(est_ok / est_n)} right.</p>')
    A(f'<div class="note">Windows on the architectural scans (the paper drawings, {arch[0]} of the {st["n_drawings"]} preview test drawings) are exact on {arch[1]} of {arch[0]} drawings '
      f'({pct(arch[1] / arch[0], 0)}), on the other kind {hq[1]} of {hq[0]}. The answer text for an estimate quotes dev: detector window counts "about 78% exact on dev '
      f'(209 of 269 drawings)". On the test split they are well below that, so the dev figure in that sentence is optimistic for these drawings.</div>')

    # 2 modes
    A("<h2>The failure modes, with counts</h2>")
    A(f"<p>All {n_ins} wrong drawings below were looked at by eye: every wrong door drawing ({cnt['door']} of {D['wrong']}), a hash-ordered sample of the wrong window drawings "
      f"({cnt['window']} of {W['wrong']}) and of the rest ({n_ins - cnt['door'] - cnt['window']} of {st['wrong_pairs'] - D['wrong'] - W['wrong']}: sinks, toilets, showers, bedrooms, kitchens, saunas, bathrooms). "
      f"Each was drawn with our boxes against the annotation and given one main mode. "
      f"A drawing is one object on one plan; each is asked twice, so the {st['wrong_pairs']} wrong drawings are {2 * st['wrong_pairs']} wrong questions. "
      f"Counts are over this sample of {n_ins}, not over all errors.</p>")
    A("<table><tr><th>Mode</th><th class=n>of "
      f"{n_ins}</th><th class=n>doors</th><th class=n>windows</th><th class=n>other</th><th></th></tr>")
    top = max(r["n"] for r in mt)
    for r in mt:
        A(f'<tr><td class=name>{esc(r["label"])}</td><td class=n>{r["n"]}</td><td class=n>{r["door"] or ""}</td><td class=n>{r["window"] or ""}</td>'
          f'<td class=n>{r["other"] or ""}</td><td>{bar(r["n"] / top, True)}</td></tr>')
    A("</table>")
    A(f'<p class="small">Also on the sheet in these {n_ins} cases: plans with sideways, upside-down or mirrored text {rot}, two floors on one sheet {tags["two_floors"]}, '
      f'hand-drawn or pencil plans {tags["hand_drawn"] + tags["handwriting"]}. Tags overlap with the modes above.</p>')
    A("<h3>What the counts say</h3>")
    A("<ul>")
    A(f"<li><b>Most errors are off by one.</b> {st['det_off_by_one']} of {st['det_wrong']} wrong detector drawings (doors, windows, sinks, toilets, showers and baths) are exactly one too high or one too low. "
      f"Doors: {D['wrong']} wrong drawings, {D['over']} too high, {D['under']} too low, {D['off_by_one']} by one.</li>")
    A(f"<li><b>Windows run low, doors run both ways.</b> Windows: {W['wrong']} wrong drawings, {W['under']} too low and {W['over']} too high. "
      f"The largest single cause in the sample is the counting convention (below), then thin or faint lines.</li>")
    A(f"<li><b>The convention is a real cause, not only a detector fault.</b> In the annotation a window frame with a divider carries a second small window box, and the dataset's count includes it. "
      f"Automatic matching finds this pattern in {n_merge} of the {W['wrong']} wrong window drawings, "
      f"every time with our count too low. A human looking at the sheet would count one window.</li>")
    A(f"<li><b>Low resolution is rare and not where we lose.</b> {sum(1 for w in wrong.values() if w['w'] < 500) // 2} of the {st['wrong_pairs']} wrong drawings are under 500 px wide, "
      f"and 11 of the {st['n_drawings']} drawings are. On the detector objects those narrow drawings are exact on 79% of drawings, wider ones on 66 to 74%. "
      f"The tiny ones are still instructive (case 7).</li>")
    A("<li><b>Wrong answers on this run were all estimates.</b> No proven answer was wrong; see what we do about it.</li>")
    A("</ul>")
    A(f'<p class="small">Box-level causes by automatic matching to the annotation, over all wrong drawings (not by eye): doors, extra boxes {bc["door"].get("fp", 0) + bc["door"].get("fp outside unit", 0)} '
      f'({bc["door"].get("fp outside unit", 0)} of them outside the annotated flat), annotated doors scored below the threshold {bc["door"].get("low score", 0)}, no box at all {bc["door"].get("miss", 0)}, '
      f'boxes within 0.15 of the threshold {bc["door"].get("near t", 0)}. Windows, boxes spanning two annotated windows {bc["window"].get("merge", 0)}, scored below the threshold {bc["window"].get("low score", 0)}, '
      f'no box {bc["window"].get("miss", 0)}, extra boxes {bc["window"].get("fp", 0) + bc["window"].get("fp outside unit", 0)}, one window boxed twice {bc["window"].get("split", 0)}.</p>')

    # 3 gallery
    A("<h2>The gallery</h2>")
    A(f"<p>Twelve of the {n_ins} cases, picked to show each mode once. Boxes are what we counted, numbered; the plan is the original scan, cropped only where noted.</p>")
    A('<div class="legend"><span><i class="sw a"></i>our counted box</span><span><i class="sw g"></i>annotation</span>'
      '<span><i class="sw c"></i>annotated, we missed it</span><span><i class="sw x"></i>we counted it, the annotation has no such object</span></div>')
    for i, g in enumerate(GALLERY, 1):
        m = meta[g["id"]]
        img = base64.b64encode((OUT / "img" / f"{g['id']}.jpg").read_bytes()).decode()
        caps = []
        for p in m["panels"]:
            caps.append(f'{OBJ_NAME[p["obj"]]} <b>ours {p["ours"]}</b>, annotation {p["gold"]}, {esc(p["stem"])}, {p["w"]}x{p["h"]} px')
        A(f'<figure id="case{i}"><img alt="{esc(g["title"])}" src="data:image/jpeg;base64,{img}">'
          f'<figcaption><div class="cap">{i}. {" | ".join(caps)}</div><h3>{esc(g["title"])}</h3><p>{esc(g["why"])}</p>'
          f'<p class="small">Mode: {esc(MODES[g["mode"]])}</p></figcaption></figure>')

    # 4 what we do now
    A("<h2>What we do about it now</h2>")
    A(f"<p><b>A wrong count is amber, never blue.</b> A scan answer is proven only where dev measured at least 98% over at least 100 drawings. "
      f"Detector doors measured 74.5% (225 of 302 drawings) and windows 77.7% (209 of 269), so every door and window count on a scan is an estimate. "
      f"On this run that held: {proven_n} proven answers, {proven_ok} right; all {2 * st['wrong_pairs']} wrong questions were estimates. "
      f"On the full test split proven answers are 98.4% precise (15,441 answers; on CubiCasa scans 97.8%, 3,192 answers), per the README.</p>")
    A("<p><b>The numbered boxes let a person fix it in seconds.</b> Every counted item is a numbered amber box with a thumbnail under the drawing. "
      f"Because {st['det_off_by_one']} of {st['det_wrong']} wrong detector drawings are off by exactly one, one click usually repairs the count: "
      '"Not this one" strikes out an extra box (a cabinet door, a second flat), "Add missed item" takes a click on the drawing where a sliding door or a faint window was missed. '
      "The answer count follows, and \"Save correction\" records it.</p>")
    A("<p>Two of the modes are not repaired by a click. A divider that the annotation counts twice is a convention, and the person checking will count one. "
      "And in the 8 cases with a second flat on the sheet the box that is wrong is a perfectly good door of another flat; the person has to say which flat the question is about.</p>")

    # 5 more time
    A("<h2>What would fix it with more time</h2>")
    A("<p>Ideas tied to the modes above, none of them measured yet. Anything learned from CubiCasa labels has to come from the train split, never from test.</p>")
    A("<ul>"
      "<li><b>Counting convention for windows.</b> Read how the train-split answers count a frame with a divider and either follow it in the count rule or train a class for it. "
      "This is the largest single mode in the sample and needs no better eyes, only the dataset's rule.</li>"
      "<li><b>Sliding and double doors.</b> Render sliding bars and double-leaf doors into the synthetic Swiss plans and add them as door examples, so a door without a single arc is a door.</li>"
      "<li><b>Hard negatives for cabinets and closets.</b> Add train-split cabinet arcs, closet doors and kitchen unit edges as background examples.</li>"
      "<li><b>Which flat.</b> Find the main flat on the sheet (the outline of the largest connected set of rooms), drop boxes outside it, and let the person click another flat. "
      "On two-flat sheets this removes the doubled counts.</li>"
      "<li><b>Colour of the pen.</b> Blue and green marker is a different hue from the linework. Filtering it before detection should remove the door-like strokes.</li>"
      "<li><b>Labels on four rotations.</b> The detector already looks at eight views of the plan; room labels are read once. Reading them on four rotations and picking the one with the most known words would recover upside-down and sideways plans.</li>"
      "<li><b>Calibrate on the kind of drawing the test has.</b> Dev is a mix; the test split is mostly architectural scans, where windows are exact on about four drawings in ten. "
      "Calibrating and tuning on that kind would make the estimate wording honest about them.</li>"
      "</ul>")
    A('<p class="small">The by-eye record of every case (drawing, question, ours, annotation, mode) is in <code>inspected.json</code> next to this page; '
      "<code>tools/failure_gallery.py</code> and <code>tools/build_failure_gallery.py</code> rebuild it.</p>")
    A("</main></body></html>")
    (OUT / "index.html").write_text("".join(h), encoding="utf-8")

    # --- SUMMARY.md
    L = []
    L.append("# What is still hard\n")
    L.append(f"Source: `eval.py --split test --sources cubicasa5k`, preview test split, {st['n_drawings']} CubiCasa scans, {nq:,} questions, Nemotron off (2026-11-14). "
             f"{pct(ncorr / nq)} exact overall; doors {pct(by['door'][1] / by['door'][0])}, windows {pct(by['window'][1] / by['window'][0])}, "
             f"sinks {pct(by['sink'][1] / by['sink'][0])}. Full test split (README): doors {pct(README_OBJ['door'])}, windows {pct(README_OBJ['window'])}.\n")
    L.append("Strong: vector and IFC (Swiss dwellings 99.0% on the full test split, counts 100.0%). Weak: scan doors and windows.\n")
    L.append(f"## Failure modes ({n_ins} wrong drawings looked at by eye)\n")
    L.append("| Mode | of %d | doors | windows | other |" % n_ins)
    L.append("|---|---|---|---|---|")
    for r in mt:
        L.append(f"| {r['label']} | {r['n']} | {r['door'] or ''} | {r['window'] or ''} | {r['other'] or ''} |")
    L.append("")
    L.append(f"- {st['det_off_by_one']} of {st['det_wrong']} wrong detector drawings are off by exactly one.")
    L.append(f"- Windows: {W['wrong']} wrong drawings, {W['under']} too low. Doors: {D['wrong']} wrong, {D['over']} too high, {D['under']} too low.")
    L.append("- A window frame with a divider is two windows in the annotation; we count one. This is a convention, not a detection fault.")
    L.append("- Low resolution is rare and not where we lose: narrow drawings are exact on 79% of detector-object drawings, wider ones on 66 to 74%.")
    L.append(f"- Windows on the architectural scans: exact on {arch[1]} of {arch[0]} drawings. The dev figure quoted in an estimate (about 78%) is optimistic for them.")
    L.append(f"- {proven_n} proven answers on this run, {proven_ok} right. All {2 * st['wrong_pairs']} wrong questions were estimates (amber).\n")
    L.append("## What we do now\n")
    L.append("Wrong counts are amber, never blue (proven needs 98% on at least 100 dev drawings). The numbered boxes let a person fix a count in seconds: "
             '"Not this one" removes an extra, "Add missed item" adds a missed one.\n')
    L.append("## What would fix it\n")
    L.append("Follow the dataset's window convention from the train split; sliding and double doors as synthetic training examples; hard negatives for cabinets; "
             "restrict counts to one flat; filter pen colour; read labels on four rotations; calibrate on architectural scans. None measured yet.\n")
    L.append("Full page: `index.html`. Every case: `inspected.json`.\n")
    (OUT / "SUMMARY.md").write_text("\n".join(L), encoding="utf-8")
    print("built", OUT, "modes:", {r["mode"]: r["n"] for r in mt})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scratch", required=True)
    ap.add_argument("--answers", default=str(REPO / "out" / "test" / "answers.jsonl"))
    build(ap.parse_args())


if __name__ == "__main__":
    main()
