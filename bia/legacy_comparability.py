"""v2.1 이행을 위한 동결 reference. 제품 경로는 이 모듈을 import 하지 않는다.

`320ba68` 의 bia/integrity.py 에 있던 기간 판정 함수를 글자 그대로 옮겼다. 새 판정
(bia/analysis/qualification.py)이 이것을 재현하는지 이중 실행 테스트가 확인한다.
이 파일은 새 구현에 맞추려고 고치지 않는다 — 해시가 tests/test_legacy_frozen.py 에 고정돼 있다.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from .integrity import (
    MODE_ALIGNED_WINDOW,
    MODE_BLOCKED,
    MODE_FULL,
    Comparability,
    PeriodIntegrity,
    dedupe_rows,
)
from .types import MetricRow, Period

MIN_WINDOW_DAYS = 7
MIN_WINDOW_FRACTION = 0.5


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
