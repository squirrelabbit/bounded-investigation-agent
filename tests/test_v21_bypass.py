from __future__ import annotations

import dataclasses
import datetime as dt
import inspect
import unittest

from bia.analysis.compiler import ExecutionPlan, PlanBranch
from bia.analysis.engine import run_plan
from bia.analysis.frame import Frame, Observation
from bia.analysis.qualification import ACTION_ALIGN, ExecutableQualification, RejectedQualification, qualify
from tests.test_qualification import ALIGN, CUR_START, STRICT, _frame, _obs, _plan


class BypassTests(unittest.TestCase):
    def test_1_no_qualification_no_execution(self):
        self.assertEqual(list(inspect.signature(run_plan).parameters), ["executable"])
        with self.assertRaises(TypeError):
            run_plan(_plan(STRICT))

    def test_2_rejected_cannot_run(self):
        q = qualify(_plan(STRICT), _frame(range(30), range(28)))
        self.assertIsInstance(q, RejectedQualification)
        with self.assertRaises(TypeError):
            run_plan(q)

    def test_3_accept_runs(self):
        result = run_plan(qualify(_plan(STRICT), _frame(range(30), range(30))))
        self.assertEqual(result.comparison["current"], 30)

    def test_4_align_runs_on_effective_windows(self):
        q = qualify(_plan(ALIGN), _frame(range(30), range(2, 30)))
        self.assertEqual(q.action, ACTION_ALIGN)
        result = run_plan(q)
        # 유효 구간은 오프셋 2..29 (28일). 기준 구간의 오프셋 0·1 행은 세지 않는다.
        self.assertEqual((result.comparison["current"], result.comparison["baseline"]), (28, 28))

    def test_5_frame_cannot_be_swapped(self):
        q = qualify(_plan(STRICT), _frame(range(30), range(30)))
        with self.assertRaises(AttributeError):
            q.frame = Frame.of([])

    def test_6_plan_cannot_be_swapped(self):
        q = qualify(_plan(STRICT), _frame(range(30), range(30)))
        with self.assertRaises(AttributeError):
            q.plan = _plan(ALIGN)

    def test_7_inner_state_cannot_be_mutated(self):
        q = qualify(_plan(STRICT), _frame(range(30), range(30)))
        self.assertIsInstance(q.frame.rows, tuple)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            q.plan.rank_by = "x"
        with self.assertRaises(TypeError):
            q.plan.domain.metrics["x"] = None
        with self.assertRaises(TypeError):
            ExecutableQualification(object(), q.plan, q.frame, q.effective, q.action, q.facts, q.detail)


class SealedDataMutationTests(unittest.TestCase):
    """판정을 통과한 데이터는 호출자가 쥔 원본 list 를 바꿔도 실행 결과가 바뀌지 않는다."""

    def test_frame_rows_list_is_copied_into_a_tuple(self):
        rows = list(_frame(range(30), range(30)).rows)
        q = qualify(_plan(STRICT), Frame(rows=rows))
        self.assertIsInstance(q, ExecutableQualification)
        self.assertIsInstance(q.frame.rows, tuple)
        before = run_plan(q).as_dict()
        rows.append(_obs(CUR_START, v=99))
        rows.extend(_obs(CUR_START + dt.timedelta(i), g="late", v=7) for i in range(30))
        self.assertEqual(run_plan(q).as_dict(), before)

    def test_observation_sequences_become_tuples(self):
        obs = Observation(day=CUR_START, keys=[("g", "a")], measures=[("v", 1)], null_dimensions=["g"])
        self.assertIsInstance(obs.keys, tuple)
        self.assertIsInstance(obs.measures, tuple)
        self.assertIsInstance(obs.null_dimensions, tuple)
        self.assertEqual(obs, _obs(CUR_START))
        self.assertEqual(hash(obs), hash(_obs(CUR_START)))

    def test_frame_rejects_a_non_observation_row(self):
        with self.assertRaises(TypeError):
            Frame(rows=[_obs(CUR_START), ("2026-07-01", "a", 1)])
        with self.assertRaises(TypeError):
            Frame.of([None])

    def test_hand_built_plan_branches_become_a_tuple(self):
        compiled = _plan(STRICT)
        branches = list(compiled.branches)
        plan = ExecutionPlan(domain=compiled.domain, metric=compiled.metric,
                             comparison=compiled.comparison, branches=branches,
                             rank_by=compiled.rank_by)
        self.assertIsInstance(plan.branches, tuple)
        branches.append(PlanBranch(dimensions=("g",), cross=True))
        self.assertEqual(plan.branches, compiled.branches)


if __name__ == "__main__":
    unittest.main()
