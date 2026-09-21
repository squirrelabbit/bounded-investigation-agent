"""EvidenceState and the server-built candidate set.

Candidates are closed: the server decides what each one means, what it retrieves,
and whether it may run. A DecisionProvider only returns one candidate_id, or DEFER.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from .integrity import Comparability, PeriodIntegrity
from .lexicon import label_ko
from .metrics import MetricResult
from .types import (
    DIM_COMPLAINT_TYPE,
    DIM_PRODUCT,
    EvidenceCandidate,
    EvidenceFilter,
    GroupDelta,
    Period,
    Ticket,
)

MAX_CANDIDATES_PER_DECISION = 4
MAX_DECISION_CALLS = 2
MAX_RETRIEVALS = 2
MIN_ADMITTED_TICKETS = 3
MIN_COVERAGE = 0.5
NOISE_FLOOR_DELTA = 3


@dataclass
class AdmittedTicket:
    ticket_id: str
    day: str
    product: str
    complaint_type: str
    source: str
    excerpt: str

    def as_dict(self) -> Dict[str, str]:
        return {
            "ticket_id": self.ticket_id,
            "day": self.day,
            "product": self.product,
            "complaint_type": self.complaint_type,
            "source": self.source,
            "excerpt": self.excerpt,
        }


@dataclass
class EvidenceRound:
    round_index: int
    candidate_id: str
    candidate_label: str
    offered_candidate_ids: List[str]
    selection_raw: str
    selection_valid: bool
    violation: Optional[str]
    retrieved: int
    admitted: List[AdmittedTicket] = field(default_factory=list)
    rejected: List[Dict[str, str]] = field(default_factory=list)
    pool_size: int = 0
    coverage: float = 0.0
    sufficient: bool = False

    def as_dict(self) -> Dict[str, object]:
        return {
            "round": self.round_index,
            "offered_candidate_ids": list(self.offered_candidate_ids),
            "selection_raw": self.selection_raw,
            "selection_valid": self.selection_valid,
            "violation": self.violation,
            "candidate_id": self.candidate_id,
            "candidate_label": self.candidate_label,
            "pool_size": self.pool_size,
            "retrieved": self.retrieved,
            "admitted": [a.as_dict() for a in self.admitted],
            "rejected": list(self.rejected),
            "coverage": self.coverage,
            "sufficient": self.sufficient,
        }


@dataclass
class EvidenceState:
    """Everything the run knows so far. Passed read-only to the DecisionProvider."""

    current_window: Optional[Period]
    baseline_window: Optional[Period]
    current_integrity: PeriodIntegrity
    baseline_integrity: PeriodIntegrity
    comparability: Comparability
    metrics: Optional[MetricResult]
    top_products: List[GroupDelta] = field(default_factory=list)
    top_complaint_types: List[GroupDelta] = field(default_factory=list)
    investigated_candidates: List[str] = field(default_factory=list)
    investigated_filters: List[str] = field(default_factory=list)
    rounds: List[EvidenceRound] = field(default_factory=list)
    decision_calls: int = 0
    retrievals: int = 0
    violations: List[str] = field(default_factory=list)
    finish_reason: str = "not_finished"

    @property
    def delta(self) -> int:
        return self.metrics.delta if self.metrics else 0

    @property
    def found_tickets(self) -> List[AdmittedTicket]:
        out: List[AdmittedTicket] = []
        for round_ in self.rounds:
            out.extend(round_.admitted)
        return out

    @property
    def found_ticket_ids(self) -> List[str]:
        return [t.ticket_id for t in self.found_tickets]

    @property
    def sources_used(self) -> List[str]:
        return sorted({t.source for t in self.found_tickets})

    @property
    def coverage(self) -> float:
        rounds = [r for r in self.rounds if r.selection_valid]
        return max([r.coverage for r in rounds], default=0.0)

    @property
    def evidence_sufficient(self) -> bool:
        return any(r.sufficient for r in self.rounds)

    def budget_left(self) -> Dict[str, int]:
        return {
            "decision_calls": MAX_DECISION_CALLS - self.decision_calls,
            "retrievals": MAX_RETRIEVALS - self.retrievals,
        }

    def as_dict(self) -> Dict[str, object]:
        return {
            "current_window": self.current_window.as_dict() if self.current_window else None,
            "baseline_window": self.baseline_window.as_dict() if self.baseline_window else None,
            "current_integrity": self.current_integrity.as_dict(),
            "baseline_integrity": self.baseline_integrity.as_dict(),
            "comparability": self.comparability.as_dict(),
            "metrics": self.metrics.as_dict() if self.metrics else None,
            "top_products": [g.as_dict() for g in self.top_products],
            "top_complaint_types": [g.as_dict() for g in self.top_complaint_types],
            "investigated_candidates": list(self.investigated_candidates),
            "found_ticket_ids": self.found_ticket_ids,
            "sources_used": self.sources_used,
            "coverage": self.coverage,
            "evidence_sufficient": self.evidence_sufficient,
            "decision_calls": self.decision_calls,
            "retrievals": self.retrievals,
            "budget_left": self.budget_left(),
            "violations": list(self.violations),
            "finish_reason": self.finish_reason,
            "rounds": [r.as_dict() for r in self.rounds],
        }


def build_candidates(
    state: EvidenceState, tickets: Sequence[Ticket], round_index: int
) -> List[EvidenceCandidate]:
    """Server-generated candidate set, at most MAX_CANDIDATES_PER_DECISION.

    Built only from groups that actually carry a positive share of the increase.
    """
    if state.metrics is None or state.current_window is None:
        return []
    window = state.current_window
    seen_filters = set(state.investigated_filters)
    candidates: List[EvidenceCandidate] = []

    def add(kind: str, label: str, flt: EvidenceFilter, delta: int, reason: str) -> None:
        if len(candidates) >= MAX_CANDIDATES_PER_DECISION:
            return
        identity = flt.identity()
        if identity in seen_filters or any(c.evidence_filter.identity() == identity for c in candidates):
            return
        pool = sum(1 for t in tickets if flt.matches(t))
        candidates.append(
            EvidenceCandidate(
                candidate_id="R%d-C%d" % (round_index, len(candidates) + 1),
                kind=kind,
                label=label,
                evidence_filter=flt,
                group_delta=delta,
                available_tickets=pool,
                server_reason=reason,
            )
        )

    positive_cells = [c for c in state.metrics.cells if c.delta > 0]
    for cell in positive_cells[:2]:
        add(
            "cell",
            "%s / %s" % (cell.product, label_ko(cell.complaint_type)),
            EvidenceFilter(window, cell.product, cell.complaint_type),
            cell.delta,
            "이 조합에서 증가분 %d건이 발생했다" % cell.delta,
        )
    for group in state.top_products[:1]:
        add(
            "product",
            group.value,
            EvidenceFilter(window, product=group.value),
            group.delta,
            "제품 기준 증가 기여 1위 (증가분의 %.0f%%)" % (100 * group.share_of_increase),
        )
    for group in state.top_complaint_types[:1]:
        add(
            "complaint_type",
            label_ko(group.value),
            EvidenceFilter(window, complaint_type=group.value),
            group.delta,
            "불만 유형 기준 증가 기여 1위 (증가분의 %.0f%%)" % (100 * group.share_of_increase),
        )
    return candidates
