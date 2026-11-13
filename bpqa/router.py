"""Router: pick the cheapest route that can prove the answer.

Order of preference, cheapest and most verifiable first:
  1. structured CAD/BIM data for the same drawing (DXF layers, IFC entities and quantities)
  2. the PDF text layer and its tables
  3. the PDF's own vector linework (pdf-vector), when no DXF/IFC twin exists or twins are ignored
  4. raster computer vision (classical, no model)
  5. nemotron (bpqa/routes/nemotron.py): a vision-language model, only when nothing above can answer;
     its answer must carry boxes of what it relied on, is validated and marked tier "estimate"

A route returns an Answer or abstains with a reason; the router then tries the next route.
Routes 1 to 4 cost $0 in model calls; route 5 is the only one that calls a model.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from . import checks as checking
from .answer import Answer, abstain
from .data import Question
from .query import Query, parse
from . import nemotron as nemo_client
from .routes import dxf_swiss, ifc_route, nemotron, pdf_text, pdf_vector, raster

RASTER_EXT = {"png", "jpg", "jpeg", "tif", "tiff"}


@dataclass
class Context:
    root: Path
    q: Question
    out_dir: Path
    evidence: bool = True
    prior: bool = False          # allow the labelled dev-prior fallback on raster questions
    checks: bool = True          # run the checking layer on raster answers (bpqa/checks.py)
    pdf_only: bool = False       # ignore DXF/IFC twins: answer from the file itself (eval.py --pdf-only)
    nemotron: bool = True        # Nemotron fallbacks (parse + last-resort route); also needs env NEMO_FALLBACK != 0
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


@lru_cache(maxsize=32)
def _licences(licence_md: Path) -> dict:
    """file name -> licence id, from the `| file | title | licence | ...` table in LICENSE.md."""
    out = {}
    if licence_md.exists():
        for line in licence_md.read_text(encoding="utf-8", errors="replace").splitlines():
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) >= 3 and "." in cells[0]:
                out[cells[0].split("/")[-1]] = cells[2]
    return out


def no_derivatives(ctx: Context) -> bool:
    """NoDerivatives files must stay unchanged, so no overlay is drawn on (or rendered from) them."""
    files = [ctx.file]
    m = ctx.model_ifc()
    if m is not None:
        files.append(m)
    lic = _licences(ctx.root / "data" / ctx.q.source / "LICENSE.md")
    return any("ND" in lic.get(f.name, "").upper().split("-") for f in files)


def candidate_routes(ctx: Context):
    routes = []
    twins = not ctx.pdf_only or ctx.q.ext != "pdf"
    has_twin = ctx.sibling("dxf") is not None or ctx.model_ifc() is not None
    if twins and ctx.sibling("dxf") is not None:
        routes.append(dxf_swiss.answer)
    if twins and ctx.model_ifc() is not None:
        routes.append(ifc_route.answer)
    if ctx.q.ext == "pdf":
        routes.append(pdf_text.answer)
        if ctx.pdf_only or not has_twin:
            routes.append(pdf_vector.answer)
    if ctx.q.ext in RASTER_EXT or (ctx.q.ext == "pdf" and not routes):
        routes.append(raster.answer)
    return routes


def solve(ctx: Context, query: Query | None = None) -> tuple[Query, Answer]:
    query = query or parse(ctx.q.question)
    use_nemo = ctx.nemotron and nemo_client.enabled()
    if not query.ok() and use_nemo:      # rule parser failed: ask Nemotron for the typed query (validated)
        query = parse(ctx.q.question, fallback=nemotron.NemotronParser())
    if not query.ok():
        return query, abstain("parser", "question not understood by the rule parser (Nemotron parse fallback off or its output invalid)")
    if query.extra.get("compound") or query.extra.get("unresolved_scope"):
        return query, abstain("parser", "compound question or unresolved scope; needs the text/LLM route")
    last = abstain("router", "no route for this file type")
    held = None
    reasons = []
    if ctx.evidence and no_derivatives(ctx):
        ctx.evidence = False   # answer still given; the JSON items list is the evidence
    for fn in candidate_routes(ctx):
        try:
            a = fn(ctx, query)
        except Exception as ex:  # a broken route must never take the run down
            a = abstain(getattr(fn, "__module__", "route").rsplit(".", 1)[-1], f"error: {type(ex).__name__}: {ex}")
        if ctx.checks and a.answered:
            try:
                a = checking.apply(ctx, query, a)
            except Exception as ex:   # a broken check must not hide a route's answer
                a.checks = [{"name": "checking-layer", "status": "error", "detail": f"{type(ex).__name__}: {ex}"}]
        ctx.tried.append(a.route)
        if a.answered:
            return query, a
        if a.checks:                    # a check-abstained answer keeps its checks and evidence
            if not use_nemo:
                return query, a
            held = a
        reasons.append(f"{a.route}: {a.abstain}")
    if use_nemo:                        # last resort: every route above abstained
        try:
            a = nemotron.answer(ctx, query)
        except Exception as ex:
            a = abstain("nemotron-estimate", f"error: {type(ex).__name__}: {ex}")
        ctx.tried.append(a.route)
        if a.answered:
            return query, a
        if held is not None:
            return query, held
        reasons.append(f"{a.route}: {a.abstain}")
    if reasons:
        last.route, last.abstain = ctx.tried[0], "; ".join(reasons)
    return query, last
