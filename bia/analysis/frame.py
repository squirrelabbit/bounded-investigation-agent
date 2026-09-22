"""측정값 로딩과 정규화. 도메인 이름을 알지 못한다."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

from ..types import parse_day
from .errors import SpecError
from .spec import DomainSpec

UNKNOWN = "__UNKNOWN__"


@dataclass(frozen=True)
class Observation:
    """한 grain 행. `keys` 와 `measures` 는 정렬된 튜플쌍이라 해시 가능하다."""

    day: object
    keys: Tuple[Tuple[str, str], ...]
    measures: Tuple[Tuple[str, int], ...]

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
            for dimension in dimensions:
                raw = (record.get(dimension) or "").strip()
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
                )
            )
    return out
