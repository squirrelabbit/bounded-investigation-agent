# v2 통합 계획 — 검증된 병렬 코어를 제품 경로의 기준으로 올린다

**브랜치:** `feat/v2-integration` (기준 `346451c` = origin/main)
**목표:** 제품 경로(`cli → controller → evidence → answer`)의 정형 계산을 v1 `bia/metrics.py` 에서
v2 공용 엔진으로 바꾼다. **기능을 늘리지 않는다.** 바꾼 뒤에도 v1 제품 동작은 그대로여야 한다.

## 출발 상태 (정확히)

"v2 는 제품 경로에 없다" 는 **엔진에 대해서만** 참이다. `bia/integrity.py:13` 이 `bia.analysis.frame` 의
`Observation`·`observation_key` 를 쓴다(`232554d`, grain 기준 중복 검사). 그래서 `bia.cli` 를 import 하면
`bia.analysis`, `.errors`, `.frame`, `.spec` 이 로드되고, **compiler·engine·operators·decompose 는 로드되지 않는다.**
Commit C 는 그 넷이 제품 경로에 처음 로드되는 커밋이다. "v2 import 0" 류의 조건은 이 구분으로 적어야 한다.

## 원칙

- 커밋 넷을 섞지 않는다. 문제가 생겼을 때 원인이 경계 추가인지 엔진 교체인지 바로 갈라야 한다.
- `bia/metrics.py` 는 제품 실행 경로에서 빠지되, **v1 회귀 기준(legacy oracle)으로 계속 남는다.**
  지우면 v2 회귀 테스트가 v2 를 v2 로 비교하는 순환이 된다.
- controller 는 complaints 조사 흐름만 안다. v2 내부 표현(`StructuredAnalysisResult`)을 보지 않는다.

## 커밋 경계

| 커밋 | 범위 | 제품 동작 변화 |
|---|---|---|
| A | tie-break 결정성 — canonical = 선언된 차원 순서 | 없음 (`3897262`) |
| B | seam 도입: `ComplaintAnalysis` 계약 + `analyze_complaints()`. **구현은 여전히 v1** | 없음 |
| C0 | 계약을 먼저 닫는다: 합 분해 `GroupResult` 에 `current_value`·`baseline_value` 보존 + 독립 oracle 확장, complaints 교차 한도 호환 계약 고정. **제품 실행은 여전히 v1** | 없음 |
| C | `analyze_complaints()` 구현만 v1 → v2 로 교체. 어댑터가 v2 결과로 호환 뷰를 만든다 | 지원 corpus·벤치마크에서는 없음. 교차 셀 1000 초과 외부 입력에만 새 실패 계약 |
| D | v2 출력 정책(`suppress_top_contributor` 등)을 최종 답변 문장까지 연결 | 의도된 변화 |

## seam 계약 (Commit B 에서 확정)

`bia/complaint_analysis.py` 의 `ComplaintAnalysis` Protocol. 멤버는 제품 코드가 실제로 읽는 것만:
`current_total`, `baseline_total`, `delta`, `pct_change`, `by_product`, `by_complaint_type`, `cells`,
`increased`, `top(dimension)`, `as_dict()`. 값 타입 `GroupDelta`·`CellDelta` 는 `bia/types.py` 의 공용
타입이라 계약이 어느 엔진에도 의존하지 않는다.

이 저장소에는 타입 검사기가 없으므로 Protocol 만으로는 아무것도 강제되지 않는다. 두 AST 테스트로 닫는다:
① `bia.metrics` 를 import 하는 제품 모듈은 `bia/complaint_analysis.py` 하나뿐 ② `EvidenceState.metrics`
가 흘러가는 경로에서 읽는 속성은 위 목록의 부분집합.

## Commit C0 계약

