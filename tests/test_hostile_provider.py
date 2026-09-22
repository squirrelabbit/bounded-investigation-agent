"""A DecisionProvider gets a snapshot, not the run. Regression tests for the
defects found in adversarial review."""
from __future__ import annotations

import datetime as _dt
import unittest
from typing import Dict, List, Sequence

from bia import controller as controller_mod
from bia.answer import causal_terms_in
from bia.controller import investigate
from bia.decision import DecisionProvider, DeterministicHeuristicSelector
from bia.types import DEFER, EvidenceCandidate, MetricRow, Ticket

from . import support


class RecordingProvider(DecisionProvider):
    name = "recording"

    def __init__(self):
        self.seen = []

    def select_next_evidence(self, state, candidates: Sequence[EvidenceCandidate]) -> str:
        self.seen.append(state)
        return DEFER


class VandalProvider(DecisionProvider):
    """Tries to rewrite every number, fabricate a verified ticket and erase the log."""

    name = "vandal"

    def select_next_evidence(self, state, candidates: Sequence[EvidenceCandidate]) -> str:
        try:
            state["metrics"]["current_total"] = 999999
            state["metrics"]["delta"] = 999998
            state["top_products"] = [
                {"dimension": "product", "value": "P-Zeta", "current": 1, "baseline": 0,
                 "delta": 777, "share_of_increase": 1.0}
            ]
            state["rounds"].append({"admitted": [{"ticket_id": "T-FAKE"}]})
            state["violations"] = []
            state["finish_reason"] = "evidence_sufficient"
        except Exception:
            pass
        return candidates[0].candidate_id


class ExplodingProvider(DecisionProvider):
    name = "exploding"

    def select_next_evidence(self, state, candidates: Sequence[EvidenceCandidate]) -> str:
        raise RuntimeError("provider exploded")


class SnapshotTests(unittest.TestCase):
    def test_the_provider_receives_a_snapshot_not_the_live_state(self):
        provider = RecordingProvider()
        result = investigate(support.intent(), support.rows(), support.tickets(), provider)
        self.assertEqual(len(provider.seen), 1)
        self.assertIsInstance(provider.seen[0], dict)
        self.assertIsNot(provider.seen[0], result.state)

    def test_mutating_the_snapshot_changes_nothing_in_the_answer(self):
        honest = investigate(
            support.intent(), support.rows(), support.tickets(), DeterministicHeuristicSelector()
        )
        vandal = investigate(support.intent(), support.rows(), support.tickets(), VandalProvider())
        self.assertEqual(honest.answer.render(), vandal.answer.render())
        self.assertNotIn("999999", vandal.answer.render())
        self.assertNotIn("P-Zeta", vandal.answer.render())
        self.assertNotIn("T-FAKE", vandal.answer.render())
        self.assertEqual(vandal.state.metrics.current_total, honest.state.metrics.current_total)

    def test_a_provider_that_raises_is_downgraded_to_defer(self):
        result = investigate(
            support.intent(), support.rows(), support.tickets(), ExplodingProvider()
        )
        state = result.state
        self.assertEqual(state.finish_reason, controller_mod.FINISH_DEFERRED)
        self.assertEqual(state.retrievals, 0)
        self.assertEqual(state.found_ticket_ids, [])
        self.assertIn(controller_mod.VIOLATION_RAISED, state.violations[0])

    def test_an_oversized_selection_is_truncated_in_the_record(self):
        from bia.decision import ScriptedSelector

        state = investigate(
            support.intent(), support.rows(), support.tickets(), ScriptedSelector(["X" * 50000])
        ).state
        self.assertLessEqual(
            len(state.rounds[0].selection_raw), controller_mod.MAX_RECORDED_SELECTION_CHARS
        )


