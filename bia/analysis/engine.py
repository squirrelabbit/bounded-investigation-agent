"""plan 실행. 도메인 이름을 알지 못한다."""
from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

from ..integrity import dedupe_observations
from .compiler import ExecutionPlan, PlanBranch
from .errors import AnalysisRefused
from .frame import Observation
from .operators import CROSS_CELL_LIMIT, aggregate, compare, group_universe, rank
from .result import (STATUS_OMITTED, BreakdownResult, GroupResult,
                     StructuredAnalysisResult)
from .spec import KIND_ADDITIVE


def run_plan(plan: ExecutionPlan, rows: Sequence[Observation]) -> StructuredAnalysisResult:
    clean, _, conflicts = dedupe_observations(rows, plan.domain.grain)
    if conflicts:
        raise AnalysisRefused(
            "integrity",
            "conflicting duplicate rows at grain %s: %s" % (list(plan.domain.grain), conflicts),
        )

    columns = plan.metric.columns
    current_all = aggregate(clean, plan.comparison.current, columns, ())
    baseline_all = aggregate(clean, plan.comparison.baseline, columns, ())
    overall_current = current_all.get((), {c: 0 for c in columns})
    overall_baseline = baseline_all.get((), {c: 0 for c in columns})

    if plan.metric.kind != KIND_ADDITIVE:
        raise AnalysisRefused("compile", "ratio metrics arrive in a later task")

    column = plan.metric.value
    comparison = compare(overall_current[column], overall_baseline[column])

    result = StructuredAnalysisResult(
        metric={"name": plan.metric.name, "kind": plan.metric.kind},
        comparison=comparison,
    )
    for branch in plan.branches:
        result.breakdowns.append(
            _run_branch(plan, clean, branch, comparison["delta"])
        )
    return result


def _run_branch(
    plan: ExecutionPlan, rows: Sequence[Observation], branch: PlanBranch, total_delta: int
) -> BreakdownResult:
    columns = plan.metric.columns
    current = aggregate(rows, plan.comparison.current, columns, branch.dimensions)
    baseline = aggregate(rows, plan.comparison.baseline, columns, branch.dimensions)
    universe = group_universe(current, baseline)

    if branch.cross and len(universe) > CROSS_CELL_LIMIT:
        return BreakdownResult(
            dimensions=branch.dimensions, cross=branch.cross,
            status=STATUS_OMITTED, reason="cross_cell_limit_exceeded",
            observed_cells=len(universe),
        )

    column = plan.metric.value
    groups: List[GroupResult] = []
    for key in universe:
        cur = current.get(key, {column: 0})[column]
        base = baseline.get(key, {column: 0})[column]
        delta = cur - base
        groups.append(GroupResult(
            key=dict(zip(branch.dimensions, key)),
            net_contribution=float(delta), group_delta=delta, comparable=True,
        ))

    out = BreakdownResult(dimensions=branch.dimensions, cross=branch.cross, groups=groups)
    out.totals = {"gross_movement": sum(abs(g.net_contribution) for g in groups)}
    out.flags = {"decomposition_complete": True, "composition_dominant": False,
                 "simpson_strict": False, "heavy_cancellation": False,
                 "suppress_top_contributor": False}
    if branch.dimensions:
        out.ranking = rank(groups, plan.rank_by)
    return out
