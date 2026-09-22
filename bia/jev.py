"""JEV decision provider — the model answers one closed multiple-choice question.

Shape confirmed in eval/v1/CONTRACT.md §7-C: `typesafe-ai/jev` is an evaluation
model, not a chat model. It is sent a `state` blob plus typed `questions` and
answers with a chosen option and a probability distribution. There is no channel
for free text, no search string, no SQL — which is why this endpoint fits a
system whose whole point is that the model may only pick from a server-built
menu.

Boundaries this module keeps:

* **The server writes the entire question.** Options are exactly the offered
  candidate ids plus DEFER, and every option's description is composed from the
  server's own candidate fields. The model contributes nothing to the question.
* **Probabilities are recorded, never acted on.** The returned
  `probabilities` map is stored in the call record for reporting only. It is
  never compared, never thresholded, never allowed to change the returned value.
  The only thing that decides the return value is `choice`. A run in which the
  chosen option carries the lowest probability returns that option anyway.
* **Fail closed.** A transport exception, a non-200 status, an unparseable
  body, a missing `answers` map, a missing question key, a missing `choice`, or
  a `choice` outside the offered options all return DEFER with a recorded
  reason. Nothing raises into the Controller.
* **Zero retries.** A failed call is never repeated — not on 429, not on 5xx,
  not on a timeout. §7-C's execution lock budgets 16 calls for the whole run and
  no retries, so variance cannot be measured and is not pretended away.
* **No default that reaches the network.** The transport is injected.
  `HttpTransport` refuses to exist without an explicit `enable_network=True`
  and a key the caller passes in; it never reads the environment itself, so the
  decision to go live is visible at the call site.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from .decision import DecisionProvider
from .types import DEFER, EvidenceCandidate

JEV_MODEL = "typesafe-ai/jev"
JEV_ENDPOINT = "https://ai-gateway.vercel.sh/v1/evaluate"
QUESTION_KEY = "next_evidence"
QUESTION_TYPE = "choice"

DEFAULT_MAX_CALLS = 16
DEFAULT_TIMEOUT_SECONDS = 30.0

# Not documented for /v1/evaluate (§7-C). Guessing them would be inventing the
# contract, so nothing here may ever put them in a request body.
UNDOCUMENTED_REQUEST_KEYS = ("seed", "temperature", "max_tokens")

QUESTION_INSTRUCTIONS = (
    "아래 state 는 서버가 계산한 비교 결과다. 제시된 선택지 중 다음에 조사할 후보를 "
    "하나만 고르라. 증가를 설명할 근거를 실제로 얻을 수 있는 후보가 없다고 판단되면 "
    "DEFER 를 고르라. 선택지 밖의 값, 설명문, 검색 조건은 받지 않는다."
)

DEFER_OPTION_DESCRIPTION = (
    "조사하지 않고 보류한다. 제시된 어떤 후보도 증가를 설명할 근거를 주지 못한다고 "
    "판단될 때 고른다. 보류는 실패가 아니라 허용된 결과다."
)

REASON_NO_CANDIDATES = "no_candidates_offered"
REASON_BUDGET_EXHAUSTED = "call_budget_exhausted"
REASON_TRANSPORT_ERROR = "transport_error"
REASON_HTTP_STATUS = "http_status"
REASON_UNPARSEABLE = "unparseable_response"
REASON_API_ERROR = "api_error"
REASON_MISSING_ANSWERS = "missing_answers"
REASON_MISSING_QUESTION = "missing_question_key"
REASON_MISSING_CHOICE = "missing_choice"
REASON_CHOICE_NOT_OFFERED = "choice_not_offered"


class JevTransportError(Exception):
    """Anything that stopped a call from producing a parsed body."""


class JevHttpError(JevTransportError):
    def __init__(self, status: int, body: object = None) -> None:
        super().__init__("HTTP %s" % status)
        self.status = status
        self.body = body


class JevNetworkLocked(RuntimeError):
    """Raised when live network use is attempted without the explicit unlock."""


class Transport:
    """One method: take the request dict, return the parsed response dict.

    Implementations signal a non-200 status by raising `JevHttpError`; any other
    failure raises `JevTransportError` or an ordinary exception. The selector
    treats every one of those the same way — DEFER, recorded, not retried.
    """

    name = "abstract"

    def send(self, request: Dict[str, object]) -> Dict[str, object]:
        raise NotImplementedError


class FakeTransport(Transport):
    """Scripted responses. Every test in this repository uses this one.

    A scripted item is either a dict (returned as the parsed body) or an
    exception instance (raised). Once the script runs out, the last item repeats
    if `repeat_last` is set, otherwise a `JevTransportError` is raised.
    """

    name = "fake"

    def __init__(
        self, responses: Sequence[object] = (), repeat_last: bool = True
    ) -> None:
        self._responses = list(responses)
        self._repeat_last = repeat_last
        self.requests: List[Dict[str, object]] = []

    @property
    def call_count(self) -> int:
        return len(self.requests)

    def send(self, request: Dict[str, object]) -> Dict[str, object]:
        index = len(self.requests)
        self.requests.append(request)
        if not self._responses:
            raise JevTransportError("FakeTransport has no scripted response")
        if index < len(self._responses):
            item = self._responses[index]
        elif self._repeat_last:
            item = self._responses[-1]
        else:
            raise JevTransportError("FakeTransport script exhausted")
        if isinstance(item, BaseException):
            raise item
        if not isinstance(item, dict):
            return item  # type: ignore[return-value]
        return item


class HttpTransport(Transport):
    """The only thing in this package that can open a socket.

    It cannot be constructed by accident: `enable_network=True` and a non-empty
    key must both be passed by the caller. It does not read the environment — the
    caller does, so that "this run may spend money" is written at the call site
    rather than hidden in a default.
    """

    name = "http"

    def __init__(
        self,
        api_key: Optional[str] = None,
        enable_network: bool = False,
        endpoint: str = JEV_ENDPOINT,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if enable_network is not True:
            raise JevNetworkLocked(
                "HttpTransport requires enable_network=True; live JEV calls are "
                "locked by eval/v1/CONTRACT.md §7-C"
            )
        if not isinstance(api_key, str) or not api_key.strip():
            raise JevNetworkLocked(
                "HttpTransport requires a non-empty api_key passed by the caller; "
                "it does not read the environment itself"
            )
        self._api_key = api_key
        self._endpoint = endpoint
        self._timeout = timeout

    def send(self, request: Dict[str, object]) -> Dict[str, object]:
        import urllib.error
        import urllib.request

        payload = json.dumps(request, ensure_ascii=False).encode("utf-8")
        http_request = urllib.request.Request(
            self._endpoint,
            data=payload,
            method="POST",
            headers={
                "Authorization": "Bearer %s" % self._api_key,
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(http_request, timeout=self._timeout) as response:
                status = getattr(response, "status", None) or response.getcode()
                raw = response.read()
        except urllib.error.HTTPError as exc:  # non-2xx carries a body
            raise JevHttpError(exc.code, _loads_or_none(exc.read()))
        except Exception as exc:
            raise JevTransportError("%s: %s" % (type(exc).__name__, exc))
        if status != 200:
            raise JevHttpError(int(status), _loads_or_none(raw))
        parsed = _loads_or_none(raw)
        if not isinstance(parsed, dict):
            raise JevTransportError("response body is not a JSON object")
        return parsed


def _loads_or_none(raw: object) -> object:
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        if not isinstance(raw, str):
            return None
        return json.loads(raw)
    except Exception:
        return None


class CallBudget:
    """A hard ceiling on attempted calls, shared across a whole run.

    Every attempt decrements it, successful or not. There is no refund for a
    failed call, because a failed call was still a request.
    """

    def __init__(self, maximum: int = DEFAULT_MAX_CALLS) -> None:
        if maximum < 0:
            raise ValueError("call budget maximum must be >= 0")
        self.maximum = maximum
        self.used = 0

    @property
    def remaining(self) -> int:
        return self.maximum - self.used

    def try_consume(self) -> bool:
        if self.used >= self.maximum:
            return False
        self.used += 1
        return True

    def as_dict(self) -> Dict[str, int]:
        return {"maximum": self.maximum, "used": self.used, "remaining": self.remaining}


@dataclass
class JevCallRecord:
    """One decision's worth of telemetry. `probabilities` is reporting only."""

    question_key: str
    offered_options: List[str]
    called: bool
    choice: Optional[str] = None
    returned: str = DEFER
    probabilities: Dict[str, float] = field(default_factory=dict)
    usage: Dict[str, object] = field(default_factory=dict)
    cost: Optional[str] = None
    generation_id: Optional[str] = None
    failure_reason: Optional[str] = None

    def as_dict(self) -> Dict[str, object]:
        return {
            "question_key": self.question_key,
            "offered_options": list(self.offered_options),
            "called": self.called,
            "choice": self.choice,
            "returned": self.returned,
            "probabilities": dict(self.probabilities),
            "usage": dict(self.usage),
            "cost": self.cost,
            "generation_id": self.generation_id,
            "failure_reason": self.failure_reason,
        }


