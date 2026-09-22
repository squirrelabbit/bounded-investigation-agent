# v2 Typed Structured Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** v1 의 고정된 `complaint_count × product × complaint_type` 계산을, 도메인이 선언한 metric·grain·dimension 계약을 받아 additive·ratio 비교 분석을 수행하는 typed execution layer 로 치환한다.

**Architecture:** 새 `bia/analysis/` 패키지가 도메인을 모르는 공용 엔진이 되고, 산출물은 `StructuredAnalysisResult` 다. 도메인은 `bia/domains/` 에 선언만 두고, v1 의 evidence pipeline 은 `bia/adapters/complaints.py` 를 통해 그 산출물을 소비한다. v1 의 `evidence/retrieval/verify/decision/controller/answer` 는 일반화하지 않는다.

**Tech Stack:** Python 3.9+ 표준 라이브러리만. 테스트는 `unittest`. 외부 의존성 0, 네트워크 0.

**Spec:** `docs/superpowers/specs/2026-09-22-v2-typed-structured-analysis-design.md`

## Global Constraints

- Python 3.9.6 과 `/opt/homebrew/bin/python3.11` 양쪽에서 통과해야 한다. `from __future__ import annotations` 를 쓰고 `X | Y` 타입 문법은 쓰지 않는다.
- 표준 라이브러리만. 새 의존성 금지. 네트워크 호출 금지.
- **동결**: `data/scenarios/**`, `data/challenges/**`, `data/oracle/**`, `eval/**`, `bia/datagen.py`, `bia/scenarios.py`, `bia/challengegen.py`, `bia/challenges.py` 를 수정하지 않는다.
- **동결**: `bia/evidence.py`, `bia/retrieval.py`, `bia/verify.py`, `bia/decision.py`, `bia/controller.py`, `bia/answer.py`, `bia/lexicon.py`, `bia/jev.py` 를 일반화하지 않는다. Task 3 의 adapter 연결에 필요한 최소 수정만 허용하며, 그 경우에도 기존 공개 시그니처를 바꾸지 않는다.
- 사전 등록 상수(수정 금지): `CANCELLATION_THRESHOLD = 0.20`, `SHARE_EPSILON = 0.0001`, `CROSS_CELL_LIMIT = 1000`, `FLOAT_TOL = 1e-9`, `GROSS_EPSILON = 1e-12`.
- `bia/analysis/` 안에 도메인 이름 문자열 리터럴이 존재하면 안 된다. Task 6 이 AST 로 검사한다.
- 모든 부호 비교는 tolerance-aware. `|x| <= FLOAT_TOL` 이면 0 으로 본다.
- 매 Task 종료 시 전체 스위트가 통과해야 한다: `python3 -m unittest discover -t . -s tests -q`
- 매 Task 종료 시 v0 채점 결과가 바이트 불변이어야 한다: `python3 eval/run_eval.py` 후 `git diff --quiet -- eval/results/heuristic.json`

---

## File Structure

| 파일 | 책임 |
|---|---|
| `bia/analysis/spec.py` | `DomainSpec` / `MetricSpec` / `DimensionSpec`. 선언만, 동작 없음 |
| `bia/analysis/request.py` | `AnalysisRequest` / `PeriodComparison` |
| `bia/analysis/registry.py` | 선언된 도메인의 닫힌 목록과 조회 |
| `bia/analysis/errors.py` | `SpecError` / `RequestError` / `AnalysisRefused` |
| `bia/analysis/frame.py` | `Observation` 과 CSV → Observation 로딩·정규화 |
| `bia/analysis/compiler.py` | request + spec → `ExecutionPlan` (검증 포함) |
| `bia/analysis/operators.py` | `AGGREGATE COMPARE BREAKDOWN CONTRIBUTION RATE RANK` |
| `bia/analysis/decompose.py` | 중간점 rate/mix 분해, 플래그, 출력 정책 |
| `bia/analysis/result.py` | `StructuredAnalysisResult` 와 하위 타입 |
| `bia/analysis/engine.py` | plan 실행 진입점 |
| `bia/domains/complaints.py` | v1 을 선언으로 재표현 |
| `bia/domains/ecommerce.py` | 새 도메인 선언 |
| `bia/domains/support_ops.py` | 새 도메인 선언 |
| `bia/adapters/complaints.py` | `StructuredAnalysisResult` → v1 evidence pipeline |
| `bia/integrity.py` | (수정) grain 기준 중복 검사 추가, 기존 동작 보존 |

---

### Task 1: 타입·레지스트리·Compiler 계약

실행 코드 없음. 선언과 검증만. 이 Task 가 끝나면 잘못된 `AnalysisRequest` 가 거부되지만 아무것도 계산되지 않는다.

**Files:**
- Create: `bia/analysis/__init__.py`, `bia/analysis/errors.py`, `bia/analysis/spec.py`, `bia/analysis/request.py`, `bia/analysis/registry.py`, `bia/analysis/compiler.py`
- Test: `tests/test_analysis_spec.py`, `tests/test_analysis_compiler.py`

**Interfaces:**
- Produces:
  - `MetricSpec(name: str, kind: str, value: Optional[str], numerator: Optional[str], denominator: Optional[str], numerator_bounded_by_denominator: bool)` — `kind` 는 `"additive"` 또는 `"ratio"`
  - `DomainSpec(name: str, grain: Tuple[str, ...], dimensions: Tuple[str, ...], metrics: Dict[str, MetricSpec])`
  - `PeriodComparison(current: Period, baseline: Period)`
  - `AnalysisRequest(domain: str, metric: str, breakdowns: Tuple[str, ...], comparison: PeriodComparison, rank_by: str)`
  - `ExecutionPlan(domain: DomainSpec, metric: MetricSpec, comparison: PeriodComparison, branches: Tuple[PlanBranch, ...], rank_by: str)`
  - `PlanBranch(dimensions: Tuple[str, ...], cross: bool)` — `dimensions` 가 빈 튜플이면 overall
  - `compile_request(request: AnalysisRequest) -> ExecutionPlan`
  - `register(spec: DomainSpec) -> None`, `get_domain(name: str) -> DomainSpec`
  - `SpecError`, `RequestError` (둘 다 `ValueError` 하위)

- [ ] **Step 1: 실패하는 테스트를 쓴다 — MetricSpec 검증**

`tests/test_analysis_spec.py`:

```python
from __future__ import annotations

import unittest

from bia.analysis.errors import SpecError
from bia.analysis.spec import DomainSpec, MetricSpec


class MetricSpecTests(unittest.TestCase):
    def test_additive_requires_a_value_column(self):
        with self.assertRaises(SpecError):
            MetricSpec(name="revenue", kind="additive")

    def test_ratio_requires_both_numerator_and_denominator(self):
        with self.assertRaises(SpecError):
            MetricSpec(name="cr", kind="ratio", numerator="orders")

    def test_additive_rejects_ratio_columns(self):
        with self.assertRaises(SpecError):
            MetricSpec(name="revenue", kind="additive", value="v", numerator="orders")

    def test_unknown_kind_is_rejected(self):
        with self.assertRaises(SpecError):
            MetricSpec(name="x", kind="cumulative", value="v")

    def test_valid_specs_construct(self):
        additive = MetricSpec(name="revenue", kind="additive", value="revenue_krw")
        ratio = MetricSpec(
            name="conversion_rate", kind="ratio",
            numerator="orders", denominator="sessions",
            numerator_bounded_by_denominator=True,
        )
        self.assertEqual(additive.kind, "additive")
        self.assertTrue(ratio.numerator_bounded_by_denominator)


class DomainSpecTests(unittest.TestCase):
    def _metric(self):
        return MetricSpec(name="m", kind="additive", value="count")

    def test_dimensions_must_be_a_subset_of_grain(self):
        with self.assertRaises(SpecError):
            DomainSpec(name="d", grain=("day", "a"), dimensions=("a", "b"),
                       metrics={"m": self._metric()})

    def test_grain_must_start_with_day(self):
        with self.assertRaises(SpecError):
            DomainSpec(name="d", grain=("a", "day"), dimensions=("a",),
                       metrics={"m": self._metric()})

    def test_metric_key_must_match_its_name(self):
        with self.assertRaises(SpecError):
            DomainSpec(name="d", grain=("day", "a"), dimensions=("a",),
                       metrics={"wrong": self._metric()})

    def test_valid_domain_constructs(self):
        spec = DomainSpec(name="d", grain=("day", "a"), dimensions=("a",),
                          metrics={"m": self._metric()})
        self.assertEqual(spec.dimensions, ("a",))
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python3 -m unittest tests.test_analysis_spec -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bia.analysis'`

- [ ] **Step 3: errors.py 와 spec.py 를 쓴다**

`bia/analysis/__init__.py`: 빈 파일.

`bia/analysis/errors.py`:

```python
"""분석 계약 위반. 모두 호출자의 잘못이지 실행 중 오류가 아니다."""
from __future__ import annotations


class SpecError(ValueError):
    """도메인·metric 선언 자체가 잘못됐다."""


class RequestError(ValueError):
    """선언은 옳으나 이번 요청이 그 계약을 벗어났다."""


class AnalysisRefused(RuntimeError):
    """데이터가 이 분석을 지탱하지 못한다. 숫자를 내지 않는다."""

    def __init__(self, stage: str, reason: str) -> None:
        super().__init__("%s: %s" % (stage, reason))
        self.stage = stage
        self.reason = reason
```

`bia/analysis/spec.py`:

```python
"""도메인이 코드에 선언하는 정적 계약. 런타임 요청과 다른 타입이다."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

from .errors import SpecError

KIND_ADDITIVE = "additive"
KIND_RATIO = "ratio"
KINDS = (KIND_ADDITIVE, KIND_RATIO)


@dataclass(frozen=True)
class MetricSpec:
    name: str
    kind: str
    value: Optional[str] = None
    numerator: Optional[str] = None
    denominator: Optional[str] = None
    numerator_bounded_by_denominator: bool = False

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise SpecError("metric %r: kind must be one of %s" % (self.name, KINDS))
        if self.kind == KIND_ADDITIVE:
            if not self.value:
                raise SpecError("metric %r: additive needs `value`" % self.name)
            if self.numerator or self.denominator:
                raise SpecError("metric %r: additive must not carry ratio columns" % self.name)
        else:
            if not (self.numerator and self.denominator):
                raise SpecError(
                    "metric %r: ratio needs both `numerator` and `denominator`" % self.name
                )
            if self.value:
                raise SpecError("metric %r: ratio must not carry `value`" % self.name)

    @property
    def columns(self) -> Tuple[str, ...]:
        if self.kind == KIND_ADDITIVE:
            return (self.value,)
        return (self.numerator, self.denominator)


@dataclass(frozen=True)
class DomainSpec:
    """`grain` 은 한 행의 물리적 최소 단위이고 `dimensions` 는 분석 축이다.
    둘은 같은 목록일 수 있으나 용도가 다르다 — 중복 검사는 언제나 grain 기준이다."""

    name: str
    grain: Tuple[str, ...]
    dimensions: Tuple[str, ...]
    metrics: Dict[str, MetricSpec] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.grain or self.grain[0] != "day":
            raise SpecError("domain %r: grain must start with 'day'" % self.name)
        extra = [d for d in self.dimensions if d not in self.grain]
        if extra:
            raise SpecError("domain %r: dimensions not in grain: %s" % (self.name, extra))
        for key, metric in self.metrics.items():
            if key != metric.name:
                raise SpecError(
                    "domain %r: metric key %r does not match its name %r"
                    % (self.name, key, metric.name)
                )

    @property
    def grain_dimensions(self) -> Tuple[str, ...]:
        return tuple(d for d in self.grain if d != "day")
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python3 -m unittest tests.test_analysis_spec -v`
Expected: PASS (9 tests)

