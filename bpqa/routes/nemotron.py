"""nemotron route: a vision-language model answers when no other route can. Tier "estimate".

Used only as the last resort of the router (every other route abstained) and for the question-parse
fallback. Nothing the model says is trusted as it comes:

* the model must return one JSON object {"value": number, "regions": [{"label", "bbox"}], "reading": str};
* `validate()` checks the JSON: value is a finite non-negative number (an integer for counts), every
  bbox is four numbers inside the image, with positive size, and does not cover the whole drawing;
  a count must come with exactly one region per counted object (up to MAX_COUNT_REGIONS); an area
  must name at least one region, and is rejected when implausible (< 0.5 or > 5000 m2);
* any failure abstains, with the reason, and the router returns "no answer" as before;
* the regions are drawn on the drawing (numbered boxes) as evidence, and the answer is marked
  `tier="estimate"`, confidence 0.3: it is the model's reading of the picture, not a derivation.

Switch: env NEMO_FALLBACK=0 turns the route and the parse fallback off. Default on, but only reached
when the other routes abstained or the rule parser could not parse the question.
"""
from __future__ import annotations

import io
import json
import math
import os
import re
from pathlib import Path

from PIL import Image

from .. import nemotron as N
from ..answer import Answer, abstain
from ..evidence import PALETTE, ImageCanvas, evidence_path, finish
from ..query import OBJECTS, Query, QueryParser, fold

ROUTE = "nemotron-estimate"
MAX_COUNT_REGIONS = 40
NUMERIC = {"count", "area", "length", "volume"}
# Assumed list price of a paid API per million tokens, only used to put a $ figure on an answer.
PRICE_IN, PRICE_OUT = float(os.environ.get("NEMO_PRICE_IN", "3")), float(os.environ.get("NEMO_PRICE_OUT", "15"))
ANSWER_MAX_TOKENS = 16384
THINK = os.environ.get("NEMO_THINK", "1") != "0"   # reasoning on for the image answer, off for the question parse
RASTER_EXT = {"png", "jpg", "jpeg", "tif", "tiff"}

SYSTEM = ("You read construction drawings. You answer with one JSON object and nothing else: no markdown, "
          "no text before or after.")


def render(file: Path) -> tuple[Image.Image, bytes, tuple[int, int]]:
    """The drawing as an image (PDF: page 1 at 150 dpi), the downscaled PNG sent, and its size."""
    if file.suffix.lower() == ".pdf":
        import pymupdf
        pix = pymupdf.open(file)[0].get_pixmap(dpi=150)
        img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
    else:
        img = Image.open(file).convert("RGB")
    png, sent, _ = N.prepare_image(img)
    return img, png, sent


def what_to_find(q: Query) -> str:
    unit = {"count": "a count", "area": "an area in square metres", "length": "a length in metres",
            "volume": "a volume in cubic metres"}[q.measure]
    return unit


def build_prompt(question: str, q: Query, size: tuple[int, int]) -> str:
    w, h = size
    kind = "count" if q.measure == "count" else q.measure
    return (
        f"The image is a construction drawing, {w} x {h} pixels. Question: {question}\n\n"
        f"The answer is {what_to_find(q)}. Reply with this JSON object:\n"
        '{"value": <number>, "regions": [{"label": "<what it is>", "bbox": [x_min, y_min, x_max, y_max]}], '
        '"reading": "<one sentence: how you got the value>"}\n'
        f"bbox values are integer pixel coordinates in the {w} x {h} image, origin top-left. "
        + ("List one region per counted object, so that the number of regions equals value. "
           if kind == "count" else
           "List the region(s) of the drawing that the value is taken from or summed over. ")
        + "If you cannot tell, give your best reading anyway; do not add other keys.")


def extract_json(text: str | None) -> dict | None:
    if not text:
        return None
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        d = json.loads(t)
    except ValueError:
        m = re.search(r"\{.*\}", t, re.S)
        if not m:
            return None
        try:
            d = json.loads(m.group(0))
        except ValueError:
            return None
    return d if isinstance(d, dict) else None


