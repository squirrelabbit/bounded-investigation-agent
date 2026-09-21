"""Unit tests for the v1 challenge scorer, on hand-built oracle and run fixtures.

Nothing here touches data/challenges/ or the real challenge oracle: the scoring
rules must be testable before the data exists.
"""
from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eval.v1 import run_eval as scorer  # noqa: E402

CURRENT = {"start": "2026-07-01", "end": "2026-07-30"}
BASELINE = {"start": "2026-06-01", "end": "2026-06-30"}

RISING_CELL = ("P-Beta", "delivery_delay")
FLAT_CELL = ("P-Alpha", "app_crash")


def oracle_case(**overrides):
    case = {
        "label": "hand-built",
        "category": "unit_test",
        "expect_status": "reported",
        "expect_comparability_mode": "full",
        "expect_current_window": dict(CURRENT),
        "expect_baseline_window": dict(BASELINE),
        "expect_current_total": 200,
        "expect_baseline_total": 100,
        "expect_delta": 100,
        "expect_top_products": ["P-Beta"],
        "expect_top_complaint_types": ["delivery_delay"],
        "forbidden_claims": ["원인", "때문"],
        "useful_ticket_ids": ["T-1", "T-2", "T-3", "T-4"],
        "offerable_filters": [],
        "best_single_filter_yield": 3,
        "best_two_filter_yield": 4,
        "best_two_filter_example": [],
        "must_not_claim": False,
        "expect_wasted_retrievals_min": 0,
    }
    case.update(overrides)
    return case


def ticket(ticket_id, cell=RISING_CELL, source="web_form", day="2026-07-05"):
    return {
        "ticket_id": ticket_id,
        "day": day,
        "product": cell[0],
        "complaint_type": cell[1],
        "source": source,
        "excerpt": "the parcel is late",
    }


def observation(admitted=None, **overrides):
    admitted = list(admitted if admitted is not None else [])
    obs = scorer.RunObservation(
        status="reported",
        comparability_mode="full",
        current_window=dict(CURRENT),
        baseline_window=dict(BASELINE),
        has_metrics=True,
        current_total=200,
        baseline_total=100,
        delta=100,
        top_products=["P-Beta"],
        top_complaint_types=["delivery_delay"],
        claim_text="## 확인된 사실\n- 비교 구간 불만 건수: 차이 +100건\n",
        admitted=admitted,
        retrieval_admitted_ids=[[t["ticket_id"] for t in admitted]] if admitted else [],
        investigated_kinds=["cell"] if admitted else [],
        positive_cells={RISING_CELL},
        known_ticket_ids={t["ticket_id"] for t in admitted},
        decision_calls=1 if admitted else 0,
        retrievals=1 if admitted else 0,
        finish_reason="evidence_sufficient",
    )
    for key, value in overrides.items():
        setattr(obs, key, value)
    return obs


class YieldTest(unittest.TestCase):
    def test_partial_yield(self):
        self.assertEqual(scorer.case_yield(["T-1", "T-2"], ["T-1", "T-2", "T-3", "T-4"], 4), 0.5)

    def test_non_useful_admitted_do_not_count(self):
        self.assertEqual(scorer.case_yield(["T-9", "T-8"], ["T-1", "T-2"], 2), 0.0)

    def test_capped_at_one(self):
        got = scorer.case_yield(["T-1", "T-2", "T-3", "T-4"], ["T-1", "T-2", "T-3", "T-4"], 2)
        self.assertEqual(got, 1.0)

    def test_zero_best_two_is_excluded_not_a_crash(self):
        self.assertIsNone(scorer.case_yield(["T-1"], ["T-1"], 0))

    def test_duplicate_admissions_do_not_inflate_yield(self):
        self.assertEqual(scorer.case_yield(["T-1", "T-1"], ["T-1", "T-2"], 2), 0.5)


