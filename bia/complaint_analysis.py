"""The contract the product path reads complaint arithmetic through.

Product code (controller, evidence, answer) sees only `ComplaintAnalysis`. The
implementation behind `analyze_complaints()` is the v2 engine: the complaints
domain request is compiled and run, and the complaints adapter turns the
`StructuredAnalysisResult` into the v1-compatible view.

`bia/metrics.py` is not on this path. It stays in the repository as the
independent v1 reference the compatibility tests compare against. There is no fallback to it:
`AnalysisRefused` and `ComplaintAnalysisCompatibilityError` propagate as they are.

No type checker runs in this repository, so the Protocol enforces nothing by
itself. `tests/test_complaint_analysis_seam.py` closes it: no product module may
import `bia.metrics`, and every attribute read on `EvidenceState.metrics` must be
one of `CONTRACT_MEMBERS`.
"""
from __future__ import annotations

from typing import Dict, List, Literal, Optional, Protocol, Sequence

from .adapters.complaints import complaint_view
from .analysis.compiler import compile_request
from .analysis.engine import run_plan
from .analysis.request import RANK_NET_CONTRIBUTION, AnalysisRequest, PeriodComparison
from .domains.complaints import SPEC as COMPLAINTS_SPEC
from .integrity import _row_to_observation
from .types import METRIC_COMPLAINT_COUNT, CellDelta, GroupDelta, MetricRow, Period

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
    # Declared order, not a re-spelled tuple: the adapter accepts the joint
    # breakdown only in `SPEC.dimensions` order.
    plan = compile_request(AnalysisRequest(
        domain=COMPLAINTS_SPEC.name,
        metric=METRIC_COMPLAINT_COUNT,
        breakdowns=tuple(COMPLAINTS_SPEC.dimensions),
        comparison=PeriodComparison(current=current_window, baseline=baseline_window),
        rank_by=RANK_NET_CONTRIBUTION,
    ))
    result = run_plan(plan, [_row_to_observation(row) for row in rows])
    return complaint_view(result)
