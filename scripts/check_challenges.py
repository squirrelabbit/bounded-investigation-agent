#!/usr/bin/env python3
"""Self-check for the v1 challenge generator.

Runs `python -m bia.challengegen` twice, compares every generated file byte for
byte, confirms that the frozen v0 data is untouched, then re-reads the written
CSV/JSONL/oracle from disk and asserts that each case actually has the property
its category claims. Everything is checked through the written files, not
through in-memory generator internals, so a case that looks right in code but
lands wrong on disk still fails here.

    python3 scripts/check_challenges.py
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from bia.challengegen import (  # noqa: E402
    BASELINE_PERIOD,
    CURRENT_PERIOD,
    TEXT_TEMPLATES,
    TOP_MAX,
    TOP_SHARE_TARGET,
)
from bia.challenges import CHALLENGES  # noqa: E402
from bia.lexicon import COMPLAINT_TYPES, supports_label  # noqa: E402
from bia.types import ALLOWED_TICKET_SOURCES  # noqa: E402

DATA = os.path.join(REPO_ROOT, "data")
CHALLENGE_DIR = os.path.join(DATA, "challenges")
V0_SCENARIO_DIR = os.path.join(DATA, "scenarios")
V0_ORACLE = os.path.join(DATA, "oracle", "oracle.json")
CHALLENGE_ORACLE = os.path.join(DATA, "oracle", "challenge_oracle.json")

FAILURES = []
CHECKS = [0]


def check(condition, message):
    CHECKS[0] += 1
    if not condition:
        FAILURES.append(message)
    return bool(condition)


def sha256_of(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def digests(root):
    out = {}
    for directory, _dirs, files in os.walk(root):
        for name in sorted(files):
            path = os.path.join(directory, name)
            out[os.path.relpath(path, REPO_ROOT)] = sha256_of(path)
    return out


def v0_digests():
    out = digests(V0_SCENARIO_DIR)
    out[os.path.relpath(V0_ORACLE, REPO_ROOT)] = sha256_of(V0_ORACLE)
    return out


def challenge_digests():
    out = digests(CHALLENGE_DIR)
    out[os.path.relpath(CHALLENGE_ORACLE, REPO_ROOT)] = sha256_of(CHALLENGE_ORACLE)
    return out


def run_module(module):
    subprocess.run(
        [sys.executable, "-m", module],
        cwd=REPO_ROOT,
        check=True,
        stdout=subprocess.DEVNULL,
    )


def load_rows(challenge_id):
    path = os.path.join(CHALLENGE_DIR, challenge_id, "metrics.csv")
    with open(path, encoding="utf-8") as handle:
        lines = handle.read().split("\n")
    assert lines[0] == "day,product,complaint_type,count"
    assert lines[-1] == ""
    rows = []
    for line in lines[1:-1]:
        day, product, complaint_type, count = line.split(",")
        rows.append((day, product, complaint_type, int(count)))
    return rows


def load_tickets(challenge_id):
    path = os.path.join(CHALLENGE_DIR, challenge_id, "tickets.jsonl")
    with open(path, encoding="utf-8") as handle:
        body = handle.read()
    assert body.endswith("\n")
    return [json.loads(line) for line in body.split("\n")[:-1]]


def in_window(day, window):
    return window["start"] <= day <= window["end"]


def top_contributors(current, baseline):
    """Restated here so the oracle's own helper is not the thing under test."""
    deltas = []
    for value in sorted(set(current) | set(baseline)):
        delta = current.get(value, 0) - baseline.get(value, 0)
        if delta > 0:
            deltas.append((value, delta))
    total = sum(d for _, d in deltas)
    if not total:
        return []
    deltas.sort(key=lambda item: (-item[1], item[0]))
    picked = []
    cumulative = 0.0
    for value, delta in deltas:
        picked.append(value)
        cumulative += round(delta / float(total), 4)
        if cumulative >= TOP_SHARE_TARGET or len(picked) >= TOP_MAX:
            break
    return picked


def entry_filter(entry, kind=None, product=None, complaint_type=None):
    """The offerable filter matching the given shape, or None."""
    for item in entry["offerable_filters"]:
        if kind is not None and item["kind"] != kind:
            continue
        if item["product"] == product and item["complaint_type"] == complaint_type:
            return item
    return None


