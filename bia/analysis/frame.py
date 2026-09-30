"""측정값 로딩과 정규화. 도메인 이름을 알지 못한다."""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Tuple

from ..types import parse_day
from .errors import SpecError
from .spec import DomainSpec

UNKNOWN = "__UNKNOWN__"


@dataclass(frozen=True)
class Observation:
    """한 grain 행. `keys` 와 `measures` 는 정렬된 튜플쌍이라 해시 가능하다.
    `null_dimensions` 는 로드 시 빈칸이라 `__UNKNOWN__` 으로 바꾼 차원 이름이다. 출처일 뿐이라
    비교·해시에서 뺀다 — grain·중복 판정은 이 필드가 없던 때와 같다."""

    day: object
    keys: Tuple[Tuple[str, str], ...]
    measures: Tuple[Tuple[str, int], ...]
    null_dimensions: Tuple[str, ...] = field(default=(), compare=False)

    def key_of(self, dimension: str) -> str:
        for name, value in self.keys:
            if name == dimension:
                return value
        raise KeyError(dimension)

    def measure_of(self, column: str) -> int:
        for name, value in self.measures:
            if name == column:
                return value
        raise KeyError(column)


def observation_key(obs: Observation, grain: Sequence[str]) -> Tuple[str, ...]:
    parts = []
    for name in grain:
        parts.append(obs.day.isoformat() if name == "day" else obs.key_of(name))
    return tuple(parts)


def load_observations(
    path: str, spec: DomainSpec, measure_columns: Sequence[str]
) -> List[Observation]:
    dimensions = spec.grain_dimensions
    out: List[Observation] = []
    with open(path, "r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        header = reader.fieldnames or []
        for column in ("day",) + tuple(dimensions) + tuple(measure_columns):
            if column not in header:
                raise SpecError(
                    "%s: column %r is required by domain %r but the header is %s"
                    % (path, column, spec.name, header)
                )
        for line_no, record in enumerate(reader, start=2):
            keys = []
            nulls = []
            for dimension in dimensions:
                raw = (record.get(dimension) or "").strip()
                if not raw:
                    nulls.append(dimension)
                keys.append((dimension, raw if raw else UNKNOWN))
            measures = []
            for column in measure_columns:
                raw = (record.get(column) or "").strip()
                try:
                    number = int(raw)
                except ValueError:
                    raise SpecError(
                        "%s line %d: %r must be an integer, got %r"
                        % (path, line_no, column, raw)
                    )
                if number < 0:
                    raise SpecError(
                        "%s line %d: %r must not be negative, got %d"
                        % (path, line_no, column, number)
                    )
                measures.append((column, number))
            out.append(
                Observation(
                    day=parse_day(record["day"]),
                    keys=tuple(sorted(keys)),
                    measures=tuple(sorted(measures)),
                    null_dimensions=tuple(sorted(nulls)),
                )
            )
    return out


@dataclass(frozen=True)
class Frame:
    """판정과 실행이 함께 쓰는 불변 행 묶음. 행 자체도 frozen 이다."""

    rows: Tuple[Observation, ...]

    @staticmethod
    def of(rows) -> "Frame":
        return Frame(rows=tuple(rows))


def load_frame(path: str, spec: DomainSpec, measure_columns: Sequence[str]) -> Frame:
    return Frame.of(load_observations(path, spec, measure_columns))
