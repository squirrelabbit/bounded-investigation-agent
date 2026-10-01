from __future__ import annotations

import unittest

from bia.adapters.complaints import legacy_views
from bia.analysis.qualification import (RejectedQualification, REASON_INCOMPLETE_PERIOD_COVERAGE)


class LegacyViewContractTests(unittest.TestCase):
    def test_unreachable_reason_codes_fail_closed(self):
        fake = RejectedQualification(REASON_INCOMPLETE_PERIOD_COVERAGE, (), None, None)
        with self.assertRaises(ValueError):
            legacy_views(fake)


if __name__ == "__main__":
    unittest.main()
