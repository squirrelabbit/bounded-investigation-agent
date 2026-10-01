# v2.0.0 이후 backlog

`v2.0.0`(`76bdb1f`)과 CFPB 외부 검증(`validation/external/cfpb/`) 이후 남은 과제다. 처음 작성 때는 구현하지 않기로 했고, 이 중 일부는 v2.1(Structured Data Qualification)이 구현했다(아래 표의 v2.1 표시).
이 문서는 무엇을 알아채고 무엇을 못 알아채는지를 정확히 적어 두고, 다음에 무엇부터 할지 순서만 정한다.

근거: 외부 검증의 봉인된 결과 `validation/external/cfpb/results/`(`78576a9`)와 사후 해석
[`findings.md`](../validation/external/cfpb/findings.md), 통합 계획 [`plans/2026-09-28-v2-integration.md`](superpowers/plans/2026-09-28-v2-integration.md).

## 무엇을 알아채고, 무엇을 못 알아채는가

| 상황 | 엔진이 알아채나 | 실제 동작 | 해석 |
|---|---|---|---|
| 그룹별 증감은 큰데 전체 변화는 작음 | O | `heavy_cancellation` | 수치적 상쇄는 감지한다 |
| 상쇄가 커서 상위 기여 그룹을 믿기 어려움 | O | `suppress_top_contributor` → 답변이 기여율을 숨김 | 출력 억제까지 이어진다 |
| 비율 지표의 구성 역전 | O | `composition_dominant`·`simpson_strict` | 수학적 역전 패턴을 감지한다 |
| 그룹의 진입·이탈 — **비율 지표** | O | `decomposition_complete = False`, `entry_exit_effect`, 비교 불가 그룹 목록 | 직접 감지한다. CFPB E2 제품 분해: 비교 불가 14 개, `entry_exit_effect` −0.0243 |
| 그룹의 진입·이탈 — **합 지표** | 사실만 O (v2.1) | 합 분해에 `group_transition`(entered·exited·persisted·inactive) 사실이 실린다. 답변·억제에는 연결되지 않는다 | 사실로 관측할 뿐 경고·억제는 하지 않는다. v2.0 에서는 순변화가 이동량에 비해 작을 때만 상쇄 경고가 **우연히** 섰다(CFPB E2) |
| 카테고리 이름 변경 | 비율 O / 합 X | 비율은 진입·이탈로 감지, 합은 다른 그룹으로 조용히 취급 | 둘 다 "같은 범주의 다른 이름" 이라는 의미는 모른다 |
| 분류 체계 개편(taxonomy change) 자체 | X | 위 두 줄의 결과로 나타난다 | 개편을 이해하는 것이 아니다 |
| 기간 일부 누락 — **엔진 직접 사용** | O (v2.1) | `execute`/`qualify` 가 도메인의 `partial_period_policy` 에 따라 `incomplete_period_coverage` 로 거부하거나(`reject`) 공통 구간으로 정렬한다(`align_common_window`) | v2.0 에서는 조용히 적게 셌다(CFPB M5: 전체 증감 +15,872 → −1,276, 전체·제품 분해엔 경고 없음) |
| 기간 일부 누락 — **제품 경로(complaints)** | O | controller 가 기간 완결성을 검사한다 | 엔진 앞 계층의 방어 |
| 날짜 이동 — 엔진 직접 사용 | 일부 (v2.1) | 이동으로 요청 구간 안에 빠진 날이 생길 때만 기간 판정이 감지한다. 빠진 날 없이 구간 경계를 넘는 행은 조용히 빠진다 | 날짜 무결성 검사 자체는 없다 |
| 빈 차원값 | 정책에 따라 (v2.1) | `load_frame` 입력에서는 도메인의 `null_dimension_policy` 를 따른다: `reject` 면 `missing_required_value` 로 거부, `unknown_group` 이면 `__UNKNOWN__` 그룹으로 모이고 같은 grain 에서 충돌하면 거부하며 `caused_by_null_normalization` 사실을 남긴다. 제품 경로(`store.load_metric_rows`)에서는 빈 값이 `""` 그대로 남고 이 정책이 적용되지 않는다 | 결측의 의미를 이해한다기보다 구조 충돌로 fail-closed |
| grain 중복 · 충돌 | O | 완전 중복은 합치고, 충돌은 거부 | 명확한 구조 방어 |
| 정수 아님 · 음수 · 필수 열 누락 · 분모 0 · 분자 > 분모 | O | 거부 | 입력 계약 |
| 교차 셀 1000 초과 | O | 교차 분해 생략(단일 차원은 계산) | 사전 등록 상한 |
| 산술 오류 | O | 독립 oracle 로 검증 가능 | CFPB 에서도 불일치 0 |
| 분석이 비즈니스적으로 맞는가 | X | 판단하지 않는다 | 주장 범위 밖 |

"우연히" 는 그 원인을 알아챈 것이 아니라, 원인이 만든 **다른 수치 패턴** 때문에 경고가 설 수 있다는 뜻이다.
이런 경고를 방어 성공으로 세면 방어 능력을 실제보다 높게 보게 된다.

## 우선순위

