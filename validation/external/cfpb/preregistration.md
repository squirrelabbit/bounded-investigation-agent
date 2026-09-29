# CFPB 외부 검증 — 사전등록

**확정일:** 2026-09-29, **데이터를 받기 전.** 이 파일이 들어간 커밋이 데이터 획득 커밋보다 앞선다는 것이
git 이력으로 증명된다. 실행 후에는 이 파일을 고치지 않는다. 바꿔야 할 게 생기면 아래 "개정" 절에 날짜·이유와 함께
덧붙이고, 원문은 남긴다.

**대상:** `v2.0.0` (`76bdb1f`) 의 공용 분석 엔진 `bia/analysis/`.

## 1. 무엇을 증명하고, 무엇을 증명하지 않는가

**증명하는 것.** 외부에서 생성된 데이터셋에 대해 입력 계약, 데이터 품질 방어, 연산 산술, 정책 발동이
사전 정의된 규칙대로 동작하는지. 즉 external-shape validation + robustness validation.

**증명하지 않는 것.** "현실에서 분석 결과가 맞다" 는 것. CFPB 데이터에는 기여도의 정답이 없다. 독립 oracle 이
보이는 것은 산술이 규칙대로라는 것뿐이다. 이 결과를 production validity 의 근거로 쓰지 않는다.

**실패도 결과다.** 엔진이 예상과 다르게 거부하거나 조용히 틀린 값을 내면 그것은 발견된 외부 제약이다. 이 브랜치에서
엔진·제품 코드를 고쳐 결과를 맞추지 않는다. 결과를 먼저 봉인하고, 수정 여부는 post-release 과제로 넘긴다.

## 2. 범위 규칙

- 코드와 산출물은 `validation/external/cfpb/` 안에만 둔다. `bia/`, `data/`, `eval/`, `tests/`, `scripts/`,
  README 는 바꾸지 않는다. 실행 스크립트는 시작할 때 `git diff main -- bia data eval tests scripts` 가 비어 있음을
  확인하고, 비어 있지 않으면 멈춘다.
- 외부 데이터용 `DomainSpec` 은 `bia/domains/` 에 두지 않는다(v2 스펙의 "도메인 4개째를 만들지 않는다"). 이 디렉터리
  안에서 실행 시점에만 레지스트리에 등록한다.
- 엔진은 공개 진입점으로만 쓴다: `compile_request`, `run_plan`, `load_observations`, `registry.register`.

## 3. 데이터 획득

- 출처: CFPB Consumer Complaint Database 검색 API
  `https://www.consumerfinance.gov/data-research/consumer-complaints/search/api/v1/`,
  `format=csv&no_aggs=true&date_received_min=…&date_received_max=…`. 미국 정부 공개 데이터.
- `format=csv` 는 `frm`/`size` 를 무시하고 필터 결과 전체를 준다. API 에 열 선택 기능은 없다.
- **쓸 열만 남긴다(allowlist):** `Date received`, `Product`, `Issue`, `Company`, `Timely response?`, `Complaint ID`.
  나머지 열 — 소비자 서술문, ZIP, 주, 태그 등 — 은 **스트림으로 읽는 즉시 메모리에서 버린다.** 원본 CSV 는 저장소 밖
  임시 경로에만 둔다. 원본 행과 서술문은 저장소에 절대 쓰지 않는다.
- `date_received_max` 의 포함 여부가 문서로 확정되지 않았다. 요청은 구간보다 하루씩 넓게 하고, 로더가 정확한 구간으로
  다시 자른다.
- 기록: 요청 URL, 받은 시각(UTC), 원본 바이트 SHA-256, 원본 행 수. **CCDB 는 계속 갱신되는 데이터베이스다** — 같은
  구간을 나중에 받으면 바이트가 달라질 수 있다. 재현성은 "받은 시점의 해시 + 저장소에 고정한 파생 집계 CSV" 로 약속한다.
- 파생 파일이 5 MB 를 넘으면 커밋하지 않고 SHA-256 과 행 수만 기록한다.

## 4. 로더 (원본 → 파생 집계)

