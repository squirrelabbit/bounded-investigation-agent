"""도메인이 코드에 선언하는 정적 계약. 런타임 요청과 다른 타입이다."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

from .errors import SpecError

KIND_ADDITIVE = "additive"
KIND_RATIO = "ratio"
KINDS = (KIND_ADDITIVE, KIND_RATIO)


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

    def __post_init__(self) -> None:
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

    @property
    def grain_dimensions(self) -> Tuple[str, ...]:
        return tuple(d for d in self.grain if d != "day")
