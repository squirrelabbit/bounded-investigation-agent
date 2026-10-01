"""`analyze_complaints()` runs on the v2 engine, and its view is v1 to the byte.

`bia/metrics.py` is imported here only as the independent legacy reference. The
product path never loads it (checked in a fresh interpreter below).

Equality alone is not the comparison: in Python `1903 == 1903.0` is True, so a
dict comparison cannot see an int leaking into a float. Every comparison here is
on `json.dumps` bytes (insertion order included) and on the type of every field.
"""
from __future__ import annotations

import ast
import json
import os
import random
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from bia import controller as controller_mod
from bia import metrics as v1_metrics
from bia.adapters import complaints as adapter
from bia.adapters.complaints import (REASON_JOINT_OMITTED,
                                     ComplaintAnalysisCompatibilityError,
                                     complaint_view)
from bia.analysis import compiler, decompose, engine, operators
from bia.analysis.compiler import compile_request
from bia.analysis.engine import execute
from bia.analysis.errors import AnalysisRefused
from bia.analysis.frame import Frame, Observation
from bia.analysis.request import AnalysisRequest, PeriodComparison
from bia.complaint_analysis import CONTRACT_MEMBERS, analyze_complaints, qualify_complaints
from bia.controller import investigate
from bia.decision import (DecisionProvider, DeterministicHeuristicSelector,
                          GreedyEvidenceSelector)
from bia.domains import complaints as complaints_domain
from bia.domains import ecommerce as ecommerce_domain  # noqa: F401  (등록 부작용)
from bia.integrity import MODE_BLOCKED, _row_to_observation
from bia.legacy_comparability import decide_comparability, inspect_period
from bia.store import load_scenario
from bia.types import (DEFER, DIM_COMPLAINT_TYPE, DIM_PRODUCT, AnalysisIntent, CellDelta,
                       GroupDelta, MetricRow, Period)

from . import support

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CASES = ([("scenarios", "S%02d" % i) for i in range(1, 25)]
         + [("challenges", "C%02d" % i) for i in range(1, 9)])
SELECTORS = (DeterministicHeuristicSelector, GreedyEvidenceSelector)


def _load(group, case_id):
    return load_scenario(os.path.join(ROOT, "data", group, case_id))


def _windows(intent, rows):
    """The controller's own route to the rows and windows it hands the seam."""
    current_rows, current_integrity = inspect_period(list(rows), intent.current_period)
    baseline_rows, baseline_integrity = inspect_period(list(rows), intent.baseline_period)
    comparability = decide_comparability(current_integrity, baseline_integrity)
    if not comparability.usable:
        return None
    return (current_rows + baseline_rows, comparability.current_window,
            comparability.baseline_window)


def type_tree(value):
    """Structure with every leaf replaced by its exact type name, keys in order."""
    if isinstance(value, dict):
        return ["dict", [(k, type_tree(v)) for k, v in value.items()]]
    if isinstance(value, list):
        return ["list", [type_tree(v) for v in value]]
    return type(value).__name__


def dump(value):
    return json.dumps(value, ensure_ascii=False)


def _member_payload(view, member):
    """A JSON-able rendering of one contract member, plus its Python types."""
    if member == "as_dict":
        return view.as_dict()
    if member == "top":
        return {d: [g.as_dict() for g in view.top(d)]
                for d in (DIM_PRODUCT, DIM_COMPLAINT_TYPE)}
    value = getattr(view, member)
    if member in ("by_product", "by_complaint_type", "cells"):
        return [item.as_dict() for item in value]
    return value


def _expected_suppression(legacy, dimension):
    """`suppress_top_contributor` has no v1 counterpart, so it is checked against a
    re-derivation from the legacy group deltas with the pre-registered constants,
    not against the engine's own flag."""
    groups = legacy.by_product if dimension == DIM_PRODUCT else legacy.by_complaint_type
    net = sum(g.delta for g in groups)
    gross = sum(abs(g.delta) for g in groups)
    heavy = gross > decompose.GROSS_EPSILON and abs(net) / gross < decompose.CANCELLATION_THRESHOLD
    return heavy or abs(net) <= decompose.SHARE_EPSILON


