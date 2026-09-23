"""plan 실행. 도메인 이름을 알지 못한다."""
from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

from ..integrity import dedupe_observations
from .compiler import ExecutionPlan, PlanBranch
from .decompose import decompose_ratio
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

    if plan.metric.kind == KIND_ADDITIVE:
        column = plan.metric.value
        comparison = compare(overall_current[column], overall_baseline[column])
    else:
        numerator = plan.metric.numerator
        denominator = plan.metric.denominator
        if overall_current[denominator] == 0 or overall_baseline[denominator] == 0:
            raise AnalysisRefused(
                "aggregate",
                "the overall denominator %r is zero in one of the periods "
                "(current=%d, baseline=%d); a rate is undefined there"
                % (denominator, overall_current[denominator], overall_baseline[denominator]),
            )
        current_rate = overall_current[numerator] / float(overall_current[denominator])
        baseline_rate = overall_baseline[numerator] / float(overall_baseline[denominator])
        delta = current_rate - baseline_rate
        comparison = {
            "current": current_rate,
            "baseline": baseline_rate,
            "delta": delta,
            "relative_change": (delta / baseline_rate) if baseline_rate else None,
        }

    result = StructuredAnalysisResult(
        metric={"name": plan.metric.name, "kind": plan.metric.kind},
        comparison=comparison,
    )
    for branch in plan.branches:
        result.breakdowns.append(
            _run_branch(plan, clean, branch)
        )
    return result


def _run_branch(
    plan: ExecutionPlan, rows: Sequence[Observation], branch: PlanBranch
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

    if plan.metric.kind == KIND_ADDITIVE:
        return _additive_branch(plan, branch, current, baseline, universe)
    return _ratio_branch(plan, branch, current, baseline, universe)


def _additive_branch(plan, branch, current, baseline, universe) -> BreakdownResult:
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


def _ratio_branch(plan, branch, current, baseline, universe) -> BreakdownResult:
    numerator = plan.metric.numerator
    denominator = plan.metric.denominator
    empty = {numerator: 0, denominator: 0}

    for key in universe:
        for period_name, bucket in (("current", current), ("baseline", baseline)):
            cell = bucket.get(key, empty)
            n_value, d_value = cell[numerator], cell[denominator]
            if d_value == 0 and n_value > 0:
                raise AnalysisRefused(
                    "integrity",
                    "group %s has %s=%d with %s=0 in the %s period; a numerator "
                    "without a denominator is impossible, and interpolating it "
                    "would invent a rate"
                    % (dict(zip(branch.dimensions, key)), numerator, n_value,
                       denominator, period_name),
                )
            if plan.metric.numerator_bounded_by_denominator and n_value > d_value:
                raise AnalysisRefused(
                    "integrity",
                    "group %s has %s=%d greater than %s=%d in the %s period, but "
                    "this metric declares the numerator is bounded by the denominator"
                    % (dict(zip(branch.dimensions, key)), numerator, n_value,
                       denominator, d_value, period_name),
                )

    total_current = {numerator: sum(v[numerator] for v in current.values()),
                     denominator: sum(v[denominator] for v in current.values())}
    total_baseline = {numerator: sum(v[numerator] for v in baseline.values()),
                      denominator: sum(v[denominator] for v in baseline.values())}

    groups, totals, flags = decompose_ratio(
        current, baseline, universe, numerator, denominator,
        total_current, total_baseline, branch.dimensions or ("__overall__",),
    )

    out = BreakdownResult(dimensions=branch.dimensions, cross=branch.cross, groups=groups)
    out.totals = totals
    out.flags = flags
    out.non_comparable_groups = [dict(g.key) for g in groups if not g.comparable]
    if branch.dimensions:
        out.ranking = rank(groups, plan.rank_by)
    return out
