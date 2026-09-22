"""CLI. The question shape is fixed; periods come from explicit arguments."""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

from .controller import RunResult, investigate
from .decision import DeterministicHeuristicSelector, GreedyEvidenceSelector
from .external import ExternalDataError, load_external_dataset
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

SELECTORS = {
    "heuristic": DeterministicHeuristicSelector,
    "greedy": GreedyEvidenceSelector,
}

# 모델 기반 selector 는 CLI 에서 도달할 수 없다. 이름을 여기 적어두는 것은
# 오타 취급으로 넘기지 않고 "왜 안 되는지"를 말해주기 위해서다.
MODEL_SELECTORS = ("jev", "model")


def _resolve_selector(name: str):
    if name in SELECTORS:
        return SELECTORS[name]()
    if name in MODEL_SELECTORS:
        raise SystemExit(
            "selector %s 는 bia.cli 에서 실행할 수 없다. CLI 는 오프라인 코드 selector(%s)만 "
            "실행한다 — 모델 호출은 과금되므로 CLI 한 줄로 발생시키지 않는다."
            % (name, ", ".join(sorted(SELECTORS)))
        )
    raise SystemExit(
        "selector %s 를 모른다. 사용할 수 있는 값: %s" % (name, ", ".join(sorted(SELECTORS)))
    )


def _scenario_dir(scenario_id: str) -> str:
    path = os.path.join(SCENARIO_ROOT, scenario_id)
    if not os.path.isdir(path):
        raise SystemExit(
            "시나리오 %s 가 없다. 먼저 `python3 -m bia.datagen` 으로 데이터를 생성하라." % scenario_id
        )
    return path


def run_scenario(scenario_id: str, selector: str = "heuristic") -> RunResult:
    intent, rows, tickets, meta = load_scenario(_scenario_dir(scenario_id))
    provider = _resolve_selector(selector)
    return investigate(intent, rows, tickets, provider)


def run_data_dir(
    data_dir: str, current: str, baseline: str, selector: str = "heuristic"
) -> RunResult:
    """사용자 데이터로 같은 파이프라인을 돈다. 로더·무결성·지표·후보·검색·검증·
    컨트롤러·렌더러는 고정 시나리오와 완전히 동일하다."""
    provider = _resolve_selector(selector)
    intent, rows, tickets = load_external_dataset(data_dir, current, baseline)
    return investigate(intent, rows, tickets, provider)


def _print_header(scenario_id: str, meta_label: Optional[str]) -> None:
    print("질문: %s" % QUESTION)
    print("시나리오: %s%s" % (scenario_id, (" — " + meta_label) if meta_label else ""))
    print("")


def _print_external_header(data_dir: str, intent: AnalysisIntent) -> None:
    print("질문: %s" % QUESTION)
    print("데이터: %s" % data_dir)
    print("비교 구간: 현재 %s / 기준 %s" % (intent.current_period, intent.baseline_period))
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

    p_run = sub.add_parser("run", help="시나리오 하나 또는 내 데이터 하나를 조사한다")
    p_run.add_argument("--scenario", help="번들된 시나리오 id")
    p_run.add_argument(
        "--data-dir",
        dest="data_dir",
        help="metrics.csv 와 tickets.jsonl 이 들어 있는 디렉터리",
    )
    p_run.add_argument("--current", help="현재 구간 START:END (--data-dir 전용, 양끝 포함)")
    p_run.add_argument("--baseline", help="기준 구간 START:END (--data-dir 전용, 양끝 포함)")
    p_run.add_argument("--selector", default="heuristic")
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

    if args.scenario and args.data_dir:
        raise SystemExit("--scenario 와 --data-dir 은 함께 쓸 수 없다. 하나만 지정하라.")
    if not args.scenario and not args.data_dir:
        raise SystemExit(
            "--scenario 또는 --data-dir 중 하나가 필요하다. "
            "내 데이터를 쓰려면 --data-dir DIR --current START:END --baseline START:END."
        )

    if args.data_dir:
        if not args.current or not args.baseline:
            raise SystemExit(
                "--data-dir 에는 --current 와 --baseline 이 모두 필요하다 "
                "(예: --current 2026-07-01:2026-07-30 --baseline 2026-06-01:2026-06-30). "
                "scenario.json 이 없으므로 질문의 구간은 인자에서만 온다."
            )
        try:
            result = run_data_dir(args.data_dir, args.current, args.baseline, args.selector)
        except ExternalDataError as exc:
            raise SystemExit("데이터를 받을 수 없다 — %s" % exc)
        if args.json:
            print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        _print_external_header(args.data_dir, result.intent)
        _print_run(result)
        return 0

    if args.current or args.baseline:
        raise SystemExit("--current/--baseline 은 --data-dir 에서만 쓴다. 시나리오의 구간은 scenario.json 이 갖고 있다.")

    result = run_scenario(args.scenario, args.selector)
    if args.json:
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    _print_header(args.scenario, None)
    _print_run(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
