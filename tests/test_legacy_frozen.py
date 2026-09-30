from __future__ import annotations

import ast
import hashlib
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEGACY = os.path.join(ROOT, "bia", "legacy_comparability.py")

# Task 1 에서 옮긴 직후의 해시. 이 값을 바꾸는 커밋은 이행 증거를 무효로 만든다.
LEGACY_SHA256 = "0c53e70f0243eb48ec54d237eba93fc04fd8d28f7fba4479eb285486603edfea"

ALLOWED_PRODUCT_IMPORTS = {
    "MODE_ALIGNED_WINDOW",
    "MODE_BLOCKED",
    "MODE_FULL",
    "Comparability",
    "PeriodIntegrity",
    "Observation",
}
GUARDED_MODULES = {"integrity", "analysis.frame"}


def imported_from_guarded(source):
    names = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module in GUARDED_MODULES:
            names.update(alias.name for alias in node.names)
    return names


class LegacyFrozenTests(unittest.TestCase):
    def test_legacy_reference_is_unchanged_since_extraction(self):
        with open(LEGACY, "rb") as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()
        self.assertEqual(digest, LEGACY_SHA256)

    def test_legacy_imports_no_function_from_product_modules(self):
        with open(LEGACY, encoding="utf-8") as handle:
            imported = imported_from_guarded(handle.read())
        self.assertTrue(imported)
        self.assertLessEqual(imported, ALLOWED_PRODUCT_IMPORTS)

    def test_import_check_catches_shared_dedupe(self):
        planted = "from .integrity import MODE_FULL, dedupe_rows\nfrom .analysis.frame import observation_key\n"
        leaked = imported_from_guarded(planted) - ALLOWED_PRODUCT_IMPORTS
        self.assertEqual(leaked, {"dedupe_rows", "observation_key"})


if __name__ == "__main__":
    unittest.main()
