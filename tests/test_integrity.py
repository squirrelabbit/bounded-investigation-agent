"""Tests for bia.integrity: dedupe, period completeness, comparability.

Every guard here is exercised with an input that ACTUALLY fires it and with a
clean input that must not fire it. A test that only walks the happy path and
gets an empty result back is a false pass.
"""
import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bia.integrity import (  # noqa: E402
    MIN_WINDOW_DAYS,
    MIN_WINDOW_FRACTION,
    MODE_ALIGNED_WINDOW,
    MODE_BLOCKED,
    MODE_FULL,
    decide_comparability,
    dedupe_rows,
    inspect_period,
)
from bia.types import MetricRow, Period  # noqa: E402

CURRENT_30 = Period.of("2026-03-01", "2026-03-30")
BASELINE_30 = Period.of("2026-01-01", "2026-01-30")


def row(day, product="alpha", complaint_type="billing", count=1):
    if isinstance(day, str):
        day = dt.date.fromisoformat(day)
    return MetricRow(day=day, product=product, complaint_type=complaint_type, count=count)


def rows_at_offsets(period, offsets, product="alpha", complaint_type="billing", count=1):
    return [
        row(period.start + dt.timedelta(days=o), product, complaint_type, count)
        for o in offsets
    ]


def integrity_at(period, offsets, extra_rows=None):
    rows = rows_at_offsets(period, offsets)
    if extra_rows:
        rows = rows + list(extra_rows)
    _, integrity = inspect_period(rows, period)
    return integrity


def total(rows):
    return sum(r.count for r in rows)


class DedupeRowsTest(unittest.TestCase):
    def test_exact_duplicates_are_collapsed_and_counted(self):
        base = [
            row("2026-03-01", "alpha", "billing", 7),
            row("2026-03-01", "beta", "login", 3),
            row("2026-03-02", "alpha", "billing", 5),
        ]
        rows = base + [
            row("2026-03-01", "alpha", "billing", 7),  # exact duplicate
            row("2026-03-01", "alpha", "billing", 7),  # again
            row("2026-03-02", "alpha", "billing", 5),  # exact duplicate
        ]
        clean, duplicates, conflicts = dedupe_rows(rows)

        self.assertEqual(duplicates, 3)
        self.assertEqual(conflicts, [])
        self.assertEqual(len(clean), 3)
        # surviving totals are the distinct rows' totals, not the inflated input
        self.assertEqual(total(clean), 15)
        self.assertEqual(total(clean), total(base))
        self.assertNotEqual(total(clean), total(rows))

    def test_same_key_with_different_counts_is_reported_as_conflicting(self):
        rows = [
            row("2026-03-01", "alpha", "billing", 7),
            row("2026-03-01", "alpha", "billing", 9),  # same key, different count
            row("2026-03-02", "beta", "login", 4),
        ]
        clean, duplicates, conflicts = dedupe_rows(rows)

        self.assertEqual(conflicts, ["2026-03-01|alpha|billing"])
        # the disagreement is NOT counted as a harmless duplicate
        self.assertEqual(duplicates, 0)
        # and it is not silently resolved into two rows either
        self.assertEqual(len(clean), 2)

    def test_conflicting_key_is_reported_once_even_with_many_repeats(self):
        rows = [
            row("2026-03-01", "alpha", "billing", 7),
            row("2026-03-01", "alpha", "billing", 9),
            row("2026-03-01", "alpha", "billing", 11),
            row("2026-03-01", "alpha", "billing", 7),  # exact duplicate of the first
        ]
        clean, duplicates, conflicts = dedupe_rows(rows)
        self.assertEqual(conflicts, ["2026-03-01|alpha|billing"])
        self.assertEqual(duplicates, 1)
        self.assertEqual(len(clean), 1)

    def test_clean_row_set_reports_zero_duplicates_and_zero_conflicts(self):
        rows = [
            row("2026-03-01", "alpha", "billing", 7),
            row("2026-03-01", "beta", "login", 3),
            row("2026-03-02", "alpha", "billing", 5),
        ]
        clean, duplicates, conflicts = dedupe_rows(rows)
        self.assertEqual(duplicates, 0)
        self.assertEqual(conflicts, [])
        self.assertEqual(len(clean), 3)
        self.assertEqual(total(clean), total(rows))

    def test_empty_input(self):
        clean, duplicates, conflicts = dedupe_rows([])
        self.assertEqual((clean, duplicates, conflicts), ([], 0, []))

    def test_output_order_is_stable_and_sorted_regardless_of_input_order(self):
        rows = [
            row("2026-03-02", "beta", "login", 1),
            row("2026-03-01", "alpha", "billing", 2),
            row("2026-03-01", "beta", "billing", 3),
            row("2026-03-02", "alpha", "shipping", 4),
            row("2026-03-01", "alpha", "shipping", 5),
        ]
        shuffled_a = [rows[3], rows[0], rows[4], rows[2], rows[1]]
        shuffled_b = [rows[1], rows[2], rows[3], rows[4], rows[0]]

        clean_a = dedupe_rows(shuffled_a)[0]
        clean_b = dedupe_rows(shuffled_b)[0]

        self.assertEqual([r.key for r in clean_a], [r.key for r in clean_b])
        self.assertEqual(
            [r.key for r in clean_a],
            [
                ("2026-03-01", "alpha", "billing"),
                ("2026-03-01", "alpha", "shipping"),
                ("2026-03-01", "beta", "billing"),
                ("2026-03-02", "alpha", "shipping"),
                ("2026-03-02", "beta", "login"),
            ],
        )
        self.assertEqual([r.key for r in clean_a], sorted(r.key for r in rows))


