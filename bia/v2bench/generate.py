"""벤치마크 데이터와 oracle 생성기.

`bia.analysis` 도 `bia.metrics` 도 import 하지 않는다. 같은 코드를 부르면 oracle 이
production 의 float 처리나 집계 버그를 그대로 복제해 벤치마크가 아무것도 증명하지
못한다. 그래서 도메인 계약(grain·metric 컬럼)과 분해 수식을 여기서 다시 진술하고
`Fraction` 으로 정확히 계산한다. float 누산은 어디에도 쓰지 않는다.

난수는 쓰지 않는다 — 모든 값은 사례 선언이나 인덱스의 결정론적 함수다. 출력 순서는
전부 정렬된다. 그래서 재실행과 인터프리터 버전에 걸쳐 바이트가 같다.
"""
from __future__ import annotations

import csv
import json
import os
import shutil
from fractions import Fraction
from typing import Dict, List, Optional, Sequence, Tuple

from .cases import CASES, CaseSpec, Row

OUT_ROOT = "data/v2"

# 아래 넷은 production 의 사전등록 상수를 **복사**한 값이다. import 하지 않는다.
# 값이 갈라지면 벤치마크가 깨지는 쪽이 옳다 — 상수는 불변이기 때문이다.
CROSS_CELL_LIMIT = 1000
CANCELLATION_THRESHOLD = Fraction(1, 5)        # 0.20
SHARE_EPSILON = Fraction(1, 10000)             # 0.0001
FLOAT_TOL = Fraction(1, 10 ** 9)               # 1e-9
UNKNOWN = "__UNKNOWN__"

# production 이 rank 할 수 있는 필드. 그 밖의 값은 계산하지 않고 멈춘다.
RANK_FIELDS = ("net_contribution", "group_delta", "rate_effect", "mix_effect")

# 도메인 계약의 독립 재진술. bia.domains 를 읽지 않는다.
GRAIN_DIMENSIONS = {
    "ecommerce": ("channel", "device", "category"),
    "support_ops": ("queue", "priority"),
    "complaints": ("product", "complaint_type"),
}
METRICS = {
    ("ecommerce", "revenue"): ("additive", ("revenue_krw",)),
    ("ecommerce", "orders"): ("additive", ("orders",)),
    ("ecommerce", "conversion_rate"): ("ratio", ("orders", "sessions")),
    ("support_ops", "tickets_received"): ("additive", ("received",)),
    ("support_ops", "sla_resolution_rate"): ("ratio", ("resolved_within_sla", "received")),
    ("complaints", "complaint_count"): ("additive", ("count",)),
}


class OracleError(Exception):
    """사례 선언과 계산이 어긋났다. 조용히 넘어가지 않는다.

    `AssertionError` 를 상속하지 않는다. 호출자의 `except AssertionError` 가
    계약 위반을 삼켜 벤치마크가 조용히 통과하는 것을 막는다.
    """


def _norm(value: str) -> str:
    stripped = (value or "").strip()
    return stripped if stripped else UNKNOWN


def _metric_of(case: CaseSpec) -> Tuple[str, Tuple[str, ...]]:
    try:
        return METRICS[(case.domain, case.metric)]
    except KeyError:
        raise OracleError("%s: unknown metric %s.%s" % (case.case_id, case.domain, case.metric))


def _rows_from_source(path: str, dimensions: Sequence[str],
                      columns: Sequence[str]) -> List[Row]:
    out: List[Row] = []
    with open(path, "r", encoding="utf-8", newline="") as handle:
        for record in csv.DictReader(handle):
            out.append(Row(
                day=record["day"],
                keys=tuple(sorted((d, _norm(record[d])) for d in dimensions)),
                measures=tuple(sorted((c, int(record[c])) for c in columns)),
            ))
    return out


def case_rows(case: CaseSpec) -> List[Row]:
    """사례의 물리 행. 정규화(빈 값 → __UNKNOWN__)까지 끝난 상태다."""
    dimensions = GRAIN_DIMENSIONS[case.domain]
    _kind, columns = _metric_of(case)
    if case.source_csv:
        if case.rows:
            raise OracleError("%s: source_csv 와 rows 를 동시에 쓸 수 없다" % case.case_id)
        return _rows_from_source(case.source_csv, dimensions, columns)
    out: List[Row] = []
    for row in case.rows:
        keys = dict(row.keys)
        if sorted(keys) != sorted(dimensions):
            raise OracleError(
                "%s: 행의 차원 %s 가 도메인 grain %s 와 다르다"
                % (case.case_id, sorted(keys), sorted(dimensions)))
        measures = dict(row.measures)
        if sorted(measures) != sorted(columns):
            raise OracleError(
                "%s: 행의 측정값 %s 가 metric 컬럼 %s 와 다르다"
                % (case.case_id, sorted(measures), sorted(columns)))
        out.append(Row(day=row.day,
                       keys=tuple(sorted((d, _norm(v)) for d, v in keys.items())),
                       measures=tuple(sorted(measures.items()))))
    return out


