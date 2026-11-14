"""Build the small, credited demo set that the Modal deployment serves.

    python deploy/build_demo_set.py [--stoa ../data/stoa-full/junction-dataset] [--unseen ../data/unseen]
                                    [--out deploy/demo_data]

Picks about 25 drawings (Swiss PDF+DXF pairs, IFC models with two plans each, CubiCasa PNGs of the three
styles, two unseen public drawings), copies them into a dataset-shaped folder (`questions.jsonl`,
`data/<source>/...`), writes one trimmed `LICENSE.md` per source folder (the router reads it to skip
overlays on NoDerivatives files) and `credits.json` (drawing path -> licence and credit) for the credit
bar the deployment adds to the app. The selection is fixed by name, so the build is reproducible.

Stoa's dataset is licensed for the Junction challenge, non-commercial: the output is git-ignored and
only ever shipped inside the private Modal image. Files are copied and parsed, never executed.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent

# ------------------------------------------------------------------ the selection (fixed, by name)

SWISS = [  # PDF + DXF twins; the first two are the README examples (tiling takeoff, window count)
    "site3387_bldg5238_floor23498",
    "site1000_bldg2082_floor9385",
]
SWISS_EXTRA = 5            # more Swiss drawings, picked deterministically from the test split
IFC = {  # model -> plan PDFs (the model .ifc is copied with them so the IFC route can read it)
    "kit-fzk-haus_ac20-fzk-haus": ["plan-01-erdgeschoss", "plan-02-dachgeschoss"],
    "bsc-esplanades_1807-ep-ar-v18": ["plan-01-1-korrus", "plan-02-2-korrus"],
    "bsc-molio_molio-with-uris": ["plan-03-grundplan", "plan-04-1-sal"],
}
CUBICASA = {  # two per drawing style; the first of each is a README/search example where one exists
    "high_quality_architectural": ["high_quality_architectural-10372"],
    "high_quality": [],
    "colorful": ["colorful-11960"],
}
CUBICASA_PER_STYLE = 2
UNSEEN = [  # (folder, files); public drawings never seen in development
    ("kjdraw_architecture_floor_plan", ["architecture-floor-plan.pdf", "architecture-floor-plan.dxf"]),
    ("wm_tyneside_flat", ["Tyneside flat floorplan.png"]),
]


def split_of(path: str) -> str:
    import sys
    sys.path.insert(0, str(REPO))
    from bpqa.data import split_of as s
    return s(path)


def load_questions(root: Path) -> dict[str, list[dict]]:
    by_path: dict[str, list[dict]] = {}
    with open(root / "questions.jsonl", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                r = json.loads(line)
                by_path.setdefault(r["path"], []).append(r)
    return by_path


def licence_table(md: Path) -> tuple[str, dict[str, list[str]], str]:
    """(text before the Files table, file -> row cells, licence links section) of a source LICENSE.md."""
    text = md.read_text(encoding="utf-8", errors="replace")
    head, rows, links = text, {}, ""
    if "\n## Files" in text:
        head, rest = text.split("\n## Files", 1)
        if "\n## Licence links" in rest:
            rest, links = rest.split("\n## Licence links", 1)
            links = "## Licence links" + links
        for line in rest.splitlines():
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) >= 5 and "." in cells[0] and not cells[0].startswith("-"):
                rows[cells[0]] = cells
    return head.rstrip() + "\n", rows, links


def folder_credit(md: Path) -> tuple[str, str]:
    """(licence, credit) of a source whose LICENSE.md has one licence for all files."""
    text = md.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"All files: ([^.]+)\. Credit: (.+)", text)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    lic = re.search(r"licensed under\s+(CC [A-Z\-]+ [\d.]+)", text.replace("\n", " "))
    cred = re.search(r"Credit: (.+?)(?:\n\n|$)", text, re.S)
    return (lic.group(1) if lic else "see LICENSE.md"), (" ".join(cred.group(1).split()) if cred else "")


def source_md(md: Path) -> dict:
    text = md.read_text(encoding="utf-8", errors="replace")
    get = lambda k: (re.search(rf"^- {k}: (.+)$", text, re.M) or [None, ""])[1].strip()
    return {"licence": get("Licence"), "author": get("Author"), "credit": get("Credit"),
            "url": get("Commons page") or get("Project page") or get("URL")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stoa", default=str(REPO.parent / "data" / "stoa-full" / "junction-dataset"))
    ap.add_argument("--unseen", default=str(REPO.parent / "data" / "unseen"))
    ap.add_argument("--out", default=str(HERE / "demo_data"))
    ap.add_argument("--results", default=str(REPO.parent / "stoa-wt-polish" / "out"),
                    help="out/ folder holding the eval runs shown in the Results view")
    ap.add_argument("--results-out", default=str(HERE / "demo_results"))
    args = ap.parse_args()
    stoa, unseen, out = Path(args.stoa), Path(args.unseen), Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    qs = load_questions(stoa)
    picked: list[str] = []          # dataset-relative files to copy
    questions: list[dict] = []
    credits: dict[str, dict] = {}

    def take(rel: str, credit: dict, ask: bool = True):
        picked.append(rel)
        credits[rel] = credit
        if ask:
            for r in qs.get(rel, []):
                questions.append({k: r.get(k) for k in ("path", "question", "answer", "unit", "type")})

    # Swiss dwellings: PDF + DXF twins
    sw_dir = stoa / "data" / "swiss_dwellings"
    sw_lic, sw_cred = folder_credit(sw_dir / "LICENSE.md")
    names = list(SWISS)
    pool = sorted({p.split("/")[-1].rsplit(".", 1)[0] for p in qs if "/swiss_dwellings/" in p})
    pool = [n for n in pool if n not in names and split_of(f"data/swiss_dwellings/{n}.pdf") == "test"
            and (sw_dir / f"{n}.dxf").exists()
            and any(r["type"] == "area" for r in qs.get(f"data/swiss_dwellings/{n}.pdf", []))]
    names += pool[:: max(1, len(pool) // SWISS_EXTRA)][:SWISS_EXTRA]
    for n in names:
        for ext in ("pdf", "dxf"):
            take(f"data/swiss_dwellings/{n}.{ext}", {"source": "Swiss Dwellings", "licence": sw_lic, "credit": sw_cred,
                                                     "url": "https://doi.org/10.5281/zenodo.7788422"})

    # IFC models with plans
    ifc_dir = stoa / "data" / "ifc_models"
    head, rows, links = licence_table(ifc_dir / "LICENSE.md")
    ifc_rows = []
    for model, plans in IFC.items():
        files = [f"{model}.ifc"] + [f"{model}_{p}.pdf" for p in plans]
        for fn in files:
            row = rows.get(fn)
            if row is None:
                raise SystemExit(f"{fn}: no row in ifc_models/LICENSE.md")
            if "ND" in row[2].upper().split("-"):
                raise SystemExit(f"{fn}: NoDerivatives ({row[2]}); not used in the demo set")
            ifc_rows.append(row)
            take(f"data/ifc_models/{fn}", {"source": "IFC models", "title": row[1], "licence": row[2],
                                           "credit": row[3], "url": row[4]}, ask=fn.endswith(".pdf"))

    # CubiCasa5K: three drawing styles
    cc_dir = stoa / "data" / "cubicasa5k"
    cc_lic, cc_cred = folder_credit(cc_dir / "LICENSE.md")
    cc_cred = cc_cred or ("Ahti Kalervo, Juha Ylioinas, Markus Häikiö, Antti Karhu, Juho Kannala. CubiCasa5K, "
                          "SCIA 2019. (c) 2019 CubiCasa")
    for style, fixed in CUBICASA.items():
        chosen = list(fixed)
        pool = sorted(p.split("/")[-1].rsplit(".", 1)[0] for p in qs
                      if p.startswith(f"data/cubicasa5k/{style}-") and p.endswith(".png") and split_of(p) == "test")
        for n in pool[7::97]:
            if len(chosen) >= CUBICASA_PER_STYLE:
                break
            if n not in chosen:
                chosen.append(n)
        for n in chosen:
            take(f"data/cubicasa5k/{n}.png", {"source": "CubiCasa5K", "licence": "CC BY-NC-SA 4.0 (non-commercial)",
                                             "credit": cc_cred, "url": "https://zenodo.org/records/2613548"})

    # Unseen public drawings
    unseen_q: dict[str, list[dict]] = {}
    with open(unseen / "questions.jsonl", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                r = json.loads(line)
                unseen_q.setdefault(r["path"], []).append(r)
    unseen_rows = []
    for folder, files in UNSEEN:
        src = source_md(unseen / folder / "SOURCE.md")
        for fn in files:
            rel = f"data/unseen/{folder}/{fn}"
            owner = re.search(r"\(([^)]+)\)", src["licence"])
            cred = {"source": "Unseen public drawing", "licence": src["licence"],
                    "credit": ", ".join(x for x in (src["author"], src["credit"]) if x)
                    or (owner.group(1) if owner else src["url"]), "url": src["url"]}
            if fn.endswith(".pdf") and folder.startswith("kjdraw"):
                cred["credit"] = (cred["credit"] + "; PDF rendered by the team from the DXF").lstrip("; ")
            picked.append(rel)
            credits[rel] = cred
            unseen_rows.append([fn, folder, src["licence"], cred["credit"], src["url"]])
            for r in unseen_q.get(f"{folder}/{fn}", []):
                questions.append({"path": rel, "question": r["question"], "answer": r.get("answer"),
                                  "unit": r.get("unit"), "type": r.get("type")})

    # copy files
    for rel in picked:
        if rel.startswith("data/unseen/"):
            src = unseen / rel[len("data/unseen/"):]
        else:
            src = stoa / rel
        dst = out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)

    # trimmed LICENSE.md per source folder
    for name in ("swiss_dwellings", "cubicasa5k"):
        shutil.copyfile(stoa / "data" / name / "LICENSE.md", out / "data" / name / "LICENSE.md")
    table = "| file | title | licence | credit | source |\n|---|---|---|---|---|\n"
    (out / "data" / "ifc_models" / "LICENSE.md").write_text(
        head + "\nTrimmed to the files in this demo set.\n\n## Files\n\n" + table
        + "".join("| " + " | ".join(r[:5]) + " |\n" for r in ifc_rows) + "\n" + links, encoding="utf-8")
    (out / "data" / "unseen" / "LICENSE.md").write_text(
        "# unseen\n\nPublic drawings collected for the unseen-drawing test (see UNSEEN.md in the repository), "
        "each with its own licence.\n\n## Files\n\n" + table
        + "".join("| " + " | ".join(r) + " |\n" for r in unseen_rows), encoding="utf-8")

    with open(out / "questions.jsonl", "w", encoding="utf-8") as f:
        for r in questions:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    (out / "credits.json").write_text(json.dumps(credits, ensure_ascii=False, indent=1), encoding="utf-8")
    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    drawings = {r["path"] for r in questions}
    print(f"{len(picked)} files, {len(drawings)} drawings with questions, {len(questions)} questions, "
          f"{size / 1e6:.1f} MB -> {out}")
    build_results(Path(args.results), Path(args.results_out))


RESULT_RUNS = ["test-junction-dataset", "test", "dev"]   # full set test, preview test, preview dev


def build_results(src: Path, dst: Path):
    """Eval outputs for the Results view (out/-shaped): the plain runs, the Nemotron baseline, the unseen run."""
    if dst.exists():
        shutil.rmtree(dst)
    n = 0
    for run in RESULT_RUNS:
        for fn in ("summary.json", "summary.txt", "answers.jsonl"):
            f = src / run / fn
            if f.exists():
                (dst / run).mkdir(parents=True, exist_ok=True)
                shutil.copyfile(f, dst / run / fn)
                n += 1
    for rel in ("baseline_nemotron.json", "unseen/results.jsonl"):
        f = src / rel
        if not f.exists() and rel.startswith("unseen/"):
            f = REPO / "unseen" / "results.jsonl"
        if f.exists():
            (dst / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(f, dst / rel)
            n += 1
    size = sum(p.stat().st_size for p in dst.rglob("*") if p.is_file()) if dst.exists() else 0
    print(f"{n} result files, {size / 1e6:.1f} MB -> {dst}")


if __name__ == "__main__":
    main()
