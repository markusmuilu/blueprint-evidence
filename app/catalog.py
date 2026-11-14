"""Dataset browser: every drawing that has questions, grouped by source, with its questions.

Built once from questions.jsonl (about 127k lines on the full set, under two seconds). The data
is parsed only; nothing in it is executed.
"""
from __future__ import annotations

import json
import os
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

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]

    def summary(self) -> dict:
        types = sorted({q[3] for q in self.questions if q[3]})
        return {"path": self.path, "name": self.name, "source": self.source, "ext": self.ext,
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
            if terms and not all(t in d.path.lower() for t in terms):
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