def validate(d: dict | None, q: Query, size: tuple[int, int]) -> tuple[dict | None, str]:
    """(clean answer, "") or (None, reason). The clean answer has value, regions [{label, bbox}], reading."""
    if d is None:
        return None, "no JSON object in the reply"
    if q.measure not in NUMERIC:
        return None, f"measure {q.measure} is not numeric"
    v = d.get("value")
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
        return None, f"value is not a finite non-negative number: {v!r}"
    if q.measure == "count" and abs(v - round(v)) > 1e-9:
        return None, f"count is not an integer: {v}"
    if q.measure == "area" and not 0.5 <= v <= 5000:
        return None, f"area {v} outside the plausible range 0.5 to 5000 m2"
    regs = d.get("regions")
    if not isinstance(regs, list):
        return None, "regions is not a list"
    w, h = size
    clean = []
    for i, r in enumerate(regs):
        b = r.get("bbox") if isinstance(r, dict) else None
        if not (isinstance(b, list) and len(b) == 4 and all(isinstance(x, (int, float)) and not isinstance(x, bool)
                                                            and math.isfinite(x) for x in b)):
            return None, f"region {i + 1}: bbox is not four numbers"
        x0, y0, x1, y1 = b
        tol = 0.02
        if x1 <= x0 or y1 <= y0:
            return None, f"region {i + 1}: empty or inverted bbox"
        if x0 < -tol * w or y0 < -tol * h or x1 > (1 + tol) * w or y1 > (1 + tol) * h:
            return None, f"region {i + 1}: bbox outside the {w} x {h} image"
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
        if q.measure == "count" and (x1 - x0) * (y1 - y0) > 0.5 * w * h:
            return None, f"region {i + 1}: a counted object cannot cover half the drawing"
        clean.append({"label": str(r.get("label", ""))[:60], "bbox": [round(x0), round(y0), round(x1), round(y1)]})
    if q.measure == "count":
        if v > MAX_COUNT_REGIONS:
            return None, f"count {v:.0f} above {MAX_COUNT_REGIONS}: too many objects to check one by one"
        if len(clean) != round(v):
            return None, f"count {v:.0f} but {len(clean)} region(s); each counted object needs a box"
    elif not clean:
        return None, "no region named for the measurement"
    return {"value": int(round(v)) if q.measure == "count" else float(v), "regions": clean,
            "reading": str(d.get("reading", ""))[:300]}, ""


def answerable(ctx, q: Query) -> str | None:
    """Reason not to call the model, or None."""
    if not N.enabled():
        return "nemotron fallback switched off or no API key"
    if q.measure not in NUMERIC:
        return f"nemotron route answers numeric questions only (measure {q.measure})"
    if q.scope == "model" or q.extra.get("compound") or q.extra.get("unresolved_scope"):
        return "question is about a whole model or is compound; one image cannot prove it"
    if ctx.q.ext not in RASTER_EXT and ctx.q.ext != "pdf":
        return f"{ctx.q.ext} file cannot be shown to a vision model"
    return None


