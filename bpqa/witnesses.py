"""Three witnesses: answer one question independently from every format of the same drawing.

A Swiss Dwellings plan exists as DXF, PDF and PNG; an IFC plan as the model and the PDF rendered from
it. Each format is read by its own route through the normal router, with the others hidden:

    DXF   the DXF layers (dxf-geometry / dxf-generic)
    IFC   the IFC model (ifc)
    PDF   the PDF alone, twins ignored (pdf-text, pdf-vector, pdf-generic)
    PNG   the raster image (OCR labels, symbol detector, panel analysis)

Nemotron is off for witnesses, so every witness is a local route at $0 in model calls and they share
no reading. Agreement between independent readings is a confidence signal; a disagreement points at
the format (and on the drawing, the items) to check.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from .data import Question
from .query import parse
from .router import Context, solve

AREA_TOL = 0.02          # areas, lengths and volumes agree within 2%, counts exactly (the dataset's own rule)


class IsolatedContext(Context):
    """A context that sees only its own file: no DXF sibling, no IFC model."""

    def sibling(self, ext: str):
        return super().sibling(ext) if ext == self.q.ext else None

    def model_ifc(self):
        return None


@dataclass
class Witness:
    fmt: str            # DXF | IFC | PDF | PNG
    path: str           # dataset-relative path the witness read
    pdf_only: bool


def witnesses_for(root: Path, rel: str) -> list[Witness]:
    f = root / rel
    stem, parent = f.stem, f.parent
    base = rel.rsplit("/", 1)[0] + "/"
    ext = f.suffix.lower()[1:]
    out = []
    sib = lambda e: (parent / f"{stem}.{e}").exists()
    if ext == "dxf" or sib("dxf"):
        out.append(Witness("DXF", base + f"{stem}.dxf", False))
    model = None
    if ext == "ifc":
        model = rel
    else:
        m = parent / (stem.split("_plan-")[0] + ".ifc")
        if m.exists() and "_plan-" in stem:
            model = base + m.name
    if model is not None:
        # the router reads the model through a plan PDF too; asking the plan keeps the storey scope
        out.append(Witness("IFC", rel if ext in ("pdf", "svg") else model, False))
    if ext == "pdf" or sib("pdf"):
        out.append(Witness("PDF", base + f"{stem}.pdf", True))
    for e in ("png", "jpg", "jpeg"):
        if ext == e or sib(e):
            out.append(Witness("PNG", base + f"{stem}.{e}", False))
            break
    return out


def same(a, b, unit: str) -> bool:
    if a is None or b is None:
        return False
    try:
        a, b = float(a), float(b)
    except (TypeError, ValueError):
        return str(a).strip().lower() == str(b).strip().lower()
    if unit == "count" or unit == "":
        return a == b
    return abs(a - b) <= max(abs(b), abs(a)) * AREA_TOL


def run(root: Path, rel: str, question: str, out_dir: Path | None = None, evidence: bool = False,
        on_answer=None) -> dict:
    """Solve the question once per available format. Returns the witnesses and their agreement."""
    query = parse(question)
    rows = []
    for w in witnesses_for(root, rel):
        q = Question(qid=-1, path=w.path, question=question)
        cls = IsolatedContext if w.fmt in ("PDF", "PNG") else Context
        ctx = cls(root=root, q=q, out_dir=out_dir or Path("."), evidence=evidence and out_dir is not None,
                      pdf_only=w.pdf_only, nemotron=False)
        t0, c0 = time.perf_counter(), time.process_time()
        try:
            _, ans = solve(ctx, query)
        except Exception as ex:      # one broken witness must not hide the others
            from .answer import abstain
            ans = abstain("witness", f"error: {type(ex).__name__}: {ex}")
        row = {"format": w.fmt, "path": w.path, "pdf_only": w.pdf_only, "route": ans.route,
               "value": ans.value if ans.answered else None, "unit": ans.unit, "tier": ans.tier if ans.answered else None,
               "abstain": ans.abstain, "confidence": ans.confidence, "n_items": len(ans.items or []),
               "wall_s": round(time.perf_counter() - t0, 3), "cpu_s": round(time.process_time() - c0, 3),
               "evidence": ans.evidence}
        if on_answer is not None:
            on_answer(row, ans)
        rows.append(row)
    return {"witnesses": rows, **agreement(rows)}


def agreement(rows: list[dict]) -> dict:
    """Groups of witnesses that give the same value; the largest group is the majority."""
    answered = [r for r in rows if r["value"] is not None]
    unit = next((r["unit"] for r in answered if r["unit"]), "")
    groups: list[list[dict]] = []
    for r in answered:
        for g in groups:
            if same(r["value"], g[0]["value"], unit):
                g.append(r)
                break
        else:
            groups.append([r])
    groups.sort(key=lambda g: -len(g))
    major = groups[0] if groups else []
    for r in rows:
        r["agrees"] = None if r["value"] is None else (r in major and len(major) > 1)
    if len(answered) < 2:
        level = "single" if answered else "none"
    elif len(groups) == 1:
        level = "all"
    elif len(major) > 1 and len(major) > len(groups[1]):
        level = "majority"
    else:
        level = "split"
    return {"n_formats": len(rows), "n_answered": len(answered), "n_agree": len(major) if len(major) > 1 else (1 if answered else 0),
            "level": level, "majority_value": major[0]["value"] if major and (len(major) > 1 or len(answered) == 1) else None,
            "unit": unit}