- [ ] **Step 5: 실패하는 테스트를 쓴다 — Compiler**

`tests/test_analysis_compiler.py`:

```python
from __future__ import annotations

import unittest

from bia.analysis.compiler import compile_request
from bia.analysis.errors import RequestError
from bia.analysis.registry import get_domain, register
from bia.analysis.request import AnalysisRequest, PeriodComparison
from bia.analysis.spec import DomainSpec, MetricSpec
from bia.types import Period

DOMAIN = DomainSpec(
    name="_compilertest",
    grain=("day", "channel", "device"),
    dimensions=("channel", "device"),
    metrics={
        "revenue": MetricSpec(name="revenue", kind="additive", value="revenue_krw"),
        "conversion_rate": MetricSpec(
            name="conversion_rate", kind="ratio",
            numerator="orders", denominator="sessions",
            numerator_bounded_by_denominator=True,
        ),
    },
)
register(DOMAIN)

COMPARISON = PeriodComparison(
    current=Period.of("2026-07-01", "2026-07-30"),
    baseline=Period.of("2026-06-01", "2026-06-30"),
)


def request(**overrides) -> AnalysisRequest:
    base = dict(domain="_compilertest", metric="revenue",
                breakdowns=("channel",), comparison=COMPARISON,
                rank_by="net_contribution")
    base.update(overrides)
    return AnalysisRequest(**base)


class CompilerValidationTests(unittest.TestCase):
    def test_unknown_domain_is_rejected(self):
        with self.assertRaises(RequestError):
            compile_request(request(domain="nope"))

    def test_unknown_metric_is_rejected(self):
        with self.assertRaises(RequestError):
            compile_request(request(metric="nope"))

    def test_breakdown_must_be_a_declared_dimension(self):
        with self.assertRaises(RequestError):
            compile_request(request(breakdowns=("category",)))

    def test_duplicate_breakdowns_are_rejected(self):
        with self.assertRaises(RequestError):
            compile_request(request(breakdowns=("channel", "channel")))

    def test_empty_breakdowns_are_rejected(self):
        with self.assertRaises(RequestError):
            compile_request(request(breakdowns=()))

    def test_rate_effect_rank_is_rejected_for_an_additive_metric(self):
        with self.assertRaises(RequestError):
            compile_request(request(rank_by="rate_effect"))

    def test_group_delta_rank_is_rejected_for_a_ratio_metric(self):
        with self.assertRaises(RequestError):
            compile_request(request(metric="conversion_rate", rank_by="group_delta"))

    def test_current_must_start_after_baseline_ends(self):
        flipped = PeriodComparison(
            current=Period.of("2026-06-01", "2026-06-30"),
            baseline=Period.of("2026-07-01", "2026-07-30"),
        )
        with self.assertRaises(RequestError):
            compile_request(request(comparison=flipped))


class PlanShapeTests(unittest.TestCase):
    def test_one_dimension_yields_overall_and_one_marginal(self):
        plan = compile_request(request(breakdowns=("channel",)))
        self.assertEqual(
            [(b.dimensions, b.cross) for b in plan.branches],
            [((), False), (("channel",), False)],
        )

    def test_two_dimensions_yield_marginals_plus_one_joint(self):
        plan = compile_request(request(breakdowns=("channel", "device")))
        self.assertEqual(
            [(b.dimensions, b.cross) for b in plan.branches],
            [((), False), (("channel",), False), (("device",), False),
             (("channel", "device"), True)],
        )

    def test_no_intermediate_combinations_are_generated(self):
        """3 축이면 marginal 3 + joint 1 이고, 중간 조합은 만들지 않는다."""
        wide = DomainSpec(
            name="_wide", grain=("day", "a", "b", "c"), dimensions=("a", "b", "c"),
            metrics={"m": MetricSpec(name="m", kind="additive", value="v")},
        )
        register(wide)
        plan = compile_request(
            AnalysisRequest(domain="_wide", metric="m", breakdowns=("a", "b", "c"),
                            comparison=COMPARISON, rank_by="net_contribution")
        )
        shapes = [b.dimensions for b in plan.branches]
        self.assertEqual(shapes, [(), ("a",), ("b",), ("c",), ("a", "b", "c")])

    def test_the_domain_and_metric_travel_with_the_plan(self):
        plan = compile_request(request())
        self.assertIs(plan.domain, get_domain("_compilertest"))
        self.assertEqual(plan.metric.name, "revenue")
```

- [ ] **Step 6: 실패를 확인한다**

Run: `python3 -m unittest tests.test_analysis_compiler -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bia.analysis.compiler'`

- [ ] **Step 7: request.py, registry.py, compiler.py 를 쓴다**

`bia/analysis/request.py`:

```python
"""이번 실행에서 무엇을 분석할 것인가. 정적 선언과 별개 타입이다."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from ..types import Period

RANK_NET_CONTRIBUTION = "net_contribution"
RANK_GROUP_DELTA = "group_delta"
RANK_RATE_EFFECT = "rate_effect"
RANK_MIX_EFFECT = "mix_effect"

RANK_BY_KIND = {
    "additive": (RANK_NET_CONTRIBUTION, RANK_GROUP_DELTA),
    "ratio": (RANK_NET_CONTRIBUTION, RANK_RATE_EFFECT, RANK_MIX_EFFECT),
}


@dataclass(frozen=True)
class PeriodComparison:
    current: Period
    baseline: Period


@dataclass(frozen=True)
class AnalysisRequest:
    domain: str
    metric: str
    breakdowns: Tuple[str, ...]
    comparison: PeriodComparison
    rank_by: str = RANK_NET_CONTRIBUTION
```

`bia/analysis/registry.py`:

```python
"""선언된 도메인의 닫힌 목록. 런타임에 도메인을 발명할 수 없다."""
from __future__ import annotations

from typing import Dict, List

from .errors import RequestError, SpecError
from .spec import DomainSpec

_DOMAINS: Dict[str, DomainSpec] = {}


def register(spec: DomainSpec) -> None:
    existing = _DOMAINS.get(spec.name)
    if existing is not None and existing != spec:
        raise SpecError("domain %r is already registered with a different spec" % spec.name)
    _DOMAINS[spec.name] = spec


def get_domain(name: str) -> DomainSpec:
    try:
        return _DOMAINS[name]
    except KeyError:
        raise RequestError(
            "unknown domain %r; registered: %s" % (name, sorted(_DOMAINS))
        )


def registered_names() -> List[str]:
    return sorted(_DOMAINS)
```

`bia/analysis/compiler.py`:

```python
"""AnalysisRequest + DomainSpec -> ExecutionPlan.

호출자도 모델도 연산자를 고르지 않는다. 가능한 plan 모양은 여기 고정돼 있고
Compiler 가 metric kind 와 요청으로 그중 하나를 결정론적으로 만든다. planner 가 아니다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from .errors import RequestError
from .registry import get_domain
from .request import RANK_BY_KIND, AnalysisRequest, PeriodComparison
from .spec import DomainSpec, MetricSpec


@dataclass(frozen=True)
class PlanBranch:
    """`dimensions` 가 비면 overall. `cross` 는 전체 joint 분기 하나에만 참이다."""

    dimensions: Tuple[str, ...]
    cross: bool


@dataclass(frozen=True)
class ExecutionPlan:
    domain: DomainSpec
    metric: MetricSpec
    comparison: PeriodComparison
    branches: Tuple[PlanBranch, ...]
    rank_by: str


def compile_request(request: AnalysisRequest) -> ExecutionPlan:
    domain = get_domain(request.domain)

    metric = domain.metrics.get(request.metric)
    if metric is None:
        raise RequestError(
            "domain %r has no metric %r; declared: %s"
            % (domain.name, request.metric, sorted(domain.metrics))
        )

    if not request.breakdowns:
        raise RequestError("at least one breakdown dimension is required")
    if len(set(request.breakdowns)) != len(request.breakdowns):
        raise RequestError("duplicate breakdown dimensions: %s" % (request.breakdowns,))
    unknown = [d for d in request.breakdowns if d not in domain.dimensions]
    if unknown:
        raise RequestError(
            "domain %r does not declare dimension(s) %s; declared: %s"
            % (domain.name, unknown, list(domain.dimensions))
        )

    allowed = RANK_BY_KIND[metric.kind]
    if request.rank_by not in allowed:
        raise RequestError(
            "rank_by=%r is not meaningful for a %s metric; allowed: %s"
            % (request.rank_by, metric.kind, list(allowed))
        )

    comparison = request.comparison
    if comparison.current.start <= comparison.baseline.end:
        raise RequestError("current period must start after the baseline period ends")

    branches = [PlanBranch(dimensions=(), cross=False)]
    for dimension in request.breakdowns:
        branches.append(PlanBranch(dimensions=(dimension,), cross=False))
    if len(request.breakdowns) > 1:
        branches.append(PlanBranch(dimensions=tuple(request.breakdowns), cross=True))

    return ExecutionPlan(
        domain=domain, metric=metric, comparison=comparison,
        branches=tuple(branches), rank_by=request.rank_by,
    )
```

- [ ] **Step 8: 통과를 확인한다**

Run: `python3 -m unittest tests.test_analysis_compiler -v`
Expected: PASS (12 tests)

- [ ] **Step 9: 전체 스위트와 v0 불변을 확인한다**

```bash
python3 -m unittest discover -t . -s tests -q
/opt/homebrew/bin/python3.11 -m unittest discover -t . -s tests -q
python3 eval/run_eval.py >/dev/null && git diff --quiet -- eval/results/heuristic.json && echo "v0 불변"
```
Expected: 전부 OK, `v0 불변` 출력

- [ ] **Step 10: 커밋**

```bash
git add bia/analysis tests/test_analysis_spec.py tests/test_analysis_compiler.py
git commit -m "feat(analysis): 타입·레지스트리·Compiler 계약

- DomainSpec/MetricSpec 은 선언만. grain(물리 행 단위)과 dimensions(분석 축)를 분리
- AnalysisRequest 는 런타임 요청으로 정적 선언과 별개 타입
- Compiler 가 연산자를 결정한다. 호출자는 고를 수 없다
- BREAKDOWN 은 marginal N개 + joint 1개. 중간 조합 없음
- rank_by 를 metric kind 로 검증"
```

---

### Task 2: Observation 프레임과 grain 기준 integrity

`bia/integrity.py` 를 grain 인지형으로 넓히되 **v1 동작은 한 치도 바뀌지 않는다.** 기존 `MetricRow` 경로는 새 경로에 위임한다(죽은 코드 없음).

**Files:**
- Create: `bia/analysis/frame.py`
- Modify: `bia/integrity.py`
- Test: `tests/test_analysis_frame.py`, `tests/test_integrity_grain.py`

**Interfaces:**
- Consumes: `bia.analysis.spec.DomainSpec` (Task 1)
- Produces:
  - `Observation(day: datetime.date, keys: Tuple[Tuple[str, str], ...], measures: Tuple[Tuple[str, int], ...])` — `keys`/`measures` 는 정렬된 튜플쌍이라 해시 가능하다
  - `observation_key(obs: Observation, grain: Tuple[str, ...]) -> Tuple[str, ...]`
  - `load_observations(path: str, spec: DomainSpec, measure_columns: Sequence[str]) -> List[Observation]`
  - `bia.integrity.dedupe_observations(rows, grain) -> Tuple[List[Observation], int, List[str]]`
  - `bia.integrity.inspect_period_observations(rows, period, grain) -> Tuple[List[Observation], PeriodIntegrity]`
  - 기존 `dedupe_rows` / `inspect_period` / `decide_comparability` 의 시그니처와 반환은 그대로다

