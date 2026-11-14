"""Nemotron as an opt-in SECOND OPINION on raster count answers that are only estimates.

Where the raster route answers a count with tier "estimate" (the detector's boxes were near its threshold,
or a room label was misread or missing), this step asks the vision-language model (bpqa/nemotron.py) for its
own count and combines the two by a per-object policy chosen on the dev split (tools/nemo_second.py report).

Switch: env NEMO_SECOND=1 turns it on. Default OFF. It also needs the Nemotron client enabled (key present,
NEMO_FALLBACK not 0) and `ctx.nemotron` true. Without any of these the answer is returned unchanged.

Two ways of asking, both cached on disk by the client:
  A  the drawing and the question; reply {"count": n}
  B  our boxes drawn on the drawing as numbered red boxes; the model verifies or corrects the count and
     replies {"count": n, "boxes": [...]}

What the step may do to an answer (never to a "proven" one, never to a non-count one):
  keep    the model's count equals ours: tier stays "estimate", the check is recorded
  take    the model's count replaces ours, route becomes "<route>+nemotron", tier "estimate"
  abstain the answer is withdrawn (only for policies that say so)
Every decision is recorded as a check named "nemotron-second-opinion" on the answer, so the app and the JSON
show it. The key is never printed or written.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from PIL import Image, ImageDraw

from . import nemotron as N
from .answer import Answer, estimate

ENV = "NEMO_SECOND"
SYSTEM = "You count objects on architectural floor plans. Reply with one JSON object and nothing else."
JSON_RE = re.compile(r"\{.*\}", re.S)
MAX_TOKENS = 16384
RETRY_TOKENS = 32768
RASTER_ROUTES = ("raster-detector", "raster-ocr-labels", "raster-cv", "raster-fixtures")

# object -> policy, chosen on the dev split (see CHANGELOG.md, "Nemotron second opinion"): where our estimate is a
# room-label count (kitchens, saunas) Nemotron's own reading of the plan was right clearly more often than ours.
# For doors, windows, sinks and showers it was right less often, so those are not listed and stay as they are.
# "confidence" is the policy's measured dev precision, rounded down, used in place of the estimate's 0.3.
POLICY: dict[str, dict] = {
    "kitchen": {"ask": "A", "rule": "take_A", "confidence": 0.7},
    "sauna": {"ask": "A", "rule": "take_A", "confidence": 0.7},
}


def enabled() -> bool:
    return os.environ.get(ENV, "0").lower() in ("1", "on", "true", "yes") and N.enabled()


def prompt_a(question: str, size: tuple[int, int]) -> str:
    return (f"The image is a floor plan scan, {size[0]} x {size[1]} pixels.\nQuestion: {question}\n\n"
            'Reply with exactly this JSON: {"count": <integer>}')


def prompt_b(question: str, size: tuple[int, int], n: int) -> str:
    return (f"The image is a floor plan scan, {size[0]} x {size[1]} pixels. An automatic detector drew numbered red "
            f"boxes on it: {n} box(es). The detector is often wrong: it may have boxed things that are not there, "
            f"and missed others.\nQuestion: {question}\n\n"
            "Look at the plan yourself. Check each red box and look for objects without a box. Then give the correct count. "
            'Reply with exactly this JSON: {"count": <integer>, "boxes": [[x_min, y_min, x_max, y_max], ...]} '
            f"where boxes lists one box per counted object in the {size[0]} x {size[1]} image (origin top-left).")


def parse_reply(text: str | None):
    """(count or None, boxes or None) from the model's reply."""
    if not text:
        return None, None
    m = JSON_RE.search(text)
    if m:
        try:
            d = json.loads(m.group(0))
            c = d.get("count", d.get("value"))
            if isinstance(c, (int, float)) and c >= 0 and float(c) == int(c):
                return int(c), d.get("boxes")
            if isinstance(c, str) and c.strip().isdigit():
                return int(c), d.get("boxes")
        except (json.JSONDecodeError, AttributeError):
            pass
    m = re.search(r'"count"\s*:\s*(\d+)(?![\d.])', text)
    return (int(m.group(1)), None) if m else (None, None)


def draw_hint(img: Image.Image, boxes: list) -> Image.Image:
    from .evidence import font
    im = img.convert("RGB").copy()
    dr = ImageDraw.Draw(im)
    wid = max(2, round(max(im.size) / 450))
    ft = font(max(14, round(max(im.size) / 60)))
    for i, b in enumerate(boxes, 1):
        dr.rectangle(b, outline=(230, 20, 20), width=wid)
        dr.text((b[0] + 2, max(0, b[1] - wid - ft.size - 2)), str(i), fill=(230, 20, 20), font=ft)
    return im


