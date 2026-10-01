"""v2.1 에서 oracle.json 에 허용된 변경은 합 분해의 expect_group_transition 추가뿐이다.
새 키만 지운 결과가 320ba68 의 oracle.json 과 같아야 한다."""
from __future__ import annotations

import json
import subprocess
import sys

NEW_KEY = "expect_group_transition"


def strip(node, counter):
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            if key == NEW_KEY:
                counter[0] += 1
                continue
            out[key] = strip(value, counter)
        return out
    if isinstance(node, list):
        return [strip(v, counter) for v in node]
    return node


def main():
    old = json.loads(subprocess.check_output(["git", "show", "320ba68:data/v2/oracle.json"]).decode("utf-8"))
    new = json.load(open("data/v2/oracle.json", encoding="utf-8"))
    counter = [0]
    stripped = strip(new, counter)
    print("추가된 %s: %d" % (NEW_KEY, counter[0]))
    print("새 키 제거 후 320ba68 과 동일:", stripped == old)
    return 0 if (stripped == old and counter[0] > 0) else 1


if __name__ == "__main__":
    sys.exit(main())