- `Complaint ID` 가 중복이면 **거부**한다(원본 계층의 유일 키).
- `Date received` 형식이 예상 밖이면 추측하지 않고 **거부**한다.
- `Timely response?`: 분자 `timely_yes` = `Yes` 인 건수, 분모 `timely_known` = `Yes` 또는 `No` 인 건수. 빈 값은 분모에서
  빼되 **건수를 따로 보고**한다. 조용히 버리지 않는다.
- `Product`·`Issue` 가 빈 값이면 그대로 넘긴다(엔진이 `__UNKNOWN__` 으로 바꾼다 — 그 정책 자체가 검증 대상).
- 파생 파일 둘:
  - `derived/product_issue.csv` — grain `(day, product, issue)`, 열 `complaints, timely_yes, timely_known`
  - `derived/company_product.csv` — grain `(day, company, product)`, 열 `complaints` (회사명은 공개 법인명)

## 5. 도메인 선언 (이 디렉터리 전용)

- `cfpb_product_issue`: grain `(day, product, issue)`, dimensions `(product, issue)`,
  metric `complaint_count` = additive(`complaints`),
  metric `timely_rate` = ratio(`timely_yes` / `timely_known`, `numerator_bounded_by_denominator=True`)
- `cfpb_company_product`: grain `(day, company, product)`, dimensions `(company, product)`,
  metric `complaint_count` = additive(`complaints`)

## 6. 실험

| | 기준 구간 | 현재 구간 | 도메인 | 지표 |
|---|---|---|---|---|
| E1 안정 구간 | 2024-02-01..2024-02-28 | 2024-03-01..2024-03-28 | product_issue | count, timely_rate |
| E2 분류 개편 | 2017-03-27..2017-04-23 | 2017-04-24..2017-05-21 | product_issue | count, timely_rate |
| E3 회사 교차 | 2024-02-01..2024-02-28 | 2024-03-01..2024-03-28 | company_product | count |

