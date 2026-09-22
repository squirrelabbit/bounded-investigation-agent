# v2 — Typed Structured Analysis

- 작성일: 2026-09-22
- 상태: 설계 확정, 구현 전
- 선행: v1 (`v1.0.0` 태그로 고정), `SCOPE.md`, `eval/v1/CONTRACT.md`

## 한눈에 보기

v1 의 정형 계산 레이어를 도메인에서 분리한다.

> **도메인별로 선언된 metric·grain·dimension 계약을 받아, additive 와 ratio metric 의
> 비교·분해·기여·순위를 deterministic 하게 계산하는 typed structured-analysis engine.**

v1 은 `complaint_count` 라는 metric 하나와 `product`/`complaint_type` 이라는 축 둘을 **코드가 알고 있었다.**
v2 는 그것을 데이터(선언)로 내리고, 엔진은 도메인을 모른다. v1 의 고객불만 분석은 그 엔진 위의 **첫 번째 도메인 선언**이 된다.

v2 가 증명하려는 것은 하나다 — **서로 다른 도메인의 정형 비교 분석을 동일한 typed execution layer 로 표현하고 정확하게 실행할 수 있다.**

## v1 과의 관계

| | v1 | v2 |
|---|---|---|
| 질문 | probabilistic model 을 어디에 **쓰지 말아야** 하는가 | 코드로 **어디까지 일반화**할 수 있는가 |
| 결과 | 강한 deterministic baseline > JEV, 안전은 서버 검증이 지켰다 | (측정 대상) |
| 상태 | `v1.0.0` 으로 고정. 재실행·재해석하지 않음 | main 에서 in-place 일반화 |

**JEV 는 v2 공용 엔진에 없다.** v1 의 evidence-selection 실험은 historical benchmark 로 그대로 둔다.

---

## 1. 구조와 경계

```
Data + AnalysisRequest
        ↓
DomainSpec / MetricSpec validation
        ↓
Deterministic Compiler
        ↓
Bounded Execution Plan
        ↓
TypedAnalysisEngine
        ↓
StructuredAnalysisResult        ← v2 공용 경계. "answer" 가 아니다
        ↓
complaints adapter              ← 여기부터 v1 전용
        ↓
evidence selection → retrieval → verification → answer
```

### 모듈 지도

```
bia/
├── analysis/            도메인을 모르는 공용 엔진
│   ├── spec.py          DomainSpec / MetricSpec / DimensionSpec
│   ├── request.py       AnalysisRequest / PeriodComparison
│   ├── registry.py      선언된 도메인·metric 의 닫힌 목록
│   ├── compiler.py      request + spec → bounded execution plan
│   ├── frame.py         측정값 로딩·정규화
│   ├── operators.py     AGGREGATE COMPARE BREAKDOWN CONTRIBUTION RATE RANK
│   ├── decompose.py     중간점 rate/mix 분해 + 출력 정책
│   └── result.py        StructuredAnalysisResult
│
├── domains/             선언만. behavior 금지
│   ├── complaints.py    v1 을 spec 하나로 재표현
│   ├── ecommerce.py
│   └── support_ops.py
│
├── adapters/
│   └── complaints.py    StructuredAnalysisResult → v1 evidence pipeline
│
├── integrity.py         일반화 (중복키를 physical grain 기준으로)
└── (v1 그대로)          evidence retrieval verify decision controller answer lexicon jev
```

### 계약 문장 (구현 시 지켜야 하는 불변)

1. **Domain modules are declarative only; domain behavior belongs in adapters.**
2. **Data uniqueness is validated against the domain's declared physical grain, never against the dimensions requested by an analysis.**
3. **Static domain/metric declarations and runtime analysis requests are separate types.**
4. **연산자는 외부 계약에 노출되지 않는다.** 호출자도 모델도 `RATE` 를 고를 수 없다. Compiler 가 `MetricSpec` 과 `AnalysisRequest` 로 결정한다.
5. **`analysis/` 안에 도메인 이름을 검사하는 코드가 존재하지 않는다.** 도메인 차이는 전부 `DomainSpec` 데이터로 표현된다.

---

## 2. 타입

### 정적 선언

```python
DomainSpec(
    name="ecommerce",
    grain=("day", "channel", "device", "category"),   # 한 행의 물리적 최소 단위
    dimensions=("channel", "device", "category"),      # breakdown 가능한 축
    metrics={...},
)

MetricSpec(name="revenue",         kind="additive", value="revenue_krw")
MetricSpec(name="conversion_rate", kind="ratio",    numerator="orders",
                                                     denominator="sessions",
                                                     numerator_bounded_by_denominator=True)
```

