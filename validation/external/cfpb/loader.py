"""사전등록 4절: 원본 CSV → 파생 집계 CSV 두 개.

- 원본 CSV 레코드는 allowlist 6개 열만 뽑은 튜플로 바꾸고 나머지는 즉시 버린다.
- `Complaint ID` 중복·빈 값 → 거부. `Date received` 가 `YYYY-MM-DD` 가 아니면 → 거부(추측하지 않는다).
- `Timely response?`: `Yes` → 분자·분모, `No` → 분모, 빈 값 → 분모 제외 + 건수 보고, 그 밖의 값 → 거부.
- `Product`·`Issue`·`Company` 는 값 그대로 넘긴다(빈 값 포함, 공백 정규화도 하지 않는다).

거부는 전체를 한 번 훑은 뒤 위반 건수와 함께 `LoaderRejected` 로 던진다.
"""
from __future__ import annotations

import csv
import datetime as _dt
import hashlib
import json
import os
import re
import sys
from typing import Dict, Iterable, Iterator, List, Sequence, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
DERIVED = os.path.join(HERE, "derived")
PRODUCT_ISSUE = os.path.join(DERIVED, "product_issue.csv")
COMPANY_PRODUCT = os.path.join(DERIVED, "company_product.csv")
DERIVATION = os.path.join(HERE, "results", "derivation.json")
ACQUISITION = os.path.join(HERE, "results", "acquisition.json")

COMMIT_LIMIT_BYTES = 5 * 1024 * 1024

DATE, PRODUCT, ISSUE, COMPANY, TIMELY, CID = (
    "Date received", "Product", "Issue", "Company", "Timely response?", "Complaint ID")
ALLOWLIST = (DATE, PRODUCT, ISSUE, COMPANY, TIMELY, CID)

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# 파생 파일에 담는 정확한 구간(사전등록 6절). 경계 포함.
WINDOWS = {
    "E1_baseline": ("2024-02-01", "2024-02-28"),
    "E1_current": ("2024-03-01", "2024-03-28"),
    "E2_baseline": ("2017-03-27", "2017-04-23"),
    "E2_current": ("2017-04-24", "2017-05-21"),
}
# company_product 는 E3 전용이고 E3 구간은 E1 과 같다.
COMPANY_WINDOWS = ("E1_baseline", "E1_current")

PI_HEADER = ("day", "product", "issue", "complaints", "timely_yes", "timely_known")
CP_HEADER = ("day", "company", "product", "complaints")


class LoaderRejected(ValueError):
    def __init__(self, reasons: Dict[str, int]) -> None:
        super().__init__("loader rejected input: %s" % reasons)
        self.reasons = reasons


def read_allowlisted(path: str) -> Iterator[Tuple[str, ...]]:
    """원본 CSV 를 스트림으로 읽어 allowlist 6개 값 튜플만 내보낸다(ALLOWLIST 순서)."""
    csv.field_size_limit(sys.maxsize)
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        missing = [c for c in ALLOWLIST if c not in header]
        if missing:
            raise LoaderRejected({"allowlist_columns_missing": len(missing)})
        index = [header.index(c) for c in ALLOWLIST]
        width = len(header)
        for record in reader:
            if len(record) != width:
                raise LoaderRejected({"ragged_record": 1})
            yield tuple(record[i] for i in index)
            del record


def _window_of(day: _dt.date, windows: Dict[str, Tuple[_dt.date, _dt.date]]):
    for name, (start, end) in windows.items():
        if start <= day <= end:
            return name
    return None


