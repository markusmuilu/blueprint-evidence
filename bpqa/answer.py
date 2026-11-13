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

    @property
    def answered(self) -> bool:
        return self.abstain is None and self.value is not None

    def as_dict(self) -> dict:
        d = asdict(self)
        d["items"] = d["items"][:200]
        return d


def abstain(route: str, reason: str) -> Answer:
    return Answer(route=route, abstain=reason)