def _legacy_with_policy(rows, current_window, baseline_window):
    """v1 arithmetic plus the independent re-derivation of the v2 output policy.
    v1 never had the policy; without it the answer cannot be rendered at all."""
    legacy = v1_metrics.compute(rows, current_window, baseline_window)
    legacy.suppress_top_contributor = lambda d: _expected_suppression(legacy, d)
    return legacy


def _element_classes(view, member):
    if member == "top":
        return [type(g) for d in (DIM_PRODUCT, DIM_COMPLAINT_TYPE) for g in view.top(d)]
    if member in ("by_product", "by_complaint_type", "cells"):
        return [type(item) for item in getattr(view, member)]
    return []


class LegacyRuntimeIsolationTests(unittest.TestCase):
    """Acceptance 1 and 2 (structural): what `import bia.cli` loads, in a fresh process."""

    PROBE = (
        "import json, sys\n"
        "sys.path.insert(0, %r)\n"
        "import bia.cli\n"
        "mods = sorted(m for m in sys.modules if m == 'bia' or m.startswith('bia.'))\n"
        "print(json.dumps({'mods': mods, 'files': [sys.modules[m].__file__ for m in mods]}))\n"
    )

    def _probe(self):
        with tempfile.TemporaryDirectory() as cwd:
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
            env.pop("PYTHONPATH", None)
            out = subprocess.run([sys.executable, "-c", self.PROBE % ROOT], cwd=cwd, env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 universal_newlines=True, check=True)
        return json.loads(out.stdout)

    def test_the_product_path_does_not_load_the_legacy_module(self):
        probe = self._probe()
        self.assertNotIn("bia.metrics", probe["mods"])
        # Loaded from this tree, or the absence above could be someone else's package.
        self.assertIn("bia.controller", probe["mods"])
        for path in probe["files"]:
            self.assertTrue(os.path.abspath(path).startswith(ROOT + os.sep), path)

    def test_the_engine_modules_are_loaded_by_the_product_path(self):
        mods = set(self._probe()["mods"])
        for name in ("bia.analysis.compiler", "bia.analysis.engine",
                     "bia.analysis.operators", "bia.analysis.decompose",
                     "bia.adapters.complaints", "bia.domains.complaints"):
            self.assertIn(name, mods)


class _Boom(Exception):
    pass


def _explode(*_args, **_kwargs):
    raise _Boom("bia.metrics.compute was called")


class NoLegacyCallTests(unittest.TestCase):
    """Acceptance 1 (behavioural): all 32 cases finish with `metrics.compute` armed to explode."""

    def test_every_case_runs_to_the_end_without_metrics_compute(self):
        finished = 0
        with mock.patch.object(v1_metrics, "compute", side_effect=_explode) as armed:
            for group, case_id in CASES:
                for factory in SELECTORS:
                    intent, rows, tickets, _meta = _load(group, case_id)
                    result = investigate(intent, rows, tickets, factory())
                    self.assertTrue(result.state.finish_reason, case_id)
                    self.assertTrue(result.answer.render(), case_id)
                    finished += 1
        self.assertEqual(finished, 64)
        self.assertEqual(armed.call_count, 0)

    def test_the_armed_patch_would_fire_if_the_seam_fell_back(self):
        """Without this, a patch nothing can reach would pass trivially."""
        intent, rows, tickets, _meta = _load("scenarios", "S01")
        with mock.patch.object(v1_metrics, "compute", side_effect=_explode):
            with mock.patch.object(controller_mod, "analyze_complaints",
                                   lambda q: v1_metrics.compute(q)):
                with self.assertRaises(_Boom):
                    investigate(intent, rows, tickets, DeterministicHeuristicSelector())


