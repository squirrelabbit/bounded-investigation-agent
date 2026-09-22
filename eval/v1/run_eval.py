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
from bia.decision import (  # noqa: E402
    DecisionProvider,
    DeterministicHeuristicSelector,
    GreedyEvidenceSelector,
)
from bia.evidence import MAX_DECISION_CALLS, MAX_RETRIEVALS  # noqa: E402
from bia.jev import (
    HttpTransport,  # noqa: E402
    REASON_BUDGET_EXHAUSTED,
    REASON_NO_CANDIDATES,
    CallBudget,
    FakeTransport,
    JevSelector,
    RehearsalTransport,
    RunGuard,
)
from bia.store import load_scenario  # noqa: E402
from bia.types import DEFER  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ORACLE_PATH = os.path.join(REPO_ROOT, "data", "oracle", "challenge_oracle.json")
CHALLENGE_ROOT = os.path.join(REPO_ROOT, "data", "challenges")
RESULTS_DIR = os.path.join(REPO_ROOT, "eval", "v1", "results")

# One budget for the whole process, not one per case. The scorer builds a new
# selector for each of the 8 cases; a per-selector budget would silently reset
# and the contract's "16 calls in total" would never bind.
_JEV_PROCESS_BUDGET = CallBudget()

# One halt flag for the whole run, shared the same way and for the same reason
# (§7-E). The provider raises it on any call failure; this scorer reads it
# between cases and stops. It is not the Controller's fail-closed path — that one
# keeps working unchanged and the provider still raises nothing into it.
_JEV_PROCESS_GUARD = RunGuard()


# Unlocked 2026-09-22 on the account owner's written approval, quoted here so a
# later reader can see what was granted and how narrow it is:
#
#     "직접 경로 최대 16회 유료 실행 승인"
#
# The price was re-checked against docs.typesafe.ai/models immediately before:
# input $0.042 per Mtok, output free, jev-1.13.0 current, no promotion. That was
# the third of §7-E's lock conditions. The approval covers this path, this rate
# and this ceiling — nothing wider. A different path, a different rate, or a
# retry is a new decision, not an extension of this one.
LIVE_APPROVAL = "직접 경로 최대 16회 유료 실행 승인 (2026-09-22)"


def build_jev_selector() -> JevSelector:
    """Builds the live JEV selector when a live run is explicitly requested.

    A live run needs BOTH `BIA_JEV_LIVE=1` and a non-empty `TYPESAFE_API_KEY`.
    Neither alone starts one, and without the pair the scorer still runs against
    a `FakeTransport`, which cannot open a socket. The key's value is read only
    here, to hand to the transport, and is never logged, recorded or serialized:
    `HttpTransport` deliberately does not read the environment itself, so the
    one place a credential is touched stays visible at the call site.

    Everything the run is bounded by is unchanged by the unlock: 16 calls from
    the process-wide budget, zero retries, and a halt on the first failure of
    any kind.
    """
    live_requested = os.environ.get("BIA_JEV_LIVE") == "1"
    api_key = (os.environ.get("TYPESAFE_API_KEY") or "").strip()
    if live_requested and api_key:
        return JevSelector(
            transport=HttpTransport(api_key=api_key, enable_network=True),
            budget=_JEV_PROCESS_BUDGET,
            guard=_JEV_PROCESS_GUARD,
        )
    if live_requested and not api_key:
        raise SystemExit(
            "BIA_JEV_LIVE=1 이지만 TYPESAFE_API_KEY 가 비어 있다. "
            "실호출을 시작하지 않는다."
        )
    return JevSelector(
        transport=FakeTransport(),
        budget=_JEV_PROCESS_BUDGET,
        guard=_JEV_PROCESS_GUARD,
    )


