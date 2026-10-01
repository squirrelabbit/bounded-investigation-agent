from __future__ import annotations

import ast
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIA = os.path.join(ROOT, "bia")
LEGACY = "legacy_comparability"


def _importers():
    found = []
    for base, _, files in os.walk(BIA):
        for name in files:
            if not name.endswith(".py") or name == LEGACY + ".py":
                continue
            path = os.path.join(base, name)
            tree = ast.parse(open(path, encoding="utf-8").read(), path)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    mods = [node.module or ""] + [a.name for a in node.names]
                elif isinstance(node, ast.Import):
                    mods = [a.name for a in node.names]
                else:
                    continue
                if any(m == LEGACY or m.endswith("." + LEGACY) for m in mods):
                    found.append(os.path.relpath(path, ROOT))
    return sorted(set(found))


class LegacyIsolationTests(unittest.TestCase):
    def test_no_product_module_imports_the_legacy_reference(self):
        self.assertEqual(_importers(), [])

    def test_the_check_would_catch_an_import(self):
        tree = ast.parse("from . import legacy_comparability as legacy\n")
        node = tree.body[0]
        self.assertIn(LEGACY, [a.name for a in node.names])


if __name__ == "__main__":
    unittest.main()
