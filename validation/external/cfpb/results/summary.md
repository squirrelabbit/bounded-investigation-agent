# CFPB 외부 검증 — 실행 요약

run.py 가 생성한다. 손으로 고치지 않는다. 사전등록: `../preregistration.md`.

- HEAD: `7ad1a2ea6c4b6dc64d73c888c7a8b8e221795c27`
- 범위 가드(`git diff main -- bia data eval tests scripts` 비어 있음): True
- oracle 프로세스에 로드된 bia 모듈 수: 0
- 파생 파일 해시 = derivation.json 기록: True

## 예측 P1~P7

| # | 적중 | 관측 |
|---|---|---|
| P1 | 적중 | `{"runs": ["E1/complaint_count", "E1/timely_rate", "E2/complaint_count", "E2/timely_rate"], "refused": []}` |
| P2 | 적중 | `{"status": "ok", "cells": 130}` |
| P3 | 적중 | `{"flags": {"decomposition_complete": true, "composition_dominant": false, "simpson_strict": false, "heavy_cancellation": true, "suppress_top_contributor": true}, "non_comparable_groups": [], "entry_exit_effect": 0.0}` |
| P4 | 적중 | `{"flags": {"decomposition_complete": false, "composition_dominant": false, "simpson_strict": false, "heavy_cancellation": true, "suppress_top_contributor": true}, "non_comparable_group_count": 14, "entry_exit_effect": -0…` |
| P5 | 빗나감 | `{"flags": {"decomposition_complete": true, "heavy_cancellation": true, "suppress_top_contributor": true}, "warnings": ["heavy_cancellation", "suppress_top_contributor"], "products_entered": ["Credit card or prepaid card"…` |
| P6 | 적중 | `{"status": "omitted", "reason": "cross_cell_limit_exceeded", "observed_cells": 3629}` |
| P7 | 적중 | `{"status": "ok", "groups": 1900}` |

## 합격 기준 A1~A4

| # | 결과 | 근거 |
|---|---|---|
| A1 | 통과 | 정수 7569 · 실수 4398 비교, 불일치 0, 최대 실수 차 2.22e-15 |
| A2 | 통과 | 플래그 73 비교, 불일치 0, 모호 제외 0 |
| A3 | 통과 | 교차 분해 5, 규칙 일치 5 |
| A4 | 통과 | 채점 20, 기대 일치 17, 불일치 3, 방어 실패 0 (-), 무효 0 |

## 손상 M1~M14

| # | 지표 | 적용 행 | 기대 | 실제 | 일치 | 방어 실패 |
|---|---|---|---|---|---|---|
| M1 | loader | 1805394 | reject | reject | 예 | - |
| M2 | complaint_count | 426 | valid-correct | valid-correct | 예 | - |
| M2 | timely_rate | 426 | valid-correct | valid-correct | 예 | - |
| M3 | complaint_count | 43 | reject | reject | 예 | - |
| M4 | complaint_count | 632 | valid-silent-wrong | valid-silent-wrong | 예 | - |
| M4 | timely_rate | 632 | caveat | caveat | 예 | - |
| M5 | complaint_count | 227 | valid-silent-wrong | caveat | 아니오 | - |
| M5 | timely_rate | 227 | valid-silent-wrong | valid-silent-wrong | 예 | - |
| M6 | complaint_count | 2154 | valid-silent-wrong | valid-silent-wrong | 예 | - |
| M6 | timely_rate | 2154 | valid-silent-wrong | valid-silent-wrong | 예 | - |
| M7 | timely_rate | 690 | reject | reject | 예 | - |
| M8 | timely_rate | 690 | reject | reject | 예 | - |
| M9 | complaint_count | 1 | reject | reject | 예 | - |
| M10 | complaint_count | 1 | reject | reject | 예 | - |
| M11 | complaint_count | 213 | valid-correct | reject | 아니오 | - |
| M11 | timely_rate | 213 | valid-correct | reject | 아니오 | - |
| M12 | complaint_count | 4257 | reject | reject | 예 | - |
| M12 | timely_rate | 4257 | reject | reject | 예 | - |
| M13 | complaint_count | 1 | reject | reject | 예 | - |
| M14 | timely_rate | 2103 | reject | reject | 예 | - |

### 채점하지 않은 보조 실행

| # | 지표 | 실제 |
|---|---|---|
| M3 | timely_rate | valid-correct |
| M7 | complaint_count | valid-correct |
| M8 | complaint_count | valid-correct |
| M9 | timely_rate | valid-correct |
| M10 | timely_rate | valid-correct |
| M13 | timely_rate | valid-correct |
| M14 | complaint_count | valid-correct |
