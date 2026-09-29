"""사전등록 8절: 독립 oracle. `bia` 의 어떤 모듈도 import 하지 않는다(표준 라이브러리 + fractions).

파생 CSV 를 직접 읽어 `Fraction` 으로 계산한다. 정의는 v2 스펙
(`docs/superpowers/specs/2026-09-22-v2-typed-structured-analysis-design.md` 4·5절)을 다시 적은 것이다.
합 지표의 플래그 셋(decomposition_complete 항상 참, heavy_cancellation 은 |Δ|/gross, suppress 는
heavy 또는 |Δ| <= SHARE_EPSILON)은 통합 계획(`docs/superpowers/plans/2026-09-28-v2-integration.md`
Commit D)에 적힌 계약이다.

모호 판정(사전등록 8절):
- 임계(0.20, 0.0001)를 쓰는 판정값 q 는 |q - T| <= 1e-9 이면 모호.
- 부호 0 허용오차(1e-9)를 쓰는 판정값 x 는 0 < |x| <= 2e-9 이면 모호 — 판정 경계 |x| = 1e-9 로부터
  1e-9 안이다. 정확히 0 인 값(구조적 0)은 모호하지 않다.
- 플래그를 이루는 판정값 중 하나라도 모호하면 그 플래그는 모호.

실행: 표준입력으로 {"derived_dir": ..., "experiments": [...]} JSON 을 받아 결과 JSON 을 표준출력에 쓴다.
"""
from __future__ import annotations

import csv
import datetime as _dt
import json
import os
import re
import sys
from fractions import Fraction

UNKNOWN = "__UNKNOWN__"
FLOAT_TOL = Fraction(1, 10 ** 9)
SHARE_EPSILON = Fraction(1, 10 ** 4)
CANCELLATION_THRESHOLD = Fraction(1, 5)
GROSS_EPSILON = Fraction(1, 10 ** 12)
CROSS_CELL_LIMIT = 1000

_INT_RE = re.compile(r"^[0-9]+$")


class OracleInputError(ValueError):
    pass


def _day(value):
    return _dt.date.fromisoformat(value)