| 순위 | 과제 | 이유 |
|---|---|---|
| P1 | 손상 채점에서 **직접 방어** 와 **우연한 경고** 분리 | 지금도 증거의 해석에 영향을 준다. 현재 규칙은 "관련 플래그가 하나라도 서면 caveat" 라 CFPB M5 처럼 우연한 경고를 방어로 센다 |
| 선행 조건 — **완료(v2.1)** | 엔진 직접 사용 경로의 **기간 완결성 계약** | 누락은 결과 전체를 왜곡한다. 다만 지금 controller 없이 엔진을 쓰는 곳은 벤치마크와 외부 검증 하네스뿐이라, **노출된 제품 경로는 없다.** 도메인을 제품 경로에 하나 더 올리기 전에 반드시 한다 |
| 선행 조건 | **날짜 무결성·구간 경계** 검증 | 위와 같다 |
| P2 — **완료(v2.1, 사실만)** | 합 지표의 **진입·이탈 구조 신호** | 새 개념이 아니라 비율 지표에 이미 있는 인식을 합 지표로 옮기는 것이다. 아래 참고 |
| P2 — **검토 완료(v2.1, 순서 유지)** | `__UNKNOWN__` 치환과 grain 검사의 **순서** 재검토 | fail-closed 라 안전하지만, 같은 grain 에 빈 값이 둘 이상인 실데이터는 분석 자체가 막힌다. v2.1 은 순서를 바꾸지 않았다(로드 시 치환 유지 — 뒤로 미루면 문자 그대로 `"__UNKNOWN__"` 인 값과 빈칸이 다른 키가 된다). 대신 `Observation.null_dimensions` 로 빈칸 출처를 보존해 `caused_by_null_normalization` 사실을 남기고, 도메인이 `null_dimension_policy = reject` 를 선언할 수 있게 했다 |
| P3 | 분류 개편·이름 변경의 **의미** 감지 | 수학만으로 안 된다. 이름 대응표, 버전 붙은 분류 체계, 외부 메타데이터 같은 도메인 지식이 필요하다. 지금의 "정형 계산 계층 일반화" 범위를 넘는다 |

### P2 진입·이탈 신호의 모양

원인을 추측하지 않고 관측 가능한 사실만 싣는다. 합 분해에:

```text
group_transition:
  entered: 5      # 기준 구간에 없고 현재 구간에 있는 그룹
  exited: 9       # 기준 구간에 있고 현재 구간에 없는 그룹
  persisted: …    # 두 구간 모두 있는 그룹
```

(숫자는 CFPB E2 제품 분해의 예.) 이것으로 엔진은 "분류 개편입니다" 라고 과하게 주장하지 않으면서도 "비교 대상 그룹
구성이 크게 바뀌었다" 고 말할 근거를 갖는다. 답변에 싣기 전에, 비율 지표처럼 억제 정책과 어떻게 연결할지 먼저 정한다.

## v2 통합에서 남긴 품질 과제

- 단일 차원 분해가 없거나 생략되면 호환 오류가 아니라 `ValueError` — 같은 오류 계열로 맞춘다(지금은 도달 불가)
- seam 이 비공개 헬퍼 `integrity._row_to_observation` 을 쓴다 — 공개 이름을 줄지 결정
- seam 테스트의 하드코딩된 줄 번호
- 연산자 폐쇄 테스트의 범위가 분석 핵심 세 모듈뿐
- 26 사례 벤치마크가 교차 분해의 tie-break 규칙(선언 순서 vs 알파벳순)을 구분하지 못한다 — 역전 동률 사례가 없다
- checker 가 `data/v2` 를 통째로 재생성하는 동안 트리가 비는 구간이 있다 — 테스트와 병렬로 돌리지 않는다
- 억제 문자열 가드는 위치 문장만 본다(1 차 방어는 구조 spy)
- 측정값 음수 금지 계약이 로드 경로 셋 중 둘에만 있다(`Frame` 로드, 외부 데이터). 번들 시나리오 경로(`store.py` →
  `_row_to_observation`)에는 없다. v2.1 의 `group_transition` 은 `!= 0` 으로 정의해 이 계약에 기대지 않는다.
- grain 라벨 `"|".join(key)` 가 모호하다: 서로 다른 키 `("a|b","c")` 와 `("a","b|c")` 가 같은 라벨을 갖는다. v2.1 이전부터 있던 성질이고, legacy 바이트 형식이 이 라벨을 쓰므로 바로 바꿀 수 없다(판정 자체는 튜플 키로 한다)
- 제품 경로의 빈 차원값: `store.load_metric_rows` → `_row_to_observation` 경로에서 빈 product 는 `""` 그대로 남고 `null_dimensions` 도 없다. 그래서 `null_dimension_policy` 가 적용되지 않고, 빈칸 두 행이 같은 grain 이면 `caused_by_null_normalization = False` 인 충돌, 빈칸 한 행은 `""` 그룹이 된다(`tests/test_qualification.py` `ProductPathBlankDimensionTests` 가 고정)
- **External-validation artifact determinism:** 환경 의존적인 절대경로·임시경로·시각 등의 diagnostic metadata는 결과 artifact 직렬화 전에 안정적인 placeholder로 정규화한다. 의미 판정과 진단 원문이 필요하면 별도 execution log에 보존한다. (근거: CFPB 재현에서 `mutations.json` 만 `tempfile.mkdtemp` 경로 때문에 바이트가 달랐다 — [`validation/external/cfpb/README.md`](../validation/external/cfpb/README.md) 재현 절)
