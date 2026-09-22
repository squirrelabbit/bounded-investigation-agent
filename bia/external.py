"""사용자 데이터 디렉터리를 검증한다.

번들된 고정 시나리오는 `bia/store.py` 가 그대로 담당한다. 이 모듈은 외부에서
들어온 `metrics.csv` / `tickets.jsonl` 을 **실행 전에** 엄격히 검사만 하고,
통과하면 로딩 자체는 `store` 의 같은 로더에 넘긴다. 파서를 두 벌 두지 않기
위해서다.

스키마는 하나뿐이고 별칭을 받지 않는다. `day`(=`created_at` 아님),
`text`(=`body` 아님). 컬럼 자동 탐지·매핑·별칭은 의도적으로 없다.

`source` 와 `complaint_type` 이 허용 목록 밖이면 여기서 오류다. 통과시키면 나중에 verifier 가
`source_not_allowed` 로 조용히 떨어뜨리고, 사용자는 왜 근거가 0건인지 알 수
없게 된다.
"""
from __future__ import annotations

import csv
import datetime as _dt
import json
import os
import re
from typing import List, Tuple

from . import store
from .lexicon import COMPLAINT_TYPES
from .types import (
    ALLOWED_TICKET_SOURCES,
    AnalysisIntent,
    MetricRow,
    Period,
    Ticket,
    UnsupportedIntent,
)

METRICS_FILENAME = "metrics.csv"
TICKETS_FILENAME = "tickets.jsonl"

METRICS_HEADER: Tuple[str, ...] = ("day", "product", "complaint_type", "count")
TICKET_KEYS: Tuple[str, ...] = (
    "ticket_id",
    "day",
    "product",
    "complaint_type",
    "text",
    "source",
)

_ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_INTEGER = re.compile(r"^-?\d+$")

PERIOD_FORMAT = "START:END"


class ExternalDataError(ValueError):
    """사용자 데이터·기간 인자가 계약을 벗어났다. CLI 가 SystemExit 로 바꾼다."""


def _fail(message: str) -> None:
    raise ExternalDataError(message)


def _at(path: str, line_no: int) -> str:
    return "%s:%d" % (path, line_no)


def _check_day(raw: str, where: str, field: str) -> None:
    if not _ISO_DAY.match(raw):
        _fail(
            "%s: %s 는 ISO 날짜(YYYY-MM-DD)여야 한다. 받은 값: %r"
            % (where, field, raw)
        )
    try:
        _dt.date.fromisoformat(raw)
    except ValueError:
        _fail("%s: %s 가 존재하지 않는 날짜다. 받은 값: %r" % (where, field, raw))