def derive(rows: Iterable[Sequence[str]]):
    """allowlist 튜플 스트림 → (product_issue 행, company_product 행, 통계)."""
    windows = {k: (_dt.date.fromisoformat(a), _dt.date.fromisoformat(b))
               for k, (a, b) in WINDOWS.items()}
    seen_ids = set()
    violations = {"duplicate_complaint_id": 0, "blank_complaint_id": 0,
                  "bad_date_format": 0, "unexpected_timely_value": 0}
    stats = {"rows_read": 0, "rows_out_of_windows": 0, "rows_in_windows": {},
             "timely_blank_in_windows": {}, "blank_product_in_windows": 0,
             "blank_issue_in_windows": 0, "blank_company_in_windows": 0,
             "whitespace_padded_values_in_windows": 0}
    pi: Dict[Tuple[str, str, str], List[int]] = {}
    cp: Dict[Tuple[str, str, str], int] = {}
    for row in rows:
        stats["rows_read"] += 1
        date_s, product, issue, company, timely, cid = row
        if not cid.strip():
            violations["blank_complaint_id"] += 1
        elif cid in seen_ids:
            violations["duplicate_complaint_id"] += 1
        else:
            seen_ids.add(cid)
        if not DATE_RE.match(date_s):
            violations["bad_date_format"] += 1
            continue
        try:
            day = _dt.date.fromisoformat(date_s)
        except ValueError:
            violations["bad_date_format"] += 1
            continue
        if timely not in ("Yes", "No", ""):
            violations["unexpected_timely_value"] += 1
            continue
        window = _window_of(day, windows)
        if window is None:
            stats["rows_out_of_windows"] += 1
            continue
        stats["rows_in_windows"][window] = stats["rows_in_windows"].get(window, 0) + 1
        if timely == "":
            stats["timely_blank_in_windows"][window] = (
                stats["timely_blank_in_windows"].get(window, 0) + 1)
        for label, value in (("product", product), ("issue", issue), ("company", company)):
            if value == "":
                stats["blank_%s_in_windows" % label] += 1
            elif value != value.strip():
                stats["whitespace_padded_values_in_windows"] += 1
        cell = pi.setdefault((date_s, product, issue), [0, 0, 0])
        cell[0] += 1
        cell[1] += 1 if timely == "Yes" else 0
        cell[2] += 1 if timely in ("Yes", "No") else 0
        if window in COMPANY_WINDOWS:
            key = (date_s, company, product)
            cp[key] = cp.get(key, 0) + 1
    bad = {k: v for k, v in violations.items() if v}
    if bad:
        raise LoaderRejected(bad)
    pi_rows = [(d, p, i, c[0], c[1], c[2]) for (d, p, i), c in sorted(pi.items())]
    cp_rows = [(d, co, p, n) for (d, co, p), n in sorted(cp.items())]
    return pi_rows, cp_rows, stats


def write_csv(path: str, header: Sequence[str], rows: Iterable[Sequence[object]]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header)
        for row in rows:
            writer.writerow(row)


def file_record(path: str, rows: int) -> Dict[str, object]:
    with open(path, "rb") as handle:
        data = handle.read()
    return {"path": os.path.relpath(path, HERE), "bytes": len(data), "rows": rows,
            "sha256": hashlib.sha256(data).hexdigest(),
            "committed": len(data) <= COMMIT_LIMIT_BYTES}


def main() -> int:
    with open(ACQUISITION, "r", encoding="utf-8") as handle:
        acquisition = json.load(handle)
    paths = [r["raw_path_outside_repo"] for r in acquisition["requests"]]

    def stream():
        for path in paths:
            for row in read_allowlisted(path):
                yield row

    try:
        pi_rows, cp_rows, stats = derive(stream())
    except LoaderRejected as exc:
        print("STOP: %s" % exc)
        return 5
    write_csv(PRODUCT_ISSUE, PI_HEADER, pi_rows)
    write_csv(COMPANY_PRODUCT, CP_HEADER, cp_rows)
    record = {"windows": WINDOWS, "stats": stats,
              "files": {"product_issue": file_record(PRODUCT_ISSUE, len(pi_rows)),
                        "company_product": file_record(COMPANY_PRODUCT, len(cp_rows))}}
    with open(DERIVATION, "w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2, ensure_ascii=False, sort_keys=True)
        handle.write("\n")
    print(json.dumps(record["files"], indent=2))
    print("stats: %s" % json.dumps(stats, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