def load(path, grain_dimensions, columns):
    """(day, dims...) -> [값...]. grain 완전 중복은 하나로, 값이 다른 중복은 거부."""
    rows = {}
    with open(path, "r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for record in reader:
            key = [record["day"]]
            for dim in grain_dimensions:
                raw = (record[dim] or "").strip()
                key.append(raw if raw else UNKNOWN)
            values = []
            for col in columns:
                raw = (record[col] or "").strip()
                if not _INT_RE.match(raw):
                    raise OracleInputError("non-integer %r in %r" % (raw, col))
                values.append(int(raw))
            key = tuple(key)
            prior = rows.get(key)
            if prior is not None and prior != values:
                raise OracleInputError("conflicting duplicate at %r" % (key,))
            rows[key] = values
    return rows


def window_sum(rows, window, grain_dimensions, dims):
    start, end = _day(window[0]), _day(window[1])
    positions = [1 + grain_dimensions.index(d) for d in dims]
    out = {}
    for key, values in rows.items():
        if not (start <= _day(key[0]) <= end):
            continue
        group = tuple(key[p] for p in positions)
        bucket = out.setdefault(group, [0] * len(values))
        for i, v in enumerate(values):
            bucket[i] += v
    return out


def sign_tol(x):
    if abs(x) <= FLOAT_TOL:
        return 0
    return 1 if x > 0 else -1


def amb_tol(x):
    return x != 0 and abs(x) <= 2 * FLOAT_TOL


def amb_thr(q, threshold):
    return abs(q - threshold) <= FLOAT_TOL


def _f(x):
    return None if x is None else float(x)


def additive_branch(cur, base, dims, cross):
    universe = sorted(set(cur) | set(base))
    cells = len(universe)
    if cross and cells > CROSS_CELL_LIMIT:
        return {"dimensions": dims, "cross": cross, "status": "omitted",
                "reason": "cross_cell_limit_exceeded", "observed_cells": cells}
    groups = []
    net = 0
    gross = 0
    for key in universe:
        c = cur.get(key, [0])[0]
        b = base.get(key, [0])[0]
        groups.append({"key": list(key), "current_value": c, "baseline_value": b,
                       "group_delta": c - b, "net_contribution": float(c - b),
                       "comparable": True})
        net += c - b
        gross += abs(c - b)
    net, gross = Fraction(net), Fraction(gross)
    ratio = (abs(net) / gross) if gross else None
    heavy = gross > GROSS_EPSILON and ratio < CANCELLATION_THRESHOLD
    amb_heavy = ratio is not None and amb_thr(ratio, CANCELLATION_THRESHOLD)
    suppress = heavy or abs(net) <= SHARE_EPSILON
    amb_suppress = amb_heavy or amb_thr(abs(net), SHARE_EPSILON)
    flags = {"decomposition_complete": True, "heavy_cancellation": heavy,
             "suppress_top_contributor": suppress}
    ambiguous = [n for n, a in (("heavy_cancellation", amb_heavy),
                                ("suppress_top_contributor", amb_suppress)) if a]
    return {"dimensions": dims, "cross": cross, "status": "ok", "observed_cells": cells,
            "groups": groups, "totals": {"gross_movement": float(gross)},
            "flags": flags, "ambiguous_flags": ambiguous}


def ratio_branch(cur, base, dims, cross):
    universe = sorted(set(cur) | set(base))
    cells = len(universe)
    if cross and cells > CROSS_CELL_LIMIT:
        return {"dimensions": dims, "cross": cross, "status": "omitted",
                "reason": "cross_cell_limit_exceeded", "observed_cells": cells}
    n1 = sum(v[0] for v in cur.values())
    d1 = sum(v[1] for v in cur.values())
    n0 = sum(v[0] for v in base.values())
    d0 = sum(v[1] for v in base.values())
    r1 = Fraction(n1, d1) if d1 else Fraction(0)
    r0 = Fraction(n0, d0) if d0 else Fraction(0)
    delta_r = r1 - r0
    groups = []
    total_rate = Fraction(0)
    total_mix = Fraction(0)
    entry_exit = Fraction(0)
    gross = Fraction(0)
    rate_values = []
    for key in universe:
        gn1, gd1 = cur.get(key, [0, 0])
        gn0, gd0 = base.get(key, [0, 0])
        net = (Fraction(gn1, d1) if d1 else Fraction(0)) - (Fraction(gn0, d0) if d0 else Fraction(0))
        gross += abs(net)
        group = {"key": list(key), "net_contribution": float(net)}
        if gd0 > 0 and gd1 > 0:
            w0, w1 = Fraction(gd0, d0), Fraction(gd1, d1)
            gr0, gr1 = Fraction(gn0, gd0), Fraction(gn1, gd1)
            rate = (w0 + w1) / 2 * (gr1 - gr0)
            mix = (gr0 + gr1) / 2 * (w1 - w0)
            assert rate + mix == net  # 정확 산술에서는 항등식이 정확히 성립해야 한다
            total_rate += rate
            total_mix += mix
            rate_values.append(rate)
            group.update({"comparable": True, "rate_effect": float(rate),
                          "mix_effect": float(mix)})
        else:
            entry_exit += net
            group["comparable"] = False
        groups.append(group)

    dc = abs(entry_exit) <= FLOAT_TOL
    amb_dc = amb_tol(entry_exit)
    s_rate, s_delta = sign_tol(total_rate), sign_tol(delta_r)
    cd = dc and s_rate != 0 and s_delta != 0 and s_rate != s_delta
    amb_cd = amb_dc or amb_tol(total_rate) or amb_tol(delta_r)
    signs = {sign_tol(r) for r in rate_values}
    simpson = (dc and len(rate_values) >= 2 and len(signs) == 1 and 0 not in signs
               and signs != {s_delta} and s_delta != 0)
    amb_simpson = amb_dc or amb_tol(delta_r) or any(amb_tol(r) for r in rate_values)
    ratio = (abs(delta_r) / gross) if gross else None
    heavy = gross > GROSS_EPSILON and ratio < CANCELLATION_THRESHOLD
    amb_heavy = ratio is not None and amb_thr(ratio, CANCELLATION_THRESHOLD)
    suppress = cd or not dc or heavy or abs(delta_r) <= SHARE_EPSILON
    amb_suppress = amb_cd or amb_dc or amb_heavy or amb_thr(abs(delta_r), SHARE_EPSILON)
    flags = {"decomposition_complete": dc, "composition_dominant": cd,
             "simpson_strict": simpson, "heavy_cancellation": heavy,
             "suppress_top_contributor": suppress}
    ambiguous = [n for n, a in (("decomposition_complete", amb_dc),
                                ("composition_dominant", amb_cd),
                                ("simpson_strict", amb_simpson),
                                ("heavy_cancellation", amb_heavy),
                                ("suppress_top_contributor", amb_suppress)) if a]
    return {"dimensions": dims, "cross": cross, "status": "ok", "observed_cells": cells,
            "groups": groups,
            "totals": {"total_rate_effect": float(total_rate),
                       "total_mix_effect": float(total_mix),
                       "entry_exit_effect": float(entry_exit), "gross_movement": float(gross)},
            "flags": flags, "ambiguous_flags": ambiguous}


def analyze(derived_dir, experiment, metric_name):
    metric = experiment["metrics"][metric_name]
    gdims = experiment["grain_dimensions"]
    rows = load(os.path.join(derived_dir, experiment["file"]), gdims, metric["columns"])
    cur_all = window_sum(rows, experiment["current"], gdims, [])
    base_all = window_sum(rows, experiment["baseline"], gdims, [])
    width = len(metric["columns"])
    oc = cur_all.get((), [0] * width)
    ob = base_all.get((), [0] * width)
    if metric["kind"] == "additive":
        c, b = oc[0], ob[0]
        comparison = {"current": c, "baseline": b, "delta": c - b,
                      "relative_change": _f(Fraction(c - b, b)) if b else None}
    else:
        if oc[1] == 0 or ob[1] == 0:
            return {"refused": "aggregate"}
        rc, rb = Fraction(oc[0], oc[1]), Fraction(ob[0], ob[1])
        comparison = {"current": float(rc), "baseline": float(rb), "delta": float(rc - rb),
                      "relative_change": _f((rc - rb) / rb) if rb else None}
    branches = [[]] + [[d] for d in experiment["breakdowns"]]
    if len(experiment["breakdowns"]) > 1:
        branches.append(list(experiment["breakdowns"]))
    out = []
    for dims in branches:
        cross = len(dims) > 1
        cur = window_sum(rows, experiment["current"], gdims, dims)
        base = window_sum(rows, experiment["baseline"], gdims, dims)
        if metric["kind"] == "additive":
            out.append(additive_branch(cur, base, dims, cross))
        else:
            out.append(ratio_branch(cur, base, dims, cross))
    return {"metric": {"name": metric_name, "kind": metric["kind"]},
            "comparison": comparison, "breakdowns": out}


def main_stdin():
    request = json.loads(sys.stdin.read())
    results = {}
    for experiment in request["experiments"]:
        for metric_name in experiment["metrics"]:
            results["%s/%s" % (experiment["id"], metric_name)] = analyze(
                request["derived_dir"], experiment, metric_name)
    return results


if __name__ == "__main__":
    json.dump(main_stdin(), sys.stdout)
