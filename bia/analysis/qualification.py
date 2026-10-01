"""정형 데이터 판정. 사실을 관측하고 도메인 정책으로 실행 가능 범위를 결정한다.

산술 엔진은 이 결정의 존재를 요구하지만 결정을 만드는 책임은 갖지 않는다. 엔진은 판정을
통과한 바로 그 계획·데이터·구간만 실행한다 — `ExecutableQualification` 이 그 봉인이다.
도메인 이름과 제품 문구를 알지 못한다.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple, Union

from ..integrity import dedupe_observations
from ..types import Period
from .compiler import ExecutionPlan
from .errors import AnalysisRefused
from .frame import Frame, Observation, observation_key
from .request import PeriodComparison
from .spec import NULL_REJECT, POLICY_REJECT

ACTION_ACCEPT = "accept"
ACTION_ALIGN = "align"

REASON_MISSING_REQUIRED_VALUE = "missing_required_value"
REASON_CONFLICTING_DUPLICATE = "conflicting_duplicate"
REASON_EMPTY_PERIOD = "empty_period"
REASON_INCOMPLETE_PERIOD_COVERAGE = "incomplete_period_coverage"
REASON_INSUFFICIENT_COMMON_WINDOW = "insufficient_common_window"
WINDOW_NO_OVERLAP = "no_overlap"
WINDOW_BELOW_MINIMUM = "below_minimum"

SCOPE_CURRENT = "current"
SCOPE_BASELINE = "baseline"
SCOPE_OUTSIDE = "outside"

Detail = Tuple[Tuple[str, object], ...]


@dataclass(frozen=True)
class PeriodCoverage:
    period: Period
    observed_offsets: Tuple[int, ...]
    duplicate_rows_removed: int

    @property
    def expected_days(self) -> int:
        return self.period.days

    @property
    def observed_days(self) -> int:
        return len(self.observed_offsets)

    @property
    def missing_days(self) -> Tuple[str, ...]:
        seen = set(self.observed_offsets)
        return tuple(d.isoformat() for i, d in enumerate(self.period.dates()) if i not in seen)

    @property
    def complete(self) -> bool:
        """요청 구간의 모든 날짜가 최소 한 행 이상 관측됐다. 행 수·grain 수와 무관하다."""
        return self.observed_days == self.expected_days


@dataclass(frozen=True)
class IntegrityViolation:
    code: str
    scope: str
    grain_key: Tuple[str, ...]
    caused_by_null_normalization: bool

    @property
    def label(self) -> str:
        return "|".join(self.grain_key)


@dataclass(frozen=True)
class QualificationFacts:
    current: PeriodCoverage
    baseline: PeriodCoverage
    violations: Tuple[IntegrityViolation, ...]
    null_profile: Tuple[Tuple[str, int], ...]

    def conflicts_in(self, scope: str) -> Tuple[IntegrityViolation, ...]:
        return tuple(v for v in self.violations if v.scope == scope)


@dataclass(frozen=True)
class RejectedQualification:
    reason_code: str
    detail: Detail
    facts: QualificationFacts
    plan: ExecutionPlan

    def as_refusal(self) -> AnalysisRefused:
        """엔진 직접 호출자용. 충돌은 v2.0 의 run_plan 과 같은 stage·문장으로 알린다."""
        if self.reason_code == REASON_CONFLICTING_DUPLICATE:
            labels = sorted(v.label for v in self.facts.violations if v.scope != SCOPE_OUTSIDE)
            return AnalysisRefused(
                "integrity",
                "conflicting duplicate rows at grain %s: %s" % (list(self.plan.domain.grain), labels),
            )
        return AnalysisRefused("qualification", "%s: %s" % (self.reason_code, dict(self.detail)))


_FACTORY = object()


class ExecutableQualification:
    """판정을 통과한 plan·frame·유효 구간의 봉인. `qualify` 만 만든다. 만든 뒤에는 바꿀 수 없다."""

    __slots__ = ("_plan", "_frame", "_effective", "_effective_plan", "_action", "_facts", "_detail")

    def __init__(self, token, plan, frame, effective, action, facts, detail):
        if token is not _FACTORY:
            raise TypeError("ExecutableQualification is issued only by qualify()")
        for name, value in (("_plan", plan), ("_frame", frame), ("_effective", effective),
                            ("_action", action), ("_facts", facts), ("_detail", detail)):
            object.__setattr__(self, name, value)
        object.__setattr__(self, "_effective_plan",
                           dataclasses.replace(plan, comparison=effective))

    def __setattr__(self, name, value):
        raise AttributeError("ExecutableQualification is sealed")

    plan = property(lambda self: self._plan)
    frame = property(lambda self: self._frame)
    effective = property(lambda self: self._effective)
    effective_plan = property(lambda self: self._effective_plan)
    action = property(lambda self: self._action)
    facts = property(lambda self: self._facts)
    detail = property(lambda self: self._detail)

    @property
    def requested(self) -> PeriodComparison:
        return self._plan.comparison


def qualify(plan: ExecutionPlan, frame: Frame) -> Union[RejectedQualification, ExecutableQualification]:
    if plan.comparison.current.start <= plan.comparison.baseline.end:
        # compile_request 가 이미 막는 모양이다. 손으로 만든 plan 의 프로그래밍 오류다.
        raise ValueError("current period must start after the baseline period ends: "
                         "current starts %s, baseline ends %s"
                         % (plan.comparison.current.start, plan.comparison.baseline.end))
    facts = _observe(plan, frame)
    return _decide(plan, frame, facts)


def _observe(plan: ExecutionPlan, frame: Frame) -> QualificationFacts:
    grain = plan.domain.grain
    windows = ((SCOPE_CURRENT, plan.comparison.current), (SCOPE_BASELINE, plan.comparison.baseline))
    buckets: Dict[str, List[Observation]] = {SCOPE_CURRENT: [], SCOPE_BASELINE: [], SCOPE_OUTSIDE: []}
    for row in frame.rows:
        scope = SCOPE_OUTSIDE
        for name, period in windows:
            if period.contains(row.day):
                scope = name
                break
        buckets[scope].append(row)

    violations: List[IntegrityViolation] = []
    coverage: Dict[str, PeriodCoverage] = {}
    for scope in (SCOPE_CURRENT, SCOPE_BASELINE, SCOPE_OUTSIDE):
        rows = buckets[scope]
        clean, duplicates, conflicts = dedupe_observations(rows, grain)
        keyed: Dict[str, Tuple[str, ...]] = {}
        null_keys = set()
        plain_measures: Dict[Tuple[str, ...], set] = {}
        for r in rows:
            k = observation_key(r, grain)
            keyed.setdefault("|".join(k), k)
            if r.null_dimensions:
                null_keys.add(k)
            else:
                plain_measures.setdefault(k, set()).add(r.measures)
        for label in conflicts:
            key = keyed[label]
            # 빈칸 없는 행끼리 이미 충돌하면 정규화가 만든 충돌이 아니다.
            from_nulls = key in null_keys and len(plain_measures.get(key, ())) < 2
            violations.append(IntegrityViolation(REASON_CONFLICTING_DUPLICATE, scope, key, from_nulls))
        if scope != SCOPE_OUTSIDE:
            period = dict(windows)[scope]
            offsets = tuple(sorted({(r.day - period.start).days for r in clean}))
            coverage[scope] = PeriodCoverage(period, offsets, duplicates)

    nulls: Dict[str, int] = {}
    for scope in (SCOPE_CURRENT, SCOPE_BASELINE):
        for row in buckets[scope]:
            for name in row.null_dimensions:
                nulls[name] = nulls.get(name, 0) + 1

    return QualificationFacts(
        current=coverage[SCOPE_CURRENT],
        baseline=coverage[SCOPE_BASELINE],
        violations=tuple(violations),
        null_profile=tuple(sorted(nulls.items())),
    )


def _decide(plan, frame, facts):
    domain = plan.domain

    def reject(code: str, detail: Detail = ()) -> RejectedQualification:
        return RejectedQualification(code, detail, facts, plan)

    def execute(action: str, effective: PeriodComparison, detail: Detail = ()) -> ExecutableQualification:
        return ExecutableQualification(_FACTORY, plan, frame, effective, action, facts, detail)

    # 1. 빈 값 정책 reject — 정규화·충돌 판정으로 가지 않는다
    if domain.null_dimension_policy == NULL_REJECT and facts.null_profile:
        return reject(REASON_MISSING_REQUIRED_VALUE,
                      (("dimensions", tuple(name for name, _ in facts.null_profile)),))
    # 2. 요청 구간 안의 grain 충돌 — 정렬보다 먼저
    if facts.conflicts_in(SCOPE_CURRENT) or facts.conflicts_in(SCOPE_BASELINE):
        return reject(REASON_CONFLICTING_DUPLICATE)
    current, baseline = facts.current, facts.baseline
    # 3. 한쪽 기간이 비었다
    if current.observed_days == 0 or baseline.observed_days == 0:
        return reject(REASON_EMPTY_PERIOD)
    # 4. 두 기간 모두 완전하고 길이가 같다
    if current.complete and baseline.complete and current.expected_days == baseline.expected_days:
        return execute(ACTION_ACCEPT, plan.comparison)
    # 5. 부분 기간 정책 reject
    if domain.partial_period_policy == POLICY_REJECT:
        return reject(REASON_INCOMPLETE_PERIOD_COVERAGE,
                      (("current_missing", len(current.missing_days)),
                       ("baseline_missing", len(baseline.missing_days))))
    # 6~8. align_common_window — 달력 교집합이 아니라 시작일 기준 오프셋의 가장 긴 공통 연속 구간
    limit = min(current.expected_days, baseline.expected_days)
    start, end = _longest_common_run(current.observed_offsets, baseline.observed_offsets, limit)
    if start < 0:
        return reject(REASON_INSUFFICIENT_COMMON_WINDOW, (("kind", WINDOW_NO_OVERLAP),))
    length = end - start + 1
    # round 는 파이썬의 짝수 반올림이다: limit 15 → 8, limit 29 → 14. 기존 의미를 보존하는 계약.
    threshold = max(domain.min_comparable_days, int(round(domain.min_comparable_ratio * limit)))
    if length < threshold:
        return reject(REASON_INSUFFICIENT_COMMON_WINDOW,
                      (("kind", WINDOW_BELOW_MINIMUM), ("length", length), ("threshold", threshold)))
    effective = PeriodComparison(current=current.period.sub(start, end),
                                 baseline=baseline.period.sub(start, end))
    return execute(ACTION_ALIGN, effective, (("start_offset", start), ("end_offset", end)))


def _longest_common_run(a: Sequence[int], b: Sequence[int], limit: int) -> Tuple[int, int]:
    """두 기간에 모두 있는 날짜 오프셋 중 `limit` 미만에서 가장 긴 연속 구간. 없으면 (-1, -1).
    같은 길이가 여럿이면 먼저 나온 것. legacy 의 같은 이름 함수와 이중 실행으로 대조된다."""
    common = sorted(set(a) & set(b) & set(range(limit)))
    best: Tuple[int, int] = (-1, -1)
    best_len = 0
    run_start: Optional[int] = None
    prev: Optional[int] = None
    for offset in common:
        if run_start is None:
            run_start = offset
        elif prev is not None and offset != prev + 1:
            if prev - run_start + 1 > best_len:
                best_len = prev - run_start + 1
                best = (run_start, prev)
            run_start = offset
        prev = offset
    if run_start is not None and prev is not None and prev - run_start + 1 > best_len:
        best = (run_start, prev)
    return best