def build_jev_rehearsal_selector() -> JevSelector:
    """The offline rehearsal (§7-E 실행 전 오프라인 리허설). Not a measurement.

    The locked default transport has no scripted response, so the very first
    call fails, the run halts at C01, and the 8-case loop has never once run end
    to end. A wiring defect would therefore be discovered with paid calls. This
    factory swaps in `RehearsalTransport`, which answers every request offline
    with a well-formed body, and changes nothing else: the same process-wide
    `CallBudget` and the same `RunGuard` are shared, so the rehearsal exercises
    the budget and halt plumbing a real run would use.

    It reads no environment variable and holds no key. `build_jev_selector`'s
    lock is untouched and is not consulted here, because there is nothing to
    unlock: this path cannot open a socket.
    """
    return JevSelector(
        transport=RehearsalTransport(),
        budget=_JEV_PROCESS_BUDGET,
        guard=_JEV_PROCESS_GUARD,
    )


SELECTORS = {
    "heuristic": DeterministicHeuristicSelector,
    "greedy": GreedyEvidenceSelector,
    "jev": build_jev_selector,
}

# --dry-run is only meaningful for the selector that actually calls out.
DRY_RUN_SELECTOR = "jev"
DRY_RUN_FACTORIES = {DRY_RUN_SELECTOR: build_jev_rehearsal_selector}

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
    defer_selections: int = 0
    forced_defers: int = 0
    forced_defer_reasons: List[str] = field(default_factory=list)
    failed_decisions: int = 0
    failed_decision_reasons: List[str] = field(default_factory=list)
    starved: bool = False
    downgraded_selections: int = 0
    finish_reason: str = "not_finished"
    accounting_ok: bool = True


# What a single decision turned out to be (§7-E 굶은 결정은 보류가 아니다, 3항:
# "분류는 넷이다"). The Controller's round record cannot tell the last three
# apart — all three reach it as the string DEFER — and only the second one is a
# model holding back. `selector_deferred` counts the second and nothing else.
DECISION_CHOSEN = "chosen"
DECISION_DEFERRED = "deferred"
DECISION_FORCED = "forced"
DECISION_FAILED = "failed"


