from __future__ import annotations

import ast
import datetime as dt
import pathlib
import unittest

from bia.analysis.compiler import compile_request
from bia.analysis.engine import run_plan
from bia.analysis.frame import UNKNOWN, Frame, Observation
from bia.analysis.qualification import (
    ACTION_ACCEPT, ACTION_ALIGN, REASON_CONFLICTING_DUPLICATE, REASON_EMPTY_PERIOD,
    REASON_INCOMPLETE_PERIOD_COVERAGE, REASON_INSUFFICIENT_COMMON_WINDOW,
    REASON_MISSING_REQUIRED_VALUE, SCOPE_OUTSIDE, WINDOW_BELOW_MINIMUM, WINDOW_NO_OVERLAP,
    ExecutableQualification, RejectedQualification, qualify,
)
from bia.analysis.registry import register
from bia.analysis.request import AnalysisRequest, PeriodComparison
from bia.analysis.spec import DomainSpec, MetricSpec
from bia.types import Period

METRIC = {"m": MetricSpec(name="m", kind="additive", value="v")}
STRICT = DomainSpec(name="q_strict", grain=("day", "g"), dimensions=("g",), metrics=METRIC)
ALIGN = DomainSpec(name="q_align", grain=("day", "g"), dimensions=("g",), metrics=METRIC,
                   partial_period_policy="align_common_window",
                   min_comparable_days=7, min_comparable_ratio=0.5,
                   null_dimension_policy="unknown_group")
for _spec in (STRICT, ALIGN):
    register(_spec)

# 기준 구간은 31 일까지 늘려도 현재 구간(7/1~) 시작 전에 끝나야 한다 — 6/1 이면 31 일 기준이 7/1 에 끝나
# compile_request 가 거부한다.
BASE_START = dt.date(2026, 5, 1)
CUR_START = dt.date(2026, 7, 1)


def _plan(spec, base_days=30, cur_days=30):
    base = Period(BASE_START, BASE_START + dt.timedelta(base_days - 1))
    cur = Period(CUR_START, CUR_START + dt.timedelta(cur_days - 1))
    return compile_request(AnalysisRequest(
        domain=spec.name, metric="m", breakdowns=("g",),
        comparison=PeriodComparison(current=cur, baseline=base)))


def _obs(day, g="a", v=1, nulls=()):
    return Observation(day=day, keys=(("g", g),), measures=(("v", v),), null_dimensions=nulls)


def _days(start, offsets):
    return [start + dt.timedelta(o) for o in offsets]


def _frame(base_offsets, cur_offsets, extra=()):
    rows = [_obs(d) for d in _days(BASE_START, base_offsets)]
    rows += [_obs(d) for d in _days(CUR_START, cur_offsets)]
    return Frame.of(rows + list(extra))


