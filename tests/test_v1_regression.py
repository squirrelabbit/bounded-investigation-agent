from __future__ import annotations

import unittest

from bia import metrics as v1_metrics
from bia.adapters.complaints import top_contributor_cells
from bia.analysis.compiler import compile_request
from bia.analysis.engine import run_plan
from bia.analysis.frame import Observation
from bia.analysis.request import AnalysisRequest, PeriodComparison
from bia.domains import complaints as complaints_domain  # noqa: F401  (등록 부작용)
from bia.integrity import decide_comparability, inspect_period
from bia.store import load_scenario

SCENARIOS = ["S%02d" % n for n in range(1, 25)]


def _observations(rows):
    return [Observation(day=r.day,
                        keys=(("complaint_type", r.complaint_type), ("product", r.product)),
                        measures=(("count", r.count),))
            for r in rows]


class StructuredRegressionTests(unittest.TestCase):
    """새 엔진이 v1 의 산술을 한 자리도 바꾸지 않았는지 24개 시나리오에서 확인한다."""

    def test_every_scenario_matches_the_v1_arithmetic(self):
        checked = 0
        for scenario_id in SCENARIOS:
            intent, rows, _tickets, _meta = load_scenario("data/scenarios/%s" % scenario_id)
            current_rows, current_integrity = inspect_period(list(rows), intent.current_period)
            baseline_rows, baseline_integrity = inspect_period(list(rows), intent.baseline_period)
            comparability = decide_comparability(current_integrity, baseline_integrity)
            if not comparability.usable:
                continue

            old = v1_metrics.compute(current_rows + baseline_rows,
                                     comparability.current_window,
                                     comparability.baseline_window)

            plan = compile_request(AnalysisRequest(
                domain="complaints", metric="complaint_count",
                breakdowns=("product", "complaint_type"),
                comparison=PeriodComparison(current=comparability.current_window,
                                            baseline=comparability.baseline_window),
                rank_by="net_contribution",
            ))
            new = run_plan(plan, _observations(current_rows + baseline_rows))

            self.assertEqual(new.comparison["current"], old.current_total, scenario_id)
            self.assertEqual(new.comparison["baseline"], old.baseline_total, scenario_id)
            self.assertEqual(new.comparison["delta"], old.delta, scenario_id)

            by_product = {tuple(sorted(g.key.items())): g.group_delta
                          for g in new.breakdowns[1].groups}
            for group in old.by_product:
                self.assertEqual(by_product[(("product", group.value),)], group.delta,
                                 "%s %s" % (scenario_id, group.value))

            old_cells = [(c.product, c.complaint_type) for c in old.cells if c.delta > 0]
            self.assertEqual(top_contributor_cells(new), old_cells, scenario_id)

            old_cell_deltas = {(c.product, c.complaint_type): c.delta for c in old.cells}
            cross_breakdown = new.breakdowns[3]
            self.assertEqual(cross_breakdown.dimensions, ("product", "complaint_type"),
                             scenario_id)
            self.assertTrue(cross_breakdown.cross, scenario_id)
            new_cell_deltas = {(g.key["product"], g.key["complaint_type"]): g.group_delta
                              for g in cross_breakdown.groups}
            self.assertEqual(new_cell_deltas, old_cell_deltas, scenario_id)
            checked += 1
        self.assertGreaterEqual(checked, 20, "대부분의 시나리오가 비교 가능해야 한다")