class _RecordingSelector(DecisionProvider):
    """Delegates unchanged; records which kinds were on the menu each round, and
    what kind of decision came back.

    EvidenceRound keeps the chosen candidate_id but not its kind, and the kind is
    exactly what a future provider would differ on.

    It also classifies each decision as chosen / deferred / forced / failed. The
    Controller sees only the returned string, so a DEFER the model chose, a
    DEFER produced without any call ever being made, and a DEFER produced
    because a call came back unusable are all identical to it — and §7-D's
    표기 정정 made `selector_deferred` the one observation a "the model held back
    in time" claim is read off. The distinction is taken from the provider's own
    call record — `called=False` means no request was sent, `called=True` with a
    `failure_reason` means one was sent and came back unusable — which is an
    observation made ALONGSIDE the Controller: nothing here changes the
    Controller or its fail-closed handling, and a provider that keeps no records
    is classified from its return value exactly as before.
    """

    def __init__(self, inner: DecisionProvider) -> None:
        self._inner = inner
        self.name = inner.name
        self.offerings: List[Dict[str, str]] = []
        self.decisions: List[Dict[str, Optional[str]]] = []

    def select_next_evidence(self, state, candidates):
        self.offerings.append({c.candidate_id: c.kind for c in candidates})
        records = getattr(self._inner, "records", None)
        before = len(records) if isinstance(records, list) else None
        returned = self._inner.select_next_evidence(state, candidates)
        self.decisions.append(self._classify(candidates, returned, before))
        return returned

    def _classify(self, candidates, returned, before) -> Dict[str, Optional[str]]:
        outcome, reason = self._provider_verdict(before)
        if outcome is None and not candidates:
            # Unreachable through the Controller, which stops before asking with
            # an empty menu. Kept because a decision made with nothing on offer
            # is forced by definition, whoever asks.
            outcome, reason = DECISION_FORCED, REASON_NO_CANDIDATES
        if outcome is not None:
            return {"outcome": outcome, "reason": reason}
        if returned == DEFER:
            return {"outcome": DECISION_DEFERRED, "reason": None}
        return {"outcome": DECISION_CHOSEN, "reason": None}

    def _provider_verdict(self, before):
        """What the provider wrote down about this decision, or (None, None).

        `called=False` is the adapter's own word for "no request was sent" —
        budget exhausted, run already halted, nothing on offer, too many
        options. `called=True` with a `failure_reason` is the other one: a
        request DID go out and what came back could not be used (transport
        error, non-200, unparseable body, missing field, an option that was
        never offered). Neither is a model holding back.
        """
        if before is None:
            return None, None
        records = getattr(self._inner, "records", None)
        if not isinstance(records, list):
            return None, None
        for record in records[before:]:
            reason = getattr(record, "failure_reason", None)
            if not getattr(record, "called", True):
                return DECISION_FORCED, reason or "uncalled"
            if reason:
                return DECISION_FAILED, reason
        return None, None


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

    # Three DEFERs reach the Controller as one string; they are counted apart
    # here. `defer_selections` keeps only the genuine model DEFER. A decision
    # nobody was ever asked for is forced (and, for budget exhaustion,
    # escalated to starvation); one whose call came back unusable is failed.
    genuine_defers = 0
    forced_defers = 0
    forced_reasons: Set[str] = set()
    failed_decisions = 0
    failed_reasons: Set[str] = set()
    starved = False
    for index, round_ in enumerate(rounds):
        decision = recorder.decisions[index] if index < len(recorder.decisions) else None
        outcome = decision["outcome"] if decision is not None else None
        if outcome == DECISION_FORCED:
            forced_defers += 1
            reason = decision["reason"] or "uncalled"
            forced_reasons.add(reason)
            if reason.split(":", 1)[0] == REASON_BUDGET_EXHAUSTED:
                starved = True
            continue
        if outcome == DECISION_FAILED:
            failed_decisions += 1
            failed_reasons.add(decision["reason"] or "call_failed")
            continue
        if round_.selection_raw == DEFER and not round_.violation:
            genuine_defers += 1

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
        defer_selections=genuine_defers,
        forced_defers=forced_defers,
        forced_defer_reasons=sorted(forced_reasons),
        failed_decisions=failed_decisions,
        failed_decision_reasons=sorted(failed_reasons),
        starved=starved,
        downgraded_selections=sum(1 for r in state.rounds if r.violation),
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


