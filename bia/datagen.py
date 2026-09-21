"""Deterministic synthetic data generator for the 24 evaluation scenarios.

`python3 -m bia.datagen` writes, from a fixed seed and with no timestamps:

    data/scenarios/<SID>/scenario.json
    data/scenarios/<SID>/metrics.csv
    data/scenarios/<SID>/tickets.jsonl
    data/oracle/oracle.json

Two consecutive runs produce byte-identical files.

The oracle is the evaluation ground truth and is read ONLY by the evaluator.
It is therefore computed here from what this module *constructed* — the delivered
day offsets, the injected duplicates, the uplifts — and deliberately does NOT
import `bia.integrity` or `bia.metrics`. If the oracle were derived from the code
under evaluation, the evaluation would only prove that the code agrees with
itself. The comparability rule and the top-contributor rule are restated below
rather than reused, so a change in the runtime rule shows up as a failing
scenario instead of silently moving the answer key.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import random
from typing import Dict, List, Optional, Sequence, Tuple

from .lexicon import COMPLAINT_TYPES, PRODUCTS, supports_label
from .scenarios import SCENARIOS, PERIOD_DAYS, ScenarioSpec
from .types import ALLOWED_TICKET_SOURCES, AnalysisIntent, Period, Ticket

SEED_BASE = 1700
GENERATOR_NAME = "bia.datagen"

BASELINE_PERIOD = Period.of("2026-06-01", "2026-06-30")
CURRENT_PERIOD = Period.of("2026-07-01", "2026-07-30")

BASE_MEAN_MIN = 1
BASE_MEAN_MAX = 4
JITTER_MIN = -1
JITTER_MAX = 1

DEFAULT_TICKET_RATIO = 0.30
DEFAULT_DISTRACTOR_RATE = 0.10

DISALLOWED_SOURCE = "legacy_import"
BAD_SOURCE_MODULUS = 5
BAD_SOURCE_HITS = 2  # 2 of every 5 tickets -> exactly 40%

# Restated from the comparability rule, not imported: the shortest aligned
# window that may still be compared.
MIN_WINDOW_DAYS = max(7, int(round(0.5 * PERIOD_DAYS)))

TOP_SHARE_TARGET = 0.8
TOP_MAX = 3

FORBIDDEN_CLAIMS: Tuple[str, ...] = (
    "원인",
    "때문",
    "유발",
    "초래",
    "야기",
    "caused",
    "because of",
    "due to",
    "root cause",
)

MODE_FULL = "full"
MODE_ALIGNED_WINDOW = "aligned_window"
MODE_BLOCKED = "blocked"

STATUS_REPORTED = "reported"
STATUS_ABSTAINED = "abstained"

CELLS: Tuple[Tuple[str, str], ...] = tuple(
    (product, complaint_type) for product in PRODUCTS for complaint_type in COMPLAINT_TYPES
)

# Every sentence contains at least one SUPPORT_TERMS entry of its own type and
# none of any other type; `check_datagen.py` enforces that.
TEXT_TEMPLATES: Dict[str, Tuple[str, ...]] = {
    "delivery_delay": (
        "The order was delayed by several days.",
        "Shipping status has not changed since Monday.",
        "The parcel has not arrived yet.",
        "Dispatch was pushed back twice.",
        "Delivery is late again this week.",
    ),
    "billing_error": (
        "I was charged twice for one month.",
        "The invoice shows a wrong amount.",
        "Billing does not match the selected plan.",
        "A double charge appeared on my statement.",
        "The monthly invoice total looks incorrect.",
    ),
    "app_crash": (
        "The app crashes when opening the main tab.",
        "It freezes right after sign in.",
        "An error screen appears every time.",
        "The client keeps hitting a force close.",
        "The app crash repeats on every launch.",
    ),
    "damaged_item": (
        "The outer box was damaged on all sides.",
        "The casing is cracked near the edge.",
        "The handle was broken off completely.",
        "The metal frame is dented.",
        "The wrapping was torn open.",
    ),
    "support_wait": (
        "Still waiting for an agent to pick up.",
        "No reply after three follow ups.",
        "I was left on hold for a long stretch.",
        "The response time keeps getting worse.",
        "My request is still unanswered.",
    ),
}

_NEXT_TYPE: Dict[str, str] = {
    complaint_type: COMPLAINT_TYPES[(index + 1) % len(COMPLAINT_TYPES)]
    for index, complaint_type in enumerate(COMPLAINT_TYPES)
}


def repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------
# metric row construction
# ---------------------------------------------------------------------------


def _draw_grid(rng: random.Random) -> Dict[Tuple[int, Tuple[str, str]], int]:
    grid: Dict[Tuple[int, Tuple[str, str]], int] = {}
    for offset in range(PERIOD_DAYS):
        for cell in CELLS:
            grid[(offset, cell)] = rng.randint(JITTER_MIN, JITTER_MAX)
    return grid


def _counts(spec: ScenarioSpec, index: int):
    """Return (baseline_counts, current_counts) keyed by (offset, cell)."""
    rng = random.Random(SEED_BASE + index)

    base_mean: Dict[Tuple[str, str], int] = {}
    for cell in CELLS:
        base_mean[cell] = rng.randint(BASE_MEAN_MIN, BASE_MEAN_MAX)
    # Overrides are applied after drawing so the jitter stream stays identical
    # whether or not a scenario pins a base level.
    for product, complaint_type, mean in spec.base_mean_override:
        base_mean[(product, complaint_type)] = mean

    baseline_jitter = _draw_grid(rng)
    current_jitter = baseline_jitter if spec.mirror_draws else _draw_grid(rng)

    uplift = spec.uplift_map()
    baseline_counts: Dict[Tuple[int, Tuple[str, str]], int] = {}
    current_counts: Dict[Tuple[int, Tuple[str, str]], int] = {}
    for offset in range(PERIOD_DAYS):
        for cell in CELLS:
            mean = base_mean[cell]
            baseline_counts[(offset, cell)] = max(0, mean + baseline_jitter[(offset, cell)])
            current_counts[(offset, cell)] = max(
                0, mean + current_jitter[(offset, cell)] + uplift.get(cell, 0)
            )
    return baseline_counts, current_counts


def _day(period: Period, offset: int) -> _dt.date:
    return period.start + _dt.timedelta(days=offset)


def _rows(period: Period, counts, offsets: Sequence[int]) -> List[Dict[str, object]]:
    """Rows sorted by (day, product, complaint_type)."""
    rows: List[Dict[str, object]] = []
    for offset in sorted(offsets):
        day = _day(period, offset).isoformat()
        for product in PRODUCTS:
            for complaint_type in COMPLAINT_TYPES:
                rows.append(
                    {
                        "day": day,
                        "product": product,
                        "complaint_type": complaint_type,
                        "count": counts[(offset, (product, complaint_type))],
                        "offset": offset,
                    }
                )
    rows.sort(key=lambda r: (r["day"], r["product"], r["complaint_type"]))
    return rows


# ---------------------------------------------------------------------------
# ticket construction
# ---------------------------------------------------------------------------


def _ticket_text(complaint_type: str, product: str, variant: int, distractor: bool) -> str:
    """Synthetic English sentence. A distractor borrows another type's wording."""
    source_type = _NEXT_TYPE[complaint_type] if distractor else complaint_type
    templates = TEXT_TEMPLATES[source_type]
    sentence = templates[variant % len(templates)]
    return "[synthetic] %s (product %s)" % (sentence, product)


