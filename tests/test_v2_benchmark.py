"""사례 벤치마크. 기대값은 `bia.analysis` 를 전혀 모르는 oracle 에서 온다."""
from __future__ import annotations

import ast
import dataclasses
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from fractions import Fraction

from bia.analysis.compiler import compile_request
from bia.analysis.decompose import snap_share_boundary
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

# 검사 루프가 oracle 자신의 키를 순회하면 oracle 이 내보내지 않은 항목은 조용히
# 검사에서 빠진다 — 적게 내보내는 회귀가 통과한다. 기대하는 키 집합을 여기 적어 두고
# oracle 산출이 그 집합을 담고 있는지 먼저 단언한다.
# 기대 집합은 kind 에 따라 갈린다. `composition_dominant` 와 `simpson_strict` 는
# rate/mix 분해가 있어야 정의되는 비율 전용 개념이라 합 분해에는 **없어야** 한다 —
# False 로 실려 있으면 "검사했고 아니다" 라는 거짓 신호다.
RATIO_ONLY_FLAGS = frozenset(("composition_dominant", "simpson_strict"))
EXPECTED_FLAGS = {
    "additive": frozenset(("decomposition_complete", "heavy_cancellation",
                           "suppress_top_contributor")),
    "ratio": frozenset(("decomposition_complete", "composition_dominant",
                        "simpson_strict", "heavy_cancellation",
                        "suppress_top_contributor")),
}
EXPECTED_TOTALS = {
    "additive": frozenset(("gross_movement",)),
    "ratio": frozenset(("total_rate_effect", "total_mix_effect",
                        "entry_exit_effect", "gross_movement")),
}
# 비교 불가 그룹에는 rate/mix 가 없다(production 이 None 으로 두는 자리다).
EXPECTED_GROUP_FIELDS = {
    ("additive", True): frozenset(("expect_net_contribution", "expect_comparable",
                                   "expect_group_delta")),
    ("ratio", True): frozenset(("expect_net_contribution", "expect_comparable",
                                "expect_rate_effect", "expect_mix_effect",
                                "expect_contribution_share")),
    ("ratio", False): frozenset(("expect_net_contribution", "expect_comparable",
                                 "expect_contribution_share")),
}


def _label(breakdown, group_key):
    return "|".join(group_key[d] for d in breakdown.dimensions)


def _request(case):
    return AnalysisRequest(
        domain=case.domain, metric=case.metric, breakdowns=case.breakdowns,
        comparison=PeriodComparison(current=Period.of(*case.current),
                                    baseline=Period.of(*case.baseline)),
        rank_by=case.rank_by,
    )


def _run_engine(case):
    """사례를 디스크의 CSV 에서 끝까지 실행한다. 거부 사례에는 쓰지 않는다."""
    spec = get_domain(case.domain)
    metric = spec.metrics[case.metric]
    plan = compile_request(_request(case))
    rows = load_observations(
        os.path.join(V2_ROOT, case.case_id, "metrics.csv"), spec, metric.columns)
    return run_plan(plan, rows)


