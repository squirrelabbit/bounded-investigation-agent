"""Verification. Always run by the server, never skippable, never delegated.

Every retrieved ticket is re-checked against the store, the allowed sources, the
comparison window, the filter it was supposed to satisfy, whether its own text
supports the label it carries, and whether the group it belongs to actually rose.

That last check is the one that keeps a broad retrieval honest. A product- or
type-wide filter sweeps in tickets from cells that did not increase at all;
printing them under "this is what customers wrote about the increase" invites
exactly the misreading this system exists to prevent. Retrieval stays wide —
admission does not. The check is a required argument rather than an option
precisely so that no caller can quietly skip it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Sequence, Set, Tuple

from .evidence import MIN_ADMITTED_TICKETS, MIN_COVERAGE, AdmittedTicket
from .lexicon import supports_label
from .types import ALLOWED_TICKET_SOURCES, EvidenceFilter, Ticket

EXCERPT_CHARS = 120

REJECT_UNKNOWN_ID = "unknown_ticket_id"
REJECT_BAD_SOURCE = "source_not_allowed"
REJECT_OUT_OF_WINDOW = "outside_comparison_window"
REJECT_FILTER_MISMATCH = "does_not_match_requested_filter"
REJECT_UNSUPPORTED_TEXT = "text_does_not_support_its_own_label"
REJECT_GROUP_DID_NOT_INCREASE = "group_did_not_increase"


@dataclass
class VerificationResult:
    admitted: List[AdmittedTicket] = field(default_factory=list)
    rejected: List[Dict[str, str]] = field(default_factory=list)
    pool_size: int = 0

    @property
    def coverage(self) -> float:
        if self.pool_size <= 0:
            return 0.0
        return round(len(self.admitted) / float(self.pool_size), 4)

    @property
    def sufficient(self) -> bool:
        return len(self.admitted) >= MIN_ADMITTED_TICKETS and self.coverage >= MIN_COVERAGE

    def as_dict(self) -> Dict[str, object]:
        return {
            "admitted": [a.as_dict() for a in self.admitted],
            "rejected": list(self.rejected),
            "pool_size": self.pool_size,
            "coverage": self.coverage,
            "sufficient": self.sufficient,
        }


def _excerpt(text: str) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= EXCERPT_CHARS:
        return collapsed
    return collapsed[: EXCERPT_CHARS - 1] + "…"


def verify(
    hits: Sequence[Ticket],
    evidence_filter: EvidenceFilter,
    known_ticket_ids: Set[str],
    pool_size: int,
    increasing_cells: Set[Tuple[str, str]],
) -> VerificationResult:
    """`increasing_cells` is the set of (product, complaint_type) whose count rose
    over the comparison window. It comes from the server's own arithmetic; it is
    required, and passing an empty set admits nothing."""
    result = VerificationResult(pool_size=pool_size)
    for ticket in hits:
        reason = _reject_reason(ticket, evidence_filter, known_ticket_ids, increasing_cells)
        if reason is not None:
            result.rejected.append({"ticket_id": ticket.ticket_id, "reason": reason})
            continue
        result.admitted.append(
            AdmittedTicket(
                ticket_id=ticket.ticket_id,
                day=ticket.day.isoformat(),
                product=ticket.product,
                complaint_type=ticket.complaint_type,
                source=ticket.source,
                excerpt=_excerpt(ticket.text),
            )
        )
    result.admitted.sort(key=lambda a: (a.day, a.ticket_id))
    result.rejected.sort(key=lambda r: r["ticket_id"])
    return result


def _reject_reason(
    ticket: Ticket,
    evidence_filter: EvidenceFilter,
    known: Set[str],
    increasing_cells: Set[Tuple[str, str]],
):
    if ticket.ticket_id not in known:
        return REJECT_UNKNOWN_ID
    if ticket.source not in ALLOWED_TICKET_SOURCES:
        return REJECT_BAD_SOURCE
    if not evidence_filter.window.contains(ticket.day):
        return REJECT_OUT_OF_WINDOW
    if evidence_filter.product is not None and ticket.product != evidence_filter.product:
        return REJECT_FILTER_MISMATCH
    if evidence_filter.complaint_type is not None and ticket.complaint_type != evidence_filter.complaint_type:
        return REJECT_FILTER_MISMATCH
    if not supports_label(ticket.text, ticket.complaint_type):
        return REJECT_UNSUPPORTED_TEXT
    if (ticket.product, ticket.complaint_type) not in increasing_cells:
        return REJECT_GROUP_DID_NOT_INCREASE
    return None