def dedupe(case: CaseSpec, rows: Sequence[Row]) -> List[Row]:
    """물리 grain 중복 제거. 충돌은 거부 사례에서만 허용한다."""
    seen: Dict[Tuple[str, ...], Row] = {}
    conflicts: List[str] = []
    for row in rows:
        key = (row.day,) + tuple(v for _d, v in row.keys)
        prior = seen.get(key)
        if prior is None:
            seen[key] = row
        elif prior.measures != row.measures:
            conflicts.append("|".join(key))
    if conflicts and not case.expect_refused:
        raise OracleError("%s: 충돌하는 중복 행이 있는데 거부 사례가 아니다: %s"
                          % (case.case_id, sorted(set(conflicts))))
    return [seen[k] for k in sorted(seen)]


def _window(rows: Sequence[Row], window: Tuple[str, str]) -> List[Row]:
    start, end = window
    return [r for r in rows if start <= r.day <= end]


def _cells(rows: Sequence[Row], dimensions: Sequence[str],
           columns: Sequence[str]) -> Dict[Tuple[str, ...], Dict[str, int]]:
    out: Dict[Tuple[str, ...], Dict[str, int]] = {}
    for row in rows:
        keys = dict(row.keys)
        cell = out.setdefault(tuple(keys[d] for d in dimensions),
                              dict((c, 0) for c in columns))
        for column, value in row.measures:
            cell[column] += value
    return out


def _reject_tolerance_band(value: Fraction, what: str) -> None:
    """oracle 은 정확 0 을, production 은 `|x| <= 1e-9` 를 0 으로 본다.

    사례 값이 그 틈에 들어오면 둘의 판정이 갈리고, 그 불일치는 엔진 결함처럼
    보인다. 그런 사례는 계산하지 말고 멈춘다 — 데이터가 band 를 피해야 한다.
    """
    if 0 < abs(value) <= FLOAT_TOL:
        raise OracleError(
            "%s=%s 가 production 의 tolerance band (0 < |x| <= %s) 안에 있다. "
            "oracle 은 0 이 아니라고, 엔진은 0 이라고 판정한다."
            % (what, value, FLOAT_TOL))


def _sign(value: Fraction, what: str = "sign input") -> int:
    _reject_tolerance_band(value, what)
    if value == 0:
        return 0
    return 1 if value > 0 else -1


def _f(value) -> Optional[float]:
    return None if value is None else float(value)


def _rank(entries: Sequence[Tuple[Tuple[str, ...], Dict[str, Optional[Fraction]]]],
          dimensions: Sequence[str], rank_by: str) -> List[str]:
    """RANK 의 재진술: `rank_by` 값 내림차순, 동률은 (차원명, 값) 오름차순.

    값이 없는(None) 그룹은 production 과 같이 정렬 키 0 으로 본다.
    지원하지 않는 `rank_by` 는 계산하지 않는다 — 틀린 순서를 정답으로
    기록하느니 멈추는 쪽이 옳다.
    """
    if rank_by not in RANK_FIELDS:
        raise OracleError("지원하지 않는 rank_by: %r; 지원: %s"
                          % (rank_by, list(RANK_FIELDS)))
    ordered = []
    for key, values in entries:
        if rank_by not in values:
            raise OracleError("그룹 %s 에 rank_by=%r 에 해당하는 값이 없다"
                              % ("|".join(key), rank_by))
        value = values[rank_by]
        ordered.append(((Fraction(0) if value is None else -value),
                        sorted(zip(dimensions, key)), key))
    ordered.sort(key=lambda item: (item[0], item[1]))
    return ["|".join(key) for _value, _tie, key in ordered]