def state_payload(state: Dict[str, object]) -> Dict[str, object]:
    """The small, deterministic blob we are willing to send.

    Derived from the Controller's snapshot. It carries comparison windows,
    period completeness, totals and delta, the top contributing groups, which
    candidates were already investigated, coverage so far and the remaining
    budget. It carries no ticket id and no ticket text: the model chooses which
    group to look at, and never reads the evidence itself.
    """
    metrics = state.get("metrics") or {}
    if not isinstance(metrics, dict):
        metrics = {}
    current_integrity = _as_dict(state.get("current_integrity"))
    baseline_integrity = _as_dict(state.get("baseline_integrity"))
    comparability = _as_dict(state.get("comparability"))
    budget_left = _as_dict(state.get("budget_left"))

    return {
        "current_window": _window(state.get("current_window")),
        "baseline_window": _window(state.get("baseline_window")),
        "current_period_complete": bool(current_integrity.get("complete")),
        "baseline_period_complete": bool(baseline_integrity.get("complete")),
        "current_period_completeness": current_integrity.get("completeness"),
        "baseline_period_completeness": baseline_integrity.get("completeness"),
        "comparability_mode": comparability.get("mode"),
        "current_total": metrics.get("current_total"),
        "baseline_total": metrics.get("baseline_total"),
        "delta": metrics.get("delta"),
        "pct_change": metrics.get("pct_change"),
        "top_products": _groups(state.get("top_products")),
        "top_complaint_types": _groups(state.get("top_complaint_types")),
        "investigated_candidate_ids": sorted(
            str(c) for c in (state.get("investigated_candidates") or [])
        ),
        "coverage_so_far": state.get("coverage"),
        "evidence_sufficient": bool(state.get("evidence_sufficient")),
        "decision_calls_used": state.get("decision_calls"),
        "retrievals_used": state.get("retrievals"),
        "decision_calls_left": budget_left.get("decision_calls"),
        "retrievals_left": budget_left.get("retrievals"),
    }


