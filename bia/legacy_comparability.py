"""v2.1 이행을 위한 동결 reference. 제품 경로는 이 모듈을 import 하지 않는다.

`320ba68` 의 bia/integrity.py 에 있던 기간 판정 함수를 글자 그대로 옮겼다. 새 판정
(bia/analysis/qualification.py)이 이것을 재현하는지 이중 실행 테스트가 확인한다.
이 파일은 새 구현에 맞추려고 고치지 않는다 — 해시가 tests/test_legacy_frozen.py 에 고정돼 있다.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from .integrity import (
    MODE_ALIGNED_WINDOW,
    MODE_BLOCKED,
    MODE_FULL,
    Comparability,
    PeriodIntegrity,
)
from .analysis.frame import Observation
from .types import MetricRow, Period

MIN_WINDOW_DAYS = 7
MIN_WINDOW_FRACTION = 0.5


def observation_key(obs: Observation, grain: Sequence[str]) -> Tuple[str, ...]:
    parts = []
    for name in grain:
        parts.append(obs.day.isoformat() if name == "day" else obs.key_of(name))
    return tuple(parts)


V1_GRAIN = ("day", "product", "complaint_type")


def dedupe_observations(
    rows: Sequence[Observation], grain: Sequence[str]
) -> Tuple[List[Observation], int, List[str]]:
    """중복은 언제나 **물리 grain** 으로 판정한다. 분석이 요청한 breakdown 이 아니다.

    데이터 grain 이 day×channel×device 인데 breakdown 이 [channel] 뿐일 때 요청 기준으로
    검사하면 device 별 정상 행이 전부 중복으로 잘못 판정된다.
    """
    seen: Dict[Tuple[str, ...], Observation] = {}
    duplicates = 0
    conflicts: List[str] = []
    for row in rows:
        key = observation_key(row, grain)
        prior = seen.get(key)
        if prior is None:
            seen[key] = row
            continue
        if prior.measures == row.measures:
            duplicates += 1
        else:
            label = "|".join(key)
            if label not in conflicts:
                conflicts.append(label)
    clean = [seen[k] for k in sorted(seen.keys())]
    return clean, duplicates, sorted(conflicts)


def _row_to_observation(row: MetricRow) -> Observation:
    return Observation(
        day=row.day,
        keys=(("complaint_type", row.complaint_type), ("product", row.product)),
        measures=(("count", row.count),),
    )


def _observation_to_row(obs: Observation) -> MetricRow:
    keys = dict(obs.keys)
    return MetricRow(day=obs.day, product=keys["product"],
                     complaint_type=keys["complaint_type"],
                     count=dict(obs.measures)["count"])


def dedupe_rows(rows: List[MetricRow]) -> Tuple[List[MetricRow], int, List[str]]:
    """v1 표면. 판정은 grain 경로에 위임한다 — 구현이 둘이면 갈라진다."""
    observations = [_row_to_observation(r) for r in rows]
    clean, duplicates, conflicts = dedupe_observations(observations, V1_GRAIN)
    return [_observation_to_row(o) for o in clean], duplicates, conflicts


def inspect_period(rows: List[MetricRow], period: Period) -> Tuple[List[MetricRow], PeriodIntegrity]:
    in_period = [r for r in rows if period.contains(r.day)]
    clean, duplicates, conflicts = dedupe_rows(in_period)
    observed = sorted({r.day for r in clean})
    observed_set = set(observed)
    missing = [d.isoformat() for d in period.dates() if d not in observed_set]
    present_offsets = sorted((d - period.start).days for d in observed_set)
    integrity = PeriodIntegrity(
        period=period,
        observed_days=len(observed_set),
        missing_days=missing,
        duplicate_rows_removed=duplicates,
        conflicting_keys=conflicts,
        present_offsets=present_offsets,
    )
    return clean, integrity


def _longest_common_run(a: List[int], b: List[int], limit: int) -> Tuple[int, int]:
    """Longest contiguous run of day-offsets present in both periods, below `limit`."""
    common = sorted(set(a) & set(b) & set(range(limit)))
    best = (0, -1)
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
        best_len = prev - run_start + 1
        best = (run_start, prev)
    if best_len == 0:
        return (-1, -1)
    return best


def decide_comparability(
    current: PeriodIntegrity, baseline: PeriodIntegrity
) -> Comparability:
    if current.conflicting_keys or baseline.conflicting_keys:
        return Comparability(
            MODE_BLOCKED,
            "conflicting_duplicate_rows: the same (day, product, complaint_type) key carries "
            "two different counts, so no count can be trusted",
        )
    if current.observed_days == 0 or baseline.observed_days == 0:
        return Comparability(MODE_BLOCKED, "empty_period: one of the periods has no rows")

    if (
        current.complete
        and baseline.complete
        and current.expected_days == baseline.expected_days
    ):
        return Comparability(
            MODE_FULL,
            "both periods are complete and equal in length",
            current.period,
            baseline.period,
        )

    limit = min(current.expected_days, baseline.expected_days)
    start, end = _longest_common_run(current.present_offsets, baseline.present_offsets, limit)
    if start < 0:
        return Comparability(MODE_BLOCKED, "no_comparable_window: no day-offset is present in both periods")

    length = end - start + 1
    threshold = max(MIN_WINDOW_DAYS, int(round(MIN_WINDOW_FRACTION * limit)))
    if length < threshold:
        return Comparability(
            MODE_BLOCKED,
            "no_comparable_window: longest aligned window is %d day(s), below the required %d"
            % (length, threshold),
        )
    return Comparability(
        MODE_ALIGNED_WINDOW,
        "periods are not equally complete; compared on the aligned day-offset window %d..%d"
        % (start + 1, end + 1),
        current.period.sub(start, end),
        baseline.period.sub(start, end),
    )