- `grain` 과 `dimensions` 는 **개념이 다르다.** 같은 목록일 수 있으나 용도가 다르다.
  - `grain` = 데이터 한 행이 의미하는 최소 단위 → 중복 검사 기준
  - `dimensions` = 분석 시 breakdown 할 수 있는 축
- 비율형은 **분자·분모를 둘 다 명시**해야 한다. 분모를 모르면 `mean(그룹 비율)` 과 `sum(N)/sum(D)` 를 구분할 수 없다.
- metric 은 **코드에 선언된 닫힌 레지스트리**에서만 선택된다. 사용자 수식 문자열 금지.
- `numerator_bounded_by_denominator` 는 불리언 선언이다. 모든 비율이 1 이하는 아니므로
  (주문당 상품 수 같은 것) 도메인이 선언해야 한다. 참이면 integrity 가 `0 ≤ N_g ≤ D_g` 를 검사한다.
  수식 언어가 아니라 불리언이므로 "임의 표현식 금지" 규율은 유지된다.

### 런타임 요청

```python
AnalysisRequest(
    domain="ecommerce",
    metric="conversion_rate",
    breakdowns=("channel", "device"),
    comparison=PeriodComparison(current=Period, baseline=Period),
    rank_by="net_contribution",
)
```

### `rank_by` 유효성

| `rank_by` | additive | ratio |
|---|---|---|
| `net_contribution` | ✓ | ✓ |
| `group_delta` | ✓ | ✗ |
| `rate_effect` | ✗ | ✓ |
| `mix_effect` | ✗ | ✓ |

metric kind 와 맞지 않으면 **컴파일 단계에서 거부**한다. 그 metric 에 그 성분이 존재하지 않기 때문이다.

---

## 3. Compiler

Compiler 산출물은 선형 연산자 리스트가 아니라 **bounded execution plan** 이다. `BREAKDOWN` 뒤에서 다시 집계해야 하므로 실제 실행은 분기 구조다.

```
AnalysisRequest + DomainSpec
        ↓ validate (metric 존재 / breakdown 이 선언된 dimension / rank_by 유효성)
        ↓
plan
 ├─ overall              aggregate → (rate) → compare
 ├─ marginal[channel]    aggregate by channel → (rate) → compare → (decompose) → rank
 ├─ marginal[device]     …
 └─ joint[channel,device] …
```

가능한 plan 모양은 코드에 고정돼 있고, Compiler 는 결정론적으로 그중 하나를 생성한다. **planner 가 아니다.**

### BREAKDOWN 규칙

> For N requested dimensions, the engine produces N marginal breakdowns and at most one full-joint breakdown. It does not generate intermediate dimension combinations.

`breakdowns=(channel, device, category)` → `channel`, `device`, `category`, `channel×device×category`. 중간 조합(`channel×device` 등)은 만들지 않는다.

`observed_cells` 는 **current 와 baseline 그룹 키의 합집합 크기**로 정의한다. 한 기간의 최대치만
보면 실제 분해 대상 그룹 수를 과소평가한다 (baseline 800 + current 800, 겹침 300 → 1300).

교차 셀 수가 `CROSS_CELL_LIMIT` 을 넘으면 **조용히 자르지 않고** 생략 사실을 결과에 남긴다.

```json
{ "dimensions": ["channel","device","category"], "cross": true,
  "status": "omitted", "reason": "cross_cell_limit_exceeded",
  "observed_cells": 4821, "limit": 1000 }
```

### breakdown-local 원칙

`ranking`, `flags`, `non_comparable_groups`, 분해 결과는 **전부 breakdown 안에 있다.** channel 기준으로는 구성 지배인데 device 기준으로는 아닐 수 있다.

**서로 다른 breakdown 의 그룹을 하나의 ranking 에 섞지 않는다.** 셋은 같은 전체 변화를 서로 다른 기준으로 중복 분해한 것이다.

---

## 4. 기여도 의미론

### 원본은 `net_contribution`, 그리고 항상 정확하다

```
net_contribution_g  =  N1_g / D1  −  N0_g / D0
```

