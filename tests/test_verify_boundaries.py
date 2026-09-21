"""Verification must reject on inputs that actually trigger each check."""
from __future__ import annotations

import datetime as _dt
import unittest

from bia import verify as verify_mod
from bia.types import EvidenceFilter, Period, Ticket

from . import support

WINDOW = support.CURRENT
FILTER = EvidenceFilter(WINDOW, support.SPIKE_PRODUCT, support.SPIKE_TYPE)


def ticket(**overrides) -> Ticket:
    base = dict(
        ticket_id="T-0001",
        day=_dt.date(2026, 7, 3),
        product=support.SPIKE_PRODUCT,
        complaint_type=support.SPIKE_TYPE,
        text=support.SUPPORTING_TEXT,
        source="web_form",
    )
    base.update(overrides)
    return Ticket(**base)


class RejectionTests(unittest.TestCase):
    def _reason(self, item: Ticket, known=None):
        known = {"T-0001"} if known is None else known
        result = verify_mod.verify([item], FILTER, known, pool_size=1)
        self.assertEqual(len(result.rejected), 1, "expected a rejection, got %s" % result.as_dict())
        self.assertEqual(result.admitted, [])
        return result.rejected[0]["reason"]

    def test_clean_ticket_is_admitted(self):
        result = verify_mod.verify([ticket()], FILTER, {"T-0001"}, pool_size=1)
        self.assertEqual([a.ticket_id for a in result.admitted], ["T-0001"])
        self.assertEqual(result.rejected, [])
        self.assertEqual(result.coverage, 1.0)

    def test_ticket_id_not_in_store_is_rejected(self):
        self.assertEqual(self._reason(ticket(), known=set()), verify_mod.REJECT_UNKNOWN_ID)

    def test_source_outside_the_allowed_list_is_rejected(self):
        self.assertEqual(
            self._reason(ticket(source="legacy_import")), verify_mod.REJECT_BAD_SOURCE
        )

    def test_ticket_outside_the_comparison_window_is_rejected(self):
        self.assertEqual(
            self._reason(ticket(day=_dt.date(2026, 6, 15))), verify_mod.REJECT_OUT_OF_WINDOW
        )

    def test_ticket_from_another_product_is_rejected(self):
        self.assertEqual(
            self._reason(ticket(product="P-Alpha")), verify_mod.REJECT_FILTER_MISMATCH
        )

    def test_ticket_from_another_complaint_type_is_rejected(self):
        self.assertEqual(
            self._reason(ticket(complaint_type="app_crash")), verify_mod.REJECT_FILTER_MISMATCH
        )

    def test_text_that_does_not_support_its_own_label_is_rejected(self):
        self.assertEqual(
            self._reason(ticket(text=support.UNSUPPORTING_TEXT)),
            verify_mod.REJECT_UNSUPPORTED_TEXT,
        )


class SufficiencyTests(unittest.TestCase):
    def _result(self, admitted_count: int, pool: int):
        items = [ticket(ticket_id="T-%04d" % i) for i in range(1, admitted_count + 1)]
        known = {t.ticket_id for t in items}
        return verify_mod.verify(items, FILTER, known, pool_size=pool)

    def test_three_admitted_at_half_coverage_is_sufficient(self):
        result = self._result(3, 6)
        self.assertEqual(result.coverage, 0.5)
        self.assertTrue(result.sufficient)

    def test_two_admitted_is_below_the_evidence_floor(self):
        self.assertFalse(self._result(2, 2).sufficient)

    def test_coverage_below_half_is_not_sufficient(self):
        result = self._result(4, 10)
        self.assertEqual(result.coverage, 0.4)
        self.assertFalse(result.sufficient)

    def test_empty_pool_gives_zero_coverage_and_no_crash(self):
        result = verify_mod.verify([], FILTER, set(), pool_size=0)
        self.assertEqual(result.coverage, 0.0)
        self.assertFalse(result.sufficient)


if __name__ == "__main__":
    unittest.main()
