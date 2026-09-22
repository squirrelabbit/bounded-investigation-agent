"""Unit tests for GreedyEvidenceSelector over hand-built candidate sets.

No challenge data, no oracle, no evaluation run: every input here is built in
this file so that the rule of CONTRACT.md §7-A is checked directly.
"""
from __future__ import annotations

import unittest
from typing import List, Sequence

from bia.decision import (
    CONCENTRATION_RATIO,
    DeterministicHeuristicSelector,
    GreedyEvidenceSelector,
)
from bia.evidence import MIN_ADMITTED_TICKETS, NOISE_FLOOR_DELTA
from bia.types import DEFER, EvidenceCandidate, EvidenceFilter, Period

WINDOW = Period.of("2026-07-01", "2026-07-30")


def candidate(
    candidate_id: str,
    kind: str,
    group_delta: int,
    available_tickets: int,
    product: str = None,
    complaint_type: str = None,
) -> EvidenceCandidate:
    return EvidenceCandidate(
        candidate_id=candidate_id,
        kind=kind,
        label="%s:%s" % (kind, candidate_id),
        evidence_filter=EvidenceFilter(WINDOW, product, complaint_type),
        group_delta=group_delta,
        available_tickets=available_tickets,
        server_reason="hand-built fixture",
    )


def cell(candidate_id: str, delta: int, pool: int) -> EvidenceCandidate:
    return candidate(candidate_id, "cell", delta, pool, "P-Beta", "delivery_delay")


def product(candidate_id: str, delta: int, pool: int) -> EvidenceCandidate:
    return candidate(candidate_id, "product", delta, pool, "P-Beta", None)


def complaint_type(candidate_id: str, delta: int, pool: int) -> EvidenceCandidate:
    return candidate(candidate_id, "complaint_type", delta, pool, None, "delivery_delay")


def empty_state() -> dict:
    return {"decision_calls": 0, "investigated_candidates": [], "rounds": []}


def pick(candidates: Sequence[EvidenceCandidate], state=None) -> str:
    return GreedyEvidenceSelector().select_next_evidence(
        empty_state() if state is None else state, candidates
    )


class GreedyEligibilityTest(unittest.TestCase):
    def test_picks_largest_pool_among_eligible_cells(self):
        candidates = [cell("R1-C1", 40, 80), cell("R1-C2", 12, 300)]
        self.assertEqual(pick(candidates), "R1-C2")

    def test_below_noise_floor_never_picked_even_with_huge_pool(self):
        quiet = cell("R1-C1", NOISE_FLOOR_DELTA - 1, 100000)
        loud = cell("R1-C2", NOISE_FLOOR_DELTA + 5, 10)
        self.assertEqual(pick([quiet, loud]), "R1-C2")
        self.assertEqual(pick([quiet]), DEFER)

    def test_below_min_admitted_tickets_never_picked_even_with_huge_delta(self):
        tiny = cell("R1-C1", 5000, MIN_ADMITTED_TICKETS - 1)
        modest = cell("R1-C2", NOISE_FLOOR_DELTA, MIN_ADMITTED_TICKETS)
        self.assertEqual(pick([tiny, modest]), "R1-C2")
        self.assertEqual(pick([tiny]), DEFER)

    def test_defers_when_nothing_eligible_and_when_list_is_empty(self):
        self.assertEqual(pick([]), DEFER)
        self.assertEqual(
            pick(
                [
                    cell("R1-C1", NOISE_FLOOR_DELTA - 1, 500),
                    product("R1-C2", 900, MIN_ADMITTED_TICKETS - 1),
                ]
            ),
            DEFER,
        )


class ConcentrationGuardTest(unittest.TestCase):
    def test_guard_fires_when_one_cell_holds_nearly_all_of_the_broad_delta(self):
        candidates = [
            cell("R1-C1", 95, 120),
            cell("R1-C2", 5, 60),
            product("R1-C3", 100, 4000),
        ]
        self.assertEqual(pick(candidates), "R1-C1")

    def test_guard_also_drops_complaint_type_candidates(self):
        candidates = [
            cell("R1-C1", 90, 100),
            complaint_type("R1-C2", 100, 9000),
        ]
        self.assertEqual(pick(candidates), "R1-C1")

    def test_guard_does_not_fire_when_increase_is_spread_over_cells(self):
        candidates = [
            cell("R1-C1", 34, 90),
            cell("R1-C2", 33, 88),
            product("R1-C3", 100, 1200),
        ]
        self.assertEqual(pick(candidates), "R1-C3")

    def test_exact_boundary_is_inclusive(self):
        broad_delta = 100
        at_ratio = int(CONCENTRATION_RATIO * broad_delta)
        self.assertEqual(at_ratio, 70)
        fires = [cell("R1-C1", at_ratio, 50), product("R1-C2", broad_delta, 5000)]
        self.assertEqual(pick(fires), "R1-C1")
        does_not_fire = [
            cell("R1-C1", at_ratio - 1, 50),
            product("R1-C2", broad_delta, 5000),
        ]
        self.assertEqual(pick(does_not_fire), "R1-C2")

    def test_only_eligible_cells_can_drop_a_broad_candidate(self):
        starved_cell = cell("R1-C1", 99, MIN_ADMITTED_TICKETS - 1)
        quiet_cell = cell("R1-C2", NOISE_FLOOR_DELTA - 1, 500)
        broad = product("R1-C3", 100, 2000)
        self.assertEqual(pick([starved_cell, quiet_cell, broad]), "R1-C3")


