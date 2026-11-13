"""Dataset access: questions, data root, drawing ids and the dev/test split.

The data is never copied into the repo. Point at it with --data or the STOA_DATA
environment variable. Files are parsed, never executed.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_DATA = REPO.parent / "data" / "stoa-preview" / "junction-dataset-preview"
SPLIT_SEED = "junction2026-stoa"
TEST_FRACTION = 0.3


def data_root(arg: str | None = None) -> Path:
    root = Path(arg or os.environ.get("STOA_DATA") or DEFAULT_DATA)
    if not (root / "questions.jsonl").exists():
        raise SystemExit(f"questions.jsonl not found under {root}; pass --data or set STOA_DATA")
    return root


@dataclass
class Question:
    qid: int
    path: str          # dataset-relative, e.g. data/swiss_dwellings/x.pdf
    question: str
    answer: object = None
    unit: str = ""
    type: str = ""

    @property
    def source(self) -> str:
        return self.path.split("/")[1]

    @property
    def ext(self) -> str:
        return self.path.rsplit(".", 1)[-1].lower()


def load_questions(root: Path) -> list[Question]:
    out = []
    with open(root / "questions.jsonl", encoding="utf-8") as f:
        for i, line in enumerate(f):
            if line.strip():
                r = json.loads(line)
                out.append(Question(i, r["path"], r["question"], r.get("answer"), r.get("unit", ""), r.get("type", "")))
    return out


def drawing_id(path: str) -> str:
    """Group key so that every format and rendering of one drawing lands in the same split."""
    parts = path.split("/")
    src = parts[1]
    stem = parts[-1].rsplit(".", 1)[0]
    if src == "ifc_models":
        return f"{src}/{stem.split('_plan-')[0]}"   # plan PDFs/SVGs belong to their model
    if src == "opening_design":
        return f"{src}/{parts[2]}"                   # one project = one drawing set
    return f"{src}/{stem}"


def split_of(path: str) -> str:
    h = hashlib.sha1((SPLIT_SEED + drawing_id(path)).encode()).hexdigest()
    return "test" if int(h[:8], 16) / 0xFFFFFFFF < TEST_FRACTION else "dev"