def answer(ctx, q: Query) -> Answer:
    why = answerable(ctx, q)
    if why:
        return abstain(ROUTE, why)
    img, png, sent = render(ctx.file)
    r = N.chat(build_prompt(ctx.q.question, q, sent), png, system=SYSTEM, max_tokens=ANSWER_MAX_TOKENS, think=THINK)
    if r.get("error") or r.get("content") is None:
        return abstain(ROUTE, f"model call failed: {r.get('error') or 'empty reply (' + str(r.get('finish_reason')) + ')'}")
    clean, bad = validate(extract_json(r["content"]), q, sent)
    cost = ((r.get("prompt_tokens") or 0) * PRICE_IN + (r.get("completion_tokens") or 0) * PRICE_OUT) / 1e6
    if clean is None:
        a = abstain(ROUTE, f"invalid model output: {bad}")
        a.cost_usd = cost
        return a
    sx, sy = img.width / sent[0], img.height / sent[1]     # sent image -> original image coordinates
    items = []
    for i, g in enumerate(clean["regions"]):
        b = g["bbox"]
        items.append({"n": i + 1, "label": g["label"], "xyxy": [round(b[0] * sx), round(b[1] * sy),
                                                                round(b[2] * sx), round(b[3] * sy)]})
    unit = {"count": "count", "area": "m2", "length": "m", "volume": "m3"}[q.measure]
    ans = Answer(value=clean["value"], unit=unit, route=ROUTE, confidence=0.3, items=items, cost_usd=round(cost, 5),
                 tier="estimate",
                 explanation=f"estimate read by {N.MODEL} from the image: {clean['reading']} "
                             f"({len(items)} region(s) drawn, not independently verified)")
    if ctx.evidence:
        cv = ImageCanvas(img)
        for it in items:
            cv.box(it["xyxy"], n=it["n"], color=PALETTE[1], width=max(2, img.width // 500))
        ans.evidence = finish(cv.img, evidence_path(ctx.out_dir, ctx.q.path, ctx.q.question), ctx.q.question,
                              f"Answer: {clean['value']}   ESTIMATE by {N.MODEL}",
                              note="tier: estimate. Boxes are the regions the model said it relied on, drawn as given "
                                   "after validation; no other route could prove this answer. " + clean["reading"])
    return ans


# ---------------------------------------------------------------- question-parse fallback

OBJ_NAMES = sorted({n for n, _ in OBJECTS} | {"room", "apartment"})
MEASURES = ["count", "area", "length", "volume", "text"]
SCOPES = ["drawing", "level", "model"]
VARIANTS = ["total", "net_indoor_total", "side_area"]


class NemotronParser(QueryParser):
    """Maps a question the rules could not parse to the typed query schema, validated.

    The schema is closed (measure, obj, scope and variant come from fixed lists); a room label, room
    number, apartment letter or level name must appear in the question text itself, so a made-up value is
    rejected. A reply that fails validation leaves the query unparsed (the router abstains as before).
    """
    name = "nemotron"

    def prompt(self, question: str) -> str:
        return (
            "Map this question about a construction drawing to a JSON query. Question:\n"
            f"{question}\n\n"
            "Reply with one JSON object with these keys:\n"
            f'  "measure": one of {MEASURES}\n'
            f'  "obj": one of {OBJ_NAMES} (what is counted or measured)\n'
            '  "label": name of a room as written in the question, or null\n'
            '  "room_no": room number as written in the question, or null\n'
            '  "apartment": apartment letter as written in the question, or null\n'
            '  "level": storey or level name as written in the question, or null\n'
            f'  "scope": one of {SCOPES} ("model" if the question is about a whole building model, "level" if about '
            'one named storey, else "drawing")\n'
            f'  "variant": one of {VARIANTS} or null\n'
            "Use null for anything the question does not say. Do not add other keys.")

    def parse(self, question: str) -> Query:
        q = Query(raw=question, parser="nemotron")
        if not N.enabled():
            return q
        r = N.chat(self.prompt(question), None, system=SYSTEM, max_tokens=1024, think=False)
        d = extract_json(r.get("content"))
        if d is None:
            return q
        q.extra["cost_usd"] = ((r.get("prompt_tokens") or 0) * PRICE_IN + (r.get("completion_tokens") or 0) * PRICE_OUT) / 1e6
        f = fold(question)
        if d.get("measure") not in MEASURES or d.get("obj") not in OBJ_NAMES:
            return q
        if d.get("scope", "drawing") not in SCOPES or d.get("variant") not in VARIANTS + [None]:
            return q
        for key in ("label", "room_no", "apartment", "level"):
            v = d.get(key)
            if v is None:
                continue
            if not isinstance(v, str) or fold(v) not in f:      # a value the question does not contain: reject all
                return q
        q.measure, q.obj = d["measure"], d["obj"]
        q.label, q.room_no, q.apartment, q.level = d.get("label"), d.get("room_no"), d.get("apartment"), d.get("level")
        q.scope, q.variant = d.get("scope") or "drawing", d.get("variant")
        if q.apartment:
            q.apartment = q.apartment.upper()
        # Same conventions as the rule parser, so the routes see one shape of query whoever parsed it:
        if q.label or q.room_no:                 # a named or numbered room is obj "room" (name in `label`)
            q.obj, q.variant = "room", None
        if q.apartment and q.measure == "area":
            q.obj = "apartment"
        if q.level and q.scope == "drawing":
            q.scope = "level"
        if not q.level and q.scope == "level":
            q.scope = "drawing"
        q.extra["llm_parsed"] = True
        return q