class _CallSpy:
    """Records every Python function entered, by code object. No patching, so an
    import alias (`from .operators import aggregate`) cannot hide a call."""

    def __init__(self):
        self.codes = set()

    def _profile(self, frame, event, _arg):
        if event == "call":
            self.codes.add(frame.f_code)

    def __enter__(self):
        self._previous = sys.getprofile()
        sys.setprofile(self._profile)
        return self

    def __exit__(self, *_exc):
        sys.setprofile(self._previous)
        return False

    def called(self, function):
        return function.__code__ in self.codes


class EngineCallTests(unittest.TestCase):
    """Acceptance 2 (behavioural): one real `investigate()` goes through the engine."""

    MUST_RUN = (compiler.compile_request, engine.run_plan, operators.aggregate,
                operators.compare, operators.group_universe, operators.order_groups)

    def test_one_investigation_calls_the_engine_and_not_the_ratio_path(self):
        intent, rows, tickets, _meta = _load("scenarios", "S01")
        with _CallSpy() as spy:
            result = investigate(intent, rows, tickets, DeterministicHeuristicSelector())
        self.assertIsNotNone(result.state.metrics)
        for function in self.MUST_RUN:
            self.assertTrue(spy.called(function), function.__name__)
        # complaints is an additive metric. The ratio decomposition running here
        # would be the defect, not coverage.
        self.assertFalse(spy.called(decompose.decompose_ratio))
        self.assertFalse(spy.called(v1_metrics.compute))

    def test_the_spy_sees_the_ratio_path_when_it_does_run(self):
        """Positive control for the negative assertion above."""
        current = Period.of("2026-06-08", "2026-06-08")
        baseline = Period.of("2026-06-01", "2026-06-01")
        observations = []
        for day, orders in ((baseline.start, 3), (current.start, 5)):
            for channel in ("organic", "paid"):
                observations.append(Observation(
                    day=day,
                    keys=(("category", "shoes"), ("channel", channel), ("device", "web")),
                    measures=(("orders", orders), ("revenue_krw", 0), ("sessions", 50))))
        plan = compile_request(AnalysisRequest(
            domain="ecommerce", metric="conversion_rate", breakdowns=("channel",),
            comparison=PeriodComparison(current=current, baseline=baseline)))
        with _CallSpy() as spy:
            execute(plan, Frame.of(observations))
        self.assertTrue(spy.called(decompose.decompose_ratio))