- [ ] **Step 1: 실패하는 테스트를 쓴다 — Observation 과 로딩**

`tests/test_analysis_frame.py`:

```python
from __future__ import annotations

import datetime as dt
import os
import tempfile
import unittest

from bia.analysis.errors import SpecError
from bia.analysis.frame import Observation, load_observations, observation_key
from bia.analysis.spec import DomainSpec, MetricSpec

DOMAIN = DomainSpec(
    name="_frametest",
    grain=("day", "channel", "device"),
    dimensions=("channel", "device"),
    metrics={"orders": MetricSpec(name="orders", kind="additive", value="orders")},
)


class ObservationTests(unittest.TestCase):
    def test_keys_are_hashable_and_ordered(self):
        obs = Observation(
            day=dt.date(2026, 7, 1),
            keys=(("channel", "paid"), ("device", "mobile")),
            measures=(("orders", 5),),
        )
        self.assertEqual(observation_key(obs, ("day", "channel", "device")),
                         ("2026-07-01", "paid", "mobile"))
        self.assertIsInstance(hash(obs), int)

    def test_key_follows_the_requested_grain_order(self):
        obs = Observation(
            day=dt.date(2026, 7, 1),
            keys=(("channel", "paid"), ("device", "mobile")),
            measures=(("orders", 5),),
        )
        self.assertEqual(observation_key(obs, ("day", "device")), ("2026-07-01", "mobile"))


class LoadTests(unittest.TestCase):
    def _write(self, text):
        handle = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False,
                                             encoding="utf-8", newline="")
        handle.write(text)
        handle.close()
        return handle.name

    def test_loads_rows_into_observations(self):
        path = self._write(
            "day,channel,device,orders\n2026-07-01,paid,mobile,5\n2026-07-01,paid,desktop,3\n"
        )
        rows = load_observations(path, DOMAIN, ("orders",))
        self.assertEqual(len(rows), 2)
        self.assertEqual(dict(rows[0].measures), {"orders": 5})
        os.unlink(path)

    def test_a_missing_grain_column_is_rejected(self):
        path = self._write("day,channel,orders\n2026-07-01,paid,5\n")
        with self.assertRaises(SpecError):
            load_observations(path, DOMAIN, ("orders",))
        os.unlink(path)

    def test_a_missing_measure_column_is_rejected(self):
        path = self._write("day,channel,device\n2026-07-01,paid,mobile\n")
        with self.assertRaises(SpecError):
            load_observations(path, DOMAIN, ("orders",))
        os.unlink(path)

    def test_a_null_dimension_becomes_the_unknown_bucket(self):
        """drop 하면 partition 이 깨져 가법성 불변식이 무의미해진다."""
        path = self._write("day,channel,device,orders\n2026-07-01,,mobile,5\n")
        rows = load_observations(path, DOMAIN, ("orders",))
        self.assertEqual(dict(rows[0].keys)["channel"], "__UNKNOWN__")
        os.unlink(path)

    def test_a_negative_measure_is_rejected(self):
        path = self._write("day,channel,device,orders\n2026-07-01,paid,mobile,-1\n")
        with self.assertRaises(SpecError):
            load_observations(path, DOMAIN, ("orders",))
        os.unlink(path)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python3 -m unittest tests.test_analysis_frame -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bia.analysis.frame'`

- [ ] **Step 3: frame.py 를 쓴다**

```python
"""측정값 로딩과 정규화. 도메인 이름을 알지 못한다."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

from ..types import parse_day
from .errors import SpecError
from .spec import DomainSpec

UNKNOWN = "__UNKNOWN__"


@dataclass(frozen=True)
class Observation:
    """한 grain 행. `keys` 와 `measures` 는 정렬된 튜플쌍이라 해시 가능하다."""

    day: object
    keys: Tuple[Tuple[str, str], ...]
    measures: Tuple[Tuple[str, int], ...]

    def key_of(self, dimension: str) -> str:
        for name, value in self.keys:
            if name == dimension:
                return value
        raise KeyError(dimension)

    def measure_of(self, column: str) -> int:
        for name, value in self.measures:
            if name == column:
                return value
        raise KeyError(column)


def observation_key(obs: Observation, grain: Sequence[str]) -> Tuple[str, ...]:
    parts = []
    for name in grain:
        parts.append(obs.day.isoformat() if name == "day" else obs.key_of(name))
    return tuple(parts)


def load_observations(
    path: str, spec: DomainSpec, measure_columns: Sequence[str]
) -> List[Observation]:
    dimensions = spec.grain_dimensions
    out: List[Observation] = []
    with open(path, "r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        header = reader.fieldnames or []
        for column in ("day",) + tuple(dimensions) + tuple(measure_columns):
            if column not in header:
                raise SpecError(
                    "%s: column %r is required by domain %r but the header is %s"
                    % (path, column, spec.name, header)
                )
        for line_no, record in enumerate(reader, start=2):
            keys = []
            for dimension in dimensions:
                raw = (record.get(dimension) or "").strip()
                keys.append((dimension, raw if raw else UNKNOWN))
            measures = []
            for column in measure_columns:
                raw = (record.get(column) or "").strip()
                try:
                    number = int(raw)
                except ValueError:
                    raise SpecError(
                        "%s line %d: %r must be an integer, got %r"
                        % (path, line_no, column, raw)
                    )
                if number < 0:
                    raise SpecError(
                        "%s line %d: %r must not be negative, got %d"
                        % (path, line_no, column, number)
                    )
                measures.append((column, number))
            out.append(
                Observation(
                    day=parse_day(record["day"]),
                    keys=tuple(sorted(keys)),
                    measures=tuple(sorted(measures)),
                )
            )
    return out
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python3 -m unittest tests.test_analysis_frame -v`
Expected: PASS (7 tests)

- [ ] **Step 5: 실패하는 테스트를 쓴다 — grain 기준 중복 검사**

`tests/test_integrity_grain.py`:

```python
from __future__ import annotations

import datetime as dt
import unittest

from bia.analysis.frame import Observation
from bia.integrity import dedupe_observations

GRAIN = ("day", "channel", "device")


def obs(channel, device, orders, day=dt.date(2026, 7, 1)):
    return Observation(day=day, keys=(("channel", channel), ("device", device)),
                       measures=(("orders", orders),))


class GrainDuplicateTests(unittest.TestCase):
    def test_finer_grain_rows_are_not_duplicates(self):
        """요청이 channel 만 breakdown 해도 중복 판정은 물리 grain 으로 한다.
        이 구분이 없으면 device 별 정상 행들이 전부 중복으로 터진다."""
        rows = [obs("paid", "mobile", 5), obs("paid", "desktop", 3)]
        clean, duplicates, conflicts = dedupe_observations(rows, GRAIN)
        self.assertEqual(len(clean), 2)
        self.assertEqual(duplicates, 0)
        self.assertEqual(conflicts, [])

    def test_exact_duplicates_at_grain_are_collapsed(self):
        rows = [obs("paid", "mobile", 5), obs("paid", "mobile", 5)]
        clean, duplicates, conflicts = dedupe_observations(rows, GRAIN)
        self.assertEqual(len(clean), 1)
        self.assertEqual(duplicates, 1)
        self.assertEqual(conflicts, [])

    def test_conflicting_duplicates_are_reported_not_resolved(self):
        rows = [obs("paid", "mobile", 5), obs("paid", "mobile", 9)]
        clean, duplicates, conflicts = dedupe_observations(rows, GRAIN)
        self.assertEqual(duplicates, 0)
        self.assertEqual(len(conflicts), 1)
        self.assertIn("paid", conflicts[0])

    def test_output_order_is_stable(self):
        rows = [obs("z", "mobile", 1), obs("a", "desktop", 2)]
        first, _, _ = dedupe_observations(rows, GRAIN)
        second, _, _ = dedupe_observations(list(reversed(rows)), GRAIN)
        self.assertEqual([observation_keys(r) for r in first],
                         [observation_keys(r) for r in second])


def observation_keys(row):
    return (row.day.isoformat(), dict(row.keys))
```

- [ ] **Step 6: 실패를 확인한다**

Run: `python3 -m unittest tests.test_integrity_grain -v`
Expected: FAIL — `ImportError: cannot import name 'dedupe_observations'`

- [ ] **Step 7: integrity.py 에 grain 경로를 더하고 기존 함수를 위임으로 바꾼다**

`bia/integrity.py` 상단 import 에 추가:

```python
from .analysis.frame import Observation, observation_key
```

`dedupe_rows` 바로 위에 추가:

```python
V1_GRAIN = ("day", "product", "complaint_type")


def dedupe_observations(
    rows: Sequence[Observation], grain: Sequence[str]
) -> Tuple[List[Observation], int, List[str]]:
    """중복은 언제나 **물리 grain** 으로 판정한다. 분석이 요청한 breakdown 이 아니다.

    데이터 grain 이 day×channel×device 인데 breakdown 이 [channel] 뿐일 때 요청 기준으로
    검사하면 device 별 정상 행이 전부 중복으로 잘못 판정된다.
    """
    seen: Dict[Tuple[str, ...], Observation] = {}
    duplicates = 0
    conflicts: List[str] = []
    for row in rows:
        key = observation_key(row, grain)
        prior = seen.get(key)
        if prior is None:
            seen[key] = row
            continue
        if prior.measures == row.measures:
            duplicates += 1
        else:
            label = "|".join(key)
            if label not in conflicts:
                conflicts.append(label)
    clean = [seen[k] for k in sorted(seen.keys())]
    return clean, duplicates, sorted(conflicts)
```

그리고 기존 `dedupe_rows` 본문을 위임으로 교체한다 (동작 동일, 구현 하나):

```python
def _row_to_observation(row: MetricRow) -> Observation:
    return Observation(
        day=row.day,
        keys=(("complaint_type", row.complaint_type), ("product", row.product)),
        measures=(("count", row.count),),
    )


def _observation_to_row(obs: Observation) -> MetricRow:
    keys = dict(obs.keys)
    return MetricRow(day=obs.day, product=keys["product"],
                     complaint_type=keys["complaint_type"],
                     count=dict(obs.measures)["count"])


def dedupe_rows(rows: List[MetricRow]) -> Tuple[List[MetricRow], int, List[str]]:
    """v1 표면. 판정은 grain 경로에 위임한다 — 구현이 둘이면 갈라진다."""
    observations = [_row_to_observation(r) for r in rows]
    clean, duplicates, conflicts = dedupe_observations(observations, V1_GRAIN)
    return [_observation_to_row(o) for o in clean], duplicates, conflicts
```

- [ ] **Step 8: 통과와 v1 불변을 확인한다**

```bash
python3 -m unittest tests.test_integrity_grain tests.test_integrity -v
```
Expected: 신규 4 PASS, 기존 `test_integrity` 전부 PASS (정렬 순서·충돌키 문자열 형식이 그대로여야 한다)

- [ ] **Step 9: 전체 스위트와 v0·v1 불변을 확인한다**

```bash
python3 -m unittest discover -t . -s tests -q
/opt/homebrew/bin/python3.11 -m unittest discover -t . -s tests -q
python3 eval/run_eval.py >/dev/null
python3 -m eval.v1.run_eval --selector heuristic >/dev/null 2>&1
python3 -m eval.v1.run_eval --selector greedy >/dev/null 2>&1
git status --porcelain -- eval/results eval/v1/results
```
Expected: 테스트 전부 OK, 마지막 `git status` 출력이 **비어 있어야 한다**

- [ ] **Step 10: 커밋**

