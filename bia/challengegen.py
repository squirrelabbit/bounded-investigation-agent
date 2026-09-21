"""Deterministic synthetic data generator for the 8 v1 challenge cases.

`python3 -m bia.challengegen` writes, from a fixed seed and with no timestamps:

    data/challenges/<CID>/scenario.json
    data/challenges/<CID>/metrics.csv
    data/challenges/<CID>/tickets.jsonl
    data/oracle/challenge_oracle.json

Two consecutive runs produce byte-identical files, on any supported interpreter.

The file layout and schemas are the same as v0's `data/scenarios/<SID>/`, so the
same loader (`bia.store`) reads both. Nothing under `data/scenarios/` and
nothing in `bia.datagen` is touched by this module; v0 is frozen.

The oracle is the evaluation ground truth and is read ONLY by the evaluator. As
in v0 it is computed here from what this module *constructed*, and deliberately
does NOT import `bia.integrity`, `bia.metrics`, `bia.evidence` or
`bia.decision`. The rules it needs — the allowed sources, the label-support
test, the top-contributor rule, the candidate menu the server would offer — are
restated below rather than reused, so a change in the runtime rule shows up as a
failing case instead of silently moving the answer key. Only the shared
vocabulary (`bia.lexicon`, `bia.types`) is imported.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import random
from typing import Dict, List, Optional, Sequence, Set, Tuple

from .challenges import CHALLENGES, PERIOD_DAYS, ChallengeSpec
from .lexicon import COMPLAINT_TYPES, PRODUCTS, supports_label
from .types import ALLOWED_TICKET_SOURCES, AnalysisIntent, Period, Ticket

SEED_BASE = 2700
GENERATOR_NAME = "bia.challengegen"

BASELINE_PERIOD = Period.of("2026-06-01", "2026-06-30")
CURRENT_PERIOD = Period.of("2026-07-01", "2026-07-30")

BASE_MEAN_MIN = 1
BASE_MEAN_MAX = 4
JITTER_MIN = -1
JITTER_MAX = 1

DEFAULT_TICKET_RATIO = 0.30
DEFAULT_DISTRACTOR_RATE = 0.10

DISALLOWED_SOURCE = "legacy_import"

TOP_SHARE_TARGET = 0.8
TOP_MAX = 3

# The server's candidate menu: the two strongest growing cells, plus one
# product-level and one complaint_type-level candidate.
TOP_CELL_CANDIDATES = 2

KIND_CELL = "cell"
KIND_PRODUCT = "product"
KIND_COMPLAINT_TYPE = "complaint_type"

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
STATUS_REPORTED = "reported"

CELLS: Tuple[Tuple[str, str], ...] = tuple(
    (product, complaint_type) for product in PRODUCTS for complaint_type in COMPLAINT_TYPES
)

# Every sentence contains at least one SUPPORT_TERMS entry of its own type and
# none of any other type; `check_challenges.py` enforces that.
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


def _counts(spec: ChallengeSpec, index: int):
    """Return (baseline_counts, current_counts) keyed by (offset, cell)."""
    rng = random.Random(SEED_BASE + index)

    base_mean: Dict[Tuple[str, str], int] = {}
    for cell in CELLS:
        base_mean[cell] = rng.randint(BASE_MEAN_MIN, BASE_MEAN_MAX)
    # Overrides are applied after drawing so the jitter stream stays identical
    # whether or not a case pins a base level.
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


def _rows(period: Period, counts) -> List[Dict[str, object]]:
    """Rows sorted by (day, product, complaint_type). Every day is delivered."""
    rows: List[Dict[str, object]] = []
    for offset in range(PERIOD_DAYS):
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
    spec: ChallengeSpec,
    baseline_rows: List[Dict[str, object]],
    current_rows: List[Dict[str, object]],
) -> List[Ticket]:
    caps = {(p, c): n for p, c, n in spec.current_cell_cap}
    all_bad_source = set(spec.all_bad_source_cells_current)

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
                if period_name == "current" and cell in all_bad_source:
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
# oracle: totals, contributors, candidate menu, useful evidence
# ---------------------------------------------------------------------------


def _sums(counts, key_index: Optional[int]) -> Dict[str, int]:
    """Sum counts over the whole period, grouped by product (0), type (1), or cell."""
    out: Dict[str, int] = {}
    for offset in range(PERIOD_DAYS):
        for cell in CELLS:
            if key_index is None:
                key = cell[0] + "\x1f" + cell[1]
            else:
                key = cell[key_index]
            out[key] = out.get(key, 0) + counts[(offset, cell)]
    return out


def _positive_deltas(current: Dict[str, int], baseline: Dict[str, int]) -> List[Tuple[str, int]]:
    """(value, delta) for every group that grew, strongest first, ties by value."""
    deltas = []
    for value in sorted(set(current) | set(baseline)):
        delta = current.get(value, 0) - baseline.get(value, 0)
        if delta > 0:
            deltas.append((value, delta))
    deltas.sort(key=lambda item: (-item[1], item[0]))
    return deltas


def _top_contributors(current: Dict[str, int], baseline: Dict[str, int]) -> List[str]:
    """Positive contributors covering TOP_SHARE_TARGET of the increase, at most TOP_MAX."""
    deltas = _positive_deltas(current, baseline)
    total_increase = sum(d for _, d in deltas)
    if not total_increase:
        return []
    picked: List[str] = []
    cumulative = 0.0
    for value, delta in deltas:
        picked.append(value)
        cumulative += round(delta / float(total_increase), 4)
        if cumulative >= TOP_SHARE_TARGET or len(picked) >= TOP_MAX:
            break
    return picked


def _filter_matches(ticket: Ticket, product: Optional[str], complaint_type: Optional[str]) -> bool:
    """Restated retrieval condition: window membership is checked by the caller."""
    if product is not None and ticket.product != product:
        return False
    if complaint_type is not None and ticket.complaint_type != complaint_type:
        return False
    return True


def _build_oracle(
    spec: ChallengeSpec,
    baseline_counts,
    current_counts,
    tickets: Sequence[Ticket],
) -> Dict[str, object]:
    # Both periods are delivered complete and without duplicates in every v1
    # case, so the comparison window is the full current period.
    current_total = sum(current_counts[(o, cell)] for o in range(PERIOD_DAYS) for cell in CELLS)
    baseline_total = sum(baseline_counts[(o, cell)] for o in range(PERIOD_DAYS) for cell in CELLS)
    delta = current_total - baseline_total

    cur_products = _sums(current_counts, 0)
    base_products = _sums(baseline_counts, 0)
    cur_types = _sums(current_counts, 1)
    base_types = _sums(baseline_counts, 1)
    cur_cells = _sums(current_counts, None)
    base_cells = _sums(baseline_counts, None)

    top_products = _top_contributors(cur_products, base_products)
    top_types = _top_contributors(cur_types, base_types)

    # Cells that grew, strongest first, ties broken by product then complaint_type.
    cell_deltas: List[Tuple[Tuple[str, str], int]] = []
    for key, current_value in cur_cells.items():
        cell_delta = current_value - base_cells.get(key, 0)
        if cell_delta > 0:
            product, complaint_type = key.split("\x1f")
            cell_deltas.append(((product, complaint_type), cell_delta))
    cell_deltas.sort(key=lambda item: (-item[1], item[0][0], item[0][1]))
    positive_cell_keys: Set[Tuple[str, str]] = {cell for cell, _ in cell_deltas}

    # Useful evidence: inside the comparison window, allowed source, text that
    # supports its own label, and sitting in a cell that actually grew.
    window_tickets = [t for t in tickets if CURRENT_PERIOD.contains(t.day)]
    useful_ids: List[str] = []
    useful_id_set: Set[str] = set()
    for ticket in window_tickets:
        if (ticket.product, ticket.complaint_type) not in positive_cell_keys:
            continue
        if ticket.source not in ALLOWED_TICKET_SOURCES:
            continue
        if not supports_label(ticket.text, ticket.complaint_type):
            continue
        useful_ids.append(ticket.ticket_id)
        useful_id_set.add(ticket.ticket_id)
    useful_ids.sort()

    # The candidate menu the server would offer for this case.
    wanted: List[Tuple[str, Optional[str], Optional[str]]] = []
    for cell, _cell_delta in cell_deltas[:TOP_CELL_CANDIDATES]:
        wanted.append((KIND_CELL, cell[0], cell[1]))
    positive_products = _positive_deltas(cur_products, base_products)
    if positive_products:
        wanted.append((KIND_PRODUCT, positive_products[0][0], None))
    positive_types = _positive_deltas(cur_types, base_types)
    if positive_types:
        wanted.append((KIND_COMPLAINT_TYPE, None, positive_types[0][0]))

    offerable: List[Dict[str, object]] = []
    seen_filters: Set[Tuple[Optional[str], Optional[str]]] = set()
    filter_useful_sets: List[Set[str]] = []
    for kind, product, complaint_type in wanted:
        identity = (product, complaint_type)
        if identity in seen_filters:
            continue
        seen_filters.add(identity)
        matched = [t for t in window_tickets if _filter_matches(t, product, complaint_type)]
        hits = {t.ticket_id for t in matched if t.ticket_id in useful_id_set}
        offerable.append(
            {
                "kind": kind,
                "product": product,
                "complaint_type": complaint_type,
                "pool": len(matched),
                "useful": len(hits),
            }
        )
        filter_useful_sets.append(hits)

    best_single = max((int(entry["useful"]) for entry in offerable), default=0)

    best_two = best_single
    best_two_example: List[int] = []
    for i in range(len(offerable)):
        for j in range(i + 1, len(offerable)):
            union = len(filter_useful_sets[i] | filter_useful_sets[j])
            if union > best_two:
                best_two = union
                best_two_example = [i, j]
    if not best_two_example:
        # No pair beats the best single filter; the witness is that filter alone.
        for i, entry in enumerate(offerable):
            if int(entry["useful"]) == best_single:
                best_two_example = [i]
                break

    # Unavoidable wasted retrievals: the fewest zero-yield calls among the
    # choices that actually reach the ceiling. Reaching 0 needs no call at all.
    if best_two == 0:
        wasted_min = 0
    else:
        options: List[List[int]] = []
        for i, entry in enumerate(offerable):
            if int(entry["useful"]) == best_two:
                options.append([i])
        for i in range(len(offerable)):
            for j in range(i + 1, len(offerable)):
                if len(filter_useful_sets[i] | filter_useful_sets[j]) == best_two:
                    options.append([i, j])
        wasted_min = min(
            sum(1 for k in option if int(offerable[k]["useful"]) == 0) for option in options
        )

    return {
        "challenge_id": spec.challenge_id,
        "label": spec.label,
        "category": spec.category,
        "expect_status": STATUS_REPORTED,
        "expect_comparability_mode": MODE_FULL,
        "expect_current_window": {
            "start": CURRENT_PERIOD.start.isoformat(),
            "end": CURRENT_PERIOD.end.isoformat(),
        },
        "expect_baseline_window": {
            "start": BASELINE_PERIOD.start.isoformat(),
            "end": BASELINE_PERIOD.end.isoformat(),
        },
        "expect_current_total": current_total,
        "expect_baseline_total": baseline_total,
        "expect_delta": delta,
        "expect_top_products": top_products,
        "expect_top_complaint_types": top_types,
        "forbidden_claims": list(FORBIDDEN_CLAIMS),
        "useful_ticket_ids": useful_ids,
        "offerable_filters": offerable,
        "best_single_filter_yield": best_single,
        "best_two_filter_yield": best_two,
        "best_two_filter_example": [
            {
                "kind": offerable[k]["kind"],
                "product": offerable[k]["product"],
                "complaint_type": offerable[k]["complaint_type"],
            }
            for k in best_two_example
        ],
        "must_not_claim": spec.must_not_claim,
        "expect_wasted_retrievals_min": wasted_min,
        "positive_delta_cells": [
            {"product": cell[0], "complaint_type": cell[1], "delta": cell_delta}
            for cell, cell_delta in cell_deltas
        ],
    }


# ---------------------------------------------------------------------------
# generation and writing
# ---------------------------------------------------------------------------


def build_challenge(spec: ChallengeSpec, index: int):
    """Return (scenario_meta, rows, tickets, oracle_entry)."""
    baseline_counts, current_counts = _counts(spec, index)

    baseline_rows = _rows(BASELINE_PERIOD, baseline_counts)
    current_rows = _rows(CURRENT_PERIOD, current_counts)
    tickets = _build_tickets(spec, baseline_rows, current_rows)
    delivered = list(baseline_rows) + list(current_rows)

    # The periods live only inside `intent`, which is the shape bia.store reads.
    intent = AnalysisIntent(current_period=CURRENT_PERIOD, baseline_period=BASELINE_PERIOD)
    intent.validate()

    meta = {
        "scenario_id": spec.challenge_id,
        "challenge_id": spec.challenge_id,
        "label": spec.label,
        "category": spec.category,
        "intent": intent.as_dict(),
        "products": list(PRODUCTS),
        "complaint_types": list(COMPLAINT_TYPES),
        "files": {"metrics": "metrics.csv", "tickets": "tickets.jsonl"},
        "notes": spec.notes,
    }

    oracle_entry = _build_oracle(spec, baseline_counts, current_counts, tickets)
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
    oracle_cases: Dict[str, object] = {}

    for index, spec in enumerate(CHALLENGES):
        meta, rows, tickets, oracle_entry = build_challenge(spec, index)
        directory = os.path.join(base, "data", "challenges", spec.challenge_id)
        _write_text(os.path.join(directory, "scenario.json"), _json_document(meta))
        _write_text(os.path.join(directory, "metrics.csv"), _csv_document(rows))
        _write_text(os.path.join(directory, "tickets.jsonl"), _jsonl_document(tickets))
        oracle_cases[spec.challenge_id] = oracle_entry

    oracle = {
        "seed_base": SEED_BASE,
        "generator": GENERATOR_NAME,
        "contract": "eval/v1/CONTRACT.md",
        "challenges": oracle_cases,
    }
    _write_text(
        os.path.join(base, "data", "oracle", "challenge_oracle.json"), _json_document(oracle)
    )
    return oracle


def main() -> None:
    oracle = generate()
    cases = oracle["challenges"]
    assert isinstance(cases, dict)
    print("wrote %d challenges + oracle under %s/data" % (len(cases), repo_root()))


if __name__ == "__main__":
    main()