`w_g · r_g = (D_g/D)(N_g/D_g) = N_g/D` 이므로 이것은 **그룹의 분자 점유 변화**이고, `D_g = 0` 이어도 정의된다.

```
Σ_g net_contribution_g  =  N1/D1 − N0/D0  =  ΔR        ← 항상 성립
```

additive metric 에서는 `net_contribution_g = group_delta_g` 이며 동일한 가법성이 성립한다.

### rate/mix 는 같은 숫자의 분해다

양 기간 모두 `D_g > 0` 일 때만:

```
w_g = D_g/D,  r_g = N_g/D_g
w̄_g = (w0_g + w1_g)/2,  r̄_g = (r0_g + r1_g)/2

rate_effect_g = w̄_g · (r1_g − r0_g)
mix_effect_g  = r̄_g · (w1_g − w0_g)

rate_effect_g + mix_effect_g = net_contribution_g       (교차항이 정확히 상쇄됨)
```

중간점 가중을 쓰므로 **잔차항 배분이 필요 없다.**

### 진입/이탈

| 상황 | 처리 |
|---|---|
| `D0_g = 0` 또는 `D1_g = 0` | `comparable: false`, `rate_effect`/`mix_effect` = 없음, `net_contribution` 유지 |
| `D_g = 0` 인데 `N_g > 0` | **데이터 오류.** integrity 에서 거부. 보간하지 않음 |
| 전체 `D0 = 0` 또는 `D1 = 0` | 비율 정의 불가. 분석 중단, 사유 기록 |

```
entry_exit_effect = Σ net_contribution_g   for non-comparable groups
```

### 불변식 — 서버가 자기를 검사한다

```
1.  |Σ net_contribution − ΔR|                                    ≤ FLOAT_TOL
2.  |rate_effect_g + mix_effect_g − net_contribution_g|          ≤ FLOAT_TOL   (comparable 그룹마다)
3.  |total_rate_effect + total_mix_effect + entry_exit_effect − ΔR| ≤ FLOAT_TOL
```

허용 오차를 넘으면 **결과를 내지 않고 예외를 던진다.** 반올림이 아니라 버그다.

### partition 완전성 (전제)

breakdown 그룹은 전체 population 을 **상호배타적이고 완전하게 partition** 해야 한다. 이것이 깨지면 위 불변식이 무의미하다.

- dimension 값이 NULL 이면 drop 하지 않고 `__UNKNOWN__` 버킷에 넣거나 integrity 에서 거부한다.
- **v2 엔진은 breakdown 그룹을 top-N 으로 절단하지 않는다. 따라서 `OTHER` 버킷을 만들지 않는다.**
  교차 셀이 많으면 절단이 아니라 `omitted` 로 처리한다.
  나중에 renderer 가 표시 목적으로 그룹을 축약한다면 그것은 `StructuredAnalysisResult` **이후**의
  관심사이며, 원본 분석 결과의 partition 을 변경하지 않는다.

---

## 5. 출력 정책

### 플래그 (전부 breakdown-local)

```
decomposition_complete =  |entry_exit_effect| ≤ FLOAT_TOL

composition_dominant   =  decomposition_complete
                          AND sign(total_rate_effect) ≠ sign(ΔR)
                          AND 둘 다 0 이 아님

simpson_strict         =  decomposition_complete
                          AND comparable 그룹 수 ≥ 2
                          AND 모든 comparable Δr 이 동일한 0 아닌 부호
                          AND 그 부호가 ΔR 과 반대
heavy_cancellation    =  gross > GROSS_EPSILON  AND  |ΔR| / gross < CANCELLATION_THRESHOLD
gross_movement        =  Σ_g |net_contribution_g|            (항상 노출)
```

`composition_dominant` 는 "모든 그룹이 반대"가 아니라 **rate 효과의 합이 전체와 반대**로 정의한다. 한 그룹이 예외적으로 움직여도 "구성이 지배했다"는 사실은 그대로이기 때문이다. `simpson_strict` 는 고전적 정의의 별도 진단으로 남긴다.

플래그는 **조건과 무관하게 항상 계산해 기록**한다. 억제는 별도 규칙이다.

`composition_dominant` 와 `simpson_strict` 가 `decomposition_complete` 를 전제로 하는 이유는,
진입/이탈이 변화를 지배하는데 "구성이 지배했다"고 부르면 `entry_exit_effect` 를 따로 만든 이유와
모순되기 때문이다. 아래가 그 경우다.

