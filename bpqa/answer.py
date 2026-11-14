"""Answer record shared by all routes."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field


@dataclass
class Answer:
    value: object = None
    unit: str = ""
    route: str = ""
    confidence: float = 0.0
    evidence: str | None = None          # path to PNG overlay
    explanation: str = ""                # one-line human-readable derivation
    items: list = field(default_factory=list)   # what was counted / summed (ids, labels, values)
    cost_usd: float = 0.0                # 0 for every no-model route
    abstain: str | None = None           # reason, when no answer is given
    checks: list = field(default_factory=list)  # consistency checks run on the answer (bpqa/checks.py)
    # "proven": the route's tuned conditions for a provable answer held; "estimate": the route's best
    # answer when they did not (tier_reason says why, `uncertain` lists the items to check by eye)
    tier: str | None = None
    tier_reason: str = ""
    uncertain: list = field(default_factory=list)
    # an abstention after the route read what the drawing carries and found that it does not hold the
    # answer (e.g. bedrooms on a plan whose rooms are all labelled generically): the router stops here
    # instead of handing the question to a model that would have to guess
    final: bool = False
    # the condition a scan route met, used to look up its measured precision (bpqa/calibration.py), e.g.
    # "clear" for a detector count whose views all agree with no box near the threshold; "" for none
    tier_cell: str = ""
    # an area approximated from a door or object scale on a scan that prints no areas (raster_area, scale_objects):
    # the answer sentence, the scale source and objects, and the error band, for the app to show where it comes from
    approx: dict | None = None

    @property
    def answered(self) -> bool:
        return self.abstain is None and self.value is not None

    def as_dict(self) -> dict:
        d = asdict(self)
        d["items"] = d["items"][:200]
        d["uncertain"] = d["uncertain"][:200]
        return d


def estimate(ans: Answer, reason: str) -> Answer:
    """Demote an answer to the estimate tier, keeping every earlier reason."""
    ans.tier = "estimate"
    ans.tier_reason = f"{ans.tier_reason}; {reason}" if ans.tier_reason else reason
    return ans


def abstain(route: str, reason: str) -> Answer:
    return Answer(route=route, abstain=reason)


# ---------------------------------------------------------------- what the app shows
# Reasons are written for the log and for tests: they name env flags, exception classes and model files. The app
# shows `public_reason(...)`, the same sentence with those parts removed or put in plain words.
_INTERNAL = re.compile(
    r"\bNEMO_\w+|\bSTOA_\w+|\bNVIDIA_\w+|\b[A-Z][A-Z0-9]*_[A-Z0-9_]{2,}=\S*|Traceback|\b[A-Z]\w*(?:Error|Exception)\b|"
    r"\bInvalid device\b|\bCUDA\b|\.pt\b|\btools/\w+\.py")
_PLAIN = [
    (re.compile(r"image estimate switched off|NEMO_IMAGE_ESTIMATE", re.I), ""),
    (re.compile(r"model call failed|invalid model output", re.I), "the image model could not give a usable reading"),
    (re.compile(r"detector model missing|image unreadable", re.I), "the symbol detector could not read this image"),
    (re.compile(r"fixture model not trained|no .* head in the fixture model", re.I), "fixtures cannot be counted on scans here"),
]


def public_reason(reason: str | None) -> str | None:
    """A reason fit for the app: internal settings, exception text and file names of models are dropped or put in
    plain words; what the drawing does or does not carry is kept. Keeps `None` and empty as they are. The raw
    reason belongs in the log."""
    if not reason:
        return reason
    out = []
    for part in re.split(r";\s+", reason):
        msg = part.split(": ", 1)[1] if ": " in part else part
        route = part.split(": ", 1)[0] if ": " in part else ""
        plain = next((p for rx, p in _PLAIN if rx.search(part)), None)
        if plain is not None:
            text = plain
        elif re.match(r"error\b", msg, re.I) or _INTERNAL.search(msg):
            text = "this reading could not be completed for this drawing"
        else:
            out.append(part)
            continue
        if text:
            out.append(f"{route}: {text}" if route and " " not in route else text)
    seen, uniq = set(), []
    for p in out:
        k = p.split(": ", 1)[-1]
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    return "; ".join(uniq) or "No route could answer this from the drawing."
