"""Period completeness, missing days, duplicate rows, and comparability.

Nothing downstream is allowed to compute a delta until this module says how
(and whether) the two periods may be compared. This is the guard that stops a
partial month from being reported as a whole month.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .analysis.frame import Observation, observation_key
from .types import MetricRow, Period

MODE_FULL = "full"
MODE_ALIGNED_WINDOW = "aligned_window"
MODE_BLOCKED = "blocked"

MIN_WINDOW_DAYS = 7
MIN_WINDOW_FRACTION = 0.5


@dataclass
class PeriodIntegrity:
    period: Period
    observed_days: int
    missing_days: List[str]
    duplicate_rows_removed: int
    conflicting_keys: List[str]
    present_offsets: List[int] = field(default_factory=list)

    @property
    def expected_days(self) -> int:
        return self.period.days

    @property
    def completeness(self) -> float:
        return round(self.observed_days / float(self.expected_days), 4)

    @property
    def complete(self) -> bool:
        return self.observed_days == self.expected_days and not self.conflicting_keys

    def as_dict(self) -> Dict[str, object]:
        return {
            "period": self.period.as_dict(),
            "expected_days": self.expected_days,
            "observed_days": self.observed_days,
            "completeness": self.completeness,
            "complete": self.complete,
            "missing_days": list(self.missing_days),
            "duplicate_rows_removed": self.duplicate_rows_removed,
            "conflicting_keys": list(self.conflicting_keys),
        }


@dataclass
class Comparability:
    mode: str
    reason: str
    current_window: Optional[Period] = None
    baseline_window: Optional[Period] = None

    @property
    def usable(self) -> bool:
        return self.mode in (MODE_FULL, MODE_ALIGNED_WINDOW)

    @property
    def is_partial(self) -> bool:
        return self.mode == MODE_ALIGNED_WINDOW

    def as_dict(self) -> Dict[str, object]:
        return {
            "mode": self.mode,
            "reason": self.reason,
            "current_window": self.current_window.as_dict() if self.current_window else None,
            "baseline_window": self.baseline_window.as_dict() if self.baseline_window else None,
        }


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