class DecisionTableTests(unittest.TestCase):
    def test_complete_equal_periods_accept_with_requested_windows(self):
        plan = _plan(STRICT)
        q = qualify(plan, _frame(range(30), range(30)))
        self.assertIsInstance(q, ExecutableQualification)
        self.assertEqual(q.action, ACTION_ACCEPT)
        self.assertEqual(q.effective, plan.comparison)

    def test_incomplete_under_reject_policy(self):
        q = qualify(_plan(STRICT), _frame(range(30), range(28)))
        self.assertIsInstance(q, RejectedQualification)
        self.assertEqual(q.reason_code, REASON_INCOMPLETE_PERIOD_COVERAGE)

    def test_incomplete_under_align_policy_aligns_on_offsets(self):
        q = qualify(_plan(ALIGN), _frame(range(30), range(2, 30)))
        self.assertIsInstance(q, ExecutableQualification)
        self.assertEqual(q.action, ACTION_ALIGN)
        self.assertEqual(q.effective.current, Period(CUR_START + dt.timedelta(2), CUR_START + dt.timedelta(29)))
        self.assertEqual(q.effective.baseline, Period(BASE_START + dt.timedelta(2), BASE_START + dt.timedelta(29)))

    def test_no_common_offset(self):
        q = qualify(_plan(ALIGN), _frame(range(0, 30, 2), range(1, 30, 2)))
        self.assertEqual(q.reason_code, REASON_INSUFFICIENT_COMMON_WINDOW)
        self.assertEqual(dict(q.detail)["kind"], WINDOW_NO_OVERLAP)

    def test_run_below_threshold(self):
        q = qualify(_plan(ALIGN), _frame(range(30), list(range(10)) + [20]))
        self.assertEqual(q.reason_code, REASON_INSUFFICIENT_COMMON_WINDOW)
        d = dict(q.detail)
        self.assertEqual((d["kind"], d["length"], d["threshold"]), (WINDOW_BELOW_MINIMUM, 10, 15))

    def test_empty_period(self):
        q = qualify(_plan(ALIGN), _frame(range(30), []))
        self.assertEqual(q.reason_code, REASON_EMPTY_PERIOD)

    def test_conflict_in_window_rejects_before_alignment(self):
        day = CUR_START + dt.timedelta(29)            # 정렬로 잘려 나갈 날
        extra = [_obs(day, v=99)]                     # 같은 grain, 다른 값
        q = qualify(_plan(ALIGN), _frame(range(30), range(2, 30), extra))
        self.assertEqual(q.reason_code, REASON_CONFLICTING_DUPLICATE)

    def test_exact_duplicate_is_merged_not_rejected(self):
        extra = [_obs(CUR_START)]
        q = qualify(_plan(STRICT), _frame(range(30), range(30), extra))
        self.assertEqual(q.action, ACTION_ACCEPT)
        self.assertEqual(q.facts.current.duplicate_rows_removed, 1)


class ThresholdRoundingTests(unittest.TestCase):
    """threshold = max(min_days, round(ratio * limit)), round 는 짝수 반올림."""

    def _threshold(self, limit):
        q = qualify(_plan(ALIGN, limit, limit), _frame(range(limit), [0]))
        return dict(q.detail).get("threshold")

    def test_limit_15_gives_8(self):
        self.assertEqual(self._threshold(15), 8)

    def test_limit_29_gives_14(self):
        self.assertEqual(self._threshold(29), 14)


class NullPolicyTests(unittest.TestCase):
    def test_reject_policy_stops_before_normalisation(self):
        day = CUR_START
        extra = [_obs(day, g=UNKNOWN, v=1, nulls=("g",)), _obs(day, g=UNKNOWN, v=2, nulls=("g",))]
        q = qualify(_plan(STRICT), _frame(range(30), range(30), extra))
        # 두 빈칸 행이 같은 키로 겹쳐도 충돌이 아니라 빈 값 거부가 먼저다.
        self.assertEqual(q.reason_code, REASON_MISSING_REQUIRED_VALUE)

    def test_unknown_group_conflict_is_marked_as_caused_by_nulls(self):
        day = CUR_START
        extra = [_obs(day, g=UNKNOWN, v=1, nulls=("g",)), _obs(day, g=UNKNOWN, v=2, nulls=("g",))]
        q = qualify(_plan(ALIGN), _frame(range(30), range(30), extra))
        self.assertEqual(q.reason_code, REASON_CONFLICTING_DUPLICATE)
        self.assertTrue(any(v.caused_by_null_normalization for v in q.facts.violations))


