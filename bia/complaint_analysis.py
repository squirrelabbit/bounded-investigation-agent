"""The contract the product path reads complaint arithmetic through.

Product code (controller, evidence, answer) sees only `ComplaintAnalysis`. The
implementation behind `analyze_complaints()` is still v1 `metrics.compute`, and
its result is returned as-is: no wrapper, so `as_dict()` shape, ordering and
list identity are exactly what they were before this seam existed.

No type checker runs in this repository, so the Protocol enforces nothing by
itself. `tests/test_complaint_analysis_seam.py` closes it: only this module may
import `bia.metrics`, and every attribute read on `EvidenceState.metrics` must be
one of `CONTRACT_MEMBERS`.
"""
from __future__ import annotations

from typing import Dict, List, Literal, Optional, Protocol, Sequence

from . import metrics
from .types import CellDelta, GroupDelta, MetricRow, Period

# Annotation only. The runtime values stay `types.DIM_PRODUCT` / `types.DIM_COMPLAINT_TYPE`.
ComplaintDimension = Literal["product", "complaint_type"]

CONTRACT_MEMBERS = frozenset(
    {
        "current_total",
        "baseline_total",
        "delta",
        "pct_change",
        "by_product",
        "by_complaint_type",
        "cells",
        "increased",
        "top",
        "as_dict",
    }
)


class ComplaintAnalysis(Protocol):
    current_total: int
    baseline_total: int
    delta: int
    pct_change: Optional[float]
    by_product: Sequence[GroupDelta]
    by_complaint_type: Sequence[GroupDelta]
    cells: Sequence[CellDelta]

    @property
    def increased(self) -> bool: ...

    def top(self, dimension: ComplaintDimension) -> List[GroupDelta]: ...

    def as_dict(self) -> Dict[str, object]: ...


def analyze_complaints(
    rows: List[MetricRow], current_window: Period, baseline_window: Period
) -> ComplaintAnalysis:
    return metrics.compute(rows, current_window, baseline_window)
