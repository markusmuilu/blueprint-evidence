"""Router: pick the cheapest route that can prove the answer.

Order of preference, cheapest and most verifiable first:
  1. structured CAD/BIM data for the same drawing (DXF layers, IFC entities and quantities)
  2. the PDF text layer and its tables
  3. raster computer vision (classical, no model)
  4. (future) a vision LLM, only when nothing above can answer

A route returns an Answer or abstains with a reason; the router then tries the next route.
Every route here costs $0 in model calls.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .answer import Answer, abstain
from .data import Question
from .query import Query, parse
from .routes import dxf_swiss, ifc_route, pdf_text, raster

RASTER_EXT = {"png", "jpg", "jpeg", "tif", "tiff"}


@dataclass
class Context:
    root: Path
    q: Question
    out_dir: Path
    evidence: bool = True
    prior: bool = False          # allow the labelled dev-prior fallback on raster questions
    tried: list = field(default_factory=list)

    @property
    def file(self) -> Path:
        return self.root / self.q.path

    def sibling(self, ext: str) -> Path | None:
        p = self.file.with_suffix("." + ext)
        return p if p.exists() else None

    def model_ifc(self) -> Path | None:
        """IFC model a plan PDF/SVG was rendered from (`<model>_<level>.pdf` -> `<model>.ifc`)."""
        if self.q.ext == "ifc":
            return self.file
        stem = self.file.stem.split("_plan-")[0]
        p = self.file.parent / (stem + ".ifc")
        return p if p.exists() else None


def candidate_routes(ctx: Context):
    routes = []
    if ctx.sibling("dxf") is not None:
        routes.append(dxf_swiss.answer)
    if ctx.model_ifc() is not None:
        routes.append(ifc_route.answer)
    if ctx.q.ext == "pdf":
        routes.append(pdf_text.answer)
    if ctx.q.ext in RASTER_EXT or (ctx.q.ext == "pdf" and not routes):
        routes.append(raster.answer)
    return routes


def solve(ctx: Context, query: Query | None = None) -> tuple[Query, Answer]:
    query = query or parse(ctx.q.question)
    if not query.ok():
        return query, abstain("parser", "question not understood by the rule parser (LLM parser hook not enabled)")
    last = abstain("router", "no route for this file type")
    reasons = []
    for fn in candidate_routes(ctx):
        try:
            a = fn(ctx, query)
        except Exception as ex:  # a broken route must never take the run down
            a = abstain(getattr(fn, "__module__", "route").rsplit(".", 1)[-1], f"error: {type(ex).__name__}: {ex}")
        ctx.tried.append(a.route)
        if a.answered:
            return query, a
        reasons.append(f"{a.route}: {a.abstain}")
    if reasons:
        last.route, last.abstain = ctx.tried[0], "; ".join(reasons)
    return query, last
