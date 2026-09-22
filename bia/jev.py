"""JEV decision provider — the model answers one closed multiple-choice question.

Call path: the **TypeSafe direct API**, `POST https://api.typesafe.ai/v1/systemone`.
Shape confirmed in eval/v1/CONTRACT.md §7-D. §7-C's Vercel AI Gateway path is
cancelled and survives only as an investigation record in the contract; it is
gone from this module on purpose, so that there is exactly one live endpoint in
the code and a future live run cannot reach the wrong one.

JEV is an evaluation model, not a chat model. It is sent a `state` blob plus
typed `questions` and answers with a chosen option, a confidence and a
probability distribution. There is no channel for free text, no search string,
no SQL — which is why this endpoint fits a system whose whole point is that the
model may only pick from a server-built menu.

Boundaries this module keeps:

* **The server writes the entire question.** Options are exactly the offered
  candidate ids plus DEFER, and every option's description is composed from the
  server's own candidate fields. The model contributes nothing to the question.
  A `choice` question has no `options` field: the options ARE the keys of
  `criteria` (§7-D, max 255).
* **The model version is pinned.** `jev-1.13.0`, never a moving alias. An alias
  would let the same test paper be answered by a different model and void the
  comparison. The response's own `model` is recorded on every call so it can be
  checked after the fact which version actually answered.
* **Confidence and probabilities are recorded, never acted on.** Both are stored
  on the call record for reporting only. Neither is compared, thresholded, or
  allowed to change the returned value. The only thing that decides the return
  value is `choice`. A run in which the chosen option carries the lowest
  probability and a low confidence returns that option anyway.
* **Cost is an estimate and is named as one.** The response body carries no cost
  field (§7-D). `estimated_cost_usd` is computed here from
  `usage.input_tokens` and the published rate; it is not a billed figure.
* **Fail closed.** A transport exception, a non-200 status, an unparseable body,
  a missing `answers` map, a missing question key, a missing `choice`, or a
  `choice` outside the offered options all return DEFER with a recorded reason.
  Nothing raises into the Controller.
* **Zero retries.** A failed call is never repeated — not on 429, not on 529,
  not on a timeout. §7-D keeps §7-C's budget of 16 calls for the whole run with
  no retries, so variance cannot be measured and is not pretended away. The
  transport is dependency-free direct HTTP precisely because the official SDK
  retries by default and a path whose retry count cannot be proven is not used.
* **The error body's schema is undocumented.** §7-D says so explicitly, so
  nothing here reads a field out of it. A failure is recorded by status code
  only, and the body is parsed with a helper that returns None rather than
  raising.
* **No default that reaches the network.** The transport is injected.
  `HttpTransport` refuses to exist without an explicit `enable_network=True`
  and a key the caller passes in; it never reads the environment itself, so the
  decision to go live is visible at the call site.
* **A failed call halts the run (§7-E).** `HttpTransport` has never executed, so
  the first call of a live run is also the probe. Fail-closed alone would turn
  every broken call into a DEFER and quietly spend all 16 calls producing a
  comparison that means nothing. So a second, separate device exists: a
  `RunGuard` shared across the whole run. The provider still returns DEFER and
  still raises nothing into the Controller — and at the same time it raises the
  halt flag, after which it makes no further call and spends no further budget.
  The scorer reads the flag between cases and stops. There is no retry, no
  backoff and no resume here: resuming is a separately approved decision.
* **The first completed response is checked strictly (§7-E).** Top-level
  `model`, `answers[<question key>]`, its `choice`, and `usage.input_tokens`
  must all be present and of the documented type. The offending field's name is
  recorded. `answers`, the question key and `choice` are checked on every call
  by the fail-closed path above (and each of those halts too); the first-call
  check adds `model` and `usage.input_tokens`, which are otherwise merely
  recorded when present.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from .decision import DecisionProvider
from .types import DEFER, EvidenceCandidate

# Pinned version, never a moving alias (§7-D "모델 버전 고정").
JEV_MODEL = "jev-1.13.0"
JEV_MODEL_ALIASES = ("jev-latest", "jev-preview")
JEV_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
QUESTION_KEY = "next_evidence"
QUESTION_TYPE = "choice"

DEFAULT_MAX_CALLS = 16
DEFAULT_TIMEOUT_SECONDS = 30.0

# §7-D: the request has exactly these three top-level fields and all are
# required. Nothing else is documented, so nothing else is sent.
DOCUMENTED_REQUEST_KEYS = ("model", "questions", "state")

# Not documented for /v1/systemone (§7-D). Guessing them would be inventing the
# contract, so nothing here may ever put them in a request body.
UNDOCUMENTED_REQUEST_KEYS = ("seed", "temperature", "top_p", "max_tokens")

# §7-D: a choice question may offer at most 255 options.
MAX_CHOICE_OPTIONS = 255

# §7-D: the request identifier is a response header, not a body field, and it
# may be absent.
REQUEST_ID_HEADER = "x-typesafe-request-id"

# §7-D 비용 기록: the response carries no cost. Published rate as of 2026-09-22 —
# input $0.042 per million tokens, output free. Anything derived from these is
# an estimate and is named as one.
INPUT_USD_PER_MILLION_TOKENS = 0.042
OUTPUT_USD_PER_MILLION_TOKENS = 0.0
COST_RATE_AS_OF = "2026-09-22"
COST_BASIS = (
    "estimate computed locally from usage.input_tokens at $%s/1M input tokens "
    "(output free), rates as of %s; the response body carries no cost field"
    % (INPUT_USD_PER_MILLION_TOKENS, COST_RATE_AS_OF)
)

# Structured instructions: part of the question comes from code, and the docs say
# such a value belongs in its own field rather than spliced into a string. Phrased
# as a comparison of given options, not as "work out the best course of action" —
# that shape is documented as the wrong one for a snap judgment.
QUESTION_INSTRUCTIONS = {
    "question": "아래 선택지 중 어느 것이 이 증가에 대한 고객 문의 근거를 가장 잘 얻게 하는가?",
    "focus": (
        "`top_products` 와 `top_complaint_types` 가 증가분이 발생한 위치다. "
        "각 선택지는 서버가 이미 만들어 둔 조회 조건이고, 고른 것 하나만 실행된다."
    ),
    "not_your_job": (
        "수치 계산, 검색어 작성, 조회 조건 변경, 종료 판단은 하지 않는다. "
        "선택지 하나를 고르는 것이 전부다."
    ),
}

# Every option carries the same two field names so the model can compare them
# directly. `not_for` is the documented remedy for options that are easy to
# confuse — and ours are: a narrow cell sits in the same list as the product and
# the complaint type that contain it.
DEFER_OPTION_DESCRIPTION = {
    "what": (
        "조사할 만한 후보가 없다고 보고 보류한다. 보류는 실패가 아니라 허용된 결과다."
    ),
    "not_for": (
        "위 선택지 중 하나라도 증가한 그룹의 문의를 실제로 얻게 해 준다면 고르지 않는다."
    ),
}

_KIND_WHAT = {
    "cell": "%s 조합의 문의만 조회한다. 이 조합의 증가분 %d건, 조회 가능한 문의 %d건. %s",
    "product": "%s 제품의 모든 불만 유형을 한 번에 조회한다. 이 제품의 증가분 %d건, 조회 가능한 문의 %d건. %s",
    "complaint_type": "%s 유형의 문의를 모든 제품에 걸쳐 조회한다. 이 유형의 증가분 %d건, 조회 가능한 문의 %d건. %s",
}

_KIND_NOT_FOR = {
    "cell": (
        "같은 제품의 다른 불만 유형이나 같은 불만 유형의 다른 제품은 포함하지 않는다. "
        "그 넓은 범위는 별도 선택지로 나와 있다."
    ),
    "product": (
        "특정 (제품, 불만 유형) 조합 하나만 보려는 경우에는 고르지 않는다. 그 조합은 별도 선택지다. "
        "이 선택지는 증가하지 않은 유형의 문의까지 함께 걸린다."
    ),
    "complaint_type": (
        "특정 (제품, 불만 유형) 조합 하나만 보려는 경우에는 고르지 않는다. 그 조합은 별도 선택지다. "
        "이 선택지는 증가하지 않은 제품의 문의까지 함께 걸린다."
    ),
}

# Sibling options are asked in the order they appear, so order is part of the
# question rather than presentation. Narrow before broad, escape option last.
OPTION_KIND_ORDER = ("cell", "product", "complaint_type")

REASON_NO_CANDIDATES = "no_candidates_offered"
REASON_TOO_MANY_OPTIONS = "too_many_options"
REASON_BUDGET_EXHAUSTED = "call_budget_exhausted"
REASON_TRANSPORT_ERROR = "transport_error"
REASON_HTTP_STATUS = "http_status"
REASON_UNPARSEABLE = "unparseable_response"
REASON_MISSING_ANSWERS = "missing_answers"
REASON_MISSING_QUESTION = "missing_question_key"
REASON_MISSING_CHOICE = "missing_choice"
REASON_CHOICE_NOT_OFFERED = "choice_not_offered"
REASON_FIRST_RESPONSE_SHAPE = "first_response_shape_mismatch"
REASON_RUN_HALTED = "skipped_run_halted"

# The failure reasons that stop the whole run (§7-E 중단 규칙). Budget
# exhaustion, an empty candidate list and an over-long option list are not on
# this list: none of them is a failed call, and none of them says anything about
# whether the endpoint works.
HALTING_REASONS = (
    REASON_TRANSPORT_ERROR,
    REASON_HTTP_STATUS,
    REASON_UNPARSEABLE,
    REASON_MISSING_ANSWERS,
    REASON_MISSING_QUESTION,
    REASON_MISSING_CHOICE,
    REASON_CHOICE_NOT_OFFERED,
    REASON_FIRST_RESPONSE_SHAPE,
)


class JevTransportError(Exception):
    """Anything that stopped a call from producing a parsed body."""


class JevHttpError(JevTransportError):
    """A non-200 status.

    `body` is whatever could be parsed out of the error payload, or None. §7-D
    states the error body's JSON schema is not documented, so no caller reads a
    field out of it — the status code is the only thing relied on.
    """

    def __init__(self, status: int, body: object = None) -> None:
        super().__init__("HTTP %s" % status)
        self.status = status
        self.body = body


class JevNetworkLocked(RuntimeError):
    """Raised when live network use is attempted without the explicit unlock."""


@dataclass
class JevResponse:
    """A parsed body plus the response headers it arrived with.

    Headers matter because §7-D puts the request identifier in
    `x-typesafe-request-id` rather than in the body, and says it may be absent.
    """

    body: object
    headers: Dict[str, str] = field(default_factory=dict)


class Transport:
    """One method: take the request dict, return a `JevResponse`.

    Implementations signal a non-200 status by raising `JevHttpError`; any other
    failure raises `JevTransportError` or an ordinary exception. The selector
    treats every one of those the same way — DEFER, recorded, not retried.
    """

    name = "abstract"

    def send(self, request: Dict[str, object]) -> JevResponse:
        raise NotImplementedError


class FakeTransport(Transport):
    """Scripted responses. Every test in this repository uses this one.

    A scripted item is a `JevResponse` (returned as is), an exception instance
    (raised), or anything else (wrapped as a headerless `JevResponse`). Once the
    script runs out, the last item repeats if `repeat_last` is set, otherwise a
    `JevTransportError` is raised.
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

    def send(self, request: Dict[str, object]) -> JevResponse:
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
        if isinstance(item, JevResponse):
            return item
        return JevResponse(body=item)


