"""비율 metric 의 기여도 분해와 출력 정책.

원본은 `net_contribution = N1/D1 - N0/D0` 이다. 이것은 그룹의 분자 점유 변화이고
`D_g = 0` 이어도 정의되므로 가법성이 어떤 경우에도 깨지지 않는다.
rate/mix 는 같은 숫자를 쪼갠 것이며, 양 기간 분모가 있을 때만 정의된다.
"""
from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

from .result import GroupResult

CANCELLATION_THRESHOLD = 0.20
SHARE_EPSILON = 0.0001
FLOAT_TOL = 1e-9
GROSS_EPSILON = 1e-12


def sign_with_tol(value: float) -> int:
    """`|x| <= FLOAT_TOL` 이면 0. 정확한 `x != 0` 은 1e-16 노이즈에 뒤집힌다."""
    if abs(value) <= FLOAT_TOL:
        return 0
    return 1 if value > 0 else -1


def snap_share_boundary(candidate: float) -> float:
    """`0 < share <= 1` 판정의 두 경계를 FLOAT_TOL 로 본다.

    엔진의 candidate 는 float 이라 수학적으로 딱 1 인 기여율이 표현 오차 1 ulp 로
    `>1` 이 될 수 있고, 그러면 엔진만 share 를 버려 정확 산술과 결론이 갈린다.
    선언한 규칙이 표현 오차로 뒤집혀서는 안 되므로 1 에서 FLOAT_TOL 이내는 정확히
    `1.0` 으로, 0 에서 FLOAT_TOL 이내는 `0.0` 으로 스냅한다. 스냅 뒤에도 비교는
    그대로 `0 < c <= 1` 이므로 진짜로 1 을 넘는 값은 여전히 버려지고(100% 를 넘는
    기여율을 보이지 않는다), 0 인 값은 여전히 share 를 내지 않는다.
    """
    if abs(candidate - 1.0) <= FLOAT_TOL:
        return 1.0
    if abs(candidate) <= FLOAT_TOL:
        return 0.0
    return candidate


def decompose_ratio(
    current: Dict[Tuple[str, ...], Dict[str, int]],
    baseline: Dict[Tuple[str, ...], Dict[str, int]],
    universe: Sequence[Tuple[str, ...]],
    numerator: str,
    denominator: str,
    total_current: Dict[str, int],
    total_baseline: Dict[str, int],
    dimensions: Sequence[str] = ("dim",),
) -> Tuple[List[GroupResult], Dict[str, float], Dict[str, bool]]:
    d1 = float(total_current[denominator])
    d0 = float(total_baseline[denominator])
    delta_r = (total_current[numerator] / d1 if d1 else 0.0) - (
        total_baseline[numerator] / d0 if d0 else 0.0
    )

    groups: List[GroupResult] = []
    total_rate = 0.0
    total_mix = 0.0
    entry_exit = 0.0

    for key in universe:
        cur = current.get(key, {numerator: 0, denominator: 0})
        base = baseline.get(key, {numerator: 0, denominator: 0})
        n1, dg1 = float(cur[numerator]), float(cur[denominator])
        n0, dg0 = float(base[numerator]), float(base[denominator])

        net = (n1 / d1 if d1 else 0.0) - (n0 / d0 if d0 else 0.0)
        group = GroupResult(key=dict(zip(dimensions, key)), net_contribution=net)

        if dg0 > 0 and dg1 > 0:
            w0, w1 = dg0 / d0, dg1 / d1
            r0, r1 = n0 / dg0, n1 / dg1
            group.rate_effect = ((w0 + w1) / 2.0) * (r1 - r0)
            group.mix_effect = ((r0 + r1) / 2.0) * (w1 - w0)
            group.comparable = True
            total_rate += group.rate_effect
            total_mix += group.mix_effect
        else:
            group.comparable = False
            entry_exit += net
        groups.append(group)

    gross = sum(abs(g.net_contribution) for g in groups)
    decomposition_complete = abs(entry_exit) <= FLOAT_TOL
    heavy_cancellation = (
        gross > GROSS_EPSILON and abs(delta_r) / gross < CANCELLATION_THRESHOLD
    )
    composition_dominant = (
        decomposition_complete
        and sign_with_tol(total_rate) != 0
        and sign_with_tol(delta_r) != 0
        and sign_with_tol(total_rate) != sign_with_tol(delta_r)
    )
    comparable = [g for g in groups if g.comparable]
    rate_signs = {sign_with_tol(g.rate_effect) for g in comparable}
    simpson_strict = (
        decomposition_complete
        and len(comparable) >= 2
        and len(rate_signs) == 1
        and 0 not in rate_signs
        and rate_signs != {sign_with_tol(delta_r)}
        and sign_with_tol(delta_r) != 0
    )
    suppress = (
        composition_dominant
        or not decomposition_complete
        or heavy_cancellation
        or abs(delta_r) <= SHARE_EPSILON
    )

    for group in groups:
        share = None
        if (
            not composition_dominant
            and decomposition_complete
            and not heavy_cancellation
            and group.comparable
            and abs(delta_r) > SHARE_EPSILON
        ):
            candidate = snap_share_boundary(group.net_contribution / delta_r)
            if 0 < candidate <= 1:
                share = candidate
        group.contribution_share = share

    totals = {"total_rate_effect": total_rate, "total_mix_effect": total_mix,
              "entry_exit_effect": entry_exit, "gross_movement": gross}
    flags = {"decomposition_complete": decomposition_complete,
             "composition_dominant": composition_dominant,
             "simpson_strict": simpson_strict,
             "heavy_cancellation": heavy_cancellation,
             "suppress_top_contributor": suppress}
    _assert_invariants(groups, totals, delta_r)
    return groups, totals, flags


def _assert_invariants(groups, totals, delta_r) -> None:
    """어기면 결과를 내지 않는다. 반올림이 아니라 버그다."""
    summed = sum(g.net_contribution for g in groups)
    if abs(summed - delta_r) > FLOAT_TOL:
        raise AssertionError(
            "additivity broken: sum(net)=%r delta=%r" % (summed, delta_r)
        )
    for group in groups:
        if group.comparable:
            split = group.rate_effect + group.mix_effect
            if abs(split - group.net_contribution) > FLOAT_TOL:
                raise AssertionError(
                    "split broken for %r: %r vs %r"
                    % (group.key, split, group.net_contribution)
                )
    three = (totals["total_rate_effect"] + totals["total_mix_effect"]
             + totals["entry_exit_effect"])
    if abs(three - delta_r) > FLOAT_TOL:
        raise AssertionError("three-term identity broken: %r vs %r" % (three, delta_r))
