"""Dataset browser: every drawing that has questions, grouped by source, with its questions.

Built once from questions.jsonl (about 127k lines on the full set, under two seconds). The data
is parsed only; nothing in it is executed.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from bpqa.data import DEFAULT_DATA, split_of

REPO = Path(__file__).resolve().parent.parent
FULL_DATA = REPO.parent / "data" / "stoa-full" / "junction-dataset"

SOURCE_NAMES = {
    "swiss_dwellings": "Swiss dwellings",
    "cubicasa5k": "CubiCasa scans",
    "ifc_models": "IFC models",
    "ifc_bench": "IFC-Bench",
    "opening_design": "OpeningDesign sets",
}


# ---------------------------------------------------------------- readable drawing names
# The library shows a readable name first and the file name under it ("Molio office, 3rd floor" over
# bsc-molio_molio-with-uris_plan-06-3-sal.pdf). A small table for the projects in the dataset, then a fallback that
# tidies the file stem, so a new file still gets a sensible name. Building types are only stated where known.
PROJECTS = {
    "esplanades": "Esplanades apartment block", "molio": "Molio office", "fzk-haus": "FZK-Haus",
    "institute": "KIT institute building", "smiley-west": "Smiley West", "digitalhub": "Digital Hub (RWTH Aachen)",
    "escape-route": "TU Wien escape route model", "gymzaal-amersfoort": "Gymzaal Amersfoort (sports hall)",
    "barlad": "Barlad building", "frigento": "Palazzo Testa Cipriano, Frigento", "gdynia": "Ruchu Oporu 18, Gdynia",
    "vitoria": "ALD26, Vitoria", "warmond": "Warmond building", "clinic": "Clinic", "duplex": "Duplex house",
    "chesapeake-bay-bridge-tunnel": "Chesapeake Bay Bridge-Tunnel",
}
BENCH = {"fzk_house": "FZK-Haus", "molio": "Molio office", "smiley_west": "Smiley West", "digital_hub": "Digital Hub",
         "dental_clinic": "Dental clinic", "4351": "Model 4351", "ac20": "AC20 institute building", "hitos": "HiTOS",
         "sixty5": "Sixty5", "duplex": "Duplex house", "ettenheim_gis_city": "Ettenheim city (GIS)"}
DISCIPLINES = {"arc": "architecture", "architectural": "architecture", "a": "architecture", "str": "structure",
               "structural": "structure", "mep": "MEP", "m": "mechanical", "hvac": "HVAC", "electrical": "electrical",
               "plumbing": "plumbing", "heating": "heating", "ventilation": "ventilation", "san": "sanitary"}
# one sheet label (the part after "_plan-NN-") in plain English; numbered labels are handled in floor_name
FLOOR_WORDS = {
    "erdgeschoss": "ground floor", "eg": "ground floor", "grundplan": "ground floor", "begane-grond": "ground floor",
    "piano-terra": "ground floor", "ground-level": "ground floor", "entrance": "entrance level",
    "dachgeschoss": "attic floor", "dg": "attic floor", "attic": "attic", "keller": "basement", "kg": "basement",
    "klder": "basement", "kaelder": "basement", "basement": "basement", "solaio-interrato": "basement slab",
    "fundament": "foundation", "foundation": "foundation", "eerste-verdieping": "1st floor", "da": "roof",
    "cta": "CTA level",
}


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _tidy(stem: str) -> str:
    t = re.sub(r"[_\-]+", " ", stem).strip()
    return t[:1].upper() + t[1:] if t else stem


def _nth_floor(n: int) -> str:
    return "ground floor" if n == 0 else f"{_ordinal(n)} floor"


def floor_name(label: str) -> str:
    """'1-korrus' -> '1st floor', '3-sal' -> '3rd floor', 'erdgeschoss' -> 'ground floor', 'ug2' -> 'basement 2'."""
    lab = label.lower().strip("-")
    m = re.fullmatch(r"(\d+)-(korrus|sal|obergeschoss|og|level)", lab)
    if m:
        return _nth_floor(int(m.group(1)))
    m = re.fullmatch(r"(?:og|level)-?(\d+)", lab)
    if m:
        return _nth_floor(int(m.group(1)))
    m = re.fullmatch(r"(?:ug|b)0?(\d+)(?:-okrd)?", lab)
    if m:
        return f"basement {int(m.group(1))}"
    m = re.fullmatch(r"e0?(\d+)(?:-okrd)?", lab)
    if m:
        return _nth_floor(int(m.group(1)))
    m = re.fullmatch(r"dg(\d+)", lab)
    if m:
        return f"attic floor {m.group(1)}"
    m = re.fullmatch(r"p(\d+)", lab)
    if m:
        return "ground floor" if m.group(1) == "0" else f"floor P{m.group(1)}"
    m = re.fullmatch(r"intermediate-level-(\d+)", lab)
    if m:
        return f"intermediate level {m.group(1)}"
    m = re.fullmatch(r"\d+-(.+)", lab)
    if m and m.group(1) in FLOOR_WORDS:
        return FLOOR_WORDS[m.group(1)]
    if lab in FLOOR_WORDS:
        return FLOOR_WORDS[lab]
    if re.fullmatch(r"-?\d+", lab):
        return f"level {lab}"
    return _tidy(lab).lower()


def _ifc_model_name(stem: str) -> str:
    org_proj, _, rest = stem.partition("_")
    proj = org_proj.split("-", 1)[1] if "-" in org_proj else org_proj
    base = PROJECTS.get(proj) or next((v for k, v in PROJECTS.items() if proj.startswith(k)), None) or _tidy(proj)
    if "_plan-" in stem:
        label = re.sub(r"^\d+-", "", stem.split("_plan-", 1)[1], count=1)    # drop the sheet number before the storey
        variant = ""
        if rest.split("_")[0].endswith("-before"):
            variant = " (before)"
        elif rest.split("_")[0].endswith("-project"):
            variant = " (project)"
        return f"{base}{variant}, {floor_name(label)}"
    words = [w for w in re.split(r"[-_]", rest.lower()) if w and not re.fullmatch(r"\d{6,}|v\d+|\d+x\d+", w)]
    disc = next((DISCIPLINES[w] for w in words if w in DISCIPLINES), None)
    return f"{base}, {disc} (IFC model)" if disc else f"{base} (IFC model)"


def friendly_name(path: str) -> str:
    """A readable name for a dataset drawing, from its source folder and file stem; the file name stays the key."""
    parts = path.split("/")
    source = parts[1] if len(parts) > 2 else ""
    stem = parts[-1].rsplit(".", 1)[0]
    if source == "swiss_dwellings":
        m = re.fullmatch(r"site(\d+)_bldg(\d+)_floor(\d+)", stem)
        if m:
            return f"Swiss apartment floor, site {m.group(1)}"
    if source == "cubicasa5k":
        m = re.search(r"(\d+)$", stem)
        if m:
            return f"Scanned flat plan {m.group(1)}"
    if source == "ifc_models":
        return _ifc_model_name(stem)
    if source == "ifc_bench":
        bits = stem.split("_")
        disc = DISCIPLINES.get(bits[-1]) if len(bits) > 1 else None
        proj = "_".join(bits[:-1]) if disc else stem
        base = BENCH.get(proj) or _tidy(proj)
        return f"{base}, {disc} (IFC model)" if disc else f"{base} (IFC model)"
    if source == "opening_design" and len(parts) > 3:
        sheet = re.sub(r"\s+-\s+", " ", stem, count=1)
        code, _, title = sheet.partition(" ")
        if title.isupper():
            title = title.capitalize()
        return f"{_tidy(parts[2])}, sheet {code} {title}".rstrip()
    return _tidy(stem)


def friendly_names(paths) -> dict[str, str]:
    """friendly_name for every path. Where two drawings of one source would read the same, the plan's sheet number
    or the rest of the file stem is added (the file name is shown under the name in any case)."""
    out = {p: friendly_name(p) for p in paths}
    seen: dict[tuple[str, str], list[str]] = {}
    for p, n in out.items():
        seen.setdefault((p.split("/")[1] if p.count("/") > 1 else "", n), []).append(p)
    for (_, n), ps in seen.items():
        if len(ps) < 2:
            continue
        for p in ps:
            stem = p.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            sheet = re.search(r"_plan-(\d+)-", stem)
            rest = stem.split("_", 1)[-1]
            out[p] = f"{n}, sheet {int(sheet.group(1))}" if sheet else f"{n}, {_tidy(rest).lower()}"
    return out

def find_data_root() -> Path | None:
    for cand in (os.environ.get("STOA_DATA"), FULL_DATA, DEFAULT_DATA):
        if cand and (Path(cand) / "questions.jsonl").exists():
            return Path(cand)
    return None


@dataclass
class Drawing:
    path: str
    source: str
    ext: str
    split: str
    questions: list = field(default_factory=list)   # (question, answer, unit, type)
    title: str = ""                                  # readable name (friendly_names); the file name stays in `name`

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]

    def summary(self) -> dict:
        types = sorted({q[3] for q in self.questions if q[3]})
        return {"path": self.path, "name": self.name, "title": self.title or self.name, "source": self.source, "ext": self.ext,
                "split": self.split, "n_questions": len(self.questions), "types": types}


class Catalog:
    def __init__(self, root: Path | None):
        self.root = root
        self.drawings: dict[str, Drawing] = {}
        self.by_question: dict[tuple[str, str], tuple] = {}
        if root is None:
            return
        with open(root / "questions.jsonl", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                r = json.loads(line)
                p = r["path"]
                d = self.drawings.get(p)
                if d is None:
                    parts = p.split("/")
                    d = self.drawings[p] = Drawing(p, parts[1] if len(parts) > 1 else "?",
                                                   p.rsplit(".", 1)[-1].lower(), split_of(p))
                rec = (r["question"], r.get("answer"), r.get("unit") or "", r.get("type") or "")
                d.questions.append(rec)
                self.by_question[(p, r["question"].strip())] = rec
        # stable, readable order: source, then name
        self._sorted = sorted(self.drawings.values(), key=lambda d: (d.source, d.path))
        for p, t in friendly_names(self.drawings).items():
            self.drawings[p].title = t

    def sources(self) -> list[dict]:
        counts: dict[str, list[int]] = {}
        for d in self._sorted if self.root else []:
            c = counts.setdefault(d.source, [0, 0])
            c[0] += 1
            c[1] += len(d.questions)
        return [{"id": s, "name": SOURCE_NAMES.get(s, s), "drawings": c[0], "questions": c[1]}
                for s, c in sorted(counts.items(), key=lambda kv: -kv[1][1])]

    def search(self, source: str = "", q: str = "", ext: str = "", offset: int = 0, limit: int = 60,
               not_source: str = "") -> dict:
        if not self.root:
            return {"total": 0, "items": []}
        terms = [t for t in q.lower().split() if t]
        hits = []
        for d in self._sorted:
            if source and d.source != source:
                continue
            if not_source and d.source == not_source:      # library groups: IFC models and their plans are one group
                continue
            if ext and d.ext != ext:
                continue
            hay = (d.path + " " + d.title).lower()          # search matches the file name and the readable name
            if terms and not all(t in hay for t in terms):
                continue
            hits.append(d)
        return {"total": len(hits), "items": [d.summary() for d in hits[offset:offset + limit]]}

    def detail(self, path: str) -> dict | None:
        d = self.drawings.get(path)
        if d is None:
            return None
        twins = []
        f = self.root / path
        for ext in ("pdf", "dxf", "png", "ifc", "svg"):
            sib = f.with_suffix("." + ext)
            if ext != d.ext and sib.exists():
                twins.append(ext)
        if d.source == "ifc_models" and "_plan-" in f.stem:
            model = f.parent / (f.stem.split("_plan-")[0] + ".ifc")
            if model.exists():
                twins.append("ifc model")
        out = d.summary()
        out["twins"] = twins
        out["questions"] = [{"question": q, "answer": a, "unit": u, "type": t} for q, a, u, t in d.questions[:80]]
        return out

    def gold(self, path: str, question: str):
        rec = self.by_question.get((path, question.strip()))
        if rec is None:
            return None
        return {"answer": rec[1], "unit": rec[2], "type": rec[3], "split": split_of(path)}