```bash
git add bia/analysis/frame.py bia/integrity.py tests/test_analysis_frame.py tests/test_integrity_grain.py
git commit -m "feat(analysis): Observation 프레임과 grain 기준 중복 검사

- 중복 판정을 물리 grain 기준으로. 요청 breakdown 기준으로 하면
  데이터 grain 이 더 잘게 나뉜 경우 정상 행이 중복으로 터진다
- dedupe_rows 는 삭제하지 않고 grain 경로에 위임. 구현이 둘이면 갈라진다
- NULL dimension 은 drop 하지 않고 __UNKNOWN__ 으로. partition 이 깨지면
  가법성 불변식이 무의미해진다
- v0/v1 결과 파일 바이트 불변 확인"
```

---

### Task 3: additive 엔진 + complaints 선언 + adapter — **Gate 1**

이 Task 가 v2 의 첫 관문이다. **v1 semantic regression 0 을 통과하지 못하면 Task 4 로 가지 않는다.**

**Files:**
- Create: `bia/analysis/result.py`, `bia/analysis/operators.py`, `bia/analysis/engine.py`, `bia/domains/__init__.py`, `bia/domains/complaints.py`, `bia/adapters/__init__.py`, `bia/adapters/complaints.py`
- Test: `tests/test_analysis_additive.py`, `tests/test_v1_regression.py`

**Interfaces:**
- Consumes: `ExecutionPlan` (Task 1), `Observation` / `dedupe_observations` (Task 2), `bia.integrity.decide_comparability`, `bia.evidence.EvidenceState`, `bia.controller.investigate`
- Produces:
  - `GroupResult(key: Dict[str, str], group_delta: Optional[int], net_contribution: float, rate_effect: Optional[float], mix_effect: Optional[float], comparable: bool)`
  - `BreakdownResult(dimensions: Tuple[str, ...], cross: bool, groups: List[GroupResult], totals: Dict[str, float], ranking: Dict[str, object], flags: Dict[str, bool], non_comparable_groups: List[Dict[str, str]], status: str, reason: Optional[str])`
  - `StructuredAnalysisResult(metric: Dict[str, str], comparison: Dict[str, object], breakdowns: List[BreakdownResult])`
  - `run_plan(plan: ExecutionPlan, rows: Sequence[Observation]) -> StructuredAnalysisResult`
  - `bia.domains.complaints.SPEC: DomainSpec`
  - `bia.adapters.complaints.top_contributor_cells(result: StructuredAnalysisResult) -> List[Tuple[str, str]]`

- [ ] **Step 1: 실패하는 테스트를 쓴다 — additive 산술**

`tests/test_analysis_additive.py`:

```python
from __future__ import annotations

import datetime as dt
import unittest

from bia.analysis.compiler import compile_request
from bia.analysis.engine import run_plan
from bia.analysis.frame import Observation
from bia.analysis.registry import register
from bia.analysis.request import AnalysisRequest, PeriodComparison
from bia.analysis.spec import DomainSpec, MetricSpec
from bia.types import Period

DOMAIN = DomainSpec(
    name="_additivetest",
    grain=("day", "channel"),
    dimensions=("channel",),
    metrics={"orders": MetricSpec(name="orders", kind="additive", value="orders")},
)
register(DOMAIN)

BASE = Period.of("2026-06-01", "2026-06-02")
CUR = Period.of("2026-07-01", "2026-07-02")


def obs(day, channel, orders):
    return Observation(day=day, keys=(("channel", channel),),
                       measures=(("orders", orders),))


def rows():
    out = []
    for day in BASE.dates():
        out.append(obs(day, "paid", 10))
        out.append(obs(day, "organic", 5))
    for day in CUR.dates():
        out.append(obs(day, "paid", 20))
        out.append(obs(day, "organic", 5))
    return out


def plan():
    return compile_request(AnalysisRequest(
        domain="_additivetest", metric="orders", breakdowns=("channel",),
        comparison=PeriodComparison(current=CUR, baseline=BASE),
        rank_by="net_contribution",
    ))


class AdditiveArithmeticTests(unittest.TestCase):
    def setUp(self):
        self.result = run_plan(plan(), rows())

    def test_totals_and_delta(self):
        """baseline 30, current 50 을 손으로 계산해 둔다."""
        self.assertEqual(self.result.comparison["baseline"], 30)
        self.assertEqual(self.result.comparison["current"], 50)
        self.assertEqual(self.result.comparison["delta"], 20)

    def test_relative_change_is_present_when_baseline_is_non_zero(self):
        self.assertAlmostEqual(self.result.comparison["relative_change"], 20 / 30.0)

    def test_group_deltas_sum_to_the_total_delta(self):
        breakdown = self.result.breakdowns[1]
        self.assertEqual(sum(g.group_delta for g in breakdown.groups), 20)

    def test_net_contribution_equals_group_delta_for_additive(self):
        breakdown = self.result.breakdowns[1]
        for group in breakdown.groups:
            self.assertEqual(group.net_contribution, group.group_delta)

    def test_additive_groups_carry_no_rate_or_mix(self):
        breakdown = self.result.breakdowns[1]
        for group in breakdown.groups:
            self.assertIsNone(group.rate_effect)
            self.assertIsNone(group.mix_effect)

    def test_ranking_is_breakdown_local(self):
        breakdown = self.result.breakdowns[1]
        self.assertEqual(breakdown.ranking["by"], "net_contribution")
        self.assertEqual(breakdown.ranking["groups"][0], {"channel": "paid"})


class ZeroBaselineTests(unittest.TestCase):
    def test_relative_change_is_none_when_baseline_is_zero(self):
        data = [obs(day, "paid", 0) for day in BASE.dates()]
        data += [obs(day, "paid", 7) for day in CUR.dates()]
        result = run_plan(plan(), data)
        self.assertEqual(result.comparison["delta"], 14)
        self.assertIsNone(result.comparison["relative_change"])
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python3 -m unittest tests.test_analysis_additive -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bia.analysis.engine'`

- [ ] **Step 3: result.py, operators.py, engine.py 의 additive 경로를 쓴다**

`bia/analysis/result.py`:

```python
"""공용 산출물. answer 가 아니라 typed analytical result 다."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

STATUS_OK = "ok"
STATUS_OMITTED = "omitted"


@dataclass
class GroupResult:
    key: Dict[str, str]
    net_contribution: float
    comparable: bool = True
    group_delta: Optional[int] = None
    rate_effect: Optional[float] = None
    mix_effect: Optional[float] = None
    contribution_share: Optional[float] = None

    def as_dict(self) -> Dict[str, object]:
        out: Dict[str, object] = {"key": dict(self.key),
                                  "net_contribution": self.net_contribution,
                                  "comparable": self.comparable}
        if self.group_delta is not None:
            out["group_delta"] = self.group_delta
        if self.rate_effect is not None:
            out["rate_effect"] = self.rate_effect
        if self.mix_effect is not None:
            out["mix_effect"] = self.mix_effect
        if self.contribution_share is not None:
            out["contribution_share"] = self.contribution_share
        return out


@dataclass
class BreakdownResult:
    dimensions: Tuple[str, ...]
    cross: bool
    groups: List[GroupResult] = field(default_factory=list)
    totals: Dict[str, float] = field(default_factory=dict)
    ranking: Dict[str, object] = field(default_factory=dict)
    flags: Dict[str, bool] = field(default_factory=dict)
    non_comparable_groups: List[Dict[str, str]] = field(default_factory=list)
    status: str = STATUS_OK
    reason: Optional[str] = None
    observed_cells: Optional[int] = None

    def as_dict(self) -> Dict[str, object]:
        out: Dict[str, object] = {
            "dimensions": list(self.dimensions), "cross": self.cross,
            "status": self.status,
        }
        if self.status == STATUS_OMITTED:
            out["reason"] = self.reason
            out["observed_cells"] = self.observed_cells
            return out
        out["groups"] = [g.as_dict() for g in self.groups]
        out["totals"] = dict(self.totals)
        out["ranking"] = dict(self.ranking)
        out["flags"] = dict(self.flags)
        out["non_comparable_groups"] = [dict(k) for k in self.non_comparable_groups]
        return out


@dataclass
class StructuredAnalysisResult:
    metric: Dict[str, str]
    comparison: Dict[str, object]
    breakdowns: List[BreakdownResult] = field(default_factory=list)

    def as_dict(self) -> Dict[str, object]:
        return {"metric": dict(self.metric), "comparison": dict(self.comparison),
                "breakdowns": [b.as_dict() for b in self.breakdowns]}
```

`bia/analysis/operators.py`:

```python
"""여섯 개로 닫힌 연산자. 일곱 번째가 필요하면 그것은 v2 확장이 아니다."""
from __future__ import annotations

from typing import Dict, Sequence, Tuple

from ..types import Period
from .frame import Observation

CROSS_CELL_LIMIT = 1000


def aggregate(
    rows: Sequence[Observation], window: Period, columns: Sequence[str],
    dimensions: Sequence[str],
) -> Dict[Tuple[str, ...], Dict[str, int]]:
    """AGGREGATE. `dimensions` 가 비면 전체 하나로 모은다."""
    out: Dict[Tuple[str, ...], Dict[str, int]] = {}
    for row in rows:
        if not window.contains(row.day):
            continue
        key = tuple(row.key_of(d) for d in dimensions)
        bucket = out.setdefault(key, {c: 0 for c in columns})
        for column in columns:
            bucket[column] += row.measure_of(column)
    return out


def compare(current: int, baseline: int) -> Dict[str, object]:
    """COMPARE. `relative_change` 는 nullable 파생값이다."""
    delta = current - baseline
    relative = (delta / float(baseline)) if baseline else None
    return {"current": current, "baseline": baseline,
            "delta": delta, "relative_change": relative}


def group_universe(
    current: Dict[Tuple[str, ...], Dict[str, int]],
    baseline: Dict[Tuple[str, ...], Dict[str, int]],
) -> Tuple[Tuple[str, ...], ...]:
    """BREAKDOWN 의 그룹 우주. 두 기간 키의 **합집합**이다.

    한 기간만 보면 실제 분해 대상 수를 과소평가한다. 한쪽에 없는 그룹은
    '유효 기간 안의 부재 = 활동 0' 으로 해석한다.
    """
    return tuple(sorted(set(current) | set(baseline)))


def rank(groups, by: str) -> Dict[str, object]:
    """RANK. 한 breakdown 안에서만 수행한다. 서로 다른 breakdown 을 섞지 않는다."""
    def key(group):
        value = getattr(group, by, None)
        return (0 if value is None else -value, sorted(group.key.items()))

    ordered = sorted(groups, key=key)
    return {"by": by, "groups": [dict(g.key) for g in ordered]}
```

`bia/analysis/engine.py` (additive 경로만; ratio 는 Task 4):