def _lookup(overrides, cell, default):
    for product, complaint_type, value in overrides:
        if (product, complaint_type) == cell:
            return value
    return default


def _build_tickets(
    spec: ScenarioSpec,
    baseline_rows: List[Dict[str, object]],
    current_rows: List[Dict[str, object]],
) -> List[Ticket]:
    caps = {(p, c): n for p, c, n in spec.current_cell_cap}
    bad_source = set(spec.bad_source_cells)

    tickets: List[Ticket] = []
    for period_name, rows in (("baseline", baseline_rows), ("current", current_rows)):
        # Per (period, cell) counter: rows arrive in day order, so this indexes
        # a cell's tickets stably across the period.
        seen: Dict[Tuple[str, str], int] = {}
        for row in rows:
            cell = (str(row["product"]), str(row["complaint_type"]))
            ratio = _lookup(spec.ticket_ratio_override, cell, DEFAULT_TICKET_RATIO)
            if period_name == "current":
                rate = _lookup(spec.distractor_rate_current, cell, DEFAULT_DISTRACTOR_RATE)
            else:
                rate = DEFAULT_DISTRACTOR_RATE
            distractor_slots = int(round(rate * 10))
            cap = caps.get(cell) if period_name == "current" else None

            for _ in range(int(round(int(row["count"]) * ratio))):
                index_in_cell = seen.get(cell, 0)
                if cap is not None and index_in_cell >= cap:
                    break
                seen[cell] = index_in_cell + 1

                distractor = (index_in_cell % 10) < distractor_slots
                text = _ticket_text(cell[1], cell[0], index_in_cell, distractor)
                if (
                    period_name == "current"
                    and cell in bad_source
                    and (index_in_cell % BAD_SOURCE_MODULUS) < BAD_SOURCE_HITS
                ):
                    source = DISALLOWED_SOURCE
                else:
                    source = ALLOWED_TICKET_SOURCES[len(tickets) % len(ALLOWED_TICKET_SOURCES)]

                tickets.append(
                    Ticket(
                        ticket_id="T-%06d" % (len(tickets) + 1),
                        day=_dt.date.fromisoformat(str(row["day"])),
                        product=cell[0],
                        complaint_type=cell[1],
                        text=text,
                        source=source,
                    )
                )
    return tickets


