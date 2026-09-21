"""The answer may not carry an unsourced number or any causal vocabulary."""
from __future__ import annotations

import unittest

from bia.answer import (
    AnswerDocument,
    CausalClaimError,
    UnsupportedNumberError,
    build_answer,
    causal_terms_in,
)
from bia.controller import investigate
from bia.decision import DeterministicHeuristicSelector

from . import support


class NumberGuardTests(unittest.TestCase):
    def test_a_number_that_was_never_registered_is_rejected(self):
        doc = AnswerDocument(status="reported")
        doc.confirmed.append("불만 건수가 42건 늘었다")
        with self.assertRaises(UnsupportedNumberError):
            doc.check()

    def test_a_registered_number_passes(self):
        doc = AnswerDocument(status="reported")
        doc.confirmed.append("불만 건수가 %s건 늘었다" % doc.num(42))
        doc.check()

    def test_numbers_inside_quoted_customer_text_do_not_need_registering(self):
        doc = AnswerDocument(status="reported")
        doc.evidence.append("T-0001: %s" % doc.quote("Order 99123 is late and has not arrived."))
        doc.allowed_numbers.update({"0001"})
        doc.check()


class CausalGuardTests(unittest.TestCase):
    def test_causal_vocabulary_in_a_claim_is_rejected(self):
        doc = AnswerDocument(status="reported")
        doc.confirmed.append("배송 지연 때문에 불만이 늘었다")
        with self.assertRaises(CausalClaimError):
            doc.check()

    def test_english_causal_vocabulary_in_a_claim_is_rejected(self):
        doc = AnswerDocument(status="reported")
        doc.confirmed.append("the increase was caused by shipping")
        with self.assertRaises(CausalClaimError):
            doc.check()

    def test_causal_vocabulary_inside_quoted_customer_text_is_allowed(self):
        doc = AnswerDocument(status="reported")
        doc.evidence.append("T-0001: %s" % doc.quote("I am upset because the parcel is late."))
        doc.allowed_numbers.update({"0001"})
        doc.check()
        self.assertTrue(causal_terms_in("I am upset because the parcel is late."))


class RenderedAnswerTests(unittest.TestCase):
    def setUp(self):
        self.result = investigate(
            support.intent(), support.rows(), support.tickets(), DeterministicHeuristicSelector()
        )
        self.text = self.result.answer.render()

    def test_the_answer_has_the_three_required_sections(self):
        self.assertIn("## 확인된 사실", self.text)
        self.assertIn("## 관련 근거", self.text)
        self.assertIn("## 아직 확인되지 않은 것", self.text)

    def test_the_claim_text_carries_no_causal_vocabulary(self):
        self.assertEqual(causal_terms_in(self.result.answer.claim_text()), [])

    def test_the_unknown_section_states_that_causation_is_not_established(self):
        unknown = "\n".join(self.result.answer.unknown)
        self.assertIn("확인되지 않는다", unknown)

    def test_contribution_is_worded_as_location_not_explanation(self):
        confirmed = "\n".join(self.result.answer.confirmed)
        self.assertIn("증가분이 발생한 위치", confirmed)

    def test_a_blocked_run_reports_no_totals(self):
        broken = [r for r in support.rows() if r.day.day <= 3]
        state = investigate(
            support.intent(), broken, support.tickets(), DeterministicHeuristicSelector()
        )
        self.assertEqual(state.answer.status, "abstained")
        self.assertIsNone(state.state.metrics)
        self.assertNotIn("차이", "\n".join(state.answer.confirmed))


if __name__ == "__main__":
    unittest.main()
