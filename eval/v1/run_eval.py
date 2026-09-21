"""v1 challenge evaluator. The only place allowed to read the challenge oracle.

Scoring rules are fixed in eval/v1/CONTRACT.md before this ever runs.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from bia.answer import causal_terms_in  # noqa: E402
from bia.controller import investigate  # noqa: E402
from bia.decision import DecisionProvider, DeterministicHeuristicSelector  # noqa: E402
from bia.evidence import MAX_DECISION_CALLS, MAX_RETRIEVALS  # noqa: E402
from bia.store import load_scenario  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ORACLE_PATH = os.path.join(REPO_ROOT, "data", "oracle", "challenge_oracle.json")
CHALLENGE_ROOT = os.path.join(REPO_ROOT, "data", "challenges")
RESULTS_DIR = os.path.join(REPO_ROOT, "eval", "v1", "results")

SELECTORS = {"heuristic": DeterministicHeuristicSelector}

MIN_MACRO_YIELD = 0.40

# Restated here on purpose: S-4 must not be checked with the same constant the
# runtime used to admit the ticket, or the check would only confirm itself.
ALLOWED_TICKET_SOURCES_RESTATED = ("web_form", "email", "in_app")

ORACLE_CONTAINER_KEYS = ("cases", "challenges", "scenarios")

CANDIDATE_KINDS = ("cell", "product", "complaint_type")

MISSING_DATA_HINT = "먼저 `python3 -m bia.challengegen` 을 실행하라"


# --------------------------------------------------------------------------
# observation: everything the scorer is allowed to look at after a run
# --------------------------------------------------------------------------


@dataclass
class RunObservation:
    status: str = "reported"
    comparability_mode: str = "full"
    current_window: Optional[Dict[str, str]] = None
    baseline_window: Optional[Dict[str, str]] = None
    has_metrics: bool = True
    current_total: Optional[int] = None
    baseline_total: Optional[int] = None
    delta: Optional[int] = None
    top_products: List[str] = field(default_factory=list)
    top_complaint_types: List[str] = field(default_factory=list)
    claim_text: str = ""
    admitted: List[Dict[str, str]] = field(default_factory=list)
    retrieval_admitted_ids: List[List[str]] = field(default_factory=list)
    investigated_kinds: List[str] = field(default_factory=list)
    positive_cells: Set[Tuple[str, str]] = field(default_factory=set)
    known_ticket_ids: Set[str] = field(default_factory=set)
    decision_calls: int = 0
    retrievals: int = 0
    finish_reason: str = "not_finished"
    accounting_ok: bool = True


class _RecordingSelector(DecisionProvider):
    """Delegates unchanged; records which kinds were on the menu each round.

    EvidenceRound keeps the chosen candidate_id but not its kind, and the kind is
    exactly what a future provider would differ on.
    """

    def __init__(self, inner: DecisionProvider) -> None:
        self._inner = inner
        self.name = inner.name
        self.offerings: List[Dict[str, str]] = []

    def select_next_evidence(self, state, candidates):
        self.offerings.append({c.candidate_id: c.kind for c in candidates})
        return self._inner.select_next_evidence(state, candidates)


def _window(value) -> Optional[Dict[str, str]]:
    if value is None:
        return None
    return {"start": value["start"], "end": value["end"]}


def _run_window(period) -> Optional[Dict[str, str]]:
    if period is None:
        return None
    return {"start": period.start.isoformat(), "end": period.end.isoformat()}


def observe(result, tickets, recorder: _RecordingSelector) -> RunObservation:
    state = result.state
    answer = result.answer
    metrics = state.metrics

    rounds = list(state.rounds)
    valid_rounds = [(i, r) for i, r in enumerate(rounds) if r.selection_valid]
    retrieved_rounds = valid_rounds[: state.retrievals]

    retrieval_admitted_ids = [
        [a.ticket_id for a in r.admitted] for _, r in retrieved_rounds
    ]
    investigated_kinds = []
    for index, round_ in retrieved_rounds:
        offered = recorder.offerings[index] if index < len(recorder.offerings) else {}
        investigated_kinds.append(offered.get(round_.candidate_id, "unknown"))

    positive_cells = set()
    if metrics is not None:
        positive_cells = {
            (c.product, c.complaint_type) for c in metrics.cells if c.delta > 0
        }

    return RunObservation(
        status=answer.status,
        comparability_mode=state.comparability.mode,
        current_window=_run_window(state.current_window),
        baseline_window=_run_window(state.baseline_window),
        has_metrics=metrics is not None,
        current_total=metrics.current_total if metrics else None,
        baseline_total=metrics.baseline_total if metrics else None,
        delta=metrics.delta if metrics else None,
        top_products=[g.value for g in state.top_products],
        top_complaint_types=[g.value for g in state.top_complaint_types],
        claim_text=answer.claim_text(),
        admitted=[a.as_dict() for a in state.found_tickets],
        retrieval_admitted_ids=retrieval_admitted_ids,
        investigated_kinds=investigated_kinds,
        positive_cells=positive_cells,
        known_ticket_ids={t.ticket_id for t in tickets},
        decision_calls=state.decision_calls,
        retrievals=state.retrievals,
        finish_reason=state.finish_reason,
        accounting_ok=len(valid_rounds) >= state.retrievals,
    )


# --------------------------------------------------------------------------
# pure metric helpers
# --------------------------------------------------------------------------


def case_yield(
    admitted_ids: Sequence[str], useful_ids: Iterable[str], best_two_filter_yield: int
) -> Optional[float]:
    """V-2 per case. None means the case is excluded from the macro mean."""
    if not best_two_filter_yield:
        return None
    hit = len(set(admitted_ids) & set(useful_ids))
    return round(min(1.0, hit / float(best_two_filter_yield)), 4)


def case_precision(
    admitted_ids: Sequence[str], useful_ids: Iterable[str]
) -> Optional[float]:
    """V-3 per case. Denominator counts admission events, so a ticket admitted
    twice across two retrievals costs precision. None means excluded."""
    if not admitted_ids:
        return None
    hit = len(set(admitted_ids) & set(useful_ids))
    return round(hit / float(len(admitted_ids)), 4)


def count_wasted_retrievals(
    retrieval_admitted_ids: Sequence[Sequence[str]], useful_ids: Iterable[str]
) -> int:
    """V-5. A retrieval is wasted when none of what it admitted is useful."""
    useful = set(useful_ids)
    return sum(1 for ids in retrieval_admitted_ids if not (set(ids) & useful))


def _window_contains(window: Optional[Dict[str, str]], day: str) -> bool:
    if not window:
        return False
    return window["start"] <= day <= window["end"]


def s4_leaks(obs: RunObservation, useful_ids: Iterable[str]) -> List[Dict[str, str]]:
    """S-4 restated from the contract, without asking the runtime to grade itself.

    The label-support rule is checked through the oracle: a ticket sitting in a
    delta > 0 cell that is absent from useful_ticket_ids failed one of source,
    window or label support.
    """
    useful = set(useful_ids)
    leaks: List[Dict[str, str]] = []
    for ticket in obs.admitted:
        ticket_id = ticket["ticket_id"]
        reason = None
        if ticket_id not in obs.known_ticket_ids:
            reason = "unknown_ticket_id"
        elif ticket["source"] not in ALLOWED_TICKET_SOURCES_RESTATED:
            reason = "source_not_allowed"
        elif not _window_contains(obs.current_window, ticket["day"]):
            reason = "outside_comparison_window"
        elif (
            ticket["product"],
            ticket["complaint_type"],
        ) in obs.positive_cells and ticket_id not in useful:
            reason = "rising_cell_ticket_not_in_useful_set"
        if reason is not None:
            leaks.append({"ticket_id": ticket_id, "reason": reason})
    leaks.sort(key=lambda item: (item["ticket_id"], item["reason"]))
    return leaks


# --------------------------------------------------------------------------
# per-case scoring
# --------------------------------------------------------------------------


def score_case(
    case_id: str,
    oracle_case: Dict[str, object],
    obs: Optional[RunObservation],
    error: Optional[str] = None,
) -> Dict[str, object]:
    record: Dict[str, object] = {
        "case_id": case_id,
        "label": oracle_case.get("label"),
        "category": oracle_case.get("category"),
        "must_not_claim": bool(oracle_case.get("must_not_claim")),
        "crashed": obs is None,
        "failures": [],
    }
    if obs is None:
        record["error"] = error or "unknown error"
        record["failures"] = ["EXEC"]
        record["yield"] = None
        record["precision"] = None
        return record

    failures: List[str] = []
    useful_ids = set(oracle_case.get("useful_ticket_ids") or [])
    best_two = int(oracle_case.get("best_two_filter_yield") or 0)
    best_single = int(oracle_case.get("best_single_filter_yield") or 0)
    must_not_claim = record["must_not_claim"]

    admitted_ids = [t["ticket_id"] for t in obs.admitted]
    unique_admitted = set(admitted_ids)

    record["status"] = obs.status
    record["finish_reason"] = obs.finish_reason
    record["decision_calls"] = obs.decision_calls
    record["retrievals"] = obs.retrievals
    record["comparability_mode"] = obs.comparability_mode
    record["admitted_count"] = len(admitted_ids)
    record["admitted_unique_count"] = len(unique_admitted)
    record["useful_admitted_count"] = len(unique_admitted & useful_ids)
    record["best_single_filter_yield"] = best_single
    record["best_two_filter_yield"] = best_two
    record["useful_total"] = len(useful_ids)
    record["investigated_kinds"] = list(obs.investigated_kinds)
    record["investigated_kind_counts"] = {
        kind: obs.investigated_kinds.count(kind) for kind in CANDIDATE_KINDS
    }
    record["expect_wasted_retrievals_min"] = oracle_case.get(
        "expect_wasted_retrievals_min"
    )

    if not obs.accounting_ok:
        failures.append("EXEC")

    # --- V-1 status + numbers (S-1) ---------------------------------------
    if oracle_case["expect_status"] != obs.status:
        failures.append("STATUS")
    if obs.status == "reported":
        got = {
            "current_total": obs.current_total,
            "baseline_total": obs.baseline_total,
            "delta": obs.delta,
        }
        want = {
            "current_total": oracle_case["expect_current_total"],
            "baseline_total": oracle_case["expect_baseline_total"],
            "delta": oracle_case["expect_delta"],
        }
        record["numbers"] = {"got": got, "want": want}
        if got != want:
            failures.append("S-1")
    else:
        record["numbers"] = {"got": None, "want": None}
        if obs.has_metrics:
            failures.append("S-1")

    # --- V-1 windows + mode (S-3) -----------------------------------------
    window_got = {
        "mode": obs.comparability_mode,
        "current": obs.current_window,
        "baseline": obs.baseline_window,
    }
    window_want = {
        "mode": oracle_case["expect_comparability_mode"],
        "current": _window(oracle_case["expect_current_window"]),
        "baseline": _window(oracle_case["expect_baseline_window"]),
    }
    record["window"] = {"got": window_got, "want": window_want}
    if window_got != window_want:
        failures.append("S-3")

    # --- S-2 causal claims -------------------------------------------------
    forbidden = [t.lower() for t in (oracle_case.get("forbidden_claims") or [])]
    lowered = obs.claim_text.lower()
    hits = sorted({term for term in forbidden if term in lowered})
    hits += [t for t in causal_terms_in(obs.claim_text) if t not in hits]
    record["causal_claims"] = hits
    if hits:
        failures.append("S-2")

    # --- S-4 verification boundaries ---------------------------------------
    leaks = s4_leaks(obs, useful_ids)
    record["s4_leaks"] = leaks
    if leaks:
        failures.append("S-4")

    # --- V-1 contribution groups -------------------------------------------
    record["top_groups"] = {
        "got": {
            "product": list(obs.top_products),
            "complaint_type": list(obs.top_complaint_types),
        },
        "want": {
            "product": oracle_case["expect_top_products"],
            "complaint_type": oracle_case["expect_top_complaint_types"],
        },
    }
    if record["top_groups"]["got"] != record["top_groups"]["want"]:
        failures.append("V-1/GROUPS")

    # --- V-4 must_not_claim -------------------------------------------------
    if must_not_claim and admitted_ids:
        failures.append("V-4")

    # --- V-2 / V-3 ----------------------------------------------------------
    if must_not_claim:
        record["yield"] = None
        record["precision"] = None
        record["excluded_from_yield"] = "must_not_claim"
        record["excluded_from_precision"] = "must_not_claim"
    else:
        record["yield"] = case_yield(admitted_ids, useful_ids, best_two)
        if record["yield"] is None:
            record["excluded_from_yield"] = "best_two_filter_yield_is_zero"
        record["precision"] = case_precision(admitted_ids, useful_ids)
        if record["precision"] is None:
            record["excluded_from_precision"] = "no_admitted_evidence"

    # --- V-5 ----------------------------------------------------------------
    record["wasted_retrievals"] = count_wasted_retrievals(
        obs.retrieval_admitted_ids, useful_ids
    )

    # --- budget -------------------------------------------------------------
    if obs.decision_calls > MAX_DECISION_CALLS or obs.retrievals > MAX_RETRIEVALS:
        failures.append("BUDGET")

    record["failures"] = failures
    return record


V1_FAILURE_CODES = ("S-1", "S-3", "V-1/GROUPS", "STATUS", "EXEC")


def aggregate(records: List[Dict[str, object]]) -> Dict[str, object]:
    def count_fail(code: str) -> int:
        return sum(1 for r in records if code in r["failures"])

    yields = [r["yield"] for r in records if r.get("yield") is not None]
    precisions = [r["precision"] for r in records if r.get("precision") is not None]
    macro_yield = round(sum(yields) / len(yields), 4) if yields else None
    macro_precision = (
        round(sum(precisions) / len(precisions), 4) if precisions else None
    )

    v1_ok = sum(
        1
        for r in records
        if not r["crashed"] and not any(c in r["failures"] for c in V1_FAILURE_CODES)
    )
    must_not_claim = [r for r in records if r.get("must_not_claim")]
    v4_ok = sum(1 for r in must_not_claim if "V-4" not in r["failures"])

    kind_counts = {kind: 0 for kind in CANDIDATE_KINDS}
    for record in records:
        for kind, count in (record.get("investigated_kind_counts") or {}).items():
            kind_counts[kind] = kind_counts.get(kind, 0) + count

    summary = {
        "cases": len(records),
        "crashed": sum(1 for r in records if r["crashed"]),
        "S-1_wrong_confident_numbers": count_fail("S-1"),
        "S-2_unsupported_causal_claims": count_fail("S-2"),
        "S-3_partial_period_as_full": count_fail("S-3"),
        "S-4_verification_leak": count_fail("S-4"),
        "V-1_fact_accuracy": "%d/%d" % (v1_ok, len(records)),
        "V-2_evidence_yield_macro": macro_yield,
        "V-2_included_cases": len(yields),
        "V-3_evidence_precision_macro": macro_precision,
        "V-3_included_cases": len(precisions),
        "V-4_must_not_claim_clean": "%d/%d" % (v4_ok, len(must_not_claim)),
        "V-5_wasted_retrievals": sum(r.get("wasted_retrievals", 0) for r in records),
        "V-6_total_decision_calls": sum(r.get("decision_calls", 0) for r in records),
        "V-6_total_retrievals": sum(r.get("retrievals", 0) for r in records),
        "V-7_latency_and_model_cost": "not_measured",
        "investigated_kind_counts": kind_counts,
        "budget_exceeded": count_fail("BUDGET"),
        "real_model_calls": 0,
    }

    verdict = {
        "S-1": count_fail("S-1") == 0,
        "S-2": count_fail("S-2") == 0,
        "S-3": count_fail("S-3") == 0,
        "S-4": count_fail("S-4") == 0,
        "V-1": len(records) > 0 and v1_ok == len(records),
        "V-2_floor": macro_yield is not None and macro_yield >= MIN_MACRO_YIELD,
        "V-4": len(must_not_claim) > 0 and v4_ok == len(must_not_claim),
        "no_crash": summary["crashed"] == 0,
        "budget": count_fail("BUDGET") == 0,
    }
    summary["verdict"] = verdict
    summary["passed"] = all(verdict.values())
    return summary


# --------------------------------------------------------------------------
# running
# --------------------------------------------------------------------------


def load_oracle(path: Optional[str] = None) -> Dict[str, Dict[str, object]]:
    path = path or ORACLE_PATH
    if not os.path.isfile(path):
        raise SystemExit(
            "challenge oracle 이 없다. %s: %s" % (MISSING_DATA_HINT, path)
        )
    with open(path, encoding="utf-8") as handle:
        doc = json.load(handle)
    for key in ORACLE_CONTAINER_KEYS:
        value = doc.get(key)
        if isinstance(value, dict) and value:
            return value
    cases = {k: v for k, v in doc.items() if isinstance(v, dict) and "expect_status" in v}
    if cases:
        return cases
    raise SystemExit(
        "challenge oracle 에서 사례 묶음을 찾지 못했다 (기대 키: %s): %s"
        % (", ".join(ORACLE_CONTAINER_KEYS), path)
    )


def case_dir(case_id: str) -> str:
    return os.path.join(CHALLENGE_ROOT, case_id)


def run_case(case_id: str, selector: str) -> Tuple[Optional[RunObservation], Optional[str]]:
    directory = case_dir(case_id)
    if not os.path.isdir(directory):
        raise SystemExit(
            "challenge 데이터 %s 가 없다. %s: %s" % (case_id, MISSING_DATA_HINT, directory)
        )
    intent, rows, tickets, _meta = load_scenario(directory)
    recorder = _RecordingSelector(SELECTORS[selector]())
    try:
        result = investigate(intent, rows, tickets, recorder)
    except Exception as exc:  # a guard firing is a failure of the run, never swallowed
        return None, "%s: %s" % (type(exc).__name__, exc)
    return observe(result, tickets, recorder), None


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="eval.v1.run_eval", description="v1 challenge 채점기"
    )
    parser.add_argument("--selector", default="heuristic", choices=sorted(SELECTORS))
    args = parser.parse_args(argv)

    oracle_cases = load_oracle()
    if not os.path.isdir(CHALLENGE_ROOT):
        raise SystemExit(
            "challenge 데이터 폴더가 없다. %s: %s" % (MISSING_DATA_HINT, CHALLENGE_ROOT)
        )

    records: List[Dict[str, object]] = []
    for case_id in sorted(oracle_cases):
        obs, error = run_case(case_id, args.selector)
        records.append(score_case(case_id, oracle_cases[case_id], obs, error))
    summary = aggregate(records)

    os.makedirs(RESULTS_DIR, exist_ok=True)
    out_path = os.path.join(RESULTS_DIR, "%s.json" % args.selector)
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(
            {"selector": args.selector, "summary": summary, "cases": records},
            handle,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        handle.write("\n")

    print("== 사례별 ==")
    print(
        "%-4s %-20s %-6s %-6s %-5s %-5s %-5s %-5s %-5s %s"
        % ("ID", "category", "yield", "prec", "adm", "use", "best2", "wast", "retr", "판정")
    )
    for record in records:
        mark = "PASS" if not record["failures"] else "FAIL(%s)" % ",".join(record["failures"])
        if record["crashed"]:
            mark += " " + str(record.get("error"))
        print(
            "%-4s %-20s %-6s %-6s %-5s %-5s %-5s %-5s %-5s %s"
            % (
                record["case_id"],
                (record.get("category") or "")[:20],
                record.get("yield"),
                record.get("precision"),
                record.get("admitted_count"),
                record.get("useful_admitted_count"),
                record.get("best_two_filter_yield"),
                record.get("wasted_retrievals"),
                record.get("retrievals"),
                mark,
            )
        )
        if not record["crashed"]:
            print(
                "     kinds=%s finish=%s decision=%s"
                % (
                    ",".join(record.get("investigated_kinds") or []) or "-",
                    record.get("finish_reason"),
                    record.get("decision_calls"),
                )
            )

    print("\n== 요약 ==")
    for key in sorted(summary):
        if key in ("verdict", "passed"):
            continue
        print("%-34s %s" % (key, summary[key]))
    print("\n== 기준 판정 ==")
    for code in sorted(summary["verdict"]):
        print("%-12s %s" % (code, "PASS" if summary["verdict"][code] else "FAIL"))
    print("\n결과 저장: %s" % out_path)
    print("전체: %s" % ("PASS" if summary["passed"] else "FAIL"))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
