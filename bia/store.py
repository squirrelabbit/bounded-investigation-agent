"""Loading the delivered data. Runtime must never see the oracle manifest."""
from __future__ import annotations

import csv
import json
import os
from typing import Dict, List, Tuple

from .types import AnalysisIntent, MetricRow, Period, Ticket, parse_day

ORACLE_FILENAME = "oracle.json"


class OracleAccessError(RuntimeError):
    """Raised if runtime code ever tries to open the evaluation ground truth."""


def guard_not_oracle(path: str) -> None:
    """Refuse any path that resolves to the evaluation ground truth.

    Resolved with realpath and case-folded, so a symlink, a differently-cased
    parent directory or a `..` traversal cannot walk around the check.
    """
    resolved = os.path.realpath(os.path.abspath(path)).lower()
    parts = resolved.split(os.sep)
    if parts[-1] == ORACLE_FILENAME or "oracle" in parts[:-1]:
        raise OracleAccessError("runtime code must not read the oracle manifest: %s" % path)


_guard_not_oracle = guard_not_oracle


def load_metric_rows(path: str) -> List[MetricRow]:
    _guard_not_oracle(path)
    rows: List[MetricRow] = []
    with open(path, "r", encoding="utf-8", newline="") as handle:
        for record in csv.DictReader(handle):
            rows.append(
                MetricRow(
                    day=parse_day(record["day"]),
                    product=record["product"],
                    complaint_type=record["complaint_type"],
                    count=int(record["count"]),
                )
            )
    return rows


def load_tickets(path: str) -> List[Ticket]:
    _guard_not_oracle(path)
    tickets: List[Ticket] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            tickets.append(
                Ticket(
                    ticket_id=record["ticket_id"],
                    day=parse_day(record["day"]),
                    product=record["product"],
                    complaint_type=record["complaint_type"],
                    text=record["text"],
                    source=record["source"],
                )
            )
    return tickets


def load_intent(path: str) -> Tuple[AnalysisIntent, Dict[str, object]]:
    _guard_not_oracle(path)
    with open(path, "r", encoding="utf-8") as handle:
        record = json.load(handle)
    raw = record["intent"]
    intent = AnalysisIntent(
        current_period=Period.of(raw["current_period"]["start"], raw["current_period"]["end"]),
        baseline_period=Period.of(raw["baseline_period"]["start"], raw["baseline_period"]["end"]),
        metric=raw["metric"],
        breakdowns=tuple(raw["breakdowns"]),
        evidence_source=raw["evidence_source"],
        claim_policy=raw["claim_policy"],
    )
    return intent, record


def load_scenario(scenario_dir: str) -> Tuple[AnalysisIntent, List[MetricRow], List[Ticket], Dict[str, object]]:
    intent, meta = load_intent(os.path.join(scenario_dir, "scenario.json"))
    rows = load_metric_rows(os.path.join(scenario_dir, "metrics.csv"))
    tickets = load_tickets(os.path.join(scenario_dir, "tickets.jsonl"))
    return intent, rows, tickets, meta