```
rate +2.0%p   mix +0.2%p   entry/exit −5.0%p   →   ΔR −2.8%p
```

rate 부호와 ΔR 부호가 다르지만 지배한 것은 구성이 아니라 진입/이탈이다.

**단일 상위 기여 그룹 요약 억제 조건:**

```
suppress_top_contributor  =  composition_dominant
                             OR NOT decomposition_complete
                             OR heavy_cancellation
                             OR |ΔR| ≤ SHARE_EPSILON
```

넷 모두 "이 그룹이 제일 나빴다" 는 요약이 오도한다 — 구성 이동이 지배했거나, 진입/이탈이 지배했거나,
내부 상쇄가 크거나, 전체 변화가 표시 가능한 크기보다 작기 때문이다. 억제되면 rate/mix/entry_exit
세 항을 분리해 제시한다.

`contribution_share` 노출 조건과 **같은 철학을 따른다.** share 는 숨기면서 상위 기여 문장은
살아남는 상태가 되면 두 정책이 어긋난다.

### `contribution_share` 노출 조건

파생값이며 **여섯을 모두 통과할 때만** 키가 존재한다. 하나라도 걸리면 `0` 이나 `null` 이 아니라 **키 부재**.

```
1. composition_dominant 아님
2. decomposition_complete 참
3. heavy_cancellation 아님
4. 해당 그룹 comparable
5. |ΔR| > SHARE_EPSILON
6. 0 < net_contribution_g / ΔR ≤ 1
```

2번이 붙는 이유는 위 억제 조건과 같다 — 진입/이탈이 변화의 일부를 차지하면 "전체의 N%" 가 오도한다.
5번은 안정성 조건이다.

6번이 **120% 구멍을 막는다.** 아래는 `heavy_cancellation`(0.20)에 걸리지 않는다:

```
A −0.012   B +0.002   →   ΔR −0.010
gross 0.014,  |ΔR|/gross = 0.714        ← 상쇄 기준 통과
A 의 share = 1.2                          ← 그런데 120%
```

전체 변화와 **같은 방향**으로 기여했고 그 몫이 전체를 넘지 않을 때만 share 를 보여준다.
그 외에는 signed `net_contribution` 만 보여준다. 분모가 표시 가능한 변화보다 작으면 share 가 극도로 불안정해진다. 통계적 유의성 기준이 아니라 **출력 규칙**이다.

non-comparable 그룹은 수학적으로 share 계산이 가능하지만 노출하지 않는다. `"새로 생긴 그룹이 하락의 45% 기여"` 는 rate deterioration 으로 오독된다. signed `net_contribution` 은 그대로 보여준다.

### 사전 등록 상수 (사례를 보고 조정하지 않는다)

| 상수 | 값 | 근거 |
|---|---|---|
| `CANCELLATION_THRESHOLD` | `0.20` | gross 의 80% 가 상쇄된 상태. 0.30 은 70% 상쇄부터 걸려 정상적인 이질 데이터에서도 자주 발동 |
| `SHARE_EPSILON` | `0.0001` | 표시 정밀도 0.01%p. 1bp 미만 변화에서는 비율 표현을 하지 않는다 |
| `CROSS_CELL_LIMIT` | `1000` | 교차 폭발 방지. 초과 시 생략 사실을 결과에 명시 |
| `FLOAT_TOL` | `1e-9` | 불변식 검사·`decomposition_complete` |
| `GROSS_EPSILON` | `1e-12` | `heavy_cancellation` 의 0 나눗셈 방어 |

### 단위와 nullable

- 저장은 분수(`0.045`, `-0.0044`). 퍼센트/퍼센트포인트 변환은 **렌더링에서만**.
- `delta` 가 원본, `relative_change` 는 **nullable 파생값**. `baseline = 0` 이면 delta 는 있어도 relative 는 없다. additive·ratio 둘 다 해당.

### 언어 규칙

`mix_effect` 는 **구성 변화가 전체 지표에 미친 수학적 효과**이지 원인이 아니다. v1 의 인과 어휘 가드를 재사용하고 전용 검사를 추가한다.

- 금지: `"고객 구성이 바뀌어서 전환율이 떨어졌다"`
- 허용: `"전환율이 낮은 그룹의 분모 점유가 늘면서 전체에 −0.4%p 의 구성 효과가 나타났다"`

---

## 6. 공용 산출물

