"""검사 스크립트의 공용 실패 표면.

`assert` 는 `python3 -O` 에서 통째로 사라진다. 이 스크립트들은 README 가 인용하는
증거 산출물이므로, 최적화 플래그 하나로 모든 가드가 빠진 채 "ALL CHECKS PASSED" 를
출력해서는 안 된다. 가드는 명시적 raise 로 쓴다.

`AssertionError` 를 상속하지 않는다 — 호출자의 `except AssertionError` 가 위반을
삼키면 조용한 통과라는 같은 자리로 돌아온다.
"""
from __future__ import annotations


class CheckFailed(Exception):
    """검사 스크립트가 잡아낸 위반. 결과를 내지 않고 비-0 으로 끝난다."""


def require(condition, message):
    if not condition:
        raise CheckFailed(message)