class ReviewFocusTests(unittest.TestCase):
    def test_rows_outside_both_windows_do_not_affect_the_decision(self):
        outside = dt.date(2026, 8, 15)
        extra = [_obs(outside, v=1), _obs(outside, v=2), _obs(outside, v=2)]
        q = qualify(_plan(STRICT), _frame(range(30), range(30), extra))
        self.assertEqual(q.action, ACTION_ACCEPT)
        self.assertTrue(any(v.scope == SCOPE_OUTSIDE for v in q.facts.violations))

    def test_complete_but_unequal_lengths_is_not_accept(self):
        strict = qualify(_plan(STRICT, 31, 30), _frame(range(31), range(30)))
        self.assertEqual(strict.reason_code, REASON_INCOMPLETE_PERIOD_COVERAGE)
        aligned = qualify(_plan(ALIGN, 31, 30), _frame(range(31), range(30)))
        self.assertEqual(aligned.action, ACTION_ALIGN)

    def test_boundary_days_are_counted(self):
        q = qualify(_plan(STRICT, 3, 3), _frame([0, 1, 2], [0, 1, 2]))
        self.assertEqual((q.facts.current.observed_days, q.facts.baseline.observed_days), (3, 3))

    def test_empty_frame_is_empty_period(self):
        q = qualify(_plan(STRICT), Frame.of([]))
        self.assertEqual(q.reason_code, REASON_EMPTY_PERIOD)

    def test_null_outside_windows_does_not_reject(self):
        extra = [_obs(dt.date(2026, 8, 15), g=UNKNOWN, nulls=("g",))]
        q = qualify(_plan(STRICT), _frame(range(30), range(30), extra))
        self.assertEqual(q.action, ACTION_ACCEPT)


class SealTests(unittest.TestCase):
    def test_executable_cannot_be_built_outside_the_factory(self):
        with self.assertRaises(TypeError):
            ExecutableQualification(object(), None, None, None, None, None, None)

    def test_executable_attributes_cannot_be_replaced(self):
        q = qualify(_plan(STRICT), _frame(range(30), range(30)))
        for name in ("plan", "frame", "effective", "action"):
            with self.assertRaises(AttributeError):
                setattr(q, name, None)

    def test_effective_plan_carries_the_effective_windows(self):
        q = qualify(_plan(ALIGN), _frame(range(30), range(2, 30)))
        self.assertEqual(q.effective_plan.comparison, q.effective)
        self.assertEqual(q.effective_plan.branches, q.plan.branches)

    def test_rejected_conflict_refusal_keeps_the_engine_message(self):
        extra = [_obs(CUR_START, v=99)]
        q = qualify(_plan(STRICT), _frame(range(30), range(30), extra))
        refusal = q.as_refusal()
        self.assertEqual(refusal.stage, "integrity")
        self.assertIn("conflicting duplicate rows at grain", refusal.reason)