```python
"""plan 실행. 도메인 이름을 알지 못한다."""
from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

from ..integrity import dedupe_observations
from .compiler import ExecutionPlan, PlanBranch
from .errors import AnalysisRefused
from .frame import Observation
from .operators import CROSS_CELL_LIMIT, aggregate, compare, group_universe, rank
from .result import (STATUS_OMITTED, BreakdownResult, GroupResult,
                     StructuredAnalysisResult)
from .spec import KIND_ADDITIVE


def run_plan(plan: ExecutionPlan, rows: Sequence[Observation]) -> StructuredAnalysisResult:
    clean, _, conflicts = dedupe_observations(rows, plan.domain.grain)
    if conflicts:
        raise AnalysisRefused(
            "integrity",
            "conflicting duplicate rows at grain %s: %s" % (list(plan.domain.grain), conflicts),
        )

    columns = plan.metric.columns
    current_all = aggregate(clean, plan.comparison.current, columns, ())
    baseline_all = aggregate(clean, plan.comparison.baseline, columns, ())
    overall_current = current_all.get((), {c: 0 for c in columns})
    overall_baseline = baseline_all.get((), {c: 0 for c in columns})

    if plan.metric.kind != KIND_ADDITIVE:
        raise AnalysisRefused("compile", "ratio metrics arrive in a later task")

    column = plan.metric.value
    comparison = compare(overall_current[column], overall_baseline[column])

    result = StructuredAnalysisResult(
        metric={"name": plan.metric.name, "kind": plan.metric.kind},
        comparison=comparison,
    )
    for branch in plan.branches:
        result.breakdowns.append(
            _run_branch(plan, clean, branch, comparison["delta"])
        )
    return result


def _run_branch(
    plan: ExecutionPlan, rows: Sequence[Observation], branch: PlanBranch, total_delta: int
) -> BreakdownResult:
    columns = plan.metric.columns
    current = aggregate(rows, plan.comparison.current, columns, branch.dimensions)
    baseline = aggregate(rows, plan.comparison.baseline, columns, branch.dimensions)
    universe = group_universe(current, baseline)

    if branch.cross and len(universe) > CROSS_CELL_LIMIT:
        return BreakdownResult(
            dimensions=branch.dimensions, cross=branch.cross,
            status=STATUS_OMITTED, reason="cross_cell_limit_exceeded",
            observed_cells=len(universe),
        )

    column = plan.metric.value
    groups: List[GroupResult] = []
    for key in universe:
        cur = current.get(key, {column: 0})[column]
        base = baseline.get(key, {column: 0})[column]
        delta = cur - base
        groups.append(GroupResult(
            key=dict(zip(branch.dimensions, key)),
            net_contribution=float(delta), group_delta=delta, comparable=True,
        ))

    out = BreakdownResult(dimensions=branch.dimensions, cross=branch.cross, groups=groups)
    out.totals = {"gross_movement": sum(abs(g.net_contribution) for g in groups)}
    out.flags = {"decomposition_complete": True, "composition_dominant": False,
                 "simpson_strict": False, "heavy_cancellation": False,
                 "suppress_top_contributor": False}
    if branch.dimensions:
        out.ranking = rank(groups, plan.rank_by)
    return out
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python3 -m unittest tests.test_analysis_additive -v`
Expected: PASS (7 tests)

- [ ] **Step 5: complaints 선언과 adapter 를 쓴다**

`bia/domains/__init__.py`, `bia/adapters/__init__.py`: 빈 파일.

`bia/domains/complaints.py`:

```python
"""v1 의 고객불만 분석을 선언 하나로 재표현한다. 동작은 여기 두지 않는다."""
from __future__ import annotations

from ..analysis.registry import register
from ..analysis.spec import DomainSpec, MetricSpec

SPEC = DomainSpec(
    name="complaints",
    grain=("day", "product", "complaint_type"),
    dimensions=("product", "complaint_type"),
    metrics={"complaint_count": MetricSpec(
        name="complaint_count", kind="additive", value="count")},
)

register(SPEC)
```

`bia/adapters/complaints.py`:

```python
"""StructuredAnalysisResult 를 v1 evidence pipeline 이 쓰는 모양으로 옮긴다.

공용 엔진은 여기까지 오지 않는다. 도메인 동작은 adapter 의 책임이다.
"""
from __future__ import annotations

from typing import List, Tuple

from ..analysis.result import STATUS_OK, StructuredAnalysisResult

JOINT_DIMENSIONS = ("product", "complaint_type")


def top_contributor_cells(result: StructuredAnalysisResult) -> List[Tuple[str, str]]:
    """증가가 발생한 (제품, 불만 유형) 셀을 delta 내림차순으로 준다.

    v1 의 `metrics.compute(...).cells` 가 내던 순서와 같아야 한다 —
    delta 내림차순, 동률이면 product, complaint_type 오름차순.
    """
    for breakdown in result.breakdowns:
        if breakdown.cross and breakdown.dimensions == JOINT_DIMENSIONS:
            if breakdown.status != STATUS_OK:
                return []
            ordered = sorted(
                breakdown.groups,
                key=lambda g: (-g.net_contribution, g.key["product"], g.key["complaint_type"]),
            )
            return [(g.key["product"], g.key["complaint_type"])
                    for g in ordered if g.net_contribution > 0]
    return []
```

- [ ] **Step 6: 실패하는 테스트를 쓴다 — v1 regression gate**

`tests/test_v1_regression.py`:

```python
from __future__ import annotations

import unittest

from bia import metrics as v1_metrics
from bia.adapters.complaints import top_contributor_cells
from bia.analysis.compiler import compile_request
from bia.analysis.engine import run_plan
from bia.analysis.frame import Observation
from bia.analysis.request import AnalysisRequest, PeriodComparison
from bia.domains import complaints as complaints_domain  # noqa: F401  (등록 부작용)
from bia.integrity import decide_comparability, inspect_period
from bia.store import load_scenario

SCENARIOS = ["S%02d" % n for n in range(1, 25)]


def _observations(rows):
    return [Observation(day=r.day,
                        keys=(("complaint_type", r.complaint_type), ("product", r.product)),
                        measures=(("count", r.count),))
            for r in rows]


class StructuredRegressionTests(unittest.TestCase):
    """새 엔진이 v1 의 산술을 한 자리도 바꾸지 않았는지 24개 시나리오에서 확인한다."""

    def test_every_scenario_matches_the_v1_arithmetic(self):
        checked = 0
        for scenario_id in SCENARIOS:
            intent, rows, _tickets, _meta = load_scenario("data/scenarios/%s" % scenario_id)
            current_rows, current_integrity = inspect_period(list(rows), intent.current_period)
            baseline_rows, baseline_integrity = inspect_period(list(rows), intent.baseline_period)
            comparability = decide_comparability(current_integrity, baseline_integrity)
            if not comparability.usable:
                continue

            old = v1_metrics.compute(current_rows + baseline_rows,
                                     comparability.current_window,
                                     comparability.baseline_window)

            plan = compile_request(AnalysisRequest(
                domain="complaints", metric="complaint_count",
                breakdowns=("product", "complaint_type"),
                comparison=PeriodComparison(current=comparability.current_window,
                                            baseline=comparability.baseline_window),
                rank_by="net_contribution",
            ))
            new = run_plan(plan, _observations(current_rows + baseline_rows))

            self.assertEqual(new.comparison["current"], old.current_total, scenario_id)
            self.assertEqual(new.comparison["baseline"], old.baseline_total, scenario_id)
            self.assertEqual(new.comparison["delta"], old.delta, scenario_id)

            by_product = {tuple(sorted(g.key.items())): g.group_delta
                          for g in new.breakdowns[1].groups}
            for group in old.by_product:
                self.assertEqual(by_product[(("product", group.value),)], group.delta,
                                 "%s %s" % (scenario_id, group.value))

            old_cells = [(c.product, c.complaint_type) for c in old.cells if c.delta > 0]
            self.assertEqual(top_contributor_cells(new), old_cells, scenario_id)
            checked += 1
        self.assertGreaterEqual(checked, 20, "대부분의 시나리오가 비교 가능해야 한다")
```

- [ ] **Step 7: 실패를 확인한다**

Run: `python3 -m unittest tests.test_v1_regression -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bia.domains'` (Step 5 를 먼저 하지 않은 경우) 또는 산술 불일치

- [ ] **Step 8: 불일치를 고친다**

불일치가 나오면 **엔진을 고치지 테스트를 약화하지 않는다.** 흔한 원인 둘:
- 정렬 기준이 v1 의 `(-delta, product, complaint_type)` 과 다름
- `group_universe` 가 한 기간에만 있는 셀을 빠뜨림 (합집합이어야 한다)

Run: `python3 -m unittest tests.test_v1_regression -v`
Expected: PASS

- [ ] **Step 9: 전체 v1 평가 gate 를 돌린다 — 이것이 Gate 1 이다**

```bash
python3 -m unittest discover -t . -s tests -q
/opt/homebrew/bin/python3.11 -m unittest discover -t . -s tests -q
python3 eval/run_eval.py
python3 -m eval.v1.run_eval --selector heuristic
python3 -m eval.v1.run_eval --selector greedy
git status --porcelain -- eval/results eval/v1/results
```
Expected: v0 `전체: PASS`, v1 heuristic `FAIL`(V-2 floor, 기존과 동일), greedy `PASS`, **`git status` 출력이 비어 있어야 한다.** 하나라도 어긋나면 Task 4 로 넘어가지 않는다.

- [ ] **Step 10: 커밋**

```bash
git add bia/analysis/result.py bia/analysis/operators.py bia/analysis/engine.py \
        bia/domains bia/adapters tests/test_analysis_additive.py tests/test_v1_regression.py
git commit -m "feat(analysis): additive 엔진 + complaints 선언 + adapter — Gate 1 통과

- v1 의 고정 산술을 공용 엔진으로 치환. 24개 시나리오에서 총계·그룹 delta·
  상위 기여 셀 순서가 v1 과 동일함을 확인
- complaints 는 DomainSpec 선언 하나가 되고 동작은 adapter 로 분리
- group_universe 는 양 기간 키의 합집합. 한쪽에만 있는 그룹을 빠뜨리지 않는다
- v0/v1 채점 결과 파일 바이트 불변"
```

---

### Task 4: ratio 엔진 + 분해 + 출력 정책 — **Gate 2 전반**

**Files:**
- Create: `bia/analysis/decompose.py`
- Modify: `bia/analysis/engine.py`
- Test: `tests/test_analysis_ratio.py`, `tests/test_output_policy.py`

**Interfaces:**
- Consumes: Task 1~3 전부
- Produces:
  - `decompose_ratio(current, baseline, universe, numerator, denominator, totals) -> Tuple[List[GroupResult], Dict[str, float], Dict[str, bool]]`
  - `sign_with_tol(value: float) -> int`
  - 상수: `CANCELLATION_THRESHOLD = 0.20`, `SHARE_EPSILON = 0.0001`, `FLOAT_TOL = 1e-9`, `GROSS_EPSILON = 1e-12`

- [ ] **Step 1: 실패하는 테스트를 쓴다 — 분해 산술과 불변식**

`tests/test_analysis_ratio.py`:

