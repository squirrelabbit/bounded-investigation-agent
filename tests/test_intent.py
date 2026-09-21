"""Tests for the single supported question shape (bia.types)."""
import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bia.types import (  # noqa: E402
    ALLOWED_BREAKDOWNS,
    AnalysisIntent,
    Period,
    UnsupportedIntent,
)

BASELINE = Period.of("2026-01-01", "2026-01-31")
CURRENT = Period.of("2026-02-01", "2026-02-28")


def supported(**overrides):
    kwargs = {"current_period": CURRENT, "baseline_period": BASELINE}
    kwargs.update(overrides)
    return AnalysisIntent(**kwargs)


class PeriodTest(unittest.TestCase):
    def test_days_is_inclusive(self):
        self.assertEqual(Period.of("2026-01-01", "2026-01-01").days, 1)
        self.assertEqual(Period.of("2026-01-01", "2026-01-31").days, 31)
        self.assertEqual(Period.of("2026-02-01", "2026-02-28").days, 28)

    def test_contains_is_inclusive_on_both_ends(self):
        period = Period.of("2026-03-10", "2026-03-12")
        self.assertTrue(period.contains(dt.date(2026, 3, 10)))
        self.assertTrue(period.contains(dt.date(2026, 3, 11)))
        self.assertTrue(period.contains(dt.date(2026, 3, 12)))
        self.assertFalse(period.contains(dt.date(2026, 3, 9)))
        self.assertFalse(period.contains(dt.date(2026, 3, 13)))

    def test_dates_lists_every_day_in_order(self):
        period = Period.of("2026-03-10", "2026-03-12")
        self.assertEqual(
            period.dates(),
            [dt.date(2026, 3, 10), dt.date(2026, 3, 11), dt.date(2026, 3, 12)],
        )

    def test_sub_is_offset_from_period_start_inclusive(self):
        period = Period.of("2026-03-01", "2026-03-30")
        window = period.sub(0, 11)
        self.assertEqual(window.start, dt.date(2026, 3, 1))
        self.assertEqual(window.end, dt.date(2026, 3, 12))
        self.assertEqual(window.days, 12)

        window = period.sub(12, 29)
        self.assertEqual(window.start, dt.date(2026, 3, 13))
        self.assertEqual(window.end, dt.date(2026, 3, 30))
        self.assertEqual(window.days, 18)

    def test_sub_of_a_single_day(self):
        period = Period.of("2026-03-01", "2026-03-30")
        window = period.sub(5, 5)
        self.assertEqual(window.start, dt.date(2026, 3, 6))
        self.assertEqual(window.end, dt.date(2026, 3, 6))
        self.assertEqual(window.days, 1)

    def test_rejects_end_before_start(self):
        with self.assertRaises(ValueError):
            Period.of("2026-03-02", "2026-03-01")
        with self.assertRaises(ValueError):
            Period(dt.date(2026, 3, 2), dt.date(2026, 3, 1))

    def test_accepts_single_day_period(self):
        period = Period.of("2026-03-01", "2026-03-01")
        self.assertEqual(period.days, 1)


class AnalysisIntentValidateTest(unittest.TestCase):
    def test_supported_shape_is_accepted(self):
        intent = supported()
        self.assertIsNone(intent.validate())
        self.assertEqual(tuple(intent.breakdowns), ALLOWED_BREAKDOWNS)

    def test_other_metric_is_rejected(self):
        with self.assertRaises(UnsupportedIntent) as ctx:
            supported(metric="revenue").validate()
        self.assertIn("metric", str(ctx.exception))

    def test_other_evidence_source_is_rejected(self):
        with self.assertRaises(UnsupportedIntent) as ctx:
            supported(evidence_source="app_reviews").validate()
        self.assertIn("evidence_source", str(ctx.exception))

    def test_other_claim_policy_is_rejected(self):
        with self.assertRaises(UnsupportedIntent) as ctx:
            supported(claim_policy="causal").validate()
        self.assertIn("claim_policy", str(ctx.exception))

    def test_other_breakdowns_are_rejected(self):
        for breakdowns in (
            ("product",),
            ("complaint_type", "product"),
            ("product", "complaint_type", "region"),
            (),
        ):
            with self.assertRaises(UnsupportedIntent) as ctx:
                supported(breakdowns=breakdowns).validate()
            self.assertIn("breakdowns", str(ctx.exception))

    def test_current_period_must_start_after_baseline_ends(self):
        # current starts before baseline ends
        with self.assertRaises(UnsupportedIntent) as ctx:
            AnalysisIntent(
                current_period=Period.of("2026-01-15", "2026-02-14"),
                baseline_period=BASELINE,
            ).validate()
        self.assertIn("current_period", str(ctx.exception))

        # current starts exactly on the baseline's last day -> still rejected
        with self.assertRaises(UnsupportedIntent):
            AnalysisIntent(
                current_period=Period.of("2026-01-31", "2026-02-28"),
                baseline_period=BASELINE,
            ).validate()

    def test_current_period_starting_the_day_after_baseline_is_accepted(self):
        AnalysisIntent(
            current_period=Period.of("2026-02-01", "2026-02-28"),
            baseline_period=BASELINE,
        ).validate()

    def test_unsupported_intent_is_a_value_error(self):
        self.assertTrue(issubclass(UnsupportedIntent, ValueError))

    def test_as_dict_reports_the_fixed_shape(self):
        data = supported().as_dict()
        self.assertEqual(data["metric"], "complaint_count")
        self.assertEqual(data["evidence_source"], "support_tickets")
        self.assertEqual(data["claim_policy"], "association_only")
        self.assertEqual(data["breakdowns"], ["product", "complaint_type"])
        self.assertEqual(data["current_period"]["days"], 28)
        self.assertEqual(data["baseline_period"]["days"], 31)


if __name__ == "__main__":
    unittest.main()