두 구간은 모두 28일로 같다. E2 의 경계는 CFPB 가 제품·이슈 분류를 바꾼 날짜 2017-04-24 (CFPB "Summary of product and
sub-product changes, Effective Date: April 24, 2017").

## 7. 예측 (빗나가도 합격 여부와 무관, 적중·오답을 그대로 기록)

| # | 예측 | 확신 |
|---|---|---|
| P1 | E1·E2 의 깨끗한 파생 데이터에서 엔진은 거부하지 않는다 | 높음 |
| P2 | E1 product × issue 교차 셀은 1000 이하라 계산된다 | 낮음 |
| P3 | E1 timely_rate 의 product 분해는 `decomposition_complete = True` (두 달 사이 제품이 들고 나지 않음) | 중간 |
| P4 | E2 timely_rate 의 product 분해는 `decomposition_complete = False` 이고 따라서 `suppress_top_contributor = True` (개편으로 제품이 들고 남) | 높음 |
| P5 | E2 complaint_count(합 지표)는 **어떤 플래그도 개편을 알리지 않는다** — 합 분해에는 진입/이탈 개념이 없어, 사라진 제품과 새 제품이 각각 음·양의 그룹 증감으로만 나타난다 | 높음 |
| P6 | E3 company × product 교차는 셀이 1000 을 넘어 `status = omitted, reason = cross_cell_limit_exceeded` | 높음 |
| P7 | E3 company 단일 차원 분해는 수천 그룹이어도 생략되지 않고 계산된다(상한은 교차에만 걸림) | 높음 |

P5 가 맞으면 그것은 **발견된 한계**다: 분류 개편 같은 외부 구조 변화가 합 지표에서는 기계가 읽을 수 있는 신호 없이
지나간다. 고치지 않고 기록한다.

## 8. 독립 oracle

- `oracle.py` 는 `bia` 의 어떤 모듈도 import 하지 않는다(표준 라이브러리 + `fractions`). 실행 스크립트가 별도 프로세스의
  `sys.modules` 로 이를 확인한다.
- 파생 CSV 를 직접 읽어 `Fraction` 으로 계산한다: 기간 합계, 그룹별 current·baseline·delta, 비율 지표의 중점가중
  rate·mix·net·entry_exit, 전체 움직임, 교차 셀 수, 그리고 다섯 플래그(v2 스펙 정의를 그대로 다시 적는다).
- 플래그 판정값이 임계(0.20, 0.0001)나 부호 0 의 허용오차(1e-9) 띠 안에 들어오면 그 플래그는 "모호" 로 표시하고
  비교에서 빼되 **개수를 보고**한다.

## 9. 합격 기준 (실행 후 바꾸지 않는다)

- **A1 산술 일치.** E1·E2·E3 에서 계산된 모든 분해의 엔진 출력이 oracle 과 같다. 정수는 정확히 같고, 실수는
  `|차이| <= 1e-9`.
- **A2 플래그 일치.** 모든 플래그가 oracle 과 같다(모호 판정분 제외, 그 개수 보고).
- **A3 교차 상한.** 교차 분해의 status 가 oracle 의 관측 셀 수 규칙(`> 1000` 이면 생략)과 같다.
- **A4 손상 동작.** 아래 표의 각 손상에서 실제 동작을 기록하고 기대 동작과 대조한다. 불일치는 합격 실패가 아니라
  **결과**로 기록한다 — 단, 기대가 `reject` 또는 `caveat` 인데 실제가 `valid-silent-wrong` 이면 **방어 실패**로 표시한다.

## 10. 손상 (E1 을 기준으로)

동작 분류: `reject` 명시적 거부 · `caveat` 계산하되 플래그로 경고 · `valid-correct` 정상 결과 · `valid-silent-wrong`
경고 없이 틀린 결과.

| # | 계층 | 손상 | 기대 동작 | 근거 |
|---|---|---|---|---|
| M1 | 원본 로더 | 원본 행 10% 를 `Complaint ID` 째 복제 | reject | 로더의 유일 키 검사 |
| M2 | 엔진 입력 | 파생 행 10% 를 값까지 똑같이 복제 | valid-correct | 엔진이 grain 완전 중복을 합친다 |
| M3 | 엔진 입력 | 파생 행 1% 를 건수만 다르게 복제 | reject | grain 충돌 중복 → integrity |
| M4 | 엔진 입력 | 현재 구간에서만 제품값 20% 를 다른 이름으로 바꿈 | count: valid-silent-wrong / timely_rate: caveat | 합 분해엔 진입·이탈 개념이 없고, 비율 분해는 분해 불완전으로 억제 |
| M5 | 엔진 입력 | 현재 구간의 3일치 행을 모두 제거 | valid-silent-wrong | 엔진에는 달력 기대치가 없다(기간 완결성은 제품 경로 controller 의 몫) |
| M6 | 엔진 입력 | 현재 구간의 날짜를 모두 +1일 이동 | valid-silent-wrong | 구간 끝을 넘는 행이 조용히 빠진다(CFPB 날짜엔 시각이 없어 시간대 손상은 이 날짜 이동으로 대신한다) |
| M7 | 엔진 입력 | 한 그룹의 `timely_known` 을 전부 0 으로, `timely_yes` 는 유지 | reject | 분모 0 인데 분자 > 0 → integrity |
| M8 | 엔진 입력 | 한 그룹에서 `timely_yes > timely_known` | reject | `numerator_bounded_by_denominator` |
| M9 | 엔진 입력 | 한 행의 건수를 `12.0` 으로 | reject | 정수가 아님 → SpecError |
| M10 | 엔진 입력 | 한 행의 건수를 `1,234` 로 | reject | 정수가 아님 → SpecError |
| M11 | 엔진 입력 | 행 5% 의 제품값을 빈칸으로 | valid-correct | `__UNKNOWN__` 그룹으로 모인다(정책) |
| M12 | 엔진 입력 | `issue` 열을 삭제 | reject | 필수 열 누락 → SpecError |
| M13 | 엔진 입력 | 한 행의 건수를 음수로 | reject | 음수 → SpecError |
| M14 | 엔진 입력 | 기준 구간 전체의 `timely_known` 을 0 으로 | reject | 전체 분모 0 → aggregate |

M4·M5·M6 의 `valid-silent-wrong` 은 **기대한 결과이자 알려진 방어 공백**이다. 사전등록에 미리 적어 둔 것이므로,
실제로 그렇게 나와도 "놀라운 발견" 으로 포장하지 않는다.

## 11. 산출물

`results/` 에 실험·손상별 결과 JSON 과 요약 표를 실행 스크립트가 쓴다. 손으로 고치지 않는다. 요약 표는 예측 P1~P7
적중 여부, A1~A4 결과, 손상 14종의 기대·실제 동작을 한눈에 보인다.

## 개정

### 개정 1 — 2026-09-29: 획득 경로만 교체한다

**무엇이 일어났나.** 3절은 CFPB 검색 API(`…/search/api/v1/?format=csv&no_aggs=true&date_received_min=…&date_received_max=…`)
를 획득 경로로 지정했다. 데이터 행을 받기 전에, 실제 API 가 문서대로 동작하지 않음을 확인했다. 같은 엔드포인트로 보낸
요청과 응답 전체:

| # | 요청 | 응답 |
|---|---|---|
| 1–2 | GET, 3절 URL 그대로 (UA `bia-cfpb-external-validation/1`) | 403 |
| 3–4 | HEAD, 같은 URL (파이썬 기본 UA / curl) | 405 (서버 `envoy` 까지 도달) |
| 5–7 | GET, 같은 URL (기본 UA / 명시적 UA / 오류 본문 확인) | 400, 본문 `size` |
| 8 | GET, 같은 URL + `size=1` | 400, 본문 `size` |
| 9–10 | GET, 같은 URL + `size=100` / `size=1000000` | 429, 본문 `detail` |

API 문서는 "`format=csv` 이면 `frm`/`size` 를 무시하고 필터 결과 전체를 준다" 고 적고 있으나 서버는 `size` 를 요구했고
`size=1` 도 거부했다. **429 의 원인은 미해결이다** — 짧은 시간의 요청 횟수 때문인지, 큰 내보내기 요청에 대한 제한 때문인지
추가 요청 없이 판별할 수 없다. 확정할 수 있는 사실은 "`size=100` 및 `size=1000000` 요청에 서버가 HTTP 429 를 반환했다"
뿐이다.

**바뀌는 것 — 3절의 획득 경로만.** CFPB 가 공식 획득 방법으로 함께 안내하는 전체 데이터셋 CSV ZIP
`https://files.consumerfinance.gov/ccdb/complaints.csv.zip` 을 받아, 1·6절의 기간 필터를 **로컬에서** 적용한다. 서버 쪽
날짜 파라미터의 포함 여부 같은 미확정 동작에 더는 기대지 않으므로 "요청을 하루씩 넓게" 규칙은 불필요해진다. allowlist
여섯 열만 스트림으로 취하고 나머지(서술문 포함)를 즉시 버리는 규칙, 원본을 저장소 밖에 두는 규칙, 받은 시점의
해시·시각을 기록하는 규칙은 그대로다. 기록할 해시는 둘이다: 받은 ZIP 바이트의 SHA-256, 그리고 파생 파일의 SHA-256.

**바뀌지 않는 것.** 기간, 지표 정의, 도메인 선언, oracle, 예측 P1~P7, 합격 기준 A1~A4, 손상 M1~M14 와 판정 규칙.

**개정 시점까지 실제 민원 레코드는 한 건도 보지 않았다.** 관찰한 것은 상태 코드, 응답 헤더, 오류 본문(`size`, `detail`)뿐이다.

**추가 정지 규칙.**
- 이번 실험에서 CFPB 검색 API 에는 더 요청하지 않는다.
- 정적 파일에는 HEAD 최대 1회. 성공하고 `Content-Length` 가 있으면 기록한다. 실패하거나 길이가 없어도 **파라미터·헤더를
  바꿔 재시도하지 않는다.**
- 실제 다운로드 최대 1회. 실패하면 멈추고 보고한다.

### 개정 1 부속 — 사전등록이 열어 둔 해석의 확정 (데이터를 보기 전, 하네스 코드 `d5bd688` 에 고정)

1. `Date received` 의 기대 형식은 ISO `YYYY-MM-DD`. 다르면 4절 규칙대로 추측하지 않고 멈춘다.
2. 부호 판정값이 정확히 0 이면 "모호" 가 아니다(모호 띠는 `0 < |x| <= 1e-9`).
3. M11 은 깨끗한 실행이 아니라 정책 결과(빈 제품값 행이 `__UNKNOWN__` 그룹으로 모임)와 대조해 판정한다.
4. 각 손상은 그 손상이 바꾼 열을 쓰는 지표로 판정한다.
5. M4 의 "제품값 20%" 는 행의 20% 가 아니라 **서로 다른 제품값의 20%** 를 바꾼다.
6. A4 는 방어 실패 0건이고 0행에 적용된 손상이 없을 때만 통과로 본다(사전등록 문구보다 엄격한 해석).
7. A1 은 8절이 나열한 양만 비교한다. `contribution_share` 와 순위는 A1 대상이 아니다.

### 개정 2 — 2026-09-29: 알려진 성공 조건으로 다운로드 1회 재허용, 모호 띠 문구 정정

**근거.** 획득 시도 1(`results/acquisition_attempt1.json`, 커밋 `15b7f05`)의 정적 ZIP GET 이 403, 0바이트로 끝났다.
같은 URL 에 대한 HEAD 는 200 이었다. 403 은 User-Agent `bia-cfpb-external-validation/1` 로 보낸 요청(검색 API 요청 1–2,
그리고 이 다운로드)**에서만** 관찰됐고, 다른 UA 로 보낸 요청은 모두 서버에 도달했다(405·400·429·200). 따라서 UA 특정
차단이 **강하게 의심된다.** 확정 사실은 아니다 — 같은 조건으로 재현해 보지 않았다.

**바뀌는 것 1 — 다운로드 1회 재허용.** 같은 URL 을, **같은 파일에서 이미 200 을 받은 요청 조건 그대로** 한 번 더 요청한다:
`/usr/bin/python3` 3.9.6 의 `urllib.request`, User-Agent 헤더를 **지정하지 않음**(런타임 기본값 `Python-urllib/3.9`).
실제로 보낸 UA 문자열과 런타임 버전을 획득 기록에 남긴다. 새 UA 를 탐색하는 것이 아니다.

**정지 규칙.** 이 요청이 실패하거나 받은 바이트 수가 HEAD 의 `Content-Length`(348672205)와 다르면 UA·헤더·도구를 더 바꾸지
않고 즉시 멈춘다. 그 경우 CFPB 자동 획득은 **external-validation acquisition failure 로 봉인하고 끝낸다.** 브라우저 수동
다운로드는 별개의 획득 방식이므로 그때 다시 개정해야 한다.

**바뀌는 것 2 — 개정 1 부속 2번의 문구 정정.** 부속 2번은 모호 띠를 `0 < |x| <= 1e-9` 로 적었으나, 부속이 기록하려던
봉인 코드(`d5bd688`)는 `0 < |x| <= 2e-9` 다. 부호 판정의 경계는 `|x| = 1e-9` 이고, 엔진의 float 값과 oracle 의 정확값이
갈릴 수 있는 곳은 그 경계 근처이므로 경계에서 1e-9 안쪽 — `(0, 2e-9]` — 을 모호로 본다. 원래 문구는 1e-9 바로 위에서
float 오차로 뒤집힐 수 있는 값을 놓친다. **판정 논리는 바뀌지 않는다. 이 개정은 이미 봉인된 구현 `d5bd688` 에 맞게 서술을
바로잡는 것이다.** 두 문구가 갈리는 구간 `(1e-9, 2e-9]` 에 든 값의 개수는 진단치로 따로 보고하되 판정에 쓰지 않는다.

**바뀌지 않는 것.** 기간, 지표 정의, 도메인 선언, oracle 판정, 예측 P1~P7, 합격 기준 A1~A4, 손상 M1~M14 와 판정 규칙.

**개정 2 시점까지도 실제 민원 레코드는 한 건도 보지 않았다.**