```python
from __future__ import annotations

import unittest
from fractions import Fraction

from bia.analysis.decompose import FLOAT_TOL, decompose_ratio, sign_with_tol


class SignTests(unittest.TestCase):
    def test_noise_below_tolerance_counts_as_zero(self):
        self.assertEqual(sign_with_tol(1e-16), 0)
        self.assertEqual(sign_with_tol(-1e-16), 0)
        self.assertEqual(sign_with_tol(0.5), 1)
        self.assertEqual(sign_with_tol(-0.5), -1)


class DecompositionTests(unittest.TestCase):
    """손으로 검산 가능한 작은 정수로 짜고 Fraction 으로 기대값을 만든다."""

    def setUp(self):
        # A: 10/100 -> 30/200,  B: 20/100 -> 10/100
        self.current = {("A",): {"n": 30, "d": 200}, ("B",): {"n": 10, "d": 100}}
        self.baseline = {("A",): {"n": 10, "d": 100}, ("B",): {"n": 20, "d": 100}}
        self.universe = (("A",), ("B",))
        self.total_current = {"n": 40, "d": 300}
        self.total_baseline = {"n": 30, "d": 200}

    def _expected_delta(self):
        return Fraction(40, 300) - Fraction(30, 200)

    def test_net_contribution_is_the_numerator_share_change(self):
        groups, _totals, _flags = decompose_ratio(
            self.current, self.baseline, self.universe, "n", "d",
            self.total_current, self.total_baseline,
        )
        by_key = {g.key["dim"]: g for g in groups}
        expected_a = float(Fraction(30, 300) - Fraction(10, 200))
        self.assertAlmostEqual(by_key["A"].net_contribution, expected_a, places=12)

    def test_contributions_sum_exactly_to_the_total_change(self):
        groups, _totals, _flags = decompose_ratio(
            self.current, self.baseline, self.universe, "n", "d",
            self.total_current, self.total_baseline,
        )
        self.assertAlmostEqual(sum(g.net_contribution for g in groups),
                               float(self._expected_delta()), places=12)

    def test_rate_and_mix_sum_to_net_for_comparable_groups(self):
        groups, _totals, _flags = decompose_ratio(
            self.current, self.baseline, self.universe, "n", "d",
            self.total_current, self.total_baseline,
        )
        for group in groups:
            if group.comparable:
                self.assertAlmostEqual(group.rate_effect + group.mix_effect,
                                       group.net_contribution, places=12)

    def test_three_totals_sum_to_the_total_change(self):
        _groups, totals, _flags = decompose_ratio(
            self.current, self.baseline, self.universe, "n", "d",
            self.total_current, self.total_baseline,
        )
        summed = (totals["total_rate_effect"] + totals["total_mix_effect"]
                  + totals["entry_exit_effect"])
        self.assertAlmostEqual(summed, float(self._expected_delta()), places=12)


class EntryExitTests(unittest.TestCase):
    def setUp(self):
        # B 는 baseline 에 없다 (진입)
        self.current = {("A",): {"n": 10, "d": 100}, ("B",): {"n": 5, "d": 100}}
        self.baseline = {("A",): {"n": 20, "d": 100}}
        self.universe = (("A",), ("B",))
        self.total_current = {"n": 15, "d": 200}
        self.total_baseline = {"n": 20, "d": 100}

    def test_an_entering_group_keeps_net_but_loses_the_split(self):
        groups, totals, flags = decompose_ratio(
            self.current, self.baseline, self.universe, "n", "d",
            self.total_current, self.total_baseline,
        )
        entering = [g for g in groups if g.key["dim"] == "B"][0]
        self.assertFalse(entering.comparable)
        self.assertIsNone(entering.rate_effect)
        self.assertIsNone(entering.mix_effect)
        self.assertAlmostEqual(entering.net_contribution, 5 / 200.0, places=12)

    def test_entry_exit_effect_collects_non_comparable_net(self):
        _groups, totals, _flags = decompose_ratio(
            self.current, self.baseline, self.universe, "n", "d",
            self.total_current, self.total_baseline,
        )
        self.assertAlmostEqual(totals["entry_exit_effect"], 5 / 200.0, places=12)

    def test_decomposition_is_not_complete_when_a_group_enters(self):
        _groups, _totals, flags = decompose_ratio(
            self.current, self.baseline, self.universe, "n", "d",
            self.total_current, self.total_baseline,
        )
        self.assertFalse(flags["decomposition_complete"])

    def test_composition_dominant_requires_a_complete_decomposition(self):
        """진입/이탈이 지배하는데 '구성이 지배했다' 고 부르면
        entry_exit_effect 를 따로 만든 이유와 모순된다."""
        _groups, _totals, flags = decompose_ratio(
            self.current, self.baseline, self.universe, "n", "d",
            self.total_current, self.total_baseline,
        )
        self.assertFalse(flags["composition_dominant"])
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python3 -m unittest tests.test_analysis_ratio -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bia.analysis.decompose'`

- [ ] **Step 3: decompose.py 를 쓴다**

```python
"""비율 metric 의 기여도 분해와 출력 정책.

원본은 `net_contribution = N1/D1 - N0/D0` 이다. 이것은 그룹의 분자 점유 변화이고
`D_g = 0` 이어도 정의되므로 가법성이 어떤 경우에도 깨지지 않는다.
rate/mix 는 같은 숫자를 쪼갠 것이며, 양 기간 분모가 있을 때만 정의된다.
"""
from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

from .result import GroupResult

CANCELLATION_THRESHOLD = 0.20
SHARE_EPSILON = 0.0001
FLOAT_TOL = 1e-9
GROSS_EPSILON = 1e-12


def sign_with_tol(value: float) -> int:
    """`|x| <= FLOAT_TOL` 이면 0. 정확한 `x != 0` 은 1e-16 노이즈에 뒤집힌다."""
    if abs(value) <= FLOAT_TOL:
        return 0
    return 1 if value > 0 else -1


def decompose_ratio(
    current: Dict[Tuple[str, ...], Dict[str, int]],
    baseline: Dict[Tuple[str, ...], Dict[str, int]],
    universe: Sequence[Tuple[str, ...]],
    numerator: str,
    denominator: str,
    total_current: Dict[str, int],
    total_baseline: Dict[str, int],
    dimensions: Sequence[str] = ("dim",),
) -> Tuple[List[GroupResult], Dict[str, float], Dict[str, bool]]:
    d1 = float(total_current[denominator])
    d0 = float(total_baseline[denominator])
    delta_r = (total_current[numerator] / d1 if d1 else 0.0) - (
        total_baseline[numerator] / d0 if d0 else 0.0
    )

    groups: List[GroupResult] = []
    total_rate = 0.0
    total_mix = 0.0
    entry_exit = 0.0

    for key in universe:
        cur = current.get(key, {numerator: 0, denominator: 0})
        base = baseline.get(key, {numerator: 0, denominator: 0})
        n1, dg1 = float(cur[numerator]), float(cur[denominator])
        n0, dg0 = float(base[numerator]), float(base[denominator])

        net = (n1 / d1 if d1 else 0.0) - (n0 / d0 if d0 else 0.0)
        group = GroupResult(key=dict(zip(dimensions, key)), net_contribution=net)

        if dg0 > 0 and dg1 > 0:
            w0, w1 = dg0 / d0, dg1 / d1
            r0, r1 = n0 / dg0, n1 / dg1
            group.rate_effect = ((w0 + w1) / 2.0) * (r1 - r0)
            group.mix_effect = ((r0 + r1) / 2.0) * (w1 - w0)
            group.comparable = True
            total_rate += group.rate_effect
            total_mix += group.mix_effect
        else:
            group.comparable = False
            entry_exit += net
        groups.append(group)

    gross = sum(abs(g.net_contribution) for g in groups)
    decomposition_complete = abs(entry_exit) <= FLOAT_TOL
    heavy_cancellation = (
        gross > GROSS_EPSILON and abs(delta_r) / gross < CANCELLATION_THRESHOLD
    )
    composition_dominant = (
        decomposition_complete
        and sign_with_tol(total_rate) != 0
        and sign_with_tol(delta_r) != 0
        and sign_with_tol(total_rate) != sign_with_tol(delta_r)
    )
    comparable = [g for g in groups if g.comparable]
    rate_signs = {sign_with_tol(g.rate_effect) for g in comparable}
    simpson_strict = (
        decomposition_complete
        and len(comparable) >= 2
        and len(rate_signs) == 1
        and 0 not in rate_signs
        and rate_signs != {sign_with_tol(delta_r)}
        and sign_with_tol(delta_r) != 0
    )
    suppress = (
        composition_dominant
        or not decomposition_complete
        or heavy_cancellation
        or abs(delta_r) <= SHARE_EPSILON
    )

    for group in groups:
        share = None
        if (
            not composition_dominant
            and decomposition_complete
            and not heavy_cancellation
            and group.comparable
            and abs(delta_r) > SHARE_EPSILON
        ):
            candidate = group.net_contribution / delta_r
            if 0 < candidate <= 1:
                share = candidate
        group.contribution_share = share

    totals = {"total_rate_effect": total_rate, "total_mix_effect": total_mix,
              "entry_exit_effect": entry_exit, "gross_movement": gross}
    flags = {"decomposition_complete": decomposition_complete,
             "composition_dominant": composition_dominant,
             "simpson_strict": simpson_strict,
             "heavy_cancellation": heavy_cancellation,
             "suppress_top_contributor": suppress}
    _assert_invariants(groups, totals, delta_r)
    return groups, totals, flags


def _assert_invariants(groups, totals, delta_r) -> None:
    """어기면 결과를 내지 않는다. 반올림이 아니라 버그다."""
    summed = sum(g.net_contribution for g in groups)
    if abs(summed - delta_r) > FLOAT_TOL:
        raise AssertionError(
            "additivity broken: sum(net)=%r delta=%r" % (summed, delta_r)
        )
    for group in groups:
        if group.comparable:
            split = group.rate_effect + group.mix_effect
            if abs(split - group.net_contribution) > FLOAT_TOL:
                raise AssertionError(
                    "split broken for %r: %r vs %r"
                    % (group.key, split, group.net_contribution)
                )
    three = (totals["total_rate_effect"] + totals["total_mix_effect"]
             + totals["entry_exit_effect"])
    if abs(three - delta_r) > FLOAT_TOL:
        raise AssertionError("three-term identity broken: %r vs %r" % (three, delta_r))
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python3 -m unittest tests.test_analysis_ratio -v`
Expected: PASS (9 tests)

- [ ] **Step 5: 실패하는 테스트를 쓴다 — 출력 억제 정책**

`tests/test_output_policy.py`:

```python
from __future__ import annotations

import unittest

from bia.analysis.decompose import decompose_ratio


def run(current, baseline, total_current, total_baseline):
    universe = tuple(sorted(set(current) | set(baseline)))
    return decompose_ratio(current, baseline, universe, "n", "d",
                           total_current, total_baseline)


class CancellationTests(unittest.TestCase):
    def test_heavy_cancellation_suppresses_share_and_top_contributor(self):
        # A -0.30, B +0.29 -> 순변화는 작고 gross 는 크다
        current = {("A",): {"n": 20, "d": 100}, ("B",): {"n": 79, "d": 100}}
        baseline = {("A",): {"n": 50, "d": 100}, ("B",): {"n": 50, "d": 100}}
        groups, totals, flags = run(current, baseline,
                                    {"n": 99, "d": 200}, {"n": 100, "d": 200})
        self.assertTrue(flags["heavy_cancellation"])
        self.assertTrue(flags["suppress_top_contributor"])
        for group in groups:
            self.assertIsNone(group.contribution_share)


class ShareBoundTests(unittest.TestCase):
    def test_a_share_above_one_is_not_exposed(self):
        """heavy_cancellation 에 걸리지 않으면서 share 가 1 을 넘는 구성.
        '전체 하락의 120%' 는 수학적으로 참이면서 사람에게 거짓말이다."""
        current = {("A",): {"n": 38, "d": 100}, ("B",): {"n": 52, "d": 100}}
        baseline = {("A",): {"n": 50, "d": 100}, ("B",): {"n": 50, "d": 100}}
        groups, totals, flags = run(current, baseline,
                                    {"n": 90, "d": 200}, {"n": 100, "d": 200})
        self.assertFalse(flags["heavy_cancellation"])
        by_key = {g.key["dim"]: g for g in groups}
        self.assertIsNone(by_key["A"].contribution_share)
        self.assertIsNone(by_key["B"].contribution_share)

    def test_a_clean_share_is_exposed(self):
        current = {("A",): {"n": 40, "d": 100}, ("B",): {"n": 50, "d": 100}}
        baseline = {("A",): {"n": 50, "d": 100}, ("B",): {"n": 50, "d": 100}}
        groups, _totals, flags = run(current, baseline,
                                     {"n": 90, "d": 200}, {"n": 100, "d": 200})
        self.assertFalse(flags["suppress_top_contributor"])
        by_key = {g.key["dim"]: g for g in groups}
        self.assertIsNotNone(by_key["A"].contribution_share)
        self.assertAlmostEqual(by_key["A"].contribution_share, 1.0, places=9)


class NearZeroTests(unittest.TestCase):
    def test_a_near_zero_net_suppresses_everything(self):
        current = {("A",): {"n": 5000, "d": 100000}, ("B",): {"n": 5001, "d": 100000}}
        baseline = {("A",): {"n": 5001, "d": 100000}, ("B",): {"n": 5000, "d": 100000}}
        _groups, _totals, flags = run(current, baseline,
                                      {"n": 10001, "d": 200000},
                                      {"n": 10001, "d": 200000})
        self.assertTrue(flags["suppress_top_contributor"])
```