class InspectPeriodTest(unittest.TestCase):
    def setUp(self):
        self.period = Period.of("2026-03-01", "2026-03-05")

    def test_rows_outside_the_period_are_excluded(self):
        rows = [
            row("2026-02-28", count=100),  # before
            row("2026-03-01", count=1),
            row("2026-03-05", count=2),
            row("2026-03-06", count=100),  # after
        ]
        clean, integrity = inspect_period(rows, self.period)
        self.assertEqual([r.day.isoformat() for r in clean], ["2026-03-01", "2026-03-05"])
        self.assertEqual(total(clean), 3)
        self.assertEqual(integrity.observed_days, 2)
        self.assertEqual(integrity.missing_days, ["2026-03-02", "2026-03-03", "2026-03-04"])

    def test_missing_days_lists_exactly_the_absent_dates_in_iso_order(self):
        rows = rows_at_offsets(self.period, [4, 0, 2])
        _, integrity = inspect_period(rows, self.period)
        self.assertEqual(integrity.missing_days, ["2026-03-02", "2026-03-04"])
        self.assertEqual(integrity.missing_days, sorted(integrity.missing_days))

    def test_full_period_is_complete(self):
        rows = rows_at_offsets(self.period, range(5))
        _, integrity = inspect_period(rows, self.period)
        self.assertEqual(integrity.expected_days, 5)
        self.assertEqual(integrity.observed_days, 5)
        self.assertEqual(integrity.missing_days, [])
        self.assertEqual(integrity.completeness, 1.0)
        self.assertTrue(integrity.complete)

    def test_one_missing_day(self):
        rows = rows_at_offsets(self.period, [0, 1, 2, 4])
        _, integrity = inspect_period(rows, self.period)
        self.assertEqual(integrity.observed_days, 4)
        self.assertEqual(integrity.missing_days, ["2026-03-04"])
        self.assertEqual(integrity.completeness, 0.8)
        self.assertFalse(integrity.complete)

    def test_empty_period(self):
        _, integrity = inspect_period([], self.period)
        self.assertEqual(integrity.observed_days, 0)
        self.assertEqual(integrity.completeness, 0.0)
        self.assertFalse(integrity.complete)
        self.assertEqual(
            integrity.missing_days,
            ["2026-03-01", "2026-03-02", "2026-03-03", "2026-03-04", "2026-03-05"],
        )
        self.assertEqual(integrity.present_offsets, [])

    def test_conflicting_key_makes_the_period_incomplete_even_when_all_days_present(self):
        rows = rows_at_offsets(self.period, range(5))
        rows.append(row("2026-03-03", "alpha", "billing", 99))  # conflicts with count=1
        _, integrity = inspect_period(rows, self.period)
        self.assertEqual(integrity.observed_days, 5)
        self.assertEqual(integrity.completeness, 1.0)
        self.assertEqual(integrity.conflicting_keys, ["2026-03-03|alpha|billing"])
        self.assertFalse(integrity.complete)

    def test_duplicate_rows_removed_is_reported(self):
        rows = rows_at_offsets(self.period, range(5))
        rows.append(row("2026-03-03", "alpha", "billing", 1))  # exact duplicate
        _, integrity = inspect_period(rows, self.period)
        self.assertEqual(integrity.duplicate_rows_removed, 1)
        self.assertEqual(integrity.conflicting_keys, [])
        self.assertTrue(integrity.complete)

    def test_present_offsets_are_day_offsets_from_the_period_start(self):
        rows = rows_at_offsets(self.period, [0, 2, 4])
        _, integrity = inspect_period(rows, self.period)
        self.assertEqual(integrity.present_offsets, [0, 2, 4])

        # several rows on the same day collapse to one offset
        rows = rows_at_offsets(self.period, [3]) + [
            row("2026-03-04", "beta", "login", 2),
            row("2026-03-04", "gamma", "shipping", 5),
        ]
        _, integrity = inspect_period(rows, self.period)
        self.assertEqual(integrity.present_offsets, [3])

    def test_rows_outside_the_period_do_not_appear_in_present_offsets(self):
        rows = rows_at_offsets(self.period, [1]) + [row("2026-04-10")]
        _, integrity = inspect_period(rows, self.period)
        self.assertEqual(integrity.present_offsets, [1])


