from __future__ import annotations

import datetime as dt
import os
import tempfile
import unittest

from bia.analysis.errors import SpecError
from bia.analysis.frame import Observation, load_observations, observation_key
from bia.analysis.spec import DomainSpec, MetricSpec

DOMAIN = DomainSpec(
    name="_frametest",
    grain=("day", "channel", "device"),
    dimensions=("channel", "device"),
    metrics={"orders": MetricSpec(name="orders", kind="additive", value="orders")},
)


class ObservationTests(unittest.TestCase):
    def test_keys_are_hashable_and_ordered(self):
        obs = Observation(
            day=dt.date(2026, 7, 1),
            keys=(("channel", "paid"), ("device", "mobile")),
            measures=(("orders", 5),),
        )
        self.assertEqual(observation_key(obs, ("day", "channel", "device")),
                         ("2026-07-01", "paid", "mobile"))
        self.assertIsInstance(hash(obs), int)

    def test_key_follows_the_requested_grain_order(self):
        obs = Observation(
            day=dt.date(2026, 7, 1),
            keys=(("channel", "paid"), ("device", "mobile")),
            measures=(("orders", 5),),
        )
        self.assertEqual(observation_key(obs, ("day", "device")), ("2026-07-01", "mobile"))


class LoadTests(unittest.TestCase):
    def _write(self, text):
        handle = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False,
                                             encoding="utf-8", newline="")
        handle.write(text)
        handle.close()
        return handle.name

    def test_loads_rows_into_observations(self):
        path = self._write(
            "day,channel,device,orders\n2026-07-01,paid,mobile,5\n2026-07-01,paid,desktop,3\n"
        )
        rows = load_observations(path, DOMAIN, ("orders",))
        self.assertEqual(len(rows), 2)
        self.assertEqual(dict(rows[0].measures), {"orders": 5})
        os.unlink(path)

    def test_a_missing_grain_column_is_rejected(self):
        path = self._write("day,channel,orders\n2026-07-01,paid,5\n")
        with self.assertRaises(SpecError):
            load_observations(path, DOMAIN, ("orders",))
        os.unlink(path)

    def test_a_missing_measure_column_is_rejected(self):
        path = self._write("day,channel,device\n2026-07-01,paid,mobile\n")
        with self.assertRaises(SpecError):
            load_observations(path, DOMAIN, ("orders",))
        os.unlink(path)

    def test_a_null_dimension_becomes_the_unknown_bucket(self):
        """drop 하면 partition 이 깨져 가법성 불변식이 무의미해진다."""
        path = self._write("day,channel,device,orders\n2026-07-01,,mobile,5\n")
        rows = load_observations(path, DOMAIN, ("orders",))
        self.assertEqual(dict(rows[0].keys)["channel"], "__UNKNOWN__")
        os.unlink(path)

    def test_a_negative_measure_is_rejected(self):
        path = self._write("day,channel,device,orders\n2026-07-01,paid,mobile,-1\n")
        with self.assertRaises(SpecError):
            load_observations(path, DOMAIN, ("orders",))
        os.unlink(path)