class DeterminismTest(unittest.TestCase):
    def test_equal_pools_prefer_larger_delta(self):
        candidates = [cell("R1-C1", 10, 200), cell("R1-C2", 40, 200)]
        self.assertEqual(pick(candidates), "R1-C2")
        self.assertEqual(pick(list(reversed(candidates))), "R1-C2")

    def test_equal_pools_and_deltas_prefer_smaller_candidate_id(self):
        candidates = [cell("R1-C2", 40, 200), cell("R1-C1", 40, 200)]
        self.assertEqual(pick(candidates), "R1-C1")
        self.assertEqual(pick(list(reversed(candidates))), "R1-C1")

    def test_shuffled_order_gives_identical_output(self):
        base: List[EvidenceCandidate] = [
            cell("R1-C1", 40, 200),
            cell("R1-C2", 40, 200),
            cell("R1-C3", 12, 200),
            product("R1-C4", 60, 150),
        ]
        orders = [
            base,
            list(reversed(base)),
            [base[2], base[0], base[3], base[1]],
            [base[3], base[1], base[2], base[0]],
        ]
        results = {pick(list(order)) for order in orders}
        self.assertEqual(results, {"R1-C1"})

    def test_second_call_on_reduced_candidate_set_uses_the_same_rule(self):
        selector = GreedyEvidenceSelector()
        first = [cell("R1-C1", 40, 300), cell("R1-C2", 30, 100)]
        self.assertEqual(selector.select_next_evidence(empty_state(), first), "R1-C1")
        second = [cell("R2-C1", 30, 100), product("R2-C2", 31, 900)]
        state = {"decision_calls": 1, "investigated_candidates": ["R1-C1"], "rounds": [{}]}
        self.assertEqual(selector.select_next_evidence(state, second), "R2-C1")


class PurityTest(unittest.TestCase):
    def test_mutating_the_state_dict_changes_nothing(self):
        candidates = [cell("R1-C1", 40, 300), product("R1-C2", 41, 9000)]
        state = empty_state()
        first = pick(candidates, state)
        state["decision_calls"] = 99
        state["investigated_candidates"].append("R1-C1")
        state["injected"] = {"prefer": "R1-C2"}
        second = pick(candidates, state)
        self.assertEqual(first, second)
        self.assertEqual(first, "R1-C1")

    def test_repeated_calls_leave_selector_and_candidates_unchanged(self):
        selector = GreedyEvidenceSelector()
        candidates = [cell("R1-C1", 40, 300), cell("R1-C2", 12, 90)]
        before = [c.as_dict() for c in candidates]
        results = [
            selector.select_next_evidence(empty_state(), candidates) for _ in range(3)
        ]
        self.assertEqual(results, ["R1-C1"] * 3)
        self.assertEqual([c.as_dict() for c in candidates], before)

    def test_name_and_constant_are_fixed(self):
        self.assertEqual(GreedyEvidenceSelector.name, "greedy")
        self.assertEqual(CONCENTRATION_RATIO, 0.70)


class NarrowBaselineUnchangedTest(unittest.TestCase):
    """The recorded heuristic result must stay valid: same choice as always."""

    def test_heuristic_still_picks_the_largest_delta_cell_only(self):
        selector = DeterministicHeuristicSelector()
        candidates = [
            cell("R1-C1", 40, 12),
            cell("R1-C2", 12, 900),
            product("R1-C3", 60, 5000),
        ]
        self.assertEqual(
            selector.select_next_evidence(empty_state(), candidates), "R1-C1"
        )

    def test_heuristic_defers_when_top_cell_is_below_the_floors(self):
        selector = DeterministicHeuristicSelector()
        self.assertEqual(
            selector.select_next_evidence(
                empty_state(), [cell("R1-C1", NOISE_FLOOR_DELTA - 1, 900)]
            ),
            DEFER,
        )
        self.assertEqual(
            selector.select_next_evidence(
                empty_state(), [cell("R1-C1", 50, MIN_ADMITTED_TICKETS - 1)]
            ),
            DEFER,
        )
        self.assertEqual(
            selector.select_next_evidence(empty_state(), [product("R1-C1", 90, 900)]),
            DEFER,
        )


if __name__ == "__main__":
    unittest.main()
