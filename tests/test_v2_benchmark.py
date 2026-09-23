"""사례 벤치마크. 기대값은 `bia.analysis` 를 전혀 모르는 oracle 에서 온다."""
from __future__ import annotations

import ast
import dataclasses
import json
import os
import subprocess
import sys
import unittest
from fractions import Fraction

from bia.analysis.compiler import compile_request
from bia.analysis.engine import run_plan
from bia.analysis.errors import AnalysisRefused, RequestError
from bia.analysis.frame import load_observations
from bia.analysis.registry import get_domain
from bia.analysis.request import AnalysisRequest, PeriodComparison
from bia.analysis.result import STATUS_OK, STATUS_OMITTED
from bia.domains import complaints, ecommerce, support_ops  # noqa: F401
from bia.types import Period
from bia.v2bench import generate
from bia.v2bench.cases import CASES

CASES_BY_ID = dict((case.case_id, case) for case in CASES)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
V2_ROOT = os.path.join(REPO_ROOT, "data", "v2")
with open(os.path.join(V2_ROOT, "oracle.json"), encoding="utf-8") as _handle:
    ORACLE = json.load(_handle)

PLACES = 9


def _label(breakdown, group_key):
    return "|".join(group_key[d] for d in breakdown.dimensions)


class BenchmarkInventoryTests(unittest.TestCase):
    def test_cases_have_unique_ids_and_match_the_oracle(self):
        ids = [case.case_id for case in CASES]
        self.assertEqual(len(set(ids)), len(ids))
        # 18 은 계획의 목표치다. 사례는 늘어날 수 있어도 줄어들면 안 된다.
        self.assertGreaterEqual(len(ids), 18)
        self.assertEqual(sorted(ORACLE["cases"]), sorted(ids))
        self.assertEqual(ORACLE["case_count"], len(ids))

    def test_every_case_has_a_hand_checked_note(self):
        for case in CASES:
            with self.subTest(case=case.case_id):
                self.assertTrue(case.notes.strip(), case.case_id)


class OracleIndependenceTests(unittest.TestCase):
    """oracle 이 피검 코드를 부르면 벤치마크는 아무것도 증명하지 못한다."""

    MODULES = ("bia/v2bench/__init__.py", "bia/v2bench/cases.py",
               "bia/v2bench/generate.py")
    FORBIDDEN = ("bia.analysis", "bia.metrics")

    def _imported_names(self, path, package="bia.v2bench"):
        with open(os.path.join(REPO_ROOT, path), encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=path)
        names = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base = package.rsplit(".", node.level - 1)[0] if node.level > 1 else package
                    module = "%s.%s" % (base, node.module) if node.module else base
                else:
                    module = node.module or ""
                names.append(module)
        return names

    def test_no_source_level_import_of_the_code_under_test(self):
        for path in self.MODULES:
            for name in self._imported_names(path):
                for forbidden in self.FORBIDDEN:
                    self.assertFalse(
                        name == forbidden or name.startswith(forbidden + "."),
                        "%s imports %s" % (path, name))

    def test_generator_never_loads_the_code_under_test_at_runtime(self):
        script = (
            "import sys\n"
            "import bia.v2bench.generate\n"
            "bad = sorted(m for m in sys.modules\n"
            "             if m == 'bia.analysis' or m.startswith('bia.analysis.')\n"
            "             or m == 'bia.metrics')\n"
            "print(','.join(bad))\n"
        )
        out = subprocess.check_output([sys.executable, "-c", script], cwd=REPO_ROOT)
        self.assertEqual(out.decode("utf-8").strip(), "")