class HttpTransport(Transport):
    """The only thing in this package that can open a socket.

    It cannot be constructed by accident: `enable_network=True` and a non-empty
    key must both be passed by the caller. It does not read the environment — the
    caller does, so that "this run may spend money" is written at the call site
    rather than hidden in a default.

    One send per call. No retry loop exists here, and urllib has none of its
    own; that is why §7-D chose dependency-free HTTP over the official SDK,
    whose default is to retry.
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
                "HttpTransport requires enable_network=True; live JEV calls on the "
                "TypeSafe direct path are locked by eval/v1/CONTRACT.md §7-D"
            )
        if not isinstance(api_key, str) or not api_key.strip():
            raise JevNetworkLocked(
                "HttpTransport requires a non-empty api_key passed by the caller; "
                "it does not read the environment itself"
            )
        self._api_key = api_key
        self._endpoint = endpoint
        self._timeout = timeout

    def send(self, request: Dict[str, object]) -> JevResponse:
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
                headers = _headers_as_dict(getattr(response, "headers", None))
        except urllib.error.HTTPError as exc:  # non-2xx carries a body
            raise JevHttpError(exc.code, _loads_or_none(exc.read()))
        except Exception as exc:
            raise JevTransportError("%s: %s" % (type(exc).__name__, exc))
        if status != 200:
            raise JevHttpError(int(status), _loads_or_none(raw))
        parsed = _loads_or_none(raw)
        if not isinstance(parsed, dict):
            raise JevTransportError("response body is not a JSON object")
        return JevResponse(body=parsed, headers=headers)


def _loads_or_none(raw: object) -> object:
    """Never raises. §7-D: the error body's schema is undocumented, so a body
    that will not parse must degrade to None rather than blow up a call."""
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        if not isinstance(raw, str):
            return None
        return json.loads(raw)
    except Exception:
        return None


def _headers_as_dict(headers: object) -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        items = headers.items()  # type: ignore[union-attr]
    except Exception:
        return out
    try:
        for key, value in items:
            out[str(key)] = str(value)
    except Exception:
        return out
    return out


def header_value(headers: object, name: str) -> Optional[str]:
    """Case-insensitive header lookup that never raises and may return None."""
    wanted = name.lower()
    try:
        items = headers.items()  # type: ignore[union-attr]
    except Exception:
        return None
    try:
        for key, value in items:
            if str(key).lower() == wanted:
                text = str(value).strip()
                return text or None
    except Exception:
        return None
    return None


def _int_or_none(value: object) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


def _float_or_none(value: object) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def estimate_cost_usd(
    input_tokens: Optional[int], output_tokens: Optional[int] = None
) -> Optional[float]:
    """Local ESTIMATE, not a billed amount.

    §7-D: the response body has no cost field, so this is derived from the
    published rate. Output tokens are free at that rate and are accepted only so
    the arithmetic is visible rather than hidden.
    """
    if input_tokens is None:
        return None
    total = input_tokens * INPUT_USD_PER_MILLION_TOKENS / 1_000_000.0
    if output_tokens is not None:
        total += output_tokens * OUTPUT_USD_PER_MILLION_TOKENS / 1_000_000.0
    return total


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


class RunGuard:
    """The run-wide stop flag (§7-E), shared exactly like `CallBudget`.

    It is deliberately NOT the Controller's fail-closed path. Both hold at once:
    the provider returns DEFER and raises nothing into the Controller, and the
    run stops. The provider raises this flag; the scorer lowers the curtain
    between cases. Nothing in `bia/controller.py` knows this exists.

    `first_response_checked` records that one completed call has already been
    verified against the documented shape, so the strict check costs one call
    and is not repeated.
    """

    def __init__(self) -> None:
        self.halted = False
        self.halt_reason = ""
        self.first_response_checked = False

    def halt(self, reason: str) -> None:
        """Idempotent: the FIRST reason is kept, because that is the failure the
        run must be reported against. Later ones cannot overwrite it."""
        if self.halted:
            return
        self.halted = True
        self.halt_reason = reason

    def as_dict(self) -> Dict[str, object]:
        return {
            "halted": self.halted,
            "halt_reason": self.halt_reason,
            "first_response_checked": self.first_response_checked,
        }


@dataclass
class JevCallRecord:
    """One decision's worth of telemetry.

    `confidence` and `probabilities` are reporting only. `estimated_cost_usd` is
    a local estimate, never a billed figure. `response_model` is the version the
    API says answered, which is not assumed to equal the version we pinned.
    """

    question_key: str
    offered_options: List[str]
    called: bool
    choice: Optional[str] = None
    returned: str = DEFER
    response_model: Optional[str] = None
    confidence: Optional[float] = None
    probabilities: Dict[str, float] = field(default_factory=dict)
    usage: Dict[str, object] = field(default_factory=dict)
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    estimated_cost_usd: Optional[float] = None
    request_id: Optional[str] = None
    failure_reason: Optional[str] = None

    def as_dict(self) -> Dict[str, object]:
        return {
            "question_key": self.question_key,
            "offered_options": list(self.offered_options),
            "called": self.called,
            "choice": self.choice,
            "returned": self.returned,
            "response_model": self.response_model,
            "confidence": self.confidence,
            "probabilities": dict(self.probabilities),
            "usage": dict(self.usage),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
            "request_id": self.request_id,
            "failure_reason": self.failure_reason,
        }


def state_payload(state: Dict[str, object]) -> Dict[str, object]:
    """The small, deterministic blob we are willing to send.

    Derived from the Controller's snapshot. It carries comparison windows,
    period completeness, totals and delta, the top contributing groups, which
    candidates were already investigated, coverage so far and the remaining
    budget. It carries no ticket id and no ticket text: the model chooses which
    group to look at, and never reads the evidence itself.

    The key set is pre-registered in §7-C and explicitly unchanged by §7-D.
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


