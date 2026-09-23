from __future__ import annotations

import datetime as dt
import unittest

from bia.analysis.frame import Observation
from bia.integrity import dedupe_observations

GRAIN = ("day", "channel", "device")


def obs(channel, device, orders, day=dt.date(2026, 7, 1)):
    return Observation(day=day, keys=(("channel", channel), ("device", device)),
                       measures=(("orders", orders),))


class GrainDuplicateTests(unittest.TestCase):
    def test_finer_grain_rows_are_not_duplicates(self):
        """요청이 channel 만 breakdown 해도 중복 판정은 물리 grain 으로 한다.
        이 구분이 없으면 device 별 정상 행들이 전부 중복으로 터진다."""
        rows = [obs("paid", "mobile", 5), obs("paid", "desktop", 3)]
        clean, duplicates, conflicts = dedupe_observations(rows, GRAIN)
        self.assertEqual(len(clean), 2)
        self.assertEqual(duplicates, 0)
        self.assertEqual(conflicts, [])

    def test_exact_duplicates_at_grain_are_collapsed(self):
        rows = [obs("paid", "mobile", 5), obs("paid", "mobile", 5)]
        clean, duplicates, conflicts = dedupe_observations(rows, GRAIN)
        self.assertEqual(len(clean), 1)
        self.assertEqual(duplicates, 1)
        self.assertEqual(conflicts, [])

    def test_conflicting_duplicates_are_reported_not_resolved(self):
        rows = [obs("paid", "mobile", 5), obs("paid", "mobile", 9)]
        clean, duplicates, conflicts = dedupe_observations(rows, GRAIN)
        self.assertEqual(duplicates, 0)
        self.assertEqual(len(conflicts), 1)
        self.assertIn("paid", conflicts[0])

    def test_output_order_is_stable(self):
        rows = [obs("z", "mobile", 1), obs("a", "desktop", 2)]
        first, _, _ = dedupe_observations(rows, GRAIN)
        second, _, _ = dedupe_observations(list(reversed(rows)), GRAIN)
        self.assertEqual([observation_keys(r) for r in first],
                         [observation_keys(r) for r in second])


def observation_keys(row):
    return (row.day.isoformat(), dict(row.keys))
