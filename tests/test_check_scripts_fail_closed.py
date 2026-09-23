"""검사 스크립트는 `python3 -O` 에서도 위반을 잡아야 한다.

`assert` 는 `-O` 에서 통째로 사라진다. 검사 스크립트는 README 가 "독립 재계산" 으로
인용하는 증거 산출물이므로, 최적화 플래그 하나로 모든 가드가 빠진 채 통과하면
증거가 아니라 장식이다.

여기의 시험은 **실제로 `-O` 서브프로세스를 띄워** 위반 입력에 비-0 종료를 확인한다.
`assert` 개수를 세거나 소스를 훑는 방식으로는 이 성질을 증명할 수 없다 — 그런 검사는
스크립트가 실제로 어떻게 끝나는지 한 번도 보지 않는다.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 생성기를 패치해 디스크의 CSV 만 한 칸 틀어 놓는다. oracle 은 사례 선언에서 나오므로
# 영향을 받지 않는다. 재생성은 여전히 바이트 동일하므로 첫 가드(바이트 동일성)를
# 통과하고, 디스크에서 다시 센 합계가 oracle 과 어긋나는 **깊은** 가드가 발동한다.
CSV_CORRUPTION = '''

_original_write_case_csv = write_case_csv


def write_case_csv(case, root):  # noqa: F811
    path = _original_write_case_csv(case, root)
    if case.case_id != "C01":
        return path
    with open(path, "r", encoding="utf-8", newline="") as handle:
        lines = handle.read().split("\\n")
    fields = lines[1].split(",")
    fields[-1] = str(int(fields[-1]) + 1)
    lines[1] = ",".join(fields)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write("\\n".join(lines))
    return path
'''


class CheckV2BenchFailsClosedUnderOTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="check-v2bench-")
        self.addCleanup(shutil.rmtree, self.tmp)
        for name in ("bia", "scripts"):
            shutil.copytree(os.path.join(REPO_ROOT, name),
                            os.path.join(self.tmp, name),
                            ignore=shutil.ignore_patterns("__pycache__"))
        # C16 만 저장소의 v1 시나리오 CSV 를 그대로 읽는다.
        source = os.path.join(self.tmp, "data", "scenarios", "S01")
        os.makedirs(source)
        shutil.copyfile(os.path.join(REPO_ROOT, "data", "scenarios", "S01", "metrics.csv"),
                        os.path.join(source, "metrics.csv"))
        self._generate()

    def _generate(self):
        subprocess.check_call([sys.executable, "-m", "bia.v2bench.generate"],
                              cwd=self.tmp, stdout=subprocess.DEVNULL)

    def _run_checker(self, optimized):
        argv = [sys.executable]
        if optimized:
            argv.append("-O")
        argv.append(os.path.join(self.tmp, "scripts", "check_v2bench.py"))
        process = subprocess.Popen(argv, cwd=self.tmp, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        out, err = process.communicate()
        return (process.returncode, out.decode("utf-8", "replace"),
                err.decode("utf-8", "replace"))

    def test_an_untouched_copy_passes_under_O(self):
        """음성 대조. 이것이 실패하면 아래의 비-0 종료는 위반의 증거가 아니다."""
        code, out, err = self._run_checker(optimized=True)
        self.assertEqual(code, 0, err)
        self.assertIn("ALL CHECKS PASSED", out)

    def test_a_stray_file_is_caught_under_O(self):
        """리뷰어가 실증한 그 입력이다 — 재생성 바이트 동일성 가드."""
        stray = os.path.join(self.tmp, "data", "v2", "ZZZ")
        os.makedirs(stray)
        with open(os.path.join(stray, "stray.txt"), "w", encoding="utf-8") as handle:
            handle.write("planted\n")
        code, out, err = self._run_checker(optimized=True)
        self.assertNotEqual(code, 0, out)
        self.assertNotIn("ALL CHECKS PASSED", out)
        self.assertIn("바이트 동일하지 않다", err)

    def test_a_csv_that_disagrees_with_the_oracle_is_caught_under_O(self):
        """첫 가드를 통과한 뒤의 깊은 가드도 `-O` 에서 살아 있는지 본다."""
        path = os.path.join(self.tmp, "bia", "v2bench", "generate.py")
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
        anchor = 'if __name__ == "__main__":'
        self.assertIn(anchor, source)
        # `main()` 호출은 파일 끝에 있다. 뒤에 붙이면 이미 실행이 끝난 뒤라 패치가
        # 아무 일도 하지 않고, 시험이 조용히 공허해진다.
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(source.replace(anchor, CSV_CORRUPTION + "\n" + anchor, 1))
        self._generate()
        code, out, err = self._run_checker(optimized=True)
        self.assertNotEqual(code, 0, out)
        self.assertNotIn("ALL CHECKS PASSED", out)
        self.assertIn("recomputed", err)
        # 최적화하지 않은 실행과 같은 결론이어야 한다.
        plain_code, plain_out, _plain_err = self._run_checker(optimized=False)
        self.assertNotEqual(plain_code, 0, plain_out)


class ScenarioCheckersFailClosedUnderOTests(unittest.TestCase):
    """`check_datagen.py`·`check_challenges.py` 의 파일 파싱 가드도 같은 성질이다."""

    PROBE = (
        "import sys\n"
        "sys.path.insert(0, %(repo)r)\n"
        "sys.path.insert(0, %(scripts)r)\n"
        "import %(module)s as checker\n"
        "checker.%(constant)s = %(root)r\n"
        "checker.load_rows('X01')\n"
        "print('GUARD DID NOT FIRE')\n"
    )

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="check-scripts-")
        self.addCleanup(shutil.rmtree, self.tmp)

    def _plant(self, *parts):
        directory = os.path.join(self.tmp, *parts)
        os.makedirs(directory)
        with open(os.path.join(directory, "metrics.csv"), "w", encoding="utf-8") as handle:
            handle.write("day,product,count\n2026-06-01,a,1\n")

    def _probe(self, module, constant, root):
        script = self.PROBE % {"repo": REPO_ROOT,
                               "scripts": os.path.join(REPO_ROOT, "scripts"),
                               "module": module, "constant": constant,
                               "root": root}
        process = subprocess.Popen([sys.executable, "-O", "-c", script],
                                   cwd=REPO_ROOT, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        out, err = process.communicate()
        return (process.returncode, out.decode("utf-8", "replace"),
                err.decode("utf-8", "replace"))

    def test_check_datagen_rejects_a_wrong_header_under_O(self):
        self._plant("data", "scenarios", "X01")
        code, out, err = self._probe("check_datagen", "DATA",
                                     os.path.join(self.tmp, "data"))
        self.assertNotEqual(code, 0, out)
        self.assertNotIn("GUARD DID NOT FIRE", out)
        self.assertIn("헤더가 다르다", err)

    def test_check_challenges_rejects_a_wrong_header_under_O(self):
        self._plant("challenges", "X01")
        code, out, err = self._probe("check_challenges", "CHALLENGE_DIR",
                                     os.path.join(self.tmp, "challenges"))
        self.assertNotEqual(code, 0, out)
        self.assertNotIn("GUARD DID NOT FIRE", out)
        self.assertIn("헤더가 다르다", err)


if __name__ == "__main__":
    unittest.main()