def _as_dict(value: object) -> Dict[str, object]:
    return value if isinstance(value, dict) else {}


def _window(value: object) -> Optional[Dict[str, object]]:
    data = _as_dict(value)
    if not data:
        return None
    return {"start": data.get("start"), "end": data.get("end"), "days": data.get("days")}


def _groups(value: object) -> List[Dict[str, object]]:
    out: List[Dict[str, object]] = []
    for group in value or []:
        data = _as_dict(group)
        if not data:
            continue
        out.append(
            {
                "value": data.get("value"),
                "delta": data.get("delta"),
                "share_of_increase": data.get("share_of_increase"),
            }
        )
    return out


def serialize_state(state: Dict[str, object]) -> str:
    return json.dumps(state_payload(state), sort_keys=True, ensure_ascii=False)


def describe_candidate(candidate: EvidenceCandidate) -> str:
    """Server-written option text. Composed only from server-computed fields."""
    return (
        "%s (수준: %s). 이 그룹의 증가분 %d건, 조회 가능한 문의 %d건. 서버 설명: %s"
        % (
            candidate.label,
            candidate.kind,
            candidate.group_delta,
            candidate.available_tickets,
            candidate.server_reason,
        )
    )


def build_criteria(candidates: Sequence[EvidenceCandidate]) -> Dict[str, str]:
    criteria = {c.candidate_id: describe_candidate(c) for c in candidates}
    criteria[DEFER] = DEFER_OPTION_DESCRIPTION
    return criteria