class PrecisionTest(unittest.TestCase):
    def test_all_useful(self):
        self.assertEqual(scorer.case_precision(["T-1", "T-2"], ["T-1", "T-2", "T-3"]), 1.0)

    def test_half_useful(self):
        self.assertEqual(scorer.case_precision(["T-1", "T-9"], ["T-1"]), 0.5)

    def test_no_admitted_is_excluded(self):
        self.assertIsNone(scorer.case_precision([], ["T-1"]))

    def test_duplicate_admission_costs_precision(self):
        self.assertEqual(scorer.case_precision(["T-1", "T-1"], ["T-1"]), 0.5)


class WastedRetrievalTest(unittest.TestCase):
    def test_counts_only_retrievals_with_no_useful_hit(self):
        got = scorer.count_wasted_retrievals(
            [["T-9"], ["T-1", "T-9"], [], ["T-8", "T-7"]], ["T-1", "T-2"]
        )
        self.assertEqual(got, 3)

    def test_none_wasted(self):
        self.assertEqual(scorer.count_wasted_retrievals([["T-1"]], ["T-1"]), 0)


class S4LeakTest(unittest.TestCase):
    def test_clean_run_has_no_leak(self):
        obs = observation([ticket("T-1"), ticket("T-2")])
        self.assertEqual(scorer.s4_leaks(obs, ["T-1", "T-2"]), [])

    def test_unknown_ticket_id_leaks(self):
        obs = observation([ticket("T-1")], known_ticket_ids=set())
        self.assertEqual(
            scorer.s4_leaks(obs, ["T-1"]),
            [{"ticket_id": "T-1", "reason": "unknown_ticket_id"}],
        )

    def test_disallowed_source_leaks(self):
        obs = observation([ticket("T-1", source="scraped_forum")])
        self.assertEqual(
            scorer.s4_leaks(obs, ["T-1"]),
            [{"ticket_id": "T-1", "reason": "source_not_allowed"}],
        )

    def test_out_of_window_leaks(self):
        obs = observation([ticket("T-1", day="2026-06-15")])
        self.assertEqual(
            scorer.s4_leaks(obs, ["T-1"]),
            [{"ticket_id": "T-1", "reason": "outside_comparison_window"}],
        )

    def test_rising_cell_ticket_absent_from_useful_set_leaks(self):
        obs = observation([ticket("T-1")])
        self.assertEqual(
            scorer.s4_leaks(obs, ["T-2"]),
            [{"ticket_id": "T-1", "reason": "rising_cell_ticket_not_in_useful_set"}],
        )

    def test_flat_cell_ticket_absent_from_useful_set_is_not_a_leak(self):
        obs = observation([ticket("T-1", cell=FLAT_CELL)])
        self.assertEqual(scorer.s4_leaks(obs, ["T-2"]), [])

    def test_leak_makes_score_case_fail(self):
        record = scorer.score_case(
            "C01", oracle_case(), observation([ticket("T-1", source="scraped_forum")])
        )
        self.assertIn("S-4", record["failures"])
        self.assertEqual(
            record["s4_leaks"], [{"ticket_id": "T-1", "reason": "source_not_allowed"}]
        )


