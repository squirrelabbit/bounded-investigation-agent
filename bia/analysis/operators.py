"""여섯 개로 닫힌 연산자. 일곱 번째가 필요하면 그것은 v2 확장이 아니다."""
from __future__ import annotations

from typing import Dict, Sequence, Tuple

from ..types import Period
from .frame import Observation

CROSS_CELL_LIMIT = 1000


def aggregate(
    rows: Sequence[Observation], window: Period, columns: Sequence[str],
    dimensions: Sequence[str],
) -> Dict[Tuple[str, ...], Dict[str, int]]:
    """AGGREGATE. `dimensions` 가 비면 전체 하나로 모은다."""
    out: Dict[Tuple[str, ...], Dict[str, int]] = {}
    for row in rows:
        if not window.contains(row.day):
            continue
        key = tuple(row.key_of(d) for d in dimensions)
        bucket = out.setdefault(key, {c: 0 for c in columns})
        for column in columns:
            bucket[column] += row.measure_of(column)
    return out


def compare(current: int, baseline: int) -> Dict[str, object]:
    """COMPARE. `relative_change` 는 nullable 파생값이다."""
    delta = current - baseline
    relative = (delta / float(baseline)) if baseline else None
    return {"current": current, "baseline": baseline,
            "delta": delta, "relative_change": relative}


def group_universe(
    current: Dict[Tuple[str, ...], Dict[str, int]],
    baseline: Dict[Tuple[str, ...], Dict[str, int]],
) -> Tuple[Tuple[str, ...], ...]:
    """BREAKDOWN 의 그룹 우주. 두 기간 키의 **합집합**이다.

    한 기간만 보면 실제 분해 대상 수를 과소평가한다. 한쪽에 없는 그룹은
    '유효 기간 안의 부재 = 활동 0' 으로 해석한다.
    """
    return tuple(sorted(set(current) | set(baseline)))


def rank(groups, by: str) -> Dict[str, object]:
    """RANK. 한 breakdown 안에서만 수행한다. 서로 다른 breakdown 을 섞지 않는다."""
    def key(group):
        value = getattr(group, by, None)
        return (0 if value is None else -value, sorted(group.key.items()))

    ordered = sorted(groups, key=key)
    return {"by": by, "groups": [dict(g.key) for g in ordered]}