class ViewReproducesLegacyTests(unittest.TestCase):
    """Acceptance 3, 4, 5: all eleven members, bytes and types, over 32 cases."""

    def _assert_same(self, old, new, label):
        compared = set()
        for member in sorted(CONTRACT_MEMBERS):
            if member == "increased":
                self.assertIs(type(new.increased), bool, label)
                self.assertIs(new.increased, old.increased, label)
                compared.add(member)
                continue
            if member == "suppress_top_contributor":
                for dimension in (DIM_PRODUCT, DIM_COMPLAINT_TYPE):
                    got = new.suppress_top_contributor(dimension)
                    self.assertIs(type(got), bool, label)
                    self.assertIs(got, _expected_suppression(old, dimension),
                                  "%s %s" % (label, dimension))
                compared.add(member)
                continue
            old_payload = _member_payload(old, member)
            new_payload = _member_payload(new, member)
            self.assertEqual(dump(new_payload), dump(old_payload), "%s %s" % (label, member))
            self.assertEqual(type_tree(new_payload), type_tree(old_payload),
                             "%s %s" % (label, member))
            self.assertEqual(_element_classes(new, member), _element_classes(old, member),
                             "%s %s" % (label, member))
            compared.add(member)
        self.assertEqual(compared, set(CONTRACT_MEMBERS))
        # `top()` hands back the list's own elements, as v1 does.
        for dimension, groups in ((DIM_PRODUCT, new.by_product),
                                  (DIM_COMPLAINT_TYPE, new.by_complaint_type)):
            for picked in new.top(dimension):
                self.assertTrue(any(picked is g for g in groups), label)

    def test_every_case_matches_metrics_compute(self):
        compared, blocked, cross_cells = 0, 0, []
        for group, case_id in CASES:
            intent, rows, _tickets, _meta = _load(group, case_id)
            prepared = _windows(intent, rows)
            if prepared is None:
                blocked += 1
                continue
            clean, current_window, baseline_window = prepared
            old = v1_metrics.compute(clean, current_window, baseline_window)
            new = analyze_complaints(qualify_complaints(clean, current_window, baseline_window)[0])
            self.assertIs(type(new), adapter.ComplaintAnalysisView)
            self._assert_same(old, new, case_id)
            cross_cells.append(len(new.cells))
            compared += 1
        self.assertEqual(compared + blocked, 32)
        self.assertGreaterEqual(compared, 28)
        self.assertLess(max(cross_cells), operators.CROSS_CELL_LIMIT)

    def test_the_whole_run_record_is_byte_identical(self):
        """Everything downstream of the seam — state, view, answer — through the real controller."""
        runs = 0
        for group, case_id in CASES:
            for factory in SELECTORS:
                intent, rows, tickets, _meta = _load(group, case_id)
                new = investigate(intent, rows, tickets, factory())
                with mock.patch.object(controller_mod, "analyze_complaints",
                                       lambda _q: _legacy_with_policy(*_windows(intent, rows))):
                    old = investigate(intent, rows, tickets, factory())
                label = "%s/%s" % (case_id, factory.__name__)
                self.assertEqual(dump(new.as_dict()), dump(old.as_dict()), label)
                self.assertEqual(type_tree(new.as_dict()), type_tree(old.as_dict()), label)
                self.assertEqual(new.answer.render(), old.answer.render(), label)
                runs += 1
        self.assertEqual(runs, 64)

    def test_ties_negatives_and_one_sided_groups_keep_the_v1_order(self):
        """The bundled cases barely tie. This input is built to: equal deltas across
        products and types, decreases, zero, groups present in one period only."""
        baseline = Period.of("2026-06-02", "2026-06-02")
        current = Period.of("2026-06-10", "2026-06-10")
        spec = [
            ("zeta", "delay", 2, 5), ("alpha", "delay", 2, 5), ("mid", "billing", 1, 4),
            ("alpha", "billing", 4, 4), ("beta", "crash", 6, 0), ("gamma", "crash", 0, 3),
            ("zeta", "billing", 0, 3), ("beta", "delay", 3, 3), ("alpha", "crash", 7, 1),
        ]
        rows = []
        for product, complaint_type, base, cur in spec:
            rows.append(MetricRow(baseline.start, product,
                                  complaint_type, base))
            rows.append(MetricRow(current.start, product,
                                  complaint_type, cur))
        old = v1_metrics.compute(rows, current, baseline)
        new = analyze_complaints(qualify_complaints(rows, current, baseline)[0])
        self._assert_same(old, new, "synthetic ties")
        # The input really does tie, or the order check above proved nothing.
        deltas = [c.delta for c in new.cells]
        self.assertGreater(len(deltas), len(set(deltas)))

    def test_no_baseline_gives_a_null_pct_change_like_v1(self):
        baseline = Period.of("2026-06-01", "2026-06-01")
        current = Period.of("2026-06-08", "2026-06-08")
        rows = [MetricRow(current.start, "alpha", "delay", 4),
                MetricRow(baseline.start, "alpha", "delay", 0)]
        old = v1_metrics.compute(rows, current, baseline)
        new = analyze_complaints(qualify_complaints(rows, current, baseline)[0])
        self.assertIsNone(new.pct_change)
        self._assert_same(old, new, "zero baseline")


