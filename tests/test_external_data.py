"""사용자 데이터 경로(`run --data-dir`)의 경계 테스트.

여기서 지키려는 것은 두 가지다.

1. 번들 fixture 가 아닌 데이터에도 **같은 파이프라인**이 그대로 돌아간다.
2. 형식이 틀린 데이터는 실행 전에, 파일·줄 번호·문제 값과 함께 멈춘다.
   특히 `source` 가 허용 밖이면 여기서 막는다 — 통과시키면 verifier 가
   조용히 떨어뜨리고 사용자는 근거가 0건인 이유를 알 수 없다.

CLI 에서 모델 selector 에 도달할 수 없다는 것도 여기서 확인한다(AST 로 import
자체를 확인 + 소켓 차단 상태에서 실행).
"""
from __future__ import annotations

import ast
import contextlib
import io
import json
import os
import shutil
import socket
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bia import cli
from bia.controller import investigate
from bia.decision import DeterministicHeuristicSelector
from bia.external import (
    ExternalDataError,
    build_intent,
    load_external_dataset,
    validate_data_dir,
)
from bia.store import load_scenario

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_DIR = os.path.join(REPO_ROOT, "examples", "custom-data-template")
CURRENT = "2026-07-01:2026-07-07"
BASELINE = "2026-06-01:2026-06-07"


class _Blocked(AssertionError):
    pass


@contextlib.contextmanager
def no_network():
    """어떤 소켓도 열리지 않음을 실행 중에 강제한다."""
    real_socket = socket.socket
    real_connect = socket.create_connection

    def refuse(*args, **kwargs):
        raise _Blocked("this path must not open a socket")

    socket.socket = refuse
    socket.create_connection = refuse
    try:
        yield
    finally:
        socket.socket = real_socket
        socket.create_connection = real_connect


def copy_template(target: str) -> str:
    shutil.copytree(TEMPLATE_DIR, target)
    return target


def read_lines(name: str):
    with open(os.path.join(TEMPLATE_DIR, name), "r", encoding="utf-8") as handle:
        return handle.read().splitlines()


def write_lines(path: str, lines) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


class TempDirCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bia-external-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def data_dir(self, metrics=None, tickets=None) -> str:
        path = os.path.join(self.tmp, "data-%d" % len(os.listdir(self.tmp)))
        copy_template(path)
        if metrics is not None:
            write_lines(os.path.join(path, "metrics.csv"), metrics)
        if tickets is not None:
            write_lines(os.path.join(path, "tickets.jsonl"), tickets)
        return path

    def expect_error(self, *fragments, **kwargs):
        metrics = kwargs.pop("metrics", None)
        tickets = kwargs.pop("tickets", None)
        path = self.data_dir(metrics=metrics, tickets=tickets)
        with self.assertRaises(ExternalDataError) as ctx:
            validate_data_dir(path)
        message = str(ctx.exception)
        for fragment in fragments:
            self.assertIn(fragment, message)
        return message


class TemplateRunsEndToEnd(TempDirCase):
    """1. 템플릿이 CLI 경로로 끝까지 돌고 실제 답변을 만든다."""

    def test_the_template_produces_confirmed_facts_and_admitted_tickets(self):
        with no_network():
            result = cli.run_data_dir(TEMPLATE_DIR, CURRENT, BASELINE)
        self.assertEqual(result.answer.status, "reported")
        self.assertEqual(result.state.finish_reason, "evidence_sufficient")
        self.assertTrue(result.answer.confirmed)
        admitted = [t for round_ in result.state.rounds for t in round_.admitted]
        self.assertGreaterEqual(len(admitted), 1)
        self.assertTrue(any("Aurora" in line for line in result.answer.confirmed))

    def test_the_cli_prints_the_answer_and_exits_zero(self):
        buffer = io.StringIO()
        with no_network():
            with contextlib.redirect_stdout(buffer):
                code = cli.main(
                    [
                        "run",
                        "--data-dir",
                        TEMPLATE_DIR,
                        "--current",
                        CURRENT,
                        "--baseline",
                        BASELINE,
                    ]
                )
        printed = buffer.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("## 확인된 사실", printed)
        self.assertIn("T-0003", printed)
        self.assertIn("모델 호출: 없음", printed)

    def test_json_output_still_works_on_this_path(self):
        buffer = io.StringIO()
        with no_network():
            with contextlib.redirect_stdout(buffer):
                code = cli.main(
                    [
                        "run",
                        "--data-dir",
                        TEMPLATE_DIR,
                        "--current",
                        CURRENT,
                        "--baseline",
                        BASELINE,
                        "--json",
                    ]
                )
        self.assertEqual(code, 0)
        document = json.loads(buffer.getvalue())
        self.assertEqual(document["answer"]["status"], "reported")
        self.assertEqual(document["provider"], "heuristic")

    def test_the_greedy_selector_is_available_on_this_path(self):
        with no_network():
            result = cli.run_data_dir(TEMPLATE_DIR, CURRENT, BASELINE, "greedy")
        self.assertEqual(result.provider_name, "greedy")


