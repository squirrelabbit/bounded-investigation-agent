"""디스크에 쓰인 파일에서 다시 계산해 oracle 과 대조한다.
생성기 내부 자료구조를 믿지 않는다.

여기의 계산은 `bia.v2bench.generate` 와도 공유하지 않는다 — 같은 함수를 부르면
생성기의 버그를 그대로 복제한다. CSV 를 직접 읽어 Fraction 으로 다시 센다.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import subprocess
import sys
from fractions import Fraction

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOL = Fraction(1, 10 ** 9)
UNKNOWN = "__UNKNOWN__"
CROSS_CELL_LIMIT = 1000


def digest(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def snapshot():
    out = {}
    for base, _dirs, files in os.walk(os.path.join(ROOT, "data", "v2")):
        for name in sorted(files):
            full = os.path.join(base, name)
            out[os.path.relpath(full, ROOT)] = digest(full)
    return out


def read_rows(path, dimensions, columns):
    rows = []
    with open(path, "r", encoding="utf-8", newline="") as handle:
        for record in csv.DictReader(handle):
            keys = {}
            for dimension in dimensions:
                raw = (record[dimension] or "").strip()
                keys[dimension] = raw if raw else UNKNOWN
            rows.append((record["day"], keys,
                         dict((c, int(record[c])) for c in columns)))
    return rows


def window(rows, bounds):
    start, end = bounds
    return [r for r in rows if start <= r[0] <= end]


def cells(rows, dimensions, columns):
    out = {}
    for _day, keys, measures in rows:
        cell = out.setdefault("|".join(keys[d] for d in dimensions),
                              dict((c, 0) for c in columns))
        for column in columns:
            cell[column] += measures[column]
    return out


def close(got, want, label):
    if abs(got - Fraction(str(want))) > TOL:
        raise AssertionError("%s: recomputed %s but the oracle says %s"
                             % (label, got, want))


def main():
    checks = 0
    before = snapshot()
    subprocess.check_call([sys.executable, "-m", "bia.v2bench.generate"], cwd=ROOT)
    after = snapshot()
    assert before == after, "재생성이 바이트 동일하지 않다"
    checks += 1

    with open(os.path.join(ROOT, "data", "v2", "oracle.json"), encoding="utf-8") as handle:
        oracle = json.load(handle)
    assert oracle["case_count"] == len(oracle["cases"]), "사례 수가 맞지 않는다"
    checks += 1

    on_disk = sorted(name for name in os.listdir(os.path.join(ROOT, "data", "v2"))
                     if os.path.isdir(os.path.join(ROOT, "data", "v2", name)))
    assert on_disk == sorted(oracle["cases"]), "디렉터리와 oracle 사례가 다르다"
    checks += 1

    for case_id, entry in sorted(oracle["cases"].items()):
        path = os.path.join(ROOT, "data", "v2", case_id, "metrics.csv")
        assert os.path.exists(path), case_id
        checks += 1
        if entry.get("expect_refused"):
            continue

        columns = entry["measure_columns"]
        rows = read_rows(path, entry["grain_dimensions"], columns)
        assert rows, case_id
        grain_keys = set()
        for day, keys, _measures in rows:
            grain_keys.add((day,) + tuple(keys[d] for d in entry["grain_dimensions"]))
        assert len(grain_keys) == len(rows), "%s: grain 중복 행" % case_id
        checks += 1

        base_rows = window(rows, entry["baseline"])
        cur_rows = window(rows, entry["current"])

        if entry["kind"] == "additive":
            column = columns[0]
            base = Fraction(sum(m[column] for _d, _k, m in base_rows))
            cur = Fraction(sum(m[column] for _d, _k, m in cur_rows))
        else:
            numerator, denominator = columns
            d0 = Fraction(sum(m[denominator] for _d, _k, m in base_rows))
            d1 = Fraction(sum(m[denominator] for _d, _k, m in cur_rows))
            assert d0 and d1, case_id
            base = Fraction(sum(m[numerator] for _d, _k, m in base_rows)) / d0
            cur = Fraction(sum(m[numerator] for _d, _k, m in cur_rows)) / d1
        close(base, entry["expect_baseline"], "%s baseline" % case_id)
        close(cur, entry["expect_current"], "%s current" % case_id)
        close(cur - base, entry["expect_delta"], "%s delta" % case_id)
        checks += 3

        for name, breakdown in sorted(entry["breakdowns"].items()):
            dimensions = breakdown["dimensions"]
            current = cells(cur_rows, dimensions, columns)
            baseline = cells(base_rows, dimensions, columns)
            universe = sorted(set(current) | set(baseline))
            assert len(universe) == breakdown["observed_cells"], "%s %s" % (case_id, name)
            assert sorted(breakdown["groups"]) == universe, "%s %s" % (case_id, name)
            checks += 2

            total = Fraction(0)
            for key in universe:
                if entry["kind"] == "additive":
                    column = columns[0]
                    net = Fraction(current.get(key, {}).get(column, 0)
                                   - baseline.get(key, {}).get(column, 0))
                else:
                    numerator, denominator = columns
                    net = (Fraction(current.get(key, {}).get(numerator, 0)) / d1
                           - Fraction(baseline.get(key, {}).get(numerator, 0)) / d0)
                close(net, breakdown["groups"][key]["expect_net_contribution"],
                      "%s %s %s net" % (case_id, name, key))
                checks += 1
                total += net
            close(total, entry["expect_delta"], "%s %s partition" % (case_id, name))
            checks += 1

        for name in entry["expect_omitted_breakdowns"]:
            dimensions = name.split(",")
            universe = set(cells(cur_rows, dimensions, columns))
            universe |= set(cells(base_rows, dimensions, columns))
            assert len(universe) > CROSS_CELL_LIMIT, "%s %s" % (case_id, name)
            checks += 1

    print("checks run: %d" % checks)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