class ScoreCaseTest(unittest.TestCase):
    def test_clean_case_passes_and_records_kinds(self):
        obs = observation([ticket("T-1"), ticket("T-2")])
        record = scorer.score_case("C01", oracle_case(), obs)
        self.assertEqual(record["failures"], [])
        self.assertEqual(record["yield"], 0.5)
        self.assertEqual(record["precision"], 1.0)
        self.assertEqual(record["admitted_count"], 2)
        self.assertEqual(record["useful_admitted_count"], 2)
        self.assertEqual(record["best_single_filter_yield"], 3)
        self.assertEqual(record["best_two_filter_yield"], 4)
        self.assertEqual(record["decision_calls"], 1)
        self.assertEqual(record["retrievals"], 1)
        self.assertEqual(record["finish_reason"], "evidence_sufficient")
        self.assertEqual(record["wasted_retrievals"], 0)
        self.assertEqual(record["investigated_kinds"], ["cell"])
        self.assertEqual(
            record["investigated_kind_counts"],
            {"cell": 1, "product": 0, "complaint_type": 0},
        )

    def test_wrong_numbers_fail_s1(self):
        record = scorer.score_case("C01", oracle_case(), observation([], delta=99))
        self.assertIn("S-1", record["failures"])

    def test_wrong_window_fails_s3(self):
        obs = observation([], current_window={"start": "2026-07-01", "end": "2026-07-20"})
        record = scorer.score_case("C01", oracle_case(), obs)
        self.assertIn("S-3", record["failures"])

    def test_wrong_mode_fails_s3(self):
        record = scorer.score_case(
            "C01", oracle_case(), observation([], comparability_mode="aligned_window")
        )
        self.assertIn("S-3", record["failures"])

    def test_wrong_status_fails(self):
        record = scorer.score_case(
            "C01", oracle_case(), observation([], status="abstained")
        )
        self.assertIn("STATUS", record["failures"])
        self.assertIn("S-1", record["failures"])

    def test_wrong_top_groups_fail_v1(self):
        record = scorer.score_case(
            "C01", oracle_case(), observation([], top_products=["P-Alpha"])
        )
        self.assertIn("V-1/GROUPS", record["failures"])

    def test_forbidden_claim_fails_s2(self):
        obs = observation([], claim_text="배송 지연이 원인이다")
        record = scorer.score_case("C01", oracle_case(), obs)
        self.assertIn("S-2", record["failures"])
        self.assertIn("원인", record["causal_claims"])

    def test_must_not_claim_with_evidence_fails_v4(self):
        record = scorer.score_case(
            "C01",
            oracle_case(must_not_claim=True, best_two_filter_yield=0, useful_ticket_ids=[]),
            observation([ticket("T-1")]),
        )
        self.assertIn("V-4", record["failures"])

    def test_must_not_claim_clean_is_excluded_from_yield_and_precision(self):
        record = scorer.score_case(
            "C05",
            oracle_case(must_not_claim=True, best_two_filter_yield=0, useful_ticket_ids=[]),
            observation([]),
        )
        self.assertEqual(record["failures"], [])
        self.assertIsNone(record["yield"])
        self.assertIsNone(record["precision"])
        self.assertEqual(record["excluded_from_yield"], "must_not_claim")
        self.assertEqual(record["excluded_from_precision"], "must_not_claim")

    def test_zero_best_two_case_is_excluded_with_reason(self):
        record = scorer.score_case(
            "C01", oracle_case(best_two_filter_yield=0), observation([ticket("T-1")])
        )
        self.assertIsNone(record["yield"])
        self.assertEqual(record["excluded_from_yield"], "best_two_filter_yield_is_zero")

    def test_no_evidence_case_is_excluded_from_precision_only(self):
        record = scorer.score_case("C01", oracle_case(), observation([]))
        self.assertEqual(record["yield"], 0.0)
        self.assertIsNone(record["precision"])
        self.assertEqual(record["excluded_from_precision"], "no_admitted_evidence")

    def test_budget_overrun_fails(self):
        record = scorer.score_case(
            "C01", oracle_case(), observation([], decision_calls=3, retrievals=3)
        )
        self.assertIn("BUDGET", record["failures"])

    def test_round_accounting_mismatch_fails(self):
        obs = observation([ticket("T-1")], accounting_ok=False)
        record = scorer.score_case("C01", oracle_case(), obs)
        self.assertIn("EXEC", record["failures"])

    def test_crashed_case_is_recorded_as_failure(self):
        record = scorer.score_case("C01", oracle_case(), None, "RuntimeError: boom")
        self.assertTrue(record["crashed"])
        self.assertEqual(record["failures"], ["EXEC"])
        self.assertEqual(record["error"], "RuntimeError: boom")
        self.assertIsNone(record["yield"])


