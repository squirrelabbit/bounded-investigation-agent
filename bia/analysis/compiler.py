"""AnalysisRequest + DomainSpec -> ExecutionPlan.

호출자도 모델도 연산자를 고르지 않는다. 가능한 plan 모양은 여기 고정돼 있고
Compiler 가 metric kind 와 요청으로 그중 하나를 결정론적으로 만든다. planner 가 아니다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from .errors import RequestError
from .registry import get_domain
from .request import RANK_BY_KIND, AnalysisRequest, PeriodComparison
from .spec import DomainSpec, MetricSpec


@dataclass(frozen=True)
class PlanBranch:
    """`dimensions` 가 비면 overall. `cross` 는 전체 joint 분기 하나에만 참이다."""

    dimensions: Tuple[str, ...]
    cross: bool


@dataclass(frozen=True)
class ExecutionPlan:
    domain: DomainSpec
    metric: MetricSpec
    comparison: PeriodComparison
    branches: Tuple[PlanBranch, ...]
    rank_by: str


def compile_request(request: AnalysisRequest) -> ExecutionPlan:
    domain = get_domain(request.domain)

    metric = domain.metrics.get(request.metric)
    if metric is None:
        raise RequestError(
            "domain %r has no metric %r; declared: %s"
            % (domain.name, request.metric, sorted(domain.metrics))
        )

    if not request.breakdowns:
        raise RequestError("at least one breakdown dimension is required")
    if len(set(request.breakdowns)) != len(request.breakdowns):
        raise RequestError("duplicate breakdown dimensions: %s" % (request.breakdowns,))
    unknown = [d for d in request.breakdowns if d not in domain.dimensions]
    if unknown:
        raise RequestError(
            "domain %r does not declare dimension(s) %s; declared: %s"
            % (domain.name, unknown, list(domain.dimensions))
        )

    allowed = RANK_BY_KIND[metric.kind]
    if request.rank_by not in allowed:
        raise RequestError(
            "rank_by=%r is not meaningful for a %s metric; allowed: %s"
            % (request.rank_by, metric.kind, list(allowed))
        )

    comparison = request.comparison
    if comparison.current.start <= comparison.baseline.end:
        raise RequestError("current period must start after the baseline period ends")

    branches = [PlanBranch(dimensions=(), cross=False)]
    for dimension in request.breakdowns:
        branches.append(PlanBranch(dimensions=(dimension,), cross=False))
    if len(request.breakdowns) > 1:
        branches.append(PlanBranch(dimensions=tuple(request.breakdowns), cross=True))

    return ExecutionPlan(
        domain=domain, metric=metric, comparison=comparison,
        branches=tuple(branches), rank_by=request.rank_by,
    )