def additive_breakdown(current, baseline, universe, column,
                       dimensions, rank_by) -> Dict[str, object]:
    groups: Dict[str, Dict[str, object]] = {}
    entries: List[Tuple[Tuple[str, ...], Dict[str, Optional[Fraction]]]] = []
    gross = Fraction(0)
    for key in universe:
        delta = Fraction(current.get(key, {}).get(column, 0)
                         - baseline.get(key, {}).get(column, 0))
        groups["|".join(key)] = {
            "expect_net_contribution": float(delta),
            "expect_group_delta": int(delta),
            "expect_comparable": True,
        }
        # additive 그룹에는 rate/mix 가 없다. 그 키로 정렬을 요구하면 _rank 가 멈춘다.
        entries.append((key, {"net_contribution": delta, "group_delta": delta}))
        gross += abs(delta)
    return {
        "_exact_totals": {"gross_movement": gross},
        "expect_totals": {"gross_movement": float(gross)},
        "expect_flags": {"decomposition_complete": True,
                         "composition_dominant": False,
                         "simpson_strict": False,
                         "heavy_cancellation": False,
                         "suppress_top_contributor": False},
        "expect_ranking": _rank(entries, dimensions, rank_by),
        "groups": groups,
    }


def ratio_breakdown(current, baseline, universe, numerator, denominator,
                    dimensions, rank_by) -> Dict[str, object]:
    """spec 의 분해 수식을 Fraction 으로 다시 진술한다."""
    d0 = Fraction(sum(c[denominator] for c in baseline.values()))
    d1 = Fraction(sum(c[denominator] for c in current.values()))
    n0 = Fraction(sum(c[numerator] for c in baseline.values()))
    n1 = Fraction(sum(c[numerator] for c in current.values()))
    r0 = n0 / d0 if d0 else Fraction(0)
    r1 = n1 / d1 if d1 else Fraction(0)
    delta = r1 - r0

    raw: Dict[str, Dict[str, object]] = {}
    nets: Dict[str, Fraction] = {}
    entries: List[Tuple[Tuple[str, ...], Dict[str, Optional[Fraction]]]] = []
    total_rate = Fraction(0)
    total_mix = Fraction(0)
    entry_exit = Fraction(0)
    gross = Fraction(0)
    comparable_rate_signs = set()
    comparable_count = 0

    for key in universe:
        cur = current.get(key, {})
        base = baseline.get(key, {})
        gn1, gd1 = Fraction(cur.get(numerator, 0)), Fraction(cur.get(denominator, 0))
        gn0, gd0 = Fraction(base.get(numerator, 0)), Fraction(base.get(denominator, 0))
        net = (gn1 / d1 if d1 else Fraction(0)) - (gn0 / d0 if d0 else Fraction(0))
        label = "|".join(key)
        entry: Dict[str, object] = {"expect_net_contribution": float(net)}
        if gd0 > 0 and gd1 > 0:
            w0, w1 = gd0 / d0, gd1 / d1
            gr0, gr1 = gn0 / gd0, gn1 / gd1
            rate = ((w0 + w1) / 2) * (gr1 - gr0)
            mix = ((gr0 + gr1) / 2) * (w1 - w0)
            if rate + mix != net:
                raise OracleError("split broken at %s" % label)
            entry["expect_comparable"] = True
            entry["expect_rate_effect"] = float(rate)
            entry["expect_mix_effect"] = float(mix)
            total_rate += rate
            total_mix += mix
            comparable_rate_signs.add(_sign(rate, "%s rate_effect" % label))
            comparable_count += 1
            rankable = {"net_contribution": net,
                        "rate_effect": rate, "mix_effect": mix}
        else:
            entry["expect_comparable"] = False
            entry_exit += net
            # production 은 비교 불가 그룹의 rate/mix 를 None 으로 두고 정렬 키 0 으로 쓴다.
            rankable = {"net_contribution": net,
                        "rate_effect": None, "mix_effect": None}
        raw[label] = entry
        nets[label] = net
        entries.append((key, rankable))
        gross += abs(net)

    if total_rate + total_mix + entry_exit != delta:
        raise OracleError("three-term identity broken")
    if sum(nets.values()) != delta:
        raise OracleError("additivity broken")

    _reject_tolerance_band(entry_exit, "entry_exit_effect")
    complete = entry_exit == 0
    heavy = gross > 0 and abs(delta) / gross < CANCELLATION_THRESHOLD
    sign_total_rate = _sign(total_rate, "total_rate_effect")
    sign_delta = _sign(delta, "delta")
    dominant = (complete and sign_total_rate != 0 and sign_delta != 0
                and sign_total_rate != sign_delta)
    simpson = (complete and comparable_count >= 2
               and len(comparable_rate_signs) == 1
               and 0 not in comparable_rate_signs
               and comparable_rate_signs != set([sign_delta])
               and sign_delta != 0)
    suppress = dominant or not complete or heavy or abs(delta) <= SHARE_EPSILON

    for label, entry in raw.items():
        share = None
        if (not dominant and complete and not heavy
                and entry["expect_comparable"] and abs(delta) > SHARE_EPSILON):
            candidate = nets[label] / delta
            if 0 < candidate <= 1:
                share = candidate
        entry["expect_contribution_share"] = _f(share)

    return {
        "_exact_totals": {"total_rate_effect": total_rate,
                          "total_mix_effect": total_mix,
                          "entry_exit_effect": entry_exit,
                          "gross_movement": gross},
        "expect_totals": {"total_rate_effect": float(total_rate),
                          "total_mix_effect": float(total_mix),
                          "entry_exit_effect": float(entry_exit),
                          "gross_movement": float(gross)},
        "expect_flags": {"decomposition_complete": complete,
                         "composition_dominant": dominant,
                         "simpson_strict": simpson,
                         "heavy_cancellation": heavy,
                         "suppress_top_contributor": suppress},
        "expect_ranking": _rank(entries, dimensions, rank_by),
        "groups": raw,
    }