class TopSelectionTests(unittest.TestCase):
    def test_the_constants_are_the_legacy_ones(self):
        self.assertEqual(adapter.TOP_SHARE_TARGET, v1_metrics.TOP_SHARE_TARGET)
        self.assertIs(type(adapter.TOP_SHARE_TARGET), type(v1_metrics.TOP_SHARE_TARGET))
        self.assertEqual(adapter.TOP_MAX, v1_metrics.TOP_MAX)
        self.assertIs(type(adapter.TOP_MAX), type(v1_metrics.TOP_MAX))

    def test_the_selection_is_the_legacy_selection(self):
        rng = random.Random(20260928)
        checked = 0
        for _ in range(2000):
            size = rng.randint(0, 7)
            groups = []
            for i in range(size):
                delta = rng.choice([-3, -1, 0, 1, 2, 4, 8, 16])
                share = rng.choice([0.0, 0.1, 0.2, 0.25, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0])
                groups.append(GroupDelta("product", "g%d" % i, 0, 0, delta, share))
            old = v1_metrics.top_contributors(groups)
            new = adapter.top_contributors(groups)
            self.assertEqual([id(g) for g in new], [id(g) for g in old])
            checked += 1
        self.assertEqual(checked, 2000)


def _names_in(source, filename):
    """Every identifier, attribute, keyword and string constant in the module."""
    out = set()
    for node in ast.walk(ast.parse(source, filename)):
        if isinstance(node, ast.Name):
            out.add(node.id)
        elif isinstance(node, ast.Attribute):
            out.add(node.attr)
        elif isinstance(node, ast.keyword) and node.arg:
            out.add(node.arg)
        elif isinstance(node, ast.alias):
            out.add(node.name.split(".")[-1])
            if node.asname:
                out.add(node.asname)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.add(node.value)
    return out


FORBIDDEN_V2_QUANTITIES = ("contribution_share", "relative_change")
VIEW_SOURCES = ("bia/adapters/complaints.py", "bia/complaint_analysis.py")


def forbidden_references(source, filename):
    names = _names_in(source, filename)
    # Substring, not equality: a docstring or an f-string key naming it counts too.
    return sorted(q for q in FORBIDDEN_V2_QUANTITIES if any(q in n for n in names))


class V1QuantitiesOnlyTests(unittest.TestCase):
    """Acceptance 5: `share_of_increase` and `pct_change` are the v1 quantities.
    v2's `contribution_share` (net ÷ Δ, suppressed on cancellation) and
    `relative_change` are different numbers with similar names."""

    def test_the_view_never_reads_the_v2_quantities(self):
        for relpath in VIEW_SOURCES:
            with open(os.path.join(ROOT, relpath), encoding="utf-8") as handle:
                source = handle.read()
            self.assertEqual(forbidden_references(source, relpath), [], relpath)

    def test_the_check_fires_on_each_form(self):
        planted = {
            "attr": "def f(g):\n    return g.contribution_share\n",
            "subscript": "def f(r):\n    return r.comparison['relative_change']\n",
            "getattr": "def f(g):\n    return getattr(g, 'contribution_share')\n",
            "get": "def f(r):\n    return r.comparison.get('relative_change')\n",
            "keyword": "def f(g):\n    return dict(contribution_share=1)\n",
            "import": "from x import relative_change\n",
        }
        for label, source in planted.items():
            with self.subTest(label=label):
                self.assertTrue(forbidden_references(source, label), label)


def _small_result():
    baseline = Period.of("2026-06-01", "2026-06-01")
    current = Period.of("2026-06-08", "2026-06-08")
    rows = [MetricRow(baseline.start, "alpha", "delay", 2),
            MetricRow(current.start, "alpha", "delay", 5),
            MetricRow(current.start, "beta", "billing", 1)]
    plan = compile_request(AnalysisRequest(
        domain=complaints_domain.SPEC.name, metric="complaint_count",
        breakdowns=tuple(complaints_domain.SPEC.dimensions),
        comparison=PeriodComparison(current=current, baseline=baseline)))
    return execute(plan, Frame.of([_row_to_observation(r) for r in rows]))


