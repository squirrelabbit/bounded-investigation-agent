"""CLI. The question shape is fixed; periods come from explicit arguments."""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

from .controller import RunResult, investigate
from .decision import DeterministicHeuristicSelector
from .store import load_intent, load_scenario
from .types import AnalysisIntent, Period

DATA_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
SCENARIO_ROOT = os.path.join(DATA_ROOT, "scenarios")

QUESTION = "지난달보다 고객 불만이 왜 늘었어?"

DEMOS = {
    "normal": ("S01", "정상 증가 — 기여 그룹과 관련 문의 근거"),
    "partial": ("S07", "부분 기간 — 전체 기간 비교를 차단하고 비교 가능한 구간만 사용"),
    "no-evidence": ("S17", "근거 부족 — 기여 그룹은 계산하되 근거는 보류"),
}

SELECTORS = {"heuristic": DeterministicHeuristicSelector}


def _scenario_dir(scenario_id: str) -> str:
    path = os.path.join(SCENARIO_ROOT, scenario_id)
    if not os.path.isdir(path):
        raise SystemExit(
            "시나리오 %s 가 없다. 먼저 `python3 -m bia.datagen` 으로 데이터를 생성하라." % scenario_id
        )
    return path


def run_scenario(scenario_id: str, selector: str = "heuristic") -> RunResult:
    intent, rows, tickets, meta = load_scenario(_scenario_dir(scenario_id))
    provider = SELECTORS[selector]()
    return investigate(intent, rows, tickets, provider)


def _print_header(scenario_id: str, meta_label: Optional[str]) -> None:
    print("질문: %s" % QUESTION)
    print("시나리오: %s%s" % (scenario_id, (" — " + meta_label) if meta_label else ""))
    print("")


def _print_run(result: RunResult) -> None:
    state = result.state
    print(result.answer.render())
    print("---")
    print(
        "종료 사유: %s | decision 호출 %d회 (상한 %d) | retrieval %d회 (상한 %d) | selector %s"
        % (
            state.finish_reason,
            state.decision_calls,
            state.decision_calls + state.budget_left()["decision_calls"],
            state.retrievals,
            state.retrievals + state.budget_left()["retrievals"],
            result.provider_name,
        )
    )
    print("모델 호출: 없음 (오프라인 결정론 selector)")
    if state.violations:
        print("경계 위반 기록: %s" % ", ".join(state.violations))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bia",
        description="기간 간 고객 불만 증가 한 가지 질문만 조사하는 도구",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="시나리오 하나를 조사한다")
    p_run.add_argument("--scenario", required=True)
    p_run.add_argument("--selector", default="heuristic", choices=sorted(SELECTORS))
    p_run.add_argument("--json", action="store_true", help="구조화된 실행 기록을 출력한다")

    p_demo = sub.add_parser("demo", help="데모 3종")
    p_demo.add_argument("--case", required=True, choices=sorted(DEMOS))

    sub.add_parser("list-scenarios", help="생성된 시나리오 목록")

    args = parser.parse_args(argv)

    if args.command == "list-scenarios":
        if not os.path.isdir(SCENARIO_ROOT):
            raise SystemExit("데이터가 없다. 먼저 `python3 -m bia.datagen` 을 실행하라.")
        for name in sorted(os.listdir(SCENARIO_ROOT)):
            meta_path = os.path.join(SCENARIO_ROOT, name, "scenario.json")
            if not os.path.isfile(meta_path):
                continue
            _, meta = load_intent(meta_path)
            print("%s  %s" % (name, meta.get("label", "")))
        return 0

    if args.command == "demo":
        scenario_id, label = DEMOS[args.case]
        _print_header(scenario_id, label)
        _print_run(run_scenario(scenario_id))
        return 0

    result = run_scenario(args.scenario, args.selector)
    if args.json:
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    _print_header(args.scenario, None)
    _print_run(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