def s4_leaks(
    obs: RunObservation,
    useful_ids: Iterable[str],
    oracle_positive_cells: Optional[Set[Tuple[str, str]]],
) -> List[Dict[str, str]]:
    """S-4 restated from the contract, without asking the runtime to grade itself.

    Every admitted ticket must appear in the oracle's useful set. Two ways it can
    fail to: it sits in a cell that never rose (what a wide retrieval sweeps in),
    or it sits in a risen cell but failed source, window or label support.

    The rising-cell set comes from the ORACLE, not from the runtime's own
    metrics — otherwise the check would confirm the very computation it grades.
    An earlier revision gated the useful-set test on the runtime's rising cells,
    which made it structurally blind to the first failure mode: the pre-v1.1
    greedy run admitted 72 tickets from flat cells in C05 and still reported
    S-4 = 0. That blind spot is what this signature exists to close.
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
        elif oracle_positive_cells is None:
            if ticket_id not in useful:
                reason = "ticket_not_in_useful_set"
        elif (ticket["product"], ticket["complaint_type"]) not in oracle_positive_cells:
            reason = "non_rising_cell_ticket_admitted"
        elif ticket_id not in useful:
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
    incomplete: bool = False,
) -> Dict[str, object]:
    record: Dict[str, object] = {
        "case_id": case_id,
        "label": oracle_case.get("label"),
        "category": oracle_case.get("category"),
        "must_not_claim": bool(oracle_case.get("must_not_claim")),
        "crashed": obs is None,
        "failures": [],
    }
    # §7-E halts between cases, so the case the halt happened DURING is still
    # scored and still reported. Its later decision never completed, so its
    # numbers are not a clean case's numbers and must not be read as such. The
    # key is only present when it is true, so a clean case carries no such mark.
    if incomplete:
        record["incomplete"] = True
    # §7-E "굶은 결정은 보류가 아니다": a case that ran out of call budget produced
    # numbers without ever being asked. Marked the same way `incomplete` is —
    # present only when true, so a case that got its calls carries no such mark.
    if obs is not None and obs.starved:
        record["starved"] = True
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
    # V-4 records that no wrong evidence was SHOWN. It does not record that the
    # selector chose to hold back: a run can admit nothing because every retrieval
    # it spent came back empty. The two are separate observations and are kept apart.
    # Genuine model DEFERs only. A decision that never got a call, and one whose
    # call came back unusable, are not the model holding back and must not raise
    # this flag (§7-E 굶은 결정은 보류가 아니다, 1항·3항).
    record["selector_deferred"] = obs.defer_selections > 0
    record["defer_selections"] = obs.defer_selections
    # Present only when there is something to report, so a selector that can
    # neither starve nor fail a call — the two code baselines never see a
    # CallBudget or a transport — writes exactly the record it wrote before this
    # distinction existed.
    if obs.forced_defers:
        record["forced_defers"] = obs.forced_defers
        record["forced_defer_reasons"] = list(obs.forced_defer_reasons)
    if obs.failed_decisions:
        record["failed_decisions"] = obs.failed_decisions
        record["failed_decision_reasons"] = list(obs.failed_decision_reasons)
    record["downgraded_selections"] = obs.downgraded_selections
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
    raw_cells = oracle_case.get("positive_delta_cells")
    oracle_positive_cells = (
        None
        if raw_cells is None
        else {(cell["product"], cell["complaint_type"]) for cell in raw_cells}
    )
    leaks = s4_leaks(obs, useful_ids, oracle_positive_cells)
    if oracle_positive_cells is not None:
        record["oracle_positive_cells"] = sorted("%s/%s" % c for c in oracle_positive_cells)
        runtime_cells = set(obs.positive_cells)
        if runtime_cells != oracle_positive_cells:
            record["cell_set_disagreement"] = {
                "runtime_only": sorted("%s/%s" % c for c in runtime_cells - oracle_positive_cells),
                "oracle_only": sorted("%s/%s" % c for c in oracle_positive_cells - runtime_cells),
            }
            failures.append("ORACLE_SYNC")
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
        "oracle_runtime_cell_disagreement": count_fail("ORACLE_SYNC"),
        "must_not_claim_cases_where_selector_deferred": "%d/%d"
        % (
            sum(
                1
                for r in records
                if r.get("must_not_claim") and r.get("selector_deferred")
            ),
            sum(1 for r in records if r.get("must_not_claim")),
        ),
        "must_not_claim_retrievals_spent": sum(
            r.get("retrievals", 0) for r in records if r.get("must_not_claim")
        ),
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
        "oracle_sync": count_fail("ORACLE_SYNC") == 0,
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


def run_case(
    case_id: str, selector: str, factory=None
) -> Tuple[Optional[RunObservation], Optional[str]]:
    directory = case_dir(case_id)
    if not os.path.isdir(directory):
        raise SystemExit(
            "challenge 데이터 %s 가 없다. %s: %s" % (case_id, MISSING_DATA_HINT, directory)
        )
    intent, rows, tickets, _meta = load_scenario(directory)
    recorder = _RecordingSelector((factory or SELECTORS[selector])())
    try:
        result = investigate(intent, rows, tickets, recorder)
    except Exception as exc:  # a guard firing is a failure of the run, never swallowed
        return None, "%s: %s" % (type(exc).__name__, exc)
    return observe(result, tickets, recorder), None


# --------------------------------------------------------------------------
# §7-E halt: a run that stopped is never presentable as a comparison
# --------------------------------------------------------------------------

PARTIAL_NOTICE = (
    "부분 실행이다. 호출 실패로 중단됐고 남은 사례는 실행되지 않았다. "
    "이 파일은 §7 비교에 쓰지 않는다 — 8사례를 완주한 실행만 비교에 들어간다. "
    "재개는 자동으로 하지 않는다: 실패 내용을 보고하고 실패별로 다시 승인받는다."
)


def partial_results_path(selector: str, dry_run: bool = False) -> str:
    """A separate filename on purpose.

    The completed baselines live in `<selector>.json` and are pre-registered
    results. A halted run must not overwrite one of them, and must not be
    reachable by anything that reads the normal filename. A halted REHEARSAL is
    further away still: it is neither a result nor a real partial run.
    """
    if dry_run:
        return os.path.join(RESULTS_DIR, "%s_dryrun_PARTIAL.json" % selector)
    return os.path.join(RESULTS_DIR, "%s_PARTIAL.json" % selector)


def partial_document(
    selector: str,
    records: List[Dict[str, object]],
    halt_reason: str,
    calls_spent: int,
    total_cases: int,
    dry_run: bool = False,
) -> Dict[str, object]:
    """The marked-partial payload. It carries no summary and no verdict: there
    is nothing to pass or fail, and a reader must not be able to mistake a
    stopped run for a comparison."""
    document: Dict[str, object] = {
        "selector": selector,
        "halted": True,
        "halt_reason": halt_reason,
        "partial": True,
        "comparable": False,
        "notice": PARTIAL_NOTICE,
        "cases_completed": len(records),
        "cases_total": total_cases,
        "cases_not_run": max(0, total_cases - len(records)),
        "calls_spent": calls_spent,
        "cases": records,
    }
    if dry_run:
        document["simulated"] = True
        document["notice"] = DRY_RUN_NOTICE + " " + PARTIAL_NOTICE
        document["choice_policy"] = RehearsalTransport.policy
        document["choice_policy_description"] = RehearsalTransport.policy_description
    # A halted run can also have starved earlier cases. Both conditions are kept
    # in the document and stay readable apart: `halted` is one, `starved_cases`
    # the other.
    starved = starved_case_ids(records)
    if starved:
        document["starved_cases"] = starved
        document["starved_case_count"] = len(starved)
        document["notice"] = document["notice"] + " " + starved_notice(starved)
    return document


def write_partial(path: str, document: Dict[str, object]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(document, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def print_halt_report(document: Dict[str, object], path: str) -> None:
    print("\n" + "=" * 72)
    print("!! 실행 중단 (§7-E) — 이 결과는 비교에 쓸 수 없다")
    print("=" * 72)
    print("중단 사유        : %s" % document["halt_reason"])
    print("완료한 사례      : %s / %s" % (document["cases_completed"], document["cases_total"]))
    print("실행하지 않은 사례: %s" % document["cases_not_run"])
    print("사용한 호출 수   : %s" % document["calls_spent"])
    incomplete = [
        record["case_id"] for record in document["cases"] if record.get("incomplete")
    ]
    print("중단 중 실행된 사례: %s" % (", ".join(incomplete) or "-"))
    print("부분 결과 저장   : %s" % path)
    print(document["notice"])
    print("전체: 중단(PARTIAL) — PASS 도 FAIL 도 아니다. 비교 판정 없음.")


# --------------------------------------------------------------------------
# §7-E 굶은 결정: a run that ran out of calls is never presentable either
# --------------------------------------------------------------------------

STARVED_NOTICE = (
    "굶은 실행이다. 호출 예산이 소진돼 %d개 사례(%s)가 결정 호출을 한 번도 받지 못했다. "
    "그 사례의 보류는 모델의 판단이 아니라 강제된 것이고, yield 0.0 으로 macro 평균만 "
    "조용히 끌어내린다. 이 파일은 §7 비교에 쓰지 않는다 — 모든 결정이 호출 기회를 받은 "
    "실행만 비교에 들어간다. 중단(halted)과는 다른 조건이며 문서에 따로 적힌다. "
    "정식 결과 파일은 건드리지 않고 별도 파일명에 기록한다 — 유료로 얻은 측정 결과를 "
    "굶은 재실행이 덮는 사고를 막는다."
)


def starved_case_ids(records: List[Dict[str, object]]) -> List[str]:
    return [str(r["case_id"]) for r in records if r.get("starved")]


def starved_results_path(selector: str, dry_run: bool = False) -> str:
    """A separate filename, for the same reason a halted run gets one (§7-E 4항).

    A paid measurement is written once and cannot be obtained again without
    spending money; a later starved re-run in the same process — the margin is
    zero, so this is one stray call away — must not be able to overwrite it. So
    a starved run never writes `<selector>.json`, and nothing that reads the
    results filename can reach one.
    """
    if dry_run:
        return os.path.join(RESULTS_DIR, "%s_dryrun_STARVED.json" % selector)
    return os.path.join(RESULTS_DIR, "%s_STARVED.json" % selector)


def starved_notice(starved: Sequence[str]) -> str:
    return STARVED_NOTICE % (len(starved), ", ".join(starved))


def print_starved_report(starved: Sequence[str], path: str) -> None:
    print("\n" + "=" * 72)
    print("!! 굶은 실행 (§7-E) — 이 결과는 비교에 쓸 수 없다")
    print("=" * 72)
    print("예산 소진으로 굶은 사례: %d개" % len(starved))
    print("사례 목록              : %s" % (", ".join(starved) or "-"))
    print(starved_notice(starved))
    print("결과 저장   : %s" % path)
    print("정식 결과 파일: 쓰지 않았다 (덮어쓰기 없음)")
    print("전체: 굶음(STARVED) — 비교 판정 없음.")


# --------------------------------------------------------------------------
# §7-E offline rehearsal: a wiring check that must never look like a result
# --------------------------------------------------------------------------

DRY_RUN_NOTICE = (
    "이것은 배선 리허설(dry run)이다. 실제 모델 호출은 한 번도 일어나지 않았고, "
    "선택은 오프라인 가짜 전송이 'criteria 첫 번째 선택지'를 기계적으로 고른 결과다. "
    "따라서 여기 적힌 수치는 JEV의 성적이 아니며 §7 비교에 쓰지 않는다. "
    "이 실행이 확인하는 것은 답의 품질이 아니라 8사례가 끝까지 돌아가는가, "
    "호출 수가 상한 안에 있는가, 채점기가 결과를 기록하는가 셋뿐이다."
)


def dry_run_results_path(selector: str) -> str:
    """Never `<selector>.json`. A rehearsal must be unreachable by anything that
    reads the results filename, and must not be able to overwrite one."""
    return os.path.join(RESULTS_DIR, "%s_dryrun.json" % selector)


def dry_run_document(
    selector: str,
    records: List[Dict[str, object]],
    summary: Dict[str, object],
    calls_spent: int,
) -> Dict[str, object]:
    """The normal scoring fields are kept — that is the point, the scorer's
    plumbing is being exercised — wrapped in markings that make the document
    unusable as a measurement."""
    return {
        "selector": selector,
        "simulated": True,
        "comparable": False,
        "notice": DRY_RUN_NOTICE,
        "transport": RehearsalTransport.name,
        "choice_policy": RehearsalTransport.policy,
        "choice_policy_description": RehearsalTransport.policy_description,
        "real_model_calls": 0,
        "calls_spent": calls_spent,
        "call_budget_maximum": _JEV_PROCESS_BUDGET.maximum,
        "halted": _JEV_PROCESS_GUARD.halted,
        "halt_reason": _JEV_PROCESS_GUARD.halt_reason,
        "cases_completed": len(records),
        "summary": summary,
        "cases": records,
    }


def print_dry_run_banner() -> None:
    print("=" * 72)
    print("!! 리허설(dry run) — JEV 측정이 아니다. 비교에 쓰지 않는다")
    print("=" * 72)
    print("전송            : %s (오프라인 가짜)" % RehearsalTransport.name)
    print("선택 정책       : %s" % RehearsalTransport.policy)
    print("                  %s" % RehearsalTransport.policy_description)
    print("실제 모델 호출  : 0")
    print(DRY_RUN_NOTICE)
    print("=" * 72)


def results_path_for(
    selector: str,
    dry_run: bool = False,
    halted: bool = False,
    starved: bool = False,
) -> str:
    """The one place a run's output filename is decided.

    **Precedence when a run is both halted and starved: the halt names the
    file.** The document still records both conditions (`halt_reason` and
    `starved_cases`), so nothing is lost by the choice — and a halt is the
    stronger statement, because the remaining cases were never attempted at all
    and the reader already has a halt report to read the file against.

    That combination cannot in fact be produced by the loop: starvation means
    the budget hit zero, after which no request is ever sent, and a halt
    requires a call that was sent and failed. So the rule is a guard on a path
    that does not run today. It is pinned by a test rather than left implicit,
    because the thing it protects — a paid result file — cannot be recovered if
    a future change picks the other branch.
    """
    if halted:
        return partial_results_path(selector, dry_run=dry_run)
    if starved:
        return starved_results_path(selector, dry_run=dry_run)
    if dry_run:
        return dry_run_results_path(selector)
    return os.path.join(RESULTS_DIR, "%s.json" % selector)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="eval.v1.run_eval", description="v1 challenge 채점기"
    )
    parser.add_argument("--selector", default="heuristic", choices=sorted(SELECTORS))
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "오프라인 리허설 (--selector %s 전용). 가짜 전송으로 8사례 루프를 완주시켜 "
            "배선만 확인한다. 결과는 %s_dryrun.json 에 simulated 로 기록하고 비교에 쓰지 않는다."
            % (DRY_RUN_SELECTOR, DRY_RUN_SELECTOR)
        ),
    )
    args = parser.parse_args(argv)

    if args.dry_run and args.selector not in DRY_RUN_FACTORIES:
        raise SystemExit(
            "--dry-run 은 --selector %s 에만 쓴다 (요청된 selector: %s). "
            "코드 기준선은 모델을 호출하지 않으므로 리허설할 배선이 없다. "
            "--dry-run 없이 다시 실행하라." % (DRY_RUN_SELECTOR, args.selector)
        )

    run_kwargs = (
        {"factory": DRY_RUN_FACTORIES[args.selector]} if args.dry_run else {}
    )
    if args.dry_run:
        print_dry_run_banner()

    oracle_cases = load_oracle()
    if not os.path.isdir(CHALLENGE_ROOT):
        raise SystemExit(
            "challenge 데이터 폴더가 없다. %s: %s" % (MISSING_DATA_HINT, CHALLENGE_ROOT)
        )

    case_ids = sorted(oracle_cases)
    records: List[Dict[str, object]] = []
    for case_id in case_ids:
        halted_before = _JEV_PROCESS_GUARD.halted
        obs, error = run_case(case_id, args.selector, **run_kwargs)
        # The case the halt was raised DURING: a decision was attempted inside
        # it and never completed. It is still scored and still included (§7-E
        # halts between cases), so it is marked rather than silently mixed in.
        halted_during = _JEV_PROCESS_GUARD.halted and not halted_before
        records.append(
            score_case(
                case_id,
                oracle_cases[case_id],
                obs,
                error,
                incomplete=halted_during,
            )
        )
        # §7-E: the halt is checked BETWEEN cases. The remaining cases are not
        # run and the remaining budget is not spent automatically.
        if _JEV_PROCESS_GUARD.halted:
            document = partial_document(
                selector=args.selector,
                records=records,
                halt_reason=_JEV_PROCESS_GUARD.halt_reason,
                calls_spent=_JEV_PROCESS_BUDGET.used,
                total_cases=len(case_ids),
                dry_run=args.dry_run,
            )
            path = results_path_for(
                args.selector,
                dry_run=args.dry_run,
                halted=True,
                starved=bool(starved_case_ids(records)),
            )
            write_partial(path, document)
            print_halt_report(document, path)
            return 2

    summary = aggregate(records)
    starved = starved_case_ids(records)

    os.makedirs(RESULTS_DIR, exist_ok=True)
    out_path = results_path_for(
        args.selector, dry_run=args.dry_run, starved=bool(starved)
    )
    if args.dry_run:
        payload: Dict[str, object] = dry_run_document(
            selector=args.selector,
            records=records,
            summary=summary,
            calls_spent=_JEV_PROCESS_BUDGET.used,
        )
    else:
        payload = {"selector": args.selector, "summary": summary, "cases": records}
    # A starved run is not a comparison, exactly as a halted one is not. The two
    # conditions are marked separately so a reader can tell which one happened.
    if starved:
        payload["comparable"] = False
        payload["halted"] = _JEV_PROCESS_GUARD.halted
        payload["starved_cases"] = starved
        payload["starved_case_count"] = len(starved)
        notice = starved_notice(starved)
        existing = payload.get("notice")
        payload["notice"] = "%s %s" % (existing, notice) if existing else notice
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(
            payload,
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
        if record.get("incomplete"):
            mark += " INCOMPLETE(중단된 사례)"
        if record.get("starved"):
            mark += " STARVED(호출 예산 소진 — 보류가 아니다)"
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
                "     kinds=%s finish=%s decision=%s selector_defer=%s"
                % (
                    ",".join(record.get("investigated_kinds") or []) or "-",
                    record.get("finish_reason"),
                    record.get("decision_calls"),
                    "Y" if record.get("selector_deferred") else "N",
                )
            )

    print("\n== 요약 ==")
    for key in sorted(summary):
        if key in ("verdict", "passed"):
            continue
        print("%-34s %s" % (key, summary[key]))
    if args.dry_run:
        # No verdict is printed. A rehearsal has nothing to pass or fail, and a
        # PASS/FAIL line is the single thing most likely to be quoted as if it
        # were a JEV result.
        print("\n== 리허설 관측 (판정 아님) ==")
        print("%-34s %s" % ("완주한 사례", "%d / %d" % (len(records), len(case_ids))))
        print("%-34s %s" % ("사용한 호출 수", _JEV_PROCESS_BUDGET.used))
        print("%-34s %s" % ("호출 상한", _JEV_PROCESS_BUDGET.maximum))
        print("%-34s %s" % ("중단 여부", _JEV_PROCESS_GUARD.halted))
        print("%-34s %s" % ("실제 모델 호출", 0))
        print("\n결과 저장: %s" % out_path)
        print_dry_run_banner()
        print("전체: 리허설 완료 — PASS 도 FAIL 도 아니다. 비교 판정 없음.")
        if starved:
            print_starved_report(starved, out_path)
            return 2
        return 0

    print("\n== 기준 판정 ==")
    for code in sorted(summary["verdict"]):
        print("%-12s %s" % (code, "PASS" if summary["verdict"][code] else "FAIL"))
    print("\n결과 저장: %s" % out_path)
    print("전체: %s" % ("PASS" if summary["passed"] else "FAIL"))
    if starved:
        print_starved_report(starved, out_path)
        return 2
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