# ---------------------------------------------------------------------------
# oracle: comparability, totals, contributors
# ---------------------------------------------------------------------------


def longest_common_run(a: Sequence[int], b: Sequence[int], limit: int) -> Tuple[int, int]:
    """Longest contiguous run of day-offsets present in both, within 0..limit-1."""
    common = sorted(set(a) & set(b) & set(range(limit)))
    if not common:
        return (-1, -1)
    best = (common[0], common[0])
    start = common[0]
    prev = common[0]
    for offset in common[1:]:
        if offset != prev + 1:
            if prev - start > best[1] - best[0]:
                best = (start, prev)
            start = offset
        prev = offset
    if prev - start > best[1] - best[0]:
        best = (start, prev)
    return best


def _comparability(
    current_offsets: Sequence[int], baseline_offsets: Sequence[int], has_conflicts: bool
):
    """Return (mode, current_window, baseline_window, run_length)."""
    if has_conflicts:
        return MODE_BLOCKED, None, None, 0
    full = set(range(PERIOD_DAYS))
    if set(current_offsets) == full and set(baseline_offsets) == full:
        return MODE_FULL, CURRENT_PERIOD, BASELINE_PERIOD, PERIOD_DAYS
    start, end = longest_common_run(current_offsets, baseline_offsets, PERIOD_DAYS)
    if start < 0:
        return MODE_BLOCKED, None, None, 0
    length = end - start + 1
    if length < MIN_WINDOW_DAYS:
        return MODE_BLOCKED, None, None, length
    return (
        MODE_ALIGNED_WINDOW,
        CURRENT_PERIOD.sub(start, end),
        BASELINE_PERIOD.sub(start, end),
        length,
    )


def _window_offsets(window: Period, period: Period) -> Tuple[int, int]:
    return ((window.start - period.start).days, (window.end - period.start).days)


def _sums(counts, offsets: Sequence[int], key_index: Optional[int]) -> Dict[str, int]:
    """Sum counts over the given offsets, grouped by product (0), type (1), or cell."""
    out: Dict[str, int] = {}
    for offset in offsets:
        for cell in CELLS:
            if key_index is None:
                key = cell[0] + "\x1f" + cell[1]
            else:
                key = cell[key_index]
            out[key] = out.get(key, 0) + counts[(offset, cell)]
    return out


def _top_contributors(current: Dict[str, int], baseline: Dict[str, int]) -> List[str]:
    """Positive contributors covering TOP_SHARE_TARGET of the increase, at most TOP_MAX."""
    deltas = []
    for value in sorted(set(current) | set(baseline)):
        delta = current.get(value, 0) - baseline.get(value, 0)
        if delta > 0:
            deltas.append((value, delta))
    total_increase = sum(d for _, d in deltas)
    if not total_increase:
        return []
    deltas.sort(key=lambda item: (-item[1], item[0]))
    picked: List[str] = []
    cumulative = 0.0
    for value, delta in deltas:
        picked.append(value)
        cumulative += round(delta / float(total_increase), 4)
        if cumulative >= TOP_SHARE_TARGET or len(picked) >= TOP_MAX:
            break
    return picked