def describe_candidate(candidate: EvidenceCandidate) -> Dict[str, str]:
    """Server-written option text, composed only from server-computed fields.

    The option key is an opaque id, so the description carries the whole meaning
    of the option — never `null`, and never thinner than the neighbouring ones.
    """
    what = _KIND_WHAT.get(candidate.kind, _KIND_WHAT["cell"]) % (
        candidate.label,
        candidate.group_delta,
        candidate.available_tickets,
        candidate.server_reason,
    )
    return {"what": what, "not_for": _KIND_NOT_FOR.get(candidate.kind, _KIND_NOT_FOR["cell"])}


def order_candidates(
    candidates: Sequence[EvidenceCandidate],
) -> List[EvidenceCandidate]:
    """Narrow options first, then the broader ones that contain them."""
    ordered: List[EvidenceCandidate] = []
    for kind in OPTION_KIND_ORDER:
        ordered.extend(c for c in candidates if c.kind == kind)
    ordered.extend(c for c in candidates if c.kind not in OPTION_KIND_ORDER)
    return ordered


def build_criteria(candidates: Sequence[EvidenceCandidate]) -> Dict[str, object]:
    """The options ARE these keys. A `choice` question has no `options` field.

    Insertion order is the order the model is asked in, so it is fixed here and
    the request body is serialized without key sorting.
    """
    criteria: Dict[str, object] = {}
    for candidate in order_candidates(candidates):
        criteria[candidate.candidate_id] = describe_candidate(candidate)
    criteria[DEFER] = DEFER_OPTION_DESCRIPTION
    return criteria


