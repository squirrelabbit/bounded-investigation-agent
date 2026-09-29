"""사전등록 10절: 손상 M1~M14 와, 그 동작을 분류하는 규칙.

## 분류 규칙 (결과를 보기 전에 코드로 고정)

`classify(outcome, truth)`:

1. 손상이 0행에 적용됐으면 → `invalid` (무효, 채점하지 않는다).
2. 명시적 거부 예외(SpecError·RequestError·AnalysisRefused·LoaderRejected) → `reject`.
   그 밖의 예외 → `error` (분류표 밖, 그대로 보고).
3. 결과가 **정답 기준(truth)** 과 같으면(정수 정확, 실수 |차이| <= 1e-9, 순위·플래그 포함) → `valid-correct`.
4. 다르고, 어떤 breakdown 에서든 truth 에 없던 경고가 새로 섰으면 → `caveat`.
   경고 = `suppress_top_contributor`·`heavy_cancellation`·`composition_dominant`·`simpson_strict` 가 참,
   `decomposition_complete` 가 거짓, 또는 status 가 `omitted`.
5. 다른데 새 경고가 없으면 → `valid-silent-wrong`.

## 정답 기준(truth)

손상은 "같은 현실을 망가뜨린 입력" 이므로 기본 truth 는 **깨끗한 E1 실행**이다. 예외는 M11 하나다.
사전등록은 M11 의 기대를 "`__UNKNOWN__` 그룹으로 모인다(정책)" 로 적었다 — 즉 정답은 깨끗한 실행이
아니라 **빈칸이 된 행이 버려지지 않고 `__UNKNOWN__` 으로 합쳐진 결과**다. 그래서 M11 의 truth 는 빈칸
행들의 제품을 `__UNKNOWN__` 으로 바꾸고 grain 이 같아진 행을 **합산**한 입력에 대한 엔진 실행이다.
(깨끗한 실행과 비교하면 정책대로 동작해도 항상 "다르다" 가 되어 규칙이 무의미해진다. 이 선택은 결과를
보기 전에 여기 적었다. 깨끗한 실행 대비 차이도 `diff_vs_clean` 으로 함께 기록한다.)

## 채점 대상 지표

사전등록은 손상마다 어느 지표로 판정할지 적지 않았다(M4 만 지표별). 손상이 건드리는 열로 정한다:
건수(`complaints`)를 건드리는 M3·M9·M10·M13 은 `complaint_count`, `timely_*` 를 건드리는 M7·M8·M14 는
`timely_rate`, 행·키·날짜·열 구조를 건드리는 M2·M4·M5·M6·M11·M12 는 두 지표 모두. 나머지 조합도 모두
실행해 `supplementary` 로 기록하되 채점하지 않는다.
"""
from __future__ import annotations

import datetime as _dt
from typing import Dict, List, Sequence, Tuple

UNKNOWN = "__UNKNOWN__"
FLOAT_TOL = 1e-9

E1_BASELINE = ("2024-02-01", "2024-02-28")
E1_CURRENT = ("2024-03-01", "2024-03-28")

REJECT = "reject"
CAVEAT = "caveat"
VALID_CORRECT = "valid-correct"
SILENT_WRONG = "valid-silent-wrong"
INVALID = "invalid"
ERROR = "error"

COUNT = "complaint_count"
RATE = "timely_rate"

# 사전등록 10절 표. `expected` 의 키가 채점 대상 지표다.
TABLE = (
    ("M1", "raw_loader", {"loader": REJECT}),
    ("M2", "engine_input", {COUNT: VALID_CORRECT, RATE: VALID_CORRECT}),
    ("M3", "engine_input", {COUNT: REJECT}),
    ("M4", "engine_input", {COUNT: SILENT_WRONG, RATE: CAVEAT}),
    ("M5", "engine_input", {COUNT: SILENT_WRONG, RATE: SILENT_WRONG}),
    ("M6", "engine_input", {COUNT: SILENT_WRONG, RATE: SILENT_WRONG}),
    ("M7", "engine_input", {RATE: REJECT}),
    ("M8", "engine_input", {RATE: REJECT}),
    ("M9", "engine_input", {COUNT: REJECT}),
    ("M10", "engine_input", {COUNT: REJECT}),
    ("M11", "engine_input", {COUNT: VALID_CORRECT, RATE: VALID_CORRECT}),
    ("M12", "engine_input", {COUNT: REJECT, RATE: REJECT}),
    ("M13", "engine_input", {COUNT: REJECT}),
    ("M14", "engine_input", {RATE: REJECT}),
)

Row = Dict[str, str]


