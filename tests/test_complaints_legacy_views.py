from __future__ import annotations

import unittest

from bia.adapters.complaints import legacy_views
from bia.analysis.qualification import (RejectedQualification, REASON_INCOMPLETE_PERIOD_COVERAGE,
                                        REASON_INSUFFICIENT_COMMON_WINDOW, qualify)
from bia.complaint_analysis import analyze_complaints
from tests.test_qualification import STRICT, _frame, _plan


class LegacyViewContractTests(unittest.TestCase):
    def test_unreachable_reason_codes_fail_closed(self):
        fake = RejectedQualification(REASON_INCOMPLETE_PERIOD_COVERAGE, (), None, None)
        with self.assertRaises(ValueError):
            legacy_views(fake)

    def test_unknown_window_kind_fails_closed(self):
        real = qualify(_plan(STRICT), _frame(range(30), range(28)))
        fake = RejectedQualification(
            REASON_INSUFFICIENT_COMMON_WINDOW,
            (("kind", "unheard_of"), ("length", 3), ("threshold", 7)), real.facts, real.plan)
        with self.assertRaises(ValueError):
            legacy_views(fake)

    def test_analyze_complaints_refuses_a_rejected_qualification(self):
        rejected = qualify(_plan(STRICT), _frame(range(30), range(28)))
        self.assertIsInstance(rejected, RejectedQualification)
        with self.assertRaises(TypeError):
            analyze_complaints(rejected)


if __name__ == "__main__":
    unittest.main()