class FixRoundTests(unittest.TestCase):
    def test_pipe_in_grain_value_keeps_key_and_null_provenance(self):
        extra = [_obs(CUR_START, g="x|y", v=1, nulls=("g",)), _obs(CUR_START, g="x|y", v=2, nulls=("g",))]
        q = qualify(_plan(ALIGN), _frame(range(30), range(30), extra))
        self.assertEqual(q.reason_code, REASON_CONFLICTING_DUPLICATE)
        (v,) = q.facts.violations
        self.assertEqual(v.grain_key, (CUR_START.isoformat(), "x|y"))
        self.assertTrue(v.caused_by_null_normalization)
        self.assertIn(CUR_START.isoformat() + "|x|y", q.as_refusal().reason)

    def test_null_provenance_counted_before_dedupe(self):
        extra = [_obs(CUR_START, nulls=("g",))]
        q = qualify(_plan(STRICT), _frame(range(30), range(30), extra))
        self.assertEqual(q.reason_code, REASON_MISSING_REQUIRED_VALUE)

    def test_conflict_beats_empty_period(self):
        extra = [_obs(BASE_START, v=99)]
        q = qualify(_plan(ALIGN), _frame(range(30), [], extra))
        self.assertEqual(q.reason_code, REASON_CONFLICTING_DUPLICATE)

    def test_threshold_boundary_at_limit_30(self):
        ok = qualify(_plan(ALIGN), _frame(range(30), list(range(15)) + [20]))
        self.assertEqual(ok.action, ACTION_ALIGN)
        low = qualify(_plan(ALIGN), _frame(range(30), list(range(14)) + [20]))
        self.assertEqual(low.reason_code, REASON_INSUFFICIENT_COMMON_WINDOW)
        d = dict(low.detail)
        self.assertEqual((d["kind"], d["length"], d["threshold"]), (WINDOW_BELOW_MINIMUM, 14, 15))

    def test_min_days_dominates_ratio(self):
        q = qualify(_plan(ALIGN, 10, 10), _frame(range(10), range(6)))
        d = dict(q.detail)
        self.assertEqual((d["kind"], d["length"], d["threshold"]), (WINDOW_BELOW_MINIMUM, 6, 7))
        self.assertEqual(qualify(_plan(ALIGN, 10, 10), _frame(range(10), range(7))).action, ACTION_ALIGN)

    def test_plain_conflict_not_caused_by_nulls(self):
        q = qualify(_plan(STRICT), _frame(range(30), range(30), [_obs(CUR_START, v=99)]))
        self.assertFalse(any(v.caused_by_null_normalization for v in q.facts.violations))

    def test_tie_picks_earlier_run(self):
        from bia.analysis.qualification import _longest_common_run
        cur = list(range(2, 7)) + list(range(10, 15))
        self.assertEqual(_longest_common_run(cur, range(30), 30), (2, 6))
        self.assertEqual(_longest_common_run([], range(30), 30), (-1, -1))

    def test_non_conflict_refusal_stage(self):
        q = qualify(_plan(STRICT), _frame(range(30), range(28)))
        self.assertEqual(q.as_refusal().stage, "qualification")


def _names_factory(source):
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name) and node.id == "_FACTORY":
            return True
        if isinstance(node, ast.Attribute) and node.attr == "_FACTORY":
            return True
        if isinstance(node, ast.alias) and node.name == "_FACTORY":
            return True
    return False


class AlignedGroupTransitionTests(unittest.TestCase):
    def test_transition_counts_use_the_effective_window(self):
        extra = [_obs(d, "late", 5) for d in _days(BASE_START, (0, 1))]
        extra += [_obs(d, "gone", 5) for d in _days(BASE_START, (5,))]
        extra += [_obs(d, "gone2", 5) for d in _days(BASE_START, (6,))]
        extra += [_obs(d, "new", 5) for d in _days(CUR_START, (10,))]
        q = qualify(_plan(ALIGN), _frame(range(30), range(2, 30), extra))
        self.assertEqual(q.action, ACTION_ALIGN)
        result = run_plan(q)
        want = {"entered": 1, "exited": 2, "persisted": 1, "inactive": 0}
        checked = 0
        for breakdown in result.breakdowns:
            if not breakdown.dimensions:
                continue
            labels = sorted(g.key["g"] for g in breakdown.groups)
            self.assertEqual(labels, ["a", "gone", "gone2", "new"])
            self.assertEqual(breakdown.group_transition, want)
            checked += 1
        self.assertEqual(checked, 1)


class FactoryTokenLeakTests(unittest.TestCase):
    def test_scanner_detects_references(self):
        self.assertTrue(_names_factory("from x import _FACTORY"))
        self.assertTrue(_names_factory("y = m._FACTORY"))
        self.assertTrue(_names_factory("z = _FACTORY"))
        self.assertFalse(_names_factory("s = '_FACTORY'  # _FACTORY"))

    def test_no_other_module_references_factory(self):
        root = pathlib.Path(__file__).resolve().parent.parent / "bia"
        own = (root / "analysis" / "qualification.py").resolve()
        scanned = 0
        for path in root.rglob("*.py"):
            if path.resolve() == own:
                continue
            scanned += 1
            self.assertFalse(_names_factory(path.read_text(encoding="utf-8")), str(path))
        self.assertGreater(scanned, 5)


if __name__ == "__main__":
    unittest.main()
