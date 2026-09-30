from __future__ import annotations

import hashlib
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEGACY = os.path.join(ROOT, "bia", "legacy_comparability.py")

# Task 1 에서 옮긴 직후의 해시. 이 값을 바꾸는 커밋은 이행 증거를 무효로 만든다.
LEGACY_SHA256 = "86c710ebd40f2abd54719894659a94c0532b02ab7fb82e62c18c92c7fa123ab2"


class LegacyFrozenTests(unittest.TestCase):
    def test_legacy_reference_is_unchanged_since_extraction(self):
        with open(LEGACY, "rb") as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()
        self.assertEqual(digest, LEGACY_SHA256)


if __name__ == "__main__":
    unittest.main()
