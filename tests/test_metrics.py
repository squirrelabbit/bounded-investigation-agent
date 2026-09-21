"""Tests for bia.metrics: totals, deltas, contribution shares, top contributors.

Every expected value in this file is stated by hand. Nothing here recomputes an
expectation with the code path under test.
"""
import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bia.metrics import TOP_MAX, TOP_SHARE_TARGET, compute, top_contributors  # noqa: E402
from bia.types import GroupDelta, MetricRow, Period  # noqa: E402

BASELINE_WINDOW = Period.of("2026-02-01", "2026-02-03")
CURRENT_WINDOW = Period.of("2026-03-01", "2026-03-03")


def row(day, product, complaint_type, count):
    return MetricRow(
        day=dt.date.fromisoformat(day),
        product=product,
        complaint_type=complaint_type,
        count=count,
    )


# Hand-built fixture. Stated by hand:
#   baseline total = 10 + 5 + 5           = 20
#   current  total = 20 + 4 + 6           = 30
#   delta = 10, pct_change = 100*10/20    = 50.0
#
#   by product        current  baseline  delta
#     alpha            24       15         +9
#     beta              0        5         -5   (baseline only)
#     gamma             6        0         +6   (current only)
#   total increase = 9 + 6 = 15 -> shares alpha 0.6, gamma 0.4, beta 0.0
#
#   by complaint_type current  baseline  delta
#     billing          20       15         +5
#     login             4        5         -1
#     shipping          6        0         +6
#   total increase = 5 + 6 = 11 -> shares shipping 6/11, billing 5/11
HAND_ROWS = [
    # baseline window
    row("2026-02-01", "alpha", "billing", 10),
    row("2026-02-02", "alpha", "login", 5),
    row("2026-02-03", "beta", "billing", 5),
    # current window
    row("2026-03-01", "alpha", "billing", 20),
    row("2026-03-02", "alpha", "login", 4),
    row("2026-03-03", "gamma", "shipping", 6),
]

OUT_OF_WINDOW_ROWS = [
    row("2026-01-15", "alpha", "billing", 1000),  # before the baseline window
    row("2026-02-20", "delta", "billing", 1000),  # between the two windows
    row("2026-03-10", "delta", "shipping", 1000),  # after the current window
]


def as_pairs(groups):
    return [(g.value, g.current, g.baseline, g.delta) for g in groups]


class ComputeTotalsTest(unittest.TestCase):
    def setUp(self):
        self.result = compute(HAND_ROWS, CURRENT_WINDOW, BASELINE_WINDOW)

    def test_totals_delta_and_pct_change(self):
        self.assertEqual(self.result.current_total, 30)
        self.assertEqual(self.result.baseline_total, 20)
        self.assertEqual(self.result.delta, 10)
        self.assertEqual(self.result.pct_change, 50.0)
        self.assertTrue(self.result.increased)

    def test_pct_change_is_none_when_baseline_total_is_zero(self):
        rows = [row("2026-03-01", "alpha", "billing", 7)]
        result = compute(rows, CURRENT_WINDOW, BASELINE_WINDOW)
        self.assertEqual(result.baseline_total, 0)
        self.assertEqual(result.current_total, 7)
        self.assertEqual(result.delta, 7)
        self.assertIsNone(result.pct_change)

    def test_negative_delta_gives_a_negative_pct_change(self):
        rows = [
            row("2026-02-01", "alpha", "billing", 20),
            row("2026-03-01", "alpha", "billing", 15),
        ]
        result = compute(rows, CURRENT_WINDOW, BASELINE_WINDOW)
        self.assertEqual(result.delta, -5)
        self.assertEqual(result.pct_change, -25.0)
        self.assertFalse(result.increased)

    def test_rows_outside_the_windows_are_ignored(self):
        polluted = compute(HAND_ROWS + OUT_OF_WINDOW_ROWS, CURRENT_WINDOW, BASELINE_WINDOW)
        self.assertEqual(polluted.current_total, 30)
        self.assertEqual(polluted.baseline_total, 20)
        self.assertEqual(polluted.delta, 10)
        self.assertEqual(polluted.pct_change, 50.0)
        # the out-of-window product never shows up as a group
        self.assertNotIn("delta", [g.value for g in polluted.by_product])
        self.assertEqual(as_pairs(polluted.by_product), as_pairs(self.result.by_product))

    def test_everything_out_of_window_yields_empty_result(self):
        result = compute(OUT_OF_WINDOW_ROWS, CURRENT_WINDOW, BASELINE_WINDOW)
        self.assertEqual(result.current_total, 0)
        self.assertEqual(result.baseline_total, 0)
        self.assertIsNone(result.pct_change)
        self.assertEqual(result.by_product, [])
        self.assertEqual(result.cells, [])