def _build_oracle(
    spec: ScenarioSpec,
    baseline_counts,
    current_counts,
    current_offsets: Sequence[int],
    tickets: Sequence[Ticket],
) -> Dict[str, object]:
    baseline_offsets = list(range(PERIOD_DAYS))
    has_conflicts = spec.duplicate_conflicting_current > 0
    mode, current_window, baseline_window, _run = _comparability(
        current_offsets, baseline_offsets, has_conflicts
    )

    missing_current = [
        _day(CURRENT_PERIOD, o).isoformat()
        for o in range(PERIOD_DAYS)
        if o not in set(current_offsets)
    ]

    oracle: Dict[str, object] = {
        "scenario_id": spec.scenario_id,
        "label": spec.label,
        "category": spec.category,
        "expect_comparability_mode": mode,
        "expect_integrity": {
            "current_missing_days": missing_current,
            "baseline_missing_days": [],
            # Exact duplicates collapse; conflicting keys are an integrity
            # failure, not a removal, so they are not counted here.
            "duplicate_rows_removed_current": spec.duplicate_exact_current,
            "duplicate_rows_removed_baseline": 0,
            "has_conflicts": has_conflicts,
        },
        "must_defer": spec.must_defer,
        "expect_target_cell_admitted": spec.expect_target_cell_admitted,
        "forbidden_claims": list(FORBIDDEN_CLAIMS),
    }

    if mode == MODE_BLOCKED:
        oracle.update(
            {
                "expect_status": STATUS_ABSTAINED,
                "expect_current_window": None,
                "expect_baseline_window": None,
                "expect_current_total": None,
                "expect_baseline_total": None,
                "expect_delta": None,
                "expect_top_products": [],
                "expect_top_complaint_types": [],
                "evidence_target": None,
                "evidence_target_ticket_ids": [],
                "positive_delta_cells": [],
                "relevant_ticket_ids": [],
                "expect_rejected_source_ids": [],
            }
        )
        return oracle

    assert current_window is not None and baseline_window is not None
    start, end = _window_offsets(current_window, CURRENT_PERIOD)
    window_offsets = list(range(start, end + 1))

    current_total = sum(
        current_counts[(o, cell)] for o in window_offsets for cell in CELLS
    )
    baseline_total = sum(
        baseline_counts[(o, cell)] for o in window_offsets for cell in CELLS
    )
    delta = current_total - baseline_total

    top_products = _top_contributors(
        _sums(current_counts, window_offsets, 0), _sums(baseline_counts, window_offsets, 0)
    )
    top_types = _top_contributors(
        _sums(current_counts, window_offsets, 1), _sums(baseline_counts, window_offsets, 1)
    )

    # Every cell that grew over the comparable window, strongest first. This is
    # the precision denominator for the evaluator: a ticket from any of these
    # cells is on topic, even if it is not from the single largest one.
    positive_cells: List[Dict[str, str]] = []
    if delta > 0:
        cur_cells = _sums(current_counts, window_offsets, None)
        base_cells = _sums(baseline_counts, window_offsets, None)
        ranked = sorted(
            (
                (-(cur_cells[k] - base_cells[k]), k)
                for k in cur_cells
                if cur_cells[k] - base_cells[k] > 0
            )
        )
        for _neg_delta, key in ranked:
            product, complaint_type = key.split("\x1f")
            positive_cells.append({"product": product, "complaint_type": complaint_type})

    # The single largest cell is the recall denominator.
    evidence_target = positive_cells[0] if positive_cells else None
    positive_cell_keys = {(c["product"], c["complaint_type"]) for c in positive_cells}

    target_ids: List[str] = []
    relevant_ids: List[str] = []
    rejected_source_ids: List[str] = []
    for ticket in tickets:
        cell = (ticket.product, ticket.complaint_type)
        if cell not in positive_cell_keys:
            continue
        if not current_window.contains(ticket.day):
            continue
        is_target_cell = evidence_target is not None and cell == (
            evidence_target["product"],
            evidence_target["complaint_type"],
        )
        if ticket.source not in ALLOWED_TICKET_SOURCES:
            if is_target_cell:
                rejected_source_ids.append(ticket.ticket_id)
            continue
        if not supports_label(ticket.text, ticket.complaint_type):
            continue
        relevant_ids.append(ticket.ticket_id)
        if is_target_cell:
            target_ids.append(ticket.ticket_id)

    oracle.update(
        {
            "expect_status": STATUS_REPORTED,
            "expect_current_window": {
                "start": current_window.start.isoformat(),
                "end": current_window.end.isoformat(),
            },
            "expect_baseline_window": {
                "start": baseline_window.start.isoformat(),
                "end": baseline_window.end.isoformat(),
            },
            "expect_current_total": current_total,
            "expect_baseline_total": baseline_total,
            "expect_delta": delta,
            "expect_top_products": top_products,
            "expect_top_complaint_types": top_types,
            "evidence_target": evidence_target,
            "evidence_target_ticket_ids": sorted(target_ids),
            "positive_delta_cells": positive_cells,
            "relevant_ticket_ids": sorted(relevant_ids),
            "expect_rejected_source_ids": sorted(rejected_source_ids),
        }
    )
    return oracle