class FlagImplicationTests(unittest.TestCase):
    """`simpson_strict ⟹ composition_dominant` 은 사례 설계의 우연이 아니라 정리다.

    현행 정의에서 simpson_strict 는 comparable 의 rate_effect 가 전부 같은 부호
    s(≠0)이고 s ≠ sign(delta) 를 요구한다. total_rate 는 comparable 만 합산하므로
    상쇄 없이 sign(total_rate)=s 가 되고, decomposition_complete 와 sign(delta)≠0
    까지 이미 요구하므로 composition_dominant 의 네 조건이 모두 충족된다.
    역은 성립하지 않는다 — rate 부호가 섞이면 simpson 만 거짓이 된다.
    """

    def _breakdowns(self):
        for case_id, entry in sorted(ORACLE["cases"].items()):
            for name, breakdown in sorted(entry.get("breakdowns", {}).items()):
                yield case_id, name, breakdown

    def test_simpson_strict_implies_composition_dominant(self):
        witnesses = []
        for case_id, name, breakdown in self._breakdowns():
            flags = breakdown["expect_flags"]
            if not flags["simpson_strict"]:
                continue
            witnesses.append("%s/%s" % (case_id, name))
            self.assertTrue(flags["composition_dominant"],
                            "%s/%s: simpson_strict 인데 composition_dominant 가 "
                            "거짓이다" % (case_id, name))
        self.assertTrue(witnesses,
                        "simpson_strict 가 참인 분해가 하나도 없다 — 함의가 공허하다")

    def test_the_implication_is_not_an_equivalence(self):
        witnesses = []
        for case_id, name, breakdown in self._breakdowns():
            flags = breakdown["expect_flags"]
            if not (flags["composition_dominant"] and not flags["simpson_strict"]):
                continue
            witnesses.append("%s/%s" % (case_id, name))
            signs = set()
            for entry in breakdown["groups"].values():
                if entry["expect_comparable"]:
                    rate = entry["expect_rate_effect"]
                    signs.add(0 if rate == 0 else (1 if rate > 0 else -1))
            self.assertGreater(len(signs), 1,
                               "%s/%s: composition_dominant 만 참인데 rate 부호가 "
                               "섞여 있지 않다" % (case_id, name))
        self.assertTrue(
            witnesses,
            "composition_dominant 만 참인 사례가 없어 함의가 동치처럼 보인다")


class OracleFailClosedTests(unittest.TestCase):
    """oracle 이 모르는 것을 만나면 답을 내지 말고 멈춰야 한다."""

    def test_oracle_error_is_not_an_assertion_error(self):
        # 호출자의 `except AssertionError` 가 계약 위반을 삼키면 안 된다.
        self.assertFalse(issubclass(generate.OracleError, AssertionError))

    def test_unsupported_rank_by_stops_the_oracle(self):
        case = dataclasses.replace(CASES_BY_ID["C01"], rank_by="popularity")
        with self.assertRaises(generate.OracleError) as caught:
            generate.case_oracle(case)
        self.assertIn("rank_by", str(caught.exception))

    def test_rank_by_a_field_the_groups_do_not_have_stops_the_oracle(self):
        entries = [(("paid",), {"net_contribution": Fraction(1),
                                "group_delta": Fraction(1)})]
        with self.assertRaises(generate.OracleError):
            generate._rank(entries, ("channel",), "rate_effect")

    def test_each_supported_rank_by_orders_by_that_field(self):
        entries = [
            (("paid",), {"net_contribution": Fraction(1),
                         "rate_effect": Fraction(-5), "mix_effect": Fraction(9)}),
            (("organic",), {"net_contribution": Fraction(-2),
                            "rate_effect": Fraction(7), "mix_effect": Fraction(-9)}),
        ]
        self.assertEqual(generate._rank(entries, ("channel",), "net_contribution"),
                         ["paid", "organic"])
        self.assertEqual(generate._rank(entries, ("channel",), "rate_effect"),
                         ["organic", "paid"])
        self.assertEqual(generate._rank(entries, ("channel",), "mix_effect"),
                         ["paid", "organic"])

    def test_a_missing_rate_effect_ranks_as_zero_like_production(self):
        entries = [
            (("paid",), {"net_contribution": Fraction(1),
                         "rate_effect": Fraction(-5), "mix_effect": Fraction(1)}),
            (("organic",), {"net_contribution": Fraction(-2),
                            "rate_effect": None, "mix_effect": None}),
        ]
        self.assertEqual(generate._rank(entries, ("channel",), "rate_effect"),
                         ["organic", "paid"])

    def test_a_value_inside_the_production_tolerance_band_stops_the_oracle(self):
        inside = Fraction(1, 10 ** 10)
        for value in (inside, -inside, generate.FLOAT_TOL):
            with self.subTest(value=value):
                with self.assertRaises(generate.OracleError):
                    generate._sign(value, "probe")
        # band 밖은 그대로 부호를 낸다.
        outside = generate.FLOAT_TOL * 2
        self.assertEqual(generate._sign(outside, "probe"), 1)
        self.assertEqual(generate._sign(Fraction(0), "probe"), 0)

    def test_a_numeric_golden_key_that_matches_nothing_stops_the_oracle(self):
        case = dataclasses.replace(CASES_BY_ID["C01"], golden={"bogus_total": 1})
        entry = {"breakdowns": {"channel": {"expect_totals": {}}}}
        with self.assertRaises(generate.OracleError) as caught:
            generate._apply_golden(case, entry, {"bogus_total": Fraction(1)})
        self.assertIn("bogus_total", str(caught.exception))