- [ ] **Step 6: 실패를 확인하고 통과시킨다**

Run: `python3 -m unittest tests.test_output_policy -v`
Expected: 처음에는 일부 FAIL 가능. `decompose_ratio` 의 정책 분기를 고쳐 통과시킨다. **임계값(0.20, 0.0001)은 건드리지 않는다** — 사전 등록 값이다. 테스트 데이터를 조정해 의도한 상황을 만든다.

- [ ] **Step 7: engine.py 에 ratio 분기를 연결한다**

`run_plan` 의 `if plan.metric.kind != KIND_ADDITIVE: raise ...` 를 제거하고 분기한다:

```python
    if plan.metric.kind == KIND_ADDITIVE:
        column = plan.metric.value
        comparison = compare(overall_current[column], overall_baseline[column])
    else:
        n, d = plan.metric.numerator, plan.metric.denominator
        if overall_current[d] == 0 or overall_baseline[d] == 0:
            raise AnalysisRefused(
                "aggregate",
                "the overall denominator %r is zero in one of the periods" % d,
            )
        current_rate = overall_current[n] / float(overall_current[d])
        baseline_rate = overall_baseline[n] / float(overall_baseline[d])
        delta = current_rate - baseline_rate
        comparison = {"current": current_rate, "baseline": baseline_rate,
                      "delta": delta,
                      "relative_change": (delta / baseline_rate) if baseline_rate else None}
```

`_run_branch` 에도 ratio 분기를 더해 `decompose_ratio` 를 부르고, `numerator_bounded_by_denominator` 가 참이면 `N_g > D_g` 를 `AnalysisRefused("integrity", ...)` 로 거부한다. `N_g > 0` 인데 `D_g == 0` 인 경우도 같은 방식으로 거부한다.

- [ ] **Step 8: 전체 스위트와 Gate 1 불변을 다시 확인한다**

```bash
python3 -m unittest discover -t . -s tests -q
/opt/homebrew/bin/python3.11 -m unittest discover -t . -s tests -q
python3 eval/run_eval.py >/dev/null
python3 -m eval.v1.run_eval --selector heuristic >/dev/null 2>&1
python3 -m eval.v1.run_eval --selector greedy >/dev/null 2>&1
git status --porcelain -- eval/results eval/v1/results
```
Expected: 전부 OK, `git status` 비어 있음

- [ ] **Step 9: 커밋**

```bash
git add bia/analysis/decompose.py bia/analysis/engine.py \
        tests/test_analysis_ratio.py tests/test_output_policy.py
git commit -m "feat(analysis): ratio 분해와 출력 정책

- net_contribution = N1/D1 - N0/D0 으로 가법성을 무조건 보존
- 중간점 가중 rate/mix 분해. 교차항이 정확히 상쇄돼 잔차 배분 불필요
- 진입/이탈 그룹은 split 만 비우고 net 유지, entry_exit_effect 로 합산
- 불변식 3개를 어기면 결과를 내지 않는다
- composition_dominant/simpson_strict 는 decomposition_complete 를 전제로 한다
- share 노출 조건 6개. 0 < share <= 1 이 120% 구멍을 막는다
- 부호 비교는 전부 tolerance-aware"
```

---

### Task 5: 새 도메인 2개 + 18 벤치마크 + 독립 oracle

**Files:**
- Create: `bia/domains/ecommerce.py`, `bia/domains/support_ops.py`, `bia/v2bench/__init__.py`, `bia/v2bench/cases.py`, `bia/v2bench/generate.py`, `scripts/check_v2bench.py`
- Test: `tests/test_v2_benchmark.py`

**Interfaces:**
- Consumes: Task 1~4 전부
- Produces:
  - `bia.domains.ecommerce.SPEC`, `bia.domains.support_ops.SPEC`
  - `bia.v2bench.cases.CASES: Tuple[CaseSpec, ...]` — 18개
  - `python3 -m bia.v2bench.generate` 가 `data/v2/<CASE_ID>/metrics.csv` 와 `data/v2/oracle.json` 을 결정론적으로 쓴다

- [ ] **Step 1: 도메인 둘을 선언한다**

`bia/domains/ecommerce.py`:

```python
from __future__ import annotations

from ..analysis.registry import register
from ..analysis.spec import DomainSpec, MetricSpec

SPEC = DomainSpec(
    name="ecommerce",
    grain=("day", "channel", "device", "category"),
    dimensions=("channel", "device", "category"),
    metrics={
        "revenue": MetricSpec(name="revenue", kind="additive", value="revenue_krw"),
        "orders": MetricSpec(name="orders", kind="additive", value="orders"),
        "conversion_rate": MetricSpec(
            name="conversion_rate", kind="ratio",
            numerator="orders", denominator="sessions",
            numerator_bounded_by_denominator=True,
        ),
    },
)

register(SPEC)
```

`bia/domains/support_ops.py`:

```python
"""`day` 는 접수 cohort 날짜다. '오늘 해결 / 오늘 접수' 로 두면 동일 cohort 의
비율이 아니고 resolved > received 가 정상적으로 나올 수 있어, 비율 의미론을
검증하려는 목적과 어긋난다."""
from __future__ import annotations

from ..analysis.registry import register
from ..analysis.spec import DomainSpec, MetricSpec

SPEC = DomainSpec(
    name="support_ops",
    grain=("day", "queue", "priority"),
    dimensions=("queue", "priority"),
    metrics={
        "tickets_received": MetricSpec(
            name="tickets_received", kind="additive", value="received"),
        "sla_resolution_rate": MetricSpec(
            name="sla_resolution_rate", kind="ratio",
            numerator="resolved_within_sla", denominator="received",
            numerator_bounded_by_denominator=True,
        ),
    },
)

register(SPEC)
```

- [ ] **Step 2: 사례 타입과 18 사례를 선언한다**

`bia/v2bench/cases.py`:

```python
"""18 사례. spec §9 의 표를 그대로 옮긴 것이며 데이터 생성 전에 고정됐다.

모든 수치는 손으로 검산 가능한 작은 정수다. oracle 이 exact arithmetic 으로
같은 값을 독립 계산할 수 있어야 하기 때문이다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple


@dataclass(frozen=True)
class Row:
    day: str
    keys: Tuple[Tuple[str, str], ...]
    measures: Tuple[Tuple[str, int], ...]


@dataclass(frozen=True)
class CaseSpec:
    case_id: str
    domain: str
    metric: str
    breakdowns: Tuple[str, ...]
    rank_by: str
    baseline: Tuple[str, str]      # (start, end) ISO
    current: Tuple[str, str]
    rows: Tuple[Row, ...]
    notes: str
    expect_refused: Optional[Tuple[str, str]] = None   # (stage, reason_substring)
    golden: Dict[str, object] = field(default_factory=dict)


def _row(day, channel, device, sessions, orders):
    return Row(day=day,
               keys=(("channel", channel), ("device", device)),
               measures=(("orders", orders), ("sessions", sessions)))


# C02 composition_dominant — 두 채널 모두 자기 전환율은 올랐는데
# 전환율이 낮은 쪽의 세션 점유가 크게 늘어 전체는 내려간다.
C02 = CaseSpec(
    case_id="C02", domain="ecommerce", metric="conversion_rate",
    breakdowns=("channel",), rank_by="net_contribution",
    baseline=("2026-06-01", "2026-06-01"), current=("2026-07-01", "2026-07-01"),
    rows=(
        _row("2026-06-01", "paid", "mobile", 100, 10),
        _row("2026-06-01", "organic", "mobile", 100, 30),
        _row("2026-07-01", "paid", "mobile", 300, 33),
        _row("2026-07-01", "organic", "mobile", 100, 31),
    ),
    notes="baseline 40/200=0.20, current 64/400=0.16. 그룹 비율은 둘 다 상승.",
    golden={"delta": -0.04, "composition_dominant": True},
)

# C04 진입 그룹이 전체를 끌어내린다 — entry_exit_effect 가 지배한다.
C04 = CaseSpec(
    case_id="C04", domain="ecommerce", metric="conversion_rate",
    breakdowns=("channel",), rank_by="net_contribution",
    baseline=("2026-06-01", "2026-06-01"), current=("2026-07-01", "2026-07-01"),
    rows=(
        _row("2026-06-01", "paid", "mobile", 100, 20),
        _row("2026-07-01", "paid", "mobile", 100, 20),
        _row("2026-07-01", "affiliate", "mobile", 100, 0),
    ),
    notes="affiliate 는 baseline 에 없다. rate/mix 는 비고 net 은 유지된다.",
    golden={"delta": -0.10, "entry_exit_effect": -0.10,
            "decomposition_complete": False, "composition_dominant": False},
)

# C09 분모 0 인데 분자 > 0 — 세션 없이 주문이 있을 수 없다.
C09 = CaseSpec(
    case_id="C09", domain="ecommerce", metric="conversion_rate",
    breakdowns=("channel",), rank_by="net_contribution",
    baseline=("2026-06-01", "2026-06-01"), current=("2026-07-01", "2026-07-01"),
    rows=(
        _row("2026-06-01", "paid", "mobile", 100, 20),
        _row("2026-07-01", "paid", "mobile", 0, 5),
    ),
    notes="integrity 가 거부해야 한다. 보간하지 않는다.",
    expect_refused=("integrity", "denominator"),
)

CASES: Tuple[CaseSpec, ...] = (C02, C04, C09)  # 나머지 15 사례를 같은 형식으로 채운다
```

나머지 15 사례는 아래 표의 파라미터로 **같은 형식**으로 쓴다. 각 사례의 `notes` 에 손으로 계산한
기대값을 남긴다.

| ID | domain | metric | 구성 |
|---|---|---|---|
| C01 | ecommerce | conversion_rate | A 10/100→30/200, B 20/100→10/100. `mean(r)` 와 `ΣN/ΣD` 가 갈린다 |
| C03 | ecommerce | conversion_rate | comparable 전부 Δr 동일 부호, 전체는 반대. `simpson_strict` 참 |
| C05 | ecommerce | conversion_rate | 이탈 그룹 (`D1=0`) |
| C06 | ecommerce | conversion_rate | A −0.30 / B +0.29. `heavy_cancellation` 참 |
| C07 | ecommerce | conversion_rate | `\|ΔR\| < 0.0001`, gross 는 큼 |
| C08 | ecommerce | conversion_rate | 진입 + 구성 이동 동시. 세 항 모두 0 이 아님 |
| C10 | ecommerce | conversion_rate | 전체 `D=0` → `AnalysisRefused("aggregate", ...)` |
| C11 | ecommerce | revenue | `channel` 이 빈 문자열 → `__UNKNOWN__`, partition 보존 |
| C12 | ecommerce | revenue | grain 중복 행(충돌) → `AnalysisRefused("integrity", ...)` |
| C13 | ecommerce | revenue | `breakdowns=("channel","device","category")`, 교차 셀 1000 초과 → `omitted` |
| C14 | ecommerce | revenue | `rank_by="rate_effect"` → `RequestError` (컴파일 거부) |
| C15 | ecommerce | conversion_rate | 부분 기간 — 분자·분모가 같은 정렬 구간에서 나오는가 |
| C16 | complaints | complaint_count | S01 과 동일 산출 |
| C17 | ecommerce | revenue | 단순 증가 + 신규 category 등장 |
| C18 | support_ops | sla_resolution_rate | `baseline` 분자 0, 분모 정상 → `relative_change` 없음 |