class CleanInputPasses(TempDirCase):
    """3. 검증기가 전부 거절하지는 않는지."""

    def test_the_template_validates(self):
        validate_data_dir(TEMPLATE_DIR)

    def test_a_copy_validates_and_loads(self):
        path = self.data_dir()
        intent, rows, tickets = load_external_dataset(path, CURRENT, BASELINE)
        self.assertEqual(intent.current_period.days, 7)
        self.assertEqual(len(rows), 56)
        self.assertEqual(len(tickets), 9)

    def test_blank_lines_in_the_jsonl_are_tolerated(self):
        lines = read_lines("tickets.jsonl")
        lines.insert(2, "")
        path = self.data_dir(tickets=lines)
        validate_data_dir(path)


class MetricsValidation(TempDirCase):
    """2. metrics.csv 규칙 — 각각을 실제로 발동시키는 입력으로."""

    def test_wrong_header_order(self):
        lines = read_lines("metrics.csv")
        lines[0] = "day,product,count,complaint_type"
        self.expect_error(
            "metrics.csv:1", "헤더", "day,product,complaint_type,count", metrics=lines
        )

    def test_missing_header_column(self):
        lines = read_lines("metrics.csv")
        lines[0] = "day,product,complaint_type"
        self.expect_error("metrics.csv:1", "헤더", metrics=lines)

    def test_row_with_a_missing_column(self):
        lines = read_lines("metrics.csv")
        lines[4] = "2026-06-02,Aurora,app_crash"
        self.expect_error("metrics.csv:5", "열이 4개여야", "3개", metrics=lines)

    def test_non_iso_day(self):
        lines = read_lines("metrics.csv")
        lines[3] = "2026-6-1,Aurora,delivery_delay,1"
        self.expect_error("metrics.csv:4", "ISO", "'2026-6-1'", metrics=lines)

    def test_impossible_day(self):
        lines = read_lines("metrics.csv")
        lines[3] = "2026-02-31,Aurora,delivery_delay,1"
        self.expect_error("metrics.csv:4", "2026-02-31", metrics=lines)

    def test_negative_count(self):
        lines = read_lines("metrics.csv")
        lines[6] = "2026-06-02,Aurora,delivery_delay,-3"
        self.expect_error("metrics.csv:7", "count", "0 이상", "'-3'", metrics=lines)

    def test_non_integer_count(self):
        lines = read_lines("metrics.csv")
        lines[6] = "2026-06-02,Aurora,delivery_delay,3.5"
        self.expect_error("metrics.csv:7", "count", "정수", "'3.5'", metrics=lines)

    def test_blank_required_field(self):
        lines = read_lines("metrics.csv")
        lines[6] = "2026-06-02,,delivery_delay,2"
        self.expect_error("metrics.csv:7", "product", "비어 있다", metrics=lines)

    def test_header_only_file(self):
        self.expect_error("데이터 줄이 없다", metrics=["day,product,complaint_type,count"])

    def test_missing_file(self):
        path = self.data_dir()
        os.remove(os.path.join(path, "metrics.csv"))
        with self.assertRaises(ExternalDataError) as ctx:
            validate_data_dir(path)
        self.assertIn("metrics.csv", str(ctx.exception))