class BenchmarkTests(unittest.TestCase):
    def _request(self, case):
        return AnalysisRequest(
            domain=case.domain, metric=case.metric,
            breakdowns=case.breakdowns,
            comparison=PeriodComparison(
                current=Period.of(*case.current),
                baseline=Period.of(*case.baseline)),
            rank_by=case.rank_by,
        )

    def test_every_case_matches_its_oracle(self):
        for case in CASES:
            with self.subTest(case=case.case_id):
                self._run_case(case)

    def _run_case(self, case):
        expected = ORACLE["cases"][case.case_id]
        spec = get_domain(case.domain)
        metric = spec.metrics[case.metric]
        request = self._request(case)

        if case.expect_refused and case.expect_refused[0] == "compile":
            with self.assertRaises(RequestError) as caught:
                compile_request(request)
            self.assertIn(case.expect_refused[1], str(caught.exception))
            return

        plan = compile_request(request)
        rows = load_observations(
            os.path.join(V2_ROOT, case.case_id, "metrics.csv"), spec, metric.columns)

        if case.expect_refused:
            stage, needle = case.expect_refused
            with self.assertRaises(AnalysisRefused) as caught:
                run_plan(plan, rows)
            self.assertEqual(caught.exception.stage, stage)
            self.assertIn(needle, caught.exception.reason)
            return

        result = run_plan(plan, rows)
        self.assertEqual(result.metric["kind"], expected["kind"])
        self.assertAlmostEqual(result.comparison["delta"],
                               expected["expect_delta"], places=PLACES)
        self.assertAlmostEqual(result.comparison["baseline"],
                               expected["expect_baseline"], places=PLACES)
        self.assertAlmostEqual(result.comparison["current"],
                               expected["expect_current"], places=PLACES)
        if expected["expect_relative_change"] is None:
            self.assertIsNone(result.comparison["relative_change"], case.case_id)
        else:
            self.assertAlmostEqual(result.comparison["relative_change"],
                                   expected["expect_relative_change"], places=PLACES)

        seen = []
        for breakdown in result.breakdowns:
            if not breakdown.dimensions:
                continue
            name = ",".join(breakdown.dimensions)
            seen.append(name)
            if name in expected["expect_omitted_breakdowns"]:
                self.assertEqual(breakdown.status, STATUS_OMITTED, name)
                self.assertEqual(breakdown.reason, "cross_cell_limit_exceeded")
                continue
            self.assertEqual(breakdown.status, STATUS_OK, name)
            self._check_breakdown(case, name, breakdown, expected["breakdowns"][name])
        self.assertEqual(sorted(seen),
                         sorted(list(expected["breakdowns"])
                                + expected["expect_omitted_breakdowns"]))

    def _check_breakdown(self, case, name, breakdown, want):
        where = "%s %s" % (case.case_id, name)
        self.assertEqual(len(breakdown.groups), len(want["groups"]), where)

        for key, value in want["expect_totals"].items():
            self.assertAlmostEqual(breakdown.totals[key], value, places=PLACES,
                                   msg="%s %s" % (where, key))
        for flag, value in want["expect_flags"].items():
            self.assertEqual(breakdown.flags[flag], value,
                             "%s %s" % (where, flag))
        self.assertEqual(
            [_label(breakdown, g) for g in breakdown.ranking["groups"]],
            want["expect_ranking"], where)
        self.assertEqual(breakdown.ranking["by"], case.rank_by, where)

        for group in breakdown.groups:
            label = _label(breakdown, group.key)
            entry = want["groups"][label]
            self.assertAlmostEqual(group.net_contribution,
                                   entry["expect_net_contribution"],
                                   places=PLACES, msg="%s %s net" % (where, label))
            self.assertEqual(group.comparable, entry["expect_comparable"],
                             "%s %s comparable" % (where, label))
            if "expect_group_delta" in entry:
                self.assertEqual(group.group_delta, entry["expect_group_delta"],
                                 "%s %s group_delta" % (where, label))
            for field in ("rate_effect", "mix_effect", "contribution_share"):
                if "expect_" + field not in entry:
                    continue
                value = entry["expect_" + field]
                got = getattr(group, field)
                if value is None:
                    self.assertIsNone(got, "%s %s %s" % (where, label, field))
                else:
                    self.assertAlmostEqual(got, value, places=PLACES,
                                           msg="%s %s %s" % (where, label, field))
            if group.rate_effect is not None:
                self.assertAlmostEqual(
                    group.rate_effect + group.mix_effect, group.net_contribution,
                    places=PLACES, msg="%s %s split" % (where, label))

        non_comparable = sorted(
            _label(breakdown, key) for key in breakdown.non_comparable_groups)
        self.assertEqual(
            non_comparable,
            sorted(label for label, entry in want["groups"].items()
                   if not entry["expect_comparable"]), where)


if __name__ == "__main__":
    unittest.main()