class DecideComparabilityTest(unittest.TestCase):
    def test_threshold_constants_are_what_the_cases_below_assume(self):
        # limit = 30 -> threshold = max(7, round(0.5 * 30)) = 15
        self.assertEqual(MIN_WINDOW_DAYS, 7)
        self.assertEqual(MIN_WINDOW_FRACTION, 0.5)
        self.assertEqual(max(MIN_WINDOW_DAYS, int(round(MIN_WINDOW_FRACTION * 30))), 15)

    def test_both_complete_and_same_length_is_full(self):
        current = integrity_at(CURRENT_30, range(30))
        baseline = integrity_at(BASELINE_30, range(30))
        verdict = decide_comparability(current, baseline)

        self.assertEqual(verdict.mode, MODE_FULL)
        self.assertTrue(verdict.usable)
        self.assertFalse(verdict.is_partial)
        self.assertEqual(verdict.current_window, CURRENT_30)
        self.assertEqual(verdict.baseline_window, BASELINE_30)

    def test_current_delivered_only_for_its_first_12_of_30_days_is_blocked(self):
        """12 of 30 is below the 50% threshold (15), so it is BLOCKED, not windowed.

        NOTE (reported to the caller): the task asked for `aligned_window` with a
        12-day window here. That is incompatible with the module's documented
        MIN_WINDOW_FRACTION = 0.5 rule and with the 15-accepted / 14-rejected
        boundary also required by the task. This test asserts the module's actual,
        self-consistent behaviour. See test_current_delivered_only_for_its_first_20
        for the prefix-window shape at an accepted size.
        """
        current = integrity_at(CURRENT_30, range(12))
        baseline = integrity_at(BASELINE_30, range(30))
        verdict = decide_comparability(current, baseline)

        self.assertEqual(verdict.mode, MODE_BLOCKED)
        self.assertIn("no_comparable_window", verdict.reason)
        self.assertIn("12 day(s)", verdict.reason)
        self.assertIn("below the required 15", verdict.reason)
        self.assertIsNone(verdict.current_window)
        self.assertIsNone(verdict.baseline_window)

    def test_current_delivered_only_for_its_first_20_of_30_days_is_aligned_window(self):
        current = integrity_at(CURRENT_30, range(20))
        baseline = integrity_at(BASELINE_30, range(30))
        verdict = decide_comparability(current, baseline)

        self.assertEqual(verdict.mode, MODE_ALIGNED_WINDOW)
        self.assertTrue(verdict.is_partial)
        self.assertEqual(verdict.current_window, Period.of("2026-03-01", "2026-03-20"))
        self.assertEqual(verdict.baseline_window, Period.of("2026-01-01", "2026-01-20"))
        self.assertEqual(verdict.current_window.days, 20)
        self.assertEqual(verdict.baseline_window.days, 20)

    def test_two_missing_days_in_the_middle_window_on_the_longest_run(self):
        present = [o for o in range(30) if o not in (10, 11)]
        current = integrity_at(CURRENT_30, present)
        baseline = integrity_at(BASELINE_30, range(30))
        verdict = decide_comparability(current, baseline)

        self.assertEqual(verdict.mode, MODE_ALIGNED_WINDOW)
        # runs are offsets 0..9 (10 days) and 12..29 (18 days); the longer one wins
        self.assertEqual(verdict.current_window, CURRENT_30.sub(12, 29))
        self.assertEqual(verdict.baseline_window, BASELINE_30.sub(12, 29))
        self.assertEqual(verdict.current_window, Period.of("2026-03-13", "2026-03-30"))
        self.assertEqual(verdict.baseline_window, Period.of("2026-01-13", "2026-01-30"))
        self.assertEqual(verdict.current_window.days, 18)

    def test_current_delivered_only_for_its_first_5_of_30_days_is_blocked_as_too_short(self):
        current = integrity_at(CURRENT_30, range(5))
        baseline = integrity_at(BASELINE_30, range(30))
        verdict = decide_comparability(current, baseline)

        self.assertEqual(verdict.mode, MODE_BLOCKED)
        self.assertFalse(verdict.usable)
        self.assertIn("no_comparable_window", verdict.reason)
        self.assertIn("5 day(s)", verdict.reason)
        self.assertIn("below the required 15", verdict.reason)
        self.assertIsNone(verdict.current_window)

    def test_every_third_day_missing_is_blocked(self):
        present = [o for o in range(30) if o % 3 != 2]
        current = integrity_at(CURRENT_30, present)
        baseline = integrity_at(BASELINE_30, range(30))
        verdict = decide_comparability(current, baseline)

        self.assertEqual(verdict.mode, MODE_BLOCKED)
        self.assertIn("longest aligned window is 2 day(s)", verdict.reason)
        self.assertIsNone(verdict.current_window)

    def test_conflicting_duplicate_in_current_blocks_two_otherwise_complete_periods(self):
        conflicting = [row(CURRENT_30.start + dt.timedelta(days=7), "alpha", "billing", 42)]
        current = integrity_at(CURRENT_30, range(30), extra_rows=conflicting)
        baseline = integrity_at(BASELINE_30, range(30))
        self.assertEqual(current.observed_days, 30)  # otherwise complete
        self.assertTrue(current.conflicting_keys)

        verdict = decide_comparability(current, baseline)
        self.assertEqual(verdict.mode, MODE_BLOCKED)
        self.assertIn("conflicting_duplicate_rows", verdict.reason)
        self.assertIsNone(verdict.current_window)
        self.assertIsNone(verdict.baseline_window)

    def test_conflicting_duplicate_in_baseline_blocks_too(self):
        conflicting = [row(BASELINE_30.start + dt.timedelta(days=3), "alpha", "billing", 42)]
        current = integrity_at(CURRENT_30, range(30))
        baseline = integrity_at(BASELINE_30, range(30), extra_rows=conflicting)

        verdict = decide_comparability(current, baseline)
        self.assertEqual(verdict.mode, MODE_BLOCKED)
        self.assertIn("conflicting_duplicate_rows", verdict.reason)

    def test_exact_duplicates_alone_do_not_block(self):
        duplicate = [row(CURRENT_30.start + dt.timedelta(days=7), "alpha", "billing", 1)]
        current = integrity_at(CURRENT_30, range(30), extra_rows=duplicate)
        baseline = integrity_at(BASELINE_30, range(30))
        self.assertEqual(current.duplicate_rows_removed, 1)

        verdict = decide_comparability(current, baseline)
        self.assertEqual(verdict.mode, MODE_FULL)

    def test_empty_current_period_is_blocked(self):
        current = integrity_at(CURRENT_30, [])
        baseline = integrity_at(BASELINE_30, range(30))
        verdict = decide_comparability(current, baseline)
        self.assertEqual(verdict.mode, MODE_BLOCKED)
        self.assertIn("empty_period", verdict.reason)

    def test_empty_baseline_period_is_blocked(self):
        current = integrity_at(CURRENT_30, range(30))
        baseline = integrity_at(BASELINE_30, [])
        verdict = decide_comparability(current, baseline)
        self.assertEqual(verdict.mode, MODE_BLOCKED)
        self.assertIn("empty_period", verdict.reason)

    def test_threshold_boundary_exactly_15_of_30_is_accepted(self):
        current = integrity_at(CURRENT_30, range(15))
        baseline = integrity_at(BASELINE_30, range(30))
        verdict = decide_comparability(current, baseline)

        self.assertEqual(verdict.mode, MODE_ALIGNED_WINDOW)
        self.assertEqual(verdict.current_window, CURRENT_30.sub(0, 14))
        self.assertEqual(verdict.baseline_window, BASELINE_30.sub(0, 14))
        self.assertEqual(verdict.current_window.days, 15)

    def test_threshold_boundary_exactly_14_of_30_is_blocked(self):
        current = integrity_at(CURRENT_30, range(14))
        baseline = integrity_at(BASELINE_30, range(30))
        verdict = decide_comparability(current, baseline)

        self.assertEqual(verdict.mode, MODE_BLOCKED)
        self.assertIn("longest aligned window is 14 day(s)", verdict.reason)
        self.assertIn("below the required 15", verdict.reason)
        self.assertIsNone(verdict.current_window)

    def test_no_shared_offset_at_all_is_blocked(self):
        current = integrity_at(CURRENT_30, [o for o in range(30) if o % 2 == 0])
        baseline = integrity_at(BASELINE_30, [o for o in range(30) if o % 2 == 1])
        verdict = decide_comparability(current, baseline)
        self.assertEqual(verdict.mode, MODE_BLOCKED)
        self.assertIn("no_comparable_window", verdict.reason)

    def test_both_complete_but_different_lengths_falls_through_to_the_window_rule(self):
        short_current = Period.of("2026-03-01", "2026-03-20")  # 20 days
        current = integrity_at(short_current, range(20))
        baseline = integrity_at(BASELINE_30, range(30))
        verdict = decide_comparability(current, baseline)

        # limit = 20, threshold = max(7, 10) = 10, common run = 0..19 (20 days)
        self.assertEqual(verdict.mode, MODE_ALIGNED_WINDOW)
        self.assertEqual(verdict.current_window, short_current.sub(0, 19))
        self.assertEqual(verdict.baseline_window, BASELINE_30.sub(0, 19))


if __name__ == "__main__":
    unittest.main()