```json
{
  "metric": { "name": "conversion_rate", "kind": "ratio" },
  "comparison": { "current": 0.045, "baseline": 0.050,
                  "delta": -0.005, "relative_change": -0.10 },
  "breakdowns": [
    {
      "dimensions": ["channel"], "cross": false,
      "groups": [
        { "key": {"channel": "paid"},
          "rate_effect": -0.0062, "mix_effect": 0.0018,
          "net_contribution": -0.0044, "comparable": true }
      ],
      "totals": { "total_rate_effect": 0.002, "total_mix_effect": -0.007,
                  "entry_exit_effect": 0.0, "gross_movement": 0.0110 },
      "ranking": { "by": "net_contribution", "groups": ["paid"] },
      "flags": { "composition_dominant": false, "simpson_strict": false,
                 "heavy_cancellation": false, "decomposition_complete": true },
      "non_comparable_groups": []
    }
  ]
}
```

additive 면 `rate_effect`/`mix_effect` 가 없고 `group_delta`·`net_contribution` 만 있다.

---

## 7. Integrity (일반화)

v1 의 기간 완결성·결측·정렬 구간 로직은 이미 도메인 무관이므로 그대로 쓴다. 바뀌는 것은 **중복 검사 키**뿐이다.

```
duplicate_key = domain.grain          (요청한 breakdown 이 아니라)
```

데이터 grain 이 `day × channel × device` 인데 `breakdown=[channel]` 만 요청했을 때, 요청 기준으로 검사하면 정상 행들이 중복으로 잘못 판정된다.

---

## 8. 도메인 3개

| | grain | dimensions | metrics |
|---|---|---|---|
| complaints | `day × product × complaint_type` | product, complaint_type | `complaint_count` (additive) |
| ecommerce | `day × channel × device × category` | channel, device, category | `revenue`, `orders` (additive), `conversion_rate` (ratio: orders/sessions) |
| support_ops | `day × queue × priority` | queue, priority | `tickets_received` (additive), `sla_resolution_rate` (ratio: `resolved_within_sla`/`received`, bounded) |

### support_ops 데이터 형상 (확정)

```
columns:  day, queue, priority, received, resolved_within_sla
day    =  ticket received cohort date        ← 해결일이 아니라 접수일
metric contract:  0 ≤ resolved_within_sla ≤ received
```

`day` 를 접수 cohort 로 고정하는 이유는, "오늘 해결 / 오늘 접수" 로 두면 동일 cohort 의 비율이 아니고
`resolved > received` 가 정상적으로 나올 수 있어 비율 의미론 검증이라는 목적과 어긋나기 때문이다.
세 번째 도메인은 다양성을 보이기 위한 것이지 운영 모델을 재현하려는 것이 아니다.

**complaints 는 `data/scenarios/S01~S24` 를 바이트 그대로 유지하고 spec 만 얹는다.** 새 포맷으로 재생성하거나 변환본을 만들면 regression gate 가 약해진다.

---

## 9. 벤치마크 — 18 사례 (데이터 생성 전에 고정)

| # | 묶음 | 사례 |
|---|---|---|
| 1 | 비율 | 가중 집계 ≠ 단순 평균 (`mean(r_g)` vs `ΣN/ΣD`) |
| 2 | 비율 | `composition_dominant` — Σrate_effect 부호가 ΔR 과 반대 |
| 3 | 비율 | 엄격 Simpson — comparable 그룹 전부 Δr 동일 부호, 전체는 반대 |
| 4 | 비율 | 진입 그룹(`D0=0`)이 전체를 끌어내림 → `entry_exit_effect` 지배 |
| 5 | 비율 | 이탈 그룹(`D1=0`) |
| 6 | 비율 | `heavy_cancellation` — 상쇄비 0.20 미만 |
| 7 | 비율 | 순변화 극소(`|ΔR| < SHARE_EPSILON`) + gross 큼 → share 억제 |
| 8 | 비율 | 진입 + 구성 이동 동시 → 세 항이 모두 0 이 아님 |
| 9 | 거부 | `D_g = 0` 인데 `N_g > 0` → integrity 거부 |
| 10 | 거부 | 전체 `D = 0` → 분석 중단 |
| 11 | 무결성 | dimension NULL → `__UNKNOWN__`, partition 보존 |
| 12 | 무결성 | grain 중복 행 → 거부 (요청 breakdown 과 무관) |
| 13 | 경계 | 교차 셀 상한 초과 → `omitted` + 사유 |
| 14 | 경계 | `rank_by` 가 metric kind 와 불일치 → 컴파일 거부 |
| 15 | 경계 | 부분 기간 × 비율 — 분자·분모가 같은 정렬 구간에서 나오는가 |
| 16 | 회귀 | complaints — v1 과 동일 산출 |
| 17 | 회귀 | ecommerce `revenue` 단순 증가 **+ 신규 category 등장** (additive 그룹 진입) |
| 18 | 파생 | `baseline metric = 0`, 분모는 정상 → `delta` 계산, `relative_change` 없음 |

