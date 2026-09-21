"""The 24 evaluation scenarios, as data.

A ScenarioSpec describes how the synthetic world differs from the flat default:
which cells get a per-day uplift, which days are actually delivered, which rows
arrive twice, and which tickets are degraded (missing, capped, mislabelled, or
carrying a source outside the allowed list).

Nothing here encodes an expected answer. The oracle is derived in bia.datagen
from what these specs construct.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from .lexicon import COMPLAINT_TYPES, PRODUCTS

PERIOD_DAYS = 30

Cell = Tuple[str, str]


@dataclass(frozen=True)
class ScenarioSpec:
    scenario_id: str
    label: str
    category: str

    # (product, complaint_type, per_day_delta) applied to the CURRENT period only.
    uplift: Tuple[Tuple[str, str, int], ...] = ()
    # (product, complaint_type, mean) replaces the randomly drawn 1..4 base mean.
    base_mean_override: Tuple[Tuple[str, str, int], ...] = ()

    # Day offsets (0-based, from the current period start) actually delivered.
    # None means all PERIOD_DAYS offsets are delivered.
    current_offsets: Optional[Tuple[int, ...]] = None

    duplicate_exact_current: int = 0
    duplicate_conflicting_current: int = 0

    # (product, complaint_type, ratio) overriding the default ticket ratio.
    ticket_ratio_override: Tuple[Tuple[str, str, float], ...] = ()
    # (product, complaint_type, rate) overriding the default distractor rate,
    # CURRENT period only.
    distractor_rate_current: Tuple[Tuple[str, str, float], ...] = ()
    # (product, complaint_type, n) hard cap on that cell's CURRENT period tickets.
    current_cell_cap: Tuple[Tuple[str, str, int], ...] = ()
    # Cells whose CURRENT period tickets partly arrive with a disallowed source.
    bad_source_cells: Tuple[Cell, ...] = ()

    # Reuse the baseline per-day jitter draws for the current period, making the
    # two periods identical day for day (used to pin a delta of exactly 0).
    mirror_draws: bool = False

    # From the task specification, not derived: the run must admit zero evidence.
    must_defer: bool = False
    # Expected admitted ticket count for the evidence target cell, when pinned.
    expect_target_cell_admitted: Optional[int] = None

    notes: str = ""

    def uplift_map(self):
        return {(p, c): d for p, c, d in self.uplift}

    def delivered_current_offsets(self) -> Tuple[int, ...]:
        if self.current_offsets is None:
            return tuple(range(PERIOD_DAYS))
        return self.current_offsets


_ALL = tuple(range(PERIOD_DAYS))
# S11 drops every third day: 2026-07-03, 07-06, 07-09, ... (offsets 2, 5, 8, ...)
_EVERY_THIRD_MISSING = tuple(o for o in _ALL if o % 3 != 2)
# S10 drops 2026-07-11 and 2026-07-12 (offsets 10 and 11).
_TWO_MISSING = tuple(o for o in _ALL if o not in (10, 11))


SCENARIOS: Tuple[ScenarioSpec, ...] = (
    ScenarioSpec(
        scenario_id="S01",
        label="정상 증가 — 단일 셀",
        category="normal_increase",
        uplift=(("P-Beta", "delivery_delay", 5),),
    ),
    ScenarioSpec(
        scenario_id="S02",
        label="정상 증가 — 두 제품에 분산",
        category="normal_increase",
        uplift=(("P-Alpha", "app_crash", 4), ("P-Gamma", "billing_error", 3)),
    ),
    ScenarioSpec(
        scenario_id="S03",
        label="정상 증가 — 한 제품의 두 유형",
        category="normal_increase",
        uplift=(("P-Delta", "damaged_item", 4), ("P-Delta", "support_wait", 3)),
    ),
    ScenarioSpec(
        scenario_id="S04",
        label="정상 증가 — 한 유형이 두 제품에서",
        category="normal_increase",
        uplift=(("P-Alpha", "billing_error", 3), ("P-Beta", "billing_error", 3)),
    ),
    ScenarioSpec(
        scenario_id="S05",
        label="정상 증가 — 완만",
        category="normal_increase",
        uplift=(("P-Gamma", "support_wait", 3),),
    ),
    ScenarioSpec(
        scenario_id="S06",
        label="정상 증가 — 급증",
        category="normal_increase",
        uplift=(("P-Delta", "app_crash", 8),),
    ),
    ScenarioSpec(
        scenario_id="S07",
        label="부분 기간 — 당월 15일까지만 적재",
        category="partial_period",
        uplift=(("P-Beta", "app_crash", 5),),
        current_offsets=tuple(range(15)),
        notes=(
            "delivered window is exactly the minimum comparable length (15 days), "
            "so this scenario pins the accept side of the threshold"
        ),
    ),
    ScenarioSpec(
        scenario_id="S08",
        label="부분 기간 — 당월 20일까지만 적재",
        category="partial_period",
        uplift=(("P-Gamma", "delivery_delay", 5),),
        current_offsets=tuple(range(20)),
    ),
    ScenarioSpec(
        scenario_id="S09",
        label="부분 기간 — 5일치뿐이라 비교 불가",
        category="partial_period_blocked",
        uplift=(("P-Alpha", "delivery_delay", 5),),
        current_offsets=tuple(range(5)),
        must_defer=True,
    ),
    ScenarioSpec(
        scenario_id="S10",
        label="결측일 — 이틀 누락",
        category="missing_days",
        uplift=(("P-Alpha", "damaged_item", 5),),
        current_offsets=_TWO_MISSING,
    ),
    ScenarioSpec(
        scenario_id="S11",
        label="결측일 — 사흘마다 누락으로 연속 구간 소멸",
        category="missing_days_blocked",
        uplift=(("P-Beta", "billing_error", 5),),
        current_offsets=_EVERY_THIRD_MISSING,
        must_defer=True,
    ),
    ScenarioSpec(
        scenario_id="S12",
        label="중복 행 — 완전 동일 40건",
        category="duplicate_rows_exact",
        uplift=(("P-Gamma", "app_crash", 5),),
        duplicate_exact_current=40,
    ),
    ScenarioSpec(
        scenario_id="S13",
        label="중복 행 — 같은 키에 다른 건수 5건",
        category="duplicate_rows_conflicting",
        uplift=(("P-Delta", "delivery_delay", 5),),
        duplicate_conflicting_current=5,
        must_defer=True,
    ),
    ScenarioSpec(
        scenario_id="S14",
        label="최대 볼륨 제품이 최대 증가 제품이 아님",
        category="misleading_top_group",
        uplift=(("P-Gamma", "damaged_item", 5),),
        base_mean_override=(("P-Alpha", "support_wait", 12),),
    ),
    ScenarioSpec(
        scenario_id="S15",
        label="최대 볼륨 유형이 최대 증가 유형이 아님",
        category="misleading_top_group",
        uplift=(("P-Beta", "app_crash", 5),),
        base_mean_override=tuple((p, "support_wait", 8) for p in PRODUCTS),
    ),
    ScenarioSpec(
        scenario_id="S16",
        label="증감 혼재 — 한쪽은 증가, 한쪽은 감소",
        category="mixed_direction",
        uplift=(("P-Alpha", "delivery_delay", 6), ("P-Delta", "support_wait", -3)),
        base_mean_override=(("P-Delta", "support_wait", 4),),
        notes="the decreasing cell is given a high enough base to actually fall by 3/day",
    ),
    ScenarioSpec(
        scenario_id="S17",
        label="근거 부족 — 해당 셀의 문의가 0건",
        category="insufficient_tickets",
        uplift=(("P-Beta", "damaged_item", 5),),
        ticket_ratio_override=(("P-Beta", "damaged_item", 0.0),),
        must_defer=True,
    ),
    ScenarioSpec(
        scenario_id="S18",
        label="근거 부족 — 해당 셀의 문의가 2건뿐",
        category="insufficient_tickets",
        uplift=(("P-Gamma", "support_wait", 5),),
        current_cell_cap=(("P-Gamma", "support_wait", 2),),
        must_defer=True,
    ),
    ScenarioSpec(
        scenario_id="S19",
        label="라벨 미지지 — 문의는 있으나 본문이 라벨을 뒷받침하지 않음",
        category="unsupported_text",
        uplift=(("P-Delta", "billing_error", 5),),
        distractor_rate_current=(("P-Delta", "billing_error", 1.0),),
        expect_target_cell_admitted=0,
    ),
    ScenarioSpec(
        scenario_id="S20",
        label="교란 다수 — 해당 셀 문의의 60%가 라벨 불일치",
        category="distractor_heavy",
        uplift=(("P-Alpha", "app_crash", 5),),
        distractor_rate_current=(("P-Alpha", "app_crash", 0.60),),
    ),
    ScenarioSpec(
        scenario_id="S21",
        label="교란 물량 — 증가와 무관한 셀에 문의가 집중",
        category="distractor_volume",
        uplift=(("P-Beta", "support_wait", 5),),
        ticket_ratio_override=(("P-Delta", "damaged_item", 0.9),),
    ),
    ScenarioSpec(
        scenario_id="S22",
        label="허용되지 않은 출처 — 해당 셀 문의의 40%가 legacy_import",
        category="bad_source",
        uplift=(("P-Gamma", "billing_error", 5),),
        bad_source_cells=(("P-Gamma", "billing_error"),),
    ),
    ScenarioSpec(
        scenario_id="S23",
        label="증가 없음 — 전체적으로 감소",
        category="decrease",
        uplift=(("P-Alpha", "delivery_delay", -3), ("P-Beta", "app_crash", -3)),
        base_mean_override=(
            ("P-Alpha", "delivery_delay", 4),
            ("P-Beta", "app_crash", 4),
        ),
        must_defer=True,
        notes="both decreasing cells are given a high enough base to actually fall by 3/day",
    ),
    ScenarioSpec(
        scenario_id="S24",
        label="증가 없음 — 두 기간이 완전히 동일",
        category="flat",
        mirror_draws=True,
        must_defer=True,
        notes="current period reuses the baseline per-day draws, pinning the delta at exactly 0",
    ),
)


def _validate() -> None:
    if len(SCENARIOS) != 24:
        raise ValueError("expected exactly 24 scenarios, got %d" % len(SCENARIOS))
    for index, spec in enumerate(SCENARIOS):
        expected = "S%02d" % (index + 1)
        if spec.scenario_id != expected:
            raise ValueError(
                "scenario %d has id %r, expected %r" % (index, spec.scenario_id, expected)
            )
        cells = (
            [(p, c) for p, c, _ in spec.uplift]
            + [(p, c) for p, c, _ in spec.base_mean_override]
            + [(p, c) for p, c, _ in spec.ticket_ratio_override]
            + [(p, c) for p, c, _ in spec.distractor_rate_current]
            + [(p, c) for p, c, _ in spec.current_cell_cap]
            + list(spec.bad_source_cells)
        )
        for product, complaint_type in cells:
            if product not in PRODUCTS:
                raise ValueError("%s: unknown product %r" % (spec.scenario_id, product))
            if complaint_type not in COMPLAINT_TYPES:
                raise ValueError(
                    "%s: unknown complaint_type %r" % (spec.scenario_id, complaint_type)
                )
        for offset in spec.delivered_current_offsets():
            if not 0 <= offset < PERIOD_DAYS:
                raise ValueError("%s: day offset %d out of range" % (spec.scenario_id, offset))


_validate()