class JevSelector(DecisionProvider):
    """DecisionProvider backed by the JEV evaluation endpoint.

    The transport is injected and there is no network default. Probabilities
    coming back from the model are stored on the call record and have no effect
    on the returned value — the return value is `choice`, validated against the
    offered options, or DEFER.
    """

    name = "jev"

    def __init__(
        self,
        transport: Transport,
        budget: Optional[CallBudget] = None,
        model: str = JEV_MODEL,
        question_key: str = QUESTION_KEY,
    ) -> None:
        if transport is None:
            raise ValueError("JevSelector requires an explicit transport")
        self.transport = transport
        self.budget = budget if budget is not None else CallBudget()
        self.model = model
        self.question_key = question_key
        self.records: List[JevCallRecord] = []

    # -- telemetry ---------------------------------------------------------

    @property
    def call_count(self) -> int:
        return sum(1 for r in self.records if r.called)

    @property
    def total_cost(self) -> float:
        total = 0.0
        for record in self.records:
            try:
                total += float(record.cost)
            except (TypeError, ValueError):
                continue
        return total

    def telemetry(self) -> Dict[str, object]:
        return {
            "model": self.model,
            "calls_made": self.call_count,
            "decisions": len(self.records),
            "total_cost": self.total_cost,
            "budget": self.budget.as_dict(),
            "records": [r.as_dict() for r in self.records],
        }

    # -- request -----------------------------------------------------------

    def build_request(
        self, state: Dict[str, object], candidates: Sequence[EvidenceCandidate]
    ) -> Dict[str, object]:
        return {
            "model": self.model,
            "state": serialize_state(state),
            "questions": {
                self.question_key: {
                    "type": QUESTION_TYPE,
                    "instructions": QUESTION_INSTRUCTIONS,
                    "criteria": build_criteria(candidates),
                }
            },
        }

    # -- decision ----------------------------------------------------------

    def select_next_evidence(
        self, state: Dict[str, object], candidates: Sequence[EvidenceCandidate]
    ) -> str:
        options = [c.candidate_id for c in candidates] + [DEFER]

        if not candidates:
            self._record(options, called=False, reason=REASON_NO_CANDIDATES)
            return DEFER

        if not self.budget.try_consume():
            self._record(options, called=False, reason=REASON_BUDGET_EXHAUSTED)
            return DEFER

        request = self.build_request(state, candidates)
        try:
            body = self.transport.send(request)
        except JevHttpError as exc:
            self._record(options, called=True, reason="%s:%s" % (REASON_HTTP_STATUS, exc.status))
            return DEFER
        except Exception as exc:  # no retry, ever
            self._record(
                options,
                called=True,
                reason="%s:%s" % (REASON_TRANSPORT_ERROR, type(exc).__name__),
            )
            return DEFER

        return self._interpret(body, options)

    def _interpret(self, body: object, options: Sequence[str]) -> str:
        if not isinstance(body, dict):
            self._record(options, called=True, reason=REASON_UNPARSEABLE)
            return DEFER

        error = body.get("error")
        if isinstance(error, dict):
            self._record(
                options,
                called=True,
                reason="%s:%s" % (REASON_API_ERROR, error.get("type") or "unknown"),
            )
            return DEFER

        answers = body.get("answers")
        if not isinstance(answers, dict):
            self._record(options, called=True, reason=REASON_MISSING_ANSWERS)
            return DEFER

        answer = answers.get(self.question_key)
        if not isinstance(answer, dict):
            self._record(options, called=True, reason=REASON_MISSING_QUESTION)
            return DEFER

        choice = answer.get("choice")
        probabilities = answer.get("probabilities")
        probabilities = probabilities if isinstance(probabilities, dict) else {}
        usage = body.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        gateway = _as_dict(_as_dict(body.get("providerMetadata")).get("gateway"))
        cost = gateway.get("cost")
        generation_id = gateway.get("generationId")

        common = {
            "probabilities": probabilities,
            "usage": usage,
            "cost": cost if isinstance(cost, str) else (str(cost) if cost is not None else None),
            "generation_id": generation_id if isinstance(generation_id, str) else None,
        }

        if not isinstance(choice, str):
            self._record(options, called=True, reason=REASON_MISSING_CHOICE, **common)
            return DEFER

        if choice not in options:
            self._record(
                options,
                called=True,
                choice=choice,
                reason=REASON_CHOICE_NOT_OFFERED,
                **common
            )
            return DEFER

        # The only line that decides the return value. Probabilities were parsed
        # above and are stored; they are deliberately not consulted here.
        self._record(options, called=True, choice=choice, returned=choice, **common)
        return choice

    def _record(
        self,
        options: Sequence[str],
        called: bool,
        choice: Optional[str] = None,
        returned: str = DEFER,
        reason: Optional[str] = None,
        probabilities: Optional[Dict[str, float]] = None,
        usage: Optional[Dict[str, object]] = None,
        cost: Optional[str] = None,
        generation_id: Optional[str] = None,
    ) -> None:
        self.records.append(
            JevCallRecord(
                question_key=self.question_key,
                offered_options=list(options),
                called=called,
                choice=choice,
                returned=returned,
                probabilities=dict(probabilities or {}),
                usage=dict(usage or {}),
                cost=cost,
                generation_id=generation_id,
                failure_reason=reason,
            )
        )
