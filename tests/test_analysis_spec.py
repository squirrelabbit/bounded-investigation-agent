from __future__ import annotations

import unittest

from bia.analysis.errors import SpecError
from bia.analysis.spec import DomainSpec, MetricSpec


class MetricSpecTests(unittest.TestCase):
    def test_additive_requires_a_value_column(self):
        with self.assertRaises(SpecError):
            MetricSpec(name="revenue", kind="additive")

    def test_ratio_requires_both_numerator_and_denominator(self):
        with self.assertRaises(SpecError):
            MetricSpec(name="cr", kind="ratio", numerator="orders")

    def test_additive_rejects_ratio_columns(self):
        with self.assertRaises(SpecError):
            MetricSpec(name="revenue", kind="additive", value="v", numerator="orders")

    def test_unknown_kind_is_rejected(self):
        with self.assertRaises(SpecError):
            MetricSpec(name="x", kind="cumulative", value="v")

    def test_valid_specs_construct(self):
        additive = MetricSpec(name="revenue", kind="additive", value="revenue_krw")
        ratio = MetricSpec(
            name="conversion_rate", kind="ratio",
            numerator="orders", denominator="sessions",
            numerator_bounded_by_denominator=True,
        )
        self.assertEqual(additive.kind, "additive")
        self.assertTrue(ratio.numerator_bounded_by_denominator)


class DomainSpecTests(unittest.TestCase):
    def _metric(self):
        return MetricSpec(name="m", kind="additive", value="count")

    def test_dimensions_must_be_a_subset_of_grain(self):
        with self.assertRaises(SpecError):
            DomainSpec(name="d", grain=("day", "a"), dimensions=("a", "b"),
                       metrics={"m": self._metric()})

    def test_grain_must_start_with_day(self):
        with self.assertRaises(SpecError):
            DomainSpec(name="d", grain=("a", "day"), dimensions=("a",),
                       metrics={"m": self._metric()})

    def test_metric_key_must_match_its_name(self):
        with self.assertRaises(SpecError):
            DomainSpec(name="d", grain=("day", "a"), dimensions=("a",),
                       metrics={"wrong": self._metric()})

    def test_valid_domain_constructs(self):
        spec = DomainSpec(name="d", grain=("day", "a"), dimensions=("a",),
                          metrics={"m": self._metric()})
        self.assertEqual(spec.dimensions, ("a",))


class PolicyFieldTests(unittest.TestCase):
    def _spec(self, **kw):
        base = dict(name="t_policy", grain=("day", "g"), dimensions=("g",),
                    metrics={"m": MetricSpec(name="m", kind="additive", value="v")})
        base.update(kw)
        return DomainSpec(**base)

    def test_defaults_are_strict(self):
        spec = self._spec()
        self.assertEqual(spec.partial_period_policy, "reject")
        self.assertEqual(spec.null_dimension_policy, "reject")
        self.assertIsNone(spec.min_comparable_days)
        self.assertIsNone(spec.min_comparable_ratio)

    def test_align_with_both_thresholds_is_valid(self):
        spec = self._spec(partial_period_policy="align_common_window",
                          min_comparable_days=7, min_comparable_ratio=0.5)
        self.assertEqual(spec.min_comparable_days, 7)

    def test_align_missing_a_threshold_is_an_error(self):
        with self.assertRaises(SpecError):
            self._spec(partial_period_policy="align_common_window", min_comparable_days=7)
        with self.assertRaises(SpecError):
            self._spec(partial_period_policy="align_common_window", min_comparable_ratio=0.5)

    def test_reject_with_any_threshold_is_an_error(self):
        with self.assertRaises(SpecError):
            self._spec(min_comparable_days=7)
        with self.assertRaises(SpecError):
            self._spec(min_comparable_ratio=0.5)

    def test_threshold_ranges(self):
        for days, ratio in ((0, 0.5), (7, 0.0), (7, 1.5)):
            with self.assertRaises(SpecError):
                self._spec(partial_period_policy="align_common_window",
                           min_comparable_days=days, min_comparable_ratio=ratio)

    def test_unknown_policy_values_are_errors(self):
        with self.assertRaises(SpecError):
            self._spec(partial_period_policy="block")
        with self.assertRaises(SpecError):
            self._spec(null_dimension_policy="drop")

    def test_metrics_mapping_cannot_be_mutated(self):
        spec = self._spec()
        with self.assertRaises(TypeError):
            spec.metrics["x"] = MetricSpec(name="x", kind="additive", value="v")

    def test_existing_domains_declare_current_behaviour(self):
        import bia.domains.complaints as c
        import bia.domains.ecommerce as e
        import bia.domains.support_ops as s
        self.assertEqual((c.SPEC.partial_period_policy, c.SPEC.min_comparable_days,
                          c.SPEC.min_comparable_ratio, c.SPEC.null_dimension_policy),
                         ("align_common_window", 7, 0.5, "unknown_group"))
        for spec in (e.SPEC, s.SPEC):
            self.assertEqual((spec.partial_period_policy, spec.null_dimension_policy),
                             ("reject", "unknown_group"))
