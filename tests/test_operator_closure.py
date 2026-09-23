"""닫힌 6 연산자를 기계적으로 고정한다.

`operators.py` 와 README 는 "여섯 개로 닫힌 연산자" 를 주장하는데, 열거도 강제도
없으면 일곱 번째가 들어와도 아무 테스트가 깨지지 않는다. 사람 기억에 의존해야
지켜지는 규칙은 규칙이 아니다.

여기의 목록이 유일한 정본이다. 연산자를 더하거나 빼려면, 혹은 분석 계층에 새 공개
함수를 넣으려면 이 파일을 함께 고쳐야 한다 — 그때가 "이것이 일곱 번째 연산자인가"
를 묻는 자리다.
"""
from __future__ import annotations

import ast
import os
import unittest

from bia.analysis import decompose, engine, operators

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MODULES = {
    "bia/analysis/operators.py": operators,
    "bia/analysis/decompose.py": decompose,
    "bia/analysis/engine.py": engine,
}

# 여섯 연산자와 **각각이 어디에 구현돼 있는지**. 한 연산자가 여러 자리에 걸치면
# 전부 적는다 — "어디에 있는가" 를 흐리면 열거가 장식이 된다.
OPERATORS = {
    "AGGREGATE": (("bia/analysis/operators.py", "aggregate"),),
    "COMPARE": (("bia/analysis/operators.py", "compare"),),
    "BREAKDOWN": (("bia/analysis/operators.py", "group_universe"),
                  ("bia/analysis/engine.py", "_run_branch")),
    "CONTRIBUTION": (("bia/analysis/decompose.py", "decompose_ratio"),
                     ("bia/analysis/engine.py", "_additive_branch")),
    "RATE": (("bia/analysis/decompose.py", "decompose_ratio"),),
    "RANK": (("bia/analysis/operators.py", "order_groups"),
             ("bia/analysis/operators.py", "rank")),
}

# 연산자가 아닌 함수. 순수 보조(정렬 키·경계 스냅·부호·불변식 검사)이거나 실행
# 진입점이며, 연산자로 세지 않는다. 비공개 함수도 적는다 — `_` 하나로 목록을
# 빠져나갈 수 있으면 일곱 번째는 그 이름으로 들어온다.
NON_OPERATOR_FUNCTIONS = {
    "bia/analysis/operators.py": frozenset(("by_dimension_order",
                                            "_declared_tie_key")),
    "bia/analysis/decompose.py": frozenset(("sign_with_tol", "snap_share_boundary",
                                            "_assert_invariants")),
    "bia/analysis/engine.py": frozenset(("run_plan", "_ratio_branch")),
}


def _module_level_functions(path):
    with open(os.path.join(REPO_ROOT, path), encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=path)
    return set(node.name for node in tree.body
               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)))


class ClosedOperatorSetTests(unittest.TestCase):
    def test_there_are_exactly_six_operators(self):
        self.assertEqual(len(OPERATORS), 6, sorted(OPERATORS))
        self.assertEqual(
            sorted(OPERATORS),
            ["AGGREGATE", "BREAKDOWN", "COMPARE", "CONTRIBUTION", "RANK", "RATE"])

    def test_every_operator_resolves_to_a_callable_where_it_is_declared(self):
        for name, sites in sorted(OPERATORS.items()):
            for path, attribute in sites:
                with self.subTest(operator=name, site="%s:%s" % (path, attribute)):
                    self.assertIn(path, MODULES)
                    self.assertIn(attribute, _module_level_functions(path),
                                  "%s 가 %s 에 없다" % (attribute, path))
                    self.assertTrue(callable(getattr(MODULES[path], attribute)))

    def test_no_function_in_the_analysis_core_escapes_the_inventory(self):
        """일곱 번째가 조용히 들어오는 자리를 막는다.

        분석 핵심 세 모듈의 모듈 수준 함수는 **비공개까지 전부** (a) 여섯 중 하나의
        구현이거나 (b) 연산자가 아니라고 명시된 보조여야 한다. 새 함수를 넣으면
        여기서 분류를 요구받는다.
        """
        declared = {}
        for name, sites in OPERATORS.items():
            for path, attribute in sites:
                declared.setdefault(path, set()).add(attribute)
        for path in sorted(MODULES):
            with self.subTest(module=path):
                found = _module_level_functions(path)
                classified = declared.get(path, set()) | NON_OPERATOR_FUNCTIONS[path]
                self.assertEqual(
                    found - classified, set(),
                    "%s 에 목록에 없는 함수가 있다. 일곱 번째 연산자인지 먼저 답해라."
                    % path)
                self.assertEqual(classified - found, set(),
                                 "%s 의 목록이 실제 코드보다 앞서 있다" % path)


if __name__ == "__main__":
    unittest.main()