def _in(day: str, window: Tuple[str, str]) -> bool:
    return window[0] <= day <= window[1]


def _copy(rows: Sequence[Row]) -> List[Row]:
    return [dict(r) for r in rows]


def _target_product(rows: Sequence[Row]) -> str:
    """행 수가 가장 많고 timely_yes > 0 인 행이 있는 제품. 동률은 이름순."""
    counts: Dict[str, int] = {}
    positive = set()
    for r in rows:
        counts[r["product"]] = counts.get(r["product"], 0) + 1
        if int(r["timely_yes"]) > 0:
            positive.add(r["product"])
    candidates = [p for p in counts if p in positive and p.strip()]
    return sorted(candidates, key=lambda p: (-counts[p], p))[0]


def m2(rows, header):
    extra = [dict(rows[i]) for i in range(0, len(rows), 10)]
    return rows + extra, header, {"rows_duplicated": len(extra), "touched": len(extra)}


def m3(rows, header):
    extra = []
    for i in range(0, len(rows), 100):
        dup = dict(rows[i])
        dup["complaints"] = str(int(dup["complaints"]) + 1)
        extra.append(dup)
    return rows + extra, header, {"rows_duplicated_with_different_count": len(extra),
                                  "touched": len(extra)}


def m4(rows, header):
    products = sorted({r["product"] for r in rows if _in(r["day"], E1_CURRENT)})
    chosen = [products[i] for i in range(0, len(products), 5)]
    out = _copy(rows)
    touched = 0
    for r in out:
        if _in(r["day"], E1_CURRENT) and r["product"] in chosen:
            r["product"] = r["product"] + " [renamed]"
            touched += 1
    return out, header, {"distinct_products_in_current": len(products),
                         "products_renamed": len(chosen), "touched": touched}


def m5(rows, header):
    start = _dt.date.fromisoformat(E1_CURRENT[0])
    days = {(start + _dt.timedelta(days=k)).isoformat() for k in (13, 14, 15)}
    out = [dict(r) for r in rows if r["day"] not in days]
    return out, header, {"days_removed": sorted(days), "touched": len(rows) - len(out)}


def m6(rows, header):
    out = _copy(rows)
    touched = 0
    for r in out:
        if _in(r["day"], E1_CURRENT):
            r["day"] = (_dt.date.fromisoformat(r["day"]) + _dt.timedelta(days=1)).isoformat()
            touched += 1
    return out, header, {"touched": touched}


def m7(rows, header):
    target = _target_product(rows)
    out = _copy(rows)
    touched = 0
    with_numerator = 0
    for r in out:
        if r["product"] == target:
            r["timely_known"] = "0"
            touched += 1
            with_numerator += 1 if int(r["timely_yes"]) > 0 else 0
    return out, header, {"target_product": target, "rows_with_timely_yes_positive": with_numerator,
                         "touched": touched}


def m8(rows, header):
    target = _target_product(rows)
    out = _copy(rows)
    touched = 0
    for r in out:
        if r["product"] == target:
            r["timely_yes"] = str(int(r["timely_known"]) + 1)
            touched += 1
    return out, header, {"target_product": target, "touched": touched}


def _set_first_count(value_fn, rows, header):
    out = _copy(rows)
    before = out[0]["complaints"]
    out[0]["complaints"] = value_fn(before)
    return out, header, {"row_index": 0, "before": before, "after": out[0]["complaints"],
                         "touched": 1}


def m9(rows, header):
    return _set_first_count(lambda v: "%d.0" % int(v), rows, header)


def m10(rows, header):
    return _set_first_count(lambda v: "1,234", rows, header)


def m13(rows, header):
    return _set_first_count(lambda v: str(-max(1, int(v))), rows, header)


def m11(rows, header):
    out = _copy(rows)
    touched = 0
    for i in range(0, len(out), 20):
        out[i]["product"] = ""
        touched += 1
    return out, header, {"touched": touched}


def m11_truth(rows, header):
    """정책대로라면 나와야 할 입력: 빈칸 행의 제품은 `__UNKNOWN__`, grain 이 같아진 행은 합산."""
    mutated, _, _ = m11(rows, header)
    merged: Dict[Tuple[str, str, str], List[int]] = {}
    order: List[Tuple[str, str, str]] = []
    collisions = 0
    for r in mutated:
        key = (r["day"], r["product"] if r["product"].strip() else UNKNOWN, r["issue"])
        values = [int(r["complaints"]), int(r["timely_yes"]), int(r["timely_known"])]
        if key in merged:
            collisions += 1
            merged[key] = [a + b for a, b in zip(merged[key], values)]
        else:
            merged[key] = values
            order.append(key)
    out = [{"day": k[0], "product": k[1], "issue": k[2], "complaints": str(merged[k][0]),
            "timely_yes": str(merged[k][1]), "timely_known": str(merged[k][2])} for k in order]
    return out, {"grain_collisions_after_unknown_mapping": collisions}