def _branch_dimensions(case: CaseSpec) -> List[Tuple[str, ...]]:
    branches = [(d,) for d in case.breakdowns]
    if len(case.breakdowns) > 1:
        branches.append(tuple(case.breakdowns))
    return branches


def case_oracle(case: CaseSpec) -> Dict[str, object]:
    kind, columns = _metric_of(case)
    entry: Dict[str, object] = {
        "domain": case.domain, "metric": case.metric, "kind": kind,
        "breakdown_dimensions": list(case.breakdowns), "rank_by": case.rank_by,
        "measure_columns": list(columns),
        "grain_dimensions": sorted(GRAIN_DIMENSIONS[case.domain]),
        "baseline": list(case.baseline), "current": list(case.current),
        "notes": case.notes,
    }
    if case.expect_refused:
        entry["expect_refused"] = {"stage": case.expect_refused[0],
                                   "reason_substring": case.expect_refused[1]}
        return entry

    rows = dedupe(case, case_rows(case))
    base_rows = _window(rows, case.baseline)
    cur_rows = _window(rows, case.current)

    computed: Dict[str, object] = {}
    if kind == "additive":
        column = columns[0]
        base_total = Fraction(sum(dict(r.measures)[column] for r in base_rows))
        cur_total = Fraction(sum(dict(r.measures)[column] for r in cur_rows))
        delta = cur_total - base_total
        relative = (delta / base_total) if base_total else None
        entry["expect_baseline"] = int(base_total)
        entry["expect_current"] = int(cur_total)
    else:
        numerator, denominator = columns
        d0 = Fraction(sum(dict(r.measures)[denominator] for r in base_rows))
        d1 = Fraction(sum(dict(r.measures)[denominator] for r in cur_rows))
        if d0 == 0 or d1 == 0:
            raise OracleError("%s: 전체 분모가 0 인데 거부 사례가 아니다" % case.case_id)
        base_total = Fraction(sum(dict(r.measures)[numerator] for r in base_rows)) / d0
        cur_total = Fraction(sum(dict(r.measures)[numerator] for r in cur_rows)) / d1
        delta = cur_total - base_total
        relative = (delta / base_total) if base_total else None
        entry["expect_baseline"] = float(base_total)
        entry["expect_current"] = float(cur_total)

    entry["expect_delta"] = float(delta)
    entry["expect_relative_change"] = _f(relative)
    computed["delta"] = delta
    computed["baseline"] = base_total
    computed["current"] = cur_total

    breakdowns: Dict[str, object] = {}
    omitted: List[str] = []
    for index, dimensions in enumerate(_branch_dimensions(case)):
        name = ",".join(dimensions)
        current = _cells(cur_rows, dimensions, columns)
        baseline = _cells(base_rows, dimensions, columns)
        universe = tuple(sorted(set(current) | set(baseline)))
        cross = len(dimensions) > 1
        if cross and len(universe) > CROSS_CELL_LIMIT:
            omitted.append(name)
            continue
        if kind == "additive":
            block = additive_breakdown(current, baseline, universe, columns[0],
                                       dimensions, case.rank_by)
        else:
            block = ratio_breakdown(current, baseline, universe,
                                    columns[0], columns[1], dimensions,
                                    case.rank_by)
        block["dimensions"] = list(dimensions)
        block["observed_cells"] = len(universe)
        exact_totals = block.pop("_exact_totals")
        breakdowns[name] = block
        if index == 0:
            for key, value in exact_totals.items():
                computed.setdefault(key, value)
            for key, value in block["expect_flags"].items():
                computed.setdefault(key, value)

    entry["breakdowns"] = breakdowns
    entry["expect_omitted_breakdowns"] = sorted(omitted)
    if case.golden:
        entry["golden"] = _apply_golden(case, entry, computed)
    return entry


