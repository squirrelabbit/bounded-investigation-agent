"""The one probabilistic boundary in the system.

    select_next_evidence(state, server_generated_candidates) -> candidate_id | DEFER

That is the whole contract. A provider does not compute numbers, does not write
search conditions, does not skip verification, and cannot end the run.
"""
from __future__ import annotations

from typing import List, Sequence

from .evidence import EvidenceState, MIN_ADMITTED_TICKETS, NOISE_FLOOR_DELTA
from .types import DEFER, EvidenceCandidate

KIND_PRIORITY = {"cell": 0, "product": 1, "complaint_type": 2}


class DecisionProvider:
    """Interface. Implementations must be side-effect free."""

    name = "abstract"

    def select_next_evidence(
        self, state: EvidenceState, candidates: Sequence[EvidenceCandidate]
    ) -> str:
        raise NotImplementedError


class DeterministicHeuristicSelector(DecisionProvider):
    """The code-only baseline. No network, no model, fully reproducible.

    Rule: among candidates that carry a large enough increase AND have enough
    retrievable tickets to clear the evidence floor, take the most specific one,
    breaking ties by the size of the increase. Otherwise defer.
    """

    name = "heuristic"

    def select_next_evidence(
        self, state: EvidenceState, candidates: Sequence[EvidenceCandidate]
    ) -> str:
        viable: List[EvidenceCandidate] = [
            c
            for c in candidates
            if c.group_delta >= NOISE_FLOOR_DELTA and c.available_tickets >= MIN_ADMITTED_TICKETS
        ]
        if not viable:
            return DEFER
        viable.sort(
            key=lambda c: (KIND_PRIORITY.get(c.kind, 9), -c.group_delta, c.candidate_id)
        )
        return viable[0].candidate_id


class ScriptedSelector(DecisionProvider):
    """Replays a fixed script of returns, including invalid ones.

    Used to prove the Controller's fail-closed behaviour: an id outside the
    offered set, an already-investigated id, a malformed value or an exhausted
    script must all be downgraded to DEFER and recorded as a violation.
    """

    name = "scripted"

    def __init__(self, script: Sequence[str], on_exhausted: str = DEFER) -> None:
        self._script = list(script)
        self._index = 0
        self._on_exhausted = on_exhausted
        self.calls: List[str] = []

    def select_next_evidence(
        self, state: EvidenceState, candidates: Sequence[EvidenceCandidate]
    ) -> str:
        if self._index >= len(self._script):
            value = self._on_exhausted
        else:
            value = self._script[self._index]
            self._index += 1
        self.calls.append(value)
        return value