class IntegerFailClosedTests(unittest.TestCase):
    def test_the_real_result_builds_with_ints_everywhere(self):
        view = complaint_view(_small_result())
        for value in (view.current_total, view.baseline_total, view.delta):
            self.assertIs(type(value), int)
        for item in list(view.by_product) + list(view.by_complaint_type) + list(view.cells):
            for value in (item.current, item.baseline, item.delta):
                self.assertIs(type(value), int)

    def _assert_refused(self, mutate, fragment):
        result = _small_result()
        mutate(result)
        with self.assertRaises(TypeError) as raised:
            complaint_view(result)
        self.assertIn(fragment, str(raised.exception))

    def test_a_float_group_value_is_refused(self):
        def mutate(result):
            group = result.breakdowns[1].groups[0]
            group.current_value = float(group.current_value)
        self._assert_refused(mutate, "current_value")

    def test_a_float_cell_value_is_refused(self):
        def mutate(result):
            group = result.breakdowns[3].groups[0]
            group.baseline_value = float(group.baseline_value)
        self._assert_refused(mutate, "baseline_value")

    def test_a_float_delta_is_refused(self):
        def mutate(result):
            group = result.breakdowns[2].groups[0]
            group.group_delta = float(group.group_delta)
        self._assert_refused(mutate, "group_delta")

    def test_a_float_total_is_refused(self):
        def mutate(result):
            result.comparison["current"] = float(result.comparison["current"])
        self._assert_refused(mutate, "comparison current")

    def test_a_bool_is_not_an_int(self):
        def mutate(result):
            result.breakdowns[3].groups[0].group_delta = True
        self._assert_refused(mutate, "group_delta")

    def test_a_missing_group_value_is_refused(self):
        def mutate(result):
            result.breakdowns[1].groups[0].current_value = None
        self._assert_refused(mutate, "current_value")


def _wide_rows(cells):
    baseline = Period.of("2026-06-01", "2026-06-01")
    current = Period.of("2026-06-08", "2026-06-08")
    rows = []
    for i in range(cells):
        rows.append(MetricRow(current.start, "p%04d" % i, "delay", 1 + i % 3))
    rows.append(MetricRow(baseline.start, "p0000", "delay", 1))
    return rows, current, baseline


class NoFallbackTests(unittest.TestCase):
    def test_the_cross_cell_limit_propagates_from_the_seam(self):
        rows, current, baseline = _wide_rows(1001)
        with mock.patch.object(v1_metrics, "compute", side_effect=_explode) as armed:
            with self.assertRaises(ComplaintAnalysisCompatibilityError) as raised:
                analyze_complaints(qualify_complaints(rows, current, baseline)[0])
        error = raised.exception
        self.assertEqual(error.reason, REASON_JOINT_OMITTED)
        self.assertEqual(error.cause, "cross_cell_limit_exceeded")
        self.assertEqual(error.observed_cells, 1001)
        self.assertEqual(error.limit, 1000)
        self.assertEqual(armed.call_count, 0)

    def test_the_limit_itself_still_builds(self):
        rows, current, baseline = _wide_rows(1000)
        self.assertEqual(len(analyze_complaints(qualify_complaints(rows, current, baseline)[0]).cells), 1000)

    def test_a_conflicting_duplicate_is_refused_by_the_product_path(self):
        """Migrated from the seam-level test `test_an_engine_refusal_propagates_from_the_seam`:
        in v2.1 a rejected qualification can no longer reach `analyze_complaints`. The
        product rejects at qualification and renders it through the comparability path."""
        current, baseline, rows = _conflicting_rows()
        provider = _RecordingProvider()
        with mock.patch.object(v1_metrics, "compute", side_effect=_explode) as armed, \
                mock.patch.object(engine, "run_plan", side_effect=_explode) as engine_run, \
                mock.patch("bia.complaint_analysis.run_plan", side_effect=_explode) as seam_run:
            result = investigate(AnalysisIntent(current_period=current, baseline_period=baseline),
                                 rows, support.tickets(), provider)
        state = result.state
        self.assertEqual(state.finish_reason, controller_mod.FINISH_BLOCKED)
        self.assertEqual(state.comparability.mode, MODE_BLOCKED)
        self.assertTrue(state.comparability.reason.startswith("conflicting_duplicate_rows"),
                        state.comparability.reason)
        self.assertIn("2026-06-08|alpha|delay", state.current_integrity.conflicting_keys)
        self.assertIsNone(state.metrics)
        self.assertEqual(armed.call_count, 0)
        self.assertEqual(engine_run.call_count, 0)
        self.assertEqual(seam_run.call_count, 0)
        self.assertEqual(provider.calls, [])
        self.assertTrue(result.answer.render())

    def test_a_conflicting_duplicate_is_refused_by_the_engine_entry(self):
        current, baseline, rows = _conflicting_rows()
        plan = compile_request(AnalysisRequest(
            domain=complaints_domain.SPEC.name, metric="complaint_count",
            breakdowns=tuple(complaints_domain.SPEC.dimensions),
            comparison=PeriodComparison(current=current, baseline=baseline)))
        with self.assertRaises(AnalysisRefused) as raised:
            execute(plan, Frame.of([_row_to_observation(r) for r in rows]))
        self.assertEqual(raised.exception.stage, "integrity")
        self.assertEqual(raised.exception.reason,
                         "conflicting duplicate rows at grain ['day', 'product', 'complaint_type']: "
                         "['2026-06-08|alpha|delay']")


