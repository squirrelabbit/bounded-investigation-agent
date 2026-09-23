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
