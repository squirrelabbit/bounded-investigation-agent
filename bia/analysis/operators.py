"""여섯 개로 닫힌 연산자. 일곱 번째가 필요하면 그것은 v2 확장이 아니다.

AGGREGATE·COMPARE·BREAKDOWN·CONTRIBUTION·RATE·RANK 이고, CONTRIBUTION 과 RATE 는
`decompose.py`·`engine.py` 에 있다. 목록과 구현 위치는 주석이 아니라
`tests/test_operator_closure.py` 가 고정한다 — 세 모듈에 함수를 더하면 그 테스트가
"이것이 일곱 번째인가" 를 먼저 묻는다.
"""
from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

from ..types import Period
from .decompose import FLOAT_TOL
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


def order_groups(groups, by: str, dimensions: Sequence[str]) -> List[object]:
    """RANK 의 정렬 그 자체. 순서가 필요한 곳은 전부 여기를 지난다.

    `by` 값 내림차순, 동률은 **canonical tie-break** 오름차순이다. canonical tie-break
    은 하나뿐이다 — 도메인이 `DomainSpec.dimensions` 로 **선언한 순서**대로 그룹 키의
    값을 본 튜플. 호출자가 고를 수 있는 여지를 두지 않는다. `value is None` 은 정렬
    키 0 으로 본다.

    왜 선언 순서인가. 근거는 결과가 아니라 의미다. 선언 순서는 도메인이 스스로 밝힌
    계약이고, 차원 이름 알파벳순은 구현 부산물이다. 알파벳순을 쓰면 `product` 를
    `sku` 로 **이름만 바꿔도** 출력 순서가 바뀐다 — 순서 계약이 식별자 철자에
    매달리면 그것은 계약이 아니다. 단일 차원 분해에서는 둘이 같아 드러나지 않고,
    교차 분해에서만 갈린다.

    `dimensions` 가 비면(overall 분기) tie-break 키는 빈 튜플이고 모든 그룹이 같은
    키를 갖는다. 그래도 정의는 온전하다 — 차원이 0개인 분해의 그룹은 전체 하나뿐이라
    tie-break 이 가를 것 자체가 없다. (엔진은 이 분기에서 ranking 을 싣지도 않는다.)

    동률 판정은 `|a - b| <= FLOAT_TOL` 이지 float 동일성이 아니다 — 수학적으로 같은
    두 기여도가 부동소수 표현 오차 1 ulp 로 갈리면 선언한 tie-break 이 아예 발동하지
    못한다. 이 결함은 실제로 있었고, 같은 로직을 복사한 자리마다 다시 나타난다.
    그래서 복사하지 않고 이 함수 하나에서만 나오게 한다.

    허용오차 동률은 추이적이지 않으므로(a≈b, b≈c 인데 a≉c) `sorted` 의 key 함수로
    표현할 수 없다. 값으로 1차 정렬한 뒤 인접한 것들을 묶고 묶음 안에서 tie-break 키로
    다시 정렬한다. 묶음 경계는 **묶음의 첫 원소(그 묶음의 최댓값)** 와 비교해 정한다 —
    직전 원소와 비교해 이어 붙이면 1e-9 씩 떨어진 값들이 사슬로 이어져 임의로 넓은
    묶음이 생긴다. 1차 정렬이 값과 tie-break 키만으로 전순서를 만들므로 묶음 경계는
    입력 순서에 의존하지 않는다.
    """
    dimensions = tuple(dimensions)

    def tie_break(group):
        return tuple(group.key[d] for d in dimensions)

    def value_of(group):
        value = getattr(group, by, None)
        return 0.0 if value is None else float(value)

    ordered = sorted(groups, key=lambda g: (-value_of(g), tie_break(g)))

    out: List[object] = []
    bucket: List[object] = []
    for group in ordered:
        if bucket and abs(value_of(bucket[0]) - value_of(group)) > FLOAT_TOL:
            out.extend(sorted(bucket, key=tie_break))
            bucket = []
        bucket.append(group)
    out.extend(sorted(bucket, key=tie_break))
    return out


def rank(groups, by: str, dimensions: Sequence[str]) -> Dict[str, object]:
    """RANK. 한 breakdown 안에서만 수행한다. 서로 다른 breakdown 을 섞지 않는다."""
    return {"by": by,
            "groups": [dict(g.key) for g in order_groups(groups, by, dimensions)]}
