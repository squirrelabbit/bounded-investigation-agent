"""Small hand-built fixtures for the boundary tests."""
from __future__ import annotations

import datetime as _dt
from typing import List

from bia.types import AnalysisIntent, MetricRow, Period, Ticket

CURRENT = Period.of("2026-07-01", "2026-07-10")
BASELINE = Period.of("2026-06-01", "2026-06-10")

PRODUCTS = ("P-Alpha", "P-Beta")
TYPES = ("delivery_delay", "app_crash")

SPIKE_PRODUCT = "P-Beta"
SPIKE_TYPE = "delivery_delay"

SUPPORTING_TEXT = "The parcel is late and has not arrived yet."
UNSUPPORTING_TEXT = "Everything about the order was perfectly fine."


def intent() -> AnalysisIntent:
    return AnalysisIntent(current_period=CURRENT, baseline_period=BASELINE)


def rows() -> List[MetricRow]:
    out: List[MetricRow] = []
    for period, spike in ((BASELINE, 1), (CURRENT, 6)):
        for day in period.dates():
            for product in PRODUCTS:
                for complaint_type in TYPES:
                    count = spike if (product, complaint_type) == (SPIKE_PRODUCT, SPIKE_TYPE) else 1
                    out.append(MetricRow(day, product, complaint_type, count))
    return out


def tickets(count: int = 10, text: str = SUPPORTING_TEXT, source: str = "web_form") -> List[Ticket]:
    out: List[Ticket] = []
    for index in range(count):
        day = CURRENT.start + _dt.timedelta(days=index % CURRENT.days)
        out.append(
            Ticket(
                ticket_id="T-%04d" % (index + 1),
                day=day,
                product=SPIKE_PRODUCT,
                complaint_type=SPIKE_TYPE,
                text=text,
                source=source,
            )
        )
    return out