class ComputeGroupsTest(unittest.TestCase):
    def setUp(self):
        self.result = compute(HAND_ROWS, CURRENT_WINDOW, BASELINE_WINDOW)

    def test_per_product_group_deltas_including_one_sided_groups(self):
        self.assertEqual(
            as_pairs(self.result.by_product),
            [
                ("alpha", 24, 15, 9),
                ("gamma", 6, 0, 6),  # present only in the current period
                ("beta", 0, 5, -5),  # present only in the baseline period
            ],
        )
        self.assertEqual([g.dimension for g in self.result.by_product], ["product"] * 3)

    def test_per_complaint_type_group_deltas(self):
        self.assertEqual(
            as_pairs(self.result.by_complaint_type),
            [
                ("shipping", 6, 0, 6),
                ("billing", 20, 15, 5),
                ("login", 4, 5, -1),
            ],
        )
        self.assertEqual(
            [g.dimension for g in self.result.by_complaint_type], ["complaint_type"] * 3
        )

    def test_share_of_increase_is_zero_for_non_positive_deltas(self):
        by_value = dict((g.value, g) for g in self.result.by_product)
        self.assertEqual(by_value["beta"].share_of_increase, 0.0)
        by_type = dict((g.value, g) for g in self.result.by_complaint_type)
        self.assertEqual(by_type["login"].share_of_increase, 0.0)

    def test_share_of_increase_is_zero_when_there_is_no_increase_at_all(self):
        rows = [
            row("2026-02-01", "alpha", "billing", 10),
            row("2026-03-01", "alpha", "billing", 4),
        ]
        result = compute(rows, CURRENT_WINDOW, BASELINE_WINDOW)
        self.assertEqual([g.share_of_increase for g in result.by_product], [0.0])

    def test_positive_shares_sum_to_one(self):
        for groups in (self.result.by_product, self.result.by_complaint_type):
            positives = [g.share_of_increase for g in groups if g.delta > 0]
            self.assertTrue(positives)
            self.assertAlmostEqual(sum(positives), 1.0, places=3)

    def test_share_values_by_hand(self):
        by_value = dict((g.value, g) for g in self.result.by_product)
        self.assertEqual(by_value["alpha"].share_of_increase, 0.6)
        self.assertEqual(by_value["gamma"].share_of_increase, 0.4)
        by_type = dict((g.value, g) for g in self.result.by_complaint_type)
        self.assertAlmostEqual(by_type["shipping"].share_of_increase, 6 / 11.0, places=3)
        self.assertAlmostEqual(by_type["billing"].share_of_increase, 5 / 11.0, places=3)

    def test_cells_are_product_complaint_type_pairs_sorted_by_descending_delta(self):
        self.assertEqual(
            [(c.product, c.complaint_type, c.current, c.baseline, c.delta) for c in self.result.cells],
            [
                ("alpha", "billing", 20, 10, 10),
                ("gamma", "shipping", 6, 0, 6),
                ("alpha", "login", 4, 5, -1),
                ("beta", "billing", 0, 5, -5),
            ],
        )
        deltas = [c.delta for c in self.result.cells]
        self.assertEqual(deltas, sorted(deltas, reverse=True))

    def test_group_and_cell_order_does_not_depend_on_row_order(self):
        reordered = list(reversed(HAND_ROWS))
        other = compute(reordered, CURRENT_WINDOW, BASELINE_WINDOW)
        self.assertEqual(as_pairs(other.by_product), as_pairs(self.result.by_product))
        self.assertEqual(
            [(c.product, c.complaint_type, c.delta) for c in other.cells],
            [(c.product, c.complaint_type, c.delta) for c in self.result.cells],
        )


def build(current_counts, baseline_counts):
    """One row per product in each window; product -> count."""
    rows = []
    for product in sorted(baseline_counts):
        rows.append(row("2026-02-01", product, "billing", baseline_counts[product]))
    for product in sorted(current_counts):
        rows.append(row("2026-03-01", product, "billing", current_counts[product]))
    return compute(rows, CURRENT_WINDOW, BASELINE_WINDOW).by_product