class BenchmarkInventoryTests(unittest.TestCase):
    # 현재 사례 수. 사례를 **의도적으로** 늘릴 때만 이 숫자를 함께 올린다.
    # 부등식(`>= 18`)으로 두면 사례가 조용히 사라지는 것을 못 잡는다 —
    # `generate.py` 의 `case_count` 가 `len(CASES)` 라서 비교 양변이 같은 소스에서
    # 나오고, 사례를 지우고 재생성하면 양쪽이 같이 줄어 통과한다.
    EXPECTED_CASE_COUNT = 26

    def test_cases_have_unique_ids_and_match_the_oracle(self):
        ids = [case.case_id for case in CASES]
        self.assertEqual(len(set(ids)), len(ids))
        self.assertEqual(len(ids), self.EXPECTED_CASE_COUNT)
        self.assertEqual(sorted(ORACLE["cases"]), sorted(ids))
        self.assertEqual(ORACLE["case_count"], len(ids))

    def test_every_case_has_its_own_hand_checked_note(self):
        """`notes` 는 사례의 손 검산 기록이다. 비어있지 않다는 것만으로는 부족하다.

        실제로 틀릴 수 있는 것 둘을 본다 — 검산이 아니라 자리표시자를 넣는 것,
        그리고 새 사례를 만들 때 남의 notes 를 복사하고 숫자를 안 고치는 것.
        """
        seen = {}
        for case in CASES:
            with self.subTest(case=case.case_id):
                note = case.notes.strip()
                self.assertGreaterEqual(len(note), 40, case.case_id)
                if not case.expect_refused:
                    # 거부 사례에는 검산할 수치가 없다. 값을 내는 사례에는 있다.
                    self.assertTrue(any(ch.isdigit() for ch in note),
                                    "%s: 숫자가 없는 notes 는 손 검산 기록이 아니다"
                                    % case.case_id)
                self.assertNotIn(note, seen,
                                 "%s 의 notes 가 %s 와 똑같다"
                                 % (case.case_id, seen.get(note)))
                seen[note] = case.case_id


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

    def _breakdowns(self, kind="ratio"):
        for case_id, entry in sorted(ORACLE["cases"].items()):
            if kind is not None and entry.get("kind") != kind:
                continue
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

    def test_additive_breakdowns_never_carry_the_ratio_only_flags(self):
        """합 분해는 두 플래그를 **부재**로 말해야 한다. oracle 과 엔진 양쪽에서 본다."""
        oracle_seen = []
        for case_id, name, breakdown in self._breakdowns(kind="additive"):
            oracle_seen.append("%s/%s" % (case_id, name))
            self.assertFalse(
                RATIO_ONLY_FLAGS & frozenset(breakdown["expect_flags"]),
                "%s/%s: oracle 이 비율 전용 플래그를 합 분해에 실었다"
                % (case_id, name))
        self.assertTrue(oracle_seen, "합 분해가 하나도 없다 — 검사가 공허하다")

        engine_seen = []
        for case in CASES:
            if case.expect_refused or ORACLE["cases"][case.case_id]["kind"] != "additive":
                continue
            for breakdown in _run_engine(case).breakdowns:
                if breakdown.status != STATUS_OK:
                    continue
                engine_seen.append(case.case_id)
                self.assertFalse(
                    RATIO_ONLY_FLAGS & frozenset(breakdown.flags),
                    "%s/%s: 엔진이 비율 전용 플래그를 합 분해에 실었다"
                    % (case.case_id, ",".join(breakdown.dimensions)))
                self.assertEqual(
                    frozenset(breakdown.flags), EXPECTED_FLAGS["additive"],
                    "%s/%s" % (case.case_id, ",".join(breakdown.dimensions)))
        self.assertTrue(engine_seen, "엔진 산출에 합 분해가 하나도 없다")

    def test_the_engine_flags_satisfy_the_same_implication(self):
        """oracle 플래그만 보면 oracle 의 정리를 확인할 뿐이다.

        함의는 `decompose.py` 의 정의에서 따라 나오는 것이므로 **엔진이 낸**
        플래그에서도 성립해야 한다. 여기서 도는 것은 실제 실행 결과다.
        """
        witnesses = []
        for case in CASES:
            if case.expect_refused:
                continue
            result = _run_engine(case)
            for breakdown in result.breakdowns:
                if breakdown.status != STATUS_OK or not breakdown.dimensions:
                    continue
                name = ",".join(breakdown.dimensions)
                if not breakdown.flags.get("simpson_strict"):
                    continue
                witnesses.append("%s/%s" % (case.case_id, name))
                self.assertTrue(
                    breakdown.flags["composition_dominant"],
                    "%s/%s: 엔진이 simpson_strict 만 참으로 냈다"
                    % (case.case_id, name))
        self.assertTrue(witnesses,
                        "엔진 산출에 simpson_strict 인 분해가 하나도 없다 — "
                        "함의가 공허하다")


