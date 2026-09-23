"""공용 산출물. answer 가 아니라 typed analytical result 다."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

STATUS_OK = "ok"
STATUS_OMITTED = "omitted"


@dataclass
class GroupResult:
    key: Dict[str, str]
    net_contribution: float
    comparable: bool = True
    group_delta: Optional[int] = None
    rate_effect: Optional[float] = None
    mix_effect: Optional[float] = None
    contribution_share: Optional[float] = None

    def as_dict(self) -> Dict[str, object]:
        out: Dict[str, object] = {"key": dict(self.key),
                                  "net_contribution": self.net_contribution,
                                  "comparable": self.comparable}
        if self.group_delta is not None:
            out["group_delta"] = self.group_delta
        if self.rate_effect is not None:
            out["rate_effect"] = self.rate_effect
        if self.mix_effect is not None:
            out["mix_effect"] = self.mix_effect
        if self.contribution_share is not None:
            out["contribution_share"] = self.contribution_share
        return out


@dataclass
class BreakdownResult:
    dimensions: Tuple[str, ...]
    cross: bool
    groups: List[GroupResult] = field(default_factory=list)
    totals: Dict[str, float] = field(default_factory=dict)
    ranking: Dict[str, object] = field(default_factory=dict)
    flags: Dict[str, bool] = field(default_factory=dict)
    non_comparable_groups: List[Dict[str, str]] = field(default_factory=list)
    status: str = STATUS_OK
    reason: Optional[str] = None
    observed_cells: Optional[int] = None

    def as_dict(self) -> Dict[str, object]:
        out: Dict[str, object] = {
            "dimensions": list(self.dimensions), "cross": self.cross,
            "status": self.status,
        }
        if self.status == STATUS_OMITTED:
            out["reason"] = self.reason
            out["observed_cells"] = self.observed_cells
            return out
        out["groups"] = [g.as_dict() for g in self.groups]
        out["totals"] = dict(self.totals)
        out["ranking"] = dict(self.ranking)
        out["flags"] = dict(self.flags)
        out["non_comparable_groups"] = [dict(k) for k in self.non_comparable_groups]
        return out


@dataclass
class StructuredAnalysisResult:
    metric: Dict[str, str]
    comparison: Dict[str, object]
    breakdowns: List[BreakdownResult] = field(default_factory=list)

    def as_dict(self) -> Dict[str, object]:
        return {"metric": dict(self.metric), "comparison": dict(self.comparison),
                "breakdowns": [b.as_dict() for b in self.breakdowns]}