def m12(rows, header):
    new_header = [h for h in header if h != "issue"]
    out = [{k: v for k, v in r.items() if k != "issue"} for r in rows]
    return out, new_header, {"column_removed": "issue", "touched": len(out)}


def m14(rows, header):
    out = _copy(rows)
    touched = 0
    for r in out:
        if _in(r["day"], E1_BASELINE):
            r["timely_known"] = "0"
            touched += 1
    return out, header, {"touched": touched}


ENGINE_MUTATIONS = {"M2": m2, "M3": m3, "M4": m4, "M5": m5, "M6": m6, "M7": m7, "M8": m8,
                    "M9": m9, "M10": m10, "M11": m11, "M12": m12, "M13": m13, "M14": m14}


def m1_stream(raw_rows, every: int = 10):
    """원본 allowlist 튜플 스트림에서 10행마다 한 행을 Complaint ID 째 한 번 더 내보낸다.
    `counter["injected"]` 에 실제로 복제한 행 수가 남는다."""
    counter = {"injected": 0, "rows": 0}

    def gen():
        for i, row in enumerate(raw_rows):
            counter["rows"] += 1
            yield row
            if i % every == 0:
                counter["injected"] += 1
                yield row
    return gen(), counter


# ---------------------------------------------------------------- 분류 규칙


def values_equal(a, b) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, int) and isinstance(b, int):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) <= FLOAT_TOL
    if isinstance(a, dict) and isinstance(b, dict):
        return set(a) == set(b) and all(values_equal(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(values_equal(x, y) for x, y in zip(a, b))
    return a == b


def warnings_of(breakdown: Dict[str, object]) -> set:
    if breakdown.get("status") == "omitted":
        return {"omitted"}
    flags = breakdown.get("flags", {})
    out = {n for n in ("suppress_top_contributor", "heavy_cancellation",
                       "composition_dominant", "simpson_strict") if flags.get(n) is True}
    if flags.get("decomposition_complete") is False:
        out.add("decomposition_incomplete")
    return out


def new_warnings(result: Dict[str, object], truth: Dict[str, object]) -> List[Dict[str, object]]:
    found = []
    for got, ref in zip(result["breakdowns"], truth["breakdowns"]):
        extra = warnings_of(got) - warnings_of(ref)
        if extra:
            found.append({"dimensions": got["dimensions"], "cross": got["cross"],
                          "new": sorted(extra)})
    return found


def classify(outcome: Dict[str, object], truth: Dict[str, object]) -> str:
    if outcome.get("touched", 0) <= 0:
        return INVALID
    if outcome.get("error"):
        return REJECT if outcome.get("error_is_refusal") else ERROR
    if values_equal(outcome["result"], truth):
        return VALID_CORRECT
    if new_warnings(outcome["result"], truth):
        return CAVEAT
    return SILENT_WRONG


def is_defense_failure(expected: str, actual: str) -> bool:
    return expected in (REJECT, CAVEAT) and actual == SILENT_WRONG


def diff_summary(result: Dict[str, object], ref: Dict[str, object]) -> Dict[str, object]:
    """두 결과가 어디서 갈리는지 개수로. 분류에는 쓰지 않는다(기록용)."""
    out = {"comparison_equal": values_equal(result["comparison"], ref["comparison"]),
           "comparison": {"got": result["comparison"], "ref": ref["comparison"]},
           "breakdowns": []}
    for got, want in zip(result["breakdowns"], ref["breakdowns"]):
        entry = {"dimensions": got["dimensions"], "status": got["status"],
                 "ref_status": want["status"]}
        if got["status"] == "ok" and want["status"] == "ok":
            g = {tuple(sorted(x["key"].items())): x for x in got["groups"]}
            w = {tuple(sorted(x["key"].items())): x for x in want["groups"]}
            entry.update({
                "groups_only_in_result": len(set(g) - set(w)),
                "groups_only_in_ref": len(set(w) - set(g)),
                "groups_changed": sum(1 for k in set(g) & set(w) if not values_equal(g[k], w[k])),
                "flags_changed": {k: [want["flags"].get(k), got["flags"].get(k)]
                                  for k in set(got["flags"]) | set(want["flags"])
                                  if got["flags"].get(k) != want["flags"].get(k)},
                "ranking_equal": values_equal(got["ranking"], want["ranking"]),
            })
        out["breakdowns"].append(entry)
    return out
