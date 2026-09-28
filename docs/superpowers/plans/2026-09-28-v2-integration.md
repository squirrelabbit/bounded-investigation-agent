# v2 통합 계획 — 검증된 병렬 코어를 제품 경로의 기준으로 올린다

**브랜치:** `feat/v2-integration` (기준 `346451c` = origin/main)
**목표:** 제품 경로(`cli → controller → evidence → answer`)의 정형 계산을 v1 `bia/metrics.py` 에서
v2 공용 엔진으로 바꾼다. **기능을 늘리지 않는다.** 바꾼 뒤에도 v1 제품 동작은 그대로여야 한다.

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
| C | seam 뒤 구현을 v2 엔진 + complaints 어댑터로 교체 | 없음이어야 함 (여기서 처음 이행이 일어남) |
| D | v2 출력 정책(`suppress_top_contributor` 등)을 최종 답변 문장까지 연결 | 의도된 변화 |

## seam 계약 (Commit B 에서 확정)

`bia/complaint_analysis.py` 의 `ComplaintAnalysis` Protocol. 멤버는 제품 코드가 실제로 읽는 것만:
`current_total`, `baseline_total`, `delta`, `pct_change`, `by_product`, `by_complaint_type`, `cells`,
`increased`, `top(dimension)`, `as_dict()`. 값 타입 `GroupDelta`·`CellDelta` 는 `bia/types.py` 의 공용
타입이라 계약이 어느 엔진에도 의존하지 않는다.

이 저장소에는 타입 검사기가 없으므로 Protocol 만으로는 아무것도 강제되지 않는다. 두 AST 테스트로 닫는다:
① `bia.metrics` 를 import 하는 제품 모듈은 `bia/complaint_analysis.py` 하나뿐 ② `EvidenceState.metrics`
가 흘러가는 경로에서 읽는 속성은 위 목록의 부분집합.

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