def ask(variant: str, img: Image.Image, question: str, boxes: list | None = None) -> dict:
    """One second-opinion request. Returns the client's record plus count, boxes, sent size."""
    if variant == "B":
        png, sent, _ = N.prepare_image(draw_hint(img, boxes or []))
        p = prompt_b(question, sent, len(boxes or []))
    else:
        png, sent, _ = N.prepare_image(img)
        p = prompt_a(question, sent)
    res = N.chat(p, png, system=SYSTEM, max_tokens=MAX_TOKENS)
    retried = False
    if res.get("error") is None and (res.get("content") is None or res.get("finish_reason") == "length"):
        res, retried = N.chat(p, png, system=SYSTEM, max_tokens=RETRY_TOKENS), True
    cnt, bx = parse_reply(res.get("content"))
    return {**res, "count": cnt, "boxes": bx, "sent": list(sent), "retried": retried}


def decide(rule: str, ours: int, a: int | None, b: int | None) -> int | None:
    """Combine our count with Nemotron's (A and B may be None). Returns the final count, None = abstain."""
    if rule == "take_A":
        return a if a is not None else ours
    if rule == "take_B":
        return b if b is not None else ours
    if rule == "ours":
        return ours
    if rule == "agree_else_abstain_A":
        return ours if a == ours else None
    if rule == "agree_else_abstain_B":
        return ours if b == ours else None
    if rule == "AB_override":                       # both model readings agree on another count: take it
        return a if (a is not None and a == b) else ours
    raise ValueError(rule)


def maybe(ctx, query, ans: Answer) -> Answer:
    """Post-process an answer from the router. A no-op unless NEMO_SECOND is on and the answer qualifies."""
    if not (POLICY and ans.answered and ans.tier == "estimate" and getattr(ctx, "nemotron", False) and enabled()):
        return ans
    pol = POLICY.get(getattr(query, "obj", None))
    if pol is None or query.measure != "count" or not str(ans.route).startswith("raster"):
        return ans
    try:
        f = Path(ctx.file)
        img = Image.open(f).convert("RGB")
        boxes = [it["xyxy"] for it in ans.items if isinstance(it, dict) and it.get("xyxy")]
        ours = int(round(float(ans.value)))
        r = ask(pol["ask"], img, ctx.q.question, boxes)
    except Exception as ex:                          # a failed second opinion must not hide our answer
        ans.checks = list(ans.checks) + [{"name": "nemotron-second-opinion", "status": "error",
                                          "detail": f"{type(ex).__name__}: {ex}"}]
        return ans
    got = r["count"]
    if got is None:
        ans.checks = list(ans.checks) + [{"name": "nemotron-second-opinion", "status": "skipped",
                                          "detail": r.get("error") or "no usable count in the reply"}]
        return ans
    a_, b_ = (got, None) if pol["ask"] == "A" else (None, got)
    final = decide(pol["rule"], ours, a_, b_)
    ans.cost_usd += ((r.get("prompt_tokens") or 0) * N_PRICE_IN + (r.get("completion_tokens") or 0) * N_PRICE_OUT) / 1e6
    note = f"Nemotron ({pol['ask']}) counted {got}, our count {ours}"
    if final is None:
        ans.value, ans.abstain = None, f"{note}: they differ, so no answer is given"
        status = "abstained"
    elif final != ours:
        ans.value, ans.route = final, ans.route + "+nemotron"
        ans.explanation = f"{ans.explanation}; second opinion: {note}, taken"
        ans.items, ans.uncertain = [], []            # the boxes belonged to our count
        ans.confidence = pol.get("confidence", ans.confidence)
        estimate(ans, f"the count is Nemotron's reading of the plan ({got}), not our {ours}; the evidence image shows "
                      f"our room labels, not what Nemotron counted")
        status = "replaced"
    else:
        ans.explanation = f"{ans.explanation}; second opinion: {note}" + (", agree" if got == ours else ", kept ours")
        if got == ours:
            ans.confidence = max(ans.confidence, pol.get("confidence", ans.confidence))
        status = "agree" if got == ours else "kept"
    ans.checks = list(ans.checks) + [{"name": "nemotron-second-opinion", "status": status, "detail": note}]
    return ans


N_PRICE_IN = float(os.environ.get("NEMO_PRICE_IN", "3"))
N_PRICE_OUT = float(os.environ.get("NEMO_PRICE_OUT", "15"))
