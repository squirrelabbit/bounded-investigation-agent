from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import random
import unittest

import bia.domains.complaints as complaints
from bia.adapters.complaints import legacy_views
from bia.analysis.compiler import compile_request
from bia.analysis.frame import Frame
from bia.analysis.qualification import qualify
from bia.analysis.request import AnalysisRequest, PeriodComparison
from bia.integrity import _row_to_observation
from bia.legacy_comparability import decide_comparability, inspect_period
from bia.store import load_scenario
from bia.types import MetricRow, Period

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEED = 20260930
RANDOM_CASES = 3000
# 첫 실행에서 계산해 고정한다(Step 2). 생성 알고리즘은 이 파일의 test_seeded_random_space 가 정본이다.
GENERATED_INPUT_SHA256 = "d1808d44dd9595ac44ebba8e023cc6eb63910453248beab903512c0fed3d700c"


def _legacy(rows, current, baseline):
    _, cur = inspect_period(list(rows), current)
    _, base = inspect_period(list(rows), baseline)
    return cur, base, decide_comparability(cur, base)


def _new(rows, current, baseline):
    plan = compile_request(AnalysisRequest(
        domain=complaints.SPEC.name, metric="complaint_count",
        breakdowns=tuple(complaints.SPEC.dimensions),
        comparison=PeriodComparison(current=current, baseline=baseline)))
    return legacy_views(qualify(plan, Frame.of(_row_to_observation(r) for r in rows)))


def _bytes(views):
    cur, base, comp = views
    return json.dumps([cur.as_dict(), base.as_dict(), comp.as_dict()], ensure_ascii=False)


def _row(day, product="P-A", ctype="delivery_delay", count=1):
    return MetricRow(day=day, product=product, complaint_type=ctype, count=count)