def first_response_shape_error(body: object, question_key: str) -> Optional[str]:
    """Name the first documented field the body gets wrong, or None (§7-E).

    The documented shape is `model`, `answers[<question key>]`, that answer's
    `choice`, and `usage.input_tokens`. The returned string is the field's path
    so the halt reason says exactly what was wrong with the one response we are
    ever going to see before spending the rest of the budget.
    """
    if not isinstance(body, dict):
        return "body"
    model = body.get("model")
    if not isinstance(model, str) or not model.strip():
        return "model"
    answers = body.get("answers")
    if not isinstance(answers, dict):
        return "answers"
    answer = answers.get(question_key)
    if not isinstance(answer, dict):
        return "answers.%s" % question_key
    if not isinstance(answer.get("choice"), str):
        return "answers.%s.choice" % question_key
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return "usage"
    if _int_or_none(usage.get("input_tokens")) is None:
        return "usage.input_tokens"
    return None


class JevSelector(DecisionProvider):
    """DecisionProvider backed by the JEV evaluation endpoint.

    The transport is injected and there is no network default. Confidence and
    probabilities coming back from the model are stored on the call record and
    have no effect on the returned value — the return value is `choice`,
    validated against the offered options, or DEFER.
    """

    name = "jev"

    def __init__(
        self,
        transport: Transport,
        budget: Optional[CallBudget] = None,
        model: str = JEV_MODEL,
        question_key: str = QUESTION_KEY,
        guard: Optional["RunGuard"] = None,
    ) -> None:
        if transport is None:
            raise ValueError("JevSelector requires an explicit transport")
        if model in JEV_MODEL_ALIASES:
            raise ValueError(
                "JEV model must be a pinned version, not the moving alias %r "
                "(eval/v1/CONTRACT.md §7-D 모델 버전 고정)" % model
            )
        self.transport = transport
        self.budget = budget if budget is not None else CallBudget()
        self.guard = guard if guard is not None else RunGuard()
        self.model = model
        self.question_key = question_key
        self.records: List[JevCallRecord] = []

    # -- telemetry ---------------------------------------------------------

    @property
    def call_count(self) -> int:
        return sum(1 for r in self.records if r.called)

    @property
    def total_estimated_cost_usd(self) -> float:
        """Sum of the per-call ESTIMATES. Not a billed amount — see COST_BASIS."""
        total = 0.0
        for record in self.records:
            if record.estimated_cost_usd is not None:
                total += record.estimated_cost_usd
        return total

    def telemetry(self) -> Dict[str, object]:
        return {
            "model_requested": self.model,
            "models_answered": sorted(
                {r.response_model for r in self.records if r.response_model}
            ),
            "calls_made": self.call_count,
            "decisions": len(self.records),
            "total_estimated_cost_usd": self.total_estimated_cost_usd,
            "cost_is_estimate": True,
            "cost_basis": COST_BASIS,
            "budget": self.budget.as_dict(),
            "run_guard": self.guard.as_dict(),
            "halted": self.guard.halted,
            "halt_reason": self.guard.halt_reason,
            "records": [r.as_dict() for r in self.records],
        }

    # -- request -----------------------------------------------------------

    def build_request(
        self, state: Dict[str, object], candidates: Sequence[EvidenceCandidate]
    ) -> Dict[str, object]:
        """Exactly the three documented top-level fields, and nothing else."""
        return {
            "state": serialize_state(state),
            "model": self.model,
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

        # §7-E: once the run is halted no further call is made and no further
        # budget is spent. This is checked before the budget so that a halted
        # run cannot consume a call it will never send.
        if self.guard.halted:
            self._record(
                options,
                called=False,
                reason="%s:%s" % (REASON_RUN_HALTED, self.guard.halt_reason),
            )
            return DEFER

        if not candidates:
            self._record(options, called=False, reason=REASON_NO_CANDIDATES)
            return DEFER

        if len(options) > MAX_CHOICE_OPTIONS:
            self._record(options, called=False, reason=REASON_TOO_MANY_OPTIONS)
            return DEFER

        if not self.budget.try_consume():
            self._record(options, called=False, reason=REASON_BUDGET_EXHAUSTED)
            return DEFER

        request = self.build_request(state, candidates)
        try:
            response = self.transport.send(request)
        except JevHttpError as exc:
            # 401 / 422 / 429 / 529 and every other non-200 are one failure with
            # one recorded status. The body's schema is undocumented, so nothing
            # is read out of it. Not retried.
            self._record(
                options, called=True, reason="%s:%s" % (REASON_HTTP_STATUS, exc.status)
            )
            return DEFER
        except Exception as exc:  # no retry, ever
            self._record(
                options,
                called=True,
                reason="%s:%s" % (REASON_TRANSPORT_ERROR, type(exc).__name__),
            )
            return DEFER

        return self._interpret(response, options)

    def _interpret(self, response: object, options: Sequence[str]) -> str:
        if isinstance(response, JevResponse):
            body = response.body
            headers = response.headers
        else:  # a transport that ignored the contract is a failure, not a crash
            body = response
            headers = {}

        request_id = header_value(headers, REQUEST_ID_HEADER)

        if not isinstance(body, dict):
            self._record(
                options,
                called=True,
                reason=REASON_UNPARSEABLE,
                request_id=request_id,
            )
            return DEFER

        response_model = body.get("model")
        response_model = response_model if isinstance(response_model, str) else None
        usage = body.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        input_tokens = _int_or_none(usage.get("input_tokens"))
        output_tokens = _int_or_none(usage.get("output_tokens"))

        envelope = {
            "response_model": response_model,
            "usage": usage,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "estimated_cost_usd": estimate_cost_usd(input_tokens, output_tokens),
            "request_id": request_id,
        }

        answers = body.get("answers")
        if not isinstance(answers, dict):
            self._record(options, called=True, reason=REASON_MISSING_ANSWERS, **envelope)
            return DEFER

        answer = answers.get(self.question_key)
        if not isinstance(answer, dict):
            self._record(options, called=True, reason=REASON_MISSING_QUESTION, **envelope)
            return DEFER

        choice = answer.get("choice")
        probabilities = answer.get("probabilities")
        probabilities = probabilities if isinstance(probabilities, dict) else {}

        common = dict(envelope)
        common["probabilities"] = probabilities
        common["confidence"] = _float_or_none(answer.get("confidence"))

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

        # §7-E first-response shape verification. The call above already proved
        # `answers`, the question key and `choice`; what remains of the
        # documented shape is the top-level `model` and `usage.input_tokens`.
        # The check is spent on the first completed call of the run — §7-E (A)
        # keeps the probe inside the 16 rather than adding a 17th call.
        if not self.guard.first_response_checked:
            self.guard.first_response_checked = True
            bad_field = first_response_shape_error(body, self.question_key)
            if bad_field is not None:
                self._record(
                    options,
                    called=True,
                    choice=choice,
                    reason="%s:%s" % (REASON_FIRST_RESPONSE_SHAPE, bad_field),
                    **common
                )
                return DEFER

        # The only line that decides the return value. Confidence and
        # probabilities were parsed above and are stored; they are deliberately
        # not consulted here.
        self._record(options, called=True, choice=choice, returned=choice, **common)
        return choice

    def _record(
        self,
        options: Sequence[str],
        called: bool,
        choice: Optional[str] = None,
        returned: str = DEFER,
        reason: Optional[str] = None,
        response_model: Optional[str] = None,
        confidence: Optional[float] = None,
        probabilities: Optional[Dict[str, float]] = None,
        usage: Optional[Dict[str, object]] = None,
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
        estimated_cost_usd: Optional[float] = None,
        request_id: Optional[str] = None,
    ) -> None:
        # Every call failure halts the run, not only the first one (§7-E). The
        # halt is raised here, at the single place every failure is written
        # down, so a new failure path cannot be added that records a failure and
        # forgets to stop the run.
        if reason is not None and reason.split(":", 1)[0] in HALTING_REASONS:
            self.guard.halt(reason)
        self.records.append(
            JevCallRecord(
                question_key=self.question_key,
                offered_options=list(options),
                called=called,
                choice=choice,
                returned=returned,
                response_model=response_model,
                confidence=confidence,
                probabilities=dict(probabilities or {}),
                usage=dict(usage or {}),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost_usd=estimated_cost_usd,
                request_id=request_id,
                failure_reason=reason,
            )
        )
