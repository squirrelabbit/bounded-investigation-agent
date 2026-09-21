"""The one probabilistic boundary in the system.

    select_next_evidence(state, server_generated_candidates) -> candidate_id | DEFER

That is the whole contract. A provider does not compute numbers, does not write
search conditions, does not skip verification, and cannot end the run.

`state` is a read-only snapshot (a deep copy produced by `EvidenceState.view()`),
not the live run state, and every candidate is frozen. Writing to either changes
nothing. An exception raised here is caught by the Controller and recorded as a
violation, so a provider cannot abort the run either.
"""
from __future__ import annotations

from typing import Dict, List, Sequence

from .evidence import MIN_ADMITTED_TICKETS, NOISE_FLOOR_DELTA
from .types import DEFER, EvidenceCandidate

class DecisionProvider:
    """Interface. Implementations must be side-effect free."""

    name = "abstract"

    def select_next_evidence(
        self, state: Dict[str, object], candidates: Sequence[EvidenceCandidate]
    ) -> str:
        raise NotImplementedError


class DeterministicHeuristicSelector(DecisionProvider):
    """The code-only baseline. No network, no model, fully reproducible.

    Rule: investigate the group that carries the largest part of the increase.
    If that group cannot clear the evidence floor, defer — do not substitute a
    smaller group's tickets. Showing P-Alpha's complaints while the increase sits
    in P-Beta invites the reader to misread association as explanation, which is
    the failure this system exists to prevent.

    The server also offers broader product-level and type-level candidates. This
    baseline never takes them; a future probabilistic provider may, and the
    evaluation's precision metric is what would show whether that helps.
    """

    name = "heuristic"

    def select_next_evidence(
        self, state: Dict[str, object], candidates: Sequence[EvidenceCandidate]
    ) -> str:
        cells = [c for c in candidates if c.kind == "cell"]
        if not cells:
            return DEFER
        largest = max(cells, key=lambda c: (c.group_delta, c.candidate_id))
        if largest.group_delta < NOISE_FLOOR_DELTA:
            return DEFER
        if largest.available_tickets < MIN_ADMITTED_TICKETS:
            return DEFER
        return largest.candidate_id


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
        self, state: Dict[str, object], candidates: Sequence[EvidenceCandidate]
    ) -> str:
        if self._index >= len(self._script):
            value = self._on_exhausted
        else:
            value = self._script[self._index]
            self._index += 1
        self.calls.append(value)
        return value