# ---------------------------------------------------------------------------
# generation and writing
# ---------------------------------------------------------------------------


def build_scenario(spec: ScenarioSpec, index: int):
    """Return (scenario_meta, delivered_rows, tickets, oracle_entry)."""
    baseline_counts, current_counts = _counts(spec, index)
    current_offsets = list(spec.delivered_current_offsets())

    baseline_rows = _rows(BASELINE_PERIOD, baseline_counts, range(PERIOD_DAYS))
    current_rows = _rows(CURRENT_PERIOD, current_counts, current_offsets)

    tickets = _build_tickets(spec, baseline_rows, current_rows)

    # Duplicates are appended after the clean rows, the way a re-run batch would
    # land in a warehouse table. Tickets are generated from the clean rows only.
    delivered = list(baseline_rows) + list(current_rows)
    for row in current_rows[: spec.duplicate_exact_current]:
        delivered.append(dict(row))
    for row in current_rows[: spec.duplicate_conflicting_current]:
        conflicting = dict(row)
        conflicting["count"] = int(row["count"]) + 7
        delivered.append(conflicting)

    # The periods live only inside `intent`, which is the shape bia.store reads.
    # Restating them at the top level would give the same fact two homes.
    intent = AnalysisIntent(current_period=CURRENT_PERIOD, baseline_period=BASELINE_PERIOD)
    intent.validate()

    meta = {
        "scenario_id": spec.scenario_id,
        "label": spec.label,
        "category": spec.category,
        "intent": intent.as_dict(),
        "products": list(PRODUCTS),
        "complaint_types": list(COMPLAINT_TYPES),
        "files": {"metrics": "metrics.csv", "tickets": "tickets.jsonl"},
        "notes": spec.notes,
    }

    oracle_entry = _build_oracle(spec, baseline_counts, current_counts, current_offsets, tickets)
    return meta, delivered, tickets, oracle_entry


def _write_text(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def _json_document(payload) -> str:
    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def _csv_document(rows) -> str:
    lines = ["day,product,complaint_type,count"]
    for row in rows:
        lines.append(
            "%s,%s,%s,%d" % (row["day"], row["product"], row["complaint_type"], int(row["count"]))
        )
    return "\n".join(lines) + "\n"


def _jsonl_document(tickets: Sequence[Ticket]) -> str:
    lines = [
        json.dumps(t.as_dict(), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        for t in tickets
    ]
    return "\n".join(lines) + "\n"


def generate(root: Optional[str] = None) -> Dict[str, object]:
    base = root or repo_root()
    oracle_scenarios: Dict[str, object] = {}

    for index, spec in enumerate(SCENARIOS):
        meta, rows, tickets, oracle_entry = build_scenario(spec, index)
        directory = os.path.join(base, "data", "scenarios", spec.scenario_id)
        _write_text(os.path.join(directory, "scenario.json"), _json_document(meta))
        _write_text(os.path.join(directory, "metrics.csv"), _csv_document(rows))
        _write_text(os.path.join(directory, "tickets.jsonl"), _jsonl_document(tickets))
        oracle_scenarios[spec.scenario_id] = oracle_entry

    oracle = {
        "seed_base": SEED_BASE,
        "generator": GENERATOR_NAME,
        "scenarios": oracle_scenarios,
    }
    _write_text(os.path.join(base, "data", "oracle", "oracle.json"), _json_document(oracle))
    return oracle


def main() -> None:
    oracle = generate()
    scenarios = oracle["scenarios"]
    assert isinstance(scenarios, dict)
    print("wrote %d scenarios + oracle under %s/data" % (len(scenarios), repo_root()))


if __name__ == "__main__":
    main()
