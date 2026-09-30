"""v2.1 이행의 제품 동작 기준 digest 두 개를 만든다.

corpus: 24 시나리오 + 8 챌린지를 heuristic·greedy 로 실행한 RunResult.as_dict() 64 개.
jev:    같은 64 실행에서 decision 호출마다 JEV 입력 문자열과 state 전체 JSON.

사용: python3 scripts/v21_baseline_digests.py [--expect-corpus HEX] [--expect-jev HEX]
기대값을 주면 다를 때 비-0 으로 끝난다(assert 가 아니라 명시적 종료라 -O 에서도 산다).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import bia  # noqa: E402
from bia.controller import investigate  # noqa: E402
from bia.decision import DeterministicHeuristicSelector, GreedyEvidenceSelector  # noqa: E402
from bia.jev import serialize_state  # noqa: E402
from bia.store import load_scenario  # noqa: E402

SELECTORS = (("heuristic", DeterministicHeuristicSelector), ("greedy", GreedyEvidenceSelector))


class _Recording:
    """받은 view 를 기록하고 실제 선택기에 그대로 넘긴다. 실행 경로를 바꾸지 않는다."""

    def __init__(self, inner):
        self.inner = inner
        self.name = inner.name
        self.calls = []

    def select_next_evidence(self, state, candidates):
        self.calls.append({"serialized": serialize_state(state),
                           "view": json.dumps(state, sort_keys=True, ensure_ascii=False)})
        return self.inner.select_next_evidence(state, candidates)


def _cases():
    out = []
    for group in ("scenarios", "challenges"):
        base = os.path.join(ROOT, "data", group)
        for cid in sorted(os.listdir(base)):
            if os.path.isfile(os.path.join(base, cid, "scenario.json")):
                out.append((cid, os.path.join(base, cid)))
    return out


def compute():
    runs = {}
    records = []
    for cid, directory in _cases():
        for label, factory in SELECTORS:
            intent, rows, tickets, _ = load_scenario(directory)
            selector = _Recording(factory())
            result = investigate(intent, rows, tickets, selector)
            runs["%s/%s" % (cid, label)] = json.dumps(result.as_dict(), ensure_ascii=False)
            records.append([cid, selector.name, selector.calls])
    corpus = hashlib.sha256(
        json.dumps(runs, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    records.sort(key=lambda r: (r[0], r[1]))
    jev = hashlib.sha256(json.dumps(records, ensure_ascii=False).encode("utf-8")).hexdigest()
    calls = sum(len(r[2]) for r in records)
    return {"bia_file": os.path.abspath(bia.__file__), "runs": len(runs), "decision_calls": calls,
            "corpus": corpus, "jev": jev}


def main(argv):
    expect = {}
    for flag, key in (("--expect-corpus", "corpus"), ("--expect-jev", "jev")):
        if flag in argv:
            expect[key] = argv[argv.index(flag) + 1]
    got = compute()
    print(json.dumps(got, ensure_ascii=False, indent=1))
    bad = [k for k, v in expect.items() if got[k] != v]
    if bad:
        print("MISMATCH: %s" % ", ".join(bad))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
