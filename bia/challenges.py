"""The 8 challenge cases (v1), as data.

A ChallengeSpec describes how a challenge world differs from the flat default:
which cells get a per-day uplift, which cells produce unusually many or few
tickets, which cells are flooded with mislabelled text, and which cells deliver
their whole current-period ticket batch from a source outside the allowed list.

Unlike v0's ScenarioSpec, no case here damages the metric delivery: every case
delivers both periods in full, with no missing days and no duplicate rows. The
v1 contract puts the difficulty in *candidate selection*, not in comparability.

Nothing here encodes an expected answer. The oracle is derived in
bia.challengegen from what these specs construct.

The single fact that is stated rather than derived is `must_not_claim`: it comes
from the task specification (eval/v1/CONTRACT.md section 4), and
scripts/check_challenges.py asserts that the generated world actually agrees
with it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from .lexicon import COMPLAINT_TYPES, PRODUCTS

PERIOD_DAYS = 30

Cell = Tuple[str, str]


@dataclass(frozen=True)
class ChallengeSpec:
    challenge_id: str
    label: str
    category: str

    # (product, complaint_type, per_day_delta) applied to the CURRENT period only.
    uplift: Tuple[Tuple[str, str, int], ...] = ()
    # (product, complaint_type, mean) replaces the randomly drawn 1..4 base mean.
    base_mean_override: Tuple[Tuple[str, str, int], ...] = ()

    # (product, complaint_type, ratio) overriding the default ticket ratio.
    ticket_ratio_override: Tuple[Tuple[str, str, float], ...] = ()
    # (product, complaint_type, rate) overriding the default distractor rate,
    # CURRENT period only.
    distractor_rate_current: Tuple[Tuple[str, str, float], ...] = ()
    # (product, complaint_type, n) hard cap on that cell's CURRENT period tickets.
    current_cell_cap: Tuple[Tuple[str, str, int], ...] = ()
    # Cells whose CURRENT period tickets arrive ENTIRELY with a disallowed
    # source. v0's bad_source_cells degraded 40% of a cell; the trap case needs
    # all of it, so this flag is all-or-nothing and is named accordingly.
    all_bad_source_cells_current: Tuple[Cell, ...] = ()

    # Reuse the baseline per-day jitter draws for the current period, making the
    # two periods identical day for day except where an uplift applies. This is
    # how a case pins every other cell's delta at exactly 0.
    mirror_draws: bool = False

    # From the task specification, not derived: the run must admit zero evidence.
    must_not_claim: bool = False

    notes: str = ""

    def uplift_map(self):
        return {(p, c): d for p, c, d in self.uplift}


_SPREAD_PRODUCT = "P-Alpha"
_WIDE_TYPE = "billing_error"


CHALLENGES: Tuple[ChallengeSpec, ...] = (
    ChallengeSpec(
        challenge_id="C01",
        label="미끼 1순위 셀 — 최대 delta 셀의 문의가 전부 라벨 미뒷받침",
        category="decoy_top_cell",
        uplift=(("P-Beta", "delivery_delay", 6), ("P-Gamma", "app_crash", 4)),
        distractor_rate_current=(("P-Beta", "delivery_delay", 1.0),),
        notes=(
            "the largest-delta cell keeps a full ticket pool but yields nothing; "
            "the second cell is clean, so picking the top cell burns a retrieval"
        ),
    ),
    ChallengeSpec(
        challenge_id="C02",
        label="제품 전반 확산 — 한 제품의 5개 유형이 고르게 증가",
        category="spread_product",
        uplift=tuple((_SPREAD_PRODUCT, complaint_type, 2) for complaint_type in COMPLAINT_TYPES),
        notes="no single cell holds more than a fifth of the evidence; the product filter does",
    ),
    ChallengeSpec(
        challenge_id="C03",
        label="빈약한 1순위 셀 — 최대 delta 셀의 문의가 극소",
        category="thin_top_cell",
        uplift=(("P-Delta", "damaged_item", 5), ("P-Beta", "support_wait", 4)),
        current_cell_cap=(("P-Delta", "damaged_item", 5),),
        ticket_ratio_override=(("P-Beta", "support_wait", 0.50),),
        notes=(
            "the thin cell is pinned with a cap rather than a 0.05 ratio: the "
            "per-row rounding used by the generator turns a 0.05 ratio into zero "
            "tickets on every day, which would make the pool empty instead of thin"
        ),
    ),
    ChallengeSpec(
        challenge_id="C04",
        label="유형 전반 확산 — 한 불만 유형이 4개 제품에서 증가",
        category="type_wide",
        uplift=tuple((product, _WIDE_TYPE, 2) for product in PRODUCTS),
        notes="the complaint_type filter is the only candidate that reaches all of it",
    ),
    ChallengeSpec(
        challenge_id="C05",
        label="함정 — 유일한 증가 셀의 문의가 전부 허용되지 않은 출처",
        category="trap_defer",
        uplift=(("P-Gamma", "billing_error", 6),),
        all_bad_source_cells_current=(("P-Gamma", "billing_error"),),
        mirror_draws=True,
        must_not_claim=True,
        notes=(
            "mirrored draws pin every other cell's delta at exactly 0, so the trap "
            "cell is the only positive-delta cell and it admits nothing"
        ),
    ),
    ChallengeSpec(
        challenge_id="C06",
        label="상보적 두 셀 — 각 셀 단독으로는 충분 판정에 못 미침",
        category="complementary_cells",
        uplift=(("P-Alpha", "app_crash", 4), ("P-Delta", "support_wait", 4)),
        distractor_rate_current=(
            ("P-Alpha", "app_crash", 0.55),
            ("P-Delta", "support_wait", 0.55),
        ),
        notes="a second retrieval is the only way past a 0.5 coverage bar",
    ),
    ChallengeSpec(
        challenge_id="C07",
        label="단일 셀 — 1회 호출로 끝나는 것이 정답",
        category="clean_single",
        uplift=(("P-Beta", "app_crash", 6),),
        distractor_rate_current=(("P-Beta", "app_crash", 0.0),),
        mirror_draws=True,
        notes=(
            "mirrored draws leave exactly one positive-delta cell, so the first "
            "filter already holds every useful ticket and a second call adds nothing"
        ),
    ),
    ChallengeSpec(
        challenge_id="C08",
        label="물량 미끼 — 문의량이 압도적인 셀의 증가는 미미",
        category="volume_decoy",
        uplift=(("P-Gamma", "damaged_item", 5), ("P-Alpha", "support_wait", 1)),
        base_mean_override=(("P-Alpha", "support_wait", 12),),
        ticket_ratio_override=(("P-Alpha", "support_wait", 0.9),),
        notes=(
            "the loud cell is given a high base level as well as a 0.9 ticket ratio, "
            "so its pool clears three times the real top cell's while its delta stays small"
        ),
    ),
)


def _validate() -> None:
    if len(CHALLENGES) != 8:
        raise ValueError("expected exactly 8 challenges, got %d" % len(CHALLENGES))
    for index, spec in enumerate(CHALLENGES):
        expected = "C%02d" % (index + 1)
        if spec.challenge_id != expected:
            raise ValueError(
                "challenge %d has id %r, expected %r" % (index, spec.challenge_id, expected)
            )
        cells = (
            [(p, c) for p, c, _ in spec.uplift]
            + [(p, c) for p, c, _ in spec.base_mean_override]
            + [(p, c) for p, c, _ in spec.ticket_ratio_override]
            + [(p, c) for p, c, _ in spec.distractor_rate_current]
            + [(p, c) for p, c, _ in spec.current_cell_cap]
            + list(spec.all_bad_source_cells_current)
        )
        for product, complaint_type in cells:
            if product not in PRODUCTS:
                raise ValueError("%s: unknown product %r" % (spec.challenge_id, product))
            if complaint_type not in COMPLAINT_TYPES:
                raise ValueError(
                    "%s: unknown complaint_type %r" % (spec.challenge_id, complaint_type)
                )


_validate()