class TopContributorsTest(unittest.TestCase):
    def test_documented_rule_constants(self):
        self.assertEqual(TOP_SHARE_TARGET, 0.8)
        self.assertEqual(TOP_MAX, 3)

    def test_one_dominant_group_returns_exactly_one_contributor(self):
        # alpha +90, beta +10 -> shares 0.9 / 0.1; 0.9 >= 0.8 after the first pick
        groups = build({"alpha": 100, "beta": 20}, {"alpha": 10, "beta": 10})
        self.assertEqual([(g.value, g.delta, g.share_of_increase) for g in groups],
                         [("alpha", 90, 0.9), ("beta", 10, 0.1)])
        picked = top_contributors(groups)
        self.assertEqual([g.value for g in picked], ["alpha"])

    def test_boundary_share_of_exactly_zero_point_eight_stops_after_one(self):
        # alpha +80, beta +20 -> shares 0.8 / 0.2; cumulative 0.8 >= 0.8 -> stop
        groups = build({"alpha": 81, "beta": 21}, {"alpha": 1, "beta": 1})
        self.assertEqual([g.share_of_increase for g in groups], [0.8, 0.2])
        self.assertEqual([g.value for g in top_contributors(groups)], ["alpha"])

    def test_four_equal_groups_stop_at_three_even_though_cumulative_is_below_target(self):
        groups = build(
            {"alpha": 20, "beta": 20, "gamma": 20, "delta": 20},
            {"alpha": 10, "beta": 10, "gamma": 10, "delta": 10},
        )
        self.assertEqual([g.delta for g in groups], [10, 10, 10, 10])
        self.assertEqual([g.share_of_increase for g in groups], [0.25] * 4)
        picked = top_contributors(groups)
        # the documented rule: stop after the element that pushes cumulative
        # share >= 0.8, OR after TOP_MAX elements -- here the cap fires first
        self.assertEqual(len(picked), TOP_MAX)
        self.assertLess(sum(g.share_of_increase for g in picked), TOP_SHARE_TARGET)
        self.assertEqual([g.value for g in picked], ["alpha", "beta", "delta"])

    def test_three_roughly_equal_groups_return_all_three(self):
        groups = build(
            {"alpha": 20, "beta": 19, "gamma": 18},
            {"alpha": 10, "beta": 10, "gamma": 10},
        )
        self.assertEqual([(g.value, g.delta) for g in groups],
                         [("alpha", 10), ("beta", 9), ("gamma", 8)])
        picked = top_contributors(groups)
        self.assertEqual([g.value for g in picked], ["alpha", "beta", "gamma"])

    def test_groups_with_non_positive_delta_are_never_returned(self):
        groups = build(
            {"alpha": 15, "beta": 15, "gamma": 2, "delta": 10},
            {"alpha": 10, "beta": 10, "gamma": 10, "delta": 10},
        )
        self.assertEqual([(g.value, g.delta) for g in groups],
                         [("alpha", 5), ("beta", 5), ("delta", 0), ("gamma", -8)])
        picked = top_contributors(groups)
        self.assertEqual([g.value for g in picked], ["alpha", "beta"])
        self.assertTrue(all(g.delta > 0 for g in picked))

    def test_all_negative_set_returns_empty_list(self):
        groups = build(
            {"alpha": 1, "beta": 2, "gamma": 3},
            {"alpha": 10, "beta": 10, "gamma": 10},
        )
        self.assertTrue(all(g.delta < 0 for g in groups))
        self.assertEqual(top_contributors(groups), [])

    def test_all_zero_set_returns_empty_list(self):
        groups = build({"alpha": 10, "beta": 10}, {"alpha": 10, "beta": 10})
        self.assertEqual([g.delta for g in groups], [0, 0])
        self.assertEqual(top_contributors(groups), [])

    def test_empty_input_returns_empty_list(self):
        self.assertEqual(top_contributors([]), [])

    def test_ties_are_broken_by_group_value_ascending_and_are_deterministic(self):
        groups = build(
            {"zulu": 20, "alpha": 20, "mike": 20, "bravo": 20},
            {"zulu": 10, "alpha": 10, "mike": 10, "bravo": 10},
        )
        self.assertEqual([g.value for g in groups], ["alpha", "bravo", "mike", "zulu"])
        first = top_contributors(groups)
        second = top_contributors(groups)
        self.assertEqual([g.value for g in first], ["alpha", "bravo", "mike"])
        self.assertEqual([g.value for g in first], [g.value for g in second])

    def test_result_top_uses_the_requested_dimension(self):
        result = compute(HAND_ROWS, CURRENT_WINDOW, BASELINE_WINDOW)
        self.assertEqual([g.value for g in result.top("product")], ["alpha", "gamma"])
        self.assertEqual(
            [g.value for g in result.top("complaint_type")], ["shipping", "billing"]
        )

    def test_hand_built_groups_are_filtered_the_same_way(self):
        groups = [
            GroupDelta("product", "alpha", 10, 1, 9, 0.9),
            GroupDelta("product", "beta", 2, 1, 1, 0.1),
            GroupDelta("product", "gamma", 0, 5, -5, 0.0),
        ]
        self.assertEqual([g.value for g in top_contributors(groups)], ["alpha"])


if __name__ == "__main__":
    unittest.main()
