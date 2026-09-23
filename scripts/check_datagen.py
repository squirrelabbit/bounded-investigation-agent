#!/usr/bin/env python3
"""Self-check for the scenario generator.

Runs `python -m bia.datagen` twice, compares every generated file byte for byte,
then re-reads the written CSV/JSONL/oracle from disk and asserts that each
scenario actually does what its label claims. Everything is checked through the
written files, not through in-memory generator internals, so a scenario that
looks right in code but lands wrong on disk still fails here.

    python3 scripts/check_datagen.py
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _checks import CheckFailed, require  # noqa: E402

from bia.datagen import (  # noqa: E402
    BASELINE_PERIOD,
    CURRENT_PERIOD,
    MIN_WINDOW_DAYS,
    PERIOD_DAYS,
    TEXT_TEMPLATES,
    longest_common_run,
)
from bia.lexicon import COMPLAINT_TYPES, PRODUCTS, supports_label  # noqa: E402
from bia.scenarios import SCENARIOS  # noqa: E402
from bia.types import ALLOWED_TICKET_SOURCES  # noqa: E402

DATA = os.path.join(REPO_ROOT, "data")
FAILURES = []
CHECKS = [0]


def check(condition, message):
    CHECKS[0] += 1
    if not condition:
        FAILURES.append(message)
    return bool(condition)


def digests():
    out = {}
    for directory, _dirs, files in os.walk(DATA):
        for name in sorted(files):
            path = os.path.join(directory, name)
            with open(path, "rb") as handle:
                out[os.path.relpath(path, REPO_ROOT)] = hashlib.sha256(handle.read()).hexdigest()
    return out


def run_generator():
    subprocess.run(
        [sys.executable, "-m", "bia.datagen"],
        cwd=REPO_ROOT,
        check=True,
        stdout=subprocess.DEVNULL,
    )


def load_rows(scenario_id):
    path = os.path.join(DATA, "scenarios", scenario_id, "metrics.csv")
    with open(path, encoding="utf-8") as handle:
        lines = handle.read().split("\n")
    require(lines[0] == "day,product,complaint_type,count",
            "%s: metrics.csv 의 헤더가 다르다: %r" % (scenario_id, lines[0]))
    require(lines[-1] == "",
            "%s: metrics.csv 가 개행으로 끝나지 않는다" % scenario_id)
    rows = []
    for line in lines[1:-1]:
        day, product, complaint_type, count = line.split(",")
        rows.append((day, product, complaint_type, int(count)))
    return rows


def dedupe(rows):
    """Collapse exact duplicates; report keys whose counts disagree."""
    seen = {}
    removed = 0
    conflicts = set()
    for day, product, complaint_type, count in rows:
        key = (day, product, complaint_type)
        if key not in seen:
            seen[key] = count
        elif seen[key] == count:
            removed += 1
        else:
            conflicts.add(key)
    return seen, removed, conflicts


def load_tickets(scenario_id):
    path = os.path.join(DATA, "scenarios", scenario_id, "tickets.jsonl")
    with open(path, encoding="utf-8") as handle:
        body = handle.read()
    require(body.endswith("\n"),
            "%s: tickets.jsonl 이 개행으로 끝나지 않는다" % scenario_id)
    return [json.loads(line) for line in body.split("\n")[:-1]]


def in_window(day, window):
    return window["start"] <= day <= window["end"]


def window_offsets(scenario_id, rows_by_key):
    days = sorted({key[0] for key in rows_by_key})
    current = sorted(
        (_dt.date.fromisoformat(d) - CURRENT_PERIOD.start).days
        for d in days
        if d >= CURRENT_PERIOD.start.isoformat()
    )
    baseline = sorted(
        (_dt.date.fromisoformat(d) - BASELINE_PERIOD.start).days
        for d in days
        if d <= BASELINE_PERIOD.end.isoformat()
    )
    return current, baseline


def main():
    # 1. byte reproducibility
    run_generator()
    first = digests()
    run_generator()
    second = digests()
    check(first == second and first, "datagen is not byte-reproducible across two runs")
    if first != second:
        for name in sorted(set(first) | set(second)):
            if first.get(name) != second.get(name):
                FAILURES.append("  differs: %s" % name)

    # 0. text templates must support their own type and no other
    for complaint_type, templates in sorted(TEXT_TEMPLATES.items()):
        for sentence in templates:
            check(
                supports_label(sentence, complaint_type),
                "template does not support its own type %s: %r" % (complaint_type, sentence),
            )
            for other in COMPLAINT_TYPES:
                if other == complaint_type:
                    continue
                check(
                    not supports_label(sentence, other),
                    "template for %s also supports %s: %r" % (complaint_type, other, sentence),
                )

    with open(os.path.join(DATA, "oracle", "oracle.json"), encoding="utf-8") as handle:
        oracle = json.load(handle)

    check(oracle["seed_base"] == 1700, "oracle seed_base is not 1700")
    check(oracle["generator"] == "bia.datagen", "oracle generator name is wrong")
    check(len(oracle["scenarios"]) == 24, "oracle does not hold exactly 24 scenarios")

    summary = []
    runs = {}

    for spec in SCENARIOS:
        sid = spec.scenario_id
        entry = oracle["scenarios"][sid]
        rows = load_rows(sid)
        tickets = load_tickets(sid)
        by_key, removed, conflicts = dedupe(rows)
        cur_offsets, base_offsets = window_offsets(sid, by_key)
        start, end = longest_common_run(cur_offsets, base_offsets, PERIOD_DAYS)
        run_len = 0 if start < 0 else end - start + 1
        runs[sid] = run_len

        # integrity as delivered must match the oracle's integrity claim
        check(
            entry["expect_integrity"]["duplicate_rows_removed_current"] == removed,
            "%s: duplicate_rows_removed_current %s != delivered %d"
            % (sid, entry["expect_integrity"]["duplicate_rows_removed_current"], removed),
        )
        check(
            entry["expect_integrity"]["has_conflicts"] == bool(conflicts),
            "%s: has_conflicts %s != delivered %s"
            % (sid, entry["expect_integrity"]["has_conflicts"], bool(conflicts)),
        )
        check(
            entry["must_defer"] == spec.must_defer,
            "%s: must_defer does not match the spec" % sid,
        )
        check(
            (entry["expect_status"] == "abstained")
            == (entry["expect_comparability_mode"] == "blocked"),
            "%s: status and comparability mode disagree" % sid,
        )
        check(
            set(entry["expect_status"] == "abstained" and [sid] or []) <= {"S09", "S11", "S13"},
            "%s: abstained but is not one of S09/S11/S13" % sid,
        )
        for phrase in ("원인", "때문", "caused", "root cause"):
            check(
                phrase in entry["forbidden_claims"],
                "%s: forbidden_claims is missing %r" % (sid, phrase),
            )

        window = entry["expect_current_window"]
        delta = entry["expect_delta"]

        # totals recomputed from the written, de-duplicated CSV
        if window is not None:
            base_window = entry["expect_baseline_window"]
            cur_total = sum(c for k, c in by_key.items() if in_window(k[0], window))
            base_total = sum(c for k, c in by_key.items() if in_window(k[0], base_window))
            check(
                entry["expect_current_total"] == cur_total,
                "%s: expect_current_total %s != CSV %d"
                % (sid, entry["expect_current_total"], cur_total),
            )
            check(
                entry["expect_baseline_total"] == base_total,
                "%s: expect_baseline_total %s != CSV %d"
                % (sid, entry["expect_baseline_total"], base_total),
            )
            check(
                delta == cur_total - base_total,
                "%s: expect_delta does not match the CSV totals" % sid,
            )

        # 2. the constructed uplift cell is the evidence target
        positive_uplifts = [(p, c, d) for p, c, d in spec.uplift if d > 0]
        target = entry["evidence_target"]
        if entry["expect_comparability_mode"] == "blocked" or not positive_uplifts:
            check(target is None, "%s: expected no evidence target, got %s" % (sid, target))
        else:
            best = max(d for _, _, d in positive_uplifts)
            expected_cells = {(p, c) for p, c, d in positive_uplifts if d == best}
            check(target is not None, "%s: evidence target is missing" % sid)
            if target is not None:
                check(
                    (target["product"], target["complaint_type"]) in expected_cells,
                    "%s: evidence target %s is not the constructed uplift cell %s"
                    % (sid, target, sorted(expected_cells)),
                )

        # target ticket ids must really be in the cell, in window, allowed, supported
        ticket_by_id = {t["ticket_id"]: t for t in tickets}
        for ticket_id in entry["evidence_target_ticket_ids"]:
            ticket = ticket_by_id[ticket_id]
            check(
                ticket["product"] == target["product"]
                and ticket["complaint_type"] == target["complaint_type"]
                and in_window(ticket["day"], window)
                and ticket["source"] in ALLOWED_TICKET_SOURCES
                and supports_label(ticket["text"], ticket["complaint_type"]),
                "%s: %s does not satisfy the evidence-target conditions" % (sid, ticket_id),
            )

        # relevant_ticket_ids must be a superset of evidence_target_ticket_ids
        relevant = entry["relevant_ticket_ids"]
        check(relevant == sorted(relevant), "%s: relevant_ticket_ids is not sorted" % sid)
        check(
            set(entry["evidence_target_ticket_ids"]) <= set(relevant),
            "%s: evidence_target_ticket_ids is not contained in relevant_ticket_ids" % sid,
        )

        # positive_delta_cells must really have delta > 0 over the window, be
        # ordered by (-delta, product, complaint_type), and lead with the target
        positive_cells = entry["positive_delta_cells"]
        if entry["expect_comparability_mode"] == "blocked" or (delta is not None and delta <= 0):
            check(positive_cells == [], "%s: positive_delta_cells should be empty" % sid)
            check(relevant == [], "%s: relevant_ticket_ids should be empty" % sid)
        else:
            check(bool(positive_cells), "%s: positive_delta_cells is empty" % sid)
            base_window = entry["expect_baseline_window"]
            ordering = []
            for cell in positive_cells:
                cur = sum(
                    c
                    for k, c in by_key.items()
                    if in_window(k[0], window)
                    and k[1] == cell["product"]
                    and k[2] == cell["complaint_type"]
                )
                base = sum(
                    c
                    for k, c in by_key.items()
                    if in_window(k[0], base_window)
                    and k[1] == cell["product"]
                    and k[2] == cell["complaint_type"]
                )
                check(
                    cur - base > 0,
                    "%s: %s/%s is listed as positive but its delta is %d"
                    % (sid, cell["product"], cell["complaint_type"], cur - base),
                )
                ordering.append((-(cur - base), cell["product"], cell["complaint_type"]))
            check(
                ordering == sorted(ordering),
                "%s: positive_delta_cells is not ordered by (-delta, product, type)" % sid,
            )
            check(
                positive_cells[0] == target,
                "%s: positive_delta_cells does not lead with the evidence target" % sid,
            )

        cell_keys = {(c["product"], c["complaint_type"]) for c in positive_cells}
        for ticket_id in relevant:
            ticket = ticket_by_id[ticket_id]
            check(
                (ticket["product"], ticket["complaint_type"]) in cell_keys
                and in_window(ticket["day"], window)
                and ticket["source"] in ALLOWED_TICKET_SOURCES
                and supports_label(ticket["text"], ticket["complaint_type"]),
                "%s: %s does not satisfy the relevant-ticket conditions" % (sid, ticket_id),
            )

        pool = 0
        if target is not None:
            pool = sum(
                1
                for t in tickets
                if t["product"] == target["product"]
                and t["complaint_type"] == target["complaint_type"]
                and in_window(t["day"], window)
            )

        summary.append(
            (
                sid,
                spec.category,
                delta,
                entry["expect_comparability_mode"],
                "%s/%s" % (target["product"], target["complaint_type"]) if target else "-",
                pool,
                len(entry["evidence_target_ticket_ids"]),
            )
        )

        # every ticket has a known source and a synthetic marker
        for ticket in tickets:
            check(
                ticket["source"] in ALLOWED_TICKET_SOURCES or ticket["source"] == "legacy_import",
                "%s: unexpected source %r" % (sid, ticket["source"]),
            )
            check(ticket["text"].startswith("[synthetic] "), "%s: text is not marked synthetic" % sid)

        # 3. misleading top group: highest volume is not the top contributor
        if sid in ("S14", "S15"):
            dimension = 1 if sid == "S15" else 0  # S14 on product, S15 on complaint_type
            volume = {}
            for (day, product, complaint_type), count in by_key.items():
                if not in_window(day, window):
                    continue
                key = complaint_type if dimension else product
                volume[key] = volume.get(key, 0) + count
            loudest = max(sorted(volume), key=lambda k: (volume[k], k))
            top = (
                entry["expect_top_complaint_types"]
                if dimension
                else entry["expect_top_products"]
            )
            check(
                top and loudest != top[0],
                "%s: highest-volume group %r is also the top contributor %s"
                % (sid, loudest, top),
            )

    # 4. no-increase scenarios
    check(
        oracle["scenarios"]["S23"]["expect_delta"] < 0,
        "S23: total delta is not negative (%s)" % oracle["scenarios"]["S23"]["expect_delta"],
    )
    check(
        oracle["scenarios"]["S24"]["expect_delta"] == 0,
        "S24: total delta is not exactly 0 (%s)" % oracle["scenarios"]["S24"]["expect_delta"],
    )

    # 5. aligned-window lengths
    check(runs["S09"] < MIN_WINDOW_DAYS, "S09: run %d is not below %d" % (runs["S09"], MIN_WINDOW_DAYS))
    check(runs["S11"] < MIN_WINDOW_DAYS, "S11: run %d is not below %d" % (runs["S11"], MIN_WINDOW_DAYS))
    # The original sheet said S07 should produce 12, which is below the 15-day
    # minimum it also fixes, and would have made S07 blocked/abstained against
    # its own `partial_period` category. Corrected to 16 delivered days: just
    # above the threshold.
    check(runs["S07"] == 16, "S07: run is %d, expected 16" % runs["S07"])
    check(runs["S08"] == 20, "S08: run is %d, expected 20" % runs["S08"])
    check(runs["S10"] == 18, "S10: run is %d, expected 18" % runs["S10"])
    s07 = oracle["scenarios"]["S07"]
    check(s07["expect_comparability_mode"] == "aligned_window", "S07: mode is not aligned_window")
    check(
        s07["expect_current_window"] == {"start": "2026-07-01", "end": "2026-07-16"},
        "S07: current window is %s" % s07["expect_current_window"],
    )
    check(
        s07["expect_baseline_window"] == {"start": "2026-06-01", "end": "2026-06-16"},
        "S07: baseline window is %s" % s07["expect_baseline_window"],
    )
    check(s07["expect_status"] == "reported", "S07: status is not reported")

    # 8. the written files must load through the real runtime loader, and that
    # loader must still refuse the oracle manifest.
    from bia.store import OracleAccessError, load_metric_rows, load_scenario

    for spec in SCENARIOS:
        directory = os.path.join(DATA, "scenarios", spec.scenario_id)
        try:
            intent, rows, tickets, meta = load_scenario(directory)
            intent.validate()
        except Exception as exc:
            FAILURES.append("%s: does not load through bia.store (%s)" % (spec.scenario_id, exc))
            CHECKS[0] += 1
            continue
        CHECKS[0] += 1
        check(bool(rows) and bool(meta.get("label")), "%s: loaded scenario is empty" % spec.scenario_id)
        check(
            intent.current_period.start.isoformat() == "2026-07-01"
            and intent.baseline_period.start.isoformat() == "2026-06-01",
            "%s: loaded intent carries the wrong periods" % spec.scenario_id,
        )
    try:
        load_metric_rows(os.path.join(DATA, "oracle", "oracle.json"))
        check(False, "bia.store did not refuse to read the oracle manifest")
    except OracleAccessError:
        check(True, "")

    # 6. evidence pools
    pools = {row[0]: row[5] for row in summary}
    supported = {row[0]: row[6] for row in summary}
    check(pools["S17"] == 0, "S17: target-cell pool is %d, expected 0" % pools["S17"])
    check(pools["S18"] == 2, "S18: target-cell pool is %d, expected 2" % pools["S18"])
    check(pools["S19"] > 0, "S19: target-cell pool is empty")
    check(supported["S19"] == 0, "S19: %d tickets support their own label, expected 0" % supported["S19"])
    check(
        oracle["scenarios"]["S19"]["expect_target_cell_admitted"] == 0,
        "S19: expect_target_cell_admitted is not 0",
    )

    # 7. bad source
    s22 = oracle["scenarios"]["S22"]
    rejected = set(s22["expect_rejected_source_ids"])
    check(bool(rejected), "S22: expect_rejected_source_ids is empty")
    check(
        not (rejected & set(s22["evidence_target_ticket_ids"])),
        "S22: rejected source ids overlap the evidence target ids",
    )
    s22_tickets = {t["ticket_id"]: t for t in load_tickets("S22")}
    for ticket_id in rejected:
        check(
            s22_tickets[ticket_id]["source"] == "legacy_import",
            "S22: %s is listed as rejected but its source is allowed" % ticket_id,
        )
    for sid in [s.scenario_id for s in SCENARIOS if s.scenario_id != "S22"]:
        check(
            oracle["scenarios"][sid]["expect_rejected_source_ids"] == [],
            "%s: expect_rejected_source_ids should be empty" % sid,
        )

    print("%-5s %-26s %8s  %-15s %-26s %6s %6s" % ("SID", "category", "delta", "mode", "target cell", "pool", "ok"))
    for row in summary:
        print("%-5s %-26s %8s  %-15s %-26s %6d %6d" % row)
    print()
    print("checks run: %d" % CHECKS[0])
    if FAILURES:
        print("FAILED: %d" % len(FAILURES))
        for failure in FAILURES:
            print("  - %s" % failure)
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except CheckFailed as failure:
        sys.stderr.write("CHECK FAILED: %s\n" % failure)
        sys.exit(1)