- [ ] **Step 3: oracle 을 exact arithmetic 으로 만든다**

`bia/v2bench/generate.py` 는 `bia.analysis` 를 **import 하지 않는다.** 분해 수식을 `Fraction` 으로
독립 재진술한다.

```python
"""벤치마크 데이터와 oracle 생성기.

`bia.analysis` 를 import 하지 않는다. 같은 수식을 복붙하면 production 의 float 처리나
집계 버그를 oracle 이 똑같이 복제한다. 그래서 Fraction 으로 다시 계산한다.
"""
from __future__ import annotations

import json
import os
from fractions import Fraction
from typing import Dict, List, Tuple

from .cases import CASES, CaseSpec

OUT_ROOT = "data/v2"


def _period_rows(case: CaseSpec, start: str, end: str) -> List:
    return [r for r in case.rows if start <= r.day <= end]


def _sum_by(rows, dimension: str, column: str) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for row in rows:
        key = dict(row.keys)[dimension]
        out[key] = out.get(key, 0) + dict(row.measures)[column]
    return out


def ratio_oracle(case: CaseSpec, dimension: str,
                 numerator: str, denominator: str) -> Dict[str, object]:
    """spec §4 의 수식을 Fraction 으로 다시 진술한다."""
    base_rows = _period_rows(case, *case.baseline)
    cur_rows = _period_rows(case, *case.current)

    n0_by, d0_by = _sum_by(base_rows, dimension, numerator), _sum_by(base_rows, dimension, denominator)
    n1_by, d1_by = _sum_by(cur_rows, dimension, numerator), _sum_by(cur_rows, dimension, denominator)

    d0 = Fraction(sum(d0_by.values()))
    d1 = Fraction(sum(d1_by.values()))
    r0 = Fraction(sum(n0_by.values())) / d0 if d0 else Fraction(0)
    r1 = Fraction(sum(n1_by.values())) / d1 if d1 else Fraction(0)
    delta = r1 - r0

    groups: Dict[str, Dict[str, object]] = {}
    total_rate = Fraction(0)
    total_mix = Fraction(0)
    entry_exit = Fraction(0)

    for key in sorted(set(n0_by) | set(n1_by) | set(d0_by) | set(d1_by)):
        n0, dg0 = Fraction(n0_by.get(key, 0)), Fraction(d0_by.get(key, 0))
        n1, dg1 = Fraction(n1_by.get(key, 0)), Fraction(d1_by.get(key, 0))
        net = (n1 / d1 if d1 else Fraction(0)) - (n0 / d0 if d0 else Fraction(0))
        entry = {"expect_net_contribution": float(net)}
        if dg0 > 0 and dg1 > 0:
            w0, w1 = dg0 / d0, dg1 / d1
            gr0, gr1 = n0 / dg0, n1 / dg1
            rate = ((w0 + w1) / 2) * (gr1 - gr0)
            mix = ((gr0 + gr1) / 2) * (w1 - w0)
            entry["expect_rate_effect"] = float(rate)
            entry["expect_mix_effect"] = float(mix)
            total_rate += rate
            total_mix += mix
        else:
            entry["expect_comparable"] = False
            entry_exit += net
        groups[key] = entry

    assert total_rate + total_mix + entry_exit == delta, case.case_id
    return {
        "expect_total_rate_effect": float(total_rate),
        "expect_total_mix_effect": float(total_mix),
        "expect_entry_exit_effect": float(entry_exit),
        "groups": groups,
    }
```

`main()` 은 사례마다 `data/v2/<CASE_ID>/metrics.csv` 를 쓰고, 위 함수로 만든 breakdown-local
oracle 을 `data/v2/oracle.json` 에 `sort_keys=True, indent=2` 로 저장한다.
`case.golden` 이 비어 있지 않으면 **계산값이 아니라 그 값을 기록**하고, 계산값과 다르면 즉시 실패한다.

- [ ] **Step 4: 검사 스크립트를 쓴다**

`scripts/check_v2bench.py`:

```python
"""디스크에 쓰인 파일에서 다시 계산해 oracle 과 대조한다.
생성기 내부 자료구조를 믿지 않는다."""
from __future__ import annotations

import csv
import hashlib
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def digest(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def snapshot():
    out = {}
    for base, _dirs, files in os.walk(os.path.join(ROOT, "data", "v2")):
        for name in sorted(files):
            full = os.path.join(base, name)
            out[os.path.relpath(full, ROOT)] = digest(full)
    return out


def main():
    checks = 0
    before = snapshot()
    subprocess.check_call([sys.executable, "-m", "bia.v2bench.generate"], cwd=ROOT)
    after = snapshot()
    assert before == after, "재생성이 바이트 동일하지 않다"
    checks += 1

    oracle = json.load(open(os.path.join(ROOT, "data", "v2", "oracle.json"), encoding="utf-8"))
    for case_id, entry in sorted(oracle["cases"].items()):
        path = os.path.join(ROOT, "data", "v2", case_id, "metrics.csv")
        if entry.get("expect_refused"):
            assert os.path.exists(path), case_id
            checks += 1
            continue
        rows = list(csv.DictReader(open(path, encoding="utf-8", newline="")))
        assert rows, case_id
        for breakdown in entry.get("breakdowns", {}).values():
            summed = sum(g["expect_net_contribution"] for g in breakdown["groups"].values())
            assert abs(summed - entry["expect_delta"]) < 1e-9, case_id
            checks += 1
    print("checks run: %d" % checks)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
```

Run: `python3 -m bia.v2bench.generate && python3 scripts/check_v2bench.py`
Expected: `ALL CHECKS PASSED`

- [ ] **Step 5: 벤치마크 테스트를 쓴다**

`tests/test_v2_benchmark.py`:

```python
from __future__ import annotations

import json
import os
import unittest

from bia.analysis.compiler import compile_request
from bia.analysis.engine import run_plan
from bia.analysis.errors import AnalysisRefused, RequestError
from bia.analysis.frame import load_observations
from bia.analysis.registry import get_domain
from bia.analysis.request import AnalysisRequest, PeriodComparison
from bia.domains import complaints, ecommerce, support_ops  # noqa: F401
from bia.types import Period
from bia.v2bench.cases import CASES

ORACLE = json.load(open("data/v2/oracle.json", encoding="utf-8"))


class BenchmarkTests(unittest.TestCase):
    def test_every_case_matches_its_oracle(self):
        for case in CASES:
            with self.subTest(case=case.case_id):
                expected = ORACLE["cases"][case.case_id]
                spec = get_domain(case.domain)
                metric = spec.metrics[case.metric]
                request = AnalysisRequest(
                    domain=case.domain, metric=case.metric,
                    breakdowns=case.breakdowns,
                    comparison=PeriodComparison(
                        current=Period.of(*case.current),
                        baseline=Period.of(*case.baseline)),
                    rank_by=case.rank_by,
                )
                if case.expect_refused and case.expect_refused[0] == "compile":
                    with self.assertRaises(RequestError):
                        compile_request(request)
                    continue

                plan = compile_request(request)
                rows = load_observations(
                    "data/v2/%s/metrics.csv" % case.case_id, spec, metric.columns)

                if case.expect_refused:
                    stage, needle = case.expect_refused
                    with self.assertRaises(AnalysisRefused) as caught:
                        run_plan(plan, rows)
                    self.assertEqual(caught.exception.stage, stage)
                    self.assertIn(needle, caught.exception.reason)
                    continue

                result = run_plan(plan, rows)
                self.assertAlmostEqual(result.comparison["delta"],
                                       expected["expect_delta"], places=9)
                for breakdown in result.breakdowns:
                    if not breakdown.dimensions:
                        continue
                    name = ",".join(breakdown.dimensions)
                    if name not in expected.get("breakdowns", {}):
                        continue
                    want = expected["breakdowns"][name]
                    self.assertAlmostEqual(
                        breakdown.totals["entry_exit_effect"],
                        want["expect_entry_exit_effect"], places=9)
                    for flag, value in want.get("expect_flags", {}).items():
                        self.assertEqual(breakdown.flags[flag], value,
                                         "%s %s %s" % (case.case_id, name, flag))
```

- [ ] **Step 6: 통과·전체 스위트·Gate 1 재확인**

```bash
python3 -m unittest discover -t . -s tests -q
/opt/homebrew/bin/python3.11 -m unittest discover -t . -s tests -q
python3 eval/run_eval.py >/dev/null
git status --porcelain -- eval/results eval/v1/results
```

- [ ] **Step 7: 커밋**

```bash
git add bia/domains bia/v2bench scripts/check_v2bench.py data/v2 tests/test_v2_benchmark.py
git commit -m "feat(v2bench): 도메인 2개와 18 사례 벤치마크

- ecommerce/support_ops 선언. support_ops 의 day 는 접수 cohort
- 18 사례를 데이터 생성 전에 고정한 계약대로 구현
- oracle 은 bia.analysis 를 import 하지 않고 Fraction 으로 독립 계산,
  핵심 사례는 golden value
- 검사 스크립트가 디스크에 쓰인 파일에서 다시 계산해 대조"
```

---

### Task 6: 최종 acceptance

**Files:**
- Test: `tests/test_v2_acceptance.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: Task 1~5 전부

- [ ] **Step 1: 도메인 누수 검사를 쓴다**

```python
def test_the_engine_never_names_a_domain(self):
    """도메인 차이는 전부 DomainSpec 데이터로 표현된다.
    문자열 grep 이 아니라 AST 로 본다 — docstring 이 도메인을 언급할 수 있다."""
    import ast, os
    names = {"complaints", "ecommerce", "support_ops"}
    offenders = []
    package = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "bia", "analysis")
    for filename in sorted(os.listdir(package)):
        if not filename.endswith(".py"):
            continue
        tree = ast.parse(open(os.path.join(package, filename), encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value in names:
                    offenders.append((filename, node.value))
    self.assertEqual(offenders, [])
```

- [ ] **Step 2: 전체 acceptance 를 돌린다**

```bash
python3 -m unittest discover -t . -s tests -q
/opt/homebrew/bin/python3.11 -m unittest discover -t . -s tests -q
python3 eval/run_eval.py
python3 -m eval.v1.run_eval --selector heuristic
python3 -m eval.v1.run_eval --selector greedy
python3 -m bia.v2bench.generate && python3 scripts/check_v2bench.py
git status --porcelain -- eval/results eval/v1/results data/scenarios data/challenges data/oracle
```
Expected: 전부 통과, 마지막 `git status` 비어 있음

- [ ] **Step 3: README 를 갱신한다**

v2 섹션을 추가한다 — 무엇이 일반화됐는지, 세 도메인, 18 사례 결과, **v1 은 `v1.0.0` 으로 고정돼 있고 regression 0 이라는 것.** v1 의 JEV 결과 서술은 건드리지 않는다.

- [ ] **Step 4: 커밋**

```bash
git add tests/test_v2_acceptance.py README.md
git commit -m "test(v2): 최종 acceptance 와 문서 갱신

- analysis/ 안에 도메인 이름 리터럴이 없음을 AST 로 단언
- v1 regression 0, 18 사례 oracle 일치, 동결 데이터 불변을 한 번에 확인"
```