class AggregateTest(unittest.TestCase):
    def _records(self, *specs):
        records = []
        for index, (case, obs) in enumerate(specs, start=1):
            records.append(scorer.score_case("C%02d" % index, case, obs))
        return records

    def test_macro_mean_excludes_must_not_claim_case(self):
        records = self._records(
            (oracle_case(), observation([ticket("T-1"), ticket("T-2")])),
            (oracle_case(), observation([ticket("T-1"), ticket("T-2"), ticket("T-3"), ticket("T-4")])),
            (
                oracle_case(must_not_claim=True, best_two_filter_yield=0, useful_ticket_ids=[]),
                observation([]),
            ),
        )
        summary = scorer.aggregate(records)
        self.assertEqual(summary["V-2_included_cases"], 2)
        self.assertEqual(summary["V-2_evidence_yield_macro"], 0.75)
        self.assertEqual(summary["V-3_included_cases"], 2)
        self.assertEqual(summary["V-3_evidence_precision_macro"], 1.0)
        self.assertEqual(summary["V-4_must_not_claim_clean"], "1/1")
        self.assertEqual(summary["V-1_fact_accuracy"], "3/3")
        self.assertTrue(summary["passed"])

    def test_macro_yield_below_floor_fails(self):
        records = self._records(
            (oracle_case(), observation([ticket("T-1")])),
            (
                oracle_case(must_not_claim=True, best_two_filter_yield=0, useful_ticket_ids=[]),
                observation([]),
            ),
        )
        summary = scorer.aggregate(records)
        self.assertEqual(summary["V-2_evidence_yield_macro"], 0.25)
        self.assertFalse(summary["verdict"]["V-2_floor"])
        self.assertFalse(summary["passed"])

    def test_no_must_not_claim_case_fails_v4(self):
        records = self._records(
            (oracle_case(), observation([ticket("T-1"), ticket("T-2"), ticket("T-3")]))
        )
        summary = scorer.aggregate(records)
        self.assertFalse(summary["verdict"]["V-4"])
        self.assertFalse(summary["passed"])

    def test_crash_fails_the_run(self):
        records = [
            scorer.score_case("C01", oracle_case(), None, "RuntimeError: boom"),
            scorer.score_case(
                "C02",
                oracle_case(must_not_claim=True, best_two_filter_yield=0, useful_ticket_ids=[]),
                observation([]),
            ),
        ]
        summary = scorer.aggregate(records)
        self.assertEqual(summary["crashed"], 1)
        self.assertFalse(summary["verdict"]["no_crash"])
        self.assertFalse(summary["verdict"]["V-1"])
        self.assertFalse(summary["passed"])

    def test_cost_and_kind_totals(self):
        records = self._records(
            (oracle_case(), observation([ticket("T-1"), ticket("T-2")])),
            (
                oracle_case(),
                observation(
                    [ticket("T-1")],
                    retrieval_admitted_ids=[["T-1"], ["T-9"]],
                    investigated_kinds=["cell", "product"],
                    decision_calls=2,
                    retrievals=2,
                ),
            ),
        )
        summary = scorer.aggregate(records)
        self.assertEqual(summary["V-5_wasted_retrievals"], 1)
        self.assertEqual(summary["V-6_total_decision_calls"], 3)
        self.assertEqual(summary["V-6_total_retrievals"], 3)
        self.assertEqual(
            summary["investigated_kind_counts"],
            {"cell": 2, "product": 1, "complaint_type": 0},
        )
        self.assertEqual(summary["V-7_latency_and_model_cost"], "not_measured")
        self.assertEqual(summary["real_model_calls"], 0)


class SerializationTest(unittest.TestCase):
    def test_results_json_is_byte_stable(self):
        records = [
            scorer.score_case("C01", oracle_case(), observation([ticket("T-1"), ticket("T-2")])),
            scorer.score_case(
                "C02",
                oracle_case(must_not_claim=True, best_two_filter_yield=0, useful_ticket_ids=[]),
                observation([]),
            ),
        ]
        payload = {"selector": "heuristic", "summary": scorer.aggregate(records), "cases": records}
        first = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        second = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        self.assertEqual(first, second)
        self.assertEqual(json.loads(first), payload)


class OracleLoadTest(unittest.TestCase):
    def test_missing_oracle_names_the_generator(self):
        with self.assertRaises(SystemExit) as caught:
            scorer.load_oracle(os.path.join(os.path.dirname(__file__), "no_such_oracle.json"))
        self.assertIn("bia.challengegen", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
