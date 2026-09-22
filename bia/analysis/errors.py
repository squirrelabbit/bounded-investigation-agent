"""분석 계약 위반. 모두 호출자의 잘못이지 실행 중 오류가 아니다."""
from __future__ import annotations


class SpecError(ValueError):
    """도메인·metric 선언 자체가 잘못됐다."""


class RequestError(ValueError):
    """선언은 옳으나 이번 요청이 그 계약을 벗어났다."""


class AnalysisRefused(RuntimeError):
    """데이터가 이 분석을 지탱하지 못한다. 숫자를 내지 않는다."""

    def __init__(self, stage: str, reason: str) -> None:
        super().__init__("%s: %s" % (stage, reason))
        self.stage = stage
        self.reason = reason
