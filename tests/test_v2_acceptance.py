"""v2 최종 acceptance. 개별 기능이 아니라 **주장 자체**를 지키는 게이트다.

여기서 고정하는 주장 셋:

1. 실행 계층은 도메인을 모른다 — 도메인 차이는 전부 `DomainSpec` 데이터다.
2. 사전등록 상수는 불변이고, oracle 의 복사본이 production 과 같은 값이다.
3. v1 의 사전등록 임계값도 그대로다.

**`eval/v1/run_eval.py` 의 `V-2_floor` 는 FAIL 이다. 고장이 아니다.**
좁은 heuristic baseline 이 v1 챌린지 세트의 사전 등록된 0.40 yield floor 를
넘지 못한다는 **v1 실험의 기록된 결과**이고, `v1.0.0` 태그에서도 동일하게 FAIL 이다
(`eval/v1/CONTRACT.md` 개정 3·2026-09-21 관측 기록 참조). acceptance 의 게이트는
"eval 이 PASS 라고 말하는 것" 이 아니라 **"결과 산출물이 바뀌지 않는 것"** 이다.
FAIL 을 없애려고 floor 를 낮추거나 기준을 손대면 이 프로젝트의 핵심 규율
— 기준은 측정 전에 정하고 결과에 맞춰 바꾸지 않는다 — 을 깨는 것이다.
아래 `PreRegisteredThresholdTests` 가 그 숫자를 기계적으로 붙잡아 둔다.
"""
from __future__ import annotations

import ast
import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from bia.analysis import decompose, operators  # noqa: E402
from bia.v2bench import generate  # noqa: E402
from eval.v1 import run_eval as v1_scorer  # noqa: E402


class DomainLeakTests(unittest.TestCase):
    def test_the_engine_never_names_a_domain(self):
        """도메인 차이는 전부 DomainSpec 데이터로 표현된다.

        문자열 grep 이 아니라 AST 로 본다 — docstring 이 도메인을 언급할 수 있고,
        그것은 누수가 아니다. 누수는 코드가 도메인 이름을 **값으로** 드는 것이다.
        """
        names = {"complaints", "ecommerce", "support_ops"}
        offenders = []
        package = os.path.join(REPO_ROOT, "bia", "analysis")
        for filename in sorted(os.listdir(package)):
            if not filename.endswith(".py"):
                continue
            path = os.path.join(package, filename)
            with open(path, encoding="utf-8") as handle:
                tree = ast.parse(handle.read(), filename=filename)
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if node.value in names:
                        offenders.append((filename, node.value))
        self.assertEqual(offenders, [])

    def test_the_check_would_catch_a_real_leak(self):
        # 위 검사가 공허하지 않음을 보인다 — 도메인 이름을 값으로 든 코드를
        # 실제로 파싱해 잡히는지 확인한다.
        names = {"complaints", "ecommerce", "support_ops"}
        tree = ast.parse('def f(spec):\n    return spec.name == "ecommerce"\n')
        found = [n.value for n in ast.walk(tree)
                 if isinstance(n, ast.Constant) and n.value in names]
        self.assertEqual(found, ["ecommerce"])


class PreRegisteredThresholdTests(unittest.TestCase):
    """사전등록 상수는 불변이다. 결과를 본 뒤 옮기지 않는다."""

    def test_the_v2_constants_have_their_registered_values(self):
        self.assertEqual(decompose.CANCELLATION_THRESHOLD, 0.20)
        self.assertEqual(decompose.SHARE_EPSILON, 0.0001)
        self.assertEqual(decompose.FLOAT_TOL, 1e-9)
        self.assertEqual(decompose.GROSS_EPSILON, 1e-12)
        self.assertEqual(operators.CROSS_CELL_LIMIT, 1000)

    def test_the_oracle_copies_agree_with_production(self):
        # oracle 은 상수를 import 하지 않고 복사한다. 복사본이 갈라지면
        # 벤치마크가 조용히 다른 계약을 검사하게 된다.
        self.assertEqual(float(generate.CANCELLATION_THRESHOLD),
                         decompose.CANCELLATION_THRESHOLD)
        self.assertEqual(float(generate.SHARE_EPSILON), decompose.SHARE_EPSILON)
        self.assertEqual(float(generate.FLOAT_TOL), decompose.FLOAT_TOL)
        self.assertEqual(float(generate.GROSS_EPSILON), decompose.GROSS_EPSILON)
        self.assertEqual(generate.CROSS_CELL_LIMIT, operators.CROSS_CELL_LIMIT)

    def test_the_v1_yield_floor_is_still_the_registered_number(self):
        """모듈 docstring 참조. `V-2_floor FAIL` 은 기록된 결과다.

        이 숫자를 낮추면 FAIL 이 사라지지만, 그것은 결과에 맞춰 합격선을 옮기는
        것이다. `eval/v1/CONTRACT.md` 가 "수치 0.40은 바뀌지 않는다" 고 적었고
        여기서 기계적으로 붙잡는다.
        """
        self.assertEqual(v1_scorer.MIN_MACRO_YIELD, 0.40)


if __name__ == "__main__":
    unittest.main()
