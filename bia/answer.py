"""Answer rendering, split into confirmed facts / associated evidence / not established.

Two machine-checkable guards live here:

1. every number in the rendered text must have been registered from a computed
   value (no number may appear by way of a prose template), and
2. no causal vocabulary may appear anywhere in the answer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from .evidence import EvidenceState
from .integrity import MODE_ALIGNED_WINDOW, MODE_BLOCKED
from .lexicon import label_ko

MAX_EXCERPTS = 5

HEADING_CONFIRMED = "## 확인된 사실"
HEADING_EVIDENCE = "## 관련 근거"
HEADING_UNKNOWN = "## 아직 확인되지 않은 것"

FORBIDDEN_CAUSAL_TERMS = (
    "원인",
    "때문",
    "유발",
    "초래",
    "야기",
    "탓",
    "caused",
    "because",
    "due to",
    "root cause",
    "driven by",
    "led to",
    "resulted in",
    "responsible for",
    "비롯",
    "기인",
    "stems from",
    "triggered by",
    "brought about",
)

QUOTE_OPEN = "\x02"
QUOTE_CLOSE = "\x03"
_QUOTE_SPAN = re.compile(QUOTE_OPEN + "(.*?)" + QUOTE_CLOSE, re.S)

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


def numeric_tokens(text: str) -> Set[str]:
    return set(_NUMBER_RE.findall(text))


def causal_terms_in(text: str) -> List[str]:
    lowered = text.lower()
    return [term for term in FORBIDDEN_CAUSAL_TERMS if term in lowered]


class UnsupportedNumberError(RuntimeError):
    """A number reached the answer without coming from a computed value."""


class CausalClaimError(RuntimeError):
    """Causal vocabulary reached the answer."""


@dataclass
class AnswerDocument:
    status: str
    confirmed: List[str] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)
    unknown: List[str] = field(default_factory=list)
    allowed_numbers: Set[str] = field(default_factory=set)
    quotes: List[str] = field(default_factory=list)
    payload: Dict[str, object] = field(default_factory=dict)

    def num(self, value) -> str:
        """Register a computed value for use in the text, and return it as text."""
        rendered = value if isinstance(value, str) else str(value)
        self.allowed_numbers.update(numeric_tokens(rendered))
        return rendered

    def quote(self, text: str) -> str:
        """Verbatim customer text. Both guards apply to the system's own claims,
        not to what a customer wrote, so quoted spans are excluded from them.

        The span is delimited rather than matched back by substring: with
        `str.replace`, one customer writing "late" would strip that word out of
        every other quote, and the surviving digits of a longer quote would then
        be read as an unsourced number and abort the run.
        """
        self.quotes.append(text)
        return QUOTE_OPEN + text + QUOTE_CLOSE

    def _raw_render(self) -> str:
        parts = [HEADING_CONFIRMED]
        parts.extend("- " + line for line in self.confirmed)
        parts.append("")
        parts.append(HEADING_EVIDENCE)
        parts.extend("- " + line for line in self.evidence)
        parts.append("")
        parts.append(HEADING_UNKNOWN)
        parts.extend("- " + line for line in self.unknown)
        return "\n".join(parts) + "\n"

    def claim_text(self) -> str:
        """The rendered answer with quoted customer text replaced."""
        return _QUOTE_SPAN.sub("\u2026", self._raw_render())

    def render(self) -> str:
        return self._raw_render().replace(QUOTE_OPEN, "").replace(QUOTE_CLOSE, "")

    def check(self) -> None:
        text = self.claim_text()
        stray = numeric_tokens(text) - self.allowed_numbers
        if stray:
            raise UnsupportedNumberError("unregistered numbers in answer: %s" % sorted(stray))
        found = causal_terms_in(text)
        if found:
            raise CausalClaimError("causal vocabulary in answer: %s" % found)

    def as_dict(self) -> Dict[str, object]:
        return {
            "status": self.status,
            "confirmed_facts": list(self.confirmed),
            "associated_evidence": list(self.evidence),
            "not_established": list(self.unknown),
            "text": self.render(),
            "claim_text": self.claim_text(),
        }


def build_answer(state: EvidenceState) -> AnswerDocument:
    blocked = state.comparability.mode == MODE_BLOCKED
    doc = AnswerDocument(status="abstained" if blocked else "reported")

    _write_integrity_facts(doc, state)
    if blocked:
        _write_blocked(doc, state)
        doc.check()
        return doc

    _write_metric_facts(doc, state)
    _write_evidence(doc, state)
    _write_unknowns(doc, state)
    doc.check()
    return doc


def _write_integrity_facts(doc: AnswerDocument, state: EvidenceState) -> None:
    cur = state.current_integrity
    base = state.baseline_integrity
    doc.confirmed.append(
        "비교 대상 기간: 현재 %s (일자 %s/%s 수신), 기준 %s (일자 %s/%s 수신)"
        % (
            doc.num(str(cur.period)),
            doc.num(cur.observed_days),
            doc.num(cur.expected_days),
            doc.num(str(base.period)),
            doc.num(base.observed_days),
            doc.num(base.expected_days),
        )
    )
    for label, integrity in (("현재", cur), ("기준", base)):
        if integrity.duplicate_rows_removed:
            doc.confirmed.append(
                "%s 기간에서 완전히 동일한 중복 행 %s건을 합산 전에 제거했다"
                % (label, doc.num(integrity.duplicate_rows_removed))
            )
        if integrity.missing_days:
            doc.confirmed.append(
                "%s 기간에 데이터가 없는 날이 %s일 있다 (%s)"
                % (
                    label,
                    doc.num(len(integrity.missing_days)),
                    doc.num(", ".join(integrity.missing_days[:5]))
                    + ("" if len(integrity.missing_days) <= 5 else " 외"),
                )
            )


def _write_blocked(doc: AnswerDocument, state: EvidenceState) -> None:
    doc.evidence.append("비교가 성립하지 않아 근거 조회를 실행하지 않았다")
    doc.unknown.append(
        "두 기간의 증감: 계산하지 않았다 — %s" % doc.num(state.comparability.reason)
    )
    doc.unknown.append(
        "증가한 그룹과 그 규모: 비교 가능한 구간이 확보되기 전에는 어떤 수치도 제시하지 않는다"
    )
    doc.unknown.append(
        "무엇이 이 변화를 만들었는지: 이 데이터로는 확인되지 않는다"
    )
    doc.unknown.append(
        "다음에 필요한 것: 누락된 일자의 원본 적재 또는 같은 키의 상충 행 정정"
    )


def _write_metric_facts(doc: AnswerDocument, state: EvidenceState) -> None:
    metrics = state.metrics
    assert metrics is not None
    if state.comparability.mode == MODE_ALIGNED_WINDOW:
        doc.confirmed.append(
            "두 기간의 수신 일자가 달라 전체 기간끼리 비교하지 않았다. "
            "양쪽에 모두 존재하는 구간 현재 %s 대 기준 %s 으로만 비교했다"
            % (doc.num(str(state.current_window)), doc.num(str(state.baseline_window)))
        )
    doc.confirmed.append(
        "비교 구간 불만 건수: 현재 %s건, 기준 %s건, 차이 %s건"
        % (
            doc.num(metrics.current_total),
            doc.num(metrics.baseline_total),
            doc.num("%+d" % metrics.delta),
        )
    )
    if metrics.pct_change is not None:
        doc.confirmed.append("기준 대비 변화율 %s%%" % doc.num("%+.2f" % metrics.pct_change))

    if not metrics.increased:
        doc.confirmed.append("이 비교 구간에서 불만 건수는 증가하지 않았다")
        return

    dimensions = (
        ("제품", state.top_products, metrics.by_product),
        ("불만 유형", state.top_complaint_types, metrics.by_complaint_type),
    )
    for dimension, groups, all_groups in dimensions:
        if not groups:
            continue
        rising_total = sum(g.delta for g in all_groups if g.delta > 0)
        rendered = ", ".join(
            "%s %s건 (늘어난 그룹 합계의 %s%%)"
            % (
                _display(group.value),
                doc.num("%+d" % group.delta),
                doc.num("%.0f" % (100 * group.share_of_increase)),
            )
            for group in groups
        )
        doc.confirmed.append("%s별로 증가분이 발생한 위치: %s" % (dimension, rendered))
        if rising_total != metrics.delta:
            doc.confirmed.append(
                "%s별로 늘어난 그룹의 합은 %s건이고 순증가는 %s건이다. "
                "차이는 같은 구간에 줄어든 그룹이 상쇄한 몫이므로, 위 비율은 순증가가 아니라 "
                "늘어난 그룹 합계를 기준으로 읽어야 한다"
                % (
                    dimension,
                    doc.num("%+d" % rising_total),
                    doc.num("%+d" % metrics.delta),
                )
            )


def _display(value: str) -> str:
    return label_ko(value) if "_" in value else value


def _write_evidence(doc: AnswerDocument, state: EvidenceState) -> None:
    metrics = state.metrics
    if metrics is not None and not metrics.increased:
        doc.evidence.append("증가가 없어 고객 문의 근거를 조회하지 않았다")
        return
    admitted = state.found_tickets
    if not admitted:
        doc.evidence.append("검증을 통과한 고객 문의 근거가 없다")
        for round_ in state.rounds:
            if round_.violation:
                doc.evidence.append("조사 요청이 거부되어 실행되지 않았다: %s" % round_.violation)
            elif round_.selection_raw == "DEFER":
                doc.evidence.append("조사 가능한 후보가 근거 기준을 충족하지 못해 보류했다")
        return

    doc.evidence.append(
        "아래는 같은 구간·같은 그룹에서 관측된 고객 문의다. 증가와 함께 나타난 내용이며, 증가를 설명하는 근거로 확정된 것이 아니다"
    )
    for round_ in state.rounds:
        if not round_.admitted:
            continue
        doc.evidence.append(
            "조사 대상 [%s]: 검증 통과 %s건 / 해당 구간 문의 %s건 (coverage %s)"
            % (
                round_.candidate_label,
                doc.num(len(round_.admitted)),
                doc.num(round_.pool_size),
                doc.num("%.2f" % round_.coverage),
            )
        )
        if round_.rejected:
            reasons: Dict[str, int] = {}
            for item in round_.rejected:
                reasons[item["reason"]] = reasons.get(item["reason"], 0) + 1
            doc.evidence.append(
                "  검증에서 제외된 문의 %s건: %s"
                % (
                    doc.num(len(round_.rejected)),
                    ", ".join(
                        "%s %s건" % (doc.num(reason), doc.num(count))
                        for reason, count in sorted(reasons.items())
                    ),
                )
            )
        for item in round_.admitted[:MAX_EXCERPTS]:
            doc.evidence.append(
                "  %s (%s, %s, 출처 %s): %s"
                % (
                    doc.num(item.ticket_id),
                    doc.num(item.day),
                    _display(item.complaint_type),
                    item.source,
                    doc.quote(item.excerpt),
                )
            )
        if len(round_.admitted) > MAX_EXCERPTS:
            doc.evidence.append(
                "  외 %s건은 목록에만 있고 본문은 생략했다"
                % doc.num(len(round_.admitted) - MAX_EXCERPTS)
            )


def _write_unknowns(doc: AnswerDocument, state: EvidenceState) -> None:
    doc.unknown.append(
        "무엇이 이 증가를 만들었는지: 이 데이터로는 확인되지 않는다. "
        "위의 그룹별 수치는 증가분이 '어디서 발생했는지'이며 '왜 발생했는지'가 아니다"
    )
    if state.comparability.mode == MODE_ALIGNED_WINDOW:
        doc.unknown.append(
            "비교 구간 밖의 일자: 데이터가 한쪽에만 있어 비교에서 제외했다. 전체 기간 수치는 제시하지 않는다"
        )
    if state.metrics is not None and state.metrics.increased:
        if not state.found_tickets:
            doc.unknown.append("증가 그룹의 고객 문의 내용: 검증을 통과한 문의가 없어 확인하지 못했다")
        elif not state.evidence_sufficient:
            doc.unknown.append(
                "고객 문의 근거의 충분성: 최대 coverage %s로 기준에 미치지 못해 부분 관측으로만 남는다"
                % doc.num("%.2f" % state.coverage)
            )
    for round_ in state.rounds:
        if round_.truncated:
            doc.unknown.append(
                "조회 상한에 걸려 [%s] 구간 문의의 일부만 확인했다" % round_.candidate_label
            )
    uninvestigated = _uninvestigated(state)
    if uninvestigated:
        doc.unknown.append(
            "조사하지 않은 후보: %s — 이번 실행은 조사 호출 %s회, 조회 %s회를 쓰고 종료했다 (%s)"
            % (
                ", ".join(doc.num(c) for c in uninvestigated),
                doc.num(state.decision_calls),
                doc.num(state.retrievals),
                state.finish_reason,
            )
        )
    doc.unknown.append(
        "외부 변화(캠페인, 배포, 계절성 등)와의 관계: 이 시스템은 해당 데이터를 가지고 있지 않다"
    )


def _uninvestigated(state: EvidenceState) -> List[str]:
    labels = []
    for round_ in state.rounds:
        for candidate_id in round_.offered_candidate_ids:
            if candidate_id != round_.candidate_id and candidate_id not in labels:
                labels.append(candidate_id)
    return [c for c in labels if c not in state.investigated_candidates]
