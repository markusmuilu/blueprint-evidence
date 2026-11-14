"""Router: pick the cheapest route that can prove the answer.

Order of preference, cheapest and most verifiable first:
  1. structured CAD/BIM data for the same drawing (DXF layers, IFC entities and quantities)
  2. the PDF text layer and its tables
  3. the PDF's own vector linework (pdf-vector), when no DXF/IFC twin exists or twins are ignored
  4. raster computer vision (classical, no model)
  5. nemotron (bpqa/routes/nemotron.py): a vision-language model, only when nothing above can answer;
     its answer must carry boxes of what it relied on, is validated and marked tier "estimate". Scans only (and
     scanned PDFs): on vector drawings it was rarely right where the file's own data could not answer
     (NEMO_ESTIMATE_VECTOR=1 turns it back on there)

A route returns an Answer or abstains with a reason; the router then tries the next route.
Routes 1 to 4 cost $0 in model calls; route 5 is the only one that calls a model.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from . import calibration, qualifiers
from . import checks as checking
from .answer import Answer, abstain
from .data import Question
from .query import Query, parse
from . import nemotron as nemo_client
from . import phrasing
from . import piping
from . import second_opinion
from .routes import dxf_swiss, ifc_props, ifc_route, nemotron, pdf_text, pdf_vector, raster, raster_rooms, sheet_plan, sheet_scale

RASTER_EXT = {"png", "jpg", "jpeg", "tif", "tiff"}


@dataclass
class Context:
    root: Path
    q: Question
    out_dir: Path
    evidence: bool = True
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


def candidate_routes(ctx: Context, query: Query | None = None):
    if query is not None and query.obj == "scale":
        return [sheet_scale.answer]     # the sheet's own scale: printed ratio or the drawing's measurements
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
        routes.append(raster_rooms.answer)    # room-type counts: detector + OCR labels; anything else: raster.answer
    return routes


def text_routes(ctx: Context):
    """Free-text / property routes, tried when the numeric routes cannot answer: typed lookups over
    the IFC model (ifc-props) and over PDF schedules (pdf-text). Every value comes from the file."""
    routes = []
    twins = not ctx.pdf_only or ctx.q.ext != "pdf"
    if piping.parse_piping(ctx.q.question) is not None:
        routes.append(piping.text_answer)     # numeric piping questions (bpqa/piping.py): length, by system / diameter, fittings
    if ctx.q.ext == "pdf":
        routes.append(pdf_text.answer)
    if twins and ctx.model_ifc() is not None:
        routes.append(ifc_props.answer)
    return routes


def _try_text(ctx: Context, query: Query, why: str) -> Answer:
    reasons = [why]
    for fn in text_routes(ctx):
        try:
            a = fn(ctx, query)
        except Exception as ex:
            a = abstain(getattr(fn, "__module__", "route").rsplit(".", 1)[-1], f"error: {type(ex).__name__}: {ex}")
        ctx.tried.append(a.route)
        if a.answered:
            if a.tier is None:
                a.tier = "proven"
            return a
        reasons.append(f"{a.route}: {a.abstain}")
    return abstain("parser", "; ".join(reasons))


def calibrate(a: Answer, query: Query) -> Answer:
    """Lower a proven scan answer to an estimate unless dev supports it (bpqa/calibration.py); the routes
    already do this for their own answers, this catches any scan route that does not."""
    why = calibration.reason(a.route or "", query.obj, a.value, query.measure, a.tier_cell)
    if why:
        from .answer import estimate
        estimate(a, why)
        if a.evidence:
            from .evidence import mark_estimate
            a.evidence = mark_estimate(a.evidence, a.value, why, a.checks)
    return a


def solve(ctx: Context, query: Query | None = None) -> tuple[Query, Answer]:
    query, a = _solve(ctx, query)
    if a.answered and query.measure == "area" and isinstance(a.value, float):
        a.value = round(a.value, 2)     # areas are stated to the centimetre squared (QA 14 Nov: "536.718")
    if a.answered and query.extra.get("scope_resolved"):
        a.explanation = f"{query.extra['scope_resolved']}. {a.explanation or ''}".strip()
    return query, a


# The ground floor named without quotes on a plan sheet whose storey is the ground floor: the sheet's own storey
GROUND_Q = re.compile(r"\b(ground floor|ground level|erdgeschoss|pohjakerros|maantasokerros|bottenvaningen?|"
                      r"stueetage|rez-de-chaussee|piano terra|pianterreno|esimene korrus)\b")
GROUND_STOREY = re.compile(r"erdgeschoss|^eg\b|ground|grundplan|stue|pohjakerros|bottenv|rez-de|terra|^0+$|level 0|"
                           r"^1\.? ?korrus")


def sheet_names_ground(ctx: Context, query: Query) -> str:
    """The storey name when the question names the ground floor and the plan sheet shows it, else ''."""
    from .query import fold, strip_quoted
    if ctx.q.ext not in ("pdf", "svg") or ctx.model_ifc() is None or query.extra.get("compound"):
        return ""
    f = fold(strip_quoted(ctx.q.question))
    if not GROUND_Q.search(f) or re.search(r"\b(first|second|upper|top|basement|attic|level \d)\b", f):
        return ""
    try:
        from .routes import ifc_route
        st, _ = ifc_route.sheet_storey(ifc_route.load(str(ctx.model_ifc())), ctx)
    except Exception:
        return ""
    return st.Name if st is not None and GROUND_STOREY.search(fold(st.Name or "").strip()) else ""


# A storey named without quotes: "on the 3rd floor", "on 3.korrus", "5. kerroksessa", "level 2", "2. Sal"
_ORD = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8,
        "ninth": 9, "tenth": 10}
STOREY_NAMED = re.compile(
    r"\b(?P<n1>\d{1,2})(?:st|nd|rd|th)?\.? ?(?:floor|level|storey|story)\b"
    r"|\b(?P<w>first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth) (?:floor|level|storey|story)\b"
    r"|\b(?P<n2>\d{1,2})\. ?(?:korrus\w*|kerro\w*|sal\b|og\b|obergeschoss\w*|etage\w*|stock\w*|vaning\w*|plan\b)"
    r"|\b(?:level|floor|storey|kerros|korrus|etage|geschoss) (?P<n3>\d{1,2})\b")


def named_storey(ctx: Context, query: Query) -> tuple[str, str, bool] | None:
    """(storey name, note, resolved) for an IFC-backed drawing whose question names a storey by number without
    quotes; None when no storey is named (or the question quotes one, which the rules already read)."""
    from .query import fold, strip_quoted
    if query.level or ctx.model_ifc() is None or ctx.q.ext not in ("pdf", "svg"):
        return None                     # plan sheets only: a question on the .ifc keeps its own scope handling
    f = fold(strip_quoted(ctx.q.question))
    m = STOREY_NAMED.search(f)
    if not m:
        return None
    n = int(m.group("n1") or m.group("n2") or m.group("n3") or _ORD[m.group("w")])
    phrase = m.group(0)
    from .routes import ifc_route
    M = ifc_route.load(str(ctx.model_ifc()))
    names = [s.Name or "" for s in M.storeys]
    hits = [s for s in names if re.search(rf"(?<!\d){n}(?!\d)", fold(s)) and not re.search(r"\d{3,}", fold(s))]
    if len(hits) != 1:
        return "", (f"the question names '{phrase}', which matches {'no storey' if not hits else 'several storeys'} of "
                    f"this model (storeys: {', '.join(names)})"), False
    # a ground storey with a name and no number (Grundplan, Erdgeschoss) shifts the count by one: say so. Where the
    # ground storey is itself numbered (1.korrus) the number is unambiguous
    ground = [s for s in names if GROUND_STOREY.search(fold(s).strip()) and not re.search(r"\d", s)]
    note = f"'{phrase}' read as storey {hits[0]} (by its number)"
    if ground:
        note += f"; counted from the ground floor ({ground[0]}) it could also mean the storey below"
    return hits[0], note, True


def _image_estimate_allowed(ctx: Context) -> bool:
    """The Nemotron image estimate as a last resort: kept for scans, skipped for vector drawings (IFC sheets, CAD,
    vector PDFs) unless NEMO_ESTIMATE_VECTOR=1. Measured on dev, 14 Nov: see CHANGELOG (it was rarely right there, and
    every such 'cannot' waited about 16 s for it)."""
    if os.environ.get("NEMO_IMAGE_ESTIMATE", "1").lower() in ("0", "off", "false", "no"):
        return False                    # the live app (deploy/web.py) drops the image estimate entirely
    if ctx.q.ext in RASTER_EXT:
        return True
    if os.environ.get("NEMO_ESTIMATE_VECTOR", "0").lower() in ("1", "on", "true", "yes"):
        return True
    if ctx.q.ext == "pdf" and ctx.sibling("dxf") is None and ctx.model_ifc() is None:
        return not _pdf_is_vector(str(ctx.file))     # a scanned PDF keeps the estimate
    return False


@lru_cache(maxsize=64)
def _pdf_is_vector(path: str) -> bool:
    """True when the first page carries linework (a CAD export), False for a scan embedded as an image."""
    try:
        import pymupdf
        with pymupdf.open(path) as doc:
            return len(doc[0].get_drawings()) >= 50
    except Exception:
        return False


def _solve(ctx: Context, query: Query | None = None) -> tuple[Query, Answer]:
    use_nemo = ctx.nemotron and nemo_client.enabled()
    if query is None:
        # rules first; the language model reads the question only when the rules read nothing, read something no
        # route answers, or ignored a room type the question names (bpqa/phrasing.py), and its reading is
        # validated into the same Query shape the rules build
        # on a large drawing sheet the sheet route reads the question against the room names printed on it, so the
        # language-model reading (a network call) is not needed
        big = ctx.q.ext in RASTER_EXT and sheet_plan.applies(ctx)
        query = phrasing.read(ctx.q.question, use_llm=use_nemo and not big)
    if ctx.q.ext in RASTER_EXT and query.obj != "scale" and sheet_plan.applies(ctx):
        # a large drawing sheet: read once at the resolution each step needs (bpqa/sheet.py); the scan routes below
        # were built for 2000 px images and would run out of memory on it
        try:
            a = sheet_plan.answer(ctx, query)
        except Exception as ex:
            a = abstain(sheet_plan.ROUTE, f"error: {type(ex).__name__}: {ex}")
        ctx.tried.append(a.route)
        return query, a
    if not query.ok():
        return query, _try_text(ctx, query, "question not understood by the rule parser (language-model parse off, or it found no measurable question)")
    ns = None if query.extra.get("compound") or query.obj == "storey" else named_storey(ctx, query)
    if ns is not None:
        # "doors on the 3rd floor" asked on the 1.korrus sheet: never the sheet's storey (QA 14 Nov: 46 proven)
        name, note, ok = ns
        if not ok:
            a = abstain("parser", f"not answered: {note}; ask with the storey name")
            a.final = True
            return query, a
        query.level, query.scope = name, "level"
        query.extra.pop("unresolved_scope", None)
        query.extra["scope_resolved"] = note
        from .query import attach_qualifier
        query = attach_qualifier(ctx.q.question, query)
    if query.extra.get("unresolved_scope") and not query.extra.get("takeoff", {}).get("scope_named"):
        st = sheet_names_ground(ctx, query)
        if st:      # "doors in the ground floor" asked on the Erdgeschoss sheet: the sheet's storey
            query.extra.pop("unresolved_scope", None)
            query.extra["scope_resolved"] = f"'ground floor' read as this sheet's storey {st}"
            from .query import attach_qualifier
            query = attach_qualifier(ctx.q.question, query)    # qualifiers are not read under an unresolved scope
    if query.extra.get("blocked") and not (query.extra.get("compound") or query.extra.get("unresolved_scope")):
        a = abstain("parser", f"not answered: {query.extra['blocked']}. Ask for a room type the drawings name "
                              f"(bedroom, kitchen, bathroom, WC, office, meeting room ...) or for all rooms")
        a.final = True
        return query, a
    if query.extra.get("compound") or query.extra.get("unresolved_scope"):
        return query, _try_text(ctx, query, "compound question or unresolved scope")
    tk = query.extra.get("takeoff")
    from .takeoff import WALL_ROUTES, needs_walls, wall_reason
    if tk is not None:
        from .takeoff import precheck
        why = precheck(tk)
        if why:
            return query, abstain("takeoff", why)
    last = abstain("router", "no route for this file type")
    held = None
    reasons = []
    skipped = ""        # why routes that cannot filter by a qualifier did not run
    if ctx.evidence and no_derivatives(ctx):
        ctx.evidence = False   # answer still given; the JSON items list is the evidence
    for fn in candidate_routes(ctx, query):
        if query.extra.get("apartment_scope") and fn is not dxf_swiss.answer:
            # "rooms in apartment A": only the Swiss DXF route assigns rooms to apartments; any other would count
            # every room on the floor
            ctx.tried.append(qualifiers.route_module(fn))
            reasons.append(f"{qualifiers.route_module(fn)}: cannot assign rooms to one apartment")
            continue
        qwhy = qualifiers.cannot_honour(fn, query)   # a qualifier ("fire doors") is never dropped silently
        if qwhy:
            ctx.tried.append(qualifiers.route_module(fn))
            skipped = skipped or qwhy        # one message is enough: the same filter, whatever the route
            continue
        try:
            a = fn(ctx, query)
        except Exception as ex:  # a broken route must never take the run down
            a = abstain(getattr(fn, "__module__", "route").rsplit(".", 1)[-1], f"error: {type(ex).__name__}: {ex}")
        if ctx.checks and a.answered:
            before = a.tier
            try:
                a = checking.apply(ctx, query, a)
            except Exception as ex:   # a broken check must not hide a route's answer
                a.checks = [{"name": "checking-layer", "status": "error", "detail": f"{type(ex).__name__}: {ex}"}]
            if a.tier == "proven" and before != "proven":
                a.tier = before         # a consistency check can lower a tier, never raise one
        ctx.tried.append(a.route)
        if a.answered and tk is not None and a.route not in WALL_ROUTES and needs_walls(tk):
            a = abstain(a.route, wall_reason(tk))   # a floor-only takeoff must not answer a wall question
        if a.answered and a.tier is None:
            a.tier = "proven"           # structured routes: the answer is derived from the file's own data
        if a.answered and a.tier == "proven" and (a.route or "").startswith("raster"):
            a = calibrate(a, query)     # scan answers: proven only where dev measured it reliable
        if a.answered or a.checks:      # a check-abstained answer keeps its checks and evidence
            return query, second_opinion.maybe(ctx, query, a)   # opt-in (NEMO_SECOND=1), estimate answers only
        if a.final:                     # the drawing was read and does not carry the answer: no guessing
            return query, a
        if a.checks:                    # a check-abstained answer keeps its checks and evidence
            if not use_nemo:
                return query, a
            held = a
        reasons.append(f"{a.route}: {a.abstain}")
    if qualifiers.kinds(query) and qualifiers.blocks_fallback(query):
        # no route could filter by the qualifier, and the lookups and models below cannot either: stop here
        return query, qualifiers.final_abstention(skipped, reasons, query)
    if any(fn is ifc_props.answer for fn in text_routes(ctx)):   # property lookups before any estimate
        a = _try_text(ctx, query, "")
        if a.answered:
            return query, a
    if qualifiers.kinds(query):         # a model looking at the image cannot filter by the qualifier either
        return query, qualifiers.final_abstention(skipped, reasons, query)
    if use_nemo and not _image_estimate_allowed(ctx):
        reasons.append("nemotron-estimate: image estimate switched off (NEMO_IMAGE_ESTIMATE=0)"
                       if os.environ.get("NEMO_IMAGE_ESTIMATE", "1").lower() in ("0", "off", "false", "no") else
                       "nemotron-estimate: not tried on a vector drawing (an image estimate is rarely right where the "
                       "file's own data cannot answer)")
        use_nemo = False
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