class OracleFailClosedTests(unittest.TestCase):
    """oracle 이 모르는 것을 만나면 답을 내지 말고 멈춰야 한다."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="v2bench-")
        self.addCleanup(shutil.rmtree, self.tmpdir)

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

    def test_conflicting_rows_in_a_non_refused_case_stop_the_oracle(self):
        # `dedupe` 의 충돌 가드는 현재 사례로는 발동하지 않는다 — 충돌 사례(C12)가
        # 곧 거부 사례이기 때문이다. 발동하는 입력을 만들어 확인한다.
        case = dataclasses.replace(CASES_BY_ID["C12"], expect_refused=None)
        with self.assertRaises(generate.OracleError) as caught:
            generate.case_oracle(case)
        self.assertIn("충돌", str(caught.exception))

    def test_a_source_csv_with_a_wrong_header_stops_the_oracle(self):
        path = os.path.join(self.tmpdir, "wrong.csv")
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write("day,product,count\n2026-06-01,a,1\n")
        case = dataclasses.replace(CASES_BY_ID["C16"], source_csv=path)
        with self.assertRaises(generate.OracleError) as caught:
            generate.case_rows(case)
        self.assertIn("complaint_type", str(caught.exception))

    def test_a_missing_source_csv_stops_the_oracle(self):
        case = dataclasses.replace(CASES_BY_ID["C16"],
                                   source_csv="data/scenarios/S404/metrics.csv")
        with self.assertRaises(generate.OracleError) as caught:
            generate.case_rows(case)
        self.assertIn("source_csv", str(caught.exception))

    def test_a_boolean_golden_that_is_not_a_flag_stops_the_oracle(self):
        case = dataclasses.replace(CASES_BY_ID["C02"], golden={"not_a_flag": True})
        entry = {"breakdowns": {"channel": {"expect_flags": {}}}}
        with self.assertRaises(generate.OracleError) as caught:
            generate._apply_golden(case, entry, {"not_a_flag": True})
        self.assertIn("not_a_flag", str(caught.exception))

    def test_ranks_that_differ_inside_the_production_tolerance_stop_the_oracle(self):
        # production 의 RANK 는 `|a-b| <= FLOAT_TOL` 을 동률로 보고 tie-break 을
        # 쓴다. oracle 은 정확 비교라 값 순서를 쓴다. 그 띠 안의 차이는 두 판정을
        # 가르므로 계산하지 않는다. 정확히 같은 값(진짜 동률)은 통과해야 한다.
        inside = generate.FLOAT_TOL / 2
        entries = [(("paid",), {"net_contribution": Fraction(0)}),
                   (("organic",), {"net_contribution": inside})]
        with self.assertRaises(generate.OracleError) as caught:
            generate._rank(entries, ("channel",), "net_contribution")
        self.assertIn("tolerance band", str(caught.exception))
        tied = [(("paid",), {"net_contribution": Fraction(1, 40)}),
                (("organic",), {"net_contribution": Fraction(1, 40)})]
        self.assertEqual(generate._rank(tied, ("channel",), "net_contribution"),
                         ["organic", "paid"])

    def test_a_numeric_golden_key_that_matches_nothing_stops_the_oracle(self):
        case = dataclasses.replace(CASES_BY_ID["C01"], golden={"bogus_total": 1})
        entry = {"breakdowns": {"channel": {"expect_totals": {}}}}
        with self.assertRaises(generate.OracleError) as caught:
            generate._apply_golden(case, entry, {"bogus_total": Fraction(1)})
        self.assertIn("bogus_total", str(caught.exception))


class ShareBoundaryAgreementTests(unittest.TestCase):
    """share 경계 규칙은 oracle(정확 유리수)과 엔진(float)이 같이 진술해야 한다.

    oracle 만 정확 비교를 하면 경계에 정확히 걸린 사례에서 두 판정이 갈리고, 그
    불일치는 엔진 결함처럼 보인다(C15 의 paid 가 실제로 그 자리에 있다).
    """

    def test_both_sides_keep_or_drop_the_same_candidates(self):
        tol = generate.FLOAT_TOL
        probes = (Fraction(0), tol / 2, -tol / 2, Fraction(1, 4), Fraction(1),
                  Fraction(1) - tol / 2, Fraction(1) + tol / 2,
                  Fraction(3, 2), Fraction(-1, 4))
        for exact in probes:
            with self.subTest(candidate=exact):
                oracle_value = generate._snap_share_boundary(exact)
                engine_value = snap_share_boundary(float(exact))
                self.assertEqual(0 < oracle_value <= 1, 0 < engine_value <= 1)
                if 0 < oracle_value <= 1:
                    self.assertAlmostEqual(float(oracle_value), engine_value,
                                           places=PLACES)


class BenchmarkTests(unittest.TestCase):
    def test_every_case_matches_its_oracle(self):
        for case in CASES:
            with self.subTest(case=case.case_id):
                self._run_case(case)

    def _run_case(self, case):
        expected = ORACLE["cases"][case.case_id]
        spec = get_domain(case.domain)
        metric = spec.metrics[case.metric]
        request = _request(case)

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
                # "생략됐다" 만이 아니라 "몇 개를 보고 생략했는가" 까지 본다.
                # 이 값이 없으면 한도를 잘못 세고도 생략만 맞으면 통과한다.
                self.assertEqual(
                    breakdown.observed_cells,
                    expected["expect_omitted_observed_cells"][name], name)
                continue
            self.assertEqual(breakdown.status, STATUS_OK, name)
            self._check_breakdown(case, name, breakdown,
                                  expected["breakdowns"][name], expected["kind"])
        self.assertEqual(sorted(seen),
                         sorted(list(expected["breakdowns"])
                                + expected["expect_omitted_breakdowns"]))

    def _check_breakdown(self, case, name, breakdown, want, kind):
        where = "%s %s" % (case.case_id, name)
        self.assertEqual(len(breakdown.groups), len(want["groups"]), where)
        self._assert_oracle_is_complete(where, want, kind)
        self._compare_breakdown(case, name, breakdown, want)

    def _assert_oracle_is_complete(self, where, want, kind):
        """oracle 이 기대 키를 **전부** 내보냈는지 먼저 본다.

        아래의 대조 루프들은 oracle 의 키를 순회한다. oracle 이 어떤 total 이나 flag 를
        빼먹으면 그 항목은 검사에서 조용히 사라지고, 벤치마크는 줄어든 산출을 통과시킨다.
        기대 집합은 테스트 쪽에 적혀 있으므로 oracle 과 같이 줄어들지 않는다.
        """
        self.assertEqual(frozenset(want["expect_flags"]), EXPECTED_FLAGS[kind], where)
        self.assertEqual(frozenset(want["expect_totals"]), EXPECTED_TOTALS[kind],
                         where)
        self.assertIn("expect_ranking", want, where)
        self.assertEqual(sorted(want["expect_ranking"]), sorted(want["groups"]),
                         where)
        for label, entry in want["groups"].items():
            self.assertEqual(
                frozenset(entry),
                EXPECTED_GROUP_FIELDS[(kind, bool(entry.get("expect_comparable")))],
                "%s %s" % (where, label))

    def _compare_breakdown(self, case, name, breakdown, want):
        where = "%s %s" % (case.case_id, name)
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