class QuoteSpanTests(unittest.TestCase):
    def test_one_quote_being_a_substring_of_another_does_not_abort_the_run(self):
        texts = ["late", "Delivery is late, order 42 never came."]
        tickets: List[Ticket] = []
        for index in range(6):
            tickets.append(
                Ticket(
                    ticket_id="T-%04d" % (index + 1),
                    day=support.CURRENT.start + _dt.timedelta(days=index),
                    product=support.SPIKE_PRODUCT,
                    complaint_type=support.SPIKE_TYPE,
                    text=texts[index % 2],
                    source="web_form",
                )
            )
        result = investigate(
            support.intent(), support.rows(), tickets, DeterministicHeuristicSelector()
        )
        rendered = result.answer.render()
        self.assertIn("order 42 never came", rendered)
        self.assertEqual(causal_terms_in(result.answer.claim_text()), [])


class DenominatorTests(unittest.TestCase):
    """The share is of the groups that rose, which is not the net change."""

    def _rows(self) -> List[MetricRow]:
        out: List[MetricRow] = []
        plan = {
            (support.BASELINE, "P-Alpha"): 5,
            (support.BASELINE, "P-Beta"): 1,
            (support.CURRENT, "P-Alpha"): 2,
            (support.CURRENT, "P-Beta"): 6,
        }
        for period in (support.BASELINE, support.CURRENT):
            for day in period.dates():
                for product in ("P-Alpha", "P-Beta"):
                    out.append(MetricRow(day, product, "delivery_delay", plan[(period, product)]))
        return out

    def test_the_answer_reconciles_the_rising_total_with_the_net_change(self):
        result = investigate(
            support.intent(), self._rows(), support.tickets(), DeterministicHeuristicSelector()
        )
        confirmed = "\n".join(result.answer.confirmed)
        self.assertEqual(result.state.metrics.delta, 20)
        self.assertIn("늘어난 그룹 합계의", confirmed)
        self.assertIn("순증가는 +20건", confirmed)
        self.assertIn("+50건", confirmed)
        self.assertEqual(causal_terms_in(result.answer.claim_text()), [])


if __name__ == "__main__":
    unittest.main()


class WideRetrievalAdmissionTests(unittest.TestCase):
    """v1.1 server rule: a wide filter may retrieve, but only risen groups are promoted."""

    def _rows(self) -> List[MetricRow]:
        out: List[MetricRow] = []
        plan = {
            (support.BASELINE, "delivery_delay"): 2,
            (support.BASELINE, "app_crash"): 4,
            (support.CURRENT, "delivery_delay"): 8,
            (support.CURRENT, "app_crash"): 4,
        }
        for period in (support.BASELINE, support.CURRENT):
            for day in period.dates():
                for complaint_type in ("delivery_delay", "app_crash"):
                    out.append(
                        MetricRow(day, support.SPIKE_PRODUCT, complaint_type, plan[(period, complaint_type)])
                    )
        return out

    def _tickets(self) -> List[Ticket]:
        out: List[Ticket] = []
        texts = {
            "delivery_delay": support.SUPPORTING_TEXT,
            "app_crash": "The app crashes on every launch.",
        }
        index = 0
        for day in support.CURRENT.dates():
            for complaint_type in ("delivery_delay", "app_crash"):
                index += 1
                out.append(
                    Ticket(
                        ticket_id="T-%04d" % index,
                        day=day,
                        product=support.SPIKE_PRODUCT,
                        complaint_type=complaint_type,
                        text=texts[complaint_type],
                        source="web_form",
                    )
                )
        return out

    def test_a_product_wide_choice_cannot_promote_a_flat_group(self):
        from bia.types import EvidenceFilter

        class ProductWideProvider(DecisionProvider):
            name = "product_wide"

            def select_next_evidence(self, state, candidates):
                for candidate in candidates:
                    if candidate.kind == "product":
                        return candidate.candidate_id
                return DEFER

        result = investigate(support.intent(), self._rows(), self._tickets(), ProductWideProvider())
        admitted = result.state.found_tickets
        self.assertTrue(admitted, "the risen group should still supply evidence")
        for item in admitted:
            self.assertEqual(item.complaint_type, "delivery_delay")
        reasons = {r["reason"] for round_ in result.state.rounds for r in round_.rejected}
        self.assertIn("group_did_not_increase", reasons)