_GOLDEN_FIELDS = {"delta": "expect_delta", "baseline": "expect_baseline",
                  "current": "expect_current"}


def _exact(value) -> object:
    if isinstance(value, bool):
        return value
    if isinstance(value, Fraction):
        return value
    if isinstance(value, str):
        return Fraction(value)
    if isinstance(value, int):
        return Fraction(value)
    if isinstance(value, float):
        return Fraction(str(value))
    raise OracleError("golden 값의 타입을 모른다: %r" % (value,))


def _apply_golden(case: CaseSpec, entry: Dict[str, object],
                  computed: Dict[str, object]) -> Dict[str, object]:
    """손으로 고정한 값이 계산과 다르면 즉시 실패한다. 조정하지 않는다."""
    recorded: Dict[str, object] = {}
    for key in sorted(case.golden):
        want = case.golden[key]
        if key not in computed:
            raise OracleError("%s: golden 키 %r 에 대응하는 계산값이 없다"
                              % (case.case_id, key))
        got = computed[key]
        if isinstance(want, bool) or isinstance(got, bool):
            if bool(want) != bool(got):
                raise OracleError("%s: golden %s=%r 인데 계산은 %r"
                                  % (case.case_id, key, want, got))
            recorded[key] = bool(want)
            continue
        want_exact = _exact(want)
        if want_exact != got:
            raise OracleError("%s: golden %s=%s 인데 계산은 %s"
                              % (case.case_id, key, want_exact, got))
        recorded[key] = float(want_exact)
        # 계산값이 아니라 고정값을 기록한다. 둘이 같다는 것은 방금 확인했다.
        if key in _GOLDEN_FIELDS:
            field = _GOLDEN_FIELDS[key]
            entry[field] = (int(want_exact) if isinstance(entry[field], int)
                            else float(want_exact))
        else:
            first = case.breakdowns[0]
            totals = entry["breakdowns"].get(first, {}).get("expect_totals", {})
            if key not in totals:
                raise OracleError(
                    "%s: golden 키 %r 는 필드도 아니고 첫 breakdown(%s) 의 "
                    "totals 에도 없다. 조용히 버리지 않는다."
                    % (case.case_id, key, first))
            totals[key] = float(want_exact)
    return recorded


def write_case_csv(case: CaseSpec, root: str) -> str:
    directory = os.path.join(root, case.case_id)
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, "metrics.csv")
    if case.source_csv:
        # v1 시나리오는 바이트 그대로 복사한다. 재생성하면 regression gate 가 약해진다.
        shutil.copyfile(case.source_csv, path)
        return path
    dimensions = sorted(GRAIN_DIMENSIONS[case.domain])
    _kind, columns = _metric_of(case)
    header = ["day"] + dimensions + sorted(columns)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header)
        for row in case.rows:
            keys = dict(row.keys)
            measures = dict(row.measures)
            writer.writerow([row.day] + [keys[d] for d in dimensions]
                            + [measures[c] for c in sorted(columns)])
    return path


def main() -> None:
    root = OUT_ROOT
    if os.path.isdir(root):
        shutil.rmtree(root)
    os.makedirs(root)
    cases: Dict[str, object] = {}
    seen = set()
    for case in CASES:
        if case.case_id in seen:
            raise OracleError("중복 case_id: %s" % case.case_id)
        seen.add(case.case_id)
        write_case_csv(case, root)
        cases[case.case_id] = case_oracle(case)
    payload = {"cross_cell_limit": CROSS_CELL_LIMIT,
               "cancellation_threshold": float(CANCELLATION_THRESHOLD),
               "share_epsilon": float(SHARE_EPSILON),
               "case_count": len(CASES),
               "cases": cases}
    with open(os.path.join(root, "oracle.json"), "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    print("wrote %d cases to %s" % (len(CASES), root))


if __name__ == "__main__":
    main()
