"""The ComplaintAnalysis seam, closed by AST because nothing type-checks the Protocol.

1. Import closure: among product modules under `bia/`, only
   `bia/complaint_analysis.py` imports `bia.metrics`.
2. Consumption closure: every attribute read on `EvidenceState.metrics`, along
   every path it flows through (including local aliases such as
   `metrics = state.metrics` in `bia/answer.py`), is a contract member; and the
   object never escapes into a call, a container or a return value where the
   reads could no longer be seen.

Each check is also fed planted violations to prove it fires. A check that only
ever sees clean code can pass without having looked at anything.
"""
from __future__ import annotations

import ast
import os
import unittest
from typing import Dict, Iterable, List, Optional, Set, Tuple

from bia.complaint_analysis import CONTRACT_MEMBERS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEAM = "bia/complaint_analysis.py"
METRICS = "bia/metrics.py"

EXPECTED_MEMBERS = {
    "current_total",
    "baseline_total",
    "delta",
    "pct_change",
    "by_product",
    "by_complaint_type",
    "cells",
    "increased",
    "top",
    "as_dict",
}


def _python_files(*top: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for directory in top:
        for base, dirs, files in os.walk(os.path.join(ROOT, directory)):
            dirs[:] = sorted(d for d in dirs if d != "__pycache__")
            for name in sorted(files):
                if not name.endswith(".py"):
                    continue
                full = os.path.join(base, name)
                rel = os.path.relpath(full, ROOT).replace(os.sep, "/")
                with open(full, "r", encoding="utf-8") as handle:
                    out[rel] = handle.read()
    return out


def _product_files() -> Dict[str, str]:
    files = _python_files("bia")
    files.pop(METRICS)
    return files


# --------------------------------------------------------------------------
# 1. import closure
# --------------------------------------------------------------------------


def _package_of(relpath: str) -> str:
    # `bia/x.py` and `bia/__init__.py` both resolve relative imports against `bia`.
    return ".".join(relpath[: -len(".py")].split("/")[:-1])


def _resolve(package: str, level: int, module: Optional[str]) -> str:
    if level == 0:
        return module or ""
    parts = package.split(".")
    if level > 1:
        parts = parts[: len(parts) - (level - 1)]
    base = ".".join(parts)
    return base + "." + module if module else base


def _is_metrics_module(name: str) -> bool:
    return name == "bia.metrics" or name.startswith("bia.metrics.")


def metrics_imports(relpath: str, source: str) -> List[str]:
    """Every way this module reaches `bia.metrics`, as `line: form` strings."""
    package = _package_of(relpath)
    hits: List[str] = []
    for node in ast.walk(ast.parse(source, relpath)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_metrics_module(alias.name):
                    hits.append("%d: import %s" % (node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            base = _resolve(package, node.level, node.module)
            if _is_metrics_module(base):
                hits.append("%d: from %s import ..." % (node.lineno, base))
            elif base == "bia":
                for alias in node.names:
                    if alias.name in ("metrics", "*"):
                        hits.append("%d: from bia import %s" % (node.lineno, alias.name))
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name in ("import_module", "__import__") and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    if first.value.split(".")[-1] == "metrics":
                        hits.append("%d: %s(%r)" % (node.lineno, name, first.value))
    return hits


def metrics_importers(files: Dict[str, str]) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for relpath, source in sorted(files.items()):
        hits = metrics_imports(relpath, source)
        if hits:
            out[relpath] = hits
    return out


class ImportClosureTests(unittest.TestCase):
    def test_only_the_seam_imports_bia_metrics(self):
        files = _product_files()
        for expected in ("bia/controller.py", "bia/evidence.py", "bia/answer.py", SEAM):
            self.assertIn(expected, files)
        importers = metrics_importers(files)
        # Equality, not "nothing else": the seam's own import must be seen, or
        # the detector is blind and the empty remainder means nothing.
        self.assertEqual(sorted(importers), [SEAM], importers)

    def test_every_import_form_is_caught(self):
        planted = {
            "bia/controller.py": "from . import metrics as metrics_mod\n",
            "bia/evidence.py": "from .metrics import MetricResult\n",
            "bia/answer.py": "from bia.metrics import compute\n",
            "bia/cli.py": "import bia.metrics\n",
            "bia/verify.py": "import bia.metrics as m\n",
            "bia/adapters/complaints.py": "from ..metrics import compute\n",
            "bia/domains/ecommerce.py": "from .. import metrics\n",
            "bia/store.py": "from bia import metrics\n",
            "bia/retrieval.py": "import importlib\nm = importlib.import_module('bia.metrics')\n",
            "bia/lexicon.py": "m = __import__('bia.metrics')\n",
            "bia/jev.py": "from bia.metrics import *\n",
        }
        importers = metrics_importers(planted)
        self.assertEqual(sorted(importers), sorted(planted), importers)

    def test_real_module_with_a_planted_import_is_caught(self):
        files = _product_files()
        files["bia/controller.py"] = files["bia/controller.py"].replace(
            "from . import integrity as integrity_mod\n",
            "from . import integrity as integrity_mod\nfrom . import metrics as metrics_mod\n",
            1,
        )
        self.assertEqual(sorted(metrics_importers(files)), sorted(["bia/controller.py", SEAM]))

    def test_mentions_that_are_not_imports_are_ignored(self):
        clean = {
            "bia/datagen.py": (
                "# from . import metrics\n"
                '"""does not import `bia.metrics`"""\n'
                "from .types import METRIC_COMPLAINT_COUNT\n"
                "from . import metrics_extra\n"
                "path = 'metrics.csv'\n"
            ),
            # A sibling package's own `metrics` module is not `bia.metrics`.
            "bia/analysis/x.py": "from .metrics import thing\n",
        }
        self.assertEqual(metrics_importers(clean), {})


# --------------------------------------------------------------------------
# 2. consumption closure
# --------------------------------------------------------------------------

_SCOPES = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
_TEST_PARENTS = (ast.If, ast.While, ast.IfExp, ast.Assert)


class Report:
    def __init__(self) -> None:
        self.reads: List[Tuple[int, str, str]] = []  # (line, attribute, via)
        self.escapes: List[Tuple[int, str]] = []

    def attrs(self, via: Optional[str] = None) -> Set[str]:
        return {a for _, a, v in self.reads if via is None or v == via}


def _annotation_names(annotation: Optional[ast.expr]) -> Set[str]:
    if annotation is None:
        return set()
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        try:
            annotation = ast.parse(annotation.value, mode="eval").body
        except SyntaxError:
            return set()
    return {n.id for n in ast.walk(annotation) if isinstance(n, ast.Name)} | {
        n.attr for n in ast.walk(annotation) if isinstance(n, ast.Attribute)
    }


class _Scanner:
    def __init__(self, tree: ast.AST) -> None:
        self.parent: Dict[ast.AST, ast.AST] = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                self.parent[child] = node
        self.tree = tree

    def enclosing(self, node: ast.AST, kinds) -> Optional[ast.AST]:
        current = self.parent.get(node)
        while current is not None and not isinstance(current, kinds):
            current = self.parent.get(current)
        return current

    def state_names(self, scope: ast.AST) -> Set[str]:
        """`state`, plus any parameter annotated as EvidenceState in this scope."""
        names = {"state"}
        args = getattr(scope, "args", None)
        if isinstance(args, ast.arguments):
            for arg in args.posonlyargs + args.args + args.kwonlyargs:
                if "EvidenceState" in _annotation_names(arg.annotation):
                    names.add(arg.arg)
        return names

    def root_via(self, node: ast.AST) -> Optional[str]:
        """How `node` denotes EvidenceState.metrics, or None if it does not."""
        if not (isinstance(node, ast.Attribute) and node.attr == "metrics"):
            return None
        if not isinstance(node.ctx, ast.Load):
            return None
        value = node.value
        if isinstance(value, ast.Attribute) and value.attr == "state":
            return "state"
        if isinstance(value, ast.Name):
            scope = self.enclosing(node, _SCOPES) or self.tree
            if value.id in self.state_names(scope):
                return "state"
            if value.id == "self":
                cls = self.enclosing(node, (ast.ClassDef,))
                if cls is not None and cls.name == "EvidenceState":
                    return "self"
        return None


def _alias_target(scanner: _Scanner, node: ast.AST) -> Optional[str]:
    parent = scanner.parent.get(node)
    if isinstance(parent, ast.Assign) and parent.value is node:
        if len(parent.targets) == 1 and isinstance(parent.targets[0], ast.Name):
            return parent.targets[0].id
    if isinstance(parent, ast.AnnAssign) and parent.value is node:
        if isinstance(parent.target, ast.Name):
            return parent.target.id
    if isinstance(parent, ast.NamedExpr) and parent.value is node:
        return parent.target.id
    return None


def _in_test_position(scanner: _Scanner, node: ast.AST) -> bool:
    parent = scanner.parent.get(node)
    if isinstance(parent, _TEST_PARENTS) and parent.test is node:
        return True
    if isinstance(parent, ast.UnaryOp) and isinstance(parent.op, ast.Not):
        return True
    if isinstance(parent, ast.BoolOp):
        # `a or state.metrics` evaluates to the object itself, so a BoolOp only
        # counts as a presence test when the BoolOp is itself one.
        return _in_test_position(scanner, parent)
    return False


def _is_none_check(scanner: _Scanner, node: ast.AST) -> bool:
    parent = scanner.parent.get(node)
    if not isinstance(parent, ast.Compare):
        return False
    if not all(isinstance(op, (ast.Is, ast.IsNot)) for op in parent.ops):
        return False
    operands = [parent.left] + list(parent.comparators)
    others = [o for o in operands if o is not node]
    return all(isinstance(o, ast.Constant) and o.value is None for o in others)


def metrics_consumption(relpath: str, source: str) -> Report:
    tree = ast.parse(source, relpath)
    scanner = _Scanner(tree)
    report = Report()

    # (node, via) for every expression that denotes EvidenceState.metrics.
    tracked: List[Tuple[ast.AST, str]] = []
    for node in ast.walk(tree):
        via = scanner.root_via(node)
        if via is not None:
            tracked.append((node, via))

    # Follow local aliases (`metrics = state.metrics`, and aliases of aliases)
    # to a fixpoint. An alias lives in the scope that bound it, nested
    # functions included.
    aliases: Set[Tuple[ast.AST, str]] = set()
    changed = True
    while changed:
        changed = False
        for node, via in list(tracked):
            name = _alias_target(scanner, node)
            if name is None:
                continue
            scope = scanner.enclosing(node, _SCOPES) or tree
            if (scope, name) in aliases:
                continue
            aliases.add((scope, name))
            changed = True
            for inner in ast.walk(scope):
                if isinstance(inner, ast.Name) and inner.id == name and isinstance(inner.ctx, ast.Load):
                    tracked.append((inner, "alias:" + via))

    seen: Set[int] = set()
    for node, via in tracked:
        if id(node) in seen:
            continue
        seen.add(id(node))
        parent = scanner.parent.get(node)
        line = getattr(node, "lineno", 0)
        if isinstance(parent, ast.Attribute) and parent.value is node:
            report.reads.append((line, parent.attr, via))
        elif _is_none_check(scanner, node) or _in_test_position(scanner, node):
            continue
        elif _alias_target(scanner, node) is not None:
            continue
        else:
            report.escapes.append((line, "%s used as %s" % (via, type(parent).__name__)))
    return report


def _consumer_files() -> Dict[str, str]:
    files = _product_files()
    files.update(_python_files("eval"))
    return files


def _violations(reports: Dict[str, Report]) -> List[str]:
    out: List[str] = []
    for relpath, report in sorted(reports.items()):
        for line, attr, via in report.reads:
            if attr not in CONTRACT_MEMBERS:
                out.append("%s:%d reads .%s via %s" % (relpath, line, attr, via))
        for line, what in report.escapes:
            out.append("%s:%d escapes: %s" % (relpath, line, what))
    return out


def scan(files: Dict[str, str]) -> Dict[str, Report]:
    return {relpath: metrics_consumption(relpath, source) for relpath, source in files.items()}


class ContractDefinitionTests(unittest.TestCase):
    def test_contract_members_are_the_ten_agreed_names(self):
        self.assertEqual(set(CONTRACT_MEMBERS), EXPECTED_MEMBERS)

    def test_protocol_declares_exactly_the_contract_members(self):
        with open(os.path.join(ROOT, SEAM), "r", encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        protocol = next(
            n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ComplaintAnalysis"
        )
        declared = set()
        for item in protocol.body:
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                declared.add(item.target.id)
            elif isinstance(item, ast.FunctionDef):
                declared.add(item.name)
        self.assertEqual(declared, EXPECTED_MEMBERS)


class ConsumptionClosureTests(unittest.TestCase):
    def test_every_read_on_the_real_path_is_a_contract_member(self):
        reports = scan(_consumer_files())
        self.assertEqual(_violations(reports), [])

    def test_the_scan_actually_saw_each_consumer(self):
        # Without these, an empty violation list could come from a scan that
        # tracked nothing. answer.py reads almost everything through a local
        # alias, which a `state.metrics.X`-only matcher would miss.
        reports = scan(_consumer_files())
        answer = reports["bia/answer.py"]
        self.assertTrue(
            {"current_total", "baseline_total", "delta", "pct_change", "increased",
             "by_product", "by_complaint_type"} <= answer.attrs("alias:state"),
            answer.reads,
        )
        self.assertIn("increased", answer.attrs("state"))
        evidence = reports["bia/evidence.py"]
        self.assertEqual({"delta", "as_dict"}, evidence.attrs("self"))
        self.assertIn("cells", evidence.attrs("state"))
        controller = reports["bia/controller.py"]
        self.assertEqual({"top", "increased", "cells"}, controller.attrs("state"))

    def test_other_objects_named_metrics_are_not_in_scope(self):
        # DomainSpec.metrics is a different object (bia/analysis/compiler.py,
        # bia/analysis/spec.py). Matching every `.metrics` would flag these.
        reports = scan(_consumer_files())
        self.assertEqual(reports["bia/analysis/compiler.py"].reads, [])
        self.assertEqual(reports["bia/analysis/spec.py"].reads, [])
        planted = scan(
            {
                "bia/x.py": (
                    "class DomainSpec:\n"
                    "    def f(self):\n"
                    "        return list(self.metrics.items())\n"
                    "def g(domain):\n"
                    "    return domain.metrics.get('x')\n"
                )
            }
        )
        self.assertEqual(_violations(planted), [])
        self.assertEqual(planted["bia/x.py"].reads, [])


class ConsumptionClosureFiresTests(unittest.TestCase):
    def _assert_caught(self, source: str, fragment: str) -> None:
        violations = _violations(scan({"bia/planted.py": source}))
        self.assertTrue(
            any(fragment in v for v in violations),
            "expected %r in %r" % (fragment, violations),
        )

    def test_alias_read_outside_the_contract(self):
        self._assert_caught(
            "def f(state):\n"
            "    metrics = state.metrics\n"
            "    assert metrics is not None\n"
            "    return metrics.share_total\n",
            "reads .share_total via alias:state",
        )

    def test_alias_of_an_alias(self):
        self._assert_caught(
            "def f(state):\n"
            "    m = state.metrics\n"
            "    n = m\n"
            "    return n.frame\n",
            "reads .frame via alias:alias:state",
        )

    def test_alias_read_in_the_real_answer_module(self):
        files = _consumer_files()
        source = files["bia/answer.py"]
        mutated = source.replace("metrics.pct_change is not None", "metrics.pct_changed is not None", 1)
        self.assertNotEqual(source, mutated)
        files["bia/answer.py"] = mutated
        self.assertIn(
            "bia/answer.py:219 reads .pct_changed via alias:state",
            "\n".join(_violations(scan(files))),
        )

    def test_direct_read_outside_the_contract(self):
        self._assert_caught("def f(state):\n    return state.metrics.result\n", "reads .result via state")

    def test_nested_state_attribute(self):
        self._assert_caught("def f(r):\n    return r.state.metrics.frame\n", "reads .frame via state")

    def test_self_read_inside_evidence_state(self):
        self._assert_caught(
            "class EvidenceState:\n"
            "    def f(self):\n"
            "        return self.metrics.raw\n",
            "reads .raw via self",
        )

    def test_parameter_annotated_as_evidence_state(self):
        self._assert_caught(
            "def f(s: 'EvidenceState'):\n    return s.metrics.raw\n",
            "reads .raw via state",
        )

    def test_escape_into_a_call(self):
        self._assert_caught("def f(state):\n    helper(state.metrics)\n", "escapes: state used as Call")

    def test_escape_through_getattr_on_an_alias(self):
        self._assert_caught(
            "def f(state):\n    m = state.metrics\n    return getattr(m, 'raw')\n",
            "escapes: alias:state used as Call",
        )

    def test_escape_by_return(self):
        self._assert_caught(
            "def f(state):\n    m = state.metrics\n    return m\n",
            "escapes: alias:state used as Return",
        )

    def test_escape_through_boolop_value(self):
        self._assert_caught(
            "def f(state):\n    x = state.metrics or None\n    return x\n",
            "escapes: state used as BoolOp",
        )

    def test_escape_into_a_container(self):
        self._assert_caught("def f(state):\n    return [state.metrics]\n", "escapes: state used as List")


if __name__ == "__main__":
    unittest.main()
