from __future__ import annotations

import dataclasses
import datetime as dt
import inspect
import unittest

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


if __name__ == "__main__":
    unittest.main()
