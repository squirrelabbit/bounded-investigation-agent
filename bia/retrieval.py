"""Retrieval. The condition is a server-built EvidenceFilter; nothing else runs."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Sequence

from .types import EvidenceFilter, Ticket

MAX_RETRIEVAL_HITS = 200


@dataclass
class RetrievalResult:
    hits: List[Ticket] = field(default_factory=list)
    pool_size: int = 0
    truncated: bool = False

    def as_dict(self) -> Dict[str, object]:
        return {
            "hit_count": len(self.hits),
            "pool_size": self.pool_size,
            "truncated": self.truncated,
        }


def retrieve(tickets: Sequence[Ticket], evidence_filter: EvidenceFilter) -> RetrievalResult:
    matched = [t for t in tickets if evidence_filter.matches(t)]
    matched.sort(key=lambda t: (t.day, t.ticket_id))
    truncated = len(matched) > MAX_RETRIEVAL_HITS
    return RetrievalResult(matched[:MAX_RETRIEVAL_HITS], len(matched), truncated)