def cell_filters(entry):
    return [f for f in entry["offerable_filters"] if f["kind"] == "cell"]


def main():
    # ------------------------------------------------------------------
    # 2. the frozen v0 data must survive this work untouched
    # ------------------------------------------------------------------
    v0_before = v0_digests()
    run_module("bia.datagen")
    v0_after = v0_digests()
    check(
        bool(v0_before) and v0_before == v0_after,
        "v0 data changed when bia.datagen was re-run: v0 is supposed to be frozen",
    )
    for name in sorted(set(v0_before) | set(v0_after)):
        if v0_before.get(name) != v0_after.get(name):
            FAILURES.append(
                "  v0 differs: %s (%s -> %s)" % (name, v0_before.get(name), v0_after.get(name))
            )
    check(
        len(v0_after) == 24 * 3 + 1,
        "expected 24 scenario dirs x 3 files + oracle.json under data/, got %d" % len(v0_after),
    )

    # ------------------------------------------------------------------
    # 1. byte reproducibility of the challenge generator
    # ------------------------------------------------------------------
    run_module("bia.challengegen")
    first = challenge_digests()
    run_module("bia.challengegen")
    second = challenge_digests()
    check(bool(first) and first == second, "challengegen is not byte-reproducible across two runs")
    for name in sorted(set(first) | set(second)):
        if first.get(name) != second.get(name):
            FAILURES.append("  differs: %s" % name)
    check(
        len(first) == 8 * 3 + 1,
        "expected 8 challenge dirs x 3 files + challenge_oracle.json, got %d" % len(first),
    )
    # regenerating the challenges must not have disturbed v0 either
    check(
        v0_after == v0_digests(),
        "running bia.challengegen changed data under data/scenarios or data/oracle/oracle.json",
    )

    # text templates must support their own type and no other
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

    with open(CHALLENGE_ORACLE, encoding="utf-8") as handle:
        oracle = json.load(handle)

    check(oracle["seed_base"] == 2700, "challenge oracle seed_base is not 2700")
    check(oracle["generator"] == "bia.challengegen", "challenge oracle generator name is wrong")
    check(len(oracle["challenges"]) == 8, "challenge oracle does not hold exactly 8 cases")

    summary = []
    measured = {}

    for spec in CHALLENGES:
        cid = spec.challenge_id
        entry = oracle["challenges"][cid]
        rows = load_rows(cid)
        tickets = load_tickets(cid)
        ticket_by_id = {t["ticket_id"]: t for t in tickets}
        window = entry["expect_current_window"]
        base_window = entry["expect_baseline_window"]

        check(entry["category"] == spec.category, "%s: oracle category does not match the spec" % cid)
        check(
            entry["must_not_claim"] == spec.must_not_claim,
            "%s: must_not_claim does not match the spec" % cid,
        )
        check(
            window == {"start": "2026-07-01", "end": "2026-07-30"}
            and base_window == {"start": "2026-06-01", "end": "2026-06-30"},
            "%s: windows are %s / %s, expected the two full periods" % (cid, window, base_window),
        )
        check(
            entry["expect_comparability_mode"] == "full"
            and entry["expect_status"] == "reported",
            "%s: mode/status is %s/%s, expected full/reported"
            % (cid, entry["expect_comparability_mode"], entry["expect_status"]),
        )
        for phrase in ("원인", "때문", "caused", "root cause"):
            check(phrase in entry["forbidden_claims"], "%s: forbidden_claims is missing %r" % (cid, phrase))

        # --------------------------------------------------------------
        # 7. totals and top groups recomputed from the written CSV
        # --------------------------------------------------------------
        cur_total = sum(c for day, _p, _t, c in rows if in_window(day, window))
        base_total = sum(c for day, _p, _t, c in rows if in_window(day, base_window))
        check(
            entry["expect_current_total"] == cur_total,
            "%s: expect_current_total %s != CSV %d" % (cid, entry["expect_current_total"], cur_total),
        )
        check(
            entry["expect_baseline_total"] == base_total,
            "%s: expect_baseline_total %s != CSV %d" % (cid, entry["expect_baseline_total"], base_total),
        )
        check(
            entry["expect_delta"] == cur_total - base_total,
            "%s: expect_delta %s != CSV %d" % (cid, entry["expect_delta"], cur_total - base_total),
        )

        cur_products, base_products, cur_types, base_types = {}, {}, {}, {}
        cur_cells, base_cells = {}, {}
        for day, product, complaint_type, count in rows:
            if in_window(day, window):
                p, t, c = cur_products, cur_types, cur_cells
            elif in_window(day, base_window):
                p, t, c = base_products, base_types, base_cells
            else:
                continue
            p[product] = p.get(product, 0) + count
            t[complaint_type] = t.get(complaint_type, 0) + count
            key = (product, complaint_type)
            c[key] = c.get(key, 0) + count
        check(
            entry["expect_top_products"] == top_contributors(cur_products, base_products),
            "%s: expect_top_products %s != CSV %s"
            % (cid, entry["expect_top_products"], top_contributors(cur_products, base_products)),
        )
        check(
            entry["expect_top_complaint_types"] == top_contributors(cur_types, base_types),
            "%s: expect_top_complaint_types %s != CSV %s"
            % (cid, entry["expect_top_complaint_types"], top_contributors(cur_types, base_types)),
        )

        csv_positive = {k for k in cur_cells if cur_cells[k] - base_cells.get(k, 0) > 0}
        oracle_positive = {
            (c["product"], c["complaint_type"]) for c in entry["positive_delta_cells"]
        }
        check(
            csv_positive == oracle_positive,
            "%s: positive-delta cells from the CSV (%d) do not match the oracle (%d)"
            % (cid, len(csv_positive), len(oracle_positive)),
        )
        for cell in entry["positive_delta_cells"]:
            key = (cell["product"], cell["complaint_type"])
            csv_delta = cur_cells[key] - base_cells.get(key, 0)
            check(
                cell["delta"] == csv_delta,
                "%s: %s/%s delta %s != CSV %d"
                % (cid, key[0], key[1], cell["delta"], csv_delta),
            )

        # --------------------------------------------------------------
        # 5. every useful ticket id must really be useful
        # --------------------------------------------------------------
        useful = entry["useful_ticket_ids"]
        check(useful == sorted(useful), "%s: useful_ticket_ids is not sorted" % cid)
        check(len(set(useful)) == len(useful), "%s: useful_ticket_ids has duplicates" % cid)
        for ticket_id in useful:
            ticket = ticket_by_id.get(ticket_id)
            if ticket is None:
                check(False, "%s: %s is not in tickets.jsonl" % (cid, ticket_id))
                continue
            check(
                in_window(ticket["day"], window),
                "%s: %s is on %s, outside the window" % (cid, ticket_id, ticket["day"]),
            )
            check(
                ticket["source"] in ALLOWED_TICKET_SOURCES,
                "%s: %s has source %r, which is not allowed" % (cid, ticket_id, ticket["source"]),
            )
            check(
                supports_label(ticket["text"], ticket["complaint_type"]),
                "%s: %s text does not support its own label %s: %r"
                % (cid, ticket_id, ticket["complaint_type"], ticket["text"]),
            )
            check(
                (ticket["product"], ticket["complaint_type"]) in oracle_positive,
                "%s: %s sits in %s/%s, which did not grow"
                % (cid, ticket_id, ticket["product"], ticket["complaint_type"]),
            )
        # and nothing useful may be missing: recount independently from disk
        recomputed = sorted(
            t["ticket_id"]
            for t in tickets
            if in_window(t["day"], window)
            and t["source"] in ALLOWED_TICKET_SOURCES
            and supports_label(t["text"], t["complaint_type"])
            and (t["product"], t["complaint_type"]) in csv_positive
        )
        check(
            useful == recomputed,
            "%s: useful_ticket_ids (%d) does not match the recount from disk (%d)"
            % (cid, len(useful), len(recomputed)),
        )

        # offerable filters: pool and useful recomputed from disk
        for item in entry["offerable_filters"]:
            matched = [
                t
                for t in tickets
                if in_window(t["day"], window)
                and (item["product"] is None or t["product"] == item["product"])
                and (
                    item["complaint_type"] is None
                    or t["complaint_type"] == item["complaint_type"]
                )
            ]
            check(
                item["pool"] == len(matched),
                "%s: filter %s/%s pool %s != %d from disk"
                % (cid, item["product"], item["complaint_type"], item["pool"], len(matched)),
            )
            hits = len([t for t in matched if t["ticket_id"] in set(useful)])
            check(
                item["useful"] == hits,
                "%s: filter %s/%s useful %s != %d from disk"
                % (cid, item["product"], item["complaint_type"], item["useful"], hits),
            )
        identities = [(f["product"], f["complaint_type"]) for f in entry["offerable_filters"]]
        check(
            len(identities) == len(set(identities)),
            "%s: offerable_filters holds duplicate filters: %s" % (cid, identities),
        )

        # --------------------------------------------------------------
        # 4. yields are ordered, and both are 0 exactly when deferring
        # --------------------------------------------------------------
        single = entry["best_single_filter_yield"]
        two = entry["best_two_filter_yield"]
        check(
            two >= single,
            "%s: best_two_filter_yield %d < best_single_filter_yield %d" % (cid, two, single),
        )
        check(
            single == max([f["useful"] for f in entry["offerable_filters"]] + [0]),
            "%s: best_single_filter_yield %d is not the max filter useful" % (cid, single),
        )
        check(
            (single == 0 and two == 0) == bool(entry["must_not_claim"]),
            "%s: yields are %d/%d while must_not_claim is %s"
            % (cid, single, two, entry["must_not_claim"]),
        )
        check(
            (len(useful) == 0) == bool(entry["must_not_claim"]),
            "%s: %d useful tickets while must_not_claim is %s"
            % (cid, len(useful), entry["must_not_claim"]),
        )

        # every ticket has a known source and a synthetic marker
        for ticket in tickets:
            check(
                ticket["source"] in ALLOWED_TICKET_SOURCES or ticket["source"] == "legacy_import",
                "%s: unexpected source %r" % (cid, ticket["source"]),
            )
            check(
                ticket["text"].startswith("[synthetic] "),
                "%s: text is not marked synthetic" % cid,
            )

        top_cell = entry["positive_delta_cells"][0] if entry["positive_delta_cells"] else None
        top_cell_filter = (
            entry_filter(entry, "cell", top_cell["product"], top_cell["complaint_type"])
            if top_cell
            else None
        )
        measured[cid] = {
            "entry": entry,
            "top_cell": top_cell,
            "top_cell_filter": top_cell_filter,
            "cell_filters": cell_filters(entry),
        }
        summary.append(
            (
                cid,
                spec.category,
                entry["expect_delta"],
                "%s/%s" % (top_cell["product"], top_cell["complaint_type"]) if top_cell else "-",
                top_cell["delta"] if top_cell else 0,
                single,
                two,
                "yes" if entry["must_not_claim"] else "no",
            )
        )

    # ------------------------------------------------------------------
    # 3. the per-case properties, with the measured numbers in the message
    # ------------------------------------------------------------------
    properties = {}

    # C01: the largest-delta cell is a decoy — full pool, zero yield.
    m = measured["C01"]
    decoy = m["top_cell_filter"]
    others = [f for f in m["cell_filters"] if f is not decoy]
    second = others[0] if others else None
    check(
        decoy is not None and decoy["useful"] == 0 and decoy["pool"] >= 3,
        "C01: top cell filter is %s, expected useful == 0 with pool >= 3" % decoy,
    )
    check(
        second is not None and second["useful"] >= 20,
        "C01: second cell filter is %s, expected a rich clean yield (>= 20)" % second,
    )
    properties["C01"] = "top cell pool=%d useful=%d; second cell pool=%d useful=%d" % (
        decoy["pool"],
        decoy["useful"],
        second["pool"],
        second["useful"],
    )

    # C02: the product-level candidate beats any single cell by 3x or more.
    m = measured["C02"]
    product_filter = entry_filter(m["entry"], "product", "P-Alpha", None)
    best_cell = max(f["useful"] for f in m["cell_filters"])
    check(
        product_filter is not None and product_filter["useful"] >= 3 * best_cell,
        "C02: product filter useful %s is not >= 3x the best cell's %d"
        % (product_filter["useful"] if product_filter else None, best_cell),
    )
    check(
        m["entry"]["expect_top_products"][:1] == ["P-Alpha"],
        "C02: expect_top_products leads with %s, expected P-Alpha"
        % m["entry"]["expect_top_products"][:1],
    )
    properties["C02"] = "product useful=%d vs best cell useful=%d (%.2fx)" % (
        product_filter["useful"],
        best_cell,
        product_filter["useful"] / float(best_cell),
    )

    # C03: the largest-delta cell is thin but locally clean.
    m = measured["C03"]
    thin = m["top_cell_filter"]
    rich = [f for f in m["cell_filters"] if f is not thin][0]
    check(
        thin is not None and 3 <= thin["pool"] <= 8,
        "C03: top cell pool is %s, expected 3..8" % (thin["pool"] if thin else None),
    )
    check(
        thin["useful"] * 5 <= rich["useful"],
        "C03: top cell useful %d is not much smaller than the second cell's %d"
        % (thin["useful"], rich["useful"]),
    )
    check(
        thin["pool"] > 0 and thin["useful"] / float(thin["pool"]) >= 0.5,
        "C03: top cell useful/pool is %.3f, expected >= 0.5"
        % (thin["useful"] / float(thin["pool"]) if thin["pool"] else 0.0),
    )
    properties["C03"] = "top cell pool=%d useful=%d (ratio %.2f); second cell useful=%d" % (
        thin["pool"],
        thin["useful"],
        thin["useful"] / float(thin["pool"]),
        rich["useful"],
    )

    # C04: the complaint_type-level candidate beats any single cell by 3x or more.
    m = measured["C04"]
    type_filter = entry_filter(m["entry"], "complaint_type", None, "billing_error")
    best_cell = max(f["useful"] for f in m["cell_filters"])
    check(
        type_filter is not None and type_filter["useful"] >= 3 * best_cell,
        "C04: complaint_type filter useful %s is not >= 3x the best cell's %d"
        % (type_filter["useful"] if type_filter else None, best_cell),
    )
    check(
        m["entry"]["expect_top_complaint_types"][:1] == ["billing_error"],
        "C04: expect_top_complaint_types leads with %s, expected billing_error"
        % m["entry"]["expect_top_complaint_types"][:1],
    )
    properties["C04"] = "type useful=%d vs best cell useful=%d (%.2fx)" % (
        type_filter["useful"],
        best_cell,
        type_filter["useful"] / float(best_cell),
    )

    # C05: the trap admits nothing at all.
    entry = measured["C05"]["entry"]
    check(entry["useful_ticket_ids"] == [], "C05: useful_ticket_ids is not empty (%d ids)" % len(entry["useful_ticket_ids"]))
    check(entry["best_single_filter_yield"] == 0, "C05: best_single_filter_yield is %d" % entry["best_single_filter_yield"])
    check(entry["best_two_filter_yield"] == 0, "C05: best_two_filter_yield is %d" % entry["best_two_filter_yield"])
    check(entry["must_not_claim"] is True, "C05: must_not_claim is not true")
    trap_pool = measured["C05"]["top_cell_filter"]["pool"]
    check(trap_pool >= 3, "C05: the trap cell pool is %d, so nothing is even tempting" % trap_pool)
    trap_tickets = [
        t
        for t in load_tickets("C05")
        if in_window(t["day"], entry["expect_current_window"])
        and t["product"] == measured["C05"]["top_cell"]["product"]
        and t["complaint_type"] == measured["C05"]["top_cell"]["complaint_type"]
    ]
    bad = [t for t in trap_tickets if t["source"] != "legacy_import"]
    check(
        not bad and bool(trap_tickets),
        "C05: %d of %d trap-cell tickets are not legacy_import" % (len(bad), len(trap_tickets)),
    )
    check(
        len(entry["positive_delta_cells"]) == 1,
        "C05: %d cells have a positive delta, expected exactly 1"
        % len(entry["positive_delta_cells"]),
    )
    properties["C05"] = "trap pool=%d, all legacy_import; positive cells=%d; useful=0" % (
        trap_pool,
        len(entry["positive_delta_cells"]),
    )

    # C06: neither cell alone clears a 0.5 coverage bar.
    m = measured["C06"]
    ratios = []
    for f in m["cell_filters"]:
        ratio = f["useful"] / float(f["pool"]) if f["pool"] else 0.0
        ratios.append(ratio)
        check(
            0.30 < ratio < 0.50,
            "C06: cell %s/%s has useful/pool %.3f (%d/%d), expected strictly inside 0.30..0.50"
            % (f["product"], f["complaint_type"], ratio, f["useful"], f["pool"]),
        )
    check(len(m["cell_filters"]) == 2, "C06: expected 2 cell candidates, got %d" % len(m["cell_filters"]))
    single = m["entry"]["best_single_filter_yield"]
    two = m["entry"]["best_two_filter_yield"]
    check(
        two >= single * 1.25 and two > single,
        "C06: best_two %d is not clearly larger than best_single %d" % (two, single),
    )
    properties["C06"] = "cell coverage %s; single=%d two=%d (%.2fx)" % (
        ", ".join("%.2f" % r for r in ratios),
        single,
        two,
        two / float(single) if single else 0.0,
    )

    # C07: one retrieval is already the ceiling.
    m = measured["C07"]
    single = m["entry"]["best_single_filter_yield"]
    two = m["entry"]["best_two_filter_yield"]
    clean = m["top_cell_filter"]
    check(single == two, "C07: best_single %d != best_two %d, so a second call still pays" % (single, two))
    ratio = clean["useful"] / float(clean["pool"]) if clean["pool"] else 0.0
    check(
        ratio >= 0.85,
        "C07: cell useful/pool is %.3f (%d/%d), expected >= 0.85"
        % (ratio, clean["useful"], clean["pool"]),
    )
    properties["C07"] = "single=two=%d; cell pool=%d useful=%d (ratio %.2f)" % (
        single,
        clean["pool"],
        clean["useful"],
        ratio,
    )

    # C08: the loud cell is loud and did not grow at all (CONTRACT revision 1).
    # Every number below is recomputed from the written files, not read out of
    # the oracle, so the decoy cannot be declared harmless by the generator.
    m = measured["C08"]
    entry = m["entry"]
    real_top = m["top_cell_filter"]
    loud_cell = ("P-Alpha", "support_wait")
    window = entry["expect_current_window"]
    base_window = entry["expect_baseline_window"]
    rows = load_rows("C08")
    loud_current = sum(
        c
        for day, product, complaint_type, c in rows
        if (product, complaint_type) == loud_cell and in_window(day, window)
    )
    loud_baseline = sum(
        c
        for day, product, complaint_type, c in rows
        if (product, complaint_type) == loud_cell and in_window(day, base_window)
    )
    loud_delta = loud_current - loud_baseline
    check(
        loud_delta == 0,
        "C08: loud cell delta recomputed from the CSV is %d (%d vs %d), expected exactly 0"
        % (loud_delta, loud_current, loud_baseline),
    )
    check(
        not any(
            (c["product"], c["complaint_type"]) == loud_cell
            for c in entry["positive_delta_cells"]
        ),
        "C08: the loud cell is listed among the positive-delta cells",
    )

    c08_tickets = load_tickets("C08")
    loud_pool = [
        t
        for t in c08_tickets
        if (t["product"], t["complaint_type"]) == loud_cell and in_window(t["day"], window)
    ]
    top_pool = [
        t
        for t in c08_tickets
        if (t["product"], t["complaint_type"]) == (m["top_cell"]["product"], m["top_cell"]["complaint_type"])
        and in_window(t["day"], window)
    ]
    check(
        len(loud_pool) >= 3 * len(top_pool),
        "C08: loud cell pool %d is not >= 3x the real top cell's %d"
        % (len(loud_pool), len(top_pool)),
    )
    useful_ids = set(entry["useful_ticket_ids"])
    loud_useful = [t["ticket_id"] for t in loud_pool if t["ticket_id"] in useful_ids]
    check(
        not loud_useful,
        "C08: %d loud-cell tickets are counted as useful evidence (e.g. %s)"
        % (len(loud_useful), loud_useful[:3]),
    )
    # the filter that retrieves the loud cell and nothing else yields nothing
    loud_only_useful = len(
        [
            t
            for t in c08_tickets
            if in_window(t["day"], window)
            and (t["product"], t["complaint_type"]) == loud_cell
            and t["ticket_id"] in useful_ids
        ]
    )
    check(
        loud_only_useful == 0,
        "C08: a loud-cell-only retrieval would yield %d useful tickets, expected 0"
        % loud_only_useful,
    )
    offered_loud = entry_filter(entry, "cell", loud_cell[0], loud_cell[1])
    check(
        offered_loud is None or offered_loud["useful"] == 0,
        "C08: the loud cell is offered as a candidate with useful %s"
        % (offered_loud["useful"] if offered_loud else None),
    )

    # the yields must come from the real top cell's side, not from the decoy
    top_useful_ids = {t["ticket_id"] for t in top_pool if t["ticket_id"] in useful_ids}
    single = entry["best_single_filter_yield"]
    two = entry["best_two_filter_yield"]
    check(
        single == real_top["useful"] or single > real_top["useful"],
        "C08: best_single %d is below the real top cell's own yield %d"
        % (single, real_top["useful"]),
    )
    best_filters = [f for f in entry["offerable_filters"] if f["useful"] == single]
    for f in best_filters:
        reached = {
            t["ticket_id"]
            for t in c08_tickets
            if in_window(t["day"], window)
            and t["ticket_id"] in useful_ids
            and (f["product"] is None or t["product"] == f["product"])
            and (f["complaint_type"] is None or t["complaint_type"] == f["complaint_type"])
        }
        check(
            bool(reached & top_useful_ids),
            "C08: the best single filter %s/%s reaches none of the real top cell's evidence"
            % (f["product"], f["complaint_type"]),
        )
    check(
        two >= single and two >= len(top_useful_ids),
        "C08: best_two %d does not cover the real top cell's %d useful tickets"
        % (two, len(top_useful_ids)),
    )
    properties["C08"] = (
        "loud pool=%d delta=%d useful=%d; top cell pool=%d delta=%d useful=%d; single=%d two=%d"
        % (
            len(loud_pool),
            loud_delta,
            len(loud_useful),
            len(top_pool),
            m["top_cell"]["delta"],
            len(top_useful_ids),
            single,
            two,
        )
    )

    # ------------------------------------------------------------------
    # 6. every case loads through the real runtime loader
    # ------------------------------------------------------------------
    from bia.store import OracleAccessError, load_metric_rows, load_scenario

    for spec in CHALLENGES:
        directory = os.path.join(CHALLENGE_DIR, spec.challenge_id)
        try:
            intent, rows, tickets, meta = load_scenario(directory)
            intent.validate()
        except Exception as exc:
            FAILURES.append("%s: does not load through bia.store (%s)" % (spec.challenge_id, exc))
            CHECKS[0] += 1
            continue
        CHECKS[0] += 1
        check(
            bool(rows) and bool(tickets) and bool(meta.get("label")),
            "%s: loaded case is empty" % spec.challenge_id,
        )
        check(
            intent.current_period.start == CURRENT_PERIOD.start
            and intent.baseline_period.start == BASELINE_PERIOD.start,
            "%s: loaded intent carries the wrong periods" % spec.challenge_id,
        )
    try:
        load_metric_rows(CHALLENGE_ORACLE)
        check(False, "bia.store did not refuse to read the challenge oracle manifest")
    except OracleAccessError:
        check(True, "")

    # the oracle must not be reachable from the runtime data directory
    check(
        not os.path.exists(os.path.join(CHALLENGE_DIR, "challenge_oracle.json")),
        "the challenge oracle is sitting inside the runtime data directory",
    )

    print(
        "%-5s %-22s %7s  %-26s %7s %8s %8s %6s"
        % ("CID", "category", "delta", "top cell", "cell Δ", "single", "two", "defer")
    )
    for row in summary:
        print("%-5s %-22s %7d  %-26s %7d %8d %8d %6s" % row)
    print()
    for cid in sorted(properties):
        print("%-5s property: %s" % (cid, properties[cid]))
    print()
    print("v0 files verified untouched: %d" % len(v0_after))
    print("checks run: %d" % CHECKS[0])
    if FAILURES:
        print("FAILED: %d" % len(FAILURES))
        for failure in FAILURES:
            print("  - %s" % failure)
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
