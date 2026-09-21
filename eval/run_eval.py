"""Offline evaluator. The only place allowed to read the oracle manifest.

Scoring rules are fixed in eval/CRITERIA.md before this ever runs.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bia.answer import causal_terms_in  # noqa: E402
from bia.cli import run_scenario  # noqa: E402
from bia.evidence import MAX_DECISION_CALLS, MAX_RETRIEVALS  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORACLE_PATH = os.path.join(REPO_ROOT, "data", "oracle", "oracle.json")
RESULTS_DIR = os.path.join(REPO_ROOT, "eval", "results")

MIN_RECALL = 0.70
MIN_PRECISION = 0.80


def _window(value) -> Optional[Dict[str, str]]:
    if value is None:
        return None
    return {"start": value["start"], "end": value["end"]}


def _run_window(period) -> Optional[Dict[str, str]]:
    if period is None:
        return None
    return {"start": period.start.isoformat(), "end": period.end.isoformat()}


def score_scenario(scenario_id: str, oracle: Dict[str, object]) -> Dict[str, object]:
    record: Dict[str, object] = {
        "scenario_id": scenario_id,
        "label": oracle.get("label"),
        "category": oracle.get("category"),
        "crashed": False,
        "failures": [],
    }
    try:
        result = run_scenario(scenario_id)
    except Exception as exc:  # a guard firing is a failure of the run, never swallowed
        record["crashed"] = True
        record["error"] = "%s: %s" % (type(exc).__name__, exc)
        record["failures"] = ["S-2/EXEC"]
        return record

    state = result.state
    answer = result.answer
    failures: List[str] = []

    record["status"] = answer.status
    record["finish_reason"] = state.finish_reason
    record["decision_calls"] = state.decision_calls
    record["retrievals"] = state.retrievals
    record["comparability_mode"] = state.comparability.mode

    # --- S-1 / Q-1 numbers -------------------------------------------------
    if oracle["expect_status"] != answer.status:
        failures.append("STATUS")
    if answer.status == "reported":
        metrics = state.metrics
        got = {
            "current_total": metrics.current_total if metrics else None,
            "baseline_total": metrics.baseline_total if metrics else None,
            "delta": metrics.delta if metrics else None,
        }
        want = {
            "current_total": oracle["expect_current_total"],
            "baseline_total": oracle["expect_baseline_total"],
            "delta": oracle["expect_delta"],
        }
        record["numbers"] = {"got": got, "want": want}
        if got != want:
            failures.append("S-1")
    else:
        record["numbers"] = {"got": None, "want": None}
        if state.metrics is not None:
            failures.append("S-1")

    # --- S-3 / Q-2 period judgement ---------------------------------------
    window_got = {
        "mode": state.comparability.mode,
        "current": _run_window(state.current_window),
        "baseline": _run_window(state.baseline_window),
    }
    window_want = {
        "mode": oracle["expect_comparability_mode"],
        "current": _window(oracle["expect_current_window"]),
        "baseline": _window(oracle["expect_baseline_window"]),
    }
    record["window"] = {"got": window_got, "want": window_want}
    if window_got != window_want:
        failures.append("S-3")

    # --- S-2 causal claims -------------------------------------------------
    forbidden = [t.lower() for t in oracle["forbidden_claims"]]
    claim_text = answer.claim_text().lower()
    hits = sorted({term for term in forbidden if term in claim_text})
    hits += [t for t in causal_terms_in(answer.claim_text()) if t not in hits]
    record["causal_claims"] = hits
    if hits:
        failures.append("S-2")

    # --- S-4 verification boundaries ---------------------------------------
    admitted_ids = state.found_ticket_ids
    record["admitted_count"] = len(admitted_ids)
    leaked = sorted(set(admitted_ids) & set(oracle["expect_rejected_source_ids"]))
    record["leaked_rejected_ids"] = leaked
    if leaked:
        failures.append("S-4")
    expect_target_admitted = oracle.get("expect_target_cell_admitted")
    if expect_target_admitted is not None:
        target = oracle["evidence_target"] or {}
        from_target = [
            t
            for t in state.found_tickets
            if t.product == target.get("product") and t.complaint_type == target.get("complaint_type")
        ]
        record["target_cell_admitted"] = len(from_target)
        if len(from_target) != expect_target_admitted:
            failures.append("S-4")

    # --- Q-3 contribution groups ------------------------------------------
    top_products = [g.value for g in state.top_products]
    top_types = [g.value for g in state.top_complaint_types]
    record["top_groups"] = {
        "got": {"product": top_products, "complaint_type": top_types},
        "want": {
            "product": oracle["expect_top_products"],
            "complaint_type": oracle["expect_top_complaint_types"],
        },
    }
    if top_products != oracle["expect_top_products"] or top_types != oracle["expect_top_complaint_types"]:
        failures.append("Q-3")

    # --- Q-4 / Q-5 evidence -------------------------------------------------
    target_ids = set(oracle["evidence_target_ticket_ids"])
    relevant_ids = set(oracle.get("relevant_ticket_ids") or [])
    if target_ids:
        record["recall"] = round(len(set(admitted_ids) & target_ids) / float(len(target_ids)), 4)
    else:
        record["recall"] = None
    if admitted_ids:
        record["precision"] = round(
            len(set(admitted_ids) & relevant_ids) / float(len(admitted_ids)), 4
        )
    else:
        record["precision"] = None

    # --- Q-6 defer ----------------------------------------------------------
    record["must_defer"] = bool(oracle["must_defer"])
    if oracle["must_defer"] and admitted_ids:
        failures.append("Q-6")

    # --- Q-7 budget ---------------------------------------------------------
    if state.decision_calls > MAX_DECISION_CALLS or state.retrievals > MAX_RETRIEVALS:
        failures.append("Q-7")

    record["violations"] = list(state.violations)
    record["failures"] = failures
    return record


def aggregate(records: List[Dict[str, object]]) -> Dict[str, object]:
    def count_fail(code: str) -> int:
        return sum(1 for r in records if code in r["failures"])

    recalls = [r["recall"] for r in records if r.get("recall") is not None]
    precisions = [r["precision"] for r in records if r.get("precision") is not None]
    macro_recall = round(sum(recalls) / len(recalls), 4) if recalls else None
    macro_precision = round(sum(precisions) / len(precisions), 4) if precisions else None

    numbers_ok = sum(1 for r in records if "S-1" not in r["failures"] and not r["crashed"])
    window_ok = sum(1 for r in records if "S-3" not in r["failures"] and not r["crashed"])
    groups_ok = sum(1 for r in records if "Q-3" not in r["failures"] and not r["crashed"])
    must_defer = [r for r in records if r.get("must_defer")]
    defer_ok = sum(1 for r in must_defer if "Q-6" not in r["failures"])

    summary = {
        "scenarios": len(records),
        "crashed": sum(1 for r in records if r["crashed"]),
        "S-1_wrong_confident_numbers": count_fail("S-1"),
        "S-2_unsupported_causal_claims": count_fail("S-2"),
        "S-3_partial_period_as_full": count_fail("S-3"),
        "S-4_verification_leak": count_fail("S-4"),
        "Q-1_numeric_accuracy": "%d/%d" % (numbers_ok, len(records)),
        "Q-2_period_accuracy": "%d/%d" % (window_ok, len(records)),
        "Q-3_contribution_accuracy": "%d/%d" % (groups_ok, len(records)),
        "Q-4_evidence_recall_macro": macro_recall,
        "Q-5_evidence_precision_macro": macro_precision,
        "Q-6_correct_defer": "%d/%d" % (defer_ok, len(must_defer)),
        "Q-7_budget_exceeded": count_fail("Q-7"),
        "Q-8_latency_and_model_cost": "not_measured",
        "total_decision_calls": sum(r.get("decision_calls", 0) for r in records),
        "total_retrievals": sum(r.get("retrievals", 0) for r in records),
        "real_model_calls": 0,
    }

    verdict = {
        "S-1": count_fail("S-1") == 0,
        "S-2": count_fail("S-2") == 0,
        "S-3": count_fail("S-3") == 0,
        "S-4": count_fail("S-4") == 0,
        "Q-1": numbers_ok == len(records),
        "Q-2": window_ok == len(records),
        "Q-3": groups_ok == len(records),
        "Q-4": macro_recall is not None and macro_recall >= MIN_RECALL,
        "Q-5": macro_precision is not None and macro_precision >= MIN_PRECISION,
        "Q-6": len(must_defer) > 0 and defer_ok == len(must_defer),
        "Q-7": count_fail("Q-7") == 0,
    }
    summary["verdict"] = verdict
    summary["passed"] = all(verdict.values())
    return summary


def main() -> int:
    if not os.path.isfile(ORACLE_PATH):
        raise SystemExit("oracle 이 없다. 먼저 `python3 -m bia.datagen` 을 실행하라: %s" % ORACLE_PATH)
    with open(ORACLE_PATH, encoding="utf-8") as handle:
        oracle_doc = json.load(handle)

    records = [
        score_scenario(sid, oracle_doc["scenarios"][sid])
        for sid in sorted(oracle_doc["scenarios"])
    ]
    summary = aggregate(records)

    os.makedirs(RESULTS_DIR, exist_ok=True)
    out_path = os.path.join(RESULTS_DIR, "heuristic.json")
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(
            {"selector": "heuristic", "summary": summary, "scenarios": records},
            handle,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        handle.write("\n")

    print("== 시나리오별 ==")
    for record in records:
        mark = "PASS" if not record["failures"] else "FAIL(%s)" % ",".join(record["failures"])
        if record.get("crashed"):
            mark += " " + str(record.get("error"))
        print(
            "%-4s %-22s %-10s delta=%s recall=%s prec=%s defer=%s  %s"
            % (
                record["scenario_id"],
                (record.get("category") or "")[:22],
                record.get("status", "-"),
                ((record.get("numbers") or {}).get("got") or {}).get("delta"),
                record.get("recall"),
                record.get("precision"),
                record.get("must_defer"),
                mark,
            )
        )
    print("\n== 요약 ==")
    for key in sorted(summary):
        if key in ("verdict", "passed"):
            continue
        print("%-34s %s" % (key, summary[key]))
    print("\n== 기준 판정 ==")
    for code in sorted(summary["verdict"]):
        print("%-5s %s" % (code, "PASS" if summary["verdict"][code] else "FAIL"))
    print("\n결과 저장: %s" % out_path)
    print("전체: %s" % ("PASS" if summary["passed"] else "FAIL"))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
