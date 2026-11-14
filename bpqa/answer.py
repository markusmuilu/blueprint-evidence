"""Answer record shared by all routes."""
from __future__ import annotations

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
