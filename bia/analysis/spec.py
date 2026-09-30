"""도메인이 코드에 선언하는 정적 계약. 런타임 요청과 다른 타입이다."""
from __future__ import annotations

import types
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

from .errors import SpecError

KIND_ADDITIVE = "additive"
KIND_RATIO = "ratio"
KINDS = (KIND_ADDITIVE, KIND_RATIO)

POLICY_REJECT = "reject"
POLICY_ALIGN = "align_common_window"
PARTIAL_PERIOD_POLICIES = (POLICY_REJECT, POLICY_ALIGN)
NULL_REJECT = "reject"
NULL_UNKNOWN_GROUP = "unknown_group"
NULL_DIMENSION_POLICIES = (NULL_REJECT, NULL_UNKNOWN_GROUP)


@dataclass(frozen=True)
class MetricSpec:
    name: str
    kind: str
    value: Optional[str] = None
    numerator: Optional[str] = None
    denominator: Optional[str] = None
    numerator_bounded_by_denominator: bool = False

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise SpecError("metric %r: kind must be one of %s" % (self.name, KINDS))
        if self.kind == KIND_ADDITIVE:
            if not self.value:
                raise SpecError("metric %r: additive needs `value`" % self.name)
            if self.numerator or self.denominator:
                raise SpecError("metric %r: additive must not carry ratio columns" % self.name)
        else:
            if not (self.numerator and self.denominator):
                raise SpecError(
                    "metric %r: ratio needs both `numerator` and `denominator`" % self.name
                )
            if self.value:
                raise SpecError("metric %r: ratio must not carry `value`" % self.name)

    @property
    def columns(self) -> Tuple[str, ...]:
        if self.kind == KIND_ADDITIVE:
            return (self.value,)
        return (self.numerator, self.denominator)


@dataclass(frozen=True)
class DomainSpec:
    """`grain` 은 한 행의 물리적 최소 단위이고 `dimensions` 는 분석 축이다.
    둘은 같은 목록일 수 있으나 용도가 다르다 — 중복 검사는 언제나 grain 기준이다."""

    name: str
    grain: Tuple[str, ...]
    dimensions: Tuple[str, ...]
    metrics: Dict[str, MetricSpec] = field(default_factory=dict)
    partial_period_policy: str = POLICY_REJECT
    min_comparable_days: Optional[int] = None
    min_comparable_ratio: Optional[float] = None
    null_dimension_policy: str = NULL_REJECT

    def __post_init__(self) -> None:
        object.__setattr__(self, "grain", tuple(self.grain))
        object.__setattr__(self, "dimensions", tuple(self.dimensions))
        if not self.grain or self.grain[0] != "day":
            raise SpecError("domain %r: grain must start with 'day'" % self.name)
        extra = [d for d in self.dimensions if d not in self.grain]
        if extra:
            raise SpecError("domain %r: dimensions not in grain: %s" % (self.name, extra))
        for key, metric in self.metrics.items():
            if key != metric.name:
                raise SpecError(
                    "domain %r: metric key %r does not match its name %r"
                    % (self.name, key, metric.name)
                )
        # frozen 은 얕은 불변이다. dict 는 안에서 바뀌므로 읽기 전용 매핑으로 바꾼다.
        object.__setattr__(self, "metrics", types.MappingProxyType(dict(self.metrics)))
        if self.partial_period_policy not in PARTIAL_PERIOD_POLICIES:
            raise SpecError("domain %r: partial_period_policy must be one of %s"
                            % (self.name, PARTIAL_PERIOD_POLICIES))
        if self.null_dimension_policy not in NULL_DIMENSION_POLICIES:
            raise SpecError("domain %r: null_dimension_policy must be one of %s"
                            % (self.name, NULL_DIMENSION_POLICIES))
        thresholds = (self.min_comparable_days, self.min_comparable_ratio)
        if self.partial_period_policy == POLICY_REJECT:
            if any(t is not None for t in thresholds):
                raise SpecError("domain %r: min_comparable_* have no effect under 'reject'"
                                % self.name)
        else:
            if any(t is None for t in thresholds):
                raise SpecError("domain %r: 'align_common_window' needs both "
                                "min_comparable_days and min_comparable_ratio" % self.name)
            days = self.min_comparable_days
            if isinstance(days, bool) or not isinstance(days, int) or days < 1:
                raise SpecError("domain %r: min_comparable_days must be an integer >= 1" % self.name)
            ratio = self.min_comparable_ratio
            if isinstance(ratio, bool) or not isinstance(ratio, (int, float)):
                raise SpecError("domain %r: min_comparable_ratio must be a number" % self.name)
            if not (0 < ratio <= 1):
                raise SpecError("domain %r: min_comparable_ratio must be in (0, 1]" % self.name)

    @property
    def grain_dimensions(self) -> Tuple[str, ...]:
        return tuple(d for d in self.grain if d != "day")
