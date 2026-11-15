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

import copy
import time
from dataclasses import dataclass, fields
from pathlib import Path

from .data import Question
from .query import Query, parse
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


def main_reading(question: str) -> Query:
    """The reading the main answer uses: rules first, the language-model reading where the rules read nothing or too
    little (its parse is cached, so this is the same reading at no new cost). A witness that read the question by the
    rules alone answered another question (night QA 15 Nov: "rooms named TUALETTRUUM" 60.81 m2, the witnesses
    agreed on 870.29, the whole floor)."""
    from . import nemotron as nemo_client
    from . import phrasing
    try:
        return phrasing.read(question, use_llm=nemo_client.enabled())
    except Exception:
        return parse(question)


def query_from_dict(d: dict | None) -> Query | None:
    if not d:
        return None
    names = {f.name for f in fields(Query)}
    try:
        q = Query(**{k: v for k, v in d.items() if k in names})
    except TypeError:
        return None
    return q if q.ok() else None


def run(root: Path, rel: str, question: str, out_dir: Path | None = None, evidence: bool = False,
        on_answer=None, query: Query | None = None) -> dict:
    """Solve the question once per available format. Returns the witnesses and their agreement. `query` is the main
    answer's reading; every witness answers that reading, with the same constraint guard as the main answer."""
    query = query or main_reading(question)
    rows = []
    for w in witnesses_for(root, rel):
        q = Question(qid=-1, path=w.path, question=question)
        cls = IsolatedContext if w.fmt in ("PDF", "PNG") else Context
        ctx = cls(root=root, q=q, out_dir=out_dir or Path("."), evidence=evidence and out_dir is not None,
                      pdf_only=w.pdf_only, nemotron=False)
        t0, c0 = time.perf_counter(), time.process_time()
        try:
            _, ans = solve(ctx, copy.deepcopy(query), guard=True)
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


def spatial_disagreements(rows: list[dict], tol: float = 0.012) -> int:
    """Circle where two witnesses drawn on the same page disagree about individual items.

    Witnesses whose evidence is the whole page in pixels (a rendered PDF and its PNG) share a frame up to
    scale when their aspect ratios match; their numbered boxes are compared by centre in page-relative
    coordinates (tolerance `tol` of the page width). Each witness gets `disputed`: points (fractions of
    its evidence body) that it counted and another format did not ("only here"), or that another format
    counted and it did not ("missed here"). DXF and IFC evidence is drawn in model coordinates with no
    page frame, so it is not compared. Returns the number of disputed points.
    """
    def centres(r):
        m = r.get("marks") or {}
        fr = m.get("frame") or {}
        if fr.get("kind") != "pixel" or not m.get("w") or not m.get("h"):
            return None
        return [((b["rect"][0] + b["rect"][2]) / 2 / m["w"], (b["rect"][1] + b["rect"][3]) / 2 / m["h"], b.get("n"))
                for b in m.get("marks", []) if b["kind"] == "box"]

    paged = [(r, centres(r)) for r in rows if r.get("value") is not None and r.get("unit") == "count"]
    paged = [(r, c) for r, c in paged if c]
    for r in rows:
        r["disputed"] = []
    total = 0
    for i, (ra, ca) in enumerate(paged):
        for rb, cb in paged[i + 1:]:
            asp_a = ra["marks"]["w"] / ra["marks"]["h"]
            asp_b = rb["marks"]["w"] / rb["marks"]["h"]
            if abs(asp_a - asp_b) > 0.01 * asp_a:
                continue
            used, only_a = set(), []
            for xa, ya, na in ca:
                best, bd = None, tol
                for j, (xb, yb, _) in enumerate(cb):
                    d = max(abs(xa - xb), abs(ya - yb) * (1 / asp_a))
                    if j not in used and d <= bd:
                        best, bd = j, d
                if best is None:
                    only_a.append((xa, ya, na))
                else:
                    used.add(best)
            only_b = [c for j, c in enumerate(cb) if j not in used]
            for x, y, n in only_a:
                ra["disputed"].append({"x": round(x, 4), "y": round(y, 4), "n": n, "kind": "only", "other": rb["format"]})
                rb["disputed"].append({"x": round(x, 4), "y": round(y, 4), "n": None, "kind": "missed", "other": ra["format"]})
            for x, y, n in only_b:
                rb["disputed"].append({"x": round(x, 4), "y": round(y, 4), "n": n, "kind": "only", "other": ra["format"]})
                ra["disputed"].append({"x": round(x, 4), "y": round(y, 4), "n": None, "kind": "missed", "other": rb["format"]})
            total += len(only_a) + len(only_b)
    return total
