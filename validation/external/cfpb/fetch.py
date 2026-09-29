"""사전등록 3절 + 개정 1: CFPB 공식 전체 데이터셋 ZIP 을 저장소 밖으로 한 번만 스트리밍해 받는다.

개정 1 에 따라 검색 API 경로는 제거했다 — 이 파일은 검색 API 에 요청하지 않는다.
- 정적 파일 HEAD 는 이미 controller 가 1회 수행했다(개정 1). 여기서는 HEAD 를 보내지 않고 그 기록을 복사한다.
- 다운로드는 최대 1회, 재시도 없음. 실패하거나 바이트 수가 HEAD 의 Content-Length 와 다르면 멈춘다.
- 기록: URL, 다운로드 시작·끝 UTC, 바이트 수, ZIP 바이트 SHA-256, HEAD 기록, CSV 멤버의 allowlist 확인과 행 수.
  헤더는 allowlist 6개 열의 존재만 기록하고 나머지 열 이름은 기록하지 않는다(개수만).
- ZIP 은 풀지 않는다. CSV 멤버를 스트림으로 읽는다.
"""
from __future__ import annotations

import csv
import datetime as _dt
import hashlib
import io
import json
import os
import sys
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
RAW_DIR = "/tmp/bia-cfpb-raw"
ACQUISITION = os.path.join(HERE, "results", "acquisition.json")
ATTEMPT1 = os.path.join(HERE, "results", "acquisition_attempt1.json")  # HEAD 기록은 여기 보존돼 있다

URL = "https://files.consumerfinance.gov/ccdb/complaints.csv.zip"
RAW_ZIP = os.path.join(RAW_DIR, "complaints.csv.zip")

ALLOWLIST = ("Date received", "Product", "Issue", "Company", "Timely response?", "Complaint ID")
CHUNK = 1 << 20


def now_utc() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def open_csv_member(zip_path: str):
    """ZIP 안의 유일한 CSV 멤버를 텍스트 스트림으로 연다(디스크에 풀지 않는다)."""
    archive = zipfile.ZipFile(zip_path)
    members = [n for n in archive.namelist() if n.lower().endswith(".csv")]
    if len(members) != 1:
        raise ValueError("expected exactly one CSV member, found %d" % len(members))
    raw = archive.open(members[0], "r")
    return archive, members[0], io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")


def inspect_zip(zip_path: str):
    csv.field_size_limit(sys.maxsize)
    archive, member, text = open_csv_member(zip_path)
    with archive, text:
        reader = csv.reader(text)
        header = next(reader)
        rows = 0
        for _ in reader:
            rows += 1
    return {"csv_member": member,
            "allowlist_missing": [c for c in ALLOWLIST if c not in header],
            "allowlist_duplicated": [c for c in ALLOWLIST if header.count(c) > 1],
            "header_column_count": len(header),
            "non_allowlisted_column_count": len([c for c in header if c not in ALLOWLIST]),
            "rows": rows}


def write(record) -> None:
    os.makedirs(os.path.dirname(ACQUISITION), exist_ok=True)
    with open(ACQUISITION, "w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def main() -> int:
    if os.path.realpath(RAW_DIR).startswith(os.path.realpath(REPO) + os.sep):
        print("STOP: RAW_DIR must be outside the repository")
        return 2
    if os.path.exists(RAW_ZIP):
        print("STOP: %s already exists; this script downloads at most once" % RAW_ZIP)
        return 2
    with open(ATTEMPT1, "r", encoding="utf-8") as handle:
        head = json.load(handle)["head_record"]
    expected_bytes = int(head["headers"]["Content-Length"])
    os.makedirs(RAW_DIR, exist_ok=True)

    # 개정 2: 같은 파일에서 HEAD 200 을 받은 조건 그대로 — User-Agent 헤더를 지정하지 않는다.
    user_agent = dict(urllib.request.build_opener().addheaders).get("User-agent")
    record = {"source": URL, "amendment": "preregistration.md 개정 2", "attempt": 2,
              "request_user_agent": user_agent, "request_client":
              "urllib.request (%s %s)" % (sys.executable, sys.version.split()[0]),
              "note": "CCDB is a live database; the file may differ on refetch.",
              "head_record": head, "url": URL, "raw_path_outside_repo": RAW_ZIP}
    digest = hashlib.sha256()
    size = 0
    record["download_started_utc"] = now_utc()
    try:  # 재시도 없음(개정 1)
        req = urllib.request.Request(URL)
        with urllib.request.urlopen(req, timeout=1800) as resp, open(RAW_ZIP + ".part", "wb") as out:
            record["http_status"] = resp.status
            while True:
                chunk = resp.read(CHUNK)
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
                out.write(chunk)
    except Exception as exc:  # noqa: BLE001
        record["download_finished_utc"] = now_utc()
        record["error"] = "%s: %s" % (type(exc).__name__, exc)
        record["bytes"] = size
        write(record)
        print("STOP: download failed: %s" % record["error"])
        return 3
    record["download_finished_utc"] = now_utc()
    os.replace(RAW_ZIP + ".part", RAW_ZIP)
    record.update({"bytes": size, "sha256_zip": digest.hexdigest(),
                   "bytes_match_head_content_length": size == expected_bytes})
    if size != expected_bytes:
        write(record)
        print("STOP: byte count %d != HEAD Content-Length %d" % (size, expected_bytes))
        return 4
    shape = inspect_zip(RAW_ZIP)
    record.update(shape)
    record["allowlist_present"] = not shape["allowlist_missing"] and not shape["allowlist_duplicated"]
    write(record)
    print("bytes=%d sha256=%s rows=%d allowlist_present=%s"
          % (size, record["sha256_zip"], shape["rows"], record["allowlist_present"]))
    if not record["allowlist_present"]:
        print("STOP: header does not match the allowlist: missing=%s dup=%s"
              % (shape["allowlist_missing"], shape["allowlist_duplicated"]))
        return 5
    return 0


if __name__ == "__main__":
    sys.exit(main())