class TicketsValidation(TempDirCase):
    """2. tickets.jsonl 규칙."""

    def test_malformed_json_line(self):
        lines = read_lines("tickets.jsonl")
        lines[2] = '{"ticket_id": "T-0003", '
        self.expect_error("tickets.jsonl:3", "JSON", tickets=lines)

    def test_line_that_is_not_an_object(self):
        lines = read_lines("tickets.jsonl")
        lines[1] = '["T-0002"]'
        self.expect_error("tickets.jsonl:2", "JSON 객체", tickets=lines)

    def test_missing_key(self):
        lines = read_lines("tickets.jsonl")
        record = json.loads(lines[2])
        del record["text"]
        lines[2] = json.dumps(record, ensure_ascii=False, sort_keys=True)
        self.expect_error("tickets.jsonl:3", "필수 키가 없다", "text", tickets=lines)

    def test_extra_key(self):
        lines = read_lines("tickets.jsonl")
        record = json.loads(lines[2])
        record["body"] = "aliases are not accepted"
        lines[2] = json.dumps(record, ensure_ascii=False, sort_keys=True)
        self.expect_error("tickets.jsonl:3", "허용되지 않은 키", "body", tickets=lines)

    def test_alias_field_names_are_rejected(self):
        lines = read_lines("tickets.jsonl")
        record = json.loads(lines[0])
        record["created_at"] = record.pop("day")
        lines[0] = json.dumps(record, ensure_ascii=False, sort_keys=True)
        message = self.expect_error("tickets.jsonl:1", tickets=lines)
        self.assertIn("day", message)

    def test_non_iso_day(self):
        lines = read_lines("tickets.jsonl")
        record = json.loads(lines[3])
        record["day"] = "07/02/2026"
        lines[3] = json.dumps(record, ensure_ascii=False, sort_keys=True)
        self.expect_error("tickets.jsonl:4", "ISO", "'07/02/2026'", tickets=lines)

    def test_disallowed_source(self):
        lines = read_lines("tickets.jsonl")
        record = json.loads(lines[2])
        record["source"] = "phone"
        lines[2] = json.dumps(record, ensure_ascii=False, sort_keys=True)
        message = self.expect_error(
            "tickets.jsonl:3", "source", "'phone'", tickets=lines
        )
        for allowed in ("web_form", "email", "in_app"):
            self.assertIn(allowed, message)

    def test_non_string_value(self):
        lines = read_lines("tickets.jsonl")
        record = json.loads(lines[2])
        record["ticket_id"] = 3
        lines[2] = json.dumps(record, ensure_ascii=False, sort_keys=True)
        self.expect_error("tickets.jsonl:3", "ticket_id", "문자열", tickets=lines)

    def test_blank_value(self):
        lines = read_lines("tickets.jsonl")
        record = json.loads(lines[2])
        record["text"] = "   "
        lines[2] = json.dumps(record, ensure_ascii=False, sort_keys=True)
        self.expect_error("tickets.jsonl:3", "text", "비어 있다", tickets=lines)

    def test_empty_file(self):
        self.expect_error("티켓이 한 건도 없다", tickets=[])


class PeriodValidation(unittest.TestCase):
    """6. 기간 인자."""

    def test_unparseable_period(self):
        with self.assertRaises(ExternalDataError) as ctx:
            build_intent("2026-07-01..2026-07-07", BASELINE)
        self.assertIn("--current", str(ctx.exception))
        self.assertIn("START:END", str(ctx.exception))

    def test_non_iso_bound(self):
        with self.assertRaises(ExternalDataError) as ctx:
            build_intent("2026-07-01:2026-7-7", BASELINE)
        self.assertIn("ISO", str(ctx.exception))
        self.assertIn("2026-7-7", str(ctx.exception))

    def test_inverted_period(self):
        with self.assertRaises(ExternalDataError) as ctx:
            build_intent("2026-07-07:2026-07-01", BASELINE)
        self.assertIn("--current", str(ctx.exception))
        self.assertIn("2026-07-07", str(ctx.exception))

    def test_current_must_start_after_baseline_ends(self):
        with self.assertRaises(ExternalDataError) as ctx:
            build_intent("2026-06-05:2026-06-20", BASELINE)
        self.assertIn("current_period must start after baseline_period ends", str(ctx.exception))

    def test_a_clean_pair_builds(self):
        intent = build_intent(CURRENT, BASELINE)
        intent.validate()

    def test_missing_current_or_baseline(self):
        for current, baseline in ((None, BASELINE), (CURRENT, "")):
            with self.subTest(current=current, baseline=baseline):
                with self.assertRaises(ExternalDataError):
                    build_intent(current, baseline)


