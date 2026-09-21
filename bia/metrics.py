"""Deterministic arithmetic: totals, deltas, per-group contribution.

Contribution is arithmetic, not causation: "this share of the increase occurred
in group X" — never "group X made it increase".
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .types import CellDelta, DIM_COMPLAINT_TYPE, DIM_PRODUCT, GroupDelta, MetricRow, Period

TOP_SHARE_TARGET = 0.8
TOP_MAX = 3


@dataclass
class MetricResult:
    current_total: int
    baseline_total: int
    delta: int
    pct_change: Optional[float]
    by_product: List[GroupDelta]
    by_complaint_type: List[GroupDelta]
    cells: List[CellDelta]

    @property
    def increased(self) -> bool:
        return self.delta > 0

    def top(self, dimension: str) -> List[GroupDelta]:
        groups = self.by_product if dimension == DIM_PRODUCT else self.by_complaint_type
        return top_contributors(groups)

    def as_dict(self) -> Dict[str, object]:
        return {
            "current_total": self.current_total,
            "baseline_total": self.baseline_total,
            "delta": self.delta,
            "pct_change": self.pct_change,
            "by_product": [g.as_dict() for g in self.by_product],
            "by_complaint_type": [g.as_dict() for g in self.by_complaint_type],
        }


def _sum_by(rows: List[MetricRow], window: Period, key) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for row in rows:
        if not window.contains(row.day):
            continue
        out[key(row)] = out.get(key(row), 0) + row.count
    return out


def _group_deltas(
    dimension: str, current: Dict[str, int], baseline: Dict[str, int]
) -> List[GroupDelta]:
    values = sorted(set(current) | set(baseline))
    raw = []
    for value in values:
        cur = current.get(value, 0)
        base = baseline.get(value, 0)
        raw.append((value, cur, base, cur - base))
    total_increase = sum(d for _, _, _, d in raw if d > 0)
    groups = []
    for value, cur, base, delta in raw:
        share = round(delta / float(total_increase), 4) if (delta > 0 and total_increase) else 0.0
        groups.append(GroupDelta(dimension, value, cur, base, delta, share))
    groups.sort(key=lambda g: (-g.delta, g.value))
    return groups


def compute(
    rows: List[MetricRow], current_window: Period, baseline_window: Period
) -> MetricResult:
    cur_total = sum(r.count for r in rows if current_window.contains(r.day))
    base_total = sum(r.count for r in rows if baseline_window.contains(r.day))
    pct = round(100.0 * (cur_total - base_total) / float(base_total), 2) if base_total else None

    by_product = _group_deltas(
        DIM_PRODUCT,
        _sum_by(rows, current_window, lambda r: r.product),
        _sum_by(rows, baseline_window, lambda r: r.product),
    )
    by_type = _group_deltas(
        DIM_COMPLAINT_TYPE,
        _sum_by(rows, current_window, lambda r: r.complaint_type),
        _sum_by(rows, baseline_window, lambda r: r.complaint_type),
    )

    cur_cells = _sum_by(rows, current_window, lambda r: r.product + "\x1f" + r.complaint_type)
    base_cells = _sum_by(rows, baseline_window, lambda r: r.product + "\x1f" + r.complaint_type)
    cells: List[CellDelta] = []
    for key in sorted(set(cur_cells) | set(base_cells)):
        product, complaint_type = key.split("\x1f")
        cur = cur_cells.get(key, 0)
        base = base_cells.get(key, 0)
        cells.append(CellDelta(product, complaint_type, cur, base, cur - base))
    cells.sort(key=lambda c: (-c.delta, c.product, c.complaint_type))

    return MetricResult(cur_total, base_total, cur_total - base_total, pct, by_product, by_type, cells)


def top_contributors(groups: List[GroupDelta]) -> List[GroupDelta]:
    """Positive contributors covering TOP_SHARE_TARGET of the increase, at most TOP_MAX."""
    positives = [g for g in groups if g.delta > 0]
    picked: List[GroupDelta] = []
    cumulative = 0.0
    for group in positives:
        picked.append(group)
        cumulative += group.share_of_increase
        if cumulative >= TOP_SHARE_TARGET or len(picked) >= TOP_MAX:
            break
    return picked