class DualRunTests(unittest.TestCase):
    def assertSame(self, rows, current, baseline, label):
        self.assertEqual(_bytes(_new(rows, current, baseline)),
                         _bytes(_legacy(rows, current, baseline)), label)

    def test_all_bundled_cases(self):
        checked = 0
        for group in ("scenarios", "challenges"):
            base = os.path.join(ROOT, "data", group)
            for cid in sorted(os.listdir(base)):
                d = os.path.join(base, cid)
                if not os.path.isfile(os.path.join(d, "scenario.json")):
                    continue
                intent, rows, _, _ = load_scenario(d)
                self.assertSame(rows, intent.current_period, intent.baseline_period, cid)
                checked += 1
        self.assertEqual(checked, 32)

    def _periods(self, base_days, cur_days):
        b0, c0 = dt.date(2026, 5, 1), dt.date(2026, 7, 1)
        return (Period(c0, c0 + dt.timedelta(cur_days - 1)),
                Period(b0, b0 + dt.timedelta(base_days - 1)))

    def _rows(self, period, offsets, **kw):
        return [_row(period.start + dt.timedelta(o), **kw) for o in offsets]

    def test_boundary_fixtures(self):
        fixtures = []
        for limit in (15, 29, 30):                       # 짝수 반올림 경계 포함
            cur, base = self._periods(limit, limit)
            for run in (1, 6, 7, 8, 13, 14, 15, limit):
                fixtures.append(("limit%d-run%d" % (limit, run), cur, base,
                                 self._rows(base, range(limit)) + self._rows(cur, range(run))))
        cur, base = self._periods(31, 30)                 # 길이가 다른 완전 기간
        fixtures.append(("unequal", cur, base, self._rows(base, range(31)) + self._rows(cur, range(30))))
        cur, base = self._periods(30, 30)
        fixtures.append(("no-overlap", cur, base,
                         self._rows(base, range(0, 30, 2)) + self._rows(cur, range(1, 30, 2))))
        fixtures.append(("empty-current", cur, base, self._rows(base, range(30))))
        fixtures.append(("empty-both", cur, base, []))
        fixtures.append(("multi-run", cur, base,
                         self._rows(base, range(30)) + self._rows(cur, list(range(0, 9)) + list(range(12, 30)))))
        conflict = self._rows(base, range(30)) + self._rows(cur, range(30))
        conflict.append(_row(cur.end, count=99))
        fixtures.append(("conflict-in-window", cur, base, conflict))
        dup = self._rows(base, range(30)) + self._rows(cur, range(30)) + [_row(cur.start)]
        fixtures.append(("exact-duplicate", cur, base, dup))
        for label, cur, base, rows in fixtures:
            self.assertSame(rows, cur, base, label)

    def test_outside_and_order_fixtures(self):
        cur, base = self._periods(30, 30)
        full = self._rows(base, range(30)) + self._rows(cur, range(30))
        gap = dt.date(2026, 6, 15)
        before = base.start - dt.timedelta(1)
        after = cur.end + dt.timedelta(1)
        fixtures = []

        outside_conflict = full + [_row(gap, count=1), _row(gap, count=2)]
        fixtures.append(("outside-conflict-gap", cur, base, outside_conflict))
        fixtures.append(("outside-duplicate-gap", cur, base, full + [_row(gap), _row(gap)]))
        edges = full + [_row(before, count=1), _row(before, count=2), _row(before, count=1),
                        _row(after, count=1), _row(after, count=3), _row(after, count=1)]
        fixtures.append(("outside-edges", cur, base, edges))
        partial = self._rows(base, range(30)) + self._rows(cur, range(20))
        partial_outside = partial + [_row(gap, count=1), _row(gap, count=2),
                                     _row(before, count=1), _row(before, count=5)]
        fixtures.append(("outside-partial", cur, base, partial_outside))
        both = full + [_row(base.start, count=9), _row(cur.end, count=9)]
        fixtures.append(("conflict-both-windows", cur, base, both))
        many = list(full)
        for ctype in ("delivery_delay", "billing", "app_error"):
            for product in ("P-A", "P-B"):
                for period in (base, cur):
                    d = period.start + dt.timedelta(3)
                    many.append(_row(d, product=product, ctype=ctype, count=1))
                    many.append(_row(d, product=product, ctype=ctype, count=4))
        fixtures.append(("many-conflicts", cur, base, many))

        rng = random.Random(SEED + 1)
        for label, rows in (("outside-conflict-gap", outside_conflict),
                            ("outside-partial", partial_outside),
                            ("many-conflicts", many)):
            shuffled = list(rows)
            rng.shuffle(shuffled)
            fixtures.append((label + "-shuffled", cur, base, shuffled))

        self.assertEqual(len(fixtures), 9)
        for label, c, b, rows in fixtures:
            self.assertSame(rows, c, b, label)

    def test_seeded_random_space(self):
        rng = random.Random(SEED)
        generated = []
        for i in range(RANDOM_CASES):
            base_days, cur_days = rng.randint(1, 31), rng.randint(1, 31)
            cur, base = self._periods(base_days, cur_days)
            rows = []
            for period, days in ((base, base_days), (cur, cur_days)):
                for o in range(days):
                    if rng.random() < 0.75:
                        for product in rng.sample(("P-A", "P-B", "P-C"), rng.randint(1, 2)):
                            rows.append(_row(period.start + dt.timedelta(o), product=product,
                                             count=rng.randint(0, 5)))
            if rows and rng.random() < 0.05:
                rows.append(rows[rng.randrange(len(rows))])                     # 완전 중복
            if rows and rng.random() < 0.03:
                r = rows[rng.randrange(len(rows))]
                rows.append(_row(r.day, r.product, r.complaint_type, r.count + 1))  # 충돌
            self.assertSame(rows, cur, base, "random#%d" % i)
            generated.append([base_days, cur_days,
                              [[r.day.isoformat(), r.product, r.complaint_type, r.count] for r in rows]])
        # 생성 알고리즘·시드가 바뀌어 다른 3000 건이 되면 "3000 건 일치" 가 조용히 통과하지 못하게 한다.
        digest = hashlib.sha256(json.dumps(generated).encode("utf-8")).hexdigest()
        self.assertEqual(digest, GENERATED_INPUT_SHA256)


if __name__ == "__main__":
    unittest.main()