18개를 넘기지 않는다. 도메인 4개째를 만들지 않는다. 새 metric kind 를 넣지 않는다.

---

## 10. Oracle

- 생성기는 `bia.analysis` 를 **import 하지 않는다.** 분해 수식을 oracle 쪽에 독립적으로 재진술한다.
- 벤치마크 데이터는 **손으로 검산 가능한 작은 정수**로 설계하고, oracle 은 `Fraction` 으로 **exact arithmetic** 계산한다. production 엔진의 float 처리나 집계 버그를 oracle 이 똑같이 복제하지 않게 하려는 것이다.
- 핵심 사례(Simpson, 진입/이탈, cancellation)는 **생성기조차 계산하지 않고 명시적 golden value 를 박아둔다.**
- 검사 스크립트는 **디스크에 쓰인 파일에서 다시 계산**해 oracle 과 대조한다 (v1 `check_datagen.py` 방식).

### oracle 구조 — breakdown/group-local

```json
{
  "expect_delta": -0.005,
  "expect_relative_change": -0.10,
  "breakdowns": {
    "channel": {
      "expect_total_rate_effect": 0.002,
      "expect_total_mix_effect": -0.007,
      "expect_entry_exit_effect": 0,
      "expect_flags": { "composition_dominant": true, "simpson_strict": true,
                        "heavy_cancellation": false, "decomposition_complete": true },
      "expect_non_comparable_groups": [],
      "groups": {
        "paid": { "expect_net_contribution": -0.004, "expect_share_exposed": false }
      }
    },
    "device": { "...": "..." }
  }
}
```

`expect_share_exposed` 는 사례 전체가 아니라 **group-local** 이다.

거부 사례(#9·#10·#12·#14)는 `expect_refused` 와 **실패 단계·사유**를 기록한다.

---

## 11. 합격 기준

**통과해야 하는 것**

1. **v1 semantic regression 0** — v0 24개 시나리오의 채점 결과(Q-1~Q-7, S-1~S-4)와 v1 8개 사례의 `heuristic`·`greedy` 결과가 의미적으로 동일. **JEV 는 재실행하지 않는다** (historical).
2. 세 도메인이 같은 엔진 사용. **`analysis/` 안에 도메인 이름 검사 코드 0** — AST 로 테스트한다.
3. 18개 사례 전부 oracle 일치.
4. **모든 분석 성공 사례에서 적용 가능한 불변식이 성립하고, 거부 사례는 지정된 단계와 사유로 실패한다.** (거부 사례에는 애초에 `StructuredAnalysisResult` 가 없어야 한다.)

**기록만 하는 것** — 도메인별 실행 시간, 사례 수, 교차 셀 수.

---

## 12. 하지 않을 것

```
자유 질문 해석           임의 CSV schema 이해      metric 자동 발명
dimension 자동 탐지      JEV planner              semantic retrieval
비정형 evidence 일반화   PDF                      multi-agent
범용 analytics platform  연산자 7번째              도메인 4번째
새 metric kind (누적·이동평균 등)
```

evidence·retrieval·verify·answer 코드는 **일반화하지 않는다.** v3 의 주제다.

## 13. 미결정

없음. 설계 단계에서 열어두었던 두 항목(support_ops 데이터 형상, `OTHER` 버킷 발동 시점)은
각각 §8 과 §4 에서 확정했다.

## 14. 벤치마크 영향

위 출력 정책 수정들은 **19번째 사례를 필요로 하지 않는다.** 기존 사례에 단언을 더 붙인다:

- #6 cancellation → `suppress_top_contributor` 가 참인지
- #4·#8 진입/이탈 → `composition_dominant` 가 **거짓**인지 (진입/이탈이 지배하므로)
- #6 또는 #7 안에 share 가 1 을 넘는 구성을 포함해 노출 조건 6 을 발동시킨다