def validate_metrics_file(path: str) -> None:
    if not os.path.isfile(path):
        _fail("%s 가 없다. 데이터 디렉터리에는 %s 와 %s 가 있어야 한다." % (path, METRICS_FILENAME, TICKETS_FILENAME))
    with open(path, "r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            header = None
        if header is None:
            _fail("%s: 파일이 비어 있다. 첫 줄은 헤더 %s 여야 한다." % (path, ",".join(METRICS_HEADER)))
        cleaned = [column.strip() for column in header]
        if tuple(cleaned) != METRICS_HEADER:
            _fail(
                "%s: 헤더가 %s 여야 한다(순서 포함). 받은 헤더: %s"
                % (_at(path, 1), ",".join(METRICS_HEADER), ",".join(cleaned))
            )
        seen_rows = 0
        for line_no, record in enumerate(reader, start=2):
            if not record:
                continue
            where = _at(path, line_no)
            if len(record) != len(METRICS_HEADER):
                _fail(
                    "%s: 열이 %d개여야 하는데 %d개다. 받은 줄: %s"
                    % (where, len(METRICS_HEADER), len(record), ",".join(record))
                )
            for name, value in zip(METRICS_HEADER, record):
                if value.strip() == "":
                    _fail("%s: %s 가 비어 있다. 필수 항목이다." % (where, name))
            day, count = record[0], record[3]
            _check_day(day, where, "day")
            if not _INTEGER.match(count.strip()):
                _fail("%s: count 는 0 이상의 정수여야 한다. 받은 값: %r" % (where, count))
            if int(count.strip()) < 0:
                _fail("%s: count 는 0 이상이어야 한다. 받은 값: %r" % (where, count))
            seen_rows += 1
        if seen_rows == 0:
            _fail("%s: 데이터 줄이 없다. 헤더만 있는 파일로는 비교할 수 없다." % path)


def validate_tickets_file(path: str) -> None:
    if not os.path.isfile(path):
        _fail("%s 가 없다. 데이터 디렉터리에는 %s 와 %s 가 있어야 한다." % (path, METRICS_FILENAME, TICKETS_FILENAME))
    seen_lines = 0
    with open(path, "r", encoding="utf-8") as handle:
        for line_no, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            where = _at(path, line_no)
            try:
                record = json.loads(line)
            except ValueError as exc:
                _fail("%s: JSON 한 줄을 읽지 못했다(%s). 한 줄에 JSON 객체 하나여야 한다." % (where, exc))
            if not isinstance(record, dict):
                _fail("%s: JSON 객체여야 한다. 받은 타입: %s" % (where, type(record).__name__))
            keys = set(record)
            missing = [key for key in TICKET_KEYS if key not in keys]
            if missing:
                _fail(
                    "%s: 필수 키가 없다: %s. 필요한 키는 %s 다."
                    % (where, ", ".join(missing), ", ".join(TICKET_KEYS))
                )
            extra = sorted(keys - set(TICKET_KEYS))
            if extra:
                _fail(
                    "%s: 허용되지 않은 키가 있다: %s. 키는 정확히 %s 여야 한다."
                    % (where, ", ".join(extra), ", ".join(TICKET_KEYS))
                )
            for key in TICKET_KEYS:
                value = record[key]
                if not isinstance(value, str):
                    _fail(
                        "%s: %s 는 문자열이어야 한다. 받은 값: %r (%s)"
                        % (where, key, value, type(value).__name__)
                    )
                if value.strip() == "":
                    _fail("%s: %s 가 비어 있다. 필수 항목이다." % (where, key))
            _check_day(record["day"], where, "day")
            if record["complaint_type"] not in COMPLAINT_TYPES:
                _fail(
                    "%s: complaint_type 은 %s 중 하나여야 한다. 받은 값: %r "
                    "(이 목록 밖의 유형은 본문 뒷받침 검사를 통과할 수 없어, "
                    "실행은 되지만 근거가 0건으로 끝난다)"
                    % (where, ", ".join(COMPLAINT_TYPES), record["complaint_type"])
                )
            if record["source"] not in ALLOWED_TICKET_SOURCES:
                _fail(
                    "%s: source 는 %s 중 하나여야 한다. 받은 값: %r "
                    "(허용 밖 출처는 검증 단계에서 근거로 채택되지 않는다)"
                    % (where, ", ".join(ALLOWED_TICKET_SOURCES), record["source"])
                )
            seen_lines += 1
    if seen_lines == 0:
        _fail("%s: 티켓이 한 건도 없다." % path)


def parse_period(raw: str, flag: str) -> Period:
    """`START:END` (양끝 포함) 를 Period 로. 형식 오류는 전부 여기서 잡는다."""
    if raw is None or raw.strip() == "":
        _fail("%s 가 필요하다. 형식은 %s 다(예: 2026-07-01:2026-07-30)." % (flag, PERIOD_FORMAT))
    text = raw.strip()
    parts = text.split(":")
    if len(parts) != 2:
        _fail(
            "%s 형식이 잘못됐다. %s 여야 한다(예: 2026-07-01:2026-07-30). 받은 값: %r"
            % (flag, PERIOD_FORMAT, raw)
        )
    start_raw, end_raw = parts[0].strip(), parts[1].strip()
    _check_day(start_raw, flag, "시작일")
    _check_day(end_raw, flag, "종료일")
    try:
        return Period.of(start_raw, end_raw)
    except ValueError as exc:
        _fail("%s: %s (시작일이 종료일보다 늦을 수 없다)" % (flag, exc))


def build_intent(current_raw: str, baseline_raw: str) -> AnalysisIntent:
    current = parse_period(current_raw, "--current")
    baseline = parse_period(baseline_raw, "--baseline")
    intent = AnalysisIntent(current_period=current, baseline_period=baseline)
    try:
        intent.validate()
    except UnsupportedIntent as exc:
        _fail(
            "기간 조합을 받을 수 없다: %s (--current %s, --baseline %s)"
            % (exc, current, baseline)
        )
    return intent


def validate_data_dir(data_dir: str) -> Tuple[str, str]:
    if not os.path.isdir(data_dir):
        _fail("--data-dir %s 는 디렉터리가 아니다." % data_dir)
    metrics_path = os.path.join(data_dir, METRICS_FILENAME)
    tickets_path = os.path.join(data_dir, TICKETS_FILENAME)
    validate_metrics_file(metrics_path)
    validate_tickets_file(tickets_path)
    return metrics_path, tickets_path


def load_external_dataset(
    data_dir: str, current_raw: str, baseline_raw: str
) -> Tuple[AnalysisIntent, List[MetricRow], List[Ticket]]:
    """검증 → 기존 로더. 파이프라인은 고정 시나리오와 같은 것을 쓴다."""
    intent = build_intent(current_raw, baseline_raw)
    metrics_path, tickets_path = validate_data_dir(data_dir)
    rows = store.load_metric_rows(metrics_path)
    tickets = store.load_tickets(tickets_path)
    return intent, rows, tickets