class CliArgumentGuards(TempDirCase):
    """4·5·6. CLI 인자 조합."""

    def _exit_message(self, argv) -> str:
        buffer = io.StringIO()
        with no_network():
            with contextlib.redirect_stdout(buffer):
                with self.assertRaises(SystemExit) as ctx:
                    cli.main(argv)
        return str(ctx.exception)

    def test_both_scenario_and_data_dir(self):
        message = self._exit_message(
            ["run", "--scenario", "S01", "--data-dir", TEMPLATE_DIR,
             "--current", CURRENT, "--baseline", BASELINE]
        )
        self.assertIn("--scenario", message)
        self.assertIn("--data-dir", message)
        self.assertIn("함께 쓸 수 없다", message)

    def test_neither_scenario_nor_data_dir(self):
        message = self._exit_message(["run"])
        self.assertIn("--scenario", message)
        self.assertIn("--data-dir", message)

    def test_data_dir_without_periods(self):
        for extra in ([], ["--current", CURRENT], ["--baseline", BASELINE]):
            with self.subTest(extra=extra):
                message = self._exit_message(["run", "--data-dir", TEMPLATE_DIR] + extra)
                self.assertIn("--current", message)
                self.assertIn("--baseline", message)

    def test_periods_are_rejected_on_the_scenario_path(self):
        message = self._exit_message(
            ["run", "--scenario", "S01", "--current", CURRENT]
        )
        self.assertIn("--data-dir", message)

    def test_unparseable_period_through_the_cli(self):
        message = self._exit_message(
            ["run", "--data-dir", TEMPLATE_DIR, "--current", "july", "--baseline", BASELINE]
        )
        self.assertIn("데이터를 받을 수 없다", message)
        self.assertIn("START:END", message)

    def test_inverted_period_through_the_cli(self):
        message = self._exit_message(
            ["run", "--data-dir", TEMPLATE_DIR,
             "--current", "2026-07-07:2026-07-01", "--baseline", BASELINE]
        )
        self.assertIn("데이터를 받을 수 없다", message)

    def test_bad_data_through_the_cli_is_a_clean_exit_not_a_traceback(self):
        lines = read_lines("metrics.csv")
        lines[6] = "2026-06-02,Aurora,delivery_delay,-3"
        path = self.data_dir(metrics=lines)
        message = self._exit_message(
            ["run", "--data-dir", path, "--current", CURRENT, "--baseline", BASELINE]
        )
        self.assertIn("metrics.csv:7", message)


