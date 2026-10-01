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
from .adapters.complaints import legacy_views
from .analysis.compiler import compile_request
from .analysis.engine import run_plan
from .analysis.frame import Frame
from .analysis.qualification import ExecutableQualification, qualify
from .analysis.request import RANK_NET_CONTRIBUTION, AnalysisRequest, PeriodComparison
from .domains.complaints import SPEC as COMPLAINTS_SPEC
from .integrity import _row_to_observation
from .types import METRIC_COMPLAINT_COUNT, CellDelta, GroupDelta, Period

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
        "suppress_top_contributor",
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

    def suppress_top_contributor(self, dimension: ComplaintDimension) -> bool:
        """v2 `suppress_top_contributor` of that dimension's single-dimension breakdown.
        Read by the answer to decide whether the share of increase is shown. Not in
        `as_dict()`, which is the legacy/JEV serialization."""
        ...

    def as_dict(self) -> Dict[str, object]: ...


def _plan(current_window: Period, baseline_window: Period):
    # Declared order, not a re-spelled tuple: the adapter accepts the joint
    # breakdown only in `SPEC.dimensions` order.
    return compile_request(AnalysisRequest(
        domain=COMPLAINTS_SPEC.name,
        metric=METRIC_COMPLAINT_COUNT,
        breakdowns=tuple(COMPLAINTS_SPEC.dimensions),
        comparison=PeriodComparison(current=current_window, baseline=baseline_window),
        rank_by=RANK_NET_CONTRIBUTION,
    ))


def qualify_complaints(rows, current_period: Period, baseline_period: Period):
    """요청 구간으로 판정하고, 제품 경로가 직렬화하는 기존 두 객체를 함께 돌려준다."""
    qualification = qualify(_plan(current_period, baseline_period),
                            Frame.of(_row_to_observation(row) for row in rows))
    current, baseline, comparability = legacy_views(qualification)
    return qualification, current, baseline, comparability


def analyze_complaints(executable: ExecutableQualification) -> ComplaintAnalysis:
    return complaint_view(run_plan(executable))
