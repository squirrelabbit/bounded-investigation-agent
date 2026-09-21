"""The Controller owns the loop, the budget, and the decision to stop.

The DecisionProvider is called at most MAX_DECISION_CALLS times and can only
answer with one of the offered candidate ids or DEFER. Anything else is
downgraded to DEFER and recorded as a violation — fail closed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from . import integrity as integrity_mod
from . import metrics as metrics_mod
from .answer import AnswerDocument, build_answer
from .decision import DecisionProvider
from .evidence import (
    MAX_DECISION_CALLS,
    MAX_RETRIEVALS,
    EvidenceRound,
    EvidenceState,
    build_candidates,
)
from .retrieval import retrieve
from .types import (
    DEFER,
    DIM_COMPLAINT_TYPE,
    DIM_PRODUCT,
    AnalysisIntent,
    EvidenceCandidate,
    MetricRow,
    Ticket,
)
from .verify import verify

FINISH_BLOCKED = "data_integrity_blocked"
FINISH_NO_INCREASE = "no_increase_to_investigate"
FINISH_NO_CANDIDATES = "no_candidate_left"
FINISH_DEFERRED = "provider_deferred"
FINISH_SUFFICIENT = "evidence_sufficient"
FINISH_DECISION_BUDGET = "decision_budget_exhausted"
FINISH_RETRIEVAL_BUDGET = "retrieval_budget_exhausted"

VIOLATION_UNKNOWN_ID = "selection_not_in_offered_candidates"
VIOLATION_REPEAT = "selection_already_investigated"
VIOLATION_MALFORMED = "selection_not_a_string"
VIOLATION_RAISED = "provider_raised"


MAX_RECORDED_SELECTION_CHARS = 120


def _short(raw) -> str:
    text = raw if isinstance(raw, str) else repr(raw)
    if len(text) <= MAX_RECORDED_SELECTION_CHARS:
        return text
    return text[: MAX_RECORDED_SELECTION_CHARS - 1] + "\u2026"


@dataclass
class RunResult:
    intent: AnalysisIntent
    state: EvidenceState
    answer: AnswerDocument
    provider_name: str

    def as_dict(self) -> Dict[str, object]:
        return {
            "intent": self.intent.as_dict(),
            "provider": self.provider_name,
            "state": self.state.as_dict(),
            "answer": self.answer.as_dict(),
        }


def investigate(
    intent: AnalysisIntent,
    rows: Sequence[MetricRow],
    tickets: Sequence[Ticket],
    provider: DecisionProvider,
) -> RunResult:
    intent.validate()

    current_rows, current_integrity = integrity_mod.inspect_period(list(rows), intent.current_period)
    baseline_rows, baseline_integrity = integrity_mod.inspect_period(list(rows), intent.baseline_period)
    comparability = integrity_mod.decide_comparability(current_integrity, baseline_integrity)

    state = EvidenceState(
        current_window=comparability.current_window,
        baseline_window=comparability.baseline_window,
        current_integrity=current_integrity,
        baseline_integrity=baseline_integrity,
        comparability=comparability,
        metrics=None,
    )

    if not comparability.usable:
        state.finish_reason = FINISH_BLOCKED
        return RunResult(intent, state, build_answer(state), provider.name)

    clean_rows = current_rows + baseline_rows
    assert comparability.current_window is not None
    assert comparability.baseline_window is not None
    state.metrics = metrics_mod.compute(
        clean_rows, comparability.current_window, comparability.baseline_window
    )
    state.top_products = state.metrics.top(DIM_PRODUCT)
    state.top_complaint_types = state.metrics.top(DIM_COMPLAINT_TYPE)

    if not state.metrics.increased:
        state.finish_reason = FINISH_NO_INCREASE
        return RunResult(intent, state, build_answer(state), provider.name)

    known_ids = {t.ticket_id for t in tickets}
    _evidence_loop(state, tickets, known_ids, provider)
    return RunResult(intent, state, build_answer(state), provider.name)


def _evidence_loop(
    state: EvidenceState,
    tickets: Sequence[Ticket],
    known_ids,
    provider: DecisionProvider,
) -> None:
    for round_index in range(1, MAX_DECISION_CALLS + 1):
        candidates = build_candidates(state, tickets, round_index)
        if not candidates:
            state.finish_reason = FINISH_NO_CANDIDATES
            return

        state.decision_calls += 1
        try:
            raw = provider.select_next_evidence(state.view(), list(candidates))
        except Exception as exc:  # a provider must not be able to abort the run
            raw = "%s: %s" % (type(exc).__name__, exc)
            chosen, violation = None, VIOLATION_RAISED
        else:
            chosen, violation = _resolve_selection(raw, candidates, state)

        round_ = EvidenceRound(
            round_index=round_index,
            candidate_id=chosen.candidate_id if chosen else "",
            candidate_label=chosen.label if chosen else "",
            offered_candidate_ids=[c.candidate_id for c in candidates],
            selection_raw=_short(raw),
            selection_valid=chosen is not None,
            violation=violation,
            retrieved=0,
        )
        if violation:
            state.violations.append("round%d:%s:%s" % (round_index, violation, round_.selection_raw))
        state.rounds.append(round_)

        if chosen is None:
            state.finish_reason = FINISH_DEFERRED
            return

        if state.retrievals >= MAX_RETRIEVALS:
            state.finish_reason = FINISH_RETRIEVAL_BUDGET
            return

        state.retrievals += 1
        retrieval = retrieve(tickets, chosen.evidence_filter)
        verification = verify(retrieval.hits, chosen.evidence_filter, known_ids, retrieval.pool_size)

        round_.retrieved = len(retrieval.hits)
        round_.pool_size = verification.pool_size
        round_.truncated = retrieval.truncated
        round_.admitted = verification.admitted
        round_.rejected = verification.rejected
        round_.coverage = verification.coverage
        round_.sufficient = verification.sufficient

        state.investigated_candidates.append(chosen.candidate_id)
        state.investigated_filters.append(chosen.evidence_filter.identity())

        if verification.sufficient:
            state.finish_reason = FINISH_SUFFICIENT
            return

    state.finish_reason = FINISH_DECISION_BUDGET


def _resolve_selection(
    raw, candidates: Sequence[EvidenceCandidate], state: EvidenceState
):
    """Fail closed: anything that is not a live, unused candidate id becomes DEFER."""
    if not isinstance(raw, str):
        return None, VIOLATION_MALFORMED
    if raw == DEFER:
        return None, None
    by_id = {c.candidate_id: c for c in candidates}
    if raw not in by_id:
        return None, VIOLATION_UNKNOWN_ID
    if raw in state.investigated_candidates:
        return None, VIOLATION_REPEAT
    return by_id[raw], None