class ModelSelectorIsUnreachable(TempDirCase):
    """5. CLI 에서 유료 모델 호출이 발생할 수 없다."""

    def test_jev_is_refused_on_the_data_dir_path(self):
        buffer = io.StringIO()
        with no_network():
            with contextlib.redirect_stdout(buffer):
                with self.assertRaises(SystemExit) as ctx:
                    cli.main(
                        ["run", "--data-dir", TEMPLATE_DIR, "--current", CURRENT,
                         "--baseline", BASELINE, "--selector", "jev"]
                    )
        message = str(ctx.exception)
        self.assertIn("jev", message)
        self.assertIn("heuristic", message)
        self.assertIn("greedy", message)
        self.assertIn("과금", message)

    def test_jev_is_refused_before_any_data_is_read(self):
        """selector 판정이 먼저다 — 없는 디렉터리를 줘도 selector 오류가 난다."""
        with no_network():
            with self.assertRaises(SystemExit) as ctx:
                cli.run_data_dir(
                    os.path.join(self.tmp, "does-not-exist"), CURRENT, BASELINE, "jev"
                )
        self.assertIn("jev", str(ctx.exception))

    def test_an_unknown_selector_names_the_allowed_values(self):
        with self.assertRaises(SystemExit) as ctx:
            cli.run_data_dir(TEMPLATE_DIR, CURRENT, BASELINE, "gpt")
        self.assertIn("heuristic", str(ctx.exception))
        self.assertIn("greedy", str(ctx.exception))

    def test_the_cli_module_does_not_import_a_model_provider(self):
        """문자열 grep 이 아니라 AST 로 import 문을 본다."""
        with open(os.path.join(REPO_ROOT, "bia", "cli.py"), "r", encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                imported.append(base)
                imported.extend("%s.%s" % (base, alias.name) for alias in node.names)
        for name in imported:
            self.assertNotIn("jev", name.lower())
            self.assertNotIn("http", name.lower())
            self.assertNotIn("urllib", name.lower())
            self.assertNotIn("socket", name.lower())


class FrozenScenarioPathUnchanged(unittest.TestCase):
    """7. 고정 시나리오 경로는 그대로다."""

    def test_run_scenario_matches_the_pipeline_called_directly(self):
        intent, rows, tickets, _meta = load_scenario(
            os.path.join(REPO_ROOT, "data", "scenarios", "S01")
        )
        direct = investigate(intent, rows, tickets, DeterministicHeuristicSelector())
        through_cli = cli.run_scenario("S01")
        self.assertEqual(through_cli.as_dict(), direct.as_dict())

    def test_the_scenario_cli_still_prints_and_exits_zero(self):
        buffer = io.StringIO()
        with no_network():
            with contextlib.redirect_stdout(buffer):
                code = cli.main(["run", "--scenario", "S01"])
        printed = buffer.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("시나리오: S01", printed)
        self.assertIn("## 확인된 사실", printed)

    def test_the_demo_cases_still_run(self):
        for case in ("normal", "partial", "no-evidence"):
            with self.subTest(case=case):
                buffer = io.StringIO()
                with no_network():
                    with contextlib.redirect_stdout(buffer):
                        code = cli.main(["demo", "--case", case])
                self.assertEqual(code, 0)
                self.assertIn("## 확인된 사실", buffer.getvalue())

    def test_the_example_directory_is_outside_the_scored_data_root(self):
        self.assertFalse(TEMPLATE_DIR.startswith(os.path.join(REPO_ROOT, "data")))
        self.assertFalse(os.path.exists(os.path.join(TEMPLATE_DIR, "scenario.json")))


if __name__ == "__main__":
    unittest.main()


class ClosedVocabularyTests(unittest.TestCase):
    """A type outside the lexicon passes every structural check and then yields
    nothing, because no ticket can support a label the term table has never seen.
    That is the same silent-zero-evidence trap the source check exists to stop."""

    def test_an_unknown_complaint_type_is_rejected_at_load(self):
        from bia.external import ExternalDataError, validate_tickets_file

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "tickets.jsonl")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "ticket_id": "T-1",
                            "day": "2026-07-01",
                            "product": "Aurora",
                            "complaint_type": "payment_failed",
                            "text": "The payment failed twice.",
                            "source": "web_form",
                        }
                    )
                    + "\n"
                )
            with self.assertRaises(ExternalDataError) as caught:
                validate_tickets_file(path)
        message = str(caught.exception)
        self.assertIn("payment_failed", message)
        self.assertIn("delivery_delay", message)

    def test_a_known_complaint_type_passes(self):
        from bia.external import validate_tickets_file

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "tickets.jsonl")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "ticket_id": "T-1",
                            "day": "2026-07-01",
                            "product": "Aurora",
                            "complaint_type": "delivery_delay",
                            "text": "The parcel is late.",
                            "source": "web_form",
                        }
                    )
                    + "\n"
                )
            validate_tickets_file(path)
