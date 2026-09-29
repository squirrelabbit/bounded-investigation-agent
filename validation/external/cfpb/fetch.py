"""사전등록 3절: CFPB 검색 API 에서 원본 CSV 를 저장소 밖으로 스트리밍해 받는다.

원본은 RAW_DIR(저장소 밖)에만 쓴다. 저장소에는 요청 URL·UTC 시각·원본 SHA-256·바이트 수·행 수만
`results/acquisition.json` 으로 남긴다. 헤더는 allowlist 6개 열의 존재만 기록하고, 나머지 열 이름은
기록하지 않는다(개수만).
"""
from __future__ import annotations

import csv
import datetime as _dt
import hashlib
import json
import os
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.environ.get("BIA_CFPB_RAW_DIR", "/tmp/bia-cfpb-raw")
ACQUISITION = os.path.join(HERE, "results", "acquisition.json")

API = "https://www.consumerfinance.gov/data-research/consumer-complaints/search/api/v1/"

ALLOWLIST = ("Date received", "Product", "Issue", "Company", "Timely response?", "Complaint ID")

# 구간보다 하루씩 넓게 요청한다(3절). 정확한 구간 자르기는 loader 몫이다.
REQUESTS = (
    {"id": "r2024", "date_received_min": "2024-01-31", "date_received_max": "2024-03-29",
     "covers": ["E1", "E3"]},
    {"id": "r2017", "date_received_min": "2017-03-26", "date_received_max": "2017-05-22",
     "covers": ["E2"]},
)

CHUNK = 1 << 20


def raw_path(request_id: str) -> str:
    return os.path.join(RAW_DIR, "%s.csv" % request_id)


def request_url(req) -> str:
    query = urllib.parse.urlencode([
        ("format", "csv"), ("no_aggs", "true"),
        ("date_received_min", req["date_received_min"]),
        ("date_received_max", req["date_received_max"]),
    ])
    return API + "?" + query


def _download(url: str, dest: str):
    digest = hashlib.sha256()
    size = 0
    req = urllib.request.Request(url, headers={"User-Agent": "bia-cfpb-external-validation/1"})
    with urllib.request.urlopen(req, timeout=600) as resp, open(dest + ".part", "wb") as out:
        status = resp.status
        content_type = resp.headers.get("Content-Type")
        while True:
            chunk = resp.read(CHUNK)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
            out.write(chunk)
    os.replace(dest + ".part", dest)
    return {"http_status": status, "content_type": content_type,
            "bytes": size, "sha256": digest.hexdigest()}


def inspect_raw(path: str):
    """헤더 allowlist 확인과 CSV 레코드 수. 레코드 내용은 보관하지 않는다."""
    csv.field_size_limit(sys.maxsize)
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        rows = 0
        for _ in reader:
            rows += 1
    missing = [c for c in ALLOWLIST if c not in header]
    duplicated = [c for c in ALLOWLIST if header.count(c) > 1]
    return {"allowlist_present": not missing, "allowlist_missing": missing,
            "allowlist_duplicated": duplicated,
            "header_column_count": len(header),
            "non_allowlisted_column_count": len([c for c in header if c not in ALLOWLIST]),
            "rows": rows}


def main() -> int:
    os.makedirs(RAW_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(ACQUISITION), exist_ok=True)
    if os.path.realpath(RAW_DIR).startswith(os.path.realpath(os.path.join(HERE, "..", "..", ".."))):
        print("RAW_DIR must be outside the repository: %s" % RAW_DIR)
        return 2
    records = []
    for req in REQUESTS:
        url = request_url(req)
        dest = raw_path(req["id"])
        received = None
        info = None
        last_error = None
        for attempt in (1, 2):  # 재시도는 한 번까지
            try:
                received = _dt.datetime.now(_dt.timezone.utc).isoformat()
                info = _download(url, dest)
                break
            except Exception as exc:  # noqa: BLE001 — 실패 사유를 기록하고 멈춘다
                last_error = "%s: %s" % (type(exc).__name__, exc)
                print("attempt %d failed for %s: %s" % (attempt, req["id"], last_error))
                time.sleep(5)
        if info is None:
            print("STOP: download failed twice for %s" % req["id"])
            return 3
        shape = inspect_raw(dest)
        record = dict(req)
        record.update({"url": url, "received_utc": received, "raw_path_outside_repo": dest})
        record.update(info)
        record.update(shape)
        records.append(record)
        print("%s: bytes=%d rows=%d sha256=%s allowlist_present=%s"
              % (req["id"], info["bytes"], shape["rows"], info["sha256"],
                 shape["allowlist_present"]))
        if not shape["allowlist_present"] or shape["allowlist_duplicated"]:
            print("STOP: header does not match the pre-registered allowlist: missing=%s dup=%s"
                  % (shape["allowlist_missing"], shape["allowlist_duplicated"]))
            _write(records)
            return 4
    _write(records)
    return 0


def _write(records) -> None:
    with open(ACQUISITION, "w", encoding="utf-8") as handle:
        json.dump({"source": API, "note": "CCDB is a live database; bytes may differ on refetch.",
                   "requests": records}, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


if __name__ == "__main__":
    sys.exit(main())