**`current_value`·`baseline_value`** — 합 분해 그룹의 그 metric 값(해당 기간 합계). 엔진이 이미 계산하고
버리던 값을 보존한다. **비율 분해에는 싣지 않는다** — 비율에서 "그룹의 값" 은 rate·분자·분모 중 무엇인지
모호하다. 벤치마크의 `EXPECTED_GROUP_FIELDS` 가 (kind, comparable) 별 정확한 집합 일치를 요구하므로
이 경계는 기계적으로 강제된다. 어댑터가 관측치를 다시 합산하는 방식은 쓰지 않는다 — 그 순간 "계산은 v2
엔진이 한다" 가 그룹 값에 대해 거짓이 된다.

**oracle 확장 허용 범위** — `data/v2/oracle.json` 의 기존 key/value 는 모두 불변. 합 분해 그룹의
`expect_current_value`·`expect_baseline_value` 추가만 허용한다. 새 필드는 엔진을 보지 않는 독립 계산으로
검증하고, 세 도메인 모두에서 검증한다.

**교차 한도 호환 계약** — 엔진은 교차 셀이 `CROSS_CELL_LIMIT`(1000)을 넘으면 정상적으로
`status=omitted, reason=cross_cell_limit_exceeded` 를 낸다. 이것은 엔진 실패가 아니므로 `AnalysisRefused` 로
위장하지 않는다. 문제는 complaints 호환 뷰가 `cells` 를 필수로 요구한다는 것이므로 어댑터 경계에서
`ComplaintAnalysisCompatibilityError(reason=required_joint_breakdown_omitted, cause=cross_cell_limit_exceeded,
observed_cells, limit)` 로 구분한다 — "공용 분석은 성공했지만 complaints 호환 뷰를 만들 수 없다". 조용히
빈 `cells` 를 돌려주지 않고, `metrics.compute` 로 fallback 하지 않는다. **v1 대비 의도한 호환 변화다**
(v1 은 셀 상한이 없다). 그래서 C 를 "모든 입력에 대한 완전한 동작 보존 교체" 라고 부르지 않는다.

## Commit C 필수 acceptance (지금 고정해 둔다)

**A. v1 순서 재현** — `by_product` / `by_complaint_type` / `cells` / `top()` 의 순서가 v1 과 같다.
`top()` 은 그룹을 들어온 순서대로 누적하므로(`metrics.top_contributors`) 목록 순서가 곧 결과다.

**B. v1 호환 지표 재현** — `share_of_increase` 는 **v1 의미로 다시 계산한다**: 늘어난 그룹들의 합 대비
비율. v2 의 `contribution_share`(순기여 ÷ Δ, 상쇄 시 억제)는 **다른 양이므로 재사용하지 않는다.**
이름이 비슷해 섞이기 쉽다.

**C. 직렬화 재현** — `as_dict()` → `EvidenceState.view()` → `jev.serialize_state()` 로 이어지는 JEV 입력
문자열이, 현재 fixture 전체에서 **바이트 동일**하다. "의미 동일" 로 완화하지 않는다 — 동결된 JEV 실험의
입력 계약이다.

## Commit D 필수 acceptance

composition-dominant 등 억제 대상 사례를 **끝까지 실행해 답변을 만든 뒤**, `claim_text()`(인용 구간 제거)
이후 텍스트에 단일 기여자 서술이 없음을 검사한다. 새 금칙어 층을 옆에 두지 않고 기존 숫자 가드·인과 어휘
가드와 같은 경로에 붙인다. 플래그만 보는 테스트는 v2 에서 이미 있다 — 이번에 잡을 것은 렌더링 계층이
플래그를 무시하는 경우다.

## 알려진 공백 (후속)

- 26 사례 벤치마크는 선언 순서와 알파벳순 tie-break 을 구분하지 못한다(교차 분해에 역전 동률 사례가 없다).
  계약은 `tests/test_analysis_rank.py` 가 지킨다.
- checker 가 `data/v2` 를 통째로 재생성하는 동안 트리가 비는 구간이 있어, 테스트와 병렬 실행하면 안 된다.