def _conflicting_rows():
    baseline = Period.of("2026-06-01", "2026-06-07")
    current = Period.of("2026-06-08", "2026-06-14")
    rows = [MetricRow(current.start, "alpha", "delay", 3),
            MetricRow(current.start, "alpha", "delay", 4),
            MetricRow(baseline.start, "alpha", "delay", 1)]
    return current, baseline, rows


class _RecordingProvider(DecisionProvider):
    """The controller swallows provider exceptions, so a raising provider would not
    fail the test. This one records every call instead."""

    name = "recording"

    def __init__(self):
        self.calls = []

    def select_next_evidence(self, state, candidates):
        self.calls.append(list(candidates))
        return DEFER


class CliCompatibilityErrorTests(unittest.TestCase):
    """The real CLI on a `--data-dir` with 1001 cross cells: a message, not a traceback."""

    def _data_dir(self, root, cells):
        baseline = Period.of("2026-06-01", "2026-06-07")
        current = Period.of("2026-06-08", "2026-06-14")
        lines = ["day,product,complaint_type,count"]
        for period in (baseline, current):
            for day in period.dates():
                lines.append("%s,p0000,delivery_delay,1" % day.isoformat())
        for i in range(1, cells):
            lines.append("%s,p%04d,delivery_delay,2" % (current.start.isoformat(), i))
        with open(os.path.join(root, "metrics.csv"), "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
        ticket = {"ticket_id": "T-1", "day": current.start.isoformat(), "product": "p0001",
                  "complaint_type": "delivery_delay", "text": "The parcel is late.",
                  "source": "web_form"}
        with open(os.path.join(root, "tickets.jsonl"), "w", encoding="utf-8") as handle:
            handle.write(json.dumps(ticket) + "\n")
        return ["--current", "%s:%s" % (current.start, current.end),
                "--baseline", "%s:%s" % (baseline.start, baseline.end)]

    def _run(self, cells):
        with tempfile.TemporaryDirectory() as root:
            periods = self._data_dir(root, cells)
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
            return subprocess.run(
                [sys.executable, "-m", "bia.cli", "run", "--data-dir", root] + periods,
                cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                universal_newlines=True)

    def test_1001_cells_exit_nonzero_with_the_reason(self):
        proc = self._run(1001)
        self.assertNotEqual(proc.returncode, 0)
        self.assertNotIn("Traceback", proc.stderr)
        self.assertEqual(proc.stdout, "")
        for fragment in ("reason=required_joint_breakdown_omitted",
                         "cause=cross_cell_limit_exceeded",
                         "observed_cells=1001", "limit=1000"):
            self.assertIn(fragment, proc.stderr)

    def test_1000_cells_still_run(self):
        """The same fixture one cell smaller answers, so the failure above is the limit."""
        proc = self._run(1000)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("종료 사유", proc.stdout)


if __name__ == "__main__":
    unittest.main()
